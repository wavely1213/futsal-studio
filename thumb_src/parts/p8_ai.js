
/* ---------- AI 추천 썸네일: 분석(장면·선수·공·표정·누끼·문구) × 레퍼런스형 템플릿 → 점수 → 서로 다른 6개 ---------- */
// 픽셀은 언제나 이 화면의 renderDoc 하나로 그림 (편집기·카드·내보내기·개발용 판정 모두 같은 그림). 백엔드는 숫자만 줌 (thumb.analyze).
const BRAND_DEF = { logo: "", logoPos: "tr", colors: { hl: "#FFE14D", hl2: "#FFFFFF", accent: "#FF3B30", neon: "#00D1FF", box: "#111111" }, font: "Pretendard Black", series: "풋사관 강좌", seriesOn: false, handle: "@풋살사관학교", apply: true, aiCopy: true, autoLogo: true };  // 판정 5회차(D-090): 검은고딕 → 프리텐다드 블랙 (짝 비교 76% 이김)
const AI = { frames: [], cuts: {}, copy: [], topics: [], brand: null, seed: 0, results: [], busy: false, pick: null, ab: new Set(), copySel: null, loaded: false, ai: false, cutFail: "" };
const TEXTY = 0.05;  // 장면에 박힌 큰 글자 넓이가 이만큼 넘으면 글자를 피해 자르거나 배경을 흐림 (thumb.text_boxes)
const SAFE = { long: { x0: 0.035, y0: 0.04, x1: 0.965, y1: 0.95, dur: [0.84, 0.86] }, short: { x0: 0.06, y0: 0.07, x1: 0.84, y1: 0.76, x1Top: 0.94, top: 0.42 } };  // 쇼츠: 버튼은 0.42H 아래만 → 위쪽은 0.94W 까지
// 썸네일 스타일 (스타일 카드의 '썸네일 버릇' → thumbstyle.params · D-130): 강조색 2개·글자 크기·위치·테두리·배경 밝기·누끼·얼굴 크기
// 브랜드 키트에서 사용자가 직접 바꾼 색(AI.brandCustom)이 늘 먼저 · 'apply' 를 끄면 기본 색 + 스타일
const TS = { view: null, sets: [] };  // /api/thumb/style 결과 {styles, pick, active, ours} · 이 영상의 A/B 묶음
const activeStyle = () => (TS.view && TS.view.active ? TS.view.active.params : null);
// 스타일 색: hl = 큰 줄의 주 색(그 채널 큰 글자 대부분의 색) · hl2 = 둘째 색. 'line' 제목은 큰 줄 hl·작은 줄 hl2 (예전과 같음),
// 'word' 제목(흰 바탕 글자 + 강조 낱말)은 스타일 색일 때만 바탕 글자를 주 색으로 (swapWord · 쌈바형: 노란 큰 글자에 흰 강조)
function styleBrand(base, sp, custom) {  // 브랜드 + 스타일 값 → 이번 추천에 쓸 브랜드 (_st: 템플릿 손잡이 · 스타일이 없으면 브랜드 그대로)
  if (!sp) return base;
  const cu = custom || [], own = ["hl", "hl2"].filter(k => sp[k] && !cu.includes(k));
  const out = Object.assign({}, base, { colors: Object.assign({}, base.colors), _st: Object.assign({}, sp, { swapWord: own.length === 2 }) });
  for (const k of own) out.colors[k] = sp[k];
  return out;
}
const brandOf = () => { const b = AI.brand && AI.brand.apply !== false ? AI.brand : BRAND_DEF; return styleBrand(b, activeStyle(), b === AI.brand ? AI.brandCustom : []); };
const mix = (a, b, t) => "#" + [1, 3, 5].map(i => Math.round(parseInt(toHex(a).slice(i, i + 2), 16) * (1 - t) + parseInt(toHex(b).slice(i, i + 2), 16) * t).toString(16).padStart(2, "0")).join("").toUpperCase();

/* ----- 장면 좌표 ↔ 캔버스 좌표 (drawImageContent 와 같은 계산) ----- */
function frameAspect() { return INFO.width && INFO.height ? INFO.width / INFO.height : 16 / 9; }
function imgRect(l, ar) {  // 이미지 레이어 안에서 원본 그림이 그려지는 사각형 (레이어 기준) · ar: 원본 가로/세로
  const cw = 1 - (l.cropL || 0) - (l.cropR || 0), ch = 1 - (l.cropT || 0) - (l.cropB || 0), a = ar * cw / ch;
  let dw, dh;
  if (l.fit === "cover") { if (l.w / l.h > a) { dw = l.w; dh = l.w / a; } else { dh = l.h; dw = l.h * a; } }
  else { if (l.w / l.h > a) { dh = l.h; dw = l.h * a; } else { dw = l.w; dh = l.w / a; } }
  return { dx: (l.w - dw) * l.fx, dy: (l.h - dh) * l.fy, dw, dh, cw, ch };
}
function f2c(l, u, v, ar) {  // 장면 (u, v) 0~1 → 캔버스 px (회전 없는 레이어)
  const r = imgRect(l, ar); let x = (u - (l.cropL || 0)) / r.cw; if (l.flipX) x = 1 - x;
  return [l.x + r.dx + x * r.dw, l.y + r.dy + (v - (l.cropT || 0)) / r.ch * r.dh];
}
function boxC(l, b, ar) { const p = f2c(l, b[0], b[1], ar), q = f2c(l, b[0] + b[2], b[1] + b[3], ar); return { x: Math.min(p[0], q[0]), y: p[1], w: Math.abs(q[0] - p[0]), h: q[1] - p[1] }; }
const clipTo = (b, r) => { const x0 = Math.max(b.x, r.x), y0 = Math.max(b.y, r.y), x1 = Math.min(b.x + b.w, r.x + r.w), y1 = Math.min(b.y + b.h, r.y + r.h); return { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) }; };
const areaOf = b => Math.max(0, b.w) * Math.max(0, b.h);
function interArea(a, b) { return areaOf(clipTo(a, b)); }

/* ----- 주인공 · 머리 (장면 좌표 0~1) ----- */
function mainBox(f) { return f && f.main >= 0 ? (f.persons || [])[f.main] || null : null; }
function mainFace(f) {  // 주인공 얼굴 상자 (주인공 상자 위쪽 절반 안의 가장 큰 얼굴) · 주인공이 없으면 가장 큰 얼굴 · 없으면 null
  const fs = (f && f.faces) || [], m = mainBox(f);
  if (!fs.length) return null;
  if (!m) return fs[0].box;
  const in_ = fs.find(x => { const cx = x.box[0] + x.box[2] / 2, cy = x.box[1] + x.box[3] / 2; return cx >= m[0] && cx <= m[0] + m[2] && cy >= m[1] - 0.02 && cy <= m[1] + m[3] * 0.5; });
  return in_ ? in_.box : null;
}
function headBox(f) {  // 지켜야 할 머리: 주인공 얼굴(+이마·머리카락) · 얼굴이 없으면 주인공 상자 위 18% 가운데 · 머리가 원본에서 이미 잘렸으면 null
  const m = mainBox(f), fc = mainFace(f);
  if (fc)
    return [fc[0] - fc[2] * 0.12, Math.max(0, fc[1] - fc[3] * 0.6), fc[2] * 1.24, fc[3] * 1.65];  // 이마·머리카락까지
  if (m && m[1] > 0.012 && m[3] >= 0.12) return [m[0] + m[2] * 0.22, m[1], m[2] * 0.56, Math.min(m[3] * 0.18, m[2] * 1.2)];
  return null;
}
// 판정 2회차: 원본에서 이미 머리가 잘린 주인공 18장 (headBox 가 null 이라 머리 게이트를 그냥 통과) → 발·공 클로즈업이 아니면 쓰지 않음
function srcHeadCut(f) {  // 얼굴 없음(또는 얼굴이 원본 위 끝에 걸림) · 주인공 상자 위 끝이 원본 위 끝
  // 판정 q5 1회차(D-098): 이마가 잘린 얼굴(얼굴 상자 위 끝 0)이 있으면 '머리 보임'으로 쳐서 머리 잘린 장면이 그대로 나옴 (004_long_6 '머리가 화면 위에서 잘려 얼굴이 없고')
  const m = mainBox(f), fc = mainFace(f); return !!m && m[1] <= 0.012 && (!fc || fc[1] <= 0.01);
}
function footClose(f) {  // 발·공 클로즈업: 공이 주인공 상자 아래 35% 안 (머리가 없어도 되는 장면)
  const m = mainBox(f), b = f && f.ball; if (!m || !b) return false;
  const cx = b[0] + b[2] / 2, cy = b[1] + b[3] / 2;
  return m[3] >= 0.5 && b[2] >= 0.04 && cx >= m[0] - 0.01 && cx <= m[0] + m[2] + 0.01 && cy >= m[1] + m[3] * 0.65 && cy <= m[1] + m[3] + 0.08;  // 크게 잡은 발·공 (멀리 다리만 나온 장면은 아님)
}
// 판정 5회차(D-091): 다리만 나온 장면 16/60 중 13장이 footClose 예외로 통과 → 발·공 클로즈업은 문구가 발 이야기이고 배경이 차분할 때만 (묶음에 1장 · recommend)
const FOOT_COPY = /발바닥|디딤발|터치|드래그|발등|발 ?안쪽|발 ?바깥|트래핑|킥|발끝/;
const footCopy = c => !!c && FOOT_COPY.test(`${c.l1 || ""} ${c.l2 || ""}`);
// 판정 q5 1회차(D-098): 묶음에 1장 남긴 다리만 나온 장면도 pro 3.7~5.3 ('사람 없이 다리와 공만'·'다리만 보이는 모션블러' · 레퍼런스 2~3%) →
// 자동 추천에서는 쓰지 않고, 사용자가 '장면 고르기'로 그 장면을 직접 골랐을 때만
const footOk = (f, c) => footClose(f) && footCopy(c) && !loud(f) && AI.pick != null && Math.abs(AI.pick - f.t) < 1e-6;
const headlessBad = f => srcHeadCut(f);
function visibleText(l, f, ar, W0, H0) {  // 배경에 박힌 큰 글자가 캔버스에 보이는 넓이 (캔버스 대비 0~1)
  if (!f || !f.tboxes || !f.tboxes.length) return 0;
  const view = clipTo({ x: l.x, y: l.y, w: l.w, h: l.h }, { x: 0, y: 0, w: W0, h: H0 });
  return f.tboxes.reduce((a, b) => a + interArea(boxC(l, b, ar), view), 0) / (W0 * H0);
}

/* ----- 배경 장면 놓기: 장면 한 점(focus)이 캔버스 target 에 오도록 z 배로 · 머리는 늘 화면 안 · 박힌 글자는 되도록 밖으로 ----- */
function placeFrame(ctx, z, focus, target, o) {
  const f = ctx.frame, W0 = ctx.W, H0 = ctx.H, ar = ctx.ar;
  const l = L("image", Object.assign({ name: "배경", src: frameSrc(f.t), x: 0, y: 0, w: W0 * z, h: H0 * z, fit: "cover" }, o.extra || {}));
  if (f.grade && o.grade !== false) l.grade = Object.assign({}, f.grade, { amt: o.gradeAmt ?? 1 });
  if (ctx.band === "bottom" && o.crop !== false) l.cropB = bandCrop(ctx);  // 영상에 박힌 자막·방송 띠는 잘라냄
  if (ctx.band === "top" && o.crop !== false) l.cropT = bandCrop(ctx);
  const [u, v] = focus, [tx, ty] = target, r = imgRect(l, ar);
  const px = r.dx + ((l.flipX ? 1 - u : u) - (l.cropL || 0)) / r.cw * r.dw, py = r.dy + (v - (l.cropT || 0)) / r.ch * r.dh;
  l.x = clamp(tx - px, W0 - l.w, 0); l.y = clamp(ty - py, H0 - l.h, 0);
  if (r.dw > l.w + 1) l.fx = clamp((tx - l.x - ((u - (l.cropL || 0)) / r.cw) * r.dw) / (l.w - r.dw), 0, 1);  // 가로로 남는 그림(쇼츠에 가로 장면) → 초점으로
  if (r.dh > l.h + 1) l.fy = clamp((ty - l.y - ((v - (l.cropT || 0)) / r.ch) * r.dh) / (l.h - r.dh), 0, 1);
  return l;
}
function headOk(ctx, l, hb, m) {  // 머리 상자가 캔버스(와 레이어) 안에 다 들어오는지
  const b = boxC(l, hb, ctx.ar), view = clipTo({ x: l.x, y: l.y, w: l.w, h: l.h }, { x: 0, y: 0, w: ctx.W, h: ctx.H });
  return b.y >= view.y + m && b.x >= view.x - b.w * 0.05 && b.x + b.w <= view.x + view.w + b.w * 0.05 && b.y + b.h <= view.y + view.h;
}
// 판정 5회차(D-091): 머리 위 여백 6%H(얼굴 클로즈업 3%H) · 원본에 발이 보이면 캔버스에서도 발을 지킴(덜 확대 → 위로 옮김) — 쇼츠 칸 머리 위 중앙값 0.149(레퍼런스 0.395) ·
// 롱폼 28% 가 발이 잘림(레퍼런스 12%) · o.minHeadY: 머리가 이 높이(캔버스 px)보다 올라가면 안 됨 (제목 아래에 둔 머리) · o.headroom: 머리 위 여백 (화면 높이 대비)
// 판정 q5 2회차(D-116): '잘림' 22% (레퍼런스 12%) — 옆 끝에서 반쯤 잘린 다른 사람(쇼츠 묶음 안 pro −0.34) · 위 끝에서 머리가 잘린 다른 사람 · 아래·옆 끝에 걸린 공
// (기하 게이트는 주인공 머리만 봐서 0/99) → 놓는 자리 비용에 넣고, 걸리면 옆으로 조금 옮긴 자리도 봄
// 검토(D-119): 쇼츠 묶음 하나 1.2~3.8초 → 1.8~6.3초 (옆으로 옮긴 자리 4개 × 확대 단계) — 같은 장면·같은 자리는 다시 재지 않음 (문구·템플릿마다 같은 배경 자리가 되풀이됨)
const ECC = new Map();
function edgeCutCost(ctx, l, f, m) {
  const key = `${f.t}|${ctx.W}|${ctx.H}|${(f.persons || []).indexOf(m)}|${[l.x, l.y, l.w, l.h, l.fx, l.fy, l.cropT || 0, l.cropB || 0].map(v => Math.round(v * 100)).join(",")}`;
  if (f.t != null && ECC.has(key)) return ECC.get(key);
  const c = edgeCutCost0(ctx, l, f, m);
  if (f.t != null) { if (ECC.size > 20000) ECC.clear(); ECC.set(key, c); }
  return c;
}
function edgeCutCost0(ctx, l, f, m) {
  const W0 = ctx.W, H0 = ctx.H, view = clipTo({ x: l.x, y: l.y, w: l.w, h: l.h }, { x: 0, y: 0, w: W0, h: H0 }); let c = 0;
  for (const p of f.persons || []) {
    if (p === m || p[3] < 0.06) continue;
    const b = boxC(l, p, ctx.ar); if (b.h < H0 * 0.14) continue;
    const v = clipTo(b, view); if (!areaOf(v)) continue;
    const fx = v.w / Math.max(1, b.w);
    if (fx > 0.12 && fx < 0.85) c += 0.5 * Math.min(1, b.h / (H0 * 0.35));  // 반쯤 잘린 몸 (큰 사람일수록)
    if (p[1] > 0.012 && b.y < view.y - 2 && b.y + b.h * 0.25 > view.y) c += 0.4;  // 원본엔 있는 머리가 위 끝에서 잘림
  }
  if (m && f.ball) {  // 주인공 발 가까이 있는 공 (공 다루는 순간)은 화면 안에
    const bc = [f.ball[0] + f.ball[2] / 2, f.ball[1] + f.ball[3] / 2], near = Math.hypot((bc[0] - (m[0] + m[2] / 2)) * ctx.ar, bc[1] - (m[1] + m[3])) < m[3] * 0.6;
    if (near) { const bb = boxC(l, f.ball, ctx.ar); if (bb.y + bb.h > view.y + view.h - H0 * 0.008 || bb.x < view.x + W0 * 0.005 || bb.x + bb.w > view.x + view.w - W0 * 0.005) c += 1.2; }
  }
  if (m) { const b = boxC(l, m, ctx.ar), v = clipTo(b, view), fx = v.w / Math.max(1, b.w); if (fx < 0.9 && fx > 0) c += (0.9 - fx) * 3; }  // 주인공이 옆 끝에 걸림
  return Math.min(2.5, c);
}
function frameLayer(ctx, o = {}) {
  const f = ctx.frame, W0 = ctx.W, H0 = ctx.H, z0 = clamp(o.zoom || 1, 1, maxZoom(ctx));
  const focus = o.focus || ctx.focus, target = o.target || [W0 / 2, H0 / 2], hb = o.keep === false ? null : headBox(f), mg = H0 * 0.02;
  const m = o.keep === false ? null : mainBox(f), close = f.kind === "close";
  const want = H0 * (o.headroom ?? (close ? 0.03 : 0.06));
  const feetSrc = !!m && !close && m[1] + m[3] < 0.985 && m[3] < 0.9 && o.feet !== false;  // 원본에 발이 보이는 사람
  const texty = (f.text || 0) >= TEXTY && f.tboxes && f.tboxes.length;
  const zs = texty ? [z0, z0 * 1.2, z0 * 1.45, maxZoom(ctx)] : [z0, z0 * 0.9, z0 * 0.8, z0 * 0.7, z0 * 0.6, 1];
  const edges = o.keep !== false && o.edges !== false;
  const cost = (l, z) => {
    const ok = !hb || headOk(ctx, l, hb, mg * 0.5), vt = texty ? visibleText(l, f, ctx.ar, W0, H0) : 0;
    const ec = edges ? edgeCutCost(ctx, l, f, m) : 0;
    let c = (ok ? 0 : 10) + vt * 20 + Math.abs(z - z0) * 0.05 + ec + (ec || l._dx ? Math.max(0, z0 - z) * 0.5 : 0) + (l._dx ? Math.abs(l._dx) / W0 * 2.5 : 0);  // 옆 끝 잘림을 피하려고 주인공을 작게 만들지는 않게
    if (hb && ok) {
      const b = boxC(l, hb, ctx.ar), top = b.y - Math.max(0, l.y);
      if (top < want) c += (want - top) / H0 * 25;  // 여백 0 이면 +1.5
      if (o.minHeadY != null && b.y < o.minHeadY - 2) c += 3 + (o.minHeadY - b.y) / H0 * 20;
    }
    if (feetSrc) {
      const b = boxC(l, m, ctx.ar), vb = Math.min(H0, l.y + l.h) - H0 * 0.012;
      if (b.y + b.h > vb) c += 1 + clamp((b.y + b.h - vb) / Math.max(1, b.h), 0, 1) * 2;
    }
    return c;
  };
  let best = null;
  for (const z1 of zs) {
    const z = clamp(z1, 1, maxZoom(ctx)), l0 = placeFrame(ctx, z, focus, target, o), cands = [l0];
    if (hb) {  // 머리가 잘리거나 여백이 모자라면 장면을 내려서(또는 옆으로) 다시
      const b = boxC(l0, hb, ctx.ar), dy = b.y < want ? want - b.y + H0 * 0.005 : 0, dx = b.x < 0 ? -b.x + W0 * 0.02 : b.x + b.w > W0 ? W0 - (b.x + b.w) - W0 * 0.02 : 0;
      if (dy || dx) cands.push(placeFrame(ctx, z, focus, [target[0] + dx, target[1] + dy], o));
    }
    if (feetSrc) {  // 발이 잘리면 장면을 올려서 (머리 여백·minHeadY 는 비용으로)
      const b = boxC(l0, m, ctx.ar), over = b.y + b.h - (H0 * 0.985);
      if (over > 0) cands.push(placeFrame(ctx, z, focus, [target[0], target[1] - over], o));
    }
    if (edges && edgeCutCost(ctx, l0, f, m) > 0.2) for (const dx of [-0.08, 0.08, -0.16, 0.16]) {  // 옆 끝에 사람·공이 걸리면 장면을 옆으로 조금 옮겨 봄 (주인공 자리에서 멀수록 비용)
      const lx = placeFrame(ctx, z, focus, [target[0] + dx * W0, target[1]], o);
      if (Math.abs(lx.x - l0.x) > 1) { lx._dx = dx * W0; cands.push(lx); }
    }
    for (const l of cands) { const c = cost(l, z); if (!best || c < best.cost - 1e-9) best = { l, cost: c }; }
    if (best.cost < 0.25 && !texty) break;
  }
  delete best.l._dx;
  return best.l;
}
// 원본 대비 확대 상한 — 판정 2회차: 84장 중 33장이 원본을 1.5배 넘게 키워 흐림(쇼츠는 720p 를 1.8~1.9배) → 1.35배 (원본이 1080p 넘으면 1.5배)
const srcW = () => Math.min(1920, INFO.width || 1920);
// 판정 5회차(D-091): 1.3배 넘게 키운 장이 판정에서 깎이지 않았음(롱폼 12%) → 1080p 아래 원본도 1.5배 (720p 쇼츠 칸에서 주인공이 '작음' 게이트에 걸려 장면 1곳만 남음)
// 판정 q5 1회차(D-096): 롱폼을 2.0배까지 키워 봤으나(개발 판정 r_e·r_f·r_g) 롱폼 pro 4.61~4.79 · 1.5배(r_h) 4.81 — 차이가 흔들림 안이라 1.5배 그대로 ·
// 주인공을 크게 하는 것은 꽉 찬 쇼츠(SHORT_ZOOM_CAP)에서만
function srcCap() { return 1.5; }
function upOf(l, ar) { const r = imgRect(l, ar); return r.dw / r.cw / srcW(); }  // 이미지 레이어가 원본 화소 1개를 캔버스 몇 화소로 그리는지
function bandCrop(ctx) {  // 영상에 박힌 자막·방송 띠를 잘라내는 비율 (위 또는 아래) · 없으면 0
  const f = ctx.frame || {};
  return ctx.band === "bottom" ? clamp(1 - (f.bandY || 0.76) + 0.01, 0.05, 0.3) : ctx.band === "top" ? clamp((f.bandY || 0.2) + 0.01, 0.05, 0.3) : 0;
}
function arEff(ctx) { return ctx.ar / (1 - bandCrop(ctx)); }  // 띠를 잘라낸 뒤 장면의 가로/세로
function maxZoom(ctx) {  // 원본 장면을 srcCap 배 넘게 키우지 않게 (덮기 자체가 이미 넘으면 1) · 꽉 찬 쇼츠는 SHORT_ZOOM_CAP 배까지 (주인공을 크게)
  const a = arEff(ctx), dw = ctx.W / ctx.H > a ? ctx.W : ctx.H * a, up = dw / srcW();
  const cap = ctx.short && ctx.H >= 1900 ? Math.max(srcCap(), Math.min(SHORT_ZOOM_CAP, up * 2.5)) : srcCap();  // 세로 원본 쇼츠는 2.5배까지
  return Math.max(1, cap / up);
}
// 판정 5회차(D-091): 주인공 키 목표 상한 롱폼 0.72H · 쇼츠 0.62H (얼굴 클로즈업 제외 · 레퍼런스 롱폼 주인공 키 중앙값 0.64, IQR 0.52~0.75 — '상자 제목'은 0.95H 를 노렸음)
const SUBJ_MAX = { long: 0.72, short: 0.66 };  // 판정 q5 1회차(D-096): 고칠 점 '55~80%로 키우기' 128/200 → 쇼츠 0.62 → 0.66 (롱폼은 그대로)
function zoomFor(ctx, target, lo = 1, hi = 2) {  // 주인공(얼굴 또는 사람)이 화면 높이의 target 만큼 보이게 하는 확대 배율
  const f = ctx.frame, m = mainBox(f), fc = mainFace(f);
  if (f && f.kind !== "close") target = Math.min(target, SUBJ_MAX[ctx.short ? "short" : "long"]);
  const h = m ? m[3] : fc ? fc[3] * 2.6 : 0; if (!h) return lo;
  const base = ctx.W / ctx.H > ctx.ar ? ctx.W / ctx.ar / ctx.H : 1;  // 가로를 채우느라 이미 커진 만큼
  return clamp(target / (h * base), lo, hi);
}
function subjectH(ctx, bg) {  // 캔버스에서 주인공 높이 (화면 높이 대비 · 보이는 부분만)
  const m = mainBox(ctx.frame); if (!m) return 0;
  return clipTo(boxC(bg, m, ctx.ar), { x: 0, y: 0, w: ctx.W, h: ctx.H }).h / ctx.H;
}
function cutLayer(ctx, bg, o = {}) {  // 배경과 똑같이 놓인 누끼 — 크고 또렷한 주인공만 (작은 선수 누끼는 깨짐 · 외곽선은 주인공 키에 맞춰 가늘게)
  if (!ctx.cut) return null;
  // 누끼가 꼭 필요한 템플릿(need)이 아니면 글자 박힌 장면에서만 (배경을 흐린 판으로 바꿀 때 주인공을 살림) — 판정: 같은 장면 위 흰 테두리 누끼는 '오려 붙인 티'
  if (!o.need && (ctx.frame.text || 0) < TEXTY) return null;
  const sh = mainBox(ctx.frame) ? subjectH(ctx, bg) : 0.6;
  // 판정 2회차: 흐리거나 크게 키운 사람 누끼(노란 네온 외곽선)가 '오려 붙인 티' — 누끼 있는 장 5.79 < 없는 장 6.07 → 또렷하고 큰 주인공만
  if (sh < (o.minH ?? (ctx.short ? 0.35 : 0.5)) * (ctx.relax ? 0.8 : 1)) return null;
  if (!ctx.relax && ((ctx.frame.blur ?? 0) > 0.2 || upOf(bg, ctx.ar) > 1.3)) return null;  // relax: 쓸 만한 장면이 없는 영상의 2개 (그나마 나은 것)
  const ow = Math.round(5 * ctx.W / 1280 * (o.ow || 1));  // 흰 5px (노랑은 브랜드에서 고를 때만)
  const l = L("image", { name: "누끼", src: ctx.cut.cut, orig: ctx.cut.src, x: bg.x, y: bg.y, w: bg.w, h: bg.h, fit: bg.fit, fx: bg.fx, fy: bg.fy, flipX: bg.flipX,
    cropL: bg.cropL, cropR: bg.cropR, cropT: bg.cropT, cropB: bg.cropB,
    outline: { on: true, color: o.outline || "#FFFFFF", width: ow },
    glow: { on: false, color: "#FFFFFF", size: Math.round(22 * ctx.W / 1280), opacity: 0.75 },
    shadow: { on: true, color: "#000000", blur: Math.round(28 * ctx.W / 1280), dx: 0, dy: Math.round(6 * ctx.W / 1280), opacity: 0.55 } });
  if (bg.grade) l.grade = Object.assign({}, bg.grade);
  return l;
}

