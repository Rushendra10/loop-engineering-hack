import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.payments import (
    DepositAddressCache,
    PaymentSettings,
    StripePayToResolver,
    configure_payments,
)


ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"


def settings(**overrides):
    values = {
        "enabled": True,
        "stripe_secret_key": "sk_test_not_a_real_secret",
        "price_usdc": "0.01",
    }
    values.update(overrides)
    return PaymentSettings(**values)


def payment_intent(_secret, amount_cents):
    assert amount_cents == 1
    return {
        "id": "pi_test_123",
        "next_action": {
            "crypto_display_details": {
                "deposit_addresses": {"base": {"address": ADDRESS}}
            }
        },
    }


def encode_header(address):
    payload = {"payload": {"authorization": {"to": address}}}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_disabled_mode_does_not_protect_handler():
    app = FastAPI()

    @app.post("/fix")
    def fix():
        return {"job_id": "demo"}

    assert configure_payments(app, settings=settings(enabled=False)) is False
    response = TestClient(app).post("/fix")
    assert response.status_code == 200
    assert response.json() == {"job_id": "demo"}


def test_resolver_creates_and_reuses_stripe_deposit_address():
    resolver = StripePayToResolver(settings(), payment_intent_factory=payment_intent)

    created = asyncio.run(resolver(SimpleNamespace(payment_header=None)))
    reused = asyncio.run(
        resolver(SimpleNamespace(payment_header=encode_header(ADDRESS.upper())))
    )

    assert created == ADDRESS
    assert reused == ADDRESS
    assert resolver.last_payment_intent_id == "pi_test_123"


def test_resolver_rejects_unknown_or_malformed_payment_address():
    resolver = StripePayToResolver(settings(), payment_intent_factory=payment_intent)

    with pytest.raises(ValueError, match="not found in server cache"):
        asyncio.run(resolver(SimpleNamespace(payment_header=encode_header(ADDRESS))))
    with pytest.raises(ValueError, match="Invalid payment header"):
        asyncio.run(resolver(SimpleNamespace(payment_header="not-base64")))


def test_cache_expires_addresses(monkeypatch):
    now = 100.0
    monkeypatch.setattr("service.payments.time.monotonic", lambda: now)
    cache = DepositAddressCache(ttl_seconds=5)
    cache.put(ADDRESS, "pi_test_123")
    assert cache.contains(ADDRESS)

    now = 106.0
    assert not cache.contains(ADDRESS)


def test_enabled_mode_returns_402_before_handler_runs():
    calls = []
    app = FastAPI()

    @app.post("/fix")
    def fix():
        calls.append(True)
        return {"job_id": "should-not-run"}

    resolver = StripePayToResolver(settings(), payment_intent_factory=payment_intent)
    assert configure_payments(app, settings=settings(), resolver=resolver) is True

    response = TestClient(app).post("/fix")
    assert response.status_code == 402
    assert "payment-required" in response.headers
    assert calls == []


def test_unknown_payment_authorization_does_not_reach_handler():
    calls = []
    app = FastAPI()

    @app.post("/fix")
    def fix():
        calls.append(True)
        return {"job_id": "should-not-run"}

    resolver = StripePayToResolver(settings(), payment_intent_factory=payment_intent)
    configure_payments(app, settings=settings(), resolver=resolver)

    response = TestClient(app).post(
        "/fix", headers={"payment-signature": encode_header(ADDRESS)}
    )
    # x402 currently surfaces a rejected dynamic payTo hook as 500; the
    # important trust-boundary invariant is that the paid handler is skipped.
    assert response.status_code >= 400
    assert calls == []


def test_mocked_valid_authorization_reaches_handler_once(monkeypatch):
    from x402.http.types import HTTPProcessResult, ProcessSettleResult
    from x402.http.x402_http_server import x402HTTPResourceServer

    calls = []
    app = FastAPI()

    @app.post("/fix")
    def fix():
        calls.append(True)
        return {"job_id": "demo"}

    async def accept_payment(self, context, paywall_config=None):
        assert context.payment_header == "mock-valid-authorization"
        return HTTPProcessResult(
            type="payment-verified",
            payment_payload=SimpleNamespace(),
            payment_requirements=SimpleNamespace(network="eip155:84532"),
        )

    async def settle_payment(self, *args, **kwargs):
        return ProcessSettleResult(
            success=True,
            headers={"payment-response": "mock-settled"},
            transaction="0xtest",
            network="eip155:84532",
        )

    monkeypatch.setattr(x402HTTPResourceServer, "initialize", lambda self: None)
    monkeypatch.setattr(x402HTTPResourceServer, "process_http_request", accept_payment)
    monkeypatch.setattr(x402HTTPResourceServer, "process_settlement", settle_payment)

    resolver = StripePayToResolver(settings(), payment_intent_factory=payment_intent)
    configure_payments(app, settings=settings(), resolver=resolver)
    response = TestClient(app).post(
        "/fix", headers={"payment-signature": "mock-valid-authorization"}
    )

    assert response.status_code == 200
    assert response.json() == {"job_id": "demo"}
    assert response.headers["payment-response"] == "mock-settled"
    assert calls == [True]
