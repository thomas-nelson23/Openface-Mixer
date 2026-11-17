import copy
import json
import os
import struct
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest import mock

from openface_mixer import config, control_room, model as m
from openface_mixer.devices import DEVICES


class MatrixFileTest(unittest.TestCase):
    def test_layout(self):
        st = m.default_state()
        st["out"][0]["gain"] = -6.0
        data = config.matrix_file_bytes(st)
        self.assertEqual(len(data), 4 + (m.N_OUT * m.N_SRC + m.N_OUT) * 4)
        magic, = struct.unpack_from("<I", data)
        self.assertEqual(magic, config.MATRIX_MAGIC)
        out0, = struct.unpack_from("<f", data, 4 + m.N_OUT * m.N_SRC * 4)
        self.assertAlmostEqual(out0, 10 ** (-6 / 20), places=6)

    def test_solo_is_not_saved(self):
        st = m.default_state()
        st["selected"] = 0
        st["solo"]["play"][4] = True
        data = config.matrix_file_bytes(st)
        play1_to_out1, = struct.unpack_from("<f", data, 4 + m.N_IN * 4)
        self.assertEqual(play1_to_out1, 1.0)

    def test_state_file_leaves_out_solo(self):
        st = m.default_state()
        st["solo"]["in"][0] = True
        written = {}
        with mock.patch.object(config, "atomic_write",
                               lambda path, data, **_kw: written.update({path: data})):
            config.save_state(st)
        self.assertNotIn("solo", json.loads(written[config.state_file()]))
        self.assertTrue(st["solo"]["in"][0])   # the live state keeps it

    def test_fresh_install_is_the_engines_default(self):
        """What the engine starts with when it has no matrix.bin, or refuses the one it finds
        (ofm_matrix_default in engine/matrix_file.c, checked by tests/test_matrix_file.c):
        playback n to output n, output masters at 0 dB, no device settings."""
        gains = array("f", bytes(m.N_OUT * m.N_SRC * 4))
        for o in range(m.N_OUT):
            gains[o * m.N_SRC + m.N_IN + o] = 1.0
        self.assertEqual(config.matrix_file_bytes(m.default_state()),
                         struct.pack("<I", config.MATRIX_MAGIC) + gains.tobytes() +
                         array("f", [1.0] * m.N_OUT).tobytes())


def saved(st):
    return json.dumps({k: v for k, v in st.items() if k != "solo"})


def good_state():
    """A Digiface state as the app saves it (no sends on channels the Digiface lacks)."""
    st = m.default_state(16)
    st["sends"]["in"][0][0] = [-6.0, 0.25]
    st["out"][3] = {"gain": -12.0, "mute": True}
    m.clear_sends_outside(st, config.DEFAULT.all_channels)
    return st


def unusable_files():
    """(what is wrong, file content) for state files that can't be used as they are."""
    good = json.loads(saved(good_state()))
    yield "an empty file", ""
    yield "a truncated file", saved(good_state())[:3000]
    yield "not JSON", "{not json"
    yield "binary", bytes([0xff, 0xfe, 0x00]) + b"garbage"
    for value in ([1, 2, 3], "hello", 5, None, {}, {"out": []}):
        yield f"JSON that is {value!r}", json.dumps(value)
    for key in ("sends", "mute", "stereo"):
        for kind in ("in", "play"):
            st = copy.deepcopy(good)
            del st[key][kind]
            yield f"{key} without {kind}", json.dumps(st)
    for key in ("sends", "out", "stereo", "mute", "groups"):
        for value in ("x", 7, None, [1]):
            yield f"{key} is {value!r}", json.dumps(dict(good, **{key: value}))


class StateFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patch = mock.patch.object(config, "CONFIG_DIR", self.dir)
        patch.start()
        self.addCleanup(patch.stop)
        self.path = config.state_file()
        self.bak = config.backup_file(self.path)

    def write(self, path, content):
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)

    def usable(self, st):
        m.compute_matrix(st)
        config.matrix_file_bytes(st)
        config.save_state(st)
        self.assertEqual(config.load_state(), (st, None))

    def test_data_is_synced_before_it_replaces_the_file(self):
        self.path.write_text("old")
        tmp = Path(str(self.path) + ".tmp")
        seen = []

        def fsync(fd, real=os.fsync):
            seen.append((self.path.read_text(), tmp.read_text()))
            real(fd)

        with mock.patch.object(config.os, "fsync", fsync):
            config.atomic_write(self.path, "new")
        self.assertEqual(seen, [("old", "new")])
        self.assertEqual(self.path.read_text(), "new")
        self.assertFalse(tmp.exists())
        self.assertFalse(self.bak.exists())
        config.atomic_write(self.path, b"bytes")
        self.assertEqual(self.path.read_bytes(), b"bytes")

    def test_backup_is_the_save_before(self):
        first, second = good_state(), good_state()
        second["out"][0]["gain"] = -30.0
        config.save_state(first)
        self.assertFalse(self.bak.exists())
        first_file = self.path.read_text()
        config.save_state(second)
        self.assertEqual(self.bak.read_text(), first_file)
        self.assertEqual(json.loads(self.path.read_text())["out"][0]["gain"], -30.0)
        self.assertFalse(config.backup_file(config.matrix_file()).exists())

    def test_fresh_install(self):
        st, problem = config.load_state()
        self.assertIsNone(problem)
        self.assertEqual(st["selected"], 16)
        self.assertEqual(m.extract_mix(st)["out"], m.extract_mix(m.default_state())["out"])
        self.assertEqual(list(self.dir.iterdir()), [])
        self.usable(st)

    def test_round_trip(self):
        st = good_state()
        config.save_state(st)
        loaded, problem = config.load_state()
        self.assertIsNone(problem)
        self.assertEqual(m.extract_mix(loaded), m.extract_mix(st))

    def test_unusable_file_falls_back_to_the_backup(self):
        for what, content in unusable_files():
            with self.subTest(what):
                self.bak.write_text(saved(good_state()))
                self.write(self.path, content)
                st, problem = config.load_state()
                self.assertEqual(problem, "backup")
                self.assertEqual(m.extract_mix(st), m.extract_mix(good_state()))
                # the good copy is the state file again, so the next save backs that up
                self.assertEqual(self.path.read_text(), saved(good_state()))
                self.assertFalse(self.bak.exists())
                self.usable(st)

    def test_missing_file_falls_back_to_the_backup(self):
        self.bak.write_text(saved(good_state()))
        st, problem = config.load_state()
        self.assertEqual(problem, "backup")
        self.assertEqual(st["sends"]["in"][0][0], [-6.0, 0.25])

    def test_unusable_file_without_a_backup_is_repaired(self):
        for backup in (None, "", "[1]"):
            for what, content in unusable_files():
                with self.subTest(what, backup=backup):
                    self.bak.unlink(missing_ok=True)
                    if backup is not None:
                        self.bak.write_text(backup)
                    self.write(self.path, content)
                    st, problem = config.load_state()
                    self.assertEqual(problem, "reset")
                    self.usable(st)

    def test_repair_keeps_the_rest_of_the_mix(self):
        st = json.loads(saved(good_state()))
        st["mute"] = "x"
        self.path.write_text(json.dumps(st))
        loaded, problem = config.load_state()
        self.assertEqual(problem, "reset")
        self.assertEqual(loaded["sends"]["in"][0][0], [-6.0, 0.25])
        self.assertEqual(loaded["out"][3], {"gain": -12.0, "mute": True})
        self.assertEqual(loaded["mute"], m.default_state()["mute"])

    def test_damage_outside_the_mix_is_repaired_in_place(self):
        """An out-of-range selected output, a wrong-typed control room and the like are put
        back to their defaults; the mix in the file is good, so the file is used."""
        for key, value in (("selected", 99), ("selected", -1), ("selected", "x"),
                           ("control_room", "x"), ("control_room", [1]), ("control_room", None),
                           ("control_room", {"main": 99, "phones": "x", "talkback_dests": 5}),
                           ("tab", "x"), ("names", [1]), ("hidden", 7), ("remote", "x"),
                           ("folded", "in"), ("folded", {"in": True}), ("folded", [[], 7]),
                           ("hw", "x")):
            with self.subTest(key=key, value=value):
                self.bak.write_text(saved(m.default_state()))
                self.path.write_text(json.dumps(dict(json.loads(saved(good_state())),
                                                     **{key: value})))
                st, problem = config.load_state()
                self.assertIsNone(problem)
                self.assertEqual(st["sends"]["in"][0][0], [-6.0, 0.25])
                self.assertEqual(st["selected"], 16)
                self.assertEqual((st["control_room"]["main"], st["control_room"]["phones"]),
                                 (0, 16))
                control_room.apply(st, m.compute_matrix(st), m.compute_out_gains(st))
                self.usable(st)

    def test_talkback_never_survives_a_restart(self):
        st = good_state()
        control_room.control_room(st)["talkback"] = True
        config.save_state(st)
        self.assertFalse(config.load_state()[0]["control_room"]["talkback"])


