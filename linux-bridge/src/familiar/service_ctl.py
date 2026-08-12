"""systemctl --user + journal reads for `familiar repair`.

Kept apart from repair.py so the sequence stays pure and testable.
"""
import asyncio
import subprocess
import time


def saw_connect(journal_text: str) -> bool:
    """True if the daemon logged a SUCCESSFUL connect.

    Matches the trailing space in "connected <MAC>" -- doctor.py does the
    same. Without it "disconnected:" matches as a substring and every
    failure reads as a success.
    """
    return "[familiar] connected " in journal_text


class Service:
    def __init__(self, unit="familiar"):
        self._unit = unit
        self._started_at = None

    def _systemctl(self, verb):
        subprocess.run(["systemctl", "--user", verb, self._unit],
                       capture_output=True, text=True, timeout=30, check=False)

    def stop(self):
        self._systemctl("stop")

    def start(self):
        self._systemctl("start")
        self._started_at = time.time()

    async def wait_for_connect(self, timeout):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Anchor to the restart time if available; fall back to relative window
        # if start() was never called. Subtract 1 second deliberately so a
        # connect logged in the same second as the restart is not missed.
        if self._started_at is not None:
            since = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(self._started_at - 1))
        else:
            since = "-2min"
        while loop.time() < deadline:
            out = subprocess.run(
                ["journalctl", "--user", "-u", f"{self._unit}.service",
                 "--since", since, "--no-pager"],
                capture_output=True, text=True, timeout=10, check=False).stdout
            if saw_connect(out):
                return True
            await asyncio.sleep(2.0)
        return False
