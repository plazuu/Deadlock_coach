"""Best-effort OS desktop notifications for `limpet watch`.

Never raises. Most of the time this runs inside Docker (no display, no
notifier binary) where it's a silent no-op — `digests/YYYY-MM-DD.md` is the
reliable record of what `watch` did either way, this is just a nicety for a
natively-running process.
"""

from __future__ import annotations

import platform
import shutil
import subprocess

_TIMEOUT_S = 5


def notify(title: str, body: str) -> None:
    try:
        system = platform.system()
        if system == "Darwin" and shutil.which("osascript"):
            script = f'display notification "{_escape(body)}" with title "{_escape(title)}"'
            subprocess.run(
                ["osascript", "-e", script], check=False, timeout=_TIMEOUT_S, capture_output=True
            )
        elif system == "Linux" and shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", title, body], check=False, timeout=_TIMEOUT_S, capture_output=True
            )
        # Anything else — no supported notifier (headless/Docker/Windows/no
        # binary found) — silently do nothing.
    except Exception:
        pass  # a notification failing must never interrupt the watch loop


def _escape(s: str) -> str:
    """Escape for embedding in an AppleScript double-quoted string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
