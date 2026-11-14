# -*- coding: utf-8 -*-
"""WorkBuddy Hook 入口 —— 把会话事件翻译成信号灯状态。

由 ~/.workbuddy/settings.json 的 hooks 段调用，事件 JSON 从 stdin 进来。

运行约束（都踩过）：
  * hook 是同步阻塞的，宿主会等它退出。所以只 import 标准库，
    用 python -S 启动约 125ms（默认启动 384ms，多出的时间全耗在 site 初始化）。
  * stdin 必须带超时读。万一宿主某次没传数据，read() 会永久阻塞，把宿主卡死。
  * 无论发生什么都要返回 0。退出码 2 会被宿主当成"拦截"，信号灯坏了
    绝不能拖累 AI 主流程。
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

# 顶层 import 也要兜住：app/ 被移走时这里会抛，而宿主把非 0 退出码
# 当成"拦截工具"。宁可静默不工作，也不能卡住用户的 AI 任务。
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
    import state as st  # noqa: E402
except Exception:  # noqa: BLE001
    sys.exit(0)

SRC = "workbuddy"

# running 的 TTL 决定"宿主崩溃后灯还会亮多久"。
# 取 5 分钟：足够覆盖单条长命令，又不至于让假信号赖太久。
TTL_RUN = 300.0
TTL_DONE = 10.0
TTL_WAIT = 180.0

# 只有出现这些词才认为是在等你操作，避免把普通通知误判成"待处理"
WAIT_HINTS = ("确认", "授权", "权限", "审批", "permission", "approve", "confirm")

# WorkBuddy 会把后台任务结束、系统注入等"非用户输入"也走 UserPromptSubmit。
# 实测后台任务完成时 prompt 是 'Use the TaskOutput tool with task_id="..."'。
# 这类消息不能当新任务处理：会把标题写成那句英文，还会把耗时重置。
SYNTHETIC_MARKS = (
    "use the taskoutput tool with task_id=",
    "[system notification",
    "<task-notification>",
    "this is an automated background-task event",
)


def _is_synthetic(raw: str) -> bool:
    head = raw.lstrip()[:300].lower()
    return any(mark in head for mark in SYNTHETIC_MARKS)


def _read_stdin(timeout: float = 1.5) -> bytes:
    box: list[bytes] = []

    def run() -> None:
        try:
            box.append(sys.stdin.buffer.read())
        except Exception:  # noqa: BLE001
            box.append(b"")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else b""


# WorkBuddy 会把引用消息、附件引用、@ 徽标以标签形式拼在 prompt 最前面。
# 不剥掉的话，标题栏显示的是 "<message_quote type=..." 这种垃圾。
_LEAD_BADGE = re.compile(r'^\s*@[\w.-]+#[^\s:]*:\s*"[^"]*"\s*')
_LEAD_BLOCK = re.compile(r"^\s*<([A-Za-z][\w:.-]*)\b[^>]*>.*?</\1>\s*", re.S)
_LEAD_TAG = re.compile(r"^\s*<[^>]{0,400}?/?>\s*")


def _strip_lead_markup(s: str) -> str:
    """剥掉开头的标签块与 @徽标。限 40 轮，防病态输入拖慢 hook。"""
    for _ in range(40):
        before = s
        s = _LEAD_BADGE.sub("", s)
        s = _LEAD_BLOCK.sub("", s)
        s = _LEAD_TAG.sub("", s)
        if s == before:
            break
    return s


def _clean(v, n: int = 40) -> str:
    raw = str(v or "")
    stripped = _strip_lead_markup(raw)
    # 剥空了就退回原文：宁可显示标签，也别显示一片空白
    text = stripped if stripped.strip() else raw
    return " ".join(text.split())[:n]


def _plain(v, n: int = 24) -> str:
    """标题类文本只需要压空白 + 截断，不走标记剥离（标题里不会有引用块）。"""
    return " ".join(str(v or "").split())[:n]


def _log(line: str) -> None:
    """滚动日志，用于排查"配了但没触发"。只记事件名和会话号，不记内容。"""
    try:
        p = st.APP_DIR / "hook.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > 64_000:
            p.unlink()
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


# ---------------- 工作空间名 ----------------
# 宿主把 cwd 一起传进来了，但目录名不一定是给人看的：
# 用户在 WorkBuddy 里改过工作区显示名时，真实显示名只存在这份映射里。
# 目录名是时间戳（C:\...\20260412160055）时也一样，只能靠映射兜底。
_WS_FILE = (
    Path(os.environ["AISIGNAL_HOME"]) / "workspace-display-names.json"
    if os.environ.get("AISIGNAL_HOME")
    else Path.home() / ".workbuddy" / "workspace-display-names.json"
)
_ws_cache: dict | None = None


def _display_names() -> dict:
    global _ws_cache
    if _ws_cache is not None:
        return _ws_cache
    _ws_cache = {}
    try:
        d = json.loads(_WS_FILE.read_text(encoding="utf-8"))
        ws = d.get("workspaces") if isinstance(d, dict) else None
        if isinstance(ws, dict):
            _ws_cache = ws
    except (OSError, ValueError):
        pass
    return _ws_cache


def _workspace_name(ev: dict) -> str:
    """cwd -> 工作空间显示名。取不到就返回空，绝不让标题因此变空。

    只认 workspace-display-names.json 的映射：命中才算工作空间，
    拿不到就返回空 —— 独立任务/对话的 cwd 不在这份映射里，
    以前退回目录名会把 ~/Downloads、/tmp 之类冒充成工作空间名。
    """
    cwd = str(ev.get("cwd") or "").strip()
    if not cwd:
        return ""
    norm = cwd.replace("\\", "/").rstrip("/")
    hit = _display_names().get(norm.lower())
    if isinstance(hit, dict):
        name = str(hit.get("displayName") or "").strip()
        if name:
            return _plain(name, 12)
    return ""


# ---------------- 会话标题 ----------------
# WorkBuddy 把会话标题落在 transcript 里，两种形态：
#   {"type":"ai-title","aiTitle":"…"}                       —— 标题生成时的原始记录
#   {"type":"session-meta","meta":{"codebuddy.ai/sessionTitle":[{…}]}}  —— 每轮开头复述一次
# 后者决定了一个关键优化：标题不用扫整个文件，从尾部就能读到。
# 长会话的 transcript 有几十 MB，全量扫描会让 hook 从 125ms 涨到几百 ms。
_TAIL_STEPS = (24_000, 96_000, 384_000)


def _title_of(d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    if d.get("type") == "ai-title":
        return str(d.get("aiTitle") or "").strip()
    meta = d.get("meta")
    if isinstance(meta, dict):
        arr = meta.get("codebuddy.ai/sessionTitle")
        if isinstance(arr, list):
            for item in reversed(arr):
                if isinstance(item, dict) and item.get("aiTitle"):
                    return str(item["aiTitle"]).strip()
    return ""


def _scan_tail(path: Path, nbytes: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - nbytes)
            f.seek(start)
            data = f.read()
    except OSError:
        return ""

    lines = data.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]  # 首行大概率被字节边界切断，解析必然失败，直接跳过
    for raw in reversed(lines):
        if b"Title" not in raw:  # aiTitle / sessionTitle 都含这个片段，先做廉价预筛
            continue
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        t = _title_of(d)
        if t:
            return _plain(t, 40)
    return ""


def _scan_head(path: Path, nbytes: int = 65_536) -> str:
    """扫文件开头找 ai-title。

    实测这条记录固定在会话前 13KB 左右（第 5 行），因为它在本轮第一次
    回复前就写好了。而一轮内的工具调用能追加几百 KB —— 从尾部反而读不到，
    这是先扫头再扫尾的原因。
    """
    try:
        with open(path, "rb") as f:
            data = f.read(nbytes)
    except OSError:
        return ""
    lines = data.split(b"\n")
    if len(data) >= nbytes and lines:
        lines = lines[:-1]  # 读满时末行可能被切断
    for raw in lines:
        if b"aiTitle" not in raw:
            continue
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        t = _title_of(d)
        if t:
            return _plain(t, 40)
    return ""


def _conv_title(ev: dict) -> str:
    """取会话标题。取不到返回空 —— 调用方负责退化，不能让标题拦住一切。"""
    p = str(ev.get("transcript_path") or "").strip()
    if not p:
        return ""
    path = Path(p)
    t = _scan_head(path)
    if t:
        return t
    # 头部没有：多半是首轮标题还没生成。兜底看一眼尾部，
    # 顺带覆盖"用户手动改过标题"的情况（改名会追加新的 session-meta）。
    for nbytes in _TAIL_STEPS:
        t = _scan_tail(path, nbytes)
        if t:
            return t
        try:
            if path.stat().st_size <= nbytes:
                break  # 文件还没这么大，再扩也是白读
        except OSError:
            break
    return ""


def handle(ev: dict) -> str:
    name = str(ev.get("hook_event_name") or ev.get("event") or "")
    sid = _clean(ev.get("session_id"), 8) or "app"
    ws = _workspace_name(ev)

    if name == "SessionStart":
        # 清掉上次会话可能残留的槽位
        st.write_slot(SRC, sid, st.IDLE)
        return "clear"

    if name == "UserPromptSubmit":
        raw = str(ev.get("prompt") or "")
        if _is_synthetic(raw):
            # 系统注入 / 后台任务通知：宿主确实在干活，但这不是新任务。
            # 只续心跳，保住原标题和开始时间。
            cur = st.read_slot(SRC, sid)
            if cur and cur.state == st.RUNNING:
                st.write_slot(SRC, sid, st.RUNNING, cur.title, cur.detail, ttl=TTL_RUN,
                              started=cur.ts, workspace=cur.workspace or ws)
                return "beat(synthetic)"
            if cur:
                return "skip(synthetic)"
            title = "WorkBuddy 后台任务"
            st.write_slot(SRC, sid, st.RUNNING, title, "正在执行", ttl=TTL_RUN, workspace=ws)
            return "running(synthetic)"
        # 第一行放标识（工作空间 - 对话名），第二行放这一轮到底在干什么。
        # 会话标题是别处生成的，首轮提交时往往还没有，此时 title 留空，
        # 让界面只显示工作空间，而不是把指令在上下两行重复一遍。
        brief = _clean(raw) or "WorkBuddy 指令"
        conv = _conv_title(ev)
        title = conv or (brief if not ws else "")
        st.write_slot(SRC, sid, st.RUNNING, title, brief, ttl=TTL_RUN, workspace=ws)
        return "running"

    if name == "PostToolUse":
        # 心跳：长任务靠它续期。只更新已存在的槽位，不主动创建。
        # started 必须带上原始开始时间，否则耗时会被心跳重置成"刚刚"。
        cur = st.read_slot(SRC, sid)
        if cur and cur.state == st.RUNNING:
            # 对话标题优先于占位文本：独立任务首轮 title = brief，
            # 如果只看 cur.title 非空就跳过 transcript，真正的对话标题永远补不上。
            # _conv_title 只读尾部 24KB，拿到后写回 title，后续心跳同值不重写。
            title = _conv_title(ev) or cur.title
            st.write_slot(SRC, sid, st.RUNNING, title, cur.detail, ttl=TTL_RUN,
                          started=cur.ts, workspace=cur.workspace or ws)
            return "beat"
        return "skip"

    if name == "Notification":
        msg = _clean(ev.get("message"), 80)
        low = msg.lower()
        if any(k in low for k in WAIT_HINTS):
            cur = st.read_slot(SRC, sid)
            title = cur.title if cur and cur.title else _conv_title(ev) or "WorkBuddy 任务"
            st.write_slot(SRC, sid, st.WAITING, title, msg or "等待你确认", ttl=TTL_WAIT,
                          workspace=(cur.workspace if cur else "") or ws)
            return "waiting"
        # 记下摘要：这是唯一能判断"等待确认"能不能触发的依据。
        # 若一直是 (no message field)，说明宿主不放文本，这条链路要换方案。
        return "ignore | " + (msg[:36] or "(no message field)")

    if name == "Stop":
        cur = st.read_slot(SRC, sid)
        title = (cur.title if cur and cur.title else "") or _conv_title(ev) or "WorkBuddy 任务"
        st.write_slot(SRC, sid, st.DONE, title, "已完成", ttl=TTL_DONE,
                      workspace=(cur.workspace if cur else "") or ws)
        return "done"

    if name == "SessionEnd":
        st.write_slot(SRC, sid, st.IDLE)
        return "clear"

    return "-"


def main() -> int:
    raw = _read_stdin()
    if not raw.strip():
        _log("(empty) -> 0")
        return 0
    try:
        ev = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        _log("(bad json) -> 0")
        return 0
    if not isinstance(ev, dict):
        return 0
    try:
        name = str(ev.get("hook_event_name") or ev.get("event") or "?")
        sid = _clean(ev.get("session_id"), 8) or "app"
        _log(f"{name} {sid} -> {handle(ev)}")
    except Exception:  # noqa: BLE001 - 任何异常都不能传出去
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
