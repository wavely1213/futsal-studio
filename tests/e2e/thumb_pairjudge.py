"""짝 판정 (개발용 · 배포 안 됨): 같은 썸네일을 하나만 다르게 만든 두 장을 나란히 보여 주고 고르게 함 — 그림 처리 하나를 견줄 때 기준표 판정(thumb_judge.py, 한 판 ±0.15)보다 예민함.

  python3 tests/e2e/thumb_pairjudge.py <A 폴더> <B 폴더> <출력.json> --diff "<두 장이 무엇이 다른지 한 줄>" [--runs 2] [--seed s] [--files a.jpg,b.jpg]

A·B 폴더에 같은 이름으로 있는 JPG 만 (judge_* 시트는 뺌). 롱폼은 한 시트에 6짝(위아래 줄), 쇼츠는 5짝(가로로). 짝마다 왼쪽·오른쪽은 (씨앗, 회차, 시트)로 섞음.
--diff 는 판정자에게 그대로 알려 줌: 판정 q5 2회차는 '사진 처리만 달라요'라고만 해서 그래픽·엠블럼·제목 자리처럼 배치가 다른 짝에도 틀린 설명이 갔음 (검토 D-108).
결과: 짝마다 B 쪽을 골랐는지(pro_B · subject_B · natural_B) + 까닭 · 마지막에 형식별 B 를 고른 비율. 같은 표본으로 튜닝한 값을 같은 짝으로 재지 말 것."""
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
A, B, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d  # noqa: E731
RUNS, SEED, DIFF = int(arg("--runs", 2)), arg("--seed", "pj1"), arg("--diff", "")
CLAUDE = os.environ.get("CLAUDE_BIN", "/opt/node22/bin/claude")
FB = str(ROOT / "fonts/Pretendard-Black.otf")
if not DIFF:
    sys.exit("--diff 로 두 장이 무엇이 다른지 적어 주세요 (판정자에게 그대로 알려 줌)")
files = sorted(f for f in set(os.listdir(A)) & set(os.listdir(B)) if f.endswith(".jpg") and not f.startswith("judge_"))
if "--files" in sys.argv:
    files = [f for f in files if f in arg("--files", "").split(",")]
PROMPT = """당신은 한국 축구·풋살 유튜브 썸네일을 심사하는 엄격한 전문가예요. 이 폴더의 sheet.jpg 를 Read 도구로 열어 보세요.
줄마다 같은 썸네일을 두 가지로 만든 것이 왼쪽(L)·오른쪽(R)에 있어요 (줄 번호 {nums}). 두 쪽은 장면과 문구가 같고 이것만 달라요: {diff}
줄마다 고르세요:
 pro = 구독자 수만 명인 실제 레슨 채널이 올릴 만한 완성도는 어느 쪽인지 (L 또는 R)
 subject = 누가 주인공인지 더 바로 보이는 쪽 (L 또는 R)
 natural = 합성·가공 티가 덜하고 자연스러운 쪽 (L 또는 R)
차이가 거의 없어도 반드시 하나를 고르고, "why" 에 한 줄로 까닭을 쓰세요.
대답은 JSON 하나만 (설명 없이): {{"1": {{"pro": "L", "subject": "L", "natural": "R", "why": "한 줄"}}, ...}}"""


def tile(p, w):
    im = Image.open(p).convert("RGB")
    return im.resize((w, int(im.size[1] * w / im.size[0])), Image.LANCZOS)


def ask(tmp, prompt):
    cmd = [CLAUDE, "-p", "--output-format", "json", "--model", "opus", "--no-session-persistence", "--tools", "Read", "--permission-mode", "dontAsk"]
    for _ in range(3):
        r = subprocess.run(cmd, input=prompt, cwd=tmp, capture_output=True, text=True, timeout=900)
        try:
            txt = json.loads(r.stdout).get("result", "")
            a, b = txt.find("{"), txt.rfind("}")
            return json.loads(txt[a:b + 1])
        except (ValueError, AttributeError):
            time.sleep(5)
    raise RuntimeError("판정 실패")


