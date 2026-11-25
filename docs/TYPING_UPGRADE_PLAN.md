# RAWviewer typing & code-quality upgrade (repo-wide, strict mypy, zero blanket suppression)

> After approval this file is copied verbatim to `docs/TYPING_UPGRADE_PLAN.md` in the repo.
> It is written so a subagent can execute one **work packet** using only this file plus the files
> its packet names. Do not explore the repo beyond your packet's file list.

## Context
CLAUDE.md now requires Python 3.10 typing (PEP 604/585, no `from __future__ import annotations`,
everything annotated, strict mypy). The code does not meet that. `pyproject.toml` hides most of the
problem behind a package-wide `disable_error_code` block for `rawviewer_app.*`/`rawviewer_ui.*` (13
codes, including `no-untyped-def`). Measured on `chore/python-standards` @ 302f1f3 with **no
overrides** (`mypy --strict --python-version 3.10 --allow-subclassing-any --ignore-missing-imports`):

| Scope | Errors | Files |
|---|---:|---:|
| `src/` | 7,887 | 197 of 262 |
| `testplan/` | 1,308 | — |
| `scripts/` | 26 | — |
| `build.py` | 25 | 1 |
| `tests/` | 9 | — |
| **Total** | **≈9,255** | |

Errors in `src/` by code:

| Code | Count | Code | Count | Code | Count |
|---|---:|---|---:|---|---:|
| attr-defined | 4830 | misc | 147 | call-overload | 93 |
| no-untyped-def | 1052 | has-type | 146 | var-annotated | 49 |
| no-untyped-call | 416 | union-attr | 141 | operator | 40 |
| no-any-return | 252 | call-arg | 136 | unused-ignore | 23 |
| assignment | 200 | arg-type | 104 | override | 18 |
| type-arg | 187 | | | | |

The long tail is index, name-defined, method-assign and others.

Errors by package:

| Package | Errors | Package | Errors |
|---|---:|---|---:|
| `rawviewer_app/window` | 4116 | `processing/unified` | 255 |
| `rawviewer_ui/widgets` | 1832 | `image_load_manager` | 263 |
| `search/engine` | 257 | `rawviewer_app/core` | 123 |

The rest are under 100 each.

**Root cause of about 60% of the errors:** there are 8 mixin-composed classes. mypy checks each
`*_partN` mixin on its own, so attributes and methods that live in sibling parts are invisible to it.

| Composite | Parts |
|---|---|
| `RAWImageViewer` | 44 parts + SessionMixin + QMainWindow |
| `JustifiedGallery` | 7 |
| `ImageAdjustPanelWidget` | 6 |
| `GpuImageView` | 2 |
| `SemanticImageIndex` | 7 |
| `UnifiedImageProcessor` | 7 |
| `ImageLoadManager` | 3 |
| `RAWProcessor` | chain of 2 |

Other measured debt:
- 210 files have the future import.
- 2,162 `getattr(self, "...")` and 478 `hasattr(self, "...")` calls.
- 35 `# type: ignore`.
- 25 `print(` calls in `src`.
- `os.path` is used in 136 files.
- 27 ruff findings.

**Latent runtime bugs** found while measuring, from stale imports of modules that no longer exist:

| Stale import | Correct import | Where |
|---|---|---|
| `fast_raw_decode` | `processing.raw_decode.fast` | `processing/raw_decode/gpu_processor.py` x6 |
| `gpu_raw_processor` | `processing.raw_decode.gpu_processor` | `fast.py` x3, `gpu_bridge.py` x2 |
| `gpu_gl_bridge` | `processing.raw_decode.gpu_bridge` | `gpu_processor.py` x2 |
| `torch_bootstrap` | `bootstrap_pkg.torch` | `fast.py` x2 |
| `metadata_backend` | `metadata.backend` | `metadata/subject_area.py:637` |
| `exifread_af` | `metadata.exifread_af` | `subject_area.py:657` |
| `widgets` | `rawviewer_app.core.widgets` | `color_calibration_dialog.py:476`, `adjust_part4.py:717` |
| `rawviewer_ui.theme` | `core.theme` | 2 try/except fallbacks |

