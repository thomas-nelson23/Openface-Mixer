#!/usr/bin/env bash
# Remove what scripts/install.sh put in ~/.local (or PREFIX) and the login entries the app
# made. Your saved mixes in ~/.config/openface-mixer are kept.
set -u
PREFIX="${PREFIX:-$HOME/.local}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
UNIT_DIR="$CONFIG/systemd/user"

systemctl --user disable --now openface-mixer-engine.service 2>/dev/null
for unit in $(systemctl --user list-units --all --plain --no-legend 'openface-mixer-engine@*' | awk '{print $1}'); do
    systemctl --user disable --now "$unit" 2>/dev/null
done
# engines started without systemd (process names are cut to 15 characters)
pkill -u "$(id -u)" -x openface-mixer- 2>/dev/null
rm -f "$PREFIX/bin/openface-mixer" "$PREFIX/bin/openface-mixer-engine" \
      "$PREFIX/share/applications/openface-mixer.desktop" \
      "$PREFIX/share/icons/hicolor/scalable/apps/openface-mixer.svg" \
      "$UNIT_DIR/openface-mixer-engine.service" "$UNIT_DIR/openface-mixer-engine@.service" \
      "$UNIT_DIR"/pipewire.service.wants/openface-mixer-engine*.service \
      "$UNIT_DIR"/default.target.wants/openface-mixer-engine*.service \
      "$CONFIG"/autostart/openface-mixer-*.desktop \
      /dev/shm/openface-mixer-*"$(id -u)"
rm -rf "$PREFIX/share/openface-mixer"
systemctl --user daemon-reload
echo "Removed. Saved mixes and presets are still in $CONFIG/openface-mixer (delete it if you like)."

# The udev rules belong to root.
rules=()
for rule in /etc/udev/rules.d/70-rme-digiface.rules /etc/udev/rules.d/70-rme-fireface.rules; do
    [ -e "$rule" ] && rules+=("$rule")
done
if ((${#rules[@]})); then
    if [ -w /etc/udev/rules.d ]; then
        rm -f "${rules[@]}"
    else
        echo "The udev rules that give you access to the interfaces are still installed. To remove them, run:"
        echo "  sudo rm ${rules[*]}"
    fi
fi
