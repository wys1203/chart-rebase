"""End-to-end tests driving the real `make` targets against vendored fixtures.

Each test builds a throwaway git repo, copies the real Makefile + scripts/ into
it, and runs `make` commands with that repo as the working directory. Charts
come from tests/fixtures/e2e-repo served over file:// URLs — no network.
"""

import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_REPO = Path(__file__).resolve().parent / "fixtures" / "e2e-repo"
REPO_URL = "file://" + str(FIXTURE_REPO)


class E2ETests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="chart-rebase-e2e-")
        self.scratch = Path(tmp)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # Copy the real Makefile + scripts so `make` runs with scratch as cwd.
        shutil.copy(REPO_ROOT / "Makefile", self.scratch / "Makefile")
        shutil.copytree(REPO_ROOT / "scripts", self.scratch / "scripts")
        self._git("init", "-q")
        self._git("config", "user.email", "e2e@example.com")
        self._git("config", "user.name", "e2e")
        self._git("config", "commit.gpgsign", "false")

    def _git(self, *args):
        """Run a git command in the scratch repo; fail the test on non-zero exit."""
        return subprocess.run(
            ["git", "-C", str(self.scratch), *args],
            capture_output=True, text=True, check=True,
        )

    def _run_make(self, target, **variables):
        """Run `make <target> VAR=val ...` with the scratch repo as cwd."""
        cmd = ["make", target] + [f"{k}={v}" for k, v in variables.items()]
        return subprocess.run(
            cmd, cwd=str(self.scratch), capture_output=True, text=True,
        )

    def _seed_gateway(self, version):
        """Extract the fixture chart for `version` into scratch/gateway/, commit it."""
        tgz = FIXTURE_REPO / f"gateway-{version}.tgz"
        with tarfile.open(tgz, "r:gz") as tf:
            tf.extractall(self.scratch)
        assert (self.scratch / "gateway").is_dir(), \
            f"fixture tarball for {version} did not extract to gateway/"
        self._git("add", "gateway")
        self._git("commit", "-q", "-m", f"import gateway {version}")

    def _adopt_1_12_9(self):
        """Seed gateway 1.12.9, run `make adopt`, commit charts.json."""
        self._seed_gateway("1.12.9")
        # Explicit VERSION takes adopt.py's non-interactive path (no input() prompt).
        adopt = self._run_make("adopt", CHART="gateway", REPO=REPO_URL,
                               VERSION="1.12.9")
        self.assertEqual(adopt.returncode, 0, adopt.stderr)
        self._git("add", "charts.json")
        self._git("commit", "-q", "-m", "adopt gateway")

    def test_versions(self):
        # REPO/NAME form against the fixture repo
        r = self._run_make("versions", REPO=REPO_URL, NAME="gateway")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1.12.9", r.stdout)
        self.assertIn("1.24.6", r.stdout)

        # CHART form after adopt resolves repo/name from charts.json
        self._adopt_1_12_9()
        r2 = self._run_make("versions", CHART="gateway")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("1.12.9", r2.stdout)
        self.assertIn("1.24.6", r2.stdout)

    def _apply_clean_mods(self):
        """Apply local modifications that 3-way-merge cleanly onto 1.24.6.

        - A brand-new template file: a new file can never produce a conflict.
        - A helper define prepended to _helpers.tpl: lines 1-8 of that file are
          byte-identical in gateway 1.12.9 and 1.24.6, so a prepend is clean.
        """
        extra = self.scratch / "gateway" / "templates" / "e2e-extra.yaml"
        extra.write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: e2e-extra-marker\n"
            "data:\n"
            "  source: chart-rebase-e2e\n"
        )
        helpers = self.scratch / "gateway" / "templates" / "_helpers.tpl"
        helpers.write_text(
            '{{- define "gateway.e2eMarker" -}}\n'
            "chart-rebase-e2e\n"
            "{{- end }}\n\n"
            + helpers.read_text()
        )

    def test_clean_rebase_workflow(self):
        self._adopt_1_12_9()
        self.assertIn("vendor/gateway/1.12.9",
                      self._git("tag", "-l").stdout.split())

        self._apply_clean_mods()
        self._git("add", "gateway")
        self._git("commit", "-q", "-m", "local mods")

        diff = self._run_make("diff", CHART="gateway")
        self.assertEqual(diff.returncode, 0, diff.stderr)
        self.assertIn("e2e-extra.yaml", diff.stdout)

        patch = self._run_make("patch", CHART="gateway")
        self.assertEqual(patch.returncode, 0, patch.stderr)
        self.assertIn("e2e-extra.yaml", patch.stdout)

        status = self._run_make("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("gateway", status.stdout)
        self.assertIn("1.12.9", status.stdout)

        rebase = self._run_make("rebase", CHART="gateway", VERSION="1.24.6")
        self.assertEqual(rebase.returncode, 0, rebase.stderr)
        self.assertNotIn("CONFLICT", rebase.stdout)
        self.assertIn("vendor/gateway/1.24.6",
                      self._git("tag", "-l").stdout.split())

        finish = self._run_make("finish-rebase", CHART="gateway")
        self.assertEqual(finish.returncode, 0, finish.stderr)

        gw = self.scratch / "gateway"
        # local mods survived onto the new base
        self.assertTrue((gw / "templates" / "e2e-extra.yaml").exists())
        self.assertIn("gateway.e2eMarker",
                      (gw / "templates" / "_helpers.tpl").read_text())
        # content unique to upstream 1.24.6 is present (base advanced)
        self.assertTrue((gw / "templates" / "poddisruptionbudget.yaml").exists())
        # no conflict markers anywhere
        for p in gw.rglob("*"):
            if p.is_file():
                self.assertNotIn("<<<<<<< ", p.read_text(errors="replace"))
        # charts.json bumped to the new version
        cfg = json.loads((self.scratch / "charts.json").read_text())
        self.assertEqual(cfg["charts"]["gateway"]["version"], "1.24.6")

    def test_abort_rebase(self):
        self._adopt_1_12_9()
        self._apply_clean_mods()
        self._git("add", "gateway")
        self._git("commit", "-q", "-m", "local mods")

        rebase = self._run_make("rebase", CHART="gateway", VERSION="1.24.6")
        self.assertEqual(rebase.returncode, 0, rebase.stderr)

        abort = self._run_make("abort-rebase", CHART="gateway")
        self.assertEqual(abort.returncode, 0, abort.stderr)

        gw = self.scratch / "gateway"
        # committed local mods restored
        self.assertTrue((gw / "templates" / "e2e-extra.yaml").exists())
        # upstream-1.24.6-only content removed
        self.assertFalse((gw / "templates" / "poddisruptionbudget.yaml").exists())
        # in-progress vendor tag dropped
        self.assertNotIn("vendor/gateway/1.24.6",
                         self._git("tag", "-l").stdout.split())
        # charts.json rolled back to the base version
        cfg = json.loads((self.scratch / "charts.json").read_text())
        self.assertEqual(cfg["charts"]["gateway"]["version"], "1.12.9")
        # working tree clean with respect to gateway/
        porcelain = self._git("status", "--porcelain", "--", "gateway").stdout
        self.assertEqual(porcelain.strip(), "")

    def test_conflict_rebase(self):
        self._adopt_1_12_9()

        # Conflicting mod: edit values.yaml line 2 (`name: ""`). The whole top
        # of values.yaml is restructured in 1.24.6, so this region cannot
        # 3-way-merge — the rebase must report a conflict.
        values = self.scratch / "gateway" / "values.yaml"
        lines = values.read_text().splitlines(keepends=True)
        self.assertEqual(lines[1], 'name: ""\n')  # pin the fixture assumption
        lines[1] = 'name: "e2e-gateway"\n'
        values.write_text("".join(lines))
        self._git("add", "gateway")
        self._git("commit", "-q", "-m", "local mod: gateway name")

        rebase = self._run_make("rebase", CHART="gateway", VERSION="1.24.6")
        self.assertEqual(rebase.returncode, 0, rebase.stderr)
        self.assertIn("CONFLICT", rebase.stdout)
        self.assertIn("<<<<<<< ", values.read_text())

        # finish-rebase must refuse while conflict markers remain
        refused = self._run_make("finish-rebase", CHART="gateway")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("conflict", refused.stderr.lower())

        # Resolve by taking the upstream 1.24.6 values.yaml verbatim
        with tarfile.open(FIXTURE_REPO / "gateway-1.24.6.tgz", "r:gz") as tf:
            resolved = tf.extractfile("gateway/values.yaml").read().decode()
        values.write_text(resolved)

        finish = self._run_make("finish-rebase", CHART="gateway")
        self.assertEqual(finish.returncode, 0, finish.stderr)
        self.assertNotIn("<<<<<<< ", values.read_text())


if __name__ == "__main__":
    unittest.main()
