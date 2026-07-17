# Held-back metamorphic probes for issue-1 in the seeded public demo repo.
# These mirror demo-issue-1 so a fix that special-cases the visible repro fails.
from src.textutils import truncate


def test_perturbed_string():
    out = truncate("goodbye cruel moon", 7)
    assert len(out) <= 7


def test_perturbed_length():
    out = truncate("hello world", 8)
    assert len(out) <= 8


def test_boundary_adjacent():
    out = truncate("abcdef", 5)
    assert len(out) <= 5
