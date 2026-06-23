"""Tests for the deterministic SVG renderer (`render_infographic.py`).

Self-contained: builds synthetic angle JSON in-memory, so no DB/uv needed.
Guards the output contract (well-formed SVG, exact figures present) and the
adaptive waffle (a huge donor field must still fit a bounded grid, not emit
tens of thousands of dots).
"""
from __future__ import annotations

import importlib.util
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_renderer():
    spec = importlib.util.spec_from_file_location(
        "render_infographic", ROOT / "bin" / "render_infographic.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def donor_size_fixture(counts, amounts):
    den_n, den_amt = sum(counts), sum(amounts)
    labels = ["Small (≤$200)", "Mid ($201–$999)", "Large ($1k–$3.4k)",
              "Maxed out (≥$3,500)"]
    return {
        "candidate": {"cand_id": "H0NM03102", "name": "Teresa Leger Fernandez",
                      "party": "DEM", "office": "U.S. House", "state": "NM",
                      "district": "03", "incumbency": "Incumbent",
                      "region_note": "Represents Taos, NM (NM-03)"},
        "angle": {"id": "donor-size", "title": "Big Checks vs. Grassroots",
                  "threshold": "Flagged when small-dollar share < 10%.",
                  "featured": True},
        "headline": "7.2% grassroots, 40.5% from maxed-out donors",
        "subhead": "by check size",
        "chart": {"type": "bar", "unit": "USD",
                  "denominator": {"label": "Total individual donations",
                                  "amount": den_amt, "count": den_n},
                  "rows": [{"bucket": labels[i], "amount": amounts[i],
                            "count": counts[i],
                            "share_of_individual": amounts[i] / den_amt}
                           for i in range(4)]},
        "footnotes": ["Itemized individual contributions only."],
        "source": "FEC 2026 cycle; sql/queries/candidate_buckets.sql",
    }


def geography_fixture(home_amt, oos_states):
    """oos_states: list of (state, label, amount). Plus the home state."""
    den = home_amt + sum(a for _, _, a in oos_states)
    rows = [{"state": "NM", "label": "New Mexico (home)", "amount": home_amt,
             "count": 100, "share_of_individual": home_amt / den, "in_state": True}]
    for st, label, amt in oos_states:
        rows.append({"state": st, "label": label, "amount": amt, "count": 50,
                     "share_of_individual": amt / den, "in_state": False})
    oos_amt = den - home_amt
    return {
        "candidate": {"cand_id": "S0NM00058", "name": "Ben Ray Lujan", "party": "DEM",
                      "office": "U.S. Senate", "state": "NM", "district": "00",
                      "incumbency": "Incumbent", "region_note": "U.S. Senate, NM"},
        "angle": {"id": "geography", "title": "In-state vs. out-of-state",
                  "metric": "out_of_state_share", "metric_value": oos_amt / den,
                  "threshold": "Flagged when out-of-state ≥ 40%.", "featured": True},
        "headline": f"{oos_amt / den * 100:.1f}% out-of-state money",
        "subhead": "by donor state",
        "chart": {"type": "bar", "unit": "USD",
                  "denominator": {"label": "Total individual donations",
                                  "amount": den, "count": 100 + 50 * len(oos_states)},
                  "rows": rows,
                  "summary": {"in_state_amount": home_amt,
                              "in_state_share": home_amt / den,
                              "out_of_state_amount": oos_amt,
                              "out_of_state_share": oos_amt / den}},
        "footnotes": ["Geography is for individual donors only."],
        "source": "FEC 2026 cycle; sql/queries/candidate_top_states.sql",
    }


class RenderInfographicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_renderer()

    def test_renders_well_formed_svg_with_exact_figures(self) -> None:
        d = donor_size_fixture([1268, 306, 183, 75],
                               [46604, 114375, 225336, 262500])
        svg = self.mod.render(d)
        self.assertTrue(svg.lstrip().startswith("<svg"))
        ET.fromstring(svg)  # raises on malformed XML
        for token in ["Teresa Leger Fernandez", "40.5%", "7.2%",
                      "$262,500", "$46,604", "1,832"]:
            self.assertIn(token, svg, f"missing {token!r}")

    def test_waffle_is_bounded_for_huge_donor_fields(self) -> None:
        small = self.mod.render(donor_size_fixture(
            [1268, 306, 183, 75], [46604, 114375, 225336, 262500]))
        huge = self.mod.render(donor_size_fixture(
            [120000, 30000, 9000, 1000], [5e6, 1e7, 2e7, 3e7]))
        # Small field: one dot per gift (~1,832 + chart marks).
        self.assertGreater(small.count("<circle"), 1800)
        # Huge field (160k gifts) must still fit the box, not explode.
        self.assertLess(huge.count("<circle"), 2000)

    def test_handles_missing_bucket(self) -> None:
        # Real exports omit empty buckets; a candidate with no maxed-out tier
        # must still render (zero-filled), not crash on a positional index.
        d = donor_size_fixture([1268, 306, 183, 75],
                               [46604, 114375, 225336, 262500])
        d["chart"]["rows"] = [r for r in d["chart"]["rows"]
                              if "Maxed" not in r["bucket"]]
        svg = self.mod.render(d)            # must not raise
        ET.fromstring(svg)
        self.assertIn("Maxed-out", svg)     # tier still labeled, just at zero

    def test_geography_renders_with_exact_figures(self) -> None:
        d = geography_fixture(358792, [("CA", "California", 233460),
                                       ("DC", "Washington, D.C.", 153555),
                                       ("NY", "New York", 114885)])
        svg = self.mod.render(d)
        self.assertTrue(svg.lstrip().startswith("<svg"))
        ET.fromstring(svg)
        # home 358,792 of 860,692 -> 41.7% in / 58.3% out.
        for token in ["Ben Ray Lujan", "58.3%", "$358,792", "$233,460",
                      "New Mexico (home)", "California"]:
            self.assertIn(token, svg, f"missing {token!r}")
        # Senate card drops the meaningless "-00" district.
        self.assertNotIn("NM-00", svg)

    def test_unimplemented_angle_exits_cleanly(self) -> None:
        d = donor_size_fixture([1, 1, 1, 1], [1, 1, 1, 1])
        d["angle"]["id"] = "composition"          # no body renderer yet
        with self.assertRaises(SystemExit):
            self.mod.render(d)


if __name__ == "__main__":
    unittest.main()
