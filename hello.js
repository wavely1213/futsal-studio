// 풋살 스튜디오 · 밖에서 작업 — 연결 쪽 (/s/hello) 과 세션 없이 연 화면의 안내 쪽 (D-230)
// 세션 없이 내주는 정적 파일이라 비밀이 없다. 요청은 같은 출처(/s/handoff · /s/session)에만 보낸다.
// 넘겨주기 표는 주소의 # 뒤(#t=…)로만 오고(서버·Cloudflare 기록·Referer 에 안 남음) 읽자마자 주소창에서 지운다.
(function () {
  "use strict";
  var NEXT_RE = /^\/(editor|thumb)?(\?[A-Za-z0-9%._~&=+\-!'()*]{0,600})?$/;  // rstudio.NEXT_RE · 휴대폰 페이지 proto.nextOk 와 같음
  var TEXT = {
    none: ["다시 연결해 주세요", "PC와 연결이 끊겼어요 · 원격 홈으로 가서 다시 열어요"],
    expired: ["연결이 끝났어요", "오래 쓰지 않아 연결이 끝났어요 · 원격 홈으로 가서 다시 열어요"],
    revoked: ["PC에서 연결을 끊었어요", "다시 쓰려면 원격 홈에서 [PC 스튜디오 열기]를 눌러 주세요"],
    logout: ["연결을 끝냈어요", "이 브라우저에서 밖에서 작업 연결을 끝냈어요 · 다시 쓰려면 원격 홈에서 [PC 스튜디오 열기]를 눌러 주세요"],
    studio_off: ["밖에서 작업이 꺼져 있어요", "PC의 '휴대폰으로 보기'에서 '밖에서 작업 허용'을 켜 주세요"],
    handoff: ["다시 열어 주세요", "연결 표가 맞지 않거나 시간이 지났어요 · 원격 홈에서 [PC 스튜디오 열기]를 다시 눌러 주세요"],
    cookie: ["브라우저가 쿠키를 막고 있어요", "이 브라우저 설정에서 쿠키(사이트 데이터)를 허용한 뒤 원격 홈에서 다시 열어 주세요 · 시크릿·개인 정보 보호 모드라면 일반 창에서 열어 주세요"],
    net: ["PC와 연결하지 못했어요", "인터넷 연결을 확인한 뒤 원격 홈에서 다시 열어 주세요"],
    start: ["원격 홈에서 열어 주세요", "원격 홈(아래 주소)의 [PC 스튜디오 열기]로 들어와 주세요 · 이 쪽은 즐겨찾기로 바로 열 수 없어요"]
  };
  // '이번만' 연결한 기기 (서버가 fs-guest 로 알려 줌 · rstudio.MSG_GUEST · remote_ui.js LOCK_GUEST 와 같음)
  var TEXT_GUEST = {
    revoked: ["PC에서 연결을 끊었어요", "다시 쓰려면 원격 홈에서 아이디·비밀번호로 다시 연결해 주세요"],
    logout: ["연결을 끝냈어요", "이 기기에서 나갔어요 · 다시 쓰려면 원격 홈에서 아이디·비밀번호로 다시 연결해 주세요"]
  };

  function meta(n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute("content") || "" : ""; }
  function siteOk(s) {
    try {
      var u = new URL(String(s || ""));
      var dev = u.hostname === "127.0.0.1" || u.hostname === "localhost";
      if (u.protocol !== "https:" && !(dev && u.protocol === "http:")) return "";
      return u.origin + u.pathname;
    } catch (e) { return ""; }
  }
  function here() { var p = location.pathname + location.search; return NEXT_RE.test(p) ? p : "/"; }
  function homeUrl(site, resume) { return site + (resume ? "#resume=" + encodeURIComponent(resume) : ""); }

  function paint() {
    var b = document.body;
    if (!b) return;
    var css = { margin: "0", minHeight: "100vh", display: "grid", placeItems: "center", background: "#f3f4f6", color: "#111827",
      fontFamily: "system-ui, -apple-system, 'Segoe UI', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif" };
    if (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches) { css.background = "#0d1117"; css.color = "#e6edf3"; }
    Object.keys(css).forEach(function (k) { b.style[k] = css[k]; });
    var box = document.getElementById("box");
    if (box) { box.style.maxWidth = "420px"; box.style.padding = "24px 16px"; box.style.textAlign = "center"; box.style.lineHeight = "1.6"; }
    var h1 = box && box.querySelector("h1");
    if (h1) { h1.style.fontSize = "20px"; h1.style.margin = "0 0 8px"; }
    var a = document.getElementById("home");
    if (a) {
      var s = a.style;
      s.display = "inline-block"; s.marginTop = "12px"; s.padding = "12px 18px"; s.borderRadius = "10px"; s.background = "#0f7a3d";
      s.color = "#fff"; s.textDecoration = "none"; s.fontWeight = "600";
    }
  }
  function say(key, site, resume) {
    var t = (meta("fs-guest") === "1" && TEXT_GUEST[key]) || TEXT[key] || TEXT.none;
    var box = document.getElementById("box");
    if (!box) return;
    box.textContent = "";
    var h1 = document.createElement("h1"); h1.textContent = t[0];
    var p = document.createElement("p"); p.id = "msg"; p.textContent = t[1];
    box.appendChild(h1); box.appendChild(p);
    site = siteOk(site) || siteOk(meta("fs-site"));  // 이 쪽을 낸 PC 가 meta 로 알려 준 원격 홈 (표 없음·쿠키 막힘·끊김에도 막다른 쪽이 되지 않게)
    if (site) {
      var addr = site.replace(/^https?:\/\//, "").replace(/\/$/, "");
      var a = document.createElement("a"); a.id = "home"; a.href = homeUrl(site, resume); a.textContent = "원격 홈으로 가기";
      var w = document.createElement("p"); w.appendChild(a); box.appendChild(w);
      var q = document.createElement("p"); q.id = "addr"; q.textContent = "원격 홈 주소 · " + addr; q.style.opacity = ".75"; q.style.fontSize = "13px";
      box.appendChild(q);
    }
    paint();
  }

  async function handoff(t) {
    try { history.replaceState(null, "", "/s/hello"); } catch (e) { /* 그대로 */ }
    var r, j = {};
    try {
      r = await fetch("/s/handoff", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: t }) });
      try { j = await r.json(); } catch (e) { j = {}; }
    } catch (e) { return say("net", ""); }
    if (!r.ok || !j.ok) return say(j.code === "studio_off" ? "studio_off" : "handoff", j.site);
    try {  // 쿠키가 정말 저장됐는지 (막혀 있으면 화면이 계속 원격 홈으로 돌아가지 않게)
      var c = await fetch("/s/session", { credentials: "same-origin", cache: "no-store" });
      if (c.status === 401) return say("cookie", "");
    } catch (e) { return say("net", ""); }
    location.replace(typeof j.next === "string" && NEXT_RE.test(j.next) ? j.next : "/");
  }

  function lock(code) {
    var site = meta("fs-site"), resume = here();
    say(code, site, code === "none" || code === "expired" ? resume : "");
    // 시간이 지났거나 쿠키가 없음 → 원격 홈이 다시 넘겨줌 (PC 가 끊었거나·꺼 두었거나·이 브라우저에서 끝낸(logout) 것은 저절로 돌아가지 않음)
    if ((code === "none" || code === "expired") && siteOk(site)) {
      setTimeout(function () { location.replace(homeUrl(siteOk(site), resume)); }, 1500);
    }
  }

  function start() {
    var code = meta("fs-lock");
    if (code) return lock(code);
    var t = new URLSearchParams(String(location.hash || "").replace(/^#/, "")).get("t");
    if (t && /^[A-Za-z0-9_-]{43}$/.test(t)) return handoff(t);
    try { history.replaceState(null, "", "/s/hello"); } catch (e) { /* 그대로 */ }
    say("start", "");
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
