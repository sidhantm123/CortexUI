#!/usr/bin/env python3
"""
CortexUI: multi-channel neural recording viewer (TDT and Blackrock).

    python main.py [FOLDER]             open the app (and the recording in FOLDER)
    python main.py --smoke-test FOLDER  open FOLDER, wait for its first plot, exit 0 (ok) or 1
"""
import sys
import os
import time
import traceback
from datetime import datetime

# Add the app directory to path for imports
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Suppress Qt font-alias warning (pyqtgraph requests "Monospace" which doesn't
# exist by that name on macOS/Windows, causing a slow lookup + noisy log line)
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false")

from version import APP_NAME, APP_VERSION  # noqa: E402

# --smoke-test gives up if the first plot hasn't loaded by then
SMOKE_TEST_TIMEOUT_S = 60.0


def error_log_path() -> str:
    """error.log in the app-data folder (macOS: ~/Library/Application Support/CortexUI)."""
    from PySide6.QtCore import QStandardPaths

    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return os.path.join(folder, "error.log")


def append_error_log(text: str):
    path = error_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n")
    except OSError:
        pass


def install_error_log(app):
    """
    Unhandled exceptions (e.g. in a Qt slot) are appended to error.log in the app-data folder
    and reported once in a message box: the packaged app has no console, so they would
    otherwise vanish.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    path = error_log_path()
    shown = set()

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        if sys.stderr:
            sys.stderr.write(text)
        append_error_log(text)
        summary = f"{exc_type.__name__}: {exc}"
        if summary not in shown:  # once per distinct error
            shown.add(summary)
            # Not from inside the hook (it may run in the middle of a paint or a signal)
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                None, "Unexpected Error",
                f"Something went wrong:\n\n{summary}\n\nDetails were written to:\n{path}",
            ))

    sys.excepthook = hook


def run_smoke_test(app, window, folder: str) -> int:
    """
    Open FOLDER in the real window and wait for its first processed plot buffer: exit code 0, or 1
    on an error box, an error toast, an uncaught exception (its box) or a timeout. No dialog is
    shown. The result is printed; with no console to print to (the windowed Windows app) a
    failure goes to error.log instead. Used to check a built app (here and on the CI runners).
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox
    from ui.toast import ERROR

    result = []

    def finish(code: int, line: str):
        if result:
            return
        result.append(code)
        stream = sys.stdout if code == 0 else sys.stderr
        if stream:
            print(line, file=stream, flush=True)
        elif code:
            append_error_log(line)
        poll.stop()
        window.close()  # stops the background loads and closes the reader
        app.exit(code)

    def fail(reason: str):
        finish(1, f"smoke test failed: {reason}")

    def failing_box(_parent, title, text, *args, **kwargs):
        fail(f"{title}: {text}")
        return QMessageBox.StandardButton.Cancel

    def note_box(_parent, title, text, *args, **kwargs):
        if sys.stderr:
            print(f"smoke test note: {title}: {text}", file=sys.stderr, flush=True)
        return QMessageBox.StandardButton.Ok

    QMessageBox.critical = staticmethod(failing_box)
    QMessageBox.warning = staticmethod(failing_box)
    QMessageBox.question = staticmethod(failing_box)
    QMessageBox.information = staticmethod(note_box)

    show_toast = window._toast.show_message

    def toast(text, kind="info", ms=0):
        if kind == ERROR:
            fail(text)
        return show_toast(text, kind, ms)

    window._toast.show_message = toast

    started = time.monotonic()

    def check():
        block = window._app_state.block
        state = window._app_state.get_current_state()
        if block is not None and state is not None and window._filtered_data is not None:
            from core.update_check import ssl_context
            if not ssl_context().cert_store_stats()["x509_ca"]:  # certifi missing from the build
                fail("no root certificates: Check for Updates could not reach GitHub")
                return
            finish(0, f"smoke test ok: {block.name}, streams {', '.join(block.streams)}, "
                      f"{state.num_channels} channels at {state.sample_rate:g} Hz")
        elif time.monotonic() - started > SMOKE_TEST_TIMEOUT_S:
            fail(f"no plot data after {SMOKE_TEST_TIMEOUT_S:g} s")

    poll = QTimer()
    poll.timeout.connect(check)
    poll.start(100)
    app.setQuitOnLastWindowClosed(False)  # the exit code is ours, not the window's
    QTimer.singleShot(0, lambda: window._load_block(folder))
    app.exec()
    return result[0] if result else 1


def main():
    """Main entry point."""
    # Check PySide6 first — needed before anything else
    try:
        from PySide6.QtWidgets import QApplication, QSplashScreen
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap, QColor, QPainter, QFont, QIcon
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
    args = sys.argv[1:]
    smoke_folder = None
    if args[:1] == ["--smoke-test"]:
        if len(args) != 2:
            print("Usage: main.py --smoke-test FOLDER")
            sys.exit(2)
        smoke_folder = args[1]

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)  # no organization: app data goes to …/CortexUI
    app.setApplicationVersion(APP_VERSION)
    if sys.platform != "darwin":  # macOS takes the bundle's .icns (this PNG would replace it in the Dock)
        app.setWindowIcon(QIcon(os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "assets", "icon.png")))
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
    painter.drawText(splash_pix.rect(), Qt.AlignmentFlag.AlignCenter, f"{APP_NAME}\nLoading…")
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

    if smoke_folder is not None:
        code = run_smoke_test(app, window, smoke_folder)
    else:
        # Handle command line argument (folder path)
        if len(sys.argv) > 1:
            folder_path = sys.argv[1]
            if os.path.isdir(folder_path):
                window._load_block(folder_path)
        window.check_for_updates_at_launch()
        code = app.exec()

    # End here, without the interpreter's teardown of the Qt objects (as pyqtgraph advises): PySide
    # destroys them in no particular order, and freeing a plot item can free a lambda holding the
    # last reference to its own plot, which then deletes the item a second time, a crash on quit
    # (seen in the tests: SIGILL on macOS, heap corruption on Windows). Closing the window already
    # stopped the jobs and closed the recording; the preferences are written here.
    window._settings.sync()
    for stream in (sys.stdout, sys.stderr):
        if stream:
            stream.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
