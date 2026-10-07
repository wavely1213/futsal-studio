
/* ---------- AI 추천 썸네일: 분석(장면·선수·공·표정·누끼·문구) × 레퍼런스형 템플릿 → 점수 → 서로 다른 6개 ---------- */
// 픽셀은 언제나 이 화면의 renderDoc 하나로 그림 (편집기·카드·내보내기·개발용 판정 모두 같은 그림). 백엔드는 숫자만 줌 (thumb.analyze).
const BRAND_DEF = { logo: "", logoPos: "tr", colors: { hl: "#FFE14D", hl2: "#FFFFFF", accent: "#FF3B30", neon: "#00D1FF", box: "#111111" }, font: "Black Han Sans", series: "풋사관 강좌", seriesOn: false, handle: "@풋살사관학교", apply: true, aiCopy: true };
const AI = { frames: [], cuts: {}, copy: [], topics: [], brand: null, seed: 0, results: [], busy: false, pick: null, ab: new Set(), copySel: null, loaded: false, ai: false };
const TEXTY = 0.05;  // 장면에 박힌 큰 글자 넓이가 이만큼 넘으면 글자를 피해 자르거나 배경을 흐림 (thumb.text_boxes)
const SAFE = { long: { x0: 0.035, y0: 0.04, x1: 0.965, y1: 0.95, dur: [0.84, 0.86] }, short: { x0: 0.06, y0: 0.07, x1: 0.84, y1: 0.76 } };
const brandOf = () => (AI.brand && AI.brand.apply !== false ? AI.brand : BRAND_DEF);
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
  if (ctx.band === "bottom" && o.crop !== false) l.cropB = clamp(1 - (f.bandY || 0.76) + 0.01, 0.05, 0.3);  // 영상에 박힌 자막·방송 띠는 잘라냄
  if (ctx.band === "top" && o.crop !== false) l.cropT = clamp((f.bandY || 0.2) + 0.01, 0.05, 0.3);
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
function frameLayer(ctx, o = {}) {
  const f = ctx.frame, W0 = ctx.W, H0 = ctx.H, z0 = clamp(o.zoom || 1, 1, maxZoom(ctx));  // 1 보다 작으면 캔버스를 다 못 덮음
  const focus = o.focus || ctx.focus, target = o.target || [W0 / 2, H0 / 2], hb = o.keep === false ? null : headBox(f), mg = H0 * 0.02;
  const texty = (f.text || 0) >= TEXTY && f.tboxes && f.tboxes.length;
  const zs = texty ? [z0, z0 * 1.2, z0 * 1.45, maxZoom(ctx)] : [z0, z0 * 0.9, z0 * 0.8, z0 * 0.7, 1];
  let best = null;
  for (const z1 of zs) {
    const z = clamp(z1, 1, maxZoom(ctx));
    let l = placeFrame(ctx, z, focus, target, o);
    if (hb && !headOk(ctx, l, hb, mg)) {  // 머리가 잘리면 장면을 내려서(또는 옆으로) 다시 · 그래도 안 되면 덜 확대
      const b = boxC(l, hb, ctx.ar), dy = b.y < mg ? mg - b.y + H0 * 0.01 : 0, dx = b.x < 0 ? -b.x + W0 * 0.02 : b.x + b.w > W0 ? W0 - (b.x + b.w) - W0 * 0.02 : 0;
      l = placeFrame(ctx, z, focus, [target[0] + dx, target[1] + dy], o);
    }
    const ok = !hb || headOk(ctx, l, hb, mg * 0.5), vt = texty ? visibleText(l, f, ctx.ar, W0, H0) : 0;
    const cost = (ok ? 0 : 10) + vt * 20 + Math.abs(z - z0) * 0.05;
    if (!best || cost < best.cost) best = { l, cost };
    if (ok && !texty) break;
  }
  return best.l;
}
function maxZoom(ctx) {  // 원본 장면(가로 최대 1920)을 1.6배 넘게 키우지 않게 (세로 영상으로 롱폼을 만들 때 뭉개지지 않게)
  const srcW = Math.min(1920, INFO.width || 1920), dw = ctx.W / ctx.H > ctx.ar ? ctx.W : ctx.H * ctx.ar;
  return Math.max(1, 1.6 / (dw / srcW));
}
function zoomFor(ctx, target, lo = 1, hi = 2) {  // 주인공(얼굴 또는 사람)이 화면 높이의 target 만큼 보이게 하는 확대 배율
  const f = ctx.frame, m = mainBox(f), fc = mainFace(f);
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
  if (sh < (o.minH ?? (ctx.short ? 0.28 : 0.42))) return null;
  const ow = clamp(Math.round(sh * ctx.H * 0.011 * (o.ow || 1)), 3, Math.round(8 * ctx.W / 1280));
  const l = L("image", { name: "누끼", src: ctx.cut.cut, orig: ctx.cut.src, x: bg.x, y: bg.y, w: bg.w, h: bg.h, fit: bg.fit, fx: bg.fx, fy: bg.fy, flipX: bg.flipX,
    cropL: bg.cropL, cropR: bg.cropR, cropT: bg.cropT, cropB: bg.cropB,
    outline: { on: true, color: o.outline || "#FFFFFF", width: ow },
    glow: { on: !!o.glow && sh >= 0.5, color: o.glow || "#FFFFFF", size: Math.round(22 * ctx.W / 1280), opacity: 0.75 },
    shadow: { on: true, color: "#000000", blur: 22, dx: 0, dy: 8, opacity: 0.6 } });
  if (bg.grade) l.grade = Object.assign({}, bg.grade);
  return l;
}

