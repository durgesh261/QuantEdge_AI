from pathlib import Path

f = Path(r'C:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI\engine\src\quantedge\market_data\ingestion.py').resolve()
print('Resolved file:', f)
for i, parent in enumerate([f] + list(f.parents)):
    print(f'  Level {i}: {parent}')
    print(f'    pyproject.toml: {(parent / "pyproject.toml").exists()}')
    print(f'    .git: {(parent / ".git").exists()}')