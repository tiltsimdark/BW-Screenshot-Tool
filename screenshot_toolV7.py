#!/usr/bin/env python3
"""BW Screenshot Tool (V7) - a Greenshot-style screenshot helper.

* Region capture over a frozen image of every monitor, with full-screen
  crosshair lines, a magnifier, live coordinates and the selection size.
* Capture the last region again, or all screens at once.
* Saves into <master folder>/<folder name> (or "Screenshots"), can copy the
  image to the clipboard and open it in the built-in annotation editor.
* Global hotkeys (Print Screen, Shift+Print Screen, Ctrl+Print Screen).

Windows is the main target; Linux (X11) and macOS work too.
"""

from __future__ import annotations

import ctypes
import io
import json
import math
import os
import queue
import re
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageGrab, ImageTk

APP_NAME = "BW Screenshot Tool"
APP_VERSION = "7.0"
SETTINGS_FILE = "screenshot_settings.json"
DEFAULT_FOLDER_NAME = "Screenshots"
DEFAULT_FILE_PATTERN = "screenshot_%Y%m%d_%H%M%S"
IMAGE_FORMATS = ("png", "jpg", "bmp")
MAX_RECENT_FOLDERS = 10
HIDE_DELAY_MS = 250  # time the main window needs to disappear before a grab

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

_RESAMPLING = getattr(Image, "Resampling", Image)
LANCZOS = _RESAMPLING.LANCZOS
BILINEAR = _RESAMPLING.BILINEAR
NEAREST = _RESAMPLING.NEAREST

# Palette
BG = "#F3F4F6"
CARD = "#FFFFFF"
BORDER = "#D9DDE3"
TEXT = "#1F2937"
MUTED = "#6B7280"
ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
ACCENT_LIGHT = "#E8F0FE"
HOVER = "#F0F3F8"
ERROR_TEXT = "#B91C1C"
STATUS_COLORS = {
    "info": ("#E9EDF3", "#374151"),
    "success": ("#DCFCE7", "#166534"),
    "warning": ("#FEF3C7", "#92400E"),
    "error": ("#FEE2E2", "#991B1B"),
}
GEOMETRY_RE = re.compile(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)")


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def enable_dpi_awareness():
    """Ask Windows for real pixel coordinates.

    Without this, Windows scales the program on 125%/150% displays and the
    area you select no longer matches the pixels that get captured.  It has
    to run before the Tk root window is created.
    """
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _windows_virtual_screen():
    metric = ctypes.windll.user32.GetSystemMetrics
    return metric(76), metric(77), metric(78), metric(79)


def virtual_screen_rect(root):
    """(x, y, width, height) of the area covered by all monitors."""
    if IS_WINDOWS:
        return _windows_virtual_screen()
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


@dataclass
class ScreenGrab:
    """A frozen image of every screen and how it maps to screen coordinates."""

    image: Image.Image
    origin: tuple  # screen coordinate of the image's top-left corner
    size: tuple  # size in screen (Tk) units; differs from image.size on Retina

    @property
    def scale(self):
        return self.image.width / self.size[0], self.image.height / self.size[1]

    def crop(self, region):
        """Cut out an (x, y, width, height) region given in screen coordinates."""
        x, y, width, height = region
        sx, sy = self.scale
        left = max(0, round((x - self.origin[0]) * sx))
        top = max(0, round((y - self.origin[1]) * sy))
        right = min(self.image.width, round((x - self.origin[0] + width) * sx))
        bottom = min(self.image.height, round((y - self.origin[1] + height) * sy))
        if right <= left or bottom <= top:
            raise ValueError("the region is outside the visible screens")
        return self.image.crop((left, top, right, bottom))


def grab_screens(root):
    """Capture every monitor in one image."""
    if IS_WINDOWS:
        image = ImageGrab.grab(all_screens=True)
        x, y, _, _ = _windows_virtual_screen()
        return ScreenGrab(image.convert("RGB"), (x, y), image.size)
    image = ImageGrab.grab()
    size = (root.winfo_screenwidth(), root.winfo_screenheight())
    return ScreenGrab(image.convert("RGB"), (0, 0), size)


def get_monitors(grab):
    """Monitor rectangles (x, y, width, height) in screen coordinates."""
    everything = (grab.origin[0], grab.origin[1], grab.size[0], grab.size[1])
    if IS_MAC:  # only the main display is captured on macOS
        return [everything]
    try:
        import screeninfo

        monitors = [(m.x, m.y, m.width, m.height) for m in screeninfo.get_monitors()]
    except Exception:
        monitors = []
    return monitors or [everything]


def copy_image_to_clipboard(image):
    if IS_WINDOWS:
        _copy_image_windows(image)
    elif IS_MAC:
        _copy_image_mac(image)
    else:
        _copy_image_linux(image)


def _copy_image_windows(image):
    from ctypes import wintypes

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "BMP")
    data = buffer.getvalue()[14:]  # CF_DIB is a BMP without its file header

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    for _ in range(10):  # another program may be holding the clipboard
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError("the clipboard is in use by another program")
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        if not handle:
            raise MemoryError("not enough memory to copy the image")
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(8, handle):  # CF_DIB
            kernel32.GlobalFree(handle)
            raise OSError("Windows refused the clipboard data")
    finally:
        user32.CloseClipboard()


def _copy_image_linux(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        command = ["wl-copy", "--type", "image/png"]
    elif shutil.which("xclip"):
        command = ["xclip", "-selection", "clipboard", "-t", "image/png", "-i"]
    else:
        raise OSError("install 'xclip' (or 'wl-clipboard' on Wayland) to copy images")
    subprocess.run(command, input=buffer.getvalue(), stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=10)


def _copy_image_mac(image):
    handle, path = tempfile.mkstemp(suffix=".png")
    os.close(handle)
    try:
        image.save(path, "PNG")
        script = f'set the clipboard to (read (POSIX file "{path}") as «class PNGf»)'
        subprocess.run(["osascript", "-e", script], check=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        os.unlink(path)


def open_path(path):
    """Open a file or folder with the default program."""
    path = str(path)
    if IS_WINDOWS:
        os.startfile(path)
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal_in_folder(path):
    """Show a file selected in the system file manager."""
    path = os.path.normpath(str(path))
    if IS_WINDOWS:
        subprocess.Popen(f'explorer /select,"{path}"')
    elif IS_MAC:
        subprocess.Popen(["open", "-R", path])
    else:
        open_path(os.path.dirname(path))


# ---------------------------------------------------------------------------
# Hotkeys
# ---------------------------------------------------------------------------

MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Win")
_MODIFIER_ALIASES = {
    "ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "option": "Alt", "shift": "Shift",
    "win": "Win", "super": "Win", "cmd": "Win", "command": "Win", "meta": "Win",
}


def _build_key_table():
    # key name -> (Windows virtual-key code, pynput token, Tk keysym)
    table = {
        "Print": (0x2C, "<print_screen>", "Print"),
        "Pause": (0x13, "<pause>", "Pause"),
        "ScrollLock": (0x91, "<scroll_lock>", "Scroll_Lock"),
        "Insert": (0x2D, "<insert>", "Insert"),
        "Delete": (0x2E, "<delete>", "Delete"),
        "Home": (0x24, "<home>", "Home"),
        "End": (0x23, "<end>", "End"),
        "PageUp": (0x21, "<page_up>", "Prior"),
        "PageDown": (0x22, "<page_down>", "Next"),
        "Space": (0x20, "<space>", "space"),
    }
    for number in range(1, 25):
        table[f"F{number}"] = (0x6F + number, f"<f{number}>", f"F{number}")
    for letter in string.ascii_uppercase:
        table[letter] = (ord(letter), letter.lower(), letter.lower())
    for digit in string.digits:
        table[digit] = (ord(digit), digit, digit)
    return table


KEY_TABLE = _build_key_table()
_KEY_LOOKUP = {name.lower(): name for name in KEY_TABLE}
_KEY_LOOKUP.update({
    "prtsc": "Print", "prtscn": "Print", "printscreen": "Print", "print screen": "Print",
    "print_screen": "Print", "snapshot": "Print", "scroll_lock": "ScrollLock",
    "prior": "PageUp", "next": "PageDown", "pgup": "PageUp", "pgdn": "PageDown",
    "ins": "Insert", "del": "Delete", "break": "Pause",
})
_TK_KEYSYM_LOOKUP = {spec[2].lower(): name for name, spec in KEY_TABLE.items()}
_TK_KEYSYM_LOOKUP["sys_req"] = "Print"  # X11 reports Shift/Alt+Print as Sys_Req


def parse_hotkey(text):
    """'Ctrl+Shift+Print' -> (frozenset({'Ctrl', 'Shift'}), 'Print'); '' -> None."""
    text = (text or "").strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split("+")]
    if any(not part for part in parts):
        raise ValueError(f"'{text}' is not a valid shortcut")
    *modifier_names, key_name = parts
    modifiers = set()
    for name in modifier_names:
        modifier = _MODIFIER_ALIASES.get(name.lower())
        if modifier is None:
            raise ValueError(f"'{name}' is not a modifier key (use Ctrl, Alt, Shift or Win)")
        modifiers.add(modifier)
    key = _KEY_LOOKUP.get(key_name.lower())
    if key is None:
        raise ValueError(f"'{key_name}' is not a supported key")
    return frozenset(modifiers), key


def format_hotkey(parsed):
    if not parsed:
        return ""
    modifiers, key = parsed
    return "+".join([m for m in MODIFIER_ORDER if m in modifiers] + [key])


def normalize_hotkey(text):
    return format_hotkey(parse_hotkey(text))


def display_hotkey(text):
    """'Shift+Print' -> 'Shift + Print Screen' for labels."""
    if not text:
        return "No shortcut"
    parts = text.split("+")
    if parts[-1] == "Print":
        parts[-1] = "Print Screen"
    return " + ".join(parts)


def hotkey_label(text):
    """Text for a shortcut box: 'Shift + Print Screen', or '' when unset."""
    return display_hotkey(text) if text else ""


def hotkey_problem(parsed):
    """Reason a shortcut would get in the way of normal typing, or None."""
    if not parsed:
        return None
    modifiers, key = parsed
    typing_key = len(key) == 1 or key == "Space"
    if typing_key and not modifiers & {"Ctrl", "Alt", "Win"}:
        return f"{format_hotkey(parsed)} would fire while typing; add Ctrl, Alt or Win"
    return None


def key_from_tk_keysym(keysym):
    return _TK_KEYSYM_LOOKUP.get(keysym.lower())


def tk_sequence(parsed, release=False):
    """Tk event sequence for an in-window shortcut (None if Tk can't bind it)."""
    modifiers, key = parsed
    if "Win" in modifiers:
        return None
    parts = [tk_name for name, tk_name in (("Ctrl", "Control"), ("Alt", "Alt"), ("Shift", "Shift"))
             if name in modifiers]
    keysym = KEY_TABLE[key][2]
    if "Shift" in modifiers and len(keysym) == 1 and keysym.isalpha():
        keysym = keysym.upper()
    parts += ["KeyRelease" if release else "KeyPress", keysym]
    return "<" + "-".join(parts) + ">"


class _WindowsHotkeys:
    """RegisterHotKey on a background thread with its own message loop."""

    MODIFIER_FLAGS = {"Alt": 0x1, "Ctrl": 0x2, "Shift": 0x4, "Win": 0x8}
    MOD_NOREPEAT = 0x4000
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def __init__(self, events):
        self.events = events
        self.registered = {}
        self.errors = []
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()

    def start(self, parsed):
        self._parsed = parsed
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return self.registered, self.errors

    def _run(self):
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT]
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self._thread_id = kernel32.GetCurrentThreadId()
        ids = {}
        try:
            for hotkey_id, (action, parsed) in enumerate(self._parsed.items(), start=1):
                modifiers, key = parsed
                flags = self.MOD_NOREPEAT
                for modifier in modifiers:
                    flags |= self.MODIFIER_FLAGS[modifier]
                if user32.RegisterHotKey(None, hotkey_id, flags, KEY_TABLE[key][0]):
                    ids[hotkey_id] = action
                    self.registered[action] = format_hotkey(parsed)
                else:
                    self.errors.append(
                        f"{display_hotkey(format_hotkey(parsed))} is already used by another program")
        finally:
            self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == self.WM_HOTKEY and message.wParam in ids:
                self.events.put(ids[message.wParam])
        for hotkey_id in ids:
            user32.UnregisterHotKey(None, hotkey_id)

    def stop(self):
        if self._thread and self._thread.is_alive() and self._thread_id:
            from ctypes import wintypes

            post = ctypes.windll.user32.PostThreadMessageW
            post.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            post(self._thread_id, self.WM_QUIT, 0, 0)
            self._thread.join(1)


