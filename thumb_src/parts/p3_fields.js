
/* ---------- 값 정의 (표시 단위 기준) ---------- */
const FM = {
  x: { min: -1920, max: 3840, unit: "px" }, y: { min: -1920, max: 3840, unit: "px" }, w: { min: 1, max: 5000, unit: "px" }, h: { min: 1, max: 5000, unit: "px" },
  rot: { min: -180, max: 180, step: 0.1, unit: "°" }, skew: { min: -60, max: 60, step: 0.5, unit: "°" }, opacity: { min: 0, max: 100, mul: 100, unit: "%" },
  "c.size": { min: 8, max: 400, unit: "px" }, "c.s1w": { min: 0, max: 80, unit: "px" }, "c.s2w": { min: 0, max: 60, unit: "px" },
  lh: { min: 0.6, max: 2, step: 0.01 }, ls: { min: -20, max: 80, step: 0.5, unit: "px" }, hs: { min: 30, max: 300, mul: 100, unit: "%" }, vs: { min: 30, max: 300, mul: 100, unit: "%" },
  gradAngle: { min: 0, max: 360, unit: "°" }, "glow.size": { min: 0, max: 150, unit: "px" }, "glow.opacity": { min: 0, max: 100, mul: 100, unit: "%" },
  "shadow.dx": { min: -100, max: 100, unit: "px" }, "shadow.dy": { min: -100, max: 100, unit: "px" }, "shadow.blur": { min: 0, max: 80, unit: "px" }, "shadow.opacity": { min: 0, max: 100, mul: 100, unit: "%" },
  "outline.width": { min: 0, max: 60, unit: "px" }, "overlay.opacity": { min: 0, max: 100, mul: 100, unit: "%" },
  "fade.angle": { min: 0, max: 360, unit: "°" }, "fade.start": { min: 0, max: 100, mul: 100, unit: "%" }, "fade.end": { min: 0, max: 100, mul: 100, unit: "%" },
  "warp.bend": { min: -100, max: 100, mul: 100, unit: "%" }, "extrude.depth": { min: 0, max: 60, unit: "px" }, "extrude.angle": { min: 0, max: 360, unit: "°" },
  "box.pad": { min: 0, max: 80, unit: "px" }, "box.radius": { min: 0, max: 80, unit: "px" },
  fx: { min: 0, max: 100, mul: 100, unit: "%" }, fy: { min: 0, max: 100, mul: 100, unit: "%" }, slant: { min: -100, max: 100, mul: 100, unit: "%" },
  bright: { min: 0, max: 200, unit: "%" }, contrast: { min: 0, max: 200, unit: "%" }, sat: { min: 0, max: 200, unit: "%" }, hue: { min: -180, max: 180, unit: "°" },
  blur: { min: 0, max: 40, unit: "px" }, cropT: { min: 0, max: 45, mul: 100, unit: "%" }, cropB: { min: 0, max: 45, mul: 100, unit: "%" }, cropL: { min: 0, max: 45, mul: 100, unit: "%" }, cropR: { min: 0, max: 45, mul: 100, unit: "%" }, vignette: { min: 0, max: 100, unit: "%" },
  radius: { min: 0, max: 400, unit: "px" }, "stroke.width": { min: 0, max: 60, unit: "px" }, lines: { min: 12, max: 160 }, inner: { min: 0, max: 90, mul: 100, unit: "%" },
  "b.size": { min: 2, max: 400, unit: "px" }, "b.hard": { min: 0, max: 100, unit: "%" },
};
const REBUILD = new Set(["shape"]);
const REFIT = new Set(["lh", "ls", "hs", "vs", "box.on", "box.pad"]);
const XFK = ["x", "y", "w", "h", "rot", "skew"];
const getPath = (o, k) => k.split(".").reduce((a, p) => (a == null ? a : a[p]), o);
function setPath(o, k, v) { const ps = k.split("."), last = ps.pop(); const t = ps.reduce((a, p) => (a[p] = a[p] || {}), o); t[last] = v; }
function fmtNum(k, v) { const m = FM[k] || {}, d = v * (m.mul || 1); return (m.step || 1) < 0.1 ? Math.round(d * 100) / 100 : Math.round(d * 10) / 10; }

