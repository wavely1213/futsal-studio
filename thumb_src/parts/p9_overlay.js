
/* ---------- 전술 그래픽 넣기 · 점 핸들 · 스티커 · 브랜드 키트 · 검수(모바일 미리보기·글자 읽기·클로드 평가) · A/B 묶음 ---------- */
const TAC_MENU = [["arrow2", "곡선 화살표", {}], ["arrow2", "직선 화살표", { pts: [[0.04, 0.5], [0.96, 0.5]], h: 90 }], ["pass", "패스 점선", {}], ["ring", "발밑 원", {}],
  ["spot", "스포트라이트", {}], ["xmark", "X 표시", {}], ["marker", "공간 칩", { label: "공간" }], ["marker", "번호", { label: "1", fill: "#FFE14D", labelColor: "#111111", stroke: { color: "#111111", width: 6 } }],
  ["sparkle", "반짝이", {}], ["scribble", "손그림 화살표", {}]];
let tacPending = null;
function tacPreset(kind, extra = {}) {  // 처음 놓을 때의 크기·색 (캔버스 1280 기준 → 쇼츠는 비율대로)
  const b = (AI.brand || BRAND_DEF).colors, k = W / 1280, neon = { on: true, color: b.neon, size: Math.round(22 * k), opacity: 1 }, red = { on: true, color: b.accent, size: Math.round(26 * k), opacity: 1 };
  const P = {
    arrow2: { w: 420, h: 200, fill: b.accent, width: 13, head: 50, glow: red },
    pass: { w: 360, h: 140, fill: "#FFFFFF", width: 7, glow: neon },
    ring: { w: 220, h: 66, fill: b.neon, width: 8, glow: neon },
    spot: { w: 240, h: 520, fill: "#FFF3B0", blend: "screen" },
    xmark: { w: 130, h: 130, fill: "#FF2D2D", width: 28, stroke: { color: "#FFFFFF", width: 6 } },
    marker: { w: 120, h: 120, fill: b.accent, stroke: { color: "#FFFFFF", width: 6 }, glow: red },
    sparkle: { w: 80, h: 80, fill: "#FFFFFF", glow: { on: true, color: "#FFFFFF", size: 16, opacity: 1 } },
    scribble: { w: 300, h: 150, fill: "#FFFFFF", width: 9, stroke: { color: "#000000", width: 4 }, seed: Math.floor(Math.random() * 1e6) },
  }[kind];
  const o = Object.assign({}, P, extra);
  for (const key of ["w", "h", "width", "head"]) if (typeof o[key] === "number") o[key] = Math.round(o[key] * k);
  if (o.stroke) o.stroke = Object.assign({}, o.stroke, { width: Math.round(o.stroke.width * k) });
  return o;
}
function placeTac(p) {
  const [kind, label, extra] = tacPending; tacPending = null;
  commitXform(); commit(`${label} 넣기`);
  const o = tacPreset(kind, extra), l = normLayer(L("shape", Object.assign({ shape: kind, name: label }, o)));
  l.x = p.x - l.w / 2; l.y = p.y - l.h / 2;
  D.doc.layers.push(l); selIds = [l.id]; setTool("move"); changed(); showTab("props");
  toast(l.pts ? "점(동그라미)을 끌어 모양을 바꿔요 · Shift: 가로·세로로 반듯하게" : `${label}을(를) 넣었어요`);
}
function openTacMenu(btn) {
  const r = btn.getBoundingClientRect();
  openMenu(r.right + 4, r.top, `<div class="hint" style="padding:3px 10px">전술 그래픽 · 고른 뒤 캔버스를 누르면 그 자리에</div>` + TAC_MENU.map((m, i) => `<div data-mact="tac${i}">${m[1]}</div>`).join("")
    + `<hr><div data-mact="tacAuto">선수 자리에 자동으로 (분석 결과)</div>`);
}
TAC_MENU.forEach((m, i) => (MACT["tac" + i] = () => { tacPending = m; tool = "tac"; markTool(); stage.style.cursor = "crosshair"; toast(`캔버스를 누르면 ${m[1]}을(를) 넣어요 (Esc 취소)`); }));
MACT.tacAuto = async () => {  // 지금 디자인의 배경 장면에서 찾은 선수 자리에 발밑 원·화살표·공간 칩
  const bg = D.doc.layers.find(l => l.type === "image" && l.name === "배경"); if (!bg) return toast("배경 장면이 있는 디자인에서 써 주세요");
  const t = +(new URLSearchParams(String(bg.src).split("?")[1] || "").get("t"));
  if (!AI.loaded) { toast("선수 자리를 찾는 중이에요…"); if (!(await ensureAnalysis())) return; }
  const f = AI.frames.find(x => Math.abs(x.t - t) < 0.05);
  if (!f || !(f.persons || []).length) return toast("이 장면에서 선수를 찾지 못했어요 · 메뉴에서 하나씩 넣어 주세요");
  const cx = ctxFor(f, AI.copy[0] || { l1: "", l2: "" }, H > W ? "short" : "long", 1);
  const heads = D.doc.layers.filter(l => l.type === "text" && !l.hidden).map(l => bbox([l]));
  // 판정 2회차: 이미 자동 전술 묶음이 있으면 같은 자리에 한 벌 더 겹쳐 들어감 → 그 묶음을 바꿔 넣음
  const old = D.doc.layers.filter(l => l.type === "shape" && l.gid && /^tac/.test(l.gid));
  const tries = [() => tactics(cx, bg, heads), () => tactics(cx, bg, heads, { loose: true }), () => tactics(cx, bg, heads, { loose: true, arrow: false }),
    () => tactics(cx, bg, [], { loose: true, arrow: false, forceRing: true })];  // 제목 때문에 못 놓으면 발밑 원만이라도 · 그것도 안 되면 제목 피하기를 빼고 원만
  let ls = [];
  for (const t of tries) { ls = t(); if (ls.length) break; }
  if (!ls.length) return toast("넣을 자리가 없었어요 (선수 발이 화면 밖이에요) · 메뉴에서 하나씩 넣어 주세요");
  commit("전술 그래픽 자동");
  if (old.length) D.doc.layers = D.doc.layers.filter(l => !old.includes(l));
  D.doc.layers.splice(D.doc.layers.indexOf(bg) + 1, 0, ...ls); selIds = ls.map(l => l.id); changed();
  toast(`전술 그래픽 ${ls.length}개를 ${old.length ? "바꿔 " : ""}넣었어요 · 한 묶음이라 함께 옮겨져요 (Ctrl+Shift+G 로 풀기)`);
};
// 점 핸들: 고른 전술 도형의 점마다 동그라미 (선택 상자 안 = 회전·기울기 그대로 따라감)
function ptHandles(l) {
  if (!l || l.type !== "shape" || !Array.isArray(l.pts) || l.locked) return "";
  return l.pts.map((p, i) => `<div class="pt" data-pt="${i}" title="끌어서 점 옮기기" style="left:${p[0] * l.w * Z - 6}px;top:${p[1] * l.h * Z - 6}px"></div>`).join("");
}
function startPtDrag(e, i) {
  const l = selL(); if (!l || !l.pts) return;
  e.preventDefault(); e.stopPropagation();
  const before = snapshot(), a = (l.rot || 0) * RAD;
  const mv = ev => {
    const q = toLocal(l, toCanvas(ev));  // 가운데 기준 · 회전 풀린 좌표
    const P = l.pts.map(p => [p[0] * l.w - l.w / 2, p[1] * l.h - l.h / 2]);
    let nx = q.x, ny = q.y;
    if (ev.shiftKey) { const o = P[i === 0 ? 1 : i - 1]; if (Math.abs(nx - o[0]) > Math.abs(ny - o[1])) ny = o[1]; else nx = o[0]; }
    P[i] = [nx, ny];
    const xs = P.map(p => p[0]), ys = P.map(p => p[1]), x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const w = Math.max(24, x1 - x0), h = Math.max(24, y1 - y0), cxL = (x0 + x1) / 2, cyL = (y0 + y1) / 2;
    const cx = l.x + l.w / 2 + cxL * Math.cos(a) - cyL * Math.sin(a), cy = l.y + l.h / 2 + cxL * Math.sin(a) + cyL * Math.cos(a);
    l.pts = P.map(p => [w > 24 || x1 - x0 >= 24 ? (p[0] - x0) / w : 0.5 + (p[0] - cxL) / w, h > 24 || y1 - y0 >= 24 ? (p[1] - y0) / h : 0.5 + (p[1] - cyL) / h].map(v => Math.round(v * 1e4) / 1e4));
    l.w = w; l.h = h; l.x = cx - w / 2; l.y = cy - h / 2;
    renderAll(); syncFields(null, XFK_SET); readout(ev, `점 ${i + 1} · X ${Math.round(cx)}  Y ${Math.round(cy)}`);
  };
  const up = () => { removeEventListener("mousemove", mv); removeEventListener("mouseup", up); hideReadout(); if (before !== snapshot()) { pushUndo(before, "점 옮기기"); scheduleSave(); } renderAll(); renderProps(); };
  addEventListener("mousemove", mv); addEventListener("mouseup", up);
}
function editMarkerLabel(l) {
  const v = prompt("원 안 글자 (번호·짧은 낱말)", l.label ?? ""); if (v === null) return;
  commit("칩 글자"); l.label = cutText(v, 6); changed();
}
// 속성 칸: 전술 도형
function tacFieldsHtml(l) {
  const k = l.shape;
  let h = F("fill", "색", { t: "col" }) + swatches("fill");
  if (["arrow2", "pass", "scribble", "ring", "xmark"].includes(k)) h += F("width", "두께");
  if (k === "arrow2" || k === "scribble") h += F("head", "화살촉");
  if (k === "arrow2" || k === "pass") h += F("dash.on", "점선", { t: "chk" });
  if (k === "arrow2" || k === "ring") h += F("core.on", "가운데 흰 선", { t: "chk" });
  if (k === "ring") h += F("back", "뒤쪽 반원 흐리게", { t: "chk" }) + F("fillA", "안쪽 칠");
  if (k === "spot") h += F("topW", "위쪽 폭") + F("fillA", "밝기");
  if (k === "marker") h += F("label", "원 안 글자", { t: "txt", ph: "1 · 공간" }) + F("labelColor", "글자색", { t: "col" });
  if (k === "scribble") h += `<button class="btn sm" data-act="reseed">✏️ 다시 그리기 (흔들림 바꾸기)</button>`;
  h += fx("sstroke", "바깥 테두리", F("stroke.color", "색", { t: "col" }) + F("stroke.width", "두께"));
  return h + `<div class="hint">${l.pts ? "선택하면 보이는 점(동그라미)을 끌어 모양을 바꿔요 · Shift 반듯하게 · " : ""}네온 느낌은 '외부 광선' 효과로 조절해요</div>`;
}
Object.assign(FM, { width: { min: 1, max: 120, unit: "px" }, head: { min: 0, max: 240, unit: "px" }, fillA: { min: 0, max: 100, mul: 100, unit: "%" }, topW: { min: 2, max: 100, mul: 100, unit: "%" },
  "grade.amt": { min: 0, max: 150, mul: 100, unit: "%" }, "grade.clarity": { min: 0, max: 100 }, "grade.vib": { min: -40, max: 140 }, "grade.temp": { min: -50, max: 50 }, "grade.sharpen": { min: 0, max: 100 } });
