"""
生成占位 GIF (idle/focused/happy/worried/proud/tender)
每个 GIF: 24 帧, 200x280px, 透明背景
风格: Q 版圆头女孩, 不同状态不同表情和颜色
"""
from PIL import Image, ImageDraw, ImageFilter
import math
from pathlib import Path

OUT_DIR = Path(__file__).parent / 'assets'
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 200, 280
FRAMES = 24
FPS = 12


# 每个状态的视觉参数
PRESETS = {
    'idle': {
        'skin': (255, 220, 200, 255),
        'hair': (180, 150, 220, 255),  # 淡紫
        'cheek': (255, 180, 200, 200),
        'eye_style': 'closed_arc',  # 弯月眼
        'mouth': 'small_smile',
        'breath': 0.04,
        'extra': 'sparkle',
    },
    'tender': {
        'skin': (255, 220, 200, 255),
        'hair': (200, 170, 230, 255),
        'cheek': (255, 180, 200, 220),
        'eye_style': 'closed_arc',
        'mouth': 'small_smile',
        'breath': 0.05,
        'extra': None,
    },
    'focused': {
        'skin': (255, 220, 200, 255),
        'hair': (130, 180, 220, 255),  # 淡蓝
        'cheek': (255, 180, 200, 100),
        'eye_style': 'sharp',  # 一字眼
        'mouth': 'flat',
        'breath': 0.02,
        'extra': 'circle',  # 头顶光圈
    },
    'happy': {
        'skin': (255, 220, 200, 255),
        'hair': (255, 180, 210, 255),  # 粉
        'cheek': (255, 150, 180, 240),
        'eye_style': 'happy',  # ⌒⌒
        'mouth': 'big_smile',
        'breath': 0.10,  # 跳
        'extra': 'hearts',
    },
    'worried': {
        'skin': (255, 220, 200, 255),
        'hair': (255, 200, 130, 255),  # 橙
        'cheek': (255, 180, 200, 180),
        'eye_style': 'worried',  # 八字
        'mouth': 'tight',
        'breath': 0.03,
        'extra': 'sweat',  # 汗滴
    },
    'proud': {
        'skin': (255, 220, 200, 255),
        'hair': (255, 220, 130, 255),  # 金
        'cheek': (255, 180, 200, 200),
        'eye_style': 'proud',  # > <
        'mouth': 'smug',  # 微微上扬
        'breath': 0.04,
        'extra': 'star',
    },
}


def lerp(a, b, t):
    return a + (b - a) * t


