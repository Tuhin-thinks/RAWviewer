"""Mypy error ledger for the typing upgrade.

Runs mypy in the strict, no-override configuration (the measurement recorded in
docs/TYPING_UPGRADE_PLAN.md, Context) and counts, per file: errors by code, ``Any``
annotations and ``type: ignore`` comments. ``ratchet.py`` compares against the
committed ``typing_baseline.json``.

Run from the repository root::

    python -m scripts.typing.ledger                   # measure and write the baseline
    python -m scripts.typing.ledger --diff            # compare with the baseline
    python -m scripts.typing.ledger --emit-overrides  # print the legacy [[tool.mypy.overrides]] block
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
BASELINE_PATH: Final[Path] = ROOT / "typing_baseline.json"
CHECK_ROOTS: Final[tuple[str, ...]] = ("src", "scripts", "testplan", "tests", "build.py")
SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset({"__pycache__", "build", "dist", "rawviewer_env", ".mypy_cache"})
PYTHON_VERSION: Final[str] = "3.10"
SCHEMA_VERSION: Final[int] = 1
TYPE_IGNORE_RE: Final[re.Pattern[str]] = re.compile(r"#\s*type:\s*ignore\b")
# Modules whose codes live in the hand-written override blocks (autotest, platform).
HAND_WRITTEN_MODULES: Final[frozenset[str]] = frozenset(
    {
        "gallery_autotest",
        "nav_autotest",
        "platform_support.windows_share",
        "platform_support.display_brightness",
        "bootstrap_pkg.constants",
        "bootstrap_pkg.worker",
    }
)
# Codes that depend on which stub packages happen to be installed, so they are not baked in.
ENVIRONMENT_CODES: Final[frozenset[str]] = frozenset({"import-untyped"})
# Strict, no per-module overrides. Only the global stub-less ignore is kept, so the
# counts reflect the code and not the legacy override list.
STRICT_FLAGS: Final[tuple[str, ...]] = (
    "--strict",
    "--python-version",
    PYTHON_VERSION,
    "--allow-subclassing-any",
    "--ignore-missing-imports",
    "--explicit-package-bases",
)
# mypy has no --mypy-path flag; the search path comes from the MYPYPATH environment variable.
MYPY_PATH_ENV: Final[str] = os.pathsep.join(("src", "typings"))


@dataclass(frozen=True, slots=True)
class MypyError:
    file: str
    code: str


@dataclass(frozen=True, slots=True)
class Measurement:
    """Per-file counts. Keys are repo-relative POSIX paths."""

    errors: dict[str, dict[str, int]]
    any_counts: dict[str, int]
    ignore_counts: dict[str, int]

    def file_errors(self) -> dict[str, int]:
        return {path: sum(codes.values()) for path, codes in self.errors.items()}

    def by_code(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for codes in self.errors.values():
            total.update(codes)
        return total


def relpath(raw: str) -> str:
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def iter_python_files() -> list[Path]:
    found: list[Path] = []
    for name in CHECK_ROOTS:
        target = ROOT / name
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            for path in sorted(target.rglob("*.py")):
                if not SKIP_DIR_NAMES.intersection(path.relative_to(target).parts):
                    found.append(path)
    return found


def count_any(path: Path) -> int:
    """Count ``Any`` names and ``typing.Any`` attributes (imports are not counted)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        1
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any") or (isinstance(node, ast.Attribute) and node.attr == "Any")
    )


