"""
context_dialog.py – PySide6 meeting context dialog for MeetRec.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox,
    QPushButton, QFrame, QSizePolicy, QCheckBox
)
from PySide6.QtCore import Qt

LANGUAGES = [
    ("Auto-detect",  ""),
    ("English",      "en"),
    ("Finnish",      "fi"),
    ("Swedish",      "sv"),
    ("German",       "de"),
    ("French",       "fr"),
    ("Spanish",      "es"),
]

MEETING_TYPES = [
    ("General",       "general"),
    ("Standup",       "standup"),
    ("1:1",           "one_on_one"),
    ("Brainstorming", "brainstorm"),
    ("Interview",     "interview"),
    ("Client Call",   "client"),
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
QCheckBox {
    color: #cdd6f4;
    font-size: 13px;
    font-weight: 400;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #45475a;
    border-radius: 4px;
    background-color: #313244;
}
QCheckBox::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
}
"""


@dataclass
class MeetingContext:
    title:          str = ""
    participants:   str = ""   # other participants when me_present, else everyone
    agenda:         str = ""
    language:       str = ""
    notes_language: str = "en"
    meeting_type:   str = "general"
    my_name:        str = ""
    me_present:     bool = True

    @property
    def mic_speaker(self) -> str:
        """Name of whoever speaks into the microphone channel."""
        if self.me_present:
            return self.my_name
        return self.participants.split(",")[0].strip()


OPTIONAL_SUFFIX = "  <span style='color:#585b70;font-weight:400;text-transform:none'>optional</span>"


class ContextDialog(QDialog):
    def __init__(self, default_title: str = "", my_name: str = ""):
        self._app = QApplication.instance() or QApplication(sys.argv)

        super().__init__()
        self.result_context: MeetingContext | None = None
        self._my_name = my_name.strip()
        self._build_ui(default_title)

    def _field_label(self, text: str, optional: bool = True) -> QLabel:
        suffix = OPTIONAL_SUFFIX if optional else ""
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
        me_label = f"I'm in this meeting ({self._my_name})" if self._my_name else "I'm in this meeting"
        self.me_checkbox = QCheckBox(me_label)
        self.me_checkbox.setChecked(True)
        self.me_checkbox.setCursor(Qt.PointingHandCursor)
        self.me_checkbox.toggled.connect(self._update_participants_caption)
        root.addWidget(self.me_checkbox)
        root.addSpacing(8)

        self.participants_label = self._field_label("OTHER PARTICIPANTS")
        root.addWidget(self.participants_label)
        self.participants_input = QLineEdit()
        self.participants_input.setFixedHeight(38)
        root.addWidget(self.participants_input)
        self._update_participants_caption(True)
        root.addSpacing(12)

        # ── Agenda ──
        root.addWidget(self._field_label("TOPIC / AGENDA"))
        self.agenda_input = QTextEdit()
        self.agenda_input.setPlaceholderText("What is this meeting about?")
        self.agenda_input.setFixedHeight(80)
        self.agenda_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        root.addWidget(self.agenda_input)
        root.addSpacing(12)

        # ── Meeting type ──
        root.addWidget(self._field_label("MEETING TYPE"))
        self.meeting_type_combo = QComboBox()
        for label, code in MEETING_TYPES:
            self.meeting_type_combo.addItem(label, code)
        self.meeting_type_combo.setFixedHeight(38)
        root.addWidget(self.meeting_type_combo)
        root.addSpacing(12)

        # ── Language ──
        root.addWidget(self._field_label("LANGUAGE"))
        self.lang_combo = QComboBox()
        for label, code in LANGUAGES:
            self.lang_combo.addItem(label, code)
        self.lang_combo.setFixedHeight(38)
        root.addWidget(self.lang_combo)
        root.addSpacing(12)

        # ── Notes language ──
        root.addWidget(self._field_label("NOTES LANGUAGE"))
        self.notes_lang_combo = QComboBox()
        self.notes_lang_combo.addItem("English", "en")
        self.notes_lang_combo.addItem("Finnish", "fi")
        self.notes_lang_combo.setFixedHeight(38)
        root.addWidget(self.notes_lang_combo)

        # ── Buttons ──
        root.addSpacing(16)
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

    def _update_participants_caption(self, me_checked: bool):
        text = "OTHER PARTICIPANTS" if me_checked else "PARTICIPANTS"
        self.participants_label.setText(f"{text}{OPTIONAL_SUFFIX}")
        placeholder = ("e.g. Maria, Bob" if me_checked
                       else "e.g. Maria, Bob — first name is the mic speaker")
        self.participants_input.setPlaceholderText(placeholder)

    def _on_ok(self):
        self.result_context = MeetingContext(
            title          = self.title_input.text().strip(),
            participants   = self.participants_input.text().strip(),
            agenda         = self.agenda_input.toPlainText().strip(),
            language       = self.lang_combo.currentData(),
            notes_language = self.notes_lang_combo.currentData(),
            meeting_type   = self.meeting_type_combo.currentData(),
            my_name        = self._my_name,
            me_present     = self.me_checkbox.isChecked(),
        )
        self.accept()

    def _on_skip(self):
        # Even without details, the configured name still labels the mic channel
        self.result_context = MeetingContext(my_name=self._my_name)
        self.accept()

    def _on_cancel(self):
        self.result_context = None
        self.reject()


