#!/usr/bin/env python3
"""Generate a demo GIF showing every idle mode combination.

Renders a 3x2 grid of simultaneously animating idle modes.
Uses time-simulation so DVD bounce and rainbow cycle progress
deterministically across frames.

Requires: Pillow

Output: docs/idle_modes.gif
"""

import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Monkey-patch time.time so the DVD bounce and rainbow renderers
# advance deterministically in the GIF.
_sim_time = [time.time()]
_orig_time = time.time
time.time = lambda: _sim_time[0]

from clawd_daemon import (  # noqa: E402
    HEIGHT,
    WIDTH,
    ORANGE,
    OFF,
    render_look_around,
    render_dvd_bounce,
    render_sleeping,
    rainbow_palette,
    bounce_palette,
    _dvd_position,
)

# ─── Layout constants ─────────────────────────────────────────────────────
PIXEL = 10
GAP = 18
LABEL_H = 28
SUBLABEL_H = 18
MARGIN = 22
BG_COLOR = (18, 18, 22)
LABEL_BG = (30, 30, 36)
LABEL_FG = (230, 230, 235)
SUBLABEL_FG = (150, 150, 160)

CELL_W = WIDTH * PIXEL
CELL_H = HEIGHT * PIXEL

FPS = 8
DURATION_SECS = 8.0
DURATION_FRAMES = int(DURATION_SECS * FPS)

DISPLAY_BRI_DIM = 130
DISPLAY_BRI_NORMAL = 200


# ─── Cell definitions ─────────────────────────────────────────────────────
def cell_default_default(frame):
    return render_look_around(frame, ORANGE), DISPLAY_BRI_DIM

def cell_default_rainbow(frame):
    return render_look_around(frame, rainbow_palette()), DISPLAY_BRI_DIM

def cell_dvd_default(frame):
    return render_dvd_bounce(frame, ORANGE), DISPLAY_BRI_NORMAL

def cell_dvd_rainbow(frame):
    return render_dvd_bounce(frame, rainbow_palette()), DISPLAY_BRI_NORMAL

def cell_dvd_colorchange(frame):
    _, _, bounces, _ = _dvd_position()
    return render_dvd_bounce(frame, bounce_palette(bounces)), DISPLAY_BRI_NORMAL

def cell_sleeping(frame):
    return render_sleeping(frame, ORANGE), DISPLAY_BRI_DIM


CELLS = [
    ("default + default",    "orange • look around",       cell_default_default),
    ("default + rainbow",    "rainbow • look around",      cell_default_rainbow),
    ("dvd + default",        "orange • bouncing",          cell_dvd_default),

    ("dvd + rainbow",        "rainbow • bouncing",         cell_dvd_rainbow),
    ("dvd + colorchange",    "color shift on hit",         cell_dvd_colorchange),
    ("sleeping",             "night mode (23:00–07:00)",   cell_sleeping),
]

COLS = 3
ROWS = 2

CELL_TOTAL_H = CELL_H + LABEL_H + SUBLABEL_H
IMG_W = MARGIN * 2 + COLS * CELL_W + (COLS - 1) * GAP
IMG_H = MARGIN * 2 + ROWS * CELL_TOTAL_H + (ROWS - 1) * GAP + 40


# ─── Helpers ──────────────────────────────────────────────────────────────
def hex_to_rgb(h):
    if not h or h == "000000":
        return (0, 0, 0)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def apply_brightness(rgb, bri):
    f = bri / 255.0
    return (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f))


def draw_sprite_on(img, pixels, origin, brightness):
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
    y = MARGIN + 40 + row * (CELL_TOTAL_H + GAP)
    return x, y


def build_frame(frame_idx, sim_start):
    # Advance simulated time for this frame
    _sim_time[0] = sim_start + frame_idx * (1.0 / FPS)

    img = Image.new("RGB", (IMG_W, IMG_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    title_font = get_font(18)
    label_font = get_font(12)
    sub_font = get_font(11)

    title = "Idle modes — IDLE_MODE_PRESET + IDLE_MODE_COLOR"
    draw.text((MARGIN, MARGIN), title, fill=LABEL_FG, font=title_font)

    for idx, (name, desc, func) in enumerate(CELLS):
        ox, oy = cell_origin(idx)

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

        label_y = oy + CELL_H + 2
        draw.rectangle(
            [ox, label_y, ox + CELL_W - 1, label_y + LABEL_H - 1],
            fill=LABEL_BG,
        )
        bbox = draw.textbbox((0, 0), name, font=label_font)
        tw = bbox[2] - bbox[0]
        draw.text(
            (ox + (CELL_W - tw) // 2, label_y + 7),
            name, fill=LABEL_FG, font=label_font,
        )

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
    out_path = os.path.join(out_dir, "idle_modes.gif")

    sim_start = _orig_time()
    frames = [build_frame(f, sim_start) for f in range(DURATION_FRAMES)]

    # Restore real time
    time.time = _orig_time

    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        optimize=True,
        duration=int(1000 / FPS),
        loop=0,
        disposal=2,
    )
    print(f"Wrote {out_path}  ({len(frames)} frames, {IMG_W}x{IMG_H})")


if __name__ == "__main__":
    main()