**Outcome:** the whole repo passes `mypy --strict` with **no** package-wide `disable_error_code`. The
only overrides left are `ignore_missing_imports` for genuinely stub-less third-party modules. `Any`
appears only at third-party boundaries. No future imports remain. Ruff, including ANN, is clean. CI
enforces all of this.

## Decisions (confirmed by user)
1. **Mixin typing:** a *generated* TYPE_CHECKING-only host class per composite. Parts inherit it only
   under `TYPE_CHECKING`. The `*_partN` layout is kept.
2. **Dev tools:** a PEP 735 `[dependency-groups] dev` in `pyproject.toml` plus a pixi `dev` feature.
   They are never shipped in the installer.
3. **`Any` policy:** `Any` only at third-party boundaries (rawpy, cupy, torch, onnxruntime, objc/AppKit,
   CoreML, comtypes, lensfunpy, pyexiv2), wrapped behind typed functions. A ratchet counts `Any` per
   file and must never grow.
4. **Sequencing:**
   1. Merge `chore/python-standards`.
   2. Rebase and finish `feat/gallery-explorer-selection`.
   3. Run the typing upgrade on an integration branch `typing/upgrade`, with one PR per work packet.

## Global rules for every agent (read before any edit)
- **Forbidden:**
  - Any new `# type: ignore`. The only exception is a third-party stub gap, written exactly
    `# type: ignore[<code>]  # <lib> stub gap: <why>`.
  - `cast()` where an `isinstance`/`assert` narrowing works.
  - New `Any` outside the boundary list.
  - Widening a signature to make an error go away, such as `object` params or `-> Any`.
  - `typing.Optional`, `Union`, `List`, `Dict`, `Tuple` or `Set`.
  - PEP 695 syntax.
  - `from __future__ import annotations`.
- **Behaviour must not change.** Typing edits are refactors. The only allowed exceptions are the fixes
  listed in this plan: the stale imports, `print` → logging, and bugs that mypy proves. Each of those
  goes in its own commit with `fix(...)` and a one-line justification.
- **Recipes** are in the "Fix recipes" section. Use them instead of improvising.
- **Generated files** (`src/**/_host.py`, `typing_baseline.json`) are never hand-edited. Regenerate
  them after a rebase instead of resolving merge conflicts by hand.
- **Commands** run from the repo root with `PY=rawviewer_env/Scripts/python.exe`. On pixi, use
  `pixi run -e dev python` instead.
- **Commits:**
  - Conventional Commits, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - PRs use `gh ... -R Tuhin-thinks/RAWviewer`, and the body ends with
    `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
  - `tests/` is gitignored, so add new test files with `git add -f`.

## Phase 0: Tooling, baseline, ratchet (single agent, branch `typing/p00-tooling`)
1. `pyproject.toml`:
   - Set `[tool.mypy] python_version = "3.10"`.
   - Add `files = ["src", "scripts", "testplan", "tests", "build.py"]`, `mypy_path = "src:typings"`
     and `explicit_package_bases = true`.
   - Replace the `rawviewer_ui.*`/`rawviewer_app.*` wildcard override with an **explicit per-module
     legacy list**, generated by `ledger.py --emit-overrides`. It lists every module that currently has
     errors, with exactly the codes that module has. Packets delete their own lines from this list.
     When the list is empty, the block is removed.
   - Extend the stub-less `ignore_missing_imports` list to: objc, AppKit, Foundation, Quartz, CoreML,
     Vision, comtypes(.*), cupy(.*), torch(.*), kornia(.*), onnxruntime(.*), lensfunpy, pyexiv2,
     qtawesome. Remove `PyQt6.*` from it, because PyQt6 6.12 ships `.pyi`.
   - Add `[dependency-groups] dev = [mypy, ruff, pytest, pytest-qt, libcst, autotyping, MonkeyType,
     types-psutil]` with versions pinned to the ones installed. Add a send2trash stub: use typeshed's
     package if `pip index versions types-Send2Trash` finds one, otherwise write
     `typings/send2trash/__init__.pyi`.
   - Add a matching `[feature.dev.pypi-dependencies]` and `[environments] dev = ["dev"]` in `pixi.toml`.
2. New `scripts/typing/` (fully typed and ruff-clean; each script has `--help`):

   | Script | Purpose |
   |---|---|
   | `ledger.py` | Runs mypy with the **strict no-override** command above. Writes `typing_baseline.json` as `{file: {code: n}}` plus totals. `--emit-overrides` prints the legacy TOML block. `--diff` compares against the baseline. |
   | `ratchet.py` | Fails if any file's error count, `Any` count, or `type: ignore` count is higher than in the baseline. `--update` rewrites the baseline, and is only allowed when counts go down. |
   | `lint_ignores.py` | Fails on any `type: ignore` that lacks `[code]  # reason`. |
   | `gen_hosts.py` | Host generator; see Phase 2. `--check` exits 1 if the committed hosts are stale. |
   | `import_smoke.py` | Imports every module under `src/` in a subprocess with `QT_QPA_PLATFORM=offscreen`. Catches NameErrors from runtime-evaluated annotations once the future import is gone. |
   | `codemods/strip_future.py` | libcst codemod; see Phase 1. |
   | `codemods/getattr_to_attr.py` | libcst codemod; see Phase 2. |
   | `dump_instance_attrs.py` | When `RAWVIEWER_TYPING_DUMP=1`, registers an `atexit` hook in the app that writes `{class: {attr: sorted({type(v).__qualname__})}}` for each composite instance to `scratch/attr_types.json`. Hook it in `main.py` behind the env flag, which is opt-in. |

