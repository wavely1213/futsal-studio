
/* ---------- 선택 ---------- */
const byId = id => (D ? D.doc.layers.find(l => l.id === id) : null);
const selLs = () => (D ? D.doc.layers.filter(l => selIds.includes(l.id)) : []);
const selL = () => { const a = selLs(); return a.length === 1 ? a[0] : null; };
function select(ids) {
  selIds = ids.filter(id => byId(id)); geoBase = null;
  if (textSel && !selIds.includes(textSel.id)) textSel = null;
  if (editing && !selIds.includes(editing.id)) endEdit();
  if (xform && (selIds.length !== xform.ids.length || selIds.some(id => !xform.ids.includes(id)))) commitXform();
  if ((tool === "brush" || tool === "eraser") && !(selL() && selL().orig)) { tool = "move"; markTool(); stage.style.cursor = "default"; $("bcur").style.display = "none"; }
  refreshUI(); if (selIds.length) showTab("props");
}
function refreshUI() { renderLayers(); renderProps(); renderOpts(); renderCP(); renderLS(); renderAll(); }

/* ---------- 되돌리기 · 저장 ---------- */
function snapshot() { return JSON.stringify(D.doc, dropPriv); }
let nudgeKey = null, undoDropped = 0;
function pushUndo(s, label) {
  nudgeKey = null;
  if (liveBefore !== null && liveBefore !== s && liveOwner === D) { undoStack.push(liveBefore); histNames.push("속성 변경"); liveBefore = null; }  // 진행 중이던 속성 편집 먼저
  undoStack.push(s); histNames.push(label || "편집");
  while (undoStack.length > 120) { undoStack.shift(); histNames.shift(); undoDropped++; }
  redoStack = [];
}
const undoMark = () => undoDropped + undoStack.length;
function cutUndoTo(mark) { undoStack.length = Math.max(0, Math.min(undoStack.length, mark - undoDropped)); histNames.length = undoStack.length; }
function commit(label) { pushUndo(snapshot(), label); }
function changed(full = true) { scheduleSave(); renderAll(); if (full) { renderLayers(); renderProps(); renderOpts(); renderCP(); renderLS(); } }
function restoreDoc(s) { const old = new Map(D.doc.layers.map(l => [l.id, l])); D.doc = JSON.parse(s); D.doc.layers.forEach(l => { normLayer(l); const b = old.get(l.id); if (b) { l._bm = b._bm; l._fxS = b._fxS; } }); W = D.doc.w; H = D.doc.h; selIds = selIds.filter(id => byId(id)); textSel = null; liveBefore = null; geoBase = null; }
function undo() { nudgeKey = null; if (editing) endEdit(); xform = null; if (!undoStack.length) return toast("더 되돌릴 게 없어요"); redoStack.push(snapshot()); histNames.pop(); restoreDoc(undoStack.pop()); applyView(true); changed(); }
function redo() { if (!redoStack.length) return; undoStack.push(snapshot()); histNames.push("다시 실행"); restoreDoc(redoStack.pop()); applyView(true); changed(); }
let liveBefore = null, liveOwner = null;
function liveStart() { if (liveBefore === null && D) { liveBefore = snapshot(); liveOwner = D; } }
function liveEnd() { if (liveBefore !== null && D && liveOwner === D && liveBefore !== snapshot()) { pushUndo(liveBefore, "속성 변경"); scheduleSave(); } liveBefore = null; liveOwner = null; geoBase = null; }
const cleanDocs = () => JSON.parse(JSON.stringify(DOCS, dropPriv));
const saveBody = () => JSON.stringify({ name: NAME, docs: DOCS }, dropPriv);  // 저장할 글 한 번에 (글→객체→글 두 번 돌리지 않게)
const postRaw = async (url, body) => (await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body })).json();
function scheduleSave() { if (!D) return; saveGen++; $("saved").textContent = "저장 중…"; clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 800); }
let gone = "";  // 이 영상의 이름이 바뀌었거나 보관함에서 빠짐 (서버 404 gone · D-073) → 더 저장하지 않고 알림 · 나가기는 막지 않음
async function saveNow() {
  clearTimeout(saveTimer); saveTimer = null; if (!D) return true; const g = saveGen;
  if (gone) { $("saved").textContent = "저장 안 함 · " + gone; return true; }
  let r = null;
  try { r = await postRaw("/api/thumb/save", saveBody()); } catch (e) {}
  if (r && r.gone) { gone = r.error || "이 영상의 이름이 바뀌었어요"; $("saved").textContent = "저장 안 함 · " + gone; toast(gone); return true; }
  if (!r || r.ok === false) {  // 저장 실패(Windows: 백신·OneDrive 가 잠깐 잡음 등) → 알리고 잠시 뒤 다시 (예전: '저장 중…'에 멈춤)
    if (g === saveGen) { $("saved").textContent = "저장 실패 · 잠시 뒤 다시 저장할게요"; saveTimer = setTimeout(saveNow, 3000); }
    return false;
  }
  if (g === saveGen) $("saved").textContent = "저장됨";  // 저장하는 사이 또 바뀌었으면 '저장 중' 유지
  renderDesigns();
  return true;
}
// 창을 닫거나 숨길 때 남은 변경을 바로 보냄 (keepalive)
function flushSave() { if (!D || gone || (!saveTimer && !editing)) return; clearTimeout(saveTimer); saveTimer = null; try { fetch("/api/thumb/save", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: saveBody() }); } catch (e) {} }
addEventListener("pagehide", flushSave); document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushSave(); });

