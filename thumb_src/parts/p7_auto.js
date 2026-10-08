
/* ---------- 누끼 ---------- */
async function doCut(l0, kind) {
  const doc = D, id = l0.id, src = l0.orig || l0.src;
  const j = await post("/api/thumb/cut", { src, kind });
  if (!j.ok) return toast(j.error || "지금은 할 수 없어요");
  const res = await watchJob(j.jobId); if (!res) return;
  if (doc !== D) { const i = DOCS.designs.indexOf(doc); if (i >= 0) openDesign(i); }  // 기다리는 사이 다른 디자인을 열었으면 원래 디자인으로
  const l = D.doc.layers.find(x => x.id === id);  // 되돌리기로 객체가 바뀌었어도 id로 다시 찾기
  if (!l) return toast("누끼 딸 레이어가 없어졌어요 (되돌리기·삭제)");
  commitXform(); commit("누끼 따기");
  const cutL = L("image", { src: res.cut, orig: src, name: "누끼", fit: l.fit, fx: l.fx, fy: l.fy, x: l.x, y: l.y, w: l.w, h: l.h, rot: l.rot, skew: l.skew, flipX: l.flipX, slant: l.slant, cropT: l.cropT, cropB: l.cropB, cropL: l.cropL, cropR: l.cropR,
    outline: { on: true, color: "#FFFFFF", width: Math.round(12 * W / 1280) }, shadow: { on: true, color: "#000000", blur: 24, dx: 0, dy: 10, opacity: 0.85 } });
  D.doc.layers.splice(D.doc.layers.indexOf(l) + 1, 0, cutL);
  Object.assign(l, { bright: Math.min(l.bright, 72), blur: Math.max(l.blur, 3) });
  selIds = [cutL.id]; changed(); toast("누끼를 땄어요 · 배경은 살짝 어둡고 흐리게 했어요 (Ctrl+Z로 되돌리기)");
}
let LAST_FAIL = null;  // 마지막으로 기다린 작업의 실패 안내 (trouble.explain 의 쉬운 한 줄 · AI 추천 썸네일 분석 줄에 그대로 보여 줌)
function watchJob(id) {  // id: 시작 응답의 jobId — 그 작업의 결과만 (휴대폰이 바로 다음 작업을 시켜도 안 섞임)
  $("busy").classList.add("show"); LAST_FAIL = null;
  return new Promise(resolve => {
    const iv = setInterval(async () => {
      let s; try { s = await (await fetch("/api/state?since=999999" + (id ? "&job=" + id : ""))).json(); } catch (e) { return; }
      const pr = s.progress || {}, d = id ? s.done : (s.job ? null : s);
      if (!d) { $("busyTxt").textContent = pr.detail || s.job || "작업 중"; $("busyBar").style.width = (pr.pct || 5) + "%"; return; }
      clearInterval(iv); $("busy").classList.remove("show"); if (d.error) { LAST_FAIL = d.fail || { msg: d.error }; toast("실패 · " + d.error.slice(0, 80)); resolve(null); } else resolve(d.result);
    }, 700);
  });
}

/* ---------- 영상 장면 ---------- */
const frameSrc = t => `/frame?name=${encodeURIComponent(NAME)}&t=${t}`;
let SUBCROP = true;  // 영상에 박힌 옛 자막 가리기 (장면 아래쪽 잘라냄)
const subCrop = () => (SUBCROP ? { cropB: 0.26 } : {});
let STRIP_SORT = "time";  // 영상 장면 줄: 시간 순 / 표정 좋은 순 (FRAMES 는 서버가 고른 좋은 순 그대로 — 자동 제작이 앞에서부터 씀)
const exprOf = f => (f.emo ? Math.min(1, (f.emo.happiness || 0) + (f.emo.surprise || 0)) : -1);
function faceBadge(f) {  // 😆 웃음 / 😮 놀람 + 얼굴 크기 (화면 높이 대비) — '표정 좋은 순'과 같은 기준(exprOf)
  if (!f.face) return "";
  const h = (f.emo && f.emo.happiness) || 0, s = (f.emo && f.emo.surprise) || 0, pct = Math.round(f.face * 100);
  const tag = exprOf(f) < 0.4 ? "얼굴" : h >= s ? "😆 웃음" : "😮 놀람";
  return `<b title="얼굴 크기: 화면 높이의 ${pct}%">${tag} ${pct}%</b>`;
}
function renderStrip() {
  const list = [...FRAMES].sort(STRIP_SORT === "expr" ? (a, b) => exprOf(b) - exprOf(a) || (b.face || 0) - (a.face || 0) || (b.score || 0) - (a.score || 0) : (a, b) => a.t - b.t);
  $("strip").innerHTML = list.map(f => `<div class="fr" data-t="${f.t}"><img src="${frameSrc(f.t)}" loading="lazy"><span>${mmss(f.t)}</span>${faceBadge(f)}${f.kind ? `<i>${frameBadges(f)}</i>` : ""}</div>`).join("") || `<span class="hint" style="padding:10px">장면이 없어요</span>`;
  document.querySelectorAll(".fr").forEach(el => (el.onclick = e => {
    const src = frameSrc(el.dataset.t);
    if (e.shiftKey) return addImage(src, false, W > H ? {} : { w: W, h: W * 9 / 16, x: 0, y: (H - W * 9 / 16) / 2 });
    const bg = D.doc.layers.find(l => l.type === "image" && l.name === "배경");
    if (bg) { commit("배경 장면"); bg.src = src; delete bg._edit; changed(); toast("배경 장면을 바꿨어요"); } else addImage(src, true);
  }));
}
$("scrub").oninput = e => { $("scrubT").textContent = mmss(e.target.value * INFO.duration); };
$("grabBtn").onclick = () => { const t = Math.round($("scrub").value * INFO.duration * 10) / 10; FRAMES.push({ t }); renderStrip(); toast(`${mmss(t)} 장면을 추가했어요`); };
$("stripSort").querySelectorAll("button").forEach(b => (b.onclick = () => {
  STRIP_SORT = b.dataset.s; $("stripSort").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); renderStrip();
  if (STRIP_SORT === "expr" && FRAMES.length && !FRAMES.some(f => f.face)) toast("얼굴이 잘 보이는 장면을 찾지 못해서 점수 순으로 보여 드려요");
}));

