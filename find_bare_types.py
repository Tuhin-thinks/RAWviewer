"""Find lines with bare `list`, `dict`, `set`, etc. used as type annotations."""
import re
import glob
import ast

# Find all .py files in src/
py_files = glob.glob('src/**/*.py', recursive=True)

# Regex patterns for bare types in function signatures
patterns = {
    'list': r'(?<![.\w])list(?=\s*\)|\s*,|\s*:|\s*=|\s*\]|\s*\)|\s*\)\s*->)',
}

for fpath in sorted(py_files):
    with open(fpath) as f:
        content = f.read()
    for line_no, line in enumerate(content.split('\n'), 1):
        # Skip comments and strings
        clean = line.split('#')[0]
        # Look for type annotations
        for typ, pat in patterns.items():
            if re.search(pat, clean) and 'list[' not in clean and 'list[' not in clean:
                print(f"{fpath}:{line_no}: {line.strip()[:80]}")