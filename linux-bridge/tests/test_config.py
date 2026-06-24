from pathlib import Path
from claude_buddy.config import load


def test_load_defaults_when_missing(tmp_path):
    cfg = load(tmp_path / "nope.toml")
    assert cfg.address is None
    assert cfg.owner == ""
    assert cfg.socket_path.endswith("claude-buddy.sock")


def test_load_reads_values(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('address = "AA:BB:CC:DD:EE:FF"\nowner = "Thomas"\n')
    cfg = load(p)
    assert cfg.address == "AA:BB:CC:DD:EE:FF"
    assert cfg.owner == "Thomas"


def test_load_reads_socket_override(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('socket = "/run/custom/buddy.sock"\n')
    cfg = load(p)
    assert cfg.socket_path == "/run/custom/buddy.sock"
