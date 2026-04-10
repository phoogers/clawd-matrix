#!/usr/bin/env python3
"""Clawd animation daemon for a 16x16 WLED matrix.

Aggregates state across all active Claude Code sessions, renders
animations at ~8fps, and auto-shuts down after a long idle period.

Run manually:  python clawd_daemon.py
Auto-started by clawd_set.py if no fresh heartbeat is detected.
"""

import colorsys
import datetime
import json
import os
import sys
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from clawd import (  # noqa: E402
    POSES, WLED_URL, WIDTH, HEIGHT, WLED_MIRROR_X, WLED_MIRROR_Y,
    IDLE_MODE_PRESET, IDLE_MODE_COLOR, IDLE_BRIGHTNESS, ACTIVE_BRIGHTNESS,
)

SESSIONS_DIR = os.path.join(SCRIPT_DIR, ".clawd_sessions")
PID_PATH = os.path.join(SCRIPT_DIR, ".clawd_daemon.json")

FPS = 20                    # render rate (smoothness); does NOT affect animation speed
FRAME_INTERVAL = 1.0 / FPS

# ─── Session / lifecycle ──────────────────────────────────────────────────
SESSION_STALE_AFTER = 5 * 60      # prune session files older than this (seconds)
AUTO_SHUTDOWN_IDLE_AFTER = 10 * 60  # idle this long → daemon exits (seconds)
LONG_TASK_THRESHOLD = 30.0          # working > this → fast walk (seconds)
SLEEP_HOURS = set(list(range(23, 24)) + list(range(0, 7)))  # 23:00–06:59

# ─── Animation timing (all wall-clock, FPS-independent) ───────────────────
WALK_STEPS_PER_SEC = 4.0          # normal walk pace
WALK_FAST_STEPS_PER_SEC = 8.0     # long-task walk pace
FLASH_PERIOD = 0.4                # bg flash on/off cycle (seconds)
POSE_SWITCH_PERIOD = 1.0          # happy↔dancing alternation (seconds)
BOOT_POSE_PERIOD = 0.5            # boot carousel, time per pose (seconds)
LOOK_AROUND_CYCLE = 12.0          # full look-around cycle (seconds)
Z_DRIFT_CYCLE = 2.0               # sleeping Z drift cycle (seconds)
DVD_STEPS_PER_SEC = 2.0           # DVD bounce movement speed
RAINBOW_CYCLE_SECS = 30.0         # full hue wheel (seconds)

# ─── Colors / brightness ─────────────────────────────────────────────────
ORANGE = {"B": "CD7B5A", "S": "CA7356", "D": "1A1A1A", "W": "FFFFFF"}
YELLOW = {"B": "FFC83D", "S": "D49B14", "D": "1A1A1A", "W": "FFFFFF"}

OFF = "000000"
GREEN_BG = "00FF00"
RED_BG = "FF0000"
BLUE_BG = "0066FF"
YELLOW_BG = "FFD700"

DEFAULT_BRI = ACTIVE_BRIGHTNESS
IDLE_BRI = IDLE_BRIGHTNESS


def _rgb_hex(r, g, b):
    return f"{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


def rainbow_palette():
    """Generate a Clawd color palette that cycles through the rainbow.
    Uses wall-clock time so the cycle isn't reset by state transitions."""
    hue = (time.time() % RAINBOW_CYCLE_SECS) / RAINBOW_CYCLE_SECS
    r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 1.0)
    sr, sg, sb = colorsys.hsv_to_rgb(hue, 0.85, 0.7)  # shadow = darker
    return {"B": _rgb_hex(r, g, b), "S": _rgb_hex(sr, sg, sb), "D": "1A1A1A", "W": "FFFFFF"}

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
    "boot": 4.0,
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
            set_pixel(pixels, cx - i, cy + 2, "00DD00")


