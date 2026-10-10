from __future__ import annotations

import ctypes
import math

from PySide6.QtCore import QPoint, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetricsF, QIcon, QMouseEvent, QPainter,
    QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QStyle, QStyleOptionButton,
    QVBoxLayout, QWidget,
)

from .assets import asset_path
from .models import TimeBankState, format_duration_parts

OVERLAY_DEFAULTS = {
    "enabled": True,
    "show_buttons": True,
    "show_hours": True,
    "show_minutes": True,
    "show_seconds": True,
    "stopped_tint": 0.24,
    "hover_opacity": 0.22,
    "time_display": "activity",
    "outline_width": 1.0,
    "timer_font_size": 36,
    "icon_font_size": 15,
    "work_color": "#a0c3ec",
    "play_color": "#ff6b62",
}

STOPPED_ACCENT = "#ffc285"


def _blend_color(base_value: str, overlay_value: str, opacity: float) -> str:
    """Return the opaque result of placing a translucent color over a base."""
    base = QColor(base_value)
    overlay = QColor(overlay_value)
    amount = max(0.0, min(1.0, opacity))
    return QColor(
        round(base.red() * (1 - amount) + overlay.red() * amount),
        round(base.green() * (1 - amount) + overlay.green() * amount),
        round(base.blue() * (1 - amount) + overlay.blue() * amount),
    ).name()

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
VK_CONTROL = 0x11