/* ---------- 자동 제작 (롱폼 · 쇼츠 템플릿) ---------- */
function splitTitle(t) {
  t = t.trim(); if (t.includes("\n")) return t.split("\n").slice(0, 2);
  if (/ 꿀팁$/.test(t)) return [t.replace(/ 꿀팁$/, ""), "꿀팁 대방출"];
  if (/!$/.test(t) && t.length <= 12) return [t, "이것만 알면 끝"];
  const w = t.split(" "); if (w.length < 2) return [t, ""];
  let best = 1, diff = 1e9;
  for (let i = 1; i < w.length; i++) { const a = w.slice(0, i).join(" ").length, b = w.slice(i).join(" ").length, d = Math.abs(a - b); if (d < diff) { diff = d; best = i; } }
  return [w.slice(0, best).join(" "), w.slice(best).join(" ")];
}
function txt(text, o) { const l = L("text", Object.assign({ text }, o)); fitText(l); return l; }
function fitW(l, maxW) { if (l.w > maxW) { scaleTextStyle(l, maxW / l.w); fitText(l); } return l; }
function place(l, ax, ay, x, y) { l.x = x - l.w * ax; l.y = y - l.h * ay; return l; }
const NOSTROKE = [{ color: "#000000", width: 0 }, { color: "#FFFFFF", width: 0 }], HARD = (dx, c = "#000000") => ({ on: true, color: c, blur: 0, dx, dy: dx, opacity: 1 });
const CUTFX = w => ({ outline: { on: true, color: "#FFFFFF", width: w }, shadow: { on: true, color: "#000000", blur: 24, dx: 0, dy: 10, opacity: 0.85 } });
const LW = 1280, LH = 720, SW = 1080, SH = 1920, SB = SH * 0.775, SAFE_W = SW * 0.8;  // 쇼츠: 아래 제목·오른쪽 버튼에 안 가리게
const TPL = {
  long: {
    "큰 글씨 2줄": (f, a, b, cut) => {
      const ls = [L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 0, w: LW, h: LH, bright: 92, contrast: 112, sat: 115 }, subCrop())),
        L("shape", { name: "아래 어둡게", x: 0, y: LH * 0.42, w: LW, h: LH * 0.58, fill: "rgba(0,0,0,0)", fill2: "#000000", gradAngle: 90, opacity: 0.85 })];
      if (cut) ls.push(L("image", Object.assign({ name: "누끼", src: cut.cut, orig: cut.src, ...subCrop(), x: 0, y: 0, w: LW, h: LH }, CUTFX(12))));
      ls.push(place(fitW(txt(a, { size: 150, fill: "#FFE14D", align: "center", strokes: [{ color: "#111111", width: 26 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(9) }), LW - 60), 0.5, 1, LW / 2, LH - 150));
      if (b) ls.push(place(fitW(txt(b, { size: 120, fill: "#FFFFFF", align: "center", strokes: [{ color: "#111111", width: 22 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(8) }), LW - 60), 0.5, 1, LW / 2, LH - 28));
      return ls;
    },
    "박스 강조": (f, a, b) => [
      L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 0, w: LW, h: LH, contrast: 108 }, subCrop())),
      L("shape", { name: "사선 그림자", shape: "slant", x: 560, y: 380, w: 760, h: 340, fill: "#000000", slant: -0.55, opacity: 0.9 }),
      place(fitW(txt(a, { size: 118, fill: "#FFFFFF", strokes: NOSTROKE, shadow: { on: true, color: "#000000", blur: 20, dx: 0, dy: 8, opacity: 0.6 }, box: { on: true, color: "#1F4FE0", pad: 16, radius: 0 } }), 780), 0, 1, 470, LH - 175),
      ...(b ? [place(fitW(txt(b, { size: 118, fill: "#FFE14D", strokes: NOSTROKE, shadow: { on: false }, box: { on: true, color: "#000000", pad: 16, radius: 0 } }), 780), 0, 1, 470, LH - 40)] : []),
    ],
    "3분할 사선": (f, a, b, cut) => {
      const ls = [0, 1, 2].map(i => L("image", { name: `장면 ${i + 1}`, src: frameSrc(f[i % f.length]), x: i * 410 - 40, y: 0, w: 500, h: LH, slant: 0.22, sat: 120, contrast: 110 }));
      ls.push(L("shape", { name: "집중선", shape: "burst", x: 0, y: 0, w: LW, h: LH, fill: "#FFFFFF", opacity: 0.35, lines: 60, inner: 0.5 }));
      if (cut) ls.push(L("image", Object.assign({ name: "누끼", src: cut.cut, orig: cut.src, ...subCrop(), x: 240, y: 30, w: 800, h: 450, fit: "contain" }, CUTFX(14))));
      ls.push(place(fitW(txt(a, { size: 140, fill: "#FFE14D", align: "center", skew: -6, strokes: [{ color: "#111111", width: 30 }, { color: "#FFFFFF", width: 14 }], shadow: HARD(8) }), LW - 60), 0.5, 1, LW / 2, LH - 140));
      if (b) ls.push(place(fitW(txt(b, { size: 110, fill: "#FFFFFF", align: "center", skew: -6, strokes: [{ color: "#111111", width: 26 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(7) }), LW - 60), 0.5, 1, LW / 2, LH - 28));
      return ls;
    },
    "좌우 분할": (f, a, b) => [
      L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 380, y: 0, w: 900, h: LH }, subCrop())),
      L("shape", { name: "왼쪽 패널", shape: "slant", x: -40, y: 0, w: 660, h: LH, fill: "#0F7A3D", fill2: "#064d26", gradAngle: 90, slant: -0.18 }),
      place(fitW(txt(a + (b ? "\n" + b : ""), { size: 112, fill: "#FFFFFF", lh: 1.1, strokes: [{ color: "#062e17", width: 18 }, { color: "#FFFFFF", width: 0 }], shadow: { on: true, color: "#000000", blur: 12, dx: 0, dy: 6, opacity: 1 } }), 540), 0, 0.5, 56, LH / 2),
    ],
    "네온 강조": (f, a, b, cut) => {
      const ls = [L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 0, w: LW, h: LH, bright: 70, sat: 125, vignette: 70 }, subCrop()))];
      if (cut) ls.push(L("image", Object.assign({ name: "누끼", src: cut.cut, orig: cut.src, ...subCrop(), x: 0, y: 0, w: LW, h: LH }, CUTFX(10))));
      const t = txt(a, { size: 140, fill: "#FFFFFF", align: "center", strokes: [{ color: "#B26BFF", width: 10 }, { color: "#FFFFFF", width: 0 }], shadow: { on: false }, glow: { on: true, color: "#9B5CFF", size: 40, opacity: 1 } });
      ls.push(place(fitW(t, LW - 80), 0.5, 1, LW / 2, LH - (b ? 150 : 50)));
      if (b) ls.push(place(fitW(txt(b, { size: 100, fill: "#A6FF00", align: "center", strokes: [{ color: "#000000", width: 18 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(6) }), LW - 80), 0.5, 1, LW / 2, LH - 36));
      return ls;
    },
    "두 장면 겹치기": (f, a, b, cut) => {
      const ls = [L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 0, w: LW, h: LH, sat: 115, contrast: 108 }, subCrop())),
        L("image", Object.assign({ name: "장면 2", src: frameSrc(f[Math.min(3, f.length - 1)]), x: 520, y: 0, w: 760, h: LH, sat: 115, contrast: 108, fade: { on: true, angle: 180, start: 0.45, end: 0.95 } }, subCrop())),
        L("shape", { name: "아래 어둡게", x: 0, y: LH * 0.5, w: LW, h: LH * 0.5, fill: "rgba(0,0,0,0)", fill2: "#000000", gradAngle: 90, opacity: 0.8 })];
      if (cut) ls.push(L("image", Object.assign({ name: "누끼", src: cut.cut, orig: cut.src, ...subCrop(), x: -200, y: 0, w: LW, h: LH }, CUTFX(12))));
      ls.push(place(fitW(txt(a, { size: 140, fill: "#FFFFFF", align: "center", fill2: "#FFE14D", gradAngle: 90, strokes: [{ color: "#111111", width: 26 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(8) }), LW - 60), 0.5, 1, LW / 2, LH - (b ? 140 : 40)));
      if (b) ls.push(place(fitW(txt(b, { size: 104, fill: "#FF3B30", align: "center", strokes: [{ color: "#FFFFFF", width: 20 }, { color: "#FFFFFF", width: 0 }], outline: { on: true, color: "#111111", width: 6 }, shadow: HARD(6) }), LW - 60), 0.5, 1, LW / 2, LH - 30));
      return ls;
    },
    "미니멀 하단띠": (f, a, b) => [
      L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 0, w: LW, h: LH, bright: 95 }, subCrop())),
      L("shape", { name: "하단 띠", x: 0, y: LH - 235, w: LW, h: 235, fill: "#000000", opacity: 0.78 }),
      place(fitW(txt(a, { font: "Pretendard Black", size: 100, fill: "#FFFFFF", strokes: NOSTROKE, shadow: { on: false } }), LW - 100), 0, 0.5, 56, LH - 162),
      ...(b ? [place(fitW(txt(b, { font: "Pretendard Black", size: 80, fill: "#FFE14D", strokes: NOSTROKE, shadow: { on: false } }), LW - 100), 0, 0.5, 60, LH - 58)] : []),
    ],
  },
  short: {
    "쇼츠 · 위아래 큰 글씨": (f, a, b, cut) => {
      const ls = [L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 0, w: SW, h: SH, bright: 92, contrast: 112, sat: 118 }, subCrop())),
        L("shape", { name: "위 어둡게", x: 0, y: 0, w: SW, h: 760, fill: "#000000", fill2: "rgba(0,0,0,0)", gradAngle: 90, opacity: 0.85 }),
        L("shape", { name: "아래 어둡게", x: 0, y: SH - 700, w: SW, h: 700, fill: "rgba(0,0,0,0)", fill2: "#000000", gradAngle: 90, opacity: 0.85 })];
      if (cut) ls.push(L("image", Object.assign({ name: "누끼", src: cut.cut, orig: cut.src, ...subCrop(), x: 0, y: 0, w: SW, h: SH }, CUTFX(14))));
      ls.push(place(fitW(txt(a, { size: 170, fill: "#FFE14D", align: "center", strokes: [{ color: "#111111", width: 30 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(10) }), SAFE_W), 0.5, 0, SW / 2, 230));
      if (b) ls.push(place(fitW(txt(b, { size: 140, fill: "#FFFFFF", align: "center", strokes: [{ color: "#111111", width: 26 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(9) }), SAFE_W), 0.5, 1, SW / 2, SB));
      return ls;
    },
    "쇼츠 · 블러 배경 + 가운데 장면": (f, a, b) => [
      L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 0, w: SW, h: SH, blur: 22, bright: 55, sat: 120 }, subCrop())),
      L("image", { name: "가운데 장면", src: frameSrc(f[0]), x: 0, y: (SH - 608) / 2, w: SW, h: 608, contrast: 110, sat: 115, shadow: { on: true, color: "#000000", blur: 40, dx: 0, dy: 12, opacity: 0.9 } }),
      place(fitW(txt(a, { size: 150, fill: "#FFFFFF", align: "center", strokes: [{ color: "#000000", width: 26 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(8) }), SAFE_W), 0.5, 1, SW / 2, (SH - 608) / 2 - 60),
      ...(b ? [place(fitW(txt(b, { size: 130, fill: "#FFE14D", align: "center", strokes: [{ color: "#000000", width: 24 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(8) }), SAFE_W), 0.5, 0, SW / 2, (SH + 608) / 2 + 70)] : []),
    ],
    "쇼츠 · 상단 제목 띠": (f, a, b) => [
      L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 560, w: SW, h: SH - 560, contrast: 110, sat: 115 }, subCrop())),
      L("shape", { name: "상단 띠", x: 0, y: 0, w: SW, h: 600, fill: "#161616", fill2: "#000000", gradAngle: 90 }),
      place(fitW(txt(a, { size: 160, fill: "#FFE14D", align: "center", strokes: NOSTROKE, shadow: { on: false } }), SAFE_W), 0.5, 1, SW / 2, b ? 320 : 380),
      ...(b ? [place(fitW(txt(b, { size: 120, fill: "#FFFFFF", align: "center", strokes: NOSTROKE, shadow: { on: false } }), SAFE_W), 0.5, 0, SW / 2, 345)] : []),
    ],
    "쇼츠 · 인물 강조": (f, a, b, cut) => {
      const ls = [L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 0, w: SW, h: SH, blur: cut ? 10 : 0, bright: cut ? 55 : 80, sat: 120 }, subCrop())),
        L("shape", { name: "집중선", shape: "burst", x: 0, y: 0, w: SW, h: SH, fill: "#FFFFFF", opacity: 0.3, lines: 70, inner: 0.45 })];
      if (cut) ls.push(L("image", Object.assign({ name: "누끼", src: cut.cut, orig: cut.src, ...subCrop(), x: -540, y: 260, w: SW * 2, h: SH - 260, fit: "cover" }, CUTFX(16))));
      ls.push(place(fitW(txt(a, { size: 140, fill: "#FFFFFF", align: "center", strokes: NOSTROKE, shadow: { on: true, color: "#000000", blur: 18, dx: 0, dy: 8, opacity: 0.6 }, box: { on: true, color: "#E50914", pad: 20, radius: 8 } }), SAFE_W), 0.5, 0, SW / 2, 170));
      if (b) ls.push(place(fitW(txt(b, { size: 130, fill: "#FFE14D", align: "center", strokes: [{ color: "#000000", width: 26 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(8) }), SAFE_W), 0.5, 1, SW / 2, SB));
      return ls;
    },
    "쇼츠 · 네온": (f, a, b, cut) => {
      const ls = [L("image", Object.assign({ name: "배경", src: frameSrc(f[0]), x: 0, y: 0, w: SW, h: SH, bright: 60, sat: 130, vignette: 80 }, subCrop()))];
      if (cut) ls.push(L("image", Object.assign({ name: "누끼", src: cut.cut, orig: cut.src, ...subCrop(), x: 0, y: 0, w: SW, h: SH }, CUTFX(12))));
      ls.push(place(fitW(txt(a, { size: 160, fill: "#FFFFFF", align: "center", strokes: [{ color: "#B26BFF", width: 12 }, { color: "#FFFFFF", width: 0 }], shadow: { on: false }, glow: { on: true, color: "#9B5CFF", size: 46, opacity: 1 } }), SAFE_W), 0.5, 0, SW / 2, 240));
      if (b) ls.push(place(fitW(txt(b, { size: 130, fill: "#A6FF00", align: "center", strokes: [{ color: "#000000", width: 22 }, { color: "#FFFFFF", width: 0 }], shadow: HARD(8) }), SAFE_W), 0.5, 1, SW / 2, SB));
      return ls;
    },
  },
};
let CUT_AUTO = null, CANDS = [];
function titleOptions() {
  const out = [];
  for (const h of HOOKS) out.push(splitTitle(h));
  const kw = KEYWORDS.find(k => !["팁", "꿀팁", "중요", "잘하", "어떻게", "?"].includes(k));
  if (kw) out.push([kw.includes("국가대표") ? kw : kw + " 비법", kw.includes("국가대표") ? "제대로 알려드림" : "국가대표가 알려줌"]);
  out.push(splitTitle(cutText(niceName(NAME), 24)));
  return out.filter((x, i, a) => a.findIndex(y => y.join() === x.join()) === i);
}
function renderAuto() {
  const opts = titleOptions(), t0 = opts[0] || ["제목", ""], prev = { a: $("aL1") && $("aL1").value, b: $("aL2") && $("aL2").value };
  $("tab-auto").innerHTML = `<div class="pg"><h4>만들 형식</h4><div class="row2">${Object.entries(FMT).map(([k, f]) => `<button class="btn ${AUTO_FMT === k ? "pri" : ""}" data-act="autoFmt" data-v="${k}">${f.label}</button>`).join("")}</div>
      <div class="hint">쇼츠 썸네일은 쇼츠 화면(9:16, 1080×1920) 그대로 만들어요.</div></div>
    <div id="aiSec"></div>
    <details class="oldc" id="oldCands" open><summary>예전 템플릿 후보 <span class="hint">제목 두 줄을 직접 넣어 템플릿 14종으로</span></summary>
    <div class="pg"><h4>제목 문구</h4>
      <input class="s" id="aL1" placeholder="첫째 줄"><input class="s" id="aL2" placeholder="둘째 줄" style="margin-top:6px">
      <div class="row2" style="margin-top:8px">${opts.map((o, i) => `<button class="btn sm" data-ti="${i}">${esc(cutText(o.join(" "), 16))}</button>`).join("")}</div>
      <div class="row2"><button class="btn pri" id="aMake">후보 다시 만들기</button><button class="btn" id="aCutMake">${CUT_AUTO ? "누끼 넣은 후보 ✓" : "인물 누끼 넣어서 만들기"}</button></div>
      <label class="hint" style="display:flex;gap:6px;align-items:center;margin-bottom:6px"><input type="checkbox" id="aSub" ${SUBCROP ? "checked" : ""}> 영상에 박힌 옛 자막 가리기 (장면 아래쪽 잘라내기)</label>
      <div class="hint">영상에서 선명한 장면을 골라 템플릿으로 만들어요. 누르면 편집 화면으로 불러와요.</div></div>
    <div class="designs" id="designs"></div><div class="cands ${AUTO_FMT}" id="cands"></div></details>`;
  $("aL1").value = prev.a ?? t0[0]; $("aL2").value = prev.b ?? (t0[1] || "");
  document.querySelectorAll("[data-ti]").forEach(b => (b.onclick = () => { const o = opts[+b.dataset.ti]; $("aL1").value = o[0]; $("aL2").value = o[1] || ""; makeCands(); }));
  $("aMake").onclick = makeCands; $("aSub").onchange = e => { SUBCROP = e.target.checked; makeCands(); };
  $("aCutMake").onclick = async () => {
    if (!FRAMES.length) return toast("장면을 고르는 중이에요");
    const j = await post("/api/thumb/cut", { src: frameSrc(FRAMES[0].t), kind: "hq" });
    if (!j.ok) return toast(j.error || "지금은 할 수 없어요");
    const r = await watchJob(j.jobId); if (r) { CUT_AUTO = r; renderAuto(); }
  };
  renderDesigns(); makeCands(); renderAI();
  if (AI.results.length && AI.results[0].doc.h > AI.results[0].doc.w !== (AUTO_FMT === "short")) aiRun(AI.seed);  // 형식을 바꾸면 그 형식으로 다시 추천
}
function makeCands() {
  if (!FRAMES.length) { $("cands").innerHTML = `<div class="hint" style="padding:10px">장면을 고르는 중이에요…</div>`; return; }
  const a = $("aL1").value.trim() || "제목", b = $("aL2").value.trim(), fr = FRAMES.map(f => f.t), sets = [fr, [...fr.slice(2), ...fr.slice(0, 2)]];
  const f = FMT[AUTO_FMT]; CANDS = [];
  for (const [name, fn] of Object.entries(TPL[AUTO_FMT])) for (let k = 0; k < 2; k++) CANDS.push({ name, doc: { w: f.w, h: f.h, bg: "#000000", layers: fn(sets[k], a, b, CUT_AUTO) } });
  const cw = AUTO_FMT === "short" ? 180 : 320, ch = AUTO_FMT === "short" ? 320 : 180;
  $("cands").innerHTML = CANDS.map((c, i) => `<div class="cand" data-c="${i}"><canvas width="${cw}" height="${ch}"></canvas><div>${c.name}</div></div>`).join("");
  drawCands();
  document.querySelectorAll(".cand").forEach(el => (el.onclick = () => {
    const c = CANDS[+el.dataset.c];
    DOCS.designs.push({ id: nid(), name: `${c.name} ${DOCS.designs.length + 1}`, doc: clone(c.doc) });
    openDesign(DOCS.designs.length - 1); scheduleSave(); toast("후보를 불러왔어요 · 마음대로 고쳐 보세요");
  }));
}
/* ---------- 작은 미리보기(디자인 줄 · 자동 후보): 바뀐 것만, 화면을 먼저 보여 준 뒤 조금씩 나눠 그림 ---------- */
const THUMB_JOB = {};
function docSig(doc) {  // 그림이 달라지는 값 (문서 내용 + 이미지 준비 상태 + 글꼴)
  return JSON.stringify(doc, dropPriv) + "|" + doc.layers.map(l => (l.type === "image" && !l.hidden ? (l._edit ? "e" + (l._ev || 0) : img(l.src) ? 1 : 0) : "")).join("") + "|" + FONT_VER;
}
function paintThumbs(name, items) {  // items: [[캔버스, 문서], ...]
  const job = THUMB_JOB[name] = (THUMB_JOB[name] || 0) + 1;
  const step = () => {
    if (THUMB_JOB[name] !== job) return;  // 그사이 새로 요청됐으면 이번 건 그만
    const t0 = performance.now();
    while (items.length && performance.now() - t0 < 24) {
      const [cn, doc] = items.shift(); if (!cn.isConnected) continue;
      const k = docSig(doc); if (cn._k === k) continue;
      renderDoc(cn.getContext("2d"), doc, cn.width / (doc.w || 1280)); cn._k = k;
    }
    if (items.length) setTimeout(step, 0);
  };
  requestAnimationFrame(() => setTimeout(step, 0));  // 큰 화면이 먼저 보이게
}
function drawCands() { paintThumbs("cands", [...document.querySelectorAll(".cand")].map(el => [el.querySelector("canvas"), CANDS[+el.dataset.c] && CANDS[+el.dataset.c].doc]).filter(x => x[0] && x[1])); }
function renderDesigns() {
  const el = $("designs"); if (!el) return;
  const html = DOCS.designs.map((d, i) => { const sh = (d.doc.h || 720) > (d.doc.w || 1280); return `<div class="d ${i === cur ? "cur" : ""}" data-d="${i}" title="${esc(d.name)}" style="width:${sh ? 54 : 96}px"><canvas width="${sh ? 108 : 192}" height="${sh ? 192 : 108}"></canvas></div>`; }).join("")
    + `<div style="display:flex;flex-direction:column;gap:4px;flex:none"><button class="btn sm" data-new="long">＋ 롱폼 빈 캔버스</button><button class="btn sm" data-new="short">＋ 쇼츠 빈 캔버스</button></div>`;
  if (el._h !== html) {  // 줄 모양이 그대로면 칸은 두고 그림만 (저장할 때마다 전부 다시 그리지 않게)
    el.innerHTML = html; el._h = html;
    el.querySelectorAll("[data-d]").forEach(x => (x.onclick = () => openDesign(+x.dataset.d)));
    el.querySelectorAll("[data-new]").forEach(b => (b.onclick = () => {
      const f = FMT[b.dataset.new]; DOCS.designs.push({ id: nid(), name: `${f.label} ${DOCS.designs.length + 1}`, doc: { w: f.w, h: f.h, bg: "#111111", layers: [] } });
      openDesign(DOCS.designs.length - 1); scheduleSave();
    }));
  }
  paintThumbs("designs", [...el.querySelectorAll("[data-d]")].map(x => [x.querySelector("canvas"), DOCS.designs[+x.dataset.d] && DOCS.designs[+x.dataset.d].doc]).filter(x => x[0] && x[1]));
}
function openDesign(i) {
  if (D && i === cur && D === DOCS.designs[i]) return;
  if (editing) endEdit(); xform = null; liveBefore = null;
  cur = i; D = DOCS.designs[i]; D.doc.w = D.doc.w || 1280; D.doc.h = D.doc.h || 720; D.doc.layers.forEach(normLayer);
  W = D.doc.w; H = D.doc.h; selIds = []; undoStack = []; redoStack = []; histNames = []; textSel = null;
  fitView(); refreshUI(); renderDesigns();
}
function showTab(t) { document.querySelectorAll(".ph .tab").forEach(x => x.classList.toggle("on", x.dataset.tab === t)); $("tab-auto").style.display = t === "auto" ? "" : "none"; $("tab-props").style.display = t === "props" ? "" : "none"; }
document.querySelectorAll(".ph .tab").forEach(b => (b.onclick = () => showTab(b.dataset.tab)));

