"""Compare __all__ in __init__.py with what's defined or imported in helpers.py."""
import os, ast

src = r'C:\Users\tuhin\Downloads\RAWviewer\src\processing\unified'

# Parse __init__.py
with open(os.path.join(src, '__init__.py'), encoding='utf-8') as f:
    init_tree = ast.parse(f.read())

# Get names from from .helpers import (...)
imported_from_helpers = set()
for node in ast.walk(init_tree):
    if isinstance(node, ast.ImportFrom):
        mod = node.module
        lvl = node.level if node.level else 0
        if (lvl == 1 and mod == 'helpers') or (mod and mod.endswith('.helpers')):
            for alias in node.names:
                imported_from_helpers.add(alias.name)

# Get __all__ list
all_names = set()
for node in ast.walk(init_tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == '__all__':
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant):
                        all_names.add(elt.value)

print("=== Names in __all__ but NOT imported from .helpers ===")
missing = sorted(all_names - imported_from_helpers)
for n in missing:
    print(f'  {n}')

print(f'\nTotal missing: {len(missing)}')

# Now check which of these are actually defined in helpers.py
with open(os.path.join(src, 'helpers.py'), encoding='utf-8') as f:
    helpers_tree = ast.parse(f.read())

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
            helpers_defs.add(alias.asname or alias.name)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            helpers_defs.add(alias.asname or alias.name)

print("\n=== Which of those are defined in helpers.py? ===")
for n in missing:
    if n in helpers_defs:
        print(f'  {n} -> YES, in helpers.py')
    else:
        print(f'  {n} -> NOT in helpers.py')