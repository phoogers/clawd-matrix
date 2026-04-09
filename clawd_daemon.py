#!/usr/bin/env python3
"""Clawd animation daemon for a 16x16 WLED matrix.

Aggregates state across all active Claude Code sessions, renders
animations at ~8fps, and auto-shuts down after a long idle period.

Run manually:  python clawd_daemon.py
Auto-started by clawd_set.py if no fresh heartbeat is detected.
"""

import datetime
import json
import os
import sys
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from clawd import POSES, WLED_URL, WIDTH, HEIGHT, WLED_MIRROR_X, WLED_MIRROR_Y  # noqa: E402

SESSIONS_DIR = os.path.join(SCRIPT_DIR, ".clawd_sessions")
PID_PATH = os.path.join(SCRIPT_DIR, ".clawd_daemon.json")

FPS = 8
FRAME_INTERVAL = 1.0 / FPS

# How long a session file can go without updates before we consider it stale.
SESSION_STALE_AFTER = 5 * 60      # 5 minutes
# How long the matrix can sit in pure idle before the daemon shuts itself down.
AUTO_SHUTDOWN_IDLE_AFTER = 10 * 60  # 10 minutes
# Working state duration that triggers the "really cooking" fast walk.
LONG_TASK_THRESHOLD = 30.0
# Sleeping mode hours (local time).
SLEEP_HOURS = set(list(range(23, 24)) + list(range(0, 7)))  # 23:00–06:59

# Color schemes
ORANGE = {"B": "CD7B5A", "S": "CA7356", "D": "1A1A1A", "W": "FFFFFF"}
YELLOW = {"B": "FFC83D", "S": "D49B14", "D": "1A1A1A", "W": "FFFFFF"}

OFF = "000000"
GREEN_BG = "00FF00"
RED_BG = "FF0000"
BLUE_BG = "0066FF"
YELLOW_BG = "FFD700"

DEFAULT_BRI = 140
IDLE_BRI = 50

# Transient state priority — higher wins when multiple sessions disagree.
TRANSIENT_PRIORITY = {
    "permission": 5,
    "error": 4,
    "compact": 3,
    "done": 2,
    "boot": 1,
}
TRANSIENT_DURATION = {
    "permission": 3.0,
    "error": 3.0,
    "compact": 2.5,
    "done": 3.0,
    "boot": 3.0,
}


# ─── Sprite drawing ───────────────────────────────────────────────────────
def draw_sprite(pose_name, colors, y_offset=None, x_offset=None, bg=OFF, flip_x=False):
    pose = POSES[pose_name]
    grid = pose["grid"]
    if flip_x:
        grid = [row[::-1] for row in grid]
    w = pose["w"]
    h = len(grid)
    off_x = (WIDTH - w) // 2 if x_offset is None else x_offset
    off_y = (HEIGHT - h) // 2 if y_offset is None else y_offset

    pixels = [bg] * (WIDTH * HEIGHT)
    for ry, row in enumerate(grid):
        for rx, ch in enumerate(row):
            if ch == "." or ch not in colors:
                continue
            x = rx + off_x
            y = ry + off_y
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                pixels[y * WIDTH + x] = colors[ch]
    return pixels


def set_pixel(pixels, x, y, color):
    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        pixels[y * WIDTH + x] = color