/* ---------- 화면 (확대 · 이동) ---------- */
const cv = $("cv"), ctx = cv.getContext("2d"), stage = $("stage"), wrap = $("wrap");
const ZSTEPS = [0.08, 0.1, 0.125, 0.167, 0.2, 0.25, 0.333, 0.5, 0.667, 0.75, 1, 1.5, 2, 3, 4, 6, 8];
function fitView() {
  const sw = stage.clientWidth, sh = stage.clientHeight;
  Z = Math.max(0.05, Math.min((sw - 60) / W, (sh - 60) / H)); PX = (sw - W * Z) / 2; PY = (sh - H * Z) / 2; fitMode = true; applyView();
}
let zoomTimer = 0;
function setZoom(z, ax, ay, fast) {
  z = clamp(z, 0.05, 8);
  if (ax === undefined) { ax = stage.clientWidth / 2; ay = stage.clientHeight / 2; }
  const cx = (ax - PX) / Z, cy = (ay - PY) / Z; Z = z; PX = ax - cx * Z; PY = ay - cy * Z; fitMode = false;
  if (!fast) return applyView();
  cv.style.width = W * Z + "px"; cv.style.height = H * Z + "px";  // 휠 도중엔 늘이기만 (가벼움)
  Object.assign(wrap.style, { left: PX + "px", top: PY + "px", width: W * Z + "px", height: H * Z + "px" });
  $("zoomtag").textContent = `${Math.round(Z * 100)}% · ${W}×${H}`; drawSel(); drawRulers(); drawUserGuides();
  clearTimeout(zoomTimer); zoomTimer = setTimeout(applyView, 140);
}
function zoomStep(d) { const z = d > 0 ? ZSTEPS.find(s => s > Z * 1.01) || 8 : [...ZSTEPS].reverse().find(s => s < Z * 0.99) || 0.05; setZoom(z); }
function applyView(lazy) {  // lazy: 그리기는 다음 프레임에 한 번만 (되돌리기처럼 곧 또 그릴 때)
  const dpr = window.devicePixelRatio || 1, rs = Math.min(Z * dpr, 2);
  if (Math.abs(rs - RS) > 1e-3 || cv.width !== Math.round(W * rs) || cv.height !== Math.round(H * rs)) { RS = rs; cv.width = Math.round(W * rs); cv.height = Math.round(H * rs); }
  cv.style.width = W * Z + "px"; cv.style.height = H * Z + "px";
  Object.assign(wrap.style, { left: PX + "px", top: PY + "px", width: W * Z + "px", height: H * Z + "px" });
  $("zoomtag").textContent = `${Math.round(Z * 100)}% · ${W}×${H}`;
  const oz = $("oZoom"); if (oz) oz.textContent = Math.round(Z * 100) + "%";
  if (lazy) renderAll(); else paint();
}
let rq = 0;
function renderAll() { if (rq) return; rq = requestAnimationFrame(() => { rq = 0; paint(); }); }
function paint() { if (!D) return; renderMain(); drawSel(); if (editing) placeInline(); if (typeof drawSafe === "function") { drawSafe(); drawRulers(); drawUserGuides(); } }