/* ----- 글자 (브랜드 색 역할: hl 강조 · hl2 기본 · accent 포인트 · neon 전술 · box 상자) ----- */
// 판정 5회차(D-090): 프리텐다드 블랙은 얇은 외곽선(0.09) + 부드러운 그림자가 짝 비교에서 이김 (VA +1.46) · 검은고딕은 예전 굵은 외곽선 + 딱딱한 그림자 그대로
// (얇은 외곽선만 따로 쓰면 오히려 낮음 — VS −0.21: 글꼴과 함께일 때만)
const THIN_FONTS = new Set(["Pretendard Black", "Pretendard Bold"]);
const THIN_SW = 0.09;
const thinFont = f => THIN_FONTS.has(f);
function swOf(font, o = {}) { return thinFont(font) ? Math.min(o.sw ?? 0.2, THIN_SW) : (o.sw ?? 0.2); }
function softShadow(size) { return { on: true, color: "#000000", blur: Math.round(size * 0.16), dx: 0, dy: Math.round(size * 0.04), opacity: 0.75 }; }
function swFor(ctx, font, o = {}) {  // 바깥 획: 템플릿이 따로 정하지 않았으면 스타일의 테두리 버릇(thumbstyle.SW_OF_TIER) · 없으면 글꼴 기본
  const s = ctx.brand && ctx.brand._st && ctx.brand._st.sw;
  return o.sw == null && s != null ? s : swOf(font, o);
}
function tStyle(ctx, size, o = {}) {
  const font = o.font || ctx.brand.font, sw = Math.round(size * swFor(ctx, font, o));
  return Object.assign({ font, size, fill: o.fill || ctx.brand.colors.hl2, fill2: o.fill2 || "", gradAngle: 90, align: o.align || "center", lh: 1.04,
    strokes: [{ color: o.stroke || "#111111", width: sw }, { color: o.inner || "#FFFFFF", width: o.innerW ? Math.round(size * o.innerW) : 0 }],
    shadow: o.noShadow ? { on: false } : thinFont(font) ? softShadow(size) : { on: true, color: "#000000", blur: 0, dx: Math.round(size * 0.05), dy: Math.round(size * 0.065), opacity: 1 } }, o.extra || {});
}
const DIGIT_FONT = "Pretendard Black";  // 검은고딕의 숫자 1 은 작게 보면 'ㄱ'처럼 읽힘 (판정 OCR '1대1' → '그대그') → 숫자만 다른 굵은 글꼴
function digitRuns(text, font) {
  if (font === DIGIT_FONT) return [];
  const out = []; let m; const rx = /[0-9]+/g;
  while ((m = rx.exec(text))) out.push({ s: m.index, e: m.index + m[0].length, font: DIGIT_FONT });
  return out;
}
const SMALL_FONT_PX = 19;  // 목록 크기에서 글자 높이가 이보다 작으면 검은고딕 대신 프리텐다드 블랙
// 판정 5회차(D-090): 예전엔 도현으로 바꿨는데 168px OCR 정확도가 도현 0.51 < 검은고딕 0.59 < 프리텐다드 블랙 0.88 — 가장 안 읽히는 글꼴로 바꾸고 있었음
function readableFont(ctx, font, size) {
  return font === "Black Han Sans" && size * (ctx.short ? 110 : 168) / ctx.W < SMALL_FONT_PX ? "Pretendard Black" : font;
}
// 판정 4회차: 'O/X 대비' 문구의 라틴 O 가 검은고딕에서 좁고 길어 숫자 0 처럼 읽힘 → ◯(초록)·X(빨강)를 프리텐다드 기호로
const OX_RX = /(^|\s)([OX])(?=$|\s|[!?])/g;
function oxRuns(text) {
  const out = []; let m; OX_RX.lastIndex = 0;
  while ((m = OX_RX.exec(text))) { const i = m.index + m[1].length; out.push({ s: i, e: i + 1, font: DIGIT_FONT, fill: m[2] === "X" ? "#FF3B30" : "#2BD96B" }); }
  return out;
}
function lineLayer(ctx, text, size, o = {}) {  // 한 줄 글자 + 강조 낱말(runs: 강조 색·조금 크게) + 숫자 글꼴 + O/X 기호
  text = String(text || "").replace(OX_RX, (_, a, b) => a + (b === "O" ? "◯" : "X"));
  const font = readableFont(ctx, o.font || ctx.brand.font, size);
  const l = L("text", Object.assign({ text, name: o.name || "제목" }, tStyle(ctx, size, Object.assign({}, o, { font }))));
  const runs = [];
  if (o.emph && o.emph[1] > o.emph[0]) {
    const run = { s: o.emph[0], e: o.emph[1], fill: o.emphFill || ctx.brand.colors.hl };
    if (o.emphScale && o.emphScale !== 1) { run.size = Math.round(size * o.emphScale); run.s1w = Math.round(run.size * swFor(ctx, font, o)); }
    runs.push(run);
  }
  l.runs = runs.concat(digitRuns(text, l.font), oxRuns(text.replace(/◯/g, "O")));
  if (o.rot) l.rot = o.rot;
  if (o.skew) l.skew = o.skew;
  fitText(l); return l;
}
function emphOf(copy, line) { const e = copy.emph; if (!e || e[0] !== line) return null; return [e[1], e[2]]; }
function subBox(ctx, text, size, o = {}) {  // 어두운 둥근 상자 속 작은 줄 ('1분만 투자하세요' — 쪼살·해주호) · 주제 낱말은 노랗게
  if (!text) return null;
  const b = ctx.brand.colors, l = L("text", Object.assign({ text, name: "보조 문구" }, tStyle(ctx, size, { font: "Pretendard Black", fill: "#FFFFFF", sw: 0, noShadow: true, align: o.align || "center" }),
    { box: { on: true, color: o.boxColor || b.box, pad: Math.round(size * 0.32), radius: Math.round(size * 0.42) }, shadow: { on: true, color: "#000000", blur: Math.round(size * 0.3), dx: 0, dy: Math.round(size * 0.08), opacity: 0.5 } }));
  const tp = (AI.topics || []).find(t => t && text.includes(t));
  l.runs = (tp ? [{ s: text.indexOf(tp), e: text.indexOf(tp) + tp.length, fill: b.hl }] : []).concat(digitRuns(text, l.font));
  fitText(l); return l;
}
// 판정 5회차(D-090): 두 줄 크기 비율 — 레퍼런스는 두 줄이 거의 같은 크기(≥0.75, 54%)거나 한 줄(38%) · 0.45~0.75 는 4% (우리는 62~71% 가 0.56)
// 한 문장으로 이어지는 두 줄(둘째 줄이 서술어로 끝남)은 0.88 · 꾸밈말 + 이름씨('수비를 속이는 / 발바닥 드래그')는 kicker 템플릿에서만 0.42 · 짧은 꼬리표(≤4자) + 큰 낱말은 0.45
const PRED_END = /(요|니다|다|까|지|네|죠|해|봐|라|자|게|걸|군|나)[?!.~]*$|[?!]$|\.\.$/;
const LINK_END = /(는|은|을|를|면|고|도|만|의|로|에서|에게|한테|와|과|할|한|된|될|진|던|려면|처럼|보다|이랑|랑)$/;
function lineRatio(c, kicker) {
  if (!c.l2) return 1;
  const bigI = c.emph ? c.emph[0] : 1, small = (bigI ? c.l1 : c.l2).replace(/\s/g, ""), big = (bigI ? c.l2 : c.l1).replace(/\s/g, "");
  if (PRED_END.test(c.l2.trim())) return 0.88;  // 한 문장 (둘째 줄이 서술어·물음으로 끝남)
  if (kicker && bigI === 1 && LINK_END.test(c.l1.trim())) return 0.42;  // 쌈바형 '수비를 속이는(작게) / 발바닥 드래그(크게)'
  if (small.length <= 4 && big.length >= small.length * 1.6) return 0.45;  // 짧은 꼬리표
  return 0.88;
}
// 두 줄 제목: 강조 낱말이 든 줄을 크게(big), 다른 줄은 같은 크기에 가깝게(lineRatio) · 상자(box) 안에 맞춰 쌓음 · o.sub: 아래에 어두운 상자 속 보조 문구
// o.grow: 상자 폭을 채우도록 그 배까지 키움 (판정 5회차: 제목 묶음 폭 0.56W → 레퍼런스 0.78W) → {layers, box, big, small, sub}
function headline(ctx, box, o = {}) {
  const c = ctx.copy, b = ctx.brand.colors, bigI = c.l2 ? (c.emph ? c.emph[0] : 1) : 0, smallI = 1 - bigI;
  const bigT = bigI ? c.l2 : c.l1, smallT = c.l2 ? (smallI ? c.l2 : c.l1) : "";
  const align = o.align || "center", ratio = o.ratioFixed ? o.ratio : lineRatio(c, o.kicker), style = o.style || "line";
  const swapW = style === "word" && !!(ctx.brand._st && ctx.brand._st.swapWord);
  const ts = (ctx.brand._st && ctx.brand._st.textScale) || 1;  // 스타일의 글자 크기 버릇: 작게 쓰는 채널은 늘 작게 · 크게 쓰는 채널은 자리가 있을 때만 크게
  let size = o.size || Math.round(ctx.H * 0.2);
  const em = emphOf(c, bigI);
  const bigO = style === "line"
    ? { fill: o.bigFill || b.hl, fill2: o.grad === false || grayish(o.bigFill || b.hl) ? "" : mix(o.bigFill || b.hl, "#FF8A00", 0.42), emph: o.emphAccent && em ? em : null, emphFill: b.accent, emphScale: 1 }
    : { fill: swapW ? b.hl : b.hl2, emph: em, emphFill: o.emphFill || (swapW ? b.hl2 : b.hl), emphScale: o.emphScale || 1.18 };
  const subT = o.sub ? (c.sub || "") : "";
  const make = k => {
    const big = lineLayer(ctx, bigT, Math.round(size * k), Object.assign({ align, name: "제목 큰 줄", rot: o.rot, skew: o.skew, sw: o.sw, font: o.font }, bigO));
    const small = smallT ? lineLayer(ctx, smallT, Math.round(size * ratio * k), { align, name: "제목 작은 줄", fill: o.smallFill || b.hl2, rot: o.rot, skew: o.skew, sw: o.sw, font: o.font,
      emph: style === "word" ? emphOf(c, smallI) : null, emphFill: swapW ? b.hl2 : b.hl }) : null;
    const sub = subT ? subBox(ctx, subT, Math.round(size * (o.subRatio || 0.36) * k), { align }) : null;
    return { big, small, sub };
  };
  let t = make(1);
  const wOf = l => (l ? l.w : 0), gap = () => -Math.round(size * 0.03), subGap = () => Math.round(size * 0.12);
  const totalH = () => t.big.h + (t.small ? t.small.h + gap() : 0) + (t.sub ? t.sub.h + subGap() : 0);
  const fitK = Math.min(box.w / Math.max(wOf(t.big), wOf(t.small), wOf(t.sub)), box.h / totalH()), k0 = Math.min((o.grow || 1) * Math.max(1, ts), fitK);
  // 작게 쓰는 버릇도 '큰 줄이 작음' 게이트(BIG_MIN · 휴대폰 목록에서 읽히는 하한) 밑으로는 줄이지 않음 — 그 밑이면 후보가 모두 빠지고 예전 템플릿만 남음
  const k = ts < 1 ? Math.max(k0 * ts, Math.min(k0, BIG_MIN[ctx.short ? "short" : "long"] * ctx.H * 1.03 / size)) : k0;
  if (Math.abs(k - 1) > 0.001) { size = Math.round(size * k); t = make(1); }
  for (let i = 0; i < 3 && Math.max(wOf(t.big), wOf(t.small), wOf(t.sub)) > box.w + 1; i++) { size = Math.round(size * 0.97); t = make(1); }  // 반올림으로 넘치면 조금 줄임
  // 휴대폰 목록 크기(롱폼 168px · 쇼츠 110px)에서 작은 줄·보조 문구도 8px 넘게: 모자라면 그 줄만 키움 (상자보다 넓어지면 점수 게이트가 거름)
  const minPx = Math.ceil(8.4 * ctx.W / (ctx.short ? 110 : 168));
  if (t.small && t.small.size < minPx) t.small = lineLayer(ctx, smallT, minPx, { align, name: "제목 작은 줄", fill: o.smallFill || b.hl2, rot: o.rot, skew: o.skew, sw: o.sw, font: o.font, emph: style === "word" ? emphOf(c, smallI) : null, emphFill: swapW ? b.hl2 : b.hl });
  if (t.sub && t.sub.size < minPx) t.sub = subBox(ctx, subT, minPx, { align });
  const order = (bigI === 0 ? [t.big, t.small] : [t.small, t.big]).concat(t.sub ? [t.sub] : []);  // 읽는 순서는 늘 첫째 줄 → 둘째 줄 (크기만 다름) → 보조 문구
  const ax = align === "center" ? 0.5 : align === "right" ? 1 : 0, X = box.x + box.w * ax;
  let y = o.anchor === "bottom" ? box.y + box.h - totalH() : o.anchor === "middle" ? box.y + (box.h - totalH()) / 2 : box.y;
  for (const l of order) { if (!l) continue; if (l === t.sub) y += subGap() - gap(); l.x = X - l.w * ax; l.y = y; y += l.h + gap(); }
  if (o.anchor === "bottom") {  // 판정 q5 1회차: 아래 제목이 그림자까지 0.955H 로 앱 안전 영역(SAFE y1 0.95H)을 3.6px 넘음(19/93) → 그림자까지 안쪽으로
    const lim = SAFE[ctx.short ? "short" : "long"].y1 * ctx.H, over = Math.max(...order.filter(Boolean).map(l => { const e = extentOf(l); return e.y + e.h; })) - lim;
    if (over > 0) for (const l of order) if (l) l.y -= over;
  }
  for (const l of [t.big, t.small]) if (l) l.fitBox = { w: box.w, h: l.h * 1.15, size: l.size, ay: 0 };
  const layers = order.filter(Boolean);
  return { layers, box: bbox(layers), big: t.big, small: t.small, sub: t.sub };
}
const grayish = hex => { const v = [1, 3, 5].map(i => parseInt(toHex(hex).slice(i, i + 2), 16)); return Math.max(...v) - Math.min(...v) < 40; };  // 흰색·회색 강조는 주황 그라데이션을 넣지 않음
// 상자 제목 (자막형): 큰 줄은 강조색 상자에 검은 글자, 작은 줄은 어두운 상자에 흰 글자 · 왼쪽 아래부터 쌓음 → {layers, box, big}
function boxHead(ctx, x, yBottom, maxW, o = {}) {
  const c = ctx.copy, b = ctx.brand.colors, two = !!c.l2, bigI = two ? (c.emph ? c.emph[0] : 1) : 0;
  const mk = (t, size, fill, box, name) => {
    const l = L("text", Object.assign({ text: t, name }, tStyle(ctx, size, { fill, sw: 0, noShadow: true, align: "left" }),
      { box: { on: true, color: box, pad: Math.round(size * 0.14), radius: Math.round(size * 0.1) }, shadow: { on: true, color: "#000000", blur: Math.round(size * 0.25), dx: 0, dy: Math.round(size * 0.06), opacity: 0.55 } }));
    l.runs = digitRuns(t, l.font);
    fitText(l); fitW(l, maxW); return l;
  };
  const sz0 = o.size || ctx.H * 0.19, sz = Math.max(sz0 * Math.min(1, (ctx.brand._st && ctx.brand._st.textScale) || 1), Math.min(sz0, BIG_MIN[ctx.short ? "short" : "long"] * ctx.H * 1.03));  // 작게 써도 게이트 밑으로는 안 줄임
  const big = mk(two ? (bigI ? c.l2 : c.l1) : c.l1, sz, "#111111", b.hl, "제목 큰 줄");
  const small = two ? mk(bigI ? c.l1 : c.l2, sz * (o.ratio || 0.58), "#FFFFFF", b.box, "제목 작은 줄") : null;
  const gap = Math.round(ctx.H * 0.03), order = !small ? [big] : bigI ? [small, big] : [big, small];
  let y = yBottom - order.reduce((a, l) => a + l.h, 0) - gap * (order.length - 1);
  for (const l of order) { l.x = x; l.y = y; y += l.h + gap; if (o.rot) l.rot = o.rot; }
  return { layers: order, box: bbox(order), big };
}
function seriesLabel(ctx, x, y) {
  const s = (ctx.brand.series || "").trim(); if (!s) return null;
  const l = L("text", Object.assign({ text: s, name: "시리즈 이름" }, tStyle(ctx, Math.round(ctx.H * (ctx.short ? 0.04 : 0.075)), { fill: "#EDEDED", font: "Pretendard Black", sw: 0.12, align: "left" })));
  l.shadow = { on: true, color: "#000000", blur: 8, dx: 0, dy: 3, opacity: 0.8 }; fitText(l); l.x = x; l.y = y; return l;
}
// 판정 5회차(D-090): 레퍼런스 롱폼 36%·쇼츠 59% 에 채널 표시(로고·시리즈) · 우리 0% (로고를 안 올리면 없음) → 로고가 없으면 채널 이름으로 둥근 엠블럼을 그려 씀
const AUTO_LOGO = {};
function autoLogo(br) {
  const name = String(br.handle || "").replace(/^@/, "").trim() || "풋살사관학교", hl = (br.colors && br.colors.hl) || "#FFE14D", key = name + "|" + hl;
  if (AUTO_LOGO[key]) return AUTO_LOGO[key];
  const ready = !document.fonts || document.fonts.check('40px "Pretendard Black"');
  const S0 = 256, c = newCanvas(S0, S0), g = c.getContext("2d"), cut = name.length >= 5 ? Math.ceil(name.length / 3) : 0;
  const lines = cut ? [name.slice(0, cut), name.slice(cut)] : [name];
  g.beginPath(); g.arc(S0 / 2, S0 / 2, S0 / 2 - 4, 0, Math.PI * 2); g.fillStyle = "#111111"; g.fill();
  g.lineWidth = 12; g.strokeStyle = hl; g.stroke();
  g.beginPath(); g.arc(S0 / 2, S0 / 2, S0 / 2 - 22, 0, Math.PI * 2); g.lineWidth = 3; g.strokeStyle = "#FFFFFF"; g.stroke();
  g.textAlign = "center"; g.textBaseline = "middle";
  lines.forEach((t, i) => {
    let sz = lines.length > 1 ? (i ? 50 : 60) : 64; g.font = `${sz}px "Pretendard Black"`;
    while (g.measureText(t).width > S0 * 0.7 && sz > 20) { sz -= 2; g.font = `${sz}px "Pretendard Black"`; }
    g.fillStyle = i ? hl : "#FFFFFF"; g.fillText(t, S0 / 2, S0 / 2 + (lines.length > 1 ? (i ? 34 : -30) : 0));
  });
  const url = c.toDataURL("image/png");
  if (ready) AUTO_LOGO[key] = url;
  return url;
}
function logoLayer(ctx, avoid) {  // 채널 로고 (높이 0.11H · 직접 올린 로고가 없으면 채널 이름 엠블럼 0.13H) — 제목·안전 영역과 겹치면 반대쪽, 그래도 겹치면 뺌
  const br = ctx.brand; if (br.logoPos === "off" || (!br.logo && br.autoLogo === false)) return null;
  const own = !!br.logo, h = Math.round(ctx.H * (ctx.short ? 0.06 : own ? 0.11 : 0.13)), w = own ? h * 1.6 : h, m = Math.round(ctx.W * 0.025);
  const src = own ? br.logo : autoLogo(br);
  for (const pos of [br.logoPos || "tr", br.logoPos === "tl" ? "tr" : "tl"]) {
    const l = L("image", { name: "로고", src, fit: "contain", w, h, y: m + (ctx.short ? ctx.H * 0.07 : 0), x: pos === "tr" ? ctx.W - w - m - (ctx.short ? ctx.W * 0.16 : 0) : m,
      shadow: { on: true, color: "#000000", blur: 12, dx: 0, dy: 4, opacity: 0.6 } });
    if (!avoid.some(b => overlap(bbox([l]), b, 8))) return l;
  }
  return null;
}
// 이모지 스티커 (Fluent Emoji 3D · stickers/): 문구 성격(tag)에 맞춰 큰 줄 끝에 하나 — 레퍼런스의 😱·👀 자리
const EMOJI = { 놀람: "face-screaming-in-fear.png", 보기: "eyes.png", 오답: "cross-mark.png", 정답: "check-mark-button.png", 불: "fire.png", 질문: "thinking-face.png" };
function emojiLayer(ctx, near, avoid, o = {}) {
  if (!near) return null;
  const file = o.file || EMOJI[ctx.copy.tag] || "fire.png", s = Math.round(clamp(near.h * (o.k || 0.95), ctx.H * (ctx.short ? 0.075 : 0.16), ctx.H * (ctx.short ? 0.09 : 0.22)));  // 목록 크기에서 14px 넘게
  const spots = [[near.x + near.w - s * 0.12, near.y - s * 0.42], [near.x + near.w + s * 0.02, near.y + near.h * 0.5 - s * 0.5], [near.x - s * 0.88, near.y - s * 0.3]];
  for (const [x, y] of spots) {
    const l = L("image", { name: "스티커", src: `/stickers/${file}`, fit: "contain", w: s, h: s, x, y, rot: o.rot ?? 12, shadow: { on: true, color: "#000000", blur: 10, dx: 0, dy: 5, opacity: 0.5 } });
    const bb = extentOf(l);
    if (inSafe(ctx, bb, 0) && !avoid.some(a => overlap(bb, a, 4))) return l;
  }
  return null;
}
function hamming(a, b) {  // 장면 지문(16자리 16진수) 사이 다른 비트 수 (thumb.hamming 과 같음)
  if (!a || !b || a.length !== b.length) return 64;
  let n = 0; for (let i = 0; i < a.length; i++) { let v = parseInt(a[i], 16) ^ parseInt(b[i], 16); if (Number.isNaN(v)) return 64; while (v) { n += v & 1; v >>= 1; } }
  return n;
}
const overlap = (a, b, pad = 0) => a.x < b.x + b.w + pad && b.x < a.x + a.w + pad && a.y < b.y + b.h + pad && b.y < a.y + a.h + pad;
function inSafe(ctx, b, pad = 0) {
  const s = SAFE[ctx.short ? "short" : "long"], W0 = ctx.W, H0 = ctx.H, x1 = s.x1Top && b.y + b.h <= s.top * H0 ? s.x1Top : s.x1;
  if (b.x < s.x0 * W0 - pad || b.y < s.y0 * H0 - pad || b.x + b.w > x1 * W0 + pad || b.y + b.h > s.y1 * H0 + pad) return false;
  if (!ctx.short && b.x + b.w > s.dur[0] * W0 && b.y + b.h > s.dur[1] * H0) return false;
  return true;
}
function headAvoid(ctx, bg) {  // 글자·그래픽이 피해야 할 주인공 머리(캔버스) — 없으면 []
  const hb = headBox(ctx.frame); if (!hb) return [];
  const b = boxC(bg, hb, ctx.ar); return [{ x: b.x - b.w * 0.1, y: b.y - b.h * 0.1, w: b.w * 1.2, h: b.h * 1.2 }];
}

/* ----- 전술 그래픽 자동 배치 (쪼살형): 주인공 발밑 원 · 선수들 사이 가장 큰 빈 자리에 '공간' 칩 + 주인공 → 그 자리로 곡선 화살표 · 동료 패스 점선 · 다른 선수 움직임(네온) ----- */
// 사람이 2명 미만·얼굴 클로즈업·벤치 장면·주인공 발이 안 보이면 넣지 않음 (판정: 장식처럼 붙은 '공간' 칩)
function tacShape(shape, o) { return normLayer(L("shape", Object.assign({ shape, name: TAC_NAMES[shape] }, o))); }
function arrowLayer(ctx, s, e, col, gid, o = {}) {  // s → e 로 위로 휘는 굵은 네온 곡선 화살표
  const scale = ctx.W / 1280, [sx, sy] = s, [ex, ey] = e;
  const mx2 = (sx + ex) / 2, my2 = (sy + ey) / 2, len = Math.hypot(ex - sx, ey - sy) || 1, nx = -(ey - sy) / len, ny = (ex - sx) / len;
  const bend = (ny < 0 ? -1 : 1) * (o.bend ?? 0.28) * len, cx = mx2 + nx * bend * (ny < 0 ? -1 : 1), cy = my2 - Math.abs(ny * bend) - 0.05 * len;
  const xs = [sx, cx, ex], ys = [sy, cy, ey], x0 = Math.min(...xs), y0 = Math.min(...ys), w = Math.max(30, Math.max(...xs) - x0), h = Math.max(30, Math.max(...ys) - y0);
  return tacShape("arrow2", { name: o.name || TAC_NAMES.arrow2, x: x0, y: y0, w, h, pts: xs.map((x, i) => [(x - x0) / w, (ys[i] - y0) / h]), fill: col,
    width: Math.round((o.width || 19) * scale), head: Math.round((o.head || 70) * scale), core: "#FFFFFF", gid, glow: { on: true, color: col, size: Math.round(30 * scale), opacity: 1 } });
}
// 판정 2회차: 선수 2명 장면의 '공간' 칩·화살표가 붙여 넣은 티(35건) → 선수 3명 이상 · 주인공이 화면 높이 0.25 넘게 보일 때만 자동으로 (사용자가 메뉴로 부르면 느슨하게)
const TAC_MIN_P = 2, TAC_MIN_H = 0.25;  // 판정 5회차(D-091): 3 → 2 (전술 그래픽이 롱폼 5.6% 에만 · 레퍼런스 그래픽 70%) — 발·크기·화살표 끝 검사는 그대로
function tipClear(pt, ps, W0) {  // 화살표 끝이 다른 선수 몸 상자에서 0.1W 넘게 떨어졌는지 (끝이 사람에 꽂히면 '붙여 넣은 티')
  return ps.every(p => { const b = p.box, dx = Math.max(b.x - pt[0], 0, pt[0] - (b.x + b.w)), dy = Math.max(b.y - pt[1], 0, pt[1] - (b.y + b.h)); return Math.hypot(dx, dy) >= 0.1 * W0; });
}
function tacOk(ctx) { const f = ctx.frame; return (f.persons || []).length >= TAC_MIN_P && f.main >= 0 && f.kind !== "close" && !(f.flags || []).includes("bench"); }
function tactics(ctx, bg, avoid, o = {}) {
  const f = ctx.frame, ps = f.persons || [], ar = ctx.ar, out = [], W0 = ctx.W, H0 = ctx.H, gid = "tac" + nid().slice(0, 4), b = ctx.brand.colors, scale = W0 / 1280;
  const loose = !!o.loose;  // 사용자가 메뉴에서 직접 부름: 선수 1명·클로즈업·발이 잘린 장면이어도 놓을 수 있는 만큼 (자동 추천은 엄격하게)
  if (loose ? !(ps.length && f.main >= 0) : !tacOk(ctx)) return out;
  const P = ps.map(p => { const q = boxC(bg, p, ar); return { x: q.x + q.w / 2, y: q.y + q.h, h: q.h, w: q.w, box: q }; });
  const feetIn = p => !(p.y > H0 * 0.985 || p.y < H0 * 0.3 || p.h < H0 * 0.1 || p.x < 0 || p.x > W0);
  let M = P[f.main];
  if (!loose && M.h < H0 * TAC_MIN_H) return out;  // 주인공이 작으면 그래픽만 떠 보임
  if (!feetIn(M)) { if (!loose) return out; M = Object.assign({}, M, { x: clamp(M.x, W0 * 0.15, W0 * 0.85), y: clamp(M.y, H0 * 0.45, H0 * 0.85), noRing: true }); }  // 주인공 발이 화면 안에 있어야 (발밑 원·출발점)
  let vis = P.filter(p => p.x > W0 * 0.02 && p.x < W0 * 0.98 && p.y > H0 * 0.25 && p.y < H0 * 1.02);
  if (loose && !vis.length) vis = [M];
  if (vis.length < (loose ? 1 : 2)) return out;
  const taken = [], body = vis.map(p => ({ x: p.box.x - p.box.w * 0.1, y: p.box.y, w: p.box.w * 1.2, h: p.box.h }));
  // 검토(D-119): 원·칩은 화면 안에 다 (스톡 001 롱폼 3 '주인공 발밑 원이 왼쪽 끝에서 잘림') — 화살표만 끝이 조금 나가도 됨
  const ok = l => { const bb = bbox([l]), full = l.shape === "ring" || l.shape === "marker", sl = full ? 0 : bb.w * 0.2;
    return bb.x > -sl + (full ? W0 * 0.01 : 0) && bb.x + bb.w < W0 + sl - (full ? W0 * 0.01 : 0) && bb.y + bb.h < H0 && bb.y > 0 && !avoid.some(a => overlap(bb, a, 12)); };
  const put = l => { out.push(l); taken.push(bbox([l])); };
  const bl0 = f.ball ? boxC(bg, f.ball, ar) : null, ballC = bl0 ? [bl0.x + bl0.w / 2, bl0.y + bl0.h / 2] : null;
  const ringOnBall = () => { if (!ballC) return false; const rw = clamp(M.w * 1.7, 70 * scale, 360 * scale), rh = rw * 0.3;  // 판정 4회차: 원 안에 공이 들어오면 '공 밑 원'으로 읽힘
    return Math.abs(ballC[0] - M.x) < rw * 0.5 && Math.abs(ballC[1] - (M.y - rh * 0.05)) < rh * 0.9; };
  if (o.ring !== false && (!M.noRing || o.forceRing) && (loose || !ringOnBall())) {
    const rw = clamp(M.w * 1.7, 70 * scale, 360 * scale), rh = rw * 0.3;
    const ring = tacShape("ring", { x: M.x - rw / 2, y: M.y - rh * 0.55, w: rw, h: rh, fill: o.ringColor || b.neon, width: Math.round(10 * scale), gid,
      glow: { on: true, color: o.ringColor || b.neon, size: Math.round(24 * scale), opacity: 1 } });
    if (loose && ring.y + rh > H0 - 2) ring.y = Math.max(M.y - rh * 1.1, H0 - rh - 2);  // 직접 부름: 발이 화면 아래 끝에 붙어 원이 넘치면 조금 올려서
    if (ok(ring)) put(ring);
  }
  // 빈 자리: 선수들 발 사이 (선수 무리 둘레 안) 중 모든 선수 발에서 가장 먼 곳 — 원근 때문에 세로 거리를 더 크게 셈 · 주인공에서 알맞게 떨어진 곳
  const xs = vis.map(p => p.x), ys = vis.map(p => p.y), cs = Math.round((ctx.short ? 0.1 : 0.22) * H0);  // 판정: 칩·화살표가 목록 크기에서 안 보임 → 크게
  const wx = loose ? 0.35 : 0.12, wy = loose ? 0.3 : 0.12;  // 느슨하게: 선수 한 명이면 둘레가 없으니 넓게
  // 판정 4회차: '공간' 칩이 벽 쪽 빨간 테두리·콘 위에 → 빈 자리는 선수 발 높이(바닥) 근처에서만 (위로는 가장 높은 발보다 0.12H(up 0.2H)까지)
  const zone = { x0: Math.max(W0 * 0.07, Math.min(...xs) - W0 * wx), x1: Math.min(W0 * 0.93, Math.max(...xs) + W0 * wx),
    y0: Math.max(H0 * (o.up || loose ? 0.12 : 0.38), Math.min(...ys) - H0 * (o.up ? 0.2 : loose ? 0.3 : wy)), y1: Math.min(H0 * 0.9, Math.max(...ys) + H0 * 0.06) };  // up: 제목이 아래에 있으면 위쪽 빈 자리도
  const others = vis.filter(p => p !== M && p !== P[f.main]);
  const freeSpot = (from, dmin, dmax, away = []) => {
    let best = null;
    for (let x = zone.x0; x <= zone.x1; x += W0 * 0.02) for (let y = zone.y0; y <= zone.y1; y += H0 * 0.025) {
      const d = Math.hypot(x - from[0], y - from[1]);
      if (d < dmin * W0 || d > dmax * W0 || away.some(q => Math.hypot(q[0] - x, q[1] - y) < 0.18 * W0)) continue;
      const chip = { x: x - cs / 2, y: y - cs / 2, w: cs, h: cs };
      if (avoid.some(a => overlap(chip, a, 14)) || taken.some(a => overlap(chip, a, 6)) || body.some(a => overlap(chip, a, 4)) || !inSafe(ctx, chip, 0)) continue;
      if (!loose && !tipClear([x, y], others, W0)) continue;  // 판정 4회차: 끝이 선수 옆이면 다음 자리 (예전엔 화살표·칩을 통째로 버림)
      if (!loose && ballC && Math.hypot(ballC[0] - x, ballC[1] - y) < 0.12 * W0) continue;  // '공간'은 공 옆이 아니라 비어 있는 곳
      const free = Math.min(...vis.map(p => Math.hypot(p.x - x, (p.y - y) * 1.7)));
      const sc = free * 1.4 - Math.abs(d - 0.32 * W0) * 0.2;  // 가장 넓게 빈 곳 먼저
      if (!best || sc > best.sc) best = { x, y, sc, free };
    }
    return best && best.free > W0 * 0.1 ? best : null;  // 판정 3회차: 화살표 끝이 선수 바로 옆(0.065W) → 0.1W 넘게
  };
  // 판정 q5 1회차(D-098): '공간' 칩 없는 화살표가 빈 잔디를 가리킴 (003_long_1 · 005_long_4 · 002_short_1 '엉뚱한 빈 잔디') → 칩이 없으면 동료 발밑으로 (패스·움직임 길)
  const mate = !loose && o.chip === false && o.arrow !== false
    ? others.map(p => [p, Math.hypot(p.x - M.x, p.y - M.y)]).filter(([p, d]) => d >= 0.15 * W0 && d <= 0.55 * W0 && p.y > H0 * 0.3 && p.y < H0 * 0.97 && p.h >= H0 * 0.1)
      .sort((a, b) => b[0].h - a[0].h)[0] : null;
  if (mate) {
    const p = mate[0], bl = bl0, bx = bl ? bl.x + bl.w / 2 : 0, by = bl ? bl.y + bl.h / 2 : 0;
    const fromBall = bl && bx > 0 && bx < W0 && by > H0 * 0.2 && by < H0 * 0.95 && Math.hypot(bx - M.x, by - M.y) < M.h * 0.8;
    const sx0 = fromBall ? bx + (p.x > bx ? 1 : -1) * bl.w * 0.7 : M.x + (p.x > M.x ? 1 : -1) * M.w * 0.3, sy = fromBall ? by : M.y - M.h * 0.04;
    const d = Math.hypot(p.x - sx0, p.y - sy), stop = Math.min(0.4, (p.w * 0.6 + W0 * 0.02) / d);
    const end = [p.x - (p.x - sx0) * stop, p.y - p.h * 0.05 - (p.y - sy) * stop];
    const arr = arrowLayer(ctx, [sx0, sy], end, o.arrowColor || b.accent, gid, { bend: 0.22, name: "패스 화살표", width: 24, head: 80 });  // D-114: 판정 '화살표가 작아 거의 안 보임' → 19 → 24px
    const third = body.filter((q, i) => vis[i] !== p && vis[i] !== M && vis[i] !== P[f.main]);  // 출발·도착 선수 말고 다른 선수 몸을 가로지르지 않게
    if (ok(arr) && !third.some(q => overlap(bbox([arr]), q, -Math.min(q.w, q.h) * 0.35))) put(arr);
  }
  const best = o.arrow === false || o.chip === false && !loose ? null : freeSpot([M.x, M.y], ctx.short ? 0.25 : 0.2, ctx.short ? 0.6 : 0.45);  // 판정 3회차: 짧은 화살표는 '붙여 넣은 티' — 쪼살 원본은 큰 빛 화살표
  if (best) {
    // 화살표는 공 → 빈 자리 (쪼살 '공간' — 공이 보이면 공에서 출발) · 공이 없으면 주인공 발에서
    const bl = bl0, bx = bl ? bl.x + bl.w / 2 : 0, by = bl ? bl.y + bl.h / 2 : 0;
    const fromBall = bl && bx > 0 && bx < W0 && by > H0 * 0.2 && by < H0 * 0.95 && Math.hypot(bx - M.x, by - M.y) < M.h * 0.8;
    const sx0 = fromBall ? bx + (best.x > bx ? 1 : -1) * bl.w * 0.7 : M.x + (best.x > M.x ? 1 : -1) * M.w * 0.3, sy = fromBall ? by : M.y - M.h * 0.04;
    const dd = Math.hypot(best.x - sx0, best.y - sy), cut = o.chip === false ? 0 : (cs * 0.62) / dd;
    const col = o.arrowColor || b.accent;
    const end = [best.x - (best.x - sx0) * cut, best.y - (best.y - sy) * cut];
    const arr = arrowLayer(ctx, [sx0, sy], end, col, gid, { bend: 0.24 });
    if (ok(arr) && (loose || tipClear(end, others, W0))) {
      put(arr);
      if (o.chip !== false) {
        const chip = tacShape("marker", { name: "공간 칩", x: best.x - cs / 2, y: best.y - cs / 2, w: cs, h: cs, fill: col, label: o.chipText || "공간",
          stroke: { color: "#FFFFFF", width: Math.round(6 * scale) }, gid, glow: { on: true, color: col, size: Math.round(46 * scale), opacity: 1 } });  // 빛 번짐 크게 (쪼살 '공간')
        if (ok(chip)) put(chip);
      }
    }
  }
  // 다른 선수 움직임 (네온): 공과 먼 동료 하나가 다른 빈 자리로 뛰는 길
  if (o.second) {
    const from = vis.filter(p => p !== M && p !== P[f.main] && Math.hypot(p.x - M.x, p.y - M.y) > 0.1 * W0).sort((p, q) => q.y - p.y)[0];
    const c2 = from && freeSpot([from.x, from.y], 0.12, 0.3, best ? [[best.x, best.y], [M.x, M.y]] : [[M.x, M.y]]);
    if (c2) {
      const a2 = arrowLayer(ctx, [from.x, from.y - from.h * 0.04], [c2.x, c2.y], o.secondColor || b.neon, gid, { name: "움직임 화살표", width: 17, head: 62, bend: 0.2 });
      if (ok(a2) && !taken.some(a => overlap(bbox([a2]), a, -Math.min(a.w, a.h) * 0.3))) put(a2);
    }
  }
  if (o.pass) {  // 가장 가까운 동료에게 패스 점선
    const near = vis.filter(p => p !== M && p !== P[f.main]).map(p => [p, Math.hypot(p.x - M.x, p.y - M.y)]).filter(x => x[1] > 0.1 * W0 && x[1] < 0.45 * W0).sort((p, q) => p[1] - q[1])[0];
    if (near) {
      const [px, py] = [near[0].x, near[0].y], x0 = Math.min(M.x, px), y0 = Math.min(M.y, py) - 0.06 * H0, w = Math.max(20, Math.abs(px - M.x)), h = Math.max(20, Math.abs(py - M.y) + 0.06 * H0);
      const pts = [[M.x, M.y], [(M.x + px) / 2, Math.min(M.y, py) - 0.06 * H0], [px, py]].map(([x, y]) => [(x - x0) / w, (y - y0) / h]);
      const pass = tacShape("pass", { x: x0, y: y0, w, h, pts, fill: b.hl2, width: Math.round(7 * scale), gid, glow: { on: true, color: b.neon, size: Math.round(14 * scale), opacity: 0.9 } });
      if (ok(pass) && !taken.some(a => overlap(bbox([pass]), a, -10))) put(pass);
    }
  }
  return out;
}

