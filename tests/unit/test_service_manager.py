from __future__ import annotations

from pathlib import Path

from structure import service_manager


def test_build_command_uses_configured_binary(monkeypatch):
    monkeypatch.setenv("STRUCTURE_LOCAL_BIN", "/opt/bin/structure-local")
    monkeypatch.setattr(service_manager.shutil, "which", lambda _name: None)

    command, cwd = service_manager.build_structure_local_command(["status", "--json"])

    assert command == ["/opt/bin/structure-local", "status", "--json"]
    assert cwd is None


def test_build_command_uses_path_binary(monkeypatch):
    monkeypatch.delenv("STRUCTURE_LOCAL_BIN", raising=False)
    monkeypatch.setattr(
        service_manager.shutil,
        "which",
        lambda name: "/usr/local/bin/structure-local"
        if name == "structure-local"
        else None,
    )

    command, cwd = service_manager.build_structure_local_command(["parity", "--verify"])

    assert command == ["/usr/local/bin/structure-local", "parity", "--verify"]
    assert cwd is None


def test_build_command_falls_back_to_cargo(monkeypatch, tmp_path):
    repo_root = tmp_path / "Structure"
    (repo_root / "crates" / "structure-local").mkdir(parents=True)
    (repo_root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (repo_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    start = repo_root / "src" / "structure" / "service_manager.py"
    start.parent.mkdir(parents=True)
    start.write_text("", encoding="utf-8")

    monkeypatch.delenv("STRUCTURE_LOCAL_BIN", raising=False)
    monkeypatch.setattr(service_manager.shutil, "which", lambda _name: None)
    monkeypatch.setattr(service_manager, "__file__", str(start))

    command, cwd = service_manager.build_structure_local_command(["tui"])

    assert command == [
        "cargo",
        "run",
        "--quiet",
        "-p",
        "structure-local",
        "--",
        "tui",
    ]
    assert cwd == repo_root


def test_main_delegates_to_built_command(monkeypatch):
    calls: list[tuple[list[str], Path | None]] = []

    monkeypatch.setattr(
        service_manager,
        "build_structure_local_command",
        lambda args: (["structure-local", *args], None),
    )
    monkeypatch.setattr(
        service_manager.subprocess,
        "call",
        lambda command, cwd=None: calls.append((command, cwd)) or 17,
    )

    result = service_manager.main(["status"])

    assert result == 17
    assert calls == [(["structure-local", "status"], None)]
