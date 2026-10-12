// 풋살 스튜디오 · 밖에서 작업 심 (remote_ui.js · D-230~D-234)
// 세션이 있을 때만 /s/remote.js 로 내주고, 화면(ui·editor·thumb)의 head 첫 스크립트로 끼워 넣는다 → 화면 스크립트보다 먼저 돎.
// 하는 일: fetch·sendBeacon 감싸기(CSRF 머리글 · /api 45초 기다림 · 401 잠금 · 428 확인 · 끊김 안내) · 위쪽 '밖에서 작업' 줄 ·
//          내 기기에서 올리기(조각 · 이어 올리기) · 완성본 받기 · window.FS_REMOTE.
// 밖에서 온 글(파일 이름 등)은 늘 textContent 로만 넣는다.
(function () {
  "use strict";
  if (window.FS_REMOTE) return;
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute("content") || "" : ""; };
  var NEXT_RE = /^\/(editor|thumb)?(\?[A-Za-z0-9%._~&=+\-]{0,600})?$/;
  var OPEN_PATHS = { "/api/open": 1, "/api/upload/open": 1, "/api/youtube/open": 1 };
  var R = { csrf: meta("fs-csrf"), site: meta("fs-site"), device: meta("fs-device"), pc: "", locked: false, prefs: { fullRes: false },
    expiresAt: 0, chunk: 8 << 20, net: true, probing: false, ups: [], pending: [] };
  var origFetch = window.fetch.bind(window);
  var origBeacon = navigator.sendBeacon ? navigator.sendBeacon.bind(navigator) : null;
  var IOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function sameOrigin(u) { try { return new URL(u, location.href).origin === location.origin; } catch (e) { return false; } }
  function pathOf(u) { try { return new URL(u, location.href).pathname; } catch (e) { return ""; } }
  function el(tag, props) {
    var e = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === "text") e.textContent = String(v);
      else if (k === "class") e.className = v;
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : String(v));
    });
    for (var i = 2; i < arguments.length; i++) { var c = arguments[i]; if (c != null && c !== false) e.append(c); }
    return e;
  }
  function here() { var p = location.pathname + location.search; return NEXT_RE.test(p) ? p : "/"; }
  function homeUrl(resume) { return R.site + (resume ? "#resume=" + encodeURIComponent(resume) : ""); }
  function size(n) { return n >= 1 << 30 ? (n / (1 << 30)).toFixed(1) + "GB" : n >= 1 << 20 ? Math.round(n / (1 << 20)) + "MB" : Math.max(1, Math.round(n / 1024)) + "KB"; }
  function dur(s) { s = Math.max(0, Math.round(s)); return s >= 3600 ? Math.floor(s / 3600) + "시간 " + Math.floor(s % 3600 / 60) + "분" : s >= 60 ? Math.floor(s / 60) + "분 " + (s % 60) + "초" : s + "초"; }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function ready(fn) { if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn); else fn(); }

  // ---------- 알림 줄 ----------
  function toast(msg, link) {
    ready(function () {
      var t = document.getElementById("fsr-toast");
      if (!t) { t = el("div", { id: "fsr-toast", role: "status" }); document.body.append(t); }
      t.replaceChildren(el("span", { text: msg }), link ? el("a", { href: link.href, target: "_blank", rel: "noopener noreferrer", text: link.text, onclick: function () { t.classList.remove("show"); } }) : null);
      t.classList.add("show");
      clearTimeout(t._h);
      t._h = setTimeout(function () { t.classList.remove("show"); }, link ? 9000 : 4000);
    });
  }

  // ---------- 연결이 끝났을 때 (401) ----------
  var LOCK = {
    revoked: ["PC에서 연결을 끊었어요", "다시 쓰려면 원격 홈에서 [PC 스튜디오 열기]를 눌러 주세요"],
    studio_off: ["밖에서 작업이 꺼져 있어요", "PC의 '휴대폰으로 보기'에서 '밖에서 작업 허용'을 켜 주세요"],
    expired: ["연결이 끝났어요", "오래 쓰지 않아 연결이 끝났어요 · 다시 연결할게요"],
    none: ["다시 연결해 주세요", "PC와 연결이 끊겼어요 · 다시 연결할게요"]
  };
  function dirty() {
    try { if (typeof FS.isDirty === "function" && FS.isDirty()) return true; } catch (e) { /* 화면 쪽 오류는 무시 */ }
    return R.ups.some(function (u) { return u.state === "up" || u.state === "wait"; });
  }
  function lock(code, site) {
    if (R.locked) return;
    R.locked = true;
    if (site && !R.site) R.site = site;
    var t = LOCK[code] || LOCK.none;
    var auto = (code === "none" || code === "expired") && !dirty();
    ready(function () {
      var box = el("div", { class: "fsr-card" }, el("h2", { text: t[0] }),
        el("p", { text: !auto && (code === "none" || code === "expired") ? "저장하지 않은 내용이나 올리던 파일이 있어요 · 준비되면 [다시 연결]을 눌러 주세요" : t[1] }),
        el("div", { class: "fsr-row" },
          code === "revoked" || code === "studio_off" ? el("a", { class: "fsr-btn pri", href: homeUrl(""), text: "원격 홈으로" })
            : el("a", { class: "fsr-btn pri", href: homeUrl(here()), text: "다시 연결" })));
      document.body.append(el("div", { id: "fsr-lock", role: "alertdialog", "aria-modal": "true" }, box));
      if (auto) setTimeout(function () { location.replace(homeUrl(here())); }, 1200);
    });
  }

  // ---------- 끊김 (인터넷·터널) ----------
  function netDown() {
    if (R.probing || R.locked) return;
    R.probing = true;
    var bar = document.getElementById("fsr-net");
    ready(function () {
      if (!bar) { bar = el("div", { id: "fsr-net" }); document.body.append(bar); }
      bar.replaceChildren(el("span", { text: "PC와 연결이 잠시 끊겼어요 · 다시 잇는 중…" }));
      bar.classList.add("show");
    });
    var start = Date.now();
    (async function probe() {
      while (Date.now() - start < 30000) {
        await sleep(3000);
        try {
          var r = await origFetch("/s/session", { credentials: "same-origin", cache: "no-store" });
          if (r.status === 200) { R.probing = false; if (bar) bar.classList.remove("show"); toast("PC와 다시 연결됐어요"); return; }
          if (r.status === 401 || r.status === 403) { var j = {}; try { j = await r.json(); } catch (e) { /* */ } R.probing = false; if (bar) bar.classList.remove("show"); return lock(j.code || "none", j.site); }
        } catch (e) { /* 아직 끊김 */ }
      }
      R.probing = false;
      if (bar) bar.replaceChildren(el("span", { text: "PC와 연결되지 않아요 · PC가 켜져 있는지 확인한 뒤" }),
        el("a", { class: "fsr-btn", href: homeUrl(here()), text: "다시 연결" }),
        el("button", { class: "fsr-btn", type: "button", text: "닫기", onclick: function () { bar.classList.remove("show"); } }));
    })();
  }

  // ---------- fetch 감싸기 ----------
  function isCfHtml(r) {
    return (r.status >= 520 && r.status <= 530 || r.status === 502 || r.status === 503 || r.status === 504) &&
      (r.headers.get("Content-Type") || "").indexOf("text/html") === 0;
  }
  function replayable(b) { return b == null || typeof b === "string" || b instanceof Blob || b instanceof ArrayBuffer || ArrayBuffer.isView(b) || b instanceof URLSearchParams || b instanceof FormData; }
  async function handleOpen(r) {
    try {
      var j = await r.clone().json();
      if (j.remote) FS.openFolder(j.remote === "outputs" ? "out" : j.remote === "upload" ? "videos" : "log", true);
      if (j.openUrl) openWin(j.openUrl);
      if (j.download) download(j.download);
    } catch (e) { /* 글이 아님 */ }
  }
  function openWin(url) {
    var w = null;
    try { w = window.open(url, "_blank"); if (w) w.opener = null; } catch (e) { w = null; }
    if (!w) toast("새 창이 막혔어요 · 여기를 눌러 열어 주세요", { href: url, text: "열기" });
  }
  async function fsFetch(input, init) {
    init = init || {};
    var url = typeof input === "string" ? input : input instanceof URL ? input.href : input && input.url;
    if (!url || !sameOrigin(url)) return origFetch(input, init);
    var isReq = typeof Request !== "undefined" && input instanceof Request;
    var method = String(init.method || (isReq ? input.method : "GET")).toUpperCase();
    var p = pathOf(url);
    var headers = new Headers(init.headers || (isReq ? input.headers : undefined));
    if (method !== "GET" && method !== "HEAD") headers.set("X-FS-CSRF", R.csrf);
    var timer = null, signal = init.signal;
    if (p.indexOf("/api/") === 0 && !signal && p !== "/api/edit/file" && p !== "/api/edit/upload") {
      var ac = new AbortController();
      timer = setTimeout(function () { ac.abort(); }, 45000);  // 터널 너머라 끝없이 기다리지 않게 (Cloudflare 는 100초에 끊음)
      signal = ac.signal;
    }
    var opts = Object.assign({}, init, { headers: headers, credentials: "same-origin" });
    if (signal) opts.signal = signal;
    var r;
    try { r = await origFetch(isReq ? new Request(input, opts) : url, isReq ? undefined : opts); }
    catch (e) { if (!(e && e.name === "AbortError" && init.signal && init.signal.aborted)) netDown(); throw e; }
    finally { if (timer) clearTimeout(timer); }
    if (r.status === 401) {
      var j401 = {};
      try { j401 = await r.clone().json(); } catch (e) { /* */ }
      lock(j401.code || "none", j401.site);
      return r;
    }
    if (r.status === 403 && method !== "GET" && replayable(init.body) && !opts._fsRetry) {
      var j403 = {};
      try { j403 = await r.clone().json(); } catch (e) { /* */ }
      if (j403.code === "csrf" && await refreshSession()) {  // 다른 탭이 새로 연결해 CSRF 가 바뀜 → 한 번만 다시
        headers.set("X-FS-CSRF", R.csrf);
        return origFetch(url, Object.assign(opts, { headers: headers, _fsRetry: true }));
      }
      if (j403.code === "studio_off") lock("studio_off");
    }
    if (r.status === 428 && replayable(init.body)) {
      var jc = {};
      try { jc = await r.clone().json(); } catch (e) { /* */ }
      if (jc.confirm && jc.confirm.token) {
        if (!window.confirm(jc.confirm.text || "계속할까요?")) {
          return new Response(JSON.stringify({ ok: false, error: "취소했어요" }), { status: 409, headers: { "Content-Type": "application/json" } });
        }
        headers.set("X-FS-Confirm", jc.confirm.token);
        return fsFetch(url, Object.assign({}, init, { headers: headers }));
      }
    }
    if (isCfHtml(r)) netDown();
    if (OPEN_PATHS[p] && r.ok) handleOpen(r);
    return r;
  }
  window.fetch = fsFetch;
  if (origBeacon) {
    navigator.sendBeacon = function (url, data) {  // 페이지를 떠날 때 저장 — 머리글을 못 붙이는 beacon 대신 keepalive fetch (CSRF)
      if (!sameOrigin(url)) return origBeacon(url, data);
      try {
        origFetch(url, { method: "POST", keepalive: true, credentials: "same-origin", body: data,
          headers: { "Content-Type": "application/json", "X-FS-CSRF": R.csrf } }).catch(function () { /* 떠나는 중 */ });
        return true;
      } catch (e) { return false; }
    };
  }
  async function refreshSession() {
    try {
      var r = await origFetch("/s/session", { credentials: "same-origin", cache: "no-store" });
      if (!r.ok) { if (r.status === 401) { var j = {}; try { j = await r.json(); } catch (e) { /* */ } lock(j.code || "none", j.site); } return false; }
      var s = await r.json();
      R.csrf = s.csrf; R.site = s.site || R.site; R.device = s.device || R.device; R.pc = s.pc || ""; R.prefs = s.prefs || R.prefs;
      R.expiresAt = s.expiresAt || 0; R.chunk = s.chunk || R.chunk;
      return true;
    } catch (e) { return false; }
  }

  // ---------- 위쪽 줄 ----------
  function bar() {
    var b = el("div", { id: "fsr-bar", role: "region", "aria-label": "밖에서 작업" });
    var name = el("b", { id: "fsr-pc", text: (R.pc || "내 PC") + " · 밖에서 작업 중" });
    var upPct = el("span", { id: "fsr-uppct" });
    var full = el("input", { type: "checkbox", id: "fsr-full" });
    full.checked = !!R.prefs.fullRes;
    full.addEventListener("change", async function () {
      var on = full.checked;
      if (on && !window.confirm("원본 화질로 보면 데이터를 많이 써요 (와이파이 추천). 켤까요?")) { full.checked = false; return; }
      var r = await fsFetch("/s/prefs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fullRes: on }) });
      if (r.ok) { R.prefs.fullRes = on; toast(on ? "원본 화질로 봐요 · 화면을 새로 고치면 바뀌어요" : "가벼운 미리보기 파일로 봐요"); }
      else full.checked = !on;
    });
    var menu = el("div", { id: "fsr-menu", class: "fsr-acts" },
      el("button", { class: "fsr-btn", type: "button", text: "완성본 받기", onclick: function () { FS.showOutputs(); } }),
      el("button", { class: "fsr-btn", type: "button", text: "내 기기에서 올리기", onclick: function () { FS.showUploads(true); } }),
      el("label", { class: "fsr-chk", title: "데이터를 많이 써요" }, full, el("span", { text: "원본 화질로 보기" })),
      el("button", { class: "fsr-btn", type: "button", text: "연결 끝내기", onclick: logout }),
      el("a", { class: "fsr-btn", href: homeUrl(""), text: "← 원격 홈" }));
    var more = el("button", { class: "fsr-btn fsr-more", type: "button", "aria-expanded": "false", text: "메뉴", onclick: function () {
      var open = b.classList.toggle("open"); more.setAttribute("aria-expanded", open ? "true" : "false");
    } });
    b.append(el("span", { class: "fsr-dot", "aria-hidden": "true" }), name, upPct, more, menu);
    document.body.prepend(b);
  }
  async function logout() {
    if (dirty() && !window.confirm("저장하지 않은 내용이나 올리던 파일이 있어요. 그래도 연결을 끝낼까요?")) return;
    if (!dirty() && !window.confirm("밖에서 작업 연결을 끝낼까요? 다시 쓰려면 원격 홈에서 다시 열어요.")) return;
    try { await fsFetch("/s/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch (e) { /* 그래도 나감 */ }
    R.locked = true;
    location.replace(homeUrl(""));
  }

  // ---------- 창 (완성본 받기 · 올리기) ----------
  function panel(id, title, body) {
    var old = document.getElementById("fsr-panel");
    if (old) old.remove();
    var close = function () { mask.remove(); };
    var box = el("div", { class: "fsr-card fsr-panel", role: "dialog", "aria-modal": "true", "aria-label": title, id: id },
      el("div", { class: "fsr-head" }, el("h2", { text: title }), el("button", { class: "fsr-btn", type: "button", text: "닫기", onclick: close })), body);
    var mask = el("div", { id: "fsr-panel", onclick: function (e) { if (e.target === mask) close(); } }, box);
    document.body.append(mask);
    return { box: box, close: close };
  }
  function download(href) {
    var a = el("a", { href: href, download: "" });
    document.body.append(a);
    a.click();
    a.remove();
  }
  var KIND = { video: "영상", image: "썸네일", text: "올리기 키트", srt: "자막", xml: "편집 파일" };
  async function showOutputs() {
    var list = el("div", { class: "fsr-list" }, el("p", { class: "fsr-muted", text: "불러오는 중이에요…" }));
    var pn = panel("fsr-outputs", "완성본 받기", el("div", {},
      el("p", { class: "fsr-muted", text: "PC 완성본 폴더의 파일을 이 기기로 받아요 · 큰 영상은 와이파이에서 받는 게 좋아요" }), list,
      el("div", { class: "fsr-row" }, el("button", { class: "fsr-btn", type: "button", text: "새로 고침", onclick: function () { fill(); } }),
        el("a", { class: "fsr-btn", href: "/s/dl?log=1", download: "", text: "작업 기록(studio.log) 받기" }))));
    async function fill() {
      var j = null;
      try { var r = await fsFetch("/s/outputs", { cache: "no-store" }); if (r.ok) j = await r.json(); } catch (e) { /* 아래 */ }
      if (!j) { list.replaceChildren(el("p", { text: "목록을 불러오지 못했어요" })); return; }
      if (!j.outputs.length) { list.replaceChildren(el("p", { class: "fsr-muted", text: "아직 완성본이 없어요 · 편집실에서 내보내면 여기에 생겨요" })); return; }
      list.replaceChildren.apply(list, j.outputs.map(function (o) {
        var slot = el("div", { class: "fsr-slot" });
        var acts = el("div", { class: "fsr-row" }, el("a", { class: "fsr-btn pri", href: "/s/dl?f=" + encodeURIComponent(o.name), download: "", text: "받기 (" + size(o.size) + ")" }));
        if (o.kind === "video") {
          if (o.preview) acts.append(el("button", { class: "fsr-btn", type: "button", text: "작은 미리보기로 보기", onclick: function () {
            slot.replaceChildren(el("video", { controls: true, playsinline: true, preload: "metadata", src: "/s/preview?f=" + encodeURIComponent(o.name) }));
          } }));
          else acts.append(el("button", { class: "fsr-btn", type: "button", text: "작은 미리보기 만들기", onclick: async function () {
            var r = await fsFetch("/s/outputs/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ f: o.name }) });
            var jj = {}; try { jj = await r.json(); } catch (e) { /* */ }
            toast(r.ok ? "작은 미리보기를 만들기 시작했어요 · 끝나면 [새로 고침]" : (jj.error || "지금은 만들 수 없어요"));
          } }));
        }
        var qa = o.qa ? el("p", { class: "fsr-muted", text: "검수 " + o.qa.score + "점 · 고칠 것 " + (o.qa.bad || 0) + " · 볼 것 " + (o.qa.warn || 0) }) : null;
        return el("div", { class: "fsr-item" }, el("div", { class: "fsr-name", text: o.name }),
          el("div", { class: "fsr-muted", text: (KIND[o.kind] || "") + " · " + size(o.size) + " · " + new Date(o.mtime * 1000).toLocaleString("ko-KR") }), qa, acts, slot);
      }));
    }
    fill();
    return pn;
  }

  // ---------- 내 기기에서 올리기 ----------
  async function digest(buf) { return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", buf)), function (b) { return b.toString(16).padStart(2, "0"); }).join(""); }
  async function probeOf(file) {
    var a = await file.slice(0, 1 << 20).arrayBuffer();
    var b = await file.slice(Math.max(0, file.size - (1 << 20))).arrayBuffer();
    var both = new Uint8Array(a.byteLength + b.byteLength);
    both.set(new Uint8Array(a), 0); both.set(new Uint8Array(b), a.byteLength);
    return digest(both.buffer);
  }
  async function jpost(path, body) {
    var r = await fsFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    var j = {}; try { j = await r.json(); } catch (e) { /* */ }
    return { status: r.status, ok: r.ok, j: j };
  }
  function upView() {
    var pc = R.ups.filter(function (u) { return u.state === "up" || u.state === "wait"; });
    var pct = document.getElementById("fsr-uppct");
    if (pct) pct.textContent = pc.length ? " · 올리는 중 " + Math.floor(pc.reduce(function (a, u) { return a + u.received; }, 0) * 100 / Math.max(1, pc.reduce(function (a, u) { return a + u.size; }, 0))) + "%" : "";
    var list = document.getElementById("fsr-uplist");
    if (!list) return;
    var rows = R.ups.map(function (u) {
      var pctv = u.size ? Math.floor(u.received * 100 / u.size) : 0;
      var bar = el("div", { class: "fsr-bar" }, el("i", { style: "width:" + Math.max(2, pctv) + "%" }));
      var st = { up: "올리는 중", wait: "연결을 다시 잇는 중", paused: "멈춤", done: "다 올렸어요 ✓", err: "멈췄어요", cancel: "그만둠", prep: "준비 중" }[u.state] || "";
      var info = u.state === "up" && u.speed ? " · " + size(u.speed) + "/초 · 약 " + dur((u.size - u.received) / u.speed) + " 남음" : "";
      var acts = el("div", { class: "fsr-row" });
      if (u.state === "up" || u.state === "wait") acts.append(el("button", { class: "fsr-btn", type: "button", text: "잠시 멈춤", onclick: function () { u.pause = true; } }));
      if (u.state === "paused" || u.state === "err") acts.append(el("button", { class: "fsr-btn pri", type: "button", text: "이어 올리기", onclick: function () { u.pause = false; run(u); } }));
      if (u.state !== "done" && u.state !== "cancel") acts.append(el("button", { class: "fsr-btn", type: "button", text: "그만두기", onclick: function () { cancel(u); } }));
      return el("div", { class: "fsr-item", "data-up": u.name }, el("div", { class: "fsr-name", text: u.name }),
        el("div", { class: "fsr-muted", text: st + " · " + pctv + "% (" + size(u.received) + " / " + size(u.size) + ")" + info + (u.error ? " · " + u.error : "") }), bar, acts);
    });
    var pend = R.pending.filter(function (p) { return !R.ups.some(function (u) { return u.id === p.id; }); }).map(function (p) {
      return el("div", { class: "fsr-item fsr-pend" }, el("div", { class: "fsr-name", text: p.name }),
        el("div", { class: "fsr-muted", text: "이어 올릴 파일이에요 · " + Math.floor(p.received * 100 / p.size) + "% 올렸어요 · 같은 파일을 다시 고르면 이어서 올려요" }),
        el("div", { class: "fsr-row" }, el("button", { class: "fsr-btn pri", type: "button", text: "파일 다시 고르기", onclick: function () { pick(p.target); } }),
          el("button", { class: "fsr-btn", type: "button", text: "그만두기", onclick: async function () {
            await jpost("/s/upload/cancel", { id: p.id }); R.pending = R.pending.filter(function (x) { return x.id !== p.id; }); upView();
          } })));
    });
    list.replaceChildren.apply(list, pend.concat(rows).length ? pend.concat(rows) : [el("p", { class: "fsr-muted", text: "올리는 파일이 없어요" })]);
  }
  async function loadPending() {
    try { var r = await fsFetch("/s/upload/pending", { cache: "no-store" }); if (r.ok) R.pending = (await r.json()).uploads || []; } catch (e) { /* */ }
    upView();
  }
  function pick(target) {
    var inp = el("input", { type: "file", multiple: true, accept: target === "assets" ? "video/*,audio/*,image/*" : "video/*,.mts,.m2ts,.avi,.wmv,.mpg,.mpeg,.3gp,.mxf,.insv" });
    inp.style.display = "none";
    inp.addEventListener("change", function () { Array.from(inp.files || []).forEach(function (f) { upload(f, { target: target || "videos" }); }); inp.remove(); });
    document.body.append(inp);
    inp.click();
  }
  function showUploads(withPick) {
    panel("fsr-uploads", "내 기기에서 올리기", el("div", {},
      el("p", { class: "fsr-muted", text: "이 기기의 영상을 PC 보관함에 올려요 · 큰 파일도 조각으로 올리고, 끊기면 그 자리부터 이어 올려요" }),
      IOS ? el("p", { class: "fsr-hint", text: "아이폰: '사진'에서 고르면 화질이 줄 수 있어요 · '파일' 앱에서 고르면 원본 그대로 올라가요" }) : null,
      el("div", { class: "fsr-row" }, el("button", { class: "fsr-btn pri", type: "button", text: "파일 고르기", onclick: function () { pick("videos"); } })),
      el("div", { class: "fsr-list", id: "fsr-uplist" })));
    loadPending();
    if (withPick && !R.pending.length) pick("videos");
  }
  async function cancel(u) {
    u.pause = true; u.state = "cancel";
    if (u.id) await jpost("/s/upload/cancel", { id: u.id });
    if (u.done) u.done(null);
    upView();
  }
  function upload(file, opts) {
    var u = { file: file, name: file.name, size: file.size, target: (opts && opts.target) || "videos", received: 0, state: "prep", id: null, speed: 0, tries: 0 };
    R.ups.push(u);
    var p = new Promise(function (res) { u.done = res; });
    if (!document.getElementById("fsr-uplist") && u.target === "videos") showUploads(false);
    upView();
    run(u);
    return p;
  }
  async function run(u) {
    if (u.running) return;
    u.running = true;
    try {
      if (!u.probe) u.probe = await probeOf(u.file);
      if (!u.id) {
        var ini = await jpost("/s/upload/init", { name: u.file.name, size: u.file.size, lastModified: u.file.lastModified || 0, probe: u.probe, target: u.target });
        if (!ini.ok) { u.state = "err"; u.error = ini.j.error || "올리지 못했어요"; toast(u.error); return; }
        u.id = ini.j.id; u.received = ini.j.received; u.chunk = Math.min(ini.j.chunk || R.chunk, 16 << 20); u.name = ini.j.name || u.name;
        R.pending = R.pending.filter(function (x) { return x.id !== u.id; });
      }
      u.state = "up"; upView();
      var t0 = Date.now(), b0 = u.received;
      while (u.received < u.size) {
        if (u.pause) { if (u.state !== "cancel") u.state = "paused"; upView(); return; }
        var end = Math.min(u.size, u.received + u.chunk);
        var buf = await u.file.slice(u.received, end).arrayBuffer();
        var r = null, j = {};
        try {
          r = await fsFetch("/s/upload/chunk?id=" + u.id + "&offset=" + u.received, { method: "POST", body: buf,
            headers: { "Content-Type": "application/octet-stream", "X-FS-Sha256": await digest(buf) } });
          try { j = await r.json(); } catch (e) { j = {}; }
        } catch (e) { r = null; }
        if (r && r.ok) {
          u.received = j.received; u.tries = 0;
          var secs = (Date.now() - t0) / 1000;
          if (secs > 1) u.speed = (u.received - b0) / secs;
          upView();
          continue;
        }
        if (r && r.status === 409 && typeof j.received === "number") { u.received = j.received; continue; }
        if (r && (r.status === 507 || r.status === 404 || r.status === 401 || r.status === 403 || r.status === 413)) {
          u.state = "err"; u.error = j.error || "올리지 못했어요"; upView(); toast(u.error); return;
        }
        // 끊김·5xx → 잠깐 기다렸다 PC 가 받은 자리를 다시 물어 이어서 (자동)
        u.state = "wait"; upView();
        u.tries++;
        if (u.tries > 40) { u.state = "paused"; u.error = "연결이 계속 끊겨요 · [이어 올리기]를 눌러 주세요"; upView(); return; }
        await sleep(Math.min(30000, 1000 * Math.pow(2, Math.min(5, u.tries - 1))));
        try {
          var s = await fsFetch("/s/upload/status?id=" + u.id, { cache: "no-store" });
          if (s.ok) u.received = (await s.json()).received;
          else if (s.status === 404) { u.state = "err"; u.error = "PC에서 올리던 파일이 사라졌어요 · 다시 골라 주세요"; upView(); return; }
        } catch (e) { /* 다음 차례에 */ }
        u.state = "up"; t0 = Date.now(); b0 = u.received;
      }
      var fin = await jpost("/s/upload/finish", { id: u.id });
      if (!fin.ok) { u.state = "err"; u.error = fin.j.error || "마무리하지 못했어요"; upView(); toast(u.error); return; }
      u.state = "done"; upView();
      toast(u.target === "videos" ? "보관함에 올렸어요 · " + (fin.j.name || u.name) : "가져왔어요 · " + (fin.j.file || u.name));
      if (u.done) u.done(fin.j);
    } catch (e) {
      u.state = "paused"; u.error = "올리다 멈췄어요 · [이어 올리기]를 눌러 주세요"; upView();
    } finally { u.running = false; }
  }
  window.addEventListener("beforeunload", function (e) {
    if (R.ups.some(function (u) { return u.state === "up" || u.state === "wait"; })) { e.preventDefault(); e.returnValue = ""; }
  });

  // ---------- 화면에서 쓰는 것 ----------
  var FS = window.FS_REMOTE = {
    get csrf() { return R.csrf; },
    get site() { return R.site; },
    fullRes: function () { return !!R.prefs.fullRes; },
    isDirty: null,   // 화면이 정함 (저장 안 한 편집 등) → 끊겼을 때 저절로 원격 홈으로 가지 않음
    dirty: dirty,
    upload: upload,
    pick: pick,
    download: function (name) { download("/s/dl?f=" + encodeURIComponent(name)); },
    showOutputs: showOutputs,
    showUploads: showUploads,
    toast: toast,
    openFolder: function (which, quiet) {
      if (which === "out") return showOutputs();
      if (which === "videos") return showUploads(true);
      if (which === "log") return download("/s/dl?log=1");
      if (!quiet) toast("PC에서만 열 수 있어요");
      return null;
    }
  };

  ready(function () {
    document.documentElement.setAttribute("data-remote", "1");
    bar();
    refreshSession().then(function () {
      var n = document.getElementById("fsr-pc"); if (n) n.textContent = (R.pc || "내 PC") + " · 밖에서 작업 중";
      var f = document.getElementById("fsr-full"); if (f) f.checked = !!R.prefs.fullRes;
    });
    var slot = document.getElementById("remoteLibSlot");  // 보관함 머리 (ui.html)
    if (slot) slot.replaceChildren(el("button", { class: "btn", type: "button", text: "내 기기에서 올리기", onclick: function () { showUploads(true); } }));
    loadPending();
  });
})();
