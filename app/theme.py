# -*- coding: utf-8 -*-
"""界面配色与 Windows 深浅色的单一数据源。

为什么单独一个文件：
  - 卡片、菜单、托盘、状态灯、exe 图标以前各写各的颜色，图标那套红黄绿
    和状态灯的真实颜色根本不是一回事。现在全部从这里取。
  - 生成器在 _dev 下也要读这里的 STATE_HEX，保证「软件状态灯实际颜色」
    是引用关系，不是抄一遍 hex 值。

主题三态：auto（跟随 Windows）/ light / dark。auto 以注册表
HKCU\\...\\Themes\\Personalize\\AppsUseLightTheme 为准（这是 Windows
"应用模式"的真实设定），Qt 的 colorScheme 作为读不到注册表时的回退。
"""
from __future__ import annotations

from PySide6.QtGui import QColor

THEME_IDS = ("auto", "light", "dark")

# 状态灯的权威色值。图标的三盏灯直接用 failed / waiting / done。
# 浅色主题用加深一档的变体，否则天蓝和亮绿在白卡片上会糊掉。
STATE_HEX = {
    "idle": "#5C6472",
    "running": "#38BDF8",
    "done": "#22C55E",
    "waiting": "#F59E0B",
    "failed": "#EF4444",
}
STATE_HEX_LIGHT = {
    "idle": "#94A3B8",
    "running": "#0284C7",
    "done": "#15803D",
    "waiting": "#B45309",
    "failed": "#DC2626",
}

# 每项 = (hex, alpha)。用二元组而不是 8 位 hex，避免 Qt 各版本对
# #RRGGBBAA / #AARRGGBB 解析顺序不一致。
_SPECS = {
    "dark": {
        "card_bg": ("#0F1217", 243),
        "card_border": ("#2E343F", 255),
        "txt": ("#E8ECF2", 255),
        "txt_dim": ("#7C8696", 255),
        "txt_faint": ("#586170", 255),
        "divider": ("#22272F", 255),
        "track": ("#1F242C", 255),
        "bar_blue": ("#3E7BD6", 255),
        "amber": ("#F59E0B", 255),
        "menu_bg": ("#12161C", 255),
        "menu_fg": ("#E8ECF2", 255),
        "menu_border": ("#2E333C", 255),
        "menu_sel": ("#213043", 255),
        "menu_disabled": ("#7C8696", 255),
        "menu_sep": ("#242A33", 255),
        "tray_rim": ("#0F1217", 210),
        "chip_bg": ("#373737", 255),
        # 硬件指标：比主文字低一档，"相对弱化"而不是看不清。
        # 告警色（>= warn/hot）仍由 _level_color 单独接管，不受这里影响。
        "metric_value": ("#A9B4C6", 255),
        "metric_label": ("#646E7E", 255),
        "metric_bar": ("#35507A", 255),
        # 散热控制面板：按钮三态要单独给，浅色主题下深绿按钮会很突兀
        "panel_btn": ("#1E2530", 255),
        "panel_btn_hover": ("#27303D", 255),
        "panel_btn_edge_hover": ("#3A4556", 255),
        "panel_primary_bg": ("#1B3A2C", 255),
        "panel_primary_edge": ("#2C5B45", 255),
        "panel_primary_fg": ("#8FE3B4", 255),
        "panel_primary_hover": ("#204735", 255),
        "panel_danger_bg": ("#3A2318", 255),
        "panel_danger_edge": ("#5B3A2C", 255),
        "panel_danger_fg": ("#F0B48F", 255),
        "panel_danger_hover": ("#472B1D", 255),
        "panel_combo_sel": ("#2B3546", 255),
    },
    "light": {
        "card_bg": ("#FAFBFD", 247),
        "card_border": ("#D2D8E2", 255),
        "txt": ("#1B2430", 255),
        "txt_dim": ("#586474", 255),
        "txt_faint": ("#66738A", 255),
        "divider": ("#E2E7EE", 255),
        "track": ("#E8ECF2", 255),
        "bar_blue": ("#2F6FD0", 255),
        "amber": ("#B45309", 255),
        "menu_bg": ("#FFFFFF", 255),
        "menu_fg": ("#1B2430", 255),
        "menu_border": ("#D2D8E2", 255),
        "menu_sel": ("#E7F0FE", 255),
        "menu_disabled": ("#9AA5B1", 255),
        "menu_sep": ("#E2E7EE", 255),
        "tray_rim": ("#FFFFFF", 210),
        "chip_bg": ("#2A303B", 255),
        "metric_value": ("#4A5768", 255),
        "metric_label": ("#7C8899", 255),
        "metric_bar": ("#A8BEDC", 255),
        "panel_btn": ("#F1F4F9", 255),
        "panel_btn_hover": ("#E3EAF5", 255),
        "panel_btn_edge_hover": ("#AEBBD0", 255),
        "panel_primary_bg": ("#DFF3E7", 255),
        "panel_primary_edge": ("#8FCFAE", 255),
        "panel_primary_fg": ("#146C3A", 255),
        "panel_primary_hover": ("#CDEBD9", 255),
        "panel_danger_bg": ("#FBE9E2", 255),
        "panel_danger_edge": ("#E0B3A0", 255),
        "panel_danger_fg": ("#9A3B1B", 255),
        "panel_danger_hover": ("#F6DACE", 255),
        "panel_combo_sel": ("#DBE7F8", 255),
    },
}
_SHADOW = {"dark": (18, 26, 34), "light": (10, 16, 22)}

