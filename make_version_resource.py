"""Generate a PyInstaller Windows version resource from the package version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", arguments.version)
    if not match:
        raise ValueError("Windows release versions must use major.minor.patch numbers")
    numbers = tuple(int(value) for value in match.groups()) + (0,)
    if any(value > 65535 for value in numbers):
        raise ValueError("Windows version components must not exceed 65535")
    resource = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'HabitReserve'),
    StringStruct('FileDescription', 'HabitReserve Windows timer'),
    StringStruct('FileVersion', '{arguments.version}'),
    StringStruct('InternalName', 'HabitReserve'),
    StringStruct('OriginalFilename', 'HabitReserve.exe'),
    StringStruct('ProductName', 'HabitReserve'),
    StringStruct('ProductVersion', '{arguments.version}')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""
    arguments.output.write_text(resource, encoding="utf-8")


if __name__ == "__main__":
    main()
