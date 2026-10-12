"""원격 리스너의 모든 길 — 자동으로 찾아 세션 없이 막히는지 (D-231) · python3 -m unittest tests.test_remote_routes

app.py 의 GET·POST 경로 글자(do_GET·do_POST·채널 전략·유튜브 처리기 · jobs 사전 · startswith 앞머리)와 rstudio.POLICY 의 /s/*,
remote.py 의 /r/* 를 모아서:
  1) app 의 모든 길이 rstudio.POLICY 에 분류돼 있는지 (새 길을 만들면 이 시험이 실패 → 밖에서 쓸지·PC 에서만인지 정해야 함)
  2) 세션 없이: 모든 길이 401/403 (열린 길 /s/hello·/s/hello.js·/r/ping 만 200 · 비밀 없음) · 작업이 시작되거나 파일이 생기지 않음
  3) 세션이 있어도: PC 에서만 → 403 pc_only · CSRF 없는 POST → 403 · 다른 Origin → 403 · 다른 메서드 → 405/403
"""
import json
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rstudio  # noqa: E402
from test_remote_studio import NAME, StudioBase, quote  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
_PATH_RE = re.compile(r'\b(?:u\.)?path\s*(?:==|!=)\s*"(/[^"]*)"|\b(?:u\.)?path\s+in\s+\(([^)]*)\)|\b(?:u\.)?path\.startswith\(\s*\(?([^)]*)\)')
# 다른 모듈의 주소 상수로 비교하는 길 (예: path == autorun.PATH · path in (…, autorun.PATH)) — 그 모듈에서 값을 읽어 옴
_CONST_RE = re.compile(r'\b(?:u\.)?path\s*(?:==|!=)\s*([a-z_]+)\.([A-Z_]+)\b')
_CONST_IN_RE = re.compile(r'\b([a-z_]+)\.([A-Z_]+)\b')


def _const(mod, name):
    m = re.search(r'^%s\s*=\s*"(/[^"]*)"' % re.escape(name), (ROOT / f"{mod}.py").read_text(encoding="utf-8"), re.M)
    assert m, f"{mod}.{name} 주소 상수를 찾지 못함"
    return m.group(1)


def _body(lines, start, end):
    a = next(i for i, ln in enumerate(lines) if ln.strip().startswith(start))
    b = next(i for i, ln in enumerate(lines) if i > a and ln.strip().startswith(end))
    return "\n".join(lines[a:b])


def app_routes():
    """→ {(메서드, 경로)} · 앞머리(끝이 '/')는 따로."""
    lines = (ROOT / "app.py").read_text(encoding="utf-8").splitlines()
    parts = {"GET": [_body(lines, "def do_GET", "def do_POST"), _body(lines, "def _strategy_get", "def _strategy_post"),
                     _body(lines, "def _youtube_get", "def _youtube_post")],
             "POST": [_body(lines, "def do_POST", "def _strategy_get"), _body(lines, "def _strategy_post", "# ---- 유튜브에 바로"),
                      _body(lines, "def _youtube_post", "@staticmethod")]}
    exact, prefix = set(), set()
    for m, texts in parts.items():
        for t in texts:
            for a, b, c in _PATH_RE.findall(t):
                for lit in ([a] if a else re.findall(r'"(/[^"]*)"', b or c)):
                    (prefix if (c and not a) else exact).add((m, lit))
                if b:
                    exact |= {(m, _const(mod, nm)) for mod, nm in _CONST_IN_RE.findall(b)}
            exact |= {(m, _const(mod, nm)) for mod, nm in _CONST_RE.findall(t)}
            if m == "POST":  # 작업 사전 jobs = {"/api/…": (이름, …)}
                exact |= {(m, k) for k in re.findall(r'^\s+"(/api/[^"]+)":\s*\(', t, re.M)}
    # 앞머리 검사(path.startswith(("/api/edit/", …)))의 이름 검사 줄은 길이 아님 → 앞머리로만
    for m, p in list(exact):
        if p.endswith("/") and p != "/":
            exact.discard((m, p))
            prefix.add((m, p))
    return exact, prefix


