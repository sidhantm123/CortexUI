"""
Dark theme: design tokens, application palette/stylesheet, fonts and plot styling.

Everything here is applied once (at startup or when a plot is created); nothing runs
during rendering.
"""
import os
import sys
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QProgressDialog, QWidget

# ---------------------------------------------------------------- colours
WINDOW = "#16171A"
SURFACE = "#1E1F22"  # panels, plots
RAISED = "#25262B"  # inputs, tab bars, buttons
RAISED_HOVER = "#2D2F35"
BORDER = "#2E3036"
TEXT = "#E6E7EA"
TEXT_SECONDARY = "#9A9DA5"
TEXT_DISABLED = "#5C5F66"
ACCENT = "#CC79A7"  # reddish purple (Okabe-Ito)
ACCENT_HOVER = "#D891B8"
ACCENT_TEXT = "#16171A"  # text on accent fills (dark reads better than white on this accent)
WARNING = "#E0A040"
ERROR = "#E5534B"
SUCCESS = "#3FB950"

PLOT_BG = SURFACE
PLOT_TICK_TEXT = "#8B8E96"
GRID_ALPHA = 0.07  # faint white gridlines

# Trace colours tuned for a dark background
TRACE_COLORS = [
    "#5AA9FF",  # blue
    "#FFA94D",  # orange
    "#5FD38D",  # green
    "#FF6B6B",  # red
    "#B197FC",  # purple
    "#E8A87C",  # tan
    "#F783AC",  # pink
    "#A6ADB8",  # grey
]
MODALITY_COLORS = ["#FF6B6B", "#5AA9FF", "#5FD38D", "#B197FC", "#FFA94D", "#E8A87C"]
ANALOG_TRACE = "#C9CCD3"
THRESHOLD = "#FF6B6B"
BLANK_SHADE = (255, 255, 255, 22)  # blanked spans on the neural plot
HIGHLIGHT_SHADE = (255, 200, 0, 40)  # selected event on the analog plot
SELECTION_SHADE = (204, 121, 167, 45)  # FFT interval selection (accent)

# ---------------------------------------------------------------- typography
SIZE_BODY = 13
SIZE_SMALL = 11
SIZE_TITLE = 15
WEIGHT_SEMIBOLD = QFont.Weight.DemiBold

_ui_family: Optional[str] = None
_mono_family: Optional[str] = None


def _first_available(candidates):
    families = set(QFontDatabase.families())
    for name in candidates:
        if name in families:
            return name
    return None


def ui_family() -> str:
    """Inter if installed, else the platform UI font (SF Pro / Segoe UI)."""
    global _ui_family
    if _ui_family is None:
        _ui_family = _first_available(["Inter"]) or (
            "Segoe UI" if sys.platform.startswith("win")
            else QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        )
    return _ui_family


def mono_family() -> str:
    """Monospace font (all digits the same width, so numbers don't shift as they change)."""
    global _mono_family
    if _mono_family is None:
        _mono_family = _first_available(
            ["SF Mono", "Menlo", "Cascadia Mono", "Consolas", "DejaVu Sans Mono"]
        ) or QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    return _mono_family


def mono_font(size: int = SIZE_SMALL) -> QFont:
    font = QFont(mono_family())
    font.setPixelSize(size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def ui_font(size: int = SIZE_BODY, semibold: bool = False) -> QFont:
    font = QFont(ui_family())
    font.setPixelSize(size)
    if semibold:
        font.setWeight(WEIGHT_SEMIBOLD)
    return font


# ---------------------------------------------------------------- application

def _palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: WINDOW,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: RAISED,
        QPalette.ColorRole.AlternateBase: SURFACE,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.PlaceholderText: TEXT_DISABLED,
        QPalette.ColorRole.Button: RAISED,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: ACCENT_TEXT,
        QPalette.ColorRole.ToolTipBase: RAISED,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.Link: ACCENT,
        QPalette.ColorRole.BrightText: "#FFFFFF",
        QPalette.ColorRole.Light: RAISED_HOVER,
        QPalette.ColorRole.Midlight: RAISED,
        QPalette.ColorRole.Mid: BORDER,
        QPalette.ColorRole.Dark: WINDOW,
        QPalette.ColorRole.Shadow: "#000000",
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(TEXT_DISABLED))
    return p


def _asset(name: str) -> str:
    """Path to a file in ui/assets (also inside a PyInstaller bundle), with forward slashes for QSS."""
    base = getattr(sys, "_MEIPASS", None)
    folder = os.path.join(base, "ui", "assets") if base else os.path.join(os.path.dirname(__file__), "assets")
    return os.path.join(folder, name).replace("\\", "/")


