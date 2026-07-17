#!/usr/bin/env bash
# Runs the verifier against all three demo submissions.
# Usage: ./run_demo.sh [target_dir]
set -uo pipefail
TARGET="${1:-/tmp/demo-target}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BASE=$(git -C "$TARGET" rev-parse main)

for branch in good-fix gamed-fix lazy-fix; do
  echo
  echo "==================== $branch ===================="
  python3 "$HERE/verify.py" \
    --repo "$TARGET" \
    --base "$BASE" \
    --test-commit "$branch~1" \
    --fix-commit "$branch" \
    --issue demo-issue-1 \
    --config "$HERE/verifier.yml" \
    --holdback-root "$HERE/holdback" \
    --out "/tmp/verdict-$branch.json" \
    | python3 -c "import json,sys; v=json.load(sys.stdin); print('verdict :', v['verdict']); print('reasons :', v['reason_codes'] or '-')"
done
echo
echo "full verdict JSONs in /tmp/verdict-*.json"