/* ---------- 필드 HTML ---------- */
const SCRUB_T = "좌우로 끌면 값이 바뀌어요 (Shift: 10배 · Alt: 미세)";
function F(k, label, o = {}) {
  const m = FM[k] || {}, t = o.t || "num";
  if (t === "num") return `<div class="f"><label class="scrub" data-scrub="${k}" title="${SCRUB_T}">${label}</label><input type="range" data-k="${k}" min="${m.min}" max="${m.max}" step="${m.step || 1}"><span class="numw"><input class="num" type="number" data-k="${k}" step="${m.step || 1}"><i>${m.unit || ""}</i></span></div>`;
  if (t === "col") return `<div class="f"><label>${label}</label><span class="numw"><input type="color" data-k="${k}"><span class="hint" data-hex="${k}"></span></span><span></span></div>`;
  if (t === "chk") return `<div class="f"><label>${label}</label><input type="checkbox" data-k="${k}"><span></span></div>`;
  if (t === "sel") return `<div class="f"><label>${label}</label><select class="s" data-k="${k}">${o.opts.map(([v, x]) => `<option value="${v}">${x}</option>`).join("")}</select><span></span></div>`;
  if (t === "seg") return `<div class="f"><label>${label}</label><div class="seg" data-segk="${k}">${o.opts.map(([v, x]) => `<button data-v="${v}">${x}</button>`).join("")}</div><span></span></div>`;
  return `<div class="f"><label>${label}</label><input class="s" data-k="${k}" placeholder="${o.ph || ""}"><span></span></div>`;
}
function O(k, label) { const m = FM[k] || {}; return `<span class="f2">${label ? `<label class="scrub" data-scrub="${k}" title="${SCRUB_T}">${label}</label>` : ""}<input class="num" type="number" data-k="${k}" step="${m.step || 1}"><span class="hint">${m.unit || ""}</span></span>`; }
function swatches(k, n = 20) { const cs = [...new Set([...RECENT, ...PRESETS])].slice(0, n); return `<div class="swatches" data-swk="${k}">${cs.map(c => `<span class="sw" data-swc="${c}" style="background:${c}" title="${c}"></span>`).join("")}</div>`; }
function fx(name, title, body, onKey) { return `<details class="fx" data-fx="${name}" ${fxOpen.has(name) ? "open" : ""}><summary>${onKey ? `<input type="checkbox" data-k="${onKey}" title="켜기/끄기">` : "▸"} ${title}</summary><div>${body}</div></details>`; }
const FONT_OPTS = Object.entries(FONTS).map(([k, v]) => [v, k]);
const ALIGN_HTML = [["l", "⇤", "왼쪽 맞춤"], ["c", "⇹", "가로 가운데"], ["r", "⇥", "오른쪽 맞춤"], ["t", "⤒", "위 맞춤"], ["m", "⇳", "세로 가운데"], ["b", "⤓", "아래 맞춤"]].map(([v, i, t]) => `<button class="ic" data-act="al" data-v="${v}" title="${t}">${i}</button>`).join("");
const DIST_HTML = `<button class="ic" data-act="dist" data-v="h" title="가로 간격 똑같이 (3개 이상)">⇿</button><button class="ic" data-act="dist" data-v="v" title="세로 간격 똑같이 (3개 이상)">⇳̲</button>`;

