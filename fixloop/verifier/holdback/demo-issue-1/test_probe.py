# Held-back metamorphic probes for demo-issue-1.
# Generated at triage time by perturbing the repro. NEVER shown to the agent.
# If the exact repro passes but these fail, the fix special-cased the input.
from src.textutils import truncate


def test_perturbed_string():
    out = truncate("goodbye cruel moon", 7)
    assert len(out) <= 7


def test_perturbed_length():
    out = truncate("hello world", 8)
    assert len(out) <= 8


def test_boundary_adjacent():
    # exactly one char over the limit -- the original off-by-one trigger
    out = truncate("abcdef", 5)
    assert len(out) <= 5
