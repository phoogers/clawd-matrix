#!/usr/bin/env python3
"""Display Clawd (Claude Code mascot) on a 16x16 WLED matrix.

Sprite data extracted from https://claude-code-mascot-generator.replit.app/
Default colors match Anthropic's #CD7B5A.

Usage:
    python clawd.py [pose]              # show pose, default "normal"
    python clawd.py off                 # turn matrix off
    python clawd.py --list              # list available poses

Poses: normal, happy, wink, surprised, looking_down, sleeping,
       cool, angry, dancing, raising_arm, pointing_right
"""

import json
import os
import sys
import urllib.request


def _load_env():
    """Read .env from this script's directory and inject into os.environ."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_env()

WLED_URL = os.environ.get("WLED_URL", "http://wled.local")
WIDTH = int(os.environ.get("WLED_WIDTH", "16"))
HEIGHT = int(os.environ.get("WLED_HEIGHT", "16"))

# Physical-orientation corrections applied at the last moment before
# pushing pixels to WLED. Use these if your matrix is wired such that
# the sprite appears mirrored compared to the source (e.g. starts from
# the top-right, or is mounted upside down).
WLED_MIRROR_X = os.environ.get("WLED_MIRROR_X", "false").lower() in ("1", "true", "yes")
WLED_MIRROR_Y = os.environ.get("WLED_MIRROR_Y", "false").lower() in ("1", "true", "yes")

# Idle preset (animation style):
#   "default" — look around, glance down, occasional wink
#   "dvd"     — DVD-logo diagonal bounce, surprised on corner hits
IDLE_MODE_PRESET = os.environ.get("IDLE_MODE_PRESET", "default").lower()

# Idle color mode:
#   "default"          — static dim Anthropic orange
#   "rainbow"          — slow rainbow hue cycle (~30 s)
#   "colorchange"      — change color on each wall hit (best with dvd preset)
IDLE_MODE_COLOR = os.environ.get("IDLE_MODE_COLOR", "default").lower()

# Brightness (0–255) for idle vs active states.
IDLE_BRIGHTNESS = int(os.environ.get("IDLE_BRIGHTNESS", "50"))
ACTIVE_BRIGHTNESS = int(os.environ.get("ACTIVE_BRIGHTNESS", "140"))

# Color palette (char -> RRGGBB hex without #)
COLORS = {
    "B": "CD7B5A",  # body (warm orange/coral)
    "S": "CA7356",  # shadow
    "D": "1A1A1A",  # dark (eyes)
    "W": "FFFFFF",  # white
}

# Sprites: each is {"w": cols, "grid": [rows]}
POSES = {
    "normal": {
        "w": 13,
        "grid": [
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..BBBDBBDBB..",
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "BBBBBBBBBBBBB",
            "BBBBBBBBBBBBB",
            "..BBBBBBBBB..",
            "..B.B...B.B..",
            "..B.B...B.B..",
            "..B.B...B.B..",
        ],
    },
    "looking_down": {
        "w": 13,
        "grid": [
            ".............",
            ".............",
            "..BBBBBBBBB..",
            "BSBBBBBBBBBBS",
            "BSBBBBBBBBBBS",
            "BSDBBBBBDBBBS",
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..S.S...S.S..",
            "..B.B...B.B..",
            ".............",
        ],
    },
    "surprised": {
        "w": 13,
        "grid": [
            "..BBBBBBBBB..",
            "..BBWWBBWWB..",
            "..BBDWBBDWB..",
            "..BBWWBBWWB..",
            "..BBBBBBBBB..",
            "BBBBBBBBBBBBB",
            "BBBBBBBBBBBBB",
            "..BBBBBBBBB..",
            "..B.B...B.B..",
            "..B.B...B.B..",
            "..B.B...B.B..",
        ],
    },
    "happy": {
        "w": 13,
        "grid": [
            "...BB...BB...",
            "...BB...BB...",
            "..BBBBBBBBB..",
            "..BBDBBBDBB..",
            "..BDBDBDBDB..",
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..S.S...S.S..",
            "..B.B...B.B..",
            "..B.B...B.B..",
        ],
    },
    "wink": {
        "w": 13,
        "grid": [
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..BBDDBBDDB..",
            "..BBDDBBBBB..",
            "..BBBBBBBBB..",
            "BBBBBBBBBBBBB",
            "BBBBBBBBBBBBB",
            "..BBBBBBBBB..",
            "..B.B...B.B..",
            "..B.B...B.B..",
            "..B.B...B.B..",
        ],
    },
    "dancing": {
        "w": 13,
        "grid": [
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..BBDDBBDDB..",
            "..BBDDBBDDB..",
            "..BBBBBBBBB..",
            "BBBBBBBBBBBBB",
            "BBBBBBBBBBBBB",
            "..BBBBBBBBB..",
            ".B..B...B..B.",
            ".B..B...B..B.",
            "B...B...B...B",
        ],
    },
    "sleeping": {
        "w": 13,
        "grid": [
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..BDDDBDDDB..",
            "..BSSSBSSSB..",
            "BBBBBBBBBBBBB",
            "BBBBBBBBBBBBB",
            "..BBBBBBBBB..",
            "..B.B...B.B..",
            "..B.B...B.B..",
            "..B.B...B.B..",
        ],
    },
    "cool": {
        "w": 13,
        "grid": [
            "..BBBBBBBBB..",
            "..SSSSBSSSS..",
            "..DDDDBDDDD..",
            "..DDDDBDDDD..",
            "..BBBBBBBBB..",
            "BBBBBBBBBBBBB",
            "BBBBBBBBBBBBB",
            "..BBBBBBBBB..",
            "..B.B...B.B..",
            "..B.B...B.B..",
            "..B.B...B.B..",
        ],
    },
    "angry": {
        "w": 13,
        "grid": [
            "..BBBBBBBBB..",
            "..BSSBBBSSB..",
            "..BDDSBSDDB..",
            "..BBDDBDDBB..",
            "..BBBDBDBBB..",
            "BBBBBBBBBBBBB",
            "BBBBBBBBBBBBB",
            "..BBBBBBBBB..",
            "..B.B...B.B..",
            "..B.B...B.B..",
            "..B.B...B.B..",
        ],
    },
    "raising_arm": {
        "w": 13,
        "grid": [
            ".........BB..",
            ".........SS..",
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..BBBDBBDBB..",
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "..BBBBBBBBB..",
            "BBBBBBBBBBB..",
            "BBBBBBBBBBB..",
            "..B..B.B..B..",
            "...B.B..B..B.",
            "...B.B..B...B",
        ],
    },
}


def render_pose(pose_name: str, brightness: int = 128) -> None:
    if pose_name not in POSES:
        raise SystemExit(f"Unknown pose: {pose_name}. Use --list to see options.")

    pose = POSES[pose_name]
    grid = pose["grid"]
    w = pose["w"]
    h = len(grid)

    # Center sprite on 16x16 (slightly biased toward top so legs reach bottom)
    off_x = (WIDTH - w) // 2
    off_y = (HEIGHT - h) // 2

    # Build per-pixel array (logical row-major order; WLED's 2D matrix
    # config handles the physical serpentine mapping internally).
    pixels = ["000000"] * (WIDTH * HEIGHT)
    for ry, row in enumerate(grid):
        for rx, ch in enumerate(row):
            if ch == "." or ch not in COLORS:
                continue
            x = rx + off_x
            y = ry + off_y
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                pixels[y * WIDTH + x] = COLORS[ch]

    # WLED segment 'i' format: [idx, "RRGGBB", idx, "RRGGBB", ...]
    i_array = []
    for idx, color in enumerate(pixels):
        i_array.append(idx)
        i_array.append(color)

    payload = {
        "on": True,
        "bri": brightness,
        "seg": [{"id": 0, "i": i_array}],
    }
    _post(payload)


def turn_off() -> None:
    _post({"on": False})


def _post(payload: dict) -> None:
    req = urllib.request.Request(
        f"{WLED_URL}/json/state",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "normal"
    if arg in ("--list", "-l"):
        print("Available poses:")
        for name in POSES:
            print(f"  {name}")
        return
    if arg == "off":
        turn_off()
        return
    render_pose(arg)


if __name__ == "__main__":
    main()
