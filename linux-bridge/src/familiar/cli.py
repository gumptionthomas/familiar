"""Single entry point: `familiar run | init | hook <event>`."""
import sys

from . import archive, daemon, doctor, hook, init

_HELP = """familiar — a desk buddy for Claude Code, on an M5 or a Tidbyt

usage:
  familiar run [--stdout]      run the daemon (M5 and/or Tidbyt, from config)
  familiar init [flags]        set up config + Claude Code hooks
  familiar hook <event>        (invoked by Claude Code's hooks)
  familiar haikus [--stats]    browse the archived haikus, or their trends
  familiar doctor              diagnose why the buddy isn't connecting
"""


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(_HELP)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "run":
        return daemon.main(rest)
    if cmd == "init":
        return init.main(rest)
    if cmd == "hook":
        # hook.main reads argv[1] as the event name, so prepend a dummy argv[0].
        return hook.main(["familiar-hook", *rest])
    if cmd == "haikus":
        return archive.main(rest)
    if cmd == "doctor":
        return doctor.main(rest)
    sys.stderr.write(f"unknown command: {cmd}\n{_HELP}")
    return 2
