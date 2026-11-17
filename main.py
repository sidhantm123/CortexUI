#!/usr/bin/env python3
"""
Multi-Channel Neural Viewer (TDT and Blackrock)

A cross-platform application for viewing and analyzing
Tucker-Davis Technologies neural recording data.
"""
import sys
import os
import traceback
from datetime import datetime

# Add the app directory to path for imports
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Suppress Qt font-alias warning (pyqtgraph requests "Monospace" which doesn't
# exist by that name on macOS/Windows, causing a slow lookup + noisy log line)
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false")


def install_error_log(app):
    """
    Unhandled exceptions (e.g. in a Qt slot) are appended to error.log in the app-data folder
    and reported once in a message box: the packaged app has no console, so they would
    otherwise vanish.
    """
    from PySide6.QtCore import QStandardPaths, QTimer
    from PySide6.QtWidgets import QMessageBox

    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    path = os.path.join(folder, "error.log")
    shown = set()

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(text)
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, "a") as f:
                f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n")
        except OSError:
            pass
        summary = f"{exc_type.__name__}: {exc}"
        if summary not in shown:  # once per distinct error
            shown.add(summary)
            # Not from inside the hook (it may run in the middle of a paint or a signal)
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                None, "Unexpected Error",
                f"Something went wrong:\n\n{summary}\n\nDetails were written to:\n{path}",
            ))

    sys.excepthook = hook


def main():
    """Main entry point."""
    # Check PySide6 first — needed before anything else
    try:
        from PySide6.QtWidgets import QApplication, QSplashScreen
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap, QColor, QPainter, QFont
    except ImportError:
        print("Error: PySide6 is required. Install with: pip install PySide6")
        sys.exit(1)

    try:
        import numpy
        import scipy
    except ImportError:
        print("Error: numpy and scipy are required. Install with: pip install numpy scipy")
        sys.exit(1)

    try:
        import tdt
    except ImportError:
        print("Warning: tdt package not installed. Install with: pip install tdt")
        print("The application will run but won't be able to load TDT files.")

    try:
        import neo
    except ImportError:
        print("Warning: neo package not installed. Install with: pip install neo")
        print("The application will run but won't be able to load Blackrock files.")

    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Create application first so macOS registers it as a GUI app immediately
    app = QApplication(sys.argv)
    app.setApplicationName("Multi-Channel Neural Viewer")
    app.setOrganizationName("TDT Viewer")
    app.setApplicationVersion("1.0.0")
    install_error_log(app)
    # Dark theme (palette, stylesheet, fonts) — applied once
    from ui.theme import apply_theme, ui_font, WINDOW, TEXT
    apply_theme(app)

    # Show a splash screen while pyqtgraph (slow ~4s import) loads
    splash_pix = QPixmap(420, 120)
    splash_pix.fill(QColor(WINDOW))
    painter = QPainter(splash_pix)
    painter.setPen(QColor(TEXT))
    painter.setFont(ui_font(15, semibold=True))
    painter.drawText(splash_pix.rect(), Qt.AlignmentFlag.AlignCenter, "Multi-Channel Neural Viewer\nLoading…")
    painter.end()

    splash = QSplashScreen(splash_pix)
    splash.show()
    app.processEvents()

    # Now do the slow pyqtgraph import (while splash is visible)
    try:
        import pyqtgraph
    except ImportError:
        splash.close()
        print("Error: pyqtgraph is required. Install with: pip install pyqtgraph")
        sys.exit(1)

    # Import and create main window
    from ui import MainWindow

    window = MainWindow()
    window.show()
    splash.finish(window)  # waits until the window is on screen: show it first (else ~1 s)

    # Handle command line argument (folder path)
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]
        if os.path.isdir(folder_path):
            window._load_block(folder_path)

    # Run event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
