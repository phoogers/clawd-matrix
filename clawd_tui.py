#!/usr/bin/env python3
"""Interactive TUI for testing all Clawd states and animations.

Press a key to trigger a state on the matrix. The daemon is auto-spawned
if not already running.

Usage:  python clawd_tui.py
"""

import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from clawd_set import (
    update_state,
    update_subagents,
    end_session,
    daemon_is_alive,
    spawn_daemon,
    SESSIONS_DIR,
)

SESSION_ID = "tui-test"


def ensure_daemon():
    if not daemon_is_alive():
        spawn_daemon()
        time.sleep(0.5)


def set_state(state):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = os.path.join(SESSIONS_DIR, f"{SESSION_ID}.json")
    now = time.time()
    try:
        with open(path) as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    if state == "working" and existing.get("state") == "working":
        started = existing.get("started", now)
    else:
        started = now
    with open(path, "w") as f:
        json.dump({
            "state": state,
            "started": started,
            "subagents": existing.get("subagents", 0),
            "last_seen": now,
        }, f)


def set_working_long():
    """Set working with a started time >30s ago to trigger fast walk."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = os.path.join(SESSIONS_DIR, f"{SESSION_ID}.json")
    now = time.time()
    with open(path, "w") as f:
        json.dump({
            "state": "working",
            "started": now - 35,
            "subagents": 0,
            "last_seen": now,
        }, f)


def add_subagent(delta):
    path = os.path.join(SESSIONS_DIR, f"{SESSION_ID}.json")
    try:
        with open(path) as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {"state": "working", "started": time.time(), "subagents": 0}
    existing["subagents"] = max(0, existing.get("subagents", 0) + delta)
    existing["last_seen"] = time.time()
    with open(path, "w") as f:
        json.dump(existing, f)


def cleanup():
    path = os.path.join(SESSIONS_DIR, f"{SESSION_ID}.json")
    try:
        os.remove(path)
    except OSError:
        pass


def get_key():
    """Read a single keypress (no Enter needed)."""
    if os.name == "nt":
        import msvcrt
        return msvcrt.getch().decode("utf-8", errors="ignore").lower()
    else:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1).lower()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def clear():
    os.system("cls" if os.name == "nt" else "clear")


MENU = """
  ┌─────────────────────────────────────────────┐
  │          Clawd TUI — State Tester           │
  ├─────────────────────────────────────────────┤
  │                                             │
  │   [1]  idle          (look-around / dvd)    │
  │   [2]  working       (walking)              │
  │   [3]  working+30s   (fast walk)            │
  │   [4]  done          (happy dance + green)  │
  │   [5]  permission    (surprised + blue)     │
  │   [6]  error         (angry + red)          │
  │   [7]  compact       (yellow warning)       │
  │   [8]  boot          (pose carousel)        │
  │                                             │
  │   [+]  add subagent     (corner dot)        │
  │   [-]  remove subagent                      │
  │                                             │
  │   [q]  quit (turns off matrix)              │
  │                                             │
  └─────────────────────────────────────────────┘
"""

ACTIONS = {
    "1": ("idle",           lambda: set_state("idle")),
    "2": ("working",        lambda: set_state("working")),
    "3": ("working+30s",    lambda: set_working_long()),
    "4": ("done",           lambda: set_state("done")),
    "5": ("permission",     lambda: set_state("permission")),
    "6": ("error",          lambda: set_state("error")),
    "7": ("compact",        lambda: set_state("compact")),
    "8": ("boot",           lambda: set_state("boot")),
    "+": ("subagent +1",    lambda: add_subagent(+1)),
    "=": ("subagent +1",    lambda: add_subagent(+1)),  # unshifted + key
    "-": ("subagent -1",    lambda: add_subagent(-1)),
}


def main():
    ensure_daemon()
    clear()
    print(MENU)
    print("  Daemon running. Press a key...\n")

    try:
        while True:
            key = get_key()
            if key == "q":
                break
            if key in ACTIONS:
                label, action = ACTIONS[key]
                action()
                # Move cursor up and overwrite status line
                print(f"\r  → {label:<40}", end="", flush=True)
            else:
                print(f"\r  (unknown key: {key!r}){' ' * 20}", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()
        print("\n\n  Cleaned up test session. Matrix will turn off if no other sessions.\n")


if __name__ == "__main__":
    main()