3. Commit the initial `typing_baseline.json`. Gate: `ratchet.py` passes, and the default `mypy` run
   (with the legacy list) is green.

## Phase 1: Repo-wide mechanical codemods (single agent, sequential, branch `typing/p01-codemods`)
Every step is a **separate commit**. After each one, run `import_smoke.py`, pytest,
`testplan/run_all.sh --fast` and `ratchet.py --update`.

1. **`fix(imports)`: repair the stale imports** in the table above. **This is a behaviour change.** The
   GPU finish path in `fast.py`/`gpu_processor.py` can now actually run when torch is present.
   - The change is gated by the existing `RAWVIEWER_PREFER_GPU_DECODE` flag (default `0` in pixi).
   - Verify by launching the app on a RAW folder with the flag at 0 and at 1, and compare the logs.
   - Delete the `try: import rawviewer_ui.theme / except` fallbacks; import `core.theme` directly.
   - Also fix `testplan/run_all.sh`, which compiles 4 paths that no longer exist
     (`fast_raw_decode.py`, `processing/unified.py`, `raw_pv2012.py`, `gpu_raw_processor.py`).
2. **`refactor(typing)`: remove `from __future__ import annotations`** from all 210 files with
   `codemods/strip_future.py`. For each annotation, find names that are not bound at runtime when
   the annotation is evaluated:
   - names imported only under `TYPE_CHECKING`;
   - names defined later in the module;
   - names in the class's own body (self-references).

   Wrap those annotations in quotes. Leave everything else unquoted. `UP037` must stay clean, because
   only the necessary quotes are added. Run `import_smoke.py`; it must pass.
3. **`refactor(typing)`: autotyping** with
   `autotyping --none-return --scalar-return --bool-param --int-param --float-param --str-param --bytes-param --annotate-optional --guess-common-names src scripts testplan tests build.py`.
   Then run `ruff check --select ANN204 --fix` (`__init__ -> None`) and `ruff format`.
4. **`refactor(typing)`: MonkeyType traces.** Run under `monkeytype run`, with the DB in the
   scratchpad:
   - pytest;
   - `testplan/run_all.sh --fast`;
   - the app with `RAWVIEWER_GALLERY_AUTOTEST=1` and with `RAWVIEWER_NAV_AUTOTEST=1`, plus
     `RAWVIEWER_TYPING_DUMP=1`, on the sample folder `D:\DCIM\15761121`.

   Run `monkeytype apply <module>` **only for non-composite modules**, which are every module outside
   the 8 composites. Review rules:
   - Replace concrete `list`/`dict` params with `Sequence`/`Mapping` when the function does not mutate
     them.
   - Collapse unions of more than 3 members into the real domain type.
   - Reject any `Any` that MonkeyType produces.
   - Drop traced types for parameters that are only callbacks; use `Callable[..., None]` with the real
     arity instead.
   - Save the traces as `scratch/monkeytype.sqlite3`. Phase 3 packets reuse them with
     `monkeytype stub <module>`.
