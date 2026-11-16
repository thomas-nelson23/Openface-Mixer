import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openface_mixer import devices


def usb_tree(root, devs):
    """Fake /sys/bus/usb/devices with (name, vendor, product id, product string) entries."""
    for name, vid, pid, product in devs:
        d = Path(root) / name
        d.mkdir()
        (d / "idVendor").write_text(f"{vid:04x}\n")
        (d / "idProduct").write_text(f"{pid:04x}\n")
        if product is not None:
            (d / "product").write_text(product + "\n")
    (Path(root) / "1-1:1.0").mkdir()          # an interface node: no idVendor


class ClassCompliantDigifaceTest(unittest.TestCase):
    def find(self, devs):
        with tempfile.TemporaryDirectory() as root:
            usb_tree(root, devs)
            return devices.find_other_digiface(Path(root))

    def test_standard_firmware_is_not_flagged(self):
        self.assertIsNone(self.find([("1-1", 0x2a39, 0x3f8c, "Digiface USB (23456789)"),
                                     ("1-2", 0x2a39, 0x3fa0, "Digiface USB")]))

    def test_class_compliant_pid(self):
        self.assertEqual(self.find([("1-1", 0x2a39, 0x3fb2, None)]), 0x3fb2)

    def test_unknown_digiface_pid_by_name(self):
        self.assertEqual(self.find([("1-1", 0x2a39, 0x3fc0, "Digiface USB (2345)")]), 0x3fc0)

    def test_other_rme_and_other_vendors_ignored(self):
        self.assertIsNone(self.find([("1-1", 0x2a39, 0x0101, "ARC USB"),
                                     ("1-2", 0x1234, 0x3fb2, "Digiface USB")]))

    def test_missing_root(self):
        self.assertIsNone(devices.find_other_digiface(Path("/nonexistent/usb")))

    def test_hint(self):
        dev = devices.DEVICES["digiface"]
        with mock.patch.object(devices, "find_other_digiface", return_value=0x3fb2):
            hint = dev.missing_hint()
        self.assertIn("2a39:3fb2", hint)
        self.assertIn("Class Compliant", hint)
        with mock.patch.object(devices, "find_other_digiface", return_value=None):
            self.assertEqual(dev.missing_hint(), "")


if __name__ == "__main__":
    unittest.main()
