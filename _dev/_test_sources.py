# -*- coding: utf-8 -*-
"""多源信号链路的单元测试。

重点验证三件事：
  1. 槽位读写 + 优先级聚合 + TTL 清理
  2. Codex 会话解析（尾扫判定、增量读取、标题提取）
  3. 两个源并发时不互相覆盖

用临时目录，不碰真实的 %LOCALAPPDATA%\\AIStatusLight。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

# 必须在 import state 之前改掉数据目录
_TMP = Path(tempfile.mkdtemp(prefix="aisl-test-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")

import codex as cx  # noqa: E402
import state as st  # noqa: E402

ok = 0
bad = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        bad += 1
        print(f"  FAIL  {name}  -> {detail!r}")


def reset() -> None:
    if st.SLOT_DIR.exists():
        shutil.rmtree(st.SLOT_DIR, ignore_errors=True)


print("1) 槽位写入与读取")
reset()
st.write_slot("workbuddy", "abc12345", st.RUNNING, "写季度报告", "正在处理", ttl=60)
slots = st.read_slots()
check("写出 1 个槽位", len(slots) == 1, len(slots))
check("字段完整", slots[0].title == "写季度报告" and slots[0].state == st.RUNNING, slots[0])
check("read_slot 单读一致", (st.read_slot("workbuddy", "abc12345") or st.Slot()).title == "写季度报告")

print("2) 多源并发不覆盖")
st.write_slot("codex", "app", st.RUNNING, "重构模块", "正在执行", ttl=60)
slots = st.read_slots()
check("两个源并存", len(slots) == 2, [s.source for s in slots])
check("workbuddy 槽位未被覆盖", st.read_slot("workbuddy", "abc12345").title == "写季度报告")
check("codex 槽位独立", st.read_slot("codex", "app").title == "重构模块")

print("3) 优先级聚合")
reset()
st.write_slot("workbuddy", "old", st.RUNNING, "先发起的", "", ttl=60)
time.sleep(0.02)
st.write_slot("codex", "new", st.RUNNING, "后发起的", "", ttl=60)
view = st.compose(st.read_slots(), st.Status())
check("同优先级取时间更新的", view.title == "后发起的", view.title)
check("两源时 peers=1", view.peers == 1, view.peers)
st.write_slot("workbuddy", "old", st.WAITING, "等你确认", "需要授权", ttl=60)
view = st.compose(st.read_slots(), st.Status())
check("waiting 压过 running", view.state == st.WAITING, view.state)
check("waiting 标题优先", view.title == "等你确认", view.title)
check("多源时标注来源", "WorkBuddy" in view.detail, view.detail)
st.write_slot("codex", "new", st.FAILED, "跑测试", "退出码 1", ttl=60)
view = st.compose(st.read_slots(), st.Status())
check("failed 优先级最高", view.state == st.FAILED, view.state)
check("失败标题正确", view.title == "跑测试", view.title)
check("失败时 peers 仍计数", view.peers == 1, view.peers)

print("4) TTL 过期与清理")
reset()
st.write_slot("codex", "app", st.RUNNING, "长任务", "", ttl=0.15)
check("未到期可见", len(st.read_slots()) == 1, len(st.read_slots()))
time.sleep(0.35)
check("到期后读不到", len(st.read_slots()) == 0, len(st.read_slots()))
check("到期文件被清掉", not any(st.SLOT_DIR.glob("*.json")), list(st.SLOT_DIR.glob("*")))

print("5) idle 即清除")
st.write_slot("workbuddy", "s1", st.RUNNING, "任务", "", ttl=60)
st.write_slot("workbuddy", "s1", st.IDLE)
check("idle 后槽位消失", st.read_slot("workbuddy", "s1") is None)

print("6) 无槽位时退回单文件状态")
reset()
fallback = st.Status(st.DONE, "手动任务", "已完成", time.time(), 10.0)
view = st.compose([], fallback)
check("回退保持原状态", view.state == st.DONE and view.title == "手动任务", view)
check("回退不带来源标注", "·" not in view.detail, view.detail)

print("6.1) 心跳不重置耗时")
reset()
st.write_slot("codex", "app", st.RUNNING, "长任务", "执行中", ttl=60)
s1 = st.read_slot("codex", "app")
time.sleep(0.25)
st.write_slot("codex", "app", st.RUNNING, "长任务", "执行中", ttl=60, started=s1.ts)
s2 = st.read_slot("codex", "app")
check("ts 保持不变", abs(s2.ts - s1.ts) < 1e-6, (s1.ts, s2.ts))
check("beat 已推进", s2.beat > s1.beat, (s1.beat, s2.beat))
view = st.compose([s2], st.Status())
check("age 反映真实耗时而非归零", view.age >= 0.24, view.age)

print("6.2) 心跳能续命：总时长超 TTL 但不该降级")
reset()
st.write_slot("codex", "app", st.RUNNING, "长任务", "", ttl=0.4)
s = st.read_slot("codex", "app")
time.sleep(0.3)
st.write_slot("codex", "app", st.RUNNING, "长任务", "", ttl=0.4, started=s.ts)
time.sleep(0.3)  # 总 age≈0.6 > ttl，但距心跳仅 0.3
alive = st.read_slots()
check("靠心跳存活，未按总时长误杀", len(alive) == 1, len(alive))

print("6.3) 单文件路径同样区分 ts 与 beat")
reset()
t0 = time.time()
st.write_state(st.RUNNING, "手动任务", "执行中", ttl=0.5, started=t0)
time.sleep(0.35)
st.write_state(st.RUNNING, "手动任务", "执行中", ttl=0.5, started=t0)
st.reader_probe = st.StateReader()
view = st.reader_probe.read()
check("单文件未被总时长误降级", view.state == st.RUNNING, view.state)
check("单文件 age 为真实耗时", view.age >= 0.34, view.age)


# ---------------- Codex 解析 ----------------

def write_rollout(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def ev(payload: dict) -> dict:
    return {"timestamp": "2026-11-13T20:00:00Z", "type": "event_msg", "payload": payload}


def fresh(tag: str) -> str:
    """文件名必须带当前时间——Codex 靠文件名里的时间判断会话新鲜度。"""
    n = dt.datetime.now()
    return f"rollout-{n:%Y-%m-%dT%H-%M-%S}-{tag}.jsonl"


print("7) Codex 事件解析")
sess_root = _TMP / "codex-sessions"
today = dt.date.today()
day_dir = sess_root / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}"
cx.SESSIONS = sess_root


def clean_sessions() -> None:
    """每个用例清空会话目录：多文件同秒创建会让 _pick 选错目标。"""
    if sess_root.exists():
        shutil.rmtree(sess_root, ignore_errors=True)


reset()
clean_sessions()
roll = day_dir / fresh("t1")
write_rollout(roll, [ev({"type": "task_started", "turn_id": "t1"})])
w = cx.CodexWatcher()
w.poll()
s = st.read_slot("codex", "app")
check("首扫识别进行中", s is not None and s.state == st.RUNNING, s)

print("8) Codex 标题提取")
reset()
clean_sessions()
roll2 = day_dir / fresh("t2")
write_rollout(roll2, [
    ev({"type": "task_started"}),
    ev({"type": "item_completed", "item": {
        "type": "UserMessage",
        "content": [{"type": "text", "text": "## Referenced ChatGPT conversation:\n\n帮我重构 state.py 的槽位逻辑"}],
    }}),
])
w2 = cx.CodexWatcher()
w2.poll()
s = st.read_slot("codex", "app")
check("跳过注入前缀取真实标题", s and s.title == "帮我重构 state.py 的槽位逻辑", s.title if s else None)

print("9) Codex 增量读到完成事件")
reset()
clean_sessions()
roll3 = day_dir / fresh("t3")
write_rollout(roll3, [ev({"type": "task_started"})])
w3 = cx.CodexWatcher()
w3.poll()
check("先 running", (st.read_slot("codex", "app") or st.Slot()).state == st.RUNNING)
with open(roll3, "a", encoding="utf-8") as f:
    f.write(json.dumps(ev({"type": "item_completed", "item": {
        "type": "UserMessage", "content": [{"type": "text", "text": "跑一遍回归测试"}],
    }}), ensure_ascii=False) + "\n")
    f.write(json.dumps(ev({"type": "task_complete", "duration_ms": "42000"}), ensure_ascii=False) + "\n")
w3.poll()
s = st.read_slot("codex", "app")
check("转为 done", s and s.state == st.DONE, s.state if s else None)
check("标题随事件更新", s and s.title == "跑一遍回归测试", s.title if s else None)
check("耗时来自 duration_ms", s and "42" in s.detail, s.detail if s else None)

print("10) Codex 历史已完成不误报")
reset()
clean_sessions()
roll4 = day_dir / fresh("t4")
write_rollout(roll4, [
    ev({"type": "task_started"}),
    ev({"type": "task_complete", "duration_ms": "1000"}),
])
w4 = cx.CodexWatcher()
w4.poll()
check("接尾时不把旧 done 当新事件", st.read_slot("codex", "app") is None)

print("10.1) mtime 滞后不影响识别（实测 Codex 存在这种情况）")
reset()
clean_sessions()
roll5 = day_dir / fresh("mt")
write_rollout(roll5, [ev({"type": "task_started"})])
old = time.time() - 3600
os.utime(roll5, (old, old))  # 把 mtime 倒退 1 小时，模拟被同步工具保留旧时间
w5 = cx.CodexWatcher()
w5.poll()
s = st.read_slot("codex", "app")
check("mtime 早一小时仍能识别", s is not None and s.state == st.RUNNING, s)

print("10.2) 跟踪中的文件持续读到追加内容")
with open(roll5, "a", encoding="utf-8") as f:
    f.write(json.dumps(ev({"type": "task_complete", "duration_ms": "5000"}), ensure_ascii=False) + "\n")
os.utime(roll5, (old, old))  # 追加后 mtime 仍然滞后
w5.poll()
s = st.read_slot("codex", "app")
check("mtime 不变也能读到新追加的完成事件", s and s.state == st.DONE, s.state if s else None)

print("11) Codex 目录不存在时静默")
reset()
cx.SESSIONS = _TMP / "no-such-dir"
w6 = cx.CodexWatcher()
check("不抛异常", w6.poll() is False)
check("不产生槽位", len(st.read_slots()) == 0)

reset()
shutil.rmtree(_TMP, ignore_errors=True)
print(f"\n结果: {ok} 通过 / {bad} 失败")
sys.exit(1 if bad else 0)
