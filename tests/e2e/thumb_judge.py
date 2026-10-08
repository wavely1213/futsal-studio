"""AI 추천 썸네일 블라인드 판정 (개발용 · 배포 안 됨 · unittest 가 줍지 않음) — 기준표 C (판정 q5 회차들과 같은 프롬프트).

  python3 tests/e2e/thumb_judge.py <결과 폴더> --refs-long <롱폼 레퍼런스 폴더> --refs-short <쇼츠 표지 폴더> [--runs 2] [--jobs 3] [--seed s] [--only KEY,...]
                                  [--refs-label "쪼살·쌈바 풋살 클래스·풋살해주호·JK 아트사커"] [--save] [--agg]

<결과 폴더>: 영상마다 추천 6개 JPG + results.json (키 '<영상>_long|short' → items[{file, tpl, ...}]) — 격리 서버에서 window.__thumbAuto 로 받은 것.
묶음마다 우리 것 + 레퍼런스 6장을 섞어 A~L 라벨 → 큰 시트(롱폼 400px · 쇼츠 270px)와 휴대폰 시트(168px · 110px) 두 장을 빈 임시 폴더에 두고
Claude Code CLI(`claude -p`, Read 도구만)에게 7항목 1~10점 + '실제 프로 채널 것' 6장 고르기를 시킴. 라벨 순서·레퍼런스 표본은 (씨앗, 묶음, 회차)로 정함.
결과: judge_raw.json (판정 원문) · --agg 면 judge.json (항목·묶음·형식별 평균, 프로로 고른 비율) 도 씀.
레퍼런스 그림은 저장소에 넣지 않는다 (채널 썸네일 저작권 · docs/TESTING_GUIDELINES.md). 같은 표본으로 튜닝한 값은 같은 표본으로 재지 말 것 — 확인은 다른 레퍼런스 표본(--refs-*)·다른 씨앗으로."""
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d  # noqa: E731
CLAUDE = os.environ.get("CLAUDE_BIN", "/opt/node22/bin/claude")
FB = str(ROOT / "fonts/Pretendard-Black.otf")
KEYS = ("readability", "contrast", "hierarchy", "appeal", "pro_level", "scene", "crop")
K5 = ("readability", "contrast", "hierarchy", "appeal", "pro_level")
PROMPT = """당신은 한국 축구·풋살 유튜브 썸네일을 심사하는 아주 엄격한 전문가예요. 이 폴더의 그림 두 장을 Read 도구로 열어 보세요.
- sheet_big.jpg: {kind} 썸네일 {n}개 (각 칸 왼쪽 위 노란 라벨 {labels})
- sheet_small.jpg: 같은 썸네일을 휴대폰 목록 크기({small}px 폭)로 줄인 것 (라벨 같음)
이 중 일부는 구독자 수만~수십만 명인 실제 한국 풋살·축구 레슨 채널({refs})이 올린 썸네일이고, 나머지는 자동 생성 프로그램이 만든 것이에요. 몇 개가 어느 쪽인지는 알려 주지 않아요.
각 썸네일을 1~10점으로 매기세요 (5 = 평범한 아마추어, 7 = 괜찮은 소규모 채널, 8 = 구독자 수만 명 레슨 채널 수준, 10 = 최상위). 관대하게 주지 마세요:
 readability = sheet_small 크기에서 큰 제목을 바로 읽을 수 있는지 · contrast = 글자·주요 대상이 배경과 또렷이 구분되는지
 hierarchy = 무엇을 먼저 읽는지 분명한지 · appeal = 클릭하고 싶은지(문구의 호기심·장면의 힘) · pro_level = 잘 되는 레슨 채널이 실제로 올릴 만한 완성도인지(어색한 합성·문구·잘린 사람·흐린 화질·어색한 그래픽은 크게 감점)
 scene = 고른 장면 자체의 힘(주인공이 누구인지 바로 보이는지·얼굴/표정·동작의 절정·선명함·배경이 깔끔한지) · crop = 자르기·배치(머리·발이 어색하게 잘리지 않음·주인공 크기와 위치·머리 위 여백·글자가 사람을 가리지 않음)
그리고 실제 프로 채널이 올린 것이라고 생각하는 썸네일 6개를 "pro" 에 라벨로 고르세요.
"why" 에는 그 썸네일의 가장 큰 약점(없으면 강점)을 한 줄로, "fix" 에는 가장 효과가 클 고칠 점 하나를 구체적으로(크기·위치·색·문구 등) 쓰세요.
대답은 JSON 하나만 (설명 없이): {{"A": {{"readability": 0, "contrast": 0, "hierarchy": 0, "appeal": 0, "pro_level": 0, "scene": 0, "crop": 0, "why": "한 줄", "fix": "한 줄"}}, ..., "pro": ["A", "B", "C", "D", "E", "F"]}}"""


