import asyncio
from claude_buddy.transport import FakeTransport, StdoutTransport


def test_fake_records():
    t = FakeTransport()
    asyncio.run(t.send(b"hi\n"))
    assert t.sent == [b"hi\n"]


def test_stdout_prints(capsys):
    t = StdoutTransport()
    asyncio.run(t.send(b'{"a":1}\n'))
    assert '{"a":1}' in capsys.readouterr().out