/* ----- 템플릿 (fn(ctx) → 레이어들 · needs: 장면 종류·누끼·선수 수·반전 문구) ----- */
function ctxFor(frame, copy, fmt, seed) {
  const short = fmt === "short", W0 = short ? 1080 : 1280, H0 = short ? 1920 : 720, ar = frameAspect(), ps = frame.persons || [];
  const m = frame.main >= 0 ? ps[frame.main] : null, fc = mainFace(frame);  // 초점 = 주인공 얼굴 (누끼도 주인공 상자로 땀)
  const focus = fc ? [fc[0] + fc[2] / 2, fc[1] + fc[3] * 0.9] : m ? [m[0] + m[2] / 2, m[1] + m[3] * 0.45] : [0.5, 0.5];
  const c = AI.cuts[String(frame.t)] || null;
  const cut = c && (!c.q || c.q.ok) ? c : null;  // 품질 검사(새는 곳·조각·얼굴 빈 곳·흐린 테두리)를 못 넘은 누끼는 쓰지 않음
  return { frame, frames: AI.frames, copy, cut, persons: ps, ball: frame.ball, brand: brandOf(), W: W0, H: H0, short, ar, seed, focus, band: frame.band };
}
const shade = (name, x, y, w, h, top, op, horiz) => L("shape", { name, x, y, w, h, fill: top ? "#000000" : "rgba(0,0,0,0)", fill2: top ? "rgba(0,0,0,0)" : "#000000", gradAngle: horiz ? 0 : 90, opacity: op });
// 판정 q5 2회차(D-116): '머리가 제목 바로 아래에 붙어 답답'(쇼츠 crop 4.2) — 제목 아래 머리 자리 0.035 → 0.05H · 머리가 올라갈 수 있는 끝 0.01 → 0.025H
const UNDER_TITLE = { head: 0.05, min: 0.025 };
const underTitle = (ctx, hd, k = 1) => ({ headY: hd.box.y + hd.box.h + ctx.H * UNDER_TITLE.head * k, minHeadY: hd.box.y + hd.box.h + ctx.H * UNDER_TITLE.min, headroom: 0 });
// 제목 아래(또는 위)에 주인공이 오도록 배경 놓기: 주인공 머리 = 제목 아래 + 여백 · 주인공 키가 남은 높이에 들어가게 확대
function frameUnder(ctx, hdBox, o = {}) {
  const m = mainBox(ctx.frame), mg = ctx.H * UNDER_TITLE.head;  // D-116: 0.035 → 0.05H (제목 바로 아래 머리)
  if (!m) return frameLayer(ctx, { zoom: o.zoom || 1.05, target: o.target || [ctx.W * 0.5, ctx.H * 0.6] });
  const room = ctx.H - (hdBox.y + hdBox.h) - mg * 2, want = Math.min(o.subject || 0.5, room / ctx.H);
  const z = zoomFor(ctx, want, 1, o.max || 1.6);
  return frameLayer(ctx, { zoom: z, focus: [m[0] + m[2] / 2, m[1]], target: [ctx.W * (o.tx ?? 0.5), hdBox.y + hdBox.h + mg], minHeadY: hdBox.y + hdBox.h + ctx.H * UNDER_TITLE.min, headroom: 0 });
}
function frameAbove(ctx, hdBox, o = {}) {  // 주인공 발 = 제목 위
  const m = mainBox(ctx.frame), mg = ctx.H * 0.02;
  if (!m) return frameLayer(ctx, { zoom: o.zoom || 1.05, target: [ctx.W * 0.5, ctx.H * 0.4] });
  const room = hdBox.y - ctx.H * 0.04, z = zoomFor(ctx, Math.min(o.subject || 0.45, room / ctx.H), 1, o.max || 1.6);
  return frameLayer(ctx, { zoom: z, focus: [m[0] + m[2] / 2, m[1] + m[3]], target: [ctx.W * (o.tx ?? 0.5), hdBox.y - mg] });
}
// 지켜야 할 머리들 (장면 좌표 0~1): 주인공 머리 + 키가 화면 높이 0.15 넘는 다른 사람 머리 (얼굴이 있으면 얼굴, 없으면 몸 상자 위 18% 가운데)
// 판정 5회차(D-091): 롱폼 31% 가 제목 아래 다른 선수 머리 · '글자가 사람 가림' 18번 — 예전엔 주인공 머리만 봄
function headsOf(f, minH = 0.15) {
  const out = [], m = mainBox(f), hb = headBox(f);
  if (hb) out.push({ box: hb, main: true });
  for (const p of (f && f.persons) || []) {
    if (p === m || p[3] < minH || p[1] <= 0.012) continue;
    const fc = ((f.faces || []).map(x => x.box)).find(q => { const cx = q[0] + q[2] / 2, cy = q[1] + q[3] / 2; return cx >= p[0] && cx <= p[0] + p[2] && cy >= p[1] - 0.02 && cy <= p[1] + p[3] * 0.45; });
    out.push({ box: fc ? [fc[0] - fc[2] * 0.12, Math.max(0, fc[1] - fc[3] * 0.6), fc[2] * 1.24, fc[3] * 1.65] : [p[0] + p[2] * 0.22, p[1], p[2] * 0.56, Math.min(p[3] * 0.18, p[2] * 1.2)], main: false });
  }
  return out;
}
// 제목 레이어들이 머리를 덮는 정도: 주인공 머리 1 + 다른 사람 머리 0.5 씩 (보이는 머리만)
function headOverlap(ctx, bg, layers) {
  const view = clipTo({ x: bg.x, y: bg.y, w: bg.w, h: bg.h }, { x: 0, y: 0, w: ctx.W, h: ctx.H });
  return headsOf(ctx.frame).reduce((s, hd) => {
    const b = clipTo(boxC(bg, hd.box, ctx.ar), view), a = areaOf(b); if (!a) return s;
    return s + (hd.main ? 1 : 0.5) * Math.min(1, layers.reduce((t, l) => t + interArea(extentOf(l), b) / a, 0));
  }, 0);
}
// 위 띠 롱폼 (쪼살형): 가장 넓은 줄이 0.7W 넘게 · 위 끝 0.035H · 장면은 주인공 머리가 제목 바로 아래 — ① 두 줄 전체 폭 ② 짧으면 한 줄 (③ 옆 절반은 side 일 때만)
// 판정 5회차(D-091): 제목은 줄이지 않고 장면(frameUnder)을 옮김 — 머리를 피하려고 제목을 줄이면 더 낮음 (VL −0.23)
function topLayouts(ctx, ho, fo, side) {
  const c = ctx.copy, m = mainBox(ctx.frame), mx = m ? m[0] + m[2] / 2 : 0.5;
  const one = c.l2 && `${c.l1} ${c.l2}`.length <= LINE_MAX_FMT.long;  // 한 줄로 합쳐도 줄 길이 안일 때만
  const H0 = ctx.H, W0 = ctx.W, base = Object.assign({ grow: 1.8 }, ho);
  const tries = [() => headline(ctx, { x: W0 * 0.04, y: H0 * 0.04, w: W0 * 0.905, h: H0 * 0.42 }, base)];  // 그림자 번짐까지 0.965W 안
  if (one) tries.push(() => headline(Object.assign({}, ctx, { copy: joinCopy(c) }), { x: W0 * 0.04, y: H0 * 0.04, w: W0 * 0.905, h: H0 * 0.24 }, Object.assign({}, base, { size: Math.round(H0 * 0.2) })));
  if (side) tries.push(() => headline(ctx, { x: W0 * (mx > 0.5 ? 0.075 : 0.5), y: H0 * 0.05, w: W0 * 0.425, h: H0 * 0.5 }, Object.assign({}, ho, { align: mx > 0.5 ? "left" : "right" })));
  let best = null;
  for (const mk of tries) {
    const hd = mk(), bg = frameUnder(ctx, hd.box, fo), ov = headOverlap(ctx, bg, hd.layers);
    if (!best || ov < best.ov - 0.05) best = { hd, bg, ov };
    if (ov < 0.1) break;
  }
  return best;
}
// 아래 띠 롱폼 (쌈바형): 왼쪽 아래 두 줄 (아래 끝 0.95H 안 · 재생시간 자리 0.84W 앞에서 끝) · 주인공은 가운데~오른쪽, 머리는 위쪽
// 판정 q5 1회차(D-098): 주인공을 0.6W 에 두고 제목을 0.04~0.82W 로 펴서 13장 중 대부분이 다리·공·몸을 가림('가림/겹침' 59번) →
// 주인공을 오른쪽(0.7W)에 놓고 제목은 주인공 몸 왼쪽 빈 곳에만 · 주인공이 왼쪽에 남으면 제목을 주인공 오른쪽(재생시간 자리 앞)에
function bottomLayout(ctx, ho = {}, fo = {}) {
  const W0 = ctx.W, H0 = ctx.H, m = mainBox(ctx.frame), mg = W0 * 0.035;
  const opts = Object.assign({ style: "word", align: "left", anchor: "bottom", size: Math.round(H0 * 0.2), grow: 1.8, kicker: true, emphScale: 1.08 }, ho);
  const box = { x: W0 * 0.04, y: H0 * 0.42, w: W0 * 0.78, h: H0 * 0.53 };
  if (!m) return { hd: headline(ctx, box, opts), bg: frameLayer(ctx, { zoom: 1.05, target: [W0 * 0.5, H0 * 0.45] }) };
  const z = zoomFor(ctx, fo.subject || 0.72, 1, fo.max || 2.6), focus = [m[0] + m[2] / 2, m[1]];
  let bg = frameLayer(ctx, { zoom: z, focus, target: [W0 * (fo.tx ?? 0.7), H0 * (fo.headY ?? 0.1)] });
  let pb = clipTo(boxC(bg, m, ctx.ar), { x: 0, y: 0, w: W0, h: H0 });
  if (pb.x + pb.w / 2 < W0 * 0.45 && fo.tx == null) {  // 장면을 옮길 수 없어 주인공이 왼쪽에 남음 → 왼쪽으로 붙이고 제목은 오른쪽
    bg = frameLayer(ctx, { zoom: z, focus, target: [W0 * 0.3, H0 * (fo.headY ?? 0.1)] }); pb = clipTo(boxC(bg, m, ctx.ar), { x: 0, y: 0, w: W0, h: H0 });
    const x0 = Math.max(W0 * 0.04, pb.x + pb.w + mg);
    if (W0 * 0.82 - x0 >= W0 * 0.45) return { hd: headline(ctx, { x: x0, y: box.y, w: W0 * 0.82 - x0, h: box.h }, opts), bg };
  }
  const w = clamp(pb.x - mg - box.x, W0 * 0.6, box.w);  // 0.5W 까지 줄이면 제목이 작아져 읽기·위계가 내려감 (개발 판정 r_d·r_e)
  return { hd: headline(ctx, Object.assign({}, box, { w }), opts), bg };
}
// 쇼츠: 제목 아래 자기 칸에 장면 (위 끝은 검은 바탕으로 부드럽게 사라짐)
// — 가로 영상으로 만든 쇼츠는 화면 높이가 곧 장면 높이라 확대 없이는 머리를 제목 아래로 내릴 수 없음 (판정: 제목이 선수 머리를 덮음)
// 판정 2회차: 쇼츠 42장이 모두 '검은 띠 + 아래 장면'이고 720p 가로 장면을 1.8~1.9배 키워 흐림 → 장면 칸 높이를 원본 확대 상한(srcCap) 안으로 줄이고,
// 칸 밖(제목 뒤·아래)은 같은 장면을 크게 흐린 판으로 채움 (검은 띠 대신 · 칸 위아래 끝은 흐린 판으로 녹아듦)
function blurBack(ctx, o = {}) {
  const b = frameLayer(ctx, { zoom: 1, keep: false, grade: false });
  return Object.assign(b, { name: "흐린 배경", blur: Math.round(ctx.W * 0.035), bright: o.bright ?? 48, sat: 85, vignette: 40 });
}
function panelH(ctx, room) {  // 가로 장면을 W 폭 칸에 덮을 때 원본 확대 상한을 넘지 않는 칸 높이
  const a = arEff(ctx);
  if (a <= ctx.W / room) return room;  // 칸이 장면보다 납작하면 폭으로 맞춰져 높이 제한 없음
  return Math.min(room, Math.round(srcCap() * srcW() / a));  // 높이로 덮으면 원본 1px = 칸 높이 × (띠를 잘라낸 뒤 가로/세로) / 원본 폭
}
// 이미지 레이어에서 clip(캔버스 좌표) 밖으로 나간 부분을 자르기(crop)로 바꿔 레이어를 clip 안에만 두기 — 그림의 위치·배율은 그대로 (회전·좌우 뒤집기 없는 레이어)
function bakeCrop(l, ar, clip) {
  if (l.flipX || l.rot) return l;
  const r = imgRect(l, ar), ix = l.x + r.dx, iy = l.y + r.dy;
  const v = clipTo(clipTo({ x: l.x, y: l.y, w: l.w, h: l.h }, { x: ix, y: iy, w: r.dw, h: r.dh }), clip);
  if (!areaOf(v) || (Math.abs(v.x - l.x) < 0.5 && Math.abs(v.y - l.y) < 0.5 && Math.abs(v.w - l.w) < 0.5 && Math.abs(v.h - l.h) < 0.5 && Math.abs(r.dw - l.w) < 0.5 && Math.abs(r.dh - l.h) < 0.5)) return l;
  const u = X => (l.cropL || 0) + (X - ix) / r.dw * r.cw, w = Y => (l.cropT || 0) + (Y - iy) / r.dh * r.ch;
  return Object.assign(l, { x: v.x, y: v.y, w: v.w, h: v.h, cropL: u(v.x), cropR: 1 - u(v.x + v.w), cropT: w(v.y), cropB: 1 - w(v.y + v.h), fx: 0.5, fy: 0.5 });
}
function framesBelow(ctx, hdBox, subject = 0.62, o = {}) {
  const py0 = Math.round(hdBox.y + hdBox.h - ctx.H * (o.overlap ?? 0.02)), room = ctx.H - py0, ph = panelH(ctx, room);
  // 칸이 짧으면 아래 끝을 0.9H 근처에 (판정: '아래 1/3 이 흐린 빈 공간') — 남는 곳은 제목 뒤 흐린 판이 됨 · 칸 위가 제목에서 너무 멀어지지 않게
  const py = ph < room ? Math.round(clamp(ctx.H * 0.9 - ph, py0, py0 + ctx.H * 0.08)) : py0;
  const sub = Object.assign({}, ctx, { H: ph }), m = mainBox(ctx.frame);
  const back = blurBack(ctx);
  const fo = o.headY != null && m ? { focus: [m[0] + m[2] / 2, m[1]], target: [ctx.W * 0.5, o.headY - py] } : { target: [ctx.W * 0.5, ph * (o.ty ?? 0.5)] };  // headY: 주인공 머리를 이 높이에 (제목이 칸 위로 들어올 때)
  const f = bakeCrop(frameLayer(sub, Object.assign({ zoom: zoomFor(sub, subject, 1, 2) }, fo)), ctx.ar, { x: 0, y: 0, w: ctx.W, h: ph });
  f.y += py;  // 검토(D-119): 확대한 레이어가 칸보다 커서 위 띠(레터박스 검은 띠·흰 띠)로 올라가던 것 → 칸 밖은 자르기(crop)로 잘라 레이어가 칸 안에만
  f.fade = o.hardTop ? (ph < room ? { on: true, angle: 90, start: 0.9, end: 1 } : { on: false, angle: 90, start: 0.9, end: 1 })
    : ph < room ? { on: true, angle: 90, start: 0.9, end: 1, both: true } : { on: true, angle: -90, start: 0.88, end: 1 };  // 위(·아래) 끝이 흐린 판으로 부드럽게
  return { back, f, py, ph };
}
// 코치(얼굴 장면) 누끼 + 같은 영상의 공 다루는 장면 — 판정: 인터뷰 얼굴만 있으면 '무엇을 배우는지 장면이 없음'
function actionFrame(ctx) {
  const maxS = Math.max(1e-6, ...AI.frames.map(f => f.score || 0)), g = sceneGroups(AI.frames);
  return AI.frames.filter(f => f !== ctx.frame && f.kind !== "close" && (f.persons || []).length >= 2 && (f.text || 0) < TEXTY && g[f.t] !== g[ctx.frame.t]
    && !(f.flags || []).some(x => x === "bench" || x === "crowd")).sort((a, b) => frameQ(b, maxS) - frameQ(a, maxS))[0] || null;
}
function placeCut(ctx, m, hTarget, tx, ty, o = {}) {  // 누끼를 배경과 따로: 주인공 상자 키 = hTarget, 머리 위 가운데 = (tx, ty)
  // 판정 5회차: 레이어를 원본 모양(가로/세로)으로 — 예전엔 캔버스 모양(16:9)이라 세로 원본 누끼가 레이어 밖으로 잘려 머리 없는 몸통만 남음
  const l = L("image", { name: "누끼", src: ctx.cut.cut, orig: ctx.cut.src, x: 0, y: 0, w: ctx.H * ctx.ar, h: ctx.H, fit: "cover",
    outline: { on: true, color: o.outline || "#FFFFFF", width: Math.round(ctx.W * 0.004) }, shadow: { on: true, color: "#000000", blur: 30, dx: 0, dy: 10, opacity: 0.75 } });
  const z = hTarget / Math.max(1, m[3] * imgRect(l, ctx.ar).dh); l.w *= z; l.h *= z;
  const r = imgRect(l, ctx.ar);
  l.x = tx - (r.dx + (m[0] + m[2] / 2) * r.dw); l.y = ty - (r.dy + m[1] * r.dh);
  // 원본 화면 끝에 걸려 잘린 몸(인터뷰 상반신 아래·옆)은 그 잘린 선이 캔버스 끝에 오게 (가운데에 직선으로 잘린 누끼가 뜨지 않게)
  if (m[1] + m[3] > 0.97) l.y = Math.max(l.y, ctx.H - (r.dy + r.dh));
  if (m[0] < 0.01) l.x = Math.min(l.x, -r.dx);
  else if (m[0] + m[2] > 0.99) l.x = Math.max(l.x, ctx.W - (r.dx + r.dw));
  else { const b = boxC(l, m, ctx.ar); if (b.x + b.w > ctx.W * 0.985) l.x -= b.x + b.w - ctx.W * 0.985; else if (b.x < ctx.W * 0.015) l.x += ctx.W * 0.015 - b.x; }  // 몸이 캔버스 옆으로 잘리지 않게
  if (ctx.frame.grade) l.grade = Object.assign({}, ctx.frame.grade, { amt: 1 });
  return l;
}
// 판정 q5 2회차 검토(D-119): 누끼를 배경과 따로 옮긴 틀에서 배경에 그대로 남은 주인공이 누끼 밖으로 보이는 몫 (0~1) — '뒤에 겹친 흐린 같은 사람'(쇼츠 누끼 크게) ·
// 배경 주인공이 누끼보다 크면 누끼 뒤에 숨길 수 없음 (1) · 누끼는 상자의 가운데 80% 폭만 사람이 채운다고 봄
const GHOST_MAX = 0.25;
function ghostOf(ctx, bg, cut) {
  const m = mainBox(ctx.frame); if (!m || !bg || !cut || sameBox(cut, bg)) return 0;
  const view = clipTo({ x: bg.x, y: bg.y, w: bg.w, h: bg.h }, { x: 0, y: 0, w: ctx.W, h: ctx.H });
  const b0 = boxC(bg, m, ctx.ar), b = clipTo(b0, view), a = areaOf(b); if (!a || b.h < ctx.H * 0.08) return 0;  // 화면 밖·아주 작은 배경 주인공은 안 보임
  const c = boxC(cut, m, ctx.ar);
  if (b0.h > c.h * 1.02) return 1;
  return 1 - interArea(b, { x: c.x + c.w * 0.1, y: c.y, w: c.w * 0.8, h: c.h }) / a;
}
// 누끼 마스크(주인공 상자 좌우 8%·위아래 5% 넓힌 곳의 사람 덩어리)에 다른 사람 머리가 들어갈 장면 — 따로 옮긴 누끼에 남의 머리가 붙어 다님 (판정 '어색한 합성' 005_short_3)
function cutCrowded(f) {
  const m = mainBox(f); if (!m) return false;
  const box = { x: m[0] - m[2] * 0.08, y: m[1] - m[3] * 0.05, w: m[2] * 1.16, h: m[3] * 1.1 }, mf = mainFace(f);
  const heads = headsOf(f, 0.05).filter(h => !h.main).map(h => h.box).concat((f.faces || []).map(x => x.box).filter(q => q !== mf && !(mf && interArea({ x: q[0], y: q[1], w: q[2], h: q[3] }, { x: mf[0], y: mf[1], w: mf[2], h: mf[3] }) > 0)));
  return heads.some(h => { const hb = { x: h[0], y: h[1], w: h[2], h: h[3] }; return areaOf(hb) > 0 && interArea(hb, box) > areaOf(hb) * 0.4; });
}
// 동작 중인 장면 (서 있기만 한 사람 누끼는 '가만히 서 있는 누끼' · '동작이 없음'): 공 다루는 순간 · 믿을 만한 공이 발 근처 · 팔다리를 벌림(주인공 상자 폭/키 0.5 넘게, 화소 기준)
function actionPose(f) {
  const m = mainBox(f); if (!m) return false;
  if ((f.flags || []).includes("lesson")) return true;
  if (ballSure(f)) { const b = f.ball, rel = (b[1] + b[3] / 2 - m[1]) / Math.max(1e-6, m[3]); if (rel >= 0.55 && rel <= 1.15) return true; }
  return m[2] * frameAspect() / Math.max(1e-6, m[3]) >= 0.5;
}
// 판정 4회차: 쇼츠가 '16:9 장면 + 아래 흐린 판' (720p 원본은 확대 상한 1.35배라 장면 칸이 화면 51%) → 두 장면을 위아래 칸에 꽉 채움
// 칸(W × h)에 장면을 덮고, 주인공이 칸 높이의 subject 만큼 보이게 자르기로 확대(원본 확대 상한 안) · focus 를 칸 안 target 에 · 칸 밖으로 넘치지 않음
function panelLayer(ctx, f, y0, h, o = {}) {
  const c = Object.assign(ctxFor(f, ctx.copy, ctx.short ? "short" : "long", ctx.seed), { H: h }), m = mainBox(f);
  const focus = o.focus || (m ? [m[0] + m[2] / 2, m[1] + m[3] * (o.fv ?? 0)] : c.focus);
  const l = L("image", { name: o.name || "배경", src: frameSrc(f.t), x: 0, y: 0, w: ctx.W, h, fit: "cover" });
  if (f.grade) l.grade = Object.assign({}, f.grade, { amt: 1 });
  if (c.band === "bottom") l.cropB = bandCrop(c); else if (c.band === "top") l.cropT = bandCrop(c);
  const r0 = imgRect(l, c.ar), up0 = r0.dw / r0.cw / srcW(), ch0 = 1 - (l.cropT || 0) - (l.cropB || 0);
  const want = m ? (o.subject || 0.7) * h / Math.max(1, m[3] / ch0 * r0.dh) : 1, z = clamp(want, 1, Math.max(1, srcCap() / up0));
  if (z > 1.001) {  // 초점 둘레로 잘라서 확대 (가로·세로 같은 비율 — 장면 모양 그대로)
    const cw = (1 - (l.cropL || 0) - (l.cropR || 0)) / z, chh = ch0 / z;
    const cl = clamp(focus[0] - cw / 2, l.cropL || 0, 1 - (l.cropR || 0) - cw), ct = clamp(focus[1] - chh * (o.fy ?? 0.3), l.cropT || 0, 1 - (l.cropB || 0) - chh);
    Object.assign(l, { cropL: cl, cropR: 1 - cl - cw, cropT: ct, cropB: 1 - ct - chh });
  }
  const r = imgRect(l, c.ar), u = (focus[0] - (l.cropL || 0)) / r.cw, v = (focus[1] - (l.cropT || 0)) / r.ch, [tx, ty] = o.target || [ctx.W / 2, h / 2];
  if (r.dw > l.w + 1) l.fx = clamp((tx - u * r.dw) / (l.w - r.dw), 0, 1);
  if (r.dh > l.h + 1) l.fy = clamp((ty - v * r.dh) / (l.h - r.dh), 0, 1);
  l.y = y0;
  return l;
}
function secondFrame(ctx) {  // 두 번째 칸 장면: 다른 장면 묶음 · 사람이 있고 · 글자 없고 · 또렷 · 머리 잘림 없음 — 좋은 순
  const maxS = Math.max(1e-6, ...AI.frames.map(f => f.score || 0)), g = sceneGroups(AI.frames);
  const xs = AI.frames.filter(f => f !== ctx.frame && g[f.t] !== g[ctx.frame.t] && (f.persons || []).length && mainBox(f) && (f.text || 0) < TEXTY && (f.blur ?? 0) <= 0.35
    && !headlessBad(f) && !(f.flags || []).some(x => x === "bench" || x === "crowd")).sort((a, b) => frameQ(b, maxS) - frameQ(a, maxS));
  return xs.length ? xs[Math.abs(ctx.seed || 0) % Math.min(2, xs.length)] : null;  // 위 장면마다 아래 장면이 달라지게 (좋은 두 장 중)
}
// 큰 주인공 누끼가 들어갈 만큼 원본이 큰지 (주인공 키 × 원본 높이 × 확대 상한 ≥ 화면 높이의 want)
function bigEnough(ctx, want) { const m = mainBox(ctx.frame); return !!m && m[3] * Math.min(INFO.height || 1080, 2160) * srcCap() >= want * ctx.H; }
function joinCopy(c) {  // 두 줄을 한 줄로 (짧을 때 · 쪼살 '플랩 레벨업 바로 됩니다'처럼) — 강조 위치는 옮김
  if (!c.l2) return c;
  const t = `${c.l1} ${c.l2}`, e = c.emph ? (c.emph[0] === 0 ? [0, c.emph[1], c.emph[2]] : [0, c.emph[1] + c.l1.length + 1, c.emph[2] + c.l1.length + 1]) : null;
  return Object.assign({}, c, { l1: t, l2: "", emph: e });
}
// 판정 5회차(D-090·D-091): 가장자리 제목 밑에만 그라데이션 (반쪽 어둡게 0.55~0.72 대신 — 레퍼런스 반쪽 어둡게 1% · 우리 롱폼 63%)
function edgeShade(ctx, box, side, op = 0.45) {
  return side === "top" ? shade("위 어둡게", 0, 0, ctx.W, Math.min(ctx.H, box.y + box.h + ctx.H * 0.08), true, op)
    : shade("아래 어둡게", 0, Math.max(0, box.y - ctx.H * 0.08), ctx.W, ctx.H - Math.max(0, box.y - ctx.H * 0.08), false, op);
}
// 쇼츠 꽉 찬 장면 (판정 5회차 D-090: 띠·여러 칸 쇼츠 5.0 → 9:16 전체 장면 + 위 큰 제목 5.94 · 원본 1080p 가로 영상은 1.78배 확대인데도 장면 점수가 오름)
// 원본을 SHORT_FULL_CAP 배까지만 키워 화면 전체를 덮을 수 있을 때만 (720p 가로 영상은 2.67배 → 예전 칸·흐린 판)
// 판정 q5 1회차(D-096): 720p 가로 원본 쇼츠의 띠·여러 칸('두 장면' pro 3.6 · '합성 티' 34번)이 꽉 찬 장면보다 낮음 → 2.7배 (110~270px 쇼츠 칸에서는 720p 도 원본보다 작게 보임)
// SHORT_ZOOM_CAP: 꽉 찬 쇼츠에서 주인공을 키울 때 원본 확대 전체 상한 (1080p 가로 원본은 덮기 1.78배 위로 2배까지 · 720p 는 1.35배 — 4.5배(720p 1.7배)는 판정 '흐림' 지적이 30 → 43 · 쇼츠 주인공 키 중앙값 0.33H, 판정 고칠 점 200개 중 128개가 '주인공을 화면 높이 55~80%로 키우기')
const SHORT_FULL_CAP = 2.7;
const SHORT_ZOOM_CAP = 3.6;
function coverUp(ctx) { const a = arEff(ctx), dw = ctx.W / ctx.H > a ? ctx.W : ctx.H * a; return dw / srcW(); }
function fullOk(ctx) { return !!ctx.short && coverUp(ctx) <= SHORT_FULL_CAP + 1e-6; }
function fullShort(ctx, o = {}) {  // 9:16 전체에 장면 · 주인공 머리 위 = (0.5W, o.headY) · o.minHeadY 위로는 못 올라감
  const m = mainBox(ctx.frame), fc = mainFace(ctx.frame);
  const focus = m ? [m[0] + m[2] / 2, fc ? Math.min(m[1], fc[1]) : m[1]] : fc ? [fc[0] + fc[2] / 2, fc[1]] : ctx.focus;
  return frameLayer(ctx, { zoom: zoomFor(ctx, o.subject || 0.64, 1, 2.6), focus, target: [ctx.W * 0.5, o.headY ?? ctx.H * 0.36], minHeadY: o.minHeadY, headroom: o.headroom });
}
function shortTop(ctx, o = {}) {  // 쇼츠 위 제목 묶음: 폭 0.88W(0.06~0.94W · 버튼은 0.42H 아래만) · 위 끝 0.07H · 높이 ≤0.24H
  return headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.07, w: ctx.W * 0.85, h: ctx.H * (o.h || 0.24) }, Object.assign({ style: "line", size: Math.round(ctx.W * 0.2), grow: 1.6 }, o));  // 그림자 번짐까지 0.94W 안
}
// 판정 q5 1회차(D-099): 템플릿 가산점을 독립 판정(93장 × 3회) 템플릿별 점수로 다시 맞춤 — 위 제목(쪼살형) 4.5 → 3.5 · 쌈바형 4 → 3.5 · 옆 제목 −4 → −7(몸을 안 가리고 주인공이 커서 새 캔버스 점수를 많이 받아 롱폼 묶음마다 10장 · 총점 5.85 로 가장 낮음 — 예전 빈도 3장으로) ·
// 액션 누끼 1 → −3(5.60 '합성 티') · 질문 훅 −3 → −5 · 해주호형 인물 −10 그대로(−6 이면 crop 3.67) · 쇼츠 장면 위 제목 4 → 5.5(6.71 최고) · 꽉 찬 + 아래 제목 4 → 1.5(5.90 '몸을 가림') ·
// 두 장면 0 → −8(5.69 최저) · 세 장면 −1 → −10 · 누끼 크게 1 → −1 · 레터박스 −1 → 0(6.69) · 흰 띠 0 → −1
// 판정 q5 2회차(D-117): 개발 판정 6판(기준·r_a~r_d·독립 2회차, 우리 것 약 600장 × 2회)을 템플릿별로 모아 pro 평균과 형식 평균의 차 × 12 만큼 가산점을 옮김 —
// 액션 누끼 5.11(−3 → 1.5) · 아래 제목 + 네온 5.03(1 → 4.5) · 질문 훅 4.97(−5 → −2) · 전술 해설 4.88(5 → 7) · 쌈바형 4.65(85장, 3.5 → 2.5) · 상자 제목 4.34(−5 → −9.5) · 해주호형 인물 4.33(−10 → −14.5) ·
// 쇼츠 장면 위 제목 4.84(5.5 → 7.5) · 3단 4.83(2 → 3.5) · 레터박스 4.81(0 → 1.5) · 꽉 찬 + 위 4.65(6 → 5.5) · 전술 4.60(3.5 → 2.5) · 흰 띠 4.50(−1 → −3.5) · 꽉 찬 + 아래 4.47(1.5 → −1)
const T_NEW = {
  long: {
    // 판정 5회차(D-090): 롱폼 기본은 가장자리 넓은 제목 두 틀 — 쪼살형 위 띠(전술 그래픽 있음·없음) · 쌈바형 왼쪽 아래 (레퍼런스 가장자리 81% · 우리 31%)
    "전술 해설 (쪼살형)": { prior: 7, needs: { kinds: ["wide", "mid"], persons: 2, tactics: true }, fn: ctx => {
      const { hd, bg } = topLayouts(ctx, { style: "line", size: Math.round(ctx.H * 0.2) }, { subject: 0.5, max: 2 }), ls = [bg, edgeShade(ctx, hd.box, "top")];
      const tac = tactics(ctx, bg, [hd.box], { ring: false });  // 판정 3회차: 링·칩·화살표 셋 다 → '흩어짐' · 화살표 + '공간' 칩만 (쪼살 원본)
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 });
      ls.push(...tac.filter(l => l.shape === "ring")); if (cut) ls.push(cut); ls.push(...tac.filter(l => l.shape !== "ring"), ...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "위 제목 (쪼살형)": { prior: 4, needs: { kinds: ["wide", "mid"] }, fn: ctx => {
      const { hd, bg } = topLayouts(ctx, { style: "line", size: Math.round(ctx.H * 0.2) }, { subject: 0.6, max: 2.6 }), ls = [bg, edgeShade(ctx, hd.box, "top")];
      const ring = tacOk(ctx) ? tactics(ctx, bg, [hd.box], { arrow: false, chip: false }) : [];  // 발이 보이면 주인공 발밑 원 하나 (그래픽 하나는 늘 — 레퍼런스 70%)
      ls.push(...ring);
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "아래 제목 (쌈바형)": { prior: 1, needs: { kinds: ["wide", "mid", "close"] }, fn: ctx => {
      const { hd, bg } = bottomLayout(ctx), ls = [bg, edgeShade(ctx, hd.box, "bottom", 0.5)];
      if (headOverlap(ctx, bg, hd.layers) > 0.2) return [];  // 주인공 머리가 아래쪽에 있으면 위 띠형에 양보
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      const sl = ctx.brand.seriesOn ? seriesLabel(ctx, ctx.W * 0.04, ctx.H * 0.05) : null; if (sl) ls.push(sl);
      const lg = logoLayer(ctx, [hd.box].concat(sl ? [bbox([sl])] : [])); if (lg) ls.push(lg);
      return ls;
    } },
    "아래 제목 + 네온 (쪼살형 2)": { prior: 4.5, needs: { kinds: ["wide", "mid"], persons: 2, tactics: true }, fn: ctx => {
      const c = ctx.copy, one = c.l2 && `${c.l1} ${c.l2}`.length <= LINE_MAX_FMT.long;
      const cx = one ? Object.assign({}, ctx, { copy: joinCopy(c) }) : ctx;
      const hd = headline(cx, { x: ctx.W * 0.045, y: ctx.H * 0.45, w: ctx.W * 0.79, h: ctx.H * 0.5 }, { style: "word", align: "left", anchor: "bottom", size: Math.round(ctx.H * (one ? 0.2 : 0.23)), grow: 1.6, emphScale: 1.1, emphFill: ctx.brand.colors.neon });  // 강조는 네온(쪼살 '플랩 레벨업' 하늘색)
      const bg = frameAbove(ctx, hd.box, { subject: 0.5, max: 2 }), ls = [bg, edgeShade(ctx, hd.box, "bottom", 0.55)];
      const b = ctx.brand.colors, tac = tactics(ctx, bg, [hd.box], { chip: false, arrowColor: b.hl, ringColor: b.hl, up: true });  // 링 + 화살표 두 종류만
      ls.push(...tac.filter(l => l.shape === "ring"), ...tac.filter(l => l.shape !== "ring"), ...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "강좌 시리즈 (쌈바형)": { prior: 0, needs: { cut: true }, fn: ctx => {
      const main = mainBox(ctx.frame), left = main ? main[0] + main[2] / 2 < 0.5 : false;
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.72, 1, 2), target: [ctx.W * (left ? 0.36 : 0.64), ctx.H * 0.5] }), ls = [bg];
      Object.assign(bg, { bright: 66, blur: 4, vignette: 55 });
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 1, minH: 0.5, need: true }); if (cut) { cut.blur = 0; cut.bright = 100; }
      const al = left ? "right" : "left", bx = { x: ctx.W * 0.04, y: ctx.H * 0.4, w: ctx.W * 0.78, h: ctx.H * 0.555 };  // 재생시간 자리(아래 오른쪽) 앞에서 끝
      if (al === "right") bx.x = ctx.W * 0.18;
      const hd = headline(ctx, bx, { style: "word", align: al, anchor: "bottom", size: Math.round(ctx.H * 0.24), grow: 1.6, kicker: true, emphScale: 1.08, grad: false });
      ls.push(edgeShade(ctx, hd.box, "bottom", 0.6)); if (cut) ls.push(cut); ls.push(...hd.layers);
      const sl = ctx.brand.seriesOn ? seriesLabel(ctx, ctx.W * 0.04, ctx.H * 0.05) : null; if (sl) ls.push(sl);
      const lg = logoLayer(ctx, [hd.box].concat(sl ? [bbox([sl])] : [])); if (lg) ls.push(lg);
      return ls;
    } },
    // 판정 4회차: 롱폼에 '선수 누끼 + 흰 테두리·빛 + 어둡고 흐린 배경'(쪼살·JK) → 액션 주인공을 0.7H 로 크게 · 판정 5회차: 반쪽 어둡게 뺌 (배경이 이미 어두움)
    "액션 누끼 (JK형)": { prior: 1.5, needs: { kinds: ["wide", "mid"], cut: true, sharp: 0.3, big: 0.6, action: true, solo: true }, fn: ctx => {  // 검토(D-119): 서 있기만 한 누끼('가만히 서 있는 누끼' 4.5)·다른 사람 머리가 붙은 누끼는 안 씀
      const m = mainBox(ctx.frame), left = m[0] + m[2] / 2 < 0.5, b = ctx.brand.colors;
      const bg = Object.assign(frameLayer(ctx, { zoom: zoomFor(ctx, 0.45, 1, 1.3), target: [ctx.W * (left ? 0.3 : 0.7), ctx.H * 0.5], feet: false }), { blur: Math.round(ctx.W * 0.006), bright: 50, vignette: 65 });
      let cut = placeCut(ctx, m, ctx.H * 0.72, ctx.W * (left ? 0.29 : 0.71), ctx.H * 0.12, { outline: "#FFFFFF" });
      const up = upOf(cut, ctx.ar);
      if (up > srcCap()) cut = placeCut(ctx, m, ctx.H * 0.72 * srcCap() / up, ctx.W * (left ? 0.29 : 0.71), ctx.H * 0.12, { outline: "#FFFFFF" });  // 원본 확대 상한 안
      Object.assign(cut, { outline: { on: true, color: "#FFFFFF", width: Math.round(8 * ctx.W / 1280) }, glow: { on: true, color: b.hl, size: Math.round(34 * ctx.W / 1280), opacity: 0.85 } });
      const hd = headline(ctx, { x: ctx.W * (left ? 0.5 : 0.04), y: ctx.H * 0.08, w: ctx.W * 0.46, h: ctx.H * 0.8 }, { style: "line", align: left ? "right" : "left", anchor: "middle", size: Math.round(ctx.H * 0.27), grow: 1.4 });
      const ls = [bg, cut, ...hd.layers];
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "옆 제목 (쪼살형 3)": { prior: -7, minBigW: 0.5, needs: { kinds: ["wide", "mid", "close"] }, fn: ctx => {  // 판정 5회차: 가운데·옆 제목은 레퍼런스 12% (우리 50%) → 낮춤 · 반쪽 어둡게 뺌
      const m = mainBox(ctx.frame), fc = mainFace(ctx.frame);
      const cxs = fc ? fc[0] + fc[2] / 2 : m ? m[0] + m[2] / 2 : 0.6, right = cxs >= 0.42;  // 사람이 있는 쪽 반대편에 제목
      const hd = headline(ctx, { x: ctx.W * (right ? 0.05 : 0.42), y: ctx.H * 0.08, w: ctx.W * 0.53, h: ctx.H * 0.78 }, { style: "line", align: right ? "left" : "right", anchor: "middle", size: Math.round(ctx.H * 0.28), grow: 1.3 });
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, ctx.frame.kind === "close" ? 0.9 : 0.72, 1, 2), target: [ctx.W * (right ? 0.74 : 0.26), ctx.H * 0.5] }), ls = [bg];
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    // 세로 영상으로 롱폼: 장면을 잘라 키우면 머리·몸이 잘림(판정 5회차: 6장 중 5장이 주인공 키의 28~40%만) → 세로 장면 통째로 한쪽 판에, 뒤는 같은 장면을 흐리게
    "세로 장면 + 옆 제목": { prior: 3, minBigW: 0.4, needs: { vertical: true, noText: true }, fn: ctx => {
      const m = mainBox(ctx.frame), right = !m || m[0] + m[2] / 2 >= 0.35, ph = Math.round(ctx.H * 0.9), pw = Math.round(ph * ctx.ar);
      const back = Object.assign(frameLayer(ctx, { zoom: 1, keep: false }), { name: "흐린 배경", blur: 26, bright: 45, vignette: 55 });
      const x0 = right ? ctx.W * 0.955 - pw : ctx.W * 0.045, y0 = (ctx.H - ph) / 2;
      const panel = L("image", { name: "배경", src: frameSrc(ctx.frame.t), x: x0, y: y0, w: pw, h: ph, fit: "cover", rot: 0,
        outline: { on: true, color: "#FFFFFF", width: Math.round(ctx.W * 0.006) }, shadow: { on: true, color: "#000000", blur: 30, dx: 0, dy: 12, opacity: 0.7 } });
      if (ctx.frame.grade) panel.grade = Object.assign({}, ctx.frame.grade, { amt: 1 });
      const bx = right ? { x: ctx.W * 0.05, w: x0 - ctx.W * 0.08 } : { x: x0 + pw + ctx.W * 0.03, w: ctx.W * 0.955 - (x0 + pw + ctx.W * 0.03) };
      const hd = headline(ctx, { x: bx.x, y: ctx.H * 0.1, w: bx.w, h: ctx.H * 0.78 }, { style: "line", align: "left", anchor: "middle", size: Math.round(ctx.H * 0.27), grow: 1.4 });
      const ls = [back, panel, ...hd.layers];
      const lg = logoLayer(ctx, [hd.box, bbox([panel])]); if (lg) ls.push(lg);
      return ls;
    } },
    "코치 + 경기 장면": { prior: -6, needs: { kinds: ["close"], cut: true, combo: true }, fn: ctx => {
      const act = actionFrame(ctx), m = mainBox(ctx.frame), actx = ctxFor(act, ctx.copy, "long", ctx.seed);
      const bg = Object.assign(frameLayer(actx, { zoom: zoomFor(actx, 0.45, 1, 1.6), target: [ctx.W * 0.42, ctx.H * 0.62] }), { name: "경기 장면 배경", bright: 72 });
      const cut = placeCut(ctx, m, ctx.H * 1.05, ctx.W * 0.77, ctx.H * 0.06);
      const hd = headline(ctx, { x: ctx.W * 0.05, y: ctx.H * 0.45, w: ctx.W * 0.6, h: ctx.H * 0.5 }, { style: "line", align: "left", anchor: "bottom", size: Math.round(ctx.H * 0.24), grow: 1.4 });
      return [bg, edgeShade(ctx, hd.box, "bottom", 0.55), cut, ...hd.layers];
    } },
    // 판정 5회차(D-090): 상자(스티커) 제목은 레퍼런스 8% (우리 28%)·'템플릿 티' → 가산점 4 → −5, 상자·기울임 없이 아래 모서리 두 줄
    "상자 제목 (자막형)": { prior: -9.5, needs: { kinds: ["close", "mid"] }, fn: ctx => {
      const m = mainBox(ctx.frame), fc = mainFace(ctx.frame);
      const cx = fc ? fc[0] + fc[2] / 2 : m ? m[0] + m[2] / 2 : 0.5, right = cx >= 0.4;  // 사람이 있는 쪽 반대편에 제목
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.72, 1.05, 1.8), target: [ctx.W * (right ? 0.68 : 0.32), ctx.H * 0.48] }), ls = [bg];
      const hd = headline(ctx, { x: ctx.W * (right ? 0.04 : 0.36), y: ctx.H * 0.4, w: ctx.W * 0.56, h: ctx.H * 0.555 }, { style: "word", align: right ? "left" : "right", anchor: "bottom", size: Math.round(ctx.H * 0.22), grow: 1.4, emphScale: 1.08 });
      ls.push(edgeShade(ctx, hd.box, "bottom", 0.5));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      const sl = ctx.brand.seriesOn ? seriesLabel(ctx, right ? ctx.W * 0.045 : ctx.W * 0.6, ctx.H * 0.06) : null; if (sl) ls.push(sl);
      const lg = logoLayer(ctx, [hd.box].concat(sl ? [bbox([sl])] : [])); if (lg) ls.push(lg);
      return ls;
    } },
    "리액션 클로즈업": { prior: -4, needs: { kinds: ["close"], sharp: 0.25 }, fn: ctx => {
      const fc = mainFace(ctx.frame) || ctx.frame.faces[0].box, z = clamp(0.5 / Math.max(0.12, fc[3]), 1, 1.6);
      const bg = frameLayer(ctx, { zoom: z, focus: [fc[0] + fc[2] / 2, fc[1] + fc[3] / 2], target: [ctx.W * 0.68, ctx.H * 0.46] }), ls = [bg];
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8, minH: 0.5 }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.05, y: ctx.H * 0.12, w: ctx.W * 0.5, h: ctx.H * 0.76 }, { style: "word", align: "left", anchor: "middle", size: Math.round(ctx.H * 0.3), grow: 1.3, emphScale: 1.1 });
      ls.push(...hd.layers);
      const em = emojiLayer(ctx, hd.big, headAvoid(ctx, bg).concat(hd.layers.filter(l => l !== hd.big).map(l => bbox([l]))), { k: 1.05 }); if (em) ls.push(em);
      return ls;
    } },
    "질문 훅 (JK형)": { prior: -2, needs: { noText: true }, fn: ctx => {
      const close = ctx.frame.kind === "close";  // 얼굴이 큰 장면은 얼굴을 오른쪽으로 보내고 제목은 왼쪽에만
      const ho = { style: "word", align: close ? "left" : "center", size: Math.round(ctx.H * 0.22), emphScale: 1.1 };
      const { hd, bg } = close ? { hd: headline(ctx, { x: ctx.W * 0.05, y: ctx.H * 0.08, w: ctx.W * 0.5, h: ctx.H * 0.6 }, Object.assign({ grow: 1.3 }, ho)), bg: frameLayer(ctx, { zoom: 1.05, target: [ctx.W * 0.72, ctx.H * 0.5] }) }
        : topLayouts(ctx, ho, { subject: 0.5 });
      return [bg, edgeShade(ctx, hd.box, "top", 0.45), ...hd.layers];
    } },
    "인물 + 오른쪽 제목 (해주호형)": { prior: -14.5, needs: { kinds: ["mid", "close"] }, fn: ctx => {
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.9, 1.05, 2), target: [ctx.W * 0.27, ctx.H * 0.5] }), ls = [bg];
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.46, y: ctx.H * 0.1, w: ctx.W * 0.5, h: ctx.H * 0.7 }, { style: "word", align: "center", anchor: "middle", size: Math.round(ctx.H * 0.3), emphScale: 1.1 });
      ls.push(...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "반전 O/X": { needs: { ox: true }, fn: ctx => {
      const bg = frameLayer(ctx, { zoom: 1.15, target: [ctx.W * 0.5, ctx.H * 0.42] }), ls = [bg];
      ls.push(shade("아래 어둡게", 0, ctx.H * 0.5, ctx.W, ctx.H * 0.5, false, 0.85));
      const cut = cutLayer(ctx, bg, { outline: ctx.brand.colors.hl, ow: 1 }); if (cut) ls.push(cut);
      const [no, yes] = ctx.copy.ox, s = Math.round(ctx.H * 0.1), sc = ctx.W / 1280;
      const noL = lineLayer(ctx, `${no}?`, s, { font: "Dokdo", fill: "#FFFFFF", sw: 0.14, align: "left", name: "반전 아니오" }); noL.x = ctx.W * 0.06; noL.y = ctx.H * 0.08;
      const xm = tacShape("xmark", { x: noL.x + noL.w + 8 * sc, y: noL.y + noL.h * 0.15, w: s * 0.8, h: s * 0.8, fill: "#FF2D2D", width: Math.round(s * 0.17), stroke: { color: "#FFFFFF", width: Math.round(4 * sc) } });
      const yesL = lineLayer(ctx, `${yes} O`, s, { font: "Dokdo", fill: ctx.brand.colors.hl, sw: 0.14, align: "right", name: "반전 예" }); yesL.x = ctx.W * 0.94 - yesL.w; yesL.y = ctx.H * 0.2;
      const sx = noL.x + noL.w * 0.6, sy = noL.y + noL.h * 1.05, ex = yesL.x - 10 * sc, ey = yesL.y + yesL.h * 0.5;
      const scr = tacShape("scribble", { x: Math.min(sx, ex), y: Math.min(sy, ey) - 30 * sc, w: Math.abs(ex - sx), h: Math.abs(ey - sy) + 60 * sc, pts: [[0, 0.1], [0.45, 0.95], [1, 0.55]], fill: "#FFFFFF", width: Math.round(7 * sc), head: Math.round(30 * sc), seed: ctx.seed + 3, stroke: { color: "#000000", width: Math.round(3 * sc) } });
      const hd = headline(ctx, { x: ctx.W * 0.05, y: ctx.H * 0.5, w: ctx.W * 0.78, h: ctx.H * 0.45 }, { style: "word", align: "left", anchor: "bottom", size: Math.round(ctx.H * 0.24), grow: 1.4, grad: false });
      ls.push(noL, xm, yesL, scr, ...hd.layers);
      return ls;
    } },
  },
  short: {
    // 판정 5회차(D-090): 쇼츠 기본은 꽉 찬 9:16 장면 + 큰 제목 (쪼살 87%) — 띠·여러 칸(합성 티 '이어 붙인' 23번)보다 총점 +0.97
    "쇼츠 · 꽉 찬 장면 + 위 제목": { prior: 5.5, needs: { full: true, persons: 1, noText: true }, fn: ctx => {
      const hd = shortTop(ctx, { style: "line" }), bg = fullShort(ctx, underTitle(ctx, hd));
      if (headOverlap(ctx, bg, hd.layers) > 0.15) return [];  // 머리가 제목 높이에 있으면 (가로 원본은 위아래로 못 옮김) 아래 제목형에 양보
      const ls = [bg, edgeShade(ctx, hd.box, "top", 0.5), ...hd.layers];
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "쇼츠 · 꽉 찬 장면 + 아래 제목": { prior: -1, needs: { full: true, persons: 1, noText: true }, fn: ctx => {
      const hd = headline(ctx, { x: ctx.W * 0.065, y: ctx.H * 0.5, w: ctx.W * 0.75, h: ctx.H * 0.25 }, { style: "word", align: "left", anchor: "bottom", size: Math.round(ctx.W * 0.2), grow: 1.6, kicker: true, emphScale: 1.08 });
      const bg = fullShort(ctx, { headY: ctx.H * 0.14, headroom: 0.08 });
      if (headOverlap(ctx, bg, hd.layers) > 0.15) return [];
      const ls = [bg, edgeShade(ctx, hd.box, "bottom", 0.55), ...hd.layers];
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    // 띠 없이 장면 위에 바로 큰 제목 (쪼살 쇼츠 'V자 어려우면 무조건 봐'·'핀타 움직임 중요성') — 꽉 찬 장면이 되면 그것으로, 아니면 예전 칸 + 흐린 판
    "쇼츠 · 장면 위 제목 (쪼살형)": { prior: 7.5, needs: { noText: true, persons: 1 }, fn: ctx => {
      if (fullOk(ctx)) {
        const hd = shortTop(ctx, { style: "word", emphScale: 1.1 }), bg = fullShort(ctx, underTitle(ctx, hd));
        if (headOverlap(ctx, bg, hd.layers) > 0.15) return [];
        return [bg, edgeShade(ctx, hd.box, "top", 0.5), ...hd.layers];
      }
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.1, w: ctx.W * 0.76, h: ctx.H * 0.25 }, { style: "word", size: Math.round(ctx.W * 0.24), emphScale: 1.1 });
      const { back, f, py, ph } = framesBelow(ctx, hd.box, 0.62, { overlap: (hd.box.y + hd.box.h) / ctx.H, headY: hd.box.y + hd.box.h + ctx.H * 0.03 });
      const ls = [back, f, shade("위 어둡게", 0, 0, ctx.W, hd.box.y + hd.box.h + ctx.H * 0.04, true, 0.5)];
      const pb = Math.min(ctx.H * 0.78, py + ph * 0.88);  // 장면 칸 아래 흐린 판 위에는 그래픽을 두지 않음 (판정: 흐린 곳에 뜬 원)
      const b = ctx.brand.colors, tac = tactics(ctx, f, [hd.box, { x: ctx.W * 0.84, y: ctx.H * 0.4, w: ctx.W, h: ctx.H }, { x: 0, y: pb, w: ctx.W, h: ctx.H }], { chip: false, arrowColor: b.hl, ringColor: b.neon });
      ls.push(...tac.filter(l => l.shape === "ring"), ...tac.filter(l => l.shape !== "ring"), ...hd.layers);
      return ls;
    } },
    "쇼츠 · 전술": { prior: 2.5, needs: { kinds: ["wide", "mid"], persons: 2, tactics: true }, fn: ctx => {
      if (fullOk(ctx)) {
        const hd = shortTop(ctx, { style: "line" }), bg = fullShort(ctx, Object.assign({ subject: 0.45 }, underTitle(ctx, hd, 1.3)));
        if (headOverlap(ctx, bg, hd.layers) > 0.15) return [];
        const b = ctx.brand.colors, tac = tactics(ctx, bg, [hd.box, { x: ctx.W * 0.84, y: ctx.H * 0.4, w: ctx.W, h: ctx.H }, { x: 0, y: ctx.H * 0.78, w: ctx.W, h: ctx.H }], { chip: false, arrowColor: b.hl, ringColor: b.neon });
        return [bg, edgeShade(ctx, hd.box, "top", 0.5), ...tac.filter(l => l.shape === "ring"), ...tac.filter(l => l.shape !== "ring"), ...hd.layers];
      }
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.075, w: ctx.W * 0.76, h: ctx.H * 0.26 }, { style: "line", size: Math.round(ctx.W * 0.25) });
      const { back, f, py, ph } = framesBelow(ctx, hd.box, 0.4), ls = [back, f];
      const tac = tactics(ctx, f, [hd.box, { x: ctx.W * 0.84, y: ctx.H * 0.4, w: ctx.W, h: ctx.H }, { x: 0, y: Math.min(ctx.H * 0.8, py + ph * 0.88), w: ctx.W, h: ctx.H }], { ring: false });
      const cut = cutLayer(ctx, f, { outline: "#FFFFFF", ow: 0.8, minH: 0.32 });
      ls.push(...tac.filter(l => l.shape === "ring")); if (cut) ls.push(cut); ls.push(...tac.filter(l => l.shape !== "ring"), ...hd.layers);
      return ls;
    } },
    // 쇼츠 제목: 위쪽 3단 (첫 줄 흰 · 큰 줄 노랑 · 어두운 상자 속 보조 문구) — 레퍼런스 'V자 어려우면 / 무조건 봐 / 1분안에 알려줄게'
    "쇼츠 · 3단 제목": { prior: 3.5, band: true, needs: { sub: true }, fn: ctx => {
      if (fullOk(ctx) && mainBox(ctx.frame) && (ctx.frame.text || 0) < TEXTY) {
        const hd = shortTop(ctx, { style: "line", sub: true, subRatio: 0.36, h: 0.3 }), bg = fullShort(ctx, underTitle(ctx, hd));
        if (headOverlap(ctx, bg, hd.layers) > 0.15) return [];
        return [bg, edgeShade(ctx, hd.box, "top", 0.5), ...hd.layers];
      }
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.075, w: ctx.W * 0.76, h: ctx.H * 0.3 }, { style: "line", size: Math.round(ctx.W * 0.25), sub: true, subRatio: 0.36 });
      const { back, f } = framesBelow(ctx, hd.box), ls = [back, f];
      const cut = cutLayer(ctx, f, { outline: "#FFFFFF", ow: 0.8, minH: 0.32 }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      return ls;
    } },
    // 판정 q5 2회차(D-117): 템플릿별로 모은 판정에서 롱폼 '액션 누끼 (JK형)'(어둡고 살짝 흐린 같은 장면 + 노란 빛·흰 테두리 누끼를 따로 크게)가 가장 높음 →
    // 쇼츠도 같은 짜임 (예전 '누끼 크게'는 같은 자리 누끼라 '오려 붙인 티'만 나고 한 번도 뽑히지 않음) · 뒤판 흐림은 쇼츠 '흐린 빈 곳' 게이트(흐림 6 미만) 안
    // 검토(D-119): 쇼츠에서는 판정해 보지 않고 올린 가산점(2)이었는데 개발 판정 r_f 쇼츠 템플릿 가운데 가장 낮음(pro 4.00·3.75 — '어색한 합성'·'뒤에 겹친 흐린 같은 사람'·
    // '그냥 서 있는 사람') → 가산점 −1 · 배경 주인공을 누끼 뒤에 숨김(가운데를 맞추고, 배경 주인공이 누끼보다 크면 안 씀) · 누끼에 다른 사람 머리가 들어갈 장면·서 있기만 한 장면은 안 씀
    "쇼츠 · 누끼 크게": { prior: -1, needs: { kinds: ["wide", "mid"], cut: true, sharp: 0.3, big: 0.35, action: true, solo: true }, fn: ctx => {
      const hd = shortTop(ctx, { style: "line", h: 0.24 }), m = mainBox(ctx.frame), b = ctx.brand.colors;
      const top = hd.box.y + hd.box.h + ctx.H * UNDER_TITLE.head;
      let cut = placeCut(ctx, m, ctx.H * 0.56, ctx.W * 0.5, top, { outline: "#FFFFFF" });
      const up = upOf(cut, ctx.ar);
      if (up > SHORT_ZOOM_CAP) cut = placeCut(ctx, m, ctx.H * 0.56 * SHORT_ZOOM_CAP / up, ctx.W * 0.5, top, { outline: "#FFFFFF" });  // 원본 확대 상한 안
      const cb = boxC(cut, m, ctx.ar);
      const bg = Object.assign(frameLayer(ctx, { zoom: 1, focus: [m[0] + m[2] / 2, m[1] + m[3] / 2], target: [cb.x + cb.w / 2, cb.y + cb.h / 2], keep: false, edges: false }), { blur: 5, bright: 50, vignette: 65 });
      if (ghostOf(ctx, bg, cut) > GHOST_MAX) return [];
      Object.assign(cut, { outline: { on: true, color: "#FFFFFF", width: Math.round(8 * ctx.W / 1280) }, glow: { on: true, color: b.hl, size: Math.round(34 * ctx.W / 1280), opacity: 0.85 } });
      return [bg, edgeShade(ctx, hd.box, "top", 0.5), cut, ...hd.layers];
    } },
    // 판정 4회차: 720p 가로 원본 쇼츠의 흐린 판 대신 두·세 장면 칸 → 판정 5회차: '합성 티' 23/28 · 한 장면으로 꽉 채울 수 있으면 내지 않음 (720p 처럼 못 채울 때만)
    "쇼츠 · 두 장면 (위아래)": { prior: -8, needs: { persons: 1, noText: true, second: true, notFull: true }, fn: ctx => {
      const f2 = secondFrame(ctx), H2 = Math.round(ctx.H / 2);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.39, w: ctx.W * 0.76, h: ctx.H * 0.22 }, { style: "word", anchor: "middle", size: Math.round(ctx.W * 0.22), emphScale: 1.1 });
      const a = panelLayer(ctx, ctx.frame, 0, H2, { subject: 0.62, target: [ctx.W * 0.5, H2 * 0.08], fy: 0.1 });
      const b2 = panelLayer(ctx, f2, H2, ctx.H - H2, { subject: 0.62, target: [ctx.W * 0.5, H2 * 0.22], fy: 0.2, name: "장면 2" });
      const hb2 = headBox(f2), hbx = hb2 ? boxC(b2, hb2, ctx.ar) : null;
      if (hbx && interArea(hbx, hd.box) > areaOf(hbx) * 0.2) return [];  // 아래 칸 머리를 제목이 덮으면 이 조합은 안 씀
      const y0 = hd.box.y - ctx.H * 0.06, y1 = hd.box.y + hd.box.h + ctx.H * 0.06, mid = (y0 + y1) / 2;
      return [a, b2, shade("가운데 어둡게 위", 0, y0, ctx.W, mid - y0, false, 0.7), shade("가운데 어둡게 아래", 0, mid, ctx.W, y1 - mid, true, 0.7), ...hd.layers];
    } },
    "쇼츠 · 세 장면 모음": { prior: -10, needs: { persons: 1, noText: true, second: true, notFull: true }, fn: ctx => {
      const f2 = secondFrame(ctx), f3 = secondFrame(Object.assign({}, ctx, { seed: (ctx.seed || 0) + 1 }));
      if (!f3 || f3 === f2) return [];
      const h = Math.round(ctx.H / 3);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: h - ctx.H * 0.11, w: ctx.W * 0.76, h: ctx.H * 0.22 }, { style: "word", anchor: "middle", size: Math.round(ctx.W * 0.22), emphScale: 1.1 });
      const ps = [[ctx.frame, 0, "배경"], [f2, h, "장면 2"], [f3, 2 * h, "장면 3"]].map(([f, y, nm]) => panelLayer(ctx, f, y, nm === "장면 3" ? ctx.H - 2 * h : h, { subject: 0.7, target: [ctx.W * 0.5, h * 0.12], fy: 0.1, name: nm }));
      for (const [l, f] of [[ps[1], f2]]) { const hb = headBox(f), b = hb ? boxC(l, hb, ctx.ar) : null; if (b && interArea(b, hd.box) > areaOf(b) * 0.2) return []; }
      const y0 = hd.box.y - ctx.H * 0.05, y1 = hd.box.y + hd.box.h + ctx.H * 0.05, mid = (y0 + y1) / 2;
      return [...ps, shade("이음매 어둡게 위", 0, y0, ctx.W, mid - y0, false, 0.7), shade("이음매 어둡게 아래", 0, mid, ctx.W, y1 - mid, true, 0.7), ...hd.layers];
    } },
    "쇼츠 · 코치 + 경기 장면": { prior: -1.5, needs: { kinds: ["close"], cut: true, combo: true }, fn: ctx => {
      const act = actionFrame(ctx), m = mainBox(ctx.frame), actx = ctxFor(act, ctx.copy, "short", ctx.seed);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.075, w: ctx.W * 0.76, h: ctx.H * 0.26 }, { style: "line", size: Math.round(ctx.W * 0.25) });
      const { back, f } = framesBelow(actx, hd.box, 0.4); f.name = "경기 장면 배경";
      const cut = placeCut(ctx, m, ctx.H * 0.6, ctx.W * 0.45, ctx.H * 0.44);
      return [back, f, shade("아래 어둡게", 0, ctx.H * 0.55, ctx.W, ctx.H * 0.45, false, 0.5), cut, ...hd.layers];
    } },
    "쇼츠 · 얼굴 + 아래 제목": { prior: -2, needs: { kinds: ["close"] }, fn: ctx => {  // 인터뷰처럼 얼굴이 위쪽에 큰 장면: 얼굴은 위에 두고 제목은 아래 안전 영역 끝에
      const bg = frameLayer(ctx, { zoom: 1.05, target: [ctx.W * 0.47, ctx.H * 0.3] }), ls = [bg];
      ls.push(shade("아래 어둡게", 0, ctx.H * 0.38, ctx.W, ctx.H * 0.62, false, 0.85));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.06, y: ctx.H * 0.44, w: ctx.W * 0.78, h: ctx.H * 0.31 }, { style: "line", anchor: "bottom", size: Math.round(ctx.W * 0.22), grow: 1.4, sub: true, subRatio: 0.34 });
      ls.push(...hd.layers);
      return ls;
    } },
    // 띠형 (판정 5회차: 3단 띠·흰 띠는 꽉 찬 장면보다 낮음 → 가산점 ≤0 · 꽉 찬 장면이 안 될 때 채우는 몫)
    "쇼츠 · 레터박스 질문": { prior: 1.5, band: true, needs: { noText: true }, fn: ctx => {
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.075, w: ctx.W * 0.76, h: ctx.H * 0.22 }, { style: "word", align: "center", anchor: "bottom", size: Math.round(ctx.W * 0.2), emphScale: 1.1 });
      const { back, f, py } = framesBelow(ctx, { x: 0, y: 0, w: ctx.W, h: hd.box.y + hd.box.h + ctx.H * 0.045 }, 0.7, { hardTop: true, ty: 0.45 });  // 위 검은 띠에 제목, 아래는 사진 (확대 상한 안 · 남는 아래는 흐린 판)
      return [back, L("shape", { name: "검은 띠", x: 0, y: 0, w: ctx.W, h: py, fill: "#000000" }), f, ...hd.layers];
    } },
    "쇼츠 · 흰 띠 제목 (해주호형)": { prior: -3.5, band: true, needs: { noText: true }, fn: ctx => {  // 위 흰 띠 + 검은 굵은 글자 + 큰 줄은 노란 형광펜 (풋살해주호 쇼츠)
      const c = ctx.copy, b = ctx.brand.colors, bigI = c.l2 ? (c.emph ? c.emph[0] : 1) : 0;
      const mk = (t, size, hl, name) => { const l = L("text", Object.assign({ text: t, name }, tStyle(ctx, size, { fill: "#111111", sw: 0, noShadow: true, align: "center" }),
        hl ? { box: { on: true, color: b.hl, pad: Math.round(size * 0.1), radius: Math.round(size * 0.06) } } : {})); l.runs = digitRuns(t, l.font); fitText(l); fitW(l, ctx.W * 0.76); return l; };
      const lines = c.l2 ? [mk(c.l1, Math.round(ctx.W * (bigI === 0 ? 0.14 : 0.12)), bigI === 0, bigI === 0 ? "제목 큰 줄" : "제목 작은 줄"), mk(c.l2, Math.round(ctx.W * (bigI === 1 ? 0.14 : 0.12)), bigI === 1, bigI === 1 ? "제목 큰 줄" : "제목 작은 줄")] : [mk(c.l1, Math.round(ctx.W * 0.15), true, "제목 큰 줄")];
      let y = ctx.H * 0.085;
      for (const l of lines) { l.x = ctx.W * 0.45 - l.w / 2; l.y = y; y += l.h + ctx.H * 0.012; }
      const { back, f, py } = framesBelow(ctx, { x: 0, y: 0, w: ctx.W, h: y + ctx.H * 0.04 }, 0.75, { hardTop: true });  // 사진은 확대 상한 안에서 (남는 아래는 흐린 판)
      return [back, L("shape", { name: "흰 띠", x: 0, y: 0, w: ctx.W, h: py, fill: "#FFFFFF" }), f, ...lines];
    } },
    "쇼츠 · 상자 제목": { prior: -2, band: true, needs: {}, fn: ctx => {  // 위에 상자 제목 · 사진은 그 아래 자기 칸에 (머리를 가리지 않게)
      const hd = boxHead(ctx, ctx.W * 0.07, ctx.H * 0.3, ctx.W * 0.72, { size: ctx.W * 0.18, ratio: 0.6 });
      for (const l of hd.layers) l.y -= Math.min(0, bbox(hd.layers).y - ctx.H * 0.08);
      const { back, f } = framesBelow(ctx, bbox(hd.layers)), ls = [back, f];
      const cut = cutLayer(ctx, f, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      return ls;
    } },
  },
};
/* ----- 예전 템플릿 장면 칸을 얼굴·머리에 맞춰 자르기 (판정 5회차 D-092 · P1 '자동 템플릿이 장면 가운데를 잘라 얼굴이 날아감') -----
   칸 571개 중 46% 가 주인공 머리를 절반도 못 보여 주고 34% 는 머리가 아예 없었음 (모든 칸이 fx 0.5 가운데 자르기).
   칸마다 얼굴 → 머리 → 사람 위쪽을 초점으로 머리가 칸 위 1/3 가운데에 오게 fx·fy 를 정하고, 그래도 머리가 70% 안 보이면 다른 장면으로 바꿈 */