ACT.reseed = () => { const l = selL(); if (!l) return; commit("다시 그리기"); l.seed = Math.floor(Math.random() * 1e6); changed(); };
ACT.gradeAuto = () => {  // 그 장면의 자동 보정 값 다시 (분석 결과)
  const l = selL(); if (!l || l.type !== "image") return;
  const t = +(new URLSearchParams(String(l.src).split("?")[1] || "").get("t")), f = [...AI.frames, ...FRAMES].find(x => x.grade && Math.abs(x.t - t) < 0.05);
  commit("자동 보정"); l.grade = f ? Object.assign({}, f.grade, { on: true, amt: 1 }) : Object.assign({}, l.grade, { on: true, vib: 30, clarity: 35, sharpen: 25 }); changed();
  toast(f ? "이 장면의 자동 보정 값을 넣었어요" : "기본 보정을 넣었어요 (장면 분석이 없어서)");
};
function gradeFieldsHtml() {
  return F("grade.amt", "강도") + F("grade.clarity", "클래리티") + F("grade.vib", "자연 채도") + F("grade.temp", "색온도") + F("grade.sharpen", "선명하게")
    + `<div class="row2"><button class="btn sm" data-act="gradeAuto">이 장면 자동 값</button></div><div class="hint">레벨·밝기·색온도·채도를 장면마다 자동으로 맞춘 값이에요. 강도를 낮추면 원본에 가까워져요.</div>`;
}

