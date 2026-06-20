from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_ingest():
    sys.modules.setdefault("duckdb", types.SimpleNamespace(DuckDBPyConnection=object))
    sys.modules.setdefault("requests", types.SimpleNamespace(HTTPError=Exception))
    spec = importlib.util.spec_from_file_location("ingest", ROOT / "bin" / "ingest.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class IngestHelperTests(unittest.TestCase):
    def test_sha256_file(self) -> None:
        ingest = load_ingest()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.txt"
            path.write_text("abc")
            self.assertEqual(ingest.sha256_file(path), hashlib.sha256(b"abc").hexdigest())

    def test_validate_cached_file_rejects_size_mismatch(self) -> None:
        ingest = load_ingest()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.txt"
            path.write_text("abc")
            with self.assertRaises(RuntimeError):
                ingest.validate_cached_file(path, {"content_length": 4})


if __name__ == "__main__":
    unittest.main()
