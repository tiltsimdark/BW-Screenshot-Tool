"""Unit tests for screenshot_toolV7 (no screen needed).

Run from the repository folder:  python -m unittest discover tests
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("screenshot_tool", ROOT / "screenshot_toolV7.py")
tool = importlib.util.module_from_spec(_spec)
sys.modules["screenshot_tool"] = tool
_spec.loader.exec_module(tool)


class HotkeyTests(unittest.TestCase):
    def test_parse_and_format(self):
        self.assertEqual(tool.parse_hotkey("ctrl+shift+print"), (frozenset({"Ctrl", "Shift"}), "Print"))
        self.assertEqual(tool.normalize_hotkey("Shift + Ctrl + PrtSc"), "Ctrl+Shift+Print")
        self.assertEqual(tool.normalize_hotkey("Alt + Print Screen"), "Alt+Print")
        self.assertEqual(tool.normalize_hotkey("control+alt+f"), "Ctrl+Alt+F")
        self.assertEqual(tool.normalize_hotkey("win+f12"), "Win+F12")
        self.assertEqual(tool.normalize_hotkey(""), "")
        self.assertIsNone(tool.parse_hotkey("   "))

    def test_invalid_hotkeys(self):
        for text in ("Ctrl+", "Hyper+Print", "Ctrl+NoSuchKey", "+Print"):
            with self.assertRaises(ValueError, msg=text):
                tool.parse_hotkey(text)

    def test_display_label(self):
        self.assertEqual(tool.display_hotkey("Shift+Print"), "Shift + Print Screen")
        self.assertEqual(tool.display_hotkey(""), "No shortcut")
        self.assertEqual(tool.hotkey_label(""), "")
        label = tool.hotkey_label("Ctrl+Print")
        self.assertEqual(tool.normalize_hotkey(label), "Ctrl+Print")  # labels parse back

    def test_typing_keys_need_a_modifier(self):
        self.assertIsNotNone(tool.hotkey_problem(tool.parse_hotkey("Q")))
        self.assertIsNotNone(tool.hotkey_problem(tool.parse_hotkey("Shift+Q")))
        self.assertIsNone(tool.hotkey_problem(tool.parse_hotkey("Ctrl+Q")))
        self.assertIsNone(tool.hotkey_problem(tool.parse_hotkey("Print")))
        self.assertIsNone(tool.hotkey_problem(tool.parse_hotkey("F9")))
        for key in ("Delete", "Home", "End", "Insert", "PageUp", "PageDown", "Space"):
            self.assertIsNotNone(tool.hotkey_problem(tool.parse_hotkey(key)), key)
            self.assertIsNone(tool.hotkey_problem(tool.parse_hotkey("Ctrl+" + key)), key)

    def test_tk_sequences(self):
        self.assertEqual(tool.tk_sequence(tool.parse_hotkey("Print")), "<KeyPress-Print>")
        self.assertEqual(tool.tk_sequence(tool.parse_hotkey("Shift+Print"), release=True),
                         "<Shift-KeyRelease-Print>")
        self.assertEqual(tool.tk_sequence(tool.parse_hotkey("Ctrl+Shift+a")), "<Control-Shift-KeyPress-A>")
        self.assertIsNone(tool.tk_sequence(tool.parse_hotkey("Win+S")))

    def test_tk_keysyms(self):
        self.assertEqual(tool.key_from_tk_keysym("Print"), "Print")
        self.assertEqual(tool.key_from_tk_keysym("Sys_Req"), "Print")
        self.assertEqual(tool.key_from_tk_keysym("Prior"), "PageUp")
        self.assertEqual(tool.key_from_tk_keysym("a"), "A")
        self.assertEqual(tool.key_from_tk_keysym("F5"), "F5")
        self.assertIsNone(tool.key_from_tk_keysym("Shift_L"))

    def test_keysym_variants_while_modifiers_are_held(self):
        self.assertEqual(tool.key_from_tk_keysym("Cancel"), "Pause")  # Ctrl+Pause on Windows
        self.assertEqual(tool.key_from_tk_keysym("Break"), "Pause")  # Ctrl+Pause on X11

    def test_event_modifiers(self):
        self.assertEqual(tool.event_modifiers(0), set())
        self.assertEqual(tool.event_modifiers(0x1 | 0x4), {"Shift", "Ctrl"})
        alt = 0x20000 if tool.IS_WINDOWS else (0x10 if tool.IS_MAC else 0x8)
        self.assertEqual(tool.event_modifiers(alt), {"Alt"})
        self.assertEqual(tool.event_modifiers(0x2), set())  # Caps Lock is ignored

    def test_windows_virtual_key_codes(self):
        self.assertEqual(tool.KEY_TABLE["Print"][0], 0x2C)
        self.assertEqual(tool.KEY_TABLE["F1"][0], 0x70)
        self.assertEqual(tool.KEY_TABLE["F12"][0], 0x7B)
        self.assertEqual(tool.KEY_TABLE["A"][0], 0x41)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.path = self.folder / "screenshot_settings.json"

    def test_defaults_when_missing(self):
        settings = tool.Settings(self.path)
        settings.load()
        self.assertEqual(settings.values, tool.default_settings())
        self.assertIsNone(settings.problem)

    def test_v6_settings_file_still_loads(self):
        self.path.write_text(json.dumps({
            "master_folder": "C:/Screens", "window_geometry": "400x500+760+290", "window_state": {}}))
        settings = tool.Settings(self.path)
        settings.load()
        self.assertEqual(settings["master_folder"], "C:/Screens")
        self.assertEqual(settings["window_geometry"], "400x500+760+290")
        self.assertEqual(settings["hotkey_region"], "Print")

    def test_bad_values_are_ignored_or_clamped(self):
        self.path.write_text(json.dumps({
            "master_folder": 42, "selection_width": 99, "dim_percent": -5, "selection_color": "red",
            "image_format": "JPEG", "last_region": [1, 2, 0, 5], "hotkey_region": "Nope+X",
            "copy_to_clipboard": "yes", "recent_folders": ["a", "", 3, "a", "b"], "unknown": 1}))
        settings = tool.Settings(self.path)
        settings.load()
        self.assertEqual(settings["master_folder"], "")
        self.assertEqual(settings["selection_width"], 10)
        self.assertEqual(settings["dim_percent"], 0)
        self.assertEqual(settings["selection_color"], "#FF0000")
        self.assertEqual(settings["image_format"], "jpg")
        self.assertIsNone(settings["last_region"])
        self.assertEqual(settings["hotkey_region"], "Print")
        self.assertIs(settings["copy_to_clipboard"], True)
        self.assertEqual(settings["recent_folders"], ["a", "b"])
        self.assertNotIn("unknown", settings.values)

    def test_corrupt_file_uses_defaults_and_keeps_a_backup(self):
        self.path.write_text("{not json")
        settings = tool.Settings(self.path)
        settings.load()
        self.assertEqual(settings.values, tool.default_settings())
        self.assertIsNotNone(settings.problem)
        self.assertTrue((self.folder / "screenshot_settings.json.bak").exists())

    def test_save_round_trip(self):
        settings = tool.Settings(self.path)
        settings["selection_color"] = "#00aaff"
        settings["last_region"] = (10, 20, 30, 40)
        settings["hotkey_fullscreen"] = "shift+ctrl+f9"
        settings.save()
        again = tool.Settings(self.path)
        again.load()
        self.assertEqual(again["selection_color"], "#00AAFF")
        self.assertEqual(again["last_region"], [10, 20, 30, 40])
        self.assertEqual(again["hotkey_fullscreen"], "Ctrl+Shift+F9")

    def test_setting_an_invalid_value_raises(self):
        settings = tool.Settings(self.path)
        with self.assertRaises(ValueError):
            settings["selection_color"] = "not a colour"
        with self.assertRaises(ValueError):
            settings["file_pattern"] = "   "

    def test_each_install_has_its_own_settings(self):
        original = tool.app_directory, tool.user_config_directory
        try:
            config = self.folder / "config"
            tool.user_config_directory = lambda: config
            for name in ("install_a", "install_b"):
                tool.app_directory = lambda name=name: self.folder / name
                (self.folder / name).mkdir()
                settings = tool.Settings()
                settings["master_folder"] = f"/shots/{name}"
                settings.save()
            for name in ("install_a", "install_b"):
                tool.app_directory = lambda name=name: self.folder / name
                settings = tool.Settings()
                settings.load()
                self.assertEqual(settings["master_folder"], f"/shots/{name}")
                self.assertEqual(settings.path, self.folder / name / tool.SETTINGS_FILE)
        finally:
            tool.app_directory, tool.user_config_directory = original

    def test_falls_back_to_user_folder_when_program_folder_is_read_only(self):
        original = tool.app_directory, tool.user_config_directory
        try:
            blocker = self.folder / "not_a_folder"
            blocker.write_text("")
            tool.app_directory = lambda: blocker  # can't create files "inside" a file
            tool.user_config_directory = lambda: self.folder / "config"
            settings = tool.Settings()
            settings["master_folder"] = "/somewhere"
            settings.save()
            self.assertEqual(settings.saved_to, self.folder / "config" / tool.SETTINGS_FILE)
            again = tool.Settings()
            again.load()
            self.assertEqual(again["master_folder"], "/somewhere")
        finally:
            tool.app_directory, tool.user_config_directory = original

    def test_one_failed_save_does_not_move_settings_for_good(self):
        original = tool.app_directory, tool.user_config_directory
        try:
            program = self.folder / "program"
            program.mkdir()
            tool.app_directory = lambda: program
            tool.user_config_directory = lambda: self.folder / "config"
            settings = tool.Settings()
            settings.save()
            real_replace = tool.os.replace
            calls = []

            def flaky_replace(source, target):
                if not calls and Path(target).parent == program:
                    calls.append(target)
                    raise OSError("locked")
                return real_replace(source, target)
            tool.os.replace = flaky_replace
            try:
                settings["master_folder"] = "/first"
                settings.save()  # goes to the config folder once
            finally:
                tool.os.replace = real_replace
            settings["master_folder"] = "/second"
            settings.save()  # back next to the program
            again = tool.Settings()
            again.load()
            self.assertEqual(again["master_folder"], "/second")
            self.assertEqual(again.saved_to, program / tool.SETTINGS_FILE)
        finally:
            tool.app_directory, tool.user_config_directory = original

    def test_new_copy_starts_from_shared_settings_but_saves_its_own(self):
        original = tool.app_directory, tool.user_config_directory
        try:
            config = self.folder / "config"
            config.mkdir()
            (config / tool.SETTINGS_FILE).write_text(json.dumps({"master_folder": "/shared"}))
            tool.user_config_directory = lambda: config
            tool.app_directory = lambda: self.folder / "copy"
            (self.folder / "copy").mkdir()
            settings = tool.Settings()
            settings.load()
            self.assertEqual(settings["master_folder"], "/shared")
            settings["master_folder"] = "/mine"
            settings.save()
            self.assertTrue((self.folder / "copy" / tool.SETTINGS_FILE).is_file())
            self.assertIn("/shared", (config / tool.SETTINGS_FILE).read_text())
        finally:
            tool.app_directory, tool.user_config_directory = original

    def test_infinite_numbers_cannot_stop_startup(self):
        self.path.write_text('{"jpeg_quality": 1e999, "selection_width": Infinity, '
                             '"dim_percent": NaN, "last_region": [1e999, 0, 10, 10]}')
        settings = tool.Settings(self.path)
        settings.load()
        self.assertEqual(settings["jpeg_quality"], 90)
        self.assertEqual(settings["selection_width"], 2)
        self.assertEqual(settings["dim_percent"], 40)
        self.assertIsNone(settings["last_region"])
        with self.assertRaises(ValueError):
            settings["jpeg_quality"] = "1e999"


class FolderTests(unittest.TestCase):
    def test_validate_folder_name(self):
        for good in ("", "Project", "Project A/Step 1", r"Client\Release 2.0", "  spaced  "):
            self.assertIsNone(tool.validate_folder_name(good), good)
        for bad in ("a|b", "what?", "con", "Folder/LPT1.txt", "../outside", "trailing.", "bad<name>"):
            self.assertIsNotNone(tool.validate_folder_name(bad), bad)

    def test_empty_folder_name_goes_to_screenshots(self):
        self.assertEqual(tool.resolve_output_folder("/master", ""), Path("/master", "Screenshots"))
        self.assertEqual(tool.resolve_output_folder("/master", "  "), Path("/master", "Screenshots"))
        self.assertEqual(tool.resolve_output_folder("/master", "Run 1"), Path("/master", "Run 1"))

    def test_empty_master_uses_default(self):
        expected = Path(tool.default_master_folder(), "X")
        self.assertEqual(tool.resolve_output_folder("", "X"), expected)

    def test_absolute_folder_name_is_used_as_is(self):
        absolute = os.path.abspath(os.sep + "elsewhere")
        self.assertEqual(tool.resolve_output_folder("/master", absolute), Path(absolute))

    def test_build_filename(self):
        moment = datetime(2026, 10, 22, 9, 5, 7)
        self.assertEqual(tool.build_filename(tool.DEFAULT_FILE_PATTERN, "png", moment),
                         "screenshot_20261022_090507.png")
        self.assertEqual(tool.build_filename("shot %H:%M/%S", "jpg", moment), "shot 09_05_07.jpg")
        self.assertEqual(tool.build_filename("...", "png", moment), "screenshot.png")

    def test_unique_path_never_overwrites(self):
        folder = Path(tempfile.mkdtemp())
        first = tool.unique_path(folder, "a.png")
        first.write_text("x")
        second = tool.unique_path(folder, "a.png")
        second.write_text("x")
        third = tool.unique_path(folder, "a.png")
        self.assertEqual([p.name for p in (first, second, third)], ["a.png", "a_1.png", "a_2.png"])
        # a name an open editor will save to counts as taken
        self.assertEqual(tool.unique_path(folder, "b.png", taken={folder / "b.png"}).name, "b_1.png")

    def test_format_for_path(self):
        self.assertEqual(tool.format_for_path("x.JPEG"), "jpg")
        self.assertEqual(tool.format_for_path("x.bmp"), "bmp")
        self.assertEqual(tool.format_for_path("x.gif"), "png")
        self.assertEqual(tool.format_for_path("x"), "png")


class ScreenGrabTests(unittest.TestCase):
    @staticmethod
    def _numbered(width, height):
        image = Image.new("RGB", (width, height))
        image.putdata([(x % 256, y % 256, 0) for y in range(height) for x in range(width)])
        return image

    def test_crop_with_monitor_left_of_primary(self):
        # Two monitors: one at x=-200 and the primary at x=0 (Windows virtual screen).
        grab = tool.ScreenGrab(self._numbered(400, 100), (-200, 0), (400, 100))
        crop = grab.crop((-150, 10, 20, 30))  # starts on the left monitor
        self.assertEqual(crop.size, (20, 30))
        self.assertEqual(crop.getpixel((0, 0)), (50, 10, 0))

    def test_crop_scales_for_retina(self):
        grab = tool.ScreenGrab(self._numbered(200, 200), (0, 0), (100, 100))
        crop = grab.crop((10, 20, 30, 40))
        self.assertEqual(crop.size, (60, 80))
        self.assertEqual(crop.getpixel((0, 0)), (20, 40, 0))

    def test_crop_is_clamped_and_rejects_offscreen(self):
        grab = tool.ScreenGrab(self._numbered(100, 100), (0, 0), (100, 100))
        self.assertEqual(grab.crop((90, 90, 50, 50)).size, (10, 10))
        with self.assertRaises(ValueError):
            grab.crop((500, 500, 10, 10))
        for offscreen in ((-1500, 20, 800, 30), (10, -900, 30, 400)):  # left of / above every screen
            with self.assertRaises(ValueError):
                grab.crop(offscreen)

    def test_tiny_region_on_downscaled_grab_is_at_least_one_pixel(self):
        grab = tool.ScreenGrab(self._numbered(75, 75), (0, 0), (100, 100))
        self.assertEqual(grab.crop((2, 2, 1, 1)).size, (1, 1))


class RectMinusTests(unittest.TestCase):
    @staticmethod
    def _area(rects):
        return sum((r - l) * (b - t) for l, t, r, b in rects)

    def test_parts_cover_exactly_the_difference(self):
        cases = [((0, 0, 100, 80), (20, 10, 60, 50)), ((0, 0, 100, 80), (50, 40, 200, 200)),
                 ((10, 10, 20, 20), (0, 0, 100, 100)), ((0, 0, 10, 10), (20, 20, 30, 30)),
                 ((0, 0, 100, 80), (0, 0, 100, 40))]
        for a, b in cases:
            parts = tool.rect_minus(a, b)
            pixels = {(x, y) for l, t, r, bottom in parts for x in range(l, r) for y in range(t, bottom)}
            expected = {(x, y) for x in range(a[0], a[2]) for y in range(a[1], a[3])
                        if not (b[0] <= x < b[2] and b[1] <= y < b[3])}
            self.assertEqual(pixels, expected, (a, b))
            self.assertEqual(self._area(parts), len(expected), "parts must not overlap")


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.base = Image.new("RGB", (200, 120), "white")

    def test_render_does_not_change_the_original(self):
        tool.render_shapes(self.base, [tool.Shape("rect", [(10, 10), (50, 50)])])
        self.assertEqual(self.base.getpixel((10, 10)), (255, 255, 255))

    def test_rectangle_and_ellipse(self):
        image = tool.render_shapes(self.base, [tool.Shape("rect", [(50, 50), (10, 10)], "#0000FF", 2)])
        self.assertEqual(image.getpixel((10, 30)), (0, 0, 255))
        self.assertEqual(image.getpixel((30, 30)), (255, 255, 255))
        image = tool.render_shapes(self.base, [tool.Shape("ellipse", [(10, 10), (110, 110)], "#00FF00", 3)])
        self.assertEqual(image.getpixel((60, 11)), (0, 255, 0))

    def test_arrow_line_freehand_text_counter_draw_something(self):
        for shape in (tool.Shape("arrow", [(10, 60), (190, 60)]),
                      tool.Shape("line", [(10, 60), (190, 60)]),
                      tool.Shape("freehand", [(10, 10), (40, 60), (90, 20)]),
                      tool.Shape("text", [(10, 10)], text="Hello"),
                      tool.Shape("counter", [(100, 60)], number=7)):
            image = tool.render_shapes(self.base, [shape])
            self.assertNotEqual(image.getbbox(), None)
            self.assertLess(min(image.convert("L").getdata()), 200, shape.tool)

    def test_highlight_and_obfuscate(self):
        image = tool.render_shapes(self.base, [tool.Shape("highlight", [(0, 0), (50, 50)])])
        self.assertEqual(image.getpixel((10, 10)), (255, 242, 0))
        self.assertEqual(image.getpixel((100, 100)), (255, 255, 255))
        stripes = Image.new("RGB", (60, 60), "white")
        stripes.paste((0, 0, 0), (0, 0, 60, 60), mask=Image.eval(
            Image.linear_gradient("L").resize((60, 60)), lambda v: 255 if (v // 8) % 2 else 0))
        pixelated = tool.render_shapes(stripes, [tool.Shape("obfuscate", [(0, 0), (60, 60)], size=3)])
        self.assertNotEqual(list(pixelated.getdata()), list(stripes.getdata()))

    def test_crop_and_later_shapes_use_new_coordinates(self):
        image = tool.render_shapes(self.base, [
            tool.Shape("crop", [(20, 10), (120, 70)]),
            tool.Shape("rect", [(0, 0), (99, 59)], "#FF0000", 1),
        ])
        self.assertEqual(image.size, (100, 60))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))

    def test_degenerate_shapes_are_ignored(self):
        image = tool.render_shapes(self.base, [tool.Shape("crop", [(5, 5), (5, 50)]),
                                               tool.Shape("highlight", [(5, 5), (5, 5)])])
        self.assertEqual(image.size, self.base.size)


if __name__ == "__main__":
    unittest.main()
