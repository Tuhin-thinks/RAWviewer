"""Run mypy on multiple files and report errors."""
import subprocess
import sys

files = [
    "theme.py",
    "raw_hsl.py",
    "rawviewer_profile.py",
    "burst_grouping.py",
    "cache/memory.py",
    "perf_metrics.py",
    "mobileclip_download_progress.py",
    "metadata_backend.py",
    "cache/metadata.py",
    "cache/disk.py",
    "fast_raw_decode.py",
    "raw_edit_pipeline.py",
    "gpu_raw_processor.py",
    "color_calibration.py",
    "raw_tone_curve.py",
    "raw_transform.py",
    "image_histogram.py",
    "win_launcher.py",
]

for f in files:
    result = subprocess.run(
        ["uv", "run", "mypy", f"src/{f}", "--ignore-missing-imports", "--follow-imports=skip"],
        capture_output=True, text=True, timeout=60
    )
    errors = [l for l in result.stdout.split('\n') if 'error:' in l]
    if errors:
        print(f"\n=== {f} ({len(errors)} errors) ===")
        for err in errors:
            print(err)
    else:
        print(f"{f}: CLEAN")