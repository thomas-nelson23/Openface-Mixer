import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import updater


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


class UpdaterTest(unittest.TestCase):
    """A bare 'GitHub' repo, a checkout of it, and a stand-in installer that records its runs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.origin, self.src, self.up = self.tmp / "origin.git", self.tmp / "src", self.tmp / "up"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(self.tmp, "clone", "-q", str(self.origin), str(self.up))
        git(self.up, "checkout", "-q", "-B", "main")
        git(self.up, "config", "user.email", "t@example.com")
        git(self.up, "config", "user.name", "t")
        script = self.up / "scripts" / "install.sh"
        script.parent.mkdir()
        script.write_text('#!/bin/sh\ngit rev-parse HEAD >> ran\n')
        script.chmod(0o755)
        self.commit(self.up, "first")
        git(self.tmp, "clone", "-q", str(self.origin), str(self.src))
        self.installed = git(self.src, "rev-parse", "HEAD")
        self.config = self.tmp / "config" / "openface-mixer"
        self.config.mkdir(parents=True)
        (self.config / "matrix.bin").write_bytes(b"mix")

    def commit(self, repo, msg):
        (repo / "f").write_text(msg)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", msg)
        git(repo, "push", "-q", "origin", "main")

    def test_install_source(self):
        f = self.tmp / "install-source"
        with mock.patch.object(updater, "SOURCE_FILE", f):
            self.assertIsNone(updater.install_source())
            f.write_text(f"{self.src}\n{self.installed}\n")
            self.assertEqual(updater.install_source(), (self.src, self.installed))
            f.write_text(f"{self.tmp / 'gone'}\n{self.installed}\n")
            self.assertIsNone(updater.install_source())

    def test_check_and_update(self):
        self.assertEqual(updater.check(self.src, self.installed), [])
        self.commit(self.up, "second")
        self.commit(self.up, "third")
        self.assertEqual(updater.check(self.src, self.installed), ["third", "second"])
        with mock.patch.object(updater, "CONFIG_DIR", self.config):
            updater.update(self.src)
        head = git(self.src, "rev-parse", "HEAD")
        self.assertEqual(head, git(self.up, "rev-parse", "HEAD"))
        self.assertEqual((self.src / "ran").read_text().split(), [head])
        backups = list(self.config.parent.glob("openface-mixer.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "matrix.bin").read_bytes(), b"mix")

    def test_refuses_local_changes(self):
        (self.src / "f").write_text("edited")
        with self.assertRaises(updater.UpdateError):
            updater.update(self.src)

    def test_installer_failure(self):
        script = self.up / "scripts" / "install.sh"
        script.write_text("#!/bin/sh\necho broken; exit 1\n")
        self.commit(self.up, "break installer")
        updater.check(self.src, self.installed)
        with mock.patch.object(updater, "CONFIG_DIR", self.config), \
                self.assertRaisesRegex(updater.UpdateError, "broken"):
            updater.update(self.src)


if __name__ == "__main__":
    unittest.main()
