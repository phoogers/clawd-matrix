#!/usr/bin/env python3
"""Set the Clawd state from a Claude Code hook.

Writes per-session state to .clawd_sessions/cwd-<hash>.json (one file
per project directory) and auto-spawns the daemon if it isn't running.

Usage from hooks:
    python clawd_set.py <state>
    python clawd_set.py subagent_start
    python clawd_set.py subagent_stop
    python clawd_set.py session_end

Main states: idle, working, done, permission, error, boot, compact
"""

import hashlib
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(SCRIPT_DIR, ".clawd_sessions")
PID_PATH = os.path.join(SCRIPT_DIR, ".clawd_daemon.json")
DAEMON_PATH = os.path.join(SCRIPT_DIR, "clawd_daemon.py")

MAIN_STATES = {"idle", "working", "done", "permission", "error", "boot", "compact"}
SPECIAL = {"subagent_start", "subagent_stop", "session_end"}


def get_session_id() -> str:
    """Stable per-project session id derived from the current working
    directory. We deliberately do NOT read the hook's stdin JSON, even
    though it carries Claude Code's real session_id, because some hook
    invocations don't deliver stdin reliably (manual testing, certain
    async paths). Mixing the two strategies produced ghost session
    files that kept the daemon alive after Claude Code had quit.

    Tradeoff: two Claude Code windows opened in the exact same project
    directory will share one session bucket. Closing one will briefly
    flush the daemon; the other window's next hook event respawns it.
    """
    return "cwd-" + hashlib.md5(os.getcwd().encode()).hexdigest()[:8]


def session_path(session_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return os.path.join(SESSIONS_DIR, f"{safe}.json")


def read_session(session_id: str) -> dict:
    try:
        with open(session_path(session_id)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_session(session_id: str, data: dict) -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    final = session_path(session_id)
    tmp = final + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, final)


def update_state(session_id: str, new_state: str) -> None:
    existing = read_session(session_id)
    now = time.time()
    # Preserve "started" if we're already in working — needed for long-task detection.
    if new_state == "working" and existing.get("state") == "working":
        started = existing.get("started", now)
    else:
        started = now
    write_session(session_id, {
        "state": new_state,
        "started": started,
        "subagents": existing.get("subagents", 0),
        "last_seen": now,
    })


def update_subagents(session_id: str, delta: int) -> None:
    existing = read_session(session_id)
    count = max(0, existing.get("subagents", 0) + delta)
    existing["subagents"] = count
    existing["last_seen"] = time.time()
    existing.setdefault("state", "working")
    existing.setdefault("started", time.time())
    write_session(session_id, existing)


def end_session(session_id: str) -> None:
    try:
        os.remove(session_path(session_id))
    except OSError:
        pass


def daemon_is_alive(max_age: float = 3.0) -> bool:
    try:
        with open(PID_PATH) as f:
            data = json.load(f)
        return (time.time() - data["heartbeat"]) < max_age
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return False


def spawn_daemon() -> None:
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": SCRIPT_DIR,
    }
    if os.name == "nt":
        DETACHED = 0x00000008
        NEW_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED | NEW_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, DAEMON_PATH], **kwargs)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: clawd_set.py <state|subagent_start|subagent_stop|session_end>", file=sys.stderr)
        sys.exit(1)

    arg = sys.argv[1]
    session_id = get_session_id()

    if arg in MAIN_STATES:
        update_state(session_id, arg)
    elif arg == "subagent_start":
        update_subagents(session_id, +1)
    elif arg == "subagent_stop":
        update_subagents(session_id, -1)
    elif arg == "session_end":
        end_session(session_id)
    else:
        print(f"Unknown arg: {arg}", file=sys.stderr)
        sys.exit(1)

    if not daemon_is_alive():
        spawn_daemon()


if __name__ == "__main__":
    main()
