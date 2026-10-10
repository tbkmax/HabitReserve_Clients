# Build from the desktop directory with: pyinstaller HabitReserve.spec
import os

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("keyring.backends")
datas = [("assets/icons/habitreserve.svg", "assets/icons")]

build_config = (
    os.environ.get("HABITRESERVE_BUILD_CONFIG", "").strip()
    or os.environ.get("TIME_BANK_BUILD_CONFIG", "").strip()
)
version_resource = os.environ.get("HABITRESERVE_VERSION_RESOURCE", "").strip()
if build_config:
    if not os.path.isfile(build_config):
        raise SystemExit("HABITRESERVE_BUILD_CONFIG does not name a readable file")
    datas.append((build_config, "."))

a = Analysis(
    ["src/time_bank_tracker/__main__.py"],
    pathex=["src"],
    hiddenimports=hiddenimports,
    datas=datas,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HabitReserve",
    console=False,
    onefile=True,
    icon="assets/icons/habitreserve.ico",
    version=version_resource or None,
    target_arch="x86_64",
)
