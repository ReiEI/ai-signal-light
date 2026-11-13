# -*- coding: utf-8 -*-
"""临时工具：抓屏，用于验证渲染效果。"""
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication

app = QApplication(sys.argv)
sc = QGuiApplication.primaryScreen()
pm = sc.grabWindow(0)
out = sys.argv[1] if len(sys.argv) > 1 else "_shot.png"
pm.save(out, "PNG")
print(out, pm.width(), pm.height())
