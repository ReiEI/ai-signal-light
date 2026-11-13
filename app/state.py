# -*- coding: utf-8 -*-
"""状态模型与状态文件读写。

核心机制：状态由外部"生产者"写入 state.json，本程序只做消费方。
生产者可以是 AI 工具的 Hook、脚本、批处理、命令行，任意语言。
这样信号灯本身与具体 AI 工具解耦，换工具只换写入端。

关键设计 —— TTL 心跳：
  running / waiting 状态必须由生产者持续续期（每次更新 ts）。
  超过 ttl 未续期，视为"信号源已死"，自动降级为 idle。
  这条规则防止 AI 进程崩溃后灯还一直亮着 —— 假信号比没信号更糟。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# AISIGNAL_HOME 可整体重定向数据目录；测试靠它隔离，避免把假信号写进真实状态目录
# —— 否则测试完残留的 waiting 会被当成真信号显示出来。
_HOME = os.environ.get("AISIGNAL_HOME")
APP_DIR = Path(_HOME) if _HOME else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AIStatusLight"
STATE_FILE = APP_DIR / "state.json"
SETTINGS_FILE = APP_DIR / "settings.json"

# 状态常量
IDLE = "idle"
RUNNING = "running"
DONE = "done"
WAITING = "waiting"
FAILED = "failed"

VALID_STATES = (IDLE, RUNNING, DONE, WAITING, FAILED)

# 中文标签
LABELS = {
    IDLE: "无任务",
    RUNNING: "进行中",
    DONE: "新完成",
    WAITING: "待处理",
    FAILED: "失败",
}

# 视觉优先级（同时存在多源时取高者，当前单源也用得上）
PRIORITY = {FAILED: 5, WAITING: 4, RUNNING: 3, DONE: 2, IDLE: 0}

# 终态停留时长（秒）
DONE_HOLD = 8.0
FAILED_HOLD = 60.0

# 默认 TTL
DEFAULT_TTL = {"running": 12.0, "waiting": 90.0, "done": 8.0, "failed": 60.0, "idle": 0.0}


def ensure_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


class Status:
    """不用 dataclass：它会连带 import inspect，让 hook 每次启动多花约 150ms。
    hook 在每次工具调用后都要跑一次，这点开销会被放大到每个回合。
    """

    def __init__(
        self,
        state: str = IDLE,
        title: str = "",
        detail: str = "",
        ts: float = 0.0,
        ttl: float = 0.0,
        stale: bool = False,
        age: float = 0.0,
        peers: int = 0,
        source: str = "",
        beat: float = 0.0,
        workspace: str = "",
    ) -> None:
        self.state = state
        self.title = title
        self.detail = detail
        self.ts = ts
        self.ttl = ttl
        self.stale = stale      # 是否因 TTL 过期被降级
        self.age = age
        self.peers = peers      # 除主状态外，其余仍活跃的来源数
        self.source = source    # 主状态来自哪个源
        self.beat = beat        # 最后心跳时刻；0 表示与 ts 相同
        # 工作空间名，与 title（对话名）分开存：渲染时要按可用宽度决定
        # 丢弃前者还是截断后者，拼成一个字符串就再也分不开了。
        self.workspace = workspace

    def _tuple(self) -> tuple:
        return (
            self.state, self.title, self.detail, self.ts, self.ttl,
            self.stale, self.age, self.peers, self.source, self.beat,
            self.workspace,
        )

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self._tuple() == other._tuple()  # type: ignore[attr-defined]

    def __repr__(self) -> str:
        return (
            f"Status(state={self.state!r}, title={self.title!r}, detail={self.detail!r}, "
            f"ts={self.ts:.3f}, ttl={self.ttl}, stale={self.stale}, age={self.age:.1f}, "
            f"peers={self.peers}, source={self.source!r}, beat={self.beat:.3f}, "
            f"workspace={self.workspace!r})"
        )

    @property
    def label(self) -> str:
        return LABELS.get(self.state, self.state)


class StateReader:
    """轮询读取 state.json，容错 + 单调性保护。"""

    def __init__(self) -> None:
        self._last = Status()
        self._last_mtime = 0.0

    def read(self) -> Status:
        try:
            st = STATE_FILE.stat()
            mtime = st.st_mtime
        except OSError:
            # 文件不存在：只有当前是 idle 才回落到 idle，
            # 否则保留上次状态让 TTL 逻辑决定（避免文件被删导致瞬间闪灰）
            if self._last.state == IDLE:
                return self._last
            mtime = self._last_mtime

        if mtime != self._last_mtime:
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                state = str(data.get("state", IDLE)).lower()
                if state not in VALID_STATES:
                    state = IDLE
                ts = float(data.get("ts") or time.time())
                # 单调性保护：时间戳倒退则忽略（防多写入端乱序覆盖）
                if ts >= self._last.ts:
                    self._last = Status(
                        state=state,
                        title=str(data.get("title") or "")[:120],
                        detail=str(data.get("detail") or "")[:160],
                        ts=ts,
                        ttl=float(data.get("ttl") or DEFAULT_TTL.get(state, 0.0) or 0.0),
                        beat=float(data.get("beat") or ts),
                        workspace=str(data.get("workspace") or "")[:24],
                    )
                    self._last_mtime = mtime
            except (OSError, ValueError, TypeError):
                # 文件正在写入 / JSON 损坏：保持上次状态，不打断显示
                pass

        return self._decay(self._last)

    def _decay(self, s: Status) -> Status:
        now = time.time()
        # age 用于显示"已持续多久"，必须从任务开始算；
        # 判过期要用最后心跳，两者不能共用同一个时间戳。
        age = now - s.ts if s.ts else 0.0
        silent = now - (s.beat or s.ts) if s.ts else 0.0
        out = Status(
            state=s.state, title=s.title, detail=s.detail,
            ts=s.ts, ttl=s.ttl, stale=False, age=age,
            peers=s.peers, source=s.source, beat=s.beat or s.ts,
            workspace=s.workspace,
        )

        if s.state in (RUNNING, WAITING):
            ttl = s.ttl or DEFAULT_TTL[s.state]
            if silent > ttl:
                out.state = IDLE
                out.stale = True
                out.detail = ""
        elif s.state == DONE and silent > (s.ttl or DONE_HOLD):
            out.state = IDLE
            out.title = ""
        elif s.state == FAILED and silent > (s.ttl or FAILED_HOLD):
            out.state = IDLE
            out.title = ""

        return out


def write_state(
    state: str, title: str = "", detail: str = "", ttl: float | None = None,
    started: float | None = None, workspace: str = "",
) -> Status:
    """原子写入状态文件。生产者侧统一走这里，避免半截 JSON。

    started：心跳续期时传入原始开始时间，否则每写一次都会把耗时重置为 0。
    """
    ensure_dir()
    state = state.lower()
    if state not in VALID_STATES:
        raise ValueError(f"未知状态: {state}（可选 {', '.join(VALID_STATES)}）")
    if ttl is None:
        ttl = DEFAULT_TTL.get(state, 0.0)

    now = time.time()
    payload = {
        "state": state,
        "title": title,
        "detail": detail,
        "workspace": workspace,
        "ts": float(started) if started else now,
        "beat": now,
        "ttl": ttl,
    }
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_FILE)
    return Status(state, title, detail, payload["ts"], ttl, beat=payload["beat"],
                  workspace=workspace)


# ---------------- 多源槽位 ----------------
# 为什么需要：WorkBuddy 与 Codex 会并发产出任务信号。若共写一个 state.json，
# 两边会互相覆盖，灯在两三个状态之间来回闪。改为每个来源写自己的槽位文件，
# 渲染端按优先级聚合。单源场景行为与旧的单文件完全一致。

SLOT_DIR = APP_DIR / "slots"

SOURCE_LABELS = {"workbuddy": "WorkBuddy", "codex": "Codex", "manual": ""}


class Slot:
    """同 Status：手写类以避开 dataclasses 的 import 开销。"""

    def __init__(
        self,
        source: str = "",
        sid: str = "",
        state: str = IDLE,
        title: str = "",
        detail: str = "",
        ts: float = 0.0,
        ttl: float = 0.0,
        beat: float = 0.0,
        workspace: str = "",
    ) -> None:
        self.source = source
        self.sid = sid
        self.state = state
        self.title = title
        self.detail = detail
        self.ts = ts      # 任务开始时间，用于算耗时
        self.ttl = ttl
        self.beat = beat  # 最后一次心跳，用于判 TTL
        self.workspace = workspace  # 工作空间名；空表示写入端没提供

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self.__dict__ == other.__dict__  # type: ignore[attr-defined]

    def __repr__(self) -> str:
        return (
            f"Slot(source={self.source!r}, sid={self.sid!r}, state={self.state!r}, "
            f"title={self.title!r}, detail={self.detail!r}, ts={self.ts:.3f}, "
            f"ttl={self.ttl}, beat={self.beat:.3f}, workspace={self.workspace!r})"
        )

    @property
    def label(self) -> str:
        return LABELS.get(self.state, self.state)

    def expired(self, now: float) -> bool:
        # 用 beat 判过期：心跳只推进 beat，不动 ts，否则耗时会永远显示"刚刚"
        last = self.beat or self.ts
        age = now - last if last else 1e9
        if self.state in (RUNNING, WAITING):
            return age > (self.ttl or DEFAULT_TTL[self.state])
        if self.state == DONE:
            return age > (self.ttl or DONE_HOLD)
        if self.state == FAILED:
            return age > (self.ttl or FAILED_HOLD)
        return True


def _slot_name(source: str, sid: str) -> str:
    def clean(v: str, n: int) -> str:
        return "".join(c for c in str(v) if c.isalnum() or c in "-_")[:n]

    return f"{clean(source, 16) or 'src'}__{clean(sid, 40) or 'app'}.json"


def write_slot(
    source: str,
    sid: str,
    state: str,
    title: str = "",
    detail: str = "",
    ttl: float | None = None,
    started: float | None = None,
    workspace: str = "",
) -> None:
    """写一个来源槽位。state=idle 表示清除该槽位。IO 失败静默忽略。

    started 用于心跳续期时保留原始开始时间；不传则视为新一轮任务。
    """
    state = str(state).lower()
    if state not in VALID_STATES:
        raise ValueError(f"未知状态: {state}")
    try:
        path = SLOT_DIR / _slot_name(source, sid)
        if state == IDLE:
            path.unlink(missing_ok=True)
            return
        if ttl is None:
            ttl = DEFAULT_TTL.get(state, 0.0)
        SLOT_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        payload = {
            "source": source,
            "sid": sid,
            "state": state,
            "title": str(title)[:120],
            "detail": str(detail)[:160],
            "workspace": str(workspace)[:24],
            "ts": float(started) if started else now,
            "beat": now,
            "ttl": ttl,
        }
        # 临时名带 pid，避免并发写同一槽位时互相踩到半截文件
        tmp = path.with_name(f".{path.stem}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def read_slot(source: str, sid: str) -> Slot | None:
    path = SLOT_DIR / _slot_name(source, sid)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return Slot(
            source=str(d.get("source") or ""),
            sid=str(d.get("sid") or ""),
            state=str(d.get("state") or IDLE).lower(),
            title=str(d.get("title") or ""),
            detail=str(d.get("detail") or ""),
            ts=float(d.get("ts") or 0.0),
            ttl=float(d.get("ttl") or 0.0),
            beat=float(d.get("beat") or d.get("ts") or 0.0),
            workspace=str(d.get("workspace") or ""),
        )
    except (TypeError, ValueError):
        return None


def read_slots(now: float | None = None) -> list[Slot]:
    """读出所有未过期槽位，按视觉优先级降序。过期文件顺手删除。"""
    now = time.time() if now is None else now
    out: list[Slot] = []
    try:
        paths = list(SLOT_DIR.glob("*.json"))
    except OSError:
        return out
    for f in paths:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            s = Slot(
                source=str(d.get("source") or ""),
                sid=str(d.get("sid") or ""),
                state=str(d.get("state") or IDLE).lower(),
                title=str(d.get("title") or "")[:120],
                detail=str(d.get("detail") or "")[:160],
                ts=float(d.get("ts") or 0.0),
                ttl=float(d.get("ttl") or 0.0),
                beat=float(d.get("beat") or d.get("ts") or 0.0),
                workspace=str(d.get("workspace") or "")[:24],
            )
        except (OSError, ValueError, TypeError):
            continue
        if s.state not in VALID_STATES or s.expired(now):
            try:
                f.unlink()
            except OSError:
                pass
            continue
        out.append(s)
    out.sort(key=lambda s: (PRIORITY.get(s.state, 0), s.ts), reverse=True)
    return out


def compose(slots: list[Slot], fallback: Status, now: float | None = None) -> Status:
    """把多源槽位合成为一个待渲染状态；无槽位时退回单文件状态。

    多源同时活跃时，细节行前缀标注来源名，并用 peers 带出其余任务数。
    """
    now = time.time() if now is None else now
    if not slots:
        return fallback
    top = slots[0]
    peers = len(slots) - 1
    detail = top.detail
    src = SOURCE_LABELS.get(top.source, "")
    if peers > 0 and src:
        detail = f"{src} · {detail}" if detail else src
    return Status(
        state=top.state,
        title=top.title,
        detail=detail,
        ts=top.ts,
        ttl=top.ttl,
        stale=False,
        age=now - top.ts if top.ts else 0.0,
        peers=peers,
        source=top.source,
        beat=top.beat or top.ts,
        workspace=top.workspace,
    )


# ---------------- 窗口设置持久化 ----------------

# style: standard 标准卡 / bar 横条 / stack 竖版；scale: 整体等比缩放
STYLE_IDS = ("standard", "bar", "stack")

DEFAULT_SETTINGS = {
    "x": None,
    "y": None,
    "on_top": True,
    "style": "standard",
    "scale": 1.0,
    "collapsed": False,   # 旧字段，仅用于把老配置迁移成 style=bar
    "opacity": 0.96,
    "temp_cmd": "",
    # 散热模块：默认 30 分钟自动收手。这个值不是"选项"，是安全边界 ——
    # 笔记本被锁在 100% 最小处理器状态过夜，第二天电池和风扇都受不了。
    "cool_minutes": 30,
    "burn_workers": 0,    # 0 = 按物理核数自动
    "cool_cycle": 8.9,    # EC 除尘计时重置周期（秒），小于 9 才会让风扇连续转

    # 显示元素：两个区块各自可关，硬件指标还能逐列关。
    # 关到底就是一张空卡片，所以 show_task 与"指标集合"至少留一个为真，
    # load_settings 会做兜底（手改配置改坏了也能拉回来）。
    "show_task": True,     # 区块1：AI 任务（灯 + 状态 + 名称 + 明细）
    "show_metrics": True,  # 区块2：硬件指标（总开关）
    "show_cpu": True,
    "show_mem": True,
    "show_temp": True,
    "show_net": True,
}

# 指标列的顺序即绘制顺序，改这里就等于改三套版式的列顺序
METRIC_KEYS = ("cpu", "mem", "temp", "net")
SHOW_KEYS = ("show_task", "show_metrics") + tuple("show_" + k for k in METRIC_KEYS)


def _as_bool(v, default: bool) -> bool:
    """配置里的布尔量容错：手改成 "false" / 0 / "" 都要认，不能一律 bool()。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("0", "false", "no", "off", "none", ""):
            return False
        if t in ("1", "true", "yes", "on"):
            return True
    return default



