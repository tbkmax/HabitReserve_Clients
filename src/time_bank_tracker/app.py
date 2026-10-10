from __future__ import annotations

import threading
import time
import webbrowser
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QCloseEvent, QColor, QCursor, QMouseEvent, QPaintEvent, QPainter,
    QPainterPath, QPen, QRegion, QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QSlider,
    QScrollArea, QSystemTrayIcon,
    QTabWidget, QVBoxLayout, QWidget, QMenu, QStackedWidget,
)

from .api import APIError, TimeBankAPI, TokenPair
from .auth import BrowserLogin, clear_refresh_token, open_registration, save_refresh_token, saved_refresh_token
from .config import packaged_build_config, server_url, tls_ca_file
from .hotkeys import WindowsHotkeys
from .models import TimeBankState, format_duration
from .overlay import OVERLAY_DEFAULTS, TimerOverlay
from . import __version__
from .updater import Release, fetch_release, is_newer

STYLE = """
QWidget { background: #0a0a0a; color: #dadbdf; font: 14px 'Segoe UI'; }
QMainWindow { background: transparent; border: 0; }
QWidget#windowRoot { background: #0a0a0a; border: 1px solid #2a2c30; border-radius: 10px; }
QWidget#titleBar { background: #0a0a0a; border: 0; border-bottom: 1px solid #15171a; }
QLabel, QCheckBox, QSlider { background: transparent; }
QPushButton#windowControl { padding: 0; border: 0; border-radius: 0; background: transparent; }
QPushButton#windowControl:hover { background: #1a1c20; }
QPushButton#closeControl { padding: 0; border: 0; border-radius: 0; background: transparent; }
QPushButton#closeControl:hover { background: #da373c; }
QTabWidget::pane { border: 0; border-top: 1px solid #212327; }
QTabBar::tab { min-width: 100px; padding: 11px 16px; color: #7d8187; border: 0; }
QTabBar::tab:hover { color: #dadbdf; background: #111214; }
QTabBar::tab:selected { color: #ffffff; border-bottom: 1px solid #ffffff; background: #0a0a0a; }
QPushButton { background: #0a0a0a; color: #ffffff; border: 1px solid #363a3f; border-radius: 17px; padding: 7px 15px; }
QPushButton:hover { border-color: rgba(255, 255, 255, 90); background: #111214; }
QPushButton:pressed { background: #1a1c20; }
QPushButton#primary { background: #ffffff; color: #0a0a0a; border-color: #ffffff; }
QPushButton#primary:hover { background: #fafaf7; }
QPushButton#dangerButton { color: #ff8a8f; border-color: rgba(255, 107, 98, 90); }
QLineEdit { background: #1a1c20; border: 1px solid #212327; border-radius: 8px; padding: 9px; }
QLineEdit:focus { border-color: rgba(255, 255, 255, 90); }
QComboBox { background: #1a1c20; border: 1px solid #212327; border-radius: 8px; padding: 7px 10px; }
QScrollArea#overlaySettingsScroll { background: #0a0a0a; border: 0; }
QScrollBar:vertical { background: #111214; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #363a3f; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #111214; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #363a3f; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QLabel#eyebrow { color: #7d8187; font: 12px 'Consolas'; letter-spacing: 2px; }
QLabel#pageTitle { color: #ffffff; font: 24px 'Segoe UI'; }
QLabel#bodyMuted { color: #7d8187; font-size: 12px; }
QLabel#sliderValue { color: #dadbdf; font: 12px 'Consolas'; min-width: 42px; }
QWidget#homeCard, QWidget#pageCard { background: #191919; border: 1px solid #2a2c30; border-radius: 8px; }
QLabel#metricLabel { color: #7d8187; font: 11px 'Consolas'; letter-spacing: 1px; }
QLabel#balance { color: #ffffff; font: 44px 'Consolas'; }
QLabel#activity { font: 23px 'Consolas'; }
QFrame#divider { background: #2a2c30; border: 0; max-height: 1px; }
QPushButton#workButton { color: #a0c3ec; border-color: rgba(160, 195, 236, 90); }
QPushButton#playButton { color: #ffc285; border-color: rgba(255, 194, 133, 90); }
QPushButton#stopButton { color: #a5a7ac; border-color: #2a2c30; }
QLabel#loginTitle { color: #ffffff; font: 32px 'Segoe UI'; }
"""


