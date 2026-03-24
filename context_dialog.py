"""
context_dialog.py – PySide6 meeting context dialog for MeetRec.
"""

import sys
from dataclasses import dataclass
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox,
    QPushButton, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor, QPalette

LANGUAGES = [
    ("Auto-detect",  ""),
    ("English",      "en"),
    ("Finnish",      "fi"),
    ("Swedish",      "sv"),
    ("German",       "de"),
    ("French",       "fr"),
    ("Spanish",      "es"),
]

STYLE = """
QDialog {
    background-color: #1e1e2e;
    color: #cdd6f4;
}
QLabel {
    color: #a6adc8;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
QLabel#title_label {
    color: #cdd6f4;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0;
}
QLabel#subtitle_label {
    color: #6c7086;
    font-size: 11px;
    font-weight: 400;
}
QLineEdit, QTextEdit, QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 13px;
    selection-background-color: #89b4fa;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
    border: 1px solid #89b4fa;
}
QLineEdit::placeholder, QTextEdit::placeholder {
    color: #585b70;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid #a6adc8;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    selection-background-color: #45475a;
    outline: none;
}
QPushButton {
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    padding: 9px 20px;
    border: none;
}
QPushButton#btn_primary {
    background-color: #89b4fa;
    color: #1e1e2e;
}
QPushButton#btn_primary:hover {
    background-color: #b4befe;
}
QPushButton#btn_secondary {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
}
QPushButton#btn_secondary:hover {
    background-color: #45475a;
}
QPushButton#btn_cancel {
    background-color: transparent;
    color: #6c7086;
}
QPushButton#btn_cancel:hover {
    color: #f38ba8;
}
QFrame#divider {
    background-color: #313244;
    max-height: 1px;
}
"""


@dataclass
class MeetingContext:
    title:        str = ""
    participants: str = ""
    agenda:       str = ""
    language:     str = ""
    notes_language: str = "en" 

class ContextDialog(QDialog):
    def __init__(self, default_title: str = ""):
        # Ensure a QApplication exists
        self._app = QApplication.instance() or QApplication(sys.argv)

        super().__init__()
        self.result_context: MeetingContext | None = None
        self._build_ui(default_title)

    def _field_label(self, text: str, optional: bool = True) -> QLabel:
        suffix = "  <span style='color:#585b70;font-weight:400;text-transform:none'>optional</span>" if optional else ""
        lbl = QLabel(f"{text}{suffix}")
        lbl.setTextFormat(Qt.RichText)
        lbl.setStyleSheet("color: #a6adc8; letter-spacing: 0.5px;")
        return lbl

    def _build_ui(self, default_title: str):
        self.setWindowTitle("MeetRec")
        self.setFixedWidth(460)
        self.setStyleSheet(STYLE)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 20)
        root.setSpacing(0)

        # ── Header ──
        title_lbl = QLabel("Meeting Details")
        title_lbl.setObjectName("title_label")
        title_lbl.setStyleSheet("color:#cdd6f4; font-size:16px; font-weight:700;")
        root.addWidget(title_lbl)

        sub_lbl = QLabel("All fields are optional — fill in what you know for better notes.")
        sub_lbl.setObjectName("subtitle_label")
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet("color:#6c7086; font-size:11px; margin-top:4px; margin-bottom:16px;")
        root.addWidget(sub_lbl)

        # ── Divider ──
        div = QFrame()
        div.setObjectName("divider")
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("background:#313244; margin-bottom:16px;")
        div.setFixedHeight(1)
        root.addWidget(div)

        # ── Meeting title ──
        root.addWidget(self._field_label("MEETING TITLE"))
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("e.g. Q2 Planning, 1:1 with Maria…")
        self.title_input.setText(default_title)
        self.title_input.setFixedHeight(38)
        root.addWidget(self.title_input)
        root.addSpacing(12)

        # ── Participants ──
        root.addWidget(self._field_label("PARTICIPANTS"))
        self.participants_input = QLineEdit()
        self.participants_input.setPlaceholderText("e.g. Janne, Maria, Bob")
        self.participants_input.setFixedHeight(38)
        root.addWidget(self.participants_input)
        root.addSpacing(12)

        # ── Agenda ──
        root.addWidget(self._field_label("TOPIC / AGENDA"))
        self.agenda_input = QTextEdit()
        self.agenda_input.setPlaceholderText("What is this meeting about?")
        self.agenda_input.setFixedHeight(80)
        self.agenda_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        root.addWidget(self.agenda_input)
        root.addSpacing(12)

        # ── Language ──
        root.addWidget(self._field_label("LANGUAGE"))
        self.lang_combo = QComboBox()
        for label, code in LANGUAGES:
            self.lang_combo.addItem(label, code)
        self.lang_combo.setFixedHeight(38)
        root.addWidget(self.lang_combo)
        root.addSpacing(20)

        # ── Notes language ──
        root.addSpacing(12)
        root.addWidget(self._field_label("NOTES LANGUAGE"))
        self.notes_lang_combo = QComboBox()
        self.notes_lang_combo.addItem("English", "en")
        self.notes_lang_combo.addItem("Finnish", "fi")
        self.notes_lang_combo.setFixedHeight(38)
        root.addWidget(self.notes_lang_combo)

        # ── Buttons ──
        root.addSpacing(12)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("btn_cancel")
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.clicked.connect(self._on_cancel)

        btn_skip = QPushButton("Skip details")
        btn_skip.setObjectName("btn_secondary")
        btn_skip.setCursor(Qt.PointingHandCursor)
        btn_skip.clicked.connect(self._on_skip)

        btn_ok = QPushButton("Transcribe →")
        btn_ok.setObjectName("btn_primary")
        btn_ok.setCursor(Qt.PointingHandCursor)
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_ok)

        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(btn_skip)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)

    def _on_ok(self):
        self.result_context = MeetingContext(
            title        = self.title_input.text().strip(),
            participants = self.participants_input.text().strip(),
            agenda       = self.agenda_input.toPlainText().strip(),
            language     = self.lang_combo.currentData(),
            notes_language = self.notes_lang_combo.currentData(),
        )
        self.accept()

    def _on_skip(self):
        self.result_context = MeetingContext()
        self.accept()

    def _on_cancel(self):
        self.result_context = None
        self.reject()


def ask_meeting_context(default_title: str = "") -> MeetingContext | None:
    """Show the dialog and return MeetingContext, or None if cancelled."""
    dlg = ContextDialog(default_title=default_title)
    dlg.exec()
    return dlg.result_context
