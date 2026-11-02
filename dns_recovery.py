"""Windows DNS repair, elevated only for the fixed ipconfig command."""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes


def flush_dns_as_admin() -> str:
    if os.name != "nt":
        raise RuntimeError("Tự xóa DNS với quyền admin chỉ hỗ trợ Windows.")

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL

    directory = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(directory, len(directory))
    if not length or length >= len(directory):
        raise RuntimeError("Không xác định được thư mục hệ thống Windows.")
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000400  # NOCLOSEPROCESS | FLAG_NO_UI
    info.lpVerb = "runas"
    info.lpFile = os.path.join(directory.value, "ipconfig.exe")
    info.lpParameters = "/flushdns"
    info.lpDirectory = directory.value
    info.nShow = 0
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == 1223:
            raise RuntimeError("Đã hủy yêu cầu quyền admin (UAC); chưa xóa DNS.")
        raise RuntimeError(f"Không chạy được ipconfig /flushdns với quyền admin (Windows {code}).")
    if not info.hProcess:
        raise RuntimeError("Không nhận được tiến trình ipconfig để kiểm tra kết quả.")
    try:
        result = kernel.WaitForSingleObject(info.hProcess, 30000)
        if result == 258:
            raise RuntimeError("ipconfig /flushdns chưa kết thúc sau 30 giây; chưa xác nhận xóa DNS.")
        if result != 0:
            raise RuntimeError("Không chờ được kết quả ipconfig /flushdns.")
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise RuntimeError("Không đọc được mã kết quả ipconfig /flushdns.")
        if code.value != 0:
            raise RuntimeError(f"ipconfig /flushdns thất bại (mã {code.value}).")
    finally:
        kernel.CloseHandle(info.hProcess)
    return "Đã chạy ipconfig /flushdns với quyền admin thành công."


class DnsRecovery:
    """Share one repair result across workers for a 60-second cooldown."""

    def __init__(self):
        self.lock = threading.Lock()
        self.completed_at: float | None = None
        self.success = False
        self.message = ""

    def repair(self) -> tuple[bool, str]:
        with self.lock:
            if self.completed_at is not None and time.monotonic() - self.completed_at < 60:
                return self.success, "Dùng kết quả xóa DNS gần nhất (trong 60 giây): " + self.message
            try:
                self.message = flush_dns_as_admin()
                self.success = True
            except Exception as error:
                self.message = str(error)
                self.success = False
            self.completed_at = time.monotonic()
            return self.success, self.message