def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    s.update(raw)

    # 旧版本只有 collapsed 布尔量，没有 style：一次性迁移，避免升级后样式丢失
    if "style" not in raw and raw.get("collapsed"):
        s["style"] = "bar"
    if s.get("style") not in STYLE_IDS:
        s["style"] = "standard"

    try:
        s["scale"] = float(s.get("scale") or 1.0)
    except (TypeError, ValueError):
        s["scale"] = 1.0
    if not 0.7 <= s["scale"] <= 2.0:  # 手改坏了就回到标准档
        s["scale"] = 1.0

    s["temp_cmd"] = str(s.get("temp_cmd") or "")

    try:
        s["cool_minutes"] = int(s.get("cool_minutes") or 30)
    except (TypeError, ValueError):
        s["cool_minutes"] = 30
    if not 1 <= s["cool_minutes"] <= 180:  # 手改坏了就回到默认，别让它变成永久加热
        s["cool_minutes"] = 30

    try:
        s["burn_workers"] = int(s.get("burn_workers") or 0)
    except (TypeError, ValueError):
        s["burn_workers"] = 0

    try:
        s["cool_cycle"] = float(s.get("cool_cycle") or 8.9)
    except (TypeError, ValueError):
        s["cool_cycle"] = 8.9
    if not 2.0 <= s["cool_cycle"] <= 30.0:
        s["cool_cycle"] = 8.9

    for k in SHOW_KEYS:
        s[k] = _as_bool(s.get(k), True)
    # 两个区块都关 = 一张空卡片。真发生了（手改配置）就把任务区拉回来。
    if not s["show_task"] and not (s["show_metrics"] and metric_keys_of(s)):
        s["show_task"] = True
    return s


def metric_keys_of(s: dict) -> tuple[str, ...]:
    """从设置字典里取生效的指标列。总开关关掉时返回空。"""
    if not s.get("show_metrics", True):
        return ()
    return tuple(k for k in METRIC_KEYS if s.get("show_" + k, True))



def save_settings(data: dict) -> None:
    try:
        ensure_dir()
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        pass
