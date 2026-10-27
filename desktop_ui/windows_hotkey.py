from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_SPACE = 0x20

HOTKEYS = {
    "Alt+Space": (MOD_ALT, VK_SPACE),
    "Ctrl+Alt+Space": (MOD_CONTROL | MOD_ALT, VK_SPACE),
    "Ctrl+Shift+Space": (MOD_CONTROL | MOD_SHIFT, VK_SPACE),
}


class GlobalHotkey:
    def __init__(self, root: object, callback: Callable[[], None]) -> None:
        self.root = root
        self.callback = callback
        self.hotkey_id = 0xA601
        self.current: str | None = None
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = (
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
        )
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self._poll()

    def register(self, choice: str) -> bool:
        self.unregister()
        modifiers, key = HOTKEYS[choice]
        if not self.user32.RegisterHotKey(None, self.hotkey_id, modifiers, key):
            return False
        self.current = choice
        return True

    def unregister(self) -> None:
        if self.current is not None:
            self.user32.UnregisterHotKey(None, self.hotkey_id)
            self.current = None

    def _poll(self) -> None:
        message = wintypes.MSG()
        while self.user32.PeekMessageW(
            ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE
        ):
            if message.wParam == self.hotkey_id:
                self.callback()
        self.root.after(80, self._poll)
