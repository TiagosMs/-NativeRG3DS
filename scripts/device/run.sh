#!/bin/bash
# run.sh <title id> [rom]: plays a game Pocket Native was built with, from the
# launcher scripts/build.sh puts beside this folder, or from 3ds.sh with the
# ROM it was given. EmulationStation waits, stopped, while the game runs, and
# the game starts again when its menu's Reset asks for it.
dir="$(cd "$(dirname "$0")" && pwd)"
game="$dir/games/$1"
rom="${2:-$game/game.3ds}"
data="$game/data"
mkdir -p "$data" "$dir/config"

export ZAKURO_FAST=1 ZAKURO_KMS=1 ZAKURO_DATA="$data" ZAKURO_RECOMPILED="$game/title.so" ZAKURO_SHADER_MANIFEST="$data/shaders.txt"
export POCKET_NATIVE_TITLES="$dir/titles" POCKET_NATIVE_POWER="$dir/power.sh" POCKET_NATIVE_CONFIG="$dir/config"
# draws to leave out besides the game's options, as ZAKURO_DROP_DRAWS takes them
[ -f "$data/drops.txt" ] && export ZAKURO_DROP_DRAWS="$(cat "$data/drops.txt")"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
[ -d "$XDG_RUNTIME_DIR" ] || export XDG_RUNTIME_DIR="/var/run"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
[ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ] || export WAYLAND_DISPLAY="wayland-0"
export SWAYSOCK="${SWAYSOCK:-/var/run/0-runtime-dir/sway-ipc.0.sock}"

restore_env() {
    "$dir/power.sh" balanced
    chvt 1 >/dev/null 2>&1
    if command -v swaymsg >/dev/null 2>&1; then
        swaymsg 'output DSI-1 power on, output DSI-2 power on' >/dev/null 2>&1
        python3 -c '
import json, subprocess
try:
    tree = json.loads(subprocess.check_output(["swaymsg", "-t", "get_tree"]))
    def find_es(node):
        if node.get("app_id") == "emulationstation": return node
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            res = find_es(child)
            if res: return res
        return None
    es = find_es(tree)
    if es and not es.get("visible"):
        subprocess.call(["swaymsg", "[app_id=\"emulationstation\"] scratchpad show"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.call(["swaymsg", "[app_id=\"emulationstation\"] focus"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
except Exception:
    pass
' >/dev/null 2>&1
    fi
    printf '\033[?25l' > /dev/tty0 2>/dev/null
    printf '\033[?25l' > /dev/tty1 2>/dev/null
    printf '\033[?25l' > /dev/tty2 2>/dev/null
    echo 0 > /sys/class/graphics/fbcon/cursor_blink 2>/dev/null
    return 0
}
trap restore_env EXIT HUP INT TERM

if command -v swaymsg >/dev/null 2>&1; then
    swaymsg 'output DSI-1 power on' >/dev/null 2>&1
    chvt 2 >/dev/null 2>&1
fi
"$dir/power.sh" performance
sudo -n sh -c "echo 1 > /proc/sys/debug/exception-trace" 2>/dev/null
cd "$game"
while true; do
    "$dir/pocket-native" "$rom" > "$data/log.txt" 2>&1
    status=$?
    [ $status -eq 3 ] || break
done
if [ $status -ne 0 ]; then
    echo "exit status $status" >> "$data/log.txt"
    dmesg | tail -40 >> "$data/log.txt"
fi
exit 0
