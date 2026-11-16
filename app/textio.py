# -*- coding: utf-8 -*-
"""子进程文本解码。

中文 Windows 的 PowerShell 5.1 输出 ANSI(GBK)，PowerShell 7 输出 UTF-8，
而 powercfg 的输出跟随控制台代码页。统一走这里，别再各写一份 ——
写错编码的表现是 UnicodeDecodeError 在读取线程里炸掉，主流程反而看不见。
"""

from __future__ import annotations

import io
import os
import sys


def decode(raw: bytes) -> str:
    if not raw:
        return ""
    for enc in ("utf-8", "mbcs"):
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def bind_console_encoding() -> None:
    """让 print 出的中文在 cmd 控制台里正常显示。

    Python 3.7+ 在 Windows 上默认用 UTF-8 写 stdout，但 cmd.exe 按系统
    ANSI 代码页（中文 Windows 是 GBK）解读字节，于是中文全变成乱码。
    这里把 stdout/stderr 重新绑到系统代码页。

    非 Windows 上不做任何事；绑定失败也静默忽略 —— 编码只是显示问题，
    不该让脚本因为它而失败。
    """
    if os.name != "nt":
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        buf = getattr(stream, "buffer", None)
        if buf is None:
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(
                buf, encoding="mbcs", errors="replace", line_buffering=True))
        except Exception:
            pass
