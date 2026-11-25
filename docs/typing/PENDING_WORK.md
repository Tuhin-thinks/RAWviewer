# Typing upgrade: pending work (handover)

Status snapshot for branch `typing/upgrade`. Written when the scope was reduced to what is
already implemented. Nothing in this file is a mainstream version change.

## Scope decision

The typing upgrade is paused at the import-cycle phase (P2b). The user asked to:

1. Ship what is implemented: verify it, run the app, open a PR, merge it.
2. Publish a **test pre-release** only. No mainstream version bump.
3. Keep the remaining plan documented here so it can be resumed later.

## What is done (P2a and P2b, on `typing/upgrade`)

Cycle breaks are done in bottom-up order. Each step is verified by the import smoke test, the
name checker, and the cycle report. Ruff gates apply where logic changed.

| Commit | Change |
|---|---|
| `a10f9d7` | `is_raw_file` imported from its defining module |
| `8dd6c56`, `3d7f552` | linear-light pipeline split into `raw_adjustments/linear.py` |
| `745d78b` | banding helpers in `raw_adjustments/banding.py` |
| `ac99c8d` | gamma LUT in `processing/raw_decode/gamma.py`, sRGB decode in `raw_adjustments/srgb.py` |
| `ce98117` | shared adjustment helpers in `raw_adjustments/shared.py` |
| `ab26045` | rawpy lock moved to `gil.py`; `scunet` uses `core.errors` |
| `e522c76` | camera white-balance helpers in `processing/raw_decode/camera_wb.py` |
| `0a352cf` | size/memory helpers in leaf module `src/cache_limits.py`; `recommended_preview_cache_items` moved to `cache/memory.py` |
| (this change) | GPU helpers `_download_device_rgb`, `_workspace_for`, `_GpuIspWorkspace` moved from `gpu_processor` to `gpu_bridge`, which removes the 2-module cycle |

Cycle state after these steps:

- Largest cycle: **40 modules** (was 61 at the start of P2b, 42 at `d36cb89`).
- `search.engine` cycle: **8 modules** (out of scope for P2b).
- `gpu_bridge` / `gpu_processor` cycle: **resolved**.
- Import smoke: **269/269** modules.
- Name checker (every `from src-module import name`, including function bodies): **0 unresolved**.

## Pending: P2b remainder (40-module cycle)

Members (from `scripts/typing/import_tree.py`):
`cache`, `cache.disk`, `cache.image_cache` (+ `.core`, `.full_exif_util`, `.thumb_grid_preview`),
`cache.memory`, `cache.metadata`, `common_image_loader` (+ `.edr`, `.exif_display`, `.helpers`,
`.loaders`, `.mipmap`, `.runtime`), `core.profile`, `enhanced_raw_processor.exif_extractor`,
`enhanced_raw_processor.extraction`, `enhanced_raw_processor.optimized_processor`,
`enhanced_raw_processor.thumbnail_extractor`, `image_cache`, `image_load_manager` (helpers,
manager and parts, worker), `processing.raw_decode.fast`, `processing.unified` (helpers, processor,
parts 1–7), `raw_adjustments.saved_edits`, `raw_adjustments.xmp_read`, `raw_adjustments.xmp_write`,
`bootstrap_pkg.torch`.

It has 126 internal edges. The hubs are:

- `common_image_loader` (18 incoming edges, mostly from `processing.unified`, `image_load_manager`,
  `rawviewer_ui`, `search.indexer`)
- `enhanced_raw_processor.extraction` (13)
- `image_cache` (12, the star-import facade `src/image_cache.py`)
- `image_load_manager.helpers` (10)
- `raw_adjustments.xmp_read` (8)
- `processing.unified.helpers` (8)

Suggested order, smallest risk first:

1. Replace the `src/image_cache.py` star facade with explicit re-exports. Repoint internal importers
   to defining modules so edges go to the leaves.
2. Move `enhanced_raw_processor.extraction` helpers that `cache.image_cache.core` needs into a leaf
   module (the same closure method as `cache_limits`).
3. Break `common_image_loader` ↔ `image_cache` by repointing `cache` consumers to
   `common_image_loader.helpers` or a new leaf.
4. Break `core.profile` ↔ cache (profile flags are read inside cache code). Consider passing the
   flags as arguments, or moving the read to the caller, as was done for
   `recommended_preview_cache_items`.
5. Re-run `python -m scripts.typing.import_tree`, then the name checker, then import smoke, after
   each step.

Method notes:

- Function-level imports count as edges in the graph, but import smoke does not execute function
  bodies. Check every repointed name by name, as the name checker does.
- When `move_defs.py` re-exports a moved name from its source module, it can add a back-edge. For
  a cycle break, verify the re-export does not recreate the cycle.
- Ruff `--fix` can delete re-export imports that look unused. Use one import per name with
  `# noqa: F401  # re-export`, and re-run the name checker after any `--fix`.

## Pending: `search.engine` cycle (8 modules)

Out of scope for P2b. It is a separate package-level cycle (`search.engine` and its parts). Plan it
as its own batch after the 40-module cycle.

## Pending: typing layers P3 onward

Mypy strict typing, bottom-up. Use `docs/typing/import_layers.md` for the order. Do not start a
layer until the cycles below it are broken. Run mypy on single packages, not the whole tree, to keep
runs fast (see `docs/TYPING_UPGRADE_PLAN.md`).

## Pending: branches and worktrees (keep until the user confirms)

The user asked to keep every worktree and branch until they confirm completion:

- `typing/p02-hosts-w2` (worktree `agent-ad87bea26d951358a`, parked, ledger gate not run)
- `refactor/p2a-redirects-b1`..`b4`, `refactor/p2a-cycles`, `typing/p01-codemods`,
  `typing/p02-hosts`, `fix/stale-imports`: worktrees still present. Merge state not re-verified in this handover; confirm before deleting.

## Known issues (not introduced by this work)

- Runtime warning on launch in the lite profile:
  `[GALLERY] Could not pre-seed semantic metadata: Error binding parameter 133: type 'method' is not supported`
  (`src/rawviewer_app/window/mixins/gallery_part2.py`, around line 690). The SQL bind code is the same
  as on `main` (the branch diff is formatting and imports only). It is a pre-existing bug: a method
  object is being bound as a SQL parameter. Fix separately; it is caught and logged, so the app keeps
  running.

## Verification record for this handover

- `python -m pytest tests -q`: 15 passed.
- `python -m scripts.typing.import_smoke` (offscreen Qt): 269/269.
- `python -m ruff check` on the touched cache, GPU and unified files: passed.
- `scripts\Launch\windows\launch_dev.bat lite`, three runs of about 60 seconds each: no tracebacks.
  The only error was the pre-existing warning above.
- mypy: not run for these trivial moves, per the plan rule that moves skip ruff/mypy/pytest gates
  unless logic changes. Compare error counts before resuming any typing layer.
