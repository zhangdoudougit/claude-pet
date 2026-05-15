"""
test_live2d.py - 独立验证 (调试版)

跑: python test_live2d.py
"""
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

import live2d.v3 as live2d


MODEL_DEFAULT = (
    "live2d_sumire_free/live2d_sumire_free/sumire_free_001/"
    "sumire_free_001.model3.json"
)


class Live2DCanvas(QOpenGLWidget):
    def __init__(self, model_path: str, parent=None):
        super().__init__(parent)
        self._model_path = model_path
        self._model: live2d.LAppModel | None = None
        self._inited = False
        self.setMouseTracking(True)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)

    def initializeGL(self):
        live2d.glInit()
        self._model = live2d.LAppModel()
        self._model.LoadModelJson(self._model_path)
        self._model.SetAutoBlinkEnable(True)
        self._model.SetAutoBreathEnable(True)
        # 不在这里 Resize — initializeGL 时 widget size 可能还不准
        # 等 resizeGL 第一次回调 (那时 size 是对的)
        self._timer.start(16)
        # diag
        try:
            print(f"  canvas size      = {self._model.GetCanvasSize()}", flush=True)
            print(f"  canvas pixel     = {self._model.GetCanvasSizePixel()}", flush=True)
            print(f"  pixels per unit  = {self._model.GetPixelsPerUnit()}", flush=True)
            print(f"  drawables        = {len(self._model.GetDrawableIds())}", flush=True)
            print(f"  motion groups    = {self._model.GetMotionGroups()}", flush=True)
        except Exception as e:
            print(f"  diag failed: {e}", flush=True)

    def paintGL(self):
        live2d.clearBuffer()
        if self._model is None:
            return
        self._model.Update()
        self._model.Draw()

    def resizeGL(self, w, h):
        if w <= 0 or h <= 0:
            return
        if self._model is not None:
            self._model.Resize(w, h)
            if not self._inited:
                self._inited = True
                print(f"[resizeGL] first resize: {w}x{h}", flush=True)

    def mouseMoveEvent(self, e):
        if self._model is not None:
            self._model.Drag(e.position().x(), e.position().y())

    def closeEvent(self, e):
        if self._timer.isActive():
            self._timer.stop()
        try: live2d.dispose()
        except Exception: pass
        super().closeEvent(e)


def main():
    args = sys.argv[1:]
    model = args[0] if args else MODEL_DEFAULT
    model_path = Path(model)
    if not model_path.is_absolute():
        model_path = Path(__file__).parent / model_path

    if not model_path.exists():
        print(f"❌ 模型不存在:\n  {model_path}")
        sys.exit(1)

    print(f"🎀 加载模型: {model_path}")

    # init 必须在 QApplication 之前 / 之后都可, 但要在创建 widget 前
    live2d.init()

    app = QApplication(sys.argv)

    win = QMainWindow()
    win.setWindowTitle("Live2D 验证 — 紫罗兰")
    win.resize(420, 640)

    canvas = Live2DCanvas(str(model_path))
    win.setCentralWidget(canvas)
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
