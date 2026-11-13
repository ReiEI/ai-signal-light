# -*- coding: utf-8 -*-
"""散热模块测试：解析器 / 会话状态机 / 崩溃恢复 / 执行体端到端。

这几块都不能只测"能跑"：
  * powercfg 的输出是本地化文本，解析错了会把 100% 当成 5% 写回去 ——
    比不工作更糟。所以用中文、英文、残缺三种样本喂它。
  * 会话文件的"活着/死了"判断错一次，用户就会看到一盏假的散热灯。
  * 崩溃恢复是这个功能唯一的兜底：CPU 被钉在 100% 最小状态过夜，
    第二天风扇和电池都要还债。所以要真的造一个"残留备份"出来验证。

用临时目录，不碰真实的 %LOCALAPPDATA%。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="aisl-cool-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

import cooling as cool  # noqa: E402
import ecfan  # noqa: E402
import powerplan as pp  # noqa: E402
import state as st  # noqa: E402

PY = sys.executable
AGENT = str(ROOT / "fan_agent.py")
HOME = str(st.APP_DIR)

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


CN_SAMPLE = """电源方案 GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (平衡)
  子组 GUID: 54533251-82be-4824-96c1-47b60b740d00  (处理器电源管理)
    电源设置 GUID: 893dee8e-2bef-41e0-89c6-b55d0929964c  (最小处理器状态)
      最小可能的设置: 0x00000000
      最大可能的设置: 0x00000064
      可能的设置增量: 0x00000001
      可能的设置单位: %
    当前交流电源设置索引: 0x00000005
    当前直流电源设置索引: 0x00000005
"""

EN_SAMPLE = """Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
  Subgroup GUID: 54533251-82be-4824-96c1-47b60b740d00  (Processor power management)
    Power Setting GUID: 893dee8e-2bef-41e0-89c6-b55d0929964c  (Minimum processor state)
      Minimum Possible Setting: 0x00000000
      Maximum Possible Setting: 0x00000064
      Possible Settings increment: 0x00000001
      Possible Settings units: %
    Current AC Power Setting Index: 0x00000064
    Current DC Power Setting Index: 0x00000032
"""

HIDDEN_SAMPLE = """电源方案 GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (平衡)
  GUID 别名: SCHEME_BALANCED
