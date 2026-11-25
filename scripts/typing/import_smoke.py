"""Import every module under src/ in its own subprocess with QT_QPA_PLATFORM=offscreen.

Catches NameError and similar failures from annotations that are evaluated at runtime
once ``from __future__ import annotations`` is removed. Exit 1 if any module fails.

Run from the repository root: python -m scripts.typing.import_smoke [--timeout SECONDS]
"""

import argparse
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from scripts.typing.ledger import ROOT

SRC: Final[Path] = ROOT / "src"
IMPORT_CODE: Final[str] = "import importlib, sys; importlib.import_module(sys.argv[1])"


def module_names() -> Iterator[str]:
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        if "__pycache__" in rel.parts:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            yield ".".join(parts)


def import_one(name: str, timeout: float) -> str | None:
    """Return None on success, otherwise a one-line reason."""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(SRC)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", IMPORT_CODE, name],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout:.0f}s"
    if proc.returncode == 0:
        return None
    tail = proc.stderr.strip().splitlines()
    return tail[-1] if tail else f"exit {proc.returncode}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Import every src/ module in a subprocess.")
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds allowed per module (default 60)")
    args = parser.parse_args(argv)
    failures: list[str] = []
    names = list(module_names())
    for name in names:
        reason = import_one(name, args.timeout)
        if reason is not None:
            failures.append(name)
            sys.stderr.write(f"import_smoke: FAIL {name}: {reason}\n")
    sys.stdout.write(f"import_smoke: {len(names) - len(failures)}/{len(names)} modules imported\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