/* ----- 스티커 패널 (이모지 + 배지) ----- */
const STKP = floatPanel("stkPanel", "😀 스티커 · 배지", 330);
let STICKERS = null;
async function toggleStk(on) {
  on = on ?? !STKP.classList.contains("show"); STKP.classList.toggle("show", on); if (!on) return;
  if (!STICKERS) { try { STICKERS = await (await fetch("/stickers/index.json")).json(); } catch (e) { STICKERS = []; } }
  const b = brandOf();
  const badges = [["풋살 노하우 전부 공개 ✓", "know"], ["1분 강좌", "min"], ["실전 전술", "tac"], [b.series || "시리즈 이름", "series"]].concat(b.logo ? [["로고", "logo"]] : []);
  STKP.querySelector(".bd").innerHTML = `<div class="stkgrid">${STICKERS.map(s => `<img src="/stickers/${esc(s.file)}" data-stk="${esc(s.file)}" title="${esc(s.label)} · ${esc(s.tags.join(", "))}" loading="lazy">`).join("")}</div>
    <h4 style="margin:10px 0 6px">배지</h4><div class="row2">${badges.map(([t, k]) => `<button class="btn sm" data-badge="${k}">${esc(t)}</button>`).join("")}</div>
    <div class="hint">눌러서 캔버스 가운데에 넣어요 · 이모지: Microsoft Fluent Emoji (MIT)</div>`;
  STKP.querySelectorAll("[data-stk]").forEach(im => (im.onclick = () => addSticker(im.dataset.stk)));
  STKP.querySelectorAll("[data-badge]").forEach(bt => (bt.onclick = () => addBadge(bt.dataset.badge)));
}
function addSticker(file) {
  if (!D) return; const s = Math.round(Math.min(W, H) * 0.24);
  addImage(`/stickers/${file}`, false, { name: "스티커", fit: "contain", w: s, h: s, x: (W - s) / 2, y: (H - s) / 2, rot: -6, shadow: { on: true, color: "#000000", blur: 10, dx: 0, dy: 6, opacity: 0.55 } });
}
function addBadge(kind) {
  if (!D) return; const b = brandOf(), c = b.colors, sz = Math.round(Math.min(W, H) * 0.08);  // 목록 크기에서도 7px 넘게
  if (kind === "logo") return addImage(b.logo, false, { name: "로고", fit: "contain", w: sz * 3, h: sz * 2, x: W - sz * 3.4, y: sz * 0.5 });
  const spec = { know: ["풋살 노하우 전부 공개 ✓", "#FFFFFF", c.box, [[11, 13, c.hl]]], min: ["1분 강좌", "#111111", c.hl, []], tac: ["실전 전술", "#FFFFFF", c.accent, []],
    series: [b.series || "시리즈 이름", "#D8D8D8", "#000000", []] }[kind];
  commit("배지 넣기");
  const l = L("text", { text: spec[0], name: "배지", font: "Pretendard Black", size: sz, fill: spec[1], strokes: [{ color: "#000000", width: 0 }, { color: "#FFFFFF", width: 0 }],
    shadow: { on: true, color: "#000000", blur: 12, dx: 0, dy: 4, opacity: 0.5 }, box: { on: true, color: spec[2], pad: Math.round(sz * 0.35), radius: Math.round(sz * 0.4) },
    runs: spec[3].map(([s, e, f]) => ({ s, e, fill: f })) });
  fitText(l); l.x = (W - l.w) / 2; l.y = H * 0.12; D.doc.layers.push(l); selIds = [l.id]; changed(); showTab("props");
}

