#!/usr/bin/env bash
# Install the player in this archive (GoodPlayer or DoesPlayer).
#   ./install.sh              install for this user (~/.local)
#   ./install.sh --uninstall  remove it again
# Set PREFIX to install somewhere else.
set -euo pipefail

prefix="${PREFIX:-$HOME/.local}"
here="$(cd "$(dirname "$0")" && pwd)"
app="$(basename "$(ls "$here"/*.desktop | head -1)" .desktop)"
dest="$prefix/lib/$app"
icon="$prefix/share/icons/hicolor/256x256/apps/$app.png"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -rf "$dest"
    rm -f "$prefix/bin/$app" "$prefix/share/applications/$app.desktop" "$icon"
    echo "Uninstalled $app from $prefix"
    exit 0
fi

mkdir -p "$dest" "$prefix/bin" "$prefix/share/applications" "$(dirname "$icon")"
cp -r "$here/lib" "$dest/"
install -m755 "$here/$app" "$dest/$app"
ln -sf "$dest/$app" "$prefix/bin/$app"
install -m644 "$here/$app.desktop" "$prefix/share/applications/$app.desktop"
install -m644 "$here/$app.png" "$icon"
update-desktop-database "$prefix/share/applications" 2>/dev/null || true
echo "Installed $app to $prefix (make sure $prefix/bin is on PATH)"
