"""'자동' 탭 누끼 자르기 측정 (개발용 · 배포 안 됨 · unittest 가 줍지 않음 · D-270·D-273): 예전 템플릿 후보(makeCands)에 '인물 누끼 넣어서 만들기' 누끼를 넣고
레이어마다 주인공 머리(headBox)가 화면에 보이는 비율을 잼 — 배경 칸(frame) · 배경과 같은 자리 누끼(cut_twin) · 따로 놓인 누끼(cut_free).
누끼 레이어는 화면 안(캔버스 끝 1px 안쪽)에 들어온 레이어 끝(= 원본 테두리·자른 선)도 적음(sides) — 그 끝에 사람이 닿아 있으면 흰 테두리 친 곧은 선으로 잘려 보임 (D-273).

  python3 tests/e2e/cut_measure.py <포트> <출력.json> <영상 파일 이름...>          (격리 서버 · 작업 폴더는 mkwork.py · 누끼 주소는 가짜 · 칸 계산만)
  python3 tests/e2e/cut_measure.py --real <포트> <출력.json> <영상 파일 이름...>   (장면마다 실제 누끼('fast')를 따서 화면 안 레이어 끝의 불투명 비율도 잼 · 느림)
  python3 tests/e2e/cut_measure.py --compare <전.json> <후.json>                       (두 판 견주기 · 칸 수·95% 넘게·절반도 안 보임·하나도 안 보임 · 새로 화면 안에 들어온 누끼 끝)

장면 순서를 0~5 만큼 돌려(좋은 장면 i 가 첫 장면인 영상처럼 · rot 0 이 실제 화면) 표본을 늘림.
주인공 머리가 원본에서 잘린 장면은 머리 셈에서 뺌(누끼 끝은 셈). 두 판은 같은 작업 폴더(장면 후보 캐시)로 차례로 띄워 잼."""
import json
import sys
import time
from collections import defaultdict
from urllib.parse import quote

JS = r"""async (real) => {
  const ar = frameAspect(), out = [], orig = FRAMES.slice(), cut0 = CUT_AUTO, fmt0 = AUTO_FMT, N = Math.min(6, orig.length), cuts = {};
  const sidesOf = (l, W, H, a) => {  // 화면 안에 들어온 레이어 끝 (cover: 보이는 그림 = 레이어 ∩ 그림 사각형) → { left: 불투명 비율 | null, ... }
    const r = imgRect(l, ar), ix0 = l.x + r.dx, iy0 = l.y + r.dy, ix1 = ix0 + r.dw, iy1 = iy0 + r.dh;
    const v = [Math.max(ix0, l.x), Math.max(iy0, l.y), Math.min(ix1, l.x + l.w), Math.min(iy1, l.y + l.h)], o = {};
    const scan = (x0, y0, x1, y1) => {
      if (!a) return null;
      let n = 0, hit = 0;
      for (let k = 0; k < 400; k++) {
        const X = x0 + (x1 - x0) * k / 399, Y = y0 + (y1 - y0) * k / 399; if (X < 0 || X >= W || Y < 0 || Y >= H) continue;
        let u = (X - ix0) / r.dw; if (l.flipX) u = 1 - u; u = u * r.cw + (l.cropL || 0); const vv = (Y - iy0) / r.dh * r.ch + (l.cropT || 0);
        n++; if (a.d[(Math.min(a.h - 1, Math.max(0, Math.floor(vv * a.h))) * a.w + Math.min(a.w - 1, Math.max(0, Math.floor(u * a.w)))) * 4 + 3] > 128) hit++;
      }
      return n ? Math.round(1000 * hit / n) / 1000 : 0;
    };
    if (v[0] > 1 && v[0] < W - 1) o.left = scan(v[0] + 0.5, v[1], v[0] + 0.5, v[3] - 1);
    if (v[2] > 1 && v[2] < W - 1) o.right = scan(v[2] - 0.5, v[1], v[2] - 0.5, v[3] - 1);
    if (v[1] > 1 && v[1] < H - 1) o.top = scan(v[0], v[1] + 0.5, v[2] - 1, v[1] + 0.5);
    if (v[3] > 1 && v[3] < H - 1) o.bottom = scan(v[0], v[3] - 0.5, v[2] - 1, v[3] - 0.5);
    return o;
  };
  try {
    for (const fmt of ["long", "short"]) for (let i = 0; i < N; i++) {
      FRAMES = [...orig.slice(i), ...orig.slice(0, i)]; AUTO_FMT = fmt;
      let alpha = null;
      if (real) {  // 실제 누끼 (장면마다 한 번)
        const t0 = FRAMES[0].t;
        if (!(t0 in cuts)) {
          const j = await post("/api/thumb/cut", { src: frameSrc(t0), kind: "fast" });
          const r = j.ok ? await watchJob(j.jobId) : null;
          let a = null;
          if (r && r.cut && await imgReady(r.cut)) {
            const im = img(r.cut), w = im.naturalWidth, h = im.naturalHeight, c = newCanvas(w, h), x = c.getContext("2d");
            x.drawImage(im, 0, 0); a = { w, h, d: x.getImageData(0, 0, w, h).data };
          }
          cuts[t0] = r && a ? { r, a } : null;
        }
        if (!cuts[t0]) continue;
        CUT_AUTO = cuts[t0].r; alpha = cuts[t0].a;
      } else CUT_AUTO = { cut: "/assets/cut_measure.png", src: frameSrc(FRAMES[0].t) };  // 칸 계산만 · 누끼 주소는 /frame 이 아니어야 누끼로 다룸
      makeCands();
      const W = FMT[fmt].w, H = FMT[fmt].h;
      CANDS.forEach((c, ci) => {
        const bgs = c.doc.layers.filter(l => l.type === "image" && frameT(l.src) != null);
        c.doc.layers.forEach((l, li) => {
          if (l.type !== "image") return;
          const isCut = l.name === "누끼" && frameT(l.orig) != null, t = isCut ? frameT(l.orig) : frameT(l.src);
          if (t == null) return;
          const twin = isCut && bgs.some(g => g.src === l.orig && g.x === l.x && g.y === l.y && g.w === l.w && g.h === l.h);
          const f = frameMeta(t), hb = f && headBox(f);
          const view = clipTo({ x: l.x, y: l.y, w: l.w, h: l.h }, { x: 0, y: 0, w: W, h: H });
          let vis = null, edge = false;
          if (hb) {
            const b = boxC(l, hb, ar); vis = areaOf(b) > 0 ? areaOf(clipTo(b, view)) / areaOf(b) : 0;
            vis = Math.round(vis * 1000) / 1000; edge = b.x < W * 0.08 || b.x + b.w > W * 0.92;
          }
          out.push({ fmt, rot: i, tpl: c.name, k: ci % 2, li, kind: isCut ? (twin ? "cut_twin" : "cut_free") : "frame", vis, edge,
                     x: Math.round(l.x), y: Math.round(l.y), fx: Math.round((l.fx ?? 0.5) * 1000) / 1000, t,
                     sides: isCut && l.fit !== "contain" ? sidesOf(l, W, H, alpha) : null });
        });
      });
    }
  } finally { FRAMES = orig; CUT_AUTO = cut0; AUTO_FMT = fmt0; makeCands(); }
  return out;
}"""