class DeviceStateTest(unittest.TestCase):
    """Loading for each interface: its own files, and no sends on channels it doesn't have."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(config, "CONFIG_DIR", Path(tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def routes(self, st):
        """{(kind, channel, output channel)} of the crosspoints the state switches on."""
        g = m.compute_matrix(st)
        return {(("in", s) if s < m.N_IN else ("play", s - m.N_IN)) + (o,)
                for o in range(m.N_OUT) for s in range(m.N_SRC) if g[o * m.N_SRC + s]}

    def test_fresh_install_is_playback_n_to_output_n_on_the_channels_it_has(self):
        for key, dev in DEVICES.items():
            with self.subTest(key):
                st, problem = config.load_state(dev)
                self.assertIsNone(problem)
                both = dev.all_channels("play") & dev.all_channels("out")
                self.assertEqual(self.routes(st), {("play", c, c) for c in both})
                config.matrix_file_bytes(st, dev)

    def test_sends_on_channels_the_interface_lacks_are_cleared(self):
        for key, dev in DEVICES.items():
            with self.subTest(key):
                st = m.default_state()
                for kind in ("in", "play"):
                    st["stereo"][kind] = [False] * (m.N_IN // 2)
                    st["sends"][kind] = [[[0.0, 0.0] for _ in range(m.N_PAIRS)]
                                         for _ in range(m.N_IN)]
                config.state_file(dev).parent.mkdir(parents=True, exist_ok=True)
                config.state_file(dev).write_text(saved(st))
                loaded, problem = config.load_state(dev)
                self.assertIsNone(problem)
                outs = dev.all_channels("out")
                self.assertEqual(self.routes(loaded), {
                    (kind, c, o) for kind in ("in", "play") for c in dev.all_channels(kind)
                    for o in outs})

    def test_the_aio_extension_boards_keep_their_routing(self):
        """Analog 3-6 (AI4S/AO4S, hardware channels 2-5) have no strips yet, but the engine
        mixes them: the default playback 3-6 to outputs 3-6 must stay, or a card with an AO4S
        goes silent there with no strip to bring it back."""
        for key in ("aio", "aiopro"):
            st, _problem = config.load_state(DEVICES[key])
            for c in (2, 3, 4, 5):
                self.assertIn(("play", c, c), self.routes(st), key)
            self.assertNotIn(("play", 20, 20), self.routes(st), key)

    def test_channels_of_other_sample_rates_are_kept(self):
        """A higher rate hides channels (the Digiface's ADAT 5-8 of each port at 96 kHz); their
        sends come back with the rate, so they are not cleared."""
        for key, dev in DEVICES.items():
            for kind in ("in", "play", "out"):
                with self.subTest(key, kind=kind):
                    every = dev.all_channels(kind)
                    for mode in (1, 2, 4):
                        self.assertLessEqual(set(dev.channels(kind, mode)), every)
                    self.assertLess(max(every), m.N_IN)
        digiface = DEVICES["digiface"]
        self.assertEqual(digiface.all_channels("in"), set(range(32)))
        self.assertEqual(digiface.all_channels("out"), set(range(34)))
        self.assertEqual(DEVICES["aio"].all_channels("in"), {*range(6), *range(8, 20)})
        self.assertEqual(DEVICES["ff802"].all_channels("in"), set(range(30)))


class DeviceChoiceTest(unittest.TestCase):
    def test_a_damaged_file_is_no_choice(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(config, "DEVICE_FILE", Path(tmp) / "device"):
            self.assertIsNone(config.load_device_choice())
            config.save_device_choice(DEVICES["ff802"])
            self.assertEqual(config.load_device_choice(), "ff802")
            for content in (b"\xff\xfe\x00", b"toaster\n", b""):
                config.DEVICE_FILE.write_bytes(content)
                self.assertIsNone(config.load_device_choice())


class LauncherTest(unittest.TestCase):
    def test_installed_copy_uses_its_own_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp).resolve()
            cmd = prefix / "bin" / "openface-mixer"
            module = prefix / "share" / "openface-mixer" / "openface_mixer" / "config.py"
            with mock.patch.object(config, "__file__", str(module)), \
                    mock.patch.object(config.shutil, "which", lambda name: None):
                self.assertEqual(config.launcher(), "openface-mixer")    # nothing installed
                cmd.parent.mkdir()
                cmd.write_text("#!/bin/sh\n")
                self.assertEqual(config.launcher(), str(cmd))

    def test_checkout_uses_path(self):
        with mock.patch.object(config.shutil, "which", lambda name: f"/opt/bin/{name}"):
            self.assertEqual(config.launcher(), "/opt/bin/openface-mixer")


class UiScaleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ui_file = Path(tmp.name) / "ui.json"
        patch = mock.patch.object(config, "UI_FILE", ui_file)
        patch.start()
        self.addCleanup(patch.stop)

    def test_default_and_roundtrip(self):
        self.assertEqual(config.load_ui_scale(), 1.0)
        config.save_ui_scale(1.5)
        self.assertEqual(config.load_ui_scale(), 1.5)
        self.assertEqual(json.loads(config.UI_FILE.read_text()), {"scale": 1.5})

    def test_bad_values_fall_back_or_clamp(self):
        config.UI_FILE.write_text("{not json")
        self.assertEqual(config.load_ui_scale(), 1.0)
        config.UI_FILE.write_text(json.dumps({"scale": "large"}))
        self.assertEqual(config.load_ui_scale(), 1.0)
        config.UI_FILE.write_text(json.dumps({"scale": 99}))
        self.assertEqual(config.load_ui_scale(), config.UI_SCALE_MAX)
        config.save_ui_scale(0.1)
        self.assertEqual(config.load_ui_scale(), config.UI_SCALE_MIN)

    def test_window_kept_beside_scale(self):
        self.assertEqual(config.load_window(), (None, True))
        config.save_ui_scale(1.25)
        config.save_window(1400, 900, False)
        self.assertEqual(config.load_window(), ([1400, 900], False))
        self.assertEqual(config.load_ui_scale(), 1.25)
        config.save_ui_scale(1.5)
        self.assertEqual(config.load_window(), ([1400, 900], False))
        config.UI_FILE.write_text(json.dumps({"window": "big"}))
        self.assertEqual(config.load_window(), (None, True))

    def test_update_choices(self):
        self.assertEqual(config.load_update_prefs(), (True, False))    # check, releases
        config.save_ui_scale(1.25)
        config.save_ui(update_check=False, update_channel="latest")
        self.assertEqual(config.load_update_prefs(), (False, True))
        self.assertEqual(config.load_ui_scale(), 1.25)
        config.save_ui(update_check=True, update_channel="releases")
        self.assertEqual(config.load_update_prefs(), (True, False))
        # a damaged or hand-edited file: anything unclear is the default
        for damaged in ("{not json", "[]", "null", '{"update_check": null}',
                        '{"update_check": 0, "update_channel": 1}',
                        '{"update_check": "no", "update_channel": "Latest"}',
                        '{"update_check": [], "update_channel": ["latest"]}'):
            config.UI_FILE.write_text(damaged)
            self.assertEqual(config.load_update_prefs(), (True, False), damaged)
        config.UI_FILE.write_bytes(b"\xff\xfe")
        self.assertEqual(config.load_update_prefs(), (True, False))


if __name__ == "__main__":
    unittest.main()
