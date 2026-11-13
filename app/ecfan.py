# -*- coding: utf-8 -*-
"""联想 EC 风扇接口（Lenovo ACPI Virtual Power Controller）。

原 FanSpin.ps1 走的是「PowerShell + Add-Type 运行时编译 C#」，
这条路在企业安全策略下会被直接拦掉（本机实测：Add-Type 被禁止），
而且每次编译要几百毫秒。既然挂件本身就是 Python，直接用 ctypes 调
DeviceIoControl 更短、更快、不需要编译。

设备与协议（逆向自联想电脑管家的除尘功能）：
    设备   \\\\.\\EnergyDrv
    写入   IOCTL 0x831020C0，输入 12 字节 = {6, 1, mode}
    读取   IOCTL 0x831020C4，输入 4 字节  = {14}，输出 uint32 状态
    mode   0 = 正常（交回 EC 自动调速）  1 = 全速（除尘）

EC 的除尘周期约 9 秒，到点会自动停 2 秒。所以「持续全速」不是发一次命令，
而是要每 8.9 秒重置一次计时器（先回 normal 再切 fast）。这是本模块唯一
有状态的逻辑，也是原脚本存在的真正原因。

访问权限：设备是否可写取决于机型/驱动版本，可能是
    可用（当前用户即可） / 需要管理员（ERROR_ACCESS_DENIED=5） / 设备不存在（=2）
所以对外只暴露 probe()，由上层根据探测结果决定要不要提权，绝不假设。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

DEVICE = r"\\.\EnergyDrv"
IOCTL_SET = 0x831020C0
IOCTL_GET = 0x831020C4
MODE_NORMAL = 0
MODE_FAST = 1

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value

# probe() 的四种结论。上层只认这四个值，不再解析错误码。
OK = "ok"
MISSING = "missing"        # 设备不存在：本机没有这个接口
DENIED = "denied"          # 存在但当前权限不够：需要提权
UNSUPPORTED = "unsupported"  # 能打开但 IOCTL 不被接受：驱动版本不匹配
ERROR = "error"

STATUS_LABELS = {
    OK: "可用",
    MISSING: "本机无此接口",
    DENIED: "需要管理员权限",
    UNSUPPORTED: "驱动不支持",
    ERROR: "接口异常",
}


def _err_text(code: int) -> str:
    table = {
        2: "设备不存在",
        5: "拒绝访问",
        32: "设备被独占",
        87: "参数错误",
        1: "功能不支持",
    }
    return table.get(code, f"错误码 {code}")


class EcFan:
    """无状态封装：每次调用开句柄、发 IOCTL、关句柄。

    常驻句柄没必要，反而会和联想电脑管家抢独占权；开句柄是微秒级操作。
    """

    def __init__(self, device: str = DEVICE) -> None:
        self.device = device
        self._k32 = None
        self.last_error = ""
        self._init()

    def _init(self) -> None:
        if os.name != "nt":
            return
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except OSError:
            return
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CreateFileW.argtypes = [
            wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
            wt.DWORD, wt.DWORD, ctypes.c_void_p,
        ]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = wt.BOOL
        k32.DeviceIoControl.argtypes = [
            ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD,
            ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
        ]
        k32.DeviceIoControl.restype = wt.BOOL
        k32.QueryDosDeviceW.restype = wt.DWORD
        k32.QueryDosDeviceW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD]
        self._k32 = k32

    # ---------- 底层 ----------

    def _registered(self) -> bool:
        """这个 DOS 设备名到底注册了没有。

        为什么要单独问一次：本机实测对不存在的 \\\\.\\XXX 调 CreateFileW
        返回的是 ERROR_ACCESS_DENIED(5) 而不是 FILE_NOT_FOUND(2)。
        只看错误码会把"没有这个接口"误判成"权限不够"，于是白弹一次 UAC，
        提权完还是失败。QueryDosDevice 是唯一能区分两者的调用。
        """
        if self._k32 is None:
            return False
        name = self.device[4:] if self.device.startswith("\\\\.\\") else self.device
        buf = ctypes.create_unicode_buffer(512)
        return bool(self._k32.QueryDosDeviceW(name, buf, 512))

    def _open(self, access: int):
        if self._k32 is None:
            return None, -1
        ctypes.set_last_error(0)
        h = self._k32.CreateFileW(
            self.device, access, 0, None, OPEN_EXISTING, 0, None
        )
        if not h or h == INVALID_HANDLE:
            return None, ctypes.get_last_error()
        return h, 0

    def _close(self, h) -> None:
        try:
            self._k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            pass

    def _ioctl(self, h, code: int, in_bytes: bytes, out_len: int):
        """返回 (成功?, 输出字节, 错误码)。"""
        inbuf = ctypes.create_string_buffer(in_bytes, len(in_bytes))
        outbuf = ctypes.create_string_buffer(max(1, out_len))
        ret = wt.DWORD(0)
        ctypes.set_last_error(0)
        ok = self._k32.DeviceIoControl(
            h, code, inbuf, len(in_bytes),
            outbuf, max(1, out_len), ctypes.byref(ret), None,
        )
        if not ok:
            return False, b"", ctypes.get_last_error()
        return True, outbuf.raw[:out_len], 0

    # ---------- 对外 ----------

    def set_mode(self, mode: int) -> tuple[bool, str]:
        """切换风扇模式。返回 (成功, 说明)。"""
        if self._k32 is None:
            return False, "仅支持 Windows"
        h, err = self._open(GENERIC_READ | GENERIC_WRITE)
        if h is None:
            self.last_error = _err_text(err)
            return False, self.last_error
        try:
            payload = (6).to_bytes(4, "little") + (1).to_bytes(4, "little") + int(mode).to_bytes(4, "little")
            ok, _out, code = self._ioctl(h, IOCTL_SET, payload, 0)
            self.last_error = "" if ok else _err_text(code)
            return ok, self.last_error
        finally:
            self._close(h)

    def get_mode(self) -> int | None:
        """回读 EC 当前模式。拿不到就返回 None —— 不猜。"""
        if self._k32 is None:
            return None
        h, _err = self._open(GENERIC_READ)
        if h is None:
            return None
        try:
            ok, out, _code = self._ioctl(h, IOCTL_GET, (14).to_bytes(4, "little"), 4)
            if not ok or len(out) < 4:
                return None
            return int.from_bytes(out[:4], "little")
        finally:
            self._close(h)

    def probe(self) -> tuple[str, str]:
        """探测可用性。返回 (状态, 人话说明)。

        顺序：能不能开句柄 → 能不能发 IOCTL。只看句柄会把
        「驱动接受打开但拒绝控制」的机型误判成可用。
        """
        if self._k32 is None:
            return ERROR, "仅支持 Windows"
        h, err = self._open(GENERIC_READ | GENERIC_WRITE)
        if h is None:
            if err == 2 or (err == 5 and not self._registered()):
                return MISSING, "本机没有该风扇接口"
            if err == 5:
                return DENIED, "设备存在，需要管理员权限"
            if err == 32:
                return DENIED, "设备被其它程序独占"
            return ERROR, _err_text(err)
        try:
            ok, _out, code = self._ioctl(h, IOCTL_GET, (14).to_bytes(4, "little"), 4)
            if ok:
                return OK, "EC 接口可读写"
            if code in (1, 87, 50):
                return UNSUPPORTED, f"驱动不接受该控制码（{_err_text(code)}）"
            if code == 5:
                return DENIED, "设备存在，需要管理员权限"
            return ERROR, _err_text(code)
        finally:
            self._close(h)
