#!/usr/bin/env bash
# Install Openface Mixer for the current user (~/.local). No root needed unless
# build/runtime packages are missing (Arch/CachyOS: installed via pacman).
set -euo pipefail
cd "$(dirname "$0")/.."

PREFIX="${PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share/openface-mixer"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"

if command -v pacman >/dev/null; then
    missing=()
    for pkg in pyside6 libpipewire libusb alsa-utils libpulse gcc pkgconf make; do
        pacman -Qq "$pkg" &>/dev/null || missing+=("$pkg")
    done
    if ((${#missing[@]})); then
        echo "Installing missing packages: ${missing[*]}"
        sudo pacman -S --needed "${missing[@]}"
    fi
fi

make engine

# --- one-time migration from the prototype, "TotalMix Lite" (tmlite)
if systemctl --user cat tmlite-engine.service &>/dev/null; then
    echo "Migrating from TotalMix Lite…"
    systemctl --user disable --now tmlite-engine.service || true
    if [[ -d "$CONFIG/tmlite" && ! -e "$CONFIG/openface-mixer/state.json" ]]; then
        mkdir -p "$CONFIG/openface-mixer"
        cp -n "$CONFIG/tmlite/state.json" "$CONFIG/tmlite/matrix.bin" "$CONFIG/openface-mixer/" 2>/dev/null || true
        echo "  copied your saved mix to $CONFIG/openface-mixer"
    fi
    rm -f "$PREFIX/bin/tmlite" "$PREFIX/bin/tmlite-engine" \
          "$PREFIX/share/applications/tmlite.desktop" "$UNIT_DIR/tmlite-engine.service" \
          "/dev/shm/tmlite-$(id -u)"
    rm -rf "$PREFIX/share/tmlite"
fi
pkill -x tmlite-engine 2>/dev/null || true

# --- files
install -Dm755 engine/openface-mixer-engine "$PREFIX/bin/openface-mixer-engine"
rm -rf "$SHARE"
mkdir -p "$SHARE"
cp -r openface_mixer "$SHARE/"
find "$SHARE" -name __pycache__ -prune -exec rm -rf {} +
install -Dm644 packaging/openface-mixer.desktop "$PREFIX/share/applications/openface-mixer.desktop"
install -Dm644 packaging/openface-mixer-engine.service "$UNIT_DIR/openface-mixer-engine.service"
install -Dm644 packaging/openface-mixer-engine@.service "$UNIT_DIR/openface-mixer-engine@.service"
cat > "$PREFIX/bin/openface-mixer" <<SH
#!/bin/sh
PYTHONPATH="$SHARE\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m openface_mixer "\$@"
SH
chmod 755 "$PREFIX/bin/openface-mixer"

# --- USB access for the hardware mixer (needs root once)
RULE=/etc/udev/rules.d/70-rme-digiface.rules
if ! cmp -s packaging/70-rme-digiface.rules "$RULE"; then
    echo "Installing $RULE so the mixer can talk to the Digiface over USB (needs sudo)…"
    if sudo install -Dm644 packaging/70-rme-digiface.rules "$RULE"; then
        sudo udevadm control --reload
        sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=2a39
    else
        echo "  skipped: hardware mixing stays unavailable until the rule is installed"
    fi
fi

# --- FireWire access for the Fireface 802's hardware mixer (needs root once)
RULE=/etc/udev/rules.d/70-rme-fireface.rules
if ! cmp -s packaging/70-rme-fireface.rules "$RULE"; then
    echo "Installing $RULE so the mixer can talk to RME FireWire interfaces (needs sudo)…"
    if sudo install -Dm644 packaging/70-rme-fireface.rules "$RULE"; then
        sudo udevadm control --reload
        sudo udevadm trigger --subsystem-match=firewire
    else
        echo "  skipped: Fireface 802 mixing stays unavailable until the rule is installed"
    fi
fi

# --- engine services (restart so an upgrade takes effect). The Digiface's always runs; other
# devices' engines (openface-mixer-engine@<device>) are enabled by the GUI the first time it
# opens that device, and restarted here if they are.
device_units=$(systemctl --user list-units --plain --no-legend 'openface-mixer-engine@*' | awk '{print $1}')
pkill -x openface-mixer-engine 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable openface-mixer-engine.service
systemctl --user restart openface-mixer-engine.service
for unit in $device_units; do
    systemctl --user restart "$unit"
done

echo
echo "Installed. Launch 'Openface Mixer' from your app menu or run: openface-mixer"
echo "Tip: choose 'Openface Mixer Playback' as your output device to route desktop audio through the mixer."
