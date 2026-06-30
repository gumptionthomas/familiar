import asyncio
from claude_buddy import tidbyt


def test_push_noop_without_config():
    assert asyncio.run(tidbyt.push(["a"], device_id="", api_token="t",
                                   app_path="/x")) is False
    assert asyncio.run(tidbyt.push(["a"], device_id="d", api_token="",
                                   app_path="/x")) is False
    assert asyncio.run(tidbyt.push(["", "", ""], device_id="d", api_token="t",
                                   app_path="/x")) is False


def test_render_args_fold_cap_and_shape():
    args = tidbyt.render_args("/app.star", ["shed skin—", "it’s done", "x", "y"],
                              "/o.webp")
    assert args[:3] == ["pixlet", "render", "/app.star"]
    assert "l1=shed skin-" in args      # em-dash folded
    assert "l2=it's done" in args       # curly apostrophe folded
    assert "l3=x" in args               # 4th line dropped (cap 3)
    assert args[-2:] == ["-o", "/o.webp"]


def test_render_args_pads_missing_lines():
    args = tidbyt.render_args("/a.star", ["only one"], "/o.webp")
    assert "l1=only one" in args and "l2=" in args and "l3=" in args


def test_push_args_shape():
    args = tidbyt.push_args("dev1", "/o.webp", "tok", "inst")
    assert args == ["pixlet", "push", "dev1", "/o.webp",
                    "-t", "tok", "-i", "inst"]


def test_push_runs_render_then_push():
    calls = []

    async def fake(args):
        calls.append(args)
        return 0

    ok = asyncio.run(tidbyt.push(["one", "two", "three"], device_id="dev1",
                                 api_token="tok", app_path="/app.star", runner=fake))
    assert ok is True
    assert calls[0][1] == "render"
    assert calls[1][1] == "push"
    assert "dev1" in calls[1] and "tok" in calls[1]


def test_push_short_circuits_on_render_failure():
    calls = []

    async def fail_render(args):
        calls.append(args)
        return 1

    ok = asyncio.run(tidbyt.push(["a"], device_id="d", api_token="t",
                                 app_path="/x", runner=fail_render))
    assert ok is False
    assert len(calls) == 1   # never reached push


def test_push_swallows_runner_exception():
    async def boom(args):
        raise RuntimeError("pixlet not found")

    ok = asyncio.run(tidbyt.push(["a"], device_id="d", api_token="t",
                                 app_path="/x", runner=boom))
    assert ok is False
