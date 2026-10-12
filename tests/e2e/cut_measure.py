"""'자동' 탭 누끼 자르기 측정 (개발용 · 배포 안 됨 · unittest 가 줍지 않음 · D-270): 예전 템플릿 후보(makeCands)에 '인물 누끼 넣어서 만들기' 누끼를 넣고
레이어마다 주인공 머리(headBox)가 화면에 보이는 비율을 잼 — 배경 칸(frame) · 배경과 같은 자리 누끼(cut_twin) · 따로 놓인 누끼(cut_free).

  python3 tests/e2e/cut_measure.py <포트> <출력.json> <영상 파일 이름...>     (격리 서버 · 작업 폴더는 mkwork.py)
  python3 tests/e2e/cut_measure.py --compare <전.json> <후.json>                  (두 판 견주기 · 칸 수·95% 넘게·절반도 안 보임·하나도 안 보임)

장면 순서를 0~5 만큼 돌려(좋은 장면 i 가 첫 장면인 영상처럼 · rot 0 이 실제 화면) 표본을 늘림. 누끼 그림은 따지 않음 (칸 계산만 · 누끼 주소는 가짜).
주인공 머리가 원본에서 잘린 장면은 셈에서 뺌. 두 판은 같은 작업 폴더(장면 후보 캐시)로 차례로 띄워 잼."""
import json
import sys
import time
from collections import defaultdict
from urllib.parse import quote

JS = r"""(() => {
  const ar = frameAspect(), out = [], orig = FRAMES.slice(), cut0 = CUT_AUTO, fmt0 = AUTO_FMT, N = Math.min(6, orig.length);
  try {
    for (const fmt of ["long", "short"]) for (let i = 0; i < N; i++) {
      FRAMES = [...orig.slice(i), ...orig.slice(0, i)]; AUTO_FMT = fmt;
      CUT_AUTO = { cut: "/assets/cut_measure.png", src: frameSrc(FRAMES[0].t) };  // 칸 계산만 · 누끼 주소는 /frame 이 아니어야 누끼로 다룸
      makeCands();
      const W = FMT[fmt].w, H = FMT[fmt].h;
      CANDS.forEach((c, ci) => {
        const bgs = c.doc.layers.filter(l => l.type === "image" && frameT(l.src) != null);
        for (const l of c.doc.layers) {
          if (l.type !== "image") continue;
          const isCut = l.name === "누끼" && frameT(l.orig) != null, t = isCut ? frameT(l.orig) : frameT(l.src);
          if (t == null) continue;
          const f = frameMeta(t), hb = f && headBox(f); if (!hb) continue;
          const twin = isCut && bgs.some(g => g.src === l.orig && g.x === l.x && g.y === l.y && g.w === l.w && g.h === l.h);
          const b = boxC(l, hb, ar), view = clipTo({ x: l.x, y: l.y, w: l.w, h: l.h }, { x: 0, y: 0, w: W, h: H });
          const vis = areaOf(b) > 0 ? areaOf(clipTo(b, view)) / areaOf(b) : 0;
          out.push({ fmt, rot: i, tpl: c.name, k: ci % 2, kind: isCut ? (twin ? "cut_twin" : "cut_free") : "frame", vis: Math.round(vis * 1000) / 1000,
                     edge: b.x < W * 0.08 || b.x + b.w > W * 0.92, x: Math.round(l.x), fx: Math.round((l.fx ?? 0.5) * 1000) / 1000, t });
        }
      });
    }
  } finally { FRAMES = orig; CUT_AUTO = cut0; AUTO_FMT = fmt0; makeCands(); }
  return out;
})()"""


def measure(port, out, names):
    from playwright.sync_api import sync_playwright
    res = {}
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
        for name in names:
            pg = b.new_page(viewport={"width": 1500, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(f"http://127.0.0.1:{port}/thumb?name=" + quote(name))
            pg.wait_for_function("document.getElementById('saved').textContent === '저장됨'", timeout=120000)
            t0 = time.time()
            pg.wait_for_function("FRAMES.length > 0 && CANDS.length > 0", timeout=1800000)
            rows = pg.evaluate(JS)
            res[name] = {"rows": rows, "frames": pg.evaluate("FRAMES.length"), "errors": errs}
            print(name, len(rows), "칸", f"{time.time() - t0:.0f}s", "JS 오류", errs[:2], flush=True)
            pg.close()
        b.close()
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)


def stats(rows):
    n = len(rows)
    return {"n": n, "ge95": sum(r["vis"] >= 0.95 for r in rows), "lt50": sum(r["vis"] < 0.5 for r in rows), "zero": sum(r["vis"] <= 0.001 for r in rows),
            "edge": sum(bool(r.get("edge")) for r in rows)}


def compare(pa, pb):
    A, B = (json.load(open(p, encoding="utf-8")) for p in (pa, pb))
    line = lambda s: f"{s['n']:4d}칸 · 95%↑ {s['ge95']:4d} ({100 * s['ge95'] / max(1, s['n']):.1f}%) · 50%↓ {s['lt50']:3d} · 하나도 안 보임 {s['zero']:3d} · 옆 끝 {s['edge']:3d}"  # noqa: E731
    for label, sel in (("실제 화면 (rot 0)", lambda r: r["rot"] == 0), ("장면 순서 돌림 포함 (rot 0~5)", lambda r: True)):
        print("==", label)
        for kind in ("cut_free", "cut_twin", "frame"):
            for tag, D in (("전", A), ("후", B)):
                print(f"  {kind:9s} {tag} {line(stats([r for v in D.values() for r in v['rows'] if r['kind'] == kind and sel(r)]))}")
    print("== 템플릿별 짝 없는 누끼 (rot 0~5): 50%↓ 전 → 후 / 칸 · 95%↑ 전 → 후")
    by = defaultdict(lambda: [[], []])
    for i, D in enumerate((A, B)):
        for v in D.values():
            for r in v["rows"]:
                if r["kind"] == "cut_free":
                    by[(r["fmt"], r["tpl"], r["k"])][i].append(r)
    for key in sorted(by):
        sa, sb = stats(by[key][0]), stats(by[key][1])
        print(f"  {key[0]:5s} {key[1]:22s} k={key[2]}  {sa['lt50']:3d} → {sb['lt50']:3d} / {sa['n']:3d}   95%↑ {sa['ge95']:3d} → {sb['ge95']:3d}")


if __name__ == "__main__":
    if sys.argv[1] == "--compare":
        compare(sys.argv[2], sys.argv[3])
    else:
        measure(sys.argv[1], sys.argv[2], sys.argv[3:])