/* ---------- 값 읽기 · 쓰기 ---------- */
function curRange(l) { if (textSel && textSel.id === l.id && textSel.e > textSel.s) return [textSel.s, textSel.e]; return null; }
function charGet(l, k) { const b = baseStyle(l), r = curRange(l); if (!r) return b[k]; const st = expand(l)[r[0]] || {}; return st[k] ?? b[k]; }
function setBase(l, k, v) { if (k === "s1c") l.strokes[0].color = v; else if (k === "s1w") l.strokes[0].width = v; else if (k === "s2c") l.strokes[1].color = v; else if (k === "s2w") l.strokes[1].width = v; else l[k] = v; }
function charSet(l, k, v) {
  let r = curRange(l);
  if (r && r[0] <= 0 && r[1] >= l.text.length) r = null;  // 전체 선택 = 기본 스타일 바꾸기
  if (r) { const a = expand(l); for (let i = r[0]; i < r[1]; i++) a[i][k] = v; compress(l, a); }
  else { setBase(l, k, v); for (const run of l.runs) delete run[k]; compress(l, expand(l)); if (k === "size") delete l.fitBox; }
  refitKeep(l);
}
function applyTextEdit(l, nt, caret) {
  const ot = l.text; if (ot === nt) return;
  const a = expand(l);
  const diff = qMax => {  // 뒤에서부터 같은 부분(커서 뒤까지만) → 앞에서부터 같은 부분
    let q = 0; while (q < qMax && q < ot.length && q < nt.length && ot[ot.length - 1 - q] === nt[nt.length - 1 - q]) q++;
    let p = 0; while (p < ot.length - q && p < nt.length - q && ot[p] === nt[p]) p++;
    return [p, q];
  };
  let [p, q] = diff(Infinity);
  if (caret != null) { const [p2, q2] = diff(Math.max(0, nt.length - caret)); if (ot.length - p2 - q2 <= ot.length - p - q) [p, q] = [p2, q2]; }  // 같은 크기의 바뀜이면 커서 기준을 믿음
  const del = ot.length - p - q;
  const inh = Object.assign({}, del > 0 ? a[p] : p > 0 && ot[p - 1] !== "\n" ? a[p - 1] : a[p] || {});
  a.splice(p, ot.length - p - q, ...Array.from({ length: nt.length - p - q }, () => Object.assign({}, inh)));
  l.text = nt; compress(l, a); refitKeep(l, true);
}
function scaleTextStyle(l, k) {
  const s = v => Math.round(v * k * 10) / 10;
  l.size = Math.max(1, s(l.size)); l.strokes.forEach(st => (st.width = s(st.width))); l.ls = s(l.ls);
  l.shadow.dx = s(l.shadow.dx); l.shadow.dy = s(l.shadow.dy); l.shadow.blur = s(l.shadow.blur);
  l.box.pad = s(l.box.pad); l.box.radius = s(l.box.radius); l.glow.size = s(l.glow.size); l.outline.width = s(l.outline.width); l.extrude.depth = s(l.extrude.depth);
  for (const r of l.runs) { if (r.size !== undefined) r.size = Math.max(1, s(r.size)); if (r.s1w !== undefined) r.s1w = s(r.s1w); if (r.s2w !== undefined) r.s2w = s(r.s2w); }
}
// 글자 레이어 크기를 바꾸면: 같은 비율이면 글자 크기에 반영, 다른 비율이면 가로/세로 비율로 저장
function bakeText(l) {
  const m = textLayout(ctx, l), kx = l.w / (m.natW * l.hs), ky = l.h / (m.natH * l.vs), cx = l.x + l.w / 2, cy = l.y + l.h / 2;
  if (Math.abs(kx - ky) <= 0.004 * Math.max(kx, ky)) { if (Math.abs(kx - 1) < 1e-4) return; scaleTextStyle(l, kx); fitText(l); delete l.fitBox; }
  else { l.hs = l.w / m.natW; l.vs = l.h / m.natH; }
  l.x = cx - l.w / 2; l.y = cy - l.h / 2;
}
function resizeTo(l, k, v) {
  const cx = l.x + l.w / 2, cy = l.y + l.h / 2, ow = l.w, oh = l.h; v = Math.max(1, v);
  if (k === "w") { l.w = v; if (lockRatio) l.h = oh * v / ow; } else { l.h = v; if (lockRatio) l.w = ow * v / oh; }
  l.x = cx - l.w / 2; l.y = cy - l.h / 2; if (l.type === "text") bakeText(l);
}
function scaleGroup(o, ax, ay, kx, ky) {
  o.forEach(t => {
    if (t.l.locked) return; const cx = ax + (t.x + t.w / 2 - ax) * kx, cy = ay + (t.y + t.h / 2 - ay) * ky;
    const a = (t.l.rot || 0) * RAD, c = Math.abs(Math.cos(a)), s = Math.abs(Math.sin(a));
    const lkx = Math.hypot(kx * c, ky * s), lky = Math.hypot(kx * s, ky * c);  // 회전된 레이어는 자기 축 기준으로
    t.l.w = Math.max(1, t.w * lkx); t.l.h = Math.max(1, t.h * lky); t.l.x = cx - t.l.w / 2; t.l.y = cy - t.l.h / 2;
  });
}
function groupSet(ls, k, v) {
  const b = selBox();
  if (k === "x" || k === "y") { const d = v - b[k]; ls.forEach(l => { if (!l.locked) l[k] += d; }); return; }
  let kx = k === "w" ? v / b.w : 1, ky = k === "h" ? v / b.h : 1; if (lockRatio) { if (k === "w") ky = kx; else kx = ky; }
  scaleGroup(ls.map(l => ({ l, x: l.x, y: l.y, w: l.w, h: l.h })), b.x + b.w / 2, b.y + b.h / 2, kx, ky);
  ls.filter(l => l.type === "text").forEach(bakeText);
}
function getV(k) {
  if (k === "b.size") return brush.size; if (k === "b.hard") return brush.hard;
  if (!D) return null; if (k === "bg") return D.doc.bg || "#000000";
  const ls = selLs(); if (!ls.length) return null;
  if (ls.length > 1) { if (["x", "y", "w", "h"].includes(k)) return selBox()[k]; if (k === "opacity") return ls[0].opacity; return null; }
  const l = ls[0];
  if (k.startsWith("c.")) return l.type === "text" ? charGet(l, k.slice(2)) : null;
  if (k === "grad.on") return !!l.fill2;
  if (k === "fill2") return l.fill2 || "#000000";
  if (k === "slant" && l.type === "shape") return l.slant || 0.35;
  if (k === "name") return l.name;
  return getPath(l, k);
}
function geoRestore() {  // 이번 입력을 시작할 때의 레이어 상태로 되돌림 (같은 객체 유지)
  const ls = selLs();
  if (!geoBase) { geoBase = { layers: ls.map(clone) }; return; }
  for (const o of geoBase.layers) { const cur = byId(o.id); if (!cur) continue; const f = clone(o); for (const k of Object.keys(f)) cur[k] = f[k]; }
}
function setV(k, v) {
  if (k === "b.size") { brush.size = clamp(Math.round(v), 2, 400); return; }
  if (k === "b.hard") { brush.hard = clamp(Math.round(v), 0, 100); return; }
  if (!D) return; if (k === "bg") { commitXform(); D.doc.bg = v; return; }
  const ls = selLs(); if (!ls.length) return;
  if (k === "x" || k === "y" || k === "w" || k === "h") geoRestore();
  if (ls.length > 1) {
    if (["x", "y", "w", "h"].includes(k)) groupSet(ls, k, v); else if (k === "opacity") ls.forEach(l => (l.opacity = clamp(v, 0, 1)));
    return;
  }
  const l = ls[0];
  if (l.locked && XFK.includes(k)) return;
  if (k === "w" || k === "h") resizeTo(l, k, v);
  else if (k === "opacity") l.opacity = clamp(v, 0, 1);
  else if (k === "rot") l.rot = norm180(v);
  else if (k.startsWith("c.")) { if (l.type !== "text") return; charSet(l, k.slice(2), k === "c.size" ? Math.max(1, v) : /w$/.test(k) ? Math.max(0, v) : v); }
  else if (k === "grad.on") l.fill2 = v ? l.fill2 || (l.type === "text" ? "#FF8A00" : "#000000") : "";
  else if ((k === "hs" || k === "vs") && v < 0.05) return;
  else setPath(l, k, v);
  if (l.type === "text" && REFIT.has(k)) refitKeep(l);
}
const XFK_SET = new Set(XFK);
function syncFields(except, only) {  // only: 이 값들만 (끄는 동안엔 위치·크기만 바뀜)
  const memo = new Map(), gv = k => { if (!memo.has(k)) memo.set(k, getV(k)); return memo.get(k); };  // 같은 값을 여러 칸이 보여 줌 → 한 번만 계산
  document.querySelectorAll("[data-k]").forEach(el => {
    if (el === except || el === document.activeElement || (only && !only.has(el.dataset.k))) return;
    const v = gv(el.dataset.k); if (v === null || v === undefined) return;
    if (el.type === "checkbox") { if (el.checked !== !!v) el.checked = !!v; return; }
    const s = String(el.type === "color" ? toHex(v) : el.type === "number" || el.type === "range" ? fmtNum(el.dataset.k, +v) : v);
    if (el.value !== s) el.value = s;  // 그대로면 안 건드림 (끌 때마다 화면 다시 계산 안 하게)
  });
  if (only) { if (textSel) renderAll(); return; }
  document.querySelectorAll("[data-hex]").forEach(s => { const v = gv(s.dataset.hex), t = v ? toHex(v).toUpperCase() : ""; if (s.textContent !== t) s.textContent = t; });
  document.querySelectorAll("[data-segk]").forEach(s => { const v = gv(s.dataset.segk); s.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === String(v))); });
  refreshCharUI(); if (textSel) renderAll();
}
function readEl(el) { const m = FM[el.dataset.k] || {}; if (el.type === "checkbox") return el.checked; if (el.type === "number" || el.type === "range") return +el.value / (m.mul || 1); return el.value; }