/* ---------- 내보내기 ---------- */
const PNG_LIMIT = 2 * 1024 * 1024;  // 썸네일 용량 한도 (바이트) — 7단계 썸네일 확인(upload.THUMB_MAX)·스튜디오 직접 올리기와 같은 값
async function exportImg() {
  if (!D) return; if (editing) endEdit();
  if (gone) return markGone();  // 옛 이름으로 열린 창: 완성본 폴더에 옛 이름 그림을 만들지 않음 (서버도 404 gone)
  const pend = D.doc.layers.filter(l => l.type === "image" && !l.hidden && !l._edit && l.src);
  const ok = await Promise.all(pend.map(l => imgReady(l.src)));
  if (ok.some(x => !x) && !confirm("불러오지 못한 그림이 있어요. 그래도 저장할까요? (회색 상자로 나와요)")) return;
  const c = newCanvas(W, H); renderDoc(c.getContext("2d"), D.doc, 1);
  let fmt = $("exFmt").value, data;
  const jpg = () => { let q = 0.93, d; do { d = c.toDataURL("image/jpeg", q); q -= 0.07; } while (d.length * 0.75 > 1.95e6 && q > 0.5); return d; };
  if (fmt === "png") {
    data = c.toDataURL("image/png");
    // 7단계 썸네일 확인·스튜디오 직접 올리기는 2MB 까지 (판정 A2: 쇼츠 PNG 는 2MB 를 넘는데 알려 주지 않았음 · 바로 올리기는 50MB · I-062)
    if (data.length * 0.75 > PNG_LIMIT && confirm(`PNG 그림이 ${(data.length * 0.75 / 1048576).toFixed(1)}MB 라 7단계 썸네일 확인·유튜브 스튜디오에 직접 올릴 때 한도(2MB)를 넘어요.\nJPG(화질 거의 같음)로 바꿔 저장할까요? [취소]를 누르면 PNG 그대로 저장해요.`)) { fmt = "jpg"; data = jpg(); }
  } else data = jpg();
  const j = await post("/api/thumb/export", { name: NAME, data, fmt, label: `${D.name}${H > W ? "_쇼츠" : ""}` });
  if (j.gone) return markGone(j.error);
  if (j.ok) { toast(data.length * 0.75 > PNG_LIMIT ? `저장했어요 · ${j.file} · 2MB 가 넘어 7단계 썸네일 확인에 걸려요 (JPG 로 저장하면 돼요)` : `저장했어요 · ${j.file}`); post("/api/open", { which: "out" }); } else toast(j.error || "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요");
}
$("exportBtn").onclick = exportImg;

