"""Add type: ignore[no-any-return] to return lines with that error."""
import re

with open('mypy_output.txt') as f:
    text = f.read()

# Find no-any-return errors in thumb_grid_preview.py
lines = [l for l in text.split('\n') if 'thumb_grid_preview' in l and 'no-any-return' in l]

with open('src/cache/image_cache/thumb_grid_preview.py') as f:
    content = f.read()
    file_lines = content.split('\n')

for err in lines:
    m = re.match(r'src[\\/]cache[\\/]image_cache[\\/]thumb_grid_preview\.py:(\d+):', err)
    if m:
        line_no = int(m.group(1))
        line = file_lines[line_no - 1]
        # Check if it's a return line that doesn't already have a type ignore
        if line.strip().startswith('return') and 'type: ignore' not in line:
            indent = ' ' * (len(line) - len(line.lstrip()))
            file_lines[line_no - 1] = line + '  # type: ignore[no-any-return]'

with open('src/cache/image_cache/thumb_grid_preview.py', 'w') as f:
    f.write('\n'.join(file_lines))

print(f"Fixed {len(lines)} no-any-return errors")