"""PlatformIO pre-build hook: stamp the firmware with its git version.

Injects -DFW_VERSION="<git describe --always --dirty>" into the device
environment. Wired up from platformio.ini via:

    extra_scripts = pre:tools/git_version.py

Never fails the build: if git is missing, or this is a source zip rather
than a checkout, the version is "unknown" and src/version.h's fallback
would cover it anyway.
"""
import subprocess

Import("env")  # noqa: F821 — injected by PlatformIO's SCons environment


def git_version() -> str:
    try:
        p = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return "unknown"
    v = p.stdout.strip()
    return v if p.returncode == 0 and v else "unknown"


version = git_version()
print(f"[git_version] FW_VERSION={version}")
env.Append(CPPDEFINES=[("FW_VERSION", env.StringifyMacro(version))])  # noqa: F821
