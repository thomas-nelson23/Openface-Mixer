import re
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import engine
from openface_mixer.devices import DEVICES

LAYOUT = (Path(__file__).resolve().parent.parent / "engine" / "shm_layout.h").read_text()


class SharedMemoryTest(unittest.TestCase):
    """The GUI's side of engine/shm_layout.h, on a file standing in for the segment."""

    def segment(self, version=engine.SHM_VERSION):
        f = tempfile.NamedTemporaryFile()
        self.addCleanup(f.close)
        f.write(struct.pack("<2I", engine.SHM_MAGIC, version).ljust(engine.SHM_SIZE, b"\0"))
        f.flush()
        e = engine.Engine(DEVICES["digiface"])
        e.path = f.name
        self.addCleanup(lambda: e.m and e.m.close())
        return e

    def test_version_matches_the_engine(self):
        version = int(re.search(r"#define OFM_SHM_VERSION (\d+)", LAYOUT).group(1))
        self.assertEqual(engine.SHM_VERSION, version)
        # the header's words in order: the GUI's heartbeat is the first after dev_status2
        fields = re.findall(r"^\tuint32_t (\w+)", LAYOUT, re.M)
        self.assertEqual(fields.index("gui_heartbeat"), engine.HDR_GUI_HEARTBEAT)
        self.assertEqual(fields.index("dev_status2"), engine.HDR_DEV_STATUS2)

    def test_reading_the_meters_moves_the_heartbeat(self):
        e = self.segment()
        peak = engine.OFF_PEAK_SRC + 4 * 3
        self.assertTrue(e.attach())
        struct.pack_into("<f", e.m, peak, 0.5)
        src, _out = e.read_peaks()
        self.assertEqual(src[3], 0.5)
        self.assertEqual(e.header()[engine.HDR_GUI_HEARTBEAT], 1)
        src, _out = e.read_peaks()
        self.assertEqual(src[3], 0.0)               # the max-hold was reset
        self.assertEqual(e.header()[engine.HDR_GUI_HEARTBEAT], 2)
        struct.pack_into("<I", e.m, 4 * engine.HDR_GUI_HEARTBEAT, 0xFFFFFFFF)
        e.read_peaks()
        self.assertEqual(e.header()[engine.HDR_GUI_HEARTBEAT], 0)      # wraps, never overflows
        # nothing else in the header is the GUI's to write
        self.assertEqual(e.header()[:engine.HDR_GUI_HEARTBEAT],
                         (engine.SHM_MAGIC, engine.SHM_VERSION) + (0,) * 11)

    def test_an_engine_from_another_version(self):
        """During an update one side is older for a moment: the window shows the engine as
        stopped and asks for a restart, and what it writes stays inside the segment."""
        for version in (engine.SHM_VERSION - 1, engine.SHM_VERSION + 1):
            e = self.segment(version)
            self.assertTrue(e.outdated())
            self.assertIsNone(e.status())
            self.assertFalse(e.pid_alive())
            e.read_peaks()
            self.assertEqual(e.header()[engine.HDR_GUI_HEARTBEAT], 1)
            self.assertEqual(Path(e.path).stat().st_size, engine.SHM_SIZE)
        self.assertFalse(self.segment().outdated())


class ServiceTest(unittest.TestCase):
    """Starting an engine does not enable it: that waits until the interface has been seen."""

    def run_recorded(self, call, enabled=True):
        calls = []

        def run(cmd, **_kw):
            calls.append(cmd)
            return mock.Mock(returncode=int("is-enabled" in cmd and not enabled))
        with mock.patch.object(engine.subprocess, "run", run):
            call()
        return [c[2:] for c in calls if c[:2] == ["systemctl", "--user"]]

    def test_start_does_not_enable(self):
        for key, unit in (("digiface", "openface-mixer-engine.service"),
                          ("ff802", "openface-mixer-engine@ff802.service")):
            e = engine.Engine(DEVICES[key])
            self.assertEqual(self.run_recorded(e.start), [["cat", unit], ["start", unit]])
            self.assertEqual(self.run_recorded(e.enable, enabled=False),
                             [["is-enabled", unit], ["enable", unit]])
            self.assertEqual(self.run_recorded(e.enable), [["is-enabled", unit]])

    def test_enable_without_systemd(self):
        for error in (FileNotFoundError, engine.subprocess.TimeoutExpired("systemctl", 5)):
            with mock.patch.object(engine.subprocess, "run", side_effect=error):
                engine.Engine(DEVICES["digiface"]).enable()


if __name__ == "__main__":
    unittest.main()
