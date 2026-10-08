
/* ---------- 레이어 패널 (끌어서 순서 변경) ---------- */
const LAYER_BG_ROW = `<div class="ly" style="cursor:default"><span class="grip"></span><span class="ic">■</span><span class="ty"></span><span class="nm">배경색</span><input type="color" data-k="bg"></div>`;
function layerRowHtml(l) {
  const ic = { image: "🖼", text: "T", shape: "▭" };
  return `<div class="ly ${selIds.includes(l.id) ? "sel" : ""}" data-l="${l.id}" title="끌어서 순서 변경 · 두 번 누르면 레이어 스타일"${l.gid ? ` style="box-shadow:inset 3px 0 0 ${gidColor(l.gid)}"` : ""}>
    <span class="grip">⋮⋮</span><button class="ic" data-eye="${l.id}" title="보이기/숨기기">${l.hidden ? "◌" : "👁"}</button><canvas class="lth" width="44" height="26" data-th="${l.id}"></canvas><span class="ty">${l.gid ? "📁" : ic[l.type]}</span>
    <span class="nm" title="이름을 두 번 누르면 이름 바꾸기">${esc(lname(l))}</span>${hasFx(l) ? `<span class="hint" title="효과 있음">fx</span>` : ""}<button class="ic" data-lock="${l.id}" title="잠금">${l.locked ? "🔒" : "🔓"}</button></div>`;
}
function renderLayers() {
  if (!D) return;
  { const ae = document.activeElement; if (ae && ae.tagName === "INPUT" && !ae.dataset.k && $("layers").contains(ae)) ae.blur(); }  // 이름 바꾸기 칸이 열려 있으면 먼저 끝냄 (줄을 바꿔 끼우다 blur 가 끼어들지 않게)
  const box = $("layers"), ls = [...D.doc.layers].reverse(), rows = ls.map(layerRowHtml);
  const cur = [...box.querySelectorAll(":scope > [data-l]")];
  if (cur.length === ls.length && cur.every((el, i) => el.dataset.l === ls[i].id) && box.querySelector("[data-k=bg]")) {
    // 순서가 그대로면 바뀐 줄만 바꿔 끼움 (글자 칠 때마다 줄 30개를 다시 만들지 않게) — 미리보기 그림은 옮겨 담음
    cur.forEach((el, i) => {
      if (el._h === rows[i] && !el.querySelector("input") && !el.matches(".dragging, .drop-above, .drop-below")) return;
      const t = document.createElement("template"); t.innerHTML = rows[i]; const nr = t.content.firstElementChild; nr._h = rows[i];
      const oc = el.querySelector("canvas[data-th]"); if (oc) nr.querySelector("canvas[data-th]").replaceWith(oc);
      el.replaceWith(nr);
    });
  } else {
    const prevTh = {}; box.querySelectorAll("canvas[data-th]").forEach(c => (prevTh[c.dataset.th] = c));
    box.innerHTML = rows.join("") + LAYER_BG_ROW;
    box.querySelectorAll(":scope > [data-l]").forEach((el, i) => (el._h = rows[i]));
    box.querySelectorAll("canvas[data-th]").forEach(c => { const o = prevTh[c.dataset.th]; if (o) { c.getContext("2d").drawImage(o, 0, 0); c._k = o._k; } });
  }
  const bg = box.querySelector("[data-k=bg]"); if (bg) bg.value = toHex(D.doc.bg || "#000000");
  clearTimeout(lthTimer); lthTimer = setTimeout(drawLayerThumbs, 120);
}
let lthTimer = 0;
function drawLayerThumbs() {  // 레이어 패널의 작은 미리보기 (그 레이어만 그림) — 바뀐 레이어만 다시
  document.querySelectorAll("canvas[data-th]").forEach(cn => {
    const l = byId(cn.dataset.th); if (!l) return;
    const v = Object.assign({}, l, { hidden: false }), key = layerSig(v) + `|${W}x${H}|${FONT_VER}`; if (cn._k === key) return;  // 숨긴 레이어도 보이는 모습으로
    const g = cn.getContext("2d"), k = Math.min(cn.width / W, cn.height / H), ox = (cn.width - W * k) / 2, oy = (cn.height - H * k) / 2;
    g.clearRect(0, 0, cn.width, cn.height); g.fillStyle = "#3a3a3a"; g.fillRect(ox, oy, W * k, H * k);
    g.save(); g.translate(ox, oy); g.scale(k, k); drawLayer(g, v, k); g.restore();
    cn._k = key;
  });
}
const gidColor = gid => `hsl(${[...gid].reduce((a, c) => a + c.charCodeAt(0) * 37, 0) % 360},70%,55%)`;
function groupSel(on) {
  const ls = selLs();
  if (on) { if (ls.length < 2) return toast("묶을 레이어를 2개 이상 고르세요 (Shift/Ctrl+클릭)"); commit("그룹으로 묶기"); const g = nid(); ls.forEach(l => (l.gid = g)); toast(`레이어 ${ls.length}개를 묶었어요 · 캔버스에서 누르면 함께 선택돼요 (풀기: Ctrl+Shift+G)`); }
  else { if (!ls.some(l => l.gid)) return; commit("그룹 풀기"); ls.forEach(l => delete l.gid); toast("묶음을 풀었어요"); }
  changed();
}
function remapGids(ls) { const m = {}; ls.forEach(l => { if (l.gid) l.gid = m[l.gid] || (m[l.gid] = nid()); }); return ls; }
function clickLayer(id, e) {
  if (e.ctrlKey || e.metaKey) select(selIds.includes(id) ? selIds.filter(x => x !== id) : [...selIds, id]);
  else if (e.shiftKey && anchorId && byId(anchorId)) { const ord = [...D.doc.layers].reverse().map(l => l.id), a = ord.indexOf(anchorId), b = ord.indexOf(id); select(ord.slice(Math.min(a, b), Math.max(a, b) + 1)); return; }
  else select([id]);
  anchorId = id;
}
$("layers").addEventListener("mousedown", e => {
  if (e.button !== 0) return;
  const eye = e.target.closest("[data-eye]"), lock = e.target.closest("[data-lock]");
  if (eye) { commit("보이기/숨기기"); const l = byId(eye.dataset.eye); l.hidden = !l.hidden; changed(); return; }
  if (lock) { commit("잠금"); const l = byId(lock.dataset.lock); l.locked = !l.locked; changed(); return; }
  const row = e.target.closest("[data-l]"); if (!row || e.target.closest("input")) return;
  e.preventDefault(); const id = row.dataset.l, y0 = e.clientY, onName = !!e.target.closest(".nm"); let drag = false, target = null;
  const rows = () => [...$("layers").querySelectorAll("[data-l]")];
  const mv = ev => {
    if (!drag) { if (Math.abs(ev.clientY - y0) < 5) return; drag = true; if (!selIds.includes(id)) { select([id]); } rows().forEach(r => r.classList.toggle("dragging", selIds.includes(r.dataset.l))); }
    target = null; const rs = rows();
    rs.forEach(r => {
      r.classList.remove("drop-above", "drop-below"); const b = r.getBoundingClientRect();
      if (ev.clientY >= b.top && ev.clientY < b.bottom && !selIds.includes(r.dataset.l)) { const above = ev.clientY < b.top + b.height / 2; r.classList.add(above ? "drop-above" : "drop-below"); target = { id: r.dataset.l, above }; }
    });
    if (!target && rs.length) { const last = rs[rs.length - 1], b = last.getBoundingClientRect(); if (ev.clientY >= b.bottom && !selIds.includes(last.dataset.l)) { last.classList.add("drop-below"); target = { id: last.dataset.l, above: false }; } }
  };
  const up = ev => {
    removeEventListener("mousemove", mv); removeEventListener("mouseup", up);
    if (!drag) {
      const now = Date.now(), dbl = lastRow.id === id && now - lastRow.t < 450; lastRow = { id, t: dbl ? 0 : now };
      if (!dbl) return clickLayer(id, e);
      if (onName) return renameLayer(id); select([id]); return toggleLS(true);
    }
    if (target) dropLayers(target); else renderLayers();
  };
  addEventListener("mousemove", mv); addEventListener("mouseup", up);
});
let lastRow = { id: null, t: 0 };
function renameLayer(id) {
  const l = byId(id), nm = $("layers").querySelector(`[data-l="${id}"] .nm`); if (!l || !nm) return;
  const inp = document.createElement("input"); inp.className = "s"; inp.value = lname(l); nm.replaceWith(inp); inp.focus(); inp.select();
  let done = false;
  const fin = ok => { if (done) return; done = true; if (ok && inp.value.trim() && inp.value.trim() !== lname(l)) { commit("이름 바꾸기"); l.name = inp.value.trim(); scheduleSave(); } renderLayers(); renderProps(); };
  inp.onkeydown = ev => { ev.stopPropagation(); if (ev.key === "Enter") fin(true); if (ev.key === "Escape") fin(false); };
  inp.onblur = () => fin(true);
}
function dropLayers(t) {
  const moving = selLs(); if (!moving.length || selIds.includes(t.id)) return renderLayers();
  commit("레이어 순서"); const arr = D.doc.layers.filter(l => !selIds.includes(l.id)), i = arr.findIndex(l => l.id === t.id);
  arr.splice(t.above ? i + 1 : i, 0, ...moving); D.doc.layers = arr; changed();
}
function moveLayers(d) {
  const a = D.doc.layers; if (!selIds.length) return; commit("레이어 순서");
  const idx = a.map((l, i) => (selIds.includes(l.id) ? i : -1)).filter(i => i >= 0); if (d > 0) idx.reverse();
  for (const i of idx) { const j = i + d; if (j < 0 || j >= a.length || selIds.includes(a[j].id)) continue; [a[i], a[j]] = [a[j], a[i]]; }
  changed();
}
function toEdge(front) { if (!selIds.length) return; commit("레이어 순서"); const mv = selLs(), rest = D.doc.layers.filter(l => !selIds.includes(l.id)); D.doc.layers = front ? [...rest, ...mv] : [...mv, ...rest]; changed(); }
function dupLayers() {
  const ls = selLs(); if (!ls.length) return; commitXform(); commit("복제");
  const cs = remapGids(ls.map(l => { const c = clone(l); c.id = nid(); c.x += 24; c.y += 24; D.doc.layers.splice(D.doc.layers.indexOf(l) + 1, 0, c); return c; })), ids = cs.map(c => c.id);
  selIds = ids; changed(); toast(`레이어 ${ids.length}개를 복제했어요`);
}
function delLayers() { if (!selIds.length) return; commitXform(); commit("삭제"); D.doc.layers = D.doc.layers.filter(l => !selIds.includes(l.id)); selIds = []; changed(); }
function stepSel(d) {
  const a = D.doc.layers; if (!a.length) return; const cur = selIds.length ? a.findIndex(l => l.id === selIds[selIds.length - 1]) : d > 0 ? -1 : a.length;
  const j = clamp(cur + d, 0, a.length - 1); select([a[j].id]);
}
$("lDup").onclick = dupLayers; $("lDel").onclick = delLayers;