# ─── Renderers ────────────────────────────────────────────────────────────
def render_walk(frame, colors, bg=OFF, fast=False):
    """Pace left↔right across the matrix (wall-clock timed)."""
    X_MIN, X_MAX = -3, 6
    span = X_MAX - X_MIN + 1            # 10 positions per direction
    cycle = span * 2                    # 20 positions for a round trip

    sps = WALK_FAST_STEPS_PER_SEC if fast else WALK_STEPS_PER_SEC
    step = int(time.time() * sps)

    pos = step % cycle
    if pos < span:
        x = X_MIN + pos
        flip = False
    else:
        x = X_MAX - (pos - span)
        flip = True

    pose = "normal" if step % 2 == 0 else "raising_arm"
    return draw_sprite(pose, colors, x_offset=x, bg=bg, flip_x=flip)


def render_look_around(frame, colors, bg=OFF):
    """Slow look-around cycle (wall-clock timed)."""
    t = time.time() % LOOK_AROUND_CYCLE
    if t < 4.0:
        pose = "normal"
    elif t < 6.0:
        pose = "looking_down"
    elif t < 10.0:
        pose = "normal"
    elif t < 10.4:
        pose = "wink"
    else:
        pose = "normal"
    return draw_sprite(pose, colors, bg=bg)


def _dvd_position():
    """Compute the DVD-bounce position and bounce count from wall-clock time.
    Returns (x_pos, y_pos, bounces, at_corner)."""
    sprite_w = POSES["normal"]["w"]          # 13
    sprite_h = len(POSES["normal"]["grid"])  # 11
    x_max = max(WIDTH - sprite_w, 1)         # 3
    y_max = max(HEIGHT - sprite_h, 1)        # 5

    step = int(time.time() * DVD_STEPS_PER_SEC)

    x_pos = step % (2 * x_max)
    if x_pos > x_max:
        x_pos = 2 * x_max - x_pos
    y_pos = step % (2 * y_max)
    if y_pos > y_max:
        y_pos = 2 * y_max - y_pos

    bounces = (step // x_max) + (step // y_max)
    at_corner = (x_pos in (0, x_max)) and (y_pos in (0, y_max))
    return x_pos, y_pos, bounces, at_corner


def bounce_palette(bounces):
    """Color palette that shifts hue on each wall bounce (golden ratio)."""
    hue = (bounces * 0.618033988749895) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 1.0)
    sr, sg, sb = colorsys.hsv_to_rgb(hue, 0.85, 0.7)
    return {"B": _rgb_hex(r, g, b), "S": _rgb_hex(sr, sg, sb),
            "D": "1A1A1A", "W": "FFFFFF"}


def render_dvd_bounce(frame, colors, bg=OFF):
    """DVD-logo-style diagonal bounce. Shows a surprised expression on
    the rare corner-hit moment. Color is controlled separately via the
    colors parameter."""
    x_pos, y_pos, _, at_corner = _dvd_position()
    pose = "surprised" if at_corner else "normal"
    return draw_sprite(pose, colors, x_offset=x_pos, y_offset=y_pos, bg=bg)


def render_sleeping(frame, colors, bg=OFF):
    """Sleeping pose with a drifting Z above the head (wall-clock timed)."""
    pixels = draw_sprite("sleeping", colors, bg=bg)
    t = time.time() % Z_DRIFT_CYCLE
    z_step = int(t / Z_DRIFT_CYCLE * 8)  # 8 sub-steps per cycle
    if z_step < 5:
        zx = 10 + z_step
        zy = 3 - z_step
        set_pixel(pixels, zx, zy, "FFFFFF")
    return pixels


def _flash_on():
    """Returns True during the 'on' half of the flash cycle."""
    return int(time.time() / (FLASH_PERIOD / 2)) % 2 == 0


def render_surprised_pulse(frame, colors, bg):
    bg_now = bg if _flash_on() else OFF
    return draw_sprite("surprised", colors, bg=bg_now)