def _stylesheet() -> str:
    mono = mono_family()
    check = _asset("check.svg")
    return f"""
    QWidget {{ font-size: {SIZE_BODY}px; }}
    QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; }}

    /* Group boxes become section headers: no frame, small secondary title */
    QGroupBox {{ border: none; border-top: 1px solid {BORDER}; margin-top: 18px; padding-top: 8px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 0px; padding: 0px;
                        color: {TEXT_SECONDARY}; font-size: {SIZE_SMALL}px; font-weight: 600; }}

    /* Flat tabs with an accent underline */
    QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; }}
    QTabBar::tab {{ background: transparent; color: {TEXT_SECONDARY}; padding: 6px 10px;
                    border: none; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
    QTabBar::tab:hover:!selected {{ color: {TEXT}; }}
    QTabBar::tab:disabled {{ color: {TEXT_DISABLED}; }}

    /* Buttons: quiet by default, accent-filled for the panel's main action */
    QPushButton {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER};
                   border-radius: 6px; padding: 5px 12px; }}
    QPushButton:hover {{ background: {RAISED_HOVER}; }}
    QPushButton:pressed {{ background: {BORDER}; }}
    QPushButton:disabled {{ color: {TEXT_DISABLED}; background: {SURFACE}; }}
    QPushButton[primary="true"] {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; font-weight: 600; }}
    QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton[primary="true"]:disabled {{ background: {RAISED}; color: {TEXT_DISABLED}; }}

    /* Numbers use the monospace font so digits don't shift */
    QAbstractSpinBox, QTableView, QLineEdit[mono="true"] {{ font-family: "{mono}"; }}
    QHeaderView::section {{ background: {SURFACE}; color: {TEXT_SECONDARY}; border: none;
                            border-bottom: 1px solid {BORDER}; padding: 4px; font-size: {SIZE_SMALL}px; }}
    QTableView {{ gridline-color: {BORDER}; background: {SURFACE}; border: 1px solid {BORDER}; }}
    QListView, QTreeView {{ background: {SURFACE}; border: 1px solid {BORDER}; }}
    QSplitter::handle {{ background: {WINDOW}; }}
    QMenu {{ background: {RAISED}; border: 1px solid {BORDER}; }}
    QMenu::item:selected {{ background: {ACCENT}; color: {ACCENT_TEXT}; }}

    /* Thin progress bars (the percentage is shown in the dialog text instead) */
    QProgressBar {{ background: {RAISED}; border: none; border-radius: 2px;
                    min-height: 4px; max-height: 4px; }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

    /* Check boxes and radio buttons: visible outline on dark, accent when on */
    QCheckBox::indicator, QRadioButton::indicator {{ width: 13px; height: 13px;
        border: 1px solid #4A4D55; background: {RAISED}; }}
    QCheckBox::indicator {{ border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 7px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{check}"); }}
    QRadioButton::indicator:checked {{ border-color: {ACCENT};
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                    stop:0 {ACCENT}, stop:0.5 {ACCENT}, stop:0.56 {RAISED}, stop:1 {RAISED}); }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {BORDER}; background: {SURFACE}; }}

    /* Text roles (set with set_role) */
    QLabel[role="secondary"] {{ color: {TEXT_SECONDARY}; font-size: {SIZE_SMALL}px; }}
    QLabel[role="title"] {{ color: {TEXT}; font-size: {SIZE_TITLE}px; font-weight: 600; }}
    QLabel[role="warning"] {{ color: {WARNING}; font-size: {SIZE_SMALL}px; }}
    QLabel[role="error"] {{ color: {ERROR}; font-size: {SIZE_SMALL}px; }}
    QLabel[role="success"] {{ color: {SUCCESS}; font-size: {SIZE_SMALL}px; }}
    QLabel[role="mono"] {{ font-family: "{mono}"; font-size: {SIZE_SMALL}px; color: {TEXT_SECONDARY}; }}
    QLabel[role="heading"] {{ color: {TEXT}; font-weight: 600; }}
    QLabel[role="empty"] {{ color: {TEXT_SECONDARY}; background: {SURFACE}; }}

    /* Chips in the plot header */
    QWidget#plotHeader {{ background: {SURFACE}; }}
    QLabel[role="chip"], QLabel[role="chip-mono"], QLabel[role="chip-warning"] {{
        background: {RAISED}; border: 1px solid {BORDER}; border-radius: 9px; padding: 1px 8px;
        color: {TEXT_SECONDARY}; font-size: {SIZE_SMALL}px; }}
    QLabel[role="chip-mono"] {{ font-family: "{mono}"; color: {TEXT}; }}
    QLabel[role="chip-warning"] {{ color: {WARNING}; border-color: {WARNING}; }}

    /* Toast messages over the plot area */
    /* Variants differ in colour only: a re-polished label keeps its first padding/border sizes */
    QLabel[role="toast"], QLabel[role="toast-warning"], QLabel[role="toast-error"] {{
        background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; border-left: 3px solid {ACCENT};
        border-radius: 6px; padding: 6px 12px; }}
    QLabel[role="toast-warning"] {{ border-left-color: {WARNING}; }}
    QLabel[role="toast-error"] {{ border-left-color: {ERROR}; }}
    """