def overlay_subagent_indicator(pixels, frame, count):
    """Spinning corner pixel — top-right of the matrix."""
    if count <= 0:
        return
    # 4-pixel L spinner that rotates every 2 frames
    spin = (frame // 2) % 4
    cx, cy = WIDTH - 1, 0  # top-right anchor
    positions = [
        (cx, cy),
        (cx - 1, cy),
        (cx - 1, cy + 1),
        (cx, cy + 1),
    ]
    px, py = positions[spin]
    set_pixel(pixels, px, py, "00CCFF")  # cyan dot
    # Show count as small dim dots if more than 1
    if count > 1:
        for i in range(min(count - 1, 3)):
            set_pixel(pixels, cx - i, cy + 2, "004466")


# ─── Renderers ────────────────────────────────────────────────────────────
def render_walk(frame, colors, bg=OFF, fast=False):
    """Pace left↔right across the matrix."""
    X_MIN, X_MAX = -3, 6
    span = X_MAX - X_MIN + 1            # 10 positions per direction
    cycle = span * 2                    # 20 positions for a round trip

    if fast:
        pos = frame % cycle             # one step per frame
        pose_step = frame
    else:
        pos = (frame // 2) % cycle      # one step per 2 frames (half speed)
        pose_step = frame // 2

    if pos < span:
        x = X_MIN + pos
        flip = False
    else:
        x = X_MAX - (pos - span)
        flip = True

    pose = "normal" if pose_step % 2 == 0 else "raising_arm"
    base_y = (HEIGHT - len(POSES[pose]["grid"])) // 2
    y = base_y + (-1 if pose == "raising_arm" else 0)
    return draw_sprite(pose, colors, x_offset=x, y_offset=y, bg=bg, flip_x=flip)


def render_look_around(frame, colors, bg=OFF):
    """Slow ~12s cycle: forward → down → forward → wink → forward."""
    cycle = frame % (12 * FPS)
    if cycle < 4 * FPS:
        pose = "normal"
    elif cycle < 6 * FPS:
        pose = "looking_down"
    elif cycle < 10 * FPS:
        pose = "normal"
    elif cycle < 10 * FPS + 3:
        pose = "wink"
    else:
        pose = "normal"
    return draw_sprite(pose, colors, bg=bg)


def render_sleeping(frame, colors, bg=OFF):
    """Sleeping pose with a drifting Z above the head."""
    pixels = draw_sprite("sleeping", colors, bg=bg)
    # Z drifts up-and-right over ~2 seconds, then resets
    z_cycle = (2 * FPS)
    z_step = frame % z_cycle
    if z_step < 5:
        # Position the Z relative to the centered sprite (~ x=10, y=2 starting)
        zx = 10 + z_step
        zy = 3 - z_step
        set_pixel(pixels, zx, zy, "FFFFFF")
    return pixels


def render_surprised_pulse(frame, colors, bg):
    bg_now = bg if (frame // 2) % 2 == 0 else OFF
    return draw_sprite("surprised", colors, bg=bg_now)


def render_happy_dance(frame, colors, bg):
    bg_now = bg if (frame // 2) % 2 == 0 else OFF
    pose = "happy" if (frame // 4) % 2 == 0 else "dancing"
    return draw_sprite(pose, colors, bg=bg_now)


def render_angry_pulse(frame, colors, bg):
    bg_now = bg if (frame // 2) % 2 == 0 else OFF
    return draw_sprite("angry", colors, bg=bg_now)


def render_compact_pulse(frame, colors, bg):
    bg_now = bg if (frame // 2) % 2 == 0 else OFF
    return draw_sprite("surprised", colors, bg=bg_now)


CAROUSEL_POSES = ["normal", "happy", "wink", "looking_down",
                  "dancing", "raising_arm", "cool", "surprised"]


def render_boot(frame, colors, bg=OFF):
    """Pose carousel: cycle through every pose for the boot animation."""
    idx = (frame // 2) % len(CAROUSEL_POSES)  # ~250ms per pose at 8fps
    pose = CAROUSEL_POSES[idx]
    # raising_arm is taller — anchor it differently to avoid clipping
    y_offset = None
    if pose == "raising_arm":
        y_offset = HEIGHT - len(POSES[pose]["grid"])
    return draw_sprite(pose, colors, y_offset=y_offset, bg=bg)


# ─── State aggregation ────────────────────────────────────────────────────
def read_all_sessions():
    if not os.path.isdir(SESSIONS_DIR):
        return []
    now = time.time()
    sessions = []
    for name in os.listdir(SESSIONS_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(SESSIONS_DIR, name)
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        last_seen = data.get("last_seen", 0)
        if now - last_seen > SESSION_STALE_AFTER:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        sessions.append(data)
    return sessions


def aggregate(sessions):
    """Return (effective_state, started_time, total_subagents)."""
    if not sessions:
        return "idle", time.time(), 0

    total_subagents = sum(s.get("subagents", 0) for s in sessions)
    now = time.time()

    # Drop expired transient states.
    live = []
    for s in sessions:
        st = s.get("state")
        started = s.get("started", now)
        if st in TRANSIENT_DURATION and (now - started) >= TRANSIENT_DURATION[st]:
            continue  # transient has expired for this session
        live.append(s)

    transients = [s for s in live if s.get("state") in TRANSIENT_PRIORITY]
    if transients:
        # Highest-priority transient wins; ties broken by most recent.
        winner = max(transients, key=lambda s: (TRANSIENT_PRIORITY[s["state"]], s.get("started", 0)))
        return winner["state"], winner.get("started", now), total_subagents

    working = [s for s in live if s.get("state") == "working"]
    if working:
        # Earliest-started working session for long-task detection.
        winner = min(working, key=lambda s: s.get("started", now))
        return "working", winner.get("started", now), total_subagents

    return "idle", now, total_subagents


def is_sleeping_now():
    return datetime.datetime.now().hour in SLEEP_HOURS


# ─── WLED I/O ─────────────────────────────────────────────────────────────
def _orient(pixels):
    """Apply physical-orientation corrections (mirror X / mirror Y) so the
    logical buffer the renderers produce matches the viewer's perspective
    of the physical matrix."""
    if not (WLED_MIRROR_X or WLED_MIRROR_Y):
        return pixels
    out = [OFF] * (WIDTH * HEIGHT)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            sx = WIDTH - 1 - x if WLED_MIRROR_X else x
            sy = HEIGHT - 1 - y if WLED_MIRROR_Y else y
            out[y * WIDTH + x] = pixels[sy * WIDTH + sx]
    return out


def push_pixels(pixels, brightness=DEFAULT_BRI):
    pixels = _orient(pixels)
    i_array = []
    for idx, color in enumerate(pixels):
        i_array.append(idx)
        i_array.append(color)
    payload = {
        "on": True,
        "bri": brightness,
        "seg": [{"id": 0, "i": i_array}],
    }
    try:
        req = urllib.request.Request(
            f"{WLED_URL}/json/state",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            resp.read()
    except Exception:
        pass


def push_off():
    try:
        req = urllib.request.Request(
            f"{WLED_URL}/json/state",
            data=json.dumps({"on": False}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            resp.read()
    except Exception:
        pass


def write_heartbeat():
    with open(PID_PATH, "w") as f:
        json.dump({"pid": os.getpid(), "heartbeat": time.time()}, f)


def is_daemon_alive(max_age=3.0):
    try:
        with open(PID_PATH) as f:
            data = json.load(f)
        return (time.time() - data["heartbeat"]) < max_age
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return False


# ─── Main loop ────────────────────────────────────────────────────────────
def main():
    if is_daemon_alive():
        print("Clawd daemon already running.", file=sys.stderr)
        sys.exit(0)

    os.makedirs(SESSIONS_DIR, exist_ok=True)

    frame = 0
    last_state = None
    last_started = None
    idle_since = None

    try:
        while True:
            tick_start = time.time()
            write_heartbeat()

            sessions = read_all_sessions()
            state, started, subagents = aggregate(sessions)

            # Reset frame counter on state transitions.
            if state != last_state or started != last_started:
                frame = 0
                last_state = state
                last_started = started

            # Auto-shutdown logic — only counts pure-idle time.
            if state == "idle":
                if idle_since is None:
                    idle_since = tick_start
                elif (tick_start - idle_since) >= AUTO_SHUTDOWN_IDLE_AFTER:
                    push_off()
                    break
            else:
                idle_since = None

            # Pick renderer + brightness for this state.
            bri = DEFAULT_BRI
            if state == "idle":
                if is_sleeping_now():
                    pixels = render_sleeping(frame, ORANGE)
                else:
                    pixels = render_look_around(frame, ORANGE)
                bri = IDLE_BRI
            elif state == "working":
                fast = (tick_start - started) >= LONG_TASK_THRESHOLD
                pixels = render_walk(frame, ORANGE, fast=fast)
            elif state == "done":
                pixels = render_happy_dance(frame, ORANGE, bg=GREEN_BG)
            elif state == "permission":
                pixels = render_surprised_pulse(frame, ORANGE, bg=BLUE_BG)
            elif state == "error":
                pixels = render_angry_pulse(frame, ORANGE, bg=RED_BG)
            elif state == "compact":
                pixels = render_compact_pulse(frame, YELLOW, bg=YELLOW_BG)
            elif state == "boot":
                pixels = render_boot(frame, ORANGE)
            else:
                pixels = render_look_around(frame, ORANGE)
                bri = IDLE_BRI

            overlay_subagent_indicator(pixels, frame, subagents)
            push_pixels(pixels, brightness=bri)

            frame += 1
            elapsed = time.time() - tick_start
            time.sleep(max(0, FRAME_INTERVAL - elapsed))
    except KeyboardInterrupt:
        push_off()
    finally:
        try:
            os.remove(PID_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    main()
