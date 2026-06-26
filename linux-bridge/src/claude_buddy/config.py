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
    api_key: str = ""
    model: str = "claude-haiku-4-5-20251001"


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
        # api_key enables haiku mode; falls back to the standard env var.
        api_key=data.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", ""),
        model=data.get("model") or "claude-haiku-4-5-20251001",
    )
