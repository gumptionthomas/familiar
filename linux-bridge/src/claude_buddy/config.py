import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def _default_socket() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return str(Path(base) / "claude-buddy.sock")


@dataclass
class Config:
    address: str | None = None
    owner: str = ""
    socket_path: str = ""


def _default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claude-buddy" / "config.toml"


def load(path: Path | None = None) -> Config:
    path = path or _default_config_path()
    data = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    return Config(
        address=data.get("address"),
        owner=data.get("owner", ""),
        socket_path=data.get("socket") or _default_socket(),
    )
