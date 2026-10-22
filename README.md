# BW Screenshot Tool

A screenshot helper that works like [Greenshot](https://getgreenshot.org/): press
**Print Screen**, drag over the part of the screen you want, and the image is saved
straight into your project folder, copied to the clipboard and ready to annotate.

The latest version is **`screenshot_toolV7.py`**. Older `screenshot_tool*.py` files
are kept for reference.

## Install and run

1. Install Python 3.8 or newer (on Windows tick *"Add python.exe to PATH"*).
2. Install the packages with the same Python you will run the tool with.
   The imaging package is called **Pillow** (`PIL` is only its import name):

   ```
   python -m pip install Pillow screeninfo
   ```

   If `pip` isn't found, use the full path to your Python, e.g.
   `& C:\Users\<you>\.local\bin\python3.14.exe -m pip install Pillow screeninfo`.
   If that reports *"externally-managed-environment"* or *"No module named pip"*
   (common with Pythons installed by `uv`), give the tool its own environment:

   ```
   python -m venv bw-env
   bw-env\Scripts\python.exe -m pip install Pillow screeninfo      (Linux/macOS: bw-env/bin/python)
   ```

   and start it with `bw-env\Scripts\pythonw.exe screenshot_toolV7.py`.
   With `uv` installed, `uv run --with pillow --with screeninfo screenshot_toolV7.py`
   also works without any setup.

3. Start the tool:

   ```
   pythonw screenshot_toolV7.py      (Windows, no console window)
   python3 screenshot_toolV7.py      (Linux / macOS)
   ```

If a `logo.ico` (or `logo.png`) is next to the script it is used as the window
icon, otherwise a built-in icon is shown.

## Quick start

1. *(Optional)* Open **Settings** and choose the **master folder**. If you don't,
   screenshots go to your *Pictures* folder.
2. Under **Save to**, type a folder name or pick one from the list/**Browse…**
   (e.g. `Project A/Step 1`). Leave it empty to use the `Screenshots` folder.
3. Press **Print Screen** (or click **Region**), drag over an area and let go.
4. The screenshot is saved, copied to the clipboard and shown under **Last capture**,
   where you can **Edit**, **Copy**, **Open** or **Show in folder**.
5. Press **Shift + Print Screen** (or click **Last region**) to capture the same
   area again, as often as you like.

Files are named `screenshot_YYYYMMDD_HHMMSS.png`; if two captures happen in the
same second the second one gets `_1` added, so nothing is ever overwritten.

## Capturing

| Action | Hotkey | What it does |
| --- | --- | --- |
| Region | Print Screen | Freeze all screens and drag to select an area |
| Last region | Shift + Print Screen | Capture the previous area again, or the X/Y/W/H typed in the Region box |
| Full screen | Ctrl + Print Screen | Capture every monitor in one image |

The hotkeys work while other programs are in front and can be changed in
**Settings › Hotkeys**.

While selecting a region (like Greenshot):

* lines across the whole screen follow the mouse, with the X/Y position,
  selection size and pixel colour next to a **magnifier**;
* the area outside the selection is darkened and the size (`W × H`) is shown;
* **arrow keys** move the cursor by 1 pixel (**Shift**: 10 pixels) for exact edges;
* **Enter** captures the whole monitor under the mouse;
* **Esc** or **right-click** cancels.

It works across all monitors, including ones left of or above the main screen and
displays scaled to 125 % / 150 %.

## Editor

Click **Edit** (or double-click the preview, or tick *Open in editor*) to annotate.

| Tool | Key | |
| --- | --- | --- |
| Rectangle | R | Shift draws a square |
| Ellipse | E | Shift draws a circle |
| Line | L | Shift snaps to 45° |
| Arrow | A | Shift snaps to 45° |
| Freehand | F | |
| Text | T | Click, type, press Enter |
| Highlight | H | Yellow marker over an area |
| Obfuscate | O | Pixelates an area (passwords, personal data) |
| Number | N | Numbered step markers 1, 2, 3… |
| Crop | C | Drag the area to keep |

Pick the colour and size on the left. **Ctrl+Z** / **Ctrl+Y** undo and redo,
**Ctrl+S** saves over the capture, **Ctrl+Shift+S** saves a copy, **Ctrl+C** copies
the edited image.

## Settings

* **General**: master folder, file name pattern (`%Y %m %d %H %M %S` codes),
  PNG/JPG/BMP, JPEG quality, capture delay.
* **Capture**: selection border colour and width, crosshair lines on/off, colour
  and width, magnifier, how much to darken outside the selection (with a live preview).
* **After capture**: copy to clipboard, open the editor, bring the window to the front.
* **Hotkeys**: click a box and press the keys you want.

Settings are stored in `screenshot_settings.json` **next to the program**, so you
can keep several copies (installs) with different settings side by side. If that
folder is read-only, the file goes to `%APPDATA%\BW Screenshot Tool` instead.

## Troubleshooting

* **Print Screen opens the Windows Snipping Tool.** Turn off *"Use the Print screen
  key to open screen capture"* in *Windows Settings › Accessibility › Keyboard*, or
  pick other hotkeys in Settings.
* **"… is already used by another program".** Another app (often Greenshot,
  Lightshot, OneDrive or Dropbox) has that hotkey. Close it or choose another key.
* **Old or damaged settings.** You no longer need to delete `screenshot_settings.json`:
  unknown or invalid values are ignored and a damaged file is backed up as
  `screenshot_settings.json.bak` before defaults are used.
* **Linux:** copying images needs `xclip` (or `wl-clipboard` on Wayland); global
  hotkeys need `pynput`. Without it, hotkeys work while the tool's window is focused.

## Tests

```
python -m unittest discover tests
```
