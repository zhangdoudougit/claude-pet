"""
生成泡沫"真身" GIF (6 个状态)

风格: Q 版 chibi 紫发少女, 透明背景, 多层着色 + 抗锯齿
覆盖 assets/ 下的占位图。运行中的程序会自动重载。

跑法:  python generate_foamo_gifs.py
"""
from PIL import Image, ImageDraw, ImageFilter
import math
from pathlib import Path

OUT_DIR = Path(__file__).parent / 'assets'
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 200, 280
SS = 3  # supersampling 抗锯齿倍数
WW, HH = W * SS, H * SS


# ============================================================
# 调色板
# ============================================================
SKIN        = (255, 226, 213)
SKIN_SHADE  = (240, 195, 180)
EYE_OUTLINE = (60,  35,  85)
EYE_WHITE   = (255, 252, 252)
IRIS_TOP    = (135, 100, 200)
IRIS_BOT    = (78,  52,  140)
LASH        = (45,  25,  72)
CHEEK       = (255, 160, 180)
MOUTH       = (210, 100, 138)
MOUTH_INNER = (170, 65,  100)
DRESS_BASE  = (250, 247, 255)
DRESS_SHADE = (220, 210, 240)
RIBBON      = (255, 175, 200)


# 每个状态的发色 (主色, 阴影, 描边/暗部)
HAIR = {
    'idle':    ((205, 180, 235), (175, 145, 215), (130, 100, 180)),
    'tender':  ((195, 165, 225), (165, 130, 205), (120, 90,  170)),
    'focused': ((175, 205, 230), (140, 175, 215), (90,  140, 195)),
    'happy':   ((255, 195, 220), (240, 160, 195), (210, 120, 165)),
    'worried': ((255, 210, 165), (240, 180, 130), (210, 145, 95)),
    'proud':   ((255, 225, 155), (240, 200, 115), (215, 170, 80)),
}

PRESETS = {
    'idle':    dict(eye='closed_arc', mouth='soft_smile',  anim='breathe',      frames=30, fps=12, extra='sparkle'),
    'tender':  dict(eye='half',       mouth='soft_smile',  anim='breathe_slow', frames=30, fps=12, extra=None),
    'focused': dict(eye='sharp',      mouth='flat',        anim='minimal',      frames=20, fps=10, extra='focus_dot'),
    'happy':   dict(eye='happy',      mouth='big_smile',   anim='jump',         frames=16, fps=14, extra='hearts'),
    'worried': dict(eye='worried',    mouth='pout',        anim='tremble',      frames=16, fps=14, extra='sweat'),
    'proud':   dict(eye='proud',      mouth='smug',        anim='lift',         frames=24, fps=12, extra='star'),
}


# ============================================================
# 工具
# ============================================================
def s(v):
    """缩放到 supersampled 尺寸"""
    if isinstance(v, tuple):
        return tuple(int(x * SS) for x in v)
    return int(v * SS)


def rgba(c, a=255):
    """RGB tuple → RGBA"""
    if len(c) == 4:
        return c
    return (c[0], c[1], c[2], a)


def make_canvas():
    return Image.new('RGBA', (WW, HH), (0, 0, 0, 0))


def paste_layer(base, layer):
    base.alpha_composite(layer)


