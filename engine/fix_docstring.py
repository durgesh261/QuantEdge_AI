with open(r'C:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI\engine\tests\test_structure_luxalgo.py', 'r') as f:
    lines = f.readlines()

# Fix the docstring - make it a proper multi-line docstring
lines[404] = '        """Bearish break with prev_trend=BULLISH -> CHOCH.\n'
lines[405] = '\n'
lines[406] = '        LuxAlgo sequence for CHOCH on bearish break:\n'
lines[407] = '        1. Bearish leg (creates initial pivot_high at first transition)\n'
lines[408] = '        2. Bullish leg (creates pivot_low at transition from bearish)\n'
lines[409] = '        3. Bullish break above pivot_high -> BOS (sets trend=BULLISH)\n'
lines[410] = '        4. Bearish leg (creates new pivot_high at transition from bullish)\n'
lines[411] = '        5. Bearish break below pivot_low -> CHOCH (prev_trend=BULLISH)\n'
lines[412] = '        """\n'

with open(r'C:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI\engine\tests\test_structure_luxalgo.py', 'w') as f:
    f.writelines(lines)
print('Fixed docstring')