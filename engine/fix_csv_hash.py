import re

with open('src/quantedge/market_data/ingestion.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '''def csv_hash(csv_path: Path) -> str:
    if not csv_path.exists():
        return ""
    h = hashlib.sha256()
    with open(csv_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_metadata(meta_path: Path) -> Dict[str, Any]:'''

new = '''def csv_hash(csv_path: Path) -> str:
    """Compute SHA-256 hash of CSV using row-based method (CRLF-independent).
    
    Hash is computed from parsed rows with Unix timestamps, making it
    independent of line ending style (CRLF vs LF) and timestamp format.
    """
    if not csv_path.exists():
        return ""
    h = hashlib.sha256()
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(datetime.fromisoformat(row["timestamp"])
                     .replace(tzinfo=timezone.utc).timestamp())
            line = f"{ts},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}\n"
            h.update(line.encode())
    return h.hexdigest()


def load_metadata(meta_path: Path) -> Dict[str, Any]:
'''

content = content.replace(old, new)

with open('src/quantedge/market_data/ingestion.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")