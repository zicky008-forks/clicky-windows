"""Small Windows focus helpers shared by dictation and Drive."""
import ctypes
import os
from ctypes import wintypes


def foreground():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    hwnd = user.GetForegroundWindow()
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    text = ctypes.create_unicode_buffer(4096)
    user.GetWindowTextW(hwnd, text, len(text))
    rect = wintypes.RECT()
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowRect(hwnd, ctypes.byref(rect))
    return {"hwnd": hwnd, "pid": pid.value, "title": text.value,
            "point": ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)}


def restore(context):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindow.argtypes = [wintypes.HWND]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    if not user.IsWindow(context["hwnd"]):
        raise OSError("The original window is no longer open")
    user.SetForegroundWindow(context["hwnd"])
    if foreground()["hwnd"] != context["hwnd"]:
        raise OSError("Select the original app and try again; Windows did not allow focus to change")


def front_kind(own_pid: int):
    """('excel' | 'own' | 'other', the window rect) of the window in front: Excel's main window is class XLMAIN."""
    from ui.fg_guard import EXCEL, OTHER, OWN
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    hwnd = user.GetForegroundWindow()
    if not hwnd:
        return OTHER, None
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == own_pid:
        return OWN, None
    name = ctypes.create_unicode_buffer(256)
    user.GetClassNameW(hwnd, name, len(name))
    if name.value != "XLMAIN":
        return OTHER, None
    rect = wintypes.RECT()
    user.GetWindowRect(hwnd, ctypes.byref(rect))
    return EXCEL, (rect.left, rect.top, rect.right, rect.bottom)