const frameT = src => { const m = /^\/frame\?.*[?&]t=([0-9.]+)/.exec(src || ""); return m ? +m[1] : null; };
function frameMeta(t) {
  const same = f => f && Math.abs(f.t - t) < 1e-6;
  return (AI.frames || []).find(same) || (Array.isArray(FRAMES) ? FRAMES : []).find(same) || null;
}
function slotFocus(f) {  // 칸이 지켜야 할 곳 (장면 좌표): 얼굴 → 머리 → 사람 위쪽 · 사람이 없으면 null
  if (!f) return null;
  const fc = mainFace(f) || (f.faces && f.faces[0] ? f.faces[0].box : null), hb = headBox(f);
  if (fc) return { pt: [fc[0] + fc[2] / 2, fc[1] + fc[3] / 2], head: hb || fc };
  if (hb) return { pt: [hb[0] + hb[2] / 2, hb[1] + hb[3] / 2], head: hb };
  const m = mainBox(f); return m ? { pt: [m[0] + m[2] / 2, m[1] + m[3] * 0.3], head: null } : null;
}
function anchorSlot(l, f, ar) {  // 머리(얼굴)가 칸 위 1/3 가운데에 오게 fx·fy → 머리가 칸 안에 70% 넘게 보이는지
  const sf = slotFocus(f); if (!sf) return true;
  const r = imgRect(l, ar), [u, v] = sf.pt;
  const ux = ((l.flipX ? 1 - u : u) - (l.cropL || 0)) / r.cw, vy = (v - (l.cropT || 0)) / r.ch;
  if (r.dw > l.w + 1) l.fx = clamp((l.w * 0.5 - ux * r.dw) / (l.w - r.dw), 0, 1);
  if (r.dh > l.h + 1) l.fy = clamp((l.h * 0.33 - vy * r.dh) / (l.h - r.dh), 0, 1);
  if (!sf.head) return true;
  const b = boxC(l, sf.head, ar), vis = clipTo(b, { x: l.x, y: l.y, w: l.w, h: l.h });
  return areaOf(b) > 0 && areaOf(vis) / areaOf(b) >= 0.7;
}
function anchorTpl(layers, ts) {  // layers: 예전 템플릿이 만든 레이어 · ts: 그 템플릿에 넘긴 장면 시각들 (바꿔 넣을 후보)
  const ar = frameAspect(), used = new Set(layers.map(l => frameT(l.src)).filter(t => t != null));
  for (const l of layers) {
    if (l.type !== "image" || l.fit === "contain") continue;
    const t = frameT(l.src); if (t == null) continue;
    const twin = layers.filter(c => c !== l && c.type === "image" && c.orig === l.src && c.x === l.x && c.y === l.y && c.w === l.w && c.h === l.h);  // 배경과 같이 놓인 누끼
    let ok = anchorSlot(l, frameMeta(t), ar);
    if (!ok && !twin.length && (l.blur || 0) < 8) {  // 머리를 칸에 담을 수 없으면 다음 장면으로
      for (const t2 of ts || []) {
        if (used.has(t2)) continue;
        const f2 = frameMeta(t2); if (!f2 || !mainBox(f2) || srcHeadCut(f2)) continue;
        const l2 = Object.assign({}, l, { src: frameSrc(t2) });
        if (anchorSlot(l2, f2, ar)) { Object.assign(l, { src: l2.src, fx: l2.fx, fy: l2.fy }); used.add(t2); ok = true; break; }
      }
    }
    for (const c of twin) Object.assign(c, { fx: l.fx, fy: l.fy });
  }
  return layers;
}
// 예전 템플릿(TPL)은 fn(장면 시각들, 첫 줄, 둘째 줄, 누끼)이라 어댑터로 감싸 같이 씀 (예전 후보 화면·e2e 는 그대로)
function legacyTpl(fmt) {
  return Object.fromEntries(Object.entries(TPL[fmt]).map(([k, fn]) => [k, { legacy: true, needs: {}, fn: ctx => {
    const ts = [ctx.frame.t, ...ctx.frames.filter(f => f.t !== ctx.frame.t).map(f => f.t)];
    return anchorTpl(fn(ts, ctx.copy.l1, ctx.copy.l2, null), ts);  // 예전 템플릿은 배경 자리가 달라 자동 누끼를 겹치지 않음 · 칸은 얼굴·머리에 맞춰 자름
  } }]));
}
const allTpl = fmt => Object.assign({}, T_NEW[fmt], legacyTpl(fmt));
function wholeBody(f) {  // 주인공이 위아래로 잘리지 않고 보이는지 (발만 나온 장면에 누끼·전술을 넣지 않게)
  const m = mainBox(f);
  if (f.faces && f.faces.length && f.kind === "close") return true;
  return !!m && m[1] > 0.015 && m[3] >= 0.18 && m[3] <= 0.8;
}
// 코치 + 경기 장면: 얼굴이 크게 보이는 사람(코치·인터뷰)만 · 사람 상자가 원본 위·좌·우 끝에 닿지 않음 · 롱폼은 가로 원본만
// (판정 2회차: 세로 영상의 머리 잘린 분홍 조끼 선수를 16:9 칸에 붙여 네모 모서리로 잘린 누끼)
function comboOk(ctx) {
  const f = ctx.frame, m = mainBox(f), fc = mainFace(f);
  if (!m || !fc || fc[3] < 0.12 || f.kind !== "close" || (f.blur ?? 0) > 0.3) return false;
  if (m[1] <= 0.01 || m[0] <= 0.01 || m[0] + m[2] >= 0.99) return false;
  if (!ctx.short && ctx.ar < 1.2) return false;
  return !!actionFrame(ctx);
}
function fits(t, ctx) {
  const n = t.needs || {}, f = ctx.frame;
  if ((n.cut || n.tactics) && !wholeBody(f)) return false;
  if (n.kinds && !n.kinds.includes(f.kind)) return false;
  if (n.sharp != null && (f.blur ?? 1) > n.sharp) return false;
  if (n.cut && !ctx.cut) return false;
  if (n.persons && (f.persons || []).length < n.persons) return false;
  if (n.tactics && !tacOk(ctx)) return false;
  if (n.sub && !ctx.copy.sub) return false;
  if (n.noText && (f.text || 0) >= TEXTY) return false;  // 누끼를 못 넣는 템플릿은 글자 박힌 장면을 안 씀
  if (n.vertical && !(ctx.ar < 0.9)) return false;  // 세로 영상 전용
  if (n.combo && !comboOk(ctx)) return false;  // 얼굴이 크게 보이는 사람(코치·인터뷰)만 — 뒷모습 선수를 다른 장면에 붙이면 유령처럼 보임
  if (n.ox && !(ctx.copy.ox && ctx.copy.ox.length === 2)) return false;
  if (n.full && !fullOk(ctx)) return false;  // 꽉 찬 쇼츠: 원본을 SHORT_FULL_CAP 배 안에서 9:16 전체를 덮을 수 있을 때만
  if (n.notFull && fullOk(ctx)) return false;  // 여러 칸 쇼츠: 한 장면으로 꽉 채울 수 없을 때만 (판정 5회차: '합성 티')
  if (n.second && !secondFrame(ctx)) return false;  // 두 장면 칸: 다른 좋은 장면이 있어야
  if (n.big && !bigEnough(ctx, n.big)) return false;  // 큰 누끼: 원본에서 주인공이 충분히 커야 (작은 선수를 키우면 깨짐)
  if (n.action && !actionPose(f)) return false;  // 따로 크게 놓는 누끼: 동작 중인 장면만 (D-119)
  if (n.solo && cutCrowded(f)) return false;  // 따로 옮기는 누끼: 다른 사람 머리가 마스크에 들어갈 장면은 안 씀 (D-119)
  return true;
}
// 판정 q5 2회차(D-115): 짝 판정 — 채널 엠블럼을 뺀 쪽을 프로로 20%(112짝 · 롱폼 15%) · 롱폼 배경 비네팅 45 를 더한 쪽 61%(102짝, 쇼츠는 51%로 차이 없음)
// → 엠블럼이 없는 템플릿(쇼츠 대부분·질문 훅 등)에도 글자를 피해 넣고, 롱폼 배경은 가장자리를 조금 어둡게
const VIGNETTE_LONG = 40;
// 스타일의 배경 버릇(밝기·채도 배율 · 쌈바형은 어둡게, 밝은 채널은 밝게) · 예전 템플릿은 고정 노랑·흰 글자를 스타일 강조색으로
function styleLayers(layers, b, legacy) {
  const sp = b && b._st; if (!sp) return layers;
  for (const l of layers) {
    if (l.type === "image" && l.name !== "누끼" && /배경|장면/.test(l.name || "")) {
      if (sp.bgBright) l.bright = clamp(Math.round((l.bright ?? 100) * sp.bgBright), 25, 130);
      if (sp.bgSat) l.sat = clamp(Math.round((l.sat ?? 100) * sp.bgSat), 40, 150);
    }
    if (legacy && l.type === "text") {
      const f = toHex(l.fill || "").toUpperCase(), to = f === "#FFE14D" ? b.colors.hl : f === "#FFFFFF" ? b.colors.hl2 : null;
      if (to && !(l.box && l.box.on && toHex(l.box.color || "").toUpperCase() === toHex(to).toUpperCase())) l.fill = to;  // 상자 색과 같아지면 그대로
    }
  }
  return layers;
}
function finishDoc(ctx, t, layers) {
  styleLayers(layers, ctx.brand, !!t.legacy);
  if (t.legacy || !layers.length) return;
  const bg = layers.find(l => l.type === "image" && l.name === "배경");
  if (bg && !ctx.short && bg.x <= 1 && bg.y <= 1 && bg.x + bg.w >= ctx.W - 1 && bg.y + bg.h >= ctx.H - 1) bg.vignette = Math.max(bg.vignette || 0, VIGNETTE_LONG);
  if (!layers.some(l => l.name === "로고")) {
    const lg = logoLayer(ctx, layers.filter(l => !l.hidden && (l.type === "text" || (l.type === "shape" && TAC_DEF[l.shape]))).map(extentOf));
    if (lg) layers.push(normLayer(lg));
  }
}
function buildDoc(name, t, ctx) {
  const layers = t.fn(ctx).filter(Boolean).map(normLayer);
  finishDoc(ctx, t, layers);
  const bg = layers.find(l => l.type === "image" && l.name === "배경");
  let texty = false;
  if ((ctx.frame.text || 0) >= TEXTY) for (const l of layers) if (l.type === "image" && l.name === "흐린 배경") { l.blur = Math.max(l.blur || 0, Math.round(ctx.W * 0.04)); l.bright = Math.min(l.bright, 42); }  // 뒤에 깐 흐린 판도 글자가 안 읽히게
  if (bg && (ctx.frame.text || 0) >= TEXTY) {  // 글자 박힌 장면: 피해서 잘랐는데도 글자가 보이면 → 누끼가 있으면 배경을 아주 흐리고 어둡게 + 브랜드 색 덮개 (주인공만 또렷), 없으면 후보에서 뺌
    const vt = ctx.frame.tboxes ? visibleText(bg, ctx.frame, ctx.ar, ctx.W, ctx.H) : 1, cut = layers.some(l => l.name === "누끼");
    if (vt > 0.008) {
      texty = !cut;
      bg.blur = Math.max(bg.blur || 0, Math.round(ctx.W * (cut ? 0.04 : 0.012))); bg.bright = Math.min(bg.bright, cut ? 42 : 70);
      if (cut) {
        bg.sat = 60; layers.splice(layers.indexOf(bg) + 1, 0, normLayer(L("shape", { name: "색 덮개", x: 0, y: 0, w: ctx.W, h: ctx.H, fill: mix(ctx.brand.colors.box, "#0A2A6B", 0.6), fill2: "#000000", gradAngle: 60, opacity: 0.55 })));
        // 배경이 흐린 판이 됐으니 누끼는 배경과 따로 옮겨도 됨 → 주인공이 화면 끝에 걸려 잘리면 안쪽으로 (제목 반대쪽 끝에 맞춤)
        const cl = layers.find(l => l.name === "누끼"), m = mainBox(ctx.frame);
        if (cl && m) {
          let b = boxC(cl, m, ctx.ar);
          const k = clamp((ctx.short ? 0.42 : 0.72) * ctx.H / Math.max(1, b.h), 1, 1.8);  // 작게 나온 주인공은 키워서 (배경과 맞출 필요가 없으니)
          if (k > 1.05) { const cx = b.x + b.w / 2, cy = b.y + b.h / 2; cl.x = cx - (cx - cl.x) * k; cl.y = cy - (cy - cl.y) * k; cl.w *= k; cl.h *= k; b = boxC(cl, m, ctx.ar); }
          const tb = bbox(layers.filter(l => l.type === "text")), mg = ctx.W * 0.02;
          if (ctx.short && b.y < tb.y + tb.h + ctx.H * 0.03) cl.y += tb.y + tb.h + ctx.H * 0.03 - b.y;  // 쇼츠: 제목 아래로
          b = boxC(cl, m, ctx.ar);
          if (b.y + b.h > ctx.H && !ctx.short) cl.y -= Math.min(b.y + b.h - ctx.H, Math.max(0, b.y - ctx.H * 0.03));
          const dx = b.x < mg ? mg - b.x : b.x + b.w > ctx.W - mg ? ctx.W - mg - (b.x + b.w) : 0;
          if (dx && b.w < ctx.W * 0.9) cl.x += dx;
        }
      }
    }
  }
  return { w: ctx.W, h: ctx.H, bg: "#000000", layers, _texty: texty };
}

