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


class IndexReaderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = (
            Path(__file__).resolve().parent / "fixtures" / "istio_index.yaml"
        ).read_text()

    def test_list_versions_for_gateway(self):
        versions = _lib.list_versions(self.fixture, "gateway")
        self.assertEqual(
            versions,
            [
                ("1.22.0", "https://example.com/charts/gateway-1.22.0.tgz"),
                ("1.21.0", "https://example.com/charts/gateway-1.21.0.tgz"),
                ("1.20.3", "relative/gateway-1.20.3.tgz"),
            ],
        )

    def test_list_versions_for_ambient(self):
        versions = _lib.list_versions(self.fixture, "ambient")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0][0], "1.30.0-rc.0")

    def test_list_versions_unknown_chart_returns_empty(self):
        self.assertEqual(_lib.list_versions(self.fixture, "nope"), [])

    def test_list_versions_detailed_for_gateway(self):
        versions = _lib.list_versions_detailed(self.fixture, "gateway")
        self.assertEqual(
            [(v.version, v.app_version, v.url) for v in versions],
            [
                ("1.22.0", "1.22.0", "https://example.com/charts/gateway-1.22.0.tgz"),
                ("1.21.0", "1.21.0", "https://example.com/charts/gateway-1.21.0.tgz"),
                ("1.20.3", "1.20.3", "relative/gateway-1.20.3.tgz"),
            ],
        )

    def test_list_versions_detailed_for_ambient(self):
        versions = _lib.list_versions_detailed(self.fixture, "ambient")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].version, "1.30.0-rc.0")
        self.assertEqual(versions[0].app_version, "1.30.0-rc.0")


class UrlResolutionTests(unittest.TestCase):
    def test_absolute_url_passes_through(self):
        self.assertEqual(
            _lib.resolve_url("https://example.com/charts", "https://other.com/x.tgz"),
            "https://other.com/x.tgz",
        )

    def test_relative_url_joins_with_repo(self):
        self.assertEqual(
            _lib.resolve_url("https://example.com/charts", "x-1.0.0.tgz"),
            "https://example.com/charts/x-1.0.0.tgz",
        )

    def test_relative_url_strips_trailing_slash_from_repo(self):
        self.assertEqual(
            _lib.resolve_url("https://example.com/charts/", "x-1.0.0.tgz"),
            "https://example.com/charts/x-1.0.0.tgz",
        )


class WorkspaceTests(unittest.TestCase):
    def test_creates_cache_and_work_dirs(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _lib.ensure_workspace(root)
            self.assertTrue((root / ".cache").is_dir())
            self.assertTrue((root / ".work").is_dir())

    def test_creates_gitignore_when_missing(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _lib.ensure_workspace(root)
            gi = (root / ".gitignore").read_text()
            self.assertIn(".cache/", gi)
            self.assertIn(".work/", gi)
            self.assertIn("__pycache__/", gi)

    def test_appends_missing_entries_to_existing_gitignore(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / ".gitignore").write_text("node_modules/\n")
            _lib.ensure_workspace(root)
            gi = (root / ".gitignore").read_text()
            self.assertIn("node_modules/", gi)
            self.assertIn(".cache/", gi)
            self.assertIn(".work/", gi)

    def test_does_not_duplicate_existing_entries(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / ".gitignore").write_text(".cache/\n.work/\n__pycache__/\n")
            _lib.ensure_workspace(root)
            gi = (root / ".gitignore").read_text()
            self.assertEqual(gi.count(".cache/"), 1)

    def test_appends_to_gitignore_without_trailing_newline(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / ".gitignore").write_text("node_modules/")
            _lib.ensure_workspace(root)
            lines = (root / ".gitignore").read_text().splitlines()
            self.assertIn("node_modules/", lines)
            self.assertIn(".cache/", lines)
            self.assertIn(".work/", lines)


class DiffScoreTests(unittest.TestCase):
    def test_identical_trees_score_zero(self):
        with TemporaryDirectory() as d:
            a = Path(d) / "a"
            b = Path(d) / "b"
            a.mkdir(); b.mkdir()
            (a / "f.txt").write_text("hello\nworld\n")
            (b / "f.txt").write_text("hello\nworld\n")
            score = _lib.diff_score(a, b)
            self.assertEqual(score.total_lines, 0)
            self.assertEqual(score.changed_files, 0)

    def test_one_modified_file(self):
        with TemporaryDirectory() as d:
            a = Path(d) / "a"
            b = Path(d) / "b"
            a.mkdir(); b.mkdir()
            (a / "f.txt").write_text("hello\nworld\n")
            (b / "f.txt").write_text("hello\nthere\n")
            score = _lib.diff_score(a, b)
            self.assertGreater(score.total_lines, 0)
            self.assertEqual(score.changed_files, 1)

    def test_file_only_in_one_side(self):
        with TemporaryDirectory() as d:
            a = Path(d) / "a"
            b = Path(d) / "b"
            a.mkdir(); b.mkdir()
            (a / "only.txt").write_text("a\nb\nc\n")
            score = _lib.diff_score(a, b)
            self.assertEqual(score.changed_files, 1)
            self.assertGreaterEqual(score.total_lines, 3)


if __name__ == "__main__":
    unittest.main()
