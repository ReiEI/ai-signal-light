# -*- coding: utf-8 -*-
"""资源指标采集：CPU / 内存 / 网速 / 设备温度。

设计取舍：
- CPU 用 cpu_times 差分，不用 psutil.cpu_percent 的进程级全局计时器
  （多实例会互相干扰，且首次调用恒为 0）。
- 字段用 getattr 兜底：Windows 的 cpu_times 只有 user/system/idle/interrupt/dpc，
  没有 nice / iowait（Linux 才有），硬取会抛 AttributeError。
- 网速用真实流逝时间做分母，不用固定间隔，避免 UI 卡顿导致读数虚高。

温度单独说：
Windows 上 psutil 根本没有 sensors_temperatures（只有 Linux/macOS 有），
ACPI 的 MSAcpi_ThermalZoneTemperature 又要管理员权限、且在不少机器上直接报"不支持"。
所以这里按"代价从低到高"串了几条链路，谁读得到就用谁，读不到就老实显示"—"，
绝不编一个数字出来：

    cmd     自定义命令（settings.temp_cmd），最灵活，也是最后的兜底
    psutil  Linux/macOS 的 sensors_temperatures
    adl     AMD 显卡/APU 驱动接口（atiadlxx.dll，约 1ms，本机实测可用）
    nvidia  nvidia-smi 子进程（约 80ms）
    lhm     LibreHardwareMonitor 的 WMI 命名空间（约 300ms PowerShell，覆盖 CPU）

采样全在后台线程：PowerShell 那条链路要几百毫秒，放 UI 线程会卡住动画。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import shutil
import subprocess
import threading
import time

try:
    import psutil
except ImportError:
    psutil = None

from textio import decode as _decode  # noqa: E402  中文环境的编码坑统一在一处处理

_BUSY_FIELDS = ("user", "system", "nice", "interrupt", "dpc", "irq", "softirq", "steal")
_IDLE_FIELDS = ("idle", "iowait")

# 温度的合理区间：超出这个范围的读数一律视为驱动没就绪，宁可不显示也不误导
TEMP_MIN = 15.0
TEMP_MAX = 115.0

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _busy(t) -> float:
    return sum(getattr(t, f, 0.0) or 0.0 for f in _BUSY_FIELDS)


def _idle(t) -> float:
    return sum(getattr(t, f, 0.0) or 0.0 for f in _IDLE_FIELDS)


def fmt_rate(bps: float) -> str:
    """字节/秒 转人类可读。"""
    if bps is None or bps < 0:
        bps = 0.0
    for unit, scale in (("M", 1024 * 1024), ("K", 1024)):
        if bps >= scale:
            v = bps / scale
            return f"{v:.1f}{unit}" if v < 10 else f"{v:.0f}{unit}"
    return f"{bps:.0f}B"


def _plausible(v) -> bool:
    return isinstance(v, (int, float)) and TEMP_MIN <= float(v) <= TEMP_MAX


class _FmtCounterValue(ctypes.Structure):
    """PDH_FMT_COUNTERVALUE：DWORD Status + union{double}，按 8 字节对齐。"""
    _fields_ = [("status", ctypes.c_ulong), ("value", ctypes.c_double)]


class PerfCounters:
    """% Processor Performance / % Processor Utility（PDH 性能计数器）。

    为什么不用 psutil.cpu_freq()：Windows 上它走 CallNtPowerInformation，
    返回的是额定频率 —— 本机实测恒为 1800MHz，负载 100% 时也不变，拿不到实时值。
    而这个数字恰好是判断「电源策略到底有没有生效」的唯一客观依据：
    原 FanSpin.ps1 靠 `Get-Counter` 拉一个 PowerShell 子进程（300ms+）来读它，
    每读一次就要起一个进程。改用 PDH 后同一次采样约 1ms。

    用 PdhAddEnglishCounterW 而不是 PdhAddCounterW：后者要传本地化计数器名，
    中文系统上英文路径会失败。English 版本与界面语言无关。
    """

    PDH_FMT_DOUBLE = 0x00000200
    VALID = (0, 1)  # PDH_CSTATUS_VALID_DATA / PDH_CSTATUS_NEW_DATA

    def __init__(self) -> None:
        self.perf: float | None = None
        self.util: float | None = None
        self.error = ""
        self._pdh = None
        self._query = None
        self._handles: dict = {}
        self._init()

    def _init(self) -> None:
        if os.name != "nt":
            return
        try:
            pdh = ctypes.WinDLL("pdh")
        except OSError as e:
            self.error = repr(e)
            return

        pdh.PdhOpenQueryW.argtypes = [wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhOpenQueryW.restype = ctypes.c_long
        q = ctypes.c_void_p()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(q)) != 0:
            self.error = "打开查询失败"
            return

        add = getattr(pdh, "PdhAddEnglishCounterW", None)
        if add is None:  # Vista 之前没有，现代 Windows 不会走到这里
            self.error = "系统不支持英文计数器名"
            pdh.PdhCloseQuery(q)
            return
        add.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        add.restype = ctypes.c_long

        for key, path in (
            ("perf", r"\Processor Information(_Total)\% Processor Performance"),
            ("util", r"\Processor Information(_Total)\% Processor Utility"),
        ):
            h = ctypes.c_void_p()
            if add(q, path, 0, ctypes.byref(h)) == 0 and h:
                self._handles[key] = h

        if not self._handles:
            self.error = "计数器不可用"
            pdh.PdhCloseQuery(q)
            return

        pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        pdh.PdhCollectQueryData.restype = ctypes.c_long
        pdh.PdhGetFormattedCounterValue.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(_FmtCounterValue),
        ]
        pdh.PdhGetFormattedCounterValue.restype = ctypes.c_long
        pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        self._pdh, self._query = pdh, q
        # 速率型计数器要两个样本才出值，这里先取一次基线
        pdh.PdhCollectQueryData(q)

    def sample(self) -> None:
        if self._pdh is None:
            return
        if self._pdh.PdhCollectQueryData(self._query) != 0:
            self.error = "采样失败"
            return
        for key in ("perf", "util"):
            h = self._handles.get(key)
            if not h:
                continue
            ctype = ctypes.c_ulong(0)
            fmt = _FmtCounterValue()
            if self._pdh.PdhGetFormattedCounterValue(
                h, self.PDH_FMT_DOUBLE, ctypes.byref(ctype), ctypes.byref(fmt)
            ) != 0:
                continue
            if fmt.status in self.VALID and fmt.value == fmt.value:  # NaN 自检
                setattr(self, key, float(fmt.value))

    def close(self) -> None:
        if self._pdh is None:
            return
        try:
            self._pdh.PdhCloseQuery(self._query)
        except Exception:  # noqa: BLE001
            pass
        self._pdh = None
        self._query = None
        self._handles = {}


class Metrics:
    def __init__(self) -> None:
        self.available = psutil is not None
        self.cpu = 0.0
        self.mem = 0.0
        self.mem_used = 0
        self.mem_total = 0
        self.down = 0.0
        self.up = 0.0
        self.cores = 0
        self.error = ""
        self.perf = PerfCounters()
        self._t_cpu: float | None = None
        self._cpu_times = None
        self._t_net: float | None = None
        self._net = None
        if self.available:
            self._prime()

    def _prime(self) -> None:
        try:
            self._cpu_times = psutil.cpu_times()
            self._t_cpu = time.monotonic()
            self._net = psutil.net_io_counters()
            self._t_net = time.monotonic()
            vm = psutil.virtual_memory()
            self.mem = vm.percent
            self.mem_used, self.mem_total = vm.used, vm.total
            self.cores = psutil.cpu_count(logical=True) or 0
        except Exception as e:  # noqa: BLE001
            self.available = False
            self.error = f"prime: {e!r}"

    def sample(self) -> None:
        if not self.available:
            return
        now = time.monotonic()

        try:
            ct = psutil.cpu_times()
            if self._cpu_times is not None and self._t_cpu is not None:
                dt = now - self._t_cpu
                if dt > 0.05:
                    db = _busy(ct) - _busy(self._cpu_times)
                    di = _idle(ct) - _idle(self._cpu_times)
                    total = db + di
                    if total > 0:
                        self.cpu = max(0.0, min(100.0, db / total * 100.0))
            self._cpu_times, self._t_cpu = ct, now
        except Exception as e:  # noqa: BLE001
            self.error = f"cpu: {e!r}"

        try:
            vm = psutil.virtual_memory()
            self.mem = vm.percent
            self.mem_used, self.mem_total = vm.used, vm.total
        except Exception as e:  # noqa: BLE001
            self.error = f"mem: {e!r}"

        try:
            net = psutil.net_io_counters()
            if self._net is not None and self._t_net is not None:
                dt = now - self._t_net
                if dt > 0.05:
                    self.down = max(0.0, (net.bytes_recv - self._net.bytes_recv) / dt)
                    self.up = max(0.0, (net.bytes_sent - self._net.bytes_sent) / dt)
            self._net, self._t_net = net, now
        except Exception as e:  # noqa: BLE001
            self.error = f"net: {e!r}"

        self.perf.sample()

    @property
    def perf_pct(self) -> float | None:
        """性能比：100% = 基频，>100% 表示正在睿频。"""
        return self.perf.perf

    def close(self) -> None:
        self.perf.close()

    @property
    def mem_gb(self) -> str:
        if not self.mem_total:
            return ""
        return f"{self.mem_used / 1024**3:.1f}/{self.mem_total / 1024**3:.0f}G"


# ---------------- 温度 ----------------
# 每个 provider 只要读一个属性：read() -> 摄氏度 或 None；外加 name / label / interval。


class PsutilTemp:
    name = "psutil"
    label = "CPU"
    interval = 5.0

    def __init__(self) -> None:
        self._fn = getattr(psutil, "sensors_temperatures", None) if psutil else None

    def read(self):
        if self._fn is None:
            return None
        try:
            groups = self._fn() or {}
        except Exception:  # noqa: BLE001
            return None
        # 优先整包温度；没有就取所有读数的最大值（最热的那个核才是瓶颈）
        for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
            entries = groups.get(key) or []
            for e in entries:
                lab = (getattr(e, "label", "") or "").lower()
                if any(k in lab for k in ("package", "tctl", "tdie")):
                    cur = getattr(e, "current", None)
                    if _plausible(cur):
                        return float(cur)
            vals = [getattr(e, "current", None) for e in entries]
            vals = [float(v) for v in vals if _plausible(v)]
            if vals:
                return max(vals)
        vals = []
        for entries in groups.values():
            for e in entries or []:
                cur = getattr(e, "current", None)
                if _plausible(cur):
                    vals.append(float(cur))
        return max(vals) if vals else None


class _Arena:
    """ADL 只给了 malloc 回调，没给 free。

    用一个定长缓冲区做自增分配，用完一圈回头复用。ADL 的分配都是调用期内的
    短命对象，复用无副作用；这样既不会泄漏，也不用碰真正的堆。
    """

    _MALLOC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)

    def __init__(self, size: int = 8 << 20) -> None:
        self._buf = ctypes.create_string_buffer(size)
        self._size = size
        self._off = 0
        self.cb = self._MALLOC(self._alloc)

    def _alloc(self, n: int):
        n = int(n)
        if n <= 0:
            return None
        step = (n + 15) & ~15
        if self._off + step > self._size:
            self._off = 0
        ptr = ctypes.cast(ctypes.byref(self._buf, self._off), ctypes.c_void_p).value
        self._off += step
        return ptr


class AdlTemp:
    """AMD 显卡/APU 温度（atiadlxx.dll → ADL2_OverdriveN_Temperature_Get）。

    Overdrive5 那套接口在 APU 上返回 ADL_ERR_NOT_SUPPORTED(-100)，
    OverdriveN 才是能用的那个，实测返回千分之一摄氏度（50500 → 50.5℃）。
    """

    name = "adl"
    label = "GPU"
    interval = 4.0

    def __init__(self) -> None:
        self._dll = None
        self._ctx = ctypes.c_void_p()
        self._arena = None
        self._fn_temp = None
        self._count = 0
        self._idx = -1
        self._kind = 1
        self._init()

    def _init(self) -> None:
        if os.name != "nt":
            return
        try:
            dll = ctypes.WinDLL("atiadlxx.dll")
            create = dll.ADL2_Main_Control_Create
            num = dll.ADL2_Adapter_NumberOfAdapters_Get
            temp = dll.ADL2_OverdriveN_Temperature_Get
        except (OSError, AttributeError):
            return

        arena = _Arena()
        create.argtypes = [_Arena._MALLOC, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_int
        # 第二个参数 1 = 枚举已连接的适配器；不枚举则拿不到索引
        if create(arena.cb, 1, ctypes.byref(self._ctx)) != 0:
            return

        num.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        num.restype = ctypes.c_int
        n = ctypes.c_int(0)
        if num(self._ctx, ctypes.byref(n)) != 0 or n.value <= 0:
            return

        temp.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        temp.restype = ctypes.c_int

        self._dll = dll
        self._arena = arena
        self._fn_temp = temp
        self._count = n.value

    def read(self):
        if self._fn_temp is None:
            return None
        # 先试上次成功的那个（APU 上 6 个逻辑适配器读数相同，没必要每轮全扫）
        order = [self._idx] if self._idx >= 0 else []
        order += [i for i in range(self._count) if i != self._idx]
        kinds = [self._kind] + [k for k in (1, 2, 3) if k != self._kind]
        for i in order:
            for kind in kinds:
                v = ctypes.c_int(0)
                if self._fn_temp(self._ctx, i, kind, ctypes.byref(v)) == 0:
                    t = v.value / 1000.0
                    if _plausible(t):
                        self._idx, self._kind = i, kind
                        return t
        return None

    def close(self) -> None:
        if self._dll is None or not self._ctx:
            return
        try:
            fn = self._dll.ADL2_Main_Control_Destroy
            fn.argtypes = [ctypes.c_void_p]
            fn(self._ctx)
        except (AttributeError, OSError):
            pass
        self._dll = None
        self._ctx = ctypes.c_void_p()


class NvidiaTemp:
    name = "nvidia"
    label = "GPU"
    interval = 8.0

    def __init__(self) -> None:
        self._exe = shutil.which("nvidia-smi")

    def read(self):
        if not self._exe:
            return None
        try:
            r = subprocess.run(
                [self._exe, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, timeout=3, creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        # 多卡时一行一个，取最热的
        vals = []
        for line in _decode(r.stdout).splitlines():
            try:
                v = float(line.strip())
            except ValueError:
                continue
            if _plausible(v):
                vals.append(v)
        return max(vals) if vals else None


class LhmTemp:
    """LibreHardwareMonitor（或 OpenHardwareMonitor）跑起来后暴露的 WMI 命名空间。

    这是 Windows 上拿到 CPU 封装温度最省事的办法，代价是要拉一个 PowerShell
    子进程（几百毫秒），所以只在前面几条链路都失败时才轮到它，间隔也放宽到 20s。
    """

    name = "lhm"
    label = "CPU"
    interval = 20.0

    _PS = (
        "$ErrorActionPreference='SilentlyContinue';"
        "foreach ($ns in @('root/LibreHardwareMonitor','root/OpenHardwareMonitor')) {"
        " Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction SilentlyContinue"
        " | Where-Object { $_.SensorType -eq 'Temperature' }"
        " | ForEach-Object { Write-Output ('{0}|{1}' -f $_.Name, $_.Value) } }"
    )

    @classmethod
    def _query(cls) -> list[tuple[str, float]]:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cls._PS],
                capture_output=True, timeout=15, creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        out = []
        for line in _decode(r.stdout).splitlines():
            name, _, val = line.partition("|")
            try:
                v = float(val.strip().replace(",", "."))
            except ValueError:
                continue
            if _plausible(v):
                out.append((name.strip(), v))
        return out

    def read(self):
        rows = self._query()
        if not rows:
            return None
        for key in ("package", "tctl", "tdie", "core max"):
            for name, v in rows:
                if key in name.lower():
                    return v
        return max(v for _n, v in rows)


class CmdTemp:
    """自定义命令：stdout 里第一个 15~115 的数字就是温度。

    给用户留的插口——装了什么传感器工具都能接进来，不必改代码。
    """

    name = "cmd"
    label = "设备"
    interval = 15.0
    _NUM = re.compile(r"-?\d+(?:\.\d+)?")

    def __init__(self, cmd: str) -> None:
        self._cmd = cmd

    def read(self):
        if not self._cmd:
            return None
        try:
            r = subprocess.run(
                self._cmd, shell=True, capture_output=True,
                timeout=8, creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        for m in self._NUM.finditer(_decode(r.stdout)):
            try:
                v = float(m.group())
            except ValueError:
                continue
            if _plausible(v):
                return v
        return None


def default_providers(cmd: str = "") -> list:
    out: list = []
    if cmd:
        out.append(CmdTemp(cmd))
    out.append(PsutilTemp())
    if os.name == "nt":
        out.append(AdlTemp())
        out.append(NvidiaTemp())
        out.append(LhmTemp())
    return out


class Thermal:
    """温度读数持有者：后台线程按 provider 自己的节奏采样，UI 只读属性。

    temp/label/source 都是单次赋值，CPython 下 float/str 赋值是原子的，
    UI 线程不加锁直接读也不会读到半截数据。
    """

    def __init__(self, cmd: str = "", autostart: bool = True, providers=None) -> None:
        self.temp: float | None = None
        self.label = ""
        self.source = ""
        self.error = ""
        self.probed = False       # 首轮探测是否已结束：区分"还没测"和"测了没有"
        self.retry_at = 30.0     # 一个源都找不到时的重探间隔
        self._cmd = cmd or ""
        self._providers = list(providers) if providers is not None else None
        self._factory = (lambda: default_providers(self._cmd)) if providers is None else None
        self._active = None
        self._miss = 0
        self._force = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if autostart:
            self.start()

    # ---- 生命周期 ----
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="thermal", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None

    def reprobe(self) -> None:
        self._force = True

    @property
    def available(self) -> bool:
        return self.temp is not None

    # ---- 采样 ----
    def _ensure(self) -> list:
        if self._providers is None:
            self._providers = self._factory() if self._factory else []
        return self._providers

    def probe(self):
        """按顺序试每条链路，第一条读到值的就是当前源。"""
        for p in self._ensure():
            try:
                v = p.read()
            except Exception as e:  # noqa: BLE001
                self.error = f"{getattr(p, 'name', '?')}: {e!r}"
                v = None
            # provider 自己已经过滤过，这里再兜一层：任何离谱读数都不许画到界面上
            if v is not None and _plausible(v):
                self._active = p
                self.temp = float(v)
                self.label = getattr(p, "label", "")
                self.source = getattr(p, "name", "")
                self._miss = 0
                self.probed = True
                return p
        self._active = None
        self.temp = None
        self.label = ""
        self.source = ""
        self.probed = True
        return None

    def sample_once(self):
        if self._active is None or self._force:
            self._force = False
            return self.probe()
        try:
            v = self._active.read()
        except Exception as e:  # noqa: BLE001
            self.error = f"{getattr(self._active, 'name', '?')}: {e!r}"
            v = None
        if v is None:
            # 连续三次读不到才判定该源失效：显卡休眠/驱动抖动一下不该让数字消失
            self._miss += 1
            if self._miss >= 3:
                self._active = None
                self.temp = None
                self.label = ""
                self.source = ""
        elif _plausible(v):
            self._miss = 0
            self.temp = float(v)
        return self.temp

    def _loop(self) -> None:
        try:
            self.probe()
        except Exception as e:  # noqa: BLE001
            self.error = repr(e)
        while not self._stop.is_set():
            interval = getattr(self._active, "interval", self.retry_at) if self._active else self.retry_at
            if self._stop.wait(interval):
                break
            try:
                self.sample_once()
            except Exception as e:  # noqa: BLE001
                self.error = repr(e)
        for p in self._providers or []:
            close = getattr(p, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
