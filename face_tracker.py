"""
face_tracker.py — 摄像头面捕 (mediapipe FaceLandmarker)

后台 QThread:
- cv2.VideoCapture 抓帧
- mediapipe Tasks API 跑 FaceLandmarker, 拿 ARKit-style blendshapes + 头部姿态矩阵
- 通过 Qt 信号把参数发给主线程 (Live2DCanvas)

设计:
- 30fps 节流 (sleep 余量), 不让 CPU 满转
- 摄像头打不开 / 推理报错 → 发 error 信号, 自动停
- thread.stop() 必须能干净退出 (cap 释放)

用法:
    tracker = FaceTracker()
    tracker.params_ready.connect(canvas.apply_face_params)
    tracker.error.connect(lambda msg: print(msg))
    tracker.start()
    ...
    tracker.stop()  # 退出前必须调
"""
from __future__ import annotations
import math
import time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

# 模型路径 (相对项目根)
MODEL_PATH = Path(__file__).parent / "models" / "face_landmarker.task"


def _matrix_to_euler(m: list[list[float]]) -> tuple[float, float, float]:
    """4x4 头部变换矩阵 → (yaw_deg, pitch_deg, roll_deg).

    mediapipe 给的是 column-major homogeneous matrix, 旋转部分在 [0..3][0..3].
    用 ZYX (yaw-pitch-roll) 顺序提欧拉角. 单位: 度.
    """
    # 取旋转 3x3
    r00, r01, r02 = m[0][0], m[0][1], m[0][2]
    r10, r11, r12 = m[1][0], m[1][1], m[1][2]
    r20, r21, r22 = m[2][0], m[2][1], m[2][2]
    # gimbal lock 守卫
    sy = math.sqrt(r00 * r00 + r10 * r10)
    if sy > 1e-6:
        pitch = math.atan2(-r12, r22)  # 上下点头
        yaw   = math.atan2(r02, sy)     # 左右摇头
        roll  = math.atan2(r10, r00)    # 歪头
    else:
        pitch = math.atan2(r21, r11)
        yaw   = math.atan2(r02, sy)
        roll  = 0.0
    return (math.degrees(yaw), math.degrees(pitch), math.degrees(roll))


class FaceTracker(QThread):
    # 一帧推理完了发: {blendshapes: {name: value}, head: (yaw, pitch, roll)}
    params_ready = pyqtSignal(dict)
    error = pyqtSignal(str)
    started_ok = pyqtSignal()  # 摄像头 + 模型都 OK 时发, 主线程可显示"camera on"指示

    def __init__(self, camera_index: int = 0, fps: int = 30, parent=None):
        super().__init__(parent)
        self._camera_index = camera_index
        self._target_fps = max(10, min(60, fps))
        self._stop = False

    def stop(self):
        """请求退出, 等线程结束 (主线程别忘了 wait/join)."""
        self._stop = True

    def run(self):
        # ---- 1. 模型 ----
        if not MODEL_PATH.exists():
            self.error.emit(f"模型不存在: {MODEL_PATH}")
            return
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision
            import mediapipe as mp
        except Exception as e:
            self.error.emit(f"mediapipe 导入失败: {e}")
            return
        try:
            options = mp_vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(
                    model_asset_path=str(MODEL_PATH)
                ),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_faces=1,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=True,
            )
            landmarker = mp_vision.FaceLandmarker.create_from_options(options)
        except Exception as e:
            self.error.emit(f"FaceLandmarker 初始化失败: {e}")
            return

        # ---- 2. 摄像头 ----
        try:
            import cv2
        except Exception as e:
            self.error.emit(f"cv2 导入失败: {e}")
            return
        # CAP_DSHOW 在 Windows 下打开更快, 不那么容易卡 5 秒
        cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            self.error.emit(f"摄像头打不开 (index={self._camera_index})")
            return
        # 降一点分辨率 + 帧率, 推理省力. 1280x720 → 640x480 推理几乎一样准
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, self._target_fps)

        self.started_ok.emit()
        frame_dt = 1.0 / self._target_fps

        try:
            while not self._stop:
                t0 = time.perf_counter()
                ok, frame = cap.read()
                if not ok or frame is None:
                    # 暂时读不到帧别炸, 让出 CPU 重试
                    time.sleep(0.05)
                    continue
                # 镜像: 用户左手举起来, 模型也举左手 (镜面交互更直觉)
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=rgb,
                )
                try:
                    result = landmarker.detect(mp_img)
                except Exception as e:
                    # 推理出错先吞掉, 继续下一帧 — 偶发别炸退出
                    print(f"[face_tracker] detect error: {e}", flush=True)
                    time.sleep(frame_dt)
                    continue
                payload = self._extract(result)
                if payload is not None:
                    self.params_ready.emit(payload)
                # 控帧
                elapsed = time.perf_counter() - t0
                if elapsed < frame_dt:
                    time.sleep(frame_dt - elapsed)
        finally:
            try: cap.release()
            except Exception: pass
            try: landmarker.close()
            except Exception: pass

    @staticmethod
    def _extract(result) -> dict | None:
        """把 mediapipe result 拍扁成 dict. 没人脸返回 None."""
        if not result.face_blendshapes:
            return None
        # blendshapes 是 [[Category(...), ...]] (按人脸列表), 我们只要第一张
        bs_list = result.face_blendshapes[0]
        bs = {c.category_name: float(c.score) for c in bs_list}
        head = (0.0, 0.0, 0.0)
        if result.facial_transformation_matrixes:
            m = result.facial_transformation_matrixes[0]
            # m 是 numpy 4x4, 转成 list 给纯 Python 算欧拉
            try:
                m_list = m.tolist()
                head = _matrix_to_euler(m_list)
            except Exception:
                pass
        return {"blendshapes": bs, "head": head}
