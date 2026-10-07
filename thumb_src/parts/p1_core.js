const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const NAME = new URLSearchParams(location.search).get("name");
const RAD = Math.PI / 180;
const FMT = { long: { w: 1280, h: 720, label: "롱폼 16:9" }, short: { w: 1080, h: 1920, label: "쇼츠 9:16" } };
let W = 1280, H = 720;
const nid = () => Math.random().toString(16).slice(2, 10);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const r1 = v => Math.round(v * 10) / 10;
const norm180 = a => ((((a + 180) % 360) + 360) % 360) - 180;
const niceName = n => n.replace(/^\d{8}_[A-Za-z0-9_-]{11}_/, "").replace(/\.[^.]+$/, "");
const mmss = t => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const FONTS = { "검은고딕": "Black Han Sans", "프리텐다드 블랙": "Pretendard Black", "프리텐다드 볼드": "Pretendard Bold", "도현": "Do Hyeon" };
const BLENDS = [["source-over", "표준"], ["multiply", "곱하기"], ["screen", "스크린"], ["overlay", "오버레이"], ["soft-light", "소프트 라이트"], ["hard-light", "하드 라이트"], ["darken", "어둡게"], ["lighten", "밝게"], ["color-dodge", "색상 닷지"], ["color-burn", "색상 번"], ["difference", "차이"], ["hue", "색조"], ["saturation", "채도"], ["color", "색상"], ["luminosity", "광도"]];
function toast(m) { const t = $("toast"); t.textContent = m; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2800); }
async function post(url, body) { const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); return r.json(); }
const dropPriv = (k, v) => (k.startsWith("_") ? undefined : v);
const clone = o => JSON.parse(JSON.stringify(o, dropPriv));
function toHex(c) {
  if (typeof c !== "string") return "#000000";
  if (/^#[0-9a-f]{6}$/i.test(c)) return c.toLowerCase();
  if (/^#[0-9a-f]{3}$/i.test(c)) return "#" + [...c.slice(1)].map(x => x + x).join("").toLowerCase();
  const m = c.match(/rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/);
  return m ? "#" + [m[1], m[2], m[3]].map(x => (+x).toString(16).padStart(2, "0")).join("") : "#000000";
}

const PRESETS = ["#FFFFFF", "#000000", "#FFE14D", "#A6FF00", "#FF3B30", "#FF8A00", "#1F4FE0", "#00D1FF", "#0F7A3D", "#FF3EA5", "#9B5CFF", "#7A7A7A"];
let RECENT = [];
try { RECENT = JSON.parse(localStorage.getItem("fsRecentColors") || "[]"); } catch (e) {}
function useColor(c) {
  if (!/^#[0-9a-f]{6}$/i.test(c)) return;
  c = c.toUpperCase(); RECENT = [c, ...RECENT.filter(x => x !== c)].slice(0, 8);
  try { localStorage.setItem("fsRecentColors", JSON.stringify(RECENT)); } catch (e) {}
}

let DOCS = { designs: [] }, cur = 0, D = null, selIds = [], tool = "move", anchorId = null;
let brush = { size: 40, hard: 70 };
let FRAMES = [], HOOKS = [], KEYWORDS = [], INFO = { duration: 60 }, AUTO_FMT = "long";
let undoStack = [], redoStack = [], saveTimer = null, histNames = [];
let Z = 0.6, PX = 0, PY = 0, RS = 1, fitMode = true, FONT_VER = 0;
let geoBase = null, CLIP_TOKEN = "", saveGen = 0;
let xform = null, editing = null, textSel = null, fg = "#FFE14D", eyeApply = true, lockRatio = true, CLIP = null, spaceDown = false;
const fxOpen = new Set(["space"]);

/* ---------- 이미지 캐시 ---------- */
const IMG = {};
function img(src) {
  if (!src) return null;
  if (IMG[src]) return IMG[src].complete && IMG[src].naturalWidth ? IMG[src] : null;
  const im = new Image(); im.crossOrigin = "anonymous"; im.onload = () => { renderAll(); renderThumbsLater(); }; im.src = src; IMG[src] = im; return null;
}
function imgReady(src, ms = 15000) {
  if (!src) return Promise.resolve(true);
  if (IMG[src] && IMG[src].complete && !IMG[src].naturalWidth) delete IMG[src];  // 실패했던 건 다시 시도
  img(src); const im = IMG[src];
  if (im.complete) return Promise.resolve(im.naturalWidth > 0);
  return new Promise(res => { const t = setTimeout(() => res(false), ms); im.addEventListener("load", () => { clearTimeout(t); res(true); }, { once: true }); im.addEventListener("error", () => { clearTimeout(t); res(false); }, { once: true }); });
}
let thumbTimer = 0;
function renderThumbsLater() { clearTimeout(thumbTimer); thumbTimer = setTimeout(() => { renderDesigns(); drawCands(); }, 300); }

/* ---------- 레이어 기본값 ---------- */
function L(type, o) {
  const base = { id: nid(), type, name: "", x: 0, y: 0, w: 300, h: 200, rot: 0, skew: 0, opacity: 1, blend: "source-over", hidden: false, locked: false,
    shadow: { on: false, color: "#000000", blur: 20, dx: 0, dy: 8, opacity: 1 }, glow: { on: false, color: "#FFE14D", size: 30, opacity: 1 },
    outline: { on: false, color: "#FFFFFF", width: 10 }, overlay: { on: false, color: "#FF3B30", opacity: 1 },
    fade: { on: false, angle: 0, start: 0.55, end: 1 }, extrude: { on: false, color: "#7A0000", depth: 14, angle: 45 }, warp: { style: "none", bend: 0.3 } };
  const defs = {
    image: { name: "이미지", src: "", fit: "cover", fx: 0.5, fy: 0.5, cropT: 0, cropB: 0, cropL: 0, cropR: 0, flipX: false, bright: 100, contrast: 100, sat: 100, blur: 0, hue: 0, slant: 0, vignette: 0 },
    text: { lsv: 2, text: "제목을 입력", font: "Black Han Sans", size: 110, fill: "#FFFFFF", fill2: "", gradAngle: 90, align: "left", lh: 1.12, ls: 0, hs: 1, vs: 1, runs: [],
      strokes: [{ color: "#000000", width: 16 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#000000", blur: 0, dx: 8, dy: 8, opacity: 1 },
      box: { on: false, color: "#1F5FE0", pad: 18, radius: 0 } },
    shape: { name: "도형", shape: "rect", fill: "#000000", fill2: "", gradAngle: 90, radius: 0, slant: 0, stroke: { color: "#FFFFFF", width: 0 }, lines: 60, inner: 0.35 },
  };
  const d = JSON.parse(JSON.stringify(defs[type]));
  for (const k of Object.keys(d)) if (d[k] && typeof d[k] === "object" && !Array.isArray(d[k]) && base[k]) { base[k] = Object.assign(base[k], d[k]); delete d[k]; }
  return Object.assign(base, d, o || {});
}
function normLayer(l) {
  const d = L(l.type);
  for (const [k, v] of Object.entries(d)) {
    if (k === "id") continue;
    if (l[k] === undefined || l[k] === null) l[k] = v;
    else if (v && typeof v === "object" && !Array.isArray(v) && typeof l[k] === "object") for (const [kk, vv] of Object.entries(v)) if (l[k][kk] === undefined) l[k][kk] = vv;
  }
  if (l.type === "text" && (!Array.isArray(l.strokes) || l.strokes.length < 2)) l.strokes = [...(l.strokes || []), ...d.strokes].slice(0, 2);
  if (l.type === "text" && l.lsv !== 2) {  // 예전 저장본: 줄 끝 자간이 너비에 들어가 있었음 → 글자 모양이 그대로 보이게 너비 보정
    if (l.ls) { const m = textLayout(ctx, l), k = m.natW / (m.natW + l.ls), ow = l.w; l.w *= k; if (l.align === "center") l.x += (ow - l.w) / 2; else if (l.align === "right") l.x += ow - l.w; }
    l.lsv = 2;
  }
  return l;
}
const lname = l => l.name || (l.type === "text" ? String(l.text).split("\n")[0] : { image: "이미지", shape: "도형" }[l.type]) || "레이어";

/* ---------- 글자: 글자별 스타일(runs) ---------- */
const CHAR_KEYS = ["fill", "size", "font", "s1c", "s1w", "s2c", "s2w"];
const EMPTY_KEY = CHAR_KEYS.map(() => "").join("|");
function baseStyle(l) { return { fill: l.fill, size: l.size, font: l.font, s1c: l.strokes[0].color, s1w: l.strokes[0].width, s2c: l.strokes[1].color, s2w: l.strokes[1].width }; }
function expand(l) {
  const n = l.text.length, a = Array.from({ length: n }, () => ({}));
  for (const r of l.runs || []) for (let i = Math.max(0, r.s); i < Math.min(n, r.e); i++) for (const k of CHAR_KEYS) if (r[k] !== undefined) a[i][k] = r[k];
  return a;
}
function compress(l, a) {
  const runs = [];
  a.forEach((st, i) => {
    const key = CHAR_KEYS.map(k => st[k] ?? "").join("|"); if (key === EMPTY_KEY) return;
    const last = runs[runs.length - 1];
    if (last && last._k === key && last.e === i) last.e = i + 1; else runs.push(Object.assign({ s: i, e: i + 1, _k: key }, st));
  });
  l.runs = runs.map(({ _k, ...r }) => r);
}
function textLayout(c, l) {
  if ("letterSpacing" in c) c.letterSpacing = `${l.ls}px`;  // 부르는 쪽 캔버스에 자간을 맞춰 두는 것도 이 함수 몫 (그대로 둠)
  // 글자·스타일이 그대로면 지난번 잰 결과를 씀 (글자 칠 때 한 번에 여러 번 불림)
  const memo = JSON.stringify([l.text, l.runs, l.font, l.size, l.fill, l.fill2, l.strokes, l.ls, l.lh, l.box.on, l.box.pad, FONT_VER, "letterSpacing" in c]);
  if (l._tl && l._tl.k === memo) return l._tl.m;
  const base = baseStyle(l), per = expand(l), lines = []; let i = 0;
  for (const t of String(l.text).split("\n")) {
    const spans = []; let x = 0, maxS = 0;
    for (let j = 0; j < t.length;) {
      const key = q => (l.fill2 && per[i + q].fill !== undefined ? "o|" : "") + CHAR_KEYS.map(k => per[i + q][k] ?? base[k]).join("|"), k0 = key(j);
      let k = j + 1; while (k < t.length && key(k) === k0) k++;
      const st = Object.assign({}, base, per[i + j]), s = t.slice(j, k);
      c.font = `${st.size}px "${st.font}"`; const w = c.measureText(s).width;
      spans.push({ t: s, x, w, st, own: per[i + j].fill !== undefined }); x += w; maxS = Math.max(maxS, st.size); j = k;
    }
    if (!maxS) maxS = l.size;
    lines.push({ spans, w: t.length ? Math.max(0, x - l.ls) : 0, size: maxS, lh: maxS * l.lh }); i += t.length + 1;
  }
  let y = 0; for (const ln of lines) { ln.top = y; y += ln.lh; }
  const w = Math.max(1, ...lines.map(x => x.w));
  let ms = Math.max(l.strokes[0].width, l.strokes[1].width);
  for (const r of l.runs || []) ms = Math.max(ms, r.s1w || 0, r.s2w || 0);
  const pad = (l.box.on ? l.box.pad : 0) + ms / 2;
  const m = { lines, w, h: y, pad, natW: w + pad * 2, natH: y + pad * 2 };
  l._tl = { k: memo, m }; return m;
}
function fitText(l) { const m = textLayout(ctx, l); l.w = m.natW * (l.hs || 1); l.h = m.natH * (l.vs || 1); }
function refitKeep(l) {  // 글자 크기가 바뀌어도 정렬 기준점(위쪽 왼/가운데/오른쪽)이 화면에서 그대로 있게
  const fx = l.align === "center" ? 0.5 : l.align === "right" ? 1 : 0;
  const a = (l.rot || 0) * RAD, c = Math.cos(a), s = Math.sin(a), t = Math.tan((l.skew || 0) * RAD);
  const world = (L0) => { const lx = (fx - 0.5) * L0.w + t * L0.h / 2, ly = -L0.h / 2; return [L0.x + L0.w / 2 + lx * c - ly * s, L0.y + L0.h / 2 + lx * s + ly * c]; };
  const [px, py] = world(l); fitText(l);
  const [qx, qy] = world(l); l.x += px - qx; l.y += py - qy;
}

/* ---------- 그리기 ---------- */
function rrect(c, x, y, w, h, r) { r = Math.max(0, Math.min(r, w / 2, h / 2)); c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath(); }
function slantPath(c, w, h, s) { const o = s * h; c.beginPath(); c.moveTo(o > 0 ? o : 0, 0); c.lineTo(w + (o < 0 ? o : 0), 0); c.lineTo(w - (o > 0 ? o : 0), h); c.lineTo(o < 0 ? -o : 0, h); c.closePath(); }
function linGrad(c, w, h, angle, c1, c2) {
  const a = angle * RAD, cx = w / 2, cy = h / 2, r = Math.abs(Math.cos(a)) * w / 2 + Math.abs(Math.sin(a)) * h / 2;
  const g = c.createLinearGradient(cx - Math.cos(a) * r, cy - Math.sin(a) * r, cx + Math.cos(a) * r, cy + Math.sin(a) * r);
  g.addColorStop(0, c1); g.addColorStop(1, c2); return g;
}
function drawText(c, l) {
  const m = textLayout(c, l), pad = m.pad;
  c.scale(l.w / m.natW, l.h / m.natH);
  const lx = ln => pad + (l.align === "center" ? (m.w - ln.w) / 2 : l.align === "right" ? m.w - ln.w : 0);
  if (l.box.on) { c.fillStyle = l.box.color; for (const ln of m.lines) { if (!ln.w) continue; rrect(c, lx(ln) - l.box.pad, pad + ln.top - l.box.pad * 0.6, ln.w + l.box.pad * 2, ln.lh + l.box.pad * 1.2, l.box.radius); c.fill(); } }
  c.textBaseline = "top"; c.lineJoin = "round"; c.miterLimit = 2;
  const items = [];
  for (const ln of m.lines) { const x0 = lx(ln), y0 = pad + ln.top + (ln.lh - ln.size) * 0.5; for (const s of ln.spans) items.push(Object.assign({}, s, { X: x0 + s.x, Y: y0 + (ln.size - s.st.size) * 0.8 })); }
  const font = s => { c.font = `${s.st.size}px "${s.st.font}"`; };
  for (const pass of [0, 1]) for (const s of items) {
    const a = [{ c: s.st.s1c, w: s.st.s1w }, { c: s.st.s2c, w: s.st.s2w }].sort((p, q) => q.w - p.w)[pass];
    if (a.w > 0) { font(s); c.strokeStyle = a.c; c.lineWidth = a.w; c.strokeText(s.t, s.X, s.Y); }
  }
  const grad = l.fill2 ? linGrad(c, m.natW, m.natH, l.gradAngle, l.fill, l.fill2) : null;
  for (const s of items) { font(s); c.fillStyle = grad && !s.own ? grad : s.st.fill; c.fillText(s.t, s.X, s.Y); }
}
function drawImageContent(c, l) {
  const im = l._edit || img(l.src);
  if (!im) { c.fillStyle = "#333"; c.fillRect(0, 0, l.w, l.h); return; }
  if (l.slant) { slantPath(c, l.w, l.h, l.slant); c.clip(); }
  if (l.flipX) { c.translate(l.w, 0); c.scale(-1, 1); }
  const IW = im.naturalWidth || im.width, IH = im.naturalHeight || im.height;
  const sx = IW * (l.cropL || 0), sy = IH * (l.cropT || 0), iw = Math.max(1, IW * (1 - (l.cropL || 0) - (l.cropR || 0))), ih = Math.max(1, IH * (1 - (l.cropT || 0) - (l.cropB || 0))), ar = iw / ih; let dw, dh;
  if (l.fit === "cover") { if (l.w / l.h > ar) { dw = l.w; dh = l.w / ar; } else { dh = l.h; dw = l.h * ar; } }
  else { if (l.w / l.h > ar) { dh = l.h; dw = l.h * ar; } else { dw = l.w; dh = l.w / ar; } }
  const dx = (l.w - dw) * l.fx, dy = (l.h - dh) * l.fy;
  const T = c.getTransform(), bs = l.blur ? l.blur * Math.hypot(T.a, T.b) : 0;  // 흐림은 캔버스 배율과 무관하게 문서 px 기준
  const f = `brightness(${l.bright}%) contrast(${l.contrast}%) saturate(${l.sat}%)${l.hue ? ` hue-rotate(${l.hue}deg)` : ""}${bs ? ` blur(${bs}px)` : ""}`;
  if (f !== "brightness(100%) contrast(100%) saturate(100%)") c.filter = f;
  if (bs) { const p = l.blur * 3; c.save(); c.beginPath(); c.rect(0, 0, l.w, l.h); c.clip(); c.drawImage(im, sx, sy, iw, ih, dx - p, dy - p, dw + 2 * p, dh + 2 * p); c.restore(); }
  else c.drawImage(im, sx, sy, iw, ih, dx, dy, dw, dh);
  c.filter = "none";
  if (l.vignette) {
    const g = c.createRadialGradient(l.w / 2, l.h / 2, Math.min(l.w, l.h) * 0.25, l.w / 2, l.h / 2, Math.hypot(l.w, l.h) / 2);
    g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, `rgba(0,0,0,${l.vignette / 100})`); c.fillStyle = g; c.fillRect(0, 0, l.w, l.h);
  }
}
function drawShape(c, l) {
  c.fillStyle = l.fill2 ? linGrad(c, l.w, l.h, l.gradAngle, l.fill, l.fill2) : l.fill;
  c.strokeStyle = l.stroke.color; c.lineWidth = l.stroke.width;
  if (l.shape === "burst") {
    const cx = l.w / 2, cy = l.h / 2, R = Math.hypot(l.w, l.h) / 2, r0 = R * l.inner;
    for (let i = 0; i < l.lines; i++) {
      const a = i / l.lines * Math.PI * 2 + (i % 3) * 0.013, wd = 0.012 + (i * 37 % 11) / 900;
      c.beginPath(); c.moveTo(cx + Math.cos(a) * r0 * (0.9 + (i * 17 % 7) / 30), cy + Math.sin(a) * r0 * (0.9 + (i * 13 % 7) / 30));
      c.lineTo(cx + Math.cos(a - wd) * R * 1.1, cy + Math.sin(a - wd) * R * 1.1); c.lineTo(cx + Math.cos(a + wd) * R * 1.1, cy + Math.sin(a + wd) * R * 1.1);
      c.closePath(); c.fill();
    }
    return;
  }
  if (l.shape === "ellipse") { c.beginPath(); c.ellipse(l.w / 2, l.h / 2, l.w / 2, l.h / 2, 0, 0, Math.PI * 2); }
  else if (l.shape === "slant") slantPath(c, l.w, l.h, l.slant ?? 0.35);
  else if (l.shape === "arrow") { const w = l.w, h = l.h, t = h * 0.32; c.beginPath(); c.moveTo(0, h / 2 - t / 2); c.lineTo(w * 0.62, h / 2 - t / 2); c.lineTo(w * 0.62, 0); c.lineTo(w, h / 2); c.lineTo(w * 0.62, h); c.lineTo(w * 0.62, h / 2 + t / 2); c.lineTo(0, h / 2 + t / 2); c.closePath(); }
  else if (l.shape === "star") { c.beginPath(); for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, r = i % 2 ? 0.45 : 1; c.lineTo(l.w / 2 + Math.cos(a) * l.w / 2 * r, l.h / 2 + Math.sin(a) * l.h / 2 * r); } c.closePath(); }
  else if (l.shape === "bubble") {
    const w = l.w, h = l.h * 0.8, r = Math.max(0, Math.min(Math.min(w, h) * 0.3, w / 2, h / 2)), a = Math.max(w * 0.22, r), b = Math.max(a + 1, w * 0.4);
    c.beginPath(); c.moveTo(r, 0); c.arcTo(w, 0, w, h, r); c.arcTo(w, h, 0, h, r); c.lineTo(b, h); c.lineTo(w * 0.16, l.h); c.lineTo(a, h); c.arcTo(0, h, 0, 0, r); c.arcTo(0, 0, w, 0, r); c.closePath();
  }
  else rrect(c, 0, 0, l.w, l.h, l.radius);
  c.fill(); if (l.stroke.width > 0) c.stroke();
}
function drawContent(c, l) { if (l.type === "image") drawImageContent(c, l); else if (l.type === "shape") drawShape(c, l); else drawText(c, l); }
const hasFx = l => (l.warp.style !== "none" && l.warp.bend) || l.shadow.on || l.glow.on || l.outline.on || l.overlay.on || l.fade.on || l.extrude.on || (l.blend && l.blend !== "source-over");
function fxBleed(l) {  // 상자 밖으로 삐져나오는 내용 (행간 좁은 글자, 도형 테두리, 집중선)
  if (l.type === "text") { const sz = Math.max(l.size, ...(l.runs || []).map(r => r.size || 0)), m0 = textLayout(ctx, l); return (Math.max(0, (1 - l.lh) * sz / 2) + sz * 0.06) * (l.h / m0.natH); }
  if (l.type === "shape") {
    if (l.shape === "burst") return Math.max(0, Math.hypot(l.w, l.h) / 2 * 1.1 - Math.min(l.w, l.h) / 2);
    if (l.stroke.width > 0) return l.stroke.width / 2 * (l.shape === "ellipse" ? 1 : l.shape === "rect" ? 1.5 : 5);
  }
  return 0;
}
function shapeMargin(l, bleed = fxBleed(l)) {  // 모양(내용+획+돌출)까지의 여백
  const ow = l.outline.on ? l.outline.width : 0, ex = l.extrude.on ? l.extrude.depth : 0;
  return Math.ceil(2 + bleed + ow + ex);
}
function fxMargin(l) {
  const bleed = fxBleed(l), ow = l.outline.on ? l.outline.width : 0, ex = l.extrude.on ? l.extrude.depth : 0;
  let m = shapeMargin(l, bleed);
  if (l.glow.on) m = Math.max(m, bleed + ex + l.glow.size * 1.6 + ow);
  if (l.shadow.on) m = Math.max(m, bleed + ex + l.shadow.blur * 1.6 + Math.max(Math.abs(l.shadow.dx), Math.abs(l.shadow.dy)) + ow);
  if (l.warp.style !== "none") m += Math.abs(l.warp.bend) * l.h * 0.55;
  return Math.ceil(m);
}
function newCanvas(w, h) { const c = document.createElement("canvas"); c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h)); return c; }
function tint(src, color) { const c = newCanvas(src.width, src.height), g = c.getContext("2d"); g.drawImage(src, 0, 0); g.globalCompositeOperation = "source-in"; g.fillStyle = color; g.fillRect(0, 0, c.width, c.height); return c; }
// 레이어 한 장을 효과(획·광선·그림자·색 덮기)까지 합쳐 비트맵으로 — 위치만 바뀌면 다시 그리지 않음
const BM_SKIP = new Set(["x", "y", "rot", "skew", "opacity", "blend", "name", "hidden", "locked", "gid", "id"]);  // 비트맵 밖에서 적용되는 값
const SHAPE_SKIP = new Set([...BM_SKIP, "shadow", "glow", "warp"]);
function layerBitmap(l, sIn) {
  const s = Math.min(2, 2 ** Math.ceil(Math.log2(Math.max(sIn, 0.125))));  // 배율을 계단으로 → 확대/축소마다 다시 그리지 않음
  const imReady = l.type === "image" ? (l._edit ? "e" + (l._ev || 0) : img(l.src) ? 1 : 0) : 0;
  const tail = `|${imReady}|${FONT_VER}`;
  const key = JSON.stringify(l, (k, v) => (BM_SKIP.has(k) || k.startsWith("_") ? undefined : v)) + tail;
  const cache = l._bm || (l._bm = {}), hit = cache[s];
  if (hit && hit.key === key) return hit;
  if (hit && hit.prev && hit.prev.key === key) { const p = hit.prev; hit.prev = null; p.prev = hit; return (cache[s] = p); }  // 바로 전 모습 (되돌리기·다시 실행·켰다 끄기)
  const m = fxMargin(l), t0 = performance.now();
  let k = s; const area = (l.w + 2 * m) * (l.h + 2 * m) * k * k; if (area > 16e6) k = s * Math.sqrt(16e6 / area);
  const cw = (l.w + 2 * m) * k, ch = (l.h + 2 * m) * k;
  // 1단계: 모양(내용·마스크·색 덮기·획·돌출) — 그림자·광선·뒤틀기만 바뀌면 다시 쓰기
  const sKey = JSON.stringify(l, (kk, v) => (SHAPE_SKIP.has(kk) || kk.startsWith("_") ? undefined : v)) + tail + `|${k}`;
  const sc = l._fxS || (l._fxS = {});
  let st = sc[s];
  if (!st || st.key !== sKey) {
    const mS = shapeMargin(l), sw = (l.w + 2 * mS) * k, sh = (l.h + 2 * mS) * k;
    const base = newCanvas(sw, sh), g = base.getContext("2d");
    g.setTransform(k, 0, 0, k, mS * k, mS * k); drawContent(g, l); g.setTransform(1, 0, 0, 1, 0, 0);
    if (l.fade.on) {  // 레이어 마스크: 방향(angle)으로 start~end 구간에서 서서히 투명
      const a = l.fade.angle * RAD, cx = l.w / 2, cy = l.h / 2, r = Math.abs(Math.cos(a)) * l.w / 2 + Math.abs(Math.sin(a)) * l.h / 2;
      g.setTransform(k, 0, 0, k, mS * k, mS * k);
      const gr = g.createLinearGradient(cx - Math.cos(a) * r, cy - Math.sin(a) * r, cx + Math.cos(a) * r, cy + Math.sin(a) * r);
      const s0 = clamp(Math.min(l.fade.start, l.fade.end), 0, 1), s1 = clamp(Math.max(l.fade.start, l.fade.end), 0, 1);
      gr.addColorStop(0, "rgba(0,0,0,1)"); gr.addColorStop(Math.min(s0, 0.999), "rgba(0,0,0,1)"); gr.addColorStop(Math.min(1, Math.max(s1, s0 + 0.001)), "rgba(0,0,0,0)"); gr.addColorStop(1, "rgba(0,0,0,0)");
      g.globalCompositeOperation = "destination-in"; g.fillStyle = gr; g.fillRect(-mS, -mS, l.w + 2 * mS, l.h + 2 * mS);
      g.globalCompositeOperation = "source-over"; g.setTransform(1, 0, 0, 1, 0, 0);
    }
    if (l.overlay.on) { g.globalCompositeOperation = "source-atop"; g.globalAlpha = l.overlay.opacity; g.fillStyle = l.overlay.color; g.fillRect(0, 0, base.width, base.height); g.globalAlpha = 1; g.globalCompositeOperation = "source-over"; }
    let shape = base;
    if (l.outline.on && l.outline.width > 0) {
      const sil = tint(base, l.outline.color), r = l.outline.width * k, u = newCanvas(sw, sh), ug = u.getContext("2d");
      const n = clamp(Math.round(r * 1.2), 12, 48);
      for (const f of r > 6 ? [1, 0.66, 0.33] : [1]) for (let a = 0; a < n; a++) { const t = a / n * Math.PI * 2; ug.drawImage(sil, Math.cos(t) * r * f, Math.sin(t) * r * f); }
      ug.drawImage(base, 0, 0); shape = u;
    }
    if (l.extrude.on && l.extrude.depth > 0) {  // 3D 돌출: 같은 모양을 한 방향으로 겹겹이 쌓아 입체감
      const sil = tint(shape, l.extrude.color), u = newCanvas(sw, sh), ug = u.getContext("2d"), a = l.extrude.angle * RAD, d = l.extrude.depth * k;
      for (let i = Math.ceil(d); i >= 1; i--) ug.drawImage(sil, Math.cos(a) * i, Math.sin(a) * i);
      ug.drawImage(shape, 0, 0); shape = u;
    }
    st = sc[s] = { key: sKey, c: shape, mS };
  }
  // 2단계: 그림자·광선을 깔고 모양을 올림
  const out = newCanvas(cw, ch), o = out.getContext("2d"), FAR = 20000, off = (m - st.mS) * k, shape = st.c;
  if (l.shadow.on) {
    o.save(); o.globalAlpha = l.shadow.opacity ?? 1; o.shadowColor = l.shadow.color; o.shadowBlur = l.shadow.blur * k;
    o.shadowOffsetX = l.shadow.dx * k + FAR; o.shadowOffsetY = l.shadow.dy * k; o.drawImage(shape, off - FAR, off); o.restore();
  }
  if (l.glow.on && l.glow.size > 0) {
    o.save(); o.globalAlpha = l.glow.opacity ?? 1; o.shadowColor = l.glow.color; o.shadowBlur = l.glow.size * k; o.shadowOffsetX = FAR;
    o.drawImage(shape, off - FAR, off); o.drawImage(shape, off - FAR, off); o.restore();
  }
  o.drawImage(shape, off, off);
  const res = l.warp.style !== "none" && l.warp.bend ? warpCanvas(out, l.warp, l.h * k, m * k, l.w * k) : out;
  l._bmCost = performance.now() - t0;
  cache[s] = { key, c: res, m, prev: hit && hit.c.width * hit.c.height <= 2e6 ? Object.assign(hit, { prev: null }) : null };  // 한 장 전 것까지만 (아주 큰 그림은 메모리 때문에 안 들고 있음)
  return cache[s];
}
// 뒤틀기: 세로 띠로 잘라 위아래로 밀거나 늘림 (아치·깃발·부풀리기)
function warpCanvas(src, wp, hpx, ox, cw) {
  const w = src.width, h = src.height, dst = newCanvas(w, h), g = dst.getContext("2d"), b = wp.bend, st = 2, span = Math.max(1, cw);
  for (let x = 0; x < w; x += st) {
    const t = clamp((x + st / 2 - ox) / span * 2 - 1, -1, 1);
    if (wp.style === "bulge") { const sc = Math.max(0.1, 1 + b * (1 - t * t)); g.drawImage(src, x, 0, st, h, x, h / 2 - h * sc / 2, st, h * sc); continue; }
    const dy = wp.style === "flag" ? -b * hpx * 0.5 * Math.sin(t * Math.PI) : -b * hpx * 0.5 * (1 - t * t);
    g.drawImage(src, x, 0, st, h, x, dy, st, h);
  }
  return dst;
}
// 비트맵으로 합쳐 그려야 하는 레이어: 효과가 있거나, 반투명한 글자·도형(테두리와 채우기가 따로 비치지 않게)
const needBitmap = l => !l._fast && (hasFx(l) || (l.opacity < 1 && l.type !== "image"));
function drawLayer(c, l, s) {
  if (l.hidden) return;
  c.save();
  try {
    c.globalAlpha = l.opacity;
    c.translate(l.x + l.w / 2, l.y + l.h / 2); c.rotate(l.rot * RAD);
    if (l.skew) c.transform(1, 0, -Math.tan(l.skew * RAD), 1, 0, 0);
    c.translate(-l.w / 2, -l.h / 2);
    if (needBitmap(l)) {
      const bm = layerBitmap(l, s);
      if (l.blend && l.blend !== "source-over") c.globalCompositeOperation = l.blend;
      c.drawImage(bm.c, -bm.m, -bm.m, l.w + 2 * bm.m, l.h + 2 * bm.m);
    } else drawContent(c, l);
  } finally { c.restore(); }
}
function renderDoc(c, doc, sc) {
  const w = doc.w || 1280, h = doc.h || 720;
  c.save(); c.setTransform(sc, 0, 0, sc, 0, 0);
  c.fillStyle = doc.bg || "#000"; c.fillRect(0, 0, w, h);
  for (const l of doc.layers) { try { drawLayer(c, l, sc); } catch (e) { console.error("레이어 그리기 실패", l.id, e); } }  // 레이어 하나가 깨져도 나머지는 그림
  c.restore();
}
