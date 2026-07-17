# P3 runbook — infra, payments, demo

Work top to bottom. Anything blocked > 15 min: invoke the fallback and move.

## T+0:00 — booths first (do this before opening a laptop)

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

**Cursor booth (walk past it anyway):**
1. Current headless install command for cursor-agent in Docker (the
   Dockerfile has a TODO for this).
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
      `bash verifier/buildkite/scripts/bootstrap.sh`; env
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

## T+1:00 → T+2:30 — payments + real image

- [ ] Zero listing (or x402 endpoint) pointing at the deployed URL.
      One paid test call from your own wallet. Screenshot the settlement.
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

- `Dockerfile` — worker+API image (one TODO: cursor-agent install line)
- `Dockerfile.buildkite-agent` — verifier CI agent, deploy on Akash
- `akash-deploy.yaml` — SDL, edit image path + env, deploy via console
- `seed_demo_repo.sh` — creates the public demo repo + files both issues