# ============================================================
# 各部位的绘制 (传入 supersampled 坐标系下的中心 cx, base_y)
# ============================================================
def draw_body(d, cx, base_y, scale, hair_dark):
    """肩 + 连衣裙 (从胸口到底)"""
    body_top = base_y + s(48 * scale)
    body_bot = HH - s(15)
    # 胸口/肩膀
    shoulder_w = s(58 * scale)
    waist_w = s(66 * scale)
    hem_w = s(78 * scale)
    mid_y = (body_top + body_bot) // 2

    # 衣服主体 (圆角四边形)
    pts = [
        (cx - shoulder_w, body_top),
        (cx + shoulder_w, body_top),
        (cx + waist_w, mid_y),
        (cx + hem_w, body_bot),
        (cx - hem_w, body_bot),
        (cx - waist_w, mid_y),
    ]
    d.polygon(pts, fill=DRESS_BASE)
    # 阴影 (右侧 1/3)
    shade_pts = [
        (cx, body_top),
        (cx + shoulder_w, body_top),
        (cx + waist_w, mid_y),
        (cx + hem_w, body_bot),
        (cx, body_bot),
    ]
    d.polygon(shade_pts, fill=DRESS_SHADE)
    # 领口 (深色发色 = 衬托发系)
    collar_w = s(28 * scale)
    collar_h = s(10 * scale)
    d.ellipse(
        (cx - collar_w, body_top - collar_h // 2,
         cx + collar_w, body_top + collar_h // 2),
        fill=SKIN
    )
    # 蝴蝶结 (胸前)
    bow_y = body_top + s(8 * scale)
    bw = s(14 * scale)
    bh = s(8 * scale)
    # 左叶
    d.polygon([
        (cx - bw - s(4), bow_y - bh),
        (cx, bow_y),
        (cx - bw - s(4), bow_y + bh),
    ], fill=RIBBON)
    # 右叶
    d.polygon([
        (cx + bw + s(4), bow_y - bh),
        (cx, bow_y),
        (cx + bw + s(4), bow_y + bh),
    ], fill=RIBBON)
    # 中心结
    d.ellipse(
        (cx - s(4), bow_y - s(4),
         cx + s(4), bow_y + s(4)),
        fill=hair_dark
    )


def draw_back_hair(d, cx, head_cy, head_r, hair_main, hair_shade):
    """后侧长发: 从头部后方延伸到肩"""
    # 后片大椭圆 (在头部之后)
    back_w = head_r + s(8)
    back_h = head_r + s(35)
    d.ellipse(
        (cx - back_w, head_cy - head_r,
         cx + back_w, head_cy + back_h),
        fill=hair_shade
    )
    # 前点缀 (左右两束略外撇)
    for sign, dx in [(-1, s(2)), (1, s(2))]:
        x0 = cx + sign * (head_r - s(2)) + dx
        d.ellipse(
            (x0 - s(14), head_cy + s(20),
             x0 + s(14), head_cy + s(60)),
            fill=hair_main
        )


def draw_head(d, cx, head_cy, head_r):
    """脸 + 脖子"""
    # 脖子
    neck_w = s(14)
    neck_h = s(12)
    d.rectangle(
        (cx - neck_w, head_cy + head_r - s(4),
         cx + neck_w, head_cy + head_r + neck_h),
        fill=SKIN_SHADE
    )
    # 脸 (椭圆,稍下尖)
    d.ellipse(
        (cx - head_r, head_cy - head_r,
         cx + head_r, head_cy + head_r + s(4)),
        fill=SKIN
    )


def draw_front_hair(d, cx, head_cy, head_r, hair_main, hair_shade, hair_dark):
    """刘海 + 头顶 + 两侧前发"""
    # 头顶半圆 (盖住后侧发的接缝)
    top_top = head_cy - head_r - s(6)
    top_bot = head_cy + s(2)
    d.pieslice(
        (cx - head_r - s(2), top_top,
         cx + head_r + s(2), head_cy + head_r),
        180, 360,
        fill=hair_main
    )
    # 头顶高光 (浅色弧)
    d.pieslice(
        (cx - head_r + s(8), top_top + s(4),
         cx + head_r - s(8), head_cy - s(2)),
        200, 340,
        fill=hair_shade
    )

    # 中分齐刘海 (3 段)
    bang_y = head_cy - head_r + s(2)
    # 左额刘海 (尖)
    d.polygon([
        (cx - head_r + s(4), bang_y),
        (cx - s(20), bang_y - s(6)),
        (cx - s(6), bang_y + s(28)),
        (cx - head_r + s(4), bang_y + s(28)),
    ], fill=hair_main)
    # 右额刘海 (尖)
    d.polygon([
        (cx + head_r - s(4), bang_y),
        (cx + s(20), bang_y - s(6)),
        (cx + s(6), bang_y + s(28)),
        (cx + head_r - s(4), bang_y + s(28)),
    ], fill=hair_main)
    # 中间刘海一缕 (短)
    d.polygon([
        (cx - s(12), bang_y - s(4)),
        (cx + s(12), bang_y - s(4)),
        (cx + s(6), bang_y + s(18)),
        (cx - s(6), bang_y + s(18)),
    ], fill=hair_shade)

    # 左右两侧鬓发 (从耳朵向下)
    side_top = head_cy - s(4)
    side_bot = head_cy + head_r + s(20)
    for sign in (-1, 1):
        x0 = cx + sign * (head_r - s(4))
        d.ellipse(
            (x0 - s(12), side_top,
             x0 + s(12), side_bot),
            fill=hair_main
        )


def draw_side_tufts(d, cx, head_cy, head_r, hair_main, hair_dark, lift=0):
    """两个小揪揪 (头顶) — proud 时上扬"""
    tuft_y = head_cy - head_r - s(8) - lift
    for sign, off_x in [(-1, s(36)), (1, s(36))]:
        x0 = cx + sign * off_x
        # 揪揪
        d.ellipse(
            (x0 - s(11), tuft_y - s(10),
             x0 + s(11), tuft_y + s(10)),
            fill=hair_main
        )
        # 揪揪根部小阴影
        d.ellipse(
            (x0 - s(7), tuft_y + s(2),
             x0 + s(7), tuft_y + s(8)),
            fill=hair_dark
        )


def draw_eye(d, cx, cy, style, blink_amt=0.0):
    """
    cx, cy: 单只眼中心
    style: closed_arc / half / sharp / happy / worried / proud
    blink_amt: 0=睁开, 1=闭上
    """
    eye_w = s(11)
    eye_h = int(s(12) * (1 - blink_amt))

    # 大睁 → 满版立体眼
    def draw_open(eye_w, eye_h):
        # 外眶
        d.ellipse((cx - eye_w, cy - eye_h, cx + eye_w, cy + eye_h),
                  fill=EYE_OUTLINE)
        # 眼白
        d.ellipse((cx - eye_w + s(1), cy - eye_h + s(1),
                   cx + eye_w - s(1), cy + eye_h - s(1)),
                  fill=EYE_WHITE)
        # 虹膜底色 (深紫)
        d.ellipse((cx - eye_w + s(2), cy - eye_h + s(3),
                   cx + eye_w - s(2), cy + eye_h - s(1)),
                  fill=IRIS_BOT)
        # 虹膜上半 (浅紫)
        d.pieslice((cx - eye_w + s(2), cy - eye_h + s(3),
                    cx + eye_w - s(2), cy + eye_h - s(1)),
                   180, 360, fill=IRIS_TOP)
        # 大高光 (左上)
        hx = cx - s(4)
        hy = cy - s(4)
        d.ellipse((hx - s(3), hy - s(3), hx + s(3), hy + s(3)),
                  fill=EYE_WHITE)
        # 小高光 (右下)
        d.ellipse((cx + s(4), cy + s(3), cx + s(6), cy + s(5)),
                  fill=EYE_WHITE)
        # 上睫毛粗线
        d.line([(cx - eye_w + s(1), cy - eye_h + s(2)),
                (cx + eye_w - s(1), cy - eye_h + s(2))],
               fill=LASH, width=s(2))

    if style == 'closed_arc':
        # 弯月眼 ⌒ (柔和闭眼)
        d.arc((cx - s(11), cy - s(4), cx + s(11), cy + s(10)),
              start=200, end=340, fill=LASH, width=s(2))
        # 一根小睫毛
        d.line([(cx + s(11), cy + s(2)), (cx + s(14), cy - s(1))],
               fill=LASH, width=s(1))

    elif style == 'half':
        # 半闭 (温柔)
        if blink_amt > 0.85:
            d.line([(cx - s(11), cy + s(1)), (cx + s(11), cy + s(1))],
                   fill=LASH, width=s(2))
        else:
            # 上半闭, 露出下半瞳
            draw_open(s(11), s(7))
            # 上眼帘压一刀
            d.rectangle((cx - s(13), cy - s(10), cx + s(13), cy - s(2)),
                        fill=(0, 0, 0, 0))
            # 上盖
            d.arc((cx - s(11), cy - s(7), cx + s(11), cy + s(8)),
                  start=180, end=360, fill=LASH, width=s(2))

    elif style == 'sharp':
        # 一字眼 (专注 — 但还是有点形状)
        if blink_amt > 0.85:
            d.line([(cx - s(10), cy + s(1)), (cx + s(10), cy + s(1))],
                   fill=LASH, width=s(2))
        else:
            # 细长椭圆,强压扁
            ew, eh = s(11), s(5)
            d.ellipse((cx - ew, cy - eh, cx + ew, cy + eh), fill=EYE_OUTLINE)
            d.ellipse((cx - ew + s(1), cy - eh + s(1),
                       cx + ew - s(1), cy + eh - s(1)), fill=IRIS_BOT)
            d.pieslice((cx - ew + s(1), cy - eh + s(1),
                        cx + ew - s(1), cy + eh - s(1)),
                       180, 360, fill=IRIS_TOP)
            d.ellipse((cx - s(3), cy - s(2), cx, cy + s(1)),
                      fill=EYE_WHITE)

    elif style == 'happy':
        # 大笑 ⌒⌒ (上弯)
        d.arc((cx - s(12), cy - s(4), cx + s(12), cy + s(12)),
              start=200, end=340, fill=LASH, width=s(2))
        # 弧上方一道细线 (强调)
        d.arc((cx - s(11), cy - s(2), cx + s(11), cy + s(10)),
              start=210, end=330, fill=LASH, width=s(1))

    elif style == 'worried':
        # 八字眉 + 含泪眼 (大眼睁开,带光)
        if blink_amt > 0.85:
            d.line([(cx - s(10), cy + s(1)), (cx + s(10), cy + s(1))],
                   fill=LASH, width=s(2))
        else:
            draw_open(eye_w, eye_h)
            # 加大高光 (含泪)
            d.ellipse((cx - s(5), cy - s(5), cx + s(1), cy + s(1)),
                      fill=EYE_WHITE)

    elif style == 'proud':
        # > < 自信眼
        # 左眼 (cx 是左眼中心) → 画 >
        d.line([(cx - s(8), cy - s(4)), (cx + s(2), cy + s(2))],
               fill=LASH, width=s(2))
        d.line([(cx + s(2), cy + s(2)), (cx - s(8), cy + s(8))],
               fill=LASH, width=s(2))


def draw_eyes_pair(d, cx, eye_y, style, blink):
    """画一对眼睛, 间距固定"""
    dx = s(20)
    if style == 'proud':
        # > < — 左眼画 >, 右眼画 <
        # 左眼 ">"
        l_cx = cx - dx
        d.line([(l_cx - s(8), eye_y - s(4)), (l_cx + s(2), eye_y + s(2))],
               fill=LASH, width=s(2))
        d.line([(l_cx + s(2), eye_y + s(2)), (l_cx - s(8), eye_y + s(8))],
               fill=LASH, width=s(2))
        # 右眼 "<"
        r_cx = cx + dx
        d.line([(r_cx + s(8), eye_y - s(4)), (r_cx - s(2), eye_y + s(2))],
               fill=LASH, width=s(2))
        d.line([(r_cx - s(2), eye_y + s(2)), (r_cx + s(8), eye_y + s(8))],
               fill=LASH, width=s(2))
        return
    if style == 'worried':
        # 先画八字眉
        for sign in (-1, 1):
            ex = cx + sign * dx
            if sign < 0:
                d.line([(ex - s(11), eye_y - s(11)),
                        (ex + s(7), eye_y - s(15))],
                       fill=LASH, width=s(2))
            else:
                d.line([(ex - s(7), eye_y - s(15)),
                        (ex + s(11), eye_y - s(11))],
                       fill=LASH, width=s(2))
    for sign in (-1, 1):
        draw_eye(d, cx + sign * dx, eye_y, style, blink_amt=blink)


def draw_cheeks(canvas, cx, cy, strength=1.0):
    """腮红, 用单独图层 + GaussianBlur 柔化"""
    if strength < 0.05:
        return
    layer = Image.new('RGBA', (WW, HH), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    alpha = int(160 * strength)
    for sign in (-1, 1):
        x0 = cx + sign * s(28)
        ld.ellipse(
            (x0 - s(11), cy - s(5),
             x0 + s(11), cy + s(5)),
            fill=(*CHEEK, alpha)
        )
    layer = layer.filter(ImageFilter.GaussianBlur(radius=s(2.5)))
    canvas.alpha_composite(layer)


def draw_mouth(d, cx, cy, style):
    if style == 'soft_smile':
        d.arc((cx - s(7), cy - s(3), cx + s(7), cy + s(5)),
              start=20, end=160, fill=MOUTH, width=s(2))

    elif style == 'flat':
        d.line([(cx - s(6), cy), (cx + s(6), cy)],
               fill=MOUTH, width=s(2))

    elif style == 'big_smile':
        # 张嘴大笑 (内部填深色)
        d.pieslice((cx - s(11), cy - s(4), cx + s(11), cy + s(11)),
                   start=0, end=180, fill=MOUTH_INNER)
        d.arc((cx - s(11), cy - s(4), cx + s(11), cy + s(11)),
              start=0, end=180, fill=MOUTH, width=s(2))
        # 内部小舌头
        d.ellipse((cx - s(4), cy + s(2), cx + s(4), cy + s(6)),
                  fill=(255, 130, 150))

    elif style == 'pout':
        # 抿嘴 (倒挂月)
        d.arc((cx - s(7), cy - s(3), cx + s(7), cy + s(5)),
              start=200, end=340, fill=MOUTH, width=s(2))

    elif style == 'smug':
        # 微微上扬 (得意)
        d.arc((cx - s(8), cy - s(2), cx + s(8), cy + s(6)),
              start=180, end=340, fill=MOUTH, width=s(2))


# ============================================================
# 装饰特效 (头顶/周围)
# ============================================================
def draw_extra(d, cx, head_top, t, kind, hair_dark):
    if kind == 'sparkle':
        # 头顶飘小星
        for phase, ox in [(0.0, s(48)), (0.5, -s(58))]:
            tt = (t + phase) % 1
            sy = head_top - s(18) - tt * s(20)
            sx = cx + ox
            alpha = int(255 * (1 - tt))
            sz = s(6)
            # 四角星
            d.polygon([
                (sx, sy - sz), (sx + sz // 3, sy - sz // 3),
                (sx + sz, sy), (sx + sz // 3, sy + sz // 3),
                (sx, sy + sz), (sx - sz // 3, sy + sz // 3),
                (sx - sz, sy), (sx - sz // 3, sy - sz // 3),
            ], fill=(255, 235, 160, alpha))

    elif kind == 'focus_dot':
        # 头顶 ... (思考点 — 闪烁)
        n_dots = 3
        for i in range(n_dots):
            phase = (t + i * 0.15) % 1
            alpha = int(180 + 60 * math.sin(phase * 2 * math.pi))
            dx = (i - 1) * s(8)
            d.ellipse(
                (cx + dx - s(2), head_top - s(20),
                 cx + dx + s(2), head_top - s(16)),
                fill=(*hair_dark, alpha)
            )

    elif kind == 'hearts':
        # 飘心心
        for phase, ox in [(0.0, s(50)), (0.4, -s(58)), (0.7, s(38))]:
            tt = (t * 1.4 + phase) % 1
            hy = head_top + s(20) - tt * s(40)
            hx = cx + ox + math.sin(tt * 4) * s(3)
            alpha = int(255 * (1 - tt))
            hsz = s(7)
            # 心 = 两个圆 + 三角
            d.ellipse((hx - hsz, hy - hsz // 2,
                       hx, hy + hsz // 2),
                      fill=(255, 110, 150, alpha))
            d.ellipse((hx, hy - hsz // 2,
                       hx + hsz, hy + hsz // 2),
                      fill=(255, 110, 150, alpha))
            d.polygon([
                (hx - hsz, hy),
                (hx + hsz, hy),
                (hx, hy + hsz),
            ], fill=(255, 110, 150, alpha))

    elif kind == 'sweat':
        # 汗滴 (从太阳穴落下)
        tt = (t * 1.2) % 1
        sx = cx + s(45)
        sy = head_top + s(8) + tt * s(40)
        alpha = int(255 * (1 - tt * 0.4))
        # 水滴形
        d.ellipse((sx - s(4), sy - s(2),
                   sx + s(4), sy + s(8)),
                  fill=(120, 200, 245, alpha))
        d.polygon([
            (sx - s(2), sy - s(2)),
            (sx + s(2), sy - s(2)),
            (sx, sy - s(7)),
        ], fill=(120, 200, 245, alpha))
        # 高光
        d.ellipse((sx - s(2), sy,
                   sx, sy + s(2)),
                  fill=(255, 255, 255, alpha))

    elif kind == 'star':
        # 头顶大星 (脉动)
        pulse = 1.0 + 0.15 * math.sin(t * 2 * math.pi)
        sz = int(s(11) * pulse)
        sy = head_top - s(8)
        sx = cx
        # 5 角星 (用 polygon 近似)
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            r = sz if i % 2 == 0 else sz // 2
            pts.append((sx + r * math.cos(ang),
                        sy + r * math.sin(ang)))
        d.polygon(pts, fill=(255, 215, 80))
        # 描边
        d.polygon(pts, outline=(220, 170, 50), width=s(1))


# ============================================================
# 一帧总绘制
# ============================================================
def draw_frame(state: str, t: float) -> Image.Image:
    p = PRESETS[state]
    hair_main, hair_shade, hair_dark = HAIR[state]

    canvas = make_canvas()
    d = ImageDraw.Draw(canvas)

    # 动作偏移
    anim = p['anim']
    if anim == 'breathe':
        offset_y = -math.sin(t * 2 * math.pi) * s(2)
        scale = 1.0 + math.sin(t * 2 * math.pi) * 0.015
    elif anim == 'breathe_slow':
        offset_y = -math.sin(t * 2 * math.pi) * s(1.5)
        scale = 1.0
    elif anim == 'minimal':
        offset_y = -math.sin(t * 2 * math.pi) * s(0.5)
        scale = 1.0
    elif anim == 'jump':
        # 上下弹跳
        bounce = abs(math.sin(t * 2 * math.pi))
        offset_y = -bounce * s(10)
        scale = 1.0 + bounce * 0.04
    elif anim == 'tremble':
        offset_y = math.sin(t * 12 * math.pi) * s(1.2)
        offset_x = math.sin(t * 12 * math.pi + 1) * s(0.8)
        scale = 1.0
    elif anim == 'lift':
        # 微仰头 + 缓慢上浮
        offset_y = -math.sin(t * 2 * math.pi) * s(2) - s(1)
        scale = 1.0
    else:
        offset_y = 0
        scale = 1.0

    offset_x = 0
    if anim == 'tremble':
        offset_x = math.sin(t * 12 * math.pi + 1) * s(0.8)

    # 中心点 (略偏上, 给身体留位)
    cx = WW // 2 + int(offset_x)
    head_cy = HH // 2 - s(20) + int(offset_y)
    head_r = int(s(50) * scale)
    head_top = head_cy - head_r

    # 眨眼: 一个周期里有约 6% 时间在闭眼 (约 1.5 秒一次)
    blink_amt = 0.0
    if state in ('focused', 'worried', 'proud'):
        # 这些状态本来眼睛形态固定, 只在 sharp/worried 模式下加眨
        if state in ('focused', 'worried'):
            blink_phase = (t * 1.3) % 1
            if blink_phase < 0.06:
                blink_amt = math.sin(blink_phase / 0.06 * math.pi)
    elif state == 'tender':
        # tender 半闭, 偶尔完全闭一下
        blink_phase = (t * 1.5) % 1
        if blink_phase < 0.1:
            blink_amt = math.sin(blink_phase / 0.1 * math.pi)
        else:
            blink_amt = 0.0
    # idle/happy 是闭眼/笑眼, 不眨

    # ---- 绘制 (后到前) ----
    # 1. 后侧长发
    draw_back_hair(d, cx, head_cy, head_r, hair_main, hair_shade)
    # 2. 身体
    draw_body(d, cx, head_cy, scale, hair_dark)
    # 3. 脖子+脸
    draw_head(d, cx, head_cy, head_r)
    # 4. 前侧头发 (盖额头)
    draw_front_hair(d, cx, head_cy, head_r, hair_main, hair_shade, hair_dark)
    # 5. 头顶揪揪
    lift = s(4) if state == 'proud' else 0
    draw_side_tufts(d, cx, head_cy, head_r, hair_main, hair_dark, lift=lift)
    # 6. 腮红 (柔和)
    cheek_strength = {
        'idle': 0.6, 'tender': 0.5, 'focused': 0.25,
        'happy': 1.0, 'worried': 0.7, 'proud': 0.6,
    }[state]
    cheek_y = head_cy + s(15)
    draw_cheeks(canvas, cx, cheek_y, cheek_strength)
    # 重建 ImageDraw (canvas 被修改过)
    d = ImageDraw.Draw(canvas)
    # 7. 眼睛
    eye_y = head_cy + s(2)
    draw_eyes_pair(d, cx, eye_y, p['eye'], blink_amt)
    # 8. 嘴
    mouth_y = head_cy + s(24)
    draw_mouth(d, cx, mouth_y, p['mouth'])
    # 9. 周围装饰
    if p['extra']:
        draw_extra(d, cx, head_top, t, p['extra'], hair_dark)

    return canvas


# ============================================================
# 后处理: supersample → 缩到目标尺寸 → 二值 alpha (GIF 兼容)
# ============================================================
def finalize(img_ss: Image.Image) -> Image.Image:
    # 高质量缩放
    img = img_ss.resize((W, H), Image.LANCZOS)
    # GIF 透明度只有 1bit, 把半透明像素裁断
    # 阈值 80 — 保留中等透明的边缘 (软一点), 完全透明的丢掉
    r, g, b, a = img.split()
    # 边缘的中等 alpha 提升到不透明, 避免 GIF 显示锯齿带白边
    a = a.point(lambda v: 255 if v >= 80 else 0)
    img = Image.merge('RGBA', (r, g, b, a))
    return img


def to_palette_gif_frame(img: Image.Image) -> Image.Image:
    """转为带 transparency 索引的 P 模式"""
    # 把完全透明区域设为某个特殊颜色 (洋红), 作为 transparent index
    bg = Image.new('RGBA', img.size, (255, 0, 255, 255))
    composed = Image.alpha_composite(bg, img)
    # 转 P 模式 (256 色)
    p = composed.convert('P', palette=Image.ADAPTIVE, colors=255)
    # 找出洋红对应的索引
    palette = p.getpalette()
    transparent_idx = None
    for i in range(0, len(palette), 3):
        r, g, b = palette[i:i + 3]
        if r > 240 and g < 20 and b > 240:
            transparent_idx = i // 3
            break
    if transparent_idx is None:
        transparent_idx = 0
    p.info['transparency'] = transparent_idx
    return p


def make_gif(state: str):
    p = PRESETS[state]
    n_frames = p['frames']
    fps = p['fps']
    duration_ms = max(1, int(1000 / fps))

    out = OUT_DIR / f'{state}.gif'

    frames_p = []
    for i in range(n_frames):
        t = i / n_frames
        frame_ss = draw_frame(state, t)
        frame_final = finalize(frame_ss)
        frame_p = to_palette_gif_frame(frame_final)
        frames_p.append(frame_p)

    frames_p[0].save(
        out,
        save_all=True,
        append_images=frames_p[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        transparency=frames_p[0].info.get('transparency', 0),
        optimize=False,
    )
    size_kb = out.stat().st_size / 1024
    print(f"  ✓ {out.name}  ({n_frames}f @ {fps}fps, {size_kb:.1f} KB)")


def main():
    print(f"生成 6 个状态的真身 GIF -> {OUT_DIR}/")
    for state in PRESETS:
        make_gif(state)
    print("完成。")


if __name__ == "__main__":
    main()
