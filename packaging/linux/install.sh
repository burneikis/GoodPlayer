#!/usr/bin/env bash
# Install GoodPlayer and DoesPlayer from an unpacked release archive.
#   ./install.sh            install for this user (~/.local)
#   ./install.sh --uninstall
set -euo pipefail

prefix="${PREFIX:-$HOME/.local}"
here="$(cd "$(dirname "$0")" && pwd)"
dest="$prefix/lib/goodplayer"
apps=(goodplayer doesplayer)

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -rf "$dest"
    for app in "${apps[@]}"; do
        rm -f "$prefix/bin/$app" "$prefix/share/applications/$app.desktop" \
            "$prefix/share/icons/hicolor/256x256/apps/$app.png"
    done
    echo "Uninstalled from $prefix"
    exit 0
fi

mkdir -p "$dest" "$prefix/bin" "$prefix/share/applications" "$prefix/share/icons/hicolor/256x256/apps"
cp -r "$here/lib" "$dest/"
for app in "${apps[@]}"; do
    install -m755 "$here/$app" "$dest/$app"
    ln -sf "$dest/$app" "$prefix/bin/$app"
    install -m644 "$here/$app.desktop" "$prefix/share/applications/$app.desktop"
    install -m644 "$here/$app.png" "$prefix/share/icons/hicolor/256x256/apps/$app.png"
done
update-desktop-database "$prefix/share/applications" 2>/dev/null || true
echo "Installed to $prefix (make sure $prefix/bin is on PATH)"