/* ----- 점수 (0~100): 하드 게이트(글자 잘림·가려지는 곳·작게 봤을 때 글자 높이·대비·얼굴·머리 가림·머리 잘림) + 가중합 − 감점 ----- */
// 0.22 장면 + 0.22 가독성 + 0.14 위계 + 0.14 가림 없음 + 0.12 문구 + 0.08 안전 영역 + 0.08 구도 − 주인공 작음·글자 박힘·누끼 품질
function extentOf(l) {  // 획·그림자·광선까지 포함한 상자
  const b = bbox([l]); let x0 = b.x, y0 = b.y, x1 = b.x + b.w, y1 = b.y + b.h;
  if (l.shadow && l.shadow.on) { x1 += Math.max(0, l.shadow.dx) + l.shadow.blur * 0.5; y1 += Math.max(0, l.shadow.dy) + l.shadow.blur * 0.5; x0 += Math.min(0, l.shadow.dx); y0 += Math.min(0, l.shadow.dy); }
  if (l.glow && l.glow.on) { const g = l.glow.size * 0.5; x0 -= g; y0 -= g; x1 += g; y1 += g; }
  if (l.outline && l.outline.on) { const g = l.outline.width; x0 -= g; y0 -= g; x1 += g; y1 += g; }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}
function glyphPx(l, Wd, small) {  // 목록 크기(small px 폭)에서 이 글자 레이어의 가장 작은 글자 높이
  const m = textLayout(ctx, l), sy = l.h / m.natH, sp = m.lines.flatMap(ln => ln.spans).filter(s => s.t.trim());
  return (sp.length ? Math.min(...sp.map(s => s.st.size)) : l.size) * sy * small / Wd;
}
const isHead = l => l.type === "text" && /^제목/.test(l.name || "");
function headLayers(doc) {
  const ts = doc.layers.filter(l => l.type === "text" && !l.hidden);
  const h = ts.filter(isHead);
  return h.length ? h : ts.sort((a, b) => b.size * b.h - a.size * a.h).slice(0, 2);
}
function strongStroke(l) {  // 대비 통과: 바깥 획 ≥ 글자 크기 12% · 또는 상자·광선 · 또는 (판정 5회차 D-090) 얇은 획 ≥7% + 부드러운 진한 그림자 (프리텐다드 블랙 기본 모양)
  if (l.box.on || (l.glow.on && l.glow.size >= 20) || (l.outline.on && l.outline.width >= 4)) return true;
  const soft = l.shadow && l.shadow.on && (l.shadow.opacity ?? 1) >= 0.6 && (l.shadow.blur || 0) >= l.size * 0.08, k = soft ? 0.07 : 0.12;
  const m = textLayout(ctx, l); return m.lines.flatMap(ln => ln.spans).filter(s => s.t.trim()).every(s => Math.max(s.st.s1w, s.st.s2w) >= s.st.size * k);
}
// 색이 요란한 원본 (colorfulness · 얼굴 장면은 얼굴이 주인공이라 뺌) — 판정 4회차: 레퍼런스 썸네일 168장 중앙값 59·상위 10% 83 (70 은 47장이 넘음)
// → 레퍼런스 상위 10% 보다 요란할 때만, 넘는 만큼 조금씩 깎음 (빼지 않음 · 가장 선명한 액션 장면을 잃지 않게)
const LOUD = 85;
const loud = f => !!f && f.kind !== "close" && (f.color || 0) > LOUD;
const loudMult = f => (loud(f) ? clamp(1 - ((f.color || 0) - LOUD) / 60, 0.7, 1) : 1);
const blurQ = f => { const b = f.blur ?? 0; return b <= 0.15 ? 1 : Math.max(0.55, 1 - (b - 0.15) * 1.6); };
// 판정 q5 1회차(D-095): 이 형식(롱폼·쇼츠) 캔버스에서 주인공이 얼마나 크고 홀로 또렷한지 (0.65~1.12) — 쇼츠 주인공 키 ↔ scene +0.49 · 두 번째로 큰 사람 비율 ↔ pro −0.47 ·
// 화면에 보이는 사람 수 ↔ pro −0.43 (롱폼은 scene 만 −0.34) · 쇼츠 주인공 키 중앙값 0.33H (45장 중 27장이 0.4H 아래)
function subjQ(f, fmt) {
  const m = mainBox(f); if (!m) return 0.75;
  if (f.kind === "close") return 1;
  const short = fmt === "short", ar = frameAspect(), cAr = short ? 1080 / 1920 : 1280 / 720;
  const vis = ar >= cAr ? 1 : ar / cAr;  // 덮을 때 원본 높이 중 보이는 몫 (세로 원본 롱폼은 0.32)
  const ctx0 = { short, W: short ? 1080 : 1280, H: short ? 1920 : 720, ar, frame: f, band: f.band };
  const zmax = maxZoom(ctx0), h0 = Math.min(1, m[3] / vis), want = short ? 0.66 : 0.72;
  const z = clamp(want / Math.max(1e-6, h0), 1, zmax), hc = Math.min(1, h0 * z);
  let k = (short ? clamp(0.5 + 0.95 * hc, 0.65, 1.12) : clamp(0.78 + 0.45 * hc, 0.85, 1.1)) * (1 - 0.06 * (z - 1));  // 많이 키워야 커지는 장면은 조금 덜 (원래 큰 장면이 더 또렷)
  // 경쟁자: 주인공 키의 60%(롱폼 70%) 넘는 다른 사람 — 쇼츠는 9:16 창(주인공 가운데) 안에 들어오는 사람만
  const win = short && ar > cAr ? cAr / ar / z : 1, cx = m[0] + m[2] / 2;
  const rivals = (f.persons || []).filter(p => p !== m && p[3] >= (short ? 0.6 : 0.7) * m[3] && Math.abs(p[0] + p[2] / 2 - cx) <= win / 2 + p[2] * 0.3).length;
  k *= Math.max(short ? 0.7 : 0.85, Math.pow(short ? 0.88 : 0.95, rivals));
  return k;
}
function tangleOf(f) {  // 주인공과 다른 사람(주인공 키의 60% 넘게 큰)의 상자 겹침 — 두 상자 가운데 작은 쪽 넓이 대비 (0~1 · 얼굴 클로즈업은 0)
  const m = f && f.main >= 0 ? (f.persons || [])[f.main] : null; if (!m || f.kind === "close") return 0;
  return Math.max(0, ...(f.persons || []).filter(p => p !== m && p[3] >= 0.6 * m[3]).map(p => {
    const i = Math.max(0, Math.min(m[0] + m[2], p[0] + p[2]) - Math.max(m[0], p[0])) * Math.max(0, Math.min(m[1] + m[3], p[1] + p[3]) - Math.max(m[1], p[1]));
    return i / Math.max(1e-6, Math.min(m[2] * m[3], p[2] * p[3]));
  }));
}
function biggerRival(f) {  // 다른 사람 가운데 가장 큰 키 ÷ 주인공 키 (얼굴 클로즈업·주인공 없음은 0)
  const m = f && f.main >= 0 ? (f.persons || [])[f.main] : null; if (!m || f.kind === "close") return 0;
  return Math.max(0, ...(f.persons || []).filter(p => p !== m).map(p => p[3] / Math.max(1e-6, m[3])));
}
function frameQ(f, maxS) {  // 장면 품질 0~1 (점수를 눌러 펴고 · 벤치·뒷모습·끝에 걸림·작음·글자 박힘·흔들림은 깎음 · 클로드 장면 점수가 있으면 크게 반영)
  let q = Math.pow(clamp((f.score || 0) / Math.max(1e-6, maxS || f.score || 1), 0, 1), 0.35);
  if (f.ai != null) q = Math.pow(q, 0.4) * Math.pow(clamp((f.ai - 1) / 8, 0.05, 1), 0.6);  // 클로드(1~10): 레슨과 무관한 사람·관중·잡지 같은 장면을 거름
  const fl = f.flags || [];
  if (fl.includes("bench")) q *= 0.55;
  if (fl.includes("crowd")) q *= 0.75;
  if (fl.includes("back")) q *= 0.75;
  if (fl.includes("edge")) q *= 0.8;
  if (fl.includes("small")) q *= 0.85;
  if (fl.includes("lesson") && !srcHeadCut(f)) q = Math.min(1, q * 1.15);  // 판정 5회차: 다리만 나온 장면이 '공 다루는 순간' 가산을 받아 위로 (16장 중 8장) → 머리가 보일 때만
  if ((f.text || 0) >= TEXTY) q *= 0.8;
  // 판정 q5 2회차(D-113): '사람끼리 엉킴·겹침' 22%(레퍼런스 10%) — 주인공 몸의 35% 넘게 다른 사람(주인공 키 60% 넘게 큰)이 겹친 장면 (두 사람이 붙은 몸싸움 · 누가 주인공인지 모름)
  // 검토(D-119): 주인공 몸 쪽에서만 재면 뒤에 가려진 사람(작은 상자로 잡힘)이 26% 로 빠져나감 (스톡 002 37.7초 '두 선수가 겹쳐 주인공이 누구인지' 14번) →
  // 두 쪽 모두에서 (다른 사람 상자의 몇 %가 주인공 상자 안인지도)
  { const ov = tangleOf(f); if (ov > 0.35) q *= Math.max(0.6, 1 - (ov - 0.35) * 1.2); }
  // 검토(D-119): 공을 가진 주인공보다 더 크게 나온 다른 사람 (스톡 003 53.6초 주인공 키의 1.95배 · 001 59.1초 1.19배 — '주인공이 없음'·'누가 주인공인지' 9번) → 조금 깎음
  { const k = biggerRival(f); if (k > 1.1) q *= Math.max(0.75, 1 - (k - 1.1) * 0.5); }
  q *= blurQ(f);  // 판정 q5 1회차(D-095): 주인공 흔들림 0.15~0.35 도 '흐림' 지적 47~50% · 0.35 넘으면 83% (예전엔 0.35 넘을 때만 ×0.8)
  if (headlessBad(f)) q *= 0.45;  // 원본에서 머리가 잘린 주인공 (D-098: 발·공 클로즈업도 같게 — 사용자가 고를 때만 씀)
  q *= loudMult(f);  // 분홍·초록 낙서 벽처럼 색이 요란한 원본 (판정 3회차) — 부드럽게 깎음 (판정 4회차)
  if (f.grade && f.grade.gamma > 2.2) q *= 0.5;  // 아주 어두운 장면: 밝히면 노이즈가 커져 흐릿 (판정: '얼굴을 크게 확대해 화질이 심하게 흐림')
  else if (f.grade && (f.grade.gamma || 1) > 1.6) q *= 0.85;  // 판정 q5 2회차(D-113): 감마 1.6 넘게 밝힌 저녁·밤 장면 — 묶음 안 pro −0.24 · '흐림/화질' 지적 53% (낮 장면 23~31%) · 빼지는 않음 (대신 들어온 낮 장면이 더 낮았음 — 개발 판정 r_a)
  if (f.kind === "close" && f.emo && (f.emo.happiness || 0) + (f.emo.surprise || 0) < 0.12 && (f.faces || []).length) q *= 0.9;  // 말하는 도중 무표정 얼굴 (판정: '말하다 멈춘 얼굴')
  if (f.kind === "close" && f.emo && (f.emo.happiness || 0) + (f.emo.surprise || 0) >= 0.4) q = Math.min(1, q * 1.12);  // 살아 있는 표정
  return q;
}
const ACTION_COPY = /슈팅|슛|드리블|돌파|드래그|터치|트래핑|킥|패스|페인트|수비 전환|압박/;
const shownScore = s => (s <= 85 ? s : 85 + 15 * (1 - Math.exp(-(s - 85) / 15)));  // 85 넘으면 100 에 다가가기만 (같은 점수 없음)
// 큰 줄 글자 크기 하한 (화면 높이 대비 · 판정 4회차 OCR 실측: 레퍼런스 롱폼 가장 큰 줄 상자 중앙값 0.21H·하위 25% 0.19H, 쇼츠 표지 0.061H ·
// 글꼴 크기 → 글자 상자 ≈ ×1.15) — 줄이기보다 짧은 문구가 이기게 (게이트)
const BIG_MIN = { long: 0.165, short: 0.056 };
function bigPx(l) {  // 글자 레이어의 가장 큰 글자 크기 (캔버스 px · 맞춤 배율 반영)
  const m = textLayout(ctx, l), sy = l.h / m.natH, sp = m.lines.flatMap(ln => ln.spans).filter(s => s.t.trim());
  return (sp.length ? Math.max(...sp.map(s => s.st.size)) : l.size) * sy;
}
// 쇼츠: 위 80% (아래는 유튜브 제목·버튼이 덮음) 가운데 흐린 판만 보이는 줄의 비율 — 판정 4회차: 가로 장면을 9:16 에 얹고 아래 40~50% 를 흐린 판으로 채움
function blurFill(doc) {
  const W0 = doc.w, top = doc.h * 0.8;
  const sharp = doc.layers.filter(l => !l.hidden && ((l.type === "image" && (l.blur || 0) < 6 && !/누끼|로고|스티커/.test(l.name || "")) || (l.type === "shape" && /띠/.test(l.name || "") && (l.opacity ?? 1) >= 0.9)));
  const soft = doc.layers.filter(l => !l.hidden && l.type === "image" && (l.blur || 0) >= 6);
  if (!soft.length) return 0;
  const cov = (l, y) => (y >= l.y && y <= l.y + l.h ? Math.max(0, Math.min(W0, l.x + l.w) - Math.max(0, l.x)) / W0 : 0);
  let n = 0, k = 0;
  for (let y = 4; y < top; y += 8) { k++; if (!sharp.some(l => cov(l, y) >= 0.85) && soft.some(l => cov(l, y) > 0.5)) n++; }
  return k ? n / k : 0;
}
const BLUR_FILL_MAX = 0.15;
// 판정 5회차(D-090): 두 줄 크기 비율(큰/작은) 점수 — 같은 크기(1.0~1.25) 또는 확실한 꼬리표(≥2.2)가 좋고 1.4~2.0 은 낮음 (예전엔 1.8 이상이 만점이라 어중간한 0.56 이 이김)
function hierOf(r) { return r <= 1.25 ? 1 : r >= 2.2 ? 1 : r < 1.4 ? 1 - (r - 1.25) / 0.15 * 0.6 : r > 2.0 ? 0.4 + (r - 2.0) / 0.2 * 0.6 : 0.4; }
function archetype(doc, heads, short, fullBleed, canFull = true) {
  const Wd = doc.w, Hd = doc.h; let a = 0;
  if (heads.length) {
    const B = bbox(heads), widest = Math.max(...heads.map(l => bbox([l]).w));
    const edge = short ? B.y <= Hd * 0.12 || B.y + B.h >= Hd * 0.66 : B.y <= Hd * 0.08 || B.y + B.h >= Hd * 0.86;
    if (edge) a += 3;
    if (widest >= Wd * (short ? 0.74 : 0.68)) a += 3;
    if (heads.some(l => (l.box && l.box.on) || Math.abs(l.rot || 0) > 0.5)) a -= 3;
  }
  if (doc.layers.some(l => l.type === "shape" && /^(왼쪽|오른쪽) 어둡게$/.test(l.name || "") && (l.opacity ?? 1) > 0.45)) a -= 4;
  if (short && canFull) {  // 꽉 찬 장면을 만들 수 있는 원본일 때만 띠·여러 칸을 깎음
    if (fullBleed) a += 4;
    if (doc.layers.some(l => l.type === "image" && /장면 [23]/.test(l.name || ""))) a -= 4;
    if (doc.layers.some(l => l.type === "shape" && /띠/.test(l.name || "") && (l.opacity ?? 1) >= 0.9)) a -= 2;
  }
  return a;
}
// 캔버스에 보이는 사람: 주인공(보이는 키) · 경쟁자(주인공 키의 60% 넘게 보이는 다른 사람, 몸의 절반 넘게 화면 안) · 제목·스티커가 주인공 몸(머리 아래)·다리(아래 45%)를 덮는 비율
function canvasPeople(doc, f, geo, ar) {
  const W0 = doc.w, H0 = doc.h, view = clipTo({ x: geo.x, y: geo.y, w: geo.w, h: geo.h }, { x: 0, y: 0, w: W0, h: H0 }), m = mainBox(f);
  const mb = boxC(geo, m, ar), mv = clipTo(mb, view);
  const cover = doc.layers.filter(l => !l.hidden && (l.type === "text" || /스티커/.test(l.name || "")) && !/설명/.test(l.name || "")).map(extentOf);
  let rivals = 0;
  for (const p of f.persons || []) {
    if (p === m) continue;
    const b = boxC(geo, p, ar), v = clipTo(b, view);
    if (areaOf(b) > 0 && areaOf(v) / areaOf(b) >= 0.5 && v.h >= 0.6 * mv.h) rivals++;
  }
  const hb = headBox(f), hc = hb ? boxC(geo, hb, ar) : null, top = hc ? Math.min(mv.y + mv.h, hc.y + hc.h) : mv.y + mv.h * 0.15;
  const body = clipTo({ x: mv.x, y: top, w: mv.w, h: mv.y + mv.h - top }, view), legs = clipTo({ x: mv.x, y: mv.y + mv.h * 0.55, w: mv.w, h: mv.h * 0.45 }, view);
  const covOf = r => (areaOf(r) ? Math.min(1, cover.reduce((t, c) => t + interArea(c, r), 0) / areaOf(r)) : 0);
  return { h: mv.h / H0, rivals, bodyCov: covOf(body), legCov: covOf(legs) };
}
function scoreDoc(doc, meta) {
  const Wd = doc.w, Hd = doc.h, short = Hd > Wd, small = short ? 110 : 168, gates = [], why = [];
  const heads = headLayers(doc), texts = doc.layers.filter(l => l.type === "text" && !l.hidden);
  const ui = doc.layers.filter(l => !l.hidden && (l.type === "text" || /스티커|로고/.test(l.name || "")));
  // 1) 잘림 · 가려지는 곳
  const mx = Wd * 0.015, my = Hd * 0.015;
  for (const l of ui) {
    const e = extentOf(l);
    if (e.x < mx || e.y < my || e.x + e.w > Wd - mx || e.y + e.h > Hd - my) gates.push(`잘림: ${lname(l)}`);
    if (!short && e.x + e.w > Wd * 0.84 && e.y + e.h > Hd * 0.86) gates.push(`재생시간 자리: ${lname(l)}`);
    const sx1 = e.y + e.h <= Hd * SAFE.short.top ? SAFE.short.x1Top : SAFE.short.x1;  // 판정 5회차: 버튼은 0.42H 아래만 → 위쪽 제목은 0.94W 까지 (레퍼런스 가운데 제목 x1 ≈ 0.9)
    if (short && l.type === "text" && (e.x < Wd * 0.06 - 2 || e.x + e.w > Wd * sx1 + 2 || e.y < Hd * 0.07 - 2 || e.y + e.h > Hd * 0.76 + 2)) gates.push(`쇼츠 안전 영역 밖: ${lname(l)}`);
    if (short && e.x + e.w > Wd * 0.86 && e.y + e.h > Hd * 0.42 && e.y < Hd * 0.87) gates.push(`쇼츠 버튼 자리: ${lname(l)}`);
    if (short && e.y + e.h > Hd * 0.8) gates.push(`쇼츠 제목 자리: ${lname(l)}`);
  }
  // 2) 작게 봤을 때 글자 높이 (헤드라인 ≥ 12px, 나머지 글자 ≥ 8px)
  const hpx = heads.length ? Math.max(...heads.map(l => glyphPx(l, Wd, small))) : 0;
  if (hpx < 12) gates.push(`작게 보면 안 읽힘 (${hpx.toFixed(1)}px)`);
  const bigHead = heads.find(l => /큰/.test(l.name || "")) || heads[0];
  const bigH = bigHead ? bigPx(bigHead) / Hd : 0;
  if (bigHead && bigH < BIG_MIN[short ? "short" : "long"]) gates.push(`큰 줄이 작음 (${(bigH * 100).toFixed(1)}%H)`);
  const bf = short ? blurFill(doc) : 0;
  if (bf > BLUR_FILL_MAX) gates.push(`흐린 빈 곳 ${Math.round(bf * 100)}%`);
  for (const l of texts) if (glyphPx(l, Wd, small) < 8 && !/시리즈|채널|설명/.test(l.name || "")) gates.push(`작은 글자: ${lname(l)}`);  // 설명 글자는 큰 화면용 (레퍼런스 0.045~0.06H)
  // 3) 대비 (획이 약하면 정밀 단계에서 배경 밝기로 다시 봄)
  const weak = heads.filter(l => !strongStroke(l));
  // 4) 얼굴·머리 가림 · 머리 잘림 · 전술 그래픽과 제목 겹침
  const bg = doc.layers.find(l => l.type === "image" && l.name === "배경") || doc.layers.find(l => l.type === "image" && /배경/.test(l.name || "")), f = meta.frame, hb = heads.map(extentOf), ar = frameAspect();
  const cover = ui.filter(l => l.type === "text" || /스티커/.test(l.name || "")).map(extentOf);
  let faceHit = 0, headHit = 0;
  const mf = bg ? mainFace(f) || (f.faces && f.faces[0] ? f.faces[0].box : null) : null;
  if (mf) {
    const fb = boxC(doc.layers.find(l => l.name === "누끼" && !l.hidden) || bg, mf, ar); fb.y -= fb.h * 0.35; fb.h *= 1.35;  // 얼굴 상자 + 이마·머리 (제목이 이마에 얹힌다는 평)
    const vis = clipTo(fb, clipTo({ x: bg.x, y: bg.y, w: bg.w, h: bg.h }, { x: 0, y: 0, w: Wd, h: Hd }));  // 보이는 곳만 (레터박스 위 검은 띠로 올라간 머리는 없음)
    const fa = areaOf(vis);
    for (const b of hb) faceHit += fa ? interArea(b, vis) / fa : 0;
    if (faceHit > 0.06) gates.push(`얼굴을 가림 (${Math.round(faceHit * 100)}%)`);
  }
  const hbx = bg ? headBox(f) : null, geo = doc.layers.find(l => l.name === "누끼" && !l.hidden) || bg;  // 누끼를 따로 옮겼으면(글자 박힌 장면) 그 자리의 머리
  if (hbx) {  // 주인공 머리: 화면(배경 레이어) 밖으로 잘림 → 게이트 · 글자·스티커로 덮임 → 25% 넘으면 게이트 (판정: '무조건 봐'가 선수 머리를 덮음 · 확대 뒤 머리 잘림 58/84)
    const b = boxC(geo, hbx, ar), view = clipTo({ x: bg.x, y: bg.y, w: bg.w, h: bg.h }, { x: 0, y: 0, w: Wd, h: Hd }), vis = clipTo(b, view), ba = areaOf(b);
    if (ba > 0 && areaOf(vis) / ba < 0.7) gates.push("주인공 머리 잘림");
    const va = areaOf(vis); for (const c of cover) headHit += va ? interArea(c, vis) / va : 0;
    if (headHit > 0.25 && b.h >= Hd * (short ? 0.02 : 0.035)) gates.push(`주인공 머리를 가림 (${Math.round(Math.min(1, headHit) * 100)}%)`);  // 멀리 있는 아주 작은 선수 머리는 감점만
  }
  const tac = doc.layers.filter(l => l.type === "shape" && TAC_DEF[l.shape]);
  if (tac.some(t => hb.some(b => overlap(bbox([t]), b, 0)))) gates.push("전술 그래픽이 제목과 겹침");
  const mb = bg ? mainBox(f) : null;
  // 원본에서 머리가 잘린 주인공 (발·공 클로즈업이면 공이 보이고 글자에 안 가릴 때만) — 판정 5회차: 같은 자리에 깐 누끼(강좌 시리즈)도 (예전엔 누끼가 있으면 건너뜀)
  if (mb && (geo === bg || (geo && geo.x === bg.x && geo.y === bg.y && geo.w === bg.w && geo.h === bg.h))) {
    if (footOk(f, meta.copy) && srcHeadCut(f)) {
      const bb = boxC(bg, f.ball, ar), cx = bb.x + bb.w / 2, cy = bb.y + bb.h / 2;
      if (cx < 0 || cx > Wd || cy < 0 || cy > Hd || cover.some(c => cx > c.x && cx < c.x + c.w && cy > c.y && cy < c.y + c.h)) gates.push("발 클로즈업인데 공이 안 보임");
    } else if (srcHeadCut(f)) gates.push("주인공 머리 잘림 (원본)");
    else if (f.kind !== "close" && !footClose(f)) {
      const b = boxC(bg, mb, ar); if (b.y < Hd * 0.005 && b.y + b.h > Hd * 0.05) gates.push("주인공 머리 잘림 (화면 위)");
    }
    // 판정 5회차(D-091): 화면 옆 끝에 걸린 얇은 주인공(다리·팔만)인데 얼굴도 없음 — '누가 주인공' (판정 이유 69번)
    if ((f.flags || []).includes("edge") && f.kind !== "close" && !mainFace(f)) gates.push("주인공이 화면 끝에 걸림");
    // 판정 5회차(D-091): 세로 원본 롱폼에서 주인공 키의 28~40% 만 보임 · 얼굴도 없으면 '누가 주인공인지' 모름 → 몸의 60% 아래만 보이고 얼굴(0.08H 넘게)도 안 보이면 뺌
    const bc = boxC(bg, mb, ar), vis0 = clipTo(bc, clipTo({ x: bg.x, y: bg.y, w: bg.w, h: bg.h }, { x: 0, y: 0, w: Wd, h: Hd }));
    const kept = bc.h > 0 ? vis0.h / bc.h : 1, fcv = mainFace(f) ? boxC(bg, mainFace(f), ar) : null;
    const faceShown = fcv && fcv.h >= Hd * 0.08 && fcv.y >= 0 && fcv.y + fcv.h <= Hd;
    if (f.kind !== "close" && kept < 0.6 && !faceShown && !footOk(f, meta.copy)) gates.push(`주인공이 너무 잘림 (${Math.round(kept * 100)}%)`);
  }
  if (mb && geo && geo !== bg && !(geo.x === bg.x && geo.y === bg.y && geo.w === bg.w && geo.h === bg.h)) {  // 따로 옮긴 누끼도 레이어·캔버스 밖으로 몸이 잘리면 (머리 없는 몸통 누끼)
    const bc = boxC(geo, mb, ar), vis1 = clipTo(bc, clipTo({ x: geo.x, y: geo.y, w: geo.w, h: geo.h }, { x: 0, y: 0, w: Wd, h: Hd }));
    if (bc.h > 0 && vis1.h / bc.h < 0.6) gates.push(`누끼가 잘림 (${Math.round(vis1.h / bc.h * 100)}%)`);
  }
  // 가중합
  const fr = frameQ(f, meta.maxFrame);
  const read = clamp((hpx - 9) / (17 - 9), 0, 1) * (weak.length ? 0.6 : 1);
  const big = heads.find(l => /큰/.test(l.name || "")) || heads[0], sm = heads.find(l => /작은/.test(l.name || ""));
  const ratio = big && sm ? big.size / sm.size : 1;
  const hier = hierOf(ratio) * 0.7 + (big && big.runs && big.runs.some(r => r.fill) ? 0.3 : (big && big.fill2 ? 0.25 : sm && sm.fill !== big.fill ? 0.25 : 0.1));
  const occl = clamp(1 - faceHit * 4 - Math.min(1, headHit) * 1.5, 0, 1);
  const cp = clamp(((meta.copy.score || 0) - meta.copyMin) / Math.max(1e-6, meta.copyMax - meta.copyMin), 0, 1);
  const safeM = ui.every(l => inSafe({ short, W: Wd, H: Hd }, extentOf(l), 0)) ? 1 : 0.5;
  const area = hb.reduce((a, b) => a + b.w * b.h, 0) / (Wd * Hd), comp = area >= 0.12 && area <= 0.42 ? 1 : area < 0.12 ? area / 0.12 : clamp(1 - (area - 0.42) * 3, 0, 1);
  let s = 100 * (0.22 * fr + 0.22 * read + 0.14 * hier + 0.14 * occl + 0.12 * cp + 0.08 * safeM + 0.08 * comp);
  // 감점 (판정 1회차 이유: 주인공 작음 61 · 머리·몸 잘림 58 · 누끼 어색 29 · 박힌 글자 9)
  const sh = geo && mainBox(f) ? clipTo(boxC(geo, mainBox(f), ar), { x: 0, y: 0, w: Wd, h: Hd }).h / Hd : 0.5;
  // 판정 4회차: '선수가 개미만'(001_long_5) → 전술 그래픽이 없는 롱폼은 주인공이 0.3H 아래면 게이트, 0.4H 아래는 감점
  if (bg && mainBox(f) && f.kind !== "close" && !tac.length && sh < (short ? 0.18 : 0.3)) gates.push(`주인공이 작음 (${Math.round(sh * 100)}%H)`);
  // 판정 q5 1회차(D-095): 주인공 키는 이어지는 값으로 (쇼츠 0.6H 넘는 장 scene 5.3 · 0.3H 아래 3.7 — 예전엔 계단 감점만) · 화면 안 경쟁자 · 주인공 흔들림 · 글자가 몸을 가림
  const subj = bg && mainBox(f) && f.kind !== "close";
  if (subj) s += short ? clamp((sh - 0.4) * 30, -9, 5) : clamp((sh - 0.45) * 16, tac.length ? -3 : -6, 3);
  const cv = subj ? canvasPeople(doc, f, geo, ar) : null;
  if (cv) {
    s -= Math.min(short ? 10 : 6, cv.rivals * (short ? 4 : 2));
    if (cv.bodyCov > 0.12) s -= Math.min(10, (cv.bodyCov - 0.12) * 30);  // 쌈바형 아래 제목이 다리·몸을 가림 (13장 · '가림/겹침' 59번)
    if (cv.legCov > 0.3) s -= 3;
  }
  if (bg && mainBox(f) && (f.blur ?? 0) > 0.15) s -= Math.min(8, ((f.blur ?? 0) - 0.15) * 25);
  const cut = doc.layers.find(l => l.name === "누끼" && !l.hidden);
  if (cut && meta.cutQ != null && meta.cutQ < 0.7) s -= 10;
  if (cut && bg && ghostOf({ W: Wd, H: Hd, ar, frame: f }, bg, cut) > GHOST_MAX) s -= 8;  // 검토(D-119): 따로 옮긴 누끼 뒤로 배경의 같은 주인공이 크게 보임 ('뒤에 겹친 흐린 같은 사람')
  const bgx = doc.layers.find(l => l.type === "image" && l.name === "배경");  // 이 장면이 그대로 깔린 배경 (코치+경기 장면 합성은 다른 장면이라 뺌)
  // 검토(D-119): 위 띠(레터박스 검은 띠·흰 띠) 아래 장면 칸이 띠 위로 올라와 제목 절반이 사진 위 ('수비 전환이' 005_short_5 · 262px)
  const band = doc.layers.find(l => !l.hidden && l.type === "shape" && /띠/.test(l.name || "") && (l.opacity ?? 1) >= 0.9 && l.y <= 1 && l.w >= Wd - 2);
  if (band && bgx && (bgx.blur || 0) < 6 && bgx.y < band.y + band.h - 2) gates.push("장면이 띠 위로 올라옴");
  if (bgx && (f.text || 0) >= TEXTY) {
    const vt = f.tboxes ? visibleText(bgx, f, ar, Wd, Hd) : f.text;
    if (vt > 0.008) s -= (bgx.blur || 0) >= Wd * 0.025 ? 4 : (bgx.blur || 0) > 0 ? 12 : 25;
  }
  // 공이 화면에 보이고 글자에 안 가림 (판정 252장: 공이 보이는 장면 6.11 vs 5.66 — 가장 큰 차이) · 공 다루는 순간
  if (f.ball && geo) { const bb = boxC(geo.name === "누끼" && bg ? bg : geo, f.ball, ar), cx = bb.x + bb.w / 2, cy = bb.y + bb.h / 2;
    if (cx > 0 && cx < Wd && cy > 0 && cy < Hd && !cover.some(c => cx > c.x && cx < c.x + c.w && cy > c.y && cy < c.y + c.h)) s += 6; }
  if ((f.flags || []).includes("lesson")) s += 3;
  if (!mainBox(f) && !(f.faces || []).length) s -= 15;  // 사람도 얼굴도 없는 장면 (D-113: 주인공 감점을 하나도 안 받아 위로 올라옴)
  // 판정 2회차 감점: 원본을 많이 키움(흐림 72/84) · 흐린 사람 누끼 · 제목 큰 줄이 좁음(구석에 작게 · 28)
  const sharpBg = doc.layers.find(l => l.type === "image" && (l.name === "배경" || l.name === "경기 장면 배경")), bgu = sharpBg ? upOf(sharpBg, ar) : 1;
  const fullBleed = short && !!bgx && bgx.x <= 1 && bgx.y <= 1 && bgx.x + bgx.w >= Wd - 1 && bgx.y + bgx.h >= Hd - 1 && !doc.layers.some(l => l.type === "image" && /장면 [23]|흐린 배경/.test(l.name || ""));
  if (bgu > (fullBleed ? SHORT_ZOOM_CAP : srcCap()) + 0.02) s -= 8;  // 확대 상한 밖 (꽉 찬 쇼츠는 SHORT_ZOOM_CAP · D-096)
  if (cut && (f.blur ?? 0) > 0.2) s -= 10;
  const bigL = heads.find(l => /큰/.test(l.name || "")) || heads[0];
  if (bigL && bbox([bigL]).w < Wd * 0.45) s -= 6;
  if (hpx < 15) s -= 8;  // 목록 크기에서 큰 줄이 15px 안 됨 (판정: '제목이 작아 구석에 몰림')
  if (meta.legacy) s -= 15;  // 예전 템플릿은 레퍼런스형보다 한 단계 아래 (새 템플릿이 안 맞을 때만 나오게)
  s += archetype(doc, heads, short, fullBleed, meta.canFull !== false);  // 판정 5회차(D-090): 레퍼런스 구도 — 가장자리 넓은 제목 · 꽉 찬 쇼츠 · 상자·기울임·반쪽 어둡게·여러 칸 감점
  // 판정 5회차(D-091): 다른 사람 머리를 글자가 덮음 (주인공 말고 키 0.15H 넘는 사람) · 머리 위 여백 · 발목·정강이에서 잘린 몸
  if (bg && f) {
    const view = clipTo({ x: bg.x, y: bg.y, w: bg.w, h: bg.h }, { x: 0, y: 0, w: Wd, h: Hd });
    let other = 0;
    for (const hd of headsOf(f)) {
      if (hd.main) continue;
      const b = clipTo(boxC(geo || bg, hd.box, ar), view), a = areaOf(b); if (!a) continue;
      if (cover.reduce((t, c) => t + interArea(c, b) / a, 0) > 0.1) other++;
    }
    s -= Math.min(10, other * 5);
    if (hbx && geo === bg && f.kind !== "close") {
      const b = boxC(bg, hbx, ar), top = b.y - view.y;
      // 판정 5회차(D-091): 쇼츠 칸 머리 위 중앙값 0.149 (레퍼런스 0.395) — 쇼츠는 머리가 위 끝에 붙으면(1.5%H) 빼고, 4%H 아래는 크게 깎음
      // (가로 원본을 9:16 에 덮으면 위아래로 못 옮겨 4%H 게이트로는 004·003 쇼츠가 장면 2곳만 남음)
      if (short && top < Hd * 0.015) gates.push("머리 위 여백 없음");
      else if (top >= 0 && top < Hd * (short ? 0.04 : 0.03)) s -= short ? 6 : 5; else if (top >= 0 && top < Hd * 0.06) s -= 2;
    }
    if (mb && geo === bg && f.kind !== "close" && mb[1] + mb[3] < 0.985 && mb[3] < 0.9) {
      const b = boxC(bg, mb, ar), vis = clipTo(b, view).h / Math.max(1, b.h);
      if (b.y + b.h > view.y + view.h + 2 && vis >= 0.6 && vis < 0.97) s -= 4;  // 발이 원본엔 있는데 캔버스에서 발목·정강이가 잘림
    }
  }
  s += meta.prior || 0;     // 템플릿 가산점: 클로드 블라인드 판정(롱폼·쇼츠 252장) 템플릿 평균으로 맞춤 — 평균 6.3 → +4 … 5.1 → −5
  if (tac.length) s += 2;   // 전술 그래픽 (쪼살형) 가산
  // 판정 3회차 회귀 (76장 · scoreDoc↔클로드 상관 0.17): 가장 큰 요인은 문구 — 상투 꼬리표(상관 −0.33) · 대사의 구체 낱말 · 살아 있는 표정(+0.23) · 그래픽 요소 과다
  const cpy = meta.copy || {};
  // 판정 5회차(D-094): 상투 꼬리표 −10 은 쪼살 자신의 인기 문구('실력이 늘어요' 판정 7점)까지 막음 → 묶음에 1개(굳은 규칙)로 두고 감점은 작게 · 라벨 문구 가산(+4)은 뺌
  if (cpy.stock) s -= 2; else if (cpy.vague) s -= 1;
  // 판정 q5 1회차(D-111): 서 있는 인물 사진 위 '슈팅'·웃는 인물 위 '수비 전환' — 기술 이야기 문구인데 장면에 공도 공 다루는 순간도 없으면 깎음
  if (ACTION_COPY.test(`${cpy.l1 || ""} ${cpy.l2 || ""}`) && !f.ball && !(f.flags || []).includes("lesson") && f.kind !== "wide") s -= 3;
  if (HERE_COPY.test(`${cpy.l1 || ""} ${cpy.l2 || ""}`) && !ballSure(f) && !tac.length) s -= 3;  // 검토(D-119): '여기' 문구는 공이 보이는 장면에 먼저 (없으면 발 쪽 동그라미로 대신)
  if (cpy.src === "ai") s += 2;
  if (f.emo && (f.emo.happiness || 0) + (f.emo.surprise || 0) >= 0.4 && (f.text || 0) < TEXTY) s += 3;
  if (tac.length >= 3) s -= 4;  // 한 장에 링·화살표·칩·점선이 다 들어가면 '붙여 넣은 티'
  if (doc.layers.filter(l => !l.hidden && /스티커|배지|손그림/.test(l.name || "")).length >= 2) s -= 3;
  const raw = s;            // 고르기는 100 을 넘는 차이도 씀 (예전엔 많은 후보가 100 에 붙어 구별이 안 됐음)
  s = shownScore(s);        // 판정 4회차: 100 에서 자르면 76장 중 23장이 100점 동점 → 85 위는 부드럽게 눌러 순서를 남김
  if (gates.length) s = Math.min(s, 40);
  // 이유 (카드에 2개)
  const ps = (f.persons || []).length;
  if (read >= 0.9) why.push("작게 봐도 잘 읽혀요"); else if (read >= 0.6) why.push("휴대폰에서도 읽혀요");
  if (f.kind === "close" && f.emo && ((f.emo.happiness || 0) + (f.emo.surprise || 0)) >= 0.4) why.push("표정이 살아 있어요");
  if (f.ai >= 7) why.push("클로드가 고른 장면");
  if ((f.flags || []).includes("lesson")) why.push("공 다루는 순간"); else if (ps >= 2) why.push(`선수 ${ps}명 액션`);
  if (f.ball) why.push("공이 보여요");
  if (tac.length) why.push("전술 그래픽");
  if (doc.layers.some(l => l.name === "경기 장면 배경")) why.push("코치 얼굴 + 경기 장면");
  if (meta.copy.src === "ai") why.push("클로드 문구");
  if (hier >= 0.85) why.push("한눈에 들어오는 강조");
  return { score: Math.round(s * 10) / 10, raw: gates.length ? s : raw, gates, why: why.slice(0, 2), weak, hpx, bigH, blurFill: bf };
}
function preciseContrast(doc, weak) {  // 획이 약한 제목만: 제목을 뺀 배경 밝기와 글자색 대비 (WCAG 4.5)
  let worst = 99;
  for (const l of weak) {
    const bgL = regionLumDoc(doc, l), fl = lum(l.fill), r = (Math.max(bgL, fl) + 0.05) / (Math.min(bgL, fl) + 0.05);
    worst = Math.min(worst, r);
  }
  return worst;
}
function regionLumDoc(doc, l) {
  const k = 0.25, c = newCanvas(doc.w * k, doc.h * k), g = c.getContext("2d");
  renderDoc(g, Object.assign({}, doc, { layers: doc.layers.filter(x => x !== l && x.type !== "text") }), k);
  const b = bbox([l]), x = clamp(Math.floor(b.x * k), 0, c.width - 1), y = clamp(Math.floor(b.y * k), 0, c.height - 1);
  const d = g.getImageData(x, y, clamp(Math.ceil(b.w * k), 1, c.width - x), clamp(Math.ceil(b.h * k), 1, c.height - y)).data; let s = 0, n = 0;
  for (let i = 0; i < d.length; i += 16) { s += lum("#" + [d[i], d[i + 1], d[i + 2]].map(v => v.toString(16).padStart(2, "0")).join("")); n++; }
  return n ? s / n : 0;
}

