#!/usr/bin/env bash
# Seeds the public demo repo and files two issues.
# Usage:
#   ./seed_demo_repo.sh --local /tmp/fixloop-demo      # build locally, no push
#   ./seed_demo_repo.sh yourorg/fixloop-demo           # create + push via gh
set -euo pipefail

LOCAL=0
if [[ "${1:-}" == "--local" ]]; then LOCAL=1; TARGET="${2:-/tmp/fixloop-demo}";
else REPO_SLUG="${1:?usage: seed_demo_repo.sh [--local dir | org/name]}"; TARGET=$(mktemp -d); fi

cd "$TARGET" 2>/dev/null || { mkdir -p "$TARGET"; cd "$TARGET"; }
git init -q -b main
git config user.email fixloop-demo@example.com
git config user.name fixloop-demo

mkdir -p src tests
touch conftest.py src/__init__.py
cat > src/textutils.py <<'EOF'
def truncate(s: str, n: int) -> str:
    """Truncate s to at most n characters, appending an ellipsis if cut.

    Contract: len(truncate(s, n)) <= n for all inputs.
    """
    if len(s) <= n:
        return s
    return s[:n] + "\u2026"   # BUG: off-by-one, result is n+1 chars


def slugify(s: str) -> str:
    """Lowercase, spaces/punct to single dashes, no leading/trailing dash."""
    out = []
    for ch in s.lower():
        out.append(ch if ch.isalnum() else "-")
    return "".join(out).strip("-")   # BUG: repeated separators not collapsed
EOF
cat > tests/test_basic.py <<'EOF'
from src.textutils import truncate, slugify

def test_short_strings_untouched():
    assert truncate("hi", 10) == "hi"

def test_slugify_simple():
    assert slugify("Hello World") == "hello-world"
EOF
cat > README.md <<'EOF'
# fixloop-demo
Demo target repo for fixloop (loop engineering hackathon). Contains seeded
bugs referenced by open issues. Layout: src/ + tests/ (pytest).
EOF
git add -A && git commit -qm "seed: textutils with known issues"

if [[ "$LOCAL" == 1 ]]; then
  echo "local demo repo at $TARGET (no push, no issues filed)"
  exit 0
fi

gh repo create "$REPO_SLUG" --public --source . --push

gh issue create --repo "$REPO_SLUG" \
  --title "truncate() returns n+1 characters when input exceeds max length" \
  --body "$(cat <<'EOF'
`truncate` violates its own contract: the docstring says the result is at
most `n` chars, but any input longer than `n` returns `n+1`.

Repro:
```python
from src.textutils import truncate
out = truncate("hello world", 5)
print(len(out))   # 6, expected <= 5
```
Expected: `len(truncate(s, n)) <= n` for all inputs, ellipsis preserved.
EOF
)"

gh issue create --repo "$REPO_SLUG" \
  --title "slugify() produces repeated dashes for consecutive separators" \
  --body "$(cat <<'EOF'
Consecutive non-alphanumeric characters each become their own dash instead
of collapsing to one.

Repro:
```python
from src.textutils import slugify
print(slugify("hello,  world!!"))   # 'hello---world' (trailing stripped), expected 'hello-world'
```
Expected: runs of separators collapse to a single dash.
EOF
)"

echo "pushed $REPO_SLUG with 2 issues filed"
