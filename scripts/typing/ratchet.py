"""Ratchet gate: strict error, Any and type: ignore counts may only go down.

Run from the repository root::

    python -m scripts.typing.ratchet            # exit 1 if any file is above the baseline
    python -m scripts.typing.ratchet --update   # rewrite the baseline; refused if any count rose
"""

import argparse
import sys
from typing import Final

from scripts.typing.ledger import BASELINE_PATH, compare, load_baseline, measure, write_baseline

EXIT_OK: Final[int] = 0
EXIT_REGRESSED: Final[int] = 1
EXIT_NO_BASELINE: Final[int] = 2


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Fail if strict mypy error, Any or type: ignore counts rose.")
    parser.add_argument("--update", action="store_true", help="rewrite typing_baseline.json (only if nothing rose)")
    args = parser.parse_args(argv)

    if not BASELINE_PATH.is_file():
        sys.stderr.write(f"ratchet: no baseline at {BASELINE_PATH}; run python -m scripts.typing.ledger\n")
        return EXIT_NO_BASELINE

    current = measure()
    regressions, improvements = compare(load_baseline(), current)
    if regressions:
        for line in regressions:
            sys.stderr.write(f"ratchet: REGRESSION {line}\n")
        sys.stderr.write(f"ratchet: {len(regressions)} count(s) rose; fix them or justify in review\n")
        return EXIT_REGRESSED
    if args.update:
        write_baseline(current)
        sys.stdout.write(f"ratchet: baseline updated ({len(improvements)} count(s) went down)\n")
        return EXIT_OK
    sys.stdout.write(f"ratchet: ok ({len(improvements)} count(s) below baseline)\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
