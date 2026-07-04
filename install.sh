#!/usr/bin/env bash
set -euo pipefail

APP_NAME=pinight
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"

echo "Installing $APP_NAME..."

NEEDED=()
command -v python3 >/dev/null || NEEDED+=(python3)
python3 -c "import tkinter" 2>/dev/null || NEEDED+=(python3-tk)
if ! command -v redshift >/dev/null && ! command -v gammastep >/dev/null; then
    NEEDED+=(redshift)
fi
if [ ${#NEEDED[@]} -gt 0 ]; then
    echo "Installing dependencies: ${NEEDED[*]}"
    sudo apt update
    sudo apt install -y "${NEEDED[@]}"
fi

mkdir -p "$BIN_DIR"
install -m 755 pinight.py "$BIN_DIR/$APP_NAME"

mkdir -p "$DESKTOP_DIR"
# Substitute the absolute path into Exec= so the LXDE/PIXEL menu launcher
# (which does not source ~/.profile, so ~/.local/bin is not on its PATH)
# can still find the binary.
sed "s|^Exec=pinight\$|Exec=$BIN_DIR/$APP_NAME|" pinight.desktop \
    > "$DESKTOP_DIR/$APP_NAME.desktop"
chmod 644 "$DESKTOP_DIR/$APP_NAME.desktop"

mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/$APP_NAME-apply.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=pinight (apply saved)
Exec=$BIN_DIR/$APP_NAME --apply-saved
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo
        echo "Note: $BIN_DIR is not on your PATH."
        echo "Add this to ~/.bashrc if you want to launch 'pinight' from a terminal:"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        ;;
esac

echo
echo "Installed. Launch from the Applications menu or run: $APP_NAME"