5. **`fix(lint)`: the remaining 27 ruff findings.**
   - SIM115: use a `with` block, or `contextlib.ExitStack` if the handle outlives the scope.
   - C408, F841, E741, and the rest: fix by hand.
6. **`refactor(logging)`: the 25 `print(` calls** in `src` move to `logging.getLogger(__name__)`
   with a bracketed tag. Add any new tag to the allowlist in `logging_setup.py`. Keep the `print`
   calls in CLI entry points (`build.py`, `scripts/`, the `testplan/` runners).
7. Set `select` to include `"ANN"`, with `ANN401` (Any) enabled and the legacy modules listed under
   `per-file-ignores` (generated, same ledger). This stops new untyped code from being added while
   Phase 3 runs.

## Phase 2: Typed mixin hosts (single agent, branch `typing/p02-hosts`)
This phase fixes most of the ~4,830 `attr-defined` errors, plus the bulk of `has-type`, `union-attr`
and `call-arg`.

**Generator** `scripts/typing/gen_hosts.py`: an AST pass with no imports from the app. Its config is
a table of `(composite, host module path, part modules, runtime bases)`:

| Composite | Host file | Runtime base(s) |
|---|---|---|
| RAWImageViewer | `src/rawviewer_app/window/_host.py` | `QMainWindow` (+ SessionMixin members) |
| JustifiedGallery | `src/rawviewer_ui/widgets/gallery/_host.py` | its Qt base (read from `justified_gallery.py`) |
| ImageAdjustPanelWidget | `src/rawviewer_ui/widgets/adjust/_host.py` | `QWidget` |
| GpuImageView | `src/rawviewer_ui/widgets/gpu/_host.py` | its Qt base (`gpu_view.py`) |
| SemanticImageIndex | `src/search/engine/_host.py` | `object` |
| UnifiedImageProcessor | `src/processing/unified/_host.py` | `object` |
| ImageLoadManager | `src/image_load_manager/_host.py` | its base (`manager.py`) |

`RAWProcessor` is a plain inheritance chain and needs no host.

For each composite, the generator emits:
```python
# GENERATED by scripts/typing/gen_hosts.py: do not edit. Regenerate: python scripts/typing/gen_hosts.py
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from PyQt6.QtWidgets import QMainWindow
    # ...imports for every type referenced below, copied from the defining part modules
    class ViewerHost(QMainWindow):
        # attributes: from `self.x: T = ...` (first annotated assignment found in any part, __init__ first)
        current_file_path: str | None
        # class-level constants/signals declared on the composite or parts
        # methods: signature copied verbatim from the defining part, body `...`
        def _gallery_open_path(self, file_path: str) -> None: ...
else:
    ViewerHost = object
```
- **Attributes** come from the first *annotated* `self.x: T = ...`. The order is
  `LifecyclePart1.__init__` first, then other `__init__`/`_init_*` methods, then the first assignment
  anywhere.
- An attribute that is never annotated is **reported** as missing (`--report`). It is not typed as
  `Any`. `--check` fails while any are missing (Phase 2 step 3 removes them all).
- If two parts define the same method, the MRO winner is copied, and the generator warns.

**Wiring**: every part class inherits the host:
```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .._host import ViewerHost
else:
    ViewerHost = object  # the import is type-only; runtime MRO unchanged
class GalleryPart4(ViewerHost): ...
```
At runtime the MRO is identical, because `ViewerHost is object`. Under mypy, each part sees every
sibling member, and mypy's override check flags any part whose signature drifts from the host.

**Steps**
1. Write the generator and its unit tests (`tests/test_gen_hosts.py`, on a tiny fixture package).
2. Wire all part classes to their hosts. This is a mechanical libcst edit: change the bases and add
   the import block.
3. **Annotate every composite attribute at its first assignment.** RAWImageViewer has about 517 unique
   attributes; most are in `lifecycle_part1.py` (196 assignments) and `state.py`.
   - Type sources, in priority order:
     1. the RHS literal or constructor (`QTimer()` gives `QTimer`; `None` followed by a later
        assignment gives `X | None`);
     2. `scratch/attr_types.json`, the runtime dump from Phase 1 step 4;
     3. the usages.
   - The class-level `Any` declarations in `raw_image_viewer.py` (`image_manager`, `image_cache`,
     `preload_manager`, `gpu_view`, `gallery_justified`, `gallery_view`, `gallery_content_layout`,
     `thread_pool`) get their real classes: `ImageLoadManager`, `JustifiedGallery`, `GpuImageView`,
     `QThreadPool`, and so on.
   - Apply the same process to the other 6 hosts.
