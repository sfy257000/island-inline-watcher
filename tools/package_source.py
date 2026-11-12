"""Package only tracked source files and hash the exact distribution assets."""
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {".venv", ".tools", "logs", ".browser-profile", ".browser-native-profile", "build", "dist", "release"}


def validate_paths(paths):
    for path in paths:
        parts = PurePosixPath(path).parts
        if any(part in PRIVATE or part == ".env" or part.startswith(".env.") for part in parts):
            raise ValueError(f"Refusing to package private/generated path: {path}")


def main():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", version):
        raise ValueError("Invalid app version")
    output = ROOT / "release"
    output.mkdir(exist_ok=True)
    archive_path = output / f"island-inline-watcher-v{version}-windows-source.zip"
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").strip("\0").split("\0")
    validate_paths(paths)
    required = {"start.bat", "island_inline_watcher.py", "native_browser.py", "browser_errors.py",
                "app_paths.py", "booking_verification.py", "pyproject.toml", "uv.lock", "README.md"}
    if not required <= set(paths):
        raise ValueError("Required source files are not tracked in git")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(ROOT / path, f"island-inline-watcher-v{version}/{path}")
    setup_path = output / f"island-inline-watcher-v{version}-windows-x64-setup.exe"
    if not setup_path.is_file():
        raise ValueError("The installer must be built before generating release checksums")
    lines = []
    for path in (setup_path, archive_path):
        with path.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    (output / "SHA256SUMS.txt").write_text("".join(lines), encoding="ascii")


if __name__ == "__main__":
    main()
