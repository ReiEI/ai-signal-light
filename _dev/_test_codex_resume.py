# -*- coding: utf-8 -*-
import datetime as dt, json, os, sys, tempfile, time
from pathlib import Path
tmp=tempfile.TemporaryDirectory(prefix="aisignal-codex-resume-")
os.environ["AISIGNAL_HOME"]=str(Path(tmp.name)/"status")
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"app"))
import codex as cx
import state as st
now=time.time()
cx.SESSIONS=Path(tmp.name)/"sessions"
old=dt.datetime.now()-dt.timedelta(hours=2)
day=cx.SESSIONS/f"{old.year:04d}"/f"{old.month:02d}"/f"{old.day:02d}"
day.mkdir(parents=True)
p=day/f"rollout-{old:%Y-%m-%dT%H-%M-%S}-resume.jsonl"
def event(kind):
    return json.dumps(dict(timestamp=dt.datetime.now(dt.timezone.utc).isoformat(),type="event_msg",payload=dict(type=kind)))+"\n"
p.write_text(event("task_started"),encoding="utf-8")
os.utime(p,(now-3600,now-3600))
w=cx.CodexWatcher()
w.poll()
assert st.read_slot("codex","app").state=="running", "resumed old chat was missed"
assert w._pick(now+cx.IDLE_WINDOW+1)==p, "long task lost its tracked file"
with p.open("a",encoding="utf-8") as f:f.write(event("task_complete"))
os.utime(p,(now-3600,now-3600))
w.poll()
assert st.read_slot("codex","app").state=="done", "completion was missed"
print("PASS 3 Codex resume/long-task/completion regressions")
tmp.cleanup()

assert cx._title_from({'content':[{'type':'text','text':'[{"questionItemId":"test","answer":"已切换"}]'}]}) == ''
assert cx._title_from({'content':[{'type':'text','text':'已切换'}]}) == ''
print('PASS 2 Codex question-card and acknowledgement regressions')
