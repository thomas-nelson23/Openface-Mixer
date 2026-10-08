#!/usr/bin/env bash
# Remove Openface Mixer from ~/.local. Your saved mixes in ~/.config/openface-mixer are kept.
set -u
PREFIX="${PREFIX:-$HOME/.local}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user disable --now openface-mixer-engine.service 2>/dev/null
for unit in $(systemctl --user list-units --all --plain --no-legend 'openface-mixer-engine@*' | awk '{print $1}'); do
    systemctl --user disable --now "$unit" 2>/dev/null
done
rm -f "$PREFIX/bin/openface-mixer" "$PREFIX/bin/openface-mixer-engine" \
      "$PREFIX/share/applications/openface-mixer.desktop" \
      "$UNIT_DIR/openface-mixer-engine.service" "$UNIT_DIR/openface-mixer-engine@.service" \
      /dev/shm/openface-mixer-*"$(id -u)"
rm -rf "$PREFIX/share/openface-mixer"
systemctl --user daemon-reload
echo "Removed. Saved mixes and presets are still in ~/.config/openface-mixer (delete it if you like)."