/* ---------- 복사 · 붙여넣기 ---------- */
function copySel(cut) {
  const ls = selLs(); if (!ls.length) return; CLIP = ls.map(clone);
  CLIP_TOKEN = "futsal-thumb-layers:" + nid() + nid();
  try { navigator.clipboard && navigator.clipboard.writeText(CLIP_TOKEN).catch(() => {}); } catch (_) {} if (cut) delLayers(); toast(`레이어 ${ls.length}개를 ${cut ? "잘라냈어요" : "복사했어요"} · Ctrl+V로 붙여넣기`); }
function pasteLayers(inPlace) {
  if (!CLIP) return; commitXform(); commit("붙여넣기");
  const ids = remapGids(CLIP.map(x => { const c = clone(x); c.id = nid(); if (!inPlace) { c.x += 20; c.y += 20; } normLayer(c); D.doc.layers.push(c); return c; })).map(c => c.id);
  CLIP = CLIP.map(x => Object.assign(clone(x), inPlace ? {} : { x: x.x + 20, y: x.y + 20 }));
  selIds = ids; changed(); showTab("props");
}
async function addImageFile(f) {
  if (!f || !f.type.startsWith("image/")) return;
  const data = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(f); });
  const j = await post("/api/thumb/upload", { data }).catch(() => ({}));
  if (!j.url) return toast(j.error || "그림을 넣지 못했어요. 잠시 뒤 다시 해 주세요");
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.onerror = () => r(null); i.src = j.url; });
  const ar = im ? im.naturalWidth / im.naturalHeight : 16 / 9; let w = Math.min(W * 0.7, im ? im.naturalWidth : 640), h = w / ar;
  if (h > H * 0.85) { h = H * 0.85; w = h * ar; }
  addImage(j.url, false, { w, h, x: (W - w) / 2, y: (H - h) / 2, name: f.name ? f.name.replace(/\.[^.]+$/, "") : "이미지" });
}
let pasteHandled = false, pasteInPlace = false;
const TYPING_SEL = "textarea, select, input[type=text], input:not([type]), input[type=number], input[type=search]";
document.addEventListener("paste", e => {
  if (e.target.closest && e.target.closest(TYPING_SEL)) return;
  const cd = e.clipboardData, txt = cd ? cd.getData("text/plain") : "";
  if (CLIP && CLIP_TOKEN && txt === CLIP_TOKEN) { e.preventDefault(); pasteHandled = true; const ip = pasteInPlace; pasteInPlace = false; pasteLayers(ip); return; }
  const it = [...((cd && cd.items) || [])].find(i => i.type.startsWith("image/"));
  if (it) { e.preventDefault(); pasteHandled = true; pasteInPlace = false; addImageFile(it.getAsFile()); return; }
  if (CLIP) { e.preventDefault(); pasteHandled = true; const ip = pasteInPlace; pasteInPlace = false; pasteLayers(ip); }
});
const isFileDrag = e => !!(e.dataTransfer && [...e.dataTransfer.types].includes("Files"));
document.addEventListener("dragover", e => { if (isFileDrag(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
document.addEventListener("drop", e => {
  if (!isFileDrag(e)) return; e.preventDefault();  // WebView2가 파일 화면으로 넘어가지 않게
  const f = [...e.dataTransfer.files].find(x => x.type.startsWith("image/")); if (f) addImageFile(f); else toast("이미지 파일만 넣을 수 있어요");
});

/* ---------- 자유 변형 (Ctrl+T) ---------- */
function startXform() {
  if (!selLs().length) return toast("변형할 레이어를 먼저 골라 주세요");
  if (editing) endEdit(); if (tool !== "move") setTool("move");
  xform = { before: snapshot(), ul: undoMark(), ids: [...selIds], layers: selLs().map(clone) }; renderOpts(); renderAll();
  toast("자유 변형 · 모서리=크기 (Shift 비율 해제 · Alt 가운데 기준) · 바깥 끌기=회전 · Ctrl+위/아래 손잡이=기울이기 · Enter 확정 / Esc 취소");
}
function commitXform() { if (!xform) return; const { before, ul } = xform; xform = null; cutUndoTo(ul); if (before !== snapshot()) { pushUndo(before, "자유 변형"); scheduleSave(); } stage.style.cursor = ""; renderOpts(); renderAll(); }
function cancelXform() {  // 자유 변형 대상 레이어만 Ctrl+T 직전으로 (다른 편집은 그대로)
  if (!xform) return; const { ul, layers } = xform; xform = null; cutUndoTo(ul);
  for (const o of layers) { const i = D.doc.layers.findIndex(l => l.id === o.id); if (i >= 0) D.doc.layers[i] = normLayer(clone(o)); }
  stage.style.cursor = ""; changed();
}

/* ---------- 도구 ---------- */
const TOOL_BTN = { move: "tMove", text: "tText", rect: "tShape", ellipse: "tEllipse", slant: "tSlant", eye: "tEye", brush: "tBrush", eraser: "tEraser", hand: "tHand" };
function markTool() { Object.entries(TOOL_BTN).forEach(([k, id]) => $(id).classList.toggle("on", k === tool)); }
function setTool(t) {
  if (editing && t !== "text") endEdit();
  if (t === "brush" || t === "eraser") { const l = selL(); if (!l || l.type !== "image" || !l.orig) { toast("누끼 딴 이미지 레이어를 먼저 골라 주세요 (✂ 누끼 따기)"); return; } }
  if (xform && t !== "move") commitXform();
  tool = t; markTool();
  stage.style.cursor = { move: "default", text: "text", brush: "none", eraser: "none", hand: "grab" }[t] || "crosshair";
  stage.classList.toggle("pan", t === "hand"); if (t !== "brush" && t !== "eraser") $("bcur").style.display = "none";
  renderOpts(); renderProps(); renderAll();
}
$("tMove").onclick = () => setTool("move"); $("tText").onclick = () => setTool("text"); $("tShape").onclick = () => setTool("rect");
$("tEllipse").onclick = () => setTool("ellipse"); $("tSlant").onclick = () => setTool("slant"); $("tEye").onclick = () => setTool("eye");
$("tBrush").onclick = () => setTool("brush"); $("tEraser").onclick = () => setTool("eraser"); $("tHand").onclick = () => setTool("hand");
$("tBurst").onclick = () => addShape("burst", { x: W / 2, y: H / 2 });
$("tImage").onclick = () => $("fileIn").click();
$("fileIn").onchange = async e => { await addImageFile(e.target.files[0]); e.target.value = ""; };
$("tCut").onclick = () => { const l = selL(); if (!l || l.type !== "image") return toast("누끼 딸 이미지 레이어를 먼저 골라 주세요"); doCut(l, "hq"); };
function addImage(src, asBg, extra = {}) {
  commitXform(); commit("이미지 추가");
  const l = L("image", Object.assign({ src, name: asBg ? "배경" : "이미지" }, extra));
  if (asBg) { Object.assign(l, { x: 0, y: 0, w: W, h: H, fit: "cover" }); D.doc.layers.unshift(l); }
  else { if (!extra.w) Object.assign(l, { w: 640, h: 360, x: (W - 640) / 2, y: (H - 360) / 2 }); D.doc.layers.push(l); }
  selIds = [l.id]; changed(); showTab("props");
}

/* ---------- 버튼 동작 ---------- */
const ACT = {
  cp: () => toggleCP(), ls: () => toggleLS(true),
  close: b => (b.dataset.v === "cpanel" ? toggleCP(false) : b.dataset.v === "lspanel" ? toggleLS(false) : $(b.dataset.v) && $(b.dataset.v).classList.remove("show")),
  lsSec: b => { lsSec = b.dataset.v; renderLS(); },
  endEdit: () => endEdit(), brushDone: () => { setTool("move"); renderProps(); }, brushB: () => setTool("brush"),
  xform: () => startXform(), xOk: () => commitXform(), xNo: () => cancelXform(),
  lock: () => { lockRatio = !lockRatio; renderOpts(); renderProps(); },
  al: b => align(b.dataset.v), dist: b => distribute(b.dataset.v),
  zIn: () => zoomStep(1), zOut: () => zoomStep(-1), zFit: () => fitView(), z100: () => setZoom(1),
  selAll: () => { textSel = null; for (const t of [$("xText"), $("cpText")]) if (t) t.setSelectionRange(0, 0); syncFields(); },
  cut: () => { const l = selL(); if (l && l.type === "image") doCut(l, "hq"); }, cutFast: () => { const l = selL(); if (l && l.type === "image") doCut(l, "fast"); },
  iReset: () => { const l = selL(); if (!l) return; commit("보정 초기화"); Object.assign(l, { bright: 100, contrast: 100, sat: 100, blur: 0, hue: 0, vignette: 0 }); changed(); },
  flipH: () => { const ls = selLs(); if (!ls.length) return; commit("좌우 뒤집기"); ls.forEach(l => { if (l.type === "image") { l.flipX = !l.flipX; if (l.slant) l.slant = -l.slant; } l.skew = -(l.skew || 0); l.rot = norm180(-l.rot); }); changed(); },
  eyeApply: () => { eyeApply = !eyeApply; renderOpts(); },
  fmt: b => setFormat(b.dataset.v),
  hist: b => { const n = +b.dataset.v; if (undoStack.length <= n) return; if (editing) endEdit(); xform = null; let cur = snapshot(); while (undoStack.length > n) { redoStack.push(cur); histNames.pop(); cur = undoStack.pop(); } restoreDoc(cur); applyView(true); changed(); },
  autoFmt: b => { AUTO_FMT = b.dataset.v; renderAuto(); },
  fadeDir: b => { const l = selL(); if (!l) return; commit("마스크 방향"); Object.assign(l.fade, { on: true, angle: +b.dataset.v }); changed(); },
};
const MACT = { group: () => groupSel(true), ungroup: () => groupSel(false), copy: () => copySel(), dup: dupLayers, xform: startXform, ls: () => toggleLS(true), front: () => toEdge(true), back: () => toEdge(false),
  hide: () => { commit("숨기기"); selLs().forEach(l => (l.hidden = !l.hidden)); changed(); }, del: delLayers, paste: () => pasteLayers(), all: () => select(D.doc.layers.filter(l => !l.locked).map(l => l.id)) };
function setFormat(f) {
  const s = FMT[f]; if (!s || (D.doc.w === s.w && D.doc.h === s.h)) return; commitXform();
  commit("캔버스 크기"); const ow = D.doc.w, oh = D.doc.h, k = Math.min(s.w / ow, s.h / oh);
  for (const l of D.doc.layers) {
    if (l.type === "image" && l.name === "배경" && Math.abs(l.w - ow) < 2 && Math.abs(l.h - oh) < 2) { Object.assign(l, { x: 0, y: 0, w: s.w, h: s.h }); continue; }
    const cx = (l.x + l.w / 2 - ow / 2) * k + s.w / 2, cy = (l.y + l.h / 2 - oh / 2) * k + s.h / 2; l.w *= k; l.h *= k; l.x = cx - l.w / 2; l.y = cy - l.h / 2;
    if (l.type === "text") bakeText(l);
  }
  D.doc.w = s.w; D.doc.h = s.h; W = s.w; H = s.h; fitView(); changed(); toast(`캔버스를 ${s.label} (${s.w}×${s.h})로 바꿨어요`);
}

/* ---------- 키보드 (포토샵 단축키) ---------- */
addEventListener("keydown", e => {
  const t = e.target, mod = e.ctrlKey || e.metaKey, code = e.code;
  if (e.key === "F1") { e.preventDefault(); $("helpModal").classList.toggle("show"); return; }
  const typing = t.closest && t.closest(TYPING_SEL), selPass = t.tagName === "SELECT" && (mod || e.key === "Delete" || e.key === "Backspace");
  if (typing && !selPass) {
    if (t.type === "number" && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault(); const m = FM[t.dataset.k] || {}; t.value = Math.round((+t.value + (e.key === "ArrowUp" ? 1 : -1) * (m.step || 1) * (e.shiftKey ? 10 : 1)) * 100) / 100;
      t.dispatchEvent(new Event("input", { bubbles: true })); if (t.dataset.k !== undefined) { liveEnd(); renderLayers(); } return;
    }
    if (e.key === "Enter" && t.tagName === "INPUT") t.blur();
    if (e.key === "Escape") t.blur();
    if (mod && code === "KeyS") { e.preventDefault(); e.shiftKey ? exportImg() : saveNow(); }
    return;
  }
  if (e.key === "Escape" && document.querySelector(".modal.show")) { document.querySelectorAll(".modal.show").forEach(m => m.classList.remove("show")); return; }
  if (!D) return;
  if (t.tagName === "INPUT" && t.type === "range" && !mod) {
    if (/^(Arrow|Home$|End$|PageUp$|PageDown$)/.test(e.key)) return;
    if (e.key === "Escape" || e.key === "Enter") { t.blur(); return; }
  }
  if (e.key === " ") { e.preventDefault(); if (!spaceDown) { spaceDown = true; stage.classList.add("pan"); } return; }
  if (mod) {
    const k = { KeyZ: () => (e.shiftKey ? redo() : undo()), KeyY: redo, KeyT: startXform, KeyJ: dupLayers, KeyD: () => select([]),
      KeyA: () => select(D.doc.layers.filter(l => !l.locked).map(l => l.id)), KeyC: () => copySel(), KeyX: () => copySel(true),
      KeyS: () => (e.shiftKey ? exportImg() : saveNow().then(() => toast("저장했어요"))),
      Equal: () => zoomStep(1), NumpadAdd: () => zoomStep(1), Minus: () => zoomStep(-1), NumpadSubtract: () => zoomStep(-1), Digit0: fitView, Numpad0: fitView, Digit1: () => setZoom(1),
      BracketRight: () => (e.shiftKey ? toEdge(true) : moveLayers(1)), BracketLeft: () => (e.shiftKey ? toEdge(false) : moveLayers(-1)),
      KeyR: toggleRulers, KeyG: () => groupSel(!e.shiftKey), Semicolon: () => { showGuidesU = !showGuidesU; drawUserGuides(); toast(showGuidesU ? "안내선 보임" : "안내선 숨김"); },
      Comma: () => { if (!selIds.length) return; commit("숨기기"); selLs().forEach(l => (l.hidden = !l.hidden)); changed(); } }[code];
    if (code === "KeyV") { pasteHandled = false; pasteInPlace = e.shiftKey; setTimeout(() => { const ip = pasteInPlace; pasteInPlace = false; if (!pasteHandled && CLIP) pasteLayers(ip); }, 80); return; }
    if (k) { e.preventDefault(); k(); }
    return;
  }
  if (e.altKey && (code === "BracketRight" || code === "BracketLeft")) { e.preventDefault(); stepSel(code === "BracketRight" ? 1 : -1); return; }
  if (e.key === "Escape" && menu) { closeMenu(); return; }
  if (xform && e.key === "Enter") { e.preventDefault(); commitXform(); return; }
  if (xform && e.key === "Escape") { e.preventDefault(); cancelXform(); return; }
  if (e.key === "Escape") { closeMenu(); if (tool === "tac") { tacPending = null; setTool("move"); } return; }
  if (e.key === "Enter") { if (!t.closest(".modal")) e.preventDefault(); const l = selL(); if (l && l.type === "text") startEdit(l, true); return; }
  if (e.key === "Tab" && !t.closest(".modal")) { e.preventDefault(); return; }
  const tk = { KeyV: "move", KeyT: "text", KeyU: "rect", KeyI: "eye", KeyB: "brush", KeyE: "eraser", KeyH: "hand" }[code];
  if (tk) { setTool(tk); return; }
  if (code === "KeyX" && (tool === "brush" || tool === "eraser")) { setTool(tool === "brush" ? "eraser" : "brush"); return; }
  if (code === "BracketRight" || code === "BracketLeft") {
    const up = code === "BracketRight";
    if (e.shiftKey) brush.hard = clamp(brush.hard + (up ? 10 : -10), 0, 100); else brush.size = clamp(Math.round(brush.size * (up ? 1.15 : 1 / 1.15)), 2, 400);
    syncFields(); stage.dispatchEvent(new MouseEvent("mousemove")); toast(`브러시 ${brush.size}px · 경도 ${brush.hard}%`); return;
  }
  if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); delLayers(); return; }
  if (e.key.startsWith("Arrow")) {
    const ls = selLs().filter(l => !l.locked); if (!ls.length) return; e.preventDefault(); const d = e.shiftKey ? 10 : 1;
    if (!xform) { const key = selIds.join(","); if (!(e.repeat && nudgeKey === key && histNames[histNames.length - 1] === "1px 이동")) commit("1px 이동"); nudgeKey = key; }
    ls.forEach(l => { if (e.key === "ArrowLeft") l.x -= d; if (e.key === "ArrowRight") l.x += d; if (e.key === "ArrowUp") l.y -= d; if (e.key === "ArrowDown") l.y += d; });
    changed(false); syncFields(); return;
  }
  if (/^Digit\d$/.test(code) && tool === "move" && selIds.length) { const n = +code.slice(5); commit("불투명도"); selLs().forEach(l => (l.opacity = n === 0 ? 1 : n / 10)); changed(false); syncFields(); toast(`불투명도 ${n === 0 ? 100 : n * 10}%`); }
});
document.addEventListener("click", e => { const b = e.target.closest && e.target.closest("button"); if (b && e.detail > 0) b.blur(); }, true);
addEventListener("blur", () => { if (spaceDown) { spaceDown = false; if (tool !== "hand") stage.classList.remove("pan"); } });
addEventListener("keyup", e => { if (e.key === " ") { spaceDown = false; if (tool !== "hand") stage.classList.remove("pan"); } });
$("undo").onclick = undo; $("redo").onclick = redo;
$("help").onclick = () => $("helpModal").classList.toggle("show"); $("helpClose").onclick = () => $("helpModal").classList.remove("show");
