# -*- coding: utf-8 -*-
"""自检脚本：状态写入 / 读取 / TTL 降级 / run 包裹模式。"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent

# 隔离数据目录：否则测试残留的 waiting/done 会显示成真实信号
_TEST_HOME = Path(tempfile.gettempdir()) / "aisignal-selftest"
shutil.rmtree(_TEST_HOME, ignore_errors=True)
os.environ["AISIGNAL_HOME"] = str(_TEST_HOME)

sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

import state as st  # noqa: E402

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
SIG = str(ROOT / "signal_cli.py")

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {extra}")


print("=" * 60)
print("1) 直接写状态 + 读回")
st.write_state("running", "生成报告", "读取数据表", 30)
r = st.StateReader()
s = r.read()
check("state=running", s.state == "running", s.state)
check("title 保留", s.title == "生成报告", s.title)
check("detail 保留", s.detail == "读取数据表", s.detail)

print("2) TTL 过期自动降级为空闲（假信号自愈）")
st.write_state("running", "长任务", "", -1)  # ttl=-1，必然过期
s2 = r.read()
check("过期后回落 idle", s2.state == "idle", s2.state)
check("标记 stale", s2.stale is True, str(s2.stale))

print("3) 终态停留：done 在 TTL 内保持，过后回落")
st.write_state("done", "生成报告", "完成", 8)
s3 = r.read()
check("done 保持", s3.state == "done", s3.state)
st.write_state("done", "生成报告", "完成", -1)
check("done 过期回落", r.read().state == "idle")

print("4) 非法状态被拒绝")
try:
    st.write_state("bogus")
    check("非法状态抛错", False)
except ValueError:
    check("非法状态抛错", True)

print("5) CLI: signal_cli.py running / clear")
subprocess.run([PY, SIG, "running", "CLI 测试", "-d", "参数", "-t", "20"], check=True,
               capture_output=True)
r2 = st.StateReader()
check("CLI 写入 running", r2.read().state == "running")
subprocess.run([PY, SIG, "clear"], check=True, capture_output=True)
check("CLI clear → idle", st.StateReader().read().state == "idle")

print("6) CLI: run 包裹成功命令")
p = subprocess.run([PY, SIG, "run", "包裹测试", "--", PY, "-c", "print('hi')"],
                   capture_output=True, text=True)
check("退出码透传 0", p.returncode == 0, str(p.returncode))
s6 = st.StateReader().read()
check("落 done", s6.state == "done", s6.state)
check("detail 带用时", "用时" in s6.detail, s6.detail)

print("7) CLI: run 包裹失败命令")
p = subprocess.run([PY, SIG, "run", "失败测试", "--", PY, "-c", "import sys; sys.exit(3)"],
                   capture_output=True, text=True)
check("退出码透传 3", p.returncode == 3, str(p.returncode))
s7 = st.StateReader().read()
check("落 failed", s7.state == "failed", s7.state)
check("detail 含退出码", "3" in s7.detail, s7.detail)

print("8) run 模式长时间任务心跳（3.2s，ttl=12 不应中途掉线）")
p = subprocess.run([PY, SIG, "run", "心跳测试", "--", PY, "-c", "import time; time.sleep(3.2)"],
                   capture_output=True, text=True)
check("长任务最终 done", st.StateReader().read().state == "done")
check("退出码 0", p.returncode == 0)

print("9) 外观设置：默认值与旧配置迁移")
st.SETTINGS_FILE.write_text('{"collapsed": true}', encoding="utf-8")
_s = st.load_settings()
check("旧配置 collapsed=true 迁移成横条", _s["style"] == "bar", _s["style"])
st.SETTINGS_FILE.unlink()
_s = st.load_settings()
check("默认样式为 standard", _s["style"] == "standard", _s["style"])
check("默认缩放为 1.0", _s["scale"] == 1.0, _s["scale"])

print("10) 计数器")
print(f"\n结果: {ok} 通过 / {fail} 失败")
st.write_state("idle")
sys.exit(0 if fail == 0 else 1)