def count_type_ignores(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(
            1
            for token in tokenize.tokenize(handle.readline)
            if token.type == tokenize.COMMENT and TYPE_IGNORE_RE.search(token.string)
        )


def parse_mypy_json(stdout: str) -> Iterator[MypyError]:
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        record: object = json.loads(line)
        if not isinstance(record, dict) or record.get("severity") != "error":
            continue
        file_value = record.get("file")
        code_value = record.get("code")
        if not isinstance(file_value, str):
            continue
        code = code_value if isinstance(code_value, str) else "none"
        yield MypyError(file=relpath(file_value), code=code)


def run_strict_mypy() -> list[MypyError]:
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "mypy.ini"
        config.write_text("[mypy]\n", encoding="utf-8")
        cmd = [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(config),
            *STRICT_FLAGS,
            "--output",
            "json",
            *CHECK_ROOTS,
        ]
        env = dict(os.environ)
        env["MYPYPATH"] = MYPY_PATH_ENV
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", check=False)
    # mypy exits 1 when it reports errors; anything else means mypy itself failed.
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"mypy failed with exit {proc.returncode}: {proc.stderr.strip()}")
    return list(parse_mypy_json(proc.stdout))


def run_default_mypy() -> list[MypyError]:
    """Run mypy with the repository config (legacy overrides included) and parse its errors."""
    cmd = [sys.executable, "-m", "mypy", "--output", "json"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"mypy failed with exit {proc.returncode}: {proc.stderr.strip()}")
    return list(parse_mypy_json(proc.stdout))


def measure() -> Measurement:
    errors: dict[str, Counter[str]] = {}
    for error in run_strict_mypy():
        errors.setdefault(error.file, Counter())[error.code] += 1
    any_counts: dict[str, int] = {}
    ignore_counts: dict[str, int] = {}
    for path in iter_python_files():
        rel = relpath(str(path))
        n_any = count_any(path)
        if n_any:
            any_counts[rel] = n_any
        n_ignore = count_type_ignores(path)
        if n_ignore:
            ignore_counts[rel] = n_ignore
    return Measurement(
        errors={file: dict(sorted(codes.items())) for file, codes in sorted(errors.items()) if sum(codes.values()) > 0},
        any_counts=dict(sorted(any_counts.items())),
        ignore_counts=dict(sorted(ignore_counts.items())),
    )


def _int_map(value: object, where: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected an object")
    out: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, int) or isinstance(item, bool):
            raise ValueError(f"{where}: bad entry {key!r}")
        out[key] = item
    return out


def measurement_to_json(measurement: Measurement) -> dict[str, object]:
    by_code = measurement.by_code()
    return {
        "schema": SCHEMA_VERSION,
        "command": "mypy " + " ".join(STRICT_FLAGS) + " " + " ".join(CHECK_ROOTS),
        "totals": {
            "errors": sum(by_code.values()),
            "any": sum(measurement.any_counts.values()),
            "type_ignore": sum(measurement.ignore_counts.values()),
            "by_code": dict(by_code.most_common()),
        },
        "files": measurement.errors,
        "any": measurement.any_counts,
        "type_ignore": measurement.ignore_counts,
    }


def measurement_from_json(raw: object) -> Measurement:
    if not isinstance(raw, dict):
        raise ValueError("baseline root must be an object")
    errors = {str(path): _int_map(codes, str(path)) for path, codes in _int_map_of_maps(raw.get("files", {}))}
    return Measurement(
        errors=errors,
        any_counts=_int_map(raw.get("any", {}), "any"),
        ignore_counts=_int_map(raw.get("type_ignore", {}), "type_ignore"),
    )


def _int_map_of_maps(value: object) -> Iterator[tuple[str, object]]:
    if not isinstance(value, dict):
        raise ValueError("files: expected an object")
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"files: bad key {key!r}")
        yield key, item


def load_baseline(path: Path = BASELINE_PATH) -> Measurement:
    return measurement_from_json(json.loads(path.read_text(encoding="utf-8")))