class _PynputHotkeys:
    """Global hotkeys on Linux/macOS through the optional pynput package.

    Modifiers must match exactly, so Ctrl+Print doesn't also fire Print.
    """

    MODIFIER_KEYS = (("Ctrl", ("ctrl", "ctrl_l", "ctrl_r")), ("Shift", ("shift", "shift_l", "shift_r")),
                     ("Alt", ("alt", "alt_l", "alt_r")), ("Win", ("cmd", "cmd_l", "cmd_r")))

    def __init__(self, events):
        self.events = events
        self._listener = None
        self._held = set()
        self._down = set()

    def start(self, parsed):
        try:
            from pynput import keyboard
        except Exception:
            return {}, ["global hotkeys need the 'pynput' package (pip install pynput); "
                        "shortcuts only work while this window is focused"]
        try:
            listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        except Exception as error:
            return {}, [f"global hotkeys are unavailable: {error}"]
        self._canonical = getattr(listener, "canonical", lambda key: key)
        self._aliases = {}
        if hasattr(keyboard.Key, "print_screen"):  # X11 sends Sys_Req for Shift/Alt+Print
            self._aliases[self._canonical(keyboard.KeyCode.from_vk(0xFF15))] = \
                self._canonical(keyboard.Key.print_screen)
        self._modifiers = {}
        for modifier, names in self.MODIFIER_KEYS:
            for name in names:
                key = getattr(keyboard.Key, name, None)
                if key is not None:
                    self._modifiers[self._canonical(key)] = modifier
        self._targets, registered, errors = {}, {}, []
        for action, (modifiers, key_name) in parsed.items():
            token = KEY_TABLE[key_name][1]
            if token.startswith("<"):
                key = getattr(keyboard.Key, token[1:-1], None)
            else:
                key = keyboard.KeyCode.from_char(token)
            if key is None:
                errors.append(f"{display_hotkey(format_hotkey((modifiers, key_name)))} isn't supported here")
                continue
            self._targets[(frozenset(modifiers), self._canonical(key))] = action
            registered[action] = format_hotkey((modifiers, key_name))
        if not self._targets:
            return {}, errors
        try:
            listener.start()
        except Exception as error:
            return {}, errors + [f"global hotkeys are unavailable: {error}"]
        self._listener = listener
        return registered, errors

    def _on_press(self, key, *_):
        key = self._canonical(key)
        key = self._aliases.get(key, key)
        modifier = self._modifiers.get(key)
        if modifier:
            self._held.add(modifier)
            return
        if key in self._down:  # auto-repeat
            return
        self._down.add(key)
        action = self._targets.get((frozenset(self._held), key))
        if action:
            self.events.put(action)

    def _on_release(self, key, *_):
        key = self._canonical(key)
        key = self._aliases.get(key, key)
        modifier = self._modifiers.get(key)
        if modifier:
            self._held.discard(modifier)
        else:
            self._down.discard(key)

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


class HotkeyManager:
    """Registers global hotkeys and calls back on the Tk thread."""

    def __init__(self, root, callback):
        self.root = root
        self.callback = callback
        self.events = queue.Queue()
        self.active = {}
        self._backend = None
        self._poll_id = None

    def start(self, bindings):
        """Register {action: 'Ctrl+Print', ...}. Returns a list of problems."""
        self.stop()
        parsed, problems = {}, []
        for action, text in bindings.items():
            try:
                value = parse_hotkey(text)
            except ValueError as error:
                problems.append(str(error))
                continue
            if value:
                parsed[action] = value
        if not parsed:
            return problems
        backend = _WindowsHotkeys(self.events) if IS_WINDOWS else _PynputHotkeys(self.events)
        try:
            registered, errors = backend.start(parsed)
        except Exception as error:
            registered, errors = {}, [f"global hotkeys are unavailable: {error}"]
        problems += errors
        self._backend = backend
        self.active = dict(registered)
        if self.active:
            self._poll()
        return problems

    def _poll(self):
        while True:
            try:
                action = self.events.get_nowait()
            except queue.Empty:
                break
            self.callback(action)
        self._poll_id = self.root.after(50, self._poll)

    def stop(self):
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        if self._backend is not None:
            try:
                self._backend.stop()
            except Exception:
                pass
            self._backend = None
        self.active = {}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _as_bool(value):
    if isinstance(value, bool):
        return value
    raise ValueError("expected true/false")


def _as_int(low, high):
    def convert(value):
        if isinstance(value, bool):
            raise ValueError("expected a number")
        number = int(float(str(value).strip()))
        return min(high, max(low, number))
    return convert


def _as_str(value):
    if not isinstance(value, str):
        raise ValueError("expected text")
    return value.strip()


def _as_pattern(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("the file name pattern can't be empty")
    return value.strip()


def _as_color(value):
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()):
        return value.strip().upper()
    raise ValueError("expected a colour like #FF0000")


def _as_format(value):
    value = str(value).strip().lower().lstrip(".")
    value = {"jpeg": "jpg"}.get(value, value)
    if value not in IMAGE_FORMATS:
        raise ValueError("unknown image format")
    return value


def _as_region(value):
    if value is None:
        return None
    x, y, width, height = (int(v) for v in value)
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be greater than 0")
    return [x, y, width, height]


def _as_geometry(value):
    if value is None or (isinstance(value, str) and GEOMETRY_RE.fullmatch(value)):
        return value
    raise ValueError("bad window geometry")


def _as_folder_list(value):
    if not isinstance(value, list):
        raise ValueError("expected a list")
    folders = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in folders:
            folders.append(item.strip())
    return folders[:MAX_RECENT_FOLDERS]


def _as_hotkey(value):
    if not isinstance(value, str):
        raise ValueError("expected a shortcut like Ctrl+Print")
    return normalize_hotkey(value)


SETTINGS_SCHEMA = {
    # key: (default, converter that validates or raises ValueError/TypeError)
    "master_folder": ("", _as_str),
    "folder_name": ("", _as_str),
    "recent_folders": ([], _as_folder_list),
    "last_region": (None, _as_region),
    "window_geometry": (None, _as_geometry),
    "file_pattern": (DEFAULT_FILE_PATTERN, _as_pattern),
    "image_format": ("png", _as_format),
    "jpeg_quality": (90, _as_int(10, 100)),
    "capture_delay": (0, _as_int(0, 30)),
    "copy_to_clipboard": (True, _as_bool),
    "open_editor": (False, _as_bool),
    "show_after_capture": (True, _as_bool),
    "selection_color": ("#FF0000", _as_color),
    "selection_width": (2, _as_int(1, 10)),
    "crosshair_color": ("#FF0000", _as_color),
    "crosshair_width": (1, _as_int(1, 5)),
    "show_crosshair": (True, _as_bool),
    "show_magnifier": (True, _as_bool),
    "dim_percent": (40, _as_int(0, 80)),
    "hotkeys_enabled": (True, _as_bool),
    "hotkey_region": ("Print", _as_hotkey),
    "hotkey_last_region": ("Shift+Print", _as_hotkey),
    "hotkey_fullscreen": ("Ctrl+Print", _as_hotkey),
    "editor_color": ("#FF0000", _as_color),
    "editor_size": (3, _as_int(1, 20)),
}

HOTKEY_SETTINGS = (
    ("region", "hotkey_region", "Region"),
    ("last", "hotkey_last_region", "Last region"),
    ("fullscreen", "hotkey_fullscreen", "Full screen"),
)


def default_settings():
    return {key: (list(default) if isinstance(default, list) else default)
            for key, (default, _) in SETTINGS_SCHEMA.items()}


