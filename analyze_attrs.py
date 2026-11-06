"""Find attr-defined attributes for specific files."""
import re

with open('mypy_output.txt') as f:
    text = f.read()

target = 'full_exif_util.py'
lines = [l for l in text.split('\n') if target in l and 'attr-defined' in l]
attrs = set()
for l in lines:
    m = re.search(r'has no attribute "([^"]+)"', l)
    if m:
        attrs.add(m.group(1))
    m = re.search(r'Cannot determine type of "([^"]+)"', l)
    if m:
        attrs.add(m.group(1))

print(f"Attributes referenced by {target}:")
for a in sorted(attrs):
    print(f"    {a}: Any")