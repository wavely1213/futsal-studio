"""AI 추천 썸네일 묶음 받기 (개발용 · 배포 안 됨 · unittest 가 줍지 않음): 격리 서버에서 영상마다 롱폼·쇼츠 추천 6개를 받아 JPG + results.json.

  python3 tests/e2e/thumb_gen.py <포트> <출력 폴더> <영상 파일 이름...>

서버는 mkwork.py 로 만든 작업 폴더로 띄우고, 두 판을 견줄 때는 FUTSAL_CLAUDE=/nonexistent (규칙 문구 · 결과가 매번 같음 — D-067).
results.json: '<영상>_long|short' → {items[{file, tpl, t, copy, score, raw, why, gates, frame, doc}], sec, frames(AI.frames), cuts, weak, dbg, errors}.
이미 받은 묶음은 건너뜀 (지운 키만 다시). 판정은 thumb_judge.py · 짝 판정은 thumb_pairjudge.py."""
import base64
import json
import os
import sys
import time
from urllib.parse import quote
from playwright.sync_api import sync_playwright

port, out = sys.argv[1], sys.argv[2]
names = sys.argv[3:]
os.makedirs(out, exist_ok=True)
res_path = os.path.join(out, "results.json")
allres = json.load(open(res_path, encoding="utf-8")) if os.path.exists(res_path) else {}
CHROME = os.environ.get("TH_CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")


def one(b, name):
    vid = name.split("_")[1]
    pg = b.new_page(viewport={"width": 1600, "height": 940})
    errs = []
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: d.accept())
    try:
        pg.goto(f"http://127.0.0.1:{port}/thumb?name=" + quote(name))
        pg.wait_for_function("document.getElementById('saved').textContent === '저장됨'", timeout=600000)
        for fmt in ("long", "short"):
            key = f"{vid}_{fmt}"
            if key in allres and allres[key].get("items"):
                continue
            t0 = time.time()
            r = pg.evaluate(f"window.__thumbAuto({{fmt: '{fmt}', n: 6}})")
            dt = time.time() - t0
            if not r.get("ok"):
                print("FAIL", name, fmt, r, flush=True)
                continue
            items = []
            for i, it in enumerate(r["items"]):
                fn = f"{vid}_{fmt}_{i + 1}.jpg"
                open(os.path.join(out, fn), "wb").write(base64.b64decode(it["data"].split(",", 1)[1]))
                it.pop("data")
                it["file"] = fn
                it["video"] = name
                items.append(it)
            extra = pg.evaluate("""(() => ({ info: { width: INFO.width, height: INFO.height, duration: INFO.duration },
              frames: AI.frames, cuts: Object.fromEntries(Object.entries(AI.cuts).map(([k, v]) => [k, { q: v.q || null, cut: v.cut, src: v.src }])),
              srcCap: srcCap(), weak: !!AI.weak, dbg: AI.dbg || null }))()""")
            allres[key] = {"video": name, "fmt": fmt, "sec": round(dt, 1), "items": items, "errors": errs[:], **extra}
            json.dump(allres, open(res_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"{vid} {fmt} {dt:.1f}s frames={len(extra['frames'])}", [(x["tpl"], x["score"], x["copy"]["l1"] + "/" + x["copy"]["l2"]) for x in items], flush=True)
    finally:
        try:
            pg.close()
        except Exception:
            pass


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    for name in names:
        for attempt in range(3):
            try:
                one(b, name)
                break
            except Exception as e:
                print("RETRY", name, str(e)[:300], flush=True)
                try:
                    b.close()
                except Exception:
                    pass
                b = p.chromium.launch(executable_path=CHROME)
    b.close()
json.dump(allres, open(res_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("done")
