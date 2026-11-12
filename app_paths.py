"""Keep installed user data outside the application and its bundled resources."""
import os
from pathlib import Path
import sys


def data_directory(source_file):
    override = os.environ.get("ISLAND_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "IslandInlineWatcher"
    return Path(source_file).resolve().parent
