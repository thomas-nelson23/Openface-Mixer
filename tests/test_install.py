"""scripts/install.sh and uninstall.sh, run for real in a throwaway HOME.

systemctl, pkill, sudo and pacman are stand-ins first on PATH: they log their calls and keep
which units are enabled and running in two files, so the tests see what the scripts would do to
the user's services without touching the machine. A command the scripts start to use that acts
on the system needs a stand-in here too.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from openface_mixer.devices import DIGIFACE_USB_PIDS, RME_USB_VENDOR, usb_devices

REPO = Path(__file__).resolve().parent.parent
DIGIFACE = "openface-mixer-engine.service"
FF802 = "openface-mixer-engine@ff802.service"
SYSTEMCTL = r"""#!/bin/bash
echo "systemctl $*" >> "$STUB/log"
for unit; do :; done                    # the last argument
case " $* " in
    *" is-enabled "*) grep -qx "$unit" "$STUB/enabled" ;;
    *" list-units "*) grep -- "${unit%\*}" "$STUB/running" | sed 's/$/ loaded active running/' ;;
    *" enable "*) echo "$unit" >> "$STUB/enabled" ;;
    *" disable "*) sed -i "/^$unit\$/d" "$STUB/enabled" "$STUB/running" ;;
esac
"""
STUBS = {"systemctl": SYSTEMCTL,
         "pkill": '#!/bin/sh\necho "pkill $*" >> "$STUB/log"\n',
         "sudo": '#!/bin/sh\necho "sudo $*" >> "$STUB/log"\nexit 1\n',
         "pacman": "#!/bin/sh\nexit 0\n",
         "gtk-update-icon-cache": "#!/bin/sh\nexit 0\n"}       # would add a cache file


def digiface_connected():
    return any(pid in DIGIFACE_USB_PIDS for pid, _ in usb_devices(RME_USB_VENDOR))


class InstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if subprocess.run(["make", "engine"], cwd=REPO, capture_output=True).returncode:
            raise unittest.SkipTest("the engine does not build here")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home, self.stub = self.tmp / "home", self.tmp / "stub"
        self.config = self.home / ".config"
        self.stub.mkdir()
        for name, text in STUBS.items():
            (self.stub / name).write_text(text)
            (self.stub / name).chmod(0o755)
        self.set_units(enabled=(), running=())

    def set_units(self, enabled, running):
        (self.stub / "enabled").write_text("".join(f"{u}\n" for u in enabled))
        (self.stub / "running").write_text("".join(f"{u}\n" for u in running))

    def run_script(self, name, **env):
        """Run a script; returns its output and the stand-ins' calls since the last run."""
        log = self.stub / "log"
        log.write_text("")
        env = {"HOME": str(self.home), "STUB": str(self.stub),
               "PATH": f"{self.stub}:{os.environ['PATH']}", **env}
        r = subprocess.run(["bash", str(REPO / "scripts" / name)], env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 0, r.stdout)
        return r.stdout, log.read_text().splitlines()

    def files(self, root):
        return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                      if not p.is_dir() and "openface_mixer" not in p.parts)

    def test_staged_install_for_a_package(self):
        pkg = self.tmp / "pkg"
        _, calls = self.run_script("install.sh", DESTDIR=str(pkg), PREFIX="/usr")
        self.assertEqual(self.files(pkg), [
            "usr/bin/openface-mixer", "usr/bin/openface-mixer-engine",
            "usr/lib/systemd/user/openface-mixer-engine.service",
            "usr/lib/systemd/user/openface-mixer-engine@.service",
            "usr/lib/udev/rules.d/70-rme-digiface.rules",
            "usr/lib/udev/rules.d/70-rme-fireface.rules",
            "usr/share/applications/openface-mixer.desktop",
            "usr/share/icons/hicolor/scalable/apps/openface-mixer.svg",
            "usr/share/openface-mixer/install-source"])
        usr = pkg / "usr"
        self.assertTrue((usr / "share/openface-mixer/openface_mixer/app.py").is_file())
        self.assertFalse(list(usr.rglob("__pycache__")))
        self.assertEqual((usr / "share/openface-mixer/install-source").read_text(), "package\n")
        self.assertIn('\nExec="/usr/bin/openface-mixer"\n',
                      (usr / "share/applications/openface-mixer.desktop").read_text())
        self.assertIn('\nExecStart="/usr/bin/openface-mixer-engine"\n',
                      (usr / "lib/systemd/user/openface-mixer-engine.service").read_text())
        self.assertIn('\nExecStart="/usr/bin/openface-mixer-engine" --device %i\n',
                      (usr / "lib/systemd/user/openface-mixer-engine@.service").read_text())
        self.assertIn('PYTHONPATH="/usr/share/openface-mixer$',
                      (usr / "bin/openface-mixer").read_text())
        # only files are staged: no services, no root, nothing in the builder's home
        self.assertEqual(calls, [])
        self.assertFalse(self.home.exists())

    def test_user_install_then_uninstall(self):
        prefix = self.home / "my apps"            # a prefix other than ~/.local, with a space
        _, calls = self.run_script("install.sh", PREFIX=str(prefix))
        units = self.config / "systemd/user"
        self.assertEqual(self.files(self.home), [
            ".config/systemd/user/openface-mixer-engine.service",
            ".config/systemd/user/openface-mixer-engine@.service",
            "my apps/bin/openface-mixer", "my apps/bin/openface-mixer-engine",
            "my apps/share/applications/openface-mixer.desktop",
            "my apps/share/icons/hicolor/scalable/apps/openface-mixer.svg",
            "my apps/share/openface-mixer/install-source"])
        self.assertIn(f'\nExec="{prefix}/bin/openface-mixer"\n',
                      (prefix / "share/applications/openface-mixer.desktop").read_text())
        self.assertIn(f'\nExecStart="{prefix}/bin/openface-mixer-engine" --device %i\n',
                      (units / "openface-mixer-engine@.service").read_text())
        source = (prefix / "share/openface-mixer/install-source").read_text().splitlines()
        if (REPO / ".git").exists():
            self.assertEqual(source[0], str(REPO))
        # engines are stopped by the 15 characters the kernel keeps of their name
        self.assertIn(f"pkill -u {os.getuid()} -x openface-mixer-", calls)
        # a fresh install enables the Digiface engine only with a Digiface connected
        self.assertEqual(f"systemctl --user enable {DIGIFACE}" in calls, digiface_connected())
        self.assertEqual(f"systemctl --user restart {DIGIFACE}" in calls, digiface_connected())

        rules = [r for r in ("70-rme-digiface.rules", "70-rme-fireface.rules")
                 if Path("/etc/udev/rules.d", r).exists()]
        if rules and os.access("/etc/udev/rules.d", os.W_OK):
            self.skipTest("uninstall.sh would remove this machine's udev rules")
        autostart = self.config / "autostart"
        autostart.mkdir()
        for name in ("openface-mixer-digiface.desktop", "openface-mixer-ff802.desktop",
                     "another-app.desktop"):
            (autostart / name).write_text("[Desktop Entry]\n")
        mix = self.config / "openface-mixer" / "state.json"
        mix.parent.mkdir()
        mix.write_text("{}")
        self.set_units(enabled=(DIGIFACE, FF802), running=(DIGIFACE, FF802))
        out, calls = self.run_script("uninstall.sh", PREFIX=str(prefix))
        self.assertEqual(self.files(self.home), [".config/autostart/another-app.desktop",
                                                 ".config/openface-mixer/state.json"])
        for unit in (DIGIFACE, FF802):
            self.assertIn(f"systemctl --user disable --now {unit}", calls)
        self.assertIn(f"pkill -u {os.getuid()} -x openface-mixer-", calls)
        self.assertFalse([c for c in calls if c.startswith("sudo")])
        self.assertEqual("sudo rm" in out, bool(rules))      # they are root's: it says how

    def test_update_keeps_a_digiface_users_engine(self):
        """Enabled before, a Digiface mix saved: it stays enabled and is restarted once, with
        the Digiface plugged in or not."""
        mix = self.config / "openface-mixer" / "state.json"
        mix.parent.mkdir(parents=True)
        mix.write_text("{}")
        self.set_units(enabled=(DIGIFACE,), running=(DIGIFACE, FF802))
        _, calls = self.run_script("install.sh")
        self.assertFalse([c for c in calls if " disable " in c])
        self.assertEqual(calls.count(f"systemctl --user restart {DIGIFACE}"), 1)
        self.assertEqual(calls.count(f"systemctl --user restart {FF802}"), 1)
        # enabled but not running (it had stopped): started again all the same
        self.set_units(enabled=(DIGIFACE,), running=())
        _, calls = self.run_script("install.sh")
        self.assertFalse([c for c in calls if " disable " in c])
        self.assertEqual(calls.count(f"systemctl --user restart {DIGIFACE}"), 1)

    def test_update_switches_off_a_digiface_engine_nobody_used(self):
        """Older installs enabled the Digiface engine for everyone. With no Digiface connected
        and no Digiface mix ever saved, the update switches it off and leaves the rest."""
        if digiface_connected():
            self.skipTest("a Digiface is connected")
        other = self.config / "openface-mixer" / "ff802" / "state.json"
        other.parent.mkdir(parents=True)
        other.write_text("{}")
        self.set_units(enabled=(DIGIFACE, FF802), running=(DIGIFACE, FF802))
        _, calls = self.run_script("install.sh")
        self.assertIn(f"systemctl --user disable --now {DIGIFACE}", calls)
        self.assertNotIn(f"systemctl --user restart {DIGIFACE}", calls)
        self.assertEqual(calls.count(f"systemctl --user restart {FF802}"), 1)


if __name__ == "__main__":
    unittest.main()
