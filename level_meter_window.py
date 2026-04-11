"""
level_meter_window.py – Floating audio level meter for MeetRec.
Shows live RMS bars for mic and system audio during any recording.
Thread-safe: call update_mic/update_sys from recorder threads via Qt signals.
"""

import time

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import QObject, Signal, Qt, QTimer, QRect, QPoint
from PySide6.QtGui import QPainter, QColor, QPen

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

STYLE_CLOSE = """
    QPushButton {
        color: #6c7086;
        background: transparent;
        border: none;
        font-size: 14px;
        padding: 2px 8px 2px 4px;
    }
    QPushButton:hover { color: #f38ba8; }
"""

_GREEN  = QColor("#a6e3a1")
_YELLOW = QColor("#f9e2af")
_RED    = QColor("#f38ba8")
_TRACK  = QColor("#313244")
_PEAK   = QColor("#cdd6f4")
_LABEL  = QColor("#cdd6f4")


class LevelBridge(QObject):
    """Emit from any thread; slots run on the Qt main thread via auto-queued connections."""

    _mic_level = Signal(float)
    _sys_level = Signal(float)
    _close_window = Signal()

    def __init__(self):
        super().__init__()

    def update_mic(self, rms: float):
        self._mic_level.emit(rms)

    def update_sys(self, rms: float):
        self._sys_level.emit(rms)

    def close(self):
        self._close_window.emit()


class AudioMeterWidget(QWidget):
    """Draws two horizontal RMS bars (Mic and Sys) with peak-hold indicators."""

    _DECAY = 0.15   # fraction to subtract per ~50 ms tick
    _SCALE = 8.0    # multiply raw RMS so typical speech fills ~60–80% of bar

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(52)
        self._mic = 0.0
        self._sys = 0.0
        self._mic_peak = 0.0
        self._sys_peak = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._decay_tick)
        self._timer.start(50)

    def stop(self):
        self._timer.stop()

    def set_mic(self, rms: float):
        val = min(rms * self._SCALE, 1.0)
        self._mic = val
        if val > self._mic_peak:
            self._mic_peak = val
        self.update()

    def set_sys(self, rms: float):
        val = min(rms * self._SCALE, 1.0)
        self._sys = val
        if val > self._sys_peak:
            self._sys_peak = val
        self.update()

    def _decay_tick(self):
        changed = False
        if self._mic_peak > self._mic:
            self._mic_peak = max(self._mic, self._mic_peak - self._DECAY)
            changed = True
        if self._sys_peak > self._sys:
            self._sys_peak = max(self._sys, self._sys_peak - self._DECAY)
            changed = True
        if changed:
            self.update()

    def _bar_color(self, level: float) -> QColor:
        if level < 0.6:
            return _GREEN
        if level < 0.85:
            return _YELLOW
        return _RED

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        label_w = 32
        bar_x = label_w + 4
        bar_w = w - bar_x - 8
        bar_h = 10
        row_h = h // 2

        for i, (level, peak, label) in enumerate(
            ((self._mic, self._mic_peak, "Mic"), (self._sys, self._sys_peak, "Sys"))
        ):
            cy = row_h * i + row_h // 2

            # Label
            painter.setPen(_LABEL)
            font = painter.font()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRect(4, cy - bar_h // 2 - 2, label_w - 4, bar_h + 4),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             label)

            # Track
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_TRACK)
            painter.drawRoundedRect(bar_x, cy - bar_h // 2, bar_w, bar_h, 3, 3)

            # Fill
            fill_w = int(bar_w * level)
            if fill_w > 0:
                painter.setBrush(self._bar_color(level))
                painter.drawRoundedRect(bar_x, cy - bar_h // 2, fill_w, bar_h, 3, 3)

            # Peak line
            peak_x = bar_x + min(int(bar_w * peak), bar_w - 1)
            if peak_x > bar_x:
                painter.setPen(QPen(_PEAK, 2))
                painter.drawLine(peak_x, cy - bar_h // 2, peak_x, cy + bar_h // 2)

        painter.end()


class LevelMeterWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeetRec — Audio Levels")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedSize(280, 90)
        self.setStyleSheet(STYLE_WINDOW)

        self._start_time = time.time()
        self._drag_pos: QPoint | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header row: recording label + close button
        header_row = QWidget()
        header_row.setStyleSheet("QWidget { background-color: #181825; border-radius: 8px 8px 0 0; }")
        row_layout = QHBoxLayout(header_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)

        self._header = QLabel("🔴  Recording  00:00")
        self._header.setStyleSheet(STYLE_HEADER)
        row_layout.addWidget(self._header, stretch=1)

        btn_close = QPushButton("✕")
        btn_close.setStyleSheet(STYLE_CLOSE)
        btn_close.setFixedWidth(28)
        btn_close.setToolTip("Close meter")
        btn_close.clicked.connect(self.close_window)
        row_layout.addWidget(btn_close)

        layout.addWidget(header_row)

        self._meter = AudioMeterWidget()
        layout.addWidget(self._meter)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        # Position near bottom-right of primary screen
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 16,
                  screen.bottom() - self.height() - 16)

        self.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _event):
        self._drag_pos = None

    def _tick(self):
        elapsed = int(time.time() - self._start_time)
        m, s = elapsed // 60, elapsed % 60
        self._header.setText(f"🔴  Recording  {m:02d}:{s:02d}")

    def set_mic(self, rms: float):
        self._meter.set_mic(rms)

    def set_sys(self, rms: float):
        self._meter.set_sys(rms)

    def close_window(self):
        self._timer.stop()
        self._meter.stop()
        self.close()
