"""Fail on any ``type: ignore`` comment that lacks an error code and a reason.

Accepted form, the only one the plan allows::

    x = thing  # type: ignore[attr-defined]  # <lib> stub gap: <why>

Run from the repository root: python -m scripts.typing.lint_ignores
"""

import re
import sys
import tokenize
from typing import Final

from scripts.typing.ledger import ROOT, iter_python_files

VALID_IGNORE_RE: Final[re.Pattern[str]] = re.compile(
    r"#\s*type:\s*ignore\[[a-z][a-z0-9-]*(?:,\s*[a-z][a-z0-9-]*)*\]\s+#\s*\S.*"
)
ANY_IGNORE_RE: Final[re.Pattern[str]] = re.compile(r"#\s*type:\s*ignore\b")


def violations() -> list[str]:
    found: list[str] = []
    for path in iter_python_files():
        with path.open("rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type != tokenize.COMMENT or not ANY_IGNORE_RE.search(token.string):
                    continue
                if not VALID_IGNORE_RE.fullmatch(token.string.strip()):
                    rel = path.relative_to(ROOT).as_posix()
                    found.append(f"{rel}:{token.start[0]}: {token.string.strip()}")
    return found


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(__doc__ or "")
        return 0
    found = violations()
    for line in found:
        sys.stderr.write(f"lint_ignores: {line}\n")
    if found:
        sys.stderr.write(f"lint_ignores: {len(found)} type: ignore comment(s) lack [code]  # reason\n")
        return 1
    sys.stdout.write("lint_ignores: ok\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
