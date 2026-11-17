# -*- coding: utf-8 -*-
"""Verify installed adapter live; print permitted fields only, no snapshot files."""
import os, sys, tempfile, time, queue, json
from pathlib import Path
tmp = tempfile.TemporaryDirectory(prefix="aisignal-live-check-")
os.environ["AISIGNAL_HOME"] = tmp.name
sys.path.insert(0,str(Path(__file__).resolve().parent.parent / "app"))
import desktop
w=desktop.DesktopWatcher()
end=time.time()+14
try:
    while time.time()<end:
        try:
            s=w._queue.get(timeout=1)
        except queue.Empty:
            continue
        print(json.dumps(dict(ok=s.get("ok"), windows=s.get("windows"),
            current=[dict(source=r.get("source"),title=r.get("title"),state=r.get("state"),
                          prompt=(r.get("prompt") or "")[:250]) for r in s.get("current",[])],
            tasks=[dict(source=r.get("source"),title=r.get("title"),state=r.get("state")) for r in s.get("tasks",[])],
            scopes=[dict(source=r.get("source"),titles_count=len(r.get("titles",[]))) for r in s.get("scopes",[])]),
            ensure_ascii=False),flush=True)
        w._apply(s)
        if s.get("ok"):
            break
finally:
    w.close()
    tmp.cleanup()
