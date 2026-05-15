"""
live2d_canvas.py — Live2D Cubism 模型嵌入 PyQt6 (QOpenGLWidget)

设计:
- 鼠标事件透传给父 widget (FoamoWidget 拖动逻辑保持工作)
- 透明背景 (跟桌宠悬浮窗融合)
- 60fps update, 自动眨眼/呼吸
- 不在 import 时 init, 第一次创建实例才 init (避免 Python 3.14 / cp313 ABI 抽风)
- 自动补全 model3.json: 很多素材 (尤其 VTube Studio 导出的)
  没把 Motions/Expressions 写进 FileReferences, SDK 加载就只会眨眼。
  我们扫同目录的 *.motion3.json / *.exp3.json 自动生成补全版 _foamo.model3.json
"""
import json
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

import live2d.v3 as live2d


def ensure_model_with_animations(model3_path: Path) -> Path:
    """
    检查 model3.json 是否声明了 Motions / Expressions, 没声明就扫同目录的
    *.motion3.json / *.exp3.json 补进去, 写到 <stem>_foamo.model3.json (副本).
    返回最终给 SDK 加载的 path. 不修改用户原始文件.
    """
    model3_path = Path(model3_path)
    try:
        data = json.loads(model3_path.read_text(encoding="utf-8"))
    except Exception:
        return model3_path
    fr = data.setdefault("FileReferences", {})
    has_motions = bool(fr.get("Motions"))
    has_expressions = bool(fr.get("Expressions"))
    if has_motions and has_expressions:
        return model3_path

    parent = model3_path.parent
    motion_files = sorted(parent.glob("*.motion3.json"))
    exp_files = sorted(parent.glob("*.exp3.json"))
    changed = False

    if not has_motions and motion_files:
        fr["Motions"] = {
            "Idle": [
                {"File": p.name} for p in motion_files
            ]
        }
        changed = True

    if not has_expressions and exp_files:
        fr["Expressions"] = [
            {
                "Name": p.name.replace(".exp3.json", ""),
                "File": p.name,
            }
            for p in exp_files
        ]
        changed = True

    if not changed:
        return model3_path

    out = parent / f"{model3_path.stem}_foamo.model3.json"
    try:
        out.write_text(
            json.dumps(data, ensure_ascii=False, indent="\t"),
            encoding="utf-8",
        )
        print(f"[live2d] 补全 model3 → {out.name} "
              f"(motions={len(motion_files)}, expressions={len(exp_files)})",
              flush=True)
        return out
    except Exception as e:
        print(f"[live2d] 写补全 model3 失败: {e}", flush=True)
        return model3_path


## 状态 → 表情 ID 映射 (按模型分组).
## key 是模型路径里的关键字 (大小写敏感, 子串匹配); value 是 {state: expression_id}.
## 没匹配上的模型走"随机表情"逻辑.
STATE_EXPRESSION_PRESETS: dict[str, dict[str, str]] = {
    # 魔女 — 12 个表情, 豆哥人肉对照过
    "魔女": {
        "idle":    "hdj",  # 默认
        "tender":  "x",    # 爱心 — 温柔
        "focused": "yj",   # 戴眼镜 — 专注
        "happy":   "xx",   # 星星眼 — 雀跃
        "worried": "ku",   # 哭 — 担心/心疼
        "proud":   "fz",   # 法杖 — 邀功炫技
        "jealous": "sq",   # 生气 — 独占欲被戳到 (豆哥提别的 AI 时)
    },
}


def _detect_preset(model_path: Path) -> dict[str, str]:
    """按路径里的关键字匹配 preset. 没匹配 → 空 dict (apply_state 会走随机)."""
    s = str(model_path)
    for key, preset in STATE_EXPRESSION_PRESETS.items():
        if key in s:
            return dict(preset)
    return {}


_LIVE2D_INITED = False


def _ensure_init():
    global _LIVE2D_INITED
    if not _LIVE2D_INITED:
        live2d.init()
        _LIVE2D_INITED = True


