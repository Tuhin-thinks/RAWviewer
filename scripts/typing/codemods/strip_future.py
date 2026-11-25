"""libcst codemod that removes ``from __future__ import annotations`` (Phase 1).

The transform is not written yet. The Phase 1 packet implements it. Until then the script
explains that and exits 2, so nothing is changed by accident.
"""

import argparse
import sys
from typing import Final

NOT_IMPLEMENTED: Final[str] = "strip_future: not implemented yet (Phase 1 of the typing upgrade)\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Remove the __future__ annotations import (libcst).")
    parser.add_argument("paths", nargs="*", help="files or directories to rewrite")
    parser.parse_args(argv)
    sys.stderr.write(NOT_IMPLEMENTED)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
