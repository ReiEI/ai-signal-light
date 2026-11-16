# -*- coding: utf-8 -*-
"""打包产物自检：确认 dist\\*.exe 里没有被打包坏的东西。

为什么需要这道关卡
------------------
v1.0.8 的 fan_agent.exe 里，PyInstaller 收进去的 signal 模块是**仓库根
目录那份 signal.py**，不是标准库。仓库那份没有 SIGTERM/SIGINT/SIGBREAK
常量，于是：

    getattr(signal, "SIGTERM", None)  ->  None

信号处理器一个都没装上，Ctrl+C 无法优雅退出，散热状态来不及还原。

这个缺陷在源码层面完全看不出来 —— 单元测试全绿、运行不报错、静默失效。
只有把 exe 拆开、读它内嵌的 PYZ 目录，才能确认打进去的是哪个模块。
所以把它做成打包流程的最后一关：坏包不许出。

用法：
  python verify_dist.py            # 校验 dist\\ 下所有 exe
  python verify_dist.py --quiet    # 只在失败时输出
退出码：0 = 全部通过；1 = 有问题
"""

from __future__ import annotations

import argparse
import io
import marshal
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

# 标准库 signal.py 的独有特征。
# CPython 3.13 的 Lib/signal.py 里定义了 Handlers / Signals / Sigmasks 三个
# IntEnum，"valid_signals" 与 "pthread_sigmask" 也是它的专有标识。
# 注意不能用 "only works in main thread" —— 那句在 signal.signal() 函数体里，
# 而 PyInstaller 打包后模块顶层常量与函数体常量不一定都能被扫到。
STD_SIGNAL_MARKS = ("valid_signals", "pthread_sigmask", "Handlers", "Sigmasks")
# 仓库那份 signal_cli.py 的独有特征（argparse 的子命令名）
REPO_SIGNAL_MARK = "设置状态"


def _pyz_modules(exe: Path) -> dict:
    """读出 exe 内嵌 PYZ 里的所有模块名 -> 源码常量表。"""
    try:
        sys.path.insert(0, str(ROOT / ".venv" / "Lib" / "site-packages"))
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as e:
        raise RuntimeError(f"读不了 PyInstaller 的归档模块: {e}") from e

    ar = CArchiveReader(str(exe))
    pyz = ar.extract("PYZ.pyz")
    toc_offset = struct.unpack("!i", pyz[8:12])[0]
    raw_toc = marshal.loads(io.BytesIO(pyz[toc_offset:]).read())
    toc = dict(raw_toc) if isinstance(raw_toc, list) else raw_toc

    modules = {}
    for name, entry in toc.items():
        typecode, pos, length = entry
        try:
            code = marshal.loads(zlib.decompress(pyz[pos:pos + length]))
        except Exception:
            continue
        strs = []
        stack = [code]
        while stack:
            c = stack.pop()
            for k in getattr(c, "co_consts", ()):
                if isinstance(k, str):
                    strs.append(k)
                elif hasattr(k, "co_consts"):
                    stack.append(k)
        modules[name] = strs
    return modules


def verify(exe: Path, quiet: bool = False) -> list[str]:
    """返回问题列表，空列表表示通过。"""
    problems: list[str] = []
    name = exe.name

    def say(msg: str) -> None:
        if not quiet:
            print(f"    {msg}")

    try:
        modules = _pyz_modules(exe)
    except Exception as e:
        problems.append(f"{name}: 读不出内嵌模块 ({e})")
        return problems

    say(f"内嵌模块 {len(modules)} 个")

    # --- 关键检查 1：signal 模块必须是标准库那份 -------------------------
    sig = [n for n in modules if n.lower().split(".")[0] == "signal"]
    if not sig:
        say("未内嵌 signal 模块（纯 stdlib 程序可以不依赖它）")
    else:
        for n in sig:
            strs = modules[n]
            is_std = any(mark in strs for mark in STD_SIGNAL_MARKS)
            is_repo = any(REPO_SIGNAL_MARK in s for s in strs)
            if is_repo:
                problems.append(
                    f"{name}: 内嵌的 signal 模块是**仓库那份 signal_cli.py**，"
                    f"会遮蔽标准库 -> 信号处理器装不上，Ctrl+C 无法优雅退出")
            elif not is_std:
                problems.append(
                    f"{name}: 内嵌的 signal 模块既不是标准库也不是仓库那份，"
                    f"来源不明，需人工确认")
            else:
                say(f"signal 模块来自标准库 ✔ ({n})")

    # --- 关键检查 2：不能内嵌仓库的 CLI 脚本 ------------------------------
    cli = [n for n in modules if "signal_cli" in n.lower()]
    for n in cli:
        problems.append(f"{name}: 内嵌了 signal_cli 模块（{n}）—— "
                        f"这是 CLI 脚本，不该出现在包里")

    # --- 关键检查 3：应用自己的模块要在（防止 --hidden-import 漏掉）-------
    if name.lower() == "signallight.exe":
        need = ["state", "theme", "metrics", "cooling", "ecfan"]
        for m in need:
            hit = [n for n in modules if n == m or n.startswith(m + ".")]
            if not hit:
                problems.append(f"{name}: 缺少模块 {m} —— --hidden-import 漏了？")
            else:
                say(f"模块 {m} ✔")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="校验打包产物")
    ap.add_argument("--quiet", action="store_true", help="只在失败时输出")
    args = ap.parse_args()

    if not DIST.exists():
        print("[x] dist\\ 目录不存在 —— 先跑 打包.cmd")
        return 1

    exes = sorted(DIST.glob("*.exe"))
    if not exes:
        print("[x] dist\\ 下没有 exe —— 先跑 打包.cmd")
        return 1

    print("=" * 64)
    print("打包产物自检")
    print("=" * 64)

    all_problems: list[str] = []
    for exe in exes:
        print(f"  [{exe.name}]")
        problems = verify(exe, args.quiet)
        all_problems.extend(problems)
        if problems:
            for p in problems:
                print(f"    [X] {p}")
        else:
            print("    [OK]")

    print("=" * 64)
    if all_problems:
        print(f"[X] {len(all_problems)} 个问题 —— 不要发布这批产物")
        return 1
    print("[OK] 全部产物通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