def _make_transparent_format() -> QSurfaceFormat:
    """带 alpha 通道的 surface, 让透明背景生效"""
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    return fmt


class Live2DCanvas(QOpenGLWidget):
    """
    嵌入式 Live2D 渲染区。
    - parent 应该是 FoamoWidget 之类
    - 鼠标事件透传, 不会拦截父 widget 的拖动
    """

    def __init__(self, model_path: str, parent=None):
        _ensure_init()
        super().__init__(parent)
        self._model_path = str(model_path)
        self._model: live2d.LAppModel | None = None
        self._inited = False
        self._state_preset: dict[str, str] = _detect_preset(Path(model_path))
        # 面捕模式: True 时在 paintGL 注入 face params, 覆盖 motion/blink 设的值
        self._face_mode: bool = False
        self._latest_face: dict | None = None
        # 语义 → 模型实际 ParamId 的映射 (按模型扫出来, VTube 导出常用大写蛇形)
        self._param_map: dict[str, str] = {}
        # 穿搭系统 (按 group 聚合, 一个"帽子"通常包含 5-7 个 part)
        self._outfit_mode: bool = False              # True 时鼠标点击切 group
        self._part_id_to_idx: dict[str, int] = {}    # SetPartOpacity 用 index
        self._outfit_groups: dict[str, set[str]] = {}  # group_name → {part_ids}
        self._group_visible: dict[str, bool] = {}    # group_name → 当前显隐
        self._part_to_group: dict[str, str] = {}     # part_id → group_name (反查)

        # 透明背景 + 鼠标透传
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFormat(_make_transparent_format())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)

    # ---------------- GL 生命周期 ----------------
    def initializeGL(self):
        live2d.glInit()
        self._load_current_model()

    def _load_current_model(self):
        try:
            # 自动补全 motions/expressions 到 SDK 看得见的 model3.json
            patched = ensure_model_with_animations(Path(self._model_path))
            self._model = live2d.LAppModel()
            self._model.LoadModelJson(str(patched))
            self._model.SetAutoBlinkEnable(True)
            self._model.SetAutoBreathEnable(True)
            if self.width() > 0 and self.height() > 0:
                self._model.Resize(self.width(), self.height())
            # 不主动启动 Idle motion — motion 循环会让模型一直晃 (举法杖/摇头),
            # 跟 GIF 模式那种"定格姿态"对比起来显得不稳定.
            # 静态姿态 + 眨眼 + 呼吸 + 鼠标跟随就够"活"了, 想看动作豆哥右键菜单手动触发.
            try:
                exp_ids = self._model.GetExpressionIds()
                if exp_ids:
                    print(f"[Live2DCanvas] {len(exp_ids)} expressions "
                          f"loaded: {exp_ids[:6]}{'...' if len(exp_ids) > 6 else ''}",
                          flush=True)
                groups = self._model.GetMotionGroups()
                if groups:
                    print(f"[Live2DCanvas] motion groups: {groups} (静默, 不自动播)",
                          flush=True)
            except Exception as e:
                print(f"[Live2DCanvas] motion/expression init: {e}", flush=True)
            # 扫衣物 parts 白名单 (从 cdi3 拿人话名, 过滤含"衣/袜/鞋/裙/帽"的 part)
            self._scan_outfit_parts()
        except Exception as e:
            print(f"[Live2DCanvas] load model failed: {e}", flush=True)
            self._model = None

    def paintGL(self):
        # clearBuffer 默认清成黑色, 我们要透明 — 用 OpenGL 直接清
        try:
            from OpenGL import GL
            GL.glClearColor(0.0, 0.0, 0.0, 0.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        except Exception:
            live2d.clearBuffer()
        if self._model is not None:
            self._model.Update()
            # 面捕模式: Update 后 SetParameterValue 才不会被 motion/blink 覆盖
            if self._face_mode and self._latest_face is not None:
                self._inject_face_params()
            self._model.Draw()

    def resizeGL(self, w: int, h: int):
        if w <= 0 or h <= 0:
            return
        if self._model is not None:
            self._model.Resize(w, h)
            self._inited = True

    # ---------------- 控制 ----------------
    def start(self):
        if not self._timer.isActive():
            self._timer.start(16)  # ~60fps

    def stop(self):
        self._timer.stop()

    def switch_model(self, model_path: str):
        """切换到另一个模型 (同一 OpenGL 上下文中)"""
        if str(model_path) == self._model_path:
            return
        self._model_path = str(model_path)
        self._state_preset = _detect_preset(Path(model_path))
        if self.isValid():  # OpenGL context 已初始化
            self.makeCurrent()
            try:
                self._load_current_model()
            finally:
                self.doneCurrent()
            self.update()

    def drag_to(self, x: float, y: float):
        """让模型眼神/头跟向某点 (FoamoWidget 鼠标转发用)"""
        if self._model is None:
            return
        try:
            self._model.Drag(x, y)
        except Exception as e:
            # 异常只打一次, 别刷屏
            if not getattr(self, "_drag_warned", False):
                print(f"[Live2DCanvas] Drag failed: {e}", flush=True)
                self._drag_warned = True

    # ---------- 表情 / motion 接口 (给 FoamoWidget 调) ----------
    def get_expression_ids(self) -> list[str]:
        if self._model is None: return []
        try: return list(self._model.GetExpressionIds())
        except Exception: return []

    def get_motion_groups(self) -> list[str]:
        if self._model is None: return []
        try: return list(self._model.GetMotionGroups())
        except Exception: return []

    def set_expression(self, name: str | None):
        """name=None → 随机. 失败静默"""
        if self._model is None: return
        try:
            if not name:
                self._model.SetRandomExpression()
            else:
                self._model.SetExpression(name)
        except Exception as e:
            print(f"[Live2DCanvas] set_expression({name}) failed: {e}",
                  flush=True)

    def reset_expression(self):
        if self._model is None: return
        try: self._model.ResetExpression()
        except Exception: pass

    def start_motion(self, group: str = "Idle"):
        if self._model is None: return
        try:
            self._model.StartRandomMotion(group, live2d.MotionPriority.NORMAL)
        except Exception as e:
            print(f"[Live2DCanvas] start_motion({group}) failed: {e}",
                  flush=True)

    def apply_state(self, state: str, mapping: dict | None = None):
        """
        桌宠状态 → 表情/motion.
        - mapping 优先, 然后 fallback 到模型自带的 preset (STATE_EXPRESSION_PRESETS).
        - 都没匹配 → 随机表情.
        - 模型有跟 state 同名的 motion group, 也会触发对应 motion.
        """
        if self._model is None:
            return
        # 表情: mapping > preset > 随机
        exp_id = None
        if mapping and state in mapping:
            exp_id = mapping[state]
        elif state in self._state_preset:
            exp_id = self._state_preset[state]
        self.set_expression(exp_id)
        # motion group: 看模型有没有跟 state 同名的 group
        groups = self.get_motion_groups()
        if state in groups:
            self.start_motion(state)

    # ---------- 穿搭 (Part 显隐, 按 group 聚合) ----------
    # 不同模型走不同的 group 配置, 第一项是组名, 第二项是 cdi3 里 Part Name 必须包含的关键字.
    # 一个 part 落在某 group → 跟该 group 的所有 part 一起显隐.
    # 排除"腿/手/头/眼/嘴"这些是身体本体, 切了就肉色都没了.
    _OUTFIT_GROUPS_BY_MODEL: dict[str, list[tuple[str, tuple[str, ...]]]] = {
        "魔女": [
            ("帽子", ("帽",)),                # 主体+装饰+后边+蒙皮+旋转壳 一起切
            ("上衣", ("上衣", "衣服", "胸口", "蝴蝶结")),
            ("裙子", ("裙",)),                # 短裙+裙撑+左右裙+裙撑后
            ("袜子", ("袜", "丝")),           # 左右袜 + 各种图层版本
            ("鞋子", ("鞋", "靴")),           # 左右鞋
        ],
    }

    def _detect_outfit_groups_config(self) -> list[tuple[str, tuple[str, ...]]]:
        s = str(self._model_path)
        for key, groups in self._OUTFIT_GROUPS_BY_MODEL.items():
            if key in s: return groups
        return []

    def _scan_outfit_parts(self):
        """从 model3.json 同目录的 cdi3.json 拿 part 人话名, 按 group 聚合."""
        self._outfit_groups = {}
        self._group_visible = {}
        self._part_to_group = {}
        self._part_id_to_idx = {}
        if self._model is None:
            return
        try:
            all_ids = list(self._model.GetPartIds())
        except Exception as e:
            print(f"[Live2DCanvas] GetPartIds 失败: {e}", flush=True)
            return
        self._part_id_to_idx = {pid: i for i, pid in enumerate(all_ids)}
        # cdi3 路径
        mp = Path(self._model_path)
        candidates = [
            mp.with_suffix("").with_suffix(".cdi3.json"),
            mp.parent / f"{mp.stem.replace('.model3', '')}.cdi3.json",
        ]
        cdi_path = next((c for c in candidates if c.exists()), None)
        if cdi_path is None:
            print(f"[Live2DCanvas] cdi3.json 没找到, 穿搭 group 空",
                  flush=True)
            return
        try:
            cdi = json.loads(cdi_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[Live2DCanvas] 读 cdi3 失败: {e}", flush=True)
            return
        cfg = self._detect_outfit_groups_config()
        if not cfg:
            print(f"[Live2DCanvas] 模型没配 outfit groups", flush=True)
            return
        # part 列表 (Name, Id), 只保留模型实际有的 part id
        all_parts = [
            (p.get("Name", ""), p.get("Id", ""))
            for p in cdi.get("Parts", [])
            if p.get("Id", "") in self._part_id_to_idx
        ]
        # 每个 group 收集匹配的 part_ids
        for group_name, keywords in cfg:
            ids = {pid for name, pid in all_parts
                   if any(k in name for k in keywords)}
            if ids:
                self._outfit_groups[group_name] = ids
                self._group_visible[group_name] = True
                for pid in ids:
                    self._part_to_group[pid] = group_name
        print(f"[Live2DCanvas] 穿搭 groups ({len(self._outfit_groups)}): "
              + ", ".join(
                  f"{g}({len(ps)}part)"
                  for g, ps in self._outfit_groups.items()
              ), flush=True)

    def _set_part_opacity(self, part_id: str, opacity: float):
        idx = self._part_id_to_idx.get(part_id)
        if idx is None:
            return
        try:
            self._model.SetPartOpacity(idx, opacity)
        except Exception as e:
            print(f"[Live2DCanvas] SetPartOpacity({part_id}/{idx}) failed: {e}",
                  flush=True)

    def _apply_group(self, group: str, visible: bool):
        """切整个 group 的所有 part."""
        ids = self._outfit_groups.get(group)
        if not ids: return
        self._group_visible[group] = visible
        for pid in ids:
            self._set_part_opacity(pid, 1.0 if visible else 0.0)

    def set_outfit_mode(self, on: bool):
        self._outfit_mode = bool(on)

    def is_outfit_mode(self) -> bool:
        return self._outfit_mode

    def try_toggle_part_at(self, x: float, y: float) -> str | None:
        """穿搭模式下点击切 group 显隐. 返回切的 group 名, 没切返回 None."""
        if not self._outfit_mode or self._model is None:
            return None
        if not self._outfit_groups:
            return None
        try:
            hit_ids = self._model.HitPart(x, y, True)  # topOnly=True 只要最上面
        except Exception as e:
            print(f"[Live2DCanvas] HitPart failed: {e}", flush=True)
            return None
        if isinstance(hit_ids, str): hit_ids = [hit_ids]
        if not hit_ids: return None
        for hit_id in hit_ids:
            group = self._part_to_group.get(hit_id)
            if group is not None:
                cur = self._group_visible.get(group, True)
                self._apply_group(group, not cur)
                return group
        return None

    def get_outfit_state(self) -> dict[str, bool]:
        """当前 group 显隐状态 (拷贝)."""
        return dict(self._group_visible)

    def apply_outfit_state(self, state: dict[str, bool]):
        """加载持久化的穿搭. 对每个 group 应用, 缺的当显."""
        if not self._outfit_groups: return
        for group in self._outfit_groups:
            visible = bool(state.get(group, True))
            self._apply_group(group, visible)

    def reset_outfit(self):
        """穿回默认 (所有 group 都显)."""
        for group in self._outfit_groups:
            self._apply_group(group, True)

    # ---------- 面捕 ----------
    # 语义 → 模型实际 ParamId 的候选列表. 按优先级排, 第一个能在 GetParameterIds() 里
    # 找到的就用. VTube Studio 导出的模型很多用大写蛇形 (PARAM_ANGLE_X), 也有非标准名.
    _FACE_PARAM_CANDIDATES: dict[str, tuple[str, ...]] = {
        "AngleX":     ("ParamAngleX", "PARAM_ANGLE_X", "PARAM_HEAD_X"),
        "AngleY":     ("ParamAngleY", "PARAM_ANGLE_Y", "PARAM_HEAD_Y"),
        "AngleZ":     ("ParamAngleZ", "PARAM_ANGLE_Z", "PARAM_HEAD_Z"),
        "BodyX":      ("ParamBodyAngleX", "PARAM_BODY_ANGLE_X"),
        "EyeLOpen":   ("ParamEyeLOpen", "PARAM_EYE_L_OPEN"),
        "EyeROpen":   ("ParamEyeROpen", "PARAM_EYE_R_OPEN"),
        "EyeBallX":   ("ParamEyeBallX", "PARAM_EYE_BALL_X"),
        "EyeBallY":   ("ParamEyeBallY", "PARAM_EYE_BALL_Y"),
        "MouthOpenY": ("ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y", "PARAM_MOUTH_OPEN"),
        "MouthForm":  ("ParamMouthForm", "PARAM_MOUTH_FORM"),
        "BrowLY":     ("ParamBrowLY", "PARAM_BROW_L_Y"),
        "BrowRY":     ("ParamBrowRY", "PARAM_BROW_R_Y"),
    }

    def _resolve_face_params(self):
        """扫模型实际暴露的 ParamId, 对每个语义挑第一个能用的真实 ID. 失败的跳过."""
        if self._model is None:
            self._param_map = {}
            return
        try:
            ids = list(self._model.GetParamIds())
        except Exception as e:
            print(f"[Live2DCanvas] GetParamIds 失败: {e}", flush=True)
            self._param_map = {}
            return
        id_set = set(ids)
        m: dict[str, str] = {}
        for sem, candidates in self._FACE_PARAM_CANDIDATES.items():
            for c in candidates:
                if c in id_set:
                    m[sem] = c
                    break
        self._param_map = m
        print(f"[Live2DCanvas] 模型 ParamIds ({len(ids)}): "
              f"{ids[:20]}{'...' if len(ids) > 20 else ''}", flush=True)
        print(f"[Live2DCanvas] 面捕 ID 映射: {m}", flush=True)
        missing = [k for k in self._FACE_PARAM_CANDIDATES if k not in m]
        if missing:
            print(f"[Live2DCanvas] 模型没有这些语义: {missing} (会跳过)",
                  flush=True)

    def set_face_tracking(self, on: bool):
        """开关面捕模式. 开时关掉自动眨眼 (避免和面捕的眼睛参数打架)."""
        self._face_mode = bool(on)
        if self._model is not None:
            try:
                # 自动眨眼跟面捕的 EyeOpen 冲突, 关掉
                self._model.SetAutoBlinkEnable(not self._face_mode)
            except Exception: pass
        if on:
            self._resolve_face_params()
        if not on:
            self._latest_face = None

    def apply_face_params(self, payload: dict):
        """主线程从 FaceTracker 信号收到的参数. 缓存待 paintGL 注入.
        payload = {blendshapes: {name: value}, head: (yaw, pitch, roll)}.
        """
        self._latest_face = payload

    def _inject_face_params(self):
        """在 Update() 之后、Draw() 之前覆写关键参数. 同帧 motion/blink 设的值会被压.
        用 _param_map 做语义 → 真实 ParamId 的查询, 模型缺的语义自动跳过."""
        if self._model is None or self._latest_face is None:
            return
        if not self._param_map:
            return
        p = self._latest_face
        bs = p.get("blendshapes", {})
        yaw, pitch, roll = p.get("head", (0.0, 0.0, 0.0))
        pm = self._param_map

        def _clamp(v, lo, hi): return max(lo, min(hi, v))
        def _set(sem, v):
            pid = pm.get(sem)
            if pid is None: return
            try: self._model.SetParameterValue(pid, v, 1.0)
            except Exception: pass

        # 头部姿态 (mediapipe 给的是度, Cubism Param 也大致 [-30, 30])
        # pitch 反向更直觉 (低头 = ParamAngleY 负)
        _set("AngleX", _clamp(yaw, -30, 30))
        _set("AngleY", _clamp(-pitch, -30, 30))
        _set("AngleZ", _clamp(roll, -30, 30))
        _set("BodyX", _clamp(yaw * 0.3, -10, 10))

        # 眼睛: blendshape eyeBlink=闭眼程度, ParamEyeOpen=睁开程度
        if "eyeBlinkLeft" in bs:
            _set("EyeLOpen", _clamp(1 - bs["eyeBlinkLeft"] * 1.5, 0, 1))
        if "eyeBlinkRight" in bs:
            _set("EyeROpen", _clamp(1 - bs["eyeBlinkRight"] * 1.5, 0, 1))

        # 眼球瞟向: 4 个 LookXxx 综合左右两眼算 X/Y
        # 镜像逻辑:
        #   左眼 Out = 往左, In = 往右; 右眼 Out = 往右, In = 往左
        #   X 正向 = 角色"看右", Y 正向 = "看上"
        #   * 1.5 加敏感, 让微小眼神也跟得明显
        look_left  = (bs.get("eyeLookOutLeft", 0)  + bs.get("eyeLookInRight", 0)) / 2
        look_right = (bs.get("eyeLookOutRight", 0) + bs.get("eyeLookInLeft", 0)) / 2
        eye_x = look_right - look_left
        look_up   = (bs.get("eyeLookUpLeft", 0)   + bs.get("eyeLookUpRight", 0))   / 2
        look_down = (bs.get("eyeLookDownLeft", 0) + bs.get("eyeLookDownRight", 0)) / 2
        eye_y = look_up - look_down
        _set("EyeBallX", _clamp(eye_x * 1.5, -1, 1))
        _set("EyeBallY", _clamp(eye_y * 1.5, -1, 1))

        # 嘴: jawOpen → 张嘴; smile-frown → 嘴角弧度
        if "jawOpen" in bs:
            _set("MouthOpenY", _clamp(bs["jawOpen"] * 1.5, 0, 1))
        smile = (bs.get("mouthSmileLeft", 0) + bs.get("mouthSmileRight", 0)) / 2
        frown = (bs.get("mouthFrownLeft", 0) + bs.get("mouthFrownRight", 0)) / 2
        _set("MouthForm", _clamp((smile - frown) * 2, -1, 1))

        # 眉毛 (魔女只有 BrowLY, 没有 BrowRY → 用平均值兼任两边)
        brow_up = bs.get("browInnerUp", 0)
        brow_l = brow_up - bs.get("browDownLeft", 0)
        brow_r = brow_up - bs.get("browDownRight", 0)
        if "BrowRY" in pm:
            _set("BrowLY", _clamp(brow_l, -1, 1))
            _set("BrowRY", _clamp(brow_r, -1, 1))
        else:
            _set("BrowLY", _clamp((brow_l + brow_r) / 2, -1, 1))

    def closeEvent(self, e):
        self.stop()
        super().closeEvent(e)
