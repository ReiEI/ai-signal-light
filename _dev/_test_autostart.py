# -*- coding: utf-8 -*-
"""开机自启端到端自测：注册表写入→回读→路径验证→菜单态→删除，全链路覆盖。

不依赖 Qt 事件循环，直接复现 _set_autostart / _autostart_enabled 的注册表逻辑。
同时验证 frozen / 非 frozen 两种路径推导。
"""
import os
import sys
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ok = 0
bad = 0

def check(name: str, cond: bool, detail=None) -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        bad += 1
        print(f"  FAIL  {name}" + (f"  -> {detail}" if detail is not None else ""))

RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SignalLightWidget"


def _reg_write(cmd: str) -> None:
    """复现 _set_autostart(on=True) 的注册表写入逻辑。"""
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_PATH, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, cmd)


def _reg_read() -> str | None:
    """复现 _autostart_enabled 的读取逻辑，返回值或 None。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_PATH) as k:
            val, _ = winreg.QueryValueEx(k, VALUE_NAME)
            return val
    except FileNotFoundError:
        return None


def _reg_delete() -> None:
    """复现 _set_autostart(on=False) 的删除逻辑。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_PATH, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE_NAME)
    except FileNotFoundError:
        pass


# ── 1. 初始状态：干净环境 ──────────────────────────────────────
print("1) 初始状态")
_reg_delete()  # 确保干净
check("初始无自启条目", _reg_read() is None)

# ── 2. 写入：frozen 路径（模拟 exe 运行） ─────────────────────
print("2) frozen 路径写入")
fake_exe = r"C:\Program Files\AI Signal Light\dist\SignalLight.exe"
cmd_frozen = f'"{fake_exe}"'
_reg_write(cmd_frozen)
val = _reg_read()
check("写入后可读", val is not None)
check("值等于写入内容", val == cmd_frozen, val)
check("路径含引号包裹", val.startswith('"') and val.endswith('"'), val)

# ── 3. 路径验证：exe 存在 ─────────────────────────────────────
print("3) 路径存在性")
actual_exe = Path(__file__).resolve().parent.parent / "dist" / "SignalLight.exe"
check("dist/SignalLight.exe 存在", actual_exe.exists(), str(actual_exe))
# 用真实 exe 路径重写
cmd_real = f'"{actual_exe}"'
_reg_write(cmd_real)
val = _reg_read()
check("真实 exe 路径写入可读", val == cmd_real, val)
# 解析回读值里的路径并验证
inner = val.strip('"')
check("回读路径指向真实文件", Path(inner).exists(), inner)

# ── 4. 非 frozen 路径推导（模拟源码运行） ─────────────────────
print("4) 非 frozen 路径推导")
py_exe = Path(sys.executable)
pyw = py_exe.with_name("pythonw.exe")
exe = pyw if pyw.exists() else py_exe
script = Path(__file__).resolve().parent.parent / "app" / "main.py"
cmd_src = f'"{exe}" "{script}"'
_reg_write(cmd_src)
val = _reg_read()
check("源码模式写入可读", val == cmd_src, val)
parts = val.split('" "')
check("源码模式含两个引号段（解释器 + 脚本）", len(parts) == 2, val)
check("解释器路径存在", Path(parts[0].strip('"')).exists(), parts[0])
check("脚本路径存在", Path(parts[1].strip('"')).exists(), parts[1])

# ── 5. 中文路径含空格场景 ─────────────────────────────────────
print("5) 中文 + 空格路径")
weird = r"D:\测试 目录\信号灯 app\SignalLight.exe"
cmd_weird = f'"{weird}"'
_reg_write(cmd_weird)
val = _reg_read()
check("中文空格路径原样回读", val == cmd_weird, val)

# ── 6. 覆盖写入（重复点击开机自启） ───────────────────────────
print("6) 覆盖写入")
_reg_write(cmd_real)
val = _reg_read()
check("覆盖后值正确", val == cmd_real, val)

# ── 7. 删除 ────────────────────────────────────────────────────
print("7) 删除")
_reg_delete()
check("删除后无条目", _reg_read() is None)

# ── 8. 重复删除不抛异常 ────────────────────────────────────────
print("8) 重复删除")
_reg_delete()  # 应静默
check("重复删除不抛异常", True)

# ── 9. 删除不存在的值不抛异常 ──────────────────────────────────
print("9) 删除不存在的值")
try:
    _reg_delete()
    check("删除不存在的值不抛异常", True)
except Exception as e:
    check("删除不存在的值不抛异常", False, str(e))

# ── 10. 源码级：_set_autostart 不用小写 .close() ──────────────
print("10) 源码防回归")
main_src = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
seg = main_src.split("def _set_autostart")[1].split("def _single_instance")[0]
check("不用小写 .close()", ".close()" not in seg)
check("用 with winreg.CreateKeyEx", "with winreg.CreateKeyEx" in seg)
check("setChecked 用 blockSignals 防反向触发", "a_boot.blockSignals(True)" in main_src)
check("用 toggled 信号", "a_boot.toggled.connect" in main_src)
check("frozen 用 sys.executable", 'cmd = f\'"{sys.executable}"\'' in seg)
check("非 frozen 推导 pythonw", "pythonw" in seg)

# ── 11. 模拟 _autostart_enabled 全流程 ─────────────────────────
print("11) _autostart_enabled 流程")
_reg_delete()
check("空注册表 → False", _reg_read() is None)
_reg_write(cmd_real)
check("写入后 → True", _reg_read() is not None)
_reg_delete()
check("删除后 → False", _reg_read() is None)

# ── 12. 源码级：自愈机制 ──────────────────────────────────────
print("12) 自愈机制防回归")
check("有 _heal_autostart 方法", "def _heal_autostart" in main_src)
check("有 _write_autostart_entry 方法", "def _write_autostart_entry" in main_src)
check("__init__ 调用 _heal_autostart", "self._heal_autostart()" in main_src)
check("有 30s 周期自愈定时器", "self._heal_timer.start(30_000)" in main_src)
check("_set_autostart 持久化 autostart 到 settings", 'self.settings["autostart"] = on' in main_src)
check("_autostart_enabled 优先看 settings", 'if "autostart" in self.settings' in main_src)
check("_heal_autostart 先查注册表再补写", "QueryValueEx(k, \"SignalLightWidget\")" in main_src.split("def _heal_autostart")[1].split("def _single_instance")[0])
check("_shutdown 退出时强制补写", "_write_autostart_entry" in main_src.split("def _shutdown")[1].split("app.aboutToQuit")[0])
check("_shutdown 只在 autostart=True 时补写", 'if w.settings.get("autostart")' in main_src.split("def _shutdown")[1])

# ── 收尾：还原干净 ────────────────────────────────────────────
_reg_delete()

print(f"\n结果: {ok} 通过 / {bad} 失败")
sys.exit(1 if bad else 0)
