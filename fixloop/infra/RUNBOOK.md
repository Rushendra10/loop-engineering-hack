# P3 runbook — infra, payments, demo

Work top to bottom. Anything blocked > 15 min: invoke the fallback and move.

## Current demo posture

Zero listing is pending. Keep x402 as an optional sandbox/testnet beat and
never block the core verifier demo on it. The required spoken disclaimer is:

> This uses testnet USDC and Stripe sandbox; no real money moved.

The committed Akash SDL defaults to `X402_ENABLED=false`. Enable it only in
the ignored `akash-deploy.prod.yaml` after the payment checks below are green.

## Sponsor setup

**Zero.xyz booth — exact questions:**
1. How do we list/register a paid service as a *provider*? Web flow, CLI,
   or do you onboard us manually right now?
2. What does the service need to expose — plain HTTP endpoint, x402
   handshake, or your facilitator SDK? Payout wallet setup?
3. Can you seed our test wallet so the demo call shows a real USDC charge?
4. Can a stock CLI agent (Claude Code etc.) *discover* our listing today,
   or should the demo call it by ID?
   → If listing can't go live in ~1h: ask for the raw x402 facilitator
   path and demo "payment live, listing pending."

**Akash booth:**
1. Hackathon credits / funded deployment vouchers?
2. Fastest deploy path today — console.akash.network fine? SDL below sane?
3. AkashML: base URL + API key for OpenAI-compatible inference (P2 is
   blocked on this — get it FIRST and message it to them).

