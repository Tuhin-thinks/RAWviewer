"""Run mypy on specific files to see remaining errors."""
import subprocess
import sys

files_to_check = [
    "bootstrap_pkg/__init__.py",
    "bootstrap_pkg/helpers.py",
    "bootstrap_pkg/gui.py",
    "cache/disk.py",
    "cache/metadata.py",
    "cache/memory.py",
    "color_calibration.py",
    "common_image_loader/__init__.py",
    "common_image_loader/edr.py",
    "common_image_loader/exif_display.py",
    "common_image_loader/helpers.py",
    "common_image_loader/loaders.py",
    "common_image_loader/mipmap.py",
    "common_image_loader/runtime.py",
    "display_brightness.py",
    "enhanced_raw_processor/exif_extractor.py",
    "enhanced_raw_processor/extraction.py",
    "enhanced_raw_processor/optimized_processor.py",
    "enhanced_raw_processor/thumbnail_extractor.py",
    "enhanced_raw_processor/enhanced_processor.py",
    "enhanced_raw_processor/preload_manager.py",
    "exposure_clipping.py",
    "fast_raw_decode.py",
    "gallery_autotest.py",
    "gpu_gl_bridge.py",
    "gpu_raw_processor.py",
    "image_histogram.py",
    "image_load_manager/__init__.py",
    "image_load_manager/helpers.py",
    "image_load_manager/manager.py",
    "image_load_manager/worker.py",
    "metadata_backend.py",
    "mobileclip_download_progress.py",
    "nav_autotest.py",
    "onnx_scunet.py",
    "perf_metrics.py",
    "processing/unified/helpers.py",
    "processing/unified/__init__.py",
    "raw_adjustments/__init__.py",
    "raw_adjustments/apply.py",
    "raw_adjustments/checks.py",
    "raw_adjustments/saved_edits.py",
    "raw_adjustments/xmp_read.py",
    "raw_adjustments/xmp_write.py",
    "raw_auto_adjust.py",
    "raw_chroma_denoise.py",
    "raw_detail_enhance.py",
    "raw_dodge_burn.py",
    "raw_edit_pipeline.py",
    "raw_effects.py",
    "raw_hsl.py",
    "raw_lens_correction.py",
    "raw_lut.py",
    "raw_pv2012.py",
    "raw_spot_heal.py",
    "raw_tone_curve.py",
    "raw_tone_recovery.py",
    "raw_transform.py",
    "rawrefinery_denoise.py",
    "rawviewer_profile.py",
    "search/indexer/_config.py",
    "search/indexer/_rest.py",
    "search/queries.py",
    "startup_splash.py",
    "theme.py",
    "windows_share.py",
    "burst_grouping.py",
    "fast_raw_decode.py",
]

for f in sorted(files_to_check):
    result = subprocess.run(
        ["uv", "run", "mypy", f"src/{f}", "--ignore-missing-imports", "--follow-imports=skip"],
        capture_output=True, text=True, timeout=60
    )
    errors = [l for l in result.stdout.split('\n') if 'error:' in l]
    if errors:
        print(f"\n=== {f} ({len(errors)} errors) ===")
        for err in errors[:5]:
            print(err)
        if len(errors) > 5:
            print(f"  ... ({len(errors) - 5} more)")
    else:
        print(f"  {f}: CLEAN")