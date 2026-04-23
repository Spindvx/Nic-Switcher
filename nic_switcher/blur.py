"""Windows acrylic/mica blur for PyQt frameless windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19


def enable_blur(hwnd: int, tint_abgr: int = 0xC0181818) -> bool:
    try:
        user32 = ctypes.windll.user32
        set_attr = user32.SetWindowCompositionAttribute
        policy = ACCENT_POLICY(ACCENT_ENABLE_ACRYLICBLURBEHIND, 2, tint_abgr, 0)
        data = WINCOMPATTRDATA(
            WCA_ACCENT_POLICY,
            ctypes.pointer(policy),
            ctypes.sizeof(policy),
        )
        return bool(set_attr(wintypes.HWND(hwnd), ctypes.pointer(data)))
    except Exception:
        return False


def try_enable_mica(hwnd: int) -> bool:
    """Windows 11 22H2+: DWMWA_SYSTEMBACKDROP_TYPE=3 (Mica)."""
    try:
        dwmapi = ctypes.windll.dwmapi
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        value = ctypes.c_int(3)
        hr = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return hr == 0
    except Exception:
        return False