class _OutlinedText:
    """Paint a black edge around text without changing the widget surface."""

    def __init__(self) -> None:
        self.outline_width = 1.0
        self.text_font = QFont("Consolas")

    def set_outline_width(self, width: float) -> None:
        width = max(0.0, float(width))
        if self.outline_width == width:
            return
        self.outline_width = width
        self.updateGeometry()
        self.update()

    def set_text_pixel_size(self, size: int) -> None:
        size = max(1, int(size))
        if self.text_font.pixelSize() == size:
            return
        self.text_font.setPixelSize(size)
        self.updateGeometry()
        self.update()

    def _text_size_hint(self, text: str) -> QSize:
        metrics = QFontMetricsF(self.text_font)
        outline_padding = self.outline_width * 2
        return QSize(
            math.ceil(metrics.horizontalAdvance(text) + outline_padding + 2),
            math.ceil(metrics.height() + outline_padding + 2),
        )

    def _paint_text(self, painter: QPainter, text: str, font, rect) -> None:
        if not text:
            return
        path = QPainterPath()
        metrics = QFontMetricsF(font)
        baseline = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
        path.addText(0, baseline, font, text)
        bounds = path.boundingRect()
        path.translate(rect.center().x() - bounds.center().x(), 0)
        bounds = path.boundingRect()
        path.translate(0, rect.center().y() - bounds.center().y())
        if self.outline_width:
            # Draw the full-width stroke underneath the fill so the visible
            # edge extends outward from the original glyph silhouette.
            painter.setPen(QPen(QColor("#000000"), self.outline_width * 2,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(self.foregroundRole()))
        painter.drawPath(path)


class OutlinedLabel(_OutlinedText, QLabel):
    def __init__(self, text: str = "") -> None:
        QLabel.__init__(self, text)
        _OutlinedText.__init__(self)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self._paint_text(painter, self.text(), self.text_font, self.rect())

    def sizeHint(self) -> QSize:
        return self._text_size_hint(self.text())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()


class OutlinedButton(_OutlinedText, QPushButton):
    def __init__(self, text: str) -> None:
        QPushButton.__init__(self, text)
        _OutlinedText.__init__(self)

    def paintEvent(self, _event) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        text = option.text
        option.text = ""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        self._paint_text(painter, text, self.text_font, self.rect())

    def sizeHint(self) -> QSize:
        base = QPushButton.sizeHint(self)
        text = self._text_size_hint(self.text())
        return QSize(max(base.width(), text.width() + 24),
                     max(base.height(), text.height() + 10))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()


class TimerOverlay(QWidget):
    action_requested = Signal(str)

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.state = TimeBankState()
        self.drag_offset: QPoint | None = None
        self.click_through = False
        self.setWindowTitle("HabitReserve Timer")
        self.setWindowIcon(QIcon(str(asset_path("habitreserve.svg"))))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("overlayRoot")
        self.setFixedSize(250, 104)

        self.time_label = OutlinedLabel("00:00:00")
        self.time_label.setObjectName("timer")
        # Treat the full timer row as overlay drag space. Mouse input passes
        # through the label to TimerOverlay, while the controls below remain
        # ordinary interactive buttons.
        self.time_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.mark_buttons: list[OutlinedButton] = []
        self.controls = QWidget()
        self.controls.setObjectName("overlayControls")
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        for label, action in (("▶", "work"), ("■", "stop"), ("▶", "play")):
            button = OutlinedButton(label)
            button.setObjectName(action)
            button.setAccessibleName(action.capitalize())
            button.setToolTip(action.capitalize())
            button.clicked.connect(lambda _checked=False, value=action: self.action_requested.emit(value))
            self.mark_buttons.append(button)
            controls.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(self.time_label)
        layout.addWidget(self.controls)

        self.setStyleSheet("""
            QWidget#overlayRoot { background: transparent; border: 0; }
            QWidget#overlayControls { background: transparent; border: 0; }
            QLabel { background: transparent; border: 0; }
            QPushButton { background: transparent; border: 0; border-radius: 14px; padding: 5px 12px; }
            QPushButton#work { color: #a0c3ec; }
            QPushButton#stop { color: #ffffff; }
            QPushButton#play { color: #ff6b62; }
            QPushButton:hover { background: rgba(255, 255, 255, 28); }
            QPushButton:pressed { background: rgba(255, 255, 255, 55); }
        """)
        self._apply_font_sizes()
        position = settings.value("overlay/position")
        if isinstance(position, QPoint):
            self.move(position)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def set_state(self, state: TimeBankState) -> None:
        self.state = state
        self._tick()

    def _apply_font_sizes(self) -> None:
        timer_size = max(16, min(72, int(float(self.settings.value(
            "overlay/timer_font_size", OVERLAY_DEFAULTS["timer_font_size"]
        )))))
        icon_size = max(8, min(32, int(float(self.settings.value(
            "overlay/icon_font_size", OVERLAY_DEFAULTS["icon_font_size"]
        )))))
        self.time_label.set_text_pixel_size(timer_size)
        for button in self.mark_buttons:
            button.set_text_pixel_size(icon_size)
        self._resize_for_content()

    def _resize_for_content(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            content = layout.sizeHint()
            self.setFixedSize(max(250, content.width()), max(104, content.height()))

    def _tick(self) -> None:
        mode = self.state.mode
        time_display = str(self.settings.value("overlay/time_display", OVERLAY_DEFAULTS["time_display"]))
        seconds = self.state.live_balance() if time_display == "balance" else self.state.live_activity()
        self.time_label.setText(format_duration_parts(
            seconds,
            signed=time_display == "balance",
            show_hours=self.settings.value("overlay/show_hours", OVERLAY_DEFAULTS["show_hours"], bool),
            show_minutes=self.settings.value("overlay/show_minutes", OVERLAY_DEFAULTS["show_minutes"], bool),
            show_seconds=self.settings.value("overlay/show_seconds", OVERLAY_DEFAULTS["show_seconds"], bool),
        ))
        self.controls.setVisible(
            self.settings.value("overlay/show_buttons", OVERLAY_DEFAULTS["show_buttons"], bool)
        )
        self._apply_font_sizes()
        outline_width = max(0.0, min(8.0, float(self.settings.value(
            "overlay/outline_width", OVERLAY_DEFAULTS["outline_width"]
        ))))
        self.time_label.set_outline_width(outline_width)
        for button in self.mark_buttons:
            button.set_outline_width(outline_width)
        self._resize_for_content()
        display_mode = mode if mode in {"working", "playing"} else self.state.activity_mode
        work_color = str(self.settings.value("overlay/work_color", OVERLAY_DEFAULTS["work_color"]))
        play_color = str(self.settings.value("overlay/play_color", OVERLAY_DEFAULTS["play_color"]))
        color = work_color if display_mode == "working" else play_color if display_mode == "playing" else "#ffffff"
        if mode == "idle":
            stopped_tint = max(0.0, min(1.0, float(self.settings.value(
                "overlay/stopped_tint", OVERLAY_DEFAULTS["stopped_tint"]
            ))))
            color = _blend_color(color, STOPPED_ACCENT, stopped_tint)
        self.time_label.setStyleSheet(f"color: {color}; border: 0;")
        hovering = self.frameGeometry().contains(QCursor.pos())
        ctrl = self._control_is_pressed()
        should_pass = hovering and not ctrl
        if should_pass != self.click_through:
            self._set_click_through(should_pass)
        opacity = float(self.settings.value("overlay/hover_opacity", 0.22)) if should_pass else 1.0
        self.setWindowOpacity(opacity)

    @staticmethod
    def _control_is_pressed() -> bool:
        # Qt only reliably reports modifiers delivered to this process. The
        # overlay is normally click-through and unfocused, so query Windows'
        # global key state to make Ctrl take effect wherever the cursor entered.
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        except AttributeError:
            return bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)

    def _set_click_through(self, enabled: bool) -> None:
        self.click_through = enabled
        hwnd = int(self.winId())
        get_style = ctypes.windll.user32.GetWindowLongPtrW
        set_style = ctypes.windll.user32.SetWindowLongPtrW
        style = get_style(hwnd, GWL_EXSTYLE)
        set_style(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT if enabled else style & ~WS_EX_TRANSPARENT)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._control_is_pressed():
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        if self.drag_offset is not None:
            self.settings.setValue("overlay/position", self.pos())
            self.drag_offset = None
