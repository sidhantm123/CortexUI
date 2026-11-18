"""Help → Settings Guide: every editable setting explained, searchable (built from ui.help_text)."""
import html
from typing import List

from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QTextBrowser, QVBoxLayout

from . import theme
from .help_text import HELP, SECTIONS
from .theme import set_role


class SettingsGuide(QDialog):
    """Non-modal window listing every setting by tab; typing filters the list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings Guide")
        self.setModal(False)
        self.resize(580, 680)

        layout = QVBoxLayout(self)
        intro = QLabel("Every setting in the app, by tab. Hovering a setting in the app shows the same text.")
        intro.setWordWrap(True)
        set_role(intro, "secondary")
        layout.addWidget(intro)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search settings, e.g. merge, notch, σ, blanking")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._render)
        layout.addWidget(self._search)

        self._browser = QTextBrowser()
        layout.addWidget(self._browser, 1)

        self._count = QLabel()
        set_role(self._count, "secondary")
        layout.addWidget(self._count)
        self._render("")

    @staticmethod
    def matching(query: str) -> List[str]:
        """Keys of the settings whose title, text or tab contain the query (all if empty)."""
        q = query.strip().lower()
        return [
            key for section, keys in SECTIONS for key in keys
            if not q or q in f"{section} {HELP[key].title} {HELP[key].text}".lower()
        ]

    def _render(self, query: str):
        keys = set(self.matching(query))
        parts = []
        for section, section_keys in SECTIONS:
            shown = [k for k in section_keys if k in keys]
            if not shown:
                continue
            parts.append(f"<h3>{html.escape(section)}</h3>")
            for key in shown:
                h = HELP[key]
                default = (
                    f"<br><span style='color:{theme.TEXT_SECONDARY}'>Default: {html.escape(h.default)}</span>"
                    if h.default else ""
                )
                parts.append(f"<p><b>{html.escape(h.title)}</b><br>{html.escape(h.text)}{default}</p>")
        self._browser.setHtml("".join(parts) or "<p>No setting matches.</p>")
        self._count.setText(
            f"{len(keys)} of {len(HELP)} settings" if query.strip() else f"{len(HELP)} settings"
        )
