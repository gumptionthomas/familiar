import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def _default_socket() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return str(Path(base) / "familiar.sock")


@dataclass
class Config:
    address: str | None = None
    owner: str = ""
    socket_path: str = ""
    api_key: str = ""
    model: str = "claude-haiku-4-5-20251001"
    tidbyt_device_id: str = ""
    tidbyt_api_key: str = ""
    tidbyt_pet: str = "bufo"
    haiku_archive: bool = True


def _default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "familiar" / "config.toml"


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
        # tidbyt_* (both required) mirror the haiku to a Tidbyt display.
        tidbyt_device_id=data.get("tidbyt_device_id", ""),
        tidbyt_api_key=data.get("tidbyt_api_key") or os.environ.get("TIDBYT_API_KEY", ""),
        # tidbyt_pet selects which buddy the Tidbyt shows: "bufo" (the GIF) or
        # an ASCII species name (e.g. "capybara"); unknown falls back to bufo.
        tidbyt_pet=data.get("tidbyt_pet") or "bufo",
        # The archive writes composed haikus to $XDG_DATA_HOME/familiar/haikus.jsonl.
        # Outputs only -- never the digest. Set false to opt out entirely.
        haiku_archive=bool(data.get("haiku_archive", True)),
    )