/* ---------- 화면 그리기 캐시: 안 바뀌는 아래·위 레이어를 한 장씩 합쳐 두고, 바뀌는 레이어만 매번 그림 ---------- */
// 끌기·슬라이더·글자 입력 때 30장을 다시 그리지 않고 [아래 한 장] + [바뀌는 레이어] + [위 한 장] 만 그림.
// 혼합 모드 레이어는 아래 그림과 섞여야 하므로 위쪽 덩어리를 거기서 끊고 바로 그림.
const STK = { sig: "", keys: [], below: null, above: null, off: false, last: null };
const STK_SEGS = 2;  // 위쪽에 합쳐 둘 덩어리 최대 장수
const STK_MAX = 5e6;  // 화면 픽셀이 이보다 크면(쇼츠를 크게 확대) 합쳐 두지 않음 — 한 장 20MB 이하로 메모리 아끼기
function layerSig(l) {  // 그려지는 모습을 정하는 값 전부 (위치·효과·이미지 준비 상태)
  const im = l.type === "image" && !l.hidden ? (l._edit ? "e" + (l._ev || 0) : img(l.src) ? 1 : 0) : 0;
  return JSON.stringify(l, dropPriv) + `|${im}|${l._fast ? 1 : 0}`;
}
const plainBlend = l => l.hidden || !l.blend || l.blend === "source-over";
function stackCost(l, sc) {  // 대충 그리는 양(화면 픽셀) — 효과가 있으면 두 배, 레이어마다 기본 비용
  if (l.hidden) return 0;
  const fx = ["shadow", "glow", "outline", "extrude"].some(k => l[k] && l[k].on);
  return Math.abs((l.w || 0) * (l.h || 0)) * sc * sc * (fx ? 2 : 1) + 2e4;
}
function stackCanvas(o, cw, ch) { if (!o.c || o.c.width !== cw || o.c.height !== ch) o.c = newCanvas(cw, ch); return o.c; }
function drawRange(g, ls, a, b, sc) {
  if (a >= b) return;
  g.save(); g.setTransform(sc, 0, 0, sc, 0, 0);
  for (let i = a; i < b; i++) { try { drawLayer(g, ls[i], sc); } catch (e) { console.error("레이어 그리기 실패", ls[i].id, e); } }
  g.restore();
}
function renderMain() {
  const doc = D.doc, ls = doc.layers, n = ls.length, sc = RS, cw = cv.width, ch = cv.height, w = doc.w || 1280, h = doc.h || 720;
  if (STK.off || cw * ch > STK_MAX) { STK.below = STK.above = null; STK.sig = ""; STK.last = null; return renderDoc(ctx, doc, sc); }
  const sig = [cw, ch, sc, doc.bg, w, h, FONT_VER].join("|"), keys = ls.map(layerSig), prev = STK.sig === sig ? STK.keys : null;
  if (!prev) STK.below = STK.above = null;
  // 지난번 그림과 달라진 범위 [lo, hi) (지우기·끼워 넣기는 lo === hi)
  let lo = 0, hi = n, want = null;
  if (prev) {
    while (lo < n && lo < prev.length && keys[lo] === prev[lo]) lo++;
    let j = prev.length; while (hi > lo && j > lo && keys[hi - 1] === prev[j - 1]) { hi--; j--; }
    if (lo < hi || prev.length !== n) want = [lo, hi];
  }
  if (!want) { const idx = []; ls.forEach((l, i) => { if (selIds.includes(l.id)) idx.push(i); }); if (idx.length) want = [idx[0], idx[idx.length - 1] + 1]; }  // 고른 레이어를 곧 만질 것
  // 지금 캐시가 맞는지: 아래는 [0, bk), 위는 [as, n)
  const B = STK.below, A = STK.above;
  let bk = -1, as = A ? -1 : n;
  if (B && B.k <= n) { let i = 0; while (i < B.k && B.keys[i] === keys[i]) i++; if (i === B.k) bk = B.k; }
  if (A) { const s = n - A.keys.length; if (s >= 0 && A.keys.every((k, i) => k === keys[s + i])) as = s; }
  let ta = n, tb = n;  // 새로 나눌 자리
  if (want) { ta = want[0]; tb = Math.max(ta, want[1]); }
  const fits = bk >= 0 && as >= 0 && bk <= as && (!want || (bk <= ta && tb <= as && as - bk <= tb - ta + 2));
  let built = 0;
  if (!fits) {
    let nb = STK.below;  // 아래: 배경색 + [0, ta)
    if (!nb || bk < 0 || bk > ta) nb = STK.below = { c: nb && nb.c, k: 0, keys: [] };
    if (nb.k < ta) {
      const g = stackCanvas(nb, cw, ch).getContext("2d");
      if (nb.k === 0) { g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, cw, ch); g.setTransform(sc, 0, 0, sc, 0, 0); g.fillStyle = doc.bg || "#000"; g.fillRect(0, 0, w, h); g.setTransform(1, 0, 0, 1, 0, 0); }
      drawRange(g, ls, nb.k, ta, sc); built += ta - nb.k; nb.k = ta; nb.keys = keys.slice(0, ta);
    }
    if (as !== tb) {  // 위: [tb, n) — 혼합 모드 레이어에서 끊어 덩어리마다 한 장
      const old = (STK.above && STK.above.segs) || [], segs = [];
      for (let i = tb; i < n;) {
        if (!plainBlend(ls[i])) { segs.push({ a: i, b: i + 1, direct: true }); i++; continue; }
        let j = i; while (j < n && plainBlend(ls[j])) j++;
        segs.push({ a: i, b: j, direct: true }); i = j;
      }
      // 혼합 모드 레이어가 많으면 덩어리가 잘게 쪼개짐 — 그리는 양이 화면 한 장보다 많은 덩어리 중 큰 것 STK_SEGS개만 합쳐 두고 나머지는 바로 그림 (메모리·속도 보호)
      const cost = sg => { let t = 0; for (let i = sg.a; i < sg.b; i++) t += stackCost(ls[i], sc); return t; };
      segs.forEach(sg => { sg.cost = sg.b - sg.a >= 2 && plainBlend(ls[sg.a]) ? cost(sg) : 0; });
      segs.filter(sg => sg.cost >= cw * ch).sort((p, q) => q.cost - p.cost).slice(0, STK_SEGS).forEach(sg => {
        sg.direct = false; sg.c = (old.find(o => o.c && !o.used && (o.used = true)) || {}).c;
        const g = stackCanvas(sg, cw, ch).getContext("2d");
        g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, cw, ch); drawRange(g, ls, sg.a, sg.b, sc); built += sg.b - sg.a;
      });
      STK.above = tb < n ? { keys: keys.slice(tb), segs } : null;
    }
  }
  // 화면: 아래 한 장 → 바뀌는 레이어 → 위 덩어리들
  const bl = STK.below, ab = STK.above, wa = bl ? bl.k : 0, wb = ab ? n - ab.keys.length : n;
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (wa > 0) ctx.drawImage(bl.c, 0, 0); else { ctx.setTransform(sc, 0, 0, sc, 0, 0); ctx.fillStyle = doc.bg || "#000"; ctx.fillRect(0, 0, w, h); }
  ctx.restore();
  drawRange(ctx, ls, wa, wb, sc);
  if (ab) {
    const base = wb - ab.segs[0].a;  // 아래쪽에서 지우기·끼워 넣기로 위치가 밀렸을 때
    for (const sg of ab.segs) {
      if (sg.direct) drawRange(ctx, ls, sg.a + base, sg.b + base, sc);
      else { ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(sg.c, 0, 0); ctx.restore(); }
    }
  }
  STK.sig = sig; STK.keys = keys; STK.last = { win: wb - wa, built };
}
function toCanvas(e) { const r = cv.getBoundingClientRect(); return { x: (e.clientX - r.left) / Z, y: (e.clientY - r.top) / Z }; }

