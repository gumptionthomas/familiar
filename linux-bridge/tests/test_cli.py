from familiar import cli


def test_cli_dispatches_run(monkeypatch):
    called = {}

    def fake_run(argv):
        called["run"] = argv
        return 0

    monkeypatch.setattr(cli.daemon, "main", fake_run)
    assert cli.main(["run", "--stdout"]) == 0
    assert called["run"] == ["--stdout"]


def test_cli_dispatches_hook(monkeypatch):
    called = {}

    def fake_hook(argv):
        called["hook"] = argv
        return 0

    monkeypatch.setattr(cli.hook, "main", fake_hook)
    assert cli.main(["hook", "stop"]) == 0
    assert called["hook"] == ["familiar-hook", "stop"]   # hook.main reads argv[1:] as event


def test_cli_dispatches_doctor(monkeypatch):
    called = {}

    def fake_doctor(argv):
        called["doctor"] = argv
        return 0

    monkeypatch.setattr(cli.doctor, "main", fake_doctor)
    assert cli.main(["doctor"]) == 0
    assert called["doctor"] == []


def test_cli_no_args_prints_help(capsys):
    assert cli.main([]) == 0
    assert "familiar" in capsys.readouterr().out.lower()
