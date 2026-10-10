from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

from .shortcuts import parse_hotkey

WM_HOTKEY = 0x0312


class WindowsHotkeys(QObject, QAbstractNativeEventFilter):
    activated = Signal(str)

    def __init__(self, window_id: int, user32=None) -> None:
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self._window_id = wintypes.HWND(window_id)
        self._user32 = user32 if user32 is not None else ctypes.windll.user32
        self._user32.RegisterHotKey.argtypes = (
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
        )
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._actions: dict[int, str] = {}

    def register(self, work: str, play: str) -> None:
        self.clear()
        for identifier, (action, sequence) in enumerate((("work", work), ("play", play)), 1):
            modifiers, key = parse_hotkey(sequence)
            # A real HWND makes WM_HOTKEY a window message. Null-handle
            # registrations become thread messages, which Qt 6 may not pass
            # through an application-level native event filter on Windows.
            if not self._user32.RegisterHotKey(self._window_id, identifier, modifiers, key):
                self.clear()
                raise ValueError(f"Shortcut {sequence} is already in use")
            self._actions[identifier] = action

    def clear(self) -> None:
        for identifier in self._actions:
            self._user32.UnregisterHotKey(self._window_id, identifier)
        self._actions.clear()

    def nativeEventFilter(self, event_type, message):
        native_type = bytes(event_type) if not isinstance(event_type, bytes) else event_type
        if native_type in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}:
            address = message.__int__() if hasattr(message, "__int__") else int(message)
            event = wintypes.MSG.from_address(address)
            if event.message == WM_HOTKEY and int(event.wParam) in self._actions:
                self.activated.emit(self._actions[int(event.wParam)])
                return True, 0
        return False, 0