/* ----- 후보 만들기 → 점수 → 다양하게 고르기 (MMR λ=0.7 · 템플릿 ≥4 · 장면 ≥3 · 문구 ≥3 · 같은 둘째 줄 ≤2) ----- */
function sameFace(a, b) {  // 같은 인터뷰 화면의 같은 사람 (얼굴 자리·크기가 거의 같음) — 판정 3회차: 005 쇼츠 6장 모두 같은 코치 얼굴
  const p = mainFace(a), q = mainFace(b); if (!p || !q) return false;
  const i = interArea({ x: p[0], y: p[1], w: p[2], h: p[3] }, { x: q[0], y: q[1], w: q[2], h: q[3] }), u = p[2] * p[3] + q[2] * q[3] - i;
  return u > 0 && i / u >= 0.3;
}
function sceneGroups(frames) {  // 같은 화면(지문 ≤ 10) · 같은 사람 클로즈업(얼굴 장면끼리 지문 ≤ 20) → 장면 묶음 번호
  const g = {};
  for (const f of frames) {
    const same = frames.find(h => h !== f && g[h.t] !== undefined && (hamming(h.hash, f.hash) <= 10 || (h.kind === "close" && f.kind === "close" && (hamming(h.hash, f.hash) <= 20 || sameFace(h, f)))));
    g[f.t] = same ? g[same.t] : f.t;
  }
  return g;
}
// 쓸 수 있는 장면: 박힌 글자가 크면 주인공 누끼(품질 통과)가 있어야 새 템플릿이 씀 (없으면 흐린 배경만 남아 후보가 0개)
const usableFrame = f => (f.text || 0) < TEXTY || !!(AI.cuts[String(f.t)] && (!AI.cuts[String(f.t)].q || AI.cuts[String(f.t)].q.ok));
function aiFrames(n = 6, fmt = "long") {
  const maxS = Math.max(1e-6, ...AI.frames.map(f => f.score || 0));
  const fq = f => frameQ(f, maxS) * subjQ(f, fmt) * (usableFrame(f) ? 1 : 0.15);  // 형식마다 주인공이 크고 홀로 보이는 장면 먼저 (D-095)  // 못 쓰는 장면(후보 0개)은 뒤로 — 클로드가 모든 장면을 낮게 줘도 (글자 박힌 영상) 누끼 있는 장면을 남김
  let fs = [...AI.frames].sort((a, b) => fq(b) - fq(a));
  if (AI.pick != null) { const f = fs.find(x => x.t === AI.pick); return f ? [f] : fs.slice(0, 1); }
  const sharp = fs.filter(f => (f.blur ?? 0) <= 0.45); if (sharp.length >= 2) fs = sharp;  // 판정 4회차: 흔들린 장면은 2장만 남아도 뺌
  const crispMax = fmt === "short" ? 0.3 : 0.35;  // 판정 q5 1회차(D-095): 쇼츠는 0.3 넘으면 '흐림' 지적이 대부분 — 롱폼(400px 목록)은 0.35 까지 (004 롱폼 40.3초 0.318 이 pro 5.0~5.3)
  const crisp = fs.filter(f => (f.blur ?? 0) <= crispMax); if (crisp.length >= 4) fs = crisp;  // 또렷한 장면이 4장 넘으면 흔들린 장면은 뺌
  // 클로드가 '주제와 무관'(앵커·잡지·로고 1~3점)이라 한 장면은 좋은 장면(5점 넘게)이 3장 넘으면 뺌 — 다양성 채우기로 끌려 들어오지 않게 (판정 9회차)
  const bestAi = Math.max(-1, ...fs.map(f => f.ai ?? -1));
  if (bestAi >= 5) { const keep = fs.filter(f => f.ai == null || f.ai > 3 || bestAi - f.ai < 3); if (keep.length >= 3) fs = keep; }
  // 판정 5회차(D-091): 분홍·초록 낙서 벽처럼 색이 요란한 장면(colorfulness > 85, 얼굴 클로즈업 말고)은 다른 장면이 3장 넘게 있으면 뺌 (다리만 나온 16장 중 9장이 이 벽)
  { const calm = fs.filter(f => !loud(f) && usableFrame(f)); if (calm.length >= 3) fs = fs.filter(f => !loud(f)); }
  // 판정 q5 2회차(D-113): 사람이 없는 장면(빈 경기장 원경)은 주인공 감점을 하나도 안 받아 앱 점수 88~90 으로 들어옴(판정 pro 3.0 · scene 1.5) →
  // 사람이 나오는 장면이 3장 넘으면 쓰지 않음 (모자라면 예전처럼 1장까지)
  const withP = fs.filter(f => (f.persons || []).length || (f.faces || []).length);
  if (withP.length) fs = withP.concat(withP.length >= 3 ? [] : fs.filter(f => !withP.includes(f)).slice(0, 1));
  const top = fs.length ? fq(fs[0]) : 1;
  const strongOther = fs.filter(f => f.kind !== "close" && fq(f) >= 0.6 * top).length;
  const closeCap = strongOther >= 3 ? 2 : 4;  // 인터뷰 얼굴만 6장 나오지 않게 (다른 좋은 장면이 있으면 2장까지)
  const grp = sceneGroups(fs), out = [];
  const add = (f, perGroup) => {
    if (out.includes(f) || out.length >= n) return;
    if (f.kind === "close" && out.filter(x => x.kind === "close").length >= closeCap) return;
    if (out.filter(x => grp[x.t] === grp[f.t]).length >= perGroup) return;
    out.push(f);
  };
  for (const k of ["mid", "wide", "close"]) { const f = fs.find(x => x.kind === k); if (f && fq(f) >= 0.5 * top) add(f, 1); }  // 종류마다 하나씩 (너무 나쁜 장면은 빼고)
  const ex = fs.find(f => isExpr(f) && !headlessBad(f)); if (ex) add(ex, 1);  // 살아 있는 표정 장면 하나는 꼭 (판정 2회차: 표정 장면이 있는데 안 쓴 묶음 7개)
  for (const f of fs) add(f, 1);
  for (const f of fs) add(f, 2);
  for (const f of fs) if (out.length < n && !out.includes(f)) out.push(f);
  return out.slice(0, n).sort((a, b) => fq(b) - fq(a));
}
const WIDE_MIN = 80;  // 6개를 채우려고 넓혀 다시 고를 때 모든 장이 이 점수 넘게일 때만 그 결과를 씀 (D-118)
const SCORE_FLOOR = 62;  // 판정 4회차: 58.8·61.5 점이 6개를 채우려고 들어옴 → 이 밑은 내지 않음 (적게 냄)
// 둘째 줄이 같은지 보는 열쇠: 띄어쓰기·'핵심은 ' 머리·문장 부호를 뺌 ('속도' = '핵심은 속도')
const l2Key = c => (c.l2 || c.l1 || "").replace(/^핵심은\s*/, "").replace(/[\s!?.~]/g, "");
// 한 줄 글자 수 (띄어쓰기 포함) 상한 — 판정 2회차 B5: 쇼츠 9자 줄 6장('퍼스트 터치 어려우면')·롱폼 11자 1장
const LINE_MAX_FMT = { short: 8, long: 10 };  // 판정 5회차: 롱폼 10자 (가장 넓은 줄 0.93W 에서 큰 줄 하한 0.165H 안)
function copyFits(c, fmt) { const m = LINE_MAX_FMT[fmt] || 9; return [c.l1, c.l2].every(t => (t || "").length <= m); }
function rebalance(c, fmt) {  // 두 줄 낱말을 다시 나눠 줄 길이 안으로 (두 줄 길이가 가장 비슷하게 · 강조 낱말은 다시 찾음) → 새 문구 또는 null
  const words = `${c.l1 || ""} ${c.l2 || ""}`.trim().split(/\s+/), m = LINE_MAX_FMT[fmt] || 9, et = c.emph ? [c.l1, c.l2][c.emph[0]].slice(c.emph[1], c.emph[2]) : "";
  let best = null;
  for (let i = 1; i < words.length; i++) {
    const a = words.slice(0, i).join(" "), b = words.slice(i).join(" ");
    if (a.length > m || b.length > m) continue;
    const cutsTopic = (AI.topics || []).concat(et ? [et] : []).some(t => t && t.includes(" ") && `${a} ${b}`.includes(t) && !a.includes(t) && !b.includes(t));
    if (cutsTopic || words[i - 1].length === 1) continue;
    if (words.some((w, j) => /[?!]$/.test(w) && j !== i - 1 && j !== words.length - 1)) continue;  // 물음표·느낌표는 줄 끝에만 ('1대1 돌파 / 막히면? 속도' 금지)  // 주제·강조 낱말('퍼스트 터치')을 두 줄로 쪼개지 않음 · 한 글자 낱말('이')이 줄 끝에 홀로 남지 않게
    const d = Math.abs(a.length - b.length);
    if (!best || d < best.d) best = { a, b, d };
  }
  if (!best) return null;
  const ln = et && best.b.includes(et) ? 1 : et && best.a.includes(et) ? 0 : -1, t = ln === 1 ? best.b : best.a;
  return Object.assign({}, c, { l1: best.a, l2: best.b, emph: ln >= 0 ? [ln, t.indexOf(et), t.indexOf(et) + et.length] : null, split: true });
}
function aiCopies(n = 5, seed = 0, fmt = "long") {
  if (AI.copySel) return [AI.copySel];
  const cs = [...AI.copy].sort((a, b) => b.score - a.score).map(c => (copyFits(c, fmt) ? c : rebalance(c, fmt))).filter(Boolean);
  // 구체적인 약속(숫자·결과·대상) 먼저 · 막연한 문구('무조건 봐'·'~의 비밀'·'이렇게 하세요')는 2개까지 — 판정 2회차: 문구 이유 44/84 '구체적 약속이 없음'
  // 판정 3회차: 상투 꼬리표('이 순서대로!'·'플랩 레벨업')도 막연한 문구로 셈 · 같은 둘째 줄은 한 번만 (005 쇼츠 '수비 전환 / 이 순서대로!' 3번)
  // 판정 5회차(D-094): 구체 낱말 라벨을 앞세우던 순서를 뺌 — 점수 순(훅 문장·클로드 문구가 위) · 막연·상투 문구는 1개만 앞쪽에
  const vag = cs.filter(c => c.vague), rest = cs.filter(c => !c.vague);
  const mix0 = [...rest.slice(0, 7), ...vag.slice(0, 1), ...rest.slice(7), ...vag.slice(1, 3)], seenL2 = new Set();
  const mixd = mix0.filter(c => { const k = l2Key(c); if (seenL2.has(k)) return false; seenL2.add(k); return true; });
  if (!seed) return mixd.slice(0, n);
  const k = (seed * 2) % Math.max(1, mixd.length);  // 다시 추천: 다른 문구가 앞에 오게 돌림
  return [...mixd.slice(k), ...mixd.slice(0, k)].slice(0, n);
}
const isExpr = f => !!f && !!f.emo && (f.emo.happiness || 0) + (f.emo.surprise || 0) >= 0.4 && (f.text || 0) < TEXTY && !!mainFace(f) && mainFace(f)[3] >= 0.1 && (f.blur ?? 0) <= 0.3;
// 쓸 만한 장면이 없는 영상 (판정 2회차 THUMBTEST01: 클로드가 후보 12장 모두 1~4점 · 다른 썸네일·큰 자막이 박힌 화면) → 억지로 6개를 채우지 않음
function weakScenes() {
  if (AI.pick != null || !AI.frames.length) return false;
  const rated = AI.frames.filter(f => f.ai != null), maxS = Math.max(1e-6, ...AI.frames.map(f => f.score || 0));
  if (rated.length >= 3 && rated.length >= AI.frames.length * 0.6 && Math.max(...rated.map(f => f.ai)) <= 4) return true;
  // 검토(D-119): 클로드를 끈 PC 에서는 THUMBTEST01(장면마다 다른 썸네일 글자)이 '쓸 만한 장면 없음'으로 잡히지 않아 6개를 억지로 찾다 0개 → 모든 장면에 큰 글자가 박혔으면 약한 영상
  if (AI.frames.every(f => (f.text || 0) >= TEXTY) && !AI.frames.some(f => (f.ai ?? 0) >= 5)) return true;
  return Math.max(...AI.frames.map(f => frameQ(f, maxS))) < 0.35;
}
// 스타일 버릇 가산점 (D-130): 제목 위치(롱폼만 · 레퍼런스 썸네일이 16:9 라서) · 누끼 · 큰 줄 수 · 얼굴 크기 — 템플릿 가산점(prior)과 같은 단위(점)
const ST_W = { pos: 15, cut: 8, lines: 3, face: 6 };
function styleBonus(doc, ctx) {
  const sp = ctx.brand && ctx.brand._st; if (!sp) return 0;
  const hs = doc.layers.filter(l => !l.hidden && l.type === "text" && /^제목/.test(l.name || "")), H = doc.h;
  let b = 0;
  if (sp.posW && hs.length && !ctx.short) { const bb = bbox(hs), cy = (bb.y + bb.h / 2) / H; b += ST_W.pos * ((sp.posW[cy < 0.42 ? "top" : cy > 0.58 ? "bottom" : "middle"] || 0) - 1 / 3); }
  if (sp.cut != null && doc.layers.some(l => l.name === "누끼" && !l.hidden)) b += ST_W.cut * (sp.cut - 0.3);
  if (sp.lines && hs.length) { const mx = Math.max(...hs.map(l => l.size || 0)), big = hs.filter(l => (l.size || 0) >= 0.6 * mx).length; b += big === sp.lines ? ST_W.lines : -ST_W.lines / 2; }
  const fc = mainFace(ctx.frame), bg = doc.layers.find(l => l.type === "image" && l.name === "배경");
  if (sp.face && fc && bg) { const fh = boxC(bg, fc, ctx.ar).h / H; if (fh > 0) b += ST_W.face * (0.5 - Math.min(1, Math.abs(Math.log(fh / sp.face)))); }
  return b;
}
// 우리 채널 A/B 이긴 틀·문구 틀 가산점 (YouTube '테스트 및 비교' 승자를 한 번 눌러 적어 둔 것 · thumbstyle.ours) — 이긴 횟수만큼, 둘 다 상한
const AB_W = { tpl: 4, tplMax: 8, pid: 3, pidMax: 6 };
function abBonus(tpl, copy) {
  const o = TS.view && TS.view.ours; if (!o || !o.n) return 0;
  return Math.min(AB_W.tplMax, AB_W.tpl * ((o.tpl || {})[tpl] || 0)) + (copy && copy.pid ? Math.min(AB_W.pidMax, AB_W.pid * ((o.pid || {})[copy.pid] || 0)) : 0);
}
function recommend(fmt, n = 6, seed = 0, avoidKeys = new Set(), wide = false) {
  const weak = weakScenes(); AI.weak = weak;
  if (weak) n = Math.min(n, 2);
  // wide: 6개를 못 채운 묶음만 장면 8 × 문구 14 로 한 번 더 (판정 q5 2회차 D-118: 18묶음 중 8묶음이 4~5개 — 남은 후보가 대부분 '같은 둘째 줄'·같은 장면에 걸림)
  const frames = aiFrames(wide ? 8 : 7, fmt), copies = aiCopies(wide ? 14 : 9, seed, fmt), cands = [];  // 판정 4회차: 문구 9개 — 같은 둘째 줄·같은 질문 머리 1번 규칙을 지키고도 6개를 채우게  // 장면 7 × 문구 7 — 6개가 서로 다른 장면·문구가 되게 (판정: 'A와 같은 장면·문구' 21/84)
  const maxFrame = Math.max(1e-6, ...AI.frames.map(f => f.score || 0)), cScores = AI.copy.map(c => c.score || 0);
  const copyMin = Math.min(...cScores, 0), copyMax = Math.max(...cScores, 1);
  const build = tpls => { for (const f of frames) for (const c of copies) for (const [name, t] of Object.entries(tpls)) {
    if (t.legacy && (f.text || 0) >= TEXTY) continue;  // 예전 템플릿은 박힌 글자를 피하지 못함
    const cx = ctxFor(f, c, fmt, seed + Math.round(f.t * 10)); cx.relax = weak; if (!fits(t, cx)) continue;
    let doc; try { doc = buildDoc(NAME, t, cx); } catch (e) { console.warn("템플릿 실패", name, e); continue; }
    if (!doc.layers.length) continue;  // 템플릿이 이 장면·문구를 안 쓰기로 함 (머리가 제목 자리에 있음 등)
    if (t.needs && t.needs.tactics && !doc.layers.some(l => l.shape === "arrow2")) continue;  // 전술 템플릿인데 화살표 놓을 자리가 없으면 다른 템플릿에 양보
    if (t.needs && t.needs.cut && !doc.layers.some(l => l.name === "누끼")) continue;  // 누끼 템플릿인데 주인공이 작아 누끼를 안 넣었으면 (흐린 배경만 남음) 양보
    if (t.minBigW) { const bl = doc.layers.find(l => l.name === "제목 큰 줄"); if (bl && bbox([bl]).w < cx.W * t.minBigW) continue; }  // 옆 제목형: 큰 줄이 좁으면 (구석에 작게 · 판정 5.49) 탈락
    if (doc._texty && !t.legacy) continue;  // 박힌 글자가 보이는데 주인공 누끼가 없음 → 흐리기만 하면 주인공도 흐려짐 (판정 최저 3.4~4.5점)
    delete doc._texty;
    const cq = AI.cuts[String(f.t)] && AI.cuts[String(f.t)].q ? AI.cuts[String(f.t)].q.q : null;
    // 꽉 찬 쇼츠를 못 만드는 원본(720p 가로)에서는 띠·여러 칸이 남은 길 → 예전 가산점만큼 돌려줌 (판정 5회차: 꽉 찬 장면이 될 때만 낮춤)
    // 판정 q5 1회차(D-096): 여러 칸('두 장면' pro 3.6 · crop 2.87 · '이어 붙인 티')은 돌려주지 않음 — 띠형만
    const canFull = fmt === "short" && fullOk(cx), fallback = fmt === "short" && !canFull && t.band;
    const meta = { frame: f, copy: c, legacy: !!t.legacy, prior: (t.prior || 0) + (fallback ? 4 : 0) + styleBonus(doc, cx) + abBonus(name, c), maxFrame, copyMin, copyMax, cutQ: cq, canFull }, sc = scoreDoc(doc, meta);
    const key = `${name}|${f.t}|${c.l1}/${c.l2}`;
    cands.push(Object.assign({ tpl: name, t: f.t, copy: c, doc, key, legacy: !!t.legacy, ctx: cx, band: !!t.band, once: !!t.once, close: f.kind === "close", expr: isExpr(f),
      tac: doc.layers.some(l => l.shape === "arrow2"), vague: !!c.vague, stock: !!c.stock, concrete: !!c.concrete, foot: srcHeadCut(f) }, sc, { base: sc.raw - (avoidKeys.has(key) ? 25 : 0) }));
  } };
  build(T_NEW[fmt]);
  const ok0 = cands.filter(x => !x.gates.length);  // 예전 템플릿은 새 템플릿만으로 모자랄 때만 만듦 (다시 추천 4초 안 — 판정 B8)
  // 판정 4회차: 세로 원본(006)으로 만든 롱폼에 예전 사선·기울인 카드 템플릿(4.9점)이 섞임 → 세로 원본은 예전 템플릿을 쓰지 않음
  const vertical = frameAspect() < 0.9 && fmt === "long";
  if (!vertical && (ok0.length < n || new Set(ok0.map(x => x.tpl)).size < 4 || new Set(ok0.map(x => x.t)).size < Math.min(3, frames.length))) build(legacyTpl(fmt));  // 판정 3회차: 템플릿 2~3종 묶음 3개 → 4종이 안 되면 예전 템플릿도
  cands.sort((a, b) => b.base - a.base);
  for (const x of cands.slice(0, 20)) if (x.weak.length && !x.gates.length) { const r = preciseContrast(x.doc, x.weak); if (r < 4.5) { x.gates.push(`대비 ${r.toFixed(1)}:1`); x.score = Math.min(x.score, 40); x.base = Math.min(x.base, 40); } }
  // 게이트 통과한 것만 · 새(레퍼런스형) 템플릿만으로 6개(3종 이상)를 채울 수 있으면 예전 템플릿은 빼고, 모자라면 예전 템플릿으로 채움
  const clean = cands.filter(x => !x.gates.length), fresh = clean.filter(x => !x.legacy);
  const pool = fresh.length >= n && new Set(fresh.map(x => x.tpl)).size >= 4 && new Set(fresh.map(x => x.t)).size >= Math.min(3, frames.length) ? fresh : clean.length >= n ? clean : cands;
  // 장면은 지문(dHash)이 비슷하면 같은 장면으로 봄 (시각만 다른 같은 화면·같은 사람 클로즈업이 여러 번 나오지 않게)
  const scene = sceneGroups(frames);
  for (const x of pool) x.sc = scene[x.t] ?? x.t;
  AI.dbg = { pool: pool.length, byTpl: Object.fromEntries([...new Set(cands.map(x => x.tpl))].map(t => [t, [cands.filter(x => x.tpl === t && !x.gates.length).length, cands.filter(x => x.tpl === t).length,
    [...new Set(cands.filter(x => x.tpl === t).flatMap(x => x.gates.map(g => g.replace(/[\d.%() ]+/g, ""))))].slice(0, 4)]])) };  // 개발·판정용: 템플릿별 (게이트 통과 수, 전체, 게이트 종류)
  const l2 = x => l2Key(x.copy);
  AI.dbg.top = pool.slice(0, 40).map(x => [x.tpl, x.t, Math.round(x.score), l2(x)]);  // 개발·판정용: 점수 위 후보 40개
  AI.dbg.scenes = new Set(pool.map(x => x.sc)).size;  // 개발·판정용: 게이트를 넘은 후보의 장면 묶음 수 (흔들린 장면을 뺀 뒤 — e2e '장면 ≥3' 의 기준)
  // 판정 q5 2회차(D-118): 99장 중 32장이 같은 묶음의 다른 장과 똑같은 장면(같은 t) — 'C와 같은 장면을 그대로 다시 써서' → 같은 시각은 같은 장면 묶음보다 조금 더 겹친다고 봄 (굳은 규칙은 아님)
  const sim = (a, b) => (a.tpl === b.tpl ? 0.5 : 0) + (a.sc === b.sc ? 0.3 : 0) + (a.t === b.t ? 0.3 : 0) + (a.copy === b.copy ? 0.2 : l2(a) === l2(b) ? 0.1 : 0);
  const out = [];
  const distinct = (k, xs = pool) => new Set(xs.map(x => (k === "l2" ? l2(x) : x[k]))).size;
  // 같은 종류 상한 = 6 ÷ 종류 수 (올림) — 장면이 둘뿐인 영상은 장면당 3개까지, 문구는 7개면 1번씩
  const capOf = k => Math.max(1, Math.ceil(n / Math.max(1, distinct(k))));
  const tplCap = Math.min(2, capOf("tpl")), scCap = Math.min(capOf("sc"), distinct("sc") >= 3 || new Set(frames.map(f => scene[f.t] ?? f.t)).size >= 3 ? 2 : 3), copyCap = Math.max(capOf("copy"), capOf("l2"));  // 같은 장면 묶음(같은 얼굴 포함) ≤ 2/6 (영상의 장면이 2묶음뿐이면 3 · q5 1회차: 게이트로 후보가 2묶음만 남아도 영상에 3묶음 넘게 있으면 2 — 같은 장면 3번보다 적게 냄)
  // 꼭 채울 장면 종류는 '괜찮은 장면'(가장 좋은 장면 품질의 절반 넘게)만 셈 — 인터뷰 한 사람뿐인 영상에서 나쁜 장면을 억지로 넣지 않게
  const fqT = {}; for (const f of frames) fqT[f.t] = frameQ(f, maxFrame);
  const qTop = Math.max(1e-6, ...Object.values(fqT)), goodSc = new Set(pool.filter(x => (fqT[x.t] ?? 0) >= 0.5 * qTop).map(x => x.sc)).size;
  // 템플릿 종류는 65점 넘는 후보가 있는 것만 셈 — 다양성을 채우려고 아주 낮은 예전 템플릿(29점)을 끌어오지 않게
  const need = { tpl: Math.min(4, distinct("tpl", pool.filter(x => x.score >= 65))), sc: Math.min(4, Math.max(1, goodSc)), copy: Math.min(5, distinct("copy")) };
  // 판정 2회차 몫: 롱폼 전술형 ≥ 1 (선수 3명 장면이 있을 때) · 구체적 약속 문구 ≥ 3 · 살아 있는 표정 장면 ≥ 1
  // 판정 5회차(D-094): '구체적 약속 ≥3' 몫이 라벨 문구('슈팅 / 디딤발 위치')를 억지로 끌어옴 → 1개로
  const quota = { tac: fmt === "long" ? Math.min(1, distinct("sc", pool.filter(x => x.tac))) : 0, concrete: Math.min(1, distinct("copy", pool.filter(x => x.concrete))), expr: Math.min(1, pool.filter(x => x.expr).length) };
  const nonClose = distinct("sc", pool.filter(x => !x.close && (fqT[x.t] ?? 0) >= 0.5 * qTop)), closeCap = nonClose >= 3 ? 2 : nonClose >= 1 ? 3 : n;  // 같은 코치 얼굴 반복 (판정 005: 5/6)
  const n70 = pool.filter(x => x.score >= 70).length;
  const onceHard = pool.filter(x => !x.once && !x.legacy && x.score >= 70).length >= n - 1;  // 다른 템플릿으로 70점 넘게 채울 수 있을 때만 '한 번만'
  const vagueCap = Math.min(2, (distinct("l2", pool.filter(x => !x.vague && !x.stock)) >= 4 ? 1 : 2) + (wide ? 1 : 0));  // 구체적인 문구가 모자라면 같은 문구를 되풀이하느니 막연한 문구 하나 더 (D-118: 6개를 못 채운 묶음은 하나 더 · 많아야 2)
  // 판정 4회차: 아래 규칙은 '굳은 규칙' — 모자라면 덜 겹치는 걸로 채우던 대체 고르기(!best)에서도 절대 넘지 않음 (6개가 안 되면 적게 냄)
  // 쇼츠 띠형(3단·상자·레터박스·흰 띠) ≤ 3 (롱폼 ≤ 2) · 같은 템플릿 ≤ 2 · 같은 둘째 줄('속도'='핵심은 속도') 1번 · 같은 장면 묶음 ≤ scCap ·
  // 같은 질문 머리('왜 막힐까?') 묶음에 1번·롱폼+쇼츠 합쳐 2번 · 막연·상투 문구 ≤ vagueCap · 점수 하한 SCORE_FLOOR · 한 번만 쓰는 템플릿
  const other = (AI.lastQ || {})[fmt === "long" ? "short" : "long"] || [];
  const otherHooks = (AI.lastHook || {})[fmt === "long" ? "short" : "long"] || [];
  const qKey = x => (/[?？]$/.test(x.copy.l1 || "") ? x.copy.l1.replace(/\s+/g, "") : "");
  const hardIn = (x, out) => (x.band && out.filter(o => o.band).length >= (fmt === "short" ? 3 : 2)) || out.filter(o => o.tpl === x.tpl).length >= 2
    || out.some(o => l2(o) === l2(x)) || out.filter(o => o.sc === x.sc).length >= scCap || x.score < SCORE_FLOOR
    || (qKey(x) && (out.some(o => qKey(o) === qKey(x)) || other.filter(q => q === qKey(x)).length + 1 > 2))
    || ((x.vague || x.stock) && out.filter(o => o.vague || o.stock).length >= vagueCap) || (x.once && onceHard && out.some(o => o.tpl === x.tpl))
    || out.some(o => o.tpl === x.tpl && o.sc === x.sc) || (x.foot && out.some(o => o.foot));  // 발·공 클로즈업(머리 없음)은 묶음에 1장 (판정 5회차)
  const hard = x => hardIn(x, out);
  const blocked = x => hard(x) || (x.score < 70 && n70 >= n) || (x.close && out.filter(o => o.close).length >= closeCap);
  while (out.length < n && out.length < pool.length) {
    const left = n - out.length, have = k => new Set(out.map(x => x[k])).size, owe = k => quota[k] - out.filter(o => o[k]).length;
    let best = null, bv = -1e9;
    for (const x of pool) {
      if (out.includes(x) || blocked(x)) continue;
      // 다양성 강제: 남은 자리로 채워야 할 종류가 있으면 새 종류만
      if ((["tpl", "sc", "copy"]).some(k => need[k] - have(k) >= left && out.some(o => o[k] === x[k]))) continue;
      if (Object.keys(quota).some(k => owe(k) >= left && !x[k])) continue;  // 남은 자리가 몫만큼이면 몫을 채우는 것만
      if (out.filter(o => o.tpl === x.tpl).length >= tplCap || out.filter(o => o.copy === x.copy).length >= copyCap) continue;
      const bonus = Object.keys(quota).reduce((a, k) => a + (owe(k) > 0 && x[k] ? 6 : 0), 0) - (otherHooks.includes(x.copy.pid) ? 2 : 0);  // 판정 q5 1회차(D-111): 롱폼·쇼츠가 같은 훅 틀이면 조금 깎음 (크게 깎으면 약한 문구가 끼어 appeal 이 내려감)
      const v = 0.7 * (x.base + bonus) - 0.3 * 100 * Math.max(0, ...out.map(o => sim(o, x)));
      if (v > bv) { bv = v; best = x; }
    }
    if (!best) {  // 무른 조건(다양성 강제·몫·70점·얼굴 수·템플릿 1번)만 풀고 굳은 규칙은 그대로 — 덜 겹치는 것
      const dup = x => out.filter(o => o.copy === x.copy).length * 1.5 + out.filter(o => o.tpl === x.tpl).length * 1.5 + (x.close && out.filter(o => o.close).length >= closeCap ? 1.5 : 0);
      for (const x of pool) { if (out.includes(x) || hard(x)) continue; const v = x.base - 40 * dup(x); if (!best || v > bv) { bv = v; best = x; } }
    }
    if (!best) break;
    out.push(best);
  }
  // 판정 q5 1회차: 앞에서부터 욕심내어 고르면 새 템플릿이 남은 장면(같은 장면 ≤2)에 묶여 4종을 못 채움 (ASR001 롱폼 3종) →
  // 템플릿 종류가 모자라면 두 번 나온 템플릿 하나를 굳은 규칙을 지키는 새 종류로 바꿔 봄 (점수 높은 새 종류부터 · 바꿀 것은 점수 낮은 것부터)
  for (let k = 0; k < 3 && new Set(out.map(x => x.tpl)).size < need.tpl; k++) {
    const kinds = new Set(out.map(x => x.tpl)); let swapped = false;
    for (const c of pool.filter(x => !out.includes(x) && !kinds.has(x.tpl)).sort((a, b) => b.base - a.base)) {
      if (out.length < n && !hard(c)) { out.push(c); swapped = true; break; }
      for (const o of [...out].sort((a, b) => a.base - b.base)) {
        if (out.filter(z => z.tpl === o.tpl).length < 2) continue;
        const rest = out.filter(z => z !== o);
        if (!hardIn(c, rest)) { out[out.indexOf(o)] = c; swapped = true; break; }
      }
      if (swapped) break;
    }
    if (!swapped) break;
  }
  if (out.length < n) {  // 개발·판정용: 6개를 못 채운 까닭 (남은 후보마다 걸린 굳은 규칙) — 넓혀 다시 고를지도 이것으로 정함
    const why = {}, R = { band: x => x.band && out.filter(o => o.band).length >= (fmt === "short" ? 3 : 2), tpl: x => out.filter(o => o.tpl === x.tpl).length >= 2, l2: x => out.some(o => l2(o) === l2(x)),
      scene: x => out.filter(o => o.sc === x.sc).length >= scCap, floor: x => x.score < SCORE_FLOOR, q: x => !!qKey(x) && (out.some(o => qKey(o) === qKey(x)) || other.filter(q => q === qKey(x)).length + 1 > 2),
      vague: x => (x.vague || x.stock) && out.filter(o => o.vague || o.stock).length >= vagueCap, tplScene: x => out.some(o => o.tpl === x.tpl && o.sc === x.sc) };
    for (const x of pool) if (!out.includes(x)) for (const [k, f] of Object.entries(R)) if (f(x)) why[k] = (why[k] || 0) + 1;
    AI.dbg.short = { left: pool.length - out.length, why };
  }
  if (out.length < n && !wide && !weak && !AI.copySel && AI.pick == null && wideWorth(AI.dbg.short, AI.copy.length > copies.length, AI.frames.length > frames.length)) {
    const dbg0 = AI.dbg, more = recommend(fmt, n, seed, avoidKeys, true);
    if (wideOk(more, out)) { AI.dbg.wide = true; return more; }  // 채우려고 약한 장(판정 pro 3.0)을 넣지는 않음
    AI.dbg = dbg0;
  }
  AI.lastQ = Object.assign(AI.lastQ || {}, { [fmt]: out.map(qKey).filter(Boolean) });
  AI.lastHook = Object.assign(AI.lastHook || {}, { [fmt]: out.map(x => x.copy.pid).filter(Boolean) });
  decorate(out, fmt);
  return out;
}
// 넓혀 다시 고를 만한지 (D-118 · 검토 D-119): 남은 문구·장면이 있어야 하고, 모자란 까닭이 장면 묶음(같은 장면 ≤2·같은 템플릿+장면)뿐이면 장면이 더 없을 때는 문구를 늘려도 못 채움
function wideWorth(short, moreCopies, moreFrames) {
  if (!moreCopies && !moreFrames) return false;
  const ks = Object.keys((short && short.why) || {});
  if (!moreFrames && ks.length && ks.every(k => k === "scene" || k === "tplScene")) return false;
  return true;
}
// 넓혀 고른 결과를 받는 조건: 더 많이 채우고 모든 장이 WIDE_MIN 점 넘게 (6개를 채우려고 약한 장을 넣지 않음)
function wideOk(more, out) { return more.length > out.length && Math.min(...more.map(x => x.score)) >= WIDE_MIN; }