**Cursor booth:**
1. Confirm the current headless `cursor-agent` build installed by the image.
2. Can it point at a third-party OpenAI-compatible endpoint? (Confirms or
   kills P2's spike faster than reading docs.)

**Buildkite booth:**
1. Fresh org for the team + agent token + API token (P1 needs the API
   token for trigger/poll).
2. Any hackathon gotchas with self-hosted agents? We're running ours on
   Akash (Dockerfile.buildkite-agent).

## T+0:20 → T+1:00 — infra shell

- [ ] `gh auth` as the team bot account; create org/repo if needed.
- [ ] `bash seed_demo_repo.sh yourorg/fixloop-demo` → repo + 2 issues live.
      (Dry-run first: `bash seed_demo_repo.sh --local /tmp/x`)
- [ ] Docker build the image with a placeholder app
      (`python -m http.server 8080` CMD override is fine), push to ghcr.io.
- [ ] Deploy hello-world to Akash via console with akash-deploy.yaml.
      Success = public URL returns anything. Save the URL in team chat.
- [ ] Buildkite: create org + pipeline (repo = the monorepo; steps =
      `bash fixloop/verifier/buildkite/scripts/bootstrap.sh`; env
      `VERIFIER_REPO=<repo url>`). Build + push the agent image
      (Dockerfile.buildkite-agent), deploy to Akash with
      BUILDKITE_AGENT_TOKEN + tags queue=verifier. Success = a manually
      triggered build with the demo repo's meta-data returns a verdict
      annotation. Hand P1 the API token.
- [ ] FALLBACK if Buildkite agent on Akash fights you past 30 min: run the
      agent container on a laptop (it only needs outbound network) and
      keep the Akash retry for later — service stays on VERIFIER_MODE=local
      until either works, so nothing downstream blocks.
- [ ] FALLBACK if Akash fights you past T+1:15: cloudflared tunnel from a
      laptop for the API now, retry Akash at T+2:30. AkashML inference
      keeps the Akash story honest regardless — but a real Akash deploy of
      at least ONE component is worth fighting for with a judging sponsor.

## Payments — 15-minute testnet time-box

`purl` has no Stripe-style fake buyer wallet. It signs with a real disposable
EVM key, but Base Sepolia USDC has no value. Never paste or commit its private
key; keep it in `purl`'s local keystore.

```bash
brew install stripe/purl/purl
purl wallet add --type evm
purl wallet list
```

Fund the displayed address with Base Sepolia USDC from an
[official Base faucet](https://docs.base.org/base-chain/network-information/network-faucets),
then verify the public balance:

```bash
purl balance --network base-sepolia 0xYOUR_DISPOSABLE_ADDRESS
```

Start the service with a Stripe sandbox secret. An unpaid request must return
402 and must not create a job:

```bash
X402_ENABLED=true \
STRIPE_SECRET_KEY=sk_test_REDACTED \
FACILITATOR_URL=https://www.x402.org/facilitator \
X402_NETWORK=eip155:84532 \
FIXLOOP_PRICE_USDC=0.01 \
uvicorn service.app:app --host 0.0.0.0 --port 8080

curl -i -X POST http://localhost:8080/fix \
  -H 'Content-Type: application/json' \
  -d '{"repo":"https://github.com/ORG/fixloop-demo","issue":1}'
```

The server log prints the safe `pi_...` identifier when Stripe creates the
deposit address. With the wallet funded, run the genuine 402 → sign → retry
flow and save the Base Sepolia explorer URL from `purl` output:

```bash
purl -i -X POST http://localhost:8080/fix \
  --json '{"repo":"https://github.com/ORG/fixloop-demo","issue":1}' \
  --network base-sepolia --max-amount 10000
```

[Stripe sandboxes do not observe testnet transfers](https://docs.stripe.com/payments/machine/x402).
Separately mark that sandbox PaymentIntent successful with Stripe's test
helper, then confirm the returned status and Dashboard entry:

```bash
curl "https://api.stripe.com/v1/test_helpers/payment_intents/PI_ID/simulate_crypto_deposit" \
  -u "$STRIPE_SECRET_KEY:" \
  -H 'Stripe-Version: 2026-03-04.preview' \
  -d transaction_hash=0x00000000000000000000000000000000000000000000000000000testsuccess \
  -d network=base \
  -d token_currency=usdc \
  -d buyer_wallet=0x0000000000000000000000000000000000000000
```

If PaymentIntent creation returns `Received unknown parameter:
payment_method_options[crypto][mode]`, first verify that Stripe Dashboard and
`STRIPE_SECRET_KEY` point at the same sandbox where crypto is enabled. If it
persists, that sandbox lacks the private-preview deposit-mode entitlement; ask
the Stripe booth or `machine-payments@stripe.com` to enable it.

Latest live checkpoint (2026-07-17): the correctly selected sandbox created a
PaymentIntent, unpaid `POST /fix` returned 402, and the sandbox helper moved the
intent from `processing` to `succeeded`. The separate on-chain `purl` step still
requires faucet USDC in the disposable buyer wallet.

- [ ] If the wallet/faucet is not ready in 15 minutes, retain automated
      payment tests and cut the live payment beat.
- [ ] If x402 is not green after 30 additional minutes, set
      `X402_ENABLED=false` and stop debugging payments.
- [ ] Never imply the test-helper result observed the Base Sepolia transfer.

## T+1:00 → T+2:30 — real image

- [ ] Rebuild image with P1's actual service + P2's worker as they land;
      redeploy. Keep the hello-world deployment up until the real one
      responds (blue/green by having two deployments, delete old after).
- [ ] Give P1 the AkashML creds + deployed URL as env vars via console.

## T+2:30 → T+3:30 — demo assembly

- [ ] Pre-run issue #1 through the full pipeline 3x. Note wall time — this
      sets whether the live demo runs real-time or you demo the pre-warmed
      job with a live one racing in a second terminal.
- [ ] Capture for the deck/talk: the verdict JSON of a `suspected_overfit`
      attempt (ask P1/P2 to force one with a special-cased fix if the agent
      never games naturally), the USDC settlement screenshot, cost per
      verified fix (AkashML tokens + Akash spend vs price per call).
- [ ] Record the backup video: full happy path, phone or QuickTime, 2 min.

## T+3:30 → T+4:00 — freeze + rehearse

- [ ] Nothing merges except demo fixes.
- [ ] Two full dry runs of the 3-minute script (in the design doc).
- [ ] Assign speaking: you drive terminals, P1 narrates the verifier beat,
      P2 narrates the agent beat. The closer is the gamed-fix rejection:
      "every other team demos an agent that claims success; we demo the
      system that checks."

## Files in this kit

- `Dockerfile` — worker+API image with Cursor, `gh`, verifier and x402 deps
- `Dockerfile.buildkite-agent` — verifier CI agent, deploy on Akash
- `akash-deploy.yaml` — SDL, edit image path + env, deploy via console
- `seed_demo_repo.sh` — creates the public demo repo + files both issues
