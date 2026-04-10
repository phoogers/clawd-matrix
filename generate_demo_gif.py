#!/usr/bin/env python3
"""Generate a demo GIF showing every Clawd state side-by-side.

Renders a 3x3 grid of simultaneously animating states, using the same
renderers the daemon uses so the GIF matches real-world behavior exactly.

Requires: Pillow

Output: docs/animations.gif
"""

import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Monkey-patch time.time so all wall-clock-based renderers advance
# at the GIF's display rate instead of real time.
_sim_time = [time.time()]
_orig_time = time.time
time.time = lambda: _sim_time[0]

# Pull renderers + constants from the daemon so the GIF stays in sync.
from clawd_daemon import (  # noqa: E402
    HEIGHT,
    WIDTH,
    ORANGE,
    YELLOW,
    OFF,
    GREEN_BG,
    RED_BG,
    BLUE_BG,
    YELLOW_BG,
    render_walk,
    render_look_around,
    render_sleeping,
    render_surprised_pulse,
    render_happy_dance,
    render_angry_pulse,
    render_compact_pulse,
    render_boot,
    overlay_subagent_indicator,
)

# ─── Layout constants ─────────────────────────────────────────────────────
PIXEL = 10           # how many screen pixels per LED pixel
GAP = 18             # spacing between cells
LABEL_H = 28         # label strip height below each cell
SUBLABEL_H = 18      # smaller description strip
MARGIN = 22
BG_COLOR = (18, 18, 22)
LABEL_BG = (30, 30, 36)
LABEL_FG = (230, 230, 235)
SUBLABEL_FG = (150, 150, 160)

CELL_W = WIDTH * PIXEL
CELL_H = HEIGHT * PIXEL

FPS = 8
DURATION_FRAMES = 48  # 6-second loop at 8 fps

# NOTE: real daemon uses bri=50 for idle/sleeping and bri=140 for everything
# else. In the GIF we bump dim states up so viewers can actually see them.
DIM_DISPLAY_BRI = 130
NORMAL_DISPLAY_BRI = 200


# ─── Cell definitions ─────────────────────────────────────────────────────
def cell_idle(frame):
    return render_look_around(frame, ORANGE), DIM_DISPLAY_BRI

def cell_sleeping(frame):
    return render_sleeping(frame, ORANGE), DIM_DISPLAY_BRI

def cell_boot(frame):
    return render_boot(frame, ORANGE), NORMAL_DISPLAY_BRI

def cell_working(frame):
    return render_walk(frame, ORANGE, fast=False), NORMAL_DISPLAY_BRI

def cell_done(frame):
    return render_happy_dance(frame, ORANGE, bg=GREEN_BG), NORMAL_DISPLAY_BRI

def cell_permission(frame):
    return render_surprised_pulse(frame, ORANGE, bg=BLUE_BG), NORMAL_DISPLAY_BRI

def cell_error(frame):
    return render_angry_pulse(frame, ORANGE, bg=RED_BG), NORMAL_DISPLAY_BRI

def cell_compact(frame):
    return render_compact_pulse(frame, YELLOW, bg=YELLOW_BG), NORMAL_DISPLAY_BRI

def cell_subagent(frame):
    # Working + subagent indicator overlay (count=2 to show the multi-dot variant)
    pixels = render_walk(frame, ORANGE, fast=False)
    overlay_subagent_indicator(pixels, frame, count=2)
    return pixels, NORMAL_DISPLAY_BRI


CELLS = [
    ("idle",       "look around • dim",           cell_idle),
    ("sleeping",   "night mode • dim + Z",        cell_sleeping),
    ("boot",       "pose carousel",               cell_boot),

    ("working",    "pacing left ↔ right",         cell_working),
    ("done",       "happy dance • green",         cell_done),
    ("permission", "surprised • blue",            cell_permission),

    ("error",      "angry • red",                 cell_error),
    ("compact",    "yellow warning",              cell_compact),
    ("subagent",   "working + corner dot",        cell_subagent),
]

COLS = 3
ROWS = (len(CELLS) + COLS - 1) // COLS  # 3

