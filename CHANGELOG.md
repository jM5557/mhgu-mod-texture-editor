# Changelog

## 1.1.1 (2026-11-04)

- *Export differing textures as PNG* (Compare mods) now goes through the same route as *Export original PNGs*
  (whole arc through `load_arc`, every texture through one shared `texture_png`), so both exports always
  write the same PNGs, alpha included. A test checks the compare export against the main export byte for
  byte, with a gradient and a cut-out in the alpha.

## 1.1.0 (2026-11-03)

- *Export unpacked changes…*: only the changed textures, as MHGU `.tex` (+ optional PNG) in unpacked-arc
  folders, with `_changes.json` (CLI: `mod … --out-unpacked DIR [--no-png]`).
- `.tex` files can be staged directly (drop, *Replace…*, *Stage images from folder…*); a `.tex` wins over
  its PNG copy.
- *Compare mods…*: textures that differ between two mods (folders or zips), with previews, a CSV report
  and PNG export (CLI: `compare A B [--csv F] [--export-pngs DIR] [--all]`).

## 1.0.0 (2026-11-03)

First release.
- Open a whole MHGU mod (folder or release zip); browse every arc and texture with previews.
- Stage replacements by drag and drop or *Replace…*, with Undo and original / replacement comparison.
- Export the whole modified mod or a patch with only the modified arcs, as a folder or a zip.
- Export every original texture as PNG; stage a folder of edited images in one go.
- BC1 / BC3 / BC4 / BC5 / RGBA8 encoding with mips and Switch block-linear layout; keeps the original
  alpha when the new image has none; arcs verified before they are written.
- Windows launchers; Linux launcher, installer, app-menu entry, native file pickers (zenity / kdialog),
  HiDPI scaling.
- Command line: `mod` flow plus single-arc tools.
