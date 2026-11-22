"""Theme files: the default lists every value, a user theme only what it changes."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
HAVE_QT = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ThemeTest(unittest.TestCase):
    def setUp(self):
        from openface_mixer import theme
        self.theme = theme
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = mock.patch("openface_mixer.config.CONFIG_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name, text):
        path = self.dir / "themes" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_builtin_themes_load_cleanly(self):
        for key in ("deep-ocean", "graphite"):
            values, problems = self.theme.load_theme(key)
            self.assertEqual(problems, [], key)
            self.assertEqual(len(values["colors"]["groups"]), 4)

    def test_small_reader_matches_tomllib(self):
        # the reader used on Python 3.10, which has no tomllib
        if self.theme.tomllib is None:
            self.skipTest("no tomllib to compare with")
        for path in sorted(self.theme.BUILTIN_DIR.glob("*.toml")):
            text = path.read_text()
            self.assertEqual(self.theme._parse_toml(text), self.theme.tomllib.loads(text), path)

    def test_user_theme_changes_only_what_it_lists(self):
        path = self.write("mine.toml", 'name = "Mine"\n[colors]\naccent = "#ff8800"\n'
                                       '[sizes]\nstrip_width = 70\n')
        self.assertIn("mine", self.theme.theme_files())
        self.assertEqual(self.theme.theme_name(path), "Mine")
        values, problems = self.theme.load_theme("mine")
        default, _ = self.theme.load_theme("deep-ocean")
        self.assertEqual(problems, [])
        self.assertEqual(values["colors"]["accent"], "#ff8800")
        self.assertEqual(values["sizes"]["strip_width"], 70)
        self.assertEqual(values["colors"]["alert"], default["colors"]["alert"])
        self.assertEqual(values["fonts"], default["fonts"])

    def test_bad_values_are_reported_and_left_at_the_default(self):
        self.write("bad.toml", '[colors]\naccent = "not a colour"\nshiny = "#fff"\n'
                               '[sizes]\nstrip_width = "wide"\n')
        values, problems = self.theme.load_theme("bad")
        default, _ = self.theme.load_theme("deep-ocean")
        self.assertEqual(values, default)
        self.assertEqual(len(problems), 3, problems)

    def test_sizes_must_be_usable(self):
        self.write("tiny.toml", '[sizes]\nstrip_width = 0\nstrip_spacing = 0\n[fonts]\nsize = -3\n')
        values, problems = self.theme.load_theme("tiny")
        self.assertEqual(values["sizes"]["strip_width"], 62)
        self.assertEqual(values["sizes"]["strip_spacing"], 0)
        self.assertEqual(len(problems), 2, problems)

    def test_broken_file_falls_back_to_the_default(self):
        self.write("broken.toml", "[colors\naccent = \n")
        values, problems = self.theme.load_theme("broken")
        self.assertEqual(values, self.theme.load_theme("deep-ocean")[0])
        self.assertEqual(len(problems), 1)

    def test_channel_colours_listed_replace_the_menu(self):
        self.write("few.toml", '[colors.channels]\nRust = "#b7410e"\nSea = "#2e8b57"\n')
        values, problems = self.theme.load_theme("few")
        self.assertEqual(problems, [])
        self.assertEqual(list(values["colors"]["channels"]), ["Rust", "Sea"])
        self.write("none.toml", '[colors.channels]\nRust = "rusty"\n')
        values, problems = self.theme.load_theme("none")
        self.assertEqual(len(values["colors"]["channels"]), 10)     # never an empty menu
        self.assertEqual(len(problems), 1)

    def test_new_theme_is_a_named_copy_and_never_overwrites(self):
        path = self.theme.new_theme('Late "Night"', self.theme.BUILTIN_DIR / "graphite.toml")
        self.assertEqual(path, self.dir / "themes" / "late-night.toml")
        self.assertEqual(self.theme.theme_name(path), "Late 'Night'")
        self.assertEqual(self.theme.load_theme(path=path)[1], [])
        with self.assertRaises(FileExistsError):
            self.theme.new_theme("Late Night")


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ChosenThemeTest(unittest.TestCase):
    def colours(self, env):
        code = ("from openface_mixer import theme; "
                "print(theme.THEME_NAME, theme.ACCENT.name(), theme.SIZES['strip_width'])")
        with tempfile.TemporaryDirectory() as home:
            extra, env = env, {**os.environ, "HOME": home, "XDG_CONFIG_HOME": home}
            env.pop("OPENFACE_THEME", None)
            env.update(extra)
            out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                                 capture_output=True, text=True, check=True)
        return out.stdout.split()

    def test_default_and_override(self):
        self.assertEqual(self.colours({}), ["Deep", "Ocean", "#2ad4ff", "62"])
        self.assertEqual(self.colours({"OPENFACE_THEME": "graphite"}),
                         ["Graphite", "#ff9f43", "62"])


if __name__ == "__main__":
    unittest.main()
