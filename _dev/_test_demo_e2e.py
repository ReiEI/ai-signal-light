# -*- coding: utf-8 -*-
"""真实类的演示行为端到端验证（离屏 Qt）。

前面 _test_demo.py 用桩件验了纯逻辑，但桩件是"复制一份逻辑"，
复制品和真代码可能不同步 —— 那样的测试保护不了真代码。

这里直接实例化真的 SignalLight，用真的 _tick / _poll_state /
_finish_demo 走一遍，验证三件事：

  1. 点一次演示，一轮走完确实会停（不再无限循环）
  2. 演示期间真的来了任务，结束后会被播报
  3. 演示结束后能恢复真实状态（不会被单调性保护卡在演示画面）

用临时目录 + 离屏 Qt，不碰真实 %LOCALAPPDATA%，也不弹窗。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="aisl-demo-e2e-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

import main as M  # noqa: E402
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


def clear_slots() -> None:
    st.ensure_dir()
    for f in st.SLOT_DIR.glob("*.json"):
        try:
            f.unlink()
        except OSError:
            pass


app = QApplication.instance() or QApplication([])

# 记录所有托盘气泡，避免真弹窗
notifications: list[tuple[str, str]] = []
orig_notify = M.SignalLight._notify


def spy_notify(self, text, title="AI 工作信号灯 · 散热"):
    notifications.append((text, title))
    return orig_notify(self, text, title)


M.SignalLight._notify = spy_notify

print("=" * 60)
print("真实类：演示一轮即结束")
print("=" * 60)

clear_slots()
w = M.SignalLight(demo=False)
w._anim.stop()      # 停掉动画定时器，手动驱动
w._poll.stop()
w._res.stop()
w._cool.stop()

w._start_demo()
check("_start_demo 后处于演示态", w._demo is True)
check("_start_demo 后未结束", w._demo_finished is False)
check("_start_demo 后轮次归零", w._demo_idx == 0, w._demo_idx)

# 手动推进 6 步（模拟 _tick 里时间过去的分支）
steps = 0
while w._demo and steps < 20:
    w._demo_t0 = time.time() - 10.0   # 让"过了 3 秒"的条件成立
    w._tick()
    steps += 1

check(f"演示在 {steps} 步内自行结束", w._demo is False, f"steps={steps}")
check("演示结束标记已置位", w._demo_finished is True)
check("结束标记后不再推进轮次", w._demo_idx <= M.DEMO_STEPS - 1, w._demo_idx)

# 再多调 30 次 _tick，验证不会复活（原来是 %% DEMO_STEPS 无限循环）
for _ in range(30):
    w._demo_t0 = time.time() - 10.0
    w._tick()
check("反复 _tick 不会复活演示", w._demo is False and w._demo_finished is True)

print()
print("=" * 60)
print("真实类：演示期间的真实状态巡检")
print("=" * 60)

clear_slots()
notifications.clear()
w2 = M.SignalLight(demo=False)
w2._anim.stop()
w2._poll.stop()
w2._res.stop()
w2._cool.stop()

w2._start_demo()
check("演示开始时建立了基线", isinstance(w2._demo_seen, dict), type(w2._demo_seen))
baseline_count = len(w2._demo_seen)

# 演示进行中：真实任务来了
st.write_slot("workbuddy", "e2e-task", st.RUNNING, "端到端真实任务", "正在跑", ttl=999)
w2._poll_state()          # 演示期间的轮询：应继续跑监听器 + 巡检
w2._demo_t0 = time.time() - 10.0
w2._tick()                # 推进一步，触发 _scan_real_slots

check("演示期间读到了新槽位", ("workbuddy", "e2e-task") in w2._demo_seen,
      w2._demo_seen)
check("演示期间新任务进了待播报队列", len(w2._demo_pending) >= 1, w2._demo_pending)

# 走完最后一轮
while w2._demo:
    w2._demo_t0 = time.time() - 10.0
    w2._tick()

check("结束后有播报", len(notifications) >= 1, notifications)
if notifications:
    body, title = notifications[0]
    check("播报含任务名", "端到端真实任务" in body, body)
    check("播报标题标明演示期间", "演示期间" in title, title)

print()
print("=" * 60)
print("真实类：演示结束后恢复真实状态")
print("=" * 60)

clear_slots()
w3 = M.SignalLight(demo=False)
w3._anim.stop()
w3._poll.stop()
w3._res.stop()
w3._cool.stop()

w3._start_demo()
# 演示期间产生一个真实任务
st.write_slot("workbuddy", "restore-task", st.RUNNING, "恢复验证", "x", ttl=999)
w3._poll_state()
while w3._demo:
    w3._demo_t0 = time.time() - 10.0
    w3._tick()

# 演示结束后应显示真实状态，而不是停在演示画面
check("演示结束后退出演示态", w3._demo is False)
check("演示结束后显示的是真实任务",
      "恢复验证" in (w3.status.title or "") or
      any("恢复验证" in (s.title or "") for s in w3.rows),
      f"status.title={w3.status.title!r} rows={[s.title for s in w3.rows]}")
check("演示结束后不再显示演示文案",
      "示例项目" not in (w3.status.workspace or ""),
      w3.status.workspace)

print()
print("=" * 60)
print("真实类：state.json 单文件路径的恢复")
print("=" * 60)

# 这一项针对的是一个独立于槽位的缺陷。
# 演示时 reader._last 被写成演示态（ts = 演示时刻，最新），
# 且 _last_mtime 被设成 1e18 阻断真实文件。
# 演示结束后若不重置 reader，StateReader 的单调性保护
# （ts >= self._last.ts）会把 ts 更旧的真实 state.json 判成
# "时间倒退"直接丢弃 —— 于是 signal.exe 这类单文件写入端
# 写的状态，演示结束后永远恢复不了。
clear_slots()
w4 = M.SignalLight(demo=False)
w4._anim.stop()
w4._poll.stop()
w4._res.stop()
w4._cool.stop()

# 真实的单文件状态（比演示开始得早，ts 更旧）
real_ts = time.time() - 3600
st.write_state(st.RUNNING, "单文件真实任务", "来自 signal.exe", ttl=999,
               started=real_ts)

w4._start_demo()
while w4._demo:
    w4._demo_t0 = time.time() - 10.0
    w4._tick()

w4._poll_state()
check("演示结束后恢复了 state.json 的真实状态",
      (w4.status.title or "") == "单文件真实任务",
      f"status.title={w4.status.title!r} state={w4.status.state!r}")
check("恢复后状态是 running 而非演示态",
      w4.status.state == st.RUNNING, w4.status.state)

print()
print("=" * 60)
print("真实类：主动停止演示")
print("=" * 60)

clear_slots()
notifications.clear()
w5 = M.SignalLight(demo=False)
w5._anim.stop()
w5._poll.stop()
w5._res.stop()
w5._cool.stop()

w5._start_demo()


def menu_texts():
    """右键菜单是每次现建的，所以要真的建一次看有哪些项。"""
    return [a.text() for a in w5._menu().actions()]


check("演示中菜单里出现「停止演示」",
      "停止演示" in menu_texts(), menu_texts())

# 演示中途来一个真实任务，然后用户主动停止
st.write_slot("workbuddy", "stop-task", st.RUNNING, "手动停止前的任务", "x", ttl=999)
w5._poll_state()
w5._stop_demo()

check("停止演示后退出演示态", w5._demo is False)
check("停止演示后标记为已结束", w5._demo_finished is True)
check("中途停止同样会补播报期间的变化", len(notifications) >= 1, notifications)
if notifications:
    check("停止播报含任务名",
          "手动停止前的任务" in notifications[0][0], notifications[0][0])
check("停止演示后菜单里不再有「停止演示」",
      "停止演示" not in menu_texts(), menu_texts())

print()
print(f"结果: {ok} 通过 / {bad} 失败")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(0 if bad == 0 else 1)
