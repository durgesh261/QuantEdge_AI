with open('tests/test_structure_luxalgo.py', 'r') as f:
    lines = f.readlines()

# Find the problematic function (at module level, should be in TestLuxAlgoLegTransitions)
# The function starts at line 218 (0-indexed: 217)
# We need to indent lines 217-282 (inclusive) by 4 spaces

# First, find where TestLuxAlgoLegTransitions ends and the next class begins
# The function at line 217 should be inside TestLuxAlgoLegTransitions

# Let's find the end of TestLuxAlgoLegTransitions class (where the next class starts)
# Next class is TestLuxAlgoCrossedState at line 319 (0-indexed: 318)

# We need to indent lines 217-282 (the function that was incorrectly placed at module level)
# to be inside TestLuxAlgoLegTransitions class

# The function starts at line 218 (1-indexed) = line 217 (0-indexed)
# It ends before "def test_break_index_is_break_candle_not_pivot" at line 284 (1-indexed) = line 283 (0-indexed)

# Let's fix the indentation
start_line = 217  # 0-indexed (line 218 in 1-indexed)
end_line = 282    # 0-indexed (line 283 in 1-indexed, the line before "def test_break_index...")

# Check the content to make sure we have the right lines
print(f"Line 217: {lines[217].strip()}")
print(f"Line 282: {lines[282].strip()}")

# Indent these lines by 4 spaces
for i in range(start_line, end_line + 1):
    if lines[i].strip():  # Only indent non-empty lines
        lines[i] = '    ' + lines[i]

with open('tests/test_structure_luxalgo.py', 'w') as f:
    f.writelines(lines)

print('Fixed indentation')