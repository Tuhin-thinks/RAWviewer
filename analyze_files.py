"""Count mypy errors per file."""
import re
from collections import Counter

with open('mypy_output.txt') as f:
    text = f.read()

# Parse error lines - use raw string to avoid escape issues
error_lines = [l for l in text.split('\n') if 'error:' in l]

# Count per file
file_counts = Counter()
for line in error_lines:
    m = re.match(r'src[\\/:]([^:]+):\d+:', line)
    if m:
        file_counts[m.group(1)] += 1

# Print sorted by count (ascending)
for f, c in sorted(file_counts.items(), key=lambda x: x[1]):
    print(f'{c:>5} {f}')

print(f'\nTotal files: {len(file_counts)}')
print(f'Total errors: {sum(file_counts.values())}')
print(f'Average: {sum(file_counts.values()) / len(file_counts):.1f} errors/file')