def write_baseline(measurement: Measurement, path: Path = BASELINE_PATH) -> None:
    text = json.dumps(measurement_to_json(measurement), indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def compare(baseline: Measurement, current: Measurement) -> tuple[list[str], list[str]]:
    """Return (regressions, improvements) as human-readable lines."""
    regressions: list[str] = []
    improvements: list[str] = []
    metrics: Iterable[tuple[str, Mapping[str, int], Mapping[str, int]]] = (
        ("errors", baseline.file_errors(), current.file_errors()),
        ("Any", baseline.any_counts, current.any_counts),
        ("type: ignore", baseline.ignore_counts, current.ignore_counts),
    )
    for label, before, after in metrics:
        for key in sorted(set(before) | set(after)):
            old = before.get(key, 0)
            new = after.get(key, 0)
            if new > old:
                regressions.append(f"{key}: {label} {old} -> {new}")
            elif new < old:
                improvements.append(f"{key}: {label} {old} -> {new}")
    return regressions, improvements


def module_name(rel: str) -> str | None:
    path = PurePosixPath(rel)
    if path.suffix != ".py":
        return None
    parts = list(path.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or None


def emit_overrides(measurement: Measurement) -> str:
    """Render the legacy per-module block: one entry per distinct set of codes.

    Modules in HAND_WRITTEN_MODULES are skipped: mypy rejects a module listed in two override
    blocks, so their codes live in the hand-written autotest/platform blocks in pyproject.toml.
    """
    groups: dict[tuple[str, ...], list[str]] = {}
    for file, codes in measurement.errors.items():
        name = module_name(file)
        if name is None or name in HAND_WRITTEN_MODULES:
            continue
        groups.setdefault(tuple(sorted(codes)), []).append(name)
    lines = [
        "# Legacy modules with mypy errors. Generated by: python -m scripts.typing.ledger --emit-overrides",
        "# Remove a module from its list once it has no errors. The block goes when the list is empty.",
    ]
    for codes, modules in sorted(groups.items()):
        lines.append("[[tool.mypy.overrides]]")
        lines.append("module = [")
        lines.extend(f'    "{name}",' for name in sorted(modules))
        lines.append("]")
        lines.append("disable_error_code = [" + ", ".join(f'"{code}"' for code in codes) + "]")
        lines.append("")
    return "\n".join(lines)


def with_residual_codes(measurement: Measurement) -> Measurement:
    """Add codes that only the default config run reports (e.g. import-not-found).

    The strict command passes --ignore-missing-imports, which hides those codes from the baseline.
    The legacy list must still cover them or the default run is red.
    """
    errors = {file: dict(codes) for file, codes in measurement.errors.items()}
    for error in run_default_mypy():
        if error.code in ENVIRONMENT_CODES:
            continue
        errors.setdefault(error.file, {})
        errors[error.file].setdefault(error.code, 1)
    return Measurement(errors=errors, any_counts=measurement.any_counts, ignore_counts=measurement.ignore_counts)


def summary(measurement: Measurement) -> str:
    by_code = measurement.by_code()
    lines = [
        f"errors: {sum(by_code.values())} in {len(measurement.errors)} files",
        f"Any: {sum(measurement.any_counts.values())} in {len(measurement.any_counts)} files",
        f"type: ignore: {sum(measurement.ignore_counts.values())}",
        "top codes:",
    ]
    lines.extend(f"  {code}: {count}" for code, count in by_code.most_common(10))
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Measure strict mypy errors, Any and type: ignore counts per file.")
    parser.add_argument("--diff", action="store_true", help="compare with the baseline; exit 1 on any increase")
    parser.add_argument(
        "--emit-overrides", action="store_true", help="print the legacy [[tool.mypy.overrides]] block and exit"
    )
    args = parser.parse_args(argv)

    current = measure()
    if args.emit_overrides:
        sys.stdout.write(emit_overrides(with_residual_codes(current)))
        return 0
    if args.diff:
        regressions, improvements = compare(load_baseline(), current)
        for line in regressions:
            sys.stdout.write(f"REGRESSION {line}\n")
        for line in improvements:
            sys.stdout.write(f"improved {line}\n")
        return 1 if regressions else 0
    write_baseline(current)
    sys.stdout.write(summary(current))
    sys.stdout.write(f"wrote {BASELINE_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