def app_directory():
    """Folder of this script (or of the .exe when packaged)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def user_config_directory():
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


class Settings:
    """Settings stored next to the program, so every copy has its own.

    If that folder isn't writable (e.g. Program Files) the user's config
    folder is used instead.  Unknown keys and bad values are ignored, so an
    old or damaged settings file can never stop the program from starting.
    """

    def __init__(self, path=None):
        self.values = default_settings()
        self._explicit = path is not None
        self.path = Path(path) if path else app_directory() / SETTINGS_FILE
        self.problem = None

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        _, convert = SETTINGS_SCHEMA[key]
        self.values[key] = convert(value)

    def _candidates(self):
        if self._explicit:
            return [self.path]
        # Older versions saved next to the current working directory.
        return [self.path, user_config_directory() / SETTINGS_FILE, Path.cwd() / SETTINGS_FILE]

    def load(self):
        source = next((p for p in self._candidates() if p.is_file()), None)
        if source is None:
            return
        try:
            with open(source, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("it is not a settings object")
        except (OSError, ValueError) as error:
            self.problem = f"Couldn't read {source.name} ({error}); default settings are used."
            try:
                shutil.copyfile(source, source.with_name(source.name + ".bak"))
            except OSError:
                pass
            return
        if source.parent == user_config_directory():
            self.path = source
        for key, value in data.items():
            if key in SETTINGS_SCHEMA:
                try:
                    self[key] = value
                except (TypeError, ValueError):
                    pass

    def save(self):
        text = json.dumps(self.values, indent=4)
        targets = [self.path]
        if not self._explicit:
            targets.append(user_config_directory() / SETTINGS_FILE)
        error = None
        for target in targets:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".tmp")
                temporary.write_text(text, encoding="utf-8")
                os.replace(temporary, target)
                self.path = target
                return
            except OSError as exc:
                error = exc
        raise error


# ---------------------------------------------------------------------------
# Output folders and files
# ---------------------------------------------------------------------------

_INVALID_NAME_CHARS = set('<>:"|?*')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}


def default_master_folder():
    pictures = Path.home() / "Pictures"
    return str(pictures if pictures.is_dir() else Path.home())


def validate_folder_name(name):
    """Return an error message for an unusable folder name, or None."""
    name = (name or "").strip()
    if not name:
        return None
    drive, rest = os.path.splitdrive(name)
    for part in re.split(r"[\\/]+", rest):
        if part in ("", "."):
            continue
        if part == "..":
            return "The folder name can't contain '..'"
        bad = sorted({c for c in part if c in _INVALID_NAME_CHARS or ord(c) < 32})
        if bad:
            shown = " ".join(c if ord(c) >= 32 else "?" for c in bad)
            return f"'{part}' contains characters that can't be used in folder names: {shown}"
        if part.split(".")[0].upper() in _RESERVED_NAMES:
            return f"'{part}' is a reserved name on Windows"
        if part != part.rstrip(" ."):
            return f"'{part}' can't end with a space or a dot"
    return None


def resolve_output_folder(master_folder, folder_name):
    """Where a capture is saved: <master>/<folder name or 'Screenshots'>.

    An absolute folder name is used as-is.
    """
    master = os.path.expanduser((master_folder or "").strip() or default_master_folder())
    name = os.path.expanduser((folder_name or "").strip() or DEFAULT_FOLDER_NAME)
    return Path(master, name)


def build_filename(pattern, extension, now=None):
    """Expand strftime codes in the pattern and make it safe to use as a name."""
    now = now or datetime.now()
    try:
        stem = now.strftime(pattern)
    except (ValueError, TypeError):
        stem = now.strftime(DEFAULT_FILE_PATTERN)
    stem = "".join("_" if c in _INVALID_NAME_CHARS or c in "\\/" or ord(c) < 32 else c for c in stem)
    stem = stem.strip(" .") or "screenshot"
    return f"{stem}.{extension}"


def unique_path(folder, filename):
    """folder/filename, adding _1, _2, ... so nothing gets overwritten."""
    path = Path(folder) / filename
    counter = 1
    while path.exists():
        path = Path(folder) / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
        counter += 1
    return path


def format_for_path(path):
    suffix = Path(path).suffix.lower().lstrip(".")
    return {"jpeg": "jpg"}.get(suffix, suffix) if suffix in ("png", "jpg", "jpeg", "bmp") else "png"


def save_image(image, path, image_format, jpeg_quality=90):
    if image_format == "jpg":
        image.convert("RGB").save(path, "JPEG", quality=jpeg_quality)
    elif image_format == "bmp":
        image.convert("RGB").save(path, "BMP")
    else:
        image.save(path, "PNG")


# ---------------------------------------------------------------------------
# Annotations (used by the editor)
# ---------------------------------------------------------------------------

HIGHLIGHT_COLOR = "#FFF200"
_FONT_FILES = {
    False: ("segoeui.ttf", "arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
            "LiberationSans-Regular.ttf", "Helvetica.ttc"),
    True: ("segoeuib.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf",
           "LiberationSans-Bold.ttf", "Helvetica.ttc"),
}


@lru_cache(maxsize=32)
def load_font(size, bold=False):
    for name in _FONT_FILES[bold]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def text_font_px(size):
    return 10 + 4 * size


def counter_radius(size):
    return 10 + 2 * size


def arrow_head(size):
    """(length, half width) of an arrow head for a line width."""
    return 8 + 4 * size, 5 + 2 * size


def pixel_block(size):
    return 6 + 2 * size


def _contrast_color(color):
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#000000" if 0.299 * red + 0.587 * green + 0.114 * blue > 170 else "#FFFFFF"


@dataclass
class Shape:
    tool: str
    points: list
    color: str = "#FF0000"
    size: int = 3
    text: str = ""
    number: int = 0


def _shape_box(points, image_size):
    (x0, y0), (x1, y1) = points[0], points[-1]
    width, height = image_size
    left = int(round(max(0, min(x0, x1))))
    top = int(round(max(0, min(y0, y1))))
    right = int(round(min(width, max(x0, x1))))
    bottom = int(round(min(height, max(y0, y1))))
    return left, top, right, bottom


def _draw_polyline(draw, points, color, width):
    if len(points) == 1:
        points = points * 2
    draw.line(points, fill=color, width=width, joint="curve")
    if width > 2:  # round caps
        radius = width / 2
        for x, y in (points[0], points[-1]):
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)


def _draw_arrow(draw, start, end, color, width):
    (x0, y0), (x1, y1) = start, end
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 1:
        return
    head_length, head_half = arrow_head(width)
    head_length = min(head_length, length)
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    base_x, base_y = x1 - ux * head_length, y1 - uy * head_length
    if length > head_length:
        _draw_polyline(draw, [(x0, y0), (base_x + ux, base_y + uy)], color, width)
    draw.polygon([(x1, y1),
                  (base_x - uy * head_half, base_y + ux * head_half),
                  (base_x + uy * head_half, base_y - ux * head_half)], fill=color)


def render_shape(image, shape):
    """Draw one annotation onto the image (in place) and return the result."""
    tool = shape.tool
    if tool in ("crop", "highlight", "obfuscate"):
        left, top, right, bottom = _shape_box(shape.points, image.size)
        if right - left < 1 or bottom - top < 1:
            return image
        if tool == "crop":
            return image.crop((left, top, right, bottom))
        region = image.crop((left, top, right, bottom)).convert("RGB")
        if tool == "highlight":
            region = ImageChops.multiply(region, Image.new("RGB", region.size, HIGHLIGHT_COLOR))
        else:
            block = pixel_block(shape.size)
            small = region.resize((max(1, region.width // block), max(1, region.height // block)), BILINEAR)
            region = small.resize(region.size, NEAREST)
        image.paste(region, (left, top))
        return image

    draw = ImageDraw.Draw(image)
    color, width = shape.color, max(1, int(shape.size))
    points = [(float(x), float(y)) for x, y in shape.points]
    if tool in ("rect", "ellipse"):
        (x0, y0), (x1, y1) = points[0], points[-1]
        box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        if tool == "rect":
            draw.rectangle(box, outline=color, width=width)
        else:
            draw.ellipse(box, outline=color, width=width)
    elif tool == "line":
        _draw_polyline(draw, [points[0], points[-1]], color, width)
    elif tool == "freehand":
        _draw_polyline(draw, points, color, width)
    elif tool == "arrow":
        _draw_arrow(draw, points[0], points[-1], color, width)
    elif tool == "text":
        draw.text(points[0], shape.text, fill=color, font=load_font(text_font_px(width)))
    elif tool == "counter":
        x, y = points[0]
        radius = counter_radius(width)
        draw.ellipse([x - radius, y - radius, x + radius, y + radius],
                     fill=color, outline="#FFFFFF", width=max(2, radius // 7))
        font = load_font(int(radius * 1.15), True)
        label = str(shape.number)
        try:
            draw.text((x, y), label, fill=_contrast_color(color), font=font, anchor="mm")
        except ValueError:  # bitmap fonts don't support anchors
            left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
            draw.text((x - (right - left) / 2, y - (bottom - top) / 2), label,
                      fill=_contrast_color(color), font=font)
    return image


def render_shapes(base, shapes):
    image = base.copy()
    for shape in shapes:
        image = render_shape(image, shape)
    return image


# ---------------------------------------------------------------------------
# Look and feel
# ---------------------------------------------------------------------------

class UI:
    """Fonts and DPI scale shared by all windows."""

    family = "TkDefaultFont"
    scale = 1.0

    @classmethod
    def px(cls, value):
        return int(round(value * cls.scale))

    @classmethod
    def font(cls, size=10, weight="normal"):
        return (cls.family, size, weight)


def setup_styles(root):
    UI.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    default_font = tkfont.nametofont("TkDefaultFont")
    UI.family = default_font.actual("family")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        try:
            tkfont.nametofont(name).configure(size=10)
        except tk.TclError:
            pass

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=UI.font(10),
                    bordercolor=BORDER, focuscolor=ACCENT, troughcolor="#E5E7EB")
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED, font=UI.font(9))
    style.configure("Header.TLabel", font=UI.font(15, "bold"))
    style.configure("Card.TLabel", background=CARD)
    style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=UI.font(9))
    style.configure("CardError.TLabel", background=CARD, foreground=ERROR_TEXT, font=UI.font(9))
    style.configure("CardTitle.TLabel", background=CARD, font=UI.font(10, "bold"))
    style.configure("Error.TLabel", foreground=ERROR_TEXT)
    style.configure("Section.TLabel", font=UI.font(10, "bold"))

    style.configure("TButton", background=CARD, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=CARD, darkcolor=CARD, padding=(UI.px(10), UI.px(4)))
    style.map("TButton",
              background=[("disabled", BG), ("pressed", "#E5E9F0"), ("active", HOVER)],
              foreground=[("disabled", "#9CA3AF")],
              bordercolor=[("focus", ACCENT)])
    style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
    style.map("Accent.TButton",
              background=[("disabled", "#93B4F5"), ("pressed", ACCENT_DARK), ("active", ACCENT_DARK)],
              foreground=[("disabled", "#FFFFFF")],
              bordercolor=[("focus", ACCENT_DARK)])

    for name, background in (("TCheckbutton", BG), ("Card.TCheckbutton", CARD)):
        style.configure(name, background=background, indicatorbackground=CARD,
                        indicatorforeground=ACCENT, padding=(0, UI.px(2)))
        style.map(name, background=[("active", background)],
                  indicatorbackground=[("pressed", ACCENT_LIGHT), ("selected", CARD)])

    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=CARD, bordercolor=BORDER, lightcolor=CARD,
                        darkcolor=CARD, padding=(UI.px(5), UI.px(3)), arrowsize=UI.px(12))
        style.map(name, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                  fieldbackground=[("readonly", CARD), ("disabled", BG)])
    style.configure("TNotebook", background=BG, bordercolor=BORDER, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", padding=(UI.px(14), UI.px(6)), background=BG, bordercolor=BORDER)
    style.map("TNotebook.Tab", background=[("selected", CARD)], lightcolor=[("selected", CARD)])
    style.configure("Horizontal.TScale", background=BG)

    style.configure("Toolbutton", background=CARD, anchor="w", padding=(UI.px(10), UI.px(5)),
                    relief="flat", borderwidth=0)
    style.map("Toolbutton",
              background=[("selected", ACCENT_LIGHT), ("active", HOVER)],
              foreground=[("selected", ACCENT_DARK)])
    root.option_add("*TCombobox*Listbox.font", UI.font(10))
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    return style


def make_app_icon(size=64):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    corner = size // 5
    try:
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=corner, fill=ACCENT)
    except AttributeError:
        draw.rectangle([0, 0, size - 1, size - 1], fill=ACCENT)
    inset, arm, width = size // 5, size // 5, max(2, size // 14)
    far = size - inset
    for x, y, dx, dy in ((inset, inset, 1, 1), (far, inset, -1, 1), (inset, far, 1, -1), (far, far, -1, -1)):
        draw.line([(x, y), (x + dx * arm, y)], fill="white", width=width)
        draw.line([(x, y), (x, y + dy * arm)], fill="white", width=width)
    dot = size // 9
    draw.ellipse([size / 2 - dot, size / 2 - dot, size / 2 + dot, size / 2 + dot], fill="white")
    return image


def set_window_icon(root):
    folder = app_directory()
    try:
        ico = folder / "logo.ico"
        if IS_WINDOWS and ico.is_file():
            root.iconbitmap(default=str(ico))
            return
        for candidate in (folder / "logo.png", ico):
            if candidate.is_file():
                image = Image.open(candidate).convert("RGBA").resize((64, 64), LANCZOS)
                break
        else:
            image = make_app_icon()
        root._app_icon = ImageTk.PhotoImage(image, master=root)
        root.iconphoto(True, root._app_icon)
    except Exception:
        pass  # a missing or broken icon must never stop the program


def center_window(window, width, height, parent=None):
    if parent is not None and parent.winfo_viewable():
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
    else:
        x = (window.winfo_screenwidth() - width) // 2
        y = (window.winfo_screenheight() - height) // 3
    window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")


def watch(variable, widget, callback):
    """Call callback when variable changes, until widget is destroyed.

    Removing the trace matters: Tcl keeps it (and everything the callback
    references) alive otherwise.
    """
    token = variable.trace_add("write", lambda *_: callback())

    def forget(event):
        if event.widget is widget:
            try:
                variable.trace_remove("write", token)
            except tk.TclError:
                pass
    widget.bind("<Destroy>", forget, add="+")


class ColorButton(tk.Canvas):
    """A colour swatch that opens the colour picker when clicked."""

    def __init__(self, master, variable, size=24, title="Choose a colour", background=CARD):
        size = UI.px(size)
        super().__init__(master, width=size, height=size, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT, borderwidth=0,
                         cursor="hand2", takefocus=1, background=background)
        self.variable = variable
        self.title = title
        self.swatch = self.create_rectangle(3, 3, size - 2, size - 2, outline="")
        self.bind("<Button-1>", self.pick)
        self.bind("<space>", self.pick)
        self.bind("<Return>", self.pick)
        watch(variable, self, self._refresh)
        self._refresh()

    def _refresh(self):
        try:
            self.itemconfigure(self.swatch, fill=self.variable.get())
        except tk.TclError:
            pass

    def pick(self, event=None):
        rgb, _ = colorchooser.askcolor(color=self.variable.get(), parent=self.winfo_toplevel(),
                                       title=self.title)
        if rgb:
            self.variable.set("#%02X%02X%02X" % tuple(int(c) for c in rgb))


class CaptureTile(tk.Frame):
    """Large clickable capture button with an icon and its shortcut."""

    def __init__(self, master, title, icon, command):
        super().__init__(master, bg=CARD, highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT, cursor="hand2", takefocus=1,
                         padx=UI.px(8), pady=UI.px(10))
        self.command = command
        self.canvas = tk.Canvas(self, width=UI.px(34), height=UI.px(26), bg=CARD,
                                highlightthickness=0, cursor="hand2")
        self._draw_icon(icon)
        self.title = tk.Label(self, text=title, bg=CARD, fg=TEXT, font=UI.font(11, "bold"), cursor="hand2")
        self.subtitle = tk.Label(self, text="", bg=CARD, fg=MUTED, font=UI.font(9), cursor="hand2")
        self.canvas.pack()
        self.title.pack(pady=(UI.px(4), 0))
        self.subtitle.pack()
        for widget in (self, self.canvas, self.title, self.subtitle):
            widget.bind("<Enter>", lambda e: self._hover(True))
            widget.bind("<Leave>", self._leave)
            widget.bind("<ButtonRelease-1>", self._click)
        self.bind("<Return>", lambda e: self.command())
        self.bind("<space>", lambda e: self.command())

    def _draw_icon(self, icon):
        c, w, h, p = self.canvas, UI.px(34), UI.px(26), UI.px(3)
        line = max(2, UI.px(2))
        if icon == "region":
            c.create_rectangle(p, p, w - p, h - p, outline=ACCENT, width=line, dash=(4, 3))
            c.create_line(w - p - UI.px(8), h - p, w - p + 1, h - p, fill=ACCENT_DARK, width=line + 1)
            c.create_line(w - p, h - p - UI.px(8), w - p, h - p + 1, fill=ACCENT_DARK, width=line + 1)
        elif icon == "last":
            c.create_rectangle(p, p, w - p, h - p, outline=ACCENT, width=line)
            r = UI.px(6)
            cx, cy = w / 2, h / 2
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=60, extent=280, style="arc",
                         outline=ACCENT_DARK, width=line)
            c.create_polygon(cx + r * 0.5, cy - r - UI.px(3), cx + r * 0.5 + UI.px(5), cy - r + UI.px(1),
                             cx + r * 0.5, cy - r + UI.px(4), fill=ACCENT_DARK, outline="")
        else:
            c.create_rectangle(p, p, w - p, h - UI.px(7), outline=ACCENT, width=line)
            c.create_line(w / 2, h - UI.px(7), w / 2, h - UI.px(3), fill=ACCENT, width=line)
            c.create_line(w / 2 - UI.px(7), h - UI.px(2), w / 2 + UI.px(7), h - UI.px(2),
                          fill=ACCENT, width=line)

    def set_subtitle(self, text):
        self.subtitle.configure(text=text)

    def _hover(self, on):
        color = ACCENT_LIGHT if on else CARD
        for widget in (self, self.canvas, self.title, self.subtitle):
            widget.configure(bg=color)
        self.configure(highlightbackground=ACCENT if on else BORDER)

    def _contains(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        return widget is not None and (widget is self or str(widget).startswith(str(self) + "."))

    def _leave(self, event):
        self._hover(self._contains(event))

    def _click(self, event):
        if self._contains(event):
            self.command()


class HotkeyEntry(ttk.Entry):
    """Read-only entry that records the key combination you press."""

    MODIFIER_KEYSYMS = {
        "Shift_L": "Shift", "Shift_R": "Shift", "Control_L": "Ctrl", "Control_R": "Ctrl",
        "Alt_L": "Alt", "Alt_R": "Alt", "Meta_L": "Alt", "Meta_R": "Alt",
        "Super_L": "Win", "Super_R": "Win", "Win_L": "Win", "Win_R": "Win",
    }

    def __init__(self, master, variable, **kwargs):
        super().__init__(master, textvariable=variable, state="readonly", **kwargs)
        self.variable = variable
        self.held = set()
        self.bind("<KeyPress>", self._press)
        self.bind("<KeyRelease>", self._release)
        self.bind("<FocusOut>", lambda e: self.held.clear())

    def _modifiers(self, event):
        modifiers = set(self.held)
        if event.state & 0x1:
            modifiers.add("Shift")
        if event.state & 0x4:
            modifiers.add("Ctrl")
        if (IS_WINDOWS and event.state & 0x20000) or (not IS_WINDOWS and not IS_MAC and event.state & 0x8):
            modifiers.add("Alt")
        return frozenset(modifiers)

    def _press(self, event):
        modifier = self.MODIFIER_KEYSYMS.get(event.keysym)
        if modifier:
            self.held.add(modifier)
            return "break"
        if event.keysym == "Tab":
            return None
        if event.keysym == "BackSpace" and not self.held:
            self.variable.set("")
            return "break"
        key = key_from_tk_keysym(event.keysym)
        if key:
            self.variable.set(hotkey_label(format_hotkey((self._modifiers(event), key))))
        return "break"

    def _release(self, event):
        modifier = self.MODIFIER_KEYSYMS.get(event.keysym)
        if modifier:
            self.held.discard(modifier)
        elif event.keysym == "Print":  # Windows only reports Print Screen on release
            self.variable.set(hotkey_label(format_hotkey((self._modifiers(event), "Print"))))
        return "break"


# ---------------------------------------------------------------------------
# Region selection overlay
# ---------------------------------------------------------------------------

class RegionSelector:
    """Greenshot-style region picker drawn over a frozen image of all screens."""

    MAG_RADIUS = 10  # pixels shown either side of the cursor
    MAG_ZOOM = 8
    HINT = ("Drag to select an area   •   Enter: whole screen   •   "
            "Arrow keys: move 1 px (Shift: 10 px)   •   Esc / right-click: cancel")

    def __init__(self, root, grab, settings, on_done, on_cancel):
        self.grab = grab
        self.settings = settings
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.finished = False
        self.start = None
        self._redraw_id = None

        width, height = grab.size
        self.width, self.height = width, height
        origin_x, origin_y = grab.origin
        self.monitors = [(x - origin_x, y - origin_y, w, h) for x, y, w, h in get_monitors(grab)]
        image = grab.image if grab.image.size == (width, height) else grab.image.resize((width, height), LANCZOS)
        self.image = image

        self.win = tk.Toplevel(root)
        try:
            self._build(origin_x, origin_y)
        except Exception:  # never leave a frozen full-screen window behind
            self.win.destroy()
            raise

    def _build(self, origin_x, origin_y):
        win, settings, image = self.win, self.settings, self.image
        width, height = self.width, self.height
        win.withdraw()
        win.overrideredirect(True)
        win.geometry(f"{width}x{height}+{origin_x}+{origin_y}")
        win.attributes("-topmost", True)
        win.configure(bg="black")
        canvas = self.canvas = tk.Canvas(win, width=width, height=height, bg="black",
                                         highlightthickness=0, borderwidth=0, cursor="crosshair")
        canvas.pack(fill=tk.BOTH, expand=True)

        self.bright = ImageTk.PhotoImage(image, master=win)
        dim = settings["dim_percent"] / 100
        if dim > 0:
            dark = Image.blend(image, Image.new("RGB", image.size, "black"), dim)
            self.background = ImageTk.PhotoImage(dark, master=win)
            self.selection_photo = tk.PhotoImage(master=win)
        else:
            self.background = self.bright
            self.selection_photo = None
        canvas.create_image(0, 0, anchor="nw", image=self.background)
        self.selection_item = canvas.create_image(0, 0, anchor="nw", state="hidden")
        if self.selection_photo is not None:
            canvas.itemconfigure(self.selection_item, image=self.selection_photo)

        line_state = "normal" if settings["show_crosshair"] else "hidden"
        cross, cross_width = settings["crosshair_color"], settings["crosshair_width"]
        self.h_line = canvas.create_line(0, 0, width, 0, fill=cross, width=cross_width, state=line_state)
        self.v_line = canvas.create_line(0, 0, 0, height, fill=cross, width=cross_width, state=line_state)
        self.rect = canvas.create_rectangle(0, 0, 0, 0, outline=settings["selection_color"],
                                            width=settings["selection_width"], state="hidden")
        self.size_bg = canvas.create_rectangle(0, 0, 0, 0, fill="#111827", outline="", state="hidden")
        self.size_text = canvas.create_text(0, 0, anchor="nw", fill="white",
                                            font=UI.font(10, "bold"), state="hidden")

        self.mag_photo = None
        self.mag_size = (2 * self.MAG_RADIUS + 1) * self.MAG_ZOOM
        if settings["show_magnifier"]:
            self.mag_photo = ImageTk.PhotoImage("RGB", (self.mag_size, self.mag_size), master=win)
            self.mag_item = canvas.create_image(0, 0, anchor="nw", image=self.mag_photo)
            self.mag_border = canvas.create_rectangle(0, 0, 0, 0, outline="#FFFFFF", width=2)
        self.info_bg = canvas.create_rectangle(0, 0, 0, 0, fill="#111827", outline="")
        self.info_text = canvas.create_text(0, 0, anchor="nw", fill="white", font=UI.font(9))

        pointer_x, pointer_y = win.winfo_pointerxy()
        self.cursor = (self._clamp(pointer_x - origin_x, width), self._clamp(pointer_y - origin_y, height))
        mx, my, mw, _ = self._monitor_at(*self.cursor)
        self.hint_text = canvas.create_text(mx + mw / 2, my + UI.px(28), text=self.HINT, fill="white",
                                            font=UI.font(10), anchor="n")
        x0, y0, x1, y1 = canvas.bbox(self.hint_text)
        pad = UI.px(10)
        self.hint_bg = canvas.create_rectangle(x0 - pad, y0 - pad / 2, x1 + pad, y1 + pad / 2,
                                               fill="#111827", outline="#374151")
        canvas.tag_lower(self.hint_bg, self.hint_text)

        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<B1-Motion>", self._on_motion)
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        canvas.bind("<ButtonPress-3>", self._on_right_click)
        win.bind("<Escape>", lambda e: self.cancel())
        for sequence in ("<Return>", "<KP_Enter>", "<space>"):
            win.bind(sequence, self._select_monitor)
        for keysym, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            win.bind(f"<{keysym}>", lambda e, dx=dx, dy=dy: self._nudge(e, dx, dy))

        win.deiconify()
        win.lift()
        win.focus_force()
        canvas.focus_set()
        if not IS_WINDOWS and not IS_MAC:
            self._grab_keyboard()
        win.after(80, self._refocus)
        self._redraw()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _clamp(value, limit):
        return max(0, min(limit - 1, int(value)))

    def _monitor_at(self, x, y):
        for monitor in self.monitors:
            mx, my, mw, mh = monitor
            if mx <= x < mx + mw and my <= y < my + mh:
                return monitor
        return 0, 0, self.width, self.height

    def _grab_keyboard(self, attempts=20):
        """X11 window managers don't give override-redirect windows the keyboard,
        so grab it (and the mouse) while selecting; released on close."""
        if self.finished:
            return
        try:
            self.win.grab_set_global()
        except tk.TclError:  # not viewable yet, or another program holds a grab
            if attempts:
                self.win.after(50, lambda: self._grab_keyboard(attempts - 1))

    def _refocus(self):
        if not self.finished:
            self.win.lift()
            self.win.focus_force()
            self.canvas.focus_set()

    def _current_region(self):
        (x0, y0), (x1, y1) = self.start, self.cursor
        return min(x0, x1), min(y0, y1), abs(x1 - x0) + 1, abs(y1 - y0) + 1

    # -- events ------------------------------------------------------------
    def _on_motion(self, event):
        self.cursor = (self._clamp(event.x, self.width), self._clamp(event.y, self.height))
        self._schedule_redraw()

    def _on_press(self, event):
        self._on_motion(event)
        self.start = self.cursor
        self.canvas.itemconfigure(self.hint_text, state="hidden")
        self.canvas.itemconfigure(self.hint_bg, state="hidden")
        self._redraw()

    def _on_release(self, event):
        if self.start is None:
            return
        self._on_motion(event)
        region = self._current_region()
        self.start = None
        if region[2] < 3 and region[3] < 3:  # a click, not a drag
            self._redraw()
            return
        self._finish(region)

    def _on_right_click(self, event):
        if self.start is not None:  # abandon the current drag, keep selecting
            self.start = None
            self._redraw()
        else:
            self.cancel()

    def _nudge(self, event, dx, dy):
        step = 10 if event.state & 0x1 else 1
        x = self._clamp(self.cursor[0] + dx * step, self.width)
        y = self._clamp(self.cursor[1] + dy * step, self.height)
        self.cursor = (x, y)
        self.canvas.event_generate("<Motion>", warp=True, x=x, y=y)
        self._schedule_redraw()

    def _select_monitor(self, event=None):
        if self.start is not None:
            return
        mx, my, mw, mh = self._monitor_at(*self.cursor)
        left, top = max(0, mx), max(0, my)
        right, bottom = min(self.width, mx + mw), min(self.height, my + mh)
        self._finish((left, top, right - left, bottom - top))

    # -- drawing -----------------------------------------------------------
    def _schedule_redraw(self):
        if self._redraw_id is None and not self.finished:
            self._redraw_id = self.win.after_idle(self._redraw)

    def _redraw(self):
        self._redraw_id = None
        if self.finished:
            return
        canvas = self.canvas
        x, y = self.cursor
        canvas.coords(self.h_line, 0, y, self.width, y)
        canvas.coords(self.v_line, x, 0, x, self.height)
        if self.start is not None:
            left, top, width, height = self._current_region()
            right, bottom = left + width, top + height
            pad = math.ceil(self.settings["selection_width"] / 2)
            canvas.coords(self.rect, left - pad, top - pad, right + pad - 1, bottom + pad - 1)
            canvas.itemconfigure(self.rect, state="normal")
            if self.selection_photo is not None:
                photo = self.selection_photo
                photo.blank()
                photo.configure(width=width, height=height)
                photo.tk.call(photo.name, "copy", str(self.bright),
                              "-from", left, top, right, bottom, "-to", 0, 0)
                canvas.coords(self.selection_item, left, top)
                canvas.itemconfigure(self.selection_item, state="normal")
            self._draw_size_label(f"{width} × {height}", left, top)
        else:
            for item in (self.rect, self.selection_item, self.size_bg, self.size_text):
                canvas.itemconfigure(item, state="hidden")
        self._draw_info(x, y)

    def _draw_size_label(self, text, left, top):
        canvas = self.canvas
        canvas.itemconfigure(self.size_text, text=text, state="normal")
        canvas.coords(self.size_text, 0, 0)
        x0, y0, x1, y1 = canvas.bbox(self.size_text)
        label_w, label_h = x1 - x0, y1 - y0
        pad = UI.px(4)
        gap = self.settings["selection_width"] + pad
        tx = left
        ty = top - label_h - gap - pad if top - label_h - gap - pad >= 0 else top + gap + pad
        canvas.coords(self.size_text, tx + pad, ty)
        canvas.coords(self.size_bg, tx, ty - pad / 2, tx + label_w + 2 * pad, ty + label_h + pad / 2)
        canvas.itemconfigure(self.size_bg, state="normal")
        canvas.tag_raise(self.size_bg)
        canvas.tag_raise(self.size_text)

    def _draw_info(self, x, y):
        canvas = self.canvas
        screen_x, screen_y = x + self.grab.origin[0], y + self.grab.origin[1]
        red, green, blue = self.image.getpixel((x, y))[:3]
        lines = [f"X {screen_x}    Y {screen_y}"]
        if self.start is not None:
            _, _, width, height = self._current_region()
            lines.append(f"W {width}    H {height}")
        lines.append(f"#{red:02X}{green:02X}{blue:02X}")
        canvas.itemconfigure(self.info_text, text="\n".join(lines))
        canvas.coords(self.info_text, 0, 0)
        tx0, ty0, tx1, ty1 = canvas.bbox(self.info_text)
        pad = UI.px(6)
        text_w, text_h = tx1 - tx0, ty1 - ty0
        mag = self.mag_size if self.mag_photo is not None else 0
        panel_w = max(mag, text_w + 2 * pad)
        panel_h = mag + text_h + 2 * pad

        mx, my, mw, mh = self._monitor_at(x, y)
        offset = UI.px(24)
        px = x + offset if x + offset + panel_w <= mx + mw else x - offset - panel_w
        py = y + offset if y + offset + panel_h <= my + mh else y - offset - panel_h

        if self.mag_photo is not None:
            radius, zoom = self.MAG_RADIUS, self.MAG_ZOOM
            zoomed = self.image.crop((x - radius, y - radius, x + radius + 1, y + radius + 1))
            zoomed = zoomed.resize((self.mag_size, self.mag_size), NEAREST)
            draw = ImageDraw.Draw(zoomed)
            centre = radius * zoom
            middle = centre + zoom // 2
            cross = self.settings["crosshair_color"]
            draw.line([(0, middle), (centre - 1, middle)], fill=cross)
            draw.line([(centre + zoom, middle), (self.mag_size, middle)], fill=cross)
            draw.line([(middle, 0), (middle, centre - 1)], fill=cross)
            draw.line([(middle, centre + zoom), (middle, self.mag_size)], fill=cross)
            draw.rectangle([centre - 1, centre - 1, centre + zoom, centre + zoom], outline="#000000")
            draw.rectangle([centre, centre, centre + zoom - 1, centre + zoom - 1], outline="#FFFFFF")
            self.mag_photo.paste(zoomed)
            canvas.coords(self.mag_item, px, py)
            canvas.coords(self.mag_border, px - 1, py - 1, px + mag, py + mag)
            canvas.tag_raise(self.mag_item)
            canvas.tag_raise(self.mag_border)
        canvas.coords(self.info_bg, px, py + mag, px + panel_w, py + panel_h)
        canvas.coords(self.info_text, px + pad, py + mag + pad)
        canvas.tag_raise(self.info_bg)
        canvas.tag_raise(self.info_text)

    # -- finishing ---------------------------------------------------------
    def _finish(self, region):
        if self.finished:
            return
        self.finished = True
        x, y, width, height = region
        screen_region = (x + self.grab.origin[0], y + self.grab.origin[1], width, height)
        try:
            image = self.grab.crop(screen_region)
        finally:
            self._close()
        self.on_done(image, screen_region)

    def cancel(self):
        if self.finished:
            return
        self.finished = True
        self._close()
        self.on_cancel()

    def _close(self):
        if self._redraw_id is not None:
            self.win.after_cancel(self._redraw_id)
            self._redraw_id = None
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


# ---------------------------------------------------------------------------
# Image editor
# ---------------------------------------------------------------------------

class ImageEditor:
    """Greenshot-style editor: shapes, arrows, text, highlight, pixelate, crop."""

    TOOLS = (
        ("rect", "Rectangle", "r", "Drag to draw a rectangle (Shift: square)"),
        ("ellipse", "Ellipse", "e", "Drag to draw an ellipse (Shift: circle)"),
        ("line", "Line", "l", "Drag to draw a line (Shift: snap to 45°)"),
        ("arrow", "Arrow", "a", "Drag to draw an arrow (Shift: snap to 45°)"),
        ("freehand", "Freehand", "f", "Drag to draw freely"),
        ("text", "Text", "t", "Click to type, Enter to finish"),
        ("highlight", "Highlight", "h", "Drag over an area to highlight it"),
        ("obfuscate", "Obfuscate", "o", "Drag over an area to pixelate it"),
        ("counter", "Number", "n", "Click to add the next step number"),
        ("crop", "Crop", "c", "Drag to choose the area to keep"),
    )
    PRESET_COLORS = ("#FF0000", "#FF8C00", "#FFD400", "#22C55E", "#2563EB", "#000000", "#FFFFFF")
    PAD = 16

    def __init__(self, app, image, path=None):
        self.app = app
        self.base = image.convert("RGB")
        self.path = Path(path) if path else None
        self.shapes = []
        self.undone = []
        self.saved_shapes = []
        self.rendered = self.base.copy()
        self.drag = None
        self.text_entry = None
        self.scale = 1.0
        self.offset = (0, 0)
        self.photo = None
        self._resize_id = None
        self._shortcuts = {key: tool for tool, _, key, _ in self.TOOLS}
        self._hints = {tool: hint for tool, _, _, hint in self.TOOLS}

        win = self.win = tk.Toplevel(app.root)
        win.configure(bg=BG)
        win.minsize(UI.px(620), UI.px(420))
        self.tool = tk.StringVar(value="rect")
        self.color = tk.StringVar(value=app.settings["editor_color"])
        self.size = tk.StringVar(value=str(app.settings["editor_size"]))
        self.message = tk.StringVar()

        self._build()
        self._fit_window()
        self._update_title()
        watch(self.tool, win, self._update_status)
        watch(self.color, win, self._remember_style)
        watch(self.size, win, self._remember_style)

        win.bind("<KeyPress>", self._on_key)
        for sequence, handler in (
            ("<Control-z>", self.undo), ("<Control-y>", self.redo), ("<Control-Z>", self.redo),
            ("<Control-s>", self.save), ("<Control-S>", self.save_as),
            ("<Control-c>", self.copy), ("<Control-w>", self.close), ("<Escape>", self._on_escape),
        ):
            win.bind(sequence, handler)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.after(30, self._refresh)
        win.lift()
        win.focus_force()

    # -- layout ------------------------------------------------------------
    def _build(self):
        win, px = self.win, UI.px
        top = ttk.Frame(win, padding=(px(10), px(8)))
        top.pack(fill=tk.X, side=tk.TOP)
        ttk.Button(top, text="Save", style="Accent.TButton", command=self.save).pack(side=tk.LEFT)
        ttk.Button(top, text="Save as…", command=self.save_as).pack(side=tk.LEFT, padx=(px(6), 0))
        ttk.Button(top, text="Copy", command=self.copy).pack(side=tk.LEFT, padx=(px(6), 0))
        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=px(10))
        self.undo_button = ttk.Button(top, text="Undo", command=self.undo)
        self.undo_button.pack(side=tk.LEFT)
        self.redo_button = ttk.Button(top, text="Redo", command=self.redo)
        self.redo_button.pack(side=tk.LEFT, padx=(px(6), 0))
        ttk.Button(top, text="Close", command=self.close).pack(side=tk.RIGHT)

        status = tk.Frame(win, bg=STATUS_COLORS["info"][0])
        status.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(status, textvariable=self.message, anchor="w", bg=STATUS_COLORS["info"][0],
                 fg=STATUS_COLORS["info"][1], padx=px(10), pady=px(4)).pack(fill=tk.X)

        side = tk.Frame(win, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        side.pack(side=tk.LEFT, fill=tk.Y, padx=(px(10), 0), pady=(0, px(10)))
        inner = ttk.Frame(side, style="Card.TFrame", padding=(px(6), px(8)))
        inner.pack(fill=tk.BOTH, expand=True)
        ttk.Label(inner, text="Tools", style="CardTitle.TLabel").pack(anchor="w", padx=px(4), pady=(0, px(4)))
        for tool, label, key, _ in self.TOOLS:
            button = ttk.Radiobutton(inner, text=f"{label}", value=tool, variable=self.tool,
                                     style="Toolbutton", underline=label.lower().index(key))
            button.pack(fill=tk.X)
        ttk.Separator(inner).pack(fill=tk.X, pady=px(8))
        ttk.Label(inner, text="Colour", style="CardTitle.TLabel").pack(anchor="w", padx=px(4))
        colours = ttk.Frame(inner, style="Card.TFrame")
        colours.pack(anchor="w", padx=px(4), pady=(px(4), 0))
        ColorButton(colours, self.color, size=28).grid(row=0, column=0, rowspan=2, padx=(0, px(6)))
        for index, preset in enumerate(self.PRESET_COLORS):
            swatch = tk.Canvas(colours, width=px(14), height=px(14), bg=preset, highlightthickness=1,
                               highlightbackground=BORDER, cursor="hand2")
            swatch.grid(row=index // 4, column=1 + index % 4, padx=1, pady=1)
            swatch.bind("<Button-1>", lambda e, c=preset: self.color.set(c))
        ttk.Label(inner, text="Size", style="CardTitle.TLabel").pack(anchor="w", padx=px(4), pady=(px(10), 0))
        ttk.Spinbox(inner, from_=1, to=20, textvariable=self.size, width=6).pack(
            anchor="w", padx=px(4), pady=(px(4), 0))

        self.canvas = tk.Canvas(win, bg="#DADDE3", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=px(10), pady=(0, px(10)))
        self.shadow_item = self.canvas.create_rectangle(0, 0, 0, 0, fill="#B8BDC6", outline="")
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.configure(cursor="crosshair")

    def _fit_window(self):
        width, height = self.base.size
        screen_w, screen_h = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        chrome_w, chrome_h = UI.px(200), UI.px(120)
        win_w = min(max(width + chrome_w, UI.px(720)), int(screen_w * 0.9))
        win_h = min(max(height + chrome_h, UI.px(520)), int(screen_h * 0.88))
        center_window(self.win, win_w, win_h)

    # -- coordinates -------------------------------------------------------
    def _to_image(self, x, y):
        width, height = self.rendered.size
        ix = (x - self.offset[0]) / self.scale
        iy = (y - self.offset[1]) / self.scale
        return min(max(ix, 0), width), min(max(iy, 0), height)

    def _to_canvas(self, x, y):
        return x * self.scale + self.offset[0], y * self.scale + self.offset[1]

    def _size(self):
        try:
            return min(20, max(1, int(float(self.size.get()))))
        except (ValueError, tk.TclError):
            return 3

    # -- drawing -----------------------------------------------------------
    def _on_press(self, event):
        self.canvas.focus_set()
        self._commit_text()
        tool = self.tool.get()
        point = self._to_image(event.x, event.y)
        if tool == "text":
            self._start_text(point)
            return
        if tool == "counter":
            number = 1 + sum(1 for shape in self.shapes if shape.tool == "counter")
            self._add(Shape("counter", [point], self.color.get(), self._size(), number=number))
            return
        self.drag = {"tool": tool, "points": [point, point], "item": self._preview_item(tool)}
        self._update_preview_item()

    def _preview_item(self, tool):
        canvas, color = self.canvas, self.color.get()
        width = max(1, round(self._size() * self.scale))
        if tool == "rect":
            return canvas.create_rectangle(0, 0, 0, 0, outline=color, width=width)
        if tool == "ellipse":
            return canvas.create_oval(0, 0, 0, 0, outline=color, width=width)
        if tool in ("highlight", "obfuscate", "crop"):
            outline = {"highlight": "#E6B800", "obfuscate": "#4B5563", "crop": "#111827"}[tool]
            return canvas.create_rectangle(0, 0, 0, 0, outline=outline, width=2, dash=(5, 3))
        options = dict(fill=color, width=width, capstyle=tk.ROUND, joinstyle=tk.ROUND)
        if tool == "arrow":
            length, half = (max(4, v * self.scale) for v in arrow_head(self._size()))
            options.update(arrow=tk.LAST, arrowshape=(length, length, half))
        return canvas.create_line(0, 0, 0, 0, **options)

    def _update_preview_item(self):
        points = self.drag["points"]
        coords = [value for point in points for value in self._to_canvas(*point)]
        if len(points) == 1:
            coords *= 2
        self.canvas.coords(self.drag["item"], *coords)

    def _on_drag(self, event):
        if not self.drag:
            return
        point = self._to_image(event.x, event.y)
        tool, points = self.drag["tool"], self.drag["points"]
        if tool == "freehand":
            last = points[-1]
            if abs(point[0] - last[0]) * self.scale >= 1 or abs(point[1] - last[1]) * self.scale >= 1:
                points.append(point)
        else:
            if event.state & 0x1:
                point = self._constrain(tool, points[0], point)
            self.drag["points"] = [points[0], point]
        self._update_preview_item()

    @staticmethod
    def _constrain(tool, start, point):
        dx, dy = point[0] - start[0], point[1] - start[1]
        if tool in ("line", "arrow"):
            step = math.pi / 4
            angle = round(math.atan2(dy, dx) / step) * step
            length = math.hypot(dx, dy)
            return start[0] + length * math.cos(angle), start[1] + length * math.sin(angle)
        side = max(abs(dx), abs(dy))
        return start[0] + math.copysign(side, dx or 1), start[1] + math.copysign(side, dy or 1)

    def _on_release(self, event):
        drag, self.drag = self.drag, None
        if not drag:
            return
        self.canvas.delete(drag["item"])
        tool, points = drag["tool"], drag["points"]
        if tool != "freehand":
            (x0, y0), (x1, y1) = points
            if tool in ("line", "arrow"):
                if math.hypot(x1 - x0, y1 - y0) < 2:
                    return
            elif abs(x1 - x0) < 2 or abs(y1 - y0) < 2:
                return
        self._add(Shape(tool, list(points), self.color.get(), self._size()))

    def _start_text(self, point):
        color = self.color.get()
        font_px = max(8, round(text_font_px(self._size()) * self.scale))
        entry = tk.Entry(self.canvas, font=(UI.family, -font_px), fg=color, bg="#FFFFFF",
                         insertbackground=color, relief="solid", borderwidth=1, width=24)
        x, y = self._to_canvas(*point)
        window = self.canvas.create_window(x, y, window=entry, anchor="nw")
        self.text_entry = (entry, window, point)
        entry.bind("<Return>", lambda e: self._commit_text() or "break")
        entry.bind("<KP_Enter>", lambda e: self._commit_text() or "break")
        entry.bind("<Escape>", lambda e: self._cancel_text() or "break")
        entry.bind("<FocusOut>", lambda e: self._commit_text())
        entry.focus_set()

    def _commit_text(self):
        if not self.text_entry:
            return
        entry, window, point = self.text_entry
        self.text_entry = None
        text = entry.get()
        self.canvas.delete(window)
        entry.destroy()
        if text.strip():
            self._add(Shape("text", [point], self.color.get(), self._size(), text=text))

    def _cancel_text(self):
        if self.text_entry:
            entry, window, _ = self.text_entry
            self.text_entry = None
            self.canvas.delete(window)
            entry.destroy()

    # -- history -----------------------------------------------------------
    def _add(self, shape):
        self.shapes.append(shape)
        self.undone.clear()
        self.rendered = render_shape(self.rendered, shape)
        self._changed()

    def undo(self, event=None):
        if self._typing(event):
            return None
        self._commit_text()
        if self.shapes:
            self.undone.append(self.shapes.pop())
            self.rendered = render_shapes(self.base, self.shapes)
            self._changed()
        return "break"

    def redo(self, event=None):
        if self._typing(event):
            return None
        if self.undone:
            shape = self.undone.pop()
            self.shapes.append(shape)
            self.rendered = render_shape(self.rendered, shape)
            self._changed()
        return "break"

    def _changed(self):
        self._update_title()
        self._refresh()

    def is_dirty(self):
        return self.shapes != self.saved_shapes

    # -- display -----------------------------------------------------------
    def _on_configure(self, event):
        if self._resize_id is not None:
            self.win.after_cancel(self._resize_id)
        self._resize_id = self.win.after(60, self._refresh)

    def _refresh(self):
        self._resize_id = None
        if not self.win.winfo_exists():
            return
        if self.text_entry:
            self._commit_text()
        canvas_w = max(self.canvas.winfo_width(), 50)
        canvas_h = max(self.canvas.winfo_height(), 50)
        width, height = self.rendered.size
        pad = UI.px(self.PAD)
        scale = min(1.0, (canvas_w - 2 * pad) / width, (canvas_h - 2 * pad) / height)
        scale = max(scale, 0.02)
        shown_w, shown_h = max(1, round(width * scale)), max(1, round(height * scale))
        if (shown_w, shown_h) == (width, height):
            shown = self.rendered
        else:
            shown = self.rendered.resize((shown_w, shown_h), LANCZOS, reducing_gap=3.0)
        self.scale = shown_w / width
        self.photo = ImageTk.PhotoImage(shown, master=self.win)
        x = max(pad, (canvas_w - shown_w) // 2)
        y = max(pad, (canvas_h - shown_h) // 2)
        self.offset = (x, y)
        self.canvas.itemconfigure(self.image_item, image=self.photo)
        self.canvas.coords(self.image_item, x, y)
        self.canvas.coords(self.shadow_item, x + 3, y + 3, x + shown_w + 3, y + shown_h + 3)
        self.undo_button.state(["!disabled"] if self.shapes else ["disabled"])
        self.redo_button.state(["!disabled"] if self.undone else ["disabled"])
        self._update_status()

    def _update_status(self, note=None):
        width, height = self.rendered.size
        parts = [f"{width} × {height} px", f"{round(self.scale * 100)}%",
                 note or self._hints.get(self.tool.get(), "")]
        self.message.set("     ".join(p for p in parts if p))

    def _update_title(self):
        name = self.path.name if self.path else "Unsaved capture"
        self.win.title(f"{'*' if self.is_dirty() else ''}{name} - {APP_NAME} editor")

    def _remember_style(self):
        try:
            self.app.settings["editor_color"] = self.color.get()
            self.app.settings["editor_size"] = self._size()
        except ValueError:
            pass

    # -- keyboard ----------------------------------------------------------
    @staticmethod
    def _typing(event):
        return event is not None and isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Spinbox))

    def _on_key(self, event):
        if self._typing(event) or event.state & 0x4:
            return
        tool = self._shortcuts.get(event.keysym.lower())
        if tool:
            self.tool.set(tool)

    def _on_escape(self, event=None):
        if self.drag:
            self.canvas.delete(self.drag["item"])
            self.drag = None

    # -- file actions ------------------------------------------------------
    def copy(self, event=None):
        if self._typing(event):
            return None
        self._commit_text()
        error = self.app.copy_image(self.rendered)
        self._update_status("Copied to the clipboard" if error is None else f"Copy failed: {error}")
        return "break"

    def save(self, event=None):
        self._commit_text()
        if self.path is None:
            return self.save_as()
        return self._write(self.path)

    def save_as(self, event=None):
        self._commit_text()
        folder = self.path.parent if self.path else self.app.output_folder()
        name = self.path.name if self.path else build_filename(
            self.app.settings["file_pattern"], self.app.settings["image_format"])
        filename = filedialog.asksaveasfilename(
            parent=self.win, initialdir=str(folder), initialfile=name, defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("JPEG image", "*.jpg *.jpeg"),
                       ("Bitmap image", "*.bmp"), ("All files", "*.*")])
        if not filename:
            return False
        return self._write(Path(filename))

    def _write(self, path):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            save_image(self.rendered, path, format_for_path(path), self.app.settings["jpeg_quality"])
        except (OSError, ValueError) as error:
            messagebox.showerror("Couldn't save", f"Couldn't save {path}:\n{error}", parent=self.win)
            return False
        self.path = path
        self.saved_shapes = list(self.shapes)
        self._update_title()
        self._update_status(f"Saved {path.name}")
        self.app.editor_saved(self.rendered.copy(), path)
        return True

    def close(self, event=None):
        self._commit_text()
        if self.is_dirty():
            answer = messagebox.askyesnocancel(
                "Save changes?", "Do you want to save your changes to this screenshot?", parent=self.win)
            if answer is None or (answer and not self.save()):
                return False
        self.app.editor_closed(self)
        self.win.destroy()
        return True


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------

class SettingsDialog:
    def __init__(self, app):
        self.app = app
        settings = app.settings
        win = self.win = tk.Toplevel(app.root)
        win.title(f"Settings - {APP_NAME}")
        win.configure(bg=BG)
        win.transient(app.root)
        win.resizable(False, False)

        self.vars = {}
        for key in ("master_folder", "file_pattern", "selection_color", "crosshair_color"):
            self.vars[key] = tk.StringVar(value=settings[key])
        for key in ("jpeg_quality", "capture_delay", "selection_width", "crosshair_width"):
            self.vars[key] = tk.StringVar(value=str(settings[key]))
        for key in ("copy_to_clipboard", "open_editor", "show_after_capture", "show_crosshair",
                    "show_magnifier", "hotkeys_enabled"):
            self.vars[key] = tk.BooleanVar(value=settings[key])
        self.vars["image_format"] = tk.StringVar(value=settings["image_format"].upper())
        self.vars["dim_percent"] = tk.DoubleVar(value=settings["dim_percent"])
        for _, key, _ in HOTKEY_SETTINGS:
            self.vars[key] = tk.StringVar(value=hotkey_label(settings[key]))
        self.error = tk.StringVar()

        outer = ttk.Frame(win, padding=UI.px(12))
        outer.pack(fill=tk.BOTH, expand=True)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True)
        self._general_tab(notebook)
        self._capture_tab(notebook)
        self._output_tab(notebook)
        self._hotkeys_tab(notebook)

        ttk.Label(outer, textvariable=self.error, style="Error.TLabel", wraplength=UI.px(480)).pack(
            fill=tk.X, pady=(UI.px(8), 0))
        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(UI.px(8), 0))
        ttk.Button(buttons, text="Reset to defaults", command=self.reset).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=self.save).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side=tk.RIGHT, padx=(0, UI.px(6)))

        win.protocol("WM_DELETE_WINDOW", self.cancel)
        win.bind("<Escape>", lambda e: self.cancel())
        win.update_idletasks()
        center_window(win, win.winfo_reqwidth(), win.winfo_reqheight(), app.root)
        self._grab()
        win.focus_force()

    def _grab(self):
        if not self.win.winfo_exists():
            return
        try:
            self.win.grab_set()
        except tk.TclError:  # some window managers map the window a moment later
            self.win.after(100, self._grab)

    def _tab(self, notebook, title):
        frame = ttk.Frame(notebook, padding=UI.px(16))
        notebook.add(frame, text=title)
        frame.columnconfigure(1, weight=1)
        return frame

    def _general_tab(self, notebook):
        tab, px = self._tab(notebook, "General"), UI.px
        ttk.Label(tab, text="Master folder", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(tab, textvariable=self.vars["master_folder"], width=46).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(px(4), 0))
        ttk.Button(tab, text="Browse…", command=self._browse_master).grid(row=1, column=2, padx=(px(6), 0), pady=(px(4), 0))
        ttk.Label(tab, style="Muted.TLabel", wraplength=px(460), justify=tk.LEFT,
                  text=f"Screenshots are saved in <master folder>{os.sep}<folder name>. When the "
                       f"folder name is empty they go to “{DEFAULT_FOLDER_NAME}”. Leave the master "
                       f"folder empty to use {default_master_folder()}.").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(px(4), px(12)))

        ttk.Label(tab, text="File name", style="Section.TLabel").grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Entry(tab, textvariable=self.vars["file_pattern"]).grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(px(4), 0))
        example = ttk.Label(tab, style="Muted.TLabel")
        example.grid(row=5, column=0, columnspan=3, sticky="w", pady=(px(4), px(12)))

        ttk.Label(tab, text="Format").grid(row=6, column=0, sticky="w")
        formats = ttk.Frame(tab)
        formats.grid(row=6, column=1, columnspan=2, sticky="w", padx=(px(10), 0))
        ttk.Combobox(formats, textvariable=self.vars["image_format"], values=[f.upper() for f in IMAGE_FORMATS],
                     state="readonly", width=6).pack(side=tk.LEFT)
        ttk.Label(formats, text="JPEG quality").pack(side=tk.LEFT, padx=(px(14), px(6)))
        ttk.Spinbox(formats, from_=10, to=100, textvariable=self.vars["jpeg_quality"], width=5).pack(side=tk.LEFT)

        ttk.Label(tab, text="Capture delay").grid(row=7, column=0, sticky="w", pady=(px(10), 0))
        delay = ttk.Frame(tab)
        delay.grid(row=7, column=1, columnspan=2, sticky="w", padx=(px(10), 0), pady=(px(10), 0))
        ttk.Spinbox(delay, from_=0, to=30, textvariable=self.vars["capture_delay"], width=5).pack(side=tk.LEFT)
        ttk.Label(delay, text="seconds (time to open menus before the screen is frozen)",
                  style="Muted.TLabel").pack(side=tk.LEFT, padx=(px(6), 0))

        ttk.Label(tab, text=f"Settings file: {self.app.settings.path}", style="Muted.TLabel",
                  wraplength=px(460), justify=tk.LEFT).grid(row=8, column=0, columnspan=3, sticky="w", pady=(px(16), 0))

        def update_example():
            pattern = self.vars["file_pattern"].get().strip() or DEFAULT_FILE_PATTERN
            extension = _as_format(self.vars["image_format"].get())
            example.configure(text=f"Example: {build_filename(pattern, extension)}   "
                                   f"(%Y year, %m month, %d day, %H hour, %M minute, %S second)")
        watch(self.vars["file_pattern"], self.win, update_example)
        watch(self.vars["image_format"], self.win, update_example)
        update_example()

    def _capture_tab(self, notebook):
        tab, px = self._tab(notebook, "Capture"), UI.px
        ttk.Label(tab, text="Selection border", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        row = ttk.Frame(tab)
        row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(px(6), px(12)))
        ttk.Label(row, text="Colour").pack(side=tk.LEFT)
        ColorButton(row, self.vars["selection_color"], background=BG).pack(side=tk.LEFT, padx=(px(6), px(16)))
        ttk.Label(row, text="Width").pack(side=tk.LEFT)
        ttk.Spinbox(row, from_=1, to=10, textvariable=self.vars["selection_width"], width=4).pack(side=tk.LEFT, padx=(px(6), 0))

        ttk.Label(tab, text="Crosshair", style="Section.TLabel").grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(tab, text="Show lines across the whole screen", variable=self.vars["show_crosshair"]).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(px(4), 0))
        row = ttk.Frame(tab)
        row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(px(6), px(12)))
        ttk.Label(row, text="Colour").pack(side=tk.LEFT)
        ColorButton(row, self.vars["crosshair_color"], background=BG).pack(side=tk.LEFT, padx=(px(6), px(16)))
        ttk.Label(row, text="Width").pack(side=tk.LEFT)
        ttk.Spinbox(row, from_=1, to=5, textvariable=self.vars["crosshair_width"], width=4).pack(side=tk.LEFT, padx=(px(6), 0))

        ttk.Label(tab, text="While selecting", style="Section.TLabel").grid(row=5, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(tab, text="Show magnifier, coordinates and pixel colour",
                        variable=self.vars["show_magnifier"]).grid(row=6, column=0, columnspan=3, sticky="w", pady=(px(4), 0))
        row = ttk.Frame(tab)
        row.grid(row=7, column=0, columnspan=3, sticky="w", pady=(px(6), px(12)))
        ttk.Label(row, text="Darken outside the selection").pack(side=tk.LEFT)
        ttk.Scale(row, from_=0, to=80, variable=self.vars["dim_percent"], length=px(160)).pack(
            side=tk.LEFT, padx=px(8))
        dim_label = ttk.Label(row, width=5)
        dim_label.pack(side=tk.LEFT)

        ttk.Label(tab, text="Preview", style="Section.TLabel").grid(row=8, column=0, columnspan=3, sticky="w")
        self.preview = tk.Canvas(tab, width=px(300), height=px(130), highlightthickness=1,
                                 highlightbackground=BORDER, bg=BG)
        self.preview.grid(row=9, column=0, columnspan=3, sticky="w", pady=(px(6), 0))

        def update():
            dim_label.configure(text=f"{round(self.vars['dim_percent'].get())}%")
            self._draw_selection_preview()
        for key in ("selection_color", "selection_width", "crosshair_color", "crosshair_width",
                    "show_crosshair", "dim_percent"):
            watch(self.vars[key], self.win, update)
        update()

    def _draw_selection_preview(self):
        canvas, px = self.preview, UI.px
        canvas.delete("all")
        width, height = px(300), px(130)

        def shade(color, amount):
            red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
            return "#%02X%02X%02X" % tuple(int(c * (1 - amount)) for c in (red, green, blue))

        def as_int(key, default):
            try:
                return int(self.vars[key].get())
            except (ValueError, tk.TclError):
                return default

        def color(key):
            value = self.vars[key].get()
            return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else "#FF0000"

        dim = self.vars["dim_percent"].get() / 100
        scene = [((0, 0, width, height), "#CBD5E1"), ((px(20), px(14), px(170), px(110)), "#F8FAFC"),
                 ((px(20), px(14), px(170), px(30)), "#93C5FD"), ((px(190), px(40), px(285), px(118)), "#E2E8F0")]
        for box, fill in scene:
            canvas.create_rectangle(*box, fill=shade(fill, dim), outline="")
        left, top, right, bottom = px(60), px(40), px(220), px(100)
        for (x0, y0, x1, y1), fill in scene:
            x0, y0, x1, y1 = max(x0, left), max(y0, top), min(x1, right), min(y1, bottom)
            if x0 < x1 and y0 < y1:
                canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
        if self.vars["show_crosshair"].get():
            line = as_int("crosshair_width", 1)
            canvas.create_line(0, bottom, width, bottom, fill=color("crosshair_color"), width=line)
            canvas.create_line(right, 0, right, height, fill=color("crosshair_color"), width=line)
        canvas.create_rectangle(left, top, right, bottom, outline=color("selection_color"),
                                width=as_int("selection_width", 2))
        label = canvas.create_text(left + px(4), top - px(18), text="160 × 60", anchor="nw",
                                   fill="white", font=UI.font(9, "bold"))
        x0, y0, x1, y1 = canvas.bbox(label)
        canvas.tag_lower(canvas.create_rectangle(x0 - 4, y0 - 2, x1 + 4, y1 + 2, fill="#111827", outline=""), label)

    def _output_tab(self, notebook):
        tab, px = self._tab(notebook, "After capture"), UI.px
        ttk.Label(tab, text="Every capture is saved to the folder shown on the main window. Also:",
                  style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, px(8)))
        for row, (key, text) in enumerate((
            ("copy_to_clipboard", "Copy the image to the clipboard"),
            ("open_editor", "Open the image in the editor"),
            ("show_after_capture", "Bring this window to the front (when a hotkey was used)"),
        ), start=1):
            ttk.Checkbutton(tab, text=text, variable=self.vars[key]).grid(row=row, column=0, sticky="w", pady=px(2))

    def _hotkeys_tab(self, notebook):
        tab, px = self._tab(notebook, "Hotkeys"), UI.px
        ttk.Checkbutton(tab, text="Enable global hotkeys (work while other programs are in front)",
                        variable=self.vars["hotkeys_enabled"]).grid(row=0, column=0, columnspan=3, sticky="w")
        for row, (_, key, label) in enumerate(HOTKEY_SETTINGS, start=1):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=(px(10), 0))
            HotkeyEntry(tab, self.vars[key], width=22).grid(row=row, column=1, sticky="ew",
                                                             padx=(px(10), px(6)), pady=(px(10), 0))
            ttk.Button(tab, text="Clear", command=lambda k=key: self.vars[k].set("")).grid(
                row=row, column=2, pady=(px(10), 0))
        help_text = ("Click a box and press the key combination you want, e.g. Ctrl+Shift+S. "
                     "Backspace clears it.")
        if IS_WINDOWS:
            help_text += (" If Print Screen opens the Windows Snipping Tool instead, turn off "
                          "“Use the Print screen key to open screen capture” in Windows Settings › "
                          "Accessibility › Keyboard.")
        else:
            help_text += (" Hotkeys that work in other programs need the 'pynput' package "
                          "(pip install pynput).")
        ttk.Label(tab, style="Muted.TLabel", wraplength=px(460), justify=tk.LEFT, text=help_text).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(px(14), 0))
        problems = self.app.hotkey_problems
        if problems:
            ttk.Label(tab, style="Error.TLabel", wraplength=px(460), justify=tk.LEFT,
                      text="Last problem: " + "; ".join(problems)).grid(
                row=5, column=0, columnspan=3, sticky="w", pady=(px(10), 0))

    def _browse_master(self):
        current = self.vars["master_folder"].get().strip()
        chosen = filedialog.askdirectory(parent=self.win, title="Choose the master folder",
                                         initialdir=current if os.path.isdir(current) else default_master_folder())
        if chosen:
            self.vars["master_folder"].set(os.path.normpath(chosen))

    def reset(self):
        defaults = default_settings()
        for key, variable in self.vars.items():
            if key in ("master_folder",):
                continue
            value = defaults[key]
            if key == "image_format":
                value = value.upper()
            elif key.startswith("hotkey_"):
                value = hotkey_label(value)
            variable.set(value)
        self.error.set("")

    def save(self):
        settings, values = self.app.settings, {}
        try:
            for key in ("master_folder", "file_pattern", "image_format", "selection_color",
                        "crosshair_color", "copy_to_clipboard", "open_editor", "show_after_capture",
                        "show_crosshair", "show_magnifier", "hotkeys_enabled", "dim_percent"):
                values[key] = SETTINGS_SCHEMA[key][1](self.vars[key].get())
            for key, label in (("jpeg_quality", "JPEG quality"), ("capture_delay", "Capture delay"),
                               ("selection_width", "Selection width"), ("crosshair_width", "Crosshair width")):
                try:
                    values[key] = SETTINGS_SCHEMA[key][1](self.vars[key].get())
                except (TypeError, ValueError):
                    raise ValueError(f"{label} must be a number")
            seen = {}
            for _, key, label in HOTKEY_SETTINGS:
                parsed = parse_hotkey(self.vars[key].get())
                problem = hotkey_problem(parsed)
                if problem:
                    raise ValueError(f"{label}: {problem}")
                text = format_hotkey(parsed)
                if text and text in seen:
                    raise ValueError(f"{label} and {seen[text]} use the same shortcut ({display_hotkey(text)})")
                seen[text] = label
                values[key] = text
        except ValueError as error:
            self.error.set(str(error)[:1].upper() + str(error)[1:])
            return
        for key, value in values.items():
            settings[key] = value
        try:
            settings.save()
        except OSError as error:
            messagebox.showerror("Couldn't save settings", str(error), parent=self.win)
            return
        self._close()
        self.app.settings_saved()

    def cancel(self):
        self._close()
        self.app.apply_hotkeys()

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
        self.app.settings_dialog = None


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ScreenshotApp:
    def __init__(self, root):
        self.root = root
        self.settings = Settings()
        self.settings.load()
        self.last_image = None
        self.last_path = None
        self.busy = False
        self.selector = None
        self.settings_dialog = None
        self.editors = []
        self.hotkey_problems = []
        self._local_bindings = []
        self._prev_state = "normal"
        self._hidden_by_alpha = False
        self._preview_id = None
        self.preview_photo = None

        root.title(APP_NAME)
        root.report_callback_exception = self._report_error
        if not IS_WINDOWS and not IS_MAC:
            # Tk's X11 input-method code can crash (in TkpGetString) on key
            # presses after text fields were destroyed; we don't need XIM.
            try:
                root.tk.call("tk", "useinputmethods", "0")
            except tk.TclError:
                pass
        setup_styles(root)
        set_window_icon(root)
        self.hotkeys = HotkeyManager(root, self.trigger)
        self._build_ui()
        self._restore_geometry()
        self.apply_hotkeys()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.bind("<Control-e>", lambda e: self.open_editor())

        if self.settings.problem:
            self.set_status(self.settings.problem, "warning")
        elif not self.settings["master_folder"] and not self.hotkey_problems:
            self.set_status(f"Screenshots go to {self.output_folder()}. "
                            "Pick another master folder in Settings.", "info")

    # -- layout ------------------------------------------------------------
    def _build_ui(self):
        root, px = self.root, UI.px
        root.configure(bg=BG)
        root.minsize(px(440), px(560))

        self.status = tk.Label(root, anchor="w", justify=tk.LEFT, padx=px(12), pady=px(6), font=UI.font(9))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status.bind("<Configure>", lambda e: self.status.configure(wraplength=max(100, e.width - px(24))))
        self.set_status("Ready", "info")

        outer = ttk.Frame(root, padding=(px(16), px(12), px(16), px(12)))
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X)
        ttk.Label(header, text=APP_NAME, style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Button(header, text="Settings", command=self.open_settings).pack(side=tk.RIGHT)
        ttk.Label(outer, text="Capture, save and annotate screenshots", style="Muted.TLabel").pack(
            anchor="w", pady=(0, px(10)))

        tiles = ttk.Frame(outer)
        tiles.pack(fill=tk.X)
        self.tiles = {}
        for column, (action, title, icon) in enumerate((
                ("region", "Region", "region"), ("last", "Last region", "last"),
                ("fullscreen", "Full screen", "screen"))):
            tile = CaptureTile(tiles, title, icon, command=lambda a=action: self.trigger(a))
            tile.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else px(8), 0))
            tiles.columnconfigure(column, weight=1, uniform="tiles")
            self.tiles[action] = tile

        card = self._card(outer, "Save to")
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill=tk.X)
        self.folder_var = tk.StringVar(value=self.settings["folder_name"])
        self.folder_box = ttk.Combobox(row, textvariable=self.folder_var, postcommand=self._refresh_folder_choices)
        self.folder_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse…", command=self.browse_folder).pack(side=tk.LEFT, padx=(px(6), 0))
        ttk.Button(row, text="Open", command=self.open_output_folder).pack(side=tk.LEFT, padx=(px(6), 0))
        self.path_label = ttk.Label(card, style="CardMuted.TLabel", anchor="w", justify=tk.LEFT)
        self.path_label.pack(fill=tk.X, pady=(px(4), 0))
        self.path_label.bind("<Configure>", lambda e: self.path_label.configure(wraplength=max(100, e.width)))
        options = ttk.Frame(card, style="Card.TFrame")
        options.pack(fill=tk.X, pady=(px(6), 0))
        self.copy_var = tk.BooleanVar(value=self.settings["copy_to_clipboard"])
        self.editor_var = tk.BooleanVar(value=self.settings["open_editor"])
        ttk.Checkbutton(options, text="Copy to clipboard", variable=self.copy_var, style="Card.TCheckbutton",
                        command=self._options_changed).pack(side=tk.LEFT)
        ttk.Checkbutton(options, text="Open in editor", variable=self.editor_var, style="Card.TCheckbutton",
                        command=self._options_changed).pack(side=tk.LEFT, padx=(px(16), 0))
        self.folder_var.trace_add("write", lambda *_: self._folder_changed())
        self._folder_changed()

        card = self._card(outer, "Region")
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill=tk.X)
        self.region_vars = []
        for index, label in enumerate(("X", "Y", "W", "H")):
            ttk.Label(row, text=label, style="Card.TLabel").pack(side=tk.LEFT, padx=(0 if index == 0 else px(10), px(4)))
            variable = tk.StringVar()
            entry = ttk.Entry(row, textvariable=variable, width=6)
            entry.pack(side=tk.LEFT)
            entry.bind("<Return>", lambda e: self.apply_region())
            self.region_vars.append(variable)
        ttk.Button(row, text="Apply", command=self.apply_region).pack(side=tk.RIGHT)
        ttk.Label(card, style="CardMuted.TLabel",
                  text="Screen pixels. “Last region” captures this area again.").pack(anchor="w", pady=(px(4), 0))
        self._show_region(self.settings["last_region"])

        card = self._card(outer, "Last capture", expand=True)
        self.preview = tk.Canvas(card, bg="#EEF0F3", highlightthickness=0, height=px(160))
        self.preview.pack(fill=tk.BOTH, expand=True)
        self.preview.bind("<Configure>", self._schedule_preview)
        self.preview.bind("<Double-Button-1>", lambda e: self.open_editor())
        self.preview_info = ttk.Label(card, style="CardMuted.TLabel", text="", anchor="w")
        self.preview_info.pack(fill=tk.X, pady=(px(6), 0))
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.pack(fill=tk.X, pady=(px(6), 0))
        self.action_buttons = {}
        for name, text, command in (("folder", "Show in folder", self.show_in_folder),
                                    ("open", "Open", self.open_last_file),
                                    ("copy", "Copy", self.copy_last),
                                    ("edit", "Edit", self.open_editor)):
            button = ttk.Button(actions, text=text, command=command,
                                style="Accent.TButton" if name == "edit" else "TButton")
            button.pack(side=tk.RIGHT, padx=(px(6), 0))
            self.action_buttons[name] = button
        self._update_action_buttons()

    def _card(self, parent, title, expand=False):
        frame = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=BORDER, borderwidth=0)
        frame.pack(fill=tk.BOTH if expand else tk.X, expand=expand, pady=(UI.px(10), 0))
        inner = ttk.Frame(frame, style="Card.TFrame", padding=(UI.px(12), UI.px(10)))
        inner.pack(fill=tk.BOTH, expand=True)
        ttk.Label(inner, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(0, UI.px(6)))
        return inner

    def _restore_geometry(self):
        root, px = self.root, UI.px
        vx, vy, vw, vh = virtual_screen_rect(root)
        match = GEOMETRY_RE.fullmatch(self.settings["window_geometry"] or "")
        if match:
            width, height, x, y = (int(v) for v in match.groups())
            if (px(440) <= width <= vw and px(560) <= height <= vh
                    and vx <= x <= vx + vw - 120 and vy <= y <= vy + vh - 120):
                root.geometry(f"{width}x{height}+{x}+{y}")
                return
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width, height = min(px(560), screen_w - 40), min(px(780), screen_h - 80)
        root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{max(0, (screen_h - height) // 2 - 20)}")

    # -- status ------------------------------------------------------------
    def set_status(self, message, kind="info"):
        background, foreground = STATUS_COLORS[kind]
        self.status.configure(text=message, bg=background, fg=foreground)

    def _report_error(self, exc_type, value, tb):
        traceback.print_exception(exc_type, value, tb)
        if self.selector is None:  # don't leave the window hidden after a failed capture
            self.busy = False
            if self.root.state() == "withdrawn" or self._hidden_by_alpha:
                self._show_window(force=True)
        self.set_status(f"Something went wrong: {value}", "error")

    # -- folders -----------------------------------------------------------
    def master_folder(self):
        return self.settings["master_folder"] or default_master_folder()

    def output_folder(self):
        return resolve_output_folder(self.settings["master_folder"], self.folder_var.get())

    def _folder_changed(self):
        name = self.folder_var.get()
        problem = validate_folder_name(name)
        if problem:
            self.path_label.configure(text=problem, style="CardError.TLabel")
            return
        self.settings["folder_name"] = name
        self.path_label.configure(text=str(self.output_folder()), style="CardMuted.TLabel")

    def _refresh_folder_choices(self):
        try:
            names = sorted((p.name for p in Path(self.master_folder()).iterdir()
                            if p.is_dir() and not p.name.startswith(".")), key=str.lower)
        except OSError:
            names = []
        recent = self.settings["recent_folders"]
        self.folder_box.configure(values=recent + [n for n in names if n not in recent])

    def _remember_folder(self, name):
        name = name.strip()
        if name:
            recent = [name] + [r for r in self.settings["recent_folders"] if r != name]
            self.settings["recent_folders"] = recent

    def browse_folder(self):
        master = os.path.normpath(os.path.expanduser(self.master_folder()))
        current = self.output_folder()
        initial = str(current) if current.is_dir() else (master if os.path.isdir(master) else str(Path.home()))
        chosen = filedialog.askdirectory(parent=self.root, initialdir=initial,
                                         title="Choose the folder for your screenshots")
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        try:
            relative = os.path.relpath(chosen, master)
        except ValueError:  # different drive
            relative = None
        if relative is None or relative == "." or relative.startswith(".."):
            self.folder_var.set(chosen)
        else:
            self.folder_var.set(relative)

    def _ensure_output_folder(self):
        problem = validate_folder_name(self.folder_var.get())
        if problem:
            raise OSError(problem)
        folder = self.output_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise OSError(f"Couldn't create the folder {folder}: {error.strerror or error}") from error
        return folder

    def open_output_folder(self):
        try:
            open_path(self._ensure_output_folder())
        except OSError as error:
            self.set_status(str(error), "error")

    def _options_changed(self):
        self.settings["copy_to_clipboard"] = self.copy_var.get()
        self.settings["open_editor"] = self.editor_var.get()
        self._save_settings_quietly()

    def _save_settings_quietly(self):
        try:
            self.settings.save()
        except OSError as error:
            self.set_status(f"Couldn't save settings: {error}", "warning")

    # -- region fields -----------------------------------------------------
    def _show_region(self, region):
        for variable, value in zip(self.region_vars, region or ("", "", "", "")):
            variable.set(str(value))

    def apply_region(self):
        try:
            values = [int(v.get().strip()) for v in self.region_vars]
        except ValueError:
            self.set_status("Region values must be whole numbers.", "error")
            return
        x, y, width, height = values
        if width <= 0 or height <= 0:
            self.set_status("Width and height must be greater than 0.", "error")
            return
        vx, vy, vw, vh = virtual_screen_rect(self.root)
        self.settings["last_region"] = values
        self._save_settings_quietly()
        if x + width <= vx or y + height <= vy or x >= vx + vw or y >= vy + vh:
            self.set_status("Region saved, but it is outside every screen.", "warning")
        else:
            self.set_status(f"Region set to {width} × {height} at ({x}, {y}).", "success")

    # -- hotkeys -----------------------------------------------------------
    def hotkey_bindings(self):
        return {action: self.settings[key] for action, key, _ in HOTKEY_SETTINGS}

    def apply_hotkeys(self):
        self.hotkeys.stop()
        for sequence in self._local_bindings:
            self.root.unbind(sequence)
        self._local_bindings = []
        bindings = self.hotkey_bindings()
        self.hotkey_problems = self.hotkeys.start(bindings) if self.settings["hotkeys_enabled"] else []
        for action, text in bindings.items():
            if action in self.hotkeys.active:
                continue
            try:
                parsed = parse_hotkey(text)
            except ValueError:
                continue
            if not parsed:
                continue
            sequence = tk_sequence(parsed, release=IS_WINDOWS and parsed[1] == "Print")
            if not sequence:
                continue
            sequences = [sequence]
            if parsed[1] == "Print" and not IS_WINDOWS:
                sequences.append(sequence.replace("-Print>", "-Sys_Req>"))
            for sequence in sequences:
                try:
                    self.root.bind(sequence, lambda e, a=action: self.trigger(a))
                    self._local_bindings.append(sequence)
                except tk.TclError:
                    pass
        for action, key, _ in HOTKEY_SETTINGS:
            self.tiles[action].set_subtitle(display_hotkey(self.settings[key]))
        if self.hotkey_problems:
            self.set_status("Hotkeys: " + "; ".join(self.hotkey_problems), "warning")

    # -- capturing ---------------------------------------------------------
    def trigger(self, action):
        if self.busy or self.settings_dialog is not None:
            return
        if action == "last" and not self.settings["last_region"]:
            self._show_window(force=True)
            self.set_status("There is no region yet. Capture a region first or type one in.", "error")
            return
        self.busy = True
        self._prev_state = self.root.state()
        visible = self._prev_state in ("normal", "zoomed")
        if visible and self._hide_without_focus_change():
            pass
        elif visible:
            self.root.withdraw()
        delay = self.settings["capture_delay"] * 1000 + (HIDE_DELAY_MS if visible else 50)
        self.root.after(delay, lambda: self._capture(action))

    def _hide_without_focus_change(self):
        """Make the window invisible without taking focus from the program in front.

        Used for hotkey captures when the window shouldn't come to the front;
        withdraw/deiconify would activate it on Windows.  X11 needs a compositor
        for transparency, so there the window is always withdrawn.
        """
        if self.settings["show_after_capture"] or not (IS_WINDOWS or IS_MAC):
            return False
        if self.root.focus_displayof() is not None:  # we're in front anyway
            return False
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            return False
        self._hidden_by_alpha = True
        return True

    def _capture(self, action):
        try:
            grab = grab_screens(self.root)
            if action == "region":
                self.selector = RegionSelector(self.root, grab, self.settings,
                                               self._region_selected, self._capture_cancelled)
            elif action == "last":
                self._capture_done(grab.crop(self.settings["last_region"]))
            else:
                self._capture_done(grab.image)
        except Exception as error:
            traceback.print_exc()
            self.selector = None
            self.busy = False
            self._show_window(force=True)
            self.set_status(f"Capture failed: {error}", "error")

    def _region_selected(self, image, region):
        self.selector = None
        self.settings["last_region"] = list(region)
        self._show_region(region)
        self._capture_done(image)

    def _capture_cancelled(self):
        self.selector = None
        self.busy = False
        self._show_window()
        self.set_status("Capture cancelled.", "info")

    def _capture_done(self, image):
        self.busy = False
        self.last_image = image
        self.last_path = None
        save_error = clipboard_error = None
        try:
            folder = self._ensure_output_folder()
            path = unique_path(folder, build_filename(self.settings["file_pattern"],
                                                      self.settings["image_format"]))
            save_image(image, path, self.settings["image_format"], self.settings["jpeg_quality"])
            self.last_path = path
            self._remember_folder(self.folder_var.get())
        except (OSError, ValueError) as error:
            save_error = str(error)
        copied = False
        if self.copy_var.get():
            clipboard_error = self.copy_image(image)
            copied = clipboard_error is None

        self._show_window()
        self._schedule_preview()
        self._update_action_buttons()
        size = f"{image.width} × {image.height}"
        if save_error:
            extra = " The image was copied to the clipboard." if copied else ""
            self.set_status(f"Not saved: {save_error}{extra}", "error")
        elif clipboard_error:
            self.set_status(f"Saved {self.last_path} ({size}), but copying failed: {clipboard_error}", "warning")
        else:
            extra = " and copied to the clipboard" if copied else ""
            self.set_status(f"Saved {self.last_path} ({size}){extra}.", "success")
        self._save_settings_quietly()
        if self.editor_var.get():
            self.open_editor()

    def _show_window(self, force=False):
        previous = self._prev_state
        if self._hidden_by_alpha:
            self._hidden_by_alpha = False
            self.root.attributes("-alpha", 1.0)
            if not force:
                return
        if force or previous in ("normal", "zoomed") or self.settings["show_after_capture"]:
            self.root.deiconify()
            if previous == "zoomed":
                try:
                    self.root.state("zoomed")
                except tk.TclError:
                    pass
            self.root.lift()
            self.root.focus_force()
        elif previous == "iconic":
            self.root.iconify()

    def copy_image(self, image):
        """Copy to the clipboard; returns an error message or None."""
        try:
            copy_image_to_clipboard(image)
            return None
        except Exception as error:
            return str(error) or error.__class__.__name__

    # -- preview & actions -------------------------------------------------
    def _schedule_preview(self, event=None):
        if self._preview_id is not None:
            self.root.after_cancel(self._preview_id)
        self._preview_id = self.root.after(60, self._draw_preview)

    def _draw_preview(self):
        self._preview_id = None
        canvas = self.preview
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if self.last_image is None:
            hotkey = display_hotkey(self.settings["hotkey_region"])
            canvas.create_text(width / 2, height / 2, justify=tk.CENTER, fill=MUTED, font=UI.font(10),
                               text=f"No capture yet\nClick Region or press {hotkey}")
            self.preview_info.configure(text="")
            return
        image_w, image_h = self.last_image.size
        pad = UI.px(10)
        scale = min(1.0, (width - 2 * pad) / image_w, (height - 2 * pad) / image_h)
        if scale <= 0:
            return
        size = (max(1, int(image_w * scale)), max(1, int(image_h * scale)))
        thumbnail = self.last_image if size == (image_w, image_h) else self.last_image.resize(size, LANCZOS, reducing_gap=2.0)
        self.preview_photo = ImageTk.PhotoImage(thumbnail, master=self.root)
        x0, y0 = (width - size[0]) // 2, (height - size[1]) // 2
        canvas.create_rectangle(x0 + 2, y0 + 2, x0 + size[0] + 2, y0 + size[1] + 2, fill="#C9CED6", outline="")
        canvas.create_image(x0, y0, anchor="nw", image=self.preview_photo)
        name = self.last_path.name if self.last_path else "Not saved"
        self.preview_info.configure(text=f"{name}  ·  {image_w} × {image_h}")

    def _update_action_buttons(self):
        has_image, has_file = self.last_image is not None, self.last_path is not None
        for name, enabled in (("edit", has_image), ("copy", has_image), ("open", has_file), ("folder", has_file)):
            self.action_buttons[name].state(["!disabled"] if enabled else ["disabled"])

    def copy_last(self):
        if self.last_image is None:
            return
        error = self.copy_image(self.last_image)
        if error:
            self.set_status(f"Copy failed: {error}", "error")
        else:
            self.set_status("Copied the last capture to the clipboard.", "success")

    def open_last_file(self):
        self._run_file_action(open_path)

    def show_in_folder(self):
        self._run_file_action(reveal_in_folder)

    def _run_file_action(self, action):
        if self.last_path is None:
            return
        try:
            action(self.last_path)
        except Exception as error:
            self.set_status(f"Couldn't open {self.last_path}: {error}", "error")

    # -- editor ------------------------------------------------------------
    def open_editor(self):
        if self.last_image is None:
            self.set_status("Take a screenshot first, then edit it.", "info")
            return
        for editor in self.editors:
            if editor.path is not None and editor.path == self.last_path:
                editor.win.deiconify()
                editor.win.lift()
                editor.win.focus_force()
                return
        self.editors.append(ImageEditor(self, self.last_image, self.last_path))

    def editor_saved(self, image, path):
        if path == self.last_path or self.last_path is None:
            self.last_image, self.last_path = image, path
            self._schedule_preview()
            self._update_action_buttons()
        self.set_status(f"Saved edited screenshot {path}.", "success")

    def editor_closed(self, editor):
        if editor in self.editors:
            self.editors.remove(editor)
        self._save_settings_quietly()

    # -- settings ----------------------------------------------------------
    def open_settings(self):
        if self.settings_dialog is not None:
            self.settings_dialog.win.lift()
            return
        self.hotkeys.stop()  # so the hotkey boxes can record the current keys
        self.settings_dialog = SettingsDialog(self)

    def settings_saved(self):
        self.copy_var.set(self.settings["copy_to_clipboard"])
        self.editor_var.set(self.settings["open_editor"])
        self._folder_changed()
        self._schedule_preview()
        self.apply_hotkeys()
        if not self.hotkey_problems:
            self.set_status("Settings saved.", "success")

    # -- shutdown ----------------------------------------------------------
    def quit(self):
        for editor in list(self.editors):
            if not editor.close():
                return
        if self.root.state() == "normal":
            self.settings["window_geometry"] = self.root.geometry()
        self.settings["folder_name"] = self.folder_var.get().strip()
        try:
            self.settings.save()
        except OSError:
            pass
        self.hotkeys.stop()
        self.root.destroy()


def main():
    enable_dpi_awareness()
    root = tk.Tk(className="BWScreenshotTool")
    root.withdraw()
    ScreenshotApp(root)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
