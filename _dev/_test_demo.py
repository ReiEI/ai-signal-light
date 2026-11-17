# -*- coding: utf-8 -*-
"""演示模式测试：一轮即结束 + 演示期间的���实状态巡检与补播报。

这两个行为都是"出问题时用户才会发现"的类型，所以必须钉死：

  1. 演示一轮就停。原来的实现用 `_demo_idx = (idx + 1) % DEMO_STEPS`
     取模回绕，点一次"演示五种状态"就永远停不下来 —— 用户没法回到
     正常工作状态，只能退出重开。
  2. 演示期间不能把真实状态静音。演示画面占着整个窗口，
     这段时间里真的来了新任务（完成 / 失败 / 待确认），
     用户在演示画面上是看不见的。结束后必须补播报，
     否则"点一下演示"就等于把真实进度静音了几十秒。

用临时目录，不碰真实的 %LOCALAPPDATA%。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="aisl-demo-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

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
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------- 桩件
class FakeSignalLight:
    """只保留被测逻辑，剥掉 Qt 与定时器。

    直接实例化真的 SignalLight 需要 QApplication（GUI 环境），
    而这里要测的是纯逻辑：轮次推进、巡检收集、补播报。
    """

    DEMO_STEPS = 6

    def __init__(self) -> None:
        self._demo = True
        self._demo_idx = 0
        self._demo_t0 = time.time()
        self._demo_finished = False
        self._demo_seen: dict[tuple, str] = {}
        self._demo_pending: list[tuple[str, str, str]] = []
        self._demo_gone: dict[tuple, str] = {}
        self.notified: list[tuple[str, str]] = []
        self._rows: list = []

    # ---- 被测方法（与 app/main.py 保持同一逻辑）----
    def _scan_real_slots(self) -> None:
        try:
            slots = st.read_slots()
        except OSError:
            return
        alive: set[tuple] = set()
        for s in slots:
            key = (s.source, s.sid)
            alive.add(key)
            self._demo_gone[key] = " - ".join(x for x in (s.workspace, s.title) if x)
            prev = self._demo_seen.get(key)
            if prev is None:
                self._demo_seen[key] = s.state
                if s.state != st.IDLE:
                    self._demo_pending.append((s.state, s.title, s.workspace))
                continue
            if s.state != prev:
                self._demo_seen[key] = s.state
                if s.state != st.IDLE:
                    self._demo_pending.append((s.state, s.title, s.workspace))
        for key, state in list(self._demo_seen.items()):
            if key not in alive and state != st.IDLE:
                self._demo_pending.append((st.DONE, self._demo_gone.get(key, ""), ""))
                self._demo_seen.pop(key, None)
                self._demo_gone.pop(key, None)

    def _finish_demo(self) -> None:
        self._demo = False
        self._demo_finished = True
        self._scan_real_slots()
        if self._demo_pending:
            self._announce_pending(self._demo_pending)
            self._demo_pending = []

    def _announce_pending(self, pending) -> None:
        if not pending:
            return
        lines = []
        for state, title, workspace in pending[-3:]:
            label = st.LABELS.get(state, state)
            name = " - ".join(x for x in (workspace, title) if x) or "任务"
            lines.append(f"{label}：{name}")
        body = "\n".join(lines)
        if len(pending) > 3:
            body += f"\n（演示期间共 {len(pending)} 条变化）"
        self.notified.append((body, "AI 工作信号灯 · 演示期间有变化"))

    def _advance(self) -> None:
        """模拟 _tick 里的一次演示步进。"""
        self._scan_real_slots()
        if self._demo_idx + 1 >= self.DEMO_STEPS:
            self._finish_demo()
        else:
            self._demo_idx += 1


# ================================================================ 用例
def clear_slots() -> None:
    """清空槽位目录。

    每个场景都要独立的现场：上一个场景留下的槽位（ttl=999，不会自己过期）
    在下一个场景里会被当成"期间新产生"，把结论搅乱。
    """
    st.ensure_dir()
    for f in st.SLOT_DIR.glob("*.json"):
        try:
            f.unlink()
        except OSError:
            pass


print("=" * 60)
print("演示一轮即结束")
print("=" * 60)

clear_slots()
w = FakeSignalLight()
check("初始处于演示中", w._demo is True)
check("初始未标记结束", w._demo_finished is False)

w._advance()
check("第 1 步后仍在演示", w._demo is True, f"_demo={w._demo}")
check("第 1 步后未结束", w._demo_finished is False)

# 走满 6 步：第 6 次 advance 时应该结束
for _ in range(5):
    w._advance()
check(f"走满 {FakeSignalLight.DEMO_STEPS} 步后结束", w._demo_finished is True)
check("结束后退出演示态", w._demo is False, f"_demo={w._demo}")
check("结束后不再推进轮次", w._demo_idx == FakeSignalLight.DEMO_STEPS - 1, w._demo_idx)

# 关键回归：再调多次也不能复活（原来是 % DEMO_STEPS 无限循环）
for _ in range(20):
    w._advance()
check("结束后反复调用不会复活演示", w._demo is False and w._demo_finished is True)

print()
print("=" * 60)
print("演示期间的真实状态巡检")
print("=" * 60)

# --- 场景 1：演示期间没有真实任务 → 不该有任何播报
st.ensure_dir()
clear_slots()
w2 = FakeSignalLight()
w2._demo_seen = {}
w2._demo_gone = {}
for _ in range(6):
    w2._advance()
check("无真实任务时结束不播报", w2.notified == [], w2.notified)

# --- 场景 2：演示期间来了一个新任务 → 结束时应播报
clear_slots()
w3 = FakeSignalLight()
w3._demo_seen = {}
w3._demo_gone = {}
st.write_slot("workbuddy", "demo-new", st.RUNNING, "真实任务", "正在跑", ttl=999)
w3._advance()
w3._finish_demo()
check("演示期间新任务被播报", len(w3.notified) == 1, w3.notified)
if w3.notified:
    body = w3.notified[0][0]
    check("播报内容含任务名", "真实任务" in body, body)
    check("播报标题标明是演示期间", "演示期间" in w3.notified[0][1], w3.notified[0][1])

# --- 场景 3：演示开始前就有的老任务 → 不该被当成"期间新产生"
clear_slots()
w4 = FakeSignalLight()
st.write_slot("workbuddy", "old-task", st.RUNNING, "老任务", "早就在跑", ttl=999)
w4._demo_seen = {("workbuddy", "old-task"): st.RUNNING}
w4._demo_gone = {("workbuddy", "old-task"): "老任务"}
w4._advance()
w4._finish_demo()
check("演示前就存在的任务不播报", w4.notified == [], w4.notified)

# --- 场景 4：状态跃迁（running -> done）应播报
clear_slots()
w5 = FakeSignalLight()
w5._demo_seen = {("workbuddy", "t5"): st.RUNNING}
w5._demo_gone = {("workbuddy", "t5"): "任务五"}
st.write_slot("workbuddy", "t5", st.DONE, "任务五", "已完成", ttl=999)
w5._advance()
w5._finish_demo()
check("状态跃迁被播报", len(w5.notified) == 1, w5.notified)
if w5.notified:
    check("跃迁播报含新状态标签", st.LABELS[st.DONE] in w5.notified[0][0], w5.notified[0][0])

# --- 场景 5：任务结束（槽位消失）应播报
clear_slots()
w6 = FakeSignalLight()
st.write_slot("workbuddy", "t6", st.RUNNING, "任务六", "跑完就没了", ttl=999)
w6._demo_seen = {("workbuddy", "t6"): st.RUNNING}
w6._demo_gone = {("workbuddy", "t6"): "任务六"}
# 结束任务：写 idle 会直接删掉槽位文件
st.write_slot("workbuddy", "t6", st.IDLE)
w6._advance()
w6._finish_demo()
check("任务结束（槽位消失）被播报", len(w6.notified) == 1, w6.notified)
if w6.notified:
    check("结束播报含任务名", "任务六" in w6.notified[0][0], w6.notified[0][0])

# --- 场景 6：播报后 pending 应清空，避免重复播报
clear_slots()
w7 = FakeSignalLight()
w7._demo_seen = {}
w7._demo_gone = {}
st.write_slot("workbuddy", "t7", st.RUNNING, "任务七", "x", ttl=999)
w7._advance()
w7._finish_demo()
n_after_first = len(w7.notified)
w7._demo_pending = []
w7._finish_demo()
check("播报不会重复", len(w7.notified) == n_after_first, w7.notified)

# --- 场景 7：巡检不吞掉真实槽位（读得到）
clear_slots()
w8 = FakeSignalLight()
st.write_slot("workbuddy", "t8", st.WAITING, "任务八", "等确认", ttl=999)
w8._scan_real_slots()
check("巡检能读到真实槽位", ("workbuddy", "t8") in w8._demo_seen, w8._demo_seen)
check("巡检记录了标题", w8._demo_gone.get(("workbuddy", "t8")) == "任务八",
      w8._demo_gone.get(("workbuddy", "t8")))

print()
print("=" * 60)
print("演示代码本身的静态检查")
print("=" * 60)

src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

check("不再用取模回绕推进演示",
      "(self._demo_idx + 1) % DEMO_STEPS" not in src,
      "仍存在 %% DEMO_STEPS 回绕")
check("存在演示结束处理", "_finish_demo" in src)
check("结束时会重置 reader（避免单调性保护丢弃真实状态）",
      "self.reader._last_mtime = 0.0" in src)
check("演示期间仍驱动监听器",
      "self.codex.poll()" in src,
      "演示分支没有继续驱动 codex 监听器")
check("有巡检方法", "_scan_real_slots" in src)
check("有补播报方法", "_announce_pending" in src)

# 演示期间 _poll_state 必须继续跑监听器（不能直接 return）
poll_block = src.split("def _poll_state")[1].split("def ")[0]
check("_poll_state 在演示分支里没有直接 return",
      "self._scan_real_slots()" in poll_block and
      "if self._demo:\n            return" not in poll_block,
      "演示分支仍然直接 return，真实状态会被吞")

print()
print(f"结果: {ok} 通过 / {bad} 失败")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(0 if bad == 0 else 1)
