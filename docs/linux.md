# Linux (Arch) notes

Same editor as the Windows build: open an MHGU mod (a release zip from
https://github.com/Scrimas/MH4U-port-to-MHGU or a mod folder), browse every arc and texture, drop images on
textures to stage replacements, then export the modified mod or a patch with only the changed arcs.

## Install

```sh
git clone <this repository> && cd mhgu-mod-texture-editor     # or unpack a release's linux tarball
./install.sh
```

`install.sh`:
- offers `sudo pacman -S --needed` for what is missing: `python tk python-numpy python-pillow`, plus `zenity`
  (or `kdialog` on KDE) for your desktop's own file pickers;
- installs the tool into `~/.local/share/mhgu-texture-tool/`, the command `~/.local/bin/mhgu-texture-tool`
  and an app-menu entry, **MHGU Mod Texture Editor**;
- makes a private Python environment for drag and drop (`tkinterdnd2` from PyPI, which bundles the Linux tkdnd
  library). Arch's system Python refuses `pip install` (PEP 668), so it lives in
  `~/.local/share/mhgu-texture-tool/venv` and reuses the pacman numpy / Pillow / Tk.

Update: `git pull` (or unpack the new release), then run `./install.sh` again. Remove: `./install.sh --uninstall`.

No install: `./mhgu-texture-tool` from the repository folder works too (same first-run setup).

## Run

- App menu: **MHGU Mod Texture Editor** (also offered under "Open with" for zips and folders).
- Terminal: `mhgu-texture-tool`, `mhgu-texture-tool stygian_zinogre-1.0.3.zip`, or
  `mhgu-texture-tool ~/.config/Ryujinx/mods/contents/0100770008dd8000/"MH4U Port"`.
- File pickers start in your Ryujinx mods folder (`~/.config/Ryujinx/mods/contents/0100770008dd8000`), then
  in the last folder you used.

How to use it: see the [main README](../README.md) (open → expand an arc → drop an image on the preview → Undo / compare →
*Export modified mod…* or *Export patch…*, as a folder or a zip).

To install a patch for Ryujinx, extract or copy it into `~/.config/Ryujinx/mods/contents/0100770008dd8000/`.
It holds `MH4U Port/romfs/...`, so it lands over the mod's files.

## Desktop notes

- **Wayland**: Tk runs through XWayland. Dragging files in from Dolphin, Nautilus or Thunar works on KDE
  Plasma and GNOME; if your compositor does not pass drags to XWayland windows, use *Replace…* or
  *Stage images from folder…* instead.
- **HiDPI**: Tk does not follow the desktop scale. The tool uses `GDK_SCALE` / `QT_SCALE_FACTOR` when set,
  or `MHGU_SCALE=1.5 mhgu-texture-tool` (edit the `Exec=` line of
  `~/.local/share/applications/mhgu-texture-tool.desktop` to make it stick).
- **File pickers**: `MHGU_NATIVE_DIALOGS=0` falls back to Tk's built-in dialogs.
- **Other distributions**: the launcher only needs `python3` with Tk, numpy and Pillow (Debian / Ubuntu:
  `python3-tk python3-numpy python3-pil python3-venv`); `install.sh` skips the pacman step there.

## Command line

`mhgu-texture-tool -h` lists everything; the mod flow without the GUI:
```sh
mhgu-texture-tool mod stygian_zinogre-1.0.3.zip --export-pngs originals
mhgu-texture-tool mod stygian_zinogre-1.0.3.zip --images my_edits --out patch.zip --patch
```

License: GPL-3.0-or-later.
