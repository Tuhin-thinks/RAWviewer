"""Check what names each file in processing/unified imports from .helpers."""
import os, ast

src = r'C:\Users\tuhin\Downloads\RAWviewer\src\processing\unified'
files = ['__init__.py', 'unified_part1.py', 'unified_part3.py',
         'unified_part4.py', 'unified_part5.py', 'unified_part6.py', 'unified_part7.py']

all_wanted = set()
by_file = {}
for f in files:
    path = os.path.join(src, f)
    with open(path, encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    wanted = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module
            lvl = node.level if node.level else 0
            # relative import like from .helpers import X
            is_helpers = (lvl == 1 and mod == 'helpers') or (mod and mod.endswith('.helpers'))
            if is_helpers:
                for alias in node.names:
                    name = alias.name
                    wanted.append(name)
                    all_wanted.add(name)
    by_file[f] = wanted

# Now check helpers.py
helpers_path = os.path.join(src, 'helpers.py')
with open(helpers_path, encoding='utf-8') as fh:
    helpers_tree = ast.parse(fh.read())

helpers_defs = set()
for node in ast.walk(helpers_tree):
    if isinstance(node, ast.FunctionDef):
        helpers_defs.add(node.name)
    elif isinstance(node, ast.ClassDef):
        helpers_defs.add(node.name)
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if isinstance(t, ast.Name):
                helpers_defs.add(t.id)
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            as_name = alias.asname or alias.name
            helpers_defs.add(as_name)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            as_name = alias.asname or alias.name
            helpers_defs.add(as_name)

print("=== Imports from .helpers per file ===")
for f in files:
    wanted = by_file[f]
    missing = [n for n in wanted if n not in helpers_defs]
    if missing:
        print(f'\n{f}: MISSING:')
        for m in missing:
            print(f'  - {m}')
    else:
        print(f'\n{f}: all OK')

print(f'\n\nTotal unique imported from .helpers: {len(all_wanted)}')
print(f'Total defined/re-exported in helpers.py: {len(helpers_defs)}')
missing_all = [n for n in sorted(all_wanted) if n not in helpers_defs]
if missing_all:
    print(f'\nGLOBAL MISSING:')
    for m in missing_all:
        print(f'  {m}')
else:
    print('\nAll names match!')