def apply_theme(app: QApplication):
    """Fusion style + dark palette + stylesheet + UI font. Call once at startup."""
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setFont(ui_font())
    app.setStyleSheet(_stylesheet())


def set_role(widget: QWidget, role: str):
    """
    Give a label a text role: secondary / title / heading / warning / error / success / mono,
    chip / chip-mono / chip-warning, toast / toast-warning / toast-error, empty.

    Switching roles later updates colours only (Qt keeps a label's first padding/border
    sizes), so roles that are swapped at runtime must share their box geometry.
    """
    if widget.property("role") == role:
        return
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class ElidedLabel(QLabel):
    """One-line label that elides in the middle (start and end stay visible); full text in the tooltip."""

    def __init__(self, text: str = "", parent=None, min_width: int = 1):
        super().__init__(parent)
        self._full = ""
        self.setTextFormat(Qt.TextFormat.PlainText)  # names and paths, never markup
        self.setMinimumWidth(min_width)  # don't let long text widen the panel
        self.set_full_text(text)

    def sizeHint(self) -> QSize:
        """Ask for room for the full text, so the label un-elides when space comes back."""
        hint = super().sizeHint()
        fm = self.fontMetrics()
        return QSize(hint.width() + fm.horizontalAdvance(self._full) - fm.horizontalAdvance(self.text()),
                     hint.height())

    def set_full_text(self, text: str):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        width = max(1, self.contentsRect().width())
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, width))


class ProgressDialog(QProgressDialog):
    """
    Progress dialog whose progress is reported on its bar (progress_bar().setValue), never with
    QProgressDialog.setValue: for a modal dialog that processes events, so a job's result
    handled there could close and delete the dialog inside its own setValue (a crash on the reference recording
    under load). The event loop is running anyway.
    """

    def __init__(self, text: str, cancel_text: Optional[str], maximum: int, parent: QWidget):
        super().__init__(text, cancel_text, 0, maximum, parent)
        self._bar = QProgressBar(self)
        self._bar.setTextVisible(False)
        self.setBar(self._bar)

    def progress_bar(self) -> QProgressBar:
        return self._bar


def progress_dialog(text: str, parent: QWidget, cancellable: bool = True,
                    maximum: int = 100) -> ProgressDialog:
    """
    Window-modal progress dialog with a thin bar; the percentage is appended to the text.
    maximum=0 gives a busy indicator. Report progress with progress_bar().setValue.
    """
    dialog = ProgressDialog(text, "Cancel" if cancellable else None, maximum, parent)
    bar = dialog.progress_bar()
    dialog.setRange(0, maximum)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setMinimumWidth(380)
    if maximum:
        bar.valueChanged.connect(lambda v: v >= 0 and dialog.setLabelText(f"{text}  {v}%"))
    return dialog


def set_primary(button: QWidget, primary: bool = True):
    """Mark a button as the panel's main action (accent-filled)."""
    button.setProperty("primary", primary)
    button.style().unpolish(button)
    button.style().polish(button)


# ---------------------------------------------------------------- plots

def style_plot(plot_item, graphics_widget, left_scale: float = 1.0):
    """
    Dark plot: surface background, no axis lines, faint grid, muted monospace tick labels.

    left_scale multiplies the left axis' tick values only (e.g. 1e6 to show volts as µV);
    the data itself is not touched.
    """
    import pyqtgraph as pg

    graphics_widget.setBackground(PLOT_BG)
    tick_font = mono_font(SIZE_SMALL)
    for name in ("left", "bottom"):
        axis = plot_item.getAxis(name)
        axis.setPen(pg.mkPen(color=(0, 0, 0, 0)))  # no hard axis line
        axis.setTickPen(pg.mkPen("#FFFFFF"))  # grid lines use this colour at GRID_ALPHA
        axis.setTextPen(pg.mkPen(PLOT_TICK_TEXT))
        axis.setTickFont(tick_font)
        axis.setStyle(tickLength=0)
        axis.label.setDefaultTextColor(QColor(TEXT_SECONDARY))
    if left_scale != 1.0:
        left = plot_item.getAxis("left")
        left.setScale(left_scale)
        left.enableAutoSIPrefix(False)
    plot_item.showGrid(x=True, y=True, alpha=GRID_ALPHA)


def axis_label(plot_item, side: str, text: str):
    """Axis title in the secondary text colour and size."""
    plot_item.setLabel(side, text, **{"color": TEXT_SECONDARY, "font-size": f"{SIZE_SMALL}px"})