/* ---------- 이벤트 위임 ---------- */
document.addEventListener("input", e => {
  const el = e.target; if (!el.dataset || el.dataset.k === undefined) return;
  if (el.type === "number" && (el.value === "" || isNaN(+el.value))) return;
  liveStart(); setV(el.dataset.k, readEl(el)); renderAll(); syncFields(el);
  if (el.dataset.k === "name") renderLayers();
});
document.addEventListener("change", e => {
  const el = e.target; if (!el.dataset || el.dataset.k === undefined) return;
  if (el.type === "color") useColor(el.value);
  liveEnd(); renderLayers(); if (REBUILD.has(el.dataset.k)) { renderProps(); renderLS(); }
});
document.addEventListener("mousedown", e => {
  const s = e.target.closest && e.target.closest("[data-scrub]"); if (!s || e.button !== 0) return;
  e.preventDefault(); const k = s.dataset.scrub, m = FM[k] || {}, v0 = +getV(k); if (isNaN(v0)) return;
  const x0 = e.clientX, d0 = v0 * (m.mul || 1); liveStart(); document.body.style.cursor = "ew-resize";
  const mv = ev => {
    const st = (m.step || 1) * (ev.shiftKey ? 10 : 1) * (ev.altKey ? 0.1 : 1);
    let d = d0 + Math.round((ev.clientX - x0) / 2) * st; if (m.min !== undefined && !["x", "y"].includes(k)) d = clamp(d, m.min, m.max);
    setV(k, d / (m.mul || 1)); renderAll(); syncFields();
  };
  const up = () => { removeEventListener("mousemove", mv); removeEventListener("mouseup", up); document.body.style.cursor = ""; liveEnd(); renderLayers(); };
  addEventListener("mousemove", mv); addEventListener("mouseup", up);
});
document.addEventListener("click", e => {
  const sb = e.target.closest("[data-segk] button");
  if (sb) { const k = sb.closest("[data-segk]").dataset.segk; liveStart(); setV(k, sb.dataset.v); liveEnd(); renderAll(); syncFields(); if (REBUILD.has(k)) renderProps(); return; }
  const sw = e.target.closest("[data-swc]");
  if (sw) { const k = sw.closest("[data-swk]").dataset.swk, c = sw.dataset.swc; liveStart(); setV(k, c); liveEnd(); useColor(c); renderAll(); syncFields(); return; }
  const a = e.target.closest("[data-act]");
  if (a) { if (e.target.tagName === "INPUT" && e.target !== a) return; e.preventDefault(); const f = ACT[a.dataset.act]; if (f) f(a); }
});
document.addEventListener("toggle", e => { const d = e.target; if (d.dataset && d.dataset.fx) d.open ? fxOpen.add(d.dataset.fx) : fxOpen.delete(d.dataset.fx); }, true);

