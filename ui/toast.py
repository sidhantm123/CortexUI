"""Short-lived message shown over the plot area (replaces the status bar)."""
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QLabel, QWidget

from .theme import set_role

INFO, WARNING, ERROR = "info", "warning", "error"
_ROLES = {INFO: "toast", WARNING: "toast-warning", ERROR: "toast-error"}


class Toast(QLabel):
    """
    One-line message floating at the bottom centre of its parent. It hides itself after a
    few seconds (warnings and errors stay longer) or when clicked.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        set_role(self, "toast")  # before the first polish, so the padding applies
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        parent.installEventFilter(self)

    def show_message(self, text: str, kind: str = INFO, ms: int = 0):
        set_role(self, _ROLES.get(kind, "toast"))
        self.setToolTip(text + "\n(click to dismiss)")
        max_w = max(100, self.parentWidget().width() - 48)
        self.setText(self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, max_w - 30))
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(ms or (4000 if kind == INFO else 8000))

    def _place(self):
        parent = self.parentWidget()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 16)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False

    def mousePressEvent(self, event):
        self.hide()
