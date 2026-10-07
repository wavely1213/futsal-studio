
/* ---------- AI 추천 썸네일: 분석(장면·선수·공·표정·누끼·문구) × 레퍼런스형 템플릿 → 점수 → 서로 다른 6개 ---------- */
// 픽셀은 언제나 이 화면의 renderDoc 하나로 그림 (편집기·카드·내보내기·개발용 판정 모두 같은 그림). 백엔드는 숫자만 줌 (thumb.analyze).
const BRAND_DEF = { logo: "", logoPos: "tr", colors: { hl: "#FFE14D", hl2: "#FFFFFF", accent: "#FF3B30", neon: "#00D1FF", box: "#111111" }, font: "Black Han Sans", series: "풋사관 강좌", seriesOn: false, handle: "@풋살사관학교", apply: true };
const AI = { frames: [], cuts: {}, copy: [], topics: [], brand: null, seed: 0, results: [], busy: false, pick: null, ab: new Set(), copySel: null, loaded: false, ai: false };
const TEXTY = 0.05;  // 장면에 박힌 큰 글자 넓이가 이만큼 넘으면 배경을 흐림 (thumb.text_area)
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
// 장면 한 점(u, v)이 캔버스 (tx, ty)에 오도록 배경을 z 배로 키워 놓음 (캔버스를 늘 덮게)
function frameLayer(ctx, o = {}) {
  const f = ctx.frame, W0 = ctx.W, H0 = ctx.H, ar = ctx.ar, z = clamp(o.zoom || 1, 1, maxZoom(ctx));  // 1 보다 작으면 캔버스를 다 못 덮음
  const l = L("image", Object.assign({ name: "배경", src: frameSrc(f.t), x: 0, y: 0, w: W0 * z, h: H0 * z, fit: "cover" }, o.extra || {}));
  if (f.grade && o.grade !== false) l.grade = Object.assign({}, f.grade, { amt: o.gradeAmt ?? 1 });
  if ((f.text || 0) >= TEXTY) Object.assign(l, { blur: Math.round(W0 * 0.009), bright: 70 });  // 장면에 큰 글자가 박혀 있으면 제목과 다투지 않게 흐리고 어둡게
  if (ctx.band === "bottom" && o.crop !== false) l.cropB = clamp(1 - (f.bandY || 0.76) + 0.01, 0.05, 0.3);  // 영상에 박힌 자막·방송 띠는 잘라냄
  if (ctx.band === "top" && o.crop !== false) l.cropT = clamp((f.bandY || 0.2) + 0.01, 0.05, 0.3);
  const [u, v] = o.focus || ctx.focus, [tx, ty] = o.target || [W0 / 2, H0 / 2], r = imgRect(l, ar);
  // 원하는 자리로: 레이어 x/y 를 옮기되 캔버스를 벗어나지 않게 (확대했을 때만 움직일 여유가 있음)
  const px = r.dx + ((l.flipX ? 1 - u : u) - (l.cropL || 0)) / r.cw * r.dw, py = r.dy + (v - (l.cropT || 0)) / r.ch * r.dh;
  l.x = clamp(tx - px, W0 - l.w, 0); l.y = clamp(ty - py, H0 - l.h, 0);
  // 가로로 남는 그림이 있으면(쇼츠에 가로 장면) 초점(fx)으로 맞춤
  if (r.dw > l.w + 1) l.fx = clamp((tx - l.x - ((u - (l.cropL || 0)) / r.cw) * r.dw) / (l.w - r.dw), 0, 1);
  if (r.dh > l.h + 1) l.fy = clamp((ty - l.y - ((v - (l.cropT || 0)) / r.ch) * r.dh) / (l.h - r.dh), 0, 1);
  return l;
}
function maxZoom(ctx) {  // 원본 장면(가로 최대 1920)을 1.6배 넘게 키우지 않게 (세로 영상으로 롱폼을 만들 때 뭉개지지 않게)
  const srcW = Math.min(1920, INFO.width || 1920), dw = ctx.W / ctx.H > ctx.ar ? ctx.W : ctx.H * ctx.ar;
  return Math.max(1, 1.6 / (dw / srcW));
}
function zoomFor(ctx, target, lo = 1, hi = 2) {  // 주인공(얼굴 또는 사람)이 화면 높이의 target 만큼 보이게 하는 확대 배율
  const f = ctx.frame, m = f.main >= 0 ? (f.persons || [])[f.main] : null, fc = f.faces && f.faces[0] ? f.faces[0].box : null;
  const h = m ? m[3] : fc ? fc[3] * 2.6 : 0; if (!h) return lo;
  const base = ctx.W / ctx.H > ctx.ar ? ctx.W / ctx.ar / ctx.H : 1;  // 가로를 채우느라 이미 커진 만큼
  return clamp(target / (h * base), lo, hi);
}
function cutLayer(ctx, bg, o = {}) {  // 배경과 똑같이 놓인 누끼 (외곽선·광선)
  if (!ctx.cut) return null;
  const l = L("image", { name: "누끼", src: ctx.cut.cut, orig: ctx.cut.src, x: bg.x, y: bg.y, w: bg.w, h: bg.h, fit: bg.fit, fx: bg.fx, fy: bg.fy, flipX: bg.flipX,
    cropL: bg.cropL, cropR: bg.cropR, cropT: bg.cropT, cropB: bg.cropB,
    outline: { on: true, color: o.outline || "#FFFFFF", width: Math.round((o.ow || 9) * ctx.W / 1280) },
    glow: { on: !!o.glow, color: o.glow || "#FFFFFF", size: Math.round(26 * ctx.W / 1280), opacity: 0.9 },
    shadow: { on: true, color: "#000000", blur: 24, dx: 0, dy: 10, opacity: 0.7 } });
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
function lineLayer(ctx, text, size, o = {}) {  // 한 줄 글자 + 강조 낱말(runs: 강조 색·조금 크게)
  const l = L("text", Object.assign({ text, name: o.name || "제목" }, tStyle(ctx, size, o)));
  if (o.emph && o.emph[1] > o.emph[0]) {
    const run = { s: o.emph[0], e: o.emph[1], fill: o.emphFill || ctx.brand.colors.hl };
    if (o.emphScale && o.emphScale !== 1) { run.size = Math.round(size * o.emphScale); run.s1w = Math.round(run.size * (o.sw ?? 0.2)); }
    l.runs = [run];
  }
  if (o.rot) l.rot = o.rot;
  if (o.skew) l.skew = o.skew;
  fitText(l); return l;
}
function emphOf(copy, line) { const e = copy.emph; if (!e || e[0] !== line) return null; return [e[1], e[2]]; }
// 두 줄 제목: 강조 낱말이 든 줄을 크게(big), 다른 줄은 작게(small) · 상자(box) 안에 맞춰 쌓음 → {layers, box, big, small}
function headline(ctx, box, o = {}) {
  const c = ctx.copy, b = ctx.brand.colors, bigI = c.l2 ? (c.emph ? c.emph[0] : 1) : 0, smallI = 1 - bigI;
  const bigT = bigI ? c.l2 : c.l1, smallT = c.l2 ? (smallI ? c.l2 : c.l1) : "";
  const align = o.align || "center", ratio = o.ratio || 0.56, style = o.style || "line";
  let size = o.size || Math.round(ctx.H * 0.2);
  const em = emphOf(c, bigI);
  const bigO = style === "line"
    ? { fill: o.bigFill || b.hl, fill2: o.grad === false ? "" : mix(o.bigFill || b.hl, "#FF8A00", 0.42), emph: o.emphAccent && em ? em : null, emphFill: b.accent, emphScale: 1 }
    : { fill: b.hl2, emph: em, emphFill: o.emphFill || b.hl, emphScale: o.emphScale || 1.18 };
  const make = k => {
    const big = lineLayer(ctx, bigT, Math.round(size * k), Object.assign({ align, name: "제목 큰 줄", rot: o.rot, skew: o.skew, sw: o.sw, font: o.font }, bigO));
    const small = smallT ? lineLayer(ctx, smallT, Math.round(size * ratio * k), { align, name: "제목 작은 줄", fill: o.smallFill || b.hl2, rot: o.rot, skew: o.skew, sw: o.sw, font: o.font,
      emph: style === "word" ? emphOf(c, smallI) : null, emphFill: b.hl }) : null;
    return { big, small };
  };
  let t = make(1);
  const wOf = l => (l ? l.w : 0), gap = () => -Math.round(size * 0.03);
  const totalH = () => t.big.h + (t.small ? t.small.h + gap() : 0);
  const k = Math.min(1, box.w / Math.max(wOf(t.big), wOf(t.small)), box.h / totalH());
  if (k < 0.999) { size = Math.round(size * k); t = make(1); }
  const order = bigI === 0 ? [t.big, t.small] : [t.small, t.big];  // 읽는 순서는 늘 첫째 줄 → 둘째 줄 (크기만 다름)
  const ax = align === "center" ? 0.5 : align === "right" ? 1 : 0, X = box.x + box.w * ax;
  let y = o.anchor === "bottom" ? box.y + box.h - totalH() : o.anchor === "middle" ? box.y + (box.h - totalH()) / 2 : box.y;
  for (const l of order) { if (!l) continue; l.x = X - l.w * ax; l.y = y; y += l.h + gap(); }
  for (const l of [t.big, t.small]) if (l) l.fitBox = { w: box.w, h: l.h * 1.15, size: l.size, ay: 0 };
  const layers = order.filter(Boolean);
  return { layers, box: bbox(layers), big: t.big, small: t.small };
}
// 상자 제목 (자막형): 큰 줄은 강조색 상자에 검은 글자, 작은 줄은 어두운 상자에 흰 글자 · 왼쪽 아래부터 쌓음 → {layers, box, big}
function boxHead(ctx, x, yBottom, maxW, o = {}) {
  const c = ctx.copy, b = ctx.brand.colors, two = !!c.l2, bigI = two ? (c.emph ? c.emph[0] : 1) : 0;
  const mk = (t, size, fill, box, name) => {
    const l = L("text", Object.assign({ text: t, name }, tStyle(ctx, size, { fill, sw: 0, noShadow: true, align: "left" }),
      { box: { on: true, color: box, pad: Math.round(size * 0.14), radius: Math.round(size * 0.1) }, shadow: { on: true, color: "#000000", blur: Math.round(size * 0.25), dx: 0, dy: Math.round(size * 0.06), opacity: 0.55 } }));
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
  const l = L("text", Object.assign({ text: s, name: "시리즈 이름" }, tStyle(ctx, Math.round(ctx.H * 0.062), { fill: "#EDEDED", font: "Pretendard Black", sw: 0.12, align: "left" })));
  l.shadow = { on: true, color: "#000000", blur: 8, dx: 0, dy: 3, opacity: 0.8 }; fitText(l); l.x = x; l.y = y; return l;
}
function logoLayer(ctx, avoid) {  // 채널 로고 (높이 0.11H) — 제목·안전 영역과 겹치면 반대쪽, 그래도 겹치면 뺌
  const br = ctx.brand; if (!br.logo || br.logoPos === "off") return null;
  const h = Math.round(ctx.H * 0.11), m = Math.round(ctx.W * 0.025);
  for (const pos of [br.logoPos, br.logoPos === "tr" ? "tl" : "tr"]) {
    const l = L("image", { name: "로고", src: br.logo, fit: "contain", w: h * 1.6, h, y: m + (ctx.short ? ctx.H * 0.07 : 0), x: pos === "tr" ? ctx.W - h * 1.6 - m - (ctx.short ? ctx.W * 0.16 : 0) : m,
      shadow: { on: true, color: "#000000", blur: 12, dx: 0, dy: 4, opacity: 0.6 } });
    if (!avoid.some(b => overlap(bbox([l]), b, 8))) return l;
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

/* ----- 전술 그래픽 자동 배치: 주인공 발밑 원 · 빈 공간으로 곡선 화살표 + '공간' 칩 · 다른 선수 움직임 화살표(네온) · 동료에게 패스 점선 ----- */
function tacShape(shape, o) { return normLayer(L("shape", Object.assign({ shape, name: TAC_NAMES[shape] }, o))); }
function arrowLayer(ctx, s, e, col, gid, o = {}) {  // s → e 로 위로 휘는 굵은 네온 곡선 화살표
  const scale = ctx.W / 1280, [sx, sy] = s, [ex, ey] = e;
  const mx2 = (sx + ex) / 2, my2 = (sy + ey) / 2, len = Math.hypot(ex - sx, ey - sy) || 1, nx = -(ey - sy) / len, ny = (ex - sx) / len;
  const bend = (ny < 0 ? -1 : 1) * (o.bend ?? 0.28) * len, cx = mx2 + nx * bend * (ny < 0 ? -1 : 1), cy = my2 - Math.abs(ny * bend) - 0.05 * len;
  const xs = [sx, cx, ex], ys = [sy, cy, ey], x0 = Math.min(...xs), y0 = Math.min(...ys), w = Math.max(30, Math.max(...xs) - x0), h = Math.max(30, Math.max(...ys) - y0);
  return tacShape("arrow2", { name: o.name || TAC_NAMES.arrow2, x: x0, y: y0, w, h, pts: xs.map((x, i) => [(x - x0) / w, (ys[i] - y0) / h]), fill: col,
    width: Math.round((o.width || 16) * scale), head: Math.round((o.head || 62) * scale), core: "#FFFFFF", gid, glow: { on: true, color: col, size: Math.round(34 * scale), opacity: 1 } });
}
function tactics(ctx, bg, avoid, o = {}) {
  const f = ctx.frame, ps = f.persons || [], ar = ctx.ar, out = [], W0 = ctx.W, H0 = ctx.H, gid = "tac" + nid().slice(0, 4), b = ctx.brand.colors;
  if (!ps.length || f.main < 0) return out;
  const mb = boxC(bg, ps[f.main], ar), feet = [mb.x + mb.w / 2, mb.y + mb.h], scale = W0 / 1280;
  const taken = [];  // 이미 놓은 전술 그래픽 (서로 겹치지 않게)
  const ok = l => { const bb = bbox([l]); return bb.x > -bb.w * 0.2 && bb.x + bb.w < W0 + bb.w * 0.2 && bb.y + bb.h < H0 && bb.y > 0 && !avoid.some(a => overlap(bb, a, 12)); };
  const put = l => { out.push(l); taken.push(bbox([l])); };
  if (o.ring !== false) {
    const rw = clamp(mb.w * 1.5, 70 * scale, 380 * scale), rh = rw * 0.3;
    const ring = tacShape("ring", { x: feet[0] - rw / 2, y: feet[1] - rh * 0.55, w: rw, h: rh, fill: o.ringColor || b.neon, width: Math.round(9 * scale), gid,
      glow: { on: true, color: o.ringColor || b.neon, size: Math.round(26 * scale), opacity: 1 } });
    if (ok(ring)) put(ring);
  }
  // 빈 공간: 다른 선수 발에서 가장 먼 곳 (화면 아래 2/3, 제목·안전 영역 밖, 출발점에서 dmin~dmax)
  const others = ps.map((p, i) => [p, i]).filter(([p, i]) => i !== f.main).map(([p]) => { const q = boxC(bg, p, ar); return [q.x + q.w / 2, q.y + q.h, q.h]; }).filter(p => p[0] > 0.03 * W0 && p[0] < 0.97 * W0 && p[1] < H0 * 0.97);
  const cs = Math.round((ctx.short ? 0.09 : 0.2) * H0);
  const freeCell = (from, dmin, dmax, away = []) => {
    let best = null;
    for (let gx = 0.08; gx <= 0.92; gx += 0.04) for (let gy = 0.36; gy <= 0.9; gy += 0.04) {
      const x = gx * W0, y = gy * H0, d = Math.hypot(x - from[0], y - from[1]);
      if (d < dmin * W0 || d > dmax * W0 || away.some(q => Math.hypot(q[0] - x, q[1] - y) < 0.2 * W0)) continue;
      const chip = { x: x - 0.08 * H0, y: y - 0.08 * H0, w: 0.16 * H0, h: 0.16 * H0 };
      if (avoid.some(a => overlap(chip, a, 16)) || taken.some(a => overlap(chip, a, 6)) || !inSafe(ctx, chip, 0)) continue;
      const free = others.length ? Math.min(...others.map(p => Math.hypot(p[0] - x, p[1] - y))) : 0.3 * W0;
      const sc = free + (Math.abs(y - from[1]) < 0.25 * H0 ? 20 : 0);
      if (!best || sc > best.sc) best = { x, y, sc };
    }
    return best;
  };
  const best = freeCell(feet, 0.18, 0.34);
  if (best && o.arrow !== false) {
    const sx0 = feet[0] + (best.x > feet[0] ? 1 : -1) * mb.w * 0.25, sy = feet[1] - mb.h * 0.05;
    const dd = Math.hypot(best.x - sx0, best.y - sy), cut = o.chip === false ? 0 : (cs * 0.62) / dd;
    const col = o.arrowColor || b.accent;
    const arr = arrowLayer(ctx, [sx0, sy], [best.x - (best.x - sx0) * cut, best.y - (best.y - sy) * cut], col, gid);
    if (ok(arr)) {
      put(arr);
      if (o.chip !== false) {
        const chip = tacShape("marker", { name: "공간 칩", x: best.x - cs / 2, y: best.y - cs / 2, w: cs, h: cs, fill: col, label: o.chipText || "공간",
          stroke: { color: "#FFFFFF", width: Math.round(5 * scale) }, gid, glow: { on: true, color: col, size: Math.round(26 * scale), opacity: 1 } });
        if (ok(chip)) put(chip);
        if (o.sparkle) {  // 칩 옆 반짝이 두 개
          for (const [dx, dy, k] of [[0.62, -0.62, 0.42], [-0.7, -0.35, 0.26]]) {
            const s = cs * k, sp = tacShape("sparkle", { name: "반짝이", x: best.x + dx * cs - s / 2, y: best.y + dy * cs - s / 2, w: s, h: s, fill: "#FFFFFF", gid,
              glow: { on: true, color: col, size: Math.round(16 * scale), opacity: 1 } });
            if (ok(sp) && !taken.slice(0, -1).some(a => overlap(bbox([sp]), a, 2))) put(sp);
          }
        }
      }
    }
  }
  // 두 번째 화살표 (네온): 다른 선수(없으면 주인공)에서 다른 빈 공간으로 · 첫 화살표와 다른 방향
  if (o.second && best) {
    const from = others.filter(p => Math.hypot(p[0] - feet[0], p[1] - feet[1]) > 0.1 * W0).sort((p, q) => q[1] - p[1])[0] || feet;
    const c2 = freeCell(from, 0.13, 0.3, [[best.x, best.y], feet]);
    if (c2) {
      const col2 = o.secondColor || b.neon, a2 = arrowLayer(ctx, [from[0], from[1] - (from[2] || mb.h) * 0.05], [c2.x, c2.y], col2, gid, { name: "움직임 화살표", width: 14, head: 54, bend: 0.22 });
      if (ok(a2) && !taken.some(a => overlap(bbox([a2]), a, -Math.min(a.w, a.h) * 0.3))) put(a2);
    }
  }
  if (o.pass !== false && others.length) {  // 가장 가까운 다른 선수에게 점선
    const near = others.map(p => [p, Math.hypot(p[0] - feet[0], p[1] - feet[1])]).filter(x => x[1] > 0.1 * W0 && x[1] < 0.45 * W0).sort((p, q) => p[1] - q[1])[0];
    if (near) {
      const [px, py] = near[0], x0 = Math.min(feet[0], px), y0 = Math.min(feet[1], py) - 0.06 * H0, w = Math.max(20, Math.abs(px - feet[0])), h = Math.max(20, Math.abs(py - feet[1]) + 0.06 * H0);
      const P = [[feet[0], feet[1]], [(feet[0] + px) / 2, Math.min(feet[1], py) - 0.06 * H0], [px, py]].map(([x, y]) => [(x - x0) / w, (y - y0) / h]);
      const pass = tacShape("pass", { x: x0, y: y0, w, h, pts: P, fill: b.hl2, width: Math.round(8 * scale), gid, glow: { on: true, color: b.neon, size: Math.round(16 * scale), opacity: 0.9 } });
      if (ok(pass)) put(pass);
    }
  }
  return out;
}

/* ----- 템플릿 (fn(ctx) → 레이어들 · needs: 장면 종류·누끼·선수 수·반전 문구) ----- */
function ctxFor(frame, copy, fmt, seed) {
  const short = fmt === "short", W0 = short ? 1080 : 1280, H0 = short ? 1920 : 720, ar = frameAspect(), ps = frame.persons || [];
  const m = frame.main >= 0 ? ps[frame.main] : null, fc = frame.faces && frame.faces[0] ? frame.faces[0].box : null;
  const focus = fc ? [fc[0] + fc[2] / 2, fc[1] + fc[3] * 0.9] : m ? [m[0] + m[2] / 2, m[1] + m[3] * 0.45] : [0.5, 0.5];
  return { frame, frames: AI.frames, copy, cut: AI.cuts[String(frame.t)] || null, persons: ps, ball: frame.ball, brand: brandOf(), W: W0, H: H0, short, ar, seed, focus, band: frame.band };
}
const T_NEW = {
  long: {
    "전술 해설 (쪼살형)": { prior: 3, needs: { kinds: ["wide", "mid"], persons: 1, tactics: true }, fn: ctx => {
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.42, 1.05, 1.6), target: [ctx.W * 0.5, ctx.H * 0.6] }), ls = [bg];  // 주인공이 화면 높이 42% 쯤
      ls.push(L("shape", { name: "위 어둡게", x: 0, y: 0, w: ctx.W, h: ctx.H * 0.58, fill: "#000000", fill2: "rgba(0,0,0,0)", gradAngle: 90, opacity: 0.74 }));
      const hd = headline(ctx, { x: ctx.W * 0.05, y: ctx.H * 0.035, w: ctx.W * 0.9, h: ctx.H * 0.46 }, { style: "line", size: Math.round(ctx.H * 0.27), ratio: 0.52 });
      const avoid = [hd.box];
      const tac = tactics(ctx, bg, avoid, { sparkle: true });
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 5, glow: ctx.brand.colors.neon });
      const ring = tac.filter(l => l.shape === "ring"), rest = tac.filter(l => l.shape !== "ring");
      ls.push(...ring); if (cut) ls.push(cut); ls.push(...rest, ...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "큰 제목 + 네온 화살표 (쪼살형 2)": { prior: 1.5, needs: { kinds: ["wide", "mid"], persons: 2, tactics: true }, fn: ctx => {
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.48, 1.1, 1.6), target: [ctx.W * 0.5, ctx.H * 0.62] }), ls = [bg];
      ls.push(L("shape", { name: "위 어둡게", x: 0, y: 0, w: ctx.W, h: ctx.H * 0.5, fill: "#000000", fill2: "rgba(0,0,0,0)", gradAngle: 90, opacity: 0.72 }));
      const hd = headline(ctx, { x: ctx.W * 0.06, y: ctx.H * 0.03, w: ctx.W * 0.88, h: ctx.H * 0.42 }, { style: "line", size: Math.round(ctx.H * 0.28), ratio: 0.5 });
      const avoid = [hd.box];
      const tac = tactics(ctx, bg, avoid, { second: true, pass: false, chip: false, arrowColor: ctx.brand.colors.hl, ringColor: ctx.brand.colors.hl });
      const cut = cutLayer(ctx, bg, { outline: ctx.brand.colors.neon, ow: 5 });
      ls.push(...tac.filter(l => l.shape === "ring")); if (cut) ls.push(cut); ls.push(...tac.filter(l => l.shape !== "ring"), ...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "상자 제목 (자막형)": { prior: 1, needs: { kinds: ["close", "mid"] }, fn: ctx => {
      const m = ctx.frame.main >= 0 ? ctx.persons[ctx.frame.main] : null, fc = ctx.frame.faces && ctx.frame.faces[0] ? ctx.frame.faces[0].box : null;
      const cx = fc ? fc[0] + fc[2] / 2 : m ? m[0] + m[2] / 2 : 0.5, right = cx >= 0.4;  // 사람이 있는 쪽 반대편에 상자
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.95, 1.05, 1.8), target: [ctx.W * (right ? 0.68 : 0.32), ctx.H * 0.48] }), ls = [bg];
      ls.push(L("shape", { name: right ? "왼쪽 어둡게" : "오른쪽 어둡게", x: right ? 0 : ctx.W * 0.45, y: 0, w: ctx.W * 0.55, h: ctx.H, fill: right ? "#000000" : "rgba(0,0,0,0)", fill2: right ? "rgba(0,0,0,0)" : "#000000", gradAngle: 0, opacity: 0.55 }));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 6 }); if (cut) ls.push(cut);
      const hd = boxHead(ctx, ctx.W * (right ? 0.045 : 0.43), ctx.H * 0.84, ctx.W * 0.56, { size: ctx.H * 0.23, rot: -2 });
      if (!right) for (const l of hd.layers) l.x = ctx.W * 0.955 - l.w;
      ls.push(...hd.layers);
      const sl = ctx.brand.seriesOn ? seriesLabel(ctx, right ? ctx.W * 0.045 : ctx.W * 0.6, ctx.H * 0.06) : null; if (sl) ls.push(sl);
      const lg = logoLayer(ctx, [hd.box].concat(sl ? [bbox([sl])] : [])); if (lg) ls.push(lg);
      return ls;
    } },
    "강좌 시리즈 (쌈바형)": { prior: 3, needs: { cut: true }, fn: ctx => {
      const main = ctx.frame.main >= 0 ? ctx.persons[ctx.frame.main] : null, left = main ? main[0] + main[2] / 2 < 0.5 : false;
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.85, 1, 2), target: [ctx.W * (left ? 0.36 : 0.64), ctx.H * 0.5] }), ls = [bg];
      Object.assign(bg, { bright: 68, blur: 3, vignette: 55 });
      ls.push(L("shape", { name: "아래 어둡게", x: 0, y: ctx.H * 0.45, w: ctx.W, h: ctx.H * 0.55, fill: "rgba(0,0,0,0)", fill2: "#000000", gradAngle: 90, opacity: 0.8 }));
      const cut = cutLayer(ctx, bg, { outline: ctx.brand.colors.hl, ow: 10 }); if (cut) { cut.blur = 0; cut.bright = 100; ls.push(cut); }
      const al = left ? "right" : "left", bx = { x: ctx.W * 0.045, y: ctx.H * 0.42, w: ctx.W * 0.91, h: ctx.H * 0.52 };
      const hd = headline(ctx, bx, { style: "word", align: al, anchor: "bottom", size: Math.round(ctx.H * 0.29), ratio: 0.4, emphScale: 1.05, grad: false });
      ls.push(...hd.layers);
      const sl = seriesLabel(ctx, ctx.W * 0.04, ctx.H * 0.05); if (sl) ls.push(sl);
      const lg = logoLayer(ctx, [hd.box].concat(sl ? [bbox([sl])] : [])); if (lg) ls.push(lg);
      return ls;
    } },
    "인물 + 오른쪽 제목 (해주호형)": { prior: -2, needs: { kinds: ["mid", "close"] }, fn: ctx => {
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.9, 1.05, 2), target: [ctx.W * 0.27, ctx.H * 0.5] }), ls = [bg];
      ls.push(L("shape", { name: "오른쪽 어둡게", x: ctx.W * 0.38, y: 0, w: ctx.W * 0.62, h: ctx.H, fill: "rgba(0,0,0,0)", fill2: "#000000", gradAngle: 0, opacity: 0.72 }));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 7 }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.46, y: ctx.H * 0.1, w: ctx.W * 0.5, h: ctx.H * 0.7 }, { style: "word", align: "center", anchor: "middle", size: Math.round(ctx.H * 0.3), ratio: 0.6, rot: -2, emphScale: 1.12 });
      for (const l of hd.layers) for (const r of l.runs || []) if (r.fill === ctx.brand.colors.accent) { r.s2c = "#FFFFFF"; r.s2w = Math.round((r.size || l.size) * 0.07); }
      ls.push(...hd.layers);
      const lg = logoLayer(ctx, [hd.box]); if (lg) ls.push(lg);
      return ls;
    } },
    "질문 훅 (JK형)": { prior: -1.5, needs: {}, fn: ctx => {
      const close = ctx.frame.kind === "close";  // 얼굴이 큰 장면은 얼굴을 오른쪽으로 보내고 제목은 왼쪽에만
      const bg = frameLayer(ctx, { zoom: 1.05, target: close ? [ctx.W * 0.72, ctx.H * 0.5] : [ctx.W * 0.55, ctx.H * 0.6] }), ls = [bg];
      ls.push(close ? L("shape", { name: "왼쪽 어둡게", x: 0, y: 0, w: ctx.W * 0.6, h: ctx.H, fill: "#000000", fill2: "rgba(0,0,0,0)", gradAngle: 0, opacity: 0.6 })
        : L("shape", { name: "위 어둡게", x: 0, y: 0, w: ctx.W, h: ctx.H * 0.42, fill: "#000000", fill2: "rgba(0,0,0,0)", gradAngle: 90, opacity: 0.55 }));
      const hd = headline(ctx, { x: ctx.W * 0.045, y: ctx.H * 0.05, w: ctx.W * (close ? 0.5 : 0.86), h: ctx.H * (close ? 0.6 : 0.5) }, { style: "word", align: "left", size: Math.round(ctx.H * 0.28), ratio: 0.8, sw: 0.17, emphScale: 1.0 });
      ls.push(...hd.layers);
      return ls;
    } },
    "반전 O/X": { needs: { ox: true }, fn: ctx => {
      const bg = frameLayer(ctx, { zoom: 1.15, target: [ctx.W * 0.5, ctx.H * 0.42] }), ls = [bg];
      ls.push(L("shape", { name: "아래 어둡게", x: 0, y: ctx.H * 0.5, w: ctx.W, h: ctx.H * 0.5, fill: "rgba(0,0,0,0)", fill2: "#000000", gradAngle: 90, opacity: 0.85 }));
      const cut = cutLayer(ctx, bg, { outline: ctx.brand.colors.hl, ow: 8 }); if (cut) ls.push(cut);
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
    "리액션 클로즈업": { prior: -2.5, needs: { kinds: ["close"], sharp: 0.25 }, fn: ctx => {
      const fc = ctx.frame.faces[0].box, z = clamp(0.5 / Math.max(0.12, fc[3]), 1, 1.6);
      const bg = frameLayer(ctx, { zoom: z, focus: [fc[0] + fc[2] / 2, fc[1] + fc[3] / 2], target: [ctx.W * 0.66, ctx.H * 0.4] }), ls = [bg];
      ls.push(L("shape", { name: "왼쪽 어둡게", x: 0, y: 0, w: ctx.W * 0.6, h: ctx.H, fill: "#000000", fill2: "rgba(0,0,0,0)", gradAngle: 0, opacity: 0.7 }));
      const hd = headline(ctx, { x: ctx.W * 0.04, y: ctx.H * 0.08, w: ctx.W * 0.56, h: ctx.H * 0.82 }, { style: "word", align: "left", anchor: "middle", size: Math.round(ctx.H * 0.3), ratio: 0.66 });  // 제목이 작아 얼굴에 묻힌다는 평
      ls.push(...hd.layers);
      return ls;
    } },
  },
  short: {
    "쇼츠 · 전술": { prior: 1, needs: { kinds: ["wide", "mid"], persons: 1, tactics: true }, fn: ctx => {
      const bg = frameLayer(ctx, { zoom: 1, target: [ctx.W * 0.48, ctx.H * 0.6] }), ls = [bg];
      ls.push(L("shape", { name: "위 어둡게", x: 0, y: 0, w: ctx.W, h: ctx.H * 0.46, fill: "#000000", fill2: "rgba(0,0,0,0)", gradAngle: 90, opacity: 0.9 }));
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.085, w: ctx.W * 0.76, h: ctx.H * 0.28 }, { style: "line", size: Math.round(ctx.W * 0.23), ratio: 0.66 });
      const tac = tactics(ctx, bg, [hd.box, { x: ctx.W * 0.84, y: ctx.H * 0.4, w: ctx.W, h: ctx.H }, { x: 0, y: ctx.H * 0.8, w: ctx.W, h: ctx.H }], { pass: false });
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 6 });
      ls.push(...tac.filter(l => l.shape === "ring")); if (cut) ls.push(cut); ls.push(...tac.filter(l => l.shape !== "ring"), ...hd.layers);
      return ls;
    } },
    "쇼츠 · 레터박스 질문": { prior: -2, needs: {}, fn: ctx => {
      const ls = [L("shape", { name: "검은 바탕", x: 0, y: 0, w: ctx.W, h: ctx.H, fill: "#000000" })];
      const py = ctx.H * 0.3, ph = ctx.H - py, sub = { ...ctx, W: ctx.W, H: ph };  // 위 검은 띠에 제목, 아래는 끝까지 사진 (아래가 비면 미완성처럼 보인다는 평)
      const f = frameLayer(sub, { zoom: zoomFor(sub, 0.75, 1, 1.6), target: [ctx.W * 0.5, ph * 0.5] }); f.y += py; ls.push(f);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.075, w: ctx.W * 0.76, h: ctx.H * 0.21 }, { style: "word", align: "center", anchor: "bottom", size: Math.round(ctx.W * 0.19), ratio: 0.66, sw: 0.14 });
      ls.push(...hd.layers);
      return ls;
    } },
    "쇼츠 · 누끼 크게": { prior: 2, needs: { cut: true }, fn: ctx => {
      const z = zoomFor(ctx, 0.5, 1, 1.8), bg = frameLayer(ctx, { zoom: z, target: [ctx.W * 0.46, ctx.H * 0.6] });
      const ls = [Object.assign(frameLayer(ctx, { zoom: z, target: [ctx.W * 0.46, ctx.H * 0.6] }), { name: "흐린 배경", blur: 20, bright: 55, vignette: 60 })];
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 12, glow: ctx.brand.colors.hl }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.085, w: ctx.W * 0.76, h: ctx.H * 0.27 }, { style: "word", size: Math.round(ctx.W * 0.19), ratio: 0.66, emphScale: 1.1 });
      ls.push(...hd.layers);
      return ls;
    } },
    "쇼츠 · 위 제목 + 아래 누끼": { prior: -0.5, needs: { cut: true }, fn: ctx => {
      const z = zoomFor(ctx, 0.58, 1, 1.8), bg = frameLayer(ctx, { zoom: z, target: [ctx.W * 0.46, ctx.H * 0.6] }), ls = [bg];
      Object.assign(bg, { bright: 72 });  // 흐리면 누끼 둘레에 잔상이 보인다는 평
      ls.push(L("shape", { name: "위 판", x: 0, y: 0, w: ctx.W, h: ctx.H * 0.62, fill: ctx.brand.colors.box, fill2: "rgba(0,0,0,0)", gradAngle: 90, opacity: 0.96 }));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 9 }); if (cut) { cut.bright = 100; ls.push(cut); }
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.08, w: ctx.W * 0.76, h: ctx.H * 0.25 }, { style: "word", size: Math.round(ctx.W * 0.2), ratio: 0.66, emphScale: 1.1, grad: false });
      ls.push(...hd.layers);
      return ls;
    } },
    "쇼츠 · 질문 훅 (JK)": { prior: -3, needs: { kinds: ["mid", "close"] }, fn: ctx => {
      const bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.45, 1.05, 1.6), target: [ctx.W * 0.5, ctx.H * 0.56] }), ls = [bg];
      ls.push(L("shape", { name: "위 어둡게", x: 0, y: 0, w: ctx.W, h: ctx.H * 0.5, fill: "#000000", fill2: "rgba(0,0,0,0)", gradAngle: 90, opacity: 0.92 }));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 7 }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.08, w: ctx.W * 0.76, h: ctx.H * 0.3 }, { style: "word", align: "left", size: Math.round(ctx.W * 0.22), ratio: 0.8, sw: 0.17, emphScale: 1.0 });
      ls.push(...hd.layers);
      return ls;
    } },
    "쇼츠 · 얼굴 + 아래 제목": { prior: -1.5, needs: { kinds: ["close"] }, fn: ctx => {  // 인터뷰처럼 얼굴이 위쪽에 큰 장면: 얼굴은 위에 두고 제목은 아래 안전 영역 끝에
      const bg = frameLayer(ctx, { zoom: 1.05, target: [ctx.W * 0.47, ctx.H * 0.3] }), ls = [bg];
      ls.push(L("shape", { name: "아래 어둡게", x: 0, y: ctx.H * 0.38, w: ctx.W, h: ctx.H * 0.62, fill: "rgba(0,0,0,0)", fill2: "#000000", gradAngle: 90, opacity: 0.92 }));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 7 }); if (cut) ls.push(cut);
      const hd = headline(ctx, { x: ctx.W * 0.07, y: ctx.H * 0.44, w: ctx.W * 0.76, h: ctx.H * 0.31 }, { style: "line", anchor: "bottom", size: Math.round(ctx.W * 0.25), ratio: 0.7 });  // 제목이 작다는 평
      ls.push(...hd.layers);
      return ls;
    } },
    "쇼츠 · 상자 제목": { needs: {}, fn: ctx => {
      // 제목은 위, 사람은 그 아래 (가운데 제목이 시선을 나눈다는 평) · 얼굴이 크면 얼굴을 가리지 않게 제목을 아래 안전 영역 끝에
      const face = ctx.frame.kind === "close", bg = frameLayer(ctx, { zoom: zoomFor(ctx, 0.5, 1.05, 1.7), target: [ctx.W * 0.46, ctx.H * (face ? 0.36 : 0.56)] }), ls = [bg];
      ls.push(face ? L("shape", { name: "아래 어둡게", x: 0, y: ctx.H * 0.42, w: ctx.W, h: ctx.H * 0.58, fill: "rgba(0,0,0,0)", fill2: "#000000", gradAngle: 90, opacity: 0.75 })
        : L("shape", { name: "위 어둡게", x: 0, y: 0, w: ctx.W, h: ctx.H * 0.42, fill: "#000000", fill2: "rgba(0,0,0,0)", gradAngle: 90, opacity: 0.7 }));
      const cut = cutLayer(ctx, bg, { outline: "#FFFFFF", ow: 8 }); if (cut) ls.push(cut);
      const hd = boxHead(ctx, ctx.W * 0.07, ctx.H * (face ? 0.745 : 0.33), ctx.W * 0.72, { size: ctx.W * 0.18, ratio: 0.7 });
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
  const m = f.main >= 0 ? (f.persons || [])[f.main] : null;
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
  if (n.ox && !(ctx.copy.ox && ctx.copy.ox.length === 2)) return false;
  return true;
}
function buildDoc(name, t, ctx) {
  const layers = t.fn(ctx).filter(Boolean).map(normLayer);
  if ((ctx.frame.text || 0) >= TEXTY && !layers.some(l => l.name === "누끼"))  // 글자 박힌 장면인데 누끼가 없으면 덜 흐림 (안 흐리면 박힌 글자와 제목이 다툼 · 많이 흐리면 주인공도 흐림)
    for (const l of layers) if (l.type === "image" && l.name === "배경" && l.blur) l.blur = Math.round(ctx.W * 0.006);
  return { w: ctx.W, h: ctx.H, bg: "#000000", layers };
}

/* ----- 점수 (0~100): 하드 게이트(글자 잘림·가려지는 곳·작게 봤을 때 글자 높이·대비·얼굴 가림) + 가중합 ----- */
// 0.22 장면 + 0.22 가독성 + 0.14 위계 + 0.14 가림 없음 + 0.12 문구 + 0.08 안전 영역 + 0.08 구도 (기준표와 같은 식)
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
  // 4) 얼굴 가림 · 전술 그래픽과 제목 겹침
  const bg = doc.layers.find(l => l.type === "image" && /배경/.test(l.name || "")), f = meta.frame, hb = heads.map(extentOf);
  let faceHit = 0;
  if (bg && f.faces && f.faces[0]) {
    const fb = boxC(bg, f.faces[0].box, frameAspect()); fb.y -= fb.h * 0.35; fb.h *= 1.35;  // 얼굴 상자 + 이마·머리 (제목이 이마에 얹힌다는 평)
    const y0 = Math.max(fb.y, bg.y, 0), y1 = Math.min(fb.y + fb.h, bg.y + bg.h, Hd), x0 = Math.max(fb.x, bg.x, 0), x1 = Math.min(fb.x + fb.w, bg.x + bg.w, Wd);
    Object.assign(fb, { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) });  // 보이는 곳만 (레터박스 위 검은 띠로 올라간 머리는 없음)
    const fa = fb.w * fb.h;
    for (const b of hb) { const ix = Math.max(0, Math.min(b.x + b.w, fb.x + fb.w) - Math.max(b.x, fb.x)), iy = Math.max(0, Math.min(b.y + b.h, fb.y + fb.h) - Math.max(b.y, fb.y)); faceHit += fa ? ix * iy / fa : 0; }
    if (faceHit > 0.06) gates.push(`얼굴을 가림 (${Math.round(faceHit * 100)}%)`);
  }
  const tac = doc.layers.filter(l => l.type === "shape" && TAC_DEF[l.shape]);
  if (tac.some(t => hb.some(b => overlap(bbox([t]), b, 0)))) gates.push("전술 그래픽이 제목과 겹침");
  // 가중합
  const fr = Math.sqrt(clamp((f.score || 0) / Math.max(1e-6, meta.maxFrame || f.score || 1), 0, 1));  // 얼굴 배율이 큰 장면만 몰리지 않게 제곱근
  const read = clamp((hpx - 9) / (17 - 9), 0, 1) * (weak.length ? 0.6 : 1);
  const big = heads.find(l => /큰/.test(l.name || "")) || heads[0], sm = heads.find(l => /작은/.test(l.name || ""));
  const ratio = big && sm ? big.size / sm.size : big ? 1.8 : 1;
  const hier = clamp((ratio - 1) / 0.8, 0, 1) * 0.7 + (big && big.runs && big.runs.length ? 0.3 : (big && big.fill2 ? 0.25 : 0.1));
  const occl = clamp(1 - faceHit * 4, 0, 1);
  const cp = clamp(((meta.copy.score || 0) - meta.copyMin) / Math.max(1e-6, meta.copyMax - meta.copyMin), 0, 1);
  const safeM = ui.every(l => inSafe({ short, W: Wd, H: Hd }, extentOf(l), 0)) ? 1 : 0.5;
  const area = hb.reduce((a, b) => a + b.w * b.h, 0) / (Wd * Hd), comp = area >= 0.12 && area <= 0.42 ? 1 : area < 0.12 ? area / 0.12 : clamp(1 - (area - 0.42) * 3, 0, 1);
  let s = 100 * (0.22 * fr + 0.22 * read + 0.14 * hier + 0.14 * occl + 0.12 * cp + 0.08 * safeM + 0.08 * comp);
  if (meta.legacy) s -= 15;  // 예전 템플릿은 레퍼런스형보다 한 단계 아래 (새 템플릿이 안 맞을 때만 나오게)
  s += meta.prior || 0;     // 템플릿 가산점: 개발 중 클로드 블라인드 판정 평균으로 맞춤 (7점대 +3 … 5점대 −3)
  if (tac.length) s += 3;   // 전술 그래픽 (쪼살형) 가산
  s = Math.min(100, s);
  if (gates.length) s = Math.min(s, 40);
  // 이유 (카드에 2개)
  const ps = (f.persons || []).length;
  if (read >= 0.9) why.push("작게 봐도 잘 읽혀요"); else if (read >= 0.6) why.push("휴대폰에서도 읽혀요");
  if (f.kind === "close" && f.emo && ((f.emo.happiness || 0) + (f.emo.surprise || 0)) >= 0.4) why.push("표정이 살아 있어요");
  if (ps >= 2) why.push(`선수 ${ps}명 액션`);
  if (f.ball) why.push("공이 보여요");
  if (tac.length) why.push("전술 그래픽");
  if (meta.copy.src === "ai") why.push("클로드 문구");
  if (hier >= 0.85) why.push("한눈에 들어오는 강조");
  return { score: Math.round(s * 10) / 10, gates, why: why.slice(0, 2), weak, hpx };
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

