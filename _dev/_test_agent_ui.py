# -*- coding: utf-8 -*-
"""Synthetic visual smoke check for source badges in all three layouts."""
import os, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace
tmp=tempfile.TemporaryDirectory(prefix="aisignal-agent-ui-")
os.environ["AISIGNAL_HOME"]=tmp.name
os.environ["QT_QPA_PLATFORM"]="offscreen"
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"app"))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QFontDatabase
import main
import state as st
# This fixture never reads real desktop chats.
main.desktop_mod.DesktopWatcher=lambda: SimpleNamespace(poll=lambda:False,close=lambda:None)
app=QApplication([])
for font in ('msyh.ttc','msyhbd.ttc','segoeui.ttf','consola.ttf'):
    path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/font
    if path.is_file():
        QFontDatabase.addApplicationFont(str(path))
w=main.SignalLight(demo=True)
w._anim.stop(); w._poll.stop(); w._res.stop(); w._cool.stop()
w.set_theme("dark")
w.settings["show_metrics"]=False
w.rows=[
    st.Slot("workbuddy","fixture-wb","running","示例任务：核对资料","本轮问题示例",time.time()-30,120,time.time(),"示例空间"),
    st.Slot("codex","fixture-cx","running","示例任务：修复测试","本轮问题示例",time.time()-20,120,time.time(),"示例空间"),
    st.Slot("chatgpt","fixture-gpt","running","示例任务：整理摘要","本轮问题示例",time.time()-10,120,time.time(),"示例空间"),
]
w.status=st.compose(w.rows,st.Status())
assert st.task_name(w.rows[0]).startswith("WorkBuddy · ")
assert st.task_name(w.rows[1]).startswith("Codex · ")
assert st.task_name(w.rows[2]).startswith("ChatGPT · ")
assert st.task_name(st.Slot("manual","x","running","手动任务")) == "手动任务"
assert st.agent_label("custom-agent") == "custom-agent"
captures=[]
for style in main.STYLE_ORDER:
    w.style=style; w._apply_size(); w._on_state_change(); w.show(); app.processEvents()
    image=w.grab().toImage()
    assert not image.isNull(), style
    captures.append((style,image))
    print("PASS source badges rendered:",style)
width=sum(im.width() for _,im in captures)+40
height=max(im.height() for _,im in captures)+20
out=QImage(width,height,QImage.Format_ARGB32); out.fill(QColor("#1b1d22"))
p=QPainter(out); x=10
for style,im in captures:
    p.drawImage(x,10,im); x+=im.width()+10
p.end()
dest=Path(__file__).resolve().parent/"agent-source-layouts.png"
assert out.save(str(dest))
print("PASS 5 source identity cases; 3 layout renders")
print("IMAGE",dest)
w.thermal.stop(); w.metrics.close(); w.tray.hide(); w.close()
tmp.cleanup()
