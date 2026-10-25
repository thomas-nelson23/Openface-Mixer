import unittest

from openface_mixer import model as m
from openface_mixer.devices import DEVICES, group_title


class CustomNamesTest(unittest.TestCase):
    def test_custom_name_follows_names_button(self):
        st = m.default_state()
        st["names"]["in:0"] = "Kick"
        self.assertEqual(m.display_name(st, "in:0", "AD1 1"), "Kick")
        self.assertEqual(m.display_name(st, "in:1", "AD1 2"), "AD1 2")
        st["custom_names"] = False
        self.assertEqual(m.display_name(st, "in:0", "AD1 1"), "AD1 1")

    def test_old_state_gets_names_and_hidden(self):
        st = m.default_state()
        for k in ("names", "custom_names", "hidden", "colors", "icons"):
            del st[k]
        st = m.upgrade_state(st)
        self.assertEqual((st["names"], st["custom_names"], st["hidden"], st["colors"],
                          st["icons"]), ({}, True, [], {}, {}))

    def test_channel_icon(self):
        st = m.default_state()
        self.assertIsNone(m.strip_icon(st, "in:0"))
        m.set_strip_icon(st, "in:0", "guitar")
        self.assertEqual(m.strip_icon(st, "in:0"), "guitar")
        m.set_strip_icon(st, "in:0", None)
        self.assertEqual(st["icons"], {})

    def test_every_listed_icon_is_bundled(self):
        # theme imports Qt, which the test runner may not have: read the list from the source
        import ast
        from pathlib import Path
        pkg = Path(__file__).resolve().parent.parent / "openface_mixer"
        tree = ast.parse((pkg / "theme.py").read_text())
        icons = next(ast.literal_eval(node.value) for node in tree.body
                     if isinstance(node, ast.Assign) and node.targets[0].id == "CHANNEL_ICONS")
        names = [name for _section, entries in icons for name, _label in entries]
        self.assertGreater(len(names), 40)
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertTrue((pkg / "icons" / f"{name}.svg").is_file(), name)

    def test_channel_colour(self):
        st = m.default_state()
        self.assertIsNone(m.strip_color(st, "in:0"))
        m.set_strip_color(st, "in:0", "#ff2a4f")
        self.assertEqual(m.strip_color(st, "in:0"), "#ff2a4f")
        m.set_strip_color(st, "in:0", None)
        self.assertEqual(st["colors"], {})

    def test_names_are_not_part_of_a_mix(self):
        st = m.default_state()
        st["names"]["out:0"] = "Mains"
        st["hidden"] = ["AD4"]
        st["colors"]["out:0"] = "#56e39f"
        st["icons"]["out:0"] = "speaker"
        for k in ("names", "hidden", "colors", "icons"):
            self.assertNotIn(k, m.extract_mix(st))


class ChannelGroupsTest(unittest.TestCase):
    def titles(self, key):
        return [group_title(g) for g in DEVICES[key].channel_groups()]

    def test_groups_per_device(self):
        self.assertEqual(self.titles("digiface"), ["ADAT 1", "ADAT 2", "ADAT 3", "ADAT 4", "Phones"])
        self.assertEqual(self.titles("ff802"), ["Analog", "AES", "ADAT A", "ADAT B", "Phones"])
        self.assertEqual(self.titles("ff800"), ["Analog", "S/PDIF", "ADAT 1", "ADAT 2", "Phones"])
        self.assertEqual(self.titles("raydat"),
                         ["AES", "S/PDIF", "ADAT 1", "ADAT 2", "ADAT 3", "ADAT 4"])

    def test_group_keeps_its_port_at_double_speed(self):
        dev = DEVICES["digiface"]
        self.assertEqual(dev.channel_group("in", 15, 2), "AD4")    # 16 ADAT channels at 2x
        self.assertEqual(dev.channel_group("out", 16, 2), "Ph")


if __name__ == "__main__":
    unittest.main()