/* ----- 글자 (브랜드 색 역할: hl 강조 · hl2 기본 · accent 포인트 · neon 전술 · box 상자) ----- */
function tStyle(ctx, size, o = {}) {
  const sw = Math.round(size * (o.sw ?? 0.2));
  return Object.assign({ font: o.font || ctx.brand.font, size, fill: o.fill || ctx.brand.colors.hl2, fill2: o.fill2 || "", gradAngle: 90, align: o.align || "center", lh: 1.04,
    strokes: [{ color: o.stroke || "#111111", width: sw }, { color: o.inner || "#FFFFFF", width: o.innerW ? Math.round(size * o.innerW) : 0 }],
    shadow: o.noShadow ? { on: false } : { on: true, color: "#000000", blur: 0, dx: Math.round(size * 0.05), dy: Math.round(size * 0.065), opacity: 1 } }, o.extra || {});
}
const DIGIT_FONT = "Pretendard Black";  // 검은고딕의 숫자 1 은 작게 보면 'ㄱ'처럼 읽힘 (판정 OCR '1대1' → '그대그') → 숫자만 다른 굵은 글꼴
function digitRuns(text, font) {
  if (font === DIGIT_FONT) return [];
  const out = []; let m; const rx = /[0-9]+/g;
  while ((m = rx.exec(text))) out.push({ s: m.index, e: m.index + m[0].length, font: DIGIT_FONT });
  return out;
}
function lineLayer(ctx, text, size, o = {}) {  // 한 줄 글자 + 강조 낱말(runs: 강조 색·조금 크게) + 숫자 글꼴
  const l = L("text", Object.assign({ text, name: o.name || "제목" }, tStyle(ctx, size, o)));
  const runs = [];
  if (o.emph && o.emph[1] > o.emph[0]) {
    const run = { s: o.emph[0], e: o.emph[1], fill: o.emphFill || ctx.brand.colors.hl };
    if (o.emphScale && o.emphScale !== 1) { run.size = Math.round(size * o.emphScale); run.s1w = Math.round(run.size * (o.sw ?? 0.2)); }
    runs.push(run);
  }
  l.runs = runs.concat(digitRuns(text, l.font));
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
// 두 줄 제목: 강조 낱말이 든 줄을 크게(big), 다른 줄은 작게(small) · 상자(box) 안에 맞춰 쌓음 · o.sub: 아래에 어두운 상자 속 보조 문구 → {layers, box, big, small, sub}
function headline(ctx, box, o = {}) {
  const c = ctx.copy, b = ctx.brand.colors, bigI = c.l2 ? (c.emph ? c.emph[0] : 1) : 0, smallI = 1 - bigI;
  const bigT = bigI ? c.l2 : c.l1, smallT = c.l2 ? (smallI ? c.l2 : c.l1) : "";
  const align = o.align || "center", ratio = o.ratio || 0.56, style = o.style || "line";
  let size = o.size || Math.round(ctx.H * 0.2);
  const em = emphOf(c, bigI);
  const bigO = style === "line"
    ? { fill: o.bigFill || b.hl, fill2: o.grad === false ? "" : mix(o.bigFill || b.hl, "#FF8A00", 0.42), emph: o.emphAccent && em ? em : null, emphFill: b.accent, emphScale: 1 }
    : { fill: b.hl2, emph: em, emphFill: o.emphFill || b.hl, emphScale: o.emphScale || 1.18 };
  const subT = o.sub ? (c.sub || "") : "";
  const make = k => {
    const big = lineLayer(ctx, bigT, Math.round(size * k), Object.assign({ align, name: "제목 큰 줄", rot: o.rot, skew: o.skew, sw: o.sw, font: o.font }, bigO));
    const small = smallT ? lineLayer(ctx, smallT, Math.round(size * ratio * k), { align, name: "제목 작은 줄", fill: o.smallFill || b.hl2, rot: o.rot, skew: o.skew, sw: o.sw, font: o.font,
      emph: style === "word" ? emphOf(c, smallI) : null, emphFill: b.hl }) : null;
    const sub = subT ? subBox(ctx, subT, Math.round(size * (o.subRatio || 0.36) * k), { align }) : null;
    return { big, small, sub };
  };
  let t = make(1);
  const wOf = l => (l ? l.w : 0), gap = () => -Math.round(size * 0.03), subGap = () => Math.round(size * 0.12);
  const totalH = () => t.big.h + (t.small ? t.small.h + gap() : 0) + (t.sub ? t.sub.h + subGap() : 0);
  const k = Math.min(1, box.w / Math.max(wOf(t.big), wOf(t.small), wOf(t.sub)), box.h / totalH());
  if (k < 0.999) { size = Math.round(size * k); t = make(1); }
  // 휴대폰 목록 크기(롱폼 168px · 쇼츠 110px)에서 작은 줄·보조 문구도 8px 넘게: 모자라면 그 줄만 키움 (상자보다 넓어지면 점수 게이트가 거름)
  const minPx = Math.ceil(8.4 * ctx.W / (ctx.short ? 110 : 168));
  if (t.small && t.small.size < minPx) t.small = lineLayer(ctx, smallT, minPx, { align, name: "제목 작은 줄", fill: o.smallFill || b.hl2, rot: o.rot, skew: o.skew, sw: o.sw, font: o.font, emph: style === "word" ? emphOf(c, smallI) : null, emphFill: b.hl });
  if (t.sub && t.sub.size < minPx) t.sub = subBox(ctx, subT, minPx, { align });
  const order = (bigI === 0 ? [t.big, t.small] : [t.small, t.big]).concat(t.sub ? [t.sub] : []);  // 읽는 순서는 늘 첫째 줄 → 둘째 줄 (크기만 다름) → 보조 문구
  const ax = align === "center" ? 0.5 : align === "right" ? 1 : 0, X = box.x + box.w * ax;
  let y = o.anchor === "bottom" ? box.y + box.h - totalH() : o.anchor === "middle" ? box.y + (box.h - totalH()) / 2 : box.y;
  for (const l of order) { if (!l) continue; if (l === t.sub) y += subGap() - gap(); l.x = X - l.w * ax; l.y = y; y += l.h + gap(); }
  for (const l of [t.big, t.small]) if (l) l.fitBox = { w: box.w, h: l.h * 1.15, size: l.size, ay: 0 };
  const layers = order.filter(Boolean);
  return { layers, box: bbox(layers), big: t.big, small: t.small, sub: t.sub };
}
// 상자 제목 (자막형): 큰 줄은 강조색 상자에 검은 글자, 작은 줄은 어두운 상자에 흰 글자 · 왼쪽 아래부터 쌓음 → {layers, box, big}
function boxHead(ctx, x, yBottom, maxW, o = {}) {
  const c = ctx.copy, b = ctx.brand.colors, two = !!c.l2, bigI = two ? (c.emph ? c.emph[0] : 1) : 0;
  const mk = (t, size, fill, box, name) => {
    const l = L("text", Object.assign({ text: t, name }, tStyle(ctx, size, { fill, sw: 0, noShadow: true, align: "left" }),
      { box: { on: true, color: box, pad: Math.round(size * 0.14), radius: Math.round(size * 0.1) }, shadow: { on: true, color: "#000000", blur: Math.round(size * 0.25), dx: 0, dy: Math.round(size * 0.06), opacity: 0.55 } }));
    l.runs = digitRuns(t, l.font);
    fitText(l); fitW(l, maxW); return l;
  };
  const big = mk(two ? (bigI ? c.l2 : c.l1) : c.l1, o.size || ctx.H * 0.19, "#111111", b.hl, "제목 큰 줄");
  const small = two ? mk(bigI ? c.l1 : c.l2, (o.size || ctx.H * 0.19) * (o.ratio || 0.58), "#FFFFFF", b.box, "제목 작은 줄") : null;
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
function logoLayer(ctx, avoid) {  // 채널 로고 (높이 0.11H) — 제목·안전 영역과 겹치면 반대쪽, 그래도 겹치면 뺌
  const br = ctx.brand; if (!br.logo || br.logoPos === "off") return null;
  const h = Math.round(ctx.H * (ctx.short ? 0.06 : 0.11)), m = Math.round(ctx.W * 0.025);
  for (const pos of [br.logoPos, br.logoPos === "tr" ? "tl" : "tr"]) {
    const l = L("image", { name: "로고", src: br.logo, fit: "contain", w: h * 1.6, h, y: m + (ctx.short ? ctx.H * 0.07 : 0), x: pos === "tr" ? ctx.W - h * 1.6 - m - (ctx.short ? ctx.W * 0.16 : 0) : m,
      shadow: { on: true, color: "#000000", blur: 12, dx: 0, dy: 4, opacity: 0.6 } });
    if (!avoid.some(b => overlap(bbox([l]), b, 8))) return l;
  }
  return null;
}
// 이모지 스티커 (Fluent Emoji 3D · stickers/): 문구 성격(tag)에 맞춰 큰 줄 끝에 하나 — 레퍼런스의 😱·👀 자리
const EMOJI = { 놀람: "face-screaming-in-fear.png", 보기: "eyes.png", 오답: "cross-mark.png", 정답: "check-mark-button.png", 불: "fire.png", 질문: "thinking-face.png" };
function emojiLayer(ctx, near, avoid, o = {}) {
  if (!near) return null;
  const file = o.file || EMOJI[ctx.copy.tag] || "fire.png", s = Math.round(clamp(near.h * (o.k || 0.95), ctx.H * 0.07, ctx.H * (ctx.short ? 0.09 : 0.2)));
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
  const s = SAFE[ctx.short ? "short" : "long"], W0 = ctx.W, H0 = ctx.H;
  if (b.x < s.x0 * W0 - pad || b.y < s.y0 * H0 - pad || b.x + b.w > s.x1 * W0 + pad || b.y + b.h > s.y1 * H0 + pad) return false;
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
function tacOk(ctx) { const f = ctx.frame; return (f.persons || []).length >= 2 && f.main >= 0 && f.kind !== "close" && !(f.flags || []).includes("bench"); }
function tactics(ctx, bg, avoid, o = {}) {
  const f = ctx.frame, ps = f.persons || [], ar = ctx.ar, out = [], W0 = ctx.W, H0 = ctx.H, gid = "tac" + nid().slice(0, 4), b = ctx.brand.colors, scale = W0 / 1280;
  const loose = !!o.loose;  // 사용자가 메뉴에서 직접 부름: 선수 1명·클로즈업·발이 잘린 장면이어도 놓을 수 있는 만큼 (자동 추천은 엄격하게)
  if (loose ? !(ps.length && f.main >= 0) : !tacOk(ctx)) return out;
  const P = ps.map(p => { const q = boxC(bg, p, ar); return { x: q.x + q.w / 2, y: q.y + q.h, h: q.h, w: q.w, box: q }; });
  const feetIn = p => !(p.y > H0 * 0.985 || p.y < H0 * 0.3 || p.h < H0 * 0.1 || p.x < 0 || p.x > W0);
  let M = P[f.main];
  if (!feetIn(M)) { if (!loose) return out; M = Object.assign({}, M, { x: clamp(M.x, W0 * 0.15, W0 * 0.85), y: clamp(M.y, H0 * 0.45, H0 * 0.85), noRing: true }); }  // 주인공 발이 화면 안에 있어야 (발밑 원·출발점)
  let vis = P.filter(p => p.x > W0 * 0.02 && p.x < W0 * 0.98 && p.y > H0 * 0.25 && p.y < H0 * 1.02);
  if (loose && !vis.length) vis = [M];
  if (vis.length < (loose ? 1 : 2)) return out;
  const taken = [], body = vis.map(p => ({ x: p.box.x - p.box.w * 0.1, y: p.box.y, w: p.box.w * 1.2, h: p.box.h }));
  const ok = l => { const bb = bbox([l]); return bb.x > -bb.w * 0.2 && bb.x + bb.w < W0 + bb.w * 0.2 && bb.y + bb.h < H0 && bb.y > 0 && !avoid.some(a => overlap(bb, a, 12)); };
  const put = l => { out.push(l); taken.push(bbox([l])); };
  if (o.ring !== false && !M.noRing) {
    const rw = clamp(M.w * 1.7, 70 * scale, 360 * scale), rh = rw * 0.3;
    const ring = tacShape("ring", { x: M.x - rw / 2, y: M.y - rh * 0.55, w: rw, h: rh, fill: o.ringColor || b.neon, width: Math.round(10 * scale), gid,
      glow: { on: true, color: o.ringColor || b.neon, size: Math.round(24 * scale), opacity: 1 } });
    if (ok(ring)) put(ring);
  }
  // 빈 자리: 선수들 발 사이 (선수 무리 둘레 안) 중 모든 선수 발에서 가장 먼 곳 — 원근 때문에 세로 거리를 더 크게 셈 · 주인공에서 알맞게 떨어진 곳
  const xs = vis.map(p => p.x), ys = vis.map(p => p.y), cs = Math.round((ctx.short ? 0.1 : 0.21) * H0);  // 판정: 칩·화살표가 목록 크기에서 안 보임 → 크게
  const wx = loose ? 0.35 : 0.12, wy = loose ? 0.3 : 0.12;  // 느슨하게: 선수 한 명이면 둘레가 없으니 넓게
  const zone = { x0: Math.max(W0 * 0.07, Math.min(...xs) - W0 * wx), x1: Math.min(W0 * 0.93, Math.max(...xs) + W0 * wx),
    y0: Math.max(H0 * (o.up || loose ? 0.12 : 0.38), Math.min(...ys) - H0 * (o.up ? 0.3 : wy)), y1: Math.min(H0 * 0.9, Math.max(...ys) + H0 * 0.06) };  // up: 제목이 아래에 있으면 위쪽 빈 자리도
  const freeSpot = (from, dmin, dmax, away = []) => {
    let best = null;
    for (let x = zone.x0; x <= zone.x1; x += W0 * 0.02) for (let y = zone.y0; y <= zone.y1; y += H0 * 0.025) {
      const d = Math.hypot(x - from[0], y - from[1]);
      if (d < dmin * W0 || d > dmax * W0 || away.some(q => Math.hypot(q[0] - x, q[1] - y) < 0.18 * W0)) continue;
      const chip = { x: x - cs / 2, y: y - cs / 2, w: cs, h: cs };
      if (avoid.some(a => overlap(chip, a, 14)) || taken.some(a => overlap(chip, a, 6)) || body.some(a => overlap(chip, a, 4)) || !inSafe(ctx, chip, 0)) continue;
      const free = Math.min(...vis.map(p => Math.hypot(p.x - x, (p.y - y) * 1.7)));
      const sc = free - Math.abs(d - 0.26 * W0) * 0.25;
      if (!best || sc > best.sc) best = { x, y, sc, free };
    }
    return best && best.free > W0 * 0.08 ? best : null;
  };
  const best = o.arrow === false ? null : freeSpot([M.x, M.y], ctx.short ? 0.2 : 0.14, ctx.short ? 0.55 : 0.38);
  if (best) {
    const sx0 = M.x + (best.x > M.x ? 1 : -1) * M.w * 0.3, sy = M.y - M.h * 0.04;
    const dd = Math.hypot(best.x - sx0, best.y - sy), cut = o.chip === false ? 0 : (cs * 0.62) / dd;
    const col = o.arrowColor || b.accent;
    const arr = arrowLayer(ctx, [sx0, sy], [best.x - (best.x - sx0) * cut, best.y - (best.y - sy) * cut], col, gid, { bend: 0.24 });
    if (ok(arr)) {
      put(arr);
      if (o.chip !== false) {
        const chip = tacShape("marker", { name: "공간 칩", x: best.x - cs / 2, y: best.y - cs / 2, w: cs, h: cs, fill: col, label: o.chipText || "공간",
          stroke: { color: "#FFFFFF", width: Math.round(5 * scale) }, gid, glow: { on: true, color: col, size: Math.round(24 * scale), opacity: 1 } });
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
// 제목 아래(또는 위)에 주인공이 오도록 배경 놓기: 주인공 머리 = 제목 아래 + 여백 · 주인공 키가 남은 높이에 들어가게 확대
function frameUnder(ctx, hdBox, o = {}) {
  const m = mainBox(ctx.frame), mg = ctx.H * 0.035;
  if (!m) return frameLayer(ctx, { zoom: o.zoom || 1.05, target: o.target || [ctx.W * 0.5, ctx.H * 0.6] });
  const room = ctx.H - (hdBox.y + hdBox.h) - mg * 2, want = Math.min(o.subject || 0.5, room / ctx.H);
  const z = zoomFor(ctx, want, 1, o.max || 1.6);
  return frameLayer(ctx, { zoom: z, focus: [m[0] + m[2] / 2, m[1]], target: [ctx.W * (o.tx ?? 0.5), hdBox.y + hdBox.h + mg] });
}
function frameAbove(ctx, hdBox, o = {}) {  // 주인공 발 = 제목 위
  const m = mainBox(ctx.frame), mg = ctx.H * 0.02;
  if (!m) return frameLayer(ctx, { zoom: o.zoom || 1.05, target: [ctx.W * 0.5, ctx.H * 0.4] });
  const room = hdBox.y - ctx.H * 0.04, z = zoomFor(ctx, Math.min(o.subject || 0.45, room / ctx.H), 1, o.max || 1.6);
  return frameLayer(ctx, { zoom: z, focus: [m[0] + m[2] / 2, m[1] + m[3]], target: [ctx.W * (o.tx ?? 0.5), hdBox.y - mg] });
}
// 위 제목 롱폼: 주인공 머리가 제목에 덮이지 않는 배치를 차례로 — ① 두 줄 전체 폭 ② 짧으면 한 줄 ③ 주인공 반대쪽 절반에 두 줄
function headOverlap(ctx, bg, layers) {
  const hb = headBox(ctx.frame); if (!hb) return 0;
  const b = boxC(bg, hb, ctx.ar), a = areaOf(b); if (!a) return 0;
  return layers.reduce((s, l) => s + interArea(extentOf(l), b) / a, 0);
}
function topLayouts(ctx, ho, fo, left) {
  const c = ctx.copy, m = mainBox(ctx.frame), mx = m ? m[0] + m[2] / 2 : 0.5;
  const one = c.l2 && (c.l1 + c.l2).replace(/\s/g, "").length <= 12;
  const tries = [() => headline(ctx, { x: ctx.W * 0.075, y: ctx.H * 0.04, w: ctx.W * 0.85, h: ctx.H * 0.4 }, ho)];  // 왼쪽 위 모서리는 비움
  if (one) tries.push(() => headline(Object.assign({}, ctx, { copy: joinCopy(c) }), { x: ctx.W * 0.075, y: ctx.H * 0.04, w: ctx.W * 0.85, h: ctx.H * 0.22 }, Object.assign({}, ho, { size: Math.round(ctx.H * 0.2) })));
  tries.push(() => headline(ctx, { x: ctx.W * (mx > 0.5 ? 0.075 : 0.5), y: ctx.H * 0.05, w: ctx.W * 0.425, h: ctx.H * 0.5 }, Object.assign({}, ho, { align: mx > 0.5 ? "left" : "right" })));
  let best = null;
  for (const mk of tries) {
    const hd = mk(), bg = frameUnder(ctx, hd.box, fo), ov = headOverlap(ctx, bg, hd.layers);
    if (!best || ov < best.ov - 0.05) best = { hd, bg, ov };
    if (ov < 0.1) break;
  }
  return best;
}
// 쇼츠: 제목 아래 자기 칸에 장면 (위 끝은 검은 바탕으로 부드럽게 사라짐)
// — 가로 영상으로 만든 쇼츠는 화면 높이가 곧 장면 높이라 확대 없이는 머리를 제목 아래로 내릴 수 없음 (판정: 제목이 선수 머리를 덮음)
function framesBelow(ctx, hdBox, subject = 0.62) {
  const py = Math.round(hdBox.y + hdBox.h - ctx.H * 0.02), ph = ctx.H - py, sub = Object.assign({}, ctx, { H: ph });
  const back = L("shape", { name: "검은 바탕", x: 0, y: 0, w: ctx.W, h: ctx.H, fill: "#000000" });
  const f = frameLayer(sub, { zoom: zoomFor(sub, subject, 1, 2), target: [ctx.W * 0.5, ph * 0.5] });
  f.y += py; f.fade = { on: true, angle: -90, start: 0.86, end: 1 };  // 위 끝이 검은 바탕으로 부드럽게 (딱 잘린 경계 대신)
  return { back, f };
}
// 코치(얼굴 장면) 누끼 + 같은 영상의 공 다루는 장면 — 판정: 인터뷰 얼굴만 있으면 '무엇을 배우는지 장면이 없음'
function actionFrame(ctx) {
  const maxS = Math.max(1e-6, ...AI.frames.map(f => f.score || 0)), g = sceneGroups(AI.frames);
  return AI.frames.filter(f => f !== ctx.frame && f.kind !== "close" && (f.persons || []).length >= 2 && (f.text || 0) < TEXTY && g[f.t] !== g[ctx.frame.t]
    && !(f.flags || []).some(x => x === "bench" || x === "crowd")).sort((a, b) => frameQ(b, maxS) - frameQ(a, maxS))[0] || null;
}
function placeCut(ctx, m, hTarget, tx, ty, o = {}) {  // 누끼를 배경과 따로: 주인공 상자 키 = hTarget, 머리 위 가운데 = (tx, ty)
  const l = L("image", { name: "누끼", src: ctx.cut.cut, orig: ctx.cut.src, x: 0, y: 0, w: ctx.W, h: ctx.H, fit: "cover",
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
function joinCopy(c) {  // 두 줄을 한 줄로 (짧을 때 · 쪼살 '플랩 레벨업 바로 됩니다'처럼) — 강조 위치는 옮김
  if (!c.l2) return c;
  const t = `${c.l1} ${c.l2}`, e = c.emph ? (c.emph[0] === 0 ? [0, c.emph[1], c.emph[2]] : [0, c.emph[1] + c.l1.length + 1, c.emph[2] + c.l1.length + 1]) : null;
  return Object.assign({}, c, { l1: t, l2: "", emph: e });
}
const T_NEW = {
  long: {
    "전술 해설 (쪼살형)": { prior: 2.5, needs: { kinds: ["wide", "mid"], persons: 2, tactics: true }, fn: ctx => {
      const { hd, bg } = topLayouts(ctx, { style: "line", size: Math.round(ctx.H * 0.27), ratio: 0.52 }, { subject: 0.5, max: 2 }), ls = [bg];
      ls.push(shade("위 어둡게", 0, 0, ctx.W, ctx.H * 0.5, true, 0.6));
      const tac = tactics(ctx, bg, [hd.box]);
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 });
      ls.push(...tac.filter(l => l.shape === "ring")); if (cut) ls.push(cut); ls.push(...tac.filter(l => l.shape !== "ring"), ...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "아래 제목 + 네온 (쪼살형 2)": { prior: -3, needs: { kinds: ["wide", "mid"], persons: 2, tactics: true }, fn: ctx => {
      const c = ctx.copy, one = c.l2 && (c.l1 + c.l2).replace(/\s/g, "").length <= 11;
      const cx = one ? Object.assign({}, ctx, { copy: joinCopy(c) }) : ctx;
      const hd = headline(cx, { x: ctx.W * 0.045, y: ctx.H * 0.45, w: ctx.W * 0.91, h: ctx.H * 0.38 }, { style: "word", anchor: "bottom", size: Math.round(ctx.H * (one ? 0.2 : 0.23)), ratio: 0.6, emphScale: 1.15, emphFill: ctx.brand.colors.neon });  // 아래 끝 0.83H (재생시간 자리 위) · 강조는 네온(쪼살 '플랩 레벨업' 하늘색) — 노랑만 반복하면 템플릿 티
      const bg = frameAbove(ctx, hd.box, { subject: 0.5, max: 2 }), ls = [bg];
      ls.push(shade("아래 어둡게", 0, ctx.H * 0.48, ctx.W, ctx.H * 0.52, false, 0.78));
      const b = ctx.brand.colors, tac = tactics(ctx, bg, [hd.box], { second: true, pass: true, chip: false, arrowColor: b.hl, ringColor: b.hl, up: true });
      ls.push(...tac.filter(l => l.shape === "ring"), ...tac.filter(l => l.shape !== "ring"), ...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "옆 제목 (쪼살형 3)": { prior: -1.5, needs: { kinds: ["wide", "mid", "close"] }, fn: ctx => {  // 보조 상자는 롱폼에서 '군더더기'(판정) → 두 줄만 크게
      const m = mainBox(ctx.frame), fc = mainFace(ctx.frame);
      const cxs = fc ? fc[0] + fc[2] / 2 : m ? m[0] + m[2] / 2 : 0.6, right = cxs >= 0.42;  // 사람이 있는 쪽 반대편에 제목
      const hd = headline(ctx, { x: ctx.W * (right ? 0.07 : 0.4), y: ctx.H * 0.08, w: ctx.W * 0.53, h: ctx.H * 0.78 }, { style: "line", align: right ? "left" : "right", anchor: "middle", size: Math.round(ctx.H * 0.28), ratio: 0.58 });
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, ctx.frame.kind === "close" ? 0.9 : 0.72, 1, 2), target: [ctx.W * (right ? 0.74 : 0.26), ctx.H * 0.5] }), ls = [bg];
      ls.push(shade(right ? "왼쪽 어둡게" : "오른쪽 어둡게", right ? 0 : ctx.W * 0.4, 0, ctx.W * 0.6, ctx.H, right, 0.66, true));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "세로 장면 + 옆 제목": { prior: 0, needs: { vertical: true, noText: true }, fn: ctx => {  // 세로 영상으로 롱폼: 장면을 잘라 키우면 머리·몸이 잘림 → 세로 장면 통째로 한쪽 판에, 뒤는 같은 장면을 흐리게
      const m = mainBox(ctx.frame), right = !m || m[0] + m[2] / 2 >= 0.35, ph = Math.round(ctx.H * 0.9), pw = Math.round(ph * ctx.ar);
      const back = Object.assign(frameLayer(ctx, { zoom: 1, keep: false }), { name: "흐린 배경", blur: 26, bright: 45, vignette: 55 });
      const x0 = right ? ctx.W * 0.955 - pw : ctx.W * 0.045, y0 = (ctx.H - ph) / 2;
      const panel = L("image", { name: "배경", src: frameSrc(ctx.frame.t), x: x0, y: y0, w: pw, h: ph, fit: "cover", rot: right ? 2 : -2,
        outline: { on: true, color: "#FFFFFF", width: Math.round(ctx.W * 0.006) }, shadow: { on: true, color: "#000000", blur: 30, dx: 0, dy: 12, opacity: 0.7 } });
      if (ctx.frame.grade) panel.grade = Object.assign({}, ctx.frame.grade, { amt: 1 });
      const bx = right ? { x: ctx.W * 0.07, w: x0 - ctx.W * 0.1 } : { x: x0 + pw + ctx.W * 0.03, w: ctx.W * 0.955 - (x0 + pw + ctx.W * 0.03) };
      const hd = headline(ctx, { x: bx.x, y: ctx.H * 0.1, w: bx.w, h: ctx.H * 0.78 }, { style: "line", align: "left", anchor: "middle", size: Math.round(ctx.H * 0.27), ratio: 0.56 });
      const ls = [back, panel, ...hd.layers];
      const lg = logoLayer(ctx, [hd.box, bbox([panel])]); if (lg) ls.push(lg);
      return ls;
    } },
    "코치 + 경기 장면": { prior: -2.5, needs: { kinds: ["close"], cut: true, combo: true }, fn: ctx => {
      const act = actionFrame(ctx), m = mainBox(ctx.frame), actx = ctxFor(act, ctx.copy, "long", ctx.seed);
      const bg = Object.assign(frameLayer(actx, { zoom: zoomFor(actx, 0.45, 1, 1.6), target: [ctx.W * 0.42, ctx.H * 0.62] }), { name: "경기 장면 배경", bright: 82 });
      const cut = placeCut(ctx, m, ctx.H * 1.05, ctx.W * 0.77, ctx.H * 0.06);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.1, w: ctx.W * 0.5, h: ctx.H * 0.78 }, { style: "line", align: "left", anchor: "middle", size: Math.round(ctx.H * 0.28), ratio: 0.58 });
      return [bg, shade("왼쪽 어둡게", 0, 0, ctx.W * 0.66, ctx.H, true, 0.62, true), cut, ...hd.layers];
    } },
    "상자 제목 (자막형)": { prior: 2.5, needs: { kinds: ["close", "mid"] }, fn: ctx => {
      const m = mainBox(ctx.frame), fc = mainFace(ctx.frame);
      const cx = fc ? fc[0] + fc[2] / 2 : m ? m[0] + m[2] / 2 : 0.5, right = cx >= 0.4;  // 사람이 있는 쪽 반대편에 상자
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.95, 1.05, 1.8), target: [ctx.W * (right ? 0.68 : 0.32), ctx.H * 0.48] }), ls = [bg];
      ls.push(shade(right ? "왼쪽 어둡게" : "오른쪽 어둡게", right ? 0 : ctx.W * 0.45, 0, ctx.W * 0.55, ctx.H, right, 0.55, true));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      const hd = boxHead(ctx, ctx.W * (right ? 0.045 : 0.43), ctx.H * (right ? 0.86 : 0.8), ctx.W * 0.56, { size: ctx.H * 0.23, rot: -2 });
      if (!right) for (const l of hd.layers) l.x = ctx.W * 0.955 - l.w;
      ls.push(...hd.layers);
      const sl = ctx.brand.seriesOn ? seriesLabel(ctx, right ? ctx.W * 0.045 : ctx.W * 0.6, ctx.H * 0.06) : null; if (sl) ls.push(sl);
      const lg = logoLayer(ctx, [hd.box].concat(sl ? [bbox([sl])] : [])); if (lg) ls.push(lg);
      return ls;
    } },
    "강좌 시리즈 (쌈바형)": { prior: 3, needs: { cut: true }, fn: ctx => {
      const main = mainBox(ctx.frame), left = main ? main[0] + main[2] / 2 < 0.5 : false;
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.85, 1, 2), target: [ctx.W * (left ? 0.36 : 0.64), ctx.H * 0.5] }), ls = [bg];
      Object.assign(bg, { bright: 66, blur: 4, vignette: 55 });
      ls.push(shade("아래 어둡게", 0, ctx.H * 0.45, ctx.W, ctx.H * 0.55, false, 0.8));
      const cut = cutLayer(ctx, bg, { outline: ctx.brand.colors.hl, ow: 1, minH: 0.5, need: true }); if (cut) { cut.blur = 0; cut.bright = 100; ls.push(cut); }
      const al = left ? "right" : "left", bx = { x: ctx.W * 0.045, y: ctx.H * 0.38, w: ctx.W * 0.91, h: ctx.H * 0.45 };  // 재생시간 자리(아래 오른쪽) 위에서 끝
      const hd = headline(ctx, bx, { style: "word", align: al, anchor: "bottom", size: Math.round(ctx.H * 0.29), ratio: 0.42, emphScale: 1.15, grad: false });
      ls.push(...hd.layers);
      const sl = ctx.brand.seriesOn ? seriesLabel(ctx, ctx.W * 0.06, ctx.H * 0.06) : null; if (sl) ls.push(sl);  // 판정: 목록 크기에서 안 읽히는 작은 라벨 → 브랜드 키트에서 켤 때만
      const lg = logoLayer(ctx, [hd.box].concat(sl ? [bbox([sl])] : [])); if (lg) ls.push(lg);
      return ls;
    } },
    "리액션 클로즈업": { prior: -2, needs: { kinds: ["close"], sharp: 0.25 }, fn: ctx => {
      const fc = mainFace(ctx.frame) || ctx.frame.faces[0].box, z = clamp(0.5 / Math.max(0.12, fc[3]), 1, 1.6);
      const bg = frameLayer(ctx, { zoom: z, focus: [fc[0] + fc[2] / 2, fc[1] + fc[3] / 2], target: [ctx.W * 0.68, ctx.H * 0.46] }), ls = [bg];
      ls.push(shade("왼쪽 어둡게", 0, 0, ctx.W * 0.6, ctx.H, true, 0.7, true));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8, minH: 0.5 }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.12, w: ctx.W * 0.5, h: ctx.H * 0.76 }, { style: "word", align: "left", anchor: "middle", size: Math.round(ctx.H * 0.3), ratio: 0.6, emphScale: 1.15 });
      ls.push(...hd.layers);
      const em = emojiLayer(ctx, hd.big, headAvoid(ctx, bg).concat(hd.layers.filter(l => l !== hd.big).map(l => bbox([l]))), { k: 1.05 }); if (em) ls.push(em);
      return ls;
    } },
    "질문 훅 (JK형)": { prior: -2.5, needs: { noText: true }, fn: ctx => {
      const close = ctx.frame.kind === "close";  // 얼굴이 큰 장면은 얼굴을 오른쪽으로 보내고 제목은 왼쪽에만
      const ho = { style: "word", align: "left", size: Math.round(ctx.H * 0.26), ratio: 0.62, sw: 0.17, emphScale: 1.15 };
      const { hd, bg } = close ? { hd: headline(ctx, { x: ctx.W * 0.075, y: ctx.H * 0.08, w: ctx.W * 0.48, h: ctx.H * 0.6 }, ho), bg: frameLayer(ctx, { zoom: 1.05, target: [ctx.W * 0.72, ctx.H * 0.5] }) }
        : topLayouts(ctx, ho, { subject: 0.5 }, true);
      const ls = [bg];
      ls.push(close ? shade("왼쪽 어둡게", 0, 0, ctx.W * 0.6, ctx.H, true, 0.6, true) : shade("위 어둡게", 0, 0, ctx.W, ctx.H * 0.42, true, 0.5));
      ls.push(...hd.layers);
      return ls;
    } },
    "인물 + 오른쪽 제목 (해주호형)": { prior: -5, needs: { kinds: ["mid", "close"] }, fn: ctx => {
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.9, 1.05, 2), target: [ctx.W * 0.27, ctx.H * 0.5] }), ls = [bg];
      ls.push(shade("오른쪽 어둡게", ctx.W * 0.38, 0, ctx.W * 0.62, ctx.H, false, 0.72, true));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.46, y: ctx.H * 0.1, w: ctx.W * 0.5, h: ctx.H * 0.7 }, { style: "word", align: "center", anchor: "middle", size: Math.round(ctx.H * 0.3), ratio: 0.6, rot: -2, emphScale: 1.15 });
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
      const hd = headline(ctx, { x: ctx.W * 0.05, y: ctx.H * 0.5, w: ctx.W * 0.9, h: ctx.H * 0.44 }, { style: "word", align: "left", anchor: "bottom", size: Math.round(ctx.H * 0.27), ratio: 0.48, grad: false });
      ls.push(noL, xm, yesL, scr, ...hd.layers);
      return ls;
    } },
  },
  short: {
    // 쇼츠 제목: 위쪽 안전 영역 폭을 거의 다 쓰는 3단 (첫 줄 흰 · 큰 줄 노랑 · 어두운 상자 속 보조 문구) — 레퍼런스 'V자 어려우면 / 무조건 봐 / 1분안에 알려줄게'
    "쇼츠 · 3단 제목": { prior: 2, needs: { sub: true }, fn: ctx => {
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.075, w: ctx.W * 0.76, h: ctx.H * 0.3 }, { style: "line", size: Math.round(ctx.W * 0.25), ratio: 0.56, sub: true, subRatio: 0.36 });
      const { back, f } = framesBelow(ctx, hd.box), ls = [back, f];
      const cut = cutLayer(ctx, f, { outline: "#FFFFFF", ow: 0.8, minH: 0.32 }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      return ls;
    } },
    "쇼츠 · 전술": { prior: 3.5, needs: { kinds: ["wide", "mid"], persons: 2, tactics: true }, fn: ctx => {
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.075, w: ctx.W * 0.76, h: ctx.H * 0.26 }, { style: "line", size: Math.round(ctx.W * 0.25), ratio: 0.56 });
      const { back, f } = framesBelow(ctx, hd.box, 0.4), ls = [back, f];
      const tac = tactics(ctx, f, [hd.box, { x: ctx.W * 0.84, y: ctx.H * 0.4, w: ctx.W, h: ctx.H }, { x: 0, y: ctx.H * 0.8, w: ctx.W, h: ctx.H }]);
      const cut = cutLayer(ctx, f, { outline: "#FFFFFF", ow: 0.8, minH: 0.32 });
      ls.push(...tac.filter(l => l.shape === "ring")); if (cut) ls.push(cut); ls.push(...tac.filter(l => l.shape !== "ring"), ...hd.layers);
      return ls;
    } },
    "쇼츠 · 누끼 크게": { prior: 0, needs: { cut: true }, fn: ctx => {
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.08, w: ctx.W * 0.76, h: ctx.H * 0.27 }, { style: "word", size: Math.round(ctx.W * 0.22), ratio: 0.58, emphScale: 1.15 });
      const z = zoomFor(ctx, 0.55, 1, 1.8), m = mainBox(ctx.frame);
      const o = m ? { zoom: z, focus: [m[0] + m[2] / 2, m[1]], target: [ctx.W * 0.46, hd.box.y + hd.box.h + ctx.H * 0.04] } : { zoom: z, target: [ctx.W * 0.46, ctx.H * 0.6] };
      const bg = frameLayer(ctx, o);
      const ls = [Object.assign(frameLayer(ctx, o), { name: "흐린 배경", blur: 18, bright: 55, vignette: 60 })];
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 1, minH: 0.4, need: true }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      return ls;
    } },
    "쇼츠 · 코치 + 경기 장면": { prior: -1.5, needs: { kinds: ["close"], cut: true, combo: true }, fn: ctx => {
      const act = actionFrame(ctx), m = mainBox(ctx.frame), actx = ctxFor(act, ctx.copy, "short", ctx.seed);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.075, w: ctx.W * 0.76, h: ctx.H * 0.26 }, { style: "line", size: Math.round(ctx.W * 0.25), ratio: 0.56 });
      const { back, f } = framesBelow(actx, hd.box, 0.4); f.name = "경기 장면 배경";
      const cut = placeCut(ctx, m, ctx.H * 0.6, ctx.W * 0.45, ctx.H * 0.44);
      return [back, f, shade("아래 어둡게", 0, ctx.H * 0.55, ctx.W, ctx.H * 0.45, false, 0.5), cut, ...hd.layers];
    } },
    "쇼츠 · 레터박스 질문": { prior: 3.5, needs: { noText: true }, fn: ctx => {
      const ls = [L("shape", { name: "검은 바탕", x: 0, y: 0, w: ctx.W, h: ctx.H, fill: "#000000" })];
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.075, w: ctx.W * 0.76, h: ctx.H * 0.22 }, { style: "word", align: "center", anchor: "bottom", size: Math.round(ctx.W * 0.2), ratio: 0.6, sw: 0.14, emphScale: 1.15 });
      const py = hd.box.y + hd.box.h + ctx.H * 0.025, ph = ctx.H - py, sub = { ...ctx, W: ctx.W, H: ph };  // 위 검은 띠에 제목, 아래는 끝까지 사진
      const f = frameLayer(sub, { zoom: zoomFor(sub, 0.7, 1, 1.6), target: [ctx.W * 0.5, ph * 0.45] }); f.y += py; ls.push(f);
      ls.push(...hd.layers);
      return ls;
    } },
    "쇼츠 · 흰 띠 제목 (해주호형)": { prior: 4, needs: { noText: true }, fn: ctx => {  // 위 흰 띠 + 검은 굵은 글자 + 큰 줄은 노란 형광펜 (풋살해주호 쇼츠)
      const ls = [L("shape", { name: "흰 띠", x: 0, y: 0, w: ctx.W, h: ctx.H, fill: "#FFFFFF" })];
      const c = ctx.copy, b = ctx.brand.colors, bigI = c.l2 ? (c.emph ? c.emph[0] : 1) : 0;
      const mk = (t, size, hl, name) => { const l = L("text", Object.assign({ text: t, name }, tStyle(ctx, size, { fill: "#111111", sw: 0, noShadow: true, align: "center" }),
        hl ? { box: { on: true, color: b.hl, pad: Math.round(size * 0.1), radius: Math.round(size * 0.06) } } : {})); l.runs = digitRuns(t, l.font); fitText(l); fitW(l, ctx.W * 0.76); return l; };
      const lines = c.l2 ? [mk(c.l1, Math.round(ctx.W * (bigI === 0 ? 0.155 : 0.095)), bigI === 0, bigI === 0 ? "제목 큰 줄" : "제목 작은 줄"), mk(c.l2, Math.round(ctx.W * (bigI === 1 ? 0.155 : 0.095)), bigI === 1, bigI === 1 ? "제목 큰 줄" : "제목 작은 줄")] : [mk(c.l1, Math.round(ctx.W * 0.15), true, "제목 큰 줄")];
      let y = ctx.H * 0.085;
      for (const l of lines) { l.x = ctx.W * 0.45 - l.w / 2; l.y = y; y += l.h + ctx.H * 0.012; }
      const py = y + ctx.H * 0.02, ph = ctx.H - py, sub = { ...ctx, H: ph };  // 사진은 아래 끝까지 (빈 흰 바닥이 남지 않게)
      const f = frameLayer(sub, { zoom: zoomFor(sub, 0.75, 1, 1.6), target: [ctx.W * 0.5, ph * 0.5] }); f.y += py; ls.push(f);
      ls.push(...lines);
      return ls;
    } },
    "쇼츠 · 얼굴 + 아래 제목": { prior: -4, needs: { kinds: ["close"] }, fn: ctx => {  // 인터뷰처럼 얼굴이 위쪽에 큰 장면: 얼굴은 위에 두고 제목은 아래 안전 영역 끝에
      const bg = frameLayer(ctx, { zoom: 1.05, target: [ctx.W * 0.47, ctx.H * 0.3] }), ls = [bg];
      ls.push(shade("아래 어둡게", 0, ctx.H * 0.38, ctx.W, ctx.H * 0.62, false, 0.92));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.44, w: ctx.W * 0.76, h: ctx.H * 0.31 }, { style: "line", anchor: "bottom", size: Math.round(ctx.W * 0.25), ratio: 0.58, sub: true, subRatio: 0.34 });
      ls.push(...hd.layers);
      return ls;
    } },
    "쇼츠 · 상자 제목": { prior: 2.5, needs: {}, fn: ctx => {  // 위에 상자 제목 · 사진은 그 아래 자기 칸에 (머리를 가리지 않게)
      const hd = boxHead(ctx, ctx.W * 0.07, ctx.H * 0.3, ctx.W * 0.72, { size: ctx.W * 0.18, ratio: 0.6 });
      for (const l of hd.layers) l.y -= Math.min(0, bbox(hd.layers).y - ctx.H * 0.08);
      const { back, f } = framesBelow(ctx, bbox(hd.layers)), ls = [back, f];
      const cut = cutLayer(ctx, f, { outline: "#FFFFFF", ow: 0.8 }); if (cut) ls.push(cut);
      ls.push(...hd.layers);
      return ls;
    } },
  },
};
// 예전 템플릿(TPL)은 fn(장면 시각들, 첫 줄, 둘째 줄, 누끼)이라 어댑터로 감싸 같이 씀 (예전 후보 화면·e2e 는 그대로)
function legacyTpl(fmt) {
  return Object.fromEntries(Object.entries(TPL[fmt]).map(([k, fn]) => [k, { legacy: true, needs: {}, fn: ctx => {
    const ts = [ctx.frame.t, ...ctx.frames.filter(f => f.t !== ctx.frame.t).map(f => f.t)];
    return fn(ts, ctx.copy.l1, ctx.copy.l2, null);  // 예전 템플릿은 배경 자리가 달라 자동 누끼를 겹치지 않음
  } }]));
}
const allTpl = fmt => Object.assign({}, T_NEW[fmt], legacyTpl(fmt));
function wholeBody(f) {  // 주인공이 위아래로 잘리지 않고 보이는지 (발만 나온 장면에 누끼·전술을 넣지 않게)
  const m = mainBox(f);
  if (f.faces && f.faces.length && f.kind === "close") return true;
  return !!m && m[1] > 0.015 && m[3] >= 0.18 && m[3] <= 0.8;
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
  if (n.combo && !(mainBox(f) && mainFace(f) && mainFace(f)[3] >= 0.12 && actionFrame(ctx))) return false;  // 얼굴이 크게 보이는 사람(코치·인터뷰)만 — 뒷모습 선수를 다른 장면에 붙이면 유령처럼 보임
  if (n.ox && !(ctx.copy.ox && ctx.copy.ox.length === 2)) return false;
  return true;
}
function buildDoc(name, t, ctx) {
  const layers = t.fn(ctx).filter(Boolean).map(normLayer);
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
function strongStroke(l) {  // 대비 통과: 바깥 획 ≥ 글자 크기 12% · 또는 상자·광선
  if (l.box.on || (l.glow.on && l.glow.size >= 20) || (l.outline.on && l.outline.width >= 4)) return true;
  const m = textLayout(ctx, l); return m.lines.flatMap(ln => ln.spans).filter(s => s.t.trim()).every(s => Math.max(s.st.s1w, s.st.s2w) >= s.st.size * 0.12);
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
  if (fl.includes("lesson")) q = Math.min(1, q * 1.15);
  if ((f.text || 0) >= TEXTY) q *= 0.8;
  if ((f.blur ?? 0) > 0.35) q *= 0.8;
  return q;
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
    if (short && l.type === "text" && (e.x < Wd * 0.06 - 2 || e.x + e.w > Wd * 0.84 + 2 || e.y < Hd * 0.07 - 2 || e.y + e.h > Hd * 0.76 + 2)) gates.push(`쇼츠 안전 영역 밖: ${lname(l)}`);
    if (short && e.x + e.w > Wd * 0.86 && e.y + e.h > Hd * 0.42 && e.y < Hd * 0.87) gates.push(`쇼츠 버튼 자리: ${lname(l)}`);
    if (short && e.y + e.h > Hd * 0.8) gates.push(`쇼츠 제목 자리: ${lname(l)}`);
  }
  // 2) 작게 봤을 때 글자 높이 (헤드라인 ≥ 12px, 나머지 글자 ≥ 8px)
  const hpx = heads.length ? Math.max(...heads.map(l => glyphPx(l, Wd, small))) : 0;
  if (hpx < 12) gates.push(`작게 보면 안 읽힘 (${hpx.toFixed(1)}px)`);
  for (const l of texts) if (glyphPx(l, Wd, small) < 8 && !/시리즈|채널/.test(l.name || "")) gates.push(`작은 글자: ${lname(l)}`);
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
  // 가중합
  const fr = frameQ(f, meta.maxFrame);
  const read = clamp((hpx - 9) / (17 - 9), 0, 1) * (weak.length ? 0.6 : 1);
  const big = heads.find(l => /큰/.test(l.name || "")) || heads[0], sm = heads.find(l => /작은/.test(l.name || ""));
  const ratio = big && sm ? big.size / sm.size : big ? 1.8 : 1;
  const hier = clamp((ratio - 1) / 0.8, 0, 1) * 0.7 + (big && big.runs && big.runs.some(r => r.fill) ? 0.3 : (big && big.fill2 ? 0.25 : 0.1));
  const occl = clamp(1 - faceHit * 4 - Math.min(1, headHit) * 1.5, 0, 1);
  const cp = clamp(((meta.copy.score || 0) - meta.copyMin) / Math.max(1e-6, meta.copyMax - meta.copyMin), 0, 1);
  const safeM = ui.every(l => inSafe({ short, W: Wd, H: Hd }, extentOf(l), 0)) ? 1 : 0.5;
  const area = hb.reduce((a, b) => a + b.w * b.h, 0) / (Wd * Hd), comp = area >= 0.12 && area <= 0.42 ? 1 : area < 0.12 ? area / 0.12 : clamp(1 - (area - 0.42) * 3, 0, 1);
  let s = 100 * (0.22 * fr + 0.22 * read + 0.14 * hier + 0.14 * occl + 0.12 * cp + 0.08 * safeM + 0.08 * comp);
  // 감점 (판정 1회차 이유: 주인공 작음 61 · 머리·몸 잘림 58 · 누끼 어색 29 · 박힌 글자 9)
  const sh = geo && mainBox(f) ? clipTo(boxC(geo, mainBox(f), ar), { x: 0, y: 0, w: Wd, h: Hd }).h / Hd : 0.5;
  if (bg && mainBox(f) && f.kind !== "close" && sh < (short ? 0.16 : 0.2) && !tac.length) s -= 8;
  const cut = doc.layers.find(l => l.name === "누끼" && !l.hidden);
  if (cut && meta.cutQ != null && meta.cutQ < 0.7) s -= 10;
  const bgx = doc.layers.find(l => l.type === "image" && l.name === "배경");  // 이 장면이 그대로 깔린 배경 (코치+경기 장면 합성은 다른 장면이라 뺌)
  if (bgx && (f.text || 0) >= TEXTY) {
    const vt = f.tboxes ? visibleText(bgx, f, ar, Wd, Hd) : f.text;
    if (vt > 0.008) s -= (bgx.blur || 0) >= Wd * 0.025 ? 4 : (bgx.blur || 0) > 0 ? 12 : 25;
  }
  // 공이 화면에 보이고 글자에 안 가림 (판정 252장: 공이 보이는 장면 6.11 vs 5.66 — 가장 큰 차이) · 공 다루는 순간
  if (f.ball && geo) { const bb = boxC(geo.name === "누끼" && bg ? bg : geo, f.ball, ar), cx = bb.x + bb.w / 2, cy = bb.y + bb.h / 2;
    if (cx > 0 && cx < Wd && cy > 0 && cy < Hd && !cover.some(c => cx > c.x && cx < c.x + c.w && cy > c.y && cy < c.y + c.h)) s += 6; }
  if ((f.flags || []).includes("lesson")) s += 3;
  if (meta.legacy) s -= 15;  // 예전 템플릿은 레퍼런스형보다 한 단계 아래 (새 템플릿이 안 맞을 때만 나오게)
  s += meta.prior || 0;     // 템플릿 가산점: 클로드 블라인드 판정(롱폼·쇼츠 252장) 템플릿 평균으로 맞춤 — 평균 6.3 → +4 … 5.1 → −5
  if (tac.length) s += 2;   // 전술 그래픽 (쪼살형) 가산
  const raw = s;            // 고르기는 100 을 넘는 차이도 씀 (예전엔 많은 후보가 100 에 붙어 구별이 안 됐음)
  s = Math.min(100, s);
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
  return { score: Math.round(s * 10) / 10, raw: gates.length ? s : raw, gates, why: why.slice(0, 2), weak, hpx };
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
function sceneGroups(frames) {  // 같은 화면(지문 ≤ 10) · 같은 사람 클로즈업(얼굴 장면끼리 지문 ≤ 20) → 장면 묶음 번호
  const g = {};
  for (const f of frames) {
    const same = frames.find(h => h !== f && g[h.t] !== undefined && (hamming(h.hash, f.hash) <= 10 || (h.kind === "close" && f.kind === "close" && hamming(h.hash, f.hash) <= 20)));
    g[f.t] = same ? g[same.t] : f.t;
  }
  return g;
}
// 쓸 수 있는 장면: 박힌 글자가 크면 주인공 누끼(품질 통과)가 있어야 새 템플릿이 씀 (없으면 흐린 배경만 남아 후보가 0개)
const usableFrame = f => (f.text || 0) < TEXTY || !!(AI.cuts[String(f.t)] && (!AI.cuts[String(f.t)].q || AI.cuts[String(f.t)].q.ok));
function aiFrames(n = 6) {
  const maxS = Math.max(1e-6, ...AI.frames.map(f => f.score || 0));
  const fq = f => frameQ(f, maxS) * (usableFrame(f) ? 1 : 0.15);  // 못 쓰는 장면(후보 0개)은 뒤로 — 클로드가 모든 장면을 낮게 줘도 (글자 박힌 영상) 누끼 있는 장면을 남김
  let fs = [...AI.frames].sort((a, b) => fq(b) - fq(a));
  if (AI.pick != null) { const f = fs.find(x => x.t === AI.pick); return f ? [f] : fs.slice(0, 1); }
  const sharp = fs.filter(f => (f.blur ?? 0) <= 0.45); if (sharp.length >= 3) fs = sharp;
  // 클로드가 '주제와 무관'(앵커·잡지·로고 1~3점)이라 한 장면은 좋은 장면(5점 넘게)이 3장 넘으면 뺌 — 다양성 채우기로 끌려 들어오지 않게 (판정 9회차)
  const bestAi = Math.max(-1, ...fs.map(f => f.ai ?? -1));
  if (bestAi >= 5) { const keep = fs.filter(f => f.ai == null || f.ai > 3 || bestAi - f.ai < 3); if (keep.length >= 3) fs = keep; }
  const anyP = fs.some(f => (f.persons || []).length);
  if (anyP) { const np = fs.filter(f => !(f.persons || []).length); fs = fs.filter(f => (f.persons || []).length).concat(np.slice(0, 1)); }
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
  for (const f of fs) add(f, 1);
  for (const f of fs) add(f, 2);
  for (const f of fs) if (out.length < n && !out.includes(f)) out.push(f);
  return out.slice(0, n).sort((a, b) => fq(b) - fq(a));
}
function aiCopies(n = 5, seed = 0) {
  if (AI.copySel) return [AI.copySel];
  const cs = [...AI.copy].sort((a, b) => b.score - a.score);
  const ai = cs.filter(c => c.src === "ai"), rule = cs.filter(c => c.src !== "ai");
  const mixd = ai.length ? [...ai.slice(0, 4), ...rule.slice(0, 1), ...ai.slice(4), ...rule.slice(1)] : cs;  // 클로드 문구가 있으면 앞에 (규칙 문구 1개는 섞음 — 판정: 규칙 틀 '무조건 봐'는 흔한 문구)
  if (!seed) return mixd.slice(0, n);
  const k = (seed * 2) % Math.max(1, mixd.length);  // 다시 추천: 다른 문구가 앞에 오게 돌림
  return [...mixd.slice(k), ...mixd.slice(0, k)].slice(0, n);
}
function recommend(fmt, n = 6, seed = 0, avoidKeys = new Set()) {
  const frames = aiFrames(7), copies = aiCopies(7, seed), cands = [];  // 장면 7 × 문구 7 — 6개가 서로 다른 장면·문구가 되게 (판정: 'A와 같은 장면·문구' 21/84)
  const maxFrame = Math.max(1e-6, ...AI.frames.map(f => f.score || 0)), cScores = AI.copy.map(c => c.score || 0);
  const copyMin = Math.min(...cScores, 0), copyMax = Math.max(...cScores, 1);
  const build = tpls => { for (const f of frames) for (const c of copies) for (const [name, t] of Object.entries(tpls)) {
    if (t.legacy && (f.text || 0) >= TEXTY) continue;  // 예전 템플릿은 박힌 글자를 피하지 못함
    const cx = ctxFor(f, c, fmt, seed + Math.round(f.t * 10)); if (!fits(t, cx)) continue;
    let doc; try { doc = buildDoc(NAME, t, cx); } catch (e) { console.warn("템플릿 실패", name, e); continue; }
    if (t.needs && t.needs.tactics && !doc.layers.some(l => l.shape === "arrow2")) continue;  // 전술 템플릿인데 화살표 놓을 자리가 없으면 다른 템플릿에 양보
    if (t.needs && t.needs.cut && !doc.layers.some(l => l.name === "누끼")) continue;  // 누끼 템플릿인데 주인공이 작아 누끼를 안 넣었으면 (흐린 배경만 남음) 양보
    if (doc._texty && !t.legacy) continue;  // 박힌 글자가 보이는데 주인공 누끼가 없음 → 흐리기만 하면 주인공도 흐려짐 (판정 최저 3.4~4.5점)
    delete doc._texty;
    const cq = AI.cuts[String(f.t)] && AI.cuts[String(f.t)].q ? AI.cuts[String(f.t)].q.q : null;
    const meta = { frame: f, copy: c, legacy: !!t.legacy, prior: t.prior || 0, maxFrame, copyMin, copyMax, cutQ: cq }, sc = scoreDoc(doc, meta);
    const key = `${name}|${f.t}|${c.l1}/${c.l2}`;
    cands.push(Object.assign({ tpl: name, t: f.t, copy: c, doc, key, legacy: !!t.legacy }, sc, { base: sc.raw - (avoidKeys.has(key) ? 25 : 0) }));
  } };
  build(T_NEW[fmt]);
  const ok0 = cands.filter(x => !x.gates.length);  // 예전 템플릿은 새 템플릿만으로 모자랄 때만 만듦 (다시 추천 4초 안 — 판정 B8)
  if (ok0.length < n || new Set(ok0.map(x => x.tpl)).size < 3 || new Set(ok0.map(x => x.t)).size < Math.min(3, frames.length)) build(legacyTpl(fmt));
  cands.sort((a, b) => b.base - a.base);
  for (const x of cands.slice(0, 20)) if (x.weak.length && !x.gates.length) { const r = preciseContrast(x.doc, x.weak); if (r < 4.5) { x.gates.push(`대비 ${r.toFixed(1)}:1`); x.score = Math.min(x.score, 40); x.base = Math.min(x.base, 40); } }
  // 게이트 통과한 것만 · 새(레퍼런스형) 템플릿만으로 6개(3종 이상)를 채울 수 있으면 예전 템플릿은 빼고, 모자라면 예전 템플릿으로 채움
  const clean = cands.filter(x => !x.gates.length), fresh = clean.filter(x => !x.legacy);
  const pool = fresh.length >= n && new Set(fresh.map(x => x.tpl)).size >= 3 && new Set(fresh.map(x => x.t)).size >= Math.min(3, frames.length) ? fresh : clean.length >= n ? clean : cands;
  // 장면은 지문(dHash)이 비슷하면 같은 장면으로 봄 (시각만 다른 같은 화면·같은 사람 클로즈업이 여러 번 나오지 않게)
  const scene = sceneGroups(frames);
  for (const x of pool) x.sc = scene[x.t] ?? x.t;
  const l2 = x => x.copy.l2 || x.copy.l1;
  const sim = (a, b) => (a.tpl === b.tpl ? 0.5 : 0) + (a.sc === b.sc ? 0.3 : 0) + (a.copy === b.copy ? 0.2 : l2(a) === l2(b) ? 0.1 : 0);
  const out = [];
  const distinct = k => new Set(pool.map(x => (k === "l2" ? l2(x) : x[k]))).size;
  // 같은 종류 상한 = 6 ÷ 종류 수 (올림) — 장면이 둘뿐인 영상은 장면당 3개까지, 문구는 7개면 1번씩
  const capOf = k => Math.max(1, Math.ceil(n / Math.max(1, distinct(k))));
  const tplCap = capOf("tpl"), scCap = capOf("sc"), copyCap = Math.max(capOf("copy"), capOf("l2"));
  // 꼭 채울 장면 종류는 '괜찮은 장면'(가장 좋은 장면 품질의 절반 넘게)만 셈 — 인터뷰 한 사람뿐인 영상에서 나쁜 장면을 억지로 넣지 않게
  const fqT = {}; for (const f of frames) fqT[f.t] = frameQ(f, maxFrame);
  const qTop = Math.max(1e-6, ...Object.values(fqT)), goodSc = new Set(pool.filter(x => (fqT[x.t] ?? 0) >= 0.5 * qTop).map(x => x.sc)).size;
  const need = { tpl: Math.min(4, distinct("tpl")), sc: Math.min(4, Math.max(1, goodSc)), copy: Math.min(5, distinct("copy")) };
  while (out.length < n && out.length < pool.length) {
    const left = n - out.length, have = k => new Set(out.map(x => x[k])).size;
    let best = null, bv = -1e9;
    for (const x of pool) {
      if (out.includes(x)) continue;
      // 다양성 강제: 남은 자리로 채워야 할 종류가 있으면 새 종류만
      if ((["tpl", "sc", "copy"]).some(k => need[k] - have(k) >= left && out.some(o => o[k] === x[k]))) continue;
      if (out.filter(o => o.tpl === x.tpl).length >= tplCap || out.filter(o => o.sc === x.sc).length >= scCap) continue;  // 같은 템플릿·같은 장면은 1개 (종류가 모자라면 2개)
      if (out.some(o => o.tpl === x.tpl && o.sc === x.sc)) continue;  // 같은 템플릿 + 같은 장면은 문구만 다른 거의 같은 그림
      if (out.filter(o => l2(o) === l2(x)).length >= copyCap || out.filter(o => o.copy === x.copy).length >= copyCap) continue;  // 같은 둘째 줄('무조건 봐')·같은 문구는 1번 (모자라면 2번)
      const v = 0.7 * x.base - 0.3 * 100 * Math.max(0, ...out.map(o => sim(o, x)));
      if (v > bv) { bv = v; best = x; }
    }
    if (!best) {  // 조건을 다 지킬 수 없으면 덜 겹치는 것 (같은 문구·같은 템플릿+장면을 먼저 피함)
      const dup = x => (out.some(o => o.tpl === x.tpl && o.sc === x.sc) ? 2 : 0) + (out.some(o => o.copy === x.copy) ? 1 : 0) + (out.some(o => l2(o) === l2(x)) ? 0.5 : 0);
      for (const x of pool) { if (out.includes(x)) continue; const v = x.base - 40 * dup(x); if (!best || v > bv) { bv = v; best = x; } }
    }
    if (!best) break;
    out.push(best);
  }
  return out;
}

/* ----- 분석 받기 (다 돼 있으면 바로, 아니면 작업으로: 장면·선수 찾기 → 누끼 → 문구) ----- */
async function loadBrand() { try { const j = await (await fetch("/api/thumb/brand")).json(); if (j.ok) AI.brand = j.brand; } catch (e) {} return AI.brand; }
function setAIStat(t) { const e = $("aiStat"); if (e) e.textContent = t; }
async function ensureAnalysis(force) {
  if (AI.loaded && !force) return true;
  setAIStat("장면·선수 찾는 중…");
  for (let tries = 0; tries < 120; tries++) {
    const j = await post("/api/thumb/analyze", { name: NAME });
    let r = j;
    if (j.job) r = await watchJob();
    else if (!j.ok) { setAIStat("다른 작업(장면 추출 등)이 끝나면 저절로 이어서 만들어요… 기다려 주세요"); await new Promise(res => setTimeout(res, 3000)); continue; }  // 409: 다시 누를 필요 없음
    if (!r || !r.frames) { setAIStat("분석하지 못했어요 · 작업 기록을 확인해 주세요"); return false; }
    AI.frames = r.frames; AI.cuts = r.cuts || {}; AI.copy = (r.copy && r.copy.items) || []; AI.topics = (r.copy && r.copy.topics) || []; AI.ai = !!(r.copy && r.copy.ai);
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
    await loadBrand();
    if (!AI.frames.length) { setAIStat("쓸 만한 장면을 찾지 못했어요"); return null; }
    setAIStat("조합하고 점수 매기는 중…");
    await new Promise(r => setTimeout(r, 0));
    const t0 = performance.now(), prev = new Set(seed ? AI.results.map(x => x.key) : []);
    AI.results = recommend(AUTO_FMT, 6, seed, prev); AI.seed = seed; AI.ab = new Set();
    for (const x of AI.results) for (const l of x.doc.layers) if (l.type === "image" && l.src) img(l.src);
    renderAI();
    setAIStat(`${AI.results.length}개 · ${((performance.now() - t0) / 1000).toFixed(1)}초 · 눌러서 편집하거나 A/B 에 담아 보세요`);
    return AI.results;
  } finally { AI.busy = false; done(); }
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
  renderAICards();
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
  const r = await watchJob(); if (!r) return;
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
    out.push({ tpl: x.tpl, t: x.t, copy: { l1: x.copy.l1, l2: x.copy.l2, sub: x.copy.sub, src: x.copy.src, score: x.copy.score, pid: x.copy.pid }, score: x.score, why: x.why, gates: x.gates, hpx: x.hpx,
      frame: AI.frames.find(f => f.t === x.t), doc: clone(x.doc), data });
  }
  return { ok: true, items: out, frames: AI.frames.length, copies: AI.copy.length };
};
