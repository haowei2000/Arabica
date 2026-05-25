"""Compatibility launcher for the Rust Structure local CLI.

The local CLI/TUI implementation lives in ``crates/structure-local``. This
module preserves the existing ``uv run structure ...`` Python entrypoint by
delegating immediately to the Rust binary. In an installed environment it can
use a prebuilt ``structure-local`` binary; in a source checkout it falls back to
``cargo run -p structure-local``.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def build_structure_local_command(args: list[str]) -> tuple[list[str], Path | None]:
    """Build the command used by the Python compatibility entrypoint."""
    if configured_binary := os.environ.get("STRUCTURE_LOCAL_BIN"):
        return [configured_binary, *args], None

    if binary := shutil.which("structure-local"):
        return [binary, *args], None

    repo_root = find_repo_root(Path(__file__).resolve())
    command = [
        "cargo",
        "run",
        "--quiet",
        "-p",
        "structure-local",
        "--",
        *args,
    ]
    return command, repo_root


def find_repo_root(start: Path) -> Path:
    """Find the source checkout root for Cargo fallback mode."""
    current = start if start.is_dir() else start.parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "Cargo.toml").exists()
            and (candidate / "crates" / "structure-local").exists()
            and (candidate / "pyproject.toml").exists()
        ):
            return candidate
    return Path.cwd()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command, cwd = build_structure_local_command(args)
    return subprocess.call(command, cwd=cwd)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