"""


def _fake_run(sample: str, rc: int = 0):
    def run(args, timeout=8.0):
        return rc, sample
    return run


print("=" * 62)
print("1) powercfg 解析（本地化 / 英文 / 隐藏项）")
_orig_run = pp.run
pp.run = _fake_run(CN_SAMPLE)
check("中文输出解析", pp.query(pp.SET_MIN) == {"ac": 5, "dc": 5}, pp.query(pp.SET_MIN))
pp.run = _fake_run(EN_SAMPLE)
check("英文输出解析", pp.query(pp.SET_MIN) == {"ac": 100, "dc": 50}, pp.query(pp.SET_MIN))
pp.run = _fake_run(HIDDEN_SAMPLE)
check("隐藏设置返回 None 而不是 0", pp.query(pp.SET_COOL) is None, pp.query(pp.SET_COOL))
pp.run = _fake_run("")
check("空输出返回 None", pp.query(pp.SET_MIN) is None)
pp.run = _fake_run("0x00000005")
check("只有一个值时返回 None（宁可没有也不猜）", pp.query(pp.SET_MIN) is None)

print("2) 写入失败必须抛错，不能静默")
pp.run = _fake_run("拒绝访问。", rc=1)
try:
    pp.set_value(pp.SET_MIN, 100, 100)
    check("写入失败抛 PowerError", False)
except pp.PowerError:
    check("写入失败抛 PowerError", True)

print("3) 备份 / 还原：调用序列正确且备份被清除")
calls: list[list[str]] = []


def _record(args, timeout=8.0):
    calls.append(list(args))
    if args[0] == "/query":
        return 0, CN_SAMPLE
    return 0, ""


pp.run = _record
bpath = _TMP / "backup.json"
backup = {
    "created": time.time(), "deadline": time.time() + 600,
    "scheme": "平衡", "min": {"ac": 5, "dc": 5}, "max": {"ac": 100, "dc": 90},
}
pp.save_backup(bpath, backup)
check("备份已落盘", bpath.exists())
calls.clear()
okr, msg = pp.restore(bpath)
check("还原成功", okr, msg)
check("还原后删除备份", not bpath.exists())
check("还原写回了最小状态 5", any(a[:2] == ["/setacvalueindex", "SCHEME_CURRENT"] and "5" in a for a in calls), calls[:3])
check("还原写回了最大状态 90", any("90" in a for a in calls), calls)
okr2, msg2 = pp.restore(bpath)
check("无备份时视为无需还原", okr2 and "无需" in msg2, msg2)

print("4) EC 设备：不存在的设备判为 missing，不误报可用")
probe_missing, _ = ecfan.EcFan(device=r"\\.\NoSuchDeviceForTest").probe()
check("不存在的设备 = missing", probe_missing == ecfan.MISSING, probe_missing)
real_status, real_detail = ecfan.EcFan().probe()
check("真实设备探测返回合法状态", real_status in ecfan.STATUS_LABELS, real_status)
print(f"       （本机 EC：{real_status} · {real_detail}）")

print("5) 会话模型")
s = cool.Session("ec", 1, time.time() - 100, time.time() + 500, time.time(), 7, 0, "")
check("elapsed 从 started 算", 99 < s.elapsed() < 102, s.elapsed())
check("remaining 从 deadline 算", 498 < s.remaining() < 502, s.remaining())
check("has_deadline 为真", s.has_deadline())
check("心跳新鲜 = 活着", s.live())
check("心跳落后 10 秒 = 死了", not cool.Session("ec", 1, 0, 0, time.time() - 10, 0, 0, "").live())
check("模式有中文名", s.label == "风扇全速", s.label)

print("6) 会话文件：坏文件不能让界面崩")
cool.ensure_dir()
cool.SESSION_FILE.write_text("{ 这不是 json", encoding="utf-8")
check("损坏的会话文件读成 None", cool.read_session() is None)
cool.SESSION_FILE.unlink()
check("文件不存在读成 None", cool.read_session() is None)
cool.SESSION_FILE.write_text(json.dumps({
    "mode": "burn", "pid": 1, "started": time.time(), "deadline": 0,
    "beat": time.time(), "cycles": 0, "workers": 8, "note": "8 线程", "error": "",
}), encoding="utf-8")
s2 = cool.read_session()
check("正常会话解析正确", s2 is not None and s2.mode == "burn" and s2.workers == 8)
check("无截止时间时 has_deadline 为假", not s2.has_deadline())

print("7) 启动对账：没有活会话但留着电源备份 → 必须还原")
co = cool.Cooling()
co.cap.refresh()
called = {"n": 0}


def _fake_restore():
    called["n"] += 1
    return True, "已还原 2 项处理器电源设置"


co.restore_power = _fake_restore  # type: ignore[method-assign]
pp.save_backup(cool.BACKUP_FILE, {"created": time.time(), "deadline": time.time() + 60,
                                  "min": {"ac": 5, "dc": 5}, "max": {"ac": 100, "dc": 100}})
cool.SESSION_FILE.unlink(missing_ok=True)
verdict = co.reconcile()
check("残留备份触发还原", called["n"] == 1, verdict)
check("返回值为 restored", verdict.startswith("restored"), verdict)

called["n"] = 0
cool.SESSION_FILE.write_text(json.dumps({
    "mode": "ec", "pid": 1, "started": time.time(), "deadline": 0,
    "beat": time.time(), "cycles": 3, "workers": 0, "note": "", "error": "",
}), encoding="utf-8")
verdict2 = co.reconcile()
check("活会话被接管而不是被清掉", verdict2.startswith("adopt"), verdict2)
check("接管时不误触还原", called["n"] == 0, called)
cool.SESSION_FILE.unlink(missing_ok=True)
cool.BACKUP_FILE.unlink(missing_ok=True)
check("干净现场返回 clean", co.reconcile() == "clean")

print("8) 启动守卫：不可用模式 / 重复启动")
okc, msgc = cool.Cooling().start("不存在的模式")
check("未知模式被拒", not okc, msgc)

print("8a) 打包后执行体路径解析（frozen 下必须找同目录的 fan_agent.exe）")
_c = cool.Cooling()
_old_exe = sys.executable
_old_frozen = getattr(sys, "frozen", False)
try:
    sys.frozen = True
    sys.executable = str(ROOT / "dist" / "SignalLight.exe")
    if (ROOT / "dist" / "fan_agent.exe").exists():
        tgt = _c._agent_target()
        check("frozen → 同目录 fan_agent.exe", tgt is not None and tgt[0].endswith("fan_agent.exe") and tgt[1] == [], tgt)
        cmd = tgt[1] + _c._agent_args("ec", 30, 0)
        check("参数含 mode/home/watch-ui", "--mode" in cmd and "--home" in cmd and "--watch-ui" in cmd, cmd)
        check("参数里 minutes 是整数", "--minutes" in cmd and cmd[cmd.index("--minutes") + 1] == "30", cmd)
    else:
        print("  SKIP  dist/fan_agent.exe 不存在（未打包）")
    _empty = _TMP / "emptydist"
    _empty.mkdir(exist_ok=True)
    sys.executable = str(_empty / "SignalLight.exe")
    check("frozen 但缺执行体 → 返回 None", _c._agent_target() is None)
finally:
    sys.executable = _old_exe
    if _old_frozen:
        sys.frozen = _old_frozen
    else:
        try:
            del sys.frozen
        except AttributeError:
            pass

print("8b) 电源锁定在非管理员下必须直接拒绝，不能盲改")
if not pp.is_admin():
    h0 = _TMP / "agent_spin"
    h0.mkdir(parents=True, exist_ok=True)
    r0 = subprocess.run(
        [PY, AGENT, "--mode", "spin", "--minutes", "1", "--home", str(h0)],
        capture_output=True, timeout=20,
    )
    check("非管理员跑 spin 退出码 4", r0.returncode == 4, r0.returncode)
    st0 = json.loads((h0 / "cooling" / "last.json").read_text(encoding="utf-8"))
    check("拒绝原因写明需要管理员", "管理员" in st0.get("message", ""), st0)
    check("拒绝时没有留下电源备份", not (h0 / "cooling" / "power_backup.json").exists())
else:
    print("  SKIP  当前会话已是管理员，跳过非提权用例")

print("9) 执行体端到端：烧 2 秒，会话出现再消失，退出码 0")
h1 = _TMP / "agent1"
h1.mkdir(parents=True, exist_ok=True)
p = subprocess.Popen(
    [PY, AGENT, "--mode", "burn", "--minutes", "0.05", "--workers", "2", "--home", str(h1)],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
time.sleep(1.4)
sess = json.loads((h1 / "cooling" / "session.json").read_text(encoding="utf-8"))
check("运行中写出了会话文件", sess.get("mode") == "burn", sess)
check("会话带心跳", sess.get("beat", 0) > time.time() - 5)
check("会话带截止时间", sess.get("deadline", 0) > time.time())
rc = p.wait(timeout=15)
check("到期后退出码 0", rc == 0, rc)
check("退出后清理会话文件", not (h1 / "cooling" / "session.json").exists())
last = json.loads((h1 / "cooling" / "last.json").read_text(encoding="utf-8"))
check("退出原因可追溯", "时长" in last.get("message", ""), last)

print("10) 执行体：stop.flag 能在 1 秒内叫停")
h2 = _TMP / "agent2"
h2.mkdir(parents=True, exist_ok=True)
p2 = subprocess.Popen(
    [PY, AGENT, "--mode", "burn", "--minutes", "10", "--workers", "2", "--home", str(h2)],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
time.sleep(1.6)
(h2 / "cooling" / "stop.flag").write_text(str(time.time()), encoding="utf-8")
t0 = time.time()
rc2 = p2.wait(timeout=10)
cost = time.time() - t0
check("收到停止后退出码 0", rc2 == 0, rc2)
check("停止延迟 < 2 秒", cost < 2.0, f"{cost:.2f}s")
check("停止后不留 stop.flag", not (h2 / "cooling" / "stop.flag").exists())

print("11) 执行体：同一时刻只允许一个")
h3 = _TMP / "agent3"
h3.mkdir(parents=True, exist_ok=True)
p3 = subprocess.Popen(
    [PY, AGENT, "--mode", "burn", "--minutes", "10", "--workers", "2", "--home", str(h3)],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
time.sleep(1.5)
p4 = subprocess.run(
    [PY, AGENT, "--mode", "burn", "--minutes", "1", "--workers", "1", "--home", str(h3)],
    capture_output=True, timeout=15,
)
check("第二个执行体退出码 6", p4.returncode == 6, p4.returncode)
(h3 / "cooling" / "stop.flag").write_text(str(time.time()), encoding="utf-8")
p3.wait(timeout=10)

print("12) UI 心跳超时判定（界面关掉后 agent 必须自己收手）")
import fan_agent as fa  # noqa: E402

ppaths = fa.Paths(_TMP / "agent4")
ppaths.ensure()
ppaths.ui.write_text(f"{time.time():.3f}", encoding="utf-8")
check("心跳新鲜 → 继续", fa._ui_alive(ppaths, time.time()) is True)
ppaths.ui.write_text(f"{time.time() - 200:.3f}", encoding="utf-8")
check("心跳落后 200s → 收手", fa._ui_alive(ppaths, time.time()) is False)
ppaths.ui.unlink()
check("刚启动无心跳 → 宽限期内放行", fa._ui_alive(ppaths, time.time()) is True)
check("启动超过宽限无心跳 → 收手", fa._ui_alive(ppaths, time.time() - 60) is False)

print("13) 设置项：散热默认值与越界修正")
st.SETTINGS_FILE.unlink(missing_ok=True)
_s = st.load_settings()
check("默认 30 分钟", _s["cool_minutes"] == 30, _s["cool_minutes"])
check("默认自动线程", _s["burn_workers"] == 0, _s["burn_workers"])
st.SETTINGS_FILE.write_text('{"cool_minutes": 9999, "cool_cycle": 0.1}', encoding="utf-8")
_s2 = st.load_settings()
check("越界时长被修正回 30", _s2["cool_minutes"] == 30, _s2["cool_minutes"])
check("过小周期被修正回 8.9", abs(_s2["cool_cycle"] - 8.9) < 0.01, _s2["cool_cycle"])

print()
print(f"结果: {ok} 通过 / {bad} 失败")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(0 if bad == 0 else 1)