/* ---------- 도형 계산 ---------- */
function toLocal(l, p) {
  const cx = l.x + l.w / 2, cy = l.y + l.h / 2, a = -l.rot * RAD, dx = p.x - cx, dy = p.y - cy;
  let x = dx * Math.cos(a) - dy * Math.sin(a); const y = dx * Math.sin(a) + dy * Math.cos(a);
  x += Math.tan((l.skew || 0) * RAD) * y; return { x, y };
}
const inside = (l, p) => { const q = toLocal(l, p); return Math.abs(q.x) <= l.w / 2 && Math.abs(q.y) <= l.h / 2; };
function corners(l) {
  const cx = l.x + l.w / 2, cy = l.y + l.h / 2, a = l.rot * RAD, t = Math.tan((l.skew || 0) * RAD), c = Math.cos(a), s = Math.sin(a);
  return [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) => { const y = sy * l.h / 2, x = sx * l.w / 2 - t * y; return [cx + x * c - y * s, cy + x * s + y * c]; });
}
const selBox = () => { const a = selLs(), v = a.filter(l => !l.hidden); return bbox(v.length ? v : a); };
function bbox(ls) {
  const ps = ls.flatMap(corners); if (!ps.length) return { x: 0, y: 0, w: 0, h: 0 };
  const xs = ps.map(p => p[0]), ys = ps.map(p => p[1]), x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}
function hitAt(p) { for (const l of [...D.doc.layers].reverse()) if (!l.hidden && !l.locked && inside(l, p)) return l; return null; }

