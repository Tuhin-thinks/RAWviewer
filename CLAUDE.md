# CLAUDE.md — RAWviewer working rules

Rules for any agent or contributor writing code in this repository. They apply to every new
or changed Python file.

## Python version and typing

- The supported floor is **Python 3.10** (`requires-python = ">=3.10,<3.13"`). Write code that runs on 3.10.
- **Do not add `from __future__ import annotations`.** On 3.10+, `X | None`, `list[str]` and
  `dict[str, Any]` already work in annotations at runtime, so the import adds nothing. Existing
  files that have it are legacy; remove it only when you are already editing that file, and do not
  do repo-wide sweeps without asking.
- Use built-in generics and PEP 604 unions: `list[str]`, `dict[str, int]`, `tuple[int, ...]`,
  `X | None`, `A | B`. Never `typing.Optional`, `Union`, `List`, `Dict`, `Tuple`, `Set`.
- Import abstract types from `collections.abc` (`Sequence`, `Iterable`, `Callable`, `Mapping`),
  not from `typing`.
- Do not use PEP 695 syntax (`type X = ...`, `def f[T](...)`). It needs 3.12.
- Annotate every new or changed function, including `-> None` and parameters. No bare `dict`,
  `list` or `Any` where a precise type is known.
- Use `Final` for module constants that must not change; use `ClassVar` for class-level constants.
- Prefer `@dataclass(frozen=True, slots=True)` for value objects with no behaviour beyond data.
  `slots=True` is available on 3.10.
- Use `Protocol` for structural interfaces instead of inheritance-only abstractions when the
  caller does not own the implementation.
- Never write `# type: ignore` without an error code and a reason, e.g. `# type: ignore[attr-defined]  # PyQt stub gap`.
  `warn_unused_ignores` is on.
- Avoid `cast()` unless the narrowing cannot be expressed another way; prefer `isinstance` checks.

## Modern idioms

- Use `pathlib.Path` for new filesystem code, not `os.path` string juggling.
- Use f-strings. Do not use `%` or `.format()` in new code (logging calls keep lazy `%s` args).
- Use `contextlib.suppress(...)` instead of `try: ... except X: pass` when the body is one statement.
- Use `enumerate`, `zip(..., strict=True)` where lengths must match, comprehensions over
  `map`/`filter` with lambdas.
- Use `match` only where it clearly beats `if/elif` on a closed set of values, and not below 3.10.
- Use `functools.cache` / `lru_cache` deliberately; never on methods that hold `self` without a reason.
- No mutable default arguments; use `None` and create inside the function.
- No `print` in library code; use `logging.getLogger(__name__)`.
- No bare `except:`. Catch the narrowest exception that applies. A broad `except Exception` is only
  acceptable at a UI or thread boundary, and must log with `exc_info=True` or re-raise.

## Structure

- Respect the `*_partN.py` mixin layout. Add code to the right part; do not merge parts together.
- New pure logic (no Qt) goes in a module that imports nothing from PyQt, so it can be unit-tested.
- Prefer one shared helper over two near-copies that can drift apart.

## Lint and types (gates before calling work done)

- `python -m ruff check <changed files>` must pass. `UP` (including UP010) is enabled.
- `python -m mypy <changed files>` must add **no new** errors. Compare against `main` if the
  repo already has errors.
- Run tests with `python -m pytest tests -q` for any area you touched.

## Logging and diagnostics

- Log tags use a bracketed subsystem prefix: `[GALLERY]`, `[ORIENT]`, ... Add any new prefix to the
  allowlist in `logging_setup.py`, or it will be dropped silently.
- Diagnostics are opt-in through `RAWVIEWER_*` environment flags. No noisy default logs.

## Git

- Work on a feature branch off `main`: `feat/`, `fix/`, `refactor/`, `build/`, `docs/`.
- Conventional Commits: `type(scope): summary`.
- Use `gh` with `-R Tuhin-thinks/RAWviewer`.
- Do not commit generated files or secrets.
