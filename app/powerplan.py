# -*- coding: utf-8 -*-
"""Windows 处理器电源策略读写（powercfg 封装）。

为什么要有这一层：powercfg 是唯一稳定的官方接口，但它的输出是**本地化文本**，
而且被 OEM 隐藏的设置项会「返回码 0 但内容为空」。所以这里只做两件事：
  1. 用「块内最后两个 0x 值 = 当前 AC/DC」这条与语言无关的规则解析；
  2. 把「读不到」和「读到 0」严格区分开 —— 拿不到就返回 None。

为什么能改电源策略就等于能「让风扇转」：
    最小处理器状态 = 100% 时，CPU 空闲也不降频，持续发热，EC 必然维持风扇转速。
    这是「用发热换风量」，不是降温。它比 EC 直控差得多，但不需要任何私有接口，
    所以在没有 EnergyDrv 的机器上是唯一的通用兜底。

安全约定（这条比功能本身重要）：
    改动前必须落一份**带截止时间**的备份到磁盘。进程崩了、被杀了、机器重启了，
    下一次启动读到过期的备份就能把机器还原回去。只在内存里记原始值是不够的。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from pathlib import Path

from textio import decode

SUB_PROC = "54533251-82be-4824-96c1-47b60b740d00"
SET_MIN = "893DEE8E-2BEF-41E0-89C6-B55D0929964C"   # PROCTHROTTLEMIN 最小处理器状态
SET_MAX = "BC5038F7-23E0-4960-96DA-33ABAF5935EC"   # PROCTHROTTLEMAX 最大处理器状态
SET_COOL = "94D3A615-A899-4AC5-AE2B-E4D8F634367F"  # SYSCOOLPOL 系统散热方式

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class PowerError(Exception):
    pass


def run(args: list[str], timeout: float = 8.0) -> tuple[int, str]:
    """执行 powercfg 并返回 (退出码, 输出文本)。失败不抛，由调用方判断。"""
    try:
        p = subprocess.run(
            ["powercfg", *args], capture_output=True, timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return -1, repr(e)
    return p.returncode, decode(p.stdout) + decode(p.stderr)


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def query(setting: str) -> dict | None:
    """读一个设置的当前 AC/DC 值。

    解析规则：powercfg 一个设置块内的十六进制数依次是
    「最小可能 / 最大可能 / 增量 / 当前交流 / 当前直流」，
    取最后两个即可，与界面语言无关。英文/中文/其它语种都不用改代码。
    """
    _rc, out = run(["/query", "SCHEME_CURRENT", SUB_PROC, setting])
    if not out:
        return None
    hexes = []
    for token in out.replace("0X", "0x").split():
        token = token.strip(",.;:，。（）()[]")
        if token.startswith("0x"):
            try:
                hexes.append(int(token, 16))
            except ValueError:
                continue
    if len(hexes) < 2:
        return None
    return {"ac": hexes[-2], "dc": hexes[-1]}


def scheme() -> str:
    """当前电源方案名。取括号里的中文名，取不到就返回整行。"""
    rc, out = run(["/getactivescheme"])
    if rc != 0 or not out.strip():
        return ""
    line = out.strip().splitlines()[0]
    if "(" in line and ")" in line:
        return line[line.rfind("(") + 1 : line.rfind(")")]
    return line


def read_state() -> dict:
    """一次性读出面板要展示的全部电源信息。读不到的项一律 None。"""
    return {
        "scheme": scheme(),
        "min": query(SET_MIN),
        "max": query(SET_MAX),
        "cool": query(SET_COOL),
    }


def set_value(setting: str, ac: int, dc: int) -> None:
    rc, out = run(["/setacvalueindex", "SCHEME_CURRENT", SUB_PROC, setting, str(int(ac))])
    if rc != 0:
        raise PowerError(f"写入交流值失败（{out.strip() or rc}）")
    rc, out = run(["/setdcvalueindex", "SCHEME_CURRENT", SUB_PROC, setting, str(int(dc))])
    if rc != 0:
        raise PowerError(f"写入直流值失败（{out.strip() or rc}）")


def activate() -> None:
    rc, out = run(["/setactive", "SCHEME_CURRENT"])
    if rc != 0:
        raise PowerError(f"激活电源方案失败（{out.strip() or rc}）")


# ---------------- 备份 / 还原 ----------------

def make_backup(duration_s: float) -> dict:
    """改动前落盘。deadline 是「超过这个时刻还看到备份，就说明上次没收尾」。"""
    now = time.time()
    return {
        "created": now,
        "deadline": now + max(60.0, float(duration_s)),
        "scheme": scheme(),
        "min": query(SET_MIN),
        "max": query(SET_MAX),
    }


def save_backup(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def load_backup(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    return d


def clear_backup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def restore(path: Path) -> tuple[bool, str]:
    """把备份里的最小/最大处理器状态写回去。成功即删除备份。

    没有备份时返回 (True, "无需还原")：这不是错误，说明本来就没改过。
    """
    if not path.exists():
        return True, "无需还原"
    data = load_backup(path)
    if data is None:
        # 备份坏了但文件在：删掉，否则每次启动都会重复报警
        clear_backup(path)
        return False, "备份文件损坏，已清除"

    done = 0
    try:
        for key, setting in (("min", SET_MIN), ("max", SET_MAX)):
            v = data.get(key)
            if isinstance(v, dict) and "ac" in v and "dc" in v:
                set_value(setting, int(v["ac"]), int(v["dc"]))
                done += 1
        activate()
    except (PowerError, TypeError, ValueError) as e:
        return False, str(e)

    clear_backup(path)
    return True, f"已还原 {done} 项处理器电源设置"


def apply_min_state(value: int) -> None:
    """只改最小处理器状态（风扇持续转模式）。最大状态不动 —— 动了会限制睿频。"""
    set_value(SET_MIN, value, value)
    activate()
