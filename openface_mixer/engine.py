"""Client side of the shared-memory link to openface-mixer-engine (see engine/shm_layout.h)."""
import mmap
import os
import shutil
import struct
import subprocess
import time
from array import array
from pathlib import Path

from .config import CACHE_DIR, N_DEV_CMD
from .devices import DEFAULT
from .model import N_OUT, N_SRC

ENGINE_BINARY = "openface-mixer-engine"

# Must match struct ofm_shm in engine/shm_layout.h
SHM_MAGIC = 0x584D464F
SHM_VERSION = 9
HDR_SIZE = 64
HDR_MAGIC, HDR_VERSION, HDR_N_SRC, HDR_N_OUT, HDR_HEARTBEAT, HDR_RATE, HDR_PID = 0, 1, 2, 3, 4, 5, 6
HDR_HW_STATE, HDR_HW_NODES, HDR_HW_LEVELS, HDR_SINK_LINKED, HDR_DEV_STATUS = 7, 8, 9, 10, 11
HDR_DEV_STATUS2, HDR_GUI_HEARTBEAT = 12, 13
OFF_GAIN = HDR_SIZE
OFF_OUT_GAIN = OFF_GAIN + N_OUT * N_SRC * 4
OFF_PEAK_SRC = OFF_OUT_GAIN + N_OUT * 4
OFF_PEAK_OUT = OFF_PEAK_SRC + N_SRC * 4
OFF_DEV_CONFIG = OFF_PEAK_OUT + N_OUT * 4
OFF_DEV_CMD = OFF_DEV_CONFIG + 4
OFF_RMS_SRC = OFF_DEV_CMD + N_DEV_CMD * 4      # the meter extras of version 8
OFF_RMS_OUT = OFF_RMS_SRC + N_SRC * 4
OFF_OVER_SRC = OFF_RMS_OUT + N_OUT * 4
OFF_OVER_OUT = OFF_OVER_SRC + N_SRC * 4
# the PipeWire block of version 9: pw_reload (the GUI's), pw_connected, pw_nodes, pw_linked
OFF_PW = OFF_OVER_OUT + N_OUT * 4
SHM_SIZE = OFF_PW + 4 * 4

# hw_state values (DFU_STATE_* in engine/digiface_usb.h)
HW_STATES = ("starting", "no-device", "no-access", "busy", "active", "no-nodes", "error",
             "wrong-driver", "no-reply")


