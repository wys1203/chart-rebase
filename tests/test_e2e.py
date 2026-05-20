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


if __name__ == "__main__":
    unittest.main()
