import quantedge.market_data.ingestion as m
print("__file__:", m.__file__)
print("REPO_ROOT:", m.REPO_ROOT)

from pathlib import Path
f = Path(m.__file__)
print("parent:", f.parent)
print("parent.parent:", f.parent.parent)
print("parent.parent.parent:", f.parent.parent.parent)
print("parent.parent.parent.parent:", f.parent.parent.parent.parent)