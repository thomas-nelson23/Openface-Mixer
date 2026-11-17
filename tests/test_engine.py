import unittest
from unittest import mock

from openface_mixer import engine
from openface_mixer.devices import DEVICES


class ServiceTest(unittest.TestCase):
    """Starting an engine does not enable it: that waits until the interface has been seen."""

    def run_recorded(self, call):
        calls = []

        def run(cmd, **_kw):
            calls.append(cmd)
            return mock.Mock(returncode=0)
        with mock.patch.object(engine.subprocess, "run", run):
            call()
        return [c[2:] for c in calls if c[:2] == ["systemctl", "--user"]]

    def test_start_does_not_enable(self):
        for key, unit in (("digiface", "openface-mixer-engine.service"),
                          ("ff802", "openface-mixer-engine@ff802.service")):
            e = engine.Engine(DEVICES[key])
            self.assertEqual(self.run_recorded(e.start), [["cat", unit], ["start", unit]])
            self.assertEqual(self.run_recorded(e.enable), [["enable", unit]])

    def test_enable_without_systemd(self):
        for error in (FileNotFoundError, engine.subprocess.TimeoutExpired("systemctl", 5)):
            with mock.patch.object(engine.subprocess, "run", side_effect=error):
                engine.Engine(DEVICES["digiface"]).enable()


if __name__ == "__main__":
    unittest.main()