def measure(port, out, names, real=False):
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
            rows = pg.evaluate(JS, real)
            res[name] = {"rows": rows, "frames": pg.evaluate("FRAMES.length"), "errors": errs}
            print(name, len(rows), "칸", f"{time.time() - t0:.0f}s", "JS 오류", errs[:2], flush=True)
            pg.close()
        b.close()
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)


def stats(rows):
    rows = [r for r in rows if r.get("vis") is not None]  # 주인공 머리가 원본에서 잘린 장면은 머리 셈에서 뺌
    n = len(rows)
    return {"n": n, "ge95": sum(r["vis"] >= 0.95 for r in rows), "lt50": sum(r["vis"] < 0.5 for r in rows), "zero": sum(r["vis"] <= 0.001 for r in rows),
            "edge": sum(bool(r.get("edge")) for r in rows)}


def compare(pa, pb):
    A, B = (json.load(open(p, encoding="utf-8")) for p in (pa, pb))
    # 짝·따로는 템플릿이 놓은 자리로 셈: 후 판에서 화면 끝까지만 옮겨 배경과 같은 자리에 꼭 놓인 누끼(D-273)도 전 판의 분류 그대로
    kinds = {(n, r["fmt"], r["rot"], r["tpl"], r["k"], r["li"]): r["kind"] for n, v in A.items() for r in v["rows"] if "li" in r}
    for n, v in B.items():
        for r in v["rows"]:
            if "li" in r:
                r["kind"] = kinds.get((n, r["fmt"], r["rot"], r["tpl"], r["k"], r["li"]), r["kind"])
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
    # D-273: 화면 안에 들어온 누끼 레이어 끝 (원본 테두리·자른 선) — 전에 없던 끝 · 실제 누끼(--real)면 그 끝에 사람이 닿은(불투명 2% 넘게) 것
    key = lambda name, r: (name, r["fmt"], r["rot"], r["tpl"], r["k"], r.get("li"))  # noqa: E731
    before = {key(n, r): r for n, v in A.items() for r in v["rows"] if r.get("sides") is not None}
    cut_rows = [(n, r) for n, v in B.items() for r in v["rows"] if r.get("sides") is not None]
    if not cut_rows or "li" not in cut_rows[0][1]:
        return
    new, solid, all_in = [], [], 0
    for n, r in cut_rows:
        old = (before.get(key(n, r)) or {}).get("sides") or {}
        for side, frac in r["sides"].items():
            all_in += 1
            if side not in old:
                new.append((n, r, side, frac))
                if frac is not None and frac > 0.02:
                    solid.append((n, r, side, frac))
    print(f"== 화면 안에 들어온 누끼 레이어 끝 (후): {all_in}개 · 전에 없던 끝 {len(new)}개 · 그중 사람이 닿은 끝(불투명 2%↑, 실제 누끼일 때만) {len(solid)}개")
    for n, r, side, frac in new[:40]:
        print(f"    {n[:24]:24s} {r['fmt']:5s} r{r['rot']} {r['tpl']:14s} k{r['k']} {side:6s} x {r['x']} y {r['y']}  불투명 {frac}")
    for tag, D in (("전", A), ("후", B)):
        tot = [(n, r, s, f) for n, v in D.items() for r in v["rows"] if r.get("sides") for s, f in r["sides"].items() if f is not None and f > 0.02]
        if any(r.get("sides") and any(f is not None for f in r["sides"].values()) for v in D.values() for r in v["rows"]):
            print(f"  {tag}: 사람이 닿은 채 화면 안에 있는 누끼 끝 {len(tot)}개 (템플릿이 처음부터 둔 끝 포함)")


if __name__ == "__main__":
    if sys.argv[1] == "--compare":
        compare(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "--real":
        measure(sys.argv[2], sys.argv[3], sys.argv[4:], real=True)
    else:
        measure(sys.argv[1], sys.argv[2], sys.argv[3:])
