"""Optional Stripe-backed x402 protection for ``POST /fix``.

This is intentionally a small sandbox integration.  Stripe creates a fresh
Base deposit address for each payment challenge; x402 verifies the signed
payment before the request reaches the handler.  Set ``X402_ENABLED=false``
to leave the app completely unchanged.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Mapping


STRIPE_API_VERSION = "2026-03-04.preview"
DEFAULT_FACILITATOR_URL = "https://www.x402.org/facilitator"
DEFAULT_NETWORK = "eip155:84532"  # Base Sepolia
CACHE_TTL_SECONDS = 300


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PaymentSettings:
    enabled: bool = False
    stripe_secret_key: str | None = None
    facilitator_url: str = DEFAULT_FACILITATOR_URL
    network: str = DEFAULT_NETWORK
    price_usdc: str = "0.01"

    @classmethod
    def from_env(cls) -> "PaymentSettings":
        return cls(
            enabled=_env_bool("X402_ENABLED"),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY"),
            facilitator_url=os.getenv("FACILITATOR_URL", DEFAULT_FACILITATOR_URL),
            network=os.getenv("X402_NETWORK", DEFAULT_NETWORK),
            price_usdc=os.getenv("FIXLOOP_PRICE_USDC", "0.01"),
        )

    @property
    def price(self) -> str:
        try:
            value = Decimal(self.price_usdc)
        except InvalidOperation as exc:
            raise ValueError("FIXLOOP_PRICE_USDC must be a decimal amount") from exc
        if value <= 0:
            raise ValueError("FIXLOOP_PRICE_USDC must be greater than zero")
        return f"${value:.2f}"

    @property
    def amount_cents(self) -> int:
        value = Decimal(self.price_usdc)
        cents = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if cents < 1:
            raise ValueError("FIXLOOP_PRICE_USDC must be at least $0.01")
        return cents


class DepositAddressCache:
    """Tiny process-local TTL cache suitable for the hackathon sandbox."""

    def __init__(self, ttl_seconds: int = CACHE_TTL_SECONDS, max_entries: int = 1024):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, tuple[float, str]] = {}

    def put(self, address: str, payment_intent_id: str) -> None:
        self._prune()
        if len(self._entries) >= self.max_entries:
            oldest = min(self._entries, key=lambda key: self._entries[key][0])
            self._entries.pop(oldest, None)
        self._entries[address.lower()] = (
            time.monotonic() + self.ttl_seconds,
            payment_intent_id,
        )

    def contains(self, address: str) -> bool:
        self._prune()
        return address.lower() in self._entries

    def payment_intent_id(self, address: str) -> str | None:
        self._prune()
        entry = self._entries.get(address.lower())
        return entry[1] if entry else None

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [key for key, (deadline, _) in self._entries.items() if deadline <= now]
        for key in expired:
            self._entries.pop(key, None)


def _stripe_payment_intent_factory(secret_key: str, amount_cents: int) -> Any:
    # Imported only when payments are enabled so the core demo can run without
    # Stripe/x402 dependencies installed.
    import stripe

    stripe.api_key = secret_key
    stripe.api_version = STRIPE_API_VERSION
    stripe.set_app_info(
        "fixloop",
        version="0.1.0",
        url="https://github.com/Rushendra10/loop-engineering-hack",
    )
    return stripe.PaymentIntent.create(
        amount=amount_cents,
        currency="usd",
        payment_method_types=["crypto"],
        payment_method_data={"type": "crypto"},
        payment_method_options={
            "crypto": {
                "mode": "deposit",
                "deposit_options": {"networks": ["base"]},
            }
        },
        confirm=True,
    )


class StripePayToResolver:
    """Resolve a cached retry address or create a Stripe deposit address."""

    def __init__(
        self,
        settings: PaymentSettings,
        *,
        cache: DepositAddressCache | None = None,
        payment_intent_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        if not settings.stripe_secret_key:
            raise ValueError("STRIPE_SECRET_KEY is required when X402_ENABLED=true")
        self.settings = settings
        self.cache = cache or DepositAddressCache()
        self.payment_intent_factory = payment_intent_factory or _stripe_payment_intent_factory
        self.last_payment_intent_id: str | None = None

    async def __call__(self, context: Any) -> str:
        payment_header = getattr(context, "payment_header", None) or getattr(
            context, "paymentHeader", None
        )
        if payment_header:
            return self._address_from_payment_header(payment_header)
        return self._create_deposit_address()

    def _address_from_payment_header(self, payment_header: str) -> str:
        try:
            decoded = json.loads(base64.b64decode(payment_header).decode("utf-8"))
            address = decoded["payload"]["authorization"]["to"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid payment header") from exc
        if not isinstance(address, str) or not self.cache.contains(address):
            raise ValueError("Invalid payTo address: not found in server cache")
        self.last_payment_intent_id = self.cache.payment_intent_id(address)
        return address.lower()

    def _create_deposit_address(self) -> str:
        payment_intent = self.payment_intent_factory(
            self.settings.stripe_secret_key or "", self.settings.amount_cents
        )
        try:
            intent_id = _mapping_value(payment_intent, "id")
            next_action = _mapping_value(payment_intent, "next_action")
            details = _mapping_value(next_action, "crypto_display_details")
            addresses = _mapping_value(details, "deposit_addresses")
            base = _mapping_value(addresses, "base")
            address = _mapping_value(base, "address")
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "PaymentIntent did not return expected crypto deposit details"
            ) from exc
        if not isinstance(intent_id, str) or not isinstance(address, str):
            raise ValueError("PaymentIntent did not return expected crypto deposit details")
        address = address.lower()
        self.cache.put(address, intent_id)
        self.last_payment_intent_id = intent_id
        print(f"Created Stripe PaymentIntent {intent_id} -> {address}", flush=True)
        return address


def _mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value[key]
    return getattr(value, key)


def configure_payments(
    app: Any,
    *,
    settings: PaymentSettings | None = None,
    resolver: StripePayToResolver | None = None,
) -> bool:
    """Protect only ``POST /fix`` when explicitly enabled.

    Returns whether middleware was installed.  The resolver is exposed at
    ``app.state.payment_resolver`` so the demo can show the latest sandbox
    PaymentIntent ID without logging or returning secrets.
    """

    settings = settings or PaymentSettings.from_env()
    if not settings.enabled:
        return False

    from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
    from x402.http.middleware.fastapi import PaymentMiddlewareASGI
    from x402.http.types import RouteConfig
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.server import x402ResourceServer

    resolver = resolver or StripePayToResolver(settings)
    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=settings.facilitator_url))
    server = x402ResourceServer(facilitator)
    server.register(settings.network, ExactEvmServerScheme())
    routes = {
        "POST /fix": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    price=settings.price,
                    network=settings.network,
                    pay_to=resolver,
                )
            ],
            description="Run and independently verify an agent bug fix",
            mime_type="application/json",
        )
    }
    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
    app.state.payment_resolver = resolver
    return True