/* ----- 브랜드 키트 (채널 로고·색 5개·기본 글꼴·시리즈 이름·로고 위치 — 새 추천에 반영) ----- */
const BRAND_ROLES = [["hl", "강조 글자 (노랑)"], ["hl2", "기본 글자 (흰색)"], ["accent", "포인트 (빨강)"], ["neon", "전술 네온"], ["box", "상자·띠"]];
async function openBrand() {
  const b = JSON.parse(JSON.stringify((await loadBrand()) || BRAND_DEF));
  const m = $("brandModal"), body = $("brandBody");
  const draw = () => {
    body.innerHTML = `<div class="bk"><div><h4>로고</h4><div class="bklogo">${b.logo ? `<img src="${esc(b.logo)}">` : `<span class="hint">없음</span>`}</div>
        <div class="row2"><button class="btn sm" id="bkLogo">로고 올리기</button>${b.logo ? `<button class="btn sm" id="bkLogoCut">배경 지우기</button><button class="btn sm" id="bkLogoDel">지우기</button>` : ""}</div>
        <div class="seg" id="bkPos">${[["tr", "오른쪽 위"], ["tl", "왼쪽 위"], ["off", "안 넣기"]].map(([v, t]) => `<button data-v="${v}" class="${b.logoPos === v ? "on" : ""}">${t}</button>`).join("")}</div></div>
      <div><h4>색</h4>${BRAND_ROLES.map(([k, t]) => `<div class="f"><label>${t}</label><input type="color" data-bc="${k}" value="${toHex(b.colors[k])}"><span class="hint">${toHex(b.colors[k]).toUpperCase()}</span></div>`).join("")}</div></div>
      <div class="f"><label>기본 글꼴</label><select class="s" id="bkFont">${FONT_OPTS.filter(([v]) => v !== "Dokdo").map(([v, t]) => `<option value="${v}" ${b.font === v ? "selected" : ""}>${t}</option>`).join("")}</select><span></span></div>
      <div class="f"><label>시리즈 이름</label><input class="s" id="bkSeries" maxlength="20" value="${esc(b.series || "")}"><span></span></div>
      <label class="hint" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="bkSeriesOn" ${b.seriesOn ? "checked" : ""}> 모든 추천에 시리즈 이름 넣기 (강좌 시리즈 템플릿은 늘 넣어요)</label>
      <label class="hint" style="display:flex;gap:6px;align-items:center;margin-top:4px"><input type="checkbox" id="bkApply" ${b.apply !== false ? "checked" : ""}> 새 추천에 적용</label>
      <label class="hint" style="display:flex;gap:6px;align-items:center;margin-top:4px"><input type="checkbox" id="bkAi" ${b.aiCopy !== false ? "checked" : ""}> 분석할 때 클로드로 문구 만들기·장면 고르기 (클로드가 로그인돼 있을 때 · 내 클로드 계정 사용량을 써요)</label>`;
    body.querySelectorAll("[data-bc]").forEach(i => (i.oninput = () => { b.colors[i.dataset.bc] = i.value.toUpperCase(); i.nextElementSibling.textContent = i.value.toUpperCase(); }));
    body.querySelectorAll("#bkPos button").forEach(x => (x.onclick = () => { b.logoPos = x.dataset.v; draw(); }));
    $("bkLogo").onclick = () => { const fi = document.createElement("input"); fi.type = "file"; fi.accept = "image/*"; fi.onchange = async () => {
      const f = fi.files[0]; if (!f) return; const data = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(f); });
      const j = await post("/api/thumb/upload", { data }).catch(() => ({}));  // 저장 실패(Windows 잠금·디스크 가득)도 알림 (그림 넣기와 같게)
      if (j.url) { b.logo = j.url; draw(); } else toast(j.error || "로고를 넣지 못했어요. 잠시 뒤 다시 해 주세요"); }; fi.click(); };
    if ($("bkLogoDel")) $("bkLogoDel").onclick = () => { b.logo = ""; draw(); };
    if ($("bkLogoCut")) $("bkLogoCut").onclick = async () => { const j = await post("/api/thumb/cut", { src: b.logo, kind: "hq" }); if (!j.ok) return toast(j.error || "지금은 할 수 없어요"); const r = await watchJob(j.jobId); if (r && r.cut) { b.logo = r.cut; draw(); } };
  };
  draw(); m.classList.add("show");
  $("brandSave").onclick = async () => {
    Object.assign(b, { font: $("bkFont").value, series: $("bkSeries").value.trim(), seriesOn: $("bkSeriesOn").checked, apply: $("bkApply").checked, aiCopy: $("bkAi").checked });
    const j = await post("/api/thumb/brand", { brand: b });
    if (!j.ok) return toast(j.error || "저장하지 못했어요");
    AI.brand = j.brand; m.classList.remove("show"); toast("브랜드 키트를 저장했어요 · 새 추천부터 반영돼요");
    if (AI.results.length && j.brand.apply !== false) aiRun(AI.seed);
  };
}
$("brandClose").onclick = () => $("brandModal").classList.remove("show");

