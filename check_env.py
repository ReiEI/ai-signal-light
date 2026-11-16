# -*- coding: utf-8 -*-
"""环境预检：把「装不上」的原因说清楚。

为什么要单独一个脚本：
  安装.cmd 直接 `python -m venv .venv`，失败时只知道「失败了」，
  但实际有好几种完全不同的原因，且各自的解法不一样：

  1) 机器上根本没装 Python
  2) 装了，但没勾「Add python.exe to PATH」
  3) 只装了 Microsoft Store 版 —— 那个 python.exe 是占位符，
     跑任何命令都静默退出（退出码 49），不提示任何东西
  4) 装了，但版本低于依赖要求（3.10）
  5) 装的是 32 位版，PySide6 没有对应的 wheel
  6) 装好了，但 venv 目录是坏的（换机/换路径后失效）

这个脚本把上面 6 种情况逐一测出来，给出对应的可执行动作。

用法：
  python check_env.py                 # 体检并打印报告
  python check_env.py --pick          # 顺便打印一条可直接用的建 venv 命令
  python check_env.py --create-venv   # 体检 + 用最佳解释器建好 .venv
退出码：0 = 可用；1 = 不可用（报告里写了原因和修法）
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / "Scripts" / "python.exe"

# 依赖实际要求：PySide6 6.12.0 -> >=3.10,<3.16
MIN_PY = (3, 10)
MAX_PY = (3, 16)


def _bits() -> int:
    return struct.calcsize("P") * 8


def _run(cmd: list[str], timeout: int = 25) -> tuple[int, str]:
    """跑一条命令，返回 (退出码, 输出)。不抛异常。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "(超时)"
    except OSError as e:
        return -1, f"(无法执行: {e})"
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out.strip()


def _use_gbk_stdout() -> None:
    """让 print 出的中文在 cmd 控制台（GBK 代码页）里正常显示。

    委托给 app/textio.py —— 编码逻辑只有一份，避免两处实现漂移。
    """
    try:
        sys.path.insert(0, str(ROOT / "app"))
        import textio
        textio.bind_console_encoding()
    except Exception:
        pass


def is_store_stub(exe: str) -> bool:
    """Microsoft Store 的 python.exe 是 0 字节占位符 / App Execution Alias。"""
    if not exe:
        return False
    low = exe.lower().replace("/", "\\")
    if "\\windowsapps\\" in low:
        return True
    try:
        p = Path(exe)
        if p.exists() and p.stat().st_size < 2048:
            # 占位符：文件极小，或目标是 reparse point
            if p.is_symlink() or p.stat().st_file_attributes & 0x400:
                return True
    except OSError:
        pass
    return False


def probe_interpreter(exe: str) -> dict:
    """测一个解释器能不能真的跑起来。"""
    info = {"exe": exe, "ok": False, "version": None, "bits": None,
            "error": "", "store_stub": is_store_stub(exe)}
    if not exe or not Path(exe).exists():
        info["error"] = "文件不存在"
        return info

    rc, out = _run([exe, "-c",
                    "import sys,struct;print('%d.%d.%d|%d' % (sys.version_info[0],"
                    "sys.version_info[1],sys.version_info[2],struct.calcsize('P')*8))"])
    if rc != 0:
        info["error"] = (f"退出码 {rc}" + (f"，输出：{out[:120]}" if out else "，且无任何输出"))
        if info["store_stub"]:
            info["error"] += "（Microsoft Store 占位符，不会真正执行 Python）"
        return info

    try:
        vs, bits = out.split("|")
        info["version"] = tuple(int(x) for x in vs.split("."))
        info["bits"] = int(bits)
        info["ok"] = True
    except ValueError:
        info["error"] = f"输出无法解析: {out[:120]}"
    return info


