from __future__ import annotations

from pathlib import Path

import pytest


def _parser():
    import argparse
    from hermes_cli.subcommands.memory import build_memory_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_memory_parser(subparsers, cmd_memory=lambda args: None)
    return parser


def test_memory_governance_commands_parse():
    parser = _parser()
    assert parser.parse_args(["memory", "versions", "list"]).memory_command == "versions"
    assert parser.parse_args(["memory", "versions", "rollback", "v1", "--target", "user", "--yes"]).versions_command == "rollback"
    assert parser.parse_args(["memory", "step", "record", "bad write", "--note", "retry"]).step_command == "record"
    assert parser.parse_args(["memory", "meta", "log", "strategy", "--result", "success"]).meta_command == "log"


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch):
    home = tmp_path / ".hermes"
    memories = home / "memories"
    memories.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    (memories / "MEMORY.md").write_text("old durable fact", encoding="utf-8")
    return home, memories


def test_step_and_meta_commands_write_profile_scoped_records(cli_home, capsys):
    from hermes_cli.main import cmd_memory
    import argparse

    args = argparse.Namespace(memory_command="step", step_command="record", pattern="failed memory write", note="retry")
    cmd_memory(args)
    args = argparse.Namespace(memory_command="meta", meta_command="log", strategy="merge facts", result="success", note=None)
    cmd_memory(args)

    _, memories = cli_home
    assert "failed memory write" in (memories / "governance" / "step_buffer.jsonl").read_text()
    assert "merge facts" in (memories / "governance" / "meta_skill.jsonl").read_text()
    assert "success" in capsys.readouterr().out


def test_confirmed_rollback_restores_target(cli_home, capsys):
    from hermes_cli.main import cmd_memory
    from tools import memory_governance
    import argparse

    _, memories = cli_home
    versions = memories / "l2" / "versions"
    snapshot = memory_governance.snapshot(
        memories / "MEMORY.md", versions, target="memory", reason="test", operation="test"
    )
    (memories / "MEMORY.md").write_text("new fact", encoding="utf-8")
    args = argparse.Namespace(memory_command="versions", versions_command="rollback", version_id=snapshot["id"], target="memory", yes=True)
    cmd_memory(args)
    assert (memories / "MEMORY.md").read_text(encoding="utf-8") == "old durable fact"
    assert "success" in capsys.readouterr().out


def test_rollback_without_yes_does_not_mutate(cli_home, capsys):
    from hermes_cli.main import cmd_memory
    from tools import memory_governance
    import argparse

    _, memories = cli_home
    snapshot = memory_governance.snapshot(
        memories / "MEMORY.md", memories / "l2" / "versions", target="memory", reason="test", operation="test"
    )
    (memories / "MEMORY.md").write_text("new fact", encoding="utf-8")
    args = argparse.Namespace(memory_command="versions", versions_command="rollback", version_id=snapshot["id"], target="memory", yes=False)
    cmd_memory(args)
    assert (memories / "MEMORY.md").read_text(encoding="utf-8") == "new fact"
    assert "--yes" in capsys.readouterr().out
