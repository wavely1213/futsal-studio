
/* ---------- 캔버스 마우스 ---------- */
stage.addEventListener("mousedown", e => {
  if (!D || e.button === 2) return;
  if (e.target.closest(".inline")) return;
  if (editing) { endEdit(); return; }
  if (e.button === 1 || spaceDown || tool === "hand") { e.preventDefault(); return startPan(e); }
  const p = toCanvas(e);
  if (tool === "brush" || tool === "eraser") return brushDown(e);
  if (tool === "eye") return eyeDrop(e);
  if (tool === "tac" && tacPending) return placeTac(p);
  if (tool === "text") { const l = hitAt(p); if (l && l.type === "text") { selIds = [l.id]; startEdit(l, true); } else addText(p); return; }
  if (["rect", "ellipse", "slant"].includes(tool)) return drawShapeDrag(e, p);
  if (e.target.dataset && e.target.dataset.pt !== undefined) return startPtDrag(e, +e.target.dataset.pt);
  const h = e.target.classList.contains("h") ? [...e.target.classList].find(c => c !== "h") : null;
  if (h) return startTransform(e, h);
  const one = selL();
  if (xform && selIds.length) {
    const b = bbox(selLs()), inSel = one ? inside(one, p) : (p.x >= b.x && p.x <= b.x + b.w && p.y >= b.y && p.y <= b.y + b.h);
    if (inSel) return startMove(e, p);
    if (one) return startTransform(e, "rot");
  }
  const l = hitAt(p);
  const grp = l && l.gid ? D.doc.layers.filter(x => x.gid === l.gid && !x.locked).map(x => x.id) : l ? [l.id] : [];
  if (e.shiftKey) {
    if (!l) return; const on = selIds.includes(l.id);
    if (!on) select([...new Set([...selIds, ...grp])]);
    return startMove(e, p, on ? () => select(selIds.filter(x => !grp.includes(x))) : null);  // 이미 선택된 걸 끌지 않고 누르기만 하면 빼기
  }
  if (!l) { if (!xform) select([]); return; }
  if (!selIds.includes(l.id)) select(grp);
  startMove(e, p);
});
stage.addEventListener("dblclick", e => {
  if (!D || tool !== "move") return; const l = hitAt(toCanvas(e));
  if (l && l.type === "text") { selIds = [l.id]; startEdit(l, true); }
  else if (l && l.type === "shape" && l.shape === "marker") { select([l.id]); editMarkerLabel(l); }
});
function snapCands(ls) {
  const ids = new Set(ls.map(l => l.id)), gd = D.doc.guides || { v: [], h: [] }, xs = [0, W / 2, W, ...(showGuidesU ? gd.v : [])], ys = [0, H / 2, H, ...(showGuidesU ? gd.h : [])];
  for (const l of D.doc.layers) { if (ids.has(l.id) || l.hidden) continue; const b = bbox([l]); if (b.w >= W * 0.98 && b.h >= H * 0.98) continue; xs.push(b.x, b.x + b.w / 2, b.x + b.w); ys.push(b.y, b.y + b.h / 2, b.y + b.h); }
  return { xs, ys };
}
function snapAxis(edges, d, cands) {
  const th = 7 / Z; let best = null;
  for (const e of edges) for (const c of cands) { const k = c - (e + d); if (Math.abs(k) < th && (!best || Math.abs(k) < Math.abs(best.k))) best = { k, c }; }
  return best ? { d: d + best.k, g: best.c } : { d: Math.round(d), g: null };
}
function startMove(e, p, onClick) {
  let ls = selLs().filter(l => !l.locked); if (!ls.length) return;
  const before = snapshot();
  let o = ls.map(l => ({ l, x: l.x, y: l.y })); const bb = bbox(ls), cand = snapCands(ls); let moved = false;
  const mv = ev => {
    const q = toCanvas(ev); let dx = q.x - p.x, dy = q.y - p.y;
    if (!moved && Math.hypot(dx, dy) * Z < 3) return;
    if (!moved && e.altKey) {  // Alt+끌기: 실제로 끌기 시작할 때 복제
      ls = remapGids(ls.map(l => { const c = clone(l); c.id = nid(); D.doc.layers.splice(D.doc.layers.indexOf(l) + 1, 0, c); return c; }));
      selIds = ls.map(l => l.id); o = ls.map(l => ({ l, x: l.x, y: l.y })); renderLayers();
    }
    moved = true;
    let ax = "";  // Shift: 한 방향만 (잠근 쪽은 자석도 안 붙음)
    if (ev.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) { dy = 0; ax = "x"; } else { dx = 0; ax = "y"; } }
    let gx = null, gy = null;
    if (!(ev.ctrlKey || ev.metaKey)) {
      if (ax !== "y") { const sx = snapAxis([bb.x, bb.x + bb.w / 2, bb.x + bb.w], dx, cand.xs); dx = sx.d; gx = sx.g; }
      if (ax !== "x") { const sy = snapAxis([bb.y, bb.y + bb.h / 2, bb.y + bb.h], dy, cand.ys); dy = sy.d; gy = sy.g; }
    }
    o.forEach(t => { t.l.x = t.x + dx; t.l.y = t.y + dy; });
    showGuides(gx, gy); renderAll(); syncFields(null, XFK_SET);
    readout(ev, `X ${Math.round(bb.x + dx)}  Y ${Math.round(bb.y + dy)} px\nΔX ${sgn(dx)}  ΔY ${sgn(dy)}${e.altKey ? "\n(복제됨)" : ""}`);
  };
  const up = () => {
    removeEventListener("mousemove", mv); removeEventListener("mouseup", up); showGuides(); hideReadout();
    if (!moved && onClick && !e.altKey) { onClick(); return; }
    if (before !== snapshot()) { pushUndo(before, e.altKey ? "복제하며 이동" : "이동"); scheduleSave(); }
    renderLayers(); syncFields();
  };
  addEventListener("mousemove", mv); addEventListener("mouseup", up);
}
function startTransform(e, handle) {
  const ls = selLs().filter(l => !l.locked); if (!ls.length) return;
  e.preventDefault(); e.stopPropagation();
  const before = snapshot(), p = toCanvas(e), one = ls.length === 1 ? ls[0] : null;
  const o = ls.map(l => ({ l, x: l.x, y: l.y, w: l.w, h: l.h, rot: l.rot, skew: l.skew || 0, size: l.size }));
  const bb = one ? bbox(ls) : selBox(), skewMode = one && (e.ctrlKey || e.metaKey) && (handle === "n" || handle === "s");
  if (handle === "rot" && !one) return;
  ls.forEach(l => { if ((l._bmCost || 0) > 12) l._fast = true; });  // 효과가 무거운 레이어는 끄는 동안 효과 생략
  const ex = handle.includes("e") ? 1 : handle.includes("w") ? -1 : 0, ey = handle.includes("s") ? 1 : handle.includes("n") ? -1 : 0;
  const mv = ev => {
    const q = toCanvas(ev);
    if (handle === "rot") {
      const t = o[0], cx = t.x + t.w / 2, cy = t.y + t.h / 2;
      let a = t.rot + (Math.atan2(q.y - cy, q.x - cx) - Math.atan2(p.y - cy, p.x - cx)) / RAD;
      a = ev.shiftKey ? Math.round(a / 15) * 15 : Math.round(a * 10) / 10; one.rot = norm180(a);
      readout(ev, `회전 ${one.rot.toFixed(1)}°${ev.shiftKey ? " (15° 단위)" : ""}`);
    } else if (one) {
      const t = o[0], a = t.rot * RAD, c = Math.cos(a), s = Math.sin(a), gx = q.x - p.x, gy = q.y - p.y;
      const lx = gx * c + gy * s, ly = -gx * s + gy * c;
      if (skewMode) {
        let sk = Math.atan(Math.tan(t.skew * RAD) + (handle === "n" ? 1 : -1) * 2 * lx / t.h) / RAD;
        sk = ev.shiftKey ? Math.round(sk / 5) * 5 : Math.round(sk * 10) / 10; one.skew = clamp(sk, -70, 70);
        readout(ev, `기울기 ${one.skew.toFixed(1)}°`);
      } else {
        const ctr = ev.altKey, m = ctr ? 2 : 1;
        let nw = ex ? t.w + lx * ex * m : t.w, nh = ey ? t.h + ly * ey * m : t.h;
        if (ex && ey && !ev.shiftKey) { const k = Math.abs(nw / t.w - 1) > Math.abs(nh / t.h - 1) ? nw / t.w : nh / t.h; nw = t.w * k; nh = t.h * k; }
        nw = Math.max(4, nw); nh = Math.max(4, nh);
        const dxl = ctr ? 0 : ex * (nw - t.w) / 2, dyl = ctr ? 0 : ey * (nh - t.h) / 2;
        const tk = Math.tan(t.skew * RAD), sxl = dxl - tk * dyl;
        const cx = t.x + t.w / 2 + sxl * c - dyl * s, cy = t.y + t.h / 2 + sxl * s + dyl * c;
        one.w = nw; one.h = nh; one.x = cx - nw / 2; one.y = cy - nh / 2;
        const kx = nw / t.w, ky = nh / t.h;
        readout(ev, `W ${Math.round(nw)}  H ${Math.round(nh)} px\n${Math.round(kx * 100)}% × ${Math.round(ky * 100)}%` + (one.type === "text" && Math.abs(kx - ky) < 0.005 ? `\n글자 ${r1(t.size * kx)}px` : "") + (ex && ey ? (ev.shiftKey ? "\n(비율 무시)" : "") : ""));
      }
    } else {
      const ctr = ev.altKey, m = ctr ? 2 : 1, gx = q.x - p.x, gy = q.y - p.y;
      let nw = ex ? bb.w + gx * ex * m : bb.w, nh = ey ? bb.h + gy * ey * m : bb.h;
      if (ex && ey && !ev.shiftKey) { const k = Math.abs(nw / bb.w - 1) > Math.abs(nh / bb.h - 1) ? nw / bb.w : nh / bb.h; nw = bb.w * k; nh = bb.h * k; }
      nw = Math.max(4, nw); nh = Math.max(4, nh);
      const ax = ctr ? bb.x + bb.w / 2 : ex > 0 ? bb.x : bb.x + bb.w, ay = ctr ? bb.y + bb.h / 2 : ey > 0 ? bb.y : bb.y + bb.h;
      scaleGroup(o, ax, ay, nw / bb.w, nh / bb.h);
      readout(ev, `W ${Math.round(nw)}  H ${Math.round(nh)} px\n${Math.round(nw / bb.w * 100)}% × ${Math.round(nh / bb.h * 100)}%`);
    }
    renderAll(); syncFields(null, XFK_SET);
  };
  const up = () => {
    ls.forEach(l => delete l._fast);
    removeEventListener("mousemove", mv); removeEventListener("mouseup", up); hideReadout();
    ls.filter(l => l.type === "text").forEach(bakeText);
    if (before !== snapshot()) { pushUndo(before, handle === "rot" ? "회전" : skewMode ? "기울이기" : "크기 조절"); scheduleSave(); }
    renderAll(); renderProps(); renderOpts(); renderCP();
  };
  addEventListener("mousemove", mv); addEventListener("mouseup", up);
}
function startPan(e) {
  const x0 = e.clientX, y0 = e.clientY, px = PX, py = PY; stage.classList.add("pan"); fitMode = false;
  const mv = ev => { PX = px + ev.clientX - x0; PY = py + ev.clientY - y0; wrap.style.left = PX + "px"; wrap.style.top = PY + "px"; drawRulers(); drawUserGuides(); };
  const up = () => { removeEventListener("mousemove", mv); removeEventListener("mouseup", up); if (!spaceDown && tool !== "hand") stage.classList.remove("pan"); };
  addEventListener("mousemove", mv); addEventListener("mouseup", up);
}
stage.addEventListener("wheel", e => {
  e.preventDefault(); const r = stage.getBoundingClientRect();
  if (e.ctrlKey || e.metaKey || e.altKey) setZoom(Z * Math.exp(-e.deltaY * 0.0018), e.clientX - r.left, e.clientY - r.top, true);
  else { fitMode = false; PX -= e.shiftKey ? e.deltaY : e.deltaX; PY -= e.shiftKey ? 0 : e.deltaY; wrap.style.left = PX + "px"; wrap.style.top = PY + "px"; drawRulers(); drawUserGuides(); }
}, { passive: false });
stage.addEventListener("mousemove", e => {
  const b = $("bcur");
  if (tool === "brush" || tool === "eraser") {
    const r = wrap.getBoundingClientRect(), s = brush.size * Z; b.style.display = "block";
    Object.assign(b.style, { width: s + "px", height: s + "px", left: e.clientX - r.left - s / 2 + "px", top: e.clientY - r.top - s / 2 + "px" });
  } else b.style.display = "none";
  if (xform && selL() && !e.target.classList.contains("h")) stage.style.cursor = inside(selL(), toCanvas(e)) ? "move" : "alias";
});

/* ---------- 도형 그리기 ---------- */
const SHAPE_PRESET = { rect: { w: 420, h: 140, fill: "#000000", name: "사각형" }, ellipse: { w: 220, h: 220, fill: "#FFD400", name: "원" }, slant: { w: 520, h: 720, fill: "#000000", slant: 0.3, name: "사선 패널" },
  arrow: { w: 300, h: 160, fill: "#FF3B30", name: "화살표" }, star: { w: 200, h: 200, fill: "#FFE14D", name: "별" }, bubble: { w: 360, h: 220, fill: "#FFFFFF", name: "말풍선" } };
function addShape(kind, p) {
  commitXform(); commit("도형 추가");
  const l = kind === "burst" ? L("shape", { shape: "burst", name: "집중선", x: 0, y: 0, w: W, h: H, fill: "#FFFFFF", opacity: 0.55, lines: 70, inner: 0.42 })
    : L("shape", Object.assign({ shape: kind }, SHAPE_PRESET[kind]));
  if (kind !== "burst") { l.x = p.x - l.w / 2; l.y = kind === "slant" ? 0 : p.y - l.h / 2; if (kind === "slant") l.h = H; }
  D.doc.layers.push(l); selIds = [l.id]; changed(); showTab("props");
}
function drawShapeDrag(e, p) {
  const before = snapshot(), kind = tool; let l = null;
  const mv = ev => {
    const q = toCanvas(ev); let w = q.x - p.x, h = q.y - p.y; if (!l && Math.hypot(w, h) * Z < 4) return;
    if (!l) { commitXform(); l = L("shape", Object.assign({ shape: kind }, SHAPE_PRESET[kind])); D.doc.layers.push(l); selIds = [l.id]; }
    if (ev.shiftKey) { const s = Math.max(Math.abs(w), Math.abs(h)); w = Math.sign(w || 1) * s; h = Math.sign(h || 1) * s; }
    if (ev.altKey) Object.assign(l, { x: p.x - Math.abs(w), y: p.y - Math.abs(h), w: Math.abs(w) * 2, h: Math.abs(h) * 2 });
    else Object.assign(l, { x: Math.min(p.x, p.x + w), y: Math.min(p.y, p.y + h), w: Math.abs(w), h: Math.abs(h) });
    l.w = Math.max(2, l.w); l.h = Math.max(2, l.h); renderAll(); readout(ev, `W ${Math.round(l.w)}  H ${Math.round(l.h)} px`);
  };
  const up = () => { removeEventListener("mousemove", mv); removeEventListener("mouseup", up); hideReadout(); if (!l) return addShape(kind, p); pushUndo(before, "도형 그리기"); changed(); showTab("props"); };
  addEventListener("mousemove", mv); addEventListener("mouseup", up);
}

/* ---------- 글자 바로 고치기 ---------- */
function addText(p) {
  commit("글자 추가"); const l = L("text", { text: "제목을 입력", size: Math.round(110 * W / 1280) }); fitText(l); l.x = p.x - 12; l.y = p.y - l.h / 2;
  D.doc.layers.push(l); selIds = [l.id]; startEdit(l, true); if (editing) editing.isNew = true; scheduleSave();
}
function placeInline() {
  const l = editing && byId(editing.id); if (!l) return;
  const m = textLayout(ctx, l), sx = l.w / m.natW, sy = l.h / m.natH, ta = editing.ta, k = sx / sy, w = l.w / k;
  Object.assign(ta.style, { left: (l.x + l.w / 2 - w / 2) * Z + "px", top: l.y * Z + "px", width: w * Z + "px", height: l.h * Z + "px", transformOrigin: "50% 50%",
    transform: `rotate(${l.rot}deg) skewX(${-(l.skew || 0)}deg) scaleX(${k})`, font: `${l.size * sy * Z}px/${l.size * l.lh * sy * Z}px "${l.font}"`,
    letterSpacing: l.ls * sy * Z + "px", padding: `${m.pad * sy * Z}px ${m.pad * sy * Z}px`, textAlign: l.align, color: "transparent" });
}
function startEdit(l, all) {
  if (editing) endEdit();
  if (l.locked) return toast("잠긴 레이어예요 (레이어 패널의 🔒를 풀어 주세요)");
  const ta = document.createElement("textarea"); ta.className = "inline"; ta.spellcheck = false; ta.value = l.text; $("ui").appendChild(ta);
  editing = { id: l.id, ta, before: snapshot(), ul: undoMark() };
  const upd = () => { textSel = { id: l.id, s: ta.selectionStart, e: ta.selectionEnd }; syncFields(); };
  ta.addEventListener("mousedown", ev => ev.stopPropagation());
  ta.addEventListener("input", () => { applyTextEdit(l, ta.value, ta.selectionEnd); syncTextareas(ta, l); upd(); renderAll(); renderLayers(); scheduleSave(); });
  for (const ev of ["select", "keyup", "mouseup"]) ta.addEventListener(ev, upd);
  ta.addEventListener("keydown", ev => {
    const mod = ev.ctrlKey || ev.metaKey;
    if (ev.key === "Escape" || (ev.key === "Enter" && mod)) { ev.stopPropagation(); ev.preventDefault(); endEdit(); return; }
    if ((mod && ev.code === "KeyS") || ev.key === "F1") return;  // 저장·도움말은 전체 단축키로
    ev.stopPropagation();
  });
  refreshUI(); showTab("props"); placeInline();
  ta.focus(); if (all) ta.select(); else ta.setSelectionRange(ta.value.length, ta.value.length); upd();
}
function endEdit() {
  if (!editing) return; const { ta, before, ul, id, isNew } = editing; editing = null; ta.remove(); textSel = null;
  const l = byId(id); cutUndoTo(ul);
  if (l && !l.text.trim()) {  // 글자를 다 지우면 레이어 삭제 (원래 있던 레이어면 Ctrl+Z로 되살릴 수 있게)
    D.doc.layers = D.doc.layers.filter(x => x !== l); selIds = selIds.filter(x => x !== id);
    if (!isNew) pushUndo(before, "글자 레이어 삭제");
  }
  else if (before !== snapshot()) pushUndo(before, "글자 수정");
  scheduleSave(); changed();
}

/* ---------- 누끼 브러시 ---------- */
function editCanvas(l) {
  if (l._edit) return l._edit;
  const im = img(l.src); if (!im) return null;
  const c = newCanvas(im.naturalWidth, im.naturalHeight); c.getContext("2d").drawImage(im, 0, 0); l._edit = c; l._ev = 0; return c;
}
function imgPoint(l, p, im) {
  const q = toLocal(l, p), rx = q.x + l.w / 2, ry = q.y + l.h / 2;
  const sx = im.width * (l.cropL || 0), sy = im.height * (l.cropT || 0), sw = im.width * (1 - (l.cropL || 0) - (l.cropR || 0)), sh = im.height * (1 - (l.cropT || 0) - (l.cropB || 0)), ar = sw / sh; let dw, dh;
  if (l.fit === "cover") { if (l.w / l.h > ar) { dw = l.w; dh = l.w / ar; } else { dh = l.h; dw = l.h * ar; } } else { if (l.w / l.h > ar) { dh = l.h; dw = l.h * ar; } else { dw = l.w; dh = l.w / ar; } }
  const ox = (l.w - dw) * l.fx, oy = (l.h - dh) * l.fy; let u = (rx - ox) / dw; if (l.flipX) u = (l.w - rx - ox) / dw;
  return { x: sx + u * sw, y: sy + (ry - oy) / dh * sh, k: sw / dw };
}
const tmpC = document.createElement("canvas");
function softFill(g, x, y, r, hard) { const gr = g.createRadialGradient(x, y, Math.min(r * hard, r * 0.999), x, y, r); gr.addColorStop(0, "rgba(0,0,0,1)"); gr.addColorStop(1, "rgba(0,0,0,0)"); return gr; }
function brushDown(e) {
  const l = selL(); if (!l || l.type !== "image" || !l.orig) return toast("누끼 딴 이미지 레이어를 먼저 골라 주세요");
  const ec = editCanvas(l), orig = img(l.orig); if (!ec || !orig) return toast("이미지를 불러오는 중이에요");
  const before = snapshot(), g = ec.getContext("2d"), hard = brush.hard / 100, erase = tool === "eraser"; let last = null;
  const dab = q => {
    const r = Math.max(0.5, brush.size / 2 * q.k);
    if (erase) { g.save(); g.globalCompositeOperation = "destination-out"; g.fillStyle = softFill(g, q.x, q.y, r, hard); g.beginPath(); g.arc(q.x, q.y, r, 0, Math.PI * 2); g.fill(); g.restore(); return; }
    const s = Math.ceil(r * 2) + 2; tmpC.width = s; tmpC.height = s; const t = tmpC.getContext("2d"), ox = q.x - s / 2, oy = q.y - s / 2;
    t.drawImage(orig, -ox, -oy, ec.width, ec.height); t.globalCompositeOperation = "destination-in"; t.fillStyle = softFill(t, s / 2, s / 2, r, hard); t.fillRect(0, 0, s, s);
    g.drawImage(tmpC, ox, oy);
  };
  const stroke = ev => {
    const q = imgPoint(l, toCanvas(ev), ec);
    if (last) { const d = Math.hypot(q.x - last.x, q.y - last.y), st = Math.max(1, brush.size / 2 * q.k * 0.25), n = Math.ceil(d / st); for (let i = 1; i <= n; i++) dab({ x: last.x + (q.x - last.x) * i / n, y: last.y + (q.y - last.y) * i / n, k: q.k }); }
    else dab(q);
    last = q; l._ev = (l._ev || 0) + 1; renderAll();
  };
  l._fast = true;  // 칠하는 동안엔 테두리·그림자 생략 (빠르게), 떼면 다시 그림
  stroke(e);
  const up = async () => {
    removeEventListener("mousemove", stroke); removeEventListener("mouseup", up); delete l._fast; renderAll();
    pushUndo(before, erase ? "누끼 지우기" : "누끼 되살리기");  // 올리기 전에 바로 기록 (올리는 사이 Ctrl+Z 해도 순서가 맞게)
    $("saved").textContent = "브러시 저장 중…";
    const j = await post("/api/thumb/upload", { data: ec.toDataURL("image/png") });
    if (byId(l.id) === l) { l.src = j.url; img(j.url); } scheduleSave();
  };
  addEventListener("mousemove", stroke); addEventListener("mouseup", up);
}

/* ---------- 스포이드 ---------- */
function eyeDrop(e) {
  const r = cv.getBoundingClientRect(), x = Math.floor((e.clientX - r.left) / r.width * cv.width), y = Math.floor((e.clientY - r.top) / r.height * cv.height);
  if (x < 0 || y < 0 || x >= cv.width || y >= cv.height) return;
  let d; try { d = ctx.getImageData(x, y, 1, 1).data; } catch (err) { return toast("이 위치의 색은 읽을 수 없어요"); }
  fg = "#" + [d[0], d[1], d[2]].map(v => v.toString(16).padStart(2, "0")).join("").toUpperCase(); useColor(fg);
  const l = selL();
  if (eyeApply && l && (l.type === "text" || l.type === "shape")) { commit("스포이드 색"); if (l.type === "text") charSet(l, "fill", fg); else l.fill = fg; changed(); }
  renderOpts(); toast(`색을 골랐어요 ${fg}${eyeApply && l ? " · 선택한 레이어에 넣었어요" : ""}`);
}

/* ---------- 오른쪽 클릭 메뉴 ---------- */
let menu = null;
function closeMenu() { if (menu) { menu.remove(); menu = null; } }
function openMenu(x, y, html) {
  closeMenu(); menu = document.createElement("div"); menu.className = "ctxmenu"; menu.innerHTML = html; document.body.appendChild(menu);
  menu.style.left = Math.min(x, innerWidth - menu.offsetWidth - 6) + "px"; menu.style.top = Math.min(y, innerHeight - menu.offsetHeight - 6) + "px";
  menu.addEventListener("click", ev => {
    const d = ev.target.closest("[data-pick]"), a = ev.target.closest("[data-mact]");
    if (d) select([d.dataset.pick]); if (a && MACT[a.dataset.mact]) MACT[a.dataset.mact](); closeMenu();
  });
}
const MENU_ACTS = () => (selIds.length ? `<hr>${selIds.length > 1 ? `<div data-mact="group">그룹으로 묶기 <span class="hint">Ctrl+G</span></div>` : ""}${selLs().some(l => l.gid) ? `<div data-mact="ungroup">묶음 풀기 <span class="hint">Ctrl+Shift+G</span></div>` : ""}<div data-mact="copy">복사 <span class="hint">Ctrl+C</span></div><div data-mact="dup">복제 <span class="hint">Ctrl+J</span></div><div data-mact="xform">자유 변형 <span class="hint">Ctrl+T</span></div><div data-mact="ls">레이어 스타일…</div><div data-mact="front">맨 앞으로 <span class="hint">Ctrl+Shift+]</span></div><div data-mact="back">맨 뒤로 <span class="hint">Ctrl+Shift+[</span></div><div data-mact="hide">숨기기 <span class="hint">Ctrl+,</span></div><div data-mact="del">삭제 <span class="hint">Delete</span></div>` : "")
  + (CLIP ? `<hr><div data-mact="paste">붙여넣기 <span class="hint">Ctrl+V</span></div>` : "") + `<hr><div data-mact="all">모두 선택 <span class="hint">Ctrl+A</span></div>`;
stage.addEventListener("contextmenu", e => {
  e.preventDefault(); if (!D) return; const p = toCanvas(e);
  const hits = [...D.doc.layers].reverse().filter(l => !l.hidden && inside(l, p));
  openMenu(e.clientX, e.clientY, (hits.length ? `<div class="hint" style="padding:3px 10px">여기 겹친 레이어 (위→아래)</div>` + hits.map(l => `<div data-pick="${l.id}">${l.locked ? "🔒 " : ""}${{ image: "🖼", text: "T", shape: "▭" }[l.type]} ${esc(lname(l))}</div>`).join("") : "") + MENU_ACTS());
});
addEventListener("mousedown", e => { if (menu && !e.target.closest(".ctxmenu")) closeMenu(); }, true);
document.addEventListener("contextmenu", e => { if (!e.target.closest("textarea, input")) e.preventDefault(); });