def remote_routes():
    src = (ROOT / "remote.py").read_text(encoding="utf-8")
    r = {(m, p) for m, p in re.findall(r'\("(GET|POST)", "(/r/[^"]+)"\)', src)}
    return r | {("GET", "/r/ping"), ("POST", "/r/pair"), ("GET", "/r/m/" + "A" * 22)}


def sample(path):
    """길에 넣을 그럴듯한 인자 (세션 없이도 이름 검사보다 경계가 먼저인지 보려고)."""
    q = {"/frame": "?name=" + quote(NAME) + "&t=1", "/media": "?name=" + quote(NAME), "/thumbs.jpg": "?name=" + quote(NAME),
         "/api/edit/file": "?src=videos&f=" + quote(NAME), "/api/edit/open": "?name=" + quote(NAME), "/s/dl": "?f=a.mp4"}
    if path.endswith("/"):
        path += "a.png"
    return path + q.get(path, "")


class RouteEnumerationTest(StudioBase):
    def setUp(self):
        super().setUp()
        self.exact, self.prefix = app_routes()
        self.own = {k for k, v in rstudio.POLICY.items() if k[1].startswith("/s/")}

    def test_parser_found_routes(self):
        """찾기가 제대로 도는지 (숫자가 갑자기 줄면 찾기 규칙이 깨진 것)."""
        self.assertGreater(len([1 for m, _ in self.exact if m == "GET"]), 40)
        self.assertGreater(len([1 for m, _ in self.exact if m == "POST"]), 90)
        for want in (("GET", "/"), ("GET", "/api/state"), ("POST", "/api/analyze"), ("POST", "/api/youtube/upload"),
                     ("POST", "/api/strategy/ai_paste"), ("GET", "/api/youtube/history"), ("POST", "/api/remote/on"),
                     ("POST", "/api/auto"), ("GET", "/api/auto"), ("GET", "/api/auto/pending"), ("POST", "/api/auto/release"),
                     ("POST", "/api/auto/claim"), ("POST", "/api/auto/thumb"), ("POST", "/api/auto/prefs"), ("POST", "/api/edit/sfx")):
            self.assertIn(want, self.exact)
        self.assertIn(("GET", "/fonts/"), self.prefix)
        self.assertNotIn(("POST", "/frame"), self.exact)  # 장면 주소 검사(src.startswith)는 길이 아님

    def test_every_app_route_is_classified(self):
        missing = sorted(f"{m} {p}" for m, p in self.exact if rstudio.classify(m, p) is None)
        self.assertEqual(missing, [], "rstudio.POLICY 에 분류해 주세요 (밖에서 쓸 것인지 · PC 에서만인지 · D-231)")
        for m, pre in self.prefix:  # 앞머리: 그 아래 모르는 길은 막힘 (미리 열어 둔 앞머리는 정적 파일 셋뿐)
            k = rstudio.classify(m, pre + "새로운길")
            if pre in ("/fonts/", "/stickers/", "/asset/", "/api/remote/"):
                self.assertIsNotNone(k, pre)
            else:
                self.assertIsNone(k, pre)

    def test_pc_only_set(self):
        pc = {k for k, v in rstudio.POLICY.items() if v == rstudio.PC_ONLY}
        for want in (("GET", "/api/ping"), ("GET", "/api/remote"), ("GET", "/api/browsers"), ("POST", "/api/update"),
                     ("POST", "/api/restart"), ("POST", "/api/claude/login"), ("POST", "/api/youtube/client"),
                     ("POST", "/api/youtube/login"), ("POST", "/api/youtube/logout"), ("POST", "/api/focus")):
            self.assertIn(want, pc)
        for m, p in self.exact:
            if p.startswith("/api/remote/"):
                self.assertEqual(rstudio.classify(m, p), rstudio.PC_ONLY, p)

    def _snapshot(self):
        return sorted(str(p.relative_to(self.work)) for p in self.work.rglob("*") if p.name != "studio.log")

    def test_no_session_everything_401_403(self):
        before = self._snapshot()
        jobs_before = self.app.JOB["id"]
        routes = sorted(self.exact | {(m, p) for m, p in self.prefix} | self.own | remote_routes() | {("GET", "/nothing/here"), ("POST", "/x")})
        bad = []
        for m, p in routes:
            path = sample(p)
            self.clock.tick(0.5)  # 요청 수 바구니가 다시 차게 (시험 시계)
            st, hd, out = self.raw(m, path, b"{}" if m == "POST" else b"",
                                   {"Origin": self.origin, "Content-Type": "application/json"} if m == "POST" else {})
            if (m, p) in (("GET", "/s/hello"), ("GET", "/s/hello.js"), ("GET", "/r/ping")):
                self.assertEqual(st, 200, p)
                for secret in (self.cred["keys"]["auth"], self.svc.store.data["topics"]["notify"]):
                    self.assertNotIn(secret.encode(), out)
                continue
            if st not in (401, 403):
                bad.append((m, p, st))
            if p.startswith("/r/"):
                continue
            self.assertFalse([k for k, _ in hd if k.lower().startswith("access-control-")], p)
            self.assertIn("no-store", dict((k.lower(), v) for k, v in hd).get("cache-control", ""), p)
        self.assertEqual(bad, [])
        time.sleep(0.2)
        self.assertEqual(self.app.JOB["id"], jobs_before)   # 작업이 시작되지 않음
        self.assertIsNone(self.app.JOB["name"])
        self.assertEqual(self._snapshot(), before)          # 파일이 생기지 않음

    def test_with_session_boundaries(self):
        self.login()
        routes = sorted(self.exact | self.own)
        for m, p in routes:
            kind = rstudio.classify(m, p)
            if kind == rstudio.PUBLIC:
                continue
            path = sample(p)
            self.clock.tick(0.5)  # 요청 수 바구니가 다시 차게 (시험 시계 · 세션 30분 안)
            self.svc.limiter.fails.clear()  # CSRF 실패는 잠금 횟수에 셈 (아래 따로 확인) → 길마다 새로
            self.svc.limiter.locked_until.clear()
            if kind == rstudio.PC_ONLY:
                st, j = self.jreq(m, path, {} if m == "POST" else None)
                self.assertEqual((st, j.get("code")), (403, "pc_only"), p)
                continue
            if m == "POST":
                ctype = "application/octet-stream" if p in ("/api/edit/upload", "/s/upload/chunk") else None
                raw = b"x" if ctype else None
                st, j = self.jreq("POST", path, {}, csrf=False, raw=raw, ctype=ctype)
                self.assertEqual((st, j.get("code")), (403, "csrf"), p)
                st, j = self.jreq("POST", path, {}, origin="https://evil.example", raw=raw, ctype=ctype)
                self.assertEqual((st, j.get("code")), (403, "origin"), p)
                st, j = self.jreq("POST", path, {}, headers={"Sec-Fetch-Site": "cross-site"}, raw=raw, ctype=ctype)
                self.assertEqual(st, 403, p)
            for other in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                self.assertIn(self.req(other, path)[0], (403, 405), (other, p))
        self.assertIsNone(self.app.JOB["name"])

    def test_csrf_failures_lock_out(self):
        self.login()
        for _ in range(10):
            self.assertEqual(self.jreq("POST", "/api/crash/dismiss", {}, csrf="x" * 43)[0], 403)
        self.assertEqual(self.jreq("GET", "/api/state")[0], 429)  # 그 IP 를 15분 잠금

    def test_rejected_without_session_even_with_valid_device_signature(self):
        """기기 서명(FSR2)은 /r/* 에서만 — 스튜디오 길은 세션 쿠키가 없으면 서명이 있어도 401."""
        from remote_fixture import signed
        st, hd, out = self.raw("GET", "/api/state", b"", {"Authorization": signed(self.cred, "GET", "/api/state", b"", clock=self.clock)})
        self.assertEqual((st, json.loads(out)["code"]), (401, "none"))


if __name__ == "__main__":
    unittest.main()
