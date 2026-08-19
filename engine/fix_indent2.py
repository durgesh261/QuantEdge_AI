with open('tests/test_structure_luxalgo.py', 'r') as f:
    lines = f.readlines()

# The function at line 92 (1-indexed) = line 91 (0-indexed) is at module level
# It should be inside TestLuxAlgoLegTransitions class (which ends at line 118)
# Let's find the exact range of this function
# It starts at line 91 and ends before the next class at line 118

start_line = 91  # 0-indexed
end_line = 116   # 0-indexed (the line before "class TestLuxAlgoPivotTiming")

print(f"Line 91: {lines[91].strip()}")
print(f"Line 116: {lines[116].strip()}")

# Indent these lines by 4 spaces
for i in range(start_line, end_line + 1):
    if lines[i].strip():  # Only indent non-empty lines
        lines[i] = '    ' + lines[i]

with open('tests/test_structure_luxalgo.py', 'w') as f:
    f.writelines(lines)

print('Fixed indentation for test_bearish_to_bullish_creates_pivot_low_at_low_size')