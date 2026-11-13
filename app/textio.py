# -*- coding: utf-8 -*-
"""子进程文本解码。

中文 Windows 的 PowerShell 5.1 输出 ANSI(GBK)，PowerShell 7 输出 UTF-8，
而 powercfg 的输出跟随控制台代码页。统一走这里，别再各写一份 ——
写错编码的表现是 UnicodeDecodeError 在读取线程里炸掉，主流程反而看不见。
"""

from __future__ import annotations


def decode(raw: bytes) -> str:
    if not raw:
        return ""
    for enc in ("utf-8", "mbcs"):
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")
