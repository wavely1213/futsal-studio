"""AI 추천 썸네일 e2e (개발용 · 배포 안 됨 · 판정 3회차: 예전 thq_test.py 가 지워져 '편집 가능성'을 다시 확인 못 함 → 저장소로).

준비: python3 tests/e2e/mkwork.py <작업폴더> <시험 영상 폴더> [--with-asr] → 그 작업 폴더로 격리 서버(python3 app.py --browser, FUTSAL_PORT)를 띄움.
실행: TH_PORT=<포트> python3 tests/e2e/thq_e2e.py <스크린샷 폴더> [영상 파일 이름 ...]   (마지막 줄 'FAILS: 0' 이면 통과)
판정 4회차: 영상 하나(THQSTOCK002)만 보던 것을 넘긴 영상 모두 × 롱폼·쇼츠로 — 묶음마다 굳은 규칙을 셈 (위반 수를 영상별로 찍음):
크기·게이트·같은 템플릿 ≤2(롱폼·쇼츠 같음)·같은 둘째 줄 없음('속도'='핵심은 속도')·막연·상투 문구 ≤2(구체 문구가 넉넉하면 ≤1)·전술 그래픽 종류 ≤2·
띠형 쇼츠 ≤3·점수 ≥ 하한·같은 질문 머리 묶음에 1번·롱폼+쇼츠 2번·같은 장면 묶음 ≤2(장면 묶음이 3개 넘을 때)·쇼츠 흐린 빈 곳 ≤15%·큰 줄 글자 크기 하한 ·
템플릿 ≥4 · 장면 ≥3 (쓸 만한 장면이 없는 영상(2개만 냄)과 세로 원본 롱폼은 템플릿 ≥3) · 첫 영상만: 카드 편집하기 → 저장 → 다시 열기 같은 그림 · JS 오류 없음."""
import os
import sys
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
NAMES = sys.argv[2:] or ["20261007_THQSTOCK002_1대1 돌파 이렇게 하세요.mp4"]
PORT = os.environ.get("TH_PORT", "8765")
CHROME = os.environ.get("TH_CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
TAC = ("ring", "arrow2", "marker", "pass", "xmark", "spot", "arrow")
BAND = ("쇼츠 · 3단 제목", "쇼츠 · 상자 제목", "쇼츠 · 레터박스 질문", "쇼츠 · 흰 띠 제목 (해주호형)")
fails = []
per_video = {}


def check(cond, msg, key=None):
    print(("  OK  " if cond else "  FAIL") + " " + msg, flush=True)
    if not cond:
        fails.append(msg)
        if key:
            per_video[key] = per_video.get(key, 0) + 1


def bundle(pg, fmt, name):
    key = f"{name.split('_')[1]}_{fmt}"
    r = pg.evaluate(f"window.__thumbAuto({{fmt: '{fmt}', n: 6, noData: true}})")
    check(r.get("ok"), f"{key} 추천 받기 {r.get('error', '')}", key)
    items = r.get("items") or []
    weak = pg.evaluate("!!AI.weak")
    W, H = (1280, 720) if fmt == "long" else (1080, 1920)
    want = 2 if weak else 6
    check(len(items) >= (1 if weak else 4) and len(items) <= 6, f"{key} {len(items)}개 (쓸 만한 장면 없음={weak})", key)
    if len(items) < want:
        print(f"  NOTE {key}: 굳은 규칙을 지키느라 {len(items)}개만 냄", flush=True)
    check(all((x["doc"]["w"], x["doc"]["h"]) == (W, H) for x in items), f"{key} 크기 {W}×{H}", key)
    check(all(not x["gates"] for x in items), f"{key} 게이트 통과 {[x['gates'] for x in items if x['gates']]}", key)
    tpls = [x["tpl"] for x in items]
    vertical = pg.evaluate("frameAspect() < 0.9")
    need_tpl = 3 if (weak or (vertical and fmt == "long")) else 4
    if len(items) >= 4:
        check(len(set(tpls)) >= min(need_tpl, len(items)), f"{key} 템플릿 {len(set(tpls))}종 (≥{need_tpl}) {tpls}", key)
    check(max([tpls.count(t) for t in tpls] or [0]) <= 2, f"{key} 같은 템플릿 ≤2", key)
    groups = pg.evaluate("(() => { const g = sceneGroups(AI.frames); return Object.fromEntries(Object.entries(g).map(([k, v]) => [k, String(v)])); })()")
    sc = [groups.get(str(x["t"]), str(x["t"])) for x in items]
    ngroups = len(set(groups.values()))
    if not weak and len(items) >= 4:
        check(len(set(x["t"] for x in items)) >= min(3, ngroups), f"{key} 장면 {len(set(x['t'] for x in items))}장 (≥3)", key)
    check(max([sc.count(g) for g in sc] or [0]) <= (2 if ngroups >= 3 else 3), f"{key} 같은 장면 묶음 ≤{2 if ngroups >= 3 else 3} {sc}", key)
    l2k = pg.evaluate("items => items.map(c => l2Key(c))", [x["copy"] for x in items])
    check(len(set(l2k)) == len(l2k), f"{key} 같은 둘째 줄 없음 {l2k}", key)
    vs = [x["copy"]["l1"] + "/" + x["copy"]["l2"] for x in items if x["copy"]["vague"] or x["copy"]["stock"]]
    check(len(vs) <= 2, f"{key} 막연·상투 문구 ≤2 {vs}", key)
    floor = pg.evaluate("SCORE_FLOOR")
    check(all(x["score"] >= floor for x in items), f"{key} 점수 ≥{floor} {[x['score'] for x in items]}", key)
    qs = [x["copy"]["l1"].replace(" ", "") for x in items if x["copy"]["l1"].endswith("?")]
    check(len(set(qs)) == len(qs), f"{key} 같은 질문 머리 1번 {qs}", key)
    for x in items:
        kinds = {l.get("shape") for l in x["doc"]["layers"] if l.get("type") == "shape" and l.get("shape") in TAC and l.get("name") != "손그림 화살표"}
        if len(kinds) > 2:
            check(False, f"{key} 전술 그래픽 종류 ≤2: {x['tpl']} {sorted(kinds)}", key)
    check(sum(t in BAND for t in tpls) <= (3 if fmt == "short" else 2), f"{key} 띠형 ≤{3 if fmt == 'short' else 2} ({sum(t in BAND for t in tpls)})", key)
    if fmt == "short":
        bf = pg.evaluate("docs => docs.map(d => blurFill(d))", [x["doc"] for x in items])
        check(all(v <= 0.15 for v in bf), f"{key} 흐린 빈 곳 ≤15% {[round(v, 2) for v in bf]}", key)
    check(all((x.get("bigH") or 1) >= pg.evaluate(f"BIG_MIN['{fmt}']") for x in items), f"{key} 큰 줄 크기 {[round(x.get('bigH') or 0, 3) for x in items]}", key)
    print("   ", [(x["tpl"], x["score"], x["copy"]["l1"] + "/" + x["copy"]["l2"]) for x in items], flush=True)
    return items, qs


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
    def fresh():
        p2 = b.new_page(viewport={"width": 1600, "height": 940})
        p2.on("pageerror", lambda e: errs.append(str(e)))
        p2.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        p2.on("dialog", lambda d: d.accept())
        return p2
    for i, name in enumerate(NAMES):
        print(f"== {name}")
        for attempt in range(3):  # 메모리가 모자란 시험 PC 에서 크롬 탭이 죽으면(Target crashed) 새 탭으로 다시 — 제품 오류가 아님 (오류 기록은 그대로 셈)
            n0, pv0 = len(fails), dict(per_video)
            try:
                pg.goto(f"http://127.0.0.1:{PORT}/thumb?name=" + quote(name))
                pg.wait_for_function("document.getElementById('saved').textContent === '저장됨'", timeout=300000)
                _, q1 = bundle(pg, "long", name)
                _, q2 = bundle(pg, "short", name)
                break
            except Exception as e:
                if "crashed" not in str(e) or attempt == 2:
                    raise
                print("  (탭이 죽어 다시)", flush=True)
                del fails[n0:]
                per_video.clear()
                per_video.update(pv0)
                pg = fresh()
        both = q1 + q2  # noqa: F821
        check(all(both.count(q) <= 2 for q in both), f"{name.split('_')[1]} 같은 질문 머리 롱폼+쇼츠 ≤2 {both}", name.split('_')[1])
    print("== 카드 편집하기 → 저장 → 다시 열기")
    url = f"http://127.0.0.1:{PORT}/thumb?name=" + quote(NAMES[0])
    pg.goto(url)
    pg.wait_for_function("document.getElementById('saved').textContent === '저장됨'", timeout=300000)
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
print("\n위반 수 (영상·형식별):", per_video or "없음")
print(f"\nFAILS: {len(fails)}")
sys.exit(1 if fails else 0)
