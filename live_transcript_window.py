"""
live_transcript_window.py – Floating real-time transcript overlay for MeetRec.
Shows live AssemblyAI partials (updating in place) and finals (committed lines).
Thread-safe: call update_partial/commit_final from any thread via Qt signals.
"""

import time

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPlainTextEdit
from PySide6.QtCore import QObject, Signal, Qt, QTimer
from PySide6.QtGui import QTextCursor

STYLE_WINDOW = """
    QWidget {
        background-color: #1e1e2e;
        border: 1px solid #45475a;
        border-radius: 8px;
    }
"""

STYLE_HEADER = """
    QLabel {
        color: #f38ba8;
        font-size: 12px;
        font-weight: 700;
        padding: 6px 10px 4px 10px;
        background-color: #181825;
        border-radius: 8px 8px 0 0;
    }
"""

STYLE_TEXT = """
    QPlainTextEdit {
        background-color: #1e1e2e;
        color: #cdd6f4;
        font-family: 'Segoe UI', sans-serif;
        font-size: 13px;
        border: none;
        padding: 8px 10px;
    }
"""


class LiveTranscriptBridge(QObject):
    """Emit from any thread; slots run on the Qt main thread via auto-queued connections."""

    _update_partial = Signal(str)
    _commit_final   = Signal(str)
    _close_window   = Signal()

    def __init__(self):
        super().__init__()

    def update_partial(self, text: str):
        self._update_partial.emit(text)

    def commit_final(self, text: str):
        self._commit_final.emit(text)

    def close(self):
        self._close_window.emit()


class LiveTranscriptWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeetRec — Live Transcript")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(420, 260)
        self.setStyleSheet(STYLE_WINDOW)

        self._start_time = time.time()
        self._last_final = ""  # guard against partial re-emitting last committed text

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QLabel("🔴  Recording  00:00")
        self._header.setStyleSheet(STYLE_HEADER)
        layout.addWidget(self._header)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setStyleSheet(STYLE_TEXT)
        layout.addWidget(self._text)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        self.show()

    def _tick(self):
        elapsed = int(time.time() - self._start_time)
        m, s = elapsed // 60, elapsed % 60
        self._header.setText(f"🔴  Recording  {m:02d}:{s:02d}")

    def _replace_last_line(self, text: str):
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine,
                            QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(text)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

    def on_partial(self, text: str):
        if text != self._last_final:
            self._replace_last_line(text)

    def on_final(self, text: str):
        self._last_final = text
        self._replace_last_line(text + "\n")

    def close_window(self):
        self._timer.stop()
        self.close()