/* ---------- 썸네일 검수 ---------- */
const lum = hex => { const v = [1, 3, 5].map(i => parseInt(toHex(hex).slice(i, i + 2), 16) / 255).map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
function regionLum(doc, l) {  // 그 글자 레이어를 빼고 그린 배경의 평균 밝기
  const k = 0.25, c = newCanvas(W * k, H * k), g = c.getContext("2d");
  renderDoc(g, Object.assign({}, doc, { layers: doc.layers.filter(x => x !== l && !(x.type === "text" && x.gid && x.gid === l.gid)) }), k);
  const b = bbox([l]), x = clamp(Math.floor(b.x * k), 0, c.width - 1), y = clamp(Math.floor(b.y * k), 0, c.height - 1);
  const w = clamp(Math.ceil(b.w * k), 1, c.width - x), hh = clamp(Math.ceil(b.h * k), 1, c.height - y);
  const d = g.getImageData(x, y, w, hh).data; let s = 0, n = 0;
  for (let i = 0; i < d.length; i += 16) { s += lum("#" + [d[i], d[i + 1], d[i + 2]].map(v => v.toString(16).padStart(2, "0")).join("")); n++; }
  return n ? s / n : 0;
}
function runQA() {
  if (!D) return; if (editing) endEdit();
  const items = [], add = (lv, title, msg, l) => items.push({ lv, title, msg, id: l && l.id });
  const texts = D.doc.layers.filter(l => l.type === "text" && !l.hidden), short = H > W, smallW = short ? 110 : 168;
  const want = short ? FMT.short : FMT.long;
  if (W === want.w && H === want.h) add("ok", "크기", `${W}×${H} — ${short ? "쇼츠" : "유튜브 썸네일"} 권장 크기예요.`);
  else add("bad", "크기", `${W}×${H}예요. ${want.w}×${want.h}로 맞추는 게 좋아요.`);
  if (!texts.length) add("warn", "글자 없음", "제목 글자가 없어요. 짧고 큰 글자 한두 줄이 클릭을 부르는 편이에요.");
  const chars = texts.reduce((a, l) => a + l.text.replace(/\s/g, "").length, 0);
  if (chars > 26) add("warn", "글자가 많아요", `글자 ${chars}자 — 20자 안쪽으로 줄이면 한눈에 읽혀요.`);
  else if (texts.length) add("ok", "글자 수", `글자 ${chars}자 — 적당해요.`);
  for (const l of texts) {
    const m = textLayout(ctx, l), sy = l.h / m.natH, spans = m.lines.flatMap(ln => ln.spans).filter(sp => sp.t.trim());
    const minSize = (spans.length ? Math.min(...spans.map(sp => sp.st.size)) : l.size) * sy;
    const px = minSize * smallW / W, nm = `'${cutText(lname(l), 14)}'`;
    if (px < 7) add("bad", "작게 보면 안 읽혀요", `${nm} — 작은 목록(${smallW}px)에서 글자 높이가 ${px.toFixed(1)}px예요. 더 키우세요.`, l);
    else if (px < 10) add("warn", "작게 보면 읽기 힘들어요", `${nm} — 작은 목록에서 ${px.toFixed(1)}px. 조금 더 키우면 좋아요.`, l);
    const layerOk = l.box.on || (l.outline.on && l.outline.width >= 4) || (l.glow.on && l.glow.size >= 20);
    const weak = layerOk ? [] : spans.filter(sp => Math.max(sp.st.s1w, sp.st.s2w) < sp.st.size * 0.06);  // 테두리가 약한 글자들
    if (weak.length) {
      const bg = regionLum(D.doc, l), fl = weak.map(sp => lum(sp.st.fill)).reduce((a, v) => (Math.abs(v - bg) < Math.abs(a - bg) ? v : a)), ratio = (Math.max(bg, fl) + 0.05) / (Math.min(bg, fl) + 0.05);
      if (ratio < 3) add("bad", "배경과 구분이 안 돼요", `${nm} — 글자색과 뒤 배경의 대비가 ${ratio.toFixed(1)}:1이에요. 테두리·글자 뒤 상자·그림자를 넣거나 색을 바꾸세요.`, l);
      else if (ratio < 4.5) add("warn", "대비가 약해요", `${nm} — 대비 ${ratio.toFixed(1)}:1. 테두리를 넣으면 더 또렷해요.`, l);
    }
    const b = bbox([l]);
    if (b.x < -2 || b.y < -2 || b.x + b.w > W + 2 || b.y + b.h > H + 2) add("bad", "글자가 잘려요", `${nm} — 캔버스 밖으로 나갔어요.`, l);
    const zones = short ? [[0.86, 0.42, 0.14, 0.45, "좋아요·댓글 버튼"], [0, 0.8, 0.84, 0.2, "제목·채널 이름"]] : [[0.86, 0.86, 0.14, 0.14, "재생시간 표시"]];
    for (const [zx, zy, zw, zh, zn] of zones) {
      const ix = Math.max(0, Math.min(b.x + b.w, (zx + zw) * W) - Math.max(b.x, zx * W)), iy = Math.max(0, Math.min(b.y + b.h, (zy + zh) * H) - Math.max(b.y, zy * H));
      if (ix * iy > b.w * b.h * 0.15) add("warn", "가려지는 곳에 글자", `${nm} — 유튜브 화면의 '${zn}'에 가려질 수 있어요.`, l);
    }
  }
  const hidden = D.doc.layers.filter(l => l.hidden).length;
  if (hidden) add("warn", "숨긴 레이어", `숨긴 레이어 ${hidden}개는 저장 이미지에 안 나와요.`);
  const bad = items.filter(i => i.lv === "bad").length, warn = items.filter(i => i.lv === "warn").length;
  const score = Math.max(0, 100 - bad * 20 - warn * 7);
  $("qaBody").innerHTML = `<div class="score">${score}점 · ${bad ? `고칠 것 ${bad}개` : "큰 문제 없어요"}${warn ? ` · 확인할 것 ${warn}개` : ""}</div>`
    + items.sort((a, b) => ["bad", "warn", "ok"].indexOf(a.lv) - ["bad", "warn", "ok"].indexOf(b.lv)).map(i => `<div class="qi ${i.lv}" ${i.id ? `data-ql="${i.id}"` : ""}><span>${{ ok: "✅", warn: "⚠️", bad: "⛔" }[i.lv]}</span><span><b>${esc(i.title)}</b>${esc(i.msg)}</span></div>`).join("");
  $("qaBody").querySelectorAll("[data-ql]").forEach(e => (e.onclick = () => { $("qaModal").classList.remove("show"); select([e.dataset.ql]); }));
  $("qaModal").classList.add("show"); $("judgeRes").innerHTML = ""; qaExtra();
  return { score, bad, warn, items };
}
$("qaBtn").onclick = runQA; $("qaClose").onclick = () => $("qaModal").classList.remove("show"); $("judgeBtn").onclick = judgeAI;

/* ---------- 눈금자 · 안내선 (Ctrl+R / Ctrl+;) ---------- */
let showRulers = false, showGuidesU = true;
const guidesOf = () => (D.doc.guides = D.doc.guides || { v: [], h: [] });
function drawRulers() {
  if (!showRulers || !D) return;
  for (const [id, horiz] of [["rulH", true], ["rulV", false]]) {
    const c = $(id), len = horiz ? c.clientWidth : c.clientHeight; if (!len) continue;
    c.width = horiz ? len : 18; c.height = horiz ? 18 : len; const g = c.getContext("2d");
    g.fillStyle = "#1c1c1c"; g.fillRect(0, 0, c.width, c.height); g.strokeStyle = "#666"; g.fillStyle = "#8a8a8a"; g.font = "9px sans-serif";
    const off = (horiz ? PX : PY) - 18, step = [10, 25, 50, 100, 200, 500].find(s => s * Z >= 60) || 1000;
    const a0 = Math.floor(-off / Z / step) * step, a1 = (len - off) / Z;
    g.beginPath();
    for (let a = a0; a <= a1; a += step / 5) {
      const p = Math.round(off + a * Z) + 0.5, big = Math.abs(a / step - Math.round(a / step)) < 1e-6;
      if (horiz) { g.moveTo(p, big ? 0 : 12); g.lineTo(p, 18); if (big) g.fillText(Math.round(a), p + 2, 9); }
      else { g.moveTo(big ? 0 : 12, p); g.lineTo(18, p); if (big) { g.save(); g.translate(9, p + 2); g.rotate(-Math.PI / 2); g.fillText(Math.round(a), -24, 0); g.restore(); } }
    }
    g.stroke();
  }
}
function drawUserGuides() {
  $("ui").querySelectorAll(".uguide").forEach(e => e.remove()); if (!D || !showGuidesU) return; const gd = guidesOf();
  gd.v.forEach((x, i) => { const e = document.createElement("div"); e.className = "uguide v"; e.dataset.g = "v" + i; Object.assign(e.style, { left: x * Z + "px", top: -PY + "px", height: stage.clientHeight + "px" }); $("ui").appendChild(e); });
  gd.h.forEach((y, i) => { const e = document.createElement("div"); e.className = "uguide h"; e.dataset.g = "h" + i; Object.assign(e.style, { top: y * Z + "px", left: -PX + "px", width: stage.clientWidth + "px" }); $("ui").appendChild(e); });
}
function dragGuide(kind, idx, e) {
  const gd = guidesOf(), before = snapshot(); let i = idx;
  if (i < 0) { gd[kind].push(0); i = gd[kind].length - 1; showGuidesU = true; }
  const mv = ev => { const q = toCanvas(ev); gd[kind][i] = Math.round(kind === "v" ? q.x : q.y); drawUserGuides(); readout(ev, `${kind === "v" ? "X" : "Y"} ${gd[kind][i]}px`); };
  const up = ev => {
    removeEventListener("mousemove", mv); removeEventListener("mouseup", up); hideReadout();
    const r = stage.getBoundingClientRect();
    if ((kind === "v" ? ev.clientX - r.left : ev.clientY - r.top) < 18) gd[kind].splice(i, 1);  // 눈금자로 되돌리면 삭제
    if (before !== snapshot()) { pushUndo(before, "안내선"); scheduleSave(); } drawUserGuides();
  };
  mv(e); addEventListener("mousemove", mv); addEventListener("mouseup", up);
}
$("rulH").addEventListener("mousedown", e => { e.preventDefault(); e.stopPropagation(); dragGuide("h", -1, e); });
$("rulV").addEventListener("mousedown", e => { e.preventDefault(); e.stopPropagation(); dragGuide("v", -1, e); });
$("ui").addEventListener("mousedown", e => { const g = e.target.closest(".uguide"); if (!g || tool !== "move") return; e.stopPropagation(); dragGuide(g.dataset.g[0], +g.dataset.g.slice(1), e); });
function toggleRulers() { showRulers = !showRulers; stage.classList.toggle("rulers", showRulers); drawRulers(); toast(showRulers ? "눈금자 · 눈금자에서 캔버스로 끌면 안내선이 생겨요 (다시 눈금자로 끌면 지워져요)" : "눈금자 숨김"); }

/* ---------- 목록 미리보기 · 가려지는 영역 ---------- */
function snap(w) { const c = newCanvas(w, Math.round(w * H / W)); renderDoc(c.getContext("2d"), D.doc, w / W); return c; }
$("pvBtn").onclick = () => {
  if (!D) return; if (editing) endEdit();
  const title = esc(($("aL1") && $("aL1").value ? $("aL1").value + " " + ($("aL2").value || "") : niceName(NAME)).trim()), dur = mmss(INFO.duration || 0);
  const card = (w, lab, small) => `<div class="pvc" style="width:${w}px"><div class="lab">${lab}</div><div class="pvwrap" data-pv="${w}">${H > W ? "" : `<span class="dur">${dur}</span>`}</div>${small ? "" : `<div class="tt">${title}</div><div class="ch">풋살사관학교 · 조회수 1.2만회</div>`}</div>`;
  $("pvBody").innerHTML = H > W
    ? card(220, "쇼츠 탭 (큰 카드)") + card(160, "홈 쇼츠 줄") + card(110, "검색 · 작게", true)
    : card(360, "홈 화면 (PC)") + card(246, "검색 결과") + `<div class="pvrow">${card(168, "다음 동영상 (옆 목록)", true)}${card(120, "아주 작게", true)}</div>`;
  $("pvBody").querySelectorAll("[data-pv]").forEach(el => el.prepend(snap(+el.dataset.pv)));
  $("pvModal").classList.add("show");
};
$("pvClose").onclick = () => $("pvModal").classList.remove("show");
let showSafe = false;
function drawSafe() {
  $("ui").querySelectorAll(".safe").forEach(e => e.remove()); if (!showSafe || !D) return;
  const box = (x, y, w, h, t) => { const d = document.createElement("div"); d.className = "safe"; d.textContent = t; Object.assign(d.style, { left: x * W * Z + "px", top: y * H * Z + "px", width: w * W * Z + "px", height: h * H * Z + "px" }); $("ui").appendChild(d); };
  if (H > W) { box(0.86, 0.42, 0.14, 0.45, "좋아요·댓글 버튼"); box(0, 0.8, 0.84, 0.2, "제목·채널 이름"); box(0, 0, 1, 0.07, "위쪽 메뉴"); }
  else box(0.86, 0.86, 0.14, 0.14, "재생시간");
}
$("safeBtn").onclick = () => { showSafe = !showSafe; $("safeBtn").classList.toggle("on", showSafe); drawSafe(); toast(showSafe ? "빨간 빗금 = 유튜브 화면에서 가려지는 곳이에요. 중요한 글자·얼굴은 피하세요" : "가려지는 영역 숨김"); };
$("back").onclick = async () => {  // 저장이 끝난 뒤에만 나감 (실패하면 물어봄 · 예전: 실패하면 아무 반응 없음)
  clearTimeout(saveTimer); saveTimer = null;
  if (await saveNow() || confirm("디자인을 저장하지 못했어요. 그래도 나갈까요? (마지막으로 바꾼 내용이 사라질 수 있어요)")) location.href = "/";
};
new ResizeObserver(() => { if (!D) return; fitMode ? fitView() : applyView(); }).observe(stage);

/* ---------- 시작 ---------- */
(async () => {
  if (!NAME) return;
  $("pname").textContent = niceName(NAME) + " · 썸네일";
  // 글꼴을 받는 동안 문서·장면 목록도 같이 받음 (차례로 기다리지 않게)
  const openP = fetch("/api/thumb/open?name=" + encodeURIComponent(NAME)).then(r => r.json());
  const framesP = post("/api/thumb/frames", { name: NAME });
  openP.catch(() => {}); framesP.catch(() => {});  // 실패는 아래에서 기다릴 때 그대로 드러남
  await Promise.all(FONT_FILES.map(async ([, w, f]) => {
    try { const ff = new FontFace(w, `url(/fonts/${f})`); await ff.load(); document.fonts.add(ff); } catch (e) {}
  }));
  FONT_VER++;
  const j = await openP;
  // 처음 열 디자인의 그림·후보 장면을 첫 그림 전에 받기 시작 (글꼴과 겹치지 않게 글꼴 다음에) → 첫 그림을 그리는 동안 받아 옴
  try { for (const l of j.docs.designs[0].doc.layers) if (l.type === "image" && !l.hidden && l.src) img(l.src); } catch (e) {}
  framesP.then(r => { for (const f of (r.frames || []).slice(0, 6)) img(frameSrc(f.t)); }).catch(() => {});
  await new Promise(r => setTimeout(r, 0));  // 받기 요청이 실제로 나가게 한 번 쉼
  DOCS = j.docs; DOCS.designs.forEach(d => { d.doc.w = d.doc.w || 1280; d.doc.h = d.doc.h || 720; d.doc.layers.forEach(normLayer); }); HOOKS = j.hooks || []; KEYWORDS = j.keywords || []; if (j.info) INFO = j.info;
  if (INFO.width && INFO.height && INFO.height > INFO.width) AUTO_FMT = "short";
  if (!DOCS.designs.length) DOCS.designs.push({ id: nid(), name: "디자인 1", doc: { w: 1280, h: 720, bg: "#111111", layers: [] } });
  openDesign(0); renderAuto(); setTool("move");
  $("saved").textContent = "저장됨";
  for (let tries = 0; tries < 200; tries++) {
    const r = tries === 0 ? await framesP : await post("/api/thumb/frames", { name: NAME });
    if (r.frames) { FRAMES = r.frames; break; }
    if (r.ok) { const res = await watchJob(r.jobId); FRAMES = res || []; break; }
    if (tries === 0) toast("다른 작업이 끝나면 장면을 골라요");
    $("strip").innerHTML = `<span class="hint" style="padding:10px">다른 작업이 끝나기를 기다리는 중…</span>`;
    await new Promise(r2 => setTimeout(r2, 3000));
  }
  renderStrip(); makeCands();
  loadBrand(); post("/api/thumb/copy", { name: NAME }).then(c => { if (c.ok && !AI.copy.length) { AI.copy = c.items; AI.topics = c.topics; AI.ai = c.ai; renderAI(); } }).catch(() => {});
  const d0 = DOCS.designs[0];
  if (d0 && !d0.doc.layers.length && FRAMES.length) {
    if (cur === 0) { addImage(frameSrc(FRAMES[0].t), true); undoStack = []; redoStack = []; histNames = []; selIds = []; refreshUI(); }
    else { d0.doc.layers.unshift(L("image", { src: frameSrc(FRAMES[0].t), name: "배경", x: 0, y: 0, w: d0.doc.w, h: d0.doc.h, fit: "cover" })); renderDesigns(); scheduleSave(); }
  }
  showTab("auto");
})();
