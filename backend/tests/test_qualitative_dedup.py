"""Unit tests for the qualitative dedup helper (app/qualitative/dedup.py).

Covers text normalization + hash stability/discrimination, which is what the
(ticker, dedup_hash) unique index and the pre-Groq skip both rely on.

Run with pytest, or directly:
    python3 tests/test_qualitative_dedup.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.qualitative import dedup


def test_normalize_lowercases_and_collapses_whitespace():
    assert dedup.normalize_text("  Hello   WORLD\n\tfoo ") == "hello world foo"


def test_hash_is_stable_across_cosmetic_whitespace_and_case():
    # Same lede, different casing/spacing -> same hash (the intended dedup).
    a = dedup.compute_hash("Acme Corp signs $2B contract with BigCo.")
    b = dedup.compute_hash("acme   corp signs $2b   CONTRACT with bigco.")
    assert a == b


def test_hash_differs_for_different_text():
    a = dedup.compute_hash("Acme Corp signs $2B contract.")
    b = dedup.compute_hash("Acme Corp faces SEC investigation.")
    assert a != b


def test_is_duplicate_uses_the_seen_set():
    seen = {dedup.compute_hash("Acme wins a patent.")}
    assert dedup.is_duplicate("acme  wins a PATENT.", seen) is True
    assert dedup.is_duplicate("Totally different headline.", seen) is False


def test_only_prefix_matters_so_trailing_churn_is_ignored():
    # A long shared lede + differing tails past the prefix cutoff dedup together
    # (documented, accepted V1 approximation).
    base = "X" * 200
    assert dedup.compute_hash(base + " trailing A") == dedup.compute_hash(base + " trailing B")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all dedup tests passed")
