"""Test that helpers.py properly re-exports disk_preview_max_edge."""
import sys
import importlib.util
import os

src = r'C:\Users\tuhin\Downloads\RAWviewer\src'
sys.path.insert(0, src)

# Import helpers directly bypassing __init__
spec = importlib.util.spec_from_file_location(
    'processing.unified.helpers',
    os.path.join(src, 'processing', 'unified', 'helpers.py')
)
mod = importlib.util.module_from_spec(spec)
sys.modules['processing.unified.helpers'] = mod
spec.loader.exec_module(mod)

for name in ['disk_preview_max_edge', 'memory_preview_max_edge', 'EXIFExtractor',
             'OptimizedRAWProcessor', 'ThumbnailExtractor', 'get_image_cache',
             'image_covers_sensor_resolution', 'is_raw_file']:
    ok = hasattr(mod, name)
    print(f'  {ok} {name}')