class Bridge(QObject):
    succeeded = Signal(object)
    failed = Signal(str, int, str)
    live_state = Signal(object)


class WindowControlButton(QPushButton):
    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.setObjectName("closeControl" if kind == "close" else "windowControl")
        self.setFixedSize(46, 35)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        label = "Close" if kind == "close" else "Minimize"
        self.setAccessibleName(label)
        self.setToolTip("Close to system tray" if kind == "close" else label)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        color = "#ffffff" if self.underMouse() and self.kind == "close" else "#dadbdf"
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.kind == "minimize":
            painter.drawLine(QPointF(18, 18), QPointF(28, 18))
        else:
            painter.drawLine(QPointF(19, 13), QPointF(27, 21))
            painter.drawLine(QPointF(27, 13), QPointF(19, 21))


class TitleBar(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window = window
        self.drag_offset = None
        self.setObjectName("titleBar")
        self.setFixedHeight(35)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch()
        minimize = WindowControlButton("minimize")
        minimize.clicked.connect(window.showMinimized)
        close = WindowControlButton("close")
        close.clicked.connect(window.close)
        layout.addWidget(minimize)
        layout.addWidget(close)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window.windowHandle()
            if handle is None or not handle.startSystemMove():
                self.drag_offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.drag_offset = None
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self, owner: "TimeBankApplication"):
        super().__init__()
        self.owner = owner
        self.setWindowTitle("HabitReserve")
        self.setWindowIcon(self.owner.application.windowIcon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(680, 520)
        self.pages = QStackedWidget()
        self.login_page = self._login()
        self.application_page = self._application()
        self.pages.addWidget(self.login_page)
        self.pages.addWidget(self.application_page)
        root = QWidget()
        root.setObjectName("windowRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(TitleBar(self))
        root_layout.addWidget(self.pages)
        self.setCentralWidget(root)

    def resizeEvent(self, event: QResizeEvent) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        super().resizeEvent(event)

    def _login(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(88, 48, 88, 58)
        card = QWidget()
        card.setObjectName("pageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(40, 32, 40, 32)
        card_layout.setSpacing(12)
        eyebrow = QLabel("HABITRESERVE / DESKTOP")
        eyebrow.setObjectName("eyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Sign in to continue")
        title.setObjectName("loginTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description = QLabel("Connect your HabitReserve account before using the timer, shortcuts, or settings.")
        description.setObjectName("bodyMuted")
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.login_status = QLabel("Use your browser to sign in securely.")
        self.login_status.setWordWrap(True)
        self.login_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.login_button = QPushButton("Log in with browser")
        self.login_button.setObjectName("primary")
        self.login_button.clicked.connect(self.owner.browser_login)
        self.register_button = QPushButton("Create an account")
        self.register_button.clicked.connect(self.owner.open_registration)
        card_layout.addWidget(eyebrow)
        card_layout.addSpacing(6)
        card_layout.addWidget(title)
        card_layout.addWidget(description)
        card_layout.addSpacing(8)
        card_layout.addWidget(self.login_button)
        card_layout.addWidget(self.register_button)
        card_layout.addWidget(self.login_status)
        layout.addStretch()
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _application(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._home(), "HOME")
        tabs.addTab(self._overlay_settings(), "ON-TOP TIMER")
        tabs.addTab(self._settings(), "SETTINGS")
        return tabs

    def show_logged_out(self, message: str, busy: bool = False) -> None:
        self.login_status.setText(message)
        self.login_button.setDisabled(busy)
        self.register_button.setDisabled(busy)
        self.pages.setCurrentWidget(self.login_page)

    def show_authenticated(self) -> None:
        self.login_button.setEnabled(True)
        self.register_button.setEnabled(True)
        self.pages.setCurrentWidget(self.application_page)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _home(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(38, 24, 38, 30)
        layout.setSpacing(14)
        eyebrow = QLabel("HABITRESERVE / LIVE")
        eyebrow.setObjectName("eyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QWidget()
        card.setObjectName("homeCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(34, 24, 34, 24)
        card_layout.setSpacing(10)
        balance_label = QLabel("AVAILABLE BALANCE")
        balance_label.setObjectName("metricLabel")
        balance_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.balance = QLabel("00:00:00")
        self.balance.setObjectName("balance")
        self.balance.setAlignment(Qt.AlignmentFlag.AlignCenter)
        activity_label = QLabel("CURRENT ACTIVITY")
        activity_label.setObjectName("metricLabel")
        activity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.activity = QLabel("00:00:00")
        self.activity.setObjectName("activity")
        self.activity.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_name = QLabel("")
        self.display_name.setObjectName("bodyMuted")
        self.display_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.Shape.HLine)
        controls = QHBoxLayout()
        controls.setSpacing(10)
        for label, action in (("Start work", "work"), ("Stop", "stop"), ("Start play", "play")):
            button = QPushButton(label)
            button.setObjectName(f"{action}Button")
            button.clicked.connect(lambda _checked=False, value=action: self.owner.request_action(value))
            controls.addWidget(button)
        self.status = QLabel("Loading your HabitReserve…")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setObjectName("bodyMuted")
        card_layout.addWidget(eyebrow)
        card_layout.addStretch()
        card_layout.addWidget(balance_label)
        card_layout.addWidget(self.balance)
        card_layout.addSpacing(4)
        card_layout.addWidget(activity_label)
        card_layout.addWidget(self.activity)
        card_layout.addWidget(self.display_name)
        card_layout.addSpacing(4)
        card_layout.addWidget(divider)
        card_layout.addSpacing(4)
        card_layout.addLayout(controls)
        card_layout.addStretch()
        card_layout.addWidget(self.status)
        layout.addWidget(card, 1)
        return page

    def _overlay_settings(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(38, 14, 38, 18)
        layout.setSpacing(8)
        title = QLabel("On-top timer")
        title.setObjectName("pageTitle")
        card = QWidget()
        card.setObjectName("pageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 14, 24, 16)
        card_layout.setSpacing(7)
        self.overlay_enabled = QCheckBox("Show the always-on-top timer")
        self.overlay_enabled.setChecked(
            self.owner.settings.value("overlay/enabled", OVERLAY_DEFAULTS["enabled"], bool)
        )
        self.overlay_enabled.toggled.connect(self.owner.set_overlay_visible)
        self.overlay_buttons = QCheckBox("Show start and stop buttons")
        self.overlay_buttons.setChecked(
            self.owner.settings.value("overlay/show_buttons", OVERLAY_DEFAULTS["show_buttons"], bool)
        )
        self.overlay_buttons.toggled.connect(
            lambda checked: self.owner.settings.setValue("overlay/show_buttons", checked)
        )
        hint = QLabel("Move the timer by holding Ctrl while dragging. Without Ctrl, hovering makes it click-through.")
        hint.setObjectName("bodyMuted")
        hint.setWordWrap(True)
        opacity = QSlider(Qt.Orientation.Horizontal)
        opacity.setRange(5, 80)
        opacity.setValue(int(float(self.owner.settings.value(
            "overlay/hover_opacity", OVERLAY_DEFAULTS["hover_opacity"]
        )) * 100))
        opacity.valueChanged.connect(lambda value: self.owner.settings.setValue("overlay/hover_opacity", value / 100))
        timer_display = QComboBox()
        timer_display.addItem("Current activity time", "activity")
        timer_display.addItem("Total HabitReserve balance", "balance")
        selected_display = str(self.owner.settings.value(
            "overlay/time_display", OVERLAY_DEFAULTS["time_display"]
        ))
        timer_display.setCurrentIndex(max(0, timer_display.findData(selected_display)))
        timer_display.currentIndexChanged.connect(
            lambda _index: self.owner.settings.setValue("overlay/time_display", timer_display.currentData())
        )
        self.outline_width, outline_control = self._setting_slider(
            0,
            16,
            round(float(self.owner.settings.value(
                "overlay/outline_width", OVERLAY_DEFAULTS["outline_width"]
            )) * 2),
            lambda value: f"{value / 2:g} px",
            lambda value: self.owner.settings.setValue("overlay/outline_width", value / 2),
        )
        outline_control.setToolTip("Black edge around the timer and its start/stop marks")
        self.timer_font_size, timer_size_control = self._setting_slider(
            16,
            72,
            int(float(self.owner.settings.value(
                "overlay/timer_font_size", OVERLAY_DEFAULTS["timer_font_size"]
            ))),
            lambda value: f"{value} px",
            lambda value: self.owner.settings.setValue("overlay/timer_font_size", value),
        )
        self.icon_font_size, icon_size_control = self._setting_slider(
            8,
            32,
            int(float(self.owner.settings.value(
                "overlay/icon_font_size", OVERLAY_DEFAULTS["icon_font_size"]
            ))),
            lambda value: f"{value} px",
            lambda value: self.owner.settings.setValue("overlay/icon_font_size", value),
        )
        self.stopped_tint, stopped_tint_control = self._setting_slider(
            0,
            100,
            round(float(self.owner.settings.value(
                "overlay/stopped_tint", OVERLAY_DEFAULTS["stopped_tint"]
            )) * 100),
            lambda value: f"{value}%",
            lambda value: self.owner.settings.setValue("overlay/stopped_tint", value / 100),
        )
        time_parts = QWidget()
        time_parts_layout = QHBoxLayout(time_parts)
        time_parts_layout.setContentsMargins(0, 0, 0, 0)
        time_parts_layout.setSpacing(14)
        self.time_part_checks: dict[str, QCheckBox] = {}
        for component, label in (("hours", "Hours"), ("minutes", "Minutes"), ("seconds", "Seconds")):
            checkbox = QCheckBox(label)
            checkbox.setChecked(self.owner.settings.value(
                f"overlay/show_{component}", OVERLAY_DEFAULTS[f"show_{component}"], bool
            ))
            checkbox.toggled.connect(
                lambda checked, name=component: self._set_time_component(name, checked)
            )
            self.time_part_checks[component] = checkbox
            time_parts_layout.addWidget(checkbox)
        if not any(checkbox.isChecked() for checkbox in self.time_part_checks.values()):
            self.time_part_checks["seconds"].setChecked(True)
        time_parts_layout.addStretch()
        self.work_color = QPushButton(str(self.owner.settings.value(
            "overlay/work_color", OVERLAY_DEFAULTS["work_color"]
        )))
        self.play_color = QPushButton(str(self.owner.settings.value(
            "overlay/play_color", OVERLAY_DEFAULTS["play_color"]
        )))
        self.work_color.clicked.connect(lambda: self._choose_overlay_color("work", self.work_color))
        self.play_color.clicked.connect(lambda: self._choose_overlay_color("play", self.play_color))
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.addRow("Hover opacity", opacity)
        form.addRow("Timer display", timer_display)
        form.addRow("Time parts", time_parts)
        form.addRow("Black outline width", outline_control)
        form.addRow("Timer number size", timer_size_control)
        form.addRow("Button icon size", icon_size_control)
        form.addRow("Stopped yellow tint", stopped_tint_control)
        form.addRow("Work color", self.work_color)
        form.addRow("Play color", self.play_color)
        card_layout.addWidget(self.overlay_enabled)
        card_layout.addWidget(self.overlay_buttons)
        card_layout.addWidget(hint)
        card_layout.addLayout(form)
        reset = QPushButton("Reset on-top timer settings")
        reset.clicked.connect(lambda: self._reset_overlay_settings(opacity, timer_display))
        card_layout.addWidget(reset)
        layout.addWidget(title)
        layout.addWidget(card)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setObjectName("overlaySettingsScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        page.setMinimumHeight(page.sizeHint().height())
        scroll.setWidget(page)
        return scroll

    def _setting_slider(
        self,
        minimum: int,
        maximum: int,
        value: int,
        formatter: Callable[[int], str],
        changed: Callable[[int], None],
    ) -> tuple[QSlider, QWidget]:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        value_label = QLabel(formatter(value))
        value_label.setObjectName("sliderValue")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider.valueChanged.connect(lambda current: value_label.setText(formatter(current)))
        slider.valueChanged.connect(changed)
        layout.addWidget(slider, 1)
        layout.addWidget(value_label)
        return slider, container

    def _set_time_component(self, component: str, checked: bool) -> None:
        if not checked and not any(
            checkbox.isChecked() for name, checkbox in self.time_part_checks.items() if name != component
        ):
            self.time_part_checks[component].setChecked(True)
            return
        self.owner.settings.setValue(f"overlay/show_{component}", checked)

    def _reset_overlay_settings(self, opacity: QSlider, timer_display: QComboBox) -> None:
        for key, value in OVERLAY_DEFAULTS.items():
            self.owner.settings.setValue(f"overlay/{key}", value)
        self.overlay_enabled.setChecked(OVERLAY_DEFAULTS["enabled"])
        self.overlay_buttons.setChecked(OVERLAY_DEFAULTS["show_buttons"])
        opacity.setValue(round(OVERLAY_DEFAULTS["hover_opacity"] * 100))
        timer_display.setCurrentIndex(timer_display.findData(OVERLAY_DEFAULTS["time_display"]))
        self.outline_width.setValue(round(OVERLAY_DEFAULTS["outline_width"] * 2))
        self.timer_font_size.setValue(OVERLAY_DEFAULTS["timer_font_size"])
        self.icon_font_size.setValue(OVERLAY_DEFAULTS["icon_font_size"])
        self.stopped_tint.setValue(round(OVERLAY_DEFAULTS["stopped_tint"] * 100))
        for component, checkbox in self.time_part_checks.items():
            checkbox.setChecked(OVERLAY_DEFAULTS[f"show_{component}"])
        self.work_color.setText(OVERLAY_DEFAULTS["work_color"])
        self.play_color.setText(OVERLAY_DEFAULTS["play_color"])

    def _choose_overlay_color(self, mode: str, button: QPushButton) -> None:
        default = "#a0c3ec" if mode == "work" else "#ff6b62"
        current = QColor(str(self.owner.settings.value(f"overlay/{mode}_color", default)))
        color = QColorDialog.getColor(current, self, f"Choose {mode} timer color")
        if color.isValid():
            value = color.name()
            self.owner.settings.setValue(f"overlay/{mode}_color", value)
            button.setText(value)

    def _settings(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(38, 20, 38, 28)
        layout.setSpacing(12)
        title = QLabel("Local settings")
        title.setObjectName("pageTitle")
        subtitle = QLabel(f"Keyboard shortcuts and updates for this device · Version {__version__}")
        subtitle.setObjectName("bodyMuted")
        card = QWidget()
        card.setObjectName("pageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.work_shortcut = QLineEdit(str(self.owner.settings.value("hotkeys/work", "Ctrl+Alt+W")))
        self.play_shortcut = QLineEdit(str(self.owner.settings.value("hotkeys/play", "Ctrl+Alt+P")))
        save = QPushButton("Save local settings")
        save.setObjectName("primary")
        save.clicked.connect(self._save_settings)
        logout = QPushButton("Log out of this device")
        logout.setObjectName("dangerButton")
        logout.clicked.connect(self.owner.logout)
        form.addRow("Work shortcut", self.work_shortcut)
        form.addRow("Play shortcut", self.play_shortcut)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(save)
        actions.addWidget(logout)
        card_layout.addLayout(form)
        card_layout.addSpacing(8)
        card_layout.addLayout(actions)
        card_layout.addSpacing(20)
        self.auto_update_checks = QCheckBox("Check for updates automatically")
        self.auto_update_checks.setChecked(self.owner.settings.value("updates/automatic", True, bool))
        self.auto_update_checks.toggled.connect(self.owner.set_automatic_update_checks)
        check_updates = QPushButton("Check for updates")
        check_updates.clicked.connect(lambda: self.owner.check_for_updates(manual=True))
        self.update_status = QLabel("Updates come from the public GitHub Releases page.")
        self.update_status.setObjectName("bodyMuted")
        card_layout.addWidget(self.auto_update_checks)
        card_layout.addWidget(check_updates)
        card_layout.addWidget(self.update_status)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _save_settings(self) -> None:
        try:
            self.owner.update_settings(self.work_shortcut.text(), self.play_shortcut.text())
            self.status.setText("Settings saved.")
        except ValueError as error:
            QMessageBox.warning(self, "Shortcut unavailable", str(error))

    def set_display_name(self, name: str) -> None:
        self.display_name.setText(f"Signed in as {name}" if name else "")

    def render(self, state: TimeBankState, pending: bool = False) -> None:
        self.balance.setText(format_duration(state.live_balance(), signed=True))
        activity_mode = state.mode if state.mode in {"working", "playing"} else state.activity_mode
        self.activity.setText(format_duration(state.live_activity()))
        color = "#a0c3ec" if activity_mode == "working" else "#ffc285" if activity_mode == "playing" else "#ffffff"
        self.activity.setStyleSheet(f"color: {color};")
        self.status.setText("Saving timer change…" if pending else "Synced with HabitReserve")

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()


class TimeBankApplication:
    def __init__(self, application: QApplication):
        self.application = application
        self.application.setStyleSheet(STYLE)
        self.settings = QSettings()
        configured_server_url = server_url()
        self.api = TimeBankAPI(configured_server_url, tls_ca_file(configured_server_url))
        self.state = TimeBankState()
        self.action_in_flight = False
        self.queued_actions: list[tuple[str, datetime, float]] = []
        self.previous_action_state: TimeBankState | None = None
        self.deferred_state: TimeBankState | None = None
        self.pending_action_anchor: datetime | None = None
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="time-bank")
        self.bridge = Bridge()
        self.bridge.succeeded.connect(self._task_succeeded)
        self.bridge.failed.connect(self._task_failed)
        self.bridge.live_state.connect(self._accept_state)
        self.live_stop = threading.Event()
        self.live_thread: threading.Thread | None = None
        self.overlay = TimerOverlay(self.settings)
        self.overlay.action_requested.connect(self.request_action)
        self.window = MainWindow(self)
        self.hotkeys = WindowsHotkeys(int(self.window.winId()))
        self.hotkeys.activated.connect(self.request_action)
        self.application.installNativeEventFilter(self.hotkeys)
        self.access_expires_at = datetime.min.replace(tzinfo=timezone.utc)
        self.refresh_token = ""
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_state)
        self.refresh_timer.start(30_000)
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self._render)
        self.display_timer.start(250)
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.check_for_updates)
        self.update_timer.start(60 * 60 * 1000)
        self._create_tray()
        self.update_check_in_flight = False

    def start(self) -> None:
        self.window.show()
        self.overlay.hide()
        self.check_for_updates()
        token = saved_refresh_token()
        if token:
            self.refresh_token = token
            self.window.show_logged_out("Restoring your session…", busy=True)
            self._submit(lambda: self.api.refresh(token), "tokens")
        else:
            self.window.show_logged_out("Use your browser to sign in securely.")

    def browser_login(self) -> None:
        self.window.show_logged_out("Complete sign-in in your browser…", busy=True)
        self._submit(lambda: BrowserLogin(self.api).run(), "tokens")

    def open_registration(self) -> None:
        open_registration(self.api)
        self.window.show_logged_out("Create your account in the browser, then return here to log in.")

    def request_action(self, action: str) -> None:
        if not self.api.access_token:
            self.browser_login()
            return
        effective = "stop" if action in {"work", "play"} and self.state.mode == {"work": "working", "play": "playing"}[action] else action
        if self.action_in_flight:
            clicked_at = datetime.now(timezone.utc)
            clicked_monotonic = time.monotonic()
            self.queued_actions.append((effective, clicked_at, clicked_monotonic))
            self._apply_optimistic_action(effective, clicked_at)
            return
        expected_version = self.state.version
        self.previous_action_state = self.state
        self.action_in_flight = True
        clicked_at = datetime.now(timezone.utc)
        clicked_monotonic = time.monotonic()
        self.pending_action_anchor = clicked_at
        self._apply_optimistic_action(effective, clicked_at)
        self._submit(
            lambda: self.api.action(effective, clicked_at, expected_version, clicked_monotonic),
            "action",
        )

    def _apply_optimistic_action(self, action: str, clicked_at: datetime) -> None:
        # This local preview keeps the same version until the server replies.
        # _accept_state rejects same-version updates during an action, so apply
        # the preview directly to stop the visible timer at the click boundary.
        self.state = self.state.preview_action(action, clicked_at)
        self._render()

    def refresh_state(self) -> None:
        if not self.refresh_token or self.action_in_flight:
            return
        if datetime.now(timezone.utc) >= self.access_expires_at - timedelta(minutes=2):
            self._submit(lambda: self.api.refresh(self.refresh_token), "tokens")
        else:
            self._submit(self.api.state, "state")

    def logout(self) -> None:
        self.live_stop.set()
        self.queued_actions.clear()
        token = self.refresh_token
        if token:
            self.executor.submit(lambda: self.api.logout(token))
        clear_refresh_token()
        self.refresh_token = ""
        self.api.access_token = ""
        self.hotkeys.clear()
        self.state = TimeBankState()
        self.window.set_display_name("")
        self.overlay.set_state(self.state)
        self.overlay.hide()
        self.window.show_logged_out("Logged out of this device.")

    def update_settings(self, work: str, play: str) -> None:
        self.hotkeys.register(work, play)
        self.settings.setValue("hotkeys/work", work)
        self.settings.setValue("hotkeys/play", play)
        if self.api.access_token:
            self._start_live_updates()

    def set_automatic_update_checks(self, enabled: bool) -> None:
        self.settings.setValue("updates/automatic", enabled)
        if enabled:
            self.check_for_updates()

    def check_for_updates(self, manual: bool = False) -> None:
        if self.update_check_in_flight:
            return
        if not manual:
            if not packaged_build_config().get("release") or not self.settings.value("updates/automatic", True, bool):
                return
            last_attempt = float(self.settings.value("updates/last_checked_at", 0) or 0)
            if time.time() - last_attempt < 24 * 60 * 60:
                return
            self.settings.setValue("updates/last_checked_at", time.time())
        self.update_check_in_flight = True
        if manual:
            self.window.update_status.setText("Checking GitHub Releases…")
        self._submit(fetch_release, "update_check_manual" if manual else "update_check_auto")

    def _offer_update(self, release: Release, manual: bool) -> None:
        if not is_newer(release.version):
            if manual:
                self.window.update_status.setText(f"Version {__version__} is up to date.")
                QMessageBox.information(self.window, "No update available", "You are using the latest HabitReserve version.")
            return
        if not manual and self.settings.value("updates/skipped_version", "") == release.version:
            return
        self.window.update_status.setText(f"Version {release.version} is available on GitHub Releases.")
        choice = QMessageBox.question(
            self.window, "HabitReserve update available",
            f"Version {release.version} is available.\n\nOpen its GitHub Release page to download the portable app?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice != QMessageBox.StandardButton.Yes:
            self.settings.setValue("updates/skipped_version", release.version)
            return
        webbrowser.open_new(release.release_notes_url)

    def set_overlay_visible(self, visible: bool) -> None:
        self.settings.setValue("overlay/enabled", visible)
        self.overlay.setVisible(visible and bool(self.api.access_token))

    def _submit(self, function, kind: str) -> None:
        future = self.executor.submit(function)
        def completed(result):
            try:
                self.bridge.succeeded.emit((kind, result.result()))
            except APIError as error:
                self.bridge.failed.emit(str(error), error.status, kind)
            except Exception as error:
                self.bridge.failed.emit(str(error), 0, kind)
        future.add_done_callback(completed)

    def _task_succeeded(self, value: tuple[str, object]) -> None:
        kind, result = value
        if kind in {"update_check_auto", "update_check_manual"} and isinstance(result, Release):
            self.update_check_in_flight = False
            self._offer_update(result, manual=kind == "update_check_manual")
        elif kind == "tokens" and isinstance(result, TokenPair):
            self.api.access_token = result.access_token
            self.refresh_token = result.refresh_token
            save_refresh_token(result.refresh_token)
            self.access_expires_at = datetime.now(timezone.utc) + timedelta(seconds=result.expires_in)
            self._start_live_updates()
            self.window.show_authenticated()
            self._submit(self.api.display_name, "profile")
            try:
                self.hotkeys.register(
                    str(self.settings.value("hotkeys/work", "Ctrl+Alt+W")),
                    str(self.settings.value("hotkeys/play", "Ctrl+Alt+P")),
                )
            except ValueError as error:
                self.window.status.setText(str(error))
            self.set_overlay_visible(self.settings.value("overlay/enabled", True, bool))
            self.refresh_state()
        elif kind == "action" and isinstance(result, TimeBankState):
            if self.queued_actions:
                action, clicked_at, clicked_monotonic = self.queued_actions.pop(0)
                self.previous_action_state = result
                self.pending_action_anchor = clicked_at
                self._submit(
                    lambda: self.api.action(action, clicked_at, result.version, clicked_monotonic),
                    "action",
                )
                return
            self.action_in_flight = False
            self.previous_action_state = None
            self.pending_action_anchor = None
            self._accept_state(result)
            if self.deferred_state is not None:
                deferred = self.deferred_state
                self.deferred_state = None
                self._accept_state(deferred)
        elif kind == "state" and isinstance(result, TimeBankState):
            self._accept_state(result)
        elif kind == "profile" and isinstance(result, str):
            self.window.set_display_name(result)

    def _accept_state(self, state: TimeBankState) -> None:
        if self.action_in_flight:
            if self.deferred_state is None or state.version > self.deferred_state.version:
                self.deferred_state = state
            return
        if (
            state.version == self.state.version
            and state.server_now is not None
            and self.state.server_now is not None
            and state.server_now < self.state.server_now
        ):
            return
        if state.version < self.state.version or (
            state.version == self.state.version
            and state.balance_seconds == self.state.balance_seconds
            and state.mode == self.state.mode
            and state.activity_mode == self.state.activity_mode
            and state.activity_elapsed_seconds == self.state.activity_elapsed_seconds
        ):
            return
        self.state = state
        self.overlay.set_state(state)
        self._render()

    def _start_live_updates(self) -> None:
        self.live_stop.set()
        self.live_stop = threading.Event()
        stop = self.live_stop

        def listen() -> None:
            while not stop.is_set() and self.api.access_token:
                try:
                    for state in self.api.events(stop):
                        self.bridge.live_state.emit(state)
                except APIError as error:
                    if error.status == 401:
                        return
                stop.wait(2)

        self.live_thread = threading.Thread(target=listen, name="time-bank-live", daemon=True)
        self.live_thread.start()

    def _task_failed(self, message: str, status: int, kind: str) -> None:
        if kind in {"update_check_auto", "update_check_manual"}:
            self.update_check_in_flight = False
            if kind == "update_check_manual":
                self.window.update_status.setText("Could not check for updates.")
                QMessageBox.warning(self.window, "Update check failed", message)
            return
        if kind == "action":
            self.action_in_flight = False
            self.queued_actions.clear()
            self.deferred_state = None
            self.pending_action_anchor = None
            previous = self.previous_action_state
            self.previous_action_state = None
            if previous is not None:
                self.state = previous
                self._render()
        if status == 401:
            clear_refresh_token()
            self.refresh_token = ""
            self.api.access_token = ""
            self.hotkeys.clear()
            self.overlay.hide()
            self.window.set_display_name("")
            self.window.show_logged_out("Your session ended. Log in again to continue.")
            return
        if self.api.access_token:
            self.window.status.setText(message)
            if kind == "action":
                self.refresh_state()
        else:
            self.window.show_logged_out(message)

    def _render(self) -> None:
        self.window.render(self.state, pending=self.action_in_flight) if self.api.access_token else None
        self.overlay.set_state(self.state)

    def _create_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.application.windowIcon())
        self.tray_menu = QMenu()
        show = QAction("Open HabitReserve", self.tray_menu)
        show.triggered.connect(self.window.show)
        check_updates = QAction("Check for updates", self.tray_menu)
        check_updates.triggered.connect(lambda: self.check_for_updates(manual=True))
        quit_action = QAction("Quit", self.tray_menu)
        quit_action.triggered.connect(self.application.quit)
        self.tray_menu.addAction(show)
        self.tray_menu.addAction(check_updates)
        self.tray_menu.addAction(quit_action)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.window.show()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            self._show_tray_menu()

    def _show_tray_menu(self) -> None:
        cursor = QCursor.pos()
        screen = self.application.screenAt(cursor) or self.application.primaryScreen()
        menu_size = self.tray_menu.sizeHint()
        if screen is None:
            self.tray_menu.popup(cursor - QPoint(menu_size.width(), menu_size.height() + 16))
            return

        available = screen.availableGeometry()
        margin = 12
        x = max(available.left() + margin,
                min(cursor.x() - menu_size.width() + margin,
                    available.right() - menu_size.width() - margin))
        if cursor.y() > available.bottom():
            # A bottom taskbar: keep the menu, including Quit, clearly above it.
            y = available.bottom() - menu_size.height() - margin
        elif cursor.y() < available.top():
            y = available.top() + margin
        else:
            y = max(available.top() + margin,
                    min(cursor.y() - menu_size.height() // 2,
                        available.bottom() - menu_size.height() - margin))
        self.tray_menu.popup(QPoint(x, y))