/* ----- 검수 강화: 모바일 목록 미리보기(168px · 쇼츠 110px, 어두운·밝은 화면) · 작게 봤을 때 읽힌 글자 · AI 점수 · 클로드 평가 ----- */
function mobileCanvas(doc, w, dark) {  // 유튜브 목록 한 줄 모양
  const short = doc.h > doc.w, th = Math.round(w * doc.h / doc.w), pad = 8, cw = short ? w + pad * 2 : w + 170, ch = th + pad * 2;
  const c = newCanvas(cw, ch), g = c.getContext("2d");
  g.fillStyle = dark ? "#0F0F0F" : "#FFFFFF"; g.fillRect(0, 0, cw, ch);
  const t = newCanvas(w, th); renderDoc(t.getContext("2d"), doc, w / doc.w);
  g.save(); rrect(g, pad, pad, w, th, 6); g.clip(); g.drawImage(t, pad, pad); g.restore();
  if (!short) {
    g.fillStyle = "rgba(0,0,0,.8)"; rrect(g, pad + w - 34, pad + th - 17, 30, 13, 3); g.fill(); g.fillStyle = "#fff"; g.font = "bold 9px sans-serif"; g.fillText(mmss(INFO.duration || 0), pad + w - 31, pad + th - 7);
    g.fillStyle = dark ? "#F1F1F1" : "#0F0F0F"; g.font = "bold 12px 'Pretendard Bold', sans-serif"; g.fillText(cutText(niceName(NAME), 14), w + 18, pad + 18);
    g.fillStyle = dark ? "#AAAAAA" : "#606060"; g.font = "11px sans-serif"; g.fillText("풋살사관학교", w + 18, pad + 36); g.fillText("조회수 1.2만회", w + 18, pad + 52);
  }
  return c;
}
async function qaExtra() {
  const box = $("mobPrev"); if (!box || !D) return;
  const short = H > W, w = short ? 110 : 168;
  box.innerHTML = `<div class="hint" style="margin-bottom:6px">휴대폰 목록 크기 (${w}px) — 어두운·밝은 화면</div>`;
  const row = document.createElement("div"); row.style.cssText = "display:flex;gap:8px;flex-wrap:wrap"; box.appendChild(row);
  row.appendChild(mobileCanvas(D.doc, w, true)); row.appendChild(mobileCanvas(D.doc, w, false));
  const meta = { frame: (AI.frames.find(f => D.doc.layers.some(l => l.type === "image" && String(l.src).includes(`t=${f.t}`))) || { score: 1, persons: [] }), copy: { score: 0 }, copyMin: 0, copyMax: 1, maxFrame: 1 };
  try { const sc = scoreDoc(D.doc, meta); const p = document.createElement("div"); p.className = "hint"; p.style.marginTop = "6px"; p.innerHTML = `<b style="color:#ffd24d">AI 점수 ${Math.round(sc.score)}점</b>${sc.gates.length ? ` · 고칠 것: ${esc(sc.gates.join(" · "))}` : " · 큰 문제 없어요"} · 목록에서 제목 글자 높이 ${sc.hpx.toFixed(1)}px`; box.appendChild(p); } catch (e) { console.warn(e); }
  const o = document.createElement("div"); o.className = "hint"; o.id = "ocrRes"; o.textContent = "작게 봤을 때 읽힌 글자: 읽는 중…"; box.appendChild(o);
  const small = newCanvas(w, Math.round(w * H / W)); renderDoc(small.getContext("2d"), D.doc, w / W);
  try {
    const j = await post("/api/thumb/ocr", { data: small.toDataURL("image/png") });
    o.textContent = j.ok ? `작게 봤을 때 읽힌 글자: ${j.lines.map(x => x.text).join(" / ") || "(못 읽었어요 — 글자를 더 키워 보세요)"}` : `작게 봤을 때 읽힌 글자: ${j.error}`;
  } catch (e) { o.textContent = "작게 봤을 때 읽힌 글자: 확인하지 못했어요"; }
}
async function judgeAI() {
  if (!D) return; const short = H > W, w = short ? 110 : 168;
  const s = newCanvas(w, Math.round(w * H / W)); renderDoc(s.getContext("2d"), D.doc, w / W);
  const f = newCanvas(W, H); renderDoc(f.getContext("2d"), D.doc, 1);
  const j = await post("/api/thumb/judge", { name: NAME, small: s.toDataURL("image/jpeg", 0.92), full: f.toDataURL("image/jpeg", 0.85) });
  if (!j.ok) return toast(j.error || "지금은 할 수 없어요");
  const r = await watchJob(j.jobId); const out = $("judgeRes"); if (!out) return;
  if (!r || !r.ok) { out.textContent = (r && r.error) || "평가를 받지 못했어요"; return; }
  out.innerHTML = `<b>클로드 평가</b> · 읽기 ${r.readability} · 대비 ${r.contrast} · 위계 ${r.hierarchy} · 끌림 ${r.appeal} · 프로 수준 ${r.pro_level} (10점 만점)<br>${esc(r.summary || "")}<ul>${(r.fixes || []).map(x => `<li>${esc(x)}</li>`).join("")}</ul>`;
}

