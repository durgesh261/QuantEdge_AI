import re

with open('src/quantedge/market_data/ingestion.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the csv_hash function - the f-string was corrupted
old_fstring = "line = f\"{ts},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}\n\""
new_fstring = 'line = f"{ts},{row[\'open\']},{row[\'high\']},{row[\'low\']},{row[\'close\']},{row[\'volume\']}\\n"'

content = content.replace(
    'line = f"{ts},{row[\'open\']},{row[\'high\']},{row[\'low\']},{row[\'close\']},{row[\'volume\']}\n"',
    'line = f"{ts},{row[\'open\']},{row[\'high\']},{row[\'low\']},{row[\'close\']},{row[\'volume\']}\\n"'
)

# Also fix the second occurrence if it exists
content = content.replace(
    'line = f"{ts},{row[\'open\']},{row[\'high\']},{row[\'low\']},{row[\'close\']},{row[\'volume\']}\n"',
    'line = f"{ts},{row[\'open\']},{row[\'high\']},{row[\'low\']},{row[\'close\']},{row[\'volume\']}\\n"'
)

with open('src/quantedge/market_data/ingestion.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed')