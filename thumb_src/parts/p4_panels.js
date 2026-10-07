
/* ---------- 글자 스타일 프리셋 (크기 110 기준) ---------- */
const TEXT_PRESETS = [
  ["노랑 굵은 테두리", { fill: "#FFE14D", fill2: "", strokes: [{ color: "#111111", width: 26 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#000000", blur: 0, dx: 9, dy: 9, opacity: 1 }, glow: { on: false }, outline: { on: false }, box: { on: false } }],
  ["흰 글자 2중 테두리", { fill: "#FFFFFF", fill2: "", strokes: [{ color: "#000000", width: 34 }, { color: "#FF3B30", width: 16 }], shadow: { on: true, color: "#000000", blur: 0, dx: 8, dy: 8, opacity: 1 }, glow: { on: false }, outline: { on: false }, box: { on: false } }],
  ["금색 그라데이션", { fill: "#FFF6B0", fill2: "#FF9E00", gradAngle: 90, strokes: [{ color: "#3A1D00", width: 24 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#000000", blur: 6, dx: 6, dy: 10, opacity: 0.9 }, glow: { on: false }, outline: { on: true, color: "#FFFFFF", width: 6 }, box: { on: false } }],
  ["네온 보라", { fill: "#FFFFFF", fill2: "", strokes: [{ color: "#B26BFF", width: 10 }, { color: "#FFFFFF", width: 0 }], shadow: { on: false }, glow: { on: true, color: "#9B5CFF", size: 40, opacity: 1 }, outline: { on: false }, box: { on: false } }],
  ["네온 연두", { fill: "#F4FFE0", fill2: "", strokes: [{ color: "#A6FF00", width: 10 }, { color: "#FFFFFF", width: 0 }], shadow: { on: false }, glow: { on: true, color: "#7CFF00", size: 36, opacity: 1 }, outline: { on: false }, box: { on: false } }],
  ["빨간 박스", { fill: "#FFFFFF", fill2: "", strokes: [{ color: "#000000", width: 0 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#000000", blur: 18, dx: 0, dy: 8, opacity: 0.6 }, glow: { on: false }, outline: { on: false }, box: { on: true, color: "#E50914", pad: 18, radius: 6 } }],
  ["검정 박스 노랑", { fill: "#FFE14D", fill2: "", strokes: [{ color: "#000000", width: 0 }, { color: "#FFFFFF", width: 0 }], shadow: { on: false }, glow: { on: false }, outline: { on: false }, box: { on: true, color: "#000000", pad: 18, radius: 0 } }],
  ["입체 그림자", { fill: "#FFFFFF", fill2: "", strokes: [{ color: "#000000", width: 18 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#FF3B30", blur: 0, dx: 12, dy: 12, opacity: 1 }, glow: { on: false }, outline: { on: true, color: "#000000", width: 6 }, box: { on: false } }],
  ["스티커", { fill: "#FF3B30", fill2: "", strokes: [{ color: "#FFFFFF", width: 22 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#000000", blur: 14, dx: 0, dy: 8, opacity: 0.7 }, glow: { on: false }, outline: { on: true, color: "#111111", width: 6 }, box: { on: false } }],
  ["3D 입체 노랑", { fill: "#FFE14D", fill2: "#FFB800", gradAngle: 90, strokes: [{ color: "#111111", width: 14 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#000000", blur: 18, dx: 0, dy: 10, opacity: 0.7 }, glow: { on: false }, outline: { on: false }, box: { on: false }, extrude: { on: true, color: "#8A3B00", depth: 16, angle: 50 } }],
  ["3D 입체 빨강", { fill: "#FFFFFF", fill2: "", strokes: [{ color: "#E50914", width: 14 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#000000", blur: 14, dx: 0, dy: 8, opacity: 0.7 }, glow: { on: false }, outline: { on: false }, box: { on: false }, extrude: { on: true, color: "#5A0000", depth: 18, angle: 55 } }],
  ["깔끔 흰 글자", { fill: "#FFFFFF", fill2: "", strokes: [{ color: "#000000", width: 0 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#000000", blur: 24, dx: 0, dy: 6, opacity: 0.8 }, glow: { on: false }, outline: { on: false }, box: { on: false } }],
];
function applyPreset(l, p) {
  const k = l.size / 110, s = v => Math.round(v * k * 10) / 10, st = JSON.parse(JSON.stringify(p));
  st.strokes.forEach(x => (x.width = s(x.width)));
  if (!st.extrude) st.extrude = { on: false };
  for (const n of ["shadow", "glow", "outline", "box", "extrude"]) { const o = st[n]; if (!o) continue; for (const f of ["blur", "dx", "dy", "size", "width", "pad", "radius", "depth"]) if (o[f] !== undefined) o[f] = s(o[f]); Object.assign(l[n], o); delete st[n]; }
  Object.assign(l, st);
  for (const r of l.runs) { delete r.fill; delete r.s1c; delete r.s1w; delete r.s2c; delete r.s2w; }
  compress(l, expand(l)); refitKeep(l);
}
function presetsHtml() { return `<div class="presets">${TEXT_PRESETS.map((p, i) => `<canvas width="132" height="56" data-preset="${i}" title="${p[0]}"></canvas>`).join("")}</div>`; }
let PRESET_BMP = [], PRESET_VER = -1;
function presetBitmap(i) {
  if (PRESET_VER !== FONT_VER) { PRESET_BMP = []; PRESET_VER = FONT_VER; }
  if (!PRESET_BMP[i]) {
    const c = newCanvas(132, 56), g = c.getContext("2d"), l = L("text", { text: "풋살", size: 110 }); applyPreset(l, TEXT_PRESETS[i][1]);
    const k = Math.min(120 / l.w, 46 / l.h); l.x = (132 / k - l.w) / 2; l.y = (56 / k - l.h) / 2;
    g.fillStyle = "#3a3a3a"; g.fillRect(0, 0, 132, 56); g.save(); g.scale(k, k); drawLayer(g, l, k); g.restore(); PRESET_BMP[i] = c;
  }
  return PRESET_BMP[i];
}
function drawPresets(root) { root.querySelectorAll("canvas[data-preset]").forEach(cn => cn.getContext("2d").drawImage(presetBitmap(+cn.dataset.preset), 0, 0)); }
document.addEventListener("click", e => {
  const cn = e.target.closest("canvas[data-preset]"); if (!cn) return;
  const l = selL(); if (!l || l.type !== "text") return;
  commit("글자 스타일"); applyPreset(l, TEXT_PRESETS[+cn.dataset.preset][1]); changed(); toast(`'${TEXT_PRESETS[+cn.dataset.preset][0]}' 스타일을 넣었어요`);
});

/* ---------- 속성 패널 ---------- */
function xfHtml() {
  const n = (k, lab, t) => `<span class="scrub" data-scrub="${k}" title="${t || SCRUB_T}">${lab}</span><input class="num" type="number" step="${(FM[k] || {}).step || 1}" data-k="${k}">`;
  return `<div class="xf">${n("x", "X")}${n("y", "Y")}${n("w", "W")}${n("h", "H")}${n("rot", "∠", "회전 각도 (좌우로 끌어서 조절)")}${n("skew", "⫽", "기울기 각도")}</div>
    <div class="row2"><button class="btn sm ${lockRatio ? "pri" : ""}" data-act="lock" title="W·H 비율 고정">🔗 비율 고정 ${lockRatio ? "켬" : "끔"}</button><button class="btn sm" data-act="xform" title="Ctrl+T">자유 변형</button><button class="btn sm" data-act="flipH">좌우 뒤집기</button></div>`;
}
function textFieldsHtml() {
  return `<div class="selinfo" data-selinfo></div>
    ${F("c.font", "글꼴", { t: "sel", opts: FONT_OPTS })}${F("c.size", "글자 크기")}${F("c.fill", "글자색", { t: "col" })}${swatches("c.fill")}
    ${F("c.s1c", "바깥 테두리", { t: "col" })}${F("c.s1w", "└ 두께")}${F("c.s2c", "안쪽 테두리", { t: "col" })}${F("c.s2w", "└ 두께")}
    ${F("align", "정렬", { t: "seg", opts: [["left", "왼쪽"], ["center", "가운데"], ["right", "오른쪽"]] })}
    ${fx("space", "행간 · 자간 · 장평 · 기울기", F("lh", "행간") + F("ls", "자간") + F("hs", "가로 비율") + F("vs", "세로 비율") + F("skew", "기울기"))}
    ${fx("grad", "그라데이션 채우기 (글자색 → 끝 색)", F("fill2", "끝 색", { t: "col" }) + F("gradAngle", "방향"), "grad.on")}
    ${fx("box", "글자 뒤 상자", F("box.color", "상자 색", { t: "col" }) + F("box.pad", "여백") + F("box.radius", "둥글기"), "box.on")}`;
}
function fxCommonHtml() {
  return `${fx("outline", "획 (바깥 테두리 · 스티커)", F("outline.color", "색", { t: "col" }) + F("outline.width", "두께"), "outline.on")}
    ${fx("shadow", "드롭 섀도 (그림자)", F("shadow.color", "색", { t: "col" }) + F("shadow.opacity", "진하기") + F("shadow.dx", "가로") + F("shadow.dy", "세로") + F("shadow.blur", "흐림"), "shadow.on")}
    ${fx("extrude", "3D 입체 (돌출)", F("extrude.color", "옆면 색", { t: "col" }) + F("extrude.depth", "깊이") + F("extrude.angle", "방향"), "extrude.on")}
    ${fx("glow", "외부 광선 (네온)", F("glow.color", "색", { t: "col" }) + F("glow.opacity", "진하기") + F("glow.size", "크기"), "glow.on")}
    ${fx("overlay", "색상 오버레이 (색 덮기)", F("overlay.color", "색", { t: "col" }) + F("overlay.opacity", "진하기"), "overlay.on")}
    ${fx("fade", "레이어 마스크 (한쪽으로 서서히 사라지기)", fadeHtml(), "fade.on")}
    ${fx("warp", "뒤틀기 (아치·깃발·부풀리기)", warpHtml())}`;
}
const WARP_OPTS = [["none", "없음"], ["arc", "아치"], ["flag", "깃발"], ["bulge", "부풀리기"]];
const warpHtml = () => F("warp.style", "모양", { t: "seg", opts: WARP_OPTS }) + F("warp.bend", "구부림") + `<div class="hint">구부림을 음수로 하면 반대로 휘어요.</div>`;
function fadeHtml() {
  return `<div class="row2">${[[0, "→ 오른쪽"], [180, "← 왼쪽"], [90, "↓ 아래"], [270, "↑ 위"]].map(([a, t]) => `<button class="btn sm" data-act="fadeDir" data-v="${a}">${t}으로 사라짐</button>`).join("")}</div>`
    + F("fade.angle", "방향") + F("fade.start", "사라지기 시작") + F("fade.end", "완전히 사라짐");
}
function imageFieldsHtml(l) {
  return `${F("fit", "맞춤", { t: "seg", opts: [["cover", "꽉 채우기"], ["contain", "전체 보이기"]] })}${F("fx", "가로 초점")}${F("fy", "세로 초점")}${F("slant", "사선 자르기")}
    <div class="row2"><button class="btn pri" id="iCut" data-act="cut">✂ 누끼 따기 (고품질)</button><button class="btn" data-act="cutFast">빠르게</button>${l.orig ? `<button class="btn" data-act="brushB">🖌 다듬기</button>` : ""}</div>
    <div class="hint" style="margin-bottom:8px">${l.orig ? "누끼 레이어예요. B 되살리기 · E 지우개로 가장자리를 다듬어요." : "인물·사물만 남겨요. 처음엔 모델을 내려받아 시간이 걸려요."}</div>
    ${fx("crop", "원본 자르기 (영상 속 옛 자막·로고 가리기)", F("cropB", "아래") + F("cropT", "위") + F("cropL", "왼쪽") + F("cropR", "오른쪽") + `<div class="hint">영상에 박힌 자막이 보이면 '아래'를 25% 정도로 올려 보세요.</div>`)}
    ${fx("adjust", "보정 (밝기·대비·채도·색조·비네팅)", F("bright", "밝기") + F("contrast", "대비") + F("sat", "채도") + F("hue", "색조") + F("blur", "흐림") + F("vignette", "가장자리 어둡게") + `<button class="btn sm" data-act="iReset">보정 초기화</button>`)}`;
}
const SHAPES = [["rect", "사각형"], ["ellipse", "원"], ["slant", "사선 패널"], ["burst", "집중선"], ["arrow", "화살표"], ["star", "별"], ["bubble", "말풍선"]];
function shapeFieldsHtml(l) {
  return `${F("shape", "모양", { t: "sel", opts: SHAPES })}${F("fill", "색", { t: "col" })}${swatches("fill")}
    ${l.shape === "rect" ? F("radius", "둥글기") : ""}${l.shape === "slant" ? F("slant", "기울기") : ""}${l.shape === "burst" ? F("lines", "선 개수") + F("inner", "가운데 빈 곳") : ""}
    ${fx("grad", "그라데이션", F("fill2", "끝 색", { t: "col" }) + F("gradAngle", "방향"), "grad.on")}
    ${l.shape !== "burst" ? fx("sstroke", "도형 테두리", F("stroke.color", "색", { t: "col" }) + F("stroke.width", "두께")) : ""}`;
}
function renderProps() {
  const t = $("tab-props"); if (!t || !D) return; const ls = selLs();
  if (tool === "brush" || tool === "eraser") {
    t.innerHTML = `<div class="pg"><h4>누끼 다듬기</h4><div class="hint" style="margin-bottom:8px">🖌 B 되살리기 · ⌫ E 지우개 · X 전환<br>[ ] 크기 · Shift+[ ] 경도 (낮을수록 가장자리가 부드러워요)</div>${F("b.size", "크기")}${F("b.hard", "경도")}<button class="btn" data-act="brushDone">다듬기 끝 (V)</button></div>`;
    syncFields(); return;
  }
  if (!ls.length) {
    const fmt = W > H ? "long" : "short";
    t.innerHTML = `<div class="pg"><h4>캔버스</h4><div class="row2">${Object.entries(FMT).map(([k, f]) => `<button class="btn ${fmt === k ? "pri" : ""}" data-act="fmt" data-v="${k}">${f.label} · ${f.w}×${f.h}</button>`).join("")}</div>
      ${F("bg", "배경색", { t: "col" })}<div class="hint">쇼츠 썸네일은 쇼츠(9:16)로 만들어야 쇼츠 화면에 꽉 차요.</div></div>
      <div class="pg"><h4>작업 내역 <span class="hint">누르면 그 단계로 돌아가요</span></h4><div class="hist">${histNames.map((n, i) => `<div data-act="hist" data-v="${i}">${i + 1}. ${esc(n)}</div>`).slice(-30).join("") || `<span class="hint">아직 없어요</span>`}<div class="cur">● 지금</div></div></div>
      <div class="pg hint">레이어를 누르면 여기서 자세히 조절해요 · F1 단축키 (포토샵과 같아요)</div>`;
    syncFields(); return;
  }
  if (ls.length > 1) {
    t.innerHTML = `<div class="pg"><h4>레이어 ${ls.length}개 선택</h4>${xfHtml()}${F("opacity", "불투명도")}<div class="row2">${ALIGN_HTML}${DIST_HTML}</div><div class="hint">맞춤은 고른 레이어들 기준이에요. 캔버스 기준으로 맞추려면 하나만 고르세요.</div></div>`;
    syncFields(); return;
  }
  const l = ls[0];
  let h = `<div class="pg"><h4>${{ image: "🖼 이미지", text: "T 글자", shape: "▭ 도형" }[l.type]} 레이어 <span class="sp"></span><button class="btn sm" data-act="ls">fx 레이어 스타일…</button></h4>
    ${F("name", "이름", { t: "txt", ph: lname(l) })}${xfHtml()}${F("opacity", "불투명도")}${F("blend", "혼합 모드", { t: "sel", opts: BLENDS })}
    <div class="row2">${ALIGN_HTML}<span class="hint" style="align-self:center">캔버스 기준</span></div></div>`;
  if (l.type === "text") h += `<div class="pg"><h4>글자 <span class="sp"></span><button class="btn sm" data-act="cp">文 문자 패널 (글자별 편집)</button></h4>${presetsHtml()}<textarea class="s" id="xText" spellcheck="false"></textarea><div class="chips" title="글자를 누르거나 끌어서 고르면, 고른 글자만 색·크기·테두리가 바뀌어요"></div>${textFieldsHtml()}</div>`;
  if (l.type === "image") h += `<div class="pg"><h4>이미지</h4>${imageFieldsHtml(l)}</div>`;
  if (l.type === "shape") h += `<div class="pg"><h4>도형</h4>${shapeFieldsHtml(l)}</div>`;
  h += `<div class="pg"><h4>효과</h4>${fxCommonHtml()}</div>`;
  t.innerHTML = h;
  if (l.type === "text") { bindTextArea($("xText"), l); drawPresets(t); }
  syncFields();
}

/* ---------- 글자 입력칸 · 글자 칩 ---------- */
function syncTextareas(src, l) { for (const el of [$("xText"), $("cpText"), editing && editing.ta]) if (el && el !== src && el.value !== l.text) el.value = l.text; }
function bindTextArea(ta, l) {
  if (!ta) return; ta.value = l.text;
  const upd = () => { if (document.activeElement !== ta) return; textSel = { id: l.id, s: ta.selectionStart, e: ta.selectionEnd }; syncFields(); };
  ta.addEventListener("input", () => { liveStart(); applyTextEdit(l, ta.value, ta.selectionEnd); syncTextareas(ta, l); upd(); renderAll(); renderLayers(); scheduleSave(); });
  ta.addEventListener("blur", () => liveEnd());
  for (const ev of ["select", "keyup", "mouseup"]) ta.addEventListener(ev, upd);
  if (textSel && textSel.id === l.id) try { ta.setSelectionRange(textSel.s, textSel.e); } catch (e) {}
}
const GRAPH = new Intl.Segmenter(undefined, { granularity: "grapheme" });  // 만들기가 무거워서 한 번만
function chipsHtml(l) {
  const k = JSON.stringify([l.text, l.runs, l.fill, l.size, l.font, l.strokes]);  // 글자·스타일이 그대로면 지난번 것
  if (l._ch && l._ch.k === k) return l._ch.h;
  const a = expand(l), b = baseStyle(l);
  const h = [...GRAPH.segment(l.text)].map(({ segment: ch, index: i }) => {
    if (ch === "\n") return `<i class="br"></i>`;
    const st = Object.assign({}, b, a[i]), fs = clamp(17 * st.size / Math.max(1, l.size), 9, 34);
    const sh = st.s1w > 0 ? `text-shadow:0 0 1px ${st.s1c},0 0 1px ${st.s1c},0 0 2px ${st.s1c};` : "";
    return `<span data-ci="${i}" data-cl="${ch.length}" style="color:${st.fill};font-family:'${st.font}';font-size:${fs}px;${sh}">${ch === " " ? "&nbsp;" : esc(ch)}</span>`;
  }).join("");
  l._ch = { k, h }; return h;
}
function refreshCharUI() {
  const l = selL(); if (!l || l.type !== "text") return;
  const html = chipsHtml(l), r = curRange(l), rr = r || [-1, -1];
  document.querySelectorAll(".chips").forEach(c => {
    if (c._h === html && c._r === rr.join()) return;  // 글자도 고른 범위도 그대로면 건너뜀
    if (c._h !== html) { c.innerHTML = html; c._h = html; }
    c._r = rr.join();
    c.querySelectorAll("[data-ci]").forEach(s => { const i = +s.dataset.ci; s.classList.toggle("on", i >= rr[0] && i < rr[1]); });
  });
  const info = r
    ? `✏️ 고른 <b>${r[1] - r[0]}글자</b>에만 적용돼요 · <a href="#" data-act="selAll">글자 전체에 적용</a>`
    : `글자 전체에 적용돼요 · <b>일부 글자만</b> 바꾸려면 위 글자칸이나 글자 칩을 드래그해서 고르세요`;
  document.querySelectorAll("[data-selinfo]").forEach(s => { if (s._h !== info) { s.innerHTML = info; s._h = info; } });
}
function setTextSel(l, s, e) {
  textSel = { id: l.id, s, e };
  for (const t of [$("xText"), $("cpText"), editing && editing.ta]) if (t) try { t.setSelectionRange(s, e); } catch (_) {}
  syncFields();
}
let chipDrag = null;
document.addEventListener("mousedown", e => {
  const c = e.target.closest && e.target.closest(".chips [data-ci]"); if (!c || e.button !== 0) return;
  e.preventDefault(); const l = selL(); if (!l) return; const i = +c.dataset.ci;
  const len = +(c.dataset.cl || 1), a = e.shiftKey && textSel && textSel.id === l.id ? textSel.s : i; chipDrag = { a }; setTextSel(l, Math.min(a, i), Math.max(a, i + len));
});
document.addEventListener("mouseover", e => {
  if (!chipDrag) return; if (!(e.buttons & 1)) { chipDrag = null; return; }
  const c = e.target.closest && e.target.closest(".chips [data-ci]"); if (!c) return;
  const i = +c.dataset.ci, len = +(c.dataset.cl || 1), l = selL(); if (l) setTextSel(l, Math.min(chipDrag.a, i), Math.max(chipDrag.a, i + len));
});
addEventListener("mouseup", () => (chipDrag = null)); addEventListener("blur", () => (chipDrag = null));

/* ---------- 떠 있는 창: 문자 패널 · 레이어 스타일 ---------- */
function floatPanel(id, title, width) {
  const p = document.createElement("div"); p.className = "cpanel"; p.id = id; p.style.width = width + "px";
  p.innerHTML = `<div class="hd"><span>${title}</span><div class="sp"></div><button class="ic" data-act="close" data-v="${id}">✕</button></div><div class="bd"></div>`;
  document.body.appendChild(p);
  p.querySelector(".hd").addEventListener("mousedown", e => {
    if (e.target.closest("button")) return; const r = p.getBoundingClientRect(), x0 = e.clientX, y0 = e.clientY;
    const mv = ev => { p.style.left = clamp(r.left + ev.clientX - x0, 0, innerWidth - 120) + "px"; p.style.top = clamp(r.top + ev.clientY - y0, 0, innerHeight - 40) + "px"; p.style.right = "auto"; };
    const up = () => { removeEventListener("mousemove", mv); removeEventListener("mouseup", up); };
    addEventListener("mousemove", mv); addEventListener("mouseup", up);
  });
  return p;
}
const CP = floatPanel("cpanel", "文 문자 · 글자별 상세 편집", 360), LSP = floatPanel("lspanel", "fx 레이어 스타일", 560);
function toggleCP(on) { on = on ?? !CP.classList.contains("show"); CP.classList.toggle("show", on); renderCP(); }
function renderCP() {
  if (!CP.classList.contains("show")) return; const bd = CP.querySelector(".bd"), l = selL();
  if (!l || l.type !== "text") { bd.innerHTML = `<div class="hint">글자 레이어를 고르면 여기서 글자마다 색·크기·테두리를 따로 바꿀 수 있어요.</div>`; return; }
  bd.innerHTML = `<textarea class="s" id="cpText" spellcheck="false" style="min-height:64px"></textarea>
    <div class="hint" style="margin-top:4px">아래 글자를 누르거나 끌어서 고르세요 (Shift+누르기 = 범위). 고른 글자만 바뀌어요.</div><div class="chips"></div>${textFieldsHtml()}`;
  bindTextArea($("cpText"), l); syncFields();
}
let lsSec = "blend";
function toggleLS(on) { on = on ?? !LSP.classList.contains("show"); LSP.classList.toggle("show", on); renderLS(); }
function renderLS() {
  if (!LSP.classList.contains("show")) return; const bd = LSP.querySelector(".bd"), l = selL();
  if (!l) { bd.innerHTML = `<div class="hint">레이어를 하나 고르면 효과를 자세히 조절할 수 있어요. (레이어 패널에서 레이어를 두 번 눌러도 열려요)</div>`; return; }
  const secs = [["blend", "혼합 옵션", null], ["outline", "획 (바깥 테두리)", "outline.on"], ["shadow", "드롭 섀도", "shadow.on"], ["extrude", "3D 입체 (돌출)", "extrude.on"], ["glow", "외부 광선", "glow.on"], ["overlay", "색상 오버레이", "overlay.on"], ["fade", "레이어 마스크 (페이드)", "fade.on"], ["warp", "뒤틀기", null]];
  if (l.type !== "image") secs.push(["grad", "그라데이션 오버레이", "grad.on"]);
  if (l.type === "text") secs.splice(1, 0, ["tstroke", "글자 테두리 (2겹)", null], ["preset", "스타일 프리셋", null]), secs.push(["box", "글자 뒤 상자", "box.on"]);
  if (l.type === "image") secs.push(["adjust", "이미지 보정", null]);
  if (!secs.find(s => s[0] === lsSec)) lsSec = "blend";
  const body = {
    blend: F("blend", "혼합 모드", { t: "sel", opts: BLENDS }) + F("opacity", "불투명도") + `<div class="hint">혼합 모드: 곱하기=어둡게 겹치기, 스크린=밝게 겹치기, 오버레이=대비 강하게.</div>`,
    preset: presetsHtml(),
    tstroke: F("c.s1c", "바깥 테두리", { t: "col" }) + F("c.s1w", "└ 두께") + F("c.s2c", "안쪽 테두리", { t: "col" }) + F("c.s2w", "└ 두께") + `<div class="selinfo" data-selinfo></div>`,
    outline: F("outline.on", "사용", { t: "chk" }) + F("outline.color", "색", { t: "col" }) + swatches("outline.color", 12) + F("outline.width", "두께"),
    shadow: F("shadow.on", "사용", { t: "chk" }) + F("shadow.color", "색", { t: "col" }) + F("shadow.opacity", "진하기") + F("shadow.dx", "가로 거리") + F("shadow.dy", "세로 거리") + F("shadow.blur", "흐림 (크기)"),
    extrude: F("extrude.on", "사용", { t: "chk" }) + F("extrude.color", "옆면 색", { t: "col" }) + swatches("extrude.color", 12) + F("extrude.depth", "깊이") + F("extrude.angle", "방향") + `<div class="hint">글자를 같은 방향으로 겹겹이 쌓아 튀어나온 것처럼 보이게 해요. 방향 45° = 오른쪽 아래.</div>`,
    glow: F("glow.on", "사용", { t: "chk" }) + F("glow.color", "색", { t: "col" }) + swatches("glow.color", 12) + F("glow.opacity", "진하기") + F("glow.size", "크기"),
    overlay: F("overlay.on", "사용", { t: "chk" }) + F("overlay.color", "색", { t: "col" }) + swatches("overlay.color", 12) + F("overlay.opacity", "진하기"),
    grad: F("grad.on", "사용", { t: "chk" }) + (l.type === "text" ? F("c.fill", "시작 색", { t: "col" }) : F("fill", "시작 색", { t: "col" })) + F("fill2", "끝 색", { t: "col" }) + F("gradAngle", "방향"),
    warp: warpHtml(),
    fade: F("fade.on", "사용", { t: "chk" }) + fadeHtml() + `<div class="hint">장면 두 개를 자연스럽게 겹치거나, 사진 끝을 부드럽게 지울 때 써요.</div>`,
    box: F("box.on", "사용", { t: "chk" }) + F("box.color", "상자 색", { t: "col" }) + F("box.pad", "여백") + F("box.radius", "둥글기"),
    adjust: F("bright", "밝기") + F("contrast", "대비") + F("sat", "채도") + F("hue", "색조") + F("blur", "흐림") + F("vignette", "가장자리 어둡게") + `<button class="btn sm" data-act="iReset">보정 초기화</button>`,
  }[lsSec];
  bd.innerHTML = `<div class="lsgrid"><div class="lsnav">${secs.map(([k, t, on]) => `<div class="${k === lsSec ? "on" : ""}" data-act="lsSec" data-v="${k}">${on ? `<input type="checkbox" data-k="${on}">` : `<span style="width:13px;display:inline-block"></span>`}${t}</div>`).join("")}</div>
    <div class="lsbody"><h4 style="margin:0 0 10px">${secs.find(s => s[0] === lsSec)[1]} <span class="hint">· ${esc(lname(l))}</span></h4>${body}</div></div>`;
  if (lsSec === "preset") drawPresets(bd);
  syncFields();
}

/* ---------- 옵션 막대 (포토샵 상단) ---------- */
function renderOpts() {
  const o = $("opts"); if (!o) return; const l = selL(), ls = selLs(), parts = [], g = s => `<div class="grp">${s}</div>`;
  const zoomGrp = g(`<button class="ic" data-act="zOut" title="축소 (Ctrl+−)">−</button><span class="hint" id="oZoom">${Math.round(Z * 100)}%</span><button class="ic" data-act="zIn" title="확대 (Ctrl++)">＋</button><button class="btn sm" data-act="zFit" title="Ctrl+0">화면 맞춤</button><button class="btn sm" data-act="z100" title="Ctrl+1">100%</button>`);
  if (tool === "brush" || tool === "eraser") {
    parts.push(g(`<b>${tool === "brush" ? "🖌 되살리기 브러시" : "⌫ 지우개"}</b><span class="hint">X 전환</span>`), g(O("b.size", "크기") + O("b.hard", "경도")), g(`<span class="hint">[ ] 크기 · Shift+[ ] 경도</span><button class="btn sm" data-act="brushDone">다듬기 끝 (V)</button>`), zoomGrp);
  } else if (tool === "eye") {
    parts.push(g(`<b>💧 스포이드</b><span class="sw" style="background:${fg}"></span><span class="hint">${fg}</span>`), g(`<label><input type="checkbox" data-act="eyeApply" ${eyeApply ? "checked" : ""}> 고른 색을 선택한 글자·도형에 바로 넣기</label>`), zoomGrp);
  } else if (tool === "hand") {
    parts.push(g(`<b>✋ 손 도구</b><span class="hint">끌어서 화면 이동 · Alt+휠 확대/축소</span>`), zoomGrp);
  } else {
    if (tool === "text" && !(l && l.type === "text")) parts.push(g(`<b>T 문자 도구</b><span class="hint">캔버스를 누르면 새 글자 · 있는 글자를 누르면 바로 고치기</span>`));
    if (["rect", "ellipse", "slant"].includes(tool) && !(l && l.type === "shape")) parts.push(g(`<b>▭ 도형 도구</b><span class="hint">끌어서 그리기 · Shift 정비율 · Alt 가운데부터</span>`));
    if (l && l.type === "text") {
      parts.push(g(`<select class="s" data-k="c.font" title="글꼴">${FONT_OPTS.map(([v, x]) => `<option value="${v}">${x}</option>`).join("")}</select>${O("c.size", "크기")}`),
        g(`<input type="color" data-k="c.fill" title="글자색">${swatches("c.fill", 10)}`),
        g(`<div class="seg" data-segk="align"><button data-v="left" title="왼쪽 정렬">≡⃪</button><button data-v="center" title="가운데 정렬">☰</button><button data-v="right" title="오른쪽 정렬">≡</button></div>`),
        g(`<label>테두리</label><input type="color" data-k="c.s1c">${O("c.s1w", "")}`),
        g(`<button class="btn sm" data-act="cp" title="글자별 상세 편집">文 문자 패널</button><button class="btn sm" data-act="ls">fx 스타일</button>${editing ? `<button class="btn sm pri" data-act="endEdit" title="Esc / Ctrl+Enter">✓ 확정</button>` : ""}`));
      if (editing) parts.push(g(`<span class="selinfo" data-selinfo style="margin:0"></span>`));
    }
    if (l && l.type === "shape") parts.push(g(`<label>칠</label><input type="color" data-k="fill">${swatches("fill", 8)}<label>테두리</label><input type="color" data-k="stroke.color">${O("stroke.width", "")}`));
    if (ls.length && !editing) {
      parts.push(g(O("x", "X") + O("y", "Y")), g(O("w", "W") + `<button class="ic ${lockRatio ? "on" : ""}" data-act="lock" title="가로세로 비율 고정">🔗</button>` + O("h", "H")), g(O("rot", "∠") + O("skew", "⫽")),
        g(ALIGN_HTML + (ls.length >= 3 ? DIST_HTML : "")));
      if (xform) parts.push(g(`<b style="color:#fff">자유 변형</b><button class="btn sm pri" data-act="xOk" title="Enter">✓ 확정</button><button class="btn sm" data-act="xNo" title="Esc">✕ 취소</button>`));
    } else if (tool === "move" && !ls.length) parts.push(g(`<b>➤ 이동 도구</b><span class="hint">누르면 선택 · Shift+누르기 여러 개 · 오른쪽 클릭 = 겹친 레이어 고르기 · Ctrl+A 전체 선택</span>`));
    parts.push(zoomGrp);
  }
  o.innerHTML = parts.join(""); syncFields();
}
