"""Generator for the TYPE_CHECKING-only host classes that type the mixin composites.

Hosts are added in Phase 2 of the typing upgrade. Until then HOSTS is empty, so --check
passes. Each HostSpec names a composite class, its ``*_partN`` parts and the generated
output file. Generated files are never edited by hand; regenerate them instead.

Run from the repository root: python -m scripts.typing.gen_hosts --check
"""

import argparse
import sys
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class HostSpec:
    composite: str
    parts: tuple[str, ...]
    output: str


HOSTS: Final[tuple[HostSpec, ...]] = ()


def check() -> int:
    if not HOSTS:
        sys.stdout.write("gen_hosts: no hosts configured yet (added in Phase 2); nothing to check\n")
        return 0
    sys.stderr.write(f"gen_hosts: {len(HOSTS)} host(s) configured, but the generator is not implemented\n")
    return 2


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check that the generated mixin host classes are up to date.")
    parser.add_argument("--check", action="store_true", help="exit 1 if committed hosts are stale")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    sys.stdout.write("gen_hosts: pass --check (generation runs in Phase 2)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
