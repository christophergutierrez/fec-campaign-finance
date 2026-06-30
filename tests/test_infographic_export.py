"""Smoke test for `influence.py --export-infographic`.

Runs the real CLI (via uv, exercising the actual Python + DuckDB runtime path)
against a known candidate and asserts the output contract: file naming, the
per-angle JSON schema, the partition-chart reconciliation invariant, and
run-to-run determinism. Skips when the DB artifact or uv is unavailable.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "fec_campaign_finance.db"
CAND = "S6MI00426"  # STEVENS, HALEY — has individual, PAC, and IE channels.

REQUIRED_TOP = {"candidate", "angle", "headline", "subhead", "chart", "footnotes", "source"}
REQUIRED_ANGLE = {"id", "title", "reason", "metric", "metric_value", "threshold", "featured"}
# Exported charts currently all partition their denominator.
PARTITION_ANGLES = {"donor-size", "geography"}


def export(out_dir: Path, cand: str = CAND) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", "/tmp/uv-cache")
    result = subprocess.run(
        ["uv", "run", "bin/influence.py", cand, "--export-infographic", str(out_dir)],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    if result.returncode != 0:
        env_failures = [
            "Failed to fetch:",
            "Could not acquire lock",
            "No module named 'duckdb'",
        ]
        if any(s in result.stderr for s in env_failures):
            raise unittest.SkipTest(
                "uv could not prepare the Python runtime for infographic export:\n"
                f"{result.stderr}"
            )
        raise AssertionError(
            "infographic export failed\n"
            f"command: {' '.join(result.args)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def row_share(row: dict):
    return row.get("share_of_backing", row.get("share_of_individual", "absent"))


@unittest.skipUnless(DB.exists(), "local DuckDB artifact is required")
@unittest.skipUnless(shutil.which("uv"), "uv is required to run the script")
class InfographicExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "out"
        export(cls.out)
        cls.files = sorted(cls.out.glob("*.json"))
        cls.docs = [json.loads(f.read_text()) for f in cls.files]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_files_written_and_named(self) -> None:
        self.assertTrue(self.files, "no angle files were written")
        self.assertTrue({f.stem for f in self.files} <= PARTITION_ANGLES)
        for f in self.files:
            self.assertRegex(f.name, r"^[a-z][a-z-]*\.json$")

    def test_each_file_has_required_schema(self) -> None:
        for f, d in zip(self.files, self.docs):
            with self.subTest(file=f.name):
                self.assertTrue(REQUIRED_TOP <= d.keys(),
                                f"missing top-level keys: {REQUIRED_TOP - d.keys()}")
                self.assertTrue(REQUIRED_ANGLE <= d["angle"].keys(),
                                f"missing angle keys: {REQUIRED_ANGLE - d['angle'].keys()}")
                self.assertIn("cand_id", d["candidate"])
                self.assertIn("name", d["candidate"])
                self.assertIsInstance(d["angle"]["featured"], bool)
                self.assertIsInstance(d["angle"]["metric_value"], (int, float))
                self.assertIsInstance(d["footnotes"], list)
                self.assertTrue(d["source"])
                chart = d["chart"]
                self.assertIn("denominator", chart)
                self.assertIsInstance(chart["rows"], list)
                self.assertTrue(chart["rows"], "chart has no rows")
                # Filename number prefix must match the angle id.
                self.assertTrue(f.name.endswith(f"{d['angle']['id']}.json"))

    def test_partition_charts_reconcile_to_denominator(self) -> None:
        seen = set()
        for f, d in zip(self.files, self.docs):
            aid = d["angle"]["id"]
            if aid not in PARTITION_ANGLES:
                continue
            seen.add(aid)
            rows = d["chart"]["rows"]
            total = sum(r["amount"] for r in rows
                        if r.get("amount") is not None and row_share(r) is not None)
            denom = d["chart"]["denominator"]["amount"]
            self.assertAlmostEqual(
                total, denom, delta=1.0,
                msg=f"{f.name}: rows ({total}) != denominator ({denom})")
        self.assertTrue(seen, "expected at least one partition chart to be emitted")

    def test_export_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            other = Path(td) / "out2"
            export(other)
            names_a = {f.name for f in self.files}
            names_b = {f.name for f in other.glob("*.json")}
            self.assertEqual(names_a, names_b, "different files across runs")
            for name in names_a:
                self.assertEqual((self.out / name).read_text(),
                                 (other / name).read_text(),
                                 f"{name} differs between runs")


if __name__ == "__main__":
    unittest.main()
