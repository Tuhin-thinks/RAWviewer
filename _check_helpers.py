"""Check which names are defined in processing/unified/helpers.py."""
import ast, os

src = r'C:\Users\tuhin\Downloads\RAWviewer\src'
path = os.path.join(src, 'processing', 'unified', 'helpers.py')
with open(path, encoding='utf-8') as f:
    tree = ast.parse(f.read())

top_level = set()
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        top_level.add(node.name)
    elif isinstance(node, ast.ClassDef):
        top_level.add(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                top_level.add(t.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        top_level.add(node.target.id)
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            as_name = alias.name if alias.asname is None else alias.asname
            top_level.add(as_name)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            as_name = alias.name if alias.asname is None else alias.asname
            top_level.add(as_name)

wanted = [
    '_LIBRAW_UNSUPPORTED_PATHS', 'EXIFExtractor', 'OptimizedRAWProcessor',
    'ThumbnailExtractor', 'array_matches_exif_display', 'cap_working_edit_base',
    'decode_raw_file', 'get_image_cache', 'image_covers_sensor_resolution',
    'is_raw_file', 'load_pixmap_safe', 'memory_preview_max_edge',
    'prune_libraw_unsupported_paths', 'release_adjusted_full_cache',
    'release_edit_base_cache', 'release_unpacked_raw_cache',
    'trim_edit_base_lru', 'use_full_embedded_raw_preview',
    'use_libraw_consistent_preview_first',
]
for w in wanted:
    status = 'OK' if w in top_level else 'MISSING'
    print(f'  {status} {w}')