def one(run, chunk, ci):
    rnd = random.Random(f"{SEED}-{run}-{ci}")
    rows, flips = [], []
    for f in chunk:
        flip = rnd.random() < 0.5
        flips.append(flip)
        rows.append((os.path.join(B if flip else A, f), os.path.join(A if flip else B, f)))
    short = Image.open(rows[0][0]).size[1] > Image.open(rows[0][0]).size[0]
    w = 300 if short else 520
    ims = [(tile(left, w), tile(right, w)) for left, right in rows]
    h, g = max(a.size[1] for a, _ in ims), 14
    fnt = ImageFont.truetype(FB, 26)
    if short:  # 쇼츠: 짝마다 L R 나란히, 짝은 가로로
        S = Image.new("RGB", (len(ims) * (2 * w + 3 * g) + g, h + 60), (15, 15, 15))
        d = ImageDraw.Draw(S)
        for i, (a, b) in enumerate(ims):
            x = g + i * (2 * w + 3 * g)
            S.paste(a, (x, 50))
            S.paste(b, (x + w + g, 50))
            d.text((x, 10), f"{i + 1}L", fill=(255, 210, 0), font=fnt)
            d.text((x + w + g, 10), f"{i + 1}R", fill=(255, 210, 0), font=fnt)
    else:
        S = Image.new("RGB", (2 * w + 3 * g + 60, len(ims) * (h + g) + g), (15, 15, 15))
        d = ImageDraw.Draw(S)
        for i, (a, b) in enumerate(ims):
            y = g + i * (h + g)
            d.text((8, y + h // 2 - 14), f"{i + 1}", fill=(255, 210, 0), font=fnt)
            S.paste(a, (60, y))
            S.paste(b, (60 + w + g, y))
            d.text((66, y + 4), "L", fill=(255, 210, 0), font=fnt)
            d.text((66 + w + g, y + 4), "R", fill=(255, 210, 0), font=fnt)
    with tempfile.TemporaryDirectory(prefix="pj-") as tmp:
        S.save(f"{tmp}/sheet.jpg", quality=90)
        ans = ask(tmp, PROMPT.format(nums=", ".join(str(i + 1) for i in range(len(ims))), diff=DIFF))
    out = []
    for i, f in enumerate(chunk):
        a = ans.get(str(i + 1)) or {}
        pick_b = lambda k: (a.get(k) == "L") == flips[i]  # noqa: E731,B023 — B 폴더 쪽을 골랐는지
        out.append({"file": f, "run": run, "flip": flips[i], "pro_B": pick_b("pro"), "subject_B": pick_b("subject"), "natural_B": pick_b("natural"), "why": a.get("why", "")})
    return out


longs = [f for f in files if "_long_" in f]
shorts = [f for f in files if "_short_" in f]
chunks = [longs[i:i + 6] for i in range(0, len(longs), 6)] + [shorts[i:i + 5] for i in range(0, len(shorts), 5)]
res = []
with ThreadPoolExecutor(3) as ex:
    for fu in [ex.submit(one, run, c, ci) for run in range(RUNS) for ci, c in enumerate(chunks) if c]:
        try:
            res += fu.result()
        except Exception as e:
            print("ERR", e)
json.dump({"diff": DIFF, "seed": SEED, "pairs": res}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for fmt in ("_long_", "_short_", ""):
    grp = [r for r in res if fmt in r["file"]]
    if grp:
        print(fmt or "all", len(grp), "B pro", round(sum(r["pro_B"] for r in grp) / len(grp), 2), "subject", round(sum(r["subject_B"] for r in grp) / len(grp), 2),
              "natural", round(sum(r["natural_B"] for r in grp) / len(grp), 2))