/* ----- 소품 (판정 2회차: 레퍼런스의 😱·👀·배지·손그림 화살표가 84장 중 0장) — 고른 6개 중 일부에만, 얼굴·제목·재생시간 자리를 피해서 ----- */
function propAvoid(x) {
  const d = x.doc, ctx = x.ctx, bg = d.layers.find(l => l.type === "image" && l.name === "배경");
  const av = d.layers.filter(l => !l.hidden && (l.type === "text" || l.type === "shape" || /스티커|로고|누끼/.test(l.name || "")) && !/어둡게|덮개|띠|바탕/.test(l.name || "")).map(l => (l.name === "누끼" ? null : extentOf(l))).filter(Boolean);
  if (bg) for (const p of ctx.frame.persons || []) { const b = boxC(bg, p, ctx.ar); if (b.h >= ctx.H * 0.12) av.push(b); }  // 사람 몸 위에 스티커가 얹히지 않게 (판정)
  if (bg) { av.push(...headAvoid(ctx, bg)); const mf = mainFace(ctx.frame); if (mf) { const b = boxC(bg, mf, ctx.ar); av.push({ x: b.x - b.w * 0.2, y: b.y - b.h * 0.5, w: b.w * 1.4, h: b.h * 1.9 }); } }
  const cut = d.layers.find(l => l.name === "누끼"), m = mainBox(ctx.frame);
  if (cut && m) { const b = boxC(cut, m, ctx.ar); av.push({ x: b.x, y: b.y, w: b.w, h: Math.min(b.h, ctx.H * 0.5) }); }
  return av;
}
function tryProp(x, layers) {  // 넣어 보고 게이트가 생기면 되돌림
  const before = x.doc.layers.slice();
  x.doc.layers.push(...layers.map(normLayer));
  const meta = { frame: x.ctx.frame, copy: x.copy, legacy: x.legacy, prior: 0, maxFrame: 1, copyMin: 0, copyMax: 1 };
  if (scoreDoc(x.doc, meta).gates.length) { x.doc.layers = before; return false; }
  return true;
}
function badgeLayer(ctx, text) {
  const b = ctx.brand.colors, sz = Math.round(ctx.W * (ctx.short ? 0.08 : 0.052));  // 목록 크기에서 8px 넘게
  const l = L("text", { text, name: "배지", font: "Pretendard Black", size: sz, fill: "#111111", strokes: [{ color: "#000000", width: 0 }, { color: "#FFFFFF", width: 0 }],
    shadow: { on: true, color: "#000000", blur: 12, dx: 0, dy: 4, opacity: 0.5 }, box: { on: true, color: b.hl, pad: Math.round(sz * 0.3), radius: Math.round(sz * 0.35) }, rot: -3 });
  fitText(l); return l;
}
function scribbleTo(ctx, s, e, seed) {  // s → e 손그림 화살표 (가운데가 살짝 휨)
  const sc = ctx.W / 1280, [sx, sy] = s, [ex, ey] = e, mx = (sx + ex) / 2 + (ey - sy) * 0.18, my = (sy + ey) / 2 - (ex - sx) * 0.18;
  const xs = [sx, mx, ex], ys = [sy, my, ey], x0 = Math.min(...xs), y0 = Math.min(...ys), w = Math.max(40, Math.max(...xs) - x0), h = Math.max(40, Math.max(...ys) - y0);
  return tacShape("scribble", { name: "손그림 화살표", x: x0, y: y0, w, h, pts: xs.map((x, i) => [(x - x0) / w, (ys[i] - y0) / h]), fill: "#FFFFFF", width: Math.round(11 * sc), head: Math.round(44 * sc), seed,
    stroke: { color: "#000000", width: Math.round(4 * sc) }, shadow: { on: true, color: "#000000", blur: 8, dx: 0, dy: 3, opacity: 0.5 } });
}
const AUTO_BADGE = false;  // 자동 배지(1분 강좌·실전 전술) — 판정(dev)에서 끔 · 편집기 배지 메뉴로는 언제나
// 판정 5회차(D-091): 설명 글자 — 레퍼런스 롱폼 44~57% 에 주인공 옆 작은 설명('발바닥은 필수'·'수비를 떨구는'·'(현역 1)') · 우리 6%
// 훅 제목이 숨긴 구체 낱말(문구 sub · 대사 근거)을 주인공 머리 옆에 흰 글자 + 검은 획 + 짧은 손그림 선으로 (발 이야기면 발·공으로)
const ANNOT_MIN_PX = 10;
function annotLayers(x) {
  const ctx = x.ctx, d = x.doc, sub = (x.copy.sub || "").trim(), m = mainBox(ctx.frame);
  const bg = d.layers.find(l => l.type === "image" && l.name === "배경");
  if (!sub || !m || !bg || sub.length > 9 || /[<>]/.test(sub)) return null;
  const heads = d.layers.filter(isHead), title = heads.map(l => l.text).join(" ").replace(/\s/g, "");
  if (!heads.length || title.includes(sub.replace(/\s/g, ""))) return null;
  const pb = boxC(bg, m, ctx.ar); if (pb.h < ctx.H * 0.25) return null;
  // 판정 q5 2회차(D-114): 짝 판정에서 설명 글자 + 선이 있는 쪽을 프로로 100%(8짝) · 판정 고칠 점 '설명이 작아 안 읽힘' → 롱폼 0.065 → 0.075H · 쇼츠도 (0.036H)
  // 검토(D-119): 그래도 휴대폰 목록 크기(롱폼 168px · 쇼츠 110px)에서 7px·4px ('속도 줄이기 설명은 휴대폰 크기에서 사라짐') → 목록 크기에서 10px 넘게 (롱폼 0.107H · 쇼츠 0.052H) · 자리가 없으면 안 넣음
  const size = Math.ceil(ANNOT_MIN_PX * ctx.W / (ctx.short ? 110 : 168));
  const lab = L("text", { text: sub, name: "설명 글자", font: "Pretendard Black", size, fill: "#FFFFFF", align: "left", lh: 1.04,
    strokes: [{ color: "#111111", width: Math.round(size * 0.16) }, { color: "#FFFFFF", width: 0 }], shadow: softShadow(size) });
  fitText(lab);
  const foot = footCopy(x.copy), hb = headBox(ctx.frame), hc = hb ? boxC(bg, hb, ctx.ar) : { x: pb.x + pb.w * 0.3, y: pb.y, w: pb.w * 0.4, h: pb.h * 0.15 };
  const tgt = foot ? [pb.x + pb.w / 2, pb.y + pb.h * 0.88] : [hc.x + hc.w / 2, hc.y + hc.h * 0.9];
  const av = propAvoid(x).filter(a => !(a.w >= ctx.W * 0.9 && a.h >= ctx.H * 0.9));
  const spots = [];
  for (const g of [ctx.W * 0.04, ctx.W * 0.1]) for (const y of [hc.y - ctx.H * 0.02, hc.y - lab.h - ctx.H * 0.06, pb.y + pb.h * 0.45])
    spots.push([pb.x + pb.w + g, y], [pb.x - g - lab.w, y]);  // 머리 옆 → 머리 위 비스듬히 → 몸 가운데 높이 (오른쪽·왼쪽, 가까이·조금 멀리)
  for (const [lx, ly] of spots) {
    lab.x = lx; lab.y = ly;
    const lb = extentOf(lab); if (!inSafe(ctx, lb, 0) || av.some(a => overlap(lb, a, 6))) continue;
    const right = lx > tgt[0], s0 = [right ? lab.x - ctx.W * 0.006 : lab.x + lab.w + ctx.W * 0.006, lab.y + lab.h * 0.55];
    const e0 = [tgt[0] + (right ? 1 : -1) * Math.max(pb.w * 0.35, ctx.W * 0.02), tgt[1]];
    if (Math.hypot(e0[0] - s0[0], e0[1] - s0[1]) < ctx.W * 0.04) continue;
    const ar = scribbleTo(ctx, s0, e0, 7); ar.name = "설명 선"; ar.width = Math.round(7 * ctx.W / 1280); ar.head = Math.round(30 * ctx.W / 1280);
    const near = a => e0[0] >= a.x - ctx.W * 0.06 && e0[0] <= a.x + a.w + ctx.W * 0.06 && e0[1] >= a.y - ctx.H * 0.06 && e0[1] <= a.y + a.h + ctx.H * 0.06;  // 가리키는 사람·머리는 빼고
    if (av.some(a => !near(a) && overlap(bbox([ar]), a, -Math.min(a.w, a.h) * 0.25))) continue;
    return [ar, lab];
  }
  return null;
}
// 판정 5회차(D-091): ''여기' 봐야 뚫립니다' 문구에 '여기'가 그림에 없음 (판정 '여기가 어딘지 안 보임') → 공(보이면)이나 주인공 발밑에 네온 원 하나
// 판정 q5 1회차(D-099): 공 아래 네온 빛 고리는 '싸구려 효과' (002_short_5) · 고칠 점 200개 중 56개가 '가리키는 곳을 원·화살표로' → 공이 보이면 빨간 동그라미(빛 없음)
// 판정 q5 2회차 검토(D-119): 동그라미 38장 중 6장이 확률 0.3 아래 공 — 그중 2장은 손목 띠('빨간 원이 손목을 가리켜 의미가 없음') →
// 확률 0.4 넘는 공만 · 낮으면 주인공 발 근처(키의 65~112% 높이 · 몸 옆 0.6배 안)일 때만 (가까이 찍은 진짜 공은 확률이 0.2 남짓이어도 발 옆)
const BALL_CONF = 0.4;
function ballSure(f) {
  const b = f && f.ball, m = mainBox(f); if (!b || !m) return false;
  const c = b[4] ?? 1; if (c >= BALL_CONF) return true;
  const cx = b[0] + b[2] / 2, rel = (b[1] + b[3] / 2 - m[1]) / Math.max(1e-6, m[3]);
  return c >= 0.2 && rel >= 0.65 && rel <= 1.12 && cx >= m[0] - m[2] * 0.6 && cx <= m[0] + m[2] * 1.6;
}
const sameBox = (a, b) => !!a && !!b && a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
// 판정 q5 2회차 검토(D-119): 쇼츠 동그라미 21장 중 12장이 버튼·제목 자리(SAFE 밖) · 누끼를 따로 옮긴 틀은 흐린 배경의 공('빨간 원이 빈 땅을 가리켜') · 로고와 겹침 →
// 글자·로고·스티커·전술 그래픽을 피하고 안전 영역 안 · 누끼를 배경과 따로 옮긴 틀(액션 누끼·누끼 크게)에는 넣지 않음
function markAvoid(x) { return x.doc.layers.filter(t => !t.hidden && (t.type === "text" || /로고|스티커/.test(t.name || "") || (t.type === "shape" && !!TAC_DEF[t.shape]))).map(extentOf); }
function circleAt(x, cx, cy, w) {  // (cx, cy) 둘레 빨간 손그림 동그라미 (선 14px · 그림자) — 안전 영역 밖·글자·로고와 겹치면 null
  const ctx = x.ctx, sc = ctx.W / 1280, h = w * 0.86;
  // 판정 q5 2회차(D-114): 짝 판정에서 빨간 동그라미가 있는 쪽을 프로로 92%(36짝) · 고칠 점 '원 선을 2~3배 굵게' → 9 → 14px (1280 기준)
  const l = normLayer(L("shape", { shape: "ellipse", name: "표시 동그라미", x: cx - w / 2, y: cy - h / 2, w, h, rot: -8, fill: "rgba(0,0,0,0)",
    stroke: { color: ctx.brand.colors.accent, width: Math.round(14 * sc) }, shadow: { on: true, color: "#000000", blur: Math.round(10 * sc), dx: 0, dy: Math.round(3 * sc), opacity: 0.6 } }));
  const bb = extentOf(l);
  if (!inSafe(ctx, bb, 0) || markAvoid(x).some(t => overlap(t, bb, 4))) return null;
  return [l];
}
function ballCircle(x) {  // 공 둘레 빨간 손그림 동그라미 — 믿을 만한 공이 화면 안쪽·글자 밖에 또렷이(폭 2%W 넘게) 보일 때만
  const ctx = x.ctx, f = ctx.frame, bg = x.doc.layers.find(l => l.type === "image" && l.name === "배경");
  if (!bg || !f.ball || !mainBox(f) || !ballSure(f)) return null;
  const cut = x.doc.layers.find(l => l.name === "누끼" && !l.hidden); if (cut && !sameBox(cut, bg)) return null;
  const b = boxC(bg, f.ball, ctx.ar), cx = b.x + b.w / 2, cy = b.y + b.h / 2, sc = ctx.W / 1280;
  if (b.w < ctx.W * 0.02 || cx < ctx.W * 0.08 || cx > ctx.W * 0.92 || cy < ctx.H * 0.12 || cy > ctx.H * 0.92) return null;
  return circleAt(x, cx, cy, clamp(Math.max(b.w, b.h) * 2.4, (ctx.short ? 80 : 120) * sc, 320 * sc));  // D-114: 판정 '빨간 원이 너무 작음' (롱폼 먼 공) → 롱폼 120px 부터
}
const HERE_COPY = /'여기'/;
function hereMark(x) {
  if (!HERE_COPY.test(`${x.copy.l1} ${x.copy.l2}`)) return null;
  // 판정 q5 2회차(D-114): 공이 없을 때 발밑에 깔던 네온 빛 고리는 '맥락 없이 떠 있음'·'싸구려' → 공이 또렷이 보일 때(빨간 동그라미)만
  // 검토(D-119): 그러자 '여기' 문구에 아무 표시가 없음('여기가 어딘지 안 보임') → 공이 없으면 주인공 발 쪽에 같은 빨간 동그라미
  return ballCircle(x) || feetCircle(x);
}
function feetCircle(x) {  // 주인공 발 둘레 빨간 동그라미 (발이 화면 안에 보이고 주인공이 화면 높이 0.25 넘게 보일 때만)
  const ctx = x.ctx, f = ctx.frame, m = mainBox(f), bg = x.doc.layers.find(l => l.type === "image" && l.name === "배경");
  if (!bg || !m || f.kind === "close") return null;
  const cut = x.doc.layers.find(l => l.name === "누끼" && !l.hidden); if (cut && !sameBox(cut, bg)) return null;
  const p = boxC(bg, m, ctx.ar), fy = p.y + p.h * 0.93;
  if (p.h < ctx.H * 0.25 || fy > ctx.H * 0.95 || fy < ctx.H * 0.2) return null;
  return circleAt(x, p.x + p.w / 2, fy, clamp(p.w * 1.1, 120 * ctx.W / 1280, 340 * ctx.W / 1280));
}
// 판정 q5 2회차(D-114): 짝 판정(같은 썸네일에서 그래픽만 뺀 것과 비교) — 그래픽이 있는 쪽을 프로로 82%(롱폼 90%) · 공 빨간 동그라미 92% · 패스 화살표 92% ·
// '공간' 칩 + 화살표 100% · 설명 글자 + 선 100% · 쇼츠 손그림 화살표(제목 → 주인공)만 40% — 그런데 그래픽이 있는 장은 49%(쇼츠 38%) →
// 그래픽이 없는 장마다 하나씩(설명 글자 → 공 동그라미 → 패스 화살표 → '공간' 칩 차례를 장마다 돌려서) · 넣고 게이트가 생기면 되돌림 · 쇼츠 손그림 화살표는 뺌
const GFX_NAME = /표시|설명|스티커|손그림/;
const hasGfx = x => x.doc.layers.some(l => !l.hidden && ((l.type === "shape" && !!TAC_DEF[l.shape]) || GFX_NAME.test(l.name || "")));
function tacAvoid(x) {  // 전술 그래픽이 피할 곳: 글자·로고·스티커 · 쇼츠 버튼·제목 자리
  const ctx = x.ctx, av = x.doc.layers.filter(l => !l.hidden && (l.type === "text" || /로고|스티커/.test(l.name || ""))).map(extentOf);
  if (ctx.short) av.push({ x: ctx.W * 0.84, y: ctx.H * 0.4, w: ctx.W, h: ctx.H }, { x: 0, y: ctx.H * 0.78, w: ctx.W, h: ctx.H });
  return av;
}
function passArrow(x) {  // 공(또는 주인공 발) → 동료 발밑 노란 화살표 (패스·움직임 길)
  const ctx = x.ctx, bg = x.doc.layers.find(l => l.type === "image" && l.name === "배경");
  if (!bg || !tacOk(ctx)) return null;
  const ls = tactics(ctx, bg, tacAvoid(x), { ring: false, chip: false, arrowColor: ctx.brand.colors.hl });
  return ls.length ? ls : null;
}
function spaceArrow(x) {  // 공 → 빈 자리 '공간' 칩 (쪼살)
  const ctx = x.ctx, bg = x.doc.layers.find(l => l.type === "image" && l.name === "배경");
  if (!bg || !tacOk(ctx)) return null;
  const ls = tactics(ctx, bg, tacAvoid(x), { ring: false });
  return ls.some(l => l.shape === "marker") ? ls : null;
}
const DECOR = [["annot", 2, x => annotLayers(x)], ["circle", 4, x => ballCircle(x)], ["pass", 2, x => passArrow(x)], ["space", 1, x => spaceArrow(x)]];
function decorate(out, fmt) {
  if (AI.copySel && AI.copySel.src === "user" && !out.length) return;
  const cnt = { annot: 0, circle: 0, pass: 0, space: 0 };
  for (const x of out) {  // '여기' 문구 → 그림에 '여기' (공 빨간 동그라미 · 전술 그래픽이 이미 있으면 그대로)
    if (hasGfx(x)) continue;
    const ls = hereMark(x); if (ls && tryProp(x, ls)) cnt.circle++;
  }
  if (fmt === "long") {
    let em = 0, bd = 0;
    for (const x of out) {  // 이모지 1장 — 문구가 놀람·보기·오답일 때만 (판정: 아무 문구에나 붙은 불꽃은 '뜬금없음')
      if (em >= 1) break;  // 판정 3회차: 스티커 있는 장 평균이 더 낮음(상관 −0.17) → 6장 중 1장만
      if (!["놀람", "보기", "오답"].includes(x.copy.tag) || hasGfx(x)) continue;  // 판정 4회차: 질문마다 붙던 🤔 가 3개 영상에서 '붙여 넣은 티' → 질문은 빼고, 장마다 다른 감정일 때만
      const big = x.doc.layers.find(l => l.name === "제목 큰 줄") || x.doc.layers.find(l => l.type === "text");
      if (!big) continue;
      const bb0 = extentOf(big), e = emojiLayer(x.ctx, big, propAvoid(x).filter(a => !(a.x === bb0.x && a.y === bb0.y && a.w === bb0.w)), { k: 0.9, rot: 10 });
      if (e && tryProp(x, [e])) em++;
    }
    if (AUTO_BADGE) for (const x of out) {  // 작은 배지 — 판정(dev): '1분 강좌·실전 꿀팁 라벨이 요소를 흩뜨림·잘려 보임'이 롱폼 낮은 장마다 → 자동으로는 넣지 않음 (편집기 배지 메뉴로)
      if (bd >= 1) break;
      if (x.doc.layers.some(l => /스티커|배지|로고|시리즈/.test(l.name || ""))) continue;
      const l = badgeLayer(x.ctx, (INFO.duration || 999) <= 100 ? "1분 강좌" : x.tac ? "실전 전술" : "실전 꿀팁"), av = propAvoid(x), ctx = x.ctx;
      for (const [px, py] of [[ctx.W * 0.045, ctx.H * 0.06], [ctx.W * 0.955 - l.w, ctx.H * 0.06]]) {
        l.x = px; l.y = py; const bb = extentOf(l);
        if (inSafe(ctx, bb, 0) && !av.some(a => overlap(bb, a, 6)) && tryProp(x, [l])) { bd++; break; }
      }
    }
  }
  let k = 0;
  for (const x of out) {  // 그래픽이 없는 장마다 하나 (종류를 장마다 돌려서 · 종류마다 묶음 상한)
    if (hasGfx(x)) continue;
    for (let j = 0; j < DECOR.length; j++) {
      const [nm, cap, mk] = DECOR[(k + j) % DECOR.length];
      if (cnt[nm] >= cap) continue;
      const ls = mk(x); if (ls && tryProp(x, ls)) { cnt[nm]++; k++; break; }
    }
  }
}

