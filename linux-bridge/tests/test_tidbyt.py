import asyncio
import base64
import json
from familiar import tidbyt


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
    assert body["installationID"] == "claudebuddy"
    assert body["background"] is False
    assert headers["Authorization"] == "Bearer tok"


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