/* ---------- 맞춤 · 간격 ---------- */
function align(a) {
  const ls = selLs().filter(l => !l.locked); if (!ls.length) return;
  commit("맞춤"); const ref = ls.length > 1 ? bbox(ls) : { x: 0, y: 0, w: W, h: H };
  for (const l of ls) {
    const b = bbox([l]);
    if (a === "l") l.x += ref.x - b.x; if (a === "c") l.x += ref.x + ref.w / 2 - (b.x + b.w / 2); if (a === "r") l.x += ref.x + ref.w - (b.x + b.w);
    if (a === "t") l.y += ref.y - b.y; if (a === "m") l.y += ref.y + ref.h / 2 - (b.y + b.h / 2); if (a === "b") l.y += ref.y + ref.h - (b.y + b.h);
  }
  changed(false); syncFields();
}
function distribute(dir) {
  const ls = selLs().filter(l => !l.locked); if (ls.length < 3) return toast("간격 맞추기는 레이어를 3개 이상 골라야 해요");
  commit("간격 맞추기"); const key = dir === "h" ? "x" : "y", sz = dir === "h" ? "w" : "h";
  const items = ls.map(l => ({ l, b: bbox([l]) })).sort((p, q) => p.b[key] - q.b[key]);
  const all = bbox(ls), gap = (all[sz] - items.reduce((a, it) => a + it.b[sz], 0)) / (items.length - 1);
  let pos = all[key];
  items.forEach(it => { it.l[key] += pos - it.b[key]; pos += it.b[sz] + gap; });
  changed(false); syncFields();
}
