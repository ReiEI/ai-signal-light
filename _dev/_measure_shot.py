# -*- coding: utf-8 -*-
"""开发期工具：在真实截屏里量卡片边界与文字墨迹边界。"""
import sys
from pathlib import Path

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
img = QImage(str(Path(__file__).parent / "_shot_full.png"))
W, H = img.width(), img.height()
print("截图尺寸", W, H)

CARD = (15, 18, 23)


def is_card(c):
    return abs(c.red() - CARD[0]) < 8 and abs(c.green() - CARD[1]) < 8 and abs(c.blue() - CARD[2]) < 8


def lum(c):
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


# 1) 找卡片主体：右下角区域里 CARD 色像素的包围盒
x0, y0 = int(W * 0.68), int(H * 0.70)
xs, ys = [], []
for y in range(y0, H):
    for x in range(x0, W):
        if is_card(img.pixelColor(x, y)):
            xs.append(x)
            ys.append(y)
if not xs:
    print("未找到卡片")
    sys.exit()
cl, cr = min(xs), max(xs)
ct, cb = min(ys), max(ys)
print(f"卡片填充包围盒 x[{cl}..{cr}] y[{ct}..{cb}]  宽={cr-cl} 高={cb-ct}")
print(f"推得窗口: left={cl - 8 * 1.5:.0f}  宽={(cr - cl) + 16 * 1.5:.0f}  (DPR 1.5)")

# 2) 每行文字墨迹的右边界
print("\n各行墨迹右边界（应 <= 卡片右边界 - pad）：")
print(f"  期望文字右界 = {cr} - 14*1.5 = {cr - 21:.0f}")
bands = [("第一行", 0.10, 0.28), ("第二行", 0.28, 0.44), ("资源区", 0.48, 0.90)]
for name, a, b in bands:
    ya, yb = int(ct + (cb - ct) * a), int(ct + (cb - ct) * b)
    right = -1
    for y in range(ya, yb):
        for x in range(W - 1, cl, -1):
            if lum(img.pixelColor(x, y)) > 80:
                right = max(right, x)
                break
    print(f"  {name}: 墨迹最右 x={right}   {'✓' if right <= cr else '✗ 超出卡片 ' + str(right - cr) + 'px'}")
