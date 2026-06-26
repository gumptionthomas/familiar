import asyncio
from claude_buddy import haiku


def test_to_haiku_three_lines():
    assert haiku.to_haiku("line one\nline two\nline three") == [
        "line one", "line two", "line three"]


def test_to_haiku_slash_separated_one_line():
    assert haiku.to_haiku("a stir / b flows / c rests") == [
        "a stir", "b flows", "c rests"]


def test_to_haiku_strips_blanks_and_caps_three():
    assert haiku.to_haiku("\n\nl1\n\nl2\nl3\nl4\n") == ["l1", "l2", "l3"]


def test_to_haiku_empty_is_none():
    assert haiku.to_haiku("") is None
    assert haiku.to_haiku("   \n  ") is None


def test_compose_success_with_injected_request():
    async def fake(digest):
        assert "GH" in digest          # the digest is passed through
        return "files mend themselves\na branch returns home\ntests glow green"

    out = asyncio.run(haiku.compose("Focus [GH]: edited auth.py",
                                    api_key="k", request=fake))
    assert out == ["files mend themselves", "a branch returns home", "tests glow green"]


def test_compose_no_key_returns_none():
    async def fake(d):
        return "x\ny\nz"
    assert asyncio.run(haiku.compose("d", api_key="", request=fake)) is None


def test_compose_empty_digest_returns_none():
    async def fake(d):
        return "x\ny\nz"
    assert asyncio.run(haiku.compose("", api_key="k", request=fake)) is None


def test_compose_request_error_returns_none():
    async def boom(d):
        raise RuntimeError("network down")
    assert asyncio.run(haiku.compose("d", api_key="k", request=boom)) is None


def test_compose_malformed_returns_none():
    async def empty(d):
        return ""
    assert asyncio.run(haiku.compose("d", api_key="k", request=empty)) is None