def find_candidates() -> list[str]:
    """按优先级列出候选解释器。"""
    cands: list[str] = []

    # 1) 项目自带的 venv（已装好的机器）
    if VENV_PY.exists():
        cands.append(str(VENV_PY))

    # 2) 当前正在运行的解释器。
    #    这一条很关键：用户「没把 Python 加进 PATH、但用完整路径调用」时，
    #    跑的就是这个解释器，它当然可用。之前漏掉它会误报「没装 Python」。
    running = sys.executable
    if running:
        cands.append(running)

    # 3) PATH 上的 python / python3
    which = shutil.which("python")
    if which:
        cands.append(which)
    which3 = shutil.which("python3")
    if which3 and which3.lower() != (which or "").lower():
        cands.append(which3)

    # 4) py launcher。py -3 会挑它认为"最新"的，但可能指向 3.9 这种
    #    装了就等于没装的版本，所以每个候选都要实测，而不是无条件采信。
    pyl = shutil.which("py")
    if pyl:
        for args in (["-3.13"], ["-3.12"], ["-3.11"], ["-3.10"], ["-3"]):
            cands.append(pyl + " " + args[0])

    # 5) 常见安装位置（含用户级与全机器级）
    for base in [Path.home() / "AppData/Local/Programs/Python",
                 Path("C:/Python313"), Path("C:/Python312"), Path("C:/Python311")]:
        if base.is_dir():
            for sub in sorted(base.glob("python3*")):
                exe = sub / "python.exe"
                if exe.exists():
                    cands.append(str(exe))

    # 去重（按解析后的绝对路径，大小写不敏感）
    seen, out = set(), []
    for c in cands:
        key = c.lower().rstrip()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def probe_candidate(cand: str) -> dict:
    if cand.endswith(" -3"):
        exe, extra = cand[:-3], ["-3"]
    else:
        exe, extra = cand, []
    info = probe_interpreter(exe)
    info["raw"] = cand
    info["extra"] = extra
    return info


def check_venv() -> dict:
    """检查已有 venv 是否可用。"""
    res = {"exists": VENV.exists(), "usable": False, "reason": ""}
    if not res["exists"]:
        res["reason"] = "尚未创建"
        return res
    if not VENV_PY.exists():
        res["reason"] = "venv 目录存在但没有 Scripts/python.exe（创建中断过？）"
        return res

    cfg = VENV / "pyvenv.cfg"
    home = ""
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().lower().startswith("home"):
                home = line.split("=", 1)[1].strip()
                break

    rc, out = _run([str(VENV_PY), "-c", "import sys;print(sys.version_info[0])"])
    if rc != 0:
        res["reason"] = f"venv 里的 python 跑不起来（退出码 {rc}）"
        if home and not Path(home).exists():
            res["reason"] += f"；它绑定的解释器 {home} 已不存在（换机/升级导致）"
        return res

    res["usable"] = True
    res["home"] = home
    res["base_alive"] = bool(home) and Path(home).exists()
    if home and not res["base_alive"]:
        res["usable"] = False
        res["reason"] = f"venv 绑定的解释器 {home} 已不存在（换机/换 Python 版本导致）"
    return res


def create_venv(best: dict) -> bool:
    """用最佳解释器重建 .venv。

    已有 venv 但不可用时先删掉再重建 —— venv 内部记着创建时的解释器路径，
    换机、换 Python 版本、目录搬家之后都会指向不存在的解释器。
    """
    v = check_venv()
    if v["exists"]:
        if v["usable"]:
            print(f"    .venv 已存在且可用（绑定 {v.get('home', '?')}），跳过重建。")
            return True
        print(f"    .venv 不可用：{v['reason']}")
        print("    正在删除并重建 ...")
        shutil.rmtree(VENV, ignore_errors=True)
        if VENV.exists():
            print("    [FAIL] 无法删除 .venv（可能有文件被占用，请手动删除后重试）")
            return False

    exe = best["exe"]
    args = best.get("extra") or []
    cmd = [exe] + args + ["-m", "venv", str(VENV)]
    print("    执行：" + " ".join(cmd))
    rc, out = _run(cmd, timeout=180)
    if rc != 0:
        print(f"    [FAIL] 建 venv 失败（退出码 {rc}）")
        if out:
            print("    " + out[:400])
        return False

    if not VENV_PY.exists():
        print(f"    [FAIL] venv 目录建出来了，但没有 {VENV_PY.name} —— 创建可能不完整")
        return False
    rc, out = _run([str(VENV_PY), "-c", "import sys;print(sys.version)"])
    if rc != 0:
        print(f"    [FAIL] 新建的 venv 跑不起来（退出码 {rc}）")
        return False
    print(f"    [OK]   .venv 就绪（{out.split()[0]}）")
    return True


