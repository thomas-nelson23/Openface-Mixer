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
        # the offsets tests/test_engine_loop.c takes from the struct itself
        self.assertEqual((engine.OFF_GAIN, engine.OFF_OUT_GAIN, engine.OFF_PEAK_SRC,
                          engine.OFF_PEAK_OUT, engine.OFF_DEV_CONFIG, engine.OFF_DEV_CMD,
                          engine.OFF_PW, engine.SHM_SIZE),
                         (64, 10432, 10576, 10864, 11008, 11012, 27396, 27412))

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

    def test_pipewire_block(self):
        """The engine's PipeWire status, and the GUI's word that has it read sinks.conf."""
        e = self.segment()
        self.assertTrue(e.attach())
        struct.pack_into("<3I", e.m, engine.OFF_PW + 4, 1, 3, 2)
        with mock.patch.object(e, "pid_alive", return_value=True):
            s = e.status()
        self.assertEqual((s["pipewire"], s["pw_nodes"], s["pw_linked"]), (True, 3, 2))
        e.reload_sinks()
        e.reload_sinks()
        self.assertEqual(struct.unpack_from("<4I", e.m, engine.OFF_PW), (2, 1, 3, 2))
        struct.pack_into("<I", e.m, engine.OFF_PW, 0xFFFFFFFF)
        e.reload_sinks()
        self.assertEqual(struct.unpack_from("<I", e.m, engine.OFF_PW), (0,))

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

    def test_stop_ends_an_engine_of_another_version_too(self):
        """Without systemd the window stops the engine by the process id in the segment. An
        older engine has to go as well: the new one does not start beside it."""
        for version in (engine.SHM_VERSION - 1, engine.SHM_VERSION):
            e = self.segment(version)
            self.assertTrue(e.attach())
            struct.pack_into("<I", e.m, 4 * engine.HDR_PID, 4321)
            with mock.patch.object(e, "has_service", return_value=False), \
                    mock.patch.object(engine.os, "kill") as kill:
                with mock.patch.object(engine.Path, "read_text", return_value="openface-mixer-\n"):
                    self.assertEqual(e.pid_alive(), version == engine.SHM_VERSION)
                    e.stop()
                kill.assert_called_once_with(4321, 15)
                # the number belongs to another program by now, or to nobody: left alone
                with mock.patch.object(engine.Path, "read_text", return_value="bash\n"):
                    e.stop()
                with mock.patch.object(engine.Path, "read_text", side_effect=FileNotFoundError):
                    e.stop()
                    self.assertFalse(e.pid_alive())
                struct.pack_into("<I", e.m, 4 * engine.HDR_PID, 0)      # a clean shutdown
                e.stop()
                kill.assert_called_once_with(4321, 15)


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