def ask_meeting_context(default_title: str = "", my_name: str = "") -> MeetingContext | None:
    """Show the context dialog and return MeetingContext, or None if cancelled."""
    dlg = ContextDialog(default_title=default_title, my_name=my_name)
    dlg.exec()
    return dlg.result_context


def ask_notes_conflict(notes_path: str) -> str | None:
    """Ask what to do when notes already exist for a recording.
    Returns 'overwrite', 'append', 'improve', or None (cancel)."""
    dlg = QDialog()
    dlg.setWindowTitle("MeetRec — Notes Already Exist")
    dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
    dlg.setFixedWidth(420)
    dlg.setStyleSheet(STYLE)

    result = [None]

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(0)

    title_lbl = QLabel("Notes already exist")
    title_lbl.setStyleSheet("color:#cdd6f4; font-size:15px; font-weight:700;")
    layout.addWidget(title_lbl)

    filename = Path(notes_path).name
    sub_lbl = QLabel(
        f"<span style='color:#6c7086;font-size:11px;font-weight:400'>{filename}</span>"
    )
    sub_lbl.setTextFormat(Qt.RichText)
    sub_lbl.setStyleSheet("margin-top:4px; margin-bottom:6px;")
    layout.addWidget(sub_lbl)

    desc_lbl = QLabel("What would you like to do with the existing notes?")
    desc_lbl.setStyleSheet("color:#a6adc8; font-size:12px; font-weight:400; margin-bottom:16px;")
    desc_lbl.setWordWrap(True)
    layout.addWidget(desc_lbl)

    div = QFrame()
    div.setFrameShape(QFrame.HLine)
    div.setStyleSheet("background:#313244; margin-bottom:16px;")
    div.setFixedHeight(1)
    layout.addWidget(div)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)

    def make_btn(label, obj_name, action):
        b = QPushButton(label)
        b.setObjectName(obj_name)
        b.setCursor(Qt.PointingHandCursor)
        def _click():
            result[0] = action
            dlg.accept()
        b.clicked.connect(_click)
        return b

    btn_row.addWidget(make_btn("Cancel",      "btn_cancel",    None))
    btn_row.addStretch()
    btn_row.addWidget(make_btn("Overwrite",   "btn_secondary", "overwrite"))
    btn_row.addWidget(make_btn("Append",      "btn_secondary", "append"))
    btn_row.addWidget(make_btn("Improve ✨",  "btn_primary",   "improve"))
    layout.addLayout(btn_row)

    dlg.exec()
    return result[0]