def render_happy_dance(frame, colors, bg):
    bg_now = bg if _flash_on() else OFF
    pose = "happy" if int(time.time() / POSE_SWITCH_PERIOD) % 2 == 0 else "dancing"
    return draw_sprite(pose, colors, bg=bg_now)


def render_angry_pulse(frame, colors, bg):
    bg_now = bg if _flash_on() else OFF
    return draw_sprite("angry", colors, bg=bg_now)


def render_compact_pulse(frame, colors, bg):
    bg_now = bg if _flash_on() else OFF
    return draw_sprite("surprised", colors, bg=bg_now)


CAROUSEL_POSES = ["normal", "happy", "wink", "looking_down",
                  "dancing", "raising_arm", "cool", "surprised"]



def render_boot(frame, colors, bg=OFF):
    """Pose carousel (wall-clock timed)."""
    idx = int(time.time() / BOOT_POSE_PERIOD) % len(CAROUSEL_POSES)
    pose = CAROUSEL_POSES[idx]
    return draw_sprite(pose, colors, bg=bg)


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
    """Return (effective_state, started_time, total_subagents).

    Returns "off" when there are no active Claude Code sessions — the
    main loop treats this as a signal to push the matrix off and exit.
    """
    if not sessions:
        return "off", time.time(), 0

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


# Delta-update state. Tracking what we last sent lets us push only the
# pixels that actually changed each frame, dramatically shrinking the
# JSON payload over Wi-Fi.
_last_pushed = None        # last full physical buffer we successfully sent
_last_brightness = None    # last brightness we successfully sent
_force_full_next = True    # force a full push (after errors / startup)


def push_pixels(pixels, brightness=DEFAULT_BRI):
    global _last_pushed, _last_brightness, _force_full_next
    pixels = _orient(pixels)

    full_push = _force_full_next or _last_pushed is None

    i_array = []
    for idx, color in enumerate(pixels):
        if full_push or _last_pushed[idx] != color:
            i_array.append(idx)
            i_array.append(color)

    bri_changed = brightness != _last_brightness
    if not i_array and not bri_changed:
        return  # nothing to update — skip the network call entirely

    payload = {"on": True, "bri": brightness}
    if i_array:
        payload["seg"] = [{"id": 0, "i": i_array}]

    try:
        req = urllib.request.Request(
            f"{WLED_URL}/json/state",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            resp.read()
        # Success — commit the new state to the cache.
        _last_pushed = list(pixels)
        _last_brightness = brightness
        _force_full_next = False
    except Exception:
        # Force a full re-push on the next successful tick to recover
        # from any state divergence between us and WLED.
        _force_full_next = True


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
    startup_time = time.time()
    STARTUP_GRACE = 1.0  # avoid races where the spawning hook hasn't synced yet

    try:
        while True:
            tick_start = time.time()
            write_heartbeat()

            sessions = read_all_sessions()
            state, started, subagents = aggregate(sessions)

            # No active Claude Code sessions → turn off and exit immediately
            # (after a brief startup grace period to avoid spawning races).
            if state == "off":
                if (tick_start - startup_time) >= STARTUP_GRACE:
                    push_off()
                    break
                # During the grace window, just sleep without rendering.
                time.sleep(max(0, FRAME_INTERVAL - (time.time() - tick_start)))
                continue

            # Reset frame counter on state transitions.
            if state != last_state or started != last_started:
                frame = 0
                last_state = state
                last_started = started

            # Auto-shutdown logic — only counts pure-idle time with sessions present.
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
                    # Pick color scheme
                    if IDLE_MODE_COLOR == "rainbow":
                        idle_colors = rainbow_palette()
                    elif IDLE_MODE_COLOR == "colorchange":
                        _, _, bounces, _ = _dvd_position()
                        idle_colors = bounce_palette(bounces)
                    else:
                        idle_colors = ORANGE
                    # Pick animation
                    if IDLE_MODE_PRESET == "dvd":
                        pixels = render_dvd_bounce(frame, idle_colors)
                    else:
                        pixels = render_look_around(frame, idle_colors)
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