4. **`getattr`/`hasattr` cleanup.** `codemods/getattr_to_attr.py` rewrites `getattr(self, "x", d)`
   to `self.x`, and `hasattr(self, "x")` to `True`, simplifying the branch. It only does this when the
   generator proves that `x` is assigned unconditionally in `__init__` **before** the first `self.`
   method call in `__init__`.
   - All other cases are left alone: they can legitimately run before initialisation. Phase 3
     packets convert them by initialising `x` to a typed default in `__init__`, *if* that does not
     change behaviour.
   - The codemod writes a CSV of rewrites, so the result can be reviewed.
5. **Typing `parent_viewer` / `pv`** (47 uses): in widgets, annotate as `"RAWImageViewer"` through a
   `TYPE_CHECKING` import. The cycle exists only at type-check time, which is fine.
6. Gates:
   - `gen_hosts.py --check`;
   - `import_smoke.py`;
   - pytest;
   - `run_all.sh --fast`;
   - both app autotests;
   - `ratchet.py --update`. The `attr-defined` total should drop below about 1,000.

## Phase 3: Parallel work packets (one subagent each, branch `typing/p3-<id>` off `typing/upgrade`)
**Packet procedure (identical for every packet):**
1. Rebase on `typing/upgrade`, then run `gen_hosts.py`.
2. Delete your modules' lines from the legacy override list and from the ANN per-file-ignores.
3. Run mypy on your files (`mypy <files>`, using the project config) and fix every error with the
   recipes below.
   - For untyped functions, start from `monkeytype stub <module>` (the DB from Phase 1) and review it
     using the Phase 1 rules.
4. If you find a host attribute type that is wrong, fix it at the attribute's first assignment, even
   if that file belongs to another packet. Limit the change to that one line, then regenerate the
   host.
5. Gates: see Verification, then the "per-packet" list.
6. Make one PR into `typing/upgrade`.

Packets touch disjoint files, so they can run in parallel. The error counts are from the no-override
measurement, so they include the attr-defined errors that Phase 2 removes.

| ID | Files | Errors |
|---|---|---:|
| W1 | `rawviewer_app/window/mixins/display_part1-6`, `image_load_part1-3` | ~900 |
| W2 | `window/mixins/gallery_part1-5`, `navigation_part1-2`, `shortcuts_part1` | ~750 |
| W3 | `window/mixins/search_part1-4`, `metadata_part1-2`, `batch_part1`, `dialogs_part1` | ~700 |
| W4 | `window/mixins/adjust_part1-5`, `ui_build_part1-3`, `lifecycle_part1-2`, `settings_part1`, `status_part1-2` | ~950 |
| W5 | `window/mixins/misc_part1-4`, `external_part1-2`, `window/*.py` (non-mixin), `rawviewer_app/viewer/` | ~800 |
| U1 | `rawviewer_ui/widgets/gallery/*` | ~900 |
| U2 | `rawviewer_ui/widgets/gpu/*`, `widgets/adjust/*`, other `rawviewer_ui/widgets/*` | ~930 |
| U3 | `rawviewer_ui/overlays/*`, `rawviewer_ui/dialogs/*`, `rawviewer_app/{core,services,workers,processing}/*` | ~270 |
| C1 | `search/**` (engine, indexer, backends.py, queries.py) | ~340 |
| C2 | `processing/**` (unified, raw_decode), `enhanced_raw_processor/**` | ~420 |
| C3 | `image_load_manager/**`, `cache/**`, `common_image_loader/**`, `metadata/**` | ~560 |
| C4 | `raw_adjustments/**`, `core/**`, `bootstrap_pkg/**`, `platform_support/**`, `main.py`, `gallery_autotest.py`, `nav_autotest.py`, `pyi_rth_ssl_certs.py`, remaining top-level `src/*.py` | ~420 |
| T1 | `testplan/**`, `scripts/**` (not `scripts/typing`), `tests/**`, `build.py` | ~1370 |

