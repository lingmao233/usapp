"""生成 PWA 图标（一次性脚本，产物已提交进 public/，仅备查/重新生成用）。

设计：暖纸配色里的深青底（--us-teal）+ 两个相扣的圆环（金 --us-golden / 珊瑚 --us-coral），
象征「我们」两个人的连接。不渲染中文字体，纯图形。

运行：server/.venv-mac/bin/python scripts/generate_icons.py
（pillow 只用于本脚本，不进 requirements.txt）
"""
from pathlib import Path

from PIL import Image, ImageDraw

# 与 src/index.css 的 --us-* 保持一致
TEAL = "#264653"
GOLDEN = "#E9C46A"
CORAL = "#F4A261"

OUT_DIR = Path(__file__).resolve().parent.parent / "public"
SS = 4  # 超采样倍数，抗锯齿


def _draw(size: int, rounded: bool, safe_zone: bool) -> Image.Image:
    """画一个图标：深青底 + 相扣双环。

    rounded: 圆角矩形底（purpose=any）；否则满血方底（maskable / apple-touch-icon，
    圆角交给系统裁）。
    safe_zone: 内容收进中心 80% 安全区（maskable 要求）。
    """
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if rounded:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=TEAL)
    else:
        d.rectangle([0, 0, s - 1, s - 1], fill=TEAL)

    # 双环几何：环半径与中心偏移按图标尺寸等比
    scale = 0.8 if safe_zone else 1.0
    r = s * 0.17 * scale
    dx = s * 0.125 * scale
    width = int(s * 0.085 * scale)
    cy = s / 2
    for cx, color in ((s / 2 - dx, GOLDEN), (s / 2 + dx, CORAL)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    targets = [
        ("icon-192.png", 192, True, False),
        ("icon-512.png", 512, True, False),
        ("icon-maskable-192.png", 192, False, True),
        ("icon-maskable-512.png", 512, False, True),
        # iOS 主屏幕图标：满血方底，系统自行裁圆角
        ("apple-touch-icon.png", 180, False, False),
    ]
    for name, size, rounded, safe_zone in targets:
        _draw(size, rounded, safe_zone).save(OUT_DIR / name, "PNG")
        print(f"生成 {name} ({size}x{size})")


if __name__ == "__main__":
    main()