/* ----- A/B 묶음: 고른 추천 카드 → '<영상>_썸네일_A/B/C.jpg' + '모바일 비교.jpg' ----- */
async function abSave() {
  const picks = [...AI.ab].sort((a, b) => a - b).map(i => AI.results[i]).filter(Boolean);
  if (picks.length < 2) return toast("A/B 로 비교할 카드를 2개 이상 골라 주세요");
  if (picks.length > 6) return toast("6개까지만 담을 수 있어요");
  if (gone) return markGone();  // 옛 이름으로 열린 창 (서버도 404 gone)
  setAIStat("A/B 묶음 만드는 중…");
  const items = [];
  for (const x of picks) {
    await Promise.all(x.doc.layers.filter(l => l.type === "image" && l.src).map(l => imgReady(l.src)));
    const c = newCanvas(x.doc.w, x.doc.h); renderDoc(c.getContext("2d"), x.doc, 1);
    let q = 0.93, d; do { d = c.toDataURL("image/jpeg", q); q -= 0.07; } while (d.length * 0.75 > 1.95e6 && q > 0.5);
    items.push(d);
  }
  const short = picks[0].doc.h > picks[0].doc.w, w = short ? 110 : 168, rows = picks.map(x => mobileCanvas(x.doc, w, true));
  const mw = Math.max(...rows.map(r => r.width)), mc = newCanvas(mw * picks.length + 20 * (picks.length + 1), rows[0].height + 46), g = mc.getContext("2d");
  g.fillStyle = "#0F0F0F"; g.fillRect(0, 0, mc.width, mc.height);
  rows.forEach((r, i) => { const x = 20 + i * (mw + 20); g.fillStyle = "#FFD24D"; g.font = "bold 16px sans-serif"; g.fillText(AB_TAGS[i], x, 24); g.drawImage(r, x, 34); });
  const j = await post("/api/thumb/ab", { name: NAME, items, mobile: mc.toDataURL("image/jpeg", 0.9) });
  if (j.gone) { setAIStat(""); return markGone(j.error); }
  if (!j.ok) { setAIStat(""); return toast(j.error || "저장하지 못했어요"); }
  setAIStat(`A/B 저장: ${j.files.join(", ")}`); toast(`A/B 묶음 ${picks.length}장 + 모바일 비교를 저장했어요`); post("/api/open", { which: "out" });
  return j.files;
}
const AB_TAGS = "ABCDEF";
$("ovMenu").onclick = e => openTacMenu(e.currentTarget);
$("stkBtn").onclick = () => toggleStk();
$("brandBtn").onclick = openBrand;