For T1, about 940 of the errors are `no-untyped-call` into `src`, and they disappear as `src` gets
typed. Run T1 **last**. Its scope is about 70 test functions: add `-> None`, plus fixture/helper
signatures. The tests/scripts override is deleted, because strict applies everywhere.

## Fix recipes (by error code)
- **no-untyped-def / no-untyped-call:** annotate the callee. Never silence it at the call site.
  - Qt event handlers: `def mousePressEvent(self, event: QMouseEvent | None) -> None:`. PyQt6 stubs
    declare these parameters as optional, so the override must match.
  - Slots: use the signal's argument types.
  - Callbacks: use `Callable[[int], None]` with the real arity; `Callable[..., None]` is not enough.
- **attr-defined (left over after Phase 2):**
  - On `self`: declare the attribute in `__init__` with a type.
  - On a Qt object: use the correct PyQt6 enum path (`Qt.AlignmentFlag.AlignLeft`) or method name.
  - On a module: the import is wrong. Fix the import.
  - Monkey-patched instance methods (`w.mousePressEvent = ...`, 11 sites, plus `method-assign`): make
    them small subclasses or an `eventFilter`. Do not suppress them.
- **union-attr / has-type:** narrow with an early `if x is None: return` or `assert x is not None`.
  Use `assert` only where the invariant is real. If the value is never None after `__init__`, change
  the declared type and remove the None default.
- **no-any-return / warn_return_any:** the value comes from an untyped boundary. Either type the
  boundary, or for third-party calls, convert explicitly (`return float(arr.mean())`,
  `return bool(...)`, `return str(...)`) or validate with `isinstance` before returning.
- **type-arg:**
  - `np.ndarray` becomes `npt.NDArray[np.uint8]`, `npt.NDArray[np.float32]` and so on, with the real
    dtype. `npt.NDArray[np.generic]` only for truly dtype-agnostic helpers.
  - Bare `dict`/`list`/`tuple`/`Queue`/`Future` get their parameters.
- **var-annotated:** annotate the empty literal: `items: list[str] = []`.
- **assignment:** usually `x = None` followed by a later `x = Foo()`. Declare `x: Foo | None = None`.
  Never change the RHS to fit a wrong annotation.
- **call-overload / arg-type (PyQt):**
  - `QTimer.singleShot(ms, fn)` where `fn` returns a value: wrap it in a nested `def _cb() -> None`.
  - `int` vs enum flags: use the flag enum.
  - `float` where `int` is expected (geometry): use the `F` API (`QPointF`/`QRectF`) or an explicit
    `round()`. Check that the pixel result stays the same.
- **override:** match the base signature, including `| None` on Qt event params. If it is a real
  incompatibility, rename the method. Do not suppress it.
- **misc "Class cannot subclass X (has type Any)":** goes away once the import resolves. If the
  library has no stubs, it is a boundary: keep `allow_subclassing_any` for that case only.
- **name-defined / import-not-found:** a stale import; see the Phase 1 table.
- **unused-ignore:** delete the ignore.
- **Third-party boundary** (rawpy, cupy, torch, onnxruntime, objc, comtypes, etc.): put one small typed
  wrapper function in the module that owns the integration. The `Any` stays inside it, and callers get
  concrete types. If the stub gap cannot be avoided, use the exact `# type: ignore[code]  # <lib> stub gap: ...`
  form.
- **Modernisation, done in the same edit:**
  - `os.path` becomes `pathlib.Path` only in functions you are already rewriting. Do not sweep for it.
  - `try/except X: pass` becomes `contextlib.suppress`.
  - Use `zip(strict=True)` where the lengths must match.
  - Module constants become `Final`.
  - Value-only classes become `@dataclass(frozen=True, slots=True)`.

## Phase 4: Lock-in (single agent, branch `typing/p04-lockin`)
1. Remove the legacy override block and the ANN per-file-ignores, which must already be empty. Also
   remove the `gallery_autotest`/`nav_autotest`, platform and tests/scripts loosening overrides. What
   remains is the stub-less `ignore_missing_imports` list, plus any boundary-only
   `allow_subclassing_any`.
