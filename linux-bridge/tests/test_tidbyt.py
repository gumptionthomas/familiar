import asyncio
import base64
import json
import pytest
from familiar import tidbyt


@pytest.fixture(autouse=True)
def _reset_push_warning():
    # The warn-once streak is module state; keep it from leaking across tests.
    tidbyt.reset_push_warning()


def test_push_image_posts_base64_webp():
    calls = []
    def poster(url, data, headers):
        calls.append((url, json.loads(data), headers))
        return 200
    ok = asyncio.run(tidbyt.push_image(b"WEBPDATA", device_id="dev1",
                                       api_token="tok", poster=poster))
    assert ok is True
    url, body, headers = calls[0]
    assert url == "https://api.tidbyt.com/v0/devices/dev1/push"
    assert base64.b64decode(body["image"]) == b"WEBPDATA"
    assert body["installationID"] == "familiar"
    assert body["background"] is False
    assert headers["Authorization"] == "Bearer tok"


def test_installation_id_is_alphanumeric():
    # The Tidbyt API 400s on hyphens, so a rename must never reintroduce one.
    assert tidbyt.INSTALLATION_ID.isalnum()


def test_push_image_missing_config_is_false():
    assert asyncio.run(tidbyt.push_image(b"x", device_id="", api_token="t")) is False


def test_push_image_http_error_is_false():
    def poster(url, data, headers):
        return 500
    assert asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                         poster=poster)) is False


def test_push_image_poster_raises_is_false():
    def poster(url, data, headers):
        raise OSError("network down")
    assert asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                         poster=poster)) is False


def test_push_renders_then_pushes(monkeypatch):
    monkeypatch.setattr(tidbyt.haiku_render, "render", lambda lines: b"RENDERED")
    sent = {}
    def poster(url, data, headers):
        import json, base64
        sent["img"] = base64.b64decode(json.loads(data)["image"]); return 200
    ok = asyncio.run(tidbyt.push(["a", "b", "c"], device_id="d", api_token="t",
                                 poster=poster))
    assert ok is True and sent["img"] == b"RENDERED"


def test_push_empty_lines_is_false():
    assert asyncio.run(tidbyt.push(["", "", ""], device_id="d", api_token="t")) is False


def test_http_error_warns_with_the_status(capsys):
    asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                  poster=lambda u, d_, h: 400))
    err = capsys.readouterr().err
    assert "[familiar]" in err and "400" in err


def test_repeated_failures_warn_only_once(capsys):
    for _ in range(3):
        asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                      poster=lambda u, d_, h: 500))
    assert capsys.readouterr().err.count("[familiar]") == 1


def test_recovery_is_reported_and_rearms_the_warning(capsys):
    asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                  poster=lambda u, d_, h: 500))
    asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                  poster=lambda u, d_, h: 200))
    assert "again" in capsys.readouterr().err
    # Re-armed: the next failure warns rather than staying silent.
    asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                  poster=lambda u, d_, h: 500))
    assert "[familiar]" in capsys.readouterr().err


def test_poster_exception_is_warned(capsys):
    def poster(url, data, headers):
        raise OSError("network down")
    asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                  poster=poster))
    assert "network down" in capsys.readouterr().err


def test_unconfigured_is_silent(capsys):
    # No Tidbyt configured is a normal state, not a fault worth warning about.
    asyncio.run(tidbyt.push_image(b"x", device_id="", api_token=""))
    assert capsys.readouterr().err == ""


def test_push_image_missing_token_is_false():
    assert asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="")) is False


def test_push_image_empty_bytes_is_false():
    assert asyncio.run(tidbyt.push_image(b"", device_id="d", api_token="t")) is False
