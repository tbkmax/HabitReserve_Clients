import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    pkg_dir = Path(__file__).resolve().parent
    parent_dir = str(pkg_dir.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from time_bank_tracker.app import TimeBankApplication
from time_bank_tracker.assets import asset_path


def migrate_legacy_settings() -> None:
    current = QSettings("HabitReserve", "HabitReserve")
    if current.allKeys():
        return

    legacy = QSettings("Time Bank", "Time Bank Tracker")
    for key in legacy.allKeys():
        current.setValue(key, legacy.value(key))
    current.sync()


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("HabitReserve")
    application.setOrganizationName("HabitReserve")
    application.setWindowIcon(QIcon(str(asset_path("habitreserve.svg"))))
    application.setQuitOnLastWindowClosed(False)
    migrate_legacy_settings()
    tracker = TimeBankApplication(application)
    tracker.start()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
