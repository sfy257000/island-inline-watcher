"""Reject accidental private data, development files, or Playwright in a bundle."""
from pathlib import Path
import sys


def check_bundle(folder):
    folder = Path(folder)
    if not (folder / "IslandInlineWatcher.exe").is_file():
        raise ValueError("The Windows application executable is missing")
    private_dirs = {".venv", ".tools", "logs", ".browser-profile", ".browser-native-profile", "tests"}
    for path in folder.rglob("*"):
        parts = {part.lower() for part in path.relative_to(folder).parts}
        if parts & private_dirs or path.name.lower().startswith(".env"):
            raise ValueError(f"Private or development file in bundle: {path.relative_to(folder)}")
        if any("playwright" in part for part in parts):
            raise ValueError("Playwright must not be included in the runtime bundle")
    print("Bundle contains no private data or Playwright files.")


if __name__ == "__main__":
    check_bundle(sys.argv[1])
