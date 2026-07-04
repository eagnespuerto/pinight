#!/usr/bin/env bash
set -euo pipefail

APP_NAME=pinight
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"

if command -v redshift >/dev/null; then
    redshift -x || true
elif command -v gammastep >/dev/null; then
    gammastep -x || true
fi

rm -f "$BIN_DIR/$APP_NAME"
rm -f "$DESKTOP_DIR/$APP_NAME.desktop"
rm -f "$AUTOSTART_DIR/$APP_NAME-apply.desktop"

echo "Uninstalled. Config kept at ~/.config/$APP_NAME (delete manually if you want)."