def tile(p, w, h):
    im = Image.open(p).convert("RGB")
    if abs(im.size[0] / im.size[1] - 4 / 3) < 0.05:  # 4:3 레터박스 → 16:9
        im = im.crop((0, int(im.size[1] * 0.125), im.size[0], int(im.size[1] * 0.875)))
    return im.resize((w, h), Image.LANCZOS)


def sheet(paths, labels, w, h, cols, out):
    rows = (len(paths) + cols - 1) // cols
    g = 10
    im = Image.new("RGB", (cols * (w + g) + g, rows * (h + g) + g), (15, 15, 15))
    d = ImageDraw.Draw(im)
    big = w > 200
    fs = ImageFont.truetype(FB, 22 if big else 13)
    for i, (p, lab) in enumerate(zip(paths, labels)):
        x, y = g + (i % cols) * (w + g), g + (i // cols) * (h + g)
        im.paste(tile(p, w, h), (x, y))
        s = 28 if big else 16
        d.rectangle([x, y, x + s, y + s], fill=(255, 210, 0))
        d.text((x + (6 if big else 3), y + 1), lab, fill=(0, 0, 0), font=fs)
    im.save(out, quality=92)


def ask(tmp, prompt):
    cmd = [CLAUDE, "-p", "--output-format", "json", "--model", "opus", "--no-session-persistence", "--tools", "Read", "--permission-mode", "dontAsk"]
    last = ""
    for _ in range(3):
        r = subprocess.run(cmd, input=prompt, cwd=tmp, capture_output=True, text=True, timeout=900)
        last = (r.stderr or "") + (r.stdout or "")
        try:
            txt = json.loads(r.stdout).get("result", "")
            a, b = txt.find("{"), txt.rfind("}")
            return json.loads(txt[a:b + 1])
        except (ValueError, AttributeError):
            time.sleep(5)
    raise RuntimeError("판정 실패: " + last[-400:])


def run_one(res_dir, key, v, run, refs_long, refs_short, seed, label, save):
    short = v["fmt"] == "short"
    ours = [res_dir / it["file"] for it in v["items"]]
    rnd = random.Random(f"{seed}-{key}-{run}")
    pool = refs_short if short else refs_long
    refs = rnd.sample(pool, min(6, len(pool)))
    items = [(p, "ours") for p in ours] + [(p, "ref") for p in refs]
    rnd.shuffle(items)
    labels = [chr(65 + i) for i in range(len(items))]
    with tempfile.TemporaryDirectory(prefix="thj-") as tmp:
        paths = [p for p, _ in items]
        if short:
            sheet(paths, labels, 270, 480, 6, f"{tmp}/sheet_big.jpg")
            sheet(paths, labels, 110, 196, 6, f"{tmp}/sheet_small.jpg")
        else:
            sheet(paths, labels, 400, 225, 4, f"{tmp}/sheet_big.jpg")
            sheet(paths, labels, 168, 95, 4, f"{tmp}/sheet_small.jpg")
        if save:
            for nm in ("sheet_big.jpg", "sheet_small.jpg"):
                Image.open(f"{tmp}/{nm}").save(res_dir / f"judge_{key}_r{run}_{nm}")
        prompt = PROMPT.format(kind="쇼츠 표지(9:16)" if short else "롱폼(16:9)", n=len(items), labels=f"{labels[0]}~{labels[-1]}", small=110 if short else 168, refs=label)
        t0 = time.time()
        ans = ask(tmp, prompt)
    return {"key": key, "run": run, "sec": round(time.time() - t0), "seed": seed, "map": [(str(p.name), src, lab) for (p, src), lab in zip(items, labels)], "ans": ans}


def aggregate(res_dir):
    raw = json.load(open(res_dir / "judge_raw.json", encoding="utf-8"))
    per = defaultdict(lambda: {"src": None, "key": None, "s": [], "why": [], "fix": [], "pro": 0, "runs": 0})
    hit = defaultdict(list)
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    for r in raw:
        ans, picked = r["ans"], set(r["ans"].get("pro") or [])
        nours, h = sum(1 for _, src, _ in r["map"] if src == "ours"), 0
        for name, src, lab in r["map"]:
            sc = ans.get(lab) or {}
            try:
                vals = {k: float(sc.get(k, sc.get("pro_level"))) for k in KEYS}
            except (TypeError, ValueError):
                continue
            p = per[(r["key"], name)]
            p.update(src=src, key=r["key"])
            p["s"].append(vals)
            p["why"].append(sc.get("why", ""))
            p["fix"].append(sc.get("fix", ""))
            p["runs"] += 1
            if lab in picked:
                p["pro"] += 1
                h += src == "ours"
        hit[r["key"]].append(h / max(1, nours))
    out = {"items": {}, "summary": {}}
    for (_k, name), p in per.items():
        m = {k: mean([s[k] for s in p["s"]]) for k in KEYS}
        m["total"] = mean([sum(s[k] for k in K5) / 5 for s in p["s"]])
        out["items"][f'{p["key"]}|{name}'] = dict(m, src=p["src"], key=p["key"], file=name, pro=p["pro"], runs=p["runs"], why=p["why"], fix=p["fix"])
    for fmt in ("long", "short", "all"):
        for src in ("ours", "ref"):
            xs = [v for v in out["items"].values() if v["src"] == src and (fmt == "all" or v["key"].endswith(fmt))]
            out["summary"][f"{fmt}_{src}"] = dict({k: mean([v[k] for v in xs]) for k in KEYS + ("total",)}, n=len(xs), picked=sum(v["pro"] for v in xs), shown=sum(v["runs"] for v in xs))
    json.dump(out, open(res_dir / "judge.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k, s in out["summary"].items():
        print(k, json.dumps(s, ensure_ascii=False))


def main():
    res_dir = Path(sys.argv[1])
    if "--agg" in sys.argv and "--refs-long" not in sys.argv:
        return aggregate(res_dir)
    refs_long = sorted(Path(arg("--refs-long", "")).glob("*.jpg"))
    refs_short = sorted(Path(arg("--refs-short", "")).glob("*.jpg"))
    runs, jobs, seed = int(arg("--runs", 2)), int(arg("--jobs", 3)), arg("--seed", "thj1")
    label = arg("--refs-label", "쪼살·쌈바 풋살 클래스·풋살해주호·JK 아트사커")
    only = set(arg("--only", "").split(",")) - {""}
    res = json.load(open(res_dir / "results.json", encoding="utf-8"))
    raw_path = res_dir / "judge_raw.json"
    raw = json.load(open(raw_path, encoding="utf-8")) if raw_path.exists() else []
    done = {(r["key"], r["run"]) for r in raw}
    todo = [(k, v, run) for k, v in sorted(res.items()) if v.get("items") and (not only or k in only) for run in range(runs) if (k, run) not in done]
    with ThreadPoolExecutor(jobs) as ex:
        for fut in [ex.submit(run_one, res_dir, k, v, run, refs_long, refs_short, seed, label, "--save" in sys.argv) for k, v, run in todo]:
            try:
                r = fut.result()
            except Exception as e:  # 한 묶음이 실패해도 나머지는 이어서 (다시 돌리면 빠진 것만)
                print("ERR", e, flush=True)
                continue
            raw.append(r)
            json.dump(raw, open(raw_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("done", r["key"], r["run"], r["sec"], "s", flush=True)
    if "--agg" in sys.argv:
        aggregate(res_dir)


if __name__ == "__main__":
    main()
