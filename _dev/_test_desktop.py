# -*- coding: utf-8 -*-
"""Synthetic adapter regressions; never read private chat data."""
import os, sys, tempfile, time
from pathlib import Path
tmp = tempfile.TemporaryDirectory(prefix="aisignal-desktop-test-")
os.environ["AISIGNAL_HOME"] = tmp.name
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import state as st
import desktop
w = desktop.DesktopWatcher(start=False)
checks = 0
def check(ok, label):
    global checks
    assert ok, label
    checks += 1
    print("PASS", label)
def snap(current=None, tasks=None, scopes=None, ok=True):
    return dict(ok=ok, current=current or [], tasks=tasks or [], scopes=scopes or [])
def row(source="chatgpt", title="测试对话", prompt="本轮测试问题", state="running", window="1"):
    return dict(source=source,title=title,prompt=prompt,state=state,window=window)
w._apply(snap(current=[row()]), now=time.time())
slots=st.read_slots()
check(len(slots)==1 and slots[0].source=="chatgpt", "ChatGPT independent source")
check(slots[0].title=="测试对话" and slots[0].detail=="本轮测试问题", "title and current user question")
t0=slots[0].ts
w._apply(snap(current=[row()]))
check(st.read_slots()[0].ts==t0, "heartbeat preserves start time")
w._apply(snap(current=[row(prompt="第二轮问题")]))
check(st.read_slots()[0].ts>=t0 and st.read_slots()[0].detail=="第二轮问题", "new turn replaces question")
w._apply(snap(current=[row(state="idle")]))
check(st.read_slots()[0].state=="running", "single missing indicator does not complete")
w._apply(snap(current=[row(state="idle")]))
check(st.read_slots()[0].state=="done", "two confirmed idle observations complete")
w._apply(snap(current=[row(title="Codex测试",source="codex")]))
check(any(s.source=="codex" and s.sid=="app" for s in st.read_slots()), "Codex reuses legacy slot")
w._apply(snap(current=[row(title="ChatGPT并行")],tasks=[row(title="后台任务",state="waiting")]))
check(len([s for s in st.read_slots() if s.state!="done"])==3, "ChatGPT and Codex coexist")
bg=next(s for s in st.read_slots() if s.title=="后台任务")
check(bg.state=="waiting" and "待读取" in bg.detail, "background prompt never guessed")
w._apply(snap(ok=False))
check(any(s.title=="后台任务" and s.state=="waiting" for s in st.read_slots()), "probe failure does not falsely complete")
w._apply(snap(),now=time.time()+40)
check(not w._active, "unobservable tasks expire without false done")
w._apply(snap(current=[row(prompt="")]))
check("待读取" in next(s for s in st.read_slots() if s.title=="测试对话").detail, "unreadable new prompt clears previous question")
w._apply(snap(tasks=[row(source="codex",title="并行一"),row(source="codex",title="并行二")]))
codex=[s for s in st.read_slots() if s.source=="codex" and s.state=="running"]
check(len({s.sid for s in codex})==2, "parallel Codex tasks keep separate slots")
print("RESULT:", checks, "passed")
w.close()
tmp.cleanup()
