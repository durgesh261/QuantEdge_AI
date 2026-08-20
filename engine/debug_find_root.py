from pathlib import Path

def _find_repo_root() -> Path:
    """Find the repository root by looking for a marker file."""
    current = Path(__file__).resolve()
    print(f"DEBUG: current = {current}")
    for i, parent in enumerate([current] + list(current.parents)):
        print(f"  Level {i}: {parent}")
        has_pyproject = (parent / "pyproject.toml").exists()
        has_git = (parent / ".git").exists()
        print(f"    pyproject.toml: {has_pyproject}, .git: {has_git}")
        if has_git or has_git:
            print(f"  -> MATCH! Returning: {parent}")
            return parent
    print("No marker found, using fallback")
    return Path(__file__).resolve().parent.parent.parent.parent

if __name__ == "__main__":
    result = _find_repo_root()
    print(f"Result: {result}")