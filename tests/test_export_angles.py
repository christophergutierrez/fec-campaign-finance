"""Unit tests for `influence.build_angles` — no DB, synthetic profiles.

Durable replacement for a live-candidate CLI viability check: exercises the
below-floor gate and the donor-size refund/positive-gift reconciliation directly
against the angle-assembly logic.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_influence():
    sys.path.insert(0, str(ROOT / "bin"))                 # for `import sqlutil`
    sys.modules.setdefault("duckdb", types.SimpleNamespace(connect=lambda *a, **k: None))
    spec = importlib.util.spec_from_file_location("influence", ROOT / "bin" / "influence.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CAND = {"cand_id": "H0NM00000", "name": "Test Candidate", "name_fec": "CANDIDATE, TEST",
        "party": "DEM", "office": "U.S. House", "state": "NM", "district": "03",
        "incumbency": "Incumbent", "election_year": 2026, "region_note": "NM-03"}


def profile(buckets, **over):
    """A gather_profile-shaped dict; `over` overrides scalar channels."""
    pos = [(b, n, a) for b, n, a in buckets if str(b)[:1] != "5" and a > 0]
    ind = sum(a for _, _, a in buckets)
    d = {
        "ind": ind, "ind_count": sum(n for _, n, _ in buckets),
        "dpac_amt": 0.0, "dpac_count": 0, "ie_s": 0.0, "ie_o": 0.0,
        "raised": ind, "backing": ind,
        "state_rows": [("NM", sum(a for _, _, a in pos), 10)],
        "instate": sum(a for _, _, a in pos), "buckets": buckets,
        "keyword_blocs": [], "cblocs": [], "oos_rows": [], "oos_total": 0.0, "hhi": 0.0,
        "top_pacs": [], "top_ie": [],
        "out_of_state_share": 0.0, "small_dollar_share": 0.0, "low_small_share": 0.0,
        "maxplus_share": 0.0, "pac_share": 0.0, "outside_share": 0.0,
    }
    d.update(over)
    return d


class BuildAnglesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inf = load_influence()

    def test_below_floor_yields_no_angles(self) -> None:
        d = profile([("1 small  (<=$200)", 5, 500.0)])   # $500 total, tiny
        emitted, skipped = self.inf.build_angles(CAND, d)
        self.assertEqual(emitted, [])
        self.assertTrue(skipped)                          # reasons recorded

    def test_donor_size_excludes_refunds_and_reconciles(self) -> None:
        buckets = [("1 small  (<=$200)", 1000, 50000.0),
                   ("2 mid    ($201-999)", 100, 40000.0),
                   ("3 large  ($1k-max)", 50, 80000.0),
                   ("4 max+   (>=$3500)", 10, 40000.0),
                   ("5 refund/negative", 8, -30000.0)]
        emitted, _ = self.inf.build_angles(CAND, profile(buckets))
        ds = next(a for a in emitted if a["angle"]["id"] == "donor-size")
        chart = ds["chart"]
        # Refund row dropped; denominator is the positive-gift universe.
        self.assertEqual(len(chart["rows"]), 4)
        self.assertNotIn("Refund", " ".join(r["bucket"] for r in chart["rows"]))
        self.assertEqual(chart["denominator"]["amount"], 210000.0)
        self.assertEqual(chart["denominator"]["count"], 1160)
        # Positive rows reconcile to the denominator and shares sum to ~1.0.
        self.assertEqual(sum(r["amount"] for r in chart["rows"]), 210000.0)
        self.assertAlmostEqual(sum(r["share_of_individual"] for r in chart["rows"]),
                               1.0, places=3)
        # A refund footnote is surfaced.
        self.assertTrue(any("refund" in f.lower() for f in ds["footnotes"]))

    def test_bucket_query_excludes_zero_dollar_rows(self) -> None:
        sql = (ROOT / "sql" / "queries" / "candidate_buckets.sql").read_text()
        self.assertIn("amount <> 0", sql)


if __name__ == "__main__":
    unittest.main()
