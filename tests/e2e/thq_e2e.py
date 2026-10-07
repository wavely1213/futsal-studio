"""AI 추천 썸네일 e2e (개발용 · 배포 안 됨 · 판정 3회차: 예전 thq_test.py 가 지워져 '편집 가능성'을 다시 확인 못 함 → 저장소로).

준비: python3 tests/e2e/mkwork.py <작업폴더> <시험 영상 폴더> → 그 작업 폴더로 격리 서버(python3 app.py --browser, FUTSAL_PORT)를 띄움.
실행: TH_PORT=<포트> python3 tests/e2e/thq_e2e.py <스크린샷 폴더> [영상 파일 이름]   (마지막 줄 'FAILS: 0' 이면 통과)
보는 것: 롱폼·쇼츠 6개(크기·게이트·템플릿 ≥4·장면 ≥3·같은 둘째 줄 1번·막연한 문구 ≤1·전술 그래픽 종류 ≤2·쇼츠 띠형 ≤3) ·
카드 편집하기(레이어 따로) · 저장 → 다시 열기 같은 그림 · JS 오류 없음."""
import os
import sys
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
NAME = sys.argv[2] if len(sys.argv) > 2 else "20261007_THQSTOCK002_1대1 돌파 이렇게 하세요.mp4"
PORT = os.environ.get("TH_PORT", "8765")
CHROME = os.environ.get("TH_CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
TAC = ("ring", "arrow2", "marker", "pass", "xmark", "spot", "arrow")
BAND = ("쇼츠 · 3단 제목", "쇼츠 · 상자 제목", "쇼츠 · 레터박스 질문", "쇼츠 · 흰 띠 제목 (해주호형)")
fails = []


def check(cond, msg):
    print(("  OK  " if cond else "  FAIL") + " " + msg, flush=True)
    if not cond:
        fails.append(msg)


def bundle(pg, fmt):
    r = pg.evaluate(f"window.__thumbAuto({{fmt: '{fmt}', n: 6, noData: true}})")
    check(r.get("ok"), f"{fmt} 추천 받기 {r.get('error', '')}")
    items = r.get("items") or []
    W, H = (1280, 720) if fmt == "long" else (1080, 1920)
    check(len(items) == 6, f"{fmt} 6개 ({len(items)})")
    check(all((x["doc"]["w"], x["doc"]["h"]) == (W, H) for x in items), f"{fmt} 크기 {W}×{H}")
    check(all(not x["gates"] for x in items), f"{fmt} 게이트 통과 {[x['gates'] for x in items if x['gates']]}")
    tpls = [x["tpl"] for x in items]
    check(len(set(tpls)) >= 4, f"{fmt} 템플릿 {len(set(tpls))}종 (≥4) {tpls}")
    cap = 3 if fmt == "short" else 2  # 쇼츠는 띠형 ≤3 을 지키려면 띠 없는 템플릿(장면 위 제목)이 한 번 더 나올 수 있음
    check(max(tpls.count(t) for t in tpls) <= cap, f"{fmt} 같은 템플릿 ≤{cap}")
    check(len({x["t"] for x in items}) >= 3, f"{fmt} 장면 {len({x['t'] for x in items})}장 (≥3)")
    l2s = [x["copy"]["l2"] or x["copy"]["l1"] for x in items]
    check(len(set(l2s)) == len(l2s), f"{fmt} 같은 둘째 줄 없음 {l2s}")
    check(sum(x["copy"]["vague"] for x in items) <= 1, f"{fmt} 막연한·상투 문구 ≤1 {[x['copy']['l1'] + '/' + x['copy']['l2'] for x in items if x['copy']['vague']]}")
    for x in items:
        kinds = {l.get("shape") for l in x["doc"]["layers"] if l.get("type") == "shape" and l.get("shape") in TAC and l.get("name") != "손그림 화살표"}
        if len(kinds) > 2:
            check(False, f"{fmt} 전술 그래픽 종류 ≤2: {x['tpl']} {sorted(kinds)}")
    if fmt == "short":
        check(sum(t in BAND for t in tpls) <= 3, f"쇼츠 띠형 ≤3 ({sum(t in BAND for t in tpls)})")
    print("   ", [(x["tpl"], x["score"], x["copy"]["l1"] + "/" + x["copy"]["l2"]) for x in items], flush=True)
    return items


def pixels(pg):
    return pg.evaluate("""(() => { const c = newCanvas(D.doc.w / 4, D.doc.h / 4); renderDoc(c.getContext('2d'), D.doc, 0.25);
      return Array.from(c.getContext('2d').getImageData(0, 0, c.width, c.height).data.filter((v, i) => i % 4 !== 3)); })()""")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    pg = b.new_page(viewport={"width": 1600, "height": 940})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    pg.on("dialog", lambda d: d.accept())
    url = f"http://127.0.0.1:{PORT}/thumb?name=" + quote(NAME)
    pg.goto(url)
    pg.wait_for_function("document.getElementById('saved').textContent === '저장됨'", timeout=180000)
    print("== 1. 롱폼 AI 추천 6개")
    bundle(pg, "long")
    print("== 2. 쇼츠 AI 추천 6개")
    bundle(pg, "short")
    print("== 3. 카드 편집하기 → 저장 → 다시 열기")
    pg.evaluate("window.__thumbAuto({fmt: 'long', n: 6, noData: true})")
    pg.evaluate("openAI(0)")
    pg.wait_for_timeout(1500)
    names = pg.evaluate("D.doc.layers.map(l => lname(l))")
    check(any(n.startswith("제목") for n in names), f"제목 레이어 따로 {names}")
    check(len(names) == len(set(pg.evaluate("D.doc.layers.map(l => l.id)"))), "레이어마다 id 따로")
    pg.evaluate("Promise.all(D.doc.layers.filter(l => l.type === 'image' && l.src).map(l => imgReady(l.src)))")
    before = pixels(pg)
    n = pg.evaluate("DOCS.designs.length")
    pg.wait_for_function("document.getElementById('saved').textContent === '저장됨'", timeout=60000)
    pg.screenshot(path=os.path.join(OUT, "thq_e2e_card.png"))
    pg.goto(url)
    pg.wait_for_function("document.getElementById('saved').textContent === '저장됨'", timeout=180000)
    check(pg.evaluate("DOCS.designs.length") == n, "저장된 디자인 수 그대로")
    pg.evaluate(f"openDesign({n - 1})")
    pg.wait_for_timeout(800)
    pg.evaluate("Promise.all(D.doc.layers.filter(l => l.type === 'image' && l.src).map(l => imgReady(l.src)))")
    pg.wait_for_timeout(500)
    after = pixels(pg)
    diff = sum(abs(a - c) for a, c in zip(before, after)) / max(1, len(before))
    check(len(before) == len(after) and diff <= 2, f"다시 열기 같은 그림 (평균 차이 {diff:.2f}/255)")
    check(not errs, f"JS 오류 없음 {errs[:5]}")
    b.close()
print(f"\nFAILS: {len(fails)}")
sys.exit(1 if fails else 0)