/* ----- 분석 받기 (다 돼 있으면 바로, 아니면 작업으로: 장면·선수 찾기 → 누끼 → 문구) ----- */
async function loadBrand() { try { const j = await (await fetch("/api/thumb/brand")).json(); if (j.ok) { AI.brand = j.brand; AI.brandCustom = j.custom || []; } } catch (e) {} return AI.brand; }
async function loadThumbStyle() {  // 썸네일 스타일 (고른 것·버릇) + 이 영상의 A/B 묶음
  try { const j = await (await fetch("/api/thumb/style?name=" + encodeURIComponent(NAME))).json(); if (j.ok) { TS.view = j.view; TS.sets = j.sets || []; } } catch (e) {}
  return TS.view;
}
function setAIStat(t) { const e = $("aiStat"); if (e) e.textContent = t; }
async function ensureAnalysis(force) {
  if (AI.loaded && !force) return true;
  setAIStat("장면·선수 찾는 중…");
  for (let tries = 0; tries < 120; tries++) {
    const j = await post("/api/thumb/analyze", { name: NAME });
    let r = j;
    if (j.jobId) r = await watchJob(j.jobId);
    else if (!j.ok) { setAIStat("다른 작업(장면 추출 등)이 끝나면 저절로 이어서 만들어요… 기다려 주세요"); await new Promise(res => setTimeout(res, 3000)); continue; }  // 409: 다시 누를 필요 없음
    if (!r || !r.frames) { setAIStat("분석하지 못했어요 · " + ((LAST_FAIL && LAST_FAIL.msg) || "작업 기록을 확인해 주세요")); return false; }  // 실패 안내(trouble)의 쉬운 한 줄
    AI.frames = r.frames; ECC.clear(); AI.cuts = r.cuts || {}; AI.copy = (r.copy && r.copy.items) || []; AI.topics = (r.copy && r.copy.topics) || []; AI.ai = !!(r.copy && r.copy.ai);
    AI.cutFail = r.cutFail || "";  // 자동 누끼 실패(따로 프로세스 · trouble 의 쉬운 한 줄) — 추천은 누끼 없이 계속, 분석 줄에 보여 줌
    if (!FRAMES.length || !FRAMES[0].kind) { FRAMES = r.frames; renderStrip(); }
    AI.loaded = true; return true;
  }
  return false;
}
async function aiRun(seed = 0) {
  while (AI.busy) await AI.busy;  // 하던 추천이 끝난 뒤에 (형식 바꾸기·다시 추천이 겹쳐도 차례로)
  let done; AI.busy = new Promise(r => (done = r));
  try {
    if (!(await ensureAnalysis())) return null;
    await loadBrand(); await loadThumbStyle(); renderTS();
    if (!AI.frames.length) { setAIStat("쓸 만한 장면을 찾지 못했어요"); return null; }
    setAIStat("조합하고 점수 매기는 중…");
    await new Promise(r => setTimeout(r, 0));
    const t0 = performance.now(), prev = new Set(seed ? AI.results.map(x => x.key) : []);
    AI.results = recommend(AUTO_FMT, 6, seed, prev); AI.seed = seed; AI.ab = new Set();
    for (const x of AI.results) for (const l of x.doc.layers) if (l.type === "image" && l.src) img(l.src);
    renderAI();
    setAIStat((!AI.results.length ? `추천을 만들지 못했어요 — ${emptyReason()} · 아래 '장면 고르기'에서 장면을 직접 골라 주세요`
      : AI.weak ? `쓸 만한 장면이 없어요 — 아래 '장면 고르기'에서 직접 골라 주세요 (지금은 ${AI.results.length}개만 만들었어요)`
      : `${AI.results.length}개 · ${((performance.now() - t0) / 1000).toFixed(1)}초 · 눌러서 편집하거나 A/B 에 담아 보세요`) + (AI.cutFail ? ` · ${AI.cutFail}` : ""));
    if (AI.cutFail) cutRetry();
    return AI.results;
  } finally { AI.busy = false; done(); }
}
function cutRetry() {  // 자동 누끼 실패 줄 끝에 [누끼 다시 하기] — '다시 해 주세요' 를 이 화면에서 (못 딴 장면만 다시 · 장면 후보·딴 누끼는 그대로 씀)
  const e = $("aiStat"); if (!e) return;
  const b = document.createElement("button"); b.className = "btn sm"; b.id = "cutRetry"; b.textContent = "누끼 다시 하기";
  b.onclick = () => { b.disabled = true; AI.loaded = false; aiRun(AI.seed); };
  e.append(" ", b);
}
// 판정 q5 1회차(D-111): THUMBTEST01 처럼 추천이 0개일 때 '0개 · 0.2초 · 눌러서 편집하거나…'만 나와 까닭을 몰랐음 → 가장 흔한 까닭을 쉬운 말로
function emptyReason() {
  const fs = AI.frames || [];
  if (fs.length && fs.every(f => (f.text || 0) >= TEXTY)) return "장면마다 다른 썸네일·자막 같은 큰 글자가 박혀 있어 그 위에 제목을 올릴 수 없어요";
  if (fs.length && fs.every(f => !(f.persons || []).length)) return "사람이 나오는 장면을 찾지 못했어요";
  const g = {}; for (const v of Object.values((AI.dbg && AI.dbg.byTpl) || {})) for (const k of v[2] || []) g[k] = (g[k] || 0) + 1;
  const top = Object.entries(g).sort((a, b) => b[1] - a[1])[0];
  const plain = { "주인공머리잘림": "주인공 머리가 잘린 장면뿐이에요", "주인공이작음": "주인공이 너무 작게 나오는 장면뿐이에요", "흐린빈곳": "장면이 작아 빈 곳이 많이 남아요",
    "얼굴을가림": "제목이 얼굴을 가리는 장면뿐이에요", "주인공머리를가림": "제목이 주인공 머리를 가리는 장면뿐이에요" };
  return (top && plain[top[0].replace(/[:\s]/g, "")]) || "장면·문구를 맞춰 볼 조합이 없어요";
}
/* ----- 화면: '자동' 탭 맨 위 ----- */
function frameBadges(f) {
  const ps = (f.persons || []).length, e = f.emo ? (f.emo.happiness || 0) + (f.emo.surprise || 0) : 0;
  return [f.ai != null ? `🤖${Math.round(f.ai)}` : "", ps ? `👥${ps}` : "", f.ball ? "⚽" : "", (f.blur ?? 1) < 0.25 ? "✨선명" : "", e >= 0.4 ? "😆" : ""].filter(Boolean).join(" ");
}
function renderAI() {
  const el = $("aiSec"); if (!el) return;
  const copies = [...AI.copy].sort((a, b) => b.score - a.score).slice(0, 10);
  el.innerHTML = `<div class="pg aibox"><h4>✨ AI 추천 썸네일 <span class="hint">장면·문구·디자인을 골라 레퍼런스 채널처럼 만들어요</span></h4>
    <div class="row2"><button class="btn pri aigo" id="aiGo">✨ AI 추천 썸네일 6개</button><button class="btn" id="aiRe" ${AI.results.length ? "" : "disabled"}>다시 추천</button><button class="btn" id="abSave" ${AI.ab.size ? "" : "disabled"}>A/B 묶음 저장 (${AI.ab.size})</button></div>
    <div id="tsBox"></div>
    <div class="hint" id="aiStat">${AI.results.length ? "" : "누르면 장면·선수 찾기 → 누끼 → 문구 → 조합·채점 순서로 만들어요 (처음 한 번 1~2분)"}</div>
    <div class="aicards ${AUTO_FMT}" id="aiCards"></div>
    <details class="fx" ${AI.copy.length ? "open" : ""}><summary>▸ 문구 후보 <span class="hint">누르면 그 문구로만 다시 추천</span></summary><div>
      <div class="copychips" id="copyChips">${copies.map((c, i) => `<span class="cchip ${AI.copySel === c ? "on" : ""}" data-ci="${i}" title="${esc(c.why || "")}">${chipHtml(c)}${c.src === "ai" ? "<i>클로드</i>" : ""}</span>`).join("") || `<span class="hint">문구를 만드는 중…</span>`}</div>
      <div class="row2"><input class="s" id="copyL1" placeholder="첫째 줄" style="flex:1"><input class="s" id="copyL2" placeholder="둘째 줄 (크게)" style="flex:1"></div>
      <div class="row2"><button class="btn sm" id="copyUse">직접 쓴 문구로 추천</button>${AI.copySel ? `<button class="btn sm" id="copyAll">문구 고르기 해제</button>` : ""}<button class="btn sm" id="copyAi">🤖 클로드로 더 만들기</button><span class="hint" style="align-self:center">내 클로드 계정 사용${AI.ai ? " · 클로드 문구 있음" : ""}</span></div>
    </div></details>
    <details class="fx" ${AI.frames.length ? "open" : ""}><summary>▸ 장면 고르기 <span class="hint">누르면 그 장면으로만 다시 추천</span></summary><div class="aiframes" id="aiFrames">${[...AI.frames].sort((a, b) => b.score - a.score).map(f => `<div class="af ${AI.pick === f.t ? "on" : ""}" data-t="${f.t}" title="${esc(f.aiWhy ? "클로드: " + f.aiWhy : "")}"><img src="${frameSrc(f.t)}" loading="lazy"><b>${frameBadges(f)}</b><span>${mmss(f.t)}</span></div>`).join("") || `<span class="hint">장면은 추천을 누르면 찾아요</span>`}${AI.pick != null ? `<button class="btn sm" id="pickAll">장면 고르기 해제</button>` : ""}</div></details></div>`;
  $("aiGo").onclick = () => aiRun(0); $("aiRe").onclick = () => aiRun(AI.seed + 1);
  $("abSave").onclick = () => abSave();
  el.querySelectorAll("[data-ci]").forEach(c => (c.onclick = () => { AI.copySel = copies[+c.dataset.ci]; renderAI(); if (AI.loaded) aiRun(AI.seed); }));
  $("copyUse").onclick = () => {
    const a = $("copyL1").value.trim(), b = $("copyL2").value.trim(); if (!a && !b) return toast("문구를 써 주세요");
    const l2 = b || a, l1 = b ? a : "", c = { l1: l1 || l2, l2: l1 ? l2 : "", emph: null, sub: "", pid: "user", score: 6, tag: "불", src: "user", why: "직접 쓴 문구" };
    AI.copySel = c; renderAI(); aiRun(AI.seed);
  };
  if ($("copyAll")) $("copyAll").onclick = () => { AI.copySel = null; renderAI(); aiRun(AI.seed); };
  $("copyAi").onclick = copyAi;
  el.querySelectorAll(".af").forEach(a => (a.onclick = () => { AI.pick = +a.dataset.t; renderAI(); aiRun(AI.seed); }));
  if ($("pickAll")) $("pickAll").onclick = () => { AI.pick = null; renderAI(); aiRun(AI.seed); };
  renderTS();
  renderAICards();
}
// 썸네일 스타일 고르기 + 이 영상의 A/B 묶음에서 이긴 장 적기 (D-130 · D-131)
const TS_NONE = "기본 (풋살사관학교)";
function renderTS() {
  const el = $("tsBox"); if (!el) return;
  const v = TS.view, sts = (v && v.styles) || [], o = v && v.ours, act = v && v.active, cur = act ? act.name : "";
  const opts = [["", TS_NONE]].concat(sts.map(x => [x.name, x.name])).concat(o && o.n ? [["__ours__", `우리 채널 (A/B 이긴 것 ${o.n}번)`]] : []);
  const custom = (AI.brandCustom || []).filter(k => k === "hl" || k === "hl2").length && AI.brand && AI.brand.apply !== false;
  const sets = (TS.sets || []).slice(0, 3);
  el.innerHTML = `<div class="tsrow"><span class="hint">썸네일 스타일</span><select class="s" id="tsPick">${opts.map(([k, t]) => `<option value="${esc(k)}" ${k === cur ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
    <div class="hint" id="tsDesc">${act ? `썸네일 버릇: ${esc(act.desc || "")}${act.params && act.params.hl ? ` <span class="tsw" style="background:${esc(act.params.hl)}"></span><span class="tsw" style="background:${esc(act.params.hl2 || "#FFFFFF")}"></span>` : ""}${custom ? " · 브랜드 키트에서 직접 고른 색이 먼저예요" : ""}`
      : sts.length ? "스타일을 고르면 그 채널의 글자 색·위치·크기·테두리·배경 밝기를 따라 만들어요" : "스타일 카드에서 '썸네일 버릇 배우기'를 하면 여기서 고를 수 있어요"}${o && o.n && cur !== "__ours__" ? ` · 우리 채널 A/B 에서 이긴 틀·문구에 가산점` : ""}</div>
    ${sets.map(st => `<div class="abwin" data-set="${esc(st.id)}"><span class="hint">A/B ${esc(st.at || "")} · ${st.winner ? `🏆 ${esc(st.winner)}가 이겼어요` : "YouTube '테스트 및 비교'에서 이긴 썸네일을 눌러 주세요"}</span>
      ${st.items.map(i => `<button class="btn sm ${st.winner === i.tag ? "pri" : ""}" data-abw="${esc(i.tag)}" title="${esc([i.tpl, i.l1, i.l2].filter(Boolean).join(" · "))}">${esc(i.tag)}${st.winner === i.tag ? " 🏆" : ""}</button>`).join("")}</div>`).join("")}`;
  $("tsPick").onchange = async e => {
    const j = await post("/api/thumb/style", { style: e.target.value });
    if (!j.ok) return toast(j.error || "고르지 못했어요");
    TS.view = j.view; renderTS(); toast(e.target.value ? "이 스타일의 썸네일 버릇으로 다시 추천해요" : "기본 모양으로 다시 추천해요");
    if (FRAMES.length) makeCands();  // 예전 템플릿 후보(자동 후보)도 그 버릇으로
    if (AI.results.length) aiRun(AI.seed);
  };
  el.querySelectorAll("[data-abw]").forEach(b => (b.onclick = async () => {
    const box = b.closest("[data-set]"), st = (TS.sets || []).find(x => x.id === box.dataset.set), tag = st && st.winner === b.dataset.abw ? "" : b.dataset.abw;
    const j = await post("/api/thumb/ab_win", { id: box.dataset.set, tag, name: NAME });
    if (!j.ok) return toast(j.error || "적지 못했어요");
    TS.view = j.view; TS.sets = j.sets || TS.sets; renderTS();
    toast(tag ? `${tag}가 이겼다고 적어 뒀어요 · 다음 추천부터 이긴 틀·색·문구 틀에 가산점을 줘요` : "이긴 썸네일 표시를 지웠어요");
  }));
}
function chipHtml(c) {  // 강조 낱말을 노랗게 미리 보여 줌
  const ln = [c.l1, c.l2].map((t, i) => { const e = c.emph && c.emph[0] === i ? c.emph : null; return e ? esc(t.slice(0, e[1])) + `<em>${esc(t.slice(e[1], e[2]))}</em>` + esc(t.slice(e[2])) : esc(t); });
  return ln.filter(Boolean).join(" / ");
}
function renderAICards() {
  const el = $("aiCards"); if (!el) return;
  const short = AUTO_FMT === "short", cw = short ? 180 : 320, ch = short ? 320 : 180;
  el.className = `aicards ${AUTO_FMT}`;
  el.innerHTML = AI.results.map((x, i) => `<div class="aicard" data-ai="${i}"><canvas width="${cw}" height="${ch}"></canvas>
    <div class="meta"><b>${Math.round(x.score)}점</b> ${esc(x.tpl)}<br><span class="hint">${esc(x.why.join(" · ") || "")}</span></div>
    <div class="row"><button class="btn sm pri" data-aedit="${i}">편집하기</button><label class="hint"><input type="checkbox" data-ab="${i}" ${AI.ab.has(i) ? "checked" : ""}> A/B에 담기</label></div></div>`).join("");
  paintThumbs("ai", [...el.querySelectorAll("[data-ai]")].map(c => [c.querySelector("canvas"), AI.results[+c.dataset.ai] && AI.results[+c.dataset.ai].doc]).filter(x => x[0] && x[1]));
  el.querySelectorAll("[data-aedit]").forEach(b => (b.onclick = () => openAI(+b.dataset.aedit)));
  el.querySelectorAll("[data-ab]").forEach(b => (b.onchange = () => { const i = +b.dataset.ab; b.checked ? AI.ab.add(i) : AI.ab.delete(i); const s = $("abSave"); if (s) { s.disabled = !AI.ab.size; s.textContent = `A/B 묶음 저장 (${AI.ab.size})`; } }));
  const re = $("aiRe"); if (re) re.disabled = !AI.results.length;
}
function openAI(i) {
  const x = AI.results[i]; if (!x) return;
  DOCS.designs.push({ id: nid(), name: `AI ${x.tpl} ${DOCS.designs.length + 1}`, doc: clone(x.doc) });
  openDesign(DOCS.designs.length - 1); scheduleSave(); toast("추천 썸네일을 불러왔어요 · 글자·그래픽 모두 따로 고칠 수 있어요");
}
async function copyAi() {
  const j = await post("/api/thumb/copy", { name: NAME, ai: true });
  if (!j.ok) return toast(j.error || "지금은 할 수 없어요");
  const r = await watchJob(j.jobId); if (!r) return;
  if (!r.ok) return toast(r.error || "클로드 문구를 받지 못했어요 (규칙 문구는 그대로 있어요)");
  const c = await post("/api/thumb/copy", { name: NAME }); if (c.ok) { AI.copy = c.items; AI.ai = c.ai; }
  renderAI(); toast("클로드 문구를 더했어요 · 다시 추천을 눌러 보세요");
}
// 개발·판정용: 추천 문서 + 그림(dataURL). opts: {fmt: 'long'|'short', n, seed, noData, quality}
window.__thumbAuto = async (opts = {}) => {
  if (opts.fmt && opts.fmt !== AUTO_FMT) { AUTO_FMT = opts.fmt; renderAuto(); }
  if (opts.pick !== undefined) AI.pick = opts.pick;
  if (opts.copy !== undefined) AI.copySel = opts.copy;
  const res = await aiRun(opts.seed || 0); if (!res) return { ok: false, error: $("aiStat") ? $("aiStat").textContent : "실패" };
  await document.fonts.ready;
  const out = [];
  for (const x of res.slice(0, opts.n || 6)) {
    let data = null;
    if (!opts.noData) {  // 그림이 필요할 때만 원래 크기로 그림 (속도만 재는 noData 는 추천 계산까지)
      await Promise.all(x.doc.layers.filter(l => l.type === "image" && l.src).map(l => imgReady(l.src)));
      const c = newCanvas(x.doc.w, x.doc.h); renderDoc(c.getContext("2d"), x.doc, 1);
      let q = opts.quality || 0.93; do { data = c.toDataURL("image/jpeg", q); q -= 0.07; } while (data.length * 0.75 > 1.95e6 && q > 0.5);
    }
    out.push({ tpl: x.tpl, t: x.t, copy: { l1: x.copy.l1, l2: x.copy.l2, sub: x.copy.sub, src: x.copy.src, score: x.copy.score, pid: x.copy.pid,
      vague: !!x.copy.vague, stock: !!x.copy.stock, detail: !!x.copy.detail, q: x.copy.q || "" }, score: x.score, raw: x.raw, why: x.why, gates: x.gates, hpx: x.hpx, bigH: x.bigH, blurFill: x.blurFill,
      frame: AI.frames.find(f => f.t === x.t), doc: clone(x.doc), data });
  }
  return { ok: true, items: out, frames: AI.frames.length, copies: AI.copy.length };
};