_FIELDS = tuple(_SPECS["dark"].keys())


def _mk(spec) -> QColor:
    c = QColor(spec[0])
    c.setAlpha(spec[1])
    return c


class Palette:
    """可变调色板：切换主题时原地换色，绘制代码每帧读到的一定是最新值。

    刻意不做成模块级散落的常量 —— 那样切换主题得靠 globals() 重新绑定，
    任何一处缓存了旧 QColor 就会漏掉，属于静默回归。
    """

    def __init__(self, theme: str = "dark") -> None:
        self.set(theme)

    def set(self, theme: str) -> None:
        tid = theme if theme in ("light", "dark") else "dark"
        spec = _SPECS[tid]
        self.name = tid
        for k in _FIELDS:
            setattr(self, k, _mk(spec[k]))
        self.shadow_layers = _SHADOW[tid]
        self._states = {
            k: QColor(v) for k, v in (STATE_HEX_LIGHT if tid == "light" else STATE_HEX).items()
        }

    def state(self, key: str) -> QColor:
        """按状态名取当前主题下的状态灯颜色。"""
        return self._states.get(key, self._states["idle"])

    @property
    def state_hex(self) -> dict[str, str]:
        src = STATE_HEX_LIGHT if self.name == "light" else STATE_HEX
        return dict(src)


PAL = Palette("dark")