/* ---------- 선택 상자 · 안내선 · 수치 표시 ---------- */
const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
function drawSel() {
  const ui = $("ui"), old = [...ui.querySelectorAll(".selbox")];
  const ls = D && tool !== "brush" && tool !== "eraser" && !editing ? selLs().filter(l => !l.hidden) : [];
  if (!ls.length) { old.forEach(e => e.remove()); return; }
  let cls = "selbox" + (xform ? " xform" : ""), st, inner;
  if (ls.length === 1) {
    const l = ls[0];
    st = { left: l.x * Z + "px", top: l.y * Z + "px", width: l.w * Z + "px", height: l.h * Z + "px", transform: `rotate(${l.rot}deg) skewX(${-(l.skew || 0)}deg)` };
    inner = l.locked ? "" : HANDLES.map(h => `<div class="h ${h}"></div>`).join("") + '<div class="h rot"></div>';
    const r = l.type === "text" && curRange(l);
    if (r) inner += charRects(l, r[0], r[1]).map(q => `<i class="csel" style="left:${q.x * Z}px;top:${q.y * Z}px;width:${q.w * Z}px;height:${q.h * Z}px"></i>`).join("");
  } else {
    const r = selBox(); cls += " multi";
    st = { left: r.x * Z + "px", top: r.y * Z + "px", width: r.w * Z + "px", height: r.h * Z + "px" };
    inner = HANDLES.map(h => `<div class="h ${h}"></div>`).join("");
  }
  let b = old.shift(); old.forEach(e => e.remove());
  if (!b || b.className !== cls || b._inner !== inner) {  // 모양이 같으면 상자를 새로 만들지 않고 위치만 옮김 (끌 때 가벼움)
    if (b) b.remove();
    b = document.createElement("div"); b.className = cls; b.innerHTML = inner; b._inner = inner; ui.appendChild(b);
  }
  Object.assign(b.style, st);
}
// 고른 글자 범위를 캔버스 위에 파랗게 표시 (레이어 안 좌표)
function charRects(l, s, e) {
  const m = textLayout(ctx, l), sx = l.w / m.natW, sy = l.h / m.natH, out = []; let i = 0;
  for (const ln of m.lines) {
    const x0 = m.pad + (l.align === "center" ? (m.w - ln.w) / 2 : l.align === "right" ? m.w - ln.w : 0);
    for (const sp of ln.spans) {
      const a = Math.max(s, i), b = Math.min(e, i + sp.t.length);
      if (a < b) {
        ctx.font = `${sp.st.size}px "${sp.st.font}"`;
        const p0 = ctx.measureText(sp.t.slice(0, a - i)).width, p1 = ctx.measureText(sp.t.slice(0, b - i)).width;
        out.push({ x: (x0 + sp.x + p0) * sx, y: (m.pad + ln.top) * sy, w: (p1 - p0) * sx, h: ln.lh * sy });
      }
      i += sp.t.length;
    }
    i += 1;
  }
  return out;
}
function showGuides(gx, gy) {  // 자석 안내선 (있던 줄은 다시 쓰기)
  const ui = $("ui");
  for (const [k, v] of [["v", gx], ["h", gy]]) {
    const gs = [...ui.querySelectorAll(".guide." + k)]; let g = gs.shift(); gs.forEach(e => e.remove());
    if (v == null) { if (g) g.remove(); continue; }
    if (!g) { g = document.createElement("div"); g.className = "guide " + k; ui.appendChild(g); }
    Object.assign(g.style, k === "v" ? { left: v * Z + "px", top: "0", height: H * Z + "px" } : { top: v * Z + "px", left: "0", width: W * Z + "px" });
  }
}
const RO = document.createElement("div"); RO.className = "readout"; stage.appendChild(RO);
let stageR = null;  // 화면 영역 위치 — 창 크기가 바뀔 때만 다시 잼 (끌 때마다 화면 배치를 강제로 다시 계산하지 않게)
const stageRect = () => stageR || (stageR = stage.getBoundingClientRect());
addEventListener("resize", () => (stageR = null)); new ResizeObserver(() => (stageR = null)).observe(stage);
function readout(ev, txt) {
  const r = stageRect(), shape = txt.replace(/\d/g, "0");
  if (RO.textContent !== txt) RO.textContent = txt;
  RO.style.display = "block";
  if (RO._shape !== shape) { RO._shape = shape; RO._w = RO.offsetWidth; }  // 고정폭 글꼴: 숫자만 바뀌면 폭 그대로
  RO.style.left = Math.min(ev.clientX - r.left + 18, r.width - RO._w - 6) + "px"; RO.style.top = Math.min(ev.clientY - r.top + 20, r.height - 60) + "px";
}
const hideReadout = () => { RO.style.display = "none"; };
const sgn = v => (v >= 0 ? "+" : "") + Math.round(v);
