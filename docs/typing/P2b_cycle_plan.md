# P2b: break the remaining import cycle (plan)

Generated from `import_tree` after P2a (commit a10f9d7). The big cycle is 54 modules, and 23
feedback edges must be removed to make it acyclic. The search/engine 8-module cycle is separate.

Redirects alone cannot remove these edges: each one imports a name whose definition sits
inside the cycle, so the name has to move to a lower module first.

## Edges and the fix each needs

| # | Edge (importer -> target) | Names used | Proposed fix |
|---|---|---|---|
| 1 | cache.image_cache -> cache.image_cache.core | ImageCacheCore | Move ImageCacheCore into a leaf module; core re-exports |
| 2 | cache.image_cache -> cache.image_cache.full_exif_util | ImageCacheFullExifUtil | Same pattern as 1 |
| 3 | common_image_loader -> common_image_loader.loaders | loaders (module) | Make the package init import loaders lazily |
| 4 | common_image_loader.exif_display -> enhanced_raw_processor.exif_extractor | EXIFExtractor, get_image_cache | Move get_image_cache to a leaf; pass EXIFExtractor via a callable |
| 5 | common_image_loader.helpers -> raw_adjustments.xmp_read | resolve_xmp_path, sidecar_adjustments_enabled, RAW_FILE_EXTENSIONS | Move RAW_FILE_EXTENSIONS and the sidecar check into a leaf |
| 6 | enhanced_raw_processor.exif_extractor -> raw_adjustments.xmp_write | RAW_EXIF_SENSOR_META_VER, _parse_rating_value, etc. | Move these constants and pure parsers to a leaf module |
| 7 | enhanced_raw_processor.extraction -> common_image_loader | backend, decode_embedded_jpeg_bytes, rawpy_imread_warm | Move the decode helpers to a leaf |
| 8 | image_cache -> cache | package re-export | Replace the re-export with explicit submodule imports |
| 9 | image_load_manager.helpers -> image_load_manager.manager | ImageLoadManager, ImageLoadTask | Lazy import inside the one function that needs it |
| 10 | image_load_manager.manager_part1 -> image_load_manager.worker | ImageLoadTask, Priority, env helpers | Move ImageLoadTask/Priority to a leaf `types` module |
| 11 | image_load_manager.manager_part2 -> image_load_manager.worker | ImageLoadTask, Priority, is_raw_file | Same as 10 |
| 12 | image_load_manager.manager_part3 -> image_load_manager.worker | ImageLoadWorker, Priority | Same as 10 |
| 13 | processing.raw_decode.fast -> bootstrap_pkg.torch | torch, detect_gpu_backend | Lazy import at the call site; GPU sys.modules probes stay untouched |
| 14 | processing.raw_decode.fast -> processing.raw_decode.gpu_processor | try_gpu_decode_from_unpacked | Lazy import at the call site |
| 15 | processing.raw_decode.gpu_bridge -> processing.raw_decode.gpu_processor | _download_device_rgb, _workspace_for | Move these helpers to a leaf |
| 16 | processing.raw_decode.scunet -> raw_adjustments.pipeline | urlretrieve, ExportCancelled | Use core.errors directly; urlretrieve from urllib |
| 17 | processing.unified.processor -> unified_part2/3/5/7 | UnifiedPart* mixins | Mixin composition, not a real cycle; mark as a composition edge |
| 18 | processing.unified.unified_part1 -> processing.unified.helpers | EXIFExtractor, OptimizedRAWProcessor, _ADJUSTED* | Move the shared state to helpers' leaf |
| 19 | processing.unified.unified_part4 -> processing.unified.helpers | _rawpy_global_lock, decode_half_from_unpacked, etc. | Same as 18 |
| 20 | processing.unified.unified_part6 -> processing.unified.helpers | image_covers_sensor_resolution, etc. | Same as 18 |
| 21 | raw_adjustments.apply -> raw_adjustments.pipeline | linear_to_display_uint8, process_linear_edit_buffer | Move process_linear_edit_buffer and linear_to_display_uint8 into a leaf `linear` module (main code move) |
| 22 | raw_adjustments.lut -> raw_adjustments.pipeline | process_linear_edit_buffer, linear_to_display_uint8 | Same as 21 |
| 23 | raw_adjustments.saved_edits -> raw_adjustments.pipeline | linear_to_display_uint8, process_linear_edit_buffer | Same as 21 |

## Why this is not yet started

- Edges 21–23 need `process_linear_edit_buffer`, a large function in `pipeline.py` that depends
  on most of the adjustment modules. Moving it is a real code move, and the 15 existing tests do
  not cover the adjustment pipeline well enough to confirm behaviour.
- Edges 3, 9–12 and 13–14 change image-loading and GPU-decode import timing. Lazy imports can
  change import-time side effects, which matters for the GPU probes.
- Edge 17 is a mixin composition pattern. Breaking it would change the typing approach, not just imports.

## Proposed order

1. Leaf extractions with no behaviour change: edges 5, 6, 7, 15, 16 (pure helpers and constants).
2. Enum/dataclass leaf for `ImageLoadTask` and `Priority`: edges 10–12.
3. Lazy imports where the import is already inside a function: edges 9, 13, 14.
4. Re-export removal: edges 1, 2, 8.
5. Adjustment pipeline split (edges 21–23): only after the adjustment path has regression tests.
6. Mixin composition (edges 4, 17–20): decide with the user whether a mixin cycle is acceptable.

Each step needs the gates: import_smoke, pytest, ruff, and import_tree showing the cycle
shrinking. Decisions needed from the user are listed in the final message.
