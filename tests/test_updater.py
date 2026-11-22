import os
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
        script.write_text('#!/bin/sh\ngit rev-parse HEAD >> ran\necho "$PREFIX" > prefix\n')
        script.chmod(0o755)
        self.commit(self.up, "first")
        git(self.tmp, "clone", "-q", str(self.origin), str(self.src))
        self.installed = git(self.src, "rev-parse", "HEAD")
        self.config = self.tmp / "config" / "openface-mixer"
        self.config.mkdir(parents=True)
        (self.config / "matrix.bin").write_bytes(b"mix")

    def commit(self, repo, msg, tag=None):
        (repo / "f").write_text(msg)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", msg)
        git(repo, "push", "-q", "origin", "main")
        if tag:
            self.tag(tag)
        return git(repo, "rev-parse", "HEAD")

    def tag(self, name, *at):
        git(self.up, "tag", "-f", name, *at)
        git(self.up, "push", "-qf", "origin", name)

    def update(self, releases=True):
        """Update the checkout as the app would; returns the commit it is on afterwards."""
        stamp = f"2026-01-01-{len(self.backups()):06d}"      # two updates within a second
        with mock.patch.object(updater, "CONFIG_DIR", self.config), \
                mock.patch.object(updater.time, "strftime", lambda _fmt: stamp):
            updater.update(self.src, releases)
        return git(self.src, "rev-parse", "HEAD")

    def installer_runs(self):
        ran = self.src / "ran"
        return ran.read_text().split() if ran.exists() else []

    def backups(self):
        return list(self.config.parent.glob("openface-mixer.backup-*"))

    def test_install_source(self):
        f = self.tmp / "install-source"
        with mock.patch.object(updater, "SOURCE_FILE", f):
            self.assertIsNone(updater.install_source())
            f.write_text(f"{self.src}\n{self.installed}\n")
            self.assertEqual(updater.install_source(), (self.src, self.installed))
            f.write_text(f"{self.tmp / 'gone'}\n{self.installed}\n")
            self.assertIsNone(updater.install_source())

    def test_packaged_install(self):
        f = self.tmp / "install-source"
        with mock.patch.object(updater, "SOURCE_FILE", f):
            self.assertFalse(updater.packaged())
            self.assertIn("git pull", updater.manual_hint())
            f.write_text("package\n")       # what scripts/install.sh writes with DESTDIR
            self.assertTrue(updater.packaged())
            self.assertIsNone(updater.install_source())
            self.assertIn("package manager", updater.manual_hint())
            f.write_text(f"{self.src}\n{self.installed}\n")
            self.assertFalse(updater.packaged())

    def test_check_and_update(self):
        self.assertEqual(updater.check(self.src, self.installed), (None, []))
        self.commit(self.up, "second")
        self.commit(self.up, "third")
        # no release yet: the default channel follows main, as before there were channels
        self.assertEqual(updater.check(self.src, self.installed), (None, ["third", "second"]))
        prefix = self.tmp / "prefix"
        with mock.patch.object(updater, "CONFIG_DIR", self.config), \
                mock.patch.object(updater, "SOURCE_FILE",
                                  prefix / "share" / "openface-mixer" / "install-source"):
            updater.update(self.src)
        head = git(self.src, "rev-parse", "HEAD")
        self.assertEqual(head, git(self.up, "rev-parse", "HEAD"))
        self.assertEqual(self.installer_runs(), [head])
        # reinstalled where the running copy is, not in the installer's default prefix
        self.assertEqual((self.src / "prefix").read_text().strip(), str(prefix))
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "matrix.bin").read_bytes(), b"mix")

    def test_a_release_ahead_of_the_install(self):
        release = self.commit(self.up, "second", tag="v0.4.0")
        newest = self.commit(self.up, "third")
        self.assertEqual(updater.check(self.src, self.installed), ("0.4.0", ["second"]))
        self.assertEqual(self.update(), release)
        self.assertEqual(self.installer_runs(), [release])
        # still on main, so the latest changes are one fast-forward away
        self.assertEqual(git(self.src, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(updater.check(self.src, release), ("0.4.0", []))
        self.assertEqual(updater.check(self.src, release, releases=False), (None, ["third"]))
        self.assertEqual(self.update(releases=False), newest)
        self.assertEqual(self.installer_runs(), [release, newest])

    def test_an_install_ahead_of_the_newest_release_stays_where_it_is(self):
        self.commit(self.up, "second", tag="v0.4.0")
        ahead = self.commit(self.up, "third")
        git(self.src, "pull", "-q")
        self.commit(self.up, "fourth")
        self.assertEqual(updater.check(self.src, ahead), ("0.4.0", []))
        self.assertEqual(self.update(), ahead)          # never backwards, to the release
        self.assertEqual(updater.check(self.src, ahead, releases=False), (None, ["fourth"]))
        self.commit(self.up, "fifth", tag="v0.5.0")
        self.assertEqual(updater.check(self.src, ahead), ("0.5.0", ["fifth", "fourth"]))

    def test_releases_are_compared_as_numbers(self):
        self.commit(self.up, "second", tag="v0.9.0")
        newest = self.commit(self.up, "third", tag="v0.10.0")
        for other in ("v0.11", "v1.0.0-rc1", "v2", "vnext", "0.12.0", "v0.10.0.1"):
            self.commit(self.up, f"not a release: {other}", tag=other)
        self.assertEqual(updater.check(self.src, self.installed), ("0.10.0", ["third", "second"]))
        self.assertEqual(self.update(), newest)
        # the highest number wins, not the tag made last
        self.tag("v0.2.5", self.installed)
        self.assertEqual(updater.check(self.src, newest), ("0.10.0", []))

    def test_an_annotated_release_tag(self):
        release = self.commit(self.up, "second")
        git(self.up, "tag", "-a", "-m", "Openface Mixer 1.0.0", "v1.0.0")
        git(self.up, "push", "-q", "origin", "v1.0.0")
        self.assertEqual(updater.check(self.src, self.installed), ("1.0.0", ["second"]))
        self.assertEqual(self.update(), release)
        self.assertEqual(updater.version_text((self.src, release)), "Version 1.0.0")

    def test_a_release_that_is_not_on_main_is_not_offered(self):
        git(self.up, "checkout", "-q", "-b", "side")
        (self.up / "g").write_text("side")
        git(self.up, "add", "-A")
        git(self.up, "commit", "-qm", "side work")
        git(self.up, "push", "-q", "origin", "side")
        self.tag("v9.0.0")
        git(self.up, "checkout", "-q", "main")
        newest = self.commit(self.up, "second")
        self.assertEqual(updater.check(self.src, self.installed), (None, ["second"]))
        self.assertEqual(self.update(), newest)

    def test_refuses_a_release_the_checkout_cannot_fast_forward_to(self):
        self.commit(self.up, "second", tag="v0.4.0")
        (self.src / "mine").write_text("mine")
        git(self.src, "add", "-A")
        git(self.src, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm",
            "my own change")
        mine = git(self.src, "rev-parse", "HEAD")
        self.assertEqual(updater.check(self.src, mine), ("0.4.0", ["second"]))
        for releases in (True, False):
            with self.assertRaisesRegex(updater.UpdateError, "commits of its own"):
                self.update(releases)
        self.assertEqual(git(self.src, "rev-parse", "HEAD"), mine)
        self.assertEqual(self.installer_runs(), [])
        self.assertEqual(self.backups(), [])

    def test_refuses_a_checkout_that_is_not_on_main(self):
        self.commit(self.up, "second", tag="v0.4.0")
        updater.check(self.src, self.installed)
        git(self.src, "checkout", "-q", "--detach")
        with self.assertRaisesRegex(updater.UpdateError, "not on the main branch"):
            self.update()
        self.assertEqual(self.installer_runs(), [])

    def test_a_moved_release_tag_is_followed(self):
        self.commit(self.up, "second", tag="v0.4.0")
        self.assertEqual(updater.check(self.src, self.installed), ("0.4.0", ["second"]))
        fixed = self.commit(self.up, "third", tag="v0.4.0")     # tagged again after a fix
        self.assertEqual(updater.check(self.src, self.installed), ("0.4.0", ["third", "second"]))
        self.assertEqual(self.update(), fixed)

    def test_a_check_keeps_the_tags_that_are_only_in_the_checkout(self):
        for setting in ("fetch.prune", "fetch.pruneTags", "remote.origin.prune"):
            git(self.src, "config", setting, "true")    # git would delete them for these
        git(self.src, "tag", "v0.5.0")                  # made here and not pushed yet
        git(self.src, "tag", "mine")
        self.commit(self.up, "second", tag="v0.4.0")
        updater.check(self.src, self.installed)
        self.assertEqual(git(self.src, "tag").split(), ["mine", "v0.4.0", "v0.5.0"])

    def test_a_quiet_check_never_asks_and_fails_as_an_update_error(self):
        # an ssh remote and a stand-in ssh that notes how it was called and can't connect
        ssh, seen = self.tmp / "ssh", self.tmp / "seen"
        ssh.write_text(f'#!/bin/sh\n{{ echo "$@"; env; }} >> "{seen}"\nexit 255\n')
        ssh.chmod(0o755)
        git(self.src, "remote", "set-url", "origin", "ssh://git@example.invalid/mixer.git")
        with mock.patch.dict(os.environ, GIT_SSH_COMMAND=str(ssh)):
            with self.assertRaises(updater.UpdateError):
                updater.check(self.src, self.installed)         # the button: may ask
            self.assertNotIn("BatchMode", seen.read_text())
            seen.unlink()
            with self.assertRaises(updater.UpdateError):        # what the panel keeps quiet
                updater.check(self.src, self.installed, quiet=True)
        seen = seen.read_text().splitlines()
        self.assertIn("-oBatchMode=yes", seen[0])
        self.assertIn("GIT_TERMINAL_PROMPT=0", seen)
        self.assertIn("GIT_ASKPASS=", seen)
        # a checkout that is gone
        with self.assertRaises(updater.UpdateError):
            updater.check(self.tmp / "gone", self.installed, quiet=True)

    def test_version_text(self):
        from openface_mixer import __version__
        self.assertEqual(updater.version_text(None), f"Version {__version__}")
        short = self.installed[:7]
        text = updater.version_text((self.src, self.installed))
        self.assertEqual(text, f"Version {__version__} ({short})")     # no release yet
        release = self.commit(self.up, "second", tag="v0.4.0")
        one = self.commit(self.up, "third")
        two = self.commit(self.up, "fourth")
        self.tag("v0.4.1-rc1")
        git(self.src, "pull", "-q", "--tags")
        self.assertEqual(text, updater.version_text((self.src, self.installed)))
        self.assertEqual(updater.version_text((self.src, release)), "Version 0.4.0")
        self.assertEqual(updater.version_text((self.src, one)),
                         f"Version 0.4.0 + 1 change ({one[:7]})")
        self.assertEqual(updater.version_text((self.src, two)),
                         f"Version 0.4.0 + 2 changes ({two[:7]})")
        # a commit the checkout no longer has, and a folder that is no checkout
        self.assertEqual(updater.version_text((self.src, "0" * 40)),
                         f"Version {__version__} (0000000)")
        self.assertEqual(updater.version_text((self.tmp / "gone", self.installed)),
                         f"Version {__version__} ({short})")

    def test_only_the_newest_three_backups_are_kept(self):
        parent = self.config.parent
        stamps = ["2026-01-05-120000", "2025-12-31-235959", "2026-03-01-080000",
                  "2026-02-10-101010"]
        for stamp in stamps:
            (parent / f"openface-mixer.backup-{stamp}").mkdir()
        # not named like a backup: never touched
        others = [parent / "openface-mixer.backup-mine", parent / "openface-mixer.backup-2026",
                  parent / "other.backup-2020-01-01-000000", parent / "openface-mixer.backup"]
        for other in others:
            other.mkdir()
        with mock.patch.object(updater, "CONFIG_DIR", self.config), \
                mock.patch.object(updater.time, "strftime", lambda _fmt: "2026-11-16-170000"):
            new = updater.backup_config()
        self.assertEqual((new / "matrix.bin").read_bytes(), b"mix")
        kept = sorted(p.name.rpartition("backup-")[2] for p in parent.iterdir()
                      if p.name.startswith("openface-mixer.backup-2") and len(p.name) > 30)
        self.assertEqual(kept, ["2026-02-10-101010", "2026-03-01-080000", "2026-11-16-170000"])
        self.assertTrue(all(other.is_dir() for other in others))
        self.assertTrue(self.config.is_dir())

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