def main() -> int:
    _use_gbk_stdout()
    ap = argparse.ArgumentParser(description="AI 工作信号灯 · 安装环境预检")
    ap.add_argument("--pick", action="store_true", help="打印一条建 venv 的可用命令")
    ap.add_argument("--create-venv", action="store_true", help="体检后用最佳解释器建好 .venv")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = ap.parse_args()

    report = {
        "python": sys.executable,
        "version": platform.python_version(),
        "bits": _bits(),
        "min_required": "%d.%d" % MIN_PY,
        "candidates": [],
        "venv": check_venv(),
    }

    print("=" * 64)
    print("AI 工作信号灯 · 安装环境预检")
    print("=" *  64)
    print(f"当前解释器 : {report['python']}")
    print(f"当前版本   : {report['version']}  ({report['bits']} 位)")
    print(f"依赖要求   : Python {'.'.join(map(str, MIN_PY))} - {'.'.join(map(str, MAX_PY))}，64 位")
    print()

    print("[1] 寻找可用的 Python 解释器")
    good = []
    for cand in find_candidates():
        info = probe_candidate(cand)
        report["candidates"].append(info)
        if info["ok"]:
            vs = info["version"]
            label = "%d.%d.%d (%d位)" % (vs[0], vs[1], vs[2], info["bits"])
            ok_ver = MIN_PY <= vs[:2] < MAX_PY
            if ok_ver and info["bits"] == 64:
                good.append(info)
                print(f"    [OK]   {cand}")
                print(f"           {label}")
            else:
                why = []
                if not ok_ver:
                    why.append(f"版本需在 {MIN_PY[0]}.{MIN_PY[1]}+ 且 < {MAX_PY[0]}.{MAX_PY[1]}")
                if info["bits"] != 64:
                    why.append(f"是 {info['bits']} 位，PySide6 只提供 64 位 wheel")
                print(f"    [WARN] {cand}")
                print(f"           {label} —— {'；'.join(why)}")
        else:
            tag = "Store 占位符" if info["store_stub"] else "不可用"
            print(f"    [FAIL] {cand}")
            print(f"           {tag}：{info['error']}")
    print()

    print("[2] 检查已存在的虚拟环境 (.venv)")
    v = report["venv"]
    if not v["exists"]:
        print("    尚未创建 —— 正常，会新建")
    elif v["usable"]:
        print("    [OK]   可用")
        if v.get("home"):
            print(f"           绑定解释器：{v['home']}")
    else:
        print(f"    [FAIL] {v['reason']}")
        print("           修法：删掉 .venv 目录重跑 安装.cmd")
    print()

    # 结论
    if v["usable"]:
        print("结论：环境就绪，无需重装。")
        code = 0
    elif good:
        print(f"结论：找到 {len(good)} 个可用解释器，可以安装。")
        code = 0
    else:
        print("结论：没有可用的 Python 解释器。")
        print()
        print("  修法（任选其一）：")
        print("  A) 装官方 Python 3.13 64 位：")
        print("     https://www.python.org/downloads/windows/")
        print("     安装时务必勾上「Add python.exe to PATH」")
        print("  B) 如果已经装了，只是没进 PATH，用完整路径调用：")
        print('     "%USERPROFILE%\\AppData\\Local\\Programs\\Python\\Python313\\python.exe" -m venv .venv')
        print("  C) Microsoft Store 版不可用 —— 它只是占位符，不含真正的解释器")
        code = 1
        print("=" * 64)
        if args.json:
            print()
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return code

    # 需要建 venv
    if args.create_venv and not v["usable"]:
        print()
        print("[3] 创建虚拟环境")
        best = good[0]
        if not create_venv(best):
            code = 1
            print("=" * 64)
            return code

    print("=" * 64)

    if args.pick and good:
        info = good[0]
        cmd = f'"{info["exe"]}" -m venv .venv'
        if info["extra"]:
            cmd = f'"{info["exe"]}" {" ".join(info["extra"])} -m venv .venv'
        print()
        print("建 venv 命令：")
        print("  " + cmd)

    if args.json:
        print()
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    return code


if __name__ == "__main__":
    sys.exit(main())