def draw_character(state: str, t: float) -> Image.Image:
    """
    画一帧角色。
    t: 0..1 (动画进度,可循环)
    """
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    p = PRESETS[state]

    # 呼吸/弹跳偏移
    breath_amount = p['breath']
    if state == 'happy':
        # 跳: 上下弹
        offset_y = -math.sin(t * math.pi * 2) * 8
        scale = 1.0 + abs(math.sin(t * math.pi * 2)) * breath_amount
    elif state == 'worried':
        # 抖
        offset_y = math.sin(t * math.pi * 8) * 1.5
        scale = 1.0
    else:
        # 呼吸
        offset_y = -math.sin(t * math.pi * 2) * 2
        scale = 1.0 + math.sin(t * math.pi * 2) * breath_amount * 0.3

    cx = W // 2
    base_y = H // 2 + 5 + offset_y

    # ---- 头发外圈 (作为头部轮廓) ----
    head_r = int(58 * scale)
    hair_r = head_r + 8
    d.ellipse(
        (cx - hair_r, base_y - hair_r - 5,
         cx + hair_r, base_y + hair_r - 5),
        fill=p['hair']
    )

    # 头发的两个小揪揪 (头顶)
    tuft_r = 10
    d.ellipse(
        (cx - 35, base_y - hair_r - 8,
         cx - 35 + tuft_r * 2, base_y - hair_r - 8 + tuft_r * 2),
        fill=p['hair']
    )
    d.ellipse(
        (cx + 15, base_y - hair_r - 8,
         cx + 15 + tuft_r * 2, base_y - hair_r - 8 + tuft_r * 2),
        fill=p['hair']
    )

    # ---- 脸 ----
    d.ellipse(
        (cx - head_r, base_y - head_r - 5,
         cx + head_r, base_y + head_r - 5),
        fill=p['skin']
    )

    # 刘海
    hair_color = p['hair']
    bang_top = base_y - head_r - 5
    bang_h = 30
    # 三段刘海
    d.pieslice(
        (cx - head_r, bang_top, cx + head_r, bang_top + bang_h * 2),
        180, 360, fill=hair_color
    )

    # ---- 眼睛 ----
    eye_y = base_y - 5
    eye_dx = 20
    eye_style = p['eye_style']

    # 眨眼:每 1 秒(0.5s 内)闭一下
    blink_phase = (t * 2) % 1
    blinking = blink_phase < 0.08
    # happy 不眨眼,本来就闭着
    if state in ('happy', 'idle', 'tender') and eye_style == 'closed_arc':
        blinking = False  # 已经是闭眼

    eye_color = (60, 40, 80, 255)

    if blinking:
        # 闭眼一字
        for sign in (-1, 1):
            ex = cx + sign * eye_dx
            d.line([(ex - 7, eye_y), (ex + 7, eye_y)], fill=eye_color, width=3)
    elif eye_style == 'closed_arc':
        # 弯月 ⌒(柔和)
        for sign in (-1, 1):
            ex = cx + sign * eye_dx
            d.arc(
                (ex - 9, eye_y - 4, ex + 9, eye_y + 8),
                start=200, end=340,
                fill=eye_color, width=3
            )
    elif eye_style == 'happy':
        # 大笑 ⌒⌒ (向上弯)
        for sign in (-1, 1):
            ex = cx + sign * eye_dx
            d.arc(
                (ex - 10, eye_y - 5, ex + 10, eye_y + 10),
                start=200, end=340,
                fill=eye_color, width=3
            )
    elif eye_style == 'sharp':
        # 一字眼 (专注)
        for sign in (-1, 1):
            ex = cx + sign * eye_dx
            d.line([(ex - 9, eye_y + 1), (ex + 9, eye_y + 1)],
                   fill=eye_color, width=3)
    elif eye_style == 'worried':
        # 八字 (担心)
        for sign in (-1, 1):
            ex = cx + sign * eye_dx
            if sign < 0:
                d.line([(ex - 9, eye_y + 4), (ex + 9, eye_y - 3)],
                       fill=eye_color, width=3)
            else:
                d.line([(ex - 9, eye_y - 3), (ex + 9, eye_y + 4)],
                       fill=eye_color, width=3)
    elif eye_style == 'proud':
        # > < (得意)
        for sign in (-1, 1):
            ex = cx + sign * eye_dx
            if sign < 0:
                d.line([(ex - 7, eye_y - 4), (ex + 7, eye_y + 2)],
                       fill=eye_color, width=3)
                d.line([(ex - 7, eye_y + 2), (ex + 7, eye_y - 4)],
                       fill=eye_color, width=3)
            else:
                d.line([(ex - 7, eye_y + 2), (ex + 7, eye_y - 4)],
                       fill=eye_color, width=3)
                d.line([(ex - 7, eye_y - 4), (ex + 7, eye_y + 2)],
                       fill=eye_color, width=3)

    # ---- 脸颊 ----
    cheek_color = p['cheek']
    cheek_y = base_y + 12
    cheek_dx = 32
    for sign in (-1, 1):
        cx2 = cx + sign * cheek_dx
        d.ellipse(
            (cx2 - 8, cheek_y - 4, cx2 + 8, cheek_y + 4),
            fill=cheek_color
        )

    # ---- 嘴 ----
    mouth_y = base_y + 22
    mouth_style = p['mouth']
    mc = (180, 100, 130, 255)

    if mouth_style == 'small_smile':
        d.arc((cx - 8, mouth_y - 3, cx + 8, mouth_y + 5),
              start=20, end=160, fill=mc, width=2)
    elif mouth_style == 'big_smile':
        d.pieslice((cx - 12, mouth_y - 4, cx + 12, mouth_y + 12),
                   start=0, end=180, fill=(220, 100, 130, 255))
        d.arc((cx - 12, mouth_y - 4, cx + 12, mouth_y + 12),
              start=0, end=180, fill=mc, width=2)
    elif mouth_style == 'flat':
        d.line([(cx - 8, mouth_y), (cx + 8, mouth_y)],
               fill=mc, width=2)
    elif mouth_style == 'tight':
        # 抿嘴小波浪
        d.line([(cx - 8, mouth_y), (cx - 4, mouth_y - 2)],
               fill=mc, width=2)
        d.line([(cx - 4, mouth_y - 2), (cx, mouth_y + 1)],
               fill=mc, width=2)
        d.line([(cx, mouth_y + 1), (cx + 4, mouth_y - 2)],
               fill=mc, width=2)
        d.line([(cx + 4, mouth_y - 2), (cx + 8, mouth_y)],
               fill=mc, width=2)
    elif mouth_style == 'smug':
        # 微微上扬
        d.arc((cx - 9, mouth_y - 4, cx + 9, mouth_y + 4),
              start=200, end=340, fill=mc, width=2)

    # ---- 身体 (简化:小披风/连衣裙下摆) ----
    body_top = base_y + head_r - 5
    body_w = 50
    body_h = 60
    body_color = p['hair']
    # 圆角梯形身体
    d.polygon([
        (cx - body_w // 2, body_top),
        (cx + body_w // 2, body_top),
        (cx + body_w // 2 + 12, body_top + body_h),
        (cx - body_w // 2 - 12, body_top + body_h),
    ], fill=body_color)

    # 脖子上的小白色领子
    d.ellipse(
        (cx - 18, body_top - 5, cx + 18, body_top + 5),
        fill=(255, 255, 255, 255)
    )

    # ---- 状态特殊装饰 ----
    extra = p.get('extra')
    if extra == 'sparkle':
        # 头顶飘小星星
        sp_t = (t + 0.0) % 1
        sp_y = base_y - hair_r - 25 - sp_t * 12
        sp_alpha = int(255 * (1 - sp_t))
        d.text((cx + 45, sp_y), '✦', fill=(255, 230, 150, sp_alpha))
        sp2_t = (t + 0.5) % 1
        sp2_y = base_y - hair_r - 25 - sp2_t * 12
        sp2_alpha = int(255 * (1 - sp2_t))
        d.text((cx - 55, sp2_y), '✧', fill=(200, 220, 255, sp2_alpha))
    elif extra == 'circle':
        # 头顶光圈
        ring_y = base_y - hair_r - 18
        d.ellipse(
            (cx - 30, ring_y - 4, cx + 30, ring_y + 4),
            outline=(255, 230, 100, 200), width=2
        )
    elif extra == 'hearts':
        # 飘心心
        h_t = (t * 1.5) % 1
        h_y = base_y - 30 - h_t * 30
        h_alpha = int(255 * (1 - h_t))
        d.text((cx + 50, h_y), '♥', fill=(255, 100, 150, h_alpha))
        h2_t = (t * 1.5 + 0.4) % 1
        h2_y = base_y - 30 - h2_t * 30
        h2_alpha = int(255 * (1 - h2_t))
        d.text((cx - 60, h2_y), '♥', fill=(255, 150, 200, h2_alpha))
    elif extra == 'sweat':
        # 头侧汗滴(下落)
        sw_t = (t * 1.5) % 1
        sw_y = base_y - 20 + sw_t * 30
        sw_x = cx + head_r + 5
        sw_alpha = int(255 * (1 - sw_t * 0.5))
        d.ellipse(
            (sw_x, sw_y, sw_x + 8, sw_y + 12),
            fill=(150, 200, 240, sw_alpha)
        )
    elif extra == 'star':
        # 头顶 ★
        st_size = 6 + int(math.sin(t * math.pi * 4) * 2)
        st_y = base_y - hair_r - 22
        d.text((cx - st_size, st_y), '★',
               fill=(255, 220, 80, 255))

    return img


def make_gif(state: str, out_path: Path):
    frames = []
    for i in range(FRAMES):
        t = i / FRAMES
        frame = draw_character(state, t)
        frames.append(frame)

    # 保存 GIF (透明背景)
    duration = 1000 // FPS  # ms per frame
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        disposal=2,  # 每帧清掉再画
        transparency=0,
        optimize=False,
    )
    print(f"  ✓ {out_path.name} ({FRAMES} frames, {len(frames[0].tobytes())} bytes/frame)")


def main():
    print(f"生成 6 个状态的占位 GIF -> {OUT_DIR}/")
    for state in PRESETS:
        out = OUT_DIR / f'{state}.gif'
        make_gif(state, out)
    print("完成。")


if __name__ == "__main__":
    main()
