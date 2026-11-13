# -*- coding: utf-8 -*-
"""Codex 任务监听。

为什么不用 Codex 的 notify 配置：
  1) notify 只在一轮结束时触发一次，拿不到"进行中"；
  2) 本机 notify 已被 codex-computer-use.exe turn-ended 占用，
     覆盖它会破坏 Codex 桌面端的 computer-use 功能。
所以改为增量读取会话记录：

    <CODEX_HOME>/sessions/YYYY/MM/DD/rollout-*.jsonl

每轮任务起止对应 event_msg.payload.type 的 task_started / task_complete，
任务标题来自 item_completed 里 type=UserMessage 的 content。

这是 Codex 的内部格式，不属于公开契约。因此本模块的原则是：
解析失败一律静默跳过，绝不抛到调用方 —— Codex 那一路坏了不能影响 WorkBuddy。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from pathlib import Path

import state as st

CODEX_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
SESSIONS = CODEX_HOME / "sessions"

# 会话文件超过这么久没有活动，就认为这一轮不再活跃（避免崩溃后灯长亮）
IDLE_WINDOW = 8 * 60.0
# 在跑但文件长时间不增长：Codex 可能没把 complete 落盘，强制收尾
MAX_SILENT = 15 * 60.0
# 心跳续期间隔：任务在跑但文件暂时没新事件时，靠它撑住 TTL
BEAT_EVERY = 10.0
# running 槽位 TTL，需大于心跳间隔数倍，留足长命令执行的余量
RUNNING_TTL = 120.0

SRC = "codex"
SID = "app"

# 文件名形如 rollout-2026-11-13T23-36-01-<uuid>.jsonl，内嵌会话开始时间。
# 实测文件 mtime 不可靠（有的比内容末时间早半小时，像是被同步/复制保留了旧 mtime），
# 所以排序和新鲜度判断都以文件名里的时间为准。
_NAME_RE = re.compile(r"rollout-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")


def _name_time(p: Path) -> float:
    m = _NAME_RE.search(p.name)
    if not m:
        return 0.0
    try:
        return _dt.datetime(*map(int, m.groups())).timestamp()
    except (ValueError, OSError):
        return 0.0


def _parse(raw: bytes):
    """单行 -> ('start'|'prompt'|'complete', payload)。认不出就返回 None。"""
    try:
        d = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or d.get("type") != "event_msg":
        return None
    pl = d.get("payload")
    if not isinstance(pl, dict):
        return None
    t = pl.get("type")
    if t == "task_started":
        return ("start", pl)
    if t == "task_complete":
        return ("complete", pl)
    if t == "item_completed":
        it = pl.get("item")
        if isinstance(it, dict) and it.get("type") == "UserMessage":
            return ("prompt", it)
    return None


def _title_from(item: dict) -> str:
    """从 UserMessage 里挑一句像任务描述的话。

    首条消息常被注入上下文（"## Referenced ..."、代码块、XML 标签），
    这些都跳过，取第一行真正的自然语言。
    """
    for c in item.get("content") or []:
        if not isinstance(c, dict) or c.get("type") != "text":
            continue
        for line in str(c.get("text") or "").splitlines():
            s = line.strip()
            if not s or s[0] in "#<`|>":
                continue
            s = re.sub(r"\s+", " ", s)
            return s[:40]
    return ""


class CodexWatcher:
    """增量读取 Codex 会话文件，把任务起止翻译成槽位写入。"""

    def __init__(self) -> None:
        self._path: Path | None = None
        self._offset = 0
        self._scan_t = 0.0
        self._candidates: list[Path] = []
        self._title = ""
        self._running = False
        self._beat = 0.0
        self._started = 0.0
        self._last_growth = 0.0

    # ---------- 文件发现 ----------

    def _scan(self, now: float) -> None:
        """只扫最近两天的日期目录，避免遍历全部历史。"""
        if now - self._scan_t < 5.0 and self._candidates:
            return
        self._scan_t = now
        found: list[Path] = []
        today = _dt.date.today()
        for back in (0, 1):
            d = today - _dt.timedelta(days=back)
            sub = SESSIONS / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
            try:
                found.extend(sub.glob("rollout-*.jsonl"))
            except OSError:
                continue
        try:
            found.sort(key=lambda p: (_name_time(p), p.stat().st_mtime), reverse=True)
        except OSError:
            pass
        self._candidates = found[:8]

    def _pick(self, now: float) -> Path | None:
        self._scan(now)

        # 正在跟踪的文件只要还有未读字节，就继续读它。
        # 不能用 mtime 把它踢掉 —— Codex 的 mtime 可能滞后很久，
        # 而内容其实正在追加。
        if self._path is not None:
            try:
                if self._path.exists() and self._path.stat().st_size > self._offset:
                    return self._path
            except OSError:
                self._path = None

        best: tuple[float, Path] | None = None
        for p in self._candidates:
            t = _name_time(p) or p.stat().st_mtime
            if now - t > IDLE_WINDOW:
                continue
            if best is None or t > best[0]:
                best = (t, p)
        return best[1] if best else None

    # ---------- 增量读取 ----------

    def _tail_state(self, path: Path) -> list[tuple[str, dict]]:
        """首次接入时回扫文件尾部，判断当前是否正有一轮在跑。

        否则挂件重启后，正在跑的任务会从"进行中"退化成"无任务"。
        """
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                f.seek(max(0, size - 256 * 1024))
                data = f.read()
        except OSError:
            return []

        last_start = -1
        last_done = -1
        last_prompt: tuple[int, dict] | None = None
        for i, raw in enumerate(data.splitlines()):
            ev = _parse(raw)
            if not ev:
                continue
            if ev[0] == "start":
                last_start = i
            elif ev[0] == "complete":
                last_done = i
            elif ev[0] == "prompt":
                last_prompt = (i, ev[1])

        if last_start < 0 or last_done > last_start:
            return []
        out: list[tuple[str, dict]] = [("start", {})]
        if last_prompt and last_prompt[0] > last_start:
            out.append(("prompt", last_prompt[1]))
        return out

    def _read_new(self, path: Path) -> list[tuple[str, dict]]:
        try:
            size = path.stat().st_size
        except OSError:
            return []

        if path != self._path:
            self._path = path
            events = self._tail_state(path)
            self._offset = size
            return events

        if size < self._offset:  # 文件被截断或替换
            self._offset = 0
        if size == self._offset:
            return []

        try:
            with open(path, "rb") as f:
                f.seek(self._offset)
                chunk = f.read(size - self._offset)
        except OSError:
            return []
        self._offset = size

        out: list[tuple[str, dict]] = []
        for raw in chunk.splitlines():
            ev = _parse(raw)
            if ev:
                out.append(ev)
        self._last_growth = time.time()
        return out

    # ---------- 槽位写入 ----------

    def _emit_running(self) -> None:
        st.write_slot(
            SRC, SID, st.RUNNING,
            self._title or "Codex 任务", "正在执行",
            ttl=RUNNING_TTL,
            started=self._started or None,  # 心跳保留原开始时间，耗时才不会归零
        )

    def _emit_done(self, payload: dict) -> None:
        secs = 0.0
        try:
            secs = float(payload.get("duration_ms") or 0) / 1000.0
        except (TypeError, ValueError):
            pass
        detail = f"用时 {int(secs)}s" if secs >= 1 else "已完成"
        st.write_slot(SRC, SID, st.DONE, self._title or "Codex 任务", detail, ttl=12.0)

    # ---------- 对外 ----------

    def poll(self) -> bool:
        """轮询一次，返回是否发生了状态变化。任何异常都不外抛。"""
        try:
            return self._poll()
        except Exception:  # noqa: BLE001 - 监听器坏了不能拖垮挂件
            return False

    def _poll(self) -> bool:
        now = time.time()
        path = self._pick(now)

        if path is None:
            # 会话已静默：停止心跳，让 TTL 自然收掉
            self._running = False
            self._path = None
            return False

        changed = False
        try:
            events = self._read_new(path)
        except Exception:  # noqa: BLE001
            events = []

        for kind, data in events:
            if kind == "start":
                self._title = ""
                self._running = True
                self._started = now
                self._beat = now
                self._last_growth = now
                self._emit_running()
                changed = True
            elif kind == "prompt":
                t = _title_from(data)
                if t and t != self._title:
                    self._title = t
                    if self._running:
                        self._beat = now
                        self._emit_running()
                        changed = True
            elif kind == "complete":
                self._running = False
                self._emit_done(data)
                changed = True

        # 任务在跑但文件暂时没有新事件（比如长命令执行中）：靠心跳撑住 TTL
        if self._running and now - self._beat > BEAT_EVERY:
            self._beat = now
            self._emit_running()

        # 兜底：长时间完全没有新内容，多半是 Codex 没把 complete 落盘。
        # 宁可收掉也不能一直亮假灯。
        if self._running and now - self._last_growth > MAX_SILENT:
            self._running = False
            st.write_slot(SRC, SID, st.IDLE)
            changed = True
        return changed