CELL_TOTAL_H = CELL_H + LABEL_H + SUBLABEL_H
IMG_W = MARGIN * 2 + COLS * CELL_W + (COLS - 1) * GAP
IMG_H = MARGIN * 2 + ROWS * CELL_TOTAL_H + (ROWS - 1) * GAP + 40  # room for title


# ─── Helpers ──────────────────────────────────────────────────────────────
def hex_to_rgb(h):
    if not h or h == OFF:
        return (0, 0, 0)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def apply_brightness(rgb, bri):
    f = bri / 255.0
    return (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f))


def draw_sprite_on(img, pixels, origin, brightness):
    """Draw a 16x16 logical pixel buffer into the image. This is the
    canonical orientation (matches the source sprite sheet). Physical
    matrix flips are applied separately by the daemon via WLED_MIRROR_*."""
    draw = ImageDraw.Draw(img)
    ox, oy = origin
    for y in range(HEIGHT):
        for x in range(WIDTH):
            rgb = apply_brightness(hex_to_rgb(pixels[y * WIDTH + x]), brightness)
            draw.rectangle(
                [ox + x * PIXEL, oy + y * PIXEL,
                 ox + (x + 1) * PIXEL - 1, oy + (y + 1) * PIXEL - 1],
                fill=rgb,
            )


def get_font(size):
    candidates = [
        "C:/Windows/Fonts/seguisb.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def cell_origin(idx):
    col = idx % COLS
    row = idx // COLS
    x = MARGIN + col * (CELL_W + GAP)
    y = MARGIN + 40 + row * (CELL_TOTAL_H + GAP)  # +40 for title
    return x, y


# ─── Frame builder ────────────────────────────────────────────────────────
def build_frame(frame_idx, sim_start):
    _sim_time[0] = sim_start + frame_idx * (1.0 / FPS)
    img = Image.new("RGB", (IMG_W, IMG_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    title_font = get_font(18)
    label_font = get_font(14)
    sub_font = get_font(11)

    # Title bar
    title = "Clawd states — Claude Code mascot animations"
    draw.text((MARGIN, MARGIN), title, fill=LABEL_FG, font=title_font)

    for idx, (name, desc, func) in enumerate(CELLS):
        ox, oy = cell_origin(idx)

        # Matrix background
        draw.rectangle(
            [ox - 2, oy - 2, ox + CELL_W + 1, oy + CELL_H + 1],
            outline=(60, 60, 68), width=1,
        )
        draw.rectangle(
            [ox, oy, ox + CELL_W - 1, oy + CELL_H - 1],
            fill=(0, 0, 0),
        )

        pixels, bri = func(frame_idx)
        draw_sprite_on(img, pixels, (ox, oy), bri)

        # Label strip
        label_y = oy + CELL_H + 2
        draw.rectangle(
            [ox, label_y, ox + CELL_W - 1, label_y + LABEL_H - 1],
            fill=LABEL_BG,
        )
        # Center the label
        bbox = draw.textbbox((0, 0), name, font=label_font)
        tw = bbox[2] - bbox[0]
        draw.text(
            (ox + (CELL_W - tw) // 2, label_y + 6),
            name, fill=LABEL_FG, font=label_font,
        )

        # Sub-label
        sub_y = label_y + LABEL_H
        bbox = draw.textbbox((0, 0), desc, font=sub_font)
        tw = bbox[2] - bbox[0]
        draw.text(
            (ox + (CELL_W - tw) // 2, sub_y + 2),
            desc, fill=SUBLABEL_FG, font=sub_font,
        )

    return img


def main():
    out_dir = os.path.join(SCRIPT_DIR, "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "animations.gif")

    sim_start = _orig_time()
    frames = [build_frame(f, sim_start) for f in range(DURATION_FRAMES)]

    time.time = _orig_time  # restore real time

    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        optimize=True,
        duration=int(1000 / FPS),
        loop=0,
        disposal=2,
    )
    print(f"Wrote {out_path}  ({len(frames)} frames, {IMG_W}×{IMG_H})")


if __name__ == "__main__":
    main()
