# -*- coding: utf-8 -*-
"""温度采集 + 外观设置的单元测试。

温度这块不能只测"能跑"：provider 会返回 None、会抛异常、会返回 300 这种
离谱值，界面上必须显示"—"而不是把 300° 画出来。
外观设置则是升级路径：老配置只有 collapsed，新配置有 style/scale，
迁移写错会让用户升级后挂件突然变大或变窄。

用临时目录，不碰真实的 %LOCALAPPDATA%\\AIStatusLight。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

_TMP = Path(tempfile.mkdtemp(prefix="aisl-thermal-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")

import metrics as mx  # noqa: E402
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


class Fake:
    """可控的温度源：想返回什么就返回什么，包括非法值和异常。"""

    def __init__(self, value, name="fake", label="CPU", interval=0.05, boom=False):
        self.value = value
        self.name = name
        self.label = label
        self.interval = interval
        self.boom = boom
        self.calls = 0
        self.closed = False

    def read(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("传感器炸了")
        return self.value

    def close(self):
        self.closed = True


print("1) 没有任何可用源时老实显示不可用")
t = mx.Thermal(providers=[], autostart=False)
check("probe 返回 None", t.probe() is None)
check("temp 为空", t.temp is None, t.temp)
check("available 为假", t.available is False)
check("probed 已置位（区别于还没测）", t.probed is True)

print("2) 第一条链路有值就用它")
a = Fake(52.5, name="adl", label="GPU")
t = mx.Thermal(providers=[a, Fake(30.0)], autostart=False)
t.probe()
check("取到温度", t.temp == 52.5, t.temp)
check("标签跟随来源", t.label == "GPU", t.label)
check("来源名正确", t.source == "adl", t.source)
check("只探测到第一条就停", a.calls == 1, a.calls)

print("3) 首条不可用时降级到下一条")
b = Fake(None, name="nvidia", label="GPU")
c = Fake(61.0, name="lhm", label="CPU")
t = mx.Thermal(providers=[b, c], autostart=False)
t.probe()
check("落到底二条", t.temp == 61.0 and t.source == "lhm", (t.temp, t.source))
check("首条确实被问过", b.calls == 1, b.calls)

print("4) 离谱读数被拦掉（300° 不许上屏）")
d = Fake(300.0, name="bad")
e = Fake(45.0, name="good")
t = mx.Thermal(providers=[d, e], autostart=False)
t.probe()
check("跳过 300° 取 45°", t.temp == 45.0, t.temp)
check("来源正确", t.source == "good", t.source)

t = mx.Thermal(providers=[Fake(300.0), Fake(-8.0)], autostart=False)
t.probe()
check("全离谱时显示不可用", t.temp is None, t.temp)

print("5) provider 抛异常不影响整体")
f = Fake(None, boom=True)
g = Fake(55.0, name="ok")
t = mx.Thermal(providers=[f, g], autostart=False)
t.probe()
check("异常被吞掉并继续", t.temp == 55.0, t.temp)
check("错误被记录", "传感器炸了" in t.error, t.error)

print("6) sample_once 更新读数")
h = Fake(50.0)
t = mx.Thermal(providers=[h], autostart=False)
t.probe()
h.value = 58.0
t.sample_once()
check("读数已刷新", t.temp == 58.0, t.temp)

print("7) 连续三次读不到才判定失效（抗驱动抖动）")
i = Fake(50.0)
t = mx.Thermal(providers=[i], autostart=False)
t.probe()
i.value = None
t.sample_once()
check("第一次缺失仍保留旧值", t.temp == 50.0, t.temp)
t.sample_once()
check("第二次缺失仍保留", t.temp == 50.0, t.temp)
t.sample_once()
check("第三次才置空", t.temp is None, t.temp)
i.value = 47.0
t.sample_once()
check("失效后自动重探接上", t.temp == 47.0, t.temp)

print("8) reprobe 强制重新选源")
j = Fake(None, name="one")
k = Fake(66.0, name="two")
t = mx.Thermal(providers=[j, k], autostart=False)
t.probe()
check("先选到第二条", t.source == "two", t.source)
j.value = 40.0  # 第一条恢复可用
t.reprobe()
t.sample_once()
check("重探后优先第一条", t.source == "one" and t.temp == 40.0, (t.source, t.temp))

print("9) 后台线程按 interval 采样，能干净退出")
p = Fake(48.0, interval=0.05)
t = mx.Thermal(providers=[p])
time.sleep(0.35)
check("已采样多次", p.calls >= 3, p.calls)
t.stop()
check("线程已停", t._thread is None)
check("provider 已释放", p.closed is True)
n_after = p.calls
time.sleep(0.2)
check("停止后不再采样", p.calls == n_after, (n_after, p.calls))

print("10) 本机真实链路（可用则必须给出合理值）")
real = mx.Thermal(autostart=False)
real_providers = real._ensure()
check("provider 链非空", len(real_providers) >= 1, len(real_providers))
for prov in real_providers:
    try:
        v = prov.read()
    except Exception as exc:  # noqa: BLE001
        check(f"{prov.name} 不抛异常", False, repr(exc))
        continue
    okv = v is None or mx.TEMP_MIN <= float(v) <= mx.TEMP_MAX
    check(f"{prov.name} 返回 None 或合理温度", okv, v)


# ---------------- 外观设置 ----------------

print("11) 设置默认值")
st.ensure_dir()
if st.SETTINGS_FILE.exists():
    st.SETTINGS_FILE.unlink()
s = st.load_settings()
check("默认标准卡", s["style"] == "standard", s["style"])
check("默认 100%", s["scale"] == 1.0, s["scale"])
check("默认无自定义命令", s["temp_cmd"] == "", s["temp_cmd"])

print("12) 老配置 collapsed=true 迁移成横条")
st.SETTINGS_FILE.write_text(json.dumps({"collapsed": True, "x": 10, "y": 20}), encoding="utf-8")
s = st.load_settings()
check("迁移为 bar", s["style"] == "bar", s["style"])
check("位置保留", s["x"] == 10 and s["y"] == 20, (s["x"], s["y"]))

print("13) 新配置原样保留")
st.SETTINGS_FILE.write_text(
    json.dumps({"style": "stack", "scale": 1.15, "collapsed": False, "temp_cmd": "echo 44"}),
    encoding="utf-8",
)
s = st.load_settings()
check("style 保留", s["style"] == "stack", s["style"])
check("scale 保留", abs(s["scale"] - 1.15) < 1e-9, s["scale"])
check("自定义命令保留", s["temp_cmd"] == "echo 44", s["temp_cmd"])

print("14) 非法值回退到安全档")
st.SETTINGS_FILE.write_text(json.dumps({"style": "huge", "scale": 99}), encoding="utf-8")
s = st.load_settings()
check("非法样式回退标准卡", s["style"] == "standard", s["style"])
check("越界缩放回退 1.0", s["scale"] == 1.0, s["scale"])
st.SETTINGS_FILE.write_text(json.dumps({"scale": "abc"}), encoding="utf-8")
check("非数字缩放回退 1.0", st.load_settings()["scale"] == 1.0)

print("15) 损坏的 settings.json 不致命")
st.SETTINGS_FILE.write_text("{ this is not json", encoding="utf-8")
s = st.load_settings()
check("回落默认值", s["style"] == "standard" and s["scale"] == 1.0, s)

shutil.rmtree(_TMP, ignore_errors=True)
print(f"\n结果: {ok} 通过 / {bad} 失败")
sys.exit(1 if bad else 0)
