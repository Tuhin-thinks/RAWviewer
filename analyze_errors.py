"""Analyze mypy errors from the saved output."""
import re
from collections import Counter

with open('mypy_output.txt') as f:
    text = f.read()

# Find type-arg errors
type_arg_errors = re.findall(r'Missing type arguments for generic type "([^"]+)"', text)
print("=== type-arg errors ===")
for t, c in Counter(type_arg_errors).most_common():
    print(f'{c:>5} {t}')

# Find unused-ignore errors
unused_ignore_errors = re.findall(r'src/([^:]+):(\d+): error: Unused "type: ignore" comment', text)
print("\n=== files with unused-ignore ===")
files_with = Counter(m[0] for m in unused_ignore_errors)
for f, c in files_with.most_common():
    print(f'{c:>5} {f}')