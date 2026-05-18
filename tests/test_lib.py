import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import _lib


class ConfigTests(unittest.TestCase):
    def test_load_missing_returns_empty(self):
        with TemporaryDirectory() as d:
            cfg = _lib.load_config(Path(d) / "charts.json")
            self.assertEqual(cfg, {"charts": {}})

    def test_round_trip(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "charts.json"
            original = {
                "charts": {
                    "istio-ingressgateway": {
                        "repo": "https://example.com/charts",
                        "name": "gateway",
                        "version": "1.21.0",
                    }
                }
            }
            _lib.save_config(p, original)
            loaded = _lib.load_config(p)
            self.assertEqual(loaded, original)

    def test_save_is_pretty_printed(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "charts.json"
            _lib.save_config(p, {"charts": {"x": {"a": "b"}}})
            text = p.read_text()
            self.assertIn("\n", text)
            self.assertIn("  ", text)


if __name__ == "__main__":
    unittest.main()
