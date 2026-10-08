"""썸네일 스타일 배우기 시험 그림 만들기 (개발용 · 배포 안 됨): python3 tests/make_thumb_style_fixture.py
tests/fixtures/thumb_style/ 에 두 채널 버릇의 썸네일 4장(1280×720 JPG) + 글자·얼굴 상자(boxes.json)를 씀.
- dark_*: 쌈바형 — 어두운 흐린 배경 · 아래쪽 노란 큰 글자 한 줄(글자 높이 약 18%H) · 흰 테두리를 두른 인물 둘(누끼)
- dark_3: 쌈바형 '강조 낱말' — 회색 배경 · 한 줄 안에 노란 강조 낱말 + 흰 글자 · 테두리 없이 아래로 떨어진 그림자 (그림자를 테두리로 세지 않는지)
- bright_*: 토크형 — 밝은 배경 · 큰 얼굴 · 아래쪽 흰 글자 + 노란 둘째 줄(약 14%H) · 검은 테두리
글자 상자는 글자 읽기 모델(PP-OCR)처럼 글자보다 조금 넓게(위아래 0.3배) 적음 — 시험은 모델 없이 이 상자로 잼."""
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "thumb_style"
FONT = str(ROOT / "fonts" / "BlackHanSans-Regular.ttf")
W, H = 1280, 720


def noise_bg(base, spread, seed):
    rnd = random.Random(seed)
    im = Image.new("RGB", (64, 36))
    im.putdata([tuple(max(0, min(255, c + rnd.randint(-spread, spread))) for c in base) for _ in range(64 * 36)])
    return im.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(6))


def text(d, xy, s, size, fill, stroke=0, boxes=None):
    f = ImageFont.truetype(FONT, size)
    d.text(xy, s, font=f, fill=fill, stroke_width=stroke, stroke_fill=(17, 17, 17))
    x0, y0, x1, y1 = d.textbbox(xy, s, font=f, stroke_width=stroke)
    p = (y1 - y0) * 0.3
    if boxes is not None:
        boxes.append([round(max(0, x0 - p) / W, 4), round(max(0, y0 - p) / H, 4), round(min(W, x1 + p) / W, 4), round(min(H, y1 + p) / H, 4)])


def person(d, cx, top, h, outline):
    """흰 테두리를 두른 어두운 사람 모양 (머리 + 몸)."""
    r = h * 0.11
    for k, col in ((outline, (255, 255, 255)), (0, (18, 22, 30))):
        d.ellipse([cx - r - k, top - k, cx + r + k, top + 2 * r + k], fill=col)
        d.rounded_rectangle([cx - h * 0.2 - k, top + 2 * r - k, cx + h * 0.2 + k, top + h + k], radius=40, fill=col)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {}
    for k, words in enumerate([("발바닥 드래그", "#FFE14D"), ("ALA 움직임", "#FFE14D")]):
        im = noise_bg((40, 55, 40), 25, k)
        d = ImageDraw.Draw(im)
        person(d, 640 + 60 * k, 60, 560, 7)
        person(d, 1010, 150, 470, 7)
        boxes = []
        text(d, (60, 440), words[0], 175, words[1], stroke=10 * k, boxes=boxes)
        name = f"dark_{k + 1}.jpg"
        im.save(OUT / name, quality=90)
        meta[name] = {"lines": [{"box": b} for b in boxes], "faces": []}
    im = noise_bg((120, 125, 120), 20, 7)  # dark_3: 그림자만 (테두리 없음) · 한 줄 두 색
    d = ImageDraw.Draw(im)
    person(d, 900, 60, 560, 7)
    f = ImageFont.truetype(FONT, 175)
    sh = Image.new("L", (W, H), 0)
    ds = ImageDraw.Draw(sh)
    x2 = 60 + d.textlength("발바닥", font=f)
    for xy, s in (((60, 440), "발바닥"), ((x2, 440), "드래그")):
        ds.text((xy[0] + 8, xy[1] + 14), s, font=f, fill=230)
    im.paste((10, 10, 10), (0, 0), sh.filter(ImageFilter.GaussianBlur(5)))
    d = ImageDraw.Draw(im)
    d.text((60, 440), "발바닥", font=f, fill="#FFE14D")
    d.text((x2, 440), "드래그", font=f, fill="#FFFFFF")
    x0, y0, _, y1 = d.textbbox((60, 440), "발바닥", font=f)
    _, _, x1, _ = d.textbbox((x2, 440), "드래그", font=f)
    p = (y1 - y0) * 0.3
    im.save(OUT / "dark_3.jpg", quality=90)
    meta["dark_3.jpg"] = {"lines": [{"box": [round(max(0, x0 - p) / W, 4), round(max(0, y0 - p) / H, 4), round(min(W, x1 + p) / W, 4), round(min(H, y1 + p) / H, 4)]}], "faces": []}
    for k, (l1, l2) in enumerate([("이스타 선수 시절", "패싸움 한 썰"), ("국대 출신 코치", "진짜 이야기")]):
        im = noise_bg((215, 205, 190), 18, 10 + k)
        d = ImageDraw.Draw(im)
        d.ellipse([420, 60, 700, 360], fill=(225, 180, 150))  # 큰 얼굴
        boxes = []
        text(d, (120, 450), l1, 112, "#FFFFFF", stroke=9, boxes=boxes)
        text(d, (120, 580), l2, 112, "#FFE14D", stroke=9, boxes=boxes)
        name = f"bright_{k + 1}.jpg"
        im.save(OUT / name, quality=90)
        meta[name] = {"lines": [{"box": b} for b in boxes], "faces": [[420 / W, 60 / H, 280 / W, 300 / H]]}
    (OUT / "boxes.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ok", sorted(meta))


if __name__ == "__main__":
    main()
