# -*- coding: utf-8 -*-
"""WorkBuddy hook 的进程级测试。

为什么必须真跑子进程：hook 由宿主以独立进程调用，
"函数在进程内跑通"不等于"集成到宿主后不出事"。
这里要验的是退出码、耗时、畸形输入下的健壮性——这些只有跨进程才测得到。
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
PY = ROOT / ".venv" / "Scripts" / "python.exe"
HOOK = ROOT / "hook_wb.py"

# 隔离数据目录。hook 是子进程，靠继承环境变量吃到同一个目录。
# 不隔离的话，测试完残留的 waiting 会被挂件当成真实信号一直显示。
_TEST_HOME = Path(tempfile.gettempdir()) / "aisignal-hooktest"
shutil.rmtree(_TEST_HOME, ignore_errors=True)
os.environ["AISIGNAL_HOME"] = str(_TEST_HOME)

sys.path.insert(0, str(ROOT / "app"))

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


def run(payload, raw: bytes | None = None) -> tuple[int, float, bytes]:
    data = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    t = time.perf_counter()
    p = subprocess.run([str(PY), "-S", str(HOOK)], input=data, capture_output=True, timeout=20)
    return p.returncode, (time.perf_counter() - t) * 1000, p.stderr


def clean() -> None:
    if st.SLOT_DIR.exists():
        shutil.rmtree(st.SLOT_DIR, ignore_errors=True)


clean()

print("1) UserPromptSubmit -> running")
rc, ms, err = run({"hook_event_name": "UserPromptSubmit", "session_id": "aabbccdd-1", "prompt": "帮我把这批图片按日期归档"})
s = st.read_slot("workbuddy", "aabbccdd")
check("退出码 0", rc == 0, rc)
# 阈值对齐真实风险边界：hook 超时是 5s，超过就等于拖住宿主。
# 不卡 400ms —— 那只是性能目标，磁盘忙（比如同时在跑 PyInstaller）会误报。
check("耗时远低于 5s 超时", ms < 1500, f"{ms:.0f}ms")
check("槽位为 running", s is not None and s.state == st.RUNNING, s)
check("标题取 prompt", s and s.title == "帮我把这批图片按日期归档", s.title if s else None)
check("stderr 无输出", not err.strip(), err[:200])

print("2) PostToolUse 只续期不覆盖")
before = st.read_slot("workbuddy", "aabbccdd")
time.sleep(0.05)
rc, ms, _ = run({"hook_event_name": "PostToolUse", "session_id": "aabbccdd-1", "tool_name": "Bash"})
s = st.read_slot("workbuddy", "aabbccdd")
check("退出码 0", rc == 0, rc)
check("beat 已推进", s.beat > before.beat, (before.beat, s.beat))
check("ts 未被心跳改写（耗时不会归零）", abs(s.ts - before.ts) < 1e-6, (before.ts, s.ts))
check("标题未被清空", s.title == "帮我把这批图片按日期归档", s.title)
check("状态仍为 running", s.state == st.RUNNING, s.state)

print("3) PostToolUse 不会凭空创建槽位")
rc, _, _ = run({"hook_event_name": "PostToolUse", "session_id": "nosuch01", "tool_name": "Bash"})
check("无槽位时不写入", st.read_slot("workbuddy", "nosuch01") is None)

print("4) Stop -> done")
rc, _, _ = run({"hook_event_name": "Stop", "session_id": "aabbccdd-1", "stop_hook_active": False})
s = st.read_slot("workbuddy", "aabbccdd")
check("退出码 0", rc == 0, rc)
check("状态为 done", s and s.state == st.DONE, s.state if s else None)
check("标题保留", s and s.title == "帮我把这批图片按日期归档", s.title if s else None)

print("5) Notification 关键词过滤")
rc, _, _ = run({"hook_event_name": "Notification", "session_id": "aabbccdd-1", "message": "任务已完成，点击查看"})
s = st.read_slot("workbuddy", "aabbccdd")
check("普通通知不误判为等待", s.state == st.DONE, s.state)
rc, _, _ = run({"hook_event_name": "Notification", "session_id": "aabbccdd-1", "message": "需要你确认是否覆盖同名文件"})
s = st.read_slot("workbuddy", "aabbccdd")
check("含确认字样转 waiting", s.state == st.WAITING, s.state)
check("等待详情带原文", "确认" in s.detail, s.detail)

print("6) SessionEnd 清理")
rc, _, _ = run({"hook_event_name": "SessionEnd", "session_id": "aabbccdd-1"})
check("槽位已清除", st.read_slot("workbuddy", "aabbccdd") is None)

print("7) 畸形输入不能崩")
for label, payload, raw in (
    ("空 stdin", None, b""),
    ("非 JSON", None, b"not json at all"),
    ("JSON 数组", None, b"[1,2,3]"),
    ("缺事件名", {"session_id": "x"}, None),
    ("未知事件", {"hook_event_name": "WhateverHappened", "session_id": "x"}, None),
    ("超长 prompt", {"hook_event_name": "UserPromptSubmit", "session_id": "long01", "prompt": "啊" * 5000}, None),
    ("prompt 为 null", {"hook_event_name": "UserPromptSubmit", "session_id": "nul001", "prompt": None}, None),
):
    rc, ms, err = run(payload, raw)
    check(f"{label} -> 退出码 0", rc == 0, rc)

s = st.read_slot("workbuddy", "long01")
check("超长 prompt 被截断", s and len(s.title) <= 40, len(s.title) if s else None)
s = st.read_slot("workbuddy", "nul001")
check("null prompt 不崩且有占位", s and s.title == "WorkBuddy 指令", s.title if s else None)

print("8) 并发写入不产生半截文件")
clean()
procs = []
for i in range(6):
    p = subprocess.Popen(
        [str(PY), "-S", str(HOOK)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    procs.append(p)
for i, p in enumerate(procs):
    p.communicate(json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "session_id": f"concur{i:02d}",
        "prompt": f"并发任务 {i}",
    }, ensure_ascii=False).encode("utf-8"))
slots = st.read_slots()
check("6 个并发源全部落盘", len(slots) == 6, len(slots))
leftover = [f.name for f in st.SLOT_DIR.glob("*.tmp")] if st.SLOT_DIR.exists() else []
check("无残留临时文件", not leftover, leftover)

print("10) 引用消息 / 附件前缀不污染标题")
QUOTED = (
    '<message_quote type="text" source_message_id="01a1d53f-7ee3">\n'
    "<original_text><![CDATA[完全退出 WorkBuddy\n双击 接入WorkBuddy.cmd]]></original_text>\n"
    "</message_quote>\n\n这三步已执行，并已打开 Codex。"
)
clean()
run({"hook_event_name": "UserPromptSubmit", "session_id": "quote001", "prompt": QUOTED})
s = st.read_slot("workbuddy", "quote001")
check("标题剥掉了标签", s is not None and "<message_quote" not in s.title, s.title if s else None)
check("标题取到真实内容", s is not None and s.title.startswith("这三步已执行"), s.title if s else None)

clean()
run({
    "hook_event_name": "UserPromptSubmit", "session_id": "badge001",
    "prompt": '@wedrive#abc:"方案.docx" 请把这份方案压缩成 3 页',
})
s = st.read_slot("workbuddy", "badge001")
check("剥掉 @ 附件徽标", s is not None and s.title.startswith("请把这份方案"), s.title if s else None)

clean()
run({"hook_event_name": "UserPromptSubmit", "session_id": "pure001", "prompt": "纯文本指令，没有任何前缀"})
s = st.read_slot("workbuddy", "pure001")
check("纯文本不受影响", s is not None and s.title == "纯文本指令，没有任何前缀", s.title if s else None)

clean()
run({"hook_event_name": "UserPromptSubmit", "session_id": "onlyq001", "prompt": "<message_quote>只有引用</message_quote>"})
s = st.read_slot("workbuddy", "onlyq001")
check("只有引用时退回原文不空", s is not None and s.title != "", repr(s.title) if s else None)

print("11) 系统注入 / 后台任务通知不覆盖标题")
clean()
run({"hook_event_name": "UserPromptSubmit", "session_id": "synth01", "prompt": "重构取数逻辑"})
s1 = st.read_slot("workbuddy", "synth01")
time.sleep(0.05)
run({
    "hook_event_name": "UserPromptSubmit", "session_id": "synth01",
    "prompt": 'Use the TaskOutput tool with task_id="75n0L9" to retrieve unread output if you need to act on it.',
})
s2 = st.read_slot("workbuddy", "synth01")
check("原标题保留", s2 is not None and s2.title == "重构取数逻辑", s2.title if s2 else None)
check("开始时间未被重置", s2 is not None and s2.ts == s1.ts, (s1.ts, s2.ts if s2 else None))
check("心跳已推进", s2 is not None and s2.beat > s1.beat, (s1.beat, s2.beat if s2 else None))
check("状态仍为 running", s2 is not None and s2.state == st.RUNNING, s2.state if s2 else None)

clean()
run({
    "hook_event_name": "UserPromptSubmit", "session_id": "synth02",
    "prompt": "[SYSTEM NOTIFICATION - NOT USER INPUT] This is an automated background-task event.",
})
s3 = st.read_slot("workbuddy", "synth02")
check("无前置任务时给通用标题", s3 is not None and s3.title == "WorkBuddy 后台任务", s3.title if s3 else None)

clean()
run({"hook_event_name": "UserPromptSubmit", "session_id": "synth03", "prompt": "普通提问：帮我看下这个报错"})
s4 = st.read_slot("workbuddy", "synth03")
check("普通提问不被误判", s4 is not None and s4.title.startswith("普通提问"), s4.title if s4 else None)

print("12) 工作空间与会话标题")
TR = Path(tempfile.gettempdir()) / "aisignal-transcript"
shutil.rmtree(TR, ignore_errors=True)
TR.mkdir(parents=True, exist_ok=True)


def tr_write(name: str, lines: list) -> str:
    p = TR / name
    with open(p, "w", encoding="utf-8") as f:
        for d in lines:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return str(p)


def ai_title(t: str) -> dict:
    return {"type": "ai-title", "aiTitle": t, "sessionId": "s"}


def meta_title(t: str) -> dict:
    return {"type": "session-meta", "meta": {"codebuddy.ai/sessionTitle": [ai_title(t)]}}


clean()
tp1 = tr_write("a.jsonl", [{"type": "message", "text": "x"}, ai_title("完善信号灯多任务显示")])
run({
    "hook_event_name": "UserPromptSubmit", "session_id": "ws01", "prompt": "把任务名称改一下",
    "cwd": "D:/tmp/demo-ws", "transcript_path": tp1,
})
s = st.read_slot("workbuddy", "ws01")
check("无映射时工作空间为空（独立任务）", s is not None and s.workspace == "", s.workspace if s else None)
check("标题取会话标题", s is not None and s.title == "完善信号灯多任务显示", s.title if s else None)
check("第二行保留本轮指令", s is not None and s.detail == "把任务名称改一下", s.detail if s else None)

clean()
tp2 = tr_write("b.jsonl", [{"type": "message", "text": "首轮还没有标题"}])
run({
    "hook_event_name": "UserPromptSubmit", "session_id": "ws02", "prompt": "先跑起来",
    "cwd": "D:/tmp/demo-ws", "transcript_path": tp2,
})
s = st.read_slot("workbuddy", "ws02")
check("独立任务首轮无标题时 title 用指令", s is not None and s.title == "先跑起来", s.title if s else None)
with open(tp2, "a", encoding="utf-8") as f:
    f.write(json.dumps(ai_title("后来才生成的标题"), ensure_ascii=False) + "\n")
run({
    "hook_event_name": "PostToolUse", "session_id": "ws02",
    "cwd": "D:/tmp/demo-ws", "transcript_path": tp2,
})
s = st.read_slot("workbuddy", "ws02")
check("心跳补上标题", s is not None and s.title == "后来才生成的标题", s.title if s else None)
check("指令未被心跳覆盖", s is not None and s.detail == "先跑起来", s.detail if s else None)

clean()
tp3 = tr_write("c.jsonl", [
    {"type": "message", "text": "填充" * 30000},
    {"type": "message", "text": "中间"},
    meta_title("尾部才有的标题"),
])
run({
    "hook_event_name": "UserPromptSubmit", "session_id": "ws03", "prompt": "查一下",
    "cwd": "D:/tmp/demo-ws", "transcript_path": tp3,
})
s = st.read_slot("workbuddy", "ws03")
check("头部读不到时回落尾部", s is not None and s.title == "尾部才有的标题", s.title if s else None)

clean()
run({
    "hook_event_name": "UserPromptSubmit", "session_id": "ws04", "prompt": "只有指令",
    "transcript_path": "Z:/no/such/file.jsonl",
})
s = st.read_slot("workbuddy", "ws04")
check("transcript 不存在时退回指令", s is not None and s.title == "只有指令", s.title if s else None)
check("无 cwd 时工作空间为空", s is not None and s.workspace == "", s.workspace if s else None)

clean()
run({
    "hook_event_name": "UserPromptSubmit", "session_id": "ws05",
    "prompt": "带工作空间但没标题", "cwd": "D:/tmp/demo-ws",
})
s = st.read_slot("workbuddy", "ws05")
check("独立任务 title 用指令、不显示工作空间名",
      s is not None and s.title == "带工作空间但没标题" and s.workspace == "",
      (s.title, s.workspace) if s else None)

# 映射命中时工作空间名正常显示（反向：确认没把正常工作空间也砍掉）
# 子进程读 AISIGNAL_HOME/workspace-display-names.json，直接写文件注入。
ws_map_file = _TEST_HOME / "workspace-display-names.json"
ws_map_file.write_text(
    json.dumps({"workspaces": {"d:/tmp/demo-ws": {"displayName": "信号灯项目"}}}),
    encoding="utf-8",
)
clean()
run({
    "hook_event_name": "UserPromptSubmit", "session_id": "ws06",
    "prompt": "改一下标题", "cwd": "D:/tmp/demo-ws",
})
s = st.read_slot("workbuddy", "ws06")
check("映射命中时工作空间取 displayName",
      s is not None and s.workspace == "信号灯项目", s.workspace if s else None)
check("有工作空间且无标题时 title 留空避免两行重复",
      s is not None and s.title == "", s.title if s else None)

shutil.rmtree(TR, ignore_errors=True)

clean()
print(f"\n结果: {ok} 通过 / {bad} 失败")
sys.exit(1 if bad else 0)
