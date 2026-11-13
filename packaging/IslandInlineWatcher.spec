# PyInstaller spec: build on Windows x64 with Python 3.12.
from pathlib import Path
import tomllib
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH).parent
assets = root / "assets"
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
parts = tuple(map(int, version.split(".")))
file_version = (parts + (0, 0, 0, 0))[:4]
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=file_version, prodvers=file_version, mask=0x3f, flags=0,
                      OS=0x40004, fileType=0x1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "sfy257000"),
        StringStruct("FileDescription", "Island Inline Watcher"),
        StringStruct("FileVersion", version),
        StringStruct("InternalName", "IslandInlineWatcher"),
        StringStruct("OriginalFilename", "IslandInlineWatcher.exe"),
        StringStruct("ProductName", "Island Inline Watcher"),
        StringStruct("ProductVersion", version),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)

a = Analysis([str(root / "windows_entry.py")], pathex=[str(root)],
             binaries=[], datas=[(str(assets / "island-inline-watcher.png"), "assets")],
             hiddenimports=[], hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=["playwright", "pytest", "unittest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="IslandInlineWatcher",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, version=version_info, icon=str(assets / "island-inline-watcher.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="IslandInlineWatcher")
