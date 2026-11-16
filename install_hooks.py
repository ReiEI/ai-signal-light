# -*- coding: utf-8 -*-
"""把 WorkBuddy hook 接入写进 ~/.workbuddy/settings.json。

为什么用安装器而不是让用户手改：
  settings.json 里有加密字段（$wbEncrypted）和几十项插件配置，
  手改极易破坏结构；而且重复执行必须幂等，不能越装越多条。

用法：
  python install_hooks.py            安装 / 更新
  python install_hooks.py --remove   卸载
  python install_hooks.py --check    只看当前状态
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = Path.home() / ".workbuddy" / "settings.json"
MARK = "hook_wb.py"


def use_settings(path: str) -> None:
    """允许指定配置文件路径（自测用，也方便多环境安装）。"""
    global SETTINGS
    SETTINGS = Path(path).expanduser().resolve()

# 事件 -> 超时秒数。都是毫秒级脚本，5 秒足够，绝不拖住宿主。
EVENTS = {
    "SessionStart": 5,
    "UserPromptSubmit": 5,
    "PostToolUse": 5,
    "Notification": 5,
    "Stop": 5,
    "SessionEnd": 5,
}


def _command() -> str:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    hook = ROOT / MARK
    # 用正斜杠：hook 在 Git Bash 下执行，反斜杠会被当转义符
    return f'"{py.as_posix()}" -S "{hook.as_posix()}"'


def _is_ours(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    for h in entry.get("hooks") or []:
        if isinstance(h, dict) and MARK in str(h.get("command") or ""):
            return True
    return False


def _load() -> dict | None:
    try:
        raw = SETTINGS.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        print(f"[x] 读不了 {SETTINGS}: {e}")
        return None
    try:
        data = json.loads(raw)
    except ValueError as e:
        print(f"[x] {SETTINGS} 不是合法 JSON（{e}），已中止，原文件未改动。")
        return None
    if not isinstance(data, dict):
        print("[x] settings.json 顶层不是对象，已中止。")
        return None
    return data


def _backup() -> Path | None:
    if not SETTINGS.exists():
        return None
    dst = SETTINGS.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        shutil.copy2(SETTINGS, dst)
        return dst
    except OSError:
        return None


def _save_inplace(text: str) -> bool:
    """WorkBuddy 正在运行时退回原地写。

    os.replace 需要目标文件允许被删除，WorkBuddy 打开 settings.json 时
    不给 FILE_SHARE_DELETE，所以原子替换必然失败；但普通句柄允许写入。
    先写后截断，保证内容完整（中途崩溃最坏也只是内容变短，备份可还原）。

    注意：原地写绕过原子替换，WorkBuddy 退出时可能用内存里的旧配置覆盖，
    所以接完仍要重启 WorkBuddy 才真正生效。
    """
    data = text.encode("utf-8")
    try:
        mode = "r+b" if SETTINGS.exists() else "w+b"
        with open(SETTINGS, mode) as f:
            f.seek(0)
            f.write(data)
            f.truncate(len(data))
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        print(f"[x] 原地写失败: {e}")
        return False
    return True


def _save(data: dict) -> bool:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = SETTINGS.with_name("settings.json.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, SETTINGS)
        return True
    except PermissionError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if _save_inplace(text):
            print("[i] WorkBuddy 正在运行，已改为原地写入（非原子）。")
            print("    请重启 WorkBuddy 后再验证；它退出时可能用旧配置覆盖。")
            return True
        print("[x] 写入被拒绝 —— WorkBuddy 正在运行并锁定了这份配置。")
        print("    请完全退出 WorkBuddy（右键托盘图标 -> 退出），再运行本脚本。")
        return False
    except OSError as e:
        print(f"[x] 写入失败: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def install() -> int:
    data = _load()
    if data is None:
        return 1
    cmd = _command()
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}

    for ev, timeout in EVENTS.items():
        arr = hooks.get(ev)
        if not isinstance(arr, list):
            arr = []
        arr = [e for e in arr if not _is_ours(e)]  # 幂等：先删旧条目
        arr.append({"hooks": [{"type": "command", "command": cmd, "timeout": timeout}]})
        hooks[ev] = arr

    data["hooks"] = hooks

    bak = _backup()
    if not _save(data):
        return 1

    print("[ok] WorkBuddy hook 已接入")
    print(f"     事件: {', '.join(EVENTS)}")
    print(f"     命令: {cmd}")
    if bak:
        print(f"     备份: {bak}")
    print()
    print("     注意：WorkBuddy 若正在运行，请在下次重启后再验证；")
    print("           它退出时可能会把内存里的旧配置写回。")
    return 0


def remove() -> int:
    data = _load()
    if data is None:
        return 1
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        print("[i] 没有 hooks 配置，无需卸载。")
        return 0

    for ev in list(hooks):
        arr = hooks.get(ev)
        if not isinstance(arr, list):
            continue
        left = [e for e in arr if not _is_ours(e)]
        if left:
            hooks[ev] = left
        else:
            hooks.pop(ev, None)
    if not hooks:
        data.pop("hooks", None)

    bak = _backup()
    if not _save(data):
        return 1
    print("[ok] 已移除 WorkBuddy hook 接入")
    if bak:
        print(f"     备份: {bak}")
    return 0


def check() -> int:
    data = _load()
    if data is None:
        return 1
    hooks = data.get("hooks") or {}
    hits = [ev for ev, arr in hooks.items() if isinstance(arr, list) and any(_is_ours(e) for e in arr)]
    if hits:
        print(f"[i] 已接入事件: {', '.join(sorted(hits))}")
    else:
        print("[i] 尚未接入。运行 python install_hooks.py 安装。")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    path_arg = None
    if "--settings" in args:
        i = args.index("--settings")
        if i + 1 < len(args):
            path_arg = args[i + 1]
            del args[i : i + 2]
    if path_arg:
        use_settings(path_arg)

    arg = args[0] if args else ""
    if arg == "--remove":
        sys.exit(remove())
    if arg == "--check":
        sys.exit(check())
    sys.exit(install())
