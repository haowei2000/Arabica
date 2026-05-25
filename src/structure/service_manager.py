"""Compatibility launcher for the Rust Structure local CLI.

The local CLI/TUI implementation lives in ``crates/structure-local``.  This
module only preserves the existing ``uv run structure ...`` Python entrypoint by
delegating immediately to Cargo.
"""

from __future__ import annotations

import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = [
        "cargo",
        "run",
        "--quiet",
        "-p",
        "structure-local",
        "--",
        *args,
    ]
    return subprocess.call(command)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