def tint(color: QColor, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


def hex_str(color: QColor) -> str:
    return f"#{color.red():02X}{color.green():02X}{color.blue():02X}"


def menu_stylesheet(pal: Palette) -> str:
    """右键菜单也要跟着主题走，否则浅色卡片配一块黑菜单很割裂。"""
    return f"""
        QMenu {{ background:{hex_str(pal.menu_bg)}; color:{hex_str(pal.menu_fg)};
                border:1px solid {hex_str(pal.menu_border)};
                border-radius:8px; padding:6px; font-family:'Microsoft YaHei UI'; font-size:12px; }}
        QMenu::item {{ padding:6px 18px 6px 14px; border-radius:5px; }}
        QMenu::item:selected {{ background:{hex_str(pal.menu_sel)}; }}
        QMenu::item:disabled {{ color:{hex_str(pal.menu_disabled)}; }}
        QMenu::separator {{ height:1px; background:{hex_str(pal.menu_sep)}; margin:5px 8px; }}
    """


def panel_stylesheet(pal: Palette, font: str = "Microsoft YaHei UI", mono: str = "Consolas") -> str:
    """散热控制面板的 QSS。主题切换后要重新 setStyleSheet 才会生效。"""
    h = hex_str
    return f"""
        QDialog, QInputDialog {{ background:{h(pal.menu_bg)}; }}
        QLabel {{ color:{h(pal.txt)}; font-family:'{font}'; font-size:12px; }}
        QLineEdit {{
            background:{h(pal.panel_btn)}; color:{h(pal.txt)}; border:1px solid {h(pal.divider)};
            border-radius:6px; padding:5px 8px; font-family:'{font}'; font-size:12px;
        }}
        QFrame#block {{ background:{h(pal.track)}; border:1px solid {h(pal.divider)}; border-radius:10px; }}
        QLabel#k {{ color:{h(pal.txt_faint)}; font-size:11px; }}
        QLabel#v {{ color:{h(pal.txt)}; font-size:12px; }}
        QLabel#big {{ color:{h(pal.txt)}; font-family:'{mono}'; font-size:19px; font-weight:600; }}
        QLabel#hint {{ color:{h(pal.txt_faint)}; font-size:11px; }}
        QPushButton {{
            background:{h(pal.panel_btn)}; color:{h(pal.txt)}; border:1px solid {h(pal.divider)};
            border-radius:7px; padding:7px 12px; font-family:'{font}'; font-size:12px;
        }}
        QPushButton:hover:enabled {{ background:{h(pal.panel_btn_hover)}; border-color:{h(pal.panel_btn_edge_hover)}; }}
        QPushButton:disabled {{ color:{h(pal.txt_faint)}; background:{h(pal.menu_bg)}; }}
        QPushButton#primary {{ background:{h(pal.panel_primary_bg)}; border-color:{h(pal.panel_primary_edge)}; color:{h(pal.panel_primary_fg)}; }}
        QPushButton#primary:hover:enabled {{ background:{h(pal.panel_primary_hover)}; }}
        QPushButton#danger {{ background:{h(pal.panel_danger_bg)}; border-color:{h(pal.panel_danger_edge)}; color:{h(pal.panel_danger_fg)}; }}
        QPushButton#danger:hover:enabled {{ background:{h(pal.panel_danger_hover)}; }}
        QComboBox, QSpinBox {{
            background:{h(pal.panel_btn)}; color:{h(pal.txt)}; border:1px solid {h(pal.divider)};
            border-radius:6px; padding:5px 8px; font-family:'{font}'; font-size:12px;
        }}
        QComboBox QAbstractItemView {{ background:{h(pal.panel_btn)}; color:{h(pal.txt)}; selection-background-color:{h(pal.panel_combo_sel)}; }}
    """


def _registry_light():
    """读 Windows 应用模式：1=浅色，0=深色；读不到返回 None 交给下一层。"""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            return int(winreg.QueryValueEx(key, "AppsUseLightTheme")[0]) == 1
        finally:
            winreg.CloseKey(key)
    except Exception:
        return None


def windows_scheme() -> str:
    """当前 Windows 深浅色。注册表优先，Qt 回退，都失败按深色处理。"""
    reg = _registry_light()
    if reg is not None:
        return "light" if reg else "dark"
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        inst = QGuiApplication.instance()
        if inst is not None:
            hints = inst.styleHints()
            cs = hints.colorScheme()
            if cs == Qt.ColorScheme.Light:
                return "light"
            if cs == Qt.ColorScheme.Dark:
                return "dark"
    except Exception:
        pass
    return "dark"


def resolve(pref: str) -> str:
    """把设置里的 auto/light/dark 解析成真正要用的 light/dark。"""
    if pref in ("light", "dark"):
        return pref
    return windows_scheme()


def sanitize(pref) -> str:
    return pref if pref in THEME_IDS else "auto"


def contrast(a: QColor, b: QColor) -> float:
    """WCAG 相对亮度对比度，自检用它挡住"浅色主题上浅色字"这类问题。"""
    def lum(c: QColor) -> float:
        def ch(v: int) -> float:
            x = v / 255.0
            return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)
