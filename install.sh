#!/usr/bin/env bash
# install.sh : install the MHGU Mod Texture Editor for the current user (no root needed except for pacman).
#   ./install.sh               install (or update) into ~/.local, add the app-menu entry
#   ./install.sh --uninstall   remove it again (your exports and PNGs are not touched)
set -euo pipefail

SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$DATA/mhgu-texture-tool/app"
BIN="$HOME/.local/bin"
DESK="$DATA/applications/mhgu-texture-tool.desktop"
ICON="$DATA/icons/hicolor/128x128/apps/mhgu-texture-tool.png"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$DATA/mhgu-texture-tool" "$BIN/mhgu-texture-tool" "$DESK" "$ICON"
    rm -f "$HOME/.mhgu_tex_tool.json"
    command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" 2>/dev/null || true
    echo "Uninstalled."
    exit 0
fi

# system packages (Arch): Python, Tk, numpy, Pillow; zenity / kdialog give the desktop's own file pickers
if command -v pacman >/dev/null; then
    need=()
    for p in python tk python-numpy python-pillow; do pacman -Qq "$p" >/dev/null 2>&1 || need+=("$p"); done
    if ! command -v zenity >/dev/null && ! command -v kdialog >/dev/null; then
        case "${XDG_CURRENT_DESKTOP:-}" in *KDE*) need+=(kdialog) ;; *) need+=(zenity) ;; esac
    fi
    if [ ${#need[@]} -gt 0 ]; then
        echo "Missing packages: ${need[*]}"
        read -r -p "Install them with 'sudo pacman -S --needed ${need[*]}'? [Y/n] " a
        case "${a:-y}" in [Yy]*) sudo pacman -S --needed "${need[@]}" ;; *) echo "Skipped; the launcher will tell you if something is missing." ;; esac
    fi
else
    echo "pacman not found: install python3 with Tk, numpy and Pillow from your distribution's packages."
fi

mkdir -p "$APP" "$BIN" "$(dirname "$DESK")" "$(dirname "$ICON")"
install -m 644 "$SRC/mhgu_tex_tool.py" "$APP/"
install -m 755 "$SRC/mhgu-texture-tool" "$APP/"
if [ -f "$SRC/icon.png" ]; then install -m 644 "$SRC/icon.png" "$ICON"; install -m 644 "$SRC/icon.png" "$APP/"; fi
ln -sf "$APP/mhgu-texture-tool" "$BIN/mhgu-texture-tool"

cat > "$DESK" <<EOF
[Desktop Entry]
Type=Application
Name=MHGU Mod Texture Editor
GenericName=Texture editor for MHGU mods
Comment=Browse, replace and repack the textures of MHGU mod arcs
Exec="$APP/mhgu-texture-tool" %f
Icon=mhgu-texture-tool
Terminal=false
Categories=Graphics;Game;Utility;
MimeType=application/zip;inode/directory;
StartupWMClass=Mhgutexturetool
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" 2>/dev/null || true

echo "Setting up the Python environment (first run)..."
"$APP/mhgu-texture-tool" -h >/dev/null && echo "OK."
echo
echo "Installed. Start it from your app menu (MHGU Mod Texture Editor) or run: mhgu-texture-tool [MOD.zip]"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not in your PATH; add it to use the command from a terminal." ;; esac
