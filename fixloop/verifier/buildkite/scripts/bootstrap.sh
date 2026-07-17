#!/usr/bin/env bash
# Trusted bootstrap. Configure the Buildkite pipeline's ONLY step as:
#     bash fixloop/verifier/buildkite/scripts/bootstrap.sh
# pointed at THIS repo (the verifier repo), not the target repo.
#
# Trust boundary recap -- three credentials, three blast radii:
#   1. coding agent:   push branches to fork, open PRs. Nothing else.
#   2. verifier agent: read-only clone token for the target repo.
#   3. orchestrator:   trigger builds, read verdicts, merge on verified.
set -euo pipefail

# Refuse to upload a pipeline from anywhere but the protected verifier repo.
EXPECTED_REPO="${VERIFIER_REPO:?set VERIFIER_REPO to the canonical verifier repo url}"
ACTUAL=$(git config --get remote.origin.url)
if [[ "$ACTUAL" != "$EXPECTED_REPO" ]]; then
  echo "refusing pipeline upload: checkout is $ACTUAL, expected $EXPECTED_REPO" >&2
  exit 1
fi

buildkite-agent pipeline upload fixloop/verifier/buildkite/pipeline.yml