/* ----- 후보 만들기 → 점수 → 다양하게 고르기 (MMR λ=0.7 · 템플릿 ≥4 · 장면 ≥3 · 문구 ≥3) ----- */
function aiFrames(n = 6) {
  let fs = [...AI.frames].sort((a, b) => (b.score || 0) - (a.score || 0));
  if (AI.pick != null) { const f = fs.find(x => x.t === AI.pick); return f ? [f] : fs.slice(0, 1); }
  const sharp = fs.filter(f => (f.blur ?? 0) <= 0.45); if (sharp.length >= 3) fs = sharp;
  const anyP = fs.some(f => (f.persons || []).length);
  if (anyP) { const np = fs.filter(f => !(f.persons || []).length); fs = fs.filter(f => (f.persons || []).length).concat(np.slice(0, 1)); }
  const out = [];
  for (const k of ["close", "mid", "wide"]) { const f = fs.find(x => x.kind === k && !out.includes(x)); if (f) out.push(f); }  // 종류마다 하나씩
  for (const f of fs) { if (out.length >= n) break; if (!out.includes(f)) out.push(f); }
  return out.slice(0, n).sort((a, b) => (b.score || 0) - (a.score || 0));
}
function aiCopies(n = 4, seed = 0) {
  if (AI.copySel) return [AI.copySel];
  const cs = [...AI.copy].sort((a, b) => b.score - a.score);
  if (!seed) return cs.slice(0, n);
  const k = (seed * 2) % Math.max(1, cs.length);  // 다시 추천: 다른 문구가 앞에 오게 돌림
  return [...cs.slice(k), ...cs.slice(0, k)].slice(0, n);
}
function recommend(fmt, n = 6, seed = 0, avoidKeys = new Set()) {
  const frames = aiFrames(), copies = aiCopies(4, seed), tpls = allTpl(fmt), cands = [];
  const maxFrame = Math.max(1e-6, ...AI.frames.map(f => f.score || 0)), cScores = AI.copy.map(c => c.score || 0);
  const copyMin = Math.min(...cScores, 0), copyMax = Math.max(...cScores, 1);
  for (const f of frames) for (const c of copies) for (const [name, t] of Object.entries(tpls)) {
    const cx = ctxFor(f, c, fmt, seed + Math.round(f.t * 10)); if (!fits(t, cx)) continue;
    let doc; try { doc = buildDoc(NAME, t, cx); } catch (e) { console.warn("템플릿 실패", name, e); continue; }
    if (t.needs && t.needs.tactics && !doc.layers.some(l => l.shape === "arrow2")) continue;  // 전술 템플릿인데 화살표 놓을 자리가 없으면 다른 템플릿에 양보
    const meta = { frame: f, copy: c, legacy: !!t.legacy, prior: t.prior || 0, maxFrame, copyMin, copyMax }, sc = scoreDoc(doc, meta);
    const key = `${name}|${f.t}|${c.l1}/${c.l2}`;
    cands.push(Object.assign({ tpl: name, t: f.t, copy: c, doc, key, legacy: !!t.legacy }, sc, { base: sc.score - (avoidKeys.has(key) ? 25 : 0) }));
  }
  cands.sort((a, b) => b.base - a.base);
  for (const x of cands.slice(0, 20)) if (x.weak.length && !x.gates.length) { const r = preciseContrast(x.doc, x.weak); if (r < 4.5) { x.gates.push(`대비 ${r.toFixed(1)}:1`); x.score = Math.min(x.score, 40); x.base = Math.min(x.base, 40); } }
  // 게이트 통과한 것만 · 새(레퍼런스형) 템플릿만으로 6개(3종 이상)를 채울 수 있으면 예전 템플릿은 빼고, 모자라면 예전 템플릿으로 채움
  const clean = cands.filter(x => !x.gates.length), fresh = clean.filter(x => !x.legacy);
  // (판정: 예전 템플릿 5~6점 < 같은 새 템플릿 두 번 6.5~7점 → 새 템플릿이 3가지만 돼도 새 템플릿으로)
  const pool = fresh.length >= n && new Set(fresh.map(x => x.tpl)).size >= 3 && new Set(fresh.map(x => x.t)).size >= Math.min(3, frames.length) ? fresh : clean.length >= n ? clean : cands;
  // 장면은 지문(dHash)이 비슷하면 같은 장면으로 봄 (시각만 다른 같은 화면이 여러 번 나오지 않게)
  const scene = {}; for (const f of frames) scene[f.t] = (frames.find(g => g === f || hamming(g.hash, f.hash) <= 10) || f).t;
  for (const x of pool) x.sc = scene[x.t] ?? x.t;
  const sim = (a, b) => (a.tpl === b.tpl ? 0.5 : 0) + (a.sc === b.sc ? 0.3 : 0) + (a.copy === b.copy ? 0.2 : 0);
  const out = [];
  const tplCap = new Set(pool.map(x => x.tpl)).size >= n ? 1 : 2;
  const need = { tpl: Math.min(4, new Set(pool.map(x => x.tpl)).size), sc: Math.min(3, new Set(pool.map(x => x.sc)).size), copy: Math.min(3, new Set(pool.map(x => x.copy)).size) };
  while (out.length < n && out.length < pool.length) {
    const left = n - out.length, have = k => new Set(out.map(x => x[k])).size;
    let best = null, bv = -1e9;
    for (const x of pool) {
      if (out.includes(x)) continue;
      // 다양성 강제: 남은 자리로 채워야 할 종류가 있으면 새 종류만
      if ((["tpl", "sc", "copy"]).some(k => need[k] - have(k) >= left && out.some(o => o[k] === x[k]))) continue;
      if (out.filter(o => o.tpl === x.tpl).length >= tplCap || out.filter(o => o.sc === x.sc).length >= 2) continue;  // 같은 템플릿(템플릿이 6가지 넘으면 1개)·같은 장면은 많아야 2개
      if (out.some(o => o.tpl === x.tpl && o.sc === x.sc)) continue;  // 같은 템플릿 + 같은 장면은 문구만 다른 거의 같은 그림
      const v = 0.7 * x.base - 0.3 * 100 * Math.max(0, ...out.map(o => sim(o, x)));
      if (v > bv) { bv = v; best = x; }
    }
    if (!best) best = pool.find(x => !out.includes(x) && !out.some(o => o.tpl === x.tpl && o.sc === x.sc)) || pool.find(x => !out.includes(x));
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
    else if (!j.ok) { setAIStat(j.error || "다른 작업이 끝나기를 기다리는 중…"); await new Promise(res => setTimeout(res, 3000)); continue; }
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
  return [ps ? `👥${ps}` : "", f.ball ? "⚽" : "", (f.blur ?? 1) < 0.25 ? "✨선명" : "", e >= 0.4 ? "😆" : ""].filter(Boolean).join(" ");
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
    <details class="fx" ${AI.frames.length ? "open" : ""}><summary>▸ 장면 고르기 <span class="hint">누르면 그 장면으로만 다시 추천</span></summary><div class="aiframes" id="aiFrames">${[...AI.frames].sort((a, b) => b.score - a.score).map(f => `<div class="af ${AI.pick === f.t ? "on" : ""}" data-t="${f.t}"><img src="${frameSrc(f.t)}" loading="lazy"><b>${frameBadges(f)}</b><span>${mmss(f.t)}</span></div>`).join("") || `<span class="hint">장면은 추천을 누르면 찾아요</span>`}${AI.pick != null ? `<button class="btn sm" id="pickAll">장면 고르기 해제</button>` : ""}</div></details></div>`;
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
    await Promise.all(x.doc.layers.filter(l => l.type === "image" && l.src).map(l => imgReady(l.src)));
    const c = newCanvas(x.doc.w, x.doc.h); renderDoc(c.getContext("2d"), x.doc, 1);
    let data = null;
    if (!opts.noData) { let q = opts.quality || 0.93; do { data = c.toDataURL("image/jpeg", q); q -= 0.07; } while (data.length * 0.75 > 1.95e6 && q > 0.5); }
    out.push({ tpl: x.tpl, t: x.t, copy: { l1: x.copy.l1, l2: x.copy.l2, sub: x.copy.sub, src: x.copy.src, score: x.copy.score, pid: x.copy.pid }, score: x.score, why: x.why, gates: x.gates, hpx: x.hpx,
      frame: AI.frames.find(f => f.t === x.t), doc: clone(x.doc), data });
  }
  return { ok: true, items: out, frames: AI.frames.length, copies: AI.copy.length };
};
