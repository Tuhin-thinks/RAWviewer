"""Test importing through processing.unified which triggers __init__.py."""
import sys
import os

src = r'C:\Users\tuhin\Downloads\RAWviewer\src'
sys.path.insert(0, src)

# This triggers processing.__init__ -> processing.unified.__init__
# which does from .helpers import (...disk_preview_max_edge...)
try:
    from processing.unified.helpers import disk_preview_max_edge
    print(f'OK via processing.unified.helpers: {disk_preview_max_edge}')
except Exception as e:
    print(f'FAIL: {e}')

try:
    import processing.unified
    from processing.unified import disk_preview_max_edge  
    print(f'OK via processing.unified: {disk_preview_max_edge}')
except Exception as e:
    print(f'FAIL via processing.unified: {e}')