2. Add a `.github/workflows/quality.yml` workflow on windows-latest with Python 3.10 and 3.12. It runs:
   - `ruff check`;
   - `ruff format --check`;
   - `mypy`;
   - `gen_hosts.py --check`;
   - `lint_ignores.py`;
   - `ratchet.py`;
   - `import_smoke.py`;
   - `pytest`.
3. Add a `.pre-commit-config.yaml` with ruff, ruff-format and a local hook for `gen_hosts.py --check`.
4. Update CLAUDE.md:
   - the host-regeneration rule;
   - the `Any` boundary list;
   - the scripts in `scripts/typing/`;
   - the ledger is retired.
5. Update the python-coding skill to match.
6. Open one PR from `typing/upgrade` to `main`, with the before/after table in the PR body.

## Verification
**Per packet (required before the PR):**
1. `ruff check <files>` and `ruff format --check <files>`.
2. `mypy` (project config, which now covers the whole repo) reports **0 errors in the packet's
   files**. `ratchet.py` reports no increase anywhere, then run `ratchet.py --update`.
3. `lint_ignores.py` passes, and `git diff | grep -c "type: ignore"` shows no unexplained additions.
4. `import_smoke.py`, `pytest tests -q` and `testplan/run_all.sh --fast`.
5. For W\*/U\* packets (UI code), additionally run the app with `RAWVIEWER_GALLERY_AUTOTEST=1` and
   with `RAWVIEWER_NAV_AUTOTEST=1` on `D:\DCIM\15761121`. Compare the `[GALLERYTEST]`/nav summaries
   with a run on `typing/upgrade` HEAD: the same tiles must render, with no new tracebacks in the log.

**End to end (Phase 4):**
- The strict no-override command from Context reports **0** errors repo-wide.
- `rg "from __future__ import annotations"` finds nothing.
- `rg "type: ignore"` finds only the documented stub-gap form.
- The `Any` count is at or below the boundary budget.
- CI is green on Python 3.10 and 3.12.
- Manual smoke test through `launch_dev.bat`:
  - open a RAW folder;
  - gallery scroll;
  - single view;
  - adjust panel;
  - search;
  - batch export;
  - GPU decode with `RAWVIEWER_PREFER_GPU_DECODE=1`.
- Build the installer with `build.py` and launch the frozen exe, because runtime annotations can
  break PyInstaller hidden imports.

## Risks
- **Removing the future import evaluates annotations at runtime.** Mitigated by the quoting codemod,
  `import_smoke.py`, and the frozen-build check.
- **The stale-import fix enables GPU code paths that never ran before.** It is gated by the env flag
  and goes in its own revertible commit.
- **The `getattr` → attribute rewrite** is limited to attributes proven to be initialised early, and
  every rewrite is logged in a CSV.
- **Merge churn in the shared host files:** they are generated, so regenerate them and never merge by
  hand.
- **About 9k edits overall.** The ratchet makes each packet independently safe to merge and to revert.

## Measurement strategy: scoped and incremental mypy (applies to every packet and phase)

Full strict runs over the whole repo take minutes and were the main time sink. Rules:

1. **Never run the full repo during a packet.** Run mypy on the packet's own package only:
   `MYPYPATH=src:typings python -m mypy --config-file <strict.ini> --strict --python-version 3.10 --allow-subclassing-any --ignore-missing-imports --explicit-package-bases --follow-imports=silent <package-dir-or-file>`
   `--follow-imports=silent` keeps errors in dependencies out of the report, so the count is only the packet's own files.
2. **Use the incremental cache.** Mypy keeps `.mypy_cache`; re-runs on unchanged modules are seconds. Keep one cache per worktree. A fresh worktree starts cold, so copy an existing `.mypy_cache` into it before the first run (the cache is not committed).
3. **Use the daemon for iteration.** `dmypy run -- <same flags>` then `dmypy check <files>` re-checks only the changed modules. Use `dmypy stop` at the end of a packet.
4. **Full-repo runs happen once per phase, at the gate only:** the ledger (`scripts.typing.ledger`) and the ratchet. Packets report per-file counts from their scoped run, and the lead merges those numbers into the baseline.
5. **Scope the ledger.** Add `--scope <path>` to `scripts/typing/ledger.py` so the ratchet can compare one package against its baseline entries. This is a Phase 0 follow-up; until it exists, packets use rule 1 and skip the ratchet.
6. **Parallel packets use separate worktrees and caches**, so they do not contend for a shared `.mypy_cache`.
7. **Keep the venv free of scratch tools.** Dev tools go in the scratchpad, and `MYPYPATH` includes the pytest copy so `import pytest` is not `Any`.

