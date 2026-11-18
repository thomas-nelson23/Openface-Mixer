#!/usr/bin/env bash
# Install Openface Mixer for the current user, in ~/.local or PREFIX. No root needed unless
# build/runtime packages are missing (Arch/CachyOS: installed via pacman) or the udev rules are new.
#
# With DESTDIR set it only stages the files for a package (packaging/aur): the units and udev
# rules go under PREFIX as well, and nothing on the running system is touched.
set -euo pipefail
cd "$(dirname "$0")/.."

PREFIX="${PREFIX:-$HOME/.local}"
DESTDIR="${DESTDIR:-}"
case "$PREFIX" in /*) ;; *) echo "PREFIX must be an absolute path" >&2; exit 1 ;; esac
SHARE="$PREFIX/share/openface-mixer"
if [ -n "$DESTDIR" ]; then
    UNIT_DIR="$PREFIX/lib/systemd/user"
else
    CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
    UNIT_DIR="$CONFIG/systemd/user"
fi

if [ -z "$DESTDIR" ] && command -v pacman >/dev/null; then
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

# --- files
# The menu entry and the units name the commands by their full path, so neither depends on
# PREFIX/bin being on the session's PATH.
bindir=$(printf '%s' "$PREFIX/bin" | sed 's/[&|\\]/\\&/g')
fill() {    # fill SRC DEST: install SRC with @BINDIR@ filled in
    install -Dm644 /dev/null "$2"
    sed "s|@BINDIR@|$bindir|g" "$1" > "$2"
}
install -Dm755 engine/openface-mixer-engine "$DESTDIR$PREFIX/bin/openface-mixer-engine"
rm -rf "$DESTDIR$SHARE"
mkdir -p "$DESTDIR$SHARE"
cp -r openface_mixer "$DESTDIR$SHARE/"
find "$DESTDIR$SHARE" -name __pycache__ -prune -exec rm -rf {} +
fill packaging/openface-mixer.desktop "$DESTDIR$PREFIX/share/applications/openface-mixer.desktop"
install -Dm644 openface_mixer/openface-mixer.svg "$DESTDIR$PREFIX/share/icons/hicolor/scalable/apps/openface-mixer.svg"
fill packaging/openface-mixer-engine.service "$DESTDIR$UNIT_DIR/openface-mixer-engine.service"
fill packaging/openface-mixer-engine@.service "$DESTDIR$UNIT_DIR/openface-mixer-engine@.service"
cat > "$DESTDIR$PREFIX/bin/openface-mixer" <<SH
#!/bin/sh
PYTHONPATH="$SHARE\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m openface_mixer "\$@"
SH
chmod 755 "$DESTDIR$PREFIX/bin/openface-mixer"

if [ -n "$DESTDIR" ]; then
    install -Dm644 -t "$DESTDIR$PREFIX/lib/udev/rules.d" packaging/70-rme-digiface.rules packaging/70-rme-fireface.rules
    # the GUI's Updates section then leaves updating to the package manager
    echo package > "$DESTDIR$SHARE/install-source"
    exit 0
fi

gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" 2>/dev/null || true
# where this was installed from, for the GUI's update button
if commit=$(git rev-parse HEAD 2>/dev/null); then
    printf '%s\n%s\n' "$PWD" "$commit" > "$SHARE/install-source"
fi

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

# --- FireWire access for the Fireface 802/800/400 hardware mixer (needs root once)
RULE=/etc/udev/rules.d/70-rme-fireface.rules
if ! cmp -s packaging/70-rme-fireface.rules "$RULE"; then
    echo "Installing $RULE so the mixer can talk to RME FireWire interfaces (needs sudo)…"
    if sudo install -Dm644 packaging/70-rme-fireface.rules "$RULE"; then
        sudo udevadm control --reload
        sudo udevadm trigger --subsystem-match=firewire
    else
        echo "  skipped: Fireface mixing stays unavailable until the rule is installed"
    fi
fi

# --- engine services. The GUI enables a device's engine once it has seen the interface. Here
# the Digiface's is also enabled when one is connected, and every engine that runs or is
# enabled is restarted so the upgrade takes effect.
digiface_connected() {      # a Digiface USB on its standard firmware (engine/digiface_usb.c)
    local vendor
    for vendor in /sys/bus/usb/devices/*/idVendor; do
        [ "$(cat "$vendor" 2>/dev/null)" = 2a39 ] || continue
        case "$(cat "${vendor%idVendor}idProduct" 2>/dev/null)" in 3f8c | 3fa0) return 0 ;; esac
    done
    return 1
}
DIGIFACE=openface-mixer-engine.service
if digiface_connected; then
    systemctl --user enable "$DIGIFACE"
elif [ ! -e "$CONFIG/openface-mixer/state.json" ]; then
    # Older installs enabled it for everyone. Without a saved Digiface mix it was never used:
    # switch it off, with its playback sink that goes nowhere.
    systemctl --user disable --now "$DIGIFACE" 2>/dev/null || true
fi
# Before 0.4.0 the engines were wanted by pipewire.service, so they never started without it:
# enable them again, for the login itself.
for link in "$UNIT_DIR"/pipewire.service.wants/openface-mixer-engine*.service; do
    if [ -L "$link" ]; then
        systemctl --user reenable "$(basename "$link")" || true
    fi
done
units=$(systemctl --user list-units --plain --no-legend 'openface-mixer-engine*' | awk '{print $1}')
if systemctl --user --quiet is-enabled "$DIGIFACE" 2>/dev/null; then
    units="$units $DIGIFACE"
fi
# Also stops engines started by hand or by the GUI without systemd. Process names are cut to
# 15 characters, so the full name would never match.
pkill -u "$(id -u)" -x openface-mixer- 2>/dev/null || true
systemctl --user daemon-reload
# shellcheck disable=SC2086  # unit names have no spaces: one word each
for unit in $(printf '%s\n' $units | sort -u); do
    systemctl --user restart "$unit"
done

echo
echo "Installed. Launch 'Openface Mixer' from your app menu or run: $PREFIX/bin/openface-mixer"
echo "Tip: choose 'Openface Mixer Playback' as your output device to route desktop audio through the mixer."