class Engine:
    """The engine for one device: /dev/shm/<dev.shm_name>-<uid>, systemd unit dev.service."""

    def __init__(self, dev=DEFAULT):
        self.dev = dev
        self.path = f"/dev/shm/{dev.shm_name}-{os.getuid()}"
        self.m = None
        self._last_hb = None
        self._last_hb_time = 0.0

    def attach(self):
        if self.m is not None:
            return True
        try:
            fd = os.open(self.path, os.O_RDWR)
        except FileNotFoundError:
            return False
        try:
            if os.fstat(fd).st_size < SHM_SIZE:
                return False
            self.m = mmap.mmap(fd, SHM_SIZE)
        finally:
            os.close(fd)
        return True

    def outdated(self):
        """True if an engine with an older shared-memory layout is running (needs a restart)."""
        try:
            with open(self.path, "rb") as f:
                magic, version = struct.unpack("<2I", f.read(8))
        except (OSError, struct.error):
            return False
        return magic == SHM_MAGIC and version != SHM_VERSION

    def header(self):
        if self.attach():
            return struct.unpack_from("<16I", self.m, 0)
        try:        # an older engine's smaller segment: its header still says who runs it
            with open(self.path, "rb") as f:
                return struct.unpack("<16I", f.read(HDR_SIZE))
        except (OSError, struct.error):
            return None

    def pid(self):
        """The process id of the engine that runs on the segment, whatever its layout version,
        or 0 if none does."""
        h = self.header()
        if not h or h[HDR_MAGIC] != SHM_MAGIC or not h[HDR_PID]:
            return 0
        try:  # /proc/<pid>/comm is truncated to 15 characters
            comm = Path(f"/proc/{h[HDR_PID]}/comm").read_text().strip()
        except OSError:
            return 0
        return h[HDR_PID] if comm == ENGINE_BINARY[:15] else 0

    def pid_alive(self):
        return bool(self.pid()) and self.header()[HDR_VERSION] == SHM_VERSION

    def status(self):
        """dict with the hardware mixer state and sample rate, or None if the engine isn't
        running."""
        if not self.pid_alive():
            return None
        h = self.header()
        _reload, pw, made, linked = struct.unpack_from("<4I", self.m, OFF_PW)
        now = time.monotonic()
        if h[HDR_HEARTBEAT] != self._last_hb:
            self._last_hb, self._last_hb_time = h[HDR_HEARTBEAT], now
        return {
            "processing": now - self._last_hb_time < 1.0,
            "rate": h[HDR_RATE],
            "sink_linked": bool(h[HDR_SINK_LINKED]),
            # PipeWire: connected, and how many desktop sinks and sources are made and linked
            "pipewire": bool(pw), "pw_nodes": made, "pw_linked": linked,
            "hw": HW_STATES[h[HDR_HW_STATE]] if h[HDR_HW_STATE] < len(HW_STATES) else "error",
            "hw_nodes": h[HDR_HW_NODES], "hw_levels": bool(h[HDR_HW_LEVELS]),
            "dev_status": h[HDR_DEV_STATUS], "dev_status2": h[HDR_DEV_STATUS2],
        }

    def write_matrix(self, gains, out_gains):
        """gains: array('f') of N_OUT * N_SRC, out-major (model.compute_matrix);
        out_gains: array('f') of N_OUT (model.compute_out_gains)."""
        if self.attach():
            self.m[OFF_GAIN:OFF_GAIN + len(gains) * 4] = gains.tobytes()
            self.m[OFF_OUT_GAIN:OFF_OUT_GAIN + len(out_gains) * 4] = out_gains.tobytes()

    def write_device(self, block):
        """block: (configuration word, command slots) from config.device_block(), or None."""
        if block is not None and self.attach():
            config, cmds = block
            self.m[OFF_DEV_CMD:OFF_DEV_CMD + N_DEV_CMD * 4] = struct.pack(f"<{N_DEV_CMD}I", *cmds)
            self.m[OFF_DEV_CONFIG:OFF_DEV_CONFIG + 4] = struct.pack("<I", config)

    def reload_sinks(self):
        """Tell the engine to read sinks.conf again (config.save_sinks wrote it)."""
        if self.attach():
            n, = struct.unpack_from("<I", self.m, OFF_PW)
            struct.pack_into("<I", self.m, OFF_PW, (n + 1) & 0xFFFFFFFF)

    def read_peaks(self):
        """Returns (source peaks, output peaks, source RMS, output RMS, source overs, output
        overs) as floats (levels linear, overs the consecutive full-scale samples counted) and
        resets the max-hold, or six Nones. Each call moves the GUI heartbeat: the engine reads
        the device's meters at the full rate only while someone keeps asking for them, so
        nothing else has to tell it."""
        if not self.attach():
            return (None,) * 6
        beat, = struct.unpack_from("<I", self.m, 4 * HDR_GUI_HEARTBEAT)
        struct.pack_into("<I", self.m, 4 * HDR_GUI_HEARTBEAT, (beat + 1) & 0xFFFFFFFF)
        got = tuple(array("f", self.m[off:off + n * 4]) for off, n in (
            (OFF_PEAK_SRC, N_SRC), (OFF_PEAK_OUT, N_OUT), (OFF_RMS_SRC, N_SRC),
            (OFF_RMS_OUT, N_OUT), (OFF_OVER_SRC, N_SRC), (OFF_OVER_OUT, N_OUT)))
        self.m[OFF_PEAK_SRC:OFF_PEAK_OUT + N_OUT * 4] = bytes((N_SRC + N_OUT) * 4)
        self.m[OFF_RMS_SRC:OFF_PW] = bytes(OFF_PW - OFF_RMS_SRC)
        return got

    # ---- process management: prefer the systemd user service, fall back to spawning
    @staticmethod
    def binary():
        repo_build = Path(__file__).resolve().parent.parent / "engine" / ENGINE_BINARY
        if repo_build.is_file() and os.access(repo_build, os.X_OK):
            return str(repo_build)
        return shutil.which(ENGINE_BINARY)

    def has_service(self):
        return subprocess.run(["systemctl", "--user", "cat", self.dev.service],
                              capture_output=True).returncode == 0

    def start(self):
        if self.has_service():
            subprocess.run(["systemctl", "--user", "start", self.dev.service],
                           capture_output=True)
            return True, "started via systemd"
        b = self.binary()
        if not b:
            return False, f"{ENGINE_BINARY} not found - run 'make install' or 'make' first"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        log = open(CACHE_DIR / f"engine-{self.dev.key}.log", "ab")
        subprocess.Popen([b, "--device", self.dev.key], stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True, f"started {b}"

    def enable(self):
        """Start the engine at every login from now on, so it restores the device's mix. For
        once the interface has been seen: nobody gets an engine and a playback sink for a
        device they don't have."""
        def systemctl(verb):
            return subprocess.run(["systemctl", "--user", verb, self.dev.service],
                                  capture_output=True, timeout=5).returncode
        try:
            if systemctl("is-enabled"):     # enabling again would make systemd reload its units
                systemctl("enable")
        except (OSError, subprocess.TimeoutExpired):
            pass

    def stop(self):
        if self.has_service():
            subprocess.run(["systemctl", "--user", "stop", self.dev.service])
            return
        pid = self.pid()    # also an older engine's: the new one can't start beside it
        if pid:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
