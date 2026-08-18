#!/usr/bin/env python3
"""Traverse - KDE File Manager

A simplified xplorer2-style file manager using PyQt6.
"""

import sys
from pathlib import Path

# Add project root to path for imports
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

def main():
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon
    from src.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Traverse")
    app.setOrganizationName("traverse-file-manager")
    app.setDesktopFileName("traverse")   # tells KDE which .desktop file owns this window

    icon = QIcon.fromTheme("system-file-manager")
    if icon.isNull():
        icon = QIcon.fromTheme("folder-open")
    app.setWindowIcon(icon)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
