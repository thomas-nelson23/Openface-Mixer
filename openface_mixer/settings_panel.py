"""Right-hand panel: device choice, the device's clock/port settings, card profile and hardware
mixer status."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QGroupBox, QLabel, QMessageBox, QPushButton, QVBoxLayout,
    QWidget,
)

from . import fireface800 as ff800
from . import fireface802 as ff
from . import raydat
from .devices import DEVICES
from .hardware import Hardware, set_card_profile

UDEV_RULES = {"digiface": "70-rme-digiface.rules", "ff802": "70-rme-fireface.rules",
              "ff800": "70-rme-fireface.rules"}


def udev_rule_hint(dev):
    if dev.key not in UDEV_RULES:     # PCIe cards: plain ALSA devices
        return ("Openface Mixer opens the card's ALSA control and hwdep devices in /dev/snd. "
                "Make sure your user may use audio devices (the <tt>audio</tt> group, or a "
                "local login session).")
    rule = UDEV_RULES[dev.key]
    return (f"Openface Mixer needs write access to the {dev.name}'s {dev.bus} device. Install "
            f"the udev rule once:<br><tt>sudo install -m644 packaging/{rule} "
            "/etc/udev/rules.d/</tt><br>"
            "<tt>sudo udevadm control --reload &amp;&amp; sudo udevadm trigger</tt>")


def hw_state_text(dev, state):
    short = dev.name.replace("RME ", "")
    red = "<span style='color:#ff6b5b'>{}</span>"
    return {
        "starting": f"Connecting to the {short}…",
        "no-device": red.format(f"{short} not found on {dev.bus}."),
        "no-access": red.format(f"No permission to open the {short}.") + "<br>"
                     + udev_rule_hint(dev),
        "busy": red.format(
            f"The {short} driver refuses mixer changes while playback and recording are open in "
            "two different programs. Let one program (PipeWire or JACK) use both."
            if dev.bus == "PCIe" else f"The {short} mixer interface is in use by another program."),
        "active": "<span style='color:#34c759'>Hardware mixer active</span>",
        "no-nodes": f"<span style='color:#ffd60a'>Hardware mixer active, but this mix needs "
                    f"more than {dev.max_routes} routes; some sends are missing.</span>",
        "error": red.format(f"{dev.bus} error talking to the {short}; retrying."),
        "wrong-driver": red.format(
            f"This {short} needs the snd-hdspe driver; snd-hdspm can't run cards from 2022 on. "
            "Install snd-hdspe and blacklist snd-hdspm (see the README)."),
    }.get(state, state)


class Led(QLabel):
    COLORS = {"No Lock": "#5b5b60", "Lock": "#ffd60a", "Sync": "#34c759"}

    def set_state(self, s):
        c = self.COLORS.get(s, "#5b616a")
        self.setText(f"<span style='color:{c}'>●</span> {s or '—'}")


class SettingsPanel(QWidget):
    device_selected = Signal(str)    # device key
    hw_changed = Signal()            # a device setting (Fireface 802/800) changed in st["hw"]

    def __init__(self, hw, dev, st, parent=None):
        super().__init__(parent)
        self.hw = hw
        self.dev = dev
        self.st = st
        self.setFixedWidth(310)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 0, 0, 0)
        lay.setSpacing(10)

        box = QGroupBox("DEVICE")
        bl = QVBoxLayout(box)
        self.device = QComboBox()
        for d in DEVICES.values():
            self.device.addItem(d.name, d.key)
        self.device.setCurrentIndex(self.device.findData(dev.key))
        self.device.activated.connect(lambda i: self.device_selected.emit(self.device.itemData(i)))
        bl.addWidget(self.device)
        lay.addWidget(box)
        self._card_name = None
        self._updating = False

        if dev.key == "ff802":
            self._build_ff802(lay)
        elif dev.key == "ff800":
            self._build_ff800(lay)
        elif dev.key == "raydat":
            self._build_raydat(lay)
        else:
            self._build_digiface(lay)

        eng = QGroupBox("HARDWARE MIXER")
        el = QVBoxLayout(eng)
        self.hw_label = QLabel("—")
        self.hw_label.setWordWrap(True)
        el.addWidget(self.hw_label)
        self.eng_label = QLabel("—")
        self.eng_label.setWordWrap(True)
        el.addWidget(self.eng_label)
        self.eng_btn = QPushButton("Restart engine")
        el.addWidget(self.eng_btn)
        lay.addWidget(eng)

        note = QLabel(
            f"<span style='color:#6e6e73;font-size:9px'>All mixing happens in the {dev.name}'s "
            "own DSP, like TotalMix: input monitoring has no added latency and the mix keeps "
            f"running with the computer idle or the engine stopped.<br><br>Playback "
            f"{dev.sink_channels} = the "
            "<i>Openface Mixer Playback</i> sink. Other playback channels: send apps to the "
            "interface's own output channels (Pro Audio profile) with qpwgraph/Helvum.</span>")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch()

    # ------------------------------------------------------------------ Digiface USB
    def _build_digiface(self, lay):
        clock = QGroupBox("CLOCK")
        cl = QGridLayout(clock)
        cl.addWidget(QLabel("Clock source"), 0, 0)
        self.sync_src = QComboBox()
        self.sync_src.activated.connect(lambda i: self._write("Sync Source", i))
        cl.addWidget(self.sync_src, 0, 1)
        cl.addWidget(QLabel("Current source"), 1, 0)
        self.cur_src = QLabel("—")
        cl.addWidget(self.cur_src, 1, 1)
        cl.addWidget(QLabel("Sample rate"), 2, 0)
        self.rate = QLabel("—")
        cl.addWidget(self.rate, 2, 1)
        lay.addWidget(clock)

        ports = QGroupBox("OPTICAL PORTS")
        pl = QGridLayout(ports)
        for c, h in enumerate(["", "In format", "In status", "In rate", "Out format"]):
            lbl = QLabel(f"<span style='color:#8e8e93;font-size:9px'>{h}</span>")
            pl.addWidget(lbl, 0, c)
        self.port_widgets = []
        for i in range(4):
            fmt, led, rate, out = QLabel("—"), Led(), QLabel("—"), QComboBox()
            out.addItems(["ADAT", "S/PDIF"])
            out.activated.connect(lambda v, n=i + 1: self._write(f"Output {n} Format", v))
            for c, w in enumerate([QLabel(f"{i + 1}"), fmt, led, rate, out]):
                pl.addWidget(w, i + 1, c)
            self.port_widgets.append((fmt, led, rate, out))
        lay.addWidget(ports)

        prof = QGroupBox("CARD PROFILE")
        vl = QVBoxLayout(prof)
        self.profile = QLabel("—")
        self.profile.setWordWrap(True)
        vl.addWidget(self.profile)
        self.pro_btn = QPushButton("Record all inputs (Pro Audio profile)")
        self.pro_btn.clicked.connect(self._enable_pro)
        vl.addWidget(self.pro_btn)
        lay.addWidget(prof)

    # ------------------------------------------------------------------ Fireface 802
    def _build_ff802(self, lay):
        """TotalMix's Settings dialog for the 802: clock, sync inputs and AES/optical options.
        The device can't report these settings, so they come from st["hw"]."""
        hw = self.st["hw"]
        self.ff_clock = QComboBox()
        for key, label, _ in ff.CLOCK_SOURCES:
            self.ff_clock.addItem(label, key)
        self.ff_clock.setCurrentIndex(max(0, self.ff_clock.findData(hw["clock"])))
        self.ff_clock.activated.connect(
            lambda i: self._set_hw("clock", self.ff_clock.itemData(i)))
        self._build_fw_clock(lay, self.ff_clock, ff.STATUS_INPUTS)

        opts = QGroupBox("OPTIONS")
        ol = QGridLayout(opts)
        rows = (("AES input", "aes_in", (("XLR", "xlr"), ("Optical (ADAT B)", "optical"))),
                ("Optical out", "opt_out", (("ADAT", "adat"), ("S/PDIF", "spdif"))),
                ("AES out", "aes_pro", (("Consumer", False), ("Professional", True))))
        for r, (label, key, items) in enumerate(rows):
            combo = QComboBox()
            for text, value in items:
                combo.addItem(text, value)
            combo.setCurrentIndex(max(0, combo.findData(hw[key])))
            combo.activated.connect(lambda i, c=combo, k=key: self._set_hw(k, c.itemData(i)))
            ol.addWidget(QLabel(label), r, 0)
            ol.addWidget(combo, r, 1)
        single = QCheckBox("Word clock out always single speed")
        single.setChecked(hw["word_single"])
        single.toggled.connect(lambda on: self._set_hw("word_single", on))
        ol.addWidget(single, len(rows), 0, 1, 2)
        lay.addWidget(opts)

    def _build_fw_clock(self, lay, clock_combo, status_inputs, clock_label="Clock source"):
        """The CLOCK and INPUT STATUS boxes of a FireWire Fireface (and the RayDAT). Returns the
        CLOCK box's grid."""
        clock = QGroupBox("CLOCK")
        cl = QGridLayout(clock)
        cl.addWidget(QLabel(clock_label), 0, 0)
        cl.addWidget(clock_combo, 0, 1)
        cl.addWidget(QLabel("Current source"), 1, 0)
        self.cur_src = QLabel("—")
        cl.addWidget(self.cur_src, 1, 1)
        cl.addWidget(QLabel("Sample rate"), 2, 0)
        self.rate = QLabel("—")
        cl.addWidget(self.rate, 2, 1)
        lay.addWidget(clock)

        sync = QGroupBox("INPUT STATUS")
        sl = QGridLayout(sync)
        self.sync_widgets = []
        self._sync_labels = {}
        for r, (label, *_unused) in enumerate(status_inputs):
            led, rate = Led(), QLabel("—")
            self._sync_labels[label] = QLabel(label)
            sl.addWidget(self._sync_labels[label], r, 0)
            sl.addWidget(led, r, 1)
            sl.addWidget(rate, r, 2)
            led.set_state(None)
            self.sync_widgets.append((led, rate))
        lay.addWidget(sync)
        return cl

    def _show_fw_status(self, st):
        """st: the device's decode_status() dict, or None while it is not connected."""
        if st is None:
            self.cur_src.setText("—")
            self.rate.setText(f"<span style='color:#ff6b5b'>{self.dev.name.replace('RME ', '')} "
                              "not connected</span>")
            for led, rate in self.sync_widgets:
                led.set_state(None)
                rate.setText("—")
            return
        self.cur_src.setText(st["source"])
        self.rate.setText(f"{st['rate'] / 1000:g} kHz" if st["rate"] else "—")
        for (led, rate), (_, state, r) in zip(self.sync_widgets, st["inputs"]):
            led.set_state(state)
            rate.setText(f"{r / 1000:g}k" if r else "—")

    def _set_hw(self, key, value):
        self.st["hw"][key] = value
        self.hw_changed.emit()

    def update_ff802_status(self, word):
        """word: the 802's sync status register (Engine.status()["dev_status"]), or None."""
        self._show_fw_status(None if word is None else ff.decode_status(word))

    # ------------------------------------------------------------------ Fireface 800
    def _build_ff800(self, lay):
        """TotalMix's Settings dialog for the 800: clock, sync inputs, levels and S/PDIF
        options. The clock and S/PDIF options start from what the device reports."""
        self.ff800_combos = {}
        self._build_fw_clock(lay, self._ff800_combo("clock", [(label, key) for key, label, _ in
                                                               ff800.CLOCK_SOURCES]),
                             ff800.STATUS_INPUTS)

        levels = QGroupBox("LEVELS")
        ll = QGridLayout(levels)
        ll.addWidget(QLabel("Line inputs"), 0, 0)
        ll.addWidget(self._ff800_combo("in_level", [(l, k) for k, l, *_ in ff800.IN_LEVELS]), 0, 1)
        ll.addWidget(QLabel("Line outputs"), 1, 0)
        ll.addWidget(self._ff800_combo("out_level", [(l, k) for k, l, *_ in ff800.OUT_LEVELS]),
                     1, 1)
        lay.addWidget(levels)

        opts = QGroupBox("OPTIONS")
        ol = QGridLayout(opts)
        rows = (("S/PDIF in", "spdif_in", (("Coaxial", "coax"), ("Optical", "optical"))),
                ("Optical out", "opt_out", (("ADAT", "adat"), ("S/PDIF", "spdif"))),
                ("S/PDIF out", "spdif_pro", (("Consumer", False), ("Professional", True))))
        for r, (label, key, items) in enumerate(rows):
            ol.addWidget(QLabel(label), r, 0)
            ol.addWidget(self._ff800_combo(key, items), r, 1)
        r = len(rows)
        for key, label in (("emphasis", "S/PDIF out emphasis"),
                           ("non_audio", "S/PDIF out non-audio"),
                           ("word_single", "Word clock out always single speed")):
            box = QCheckBox(label)
            box.toggled.connect(lambda on, k=key: self._set_hw(k, on))
            ol.addWidget(box, r, 0, 1, 2)
            self.ff800_combos[key] = box
            r += 1
        lay.addWidget(opts)
        self.refresh_ff800_settings()

    def _ff800_combo(self, key, items):
        combo = QComboBox()
        for text, value in items:
            combo.addItem(text, value)
        combo.activated.connect(lambda i: self._set_hw(key, combo.itemData(i)))
        self.ff800_combos[key] = combo
        return combo

    def refresh_ff800_settings(self):
        """Show st["hw"] (after it was filled in from the device's status)."""
        hw = self.st["hw"]
        for key, w in self.ff800_combos.items():
            w.blockSignals(True)
            if isinstance(w, QCheckBox):
                w.setChecked(hw[key])
            else:
                w.setCurrentIndex(max(0, w.findData(hw[key])))
            w.blockSignals(False)

    def update_ff800_status(self, words):
        """words: the 800's two status quadlets (Engine.status() dev_status, dev_status2), or
        None."""
        self._show_fw_status(None if words is None else ff800.decode_status(*words))

    # ------------------------------------------------------------------ HDSPe RayDAT
    def _build_raydat(self, lay):
        """TotalMix's Settings dialog for the RayDAT, from the driver's ALSA controls: clock mode,
        preferred sync reference, internal rate, S/PDIF and word clock options, input status.
        snd-hdspm and snd-hdspe name the controls differently (raydat.DRIVERS); options a driver
        lacks are hidden."""
        combos = {}
        for role in raydat.COMBOS:
            combo = QComboBox()
            combo.activated.connect(lambda i, r=role: self._write_role(r, i))
            combos[role] = combo
        self.rd_combos = combos
        clock = self._build_fw_clock(lay, combos["clock"], raydat.STATUS_INPUTS, "Clock mode")
        clock.addWidget(QLabel("Prefer"), 3, 0)
        clock.addWidget(combos["pref"], 3, 1)
        clock.addWidget(QLabel("Internal rate"), 4, 0)
        clock.addWidget(combos["internal"], 4, 1)

        opts = QGroupBox("OPTIONS")
        ol = QGridLayout(opts)
        self.rd_spdif_label = QLabel("S/PDIF in")
        ol.addWidget(self.rd_spdif_label, 0, 0)
        ol.addWidget(combos["spdif_in"], 0, 1)
        self.rd_toggles = {}
        for r, (name, label) in enumerate(raydat.TOGGLES, 1):
            box = QCheckBox(label)
            box.toggled.connect(lambda on, n=name: self._write(n, "on" if on else "off"))
            ol.addWidget(box, r, 0, 1, 2)
            self.rd_toggles[name] = box
        self.rd_info = QLabel()
        self.rd_info.setWordWrap(True)
        ol.addWidget(self.rd_info, len(raydat.TOGGLES) + 1, 0, 1, 2)
        lay.addWidget(opts)
        self._rd_ctrls = None

    def _write_role(self, role, value):
        name, _c = raydat.control(self._rd_ctrls, role)
        if name:
            self._write(name, value)

    def update_raydat(self, ctrls):
        """ctrls: the card's ALSA controls (Hardware.read()), or None if it is not found."""
        st = raydat.decode_status(ctrls)
        usable = True
        if st:
            drv = Hardware.card_driver(self.hw.card) or st["driver"]
            usable = raydat.driver_usable(drv, Hardware.pci_vendor(self.hw.card))
        self._rd_ctrls = ctrls if st else None
        self._updating = True
        try:
            for role, combo in self.rd_combos.items():
                _name, c = raydat.control(ctrls, role) if st else (None, None)
                # snd-hdspe can't change the internal rate while audio runs (as in hdspeconf)
                combo.setEnabled(bool(c) and usable and c["rw"]
                                 and not (role == "internal" and st["running"]
                                          and st["driver"] == raydat.HDSPE))
                if role == "spdif_in":
                    combo.setVisible(bool(c))
                    self.rd_spdif_label.setVisible(bool(c))
                if not c:
                    continue
                texts = [raydat.display_item(t) for t in c["items"]]
                if [combo.itemText(i) for i in range(combo.count())] != texts:
                    combo.clear()
                    combo.addItems(texts)
                if not combo.view().isVisible() and c["value"] is not None:
                    combo.setCurrentIndex(c["value"])
            for name, box in self.rd_toggles.items():
                c = ctrls.get(name) if st else None
                # before the card is found, show the snd-hdspm set
                box.setVisible(bool(c) or (st is None and name in raydat.HDSPM_TOGGLES))
                box.setEnabled(bool(c) and usable)
                if c and c["value"] is not None:
                    box.setChecked(bool(c["value"]))
        finally:
            self._updating = False
        if st is None:
            self.rd_info.setText("")
            self._show_fw_status(None)
            return
        if not usable:
            self.rd_info.setText(
                "<span style='color:#ff6b5b'>Settings off: snd-hdspm can't run this card."
                "</span>")
        else:
            info = "snd-hdspe driver" if st["driver"] == raydat.HDSPE else "snd-hdspm driver"
            if st["firmware"]:
                info += f", firmware {st['firmware']}"
            self.rd_info.setText(f"<span style='color:#8e8e93'>{info}</span>")
        self.cur_src.setText(st["source"])
        self.rate.setText(f"{st['rate'] / 1000:g} kHz" if st["rate"] else "—")
        shown = {label: (state, rate) for label, state, rate in st["inputs"]}
        for (led, rate), (label, *_unused) in zip(self.sync_widgets, raydat.STATUS_INPUTS):
            state, r = shown.get(label, (None, None))
            led.set_state(state)
            rate.setText(r.replace(" kHz", "k") if r else "—")
            # rows for the optional TCO module only show when the driver reports it
            for w in (led, rate, self._sync_labels[label]):
                w.setVisible(label in shown or label != "TCO")

    def _write(self, name, value):
        if not self._updating:
            self.hw.write(name, value)

    def _enable_pro(self):
        if not self._card_name:
            return
        r = QMessageBox.question(
            self, "Switch profile",
            "Switch the Digiface to the 'Pro Audio' profile?\n\n"
            "This lets apps record all 32 hardware inputs. The output device will be renamed to "
            "'…pro-output-0' (your existing combine-stream config already matches it). "
            "Audio may briefly drop out.")
        if r == QMessageBox.Yes:
            set_card_profile(self._card_name, "pro-audio")

    def update_hw(self, ctrls):
        self._updating = True
        try:
            if ctrls is None:
                self.rate.setText("<span style='color:#ff6b5b'>Digiface not found</span>")
                return
            src = ctrls.get("Sync Source")
            if src:
                if self.sync_src.count() != len(src["items"]):
                    self.sync_src.clear()
                    self.sync_src.addItems(src["items"])
                if not self.sync_src.view().isVisible():
                    self.sync_src.setCurrentIndex(src["value"])
            cs = ctrls.get("Current Sync Source")
            if cs:
                self.cur_src.setText(cs["items"][cs["value"]])
            r = ctrls.get("Current Rate")
            if r:
                self.rate.setText(f"{r['value'] / 1000:g} kHz")
            for i, (fmt, led, rate, out) in enumerate(self.port_widgets):
                n = i + 1
                sync = ctrls.get(f"Input {n} Sync")
                state = sync["items"][sync["value"]] if sync else None
                led.set_state(state)
                f = ctrls.get(f"Input {n} Format")
                locked = state in ("Lock", "Sync")
                fmt.setText(f["items"][f["value"]] if f and locked else "—")
                ir = ctrls.get(f"Input {n} Rate")
                rate.setText(f"{ir['value'] / 1000:g}k" if ir and locked and ir["value"] else "—")
                of = ctrls.get(f"Output {n} Format")
                if of and not out.view().isVisible():
                    out.setCurrentIndex(of["value"])
        finally:
            self._updating = False

    def update_hw_mixer(self, status):
        """status: Engine.status() dict, or None if the engine is not running."""
        if status is None:
            self.hw_label.setText("—")
            return
        text = hw_state_text(self.dev, status["hw"])
        if status["hw"] in ("active", "no-nodes"):
            if self.dev.max_routes:
                text += f"<br>{status['hw_nodes']} of {self.dev.max_routes} routes in use"
            else:
                text += f"<br>{status['hw_nodes']} routes in use"
            if status["hw_levels"]:
                text += " · meters from the interface"
        self.hw_label.setText(text)

    def update_profile(self, name, profile, has_in):
        self._card_name = name
        if not name:
            self.profile.setText("Digiface not present in PipeWire")
            self.pro_btn.setVisible(False)
            return
        ok = "<span style='color:#4cd964'>inputs available for recording</span>" if has_in else \
            "<span style='color:#ffcc00'>no input device — apps can't record the inputs " \
            "(monitoring still works)</span>"
        self.profile.setText(f"Active: <b>{profile}</b><br>{ok}")
        self.pro_btn.setVisible(not has_in)