## Bottom-up order: import layers (replaces the Phase 3 packet ordering where they conflict)

Generated by `python -m scripts.typing.import_tree --write` into `docs/typing/import_layers.md` and
`.json`. Regenerate after any import change; never edit those files by hand.

Why bottom-up: when a leaf is typed first, every importer sees real types, so the importer's
mypy run reports only its own errors. Typing top-down makes each importer report errors that
belong to an untyped leaf, and those errors get fixed twice.

Facts from the current graph (262 modules, 973 internal edges, including TYPE_CHECKING and
function-level imports, which count because mypy reads those modules too):

| Layer | Units | Modules | Notes |
|---|---:|---:|---|
| 0 | 56 | 56 | leaves: no internal dependencies |
| 1–4 | 36 | 36 | small utility layers |
| 5 | 2 | 61 | **one 61-module import cycle** (see below) |
| 6–11 | 53 | 53 | |
| 12–17 | 59 | 67 | the composites and app layer |

Two cycles exist: a 61-module unit around the image loader, cache, raw decode, adjustments and
processing; and an 8-module unit in `search/engine`.

**Cycle breaking, the first job before any layer work:** 53 of the 209 edges inside the big cycle
point into package `__init__` re-exports (`common_image_loader`, `image_cache`, `raw_adjustments`,
`enhanced_raw_processor`, `cache`). Importers should import from the defining submodule, for
example `from common_image_loader.loaders import X`, not from the package. That turns the cycle into a
DAG, so typing can go leaf-first. This is a behaviour-neutral refactor, but it touches import
lines in many files, so it is its own phase (P2a) with a full import_smoke run, and it is gated
by `import_tree` showing no cycle units, except the search cycle, which is handled separately.

### Work units and editing order

1. **P2a: cut cycles.** Redirect package-`__init__` imports to submodules. Re-run
   `import_tree --write`; the big cycle must be gone. Gate: import_smoke, pytest, and the
   `RAWVIEWER_GALLERY_AUTOTEST` run.
2. **Layer by layer, 0 → 17.** Within a layer, each unit is one packet. Units in the same layer
   share no imports, so they run in parallel in separate worktrees.
3. **Per-unit procedure:**
   a. `gen_hosts` for the unit's composite, if it has one (layers 12+ only).
   b. Annotate the unit's public functions and attributes, then its private ones.
   c. Scoped mypy on the unit's files (see the measurement strategy above). Errors must be zero
      in the unit before it is merged.
   d. Remove the unit's lines from the legacy override list, if any.
   e. Merge into `typing/upgrade` in layer order. A unit never merges before every unit it imports.
4. **Layer gate:** after each layer completes, run the ledger once. The ratchet must show no
   increase. Then `ratchet --update`.
5. **Composites last.** `RAWImageViewer` and the other 7 host-based composites sit at layers 12–17. Their
   hosts are generated from already-typed parts, so the host annotations come for free.

### Why this ordering saves time, concretely

- Layers 0–4 (92 modules) can each run fully in parallel with no waiting on importers, and they
  carry the highest fan-in: `core.config` (48 importers), `core.theme` (37), and the loader helpers.
  Typing those first cuts the `no-untyped-call` cascade (about 1,400 errors) before any importer
  is touched.
- A scoped mypy run per unit stays small, because `--follow-imports=silent` only checks the unit
  itself, and the cache already holds the typed layers below.
- Errors are assigned to the unit that owns them, so the per-layer ratchet points at one place
  when a count rises.

### Risks specific to this ordering

- **P2a touches many files at once.** Keep it to one agent with one commit per cycle-breaking
  package, so a bad import can be reverted alone.
- **Lazy function-level imports** in the cycle are the most likely source of runtime
  `ImportError` after P2a. import_smoke only imports modules, not function bodies, so run the
  autotests after P2a.
- The search cycle (8 modules) is typed as one unit at its own layer. Split it only if its size
  blocks the layer.
