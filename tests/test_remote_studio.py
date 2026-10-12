"""밖에서 작업 (원격 스튜디오 · D-230~D-234) — 주소 상수 · 세션 · 넘겨주기 · 확인 · 경계 · 페이지 끼워 넣기.

HTTP 시험은 개발 모드(FUTSAL_REMOTE_DEV=1) 원격 리스너를 임의 포트에 띄워 app 의 StudioHandler 와 같이 돌린다.
"""
import ast
import gzip
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import remote  # noqa: E402
import rstudio  # noqa: E402
import style  # noqa: E402
import youtube_upload  # noqa: E402
from remote_fixture import Clock, dev_env, pair_device, signed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NAME = "20260101_AbCdEfGhIjK_시험 영상 [꿀팁].mp4"
DEV = "iPhone · Safari"


class SiteConstantTest(unittest.TestCase):
    """remote.SITE 하나가 주소의 원본 (D-233) — 허용 출처·알림 글·PC 화면 안내가 여기서 나옴."""

    def test_origins_is_site_origin_only(self):
        """허용 출처 = SITE 의 출처 하나 (D-240) — 와벨리 커뮤니티(mulgyeol.kr·www)는 믿지 않음."""
        self.assertEqual(remote.SITE, "https://futsal.mulgyeol.kr")
        self.assertEqual(remote.ORIGINS, (remote.site_origin(remote.SITE),))
        self.assertEqual(remote.ORIGINS, ("https://futsal.mulgyeol.kr",))
        for bad in ("https://mulgyeol.kr", "https://www.mulgyeol.kr"):
            self.assertNotIn(bad, remote.ORIGINS)
        self.assertEqual(remote.site_origin("https://Futsal.Mulgyeol.KR/alt/fs"), "https://futsal.mulgyeol.kr")
        self.assertEqual(remote.site_origin("http://127.0.0.1:8973/x"), "http://127.0.0.1:8973")
        self.assertEqual(remote.site_origin(""), "")

    def test_moved_note_uses_site(self):
        self.assertIn(remote.SITE_TEXT, remote.NOTE_TEXT["moved"])
        self.assertEqual(remote.SITE_TEXT, remote.SITE.split("://", 1)[1])

    def test_no_other_literal(self):
        """주소 글자는 remote.SITE 한 곳에만 — 화면(ui.html)·다른 모듈에 따로 적지 않음 (옮길 때 한 줄)."""
        for f in ("ui.html", "editor.html", "thumb.html", "app.py"):
            self.assertNotIn("mulgyeol.kr", (ROOT / f).read_text(encoding="utf-8"), f)
        tree = ast.parse((ROOT / "remote.py").read_text(encoding="utf-8"))
        docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        lits = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "mulgyeol.kr" in n.value and id(n) not in docs]
        self.assertEqual(lits, ["https://futsal.mulgyeol.kr"])  # SITE = … 한 줄 (설명 글·주석 말고는 없음)



# ---------- 순수 부품 ----------

class SessionUnitTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.s = rstudio.Sessions(self.clock)
        self.dev = {"id": "a" * 16, "name": DEV}

    def test_create_lookup_and_one_per_device(self):
        tok, rec, old = self.s.create(self.dev, "203.0.113.0/24")
        self.assertRegex(tok, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(old, [])
        self.assertIs(self.s.lookup(tok)[0], rec)
        self.assertNotIn(tok, json.dumps({k: v for k, v in rec.items()}))  # 토큰은 해시로만
        tok2, rec2, old2 = self.s.create(self.dev, "203.0.113.0/24")
        self.assertEqual([r["id"] for r in old2], [rec["id"]])
        self.assertEqual(self.s.lookup(tok), (None, "expired"))  # 같은 기기는 세션 하나 (새 쿠키)
        self.assertIs(self.s.lookup(tok2)[0], rec2)
        self.assertEqual(self.s.lookup("x" * 43), (None, "none"))
        self.assertEqual(self.s.lookup("bad token"), (None, "none"))

    def test_idle_and_absolute(self):
        tok, rec, _ = self.s.create(self.dev, "n")
        self.clock.tick(rstudio.IDLE - 5)
        self.assertIsNotNone(self.s.lookup(tok)[0])
        self.s.touch(rec, "n")
        self.clock.tick(rstudio.IDLE + 1)
        self.assertEqual(self.s.lookup(tok), (None, "expired"))
        tok, rec, _ = self.s.create(self.dev, "n")
        for _ in range(int(rstudio.ABSOLUTE / 600) + 1):  # 10분마다 써도 12시간이면 끝
            self.clock.tick(600)
            if self.s.lookup(tok)[0]:
                self.s.touch(rec, "n")
        self.assertEqual(self.s.lookup(tok), (None, "expired"))

    def test_revoked_reason_remembered(self):
        tok, rec, _ = self.s.create(self.dev, "n")
        self.assertEqual(len(self.s.end(lambda r: r is rec, "revoked")), 1)
        self.assertEqual(self.s.lookup(tok), (None, "revoked"))
        self.clock.tick(rstudio.GONE_KEEP + 1)
        self.assertEqual(self.s.lookup(tok), (None, "none"))

    def test_net_change_reported_once(self):
        _, rec, _ = self.s.create(self.dev, "a")
        self.assertFalse(self.s.touch(rec, "a"))
        self.assertTrue(self.s.touch(rec, "b"))
        self.assertFalse(self.s.touch(rec, "b"))


class HandoffUnitTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.h = rstudio.Handoffs(self.clock)

    def test_single_use_net_ttl(self):
        t = self.h.issue("d1", "n1", "/editor?name=a")
        self.assertIsNone(self.h.take(t, "n2"))   # 다른 대역 → 버려짐
        self.assertIsNone(self.h.take(t, "n1"))   # 한 번뿐
        t = self.h.issue("d1", "n1", "/")
        self.assertEqual(self.h.take(t, "n1")["path"], "/")
        t = self.h.issue("d1", "n1", "/")
        self.clock.tick(rstudio.HANDOFF_TTL + 1)
        self.assertIsNone(self.h.take(t, "n1"))

    def test_one_outstanding_and_rate(self):
        a = self.h.issue("d1", "n", "/")
        b = self.h.issue("d1", "n", "/")
        self.assertIsNone(self.h.take(a, "n"))
        self.assertIsNotNone(self.h.take(b, "n"))
        for _ in range(rstudio.HANDOFF_RATE[0] - 2):
            self.h.issue("d1", "n", "/")
        with self.assertRaises(rstudio.TooMany):
            self.h.issue("d1", "n", "/")
        self.h.issue("d2", "n", "/")  # 다른 기기는 따로
        self.clock.tick(61)
        self.h.issue("d1", "n", "/")

    def test_next_re(self):
        for ok in ("/", "/editor", "/thumb", "/editor?name=20260101_a%20b.mp4", "/thumb?name=x&seq=1"):
            self.assertTrue(rstudio.NEXT_RE.fullmatch(ok), ok)
        for bad in ("", "//evil.com", "https://evil.com", "/api/state", "/editor#x", "/editor?name=<x>", "/s/hello",
                    "/editor?" + "a" * 601, "/editor\\..", "/editor?name=a b"):
            self.assertFalse(rstudio.NEXT_RE.fullmatch(bad), bad)


class ConfirmUnitTest(unittest.TestCase):
    def test_bound_and_single_use(self):
        clock = Clock()
        c = rstudio.Confirms(clock)
        t = c.issue("s1", "/api/youtube/upload", "h1")
        self.assertFalse(c.take(t, "s2", "/api/youtube/upload", "h1"))  # 다른 세션 → 버려짐
        t = c.issue("s1", "/api/youtube/upload", "h1")
        self.assertFalse(c.take(t, "s1", "/api/youtube/upload", "h2"))  # 다른 본문
        t = c.issue("s1", "/api/youtube/upload", "h1")
        self.assertTrue(c.take(t, "s1", "/api/youtube/upload", "h1"))
        self.assertFalse(c.take(t, "s1", "/api/youtube/upload", "h1"))
        t = c.issue("s1", "/api/youtube/upload", "h1")
        clock.tick(rstudio.CONFIRM_TTL + 1)
        self.assertFalse(c.take(t, "s1", "/api/youtube/upload", "h1"))


class PageUnitTest(unittest.TestCase):
    def test_inject_nonce_on_every_script_and_meta(self):
        for f in ("ui.html", "editor.html", "thumb.html"):
            raw = (ROOT / f).read_bytes()
            out = rstudio.inject(raw, "NONCE123", "CSRF<&>", "https://futsal.mulgyeol.kr", "폰 \"<x>\"", "j1", "c1").decode("utf-8")
            self.assertEqual(raw.decode().count("<script>"), len(re.findall(r'<script nonce="NONCE123">', out)), f)
            self.assertNotIn("<script>", out)
            self.assertEqual(len(re.findall(r"<script\b", out)), raw.decode().count("<script") + 1)
            self.assertTrue(all('nonce="NONCE123"' in t for t in re.findall(r"<script\b[^>]*>", out)), f)
            self.assertIn('<html data-remote="1"', out)
            self.assertIn('<meta name="fs-csrf" content="CSRF&lt;&amp;&gt;">', out)
            self.assertIn('content="폰 &quot;&lt;x&gt;&quot;"', out)
            head = out[:out.index("</head>")]
            self.assertIn('src="/s/remote.js?v=j1"', head)
            self.assertLess(out.index('src="/s/remote.js?v=j1"'), out.index('<script nonce="NONCE123">'))  # 심이 첫 스크립트
            self.assertIn('href="/s/remote.css?v=c1"', head)
            self.assertNotRegex(out, r'\son(error|click|load)="')  # 인라인 처리기 없음 (nonce CSP 에서 안 돎)

    def test_csp(self):
        c = rstudio.page_csp("N")
        self.assertIn("script-src 'self' 'nonce-N'", c)
        self.assertNotIn("unsafe-inline' https", c.split("script-src")[1].split(";")[0])
        self.assertIn("frame-ancestors 'self'", c)  # 같은 출처 틀만 (끝까지 자동으로의 보이지 않는 썸네일 틀 · D-201) — 다른 출처는 못 띄움
        self.assertNotIn("frame-ancestors *", c)
        self.assertIn("connect-src 'self'", c)
        self.assertIn("frame-ancestors 'none'", rstudio.DEFAULT_CSP)

    def test_scrub_state(self):
        d = {"workspace": "/home/kim/풋살사관학교_작업", "log": ["작업 폴더 · /home/kim/x", "ok"], "n": 3,
             "w": "C:\\Users\\kim\\x", "nest": [{"p": "/home/kim/a"}]}
        out = rstudio.scrub_state(d, "/home/kim")
        self.assertEqual(out["workspace"], "~/풋살사관학교_작업")
        self.assertEqual(out["log"][0], "작업 폴더 · ~/x")
        self.assertEqual(out["nest"][0]["p"], "~/a")
        self.assertEqual(rstudio.scrub_state({"w": "C:\\Users\\kim\\x"}, "C:\\Users\\kim")["w"], "~\\x")

    def test_cookie_attributes(self):
        svc = mock.Mock()
        svc.dev.return_value = False
        c = rstudio.set_cookie(svc, "T" * 43, 100)
        self.assertTrue(c.startswith("__Host-fs_s=" + "T" * 43 + "; "))
        for a in ("Path=/", "Secure", "HttpOnly", "SameSite=Strict", "Max-Age=100"):
            self.assertIn(a, c.split("; "))
        self.assertNotIn("Domain", c)
        svc.dev.return_value = True
        c = rstudio.set_cookie(svc, "T" * 43, 100)
        self.assertTrue(c.startswith("fs_s="))
        self.assertNotIn("Secure", c)

    def test_policy_basics(self):
        self.assertEqual(rstudio.classify("GET", "/api/remote"), rstudio.PC_ONLY)
        self.assertEqual(rstudio.classify("POST", "/api/remote/session/revoke"), rstudio.PC_ONLY)
        self.assertEqual(rstudio.classify("POST", "/api/youtube/upload"), rstudio.CONFIRM)
        self.assertEqual(rstudio.classify("GET", "/fonts/a.ttf"), rstudio.STATIC)
        self.assertIsNone(rstudio.classify("GET", "/fonts/"))
        self.assertIsNone(rstudio.classify("POST", "/api/state"))
        self.assertEqual(rstudio.bucket_of("/s/handoff"), "open")
        self.assertEqual(rstudio.bucket_of("/s/hello"), "studio")
        self.assertEqual(rstudio.bucket_of("/api/state"), "studio")

    def test_redact_new_secrets(self):
        tok = "A" * 43
        for raw in (f"https://x.trycloudflare.com/s/hello#t={tok}", f"Cookie: __Host-fs_s={tok}; a=b", f"fs_s={tok}",
                    f"X-FS-CSRF: {tok}", f'{{"token": "{tok}", "csrf": "{tok}"}}', f"{{'token': '{tok}'}}", f"X-FS-Confirm={tok}"):
            self.assertNotIn(tok, remote.redact(raw), raw)
        self.assertIn("t=12.5", remote.redact("/frame?name=a.mp4&t=12.5"))


# ---------- HTTP (개발 모드 원격 리스너 + app.StudioHandler) ----------

class StudioBase(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        self.tmp = Path(tempfile.mkdtemp(prefix="밖에서 작업 "))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        work = self.tmp / "작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        for sub in ("projects", "styles", "edit_media"):
            (work / sub).mkdir()
        patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [
            mock.patch.object(editor, "PROJECTS", work / "projects"), mock.patch.object(editor, "ASSETS", work / "edit_media"),
            mock.patch.object(style, "STYLES", work / "styles"), mock.patch.object(core, "ENGINE_HOME", self.tmp / ".futsal-studio"),
            mock.patch.dict(os.environ, dev_env()), mock.patch.object(app, "LOGFILE", work / "studio.log")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.work, self.out = work, dirs["OUT"]
        (dirs["VIDEOS"] / NAME).write_bytes(b"\x00" * 4096)
        self.clock = Clock(time.time())
        self.lines = []
        bridge = remote.Bridge(log=self.lines.append, start_job=app.start_job, job=app._job_snapshot, logs=lambda s: (self.lines[s:], len(self.lines)),
                               analyze=app.Handler._analyze, refs_job=app._refs_job, version="9.9.9", studio=app.StudioHandler)
        self.svc = remote.Service(home=self.tmp / ".futsal-studio", clock=self.clock, ntfy="http://127.0.0.1:1")
        self.svc.init(bridge)
        self.notes = []
        self.svc._note = lambda kind, text: self.notes.append((kind, text))
        p = mock.patch.object(remote, "SVC", self.svc)
        p.start()
        self.addCleanup(p.stop)
        self.assertTrue(self.svc.turn_on())
        end = time.time() + 10
        while self.svc.state != "on" and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(self.svc.state, "on")
        self.addCleanup(self.svc.turn_off)
        self.port = self.svc.port()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.cred = pair_device(self.svc, DEV)
        self.cookie = self.csrf = None

    def wait_idle(self, timeout=10):
        end = time.time() + timeout
        while self.app.JOB["name"] and time.time() < end:
            time.sleep(0.02)

    def raw(self, method, path, body=b"", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Host": f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        if method == "POST" or body:
            h.setdefault("Content-Length", str(len(body)))
        c.request(method, path, body=body if body else None, headers=h)
        r = c.getresponse()
        data = r.read()
        hd = r.getheaders()
        c.close()
        if dict(hd).get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return r.status, hd, data

    @staticmethod
    def hget(hd, k):
        return [v for kk, v in hd if kk.lower() == k.lower()]

    def signed_post(self, path, body, ip=None):
        data = json.dumps(body).encode()
        h = {"Origin": remote.ORIGINS[0], "Content-Type": "application/json", "Authorization": signed(self.cred, "POST", path, data, clock=self.clock)}
        if ip:
            h["Cf-Connecting-Ip"] = ip
        st, hd, out = self.raw("POST", path, data, h)
        return st, json.loads(out or b"{}")

    def handoff(self, path="/", ip=None, ip2=None):
        st, j = self.signed_post("/r/handoff", {"path": path}, ip)
        self.assertEqual(st, 200, j)
        st, hd, out = self.post_handoff(j["token"], ip2 or ip)
        return st, hd, json.loads(out or b"{}"), j

    def post_handoff(self, tok, ip=None, origin=None, extra=None):
        h = {"Origin": origin or self.origin, "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
        if ip:
            h["Cf-Connecting-Ip"] = ip
        h.update(extra or {})
        return self.raw("POST", "/s/handoff", json.dumps({"t": tok}).encode(), h)

    def login(self, path="/"):
        st, hd, j, _ = self.handoff(path)
        self.assertEqual(st, 200, j)
        c = self.hget(hd, "Set-Cookie")[0]
        self.cookie = c.split(";")[0]
        st, _, out = self.req("GET", "/s/session")
        self.csrf = json.loads(out)["csrf"]
        return j

    def req(self, method, path, body=None, cookie=True, csrf=True, origin=True, headers=None, raw=None, ctype=None):
        h = {}
        if cookie and self.cookie:
            h["Cookie"] = self.cookie
        data = b""
        if method == "POST":
            data = raw if raw is not None else json.dumps(body if body is not None else {}).encode()
            h["Content-Type"] = ctype or ("application/octet-stream" if raw is not None else "application/json")
            if origin:
                h["Origin"] = self.origin if origin is True else origin
            if csrf and self.csrf:
                h["X-FS-CSRF"] = self.csrf if csrf is True else csrf
        h.update(headers or {})
        return self.raw(method, path, data, h)

    def jreq(self, *a, **kw):
        st, hd, out = self.req(*a, **kw)
        try:
            return st, json.loads(out or b"{}")
        except ValueError:
            return st, {"_raw": out}


class HandoffHttpTest(StudioBase):
    def test_handoff_sets_dev_cookie_and_next(self):
        st, hd, j, ho = self.handoff("/editor?name=a.mp4")
        self.assertEqual(ho["url"], self.origin)
        self.assertEqual((st, j), (200, {"ok": True, "next": "/editor?name=a.mp4"}))
        c = self.hget(hd, "Set-Cookie")[0]
        self.assertRegex(c, r"^fs_s=[A-Za-z0-9_-]{43}; ")
        parts = c.split("; ")
        for a in ("Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={rstudio.ABSOLUTE}"):
            self.assertIn(a, parts)
        self.assertEqual(self.hget(hd, "Cache-Control"), ["private, no-store"])
        self.assertTrue(any("밖에서 작업 시작 · " + DEV in x for x in self.lines))
        studio_notes = lambda: [t for k, t in self.notes if k == "studio"]  # noqa: E731
        self.assertEqual(studio_notes(), [remote.NOTE_TEXT["studio"]])  # 새 세션 알림 (정해진 문장)
        self.handoff("/")
        self.assertEqual(len(studio_notes()), 1)  # 30분 안에 다시 열면 또 알리지 않음

    def test_token_single_use_and_counts_failure(self):
        st, j = self.signed_post("/r/handoff", {"path": "/"})
        self.assertEqual(self.post_handoff(j["token"])[0], 200)
        with mock.patch.object(self.svc.limiter, "failed", wraps=self.svc.limiter.failed) as f:
            st, _, out = self.post_handoff(j["token"])
            self.assertEqual(st, 403)
            self.assertEqual(json.loads(out)["code"], "handoff")
            self.assertEqual(f.call_count, 1)

    def test_token_bound_to_net_and_ttl(self):
        st, j = self.signed_post("/r/handoff", {"path": "/"}, ip="203.0.113.5")
        self.assertEqual(self.post_handoff(j["token"], ip="198.51.100.9")[0], 403)
        st, j = self.signed_post("/r/handoff", {"path": "/"}, ip="203.0.113.5")
        self.assertEqual(self.post_handoff(j["token"], ip="203.0.113.77")[0], 200)  # 같은 /24
        st, j = self.signed_post("/r/handoff", {"path": "/"})
        self.clock.tick(rstudio.HANDOFF_TTL + 1)
        self.assertEqual(self.post_handoff(j["token"])[0], 403)

    def test_handoff_checks(self):
        for bad in ("https://evil.com", "//evil.com", "/api/state", 5, None):
            st, j = self.signed_post("/r/handoff", {"path": bad} if bad is not None else {"path": None})
            self.assertEqual(st, 400, bad)
        st, j = self.signed_post("/r/handoff", {"path": "/"})
        tok = j["token"]
        self.assertEqual(self.post_handoff(tok, origin="https://evil.example")[0], 403)
        st, j = self.signed_post("/r/handoff", {"path": "/"})
        self.assertEqual(self.post_handoff(j["token"], extra={"Sec-Fetch-Site": "cross-site"})[0], 403)
        st, j = self.signed_post("/r/handoff", {"path": "/"})
        self.assertEqual(self.post_handoff(j["token"], extra={"Content-Type": "text/plain"})[0], 400)
        # 서명 없는 /r/handoff
        st, _, _ = self.raw("POST", "/r/handoff", b'{"path": "/"}', {"Origin": remote.ORIGINS[0], "Content-Type": "application/json"})
        self.assertEqual(st, 401)

    def test_handoff_rate(self):
        for _ in range(rstudio.HANDOFF_RATE[0]):
            self.assertEqual(self.signed_post("/r/handoff", {"path": "/"})[0], 200)
        self.assertEqual(self.signed_post("/r/handoff", {"path": "/"})[0], 429)

    def test_hello_public_static(self):
        st, hd, out = self.raw("GET", "/s/hello", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "document"})
        self.assertEqual(st, 200)
        csp = self.hget(hd, "Content-Security-Policy")[0]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe", csp)
        self.assertIn(b'<script src="/s/hello.js">', out)
        st, hd, js = self.raw("GET", "/s/hello.js")
        self.assertEqual(st, 200)
        self.assertIn("javascript", self.hget(hd, "Content-Type")[0])
        for secret in (self.cred["keys"]["auth"], self.svc.store.data["topics"]["beacon"]):
            self.assertNotIn(secret.encode(), out + js)

    def test_studio_off_blocks_handoff_and_sessions(self):
        self.login()
        self.svc.set_settings({"studio": False})
        st, j = self.jreq("GET", "/api/state")
        self.assertEqual((st, j["code"]), (403, "studio_off"))
        st, j = self.signed_post("/r/handoff", {"path": "/"})
        self.assertEqual((st, j["code"]), (403, "studio_off"))
        self.assertEqual(self.svc.studio.sessions.all(), [])
        self.svc.set_settings({"studio": True})
        st, j = self.jreq("GET", "/api/state")
        self.assertEqual((st, j["code"]), (401, "none"))  # 꺼질 때 세션도 끝 → 다시 연결
        st, hd, out = self.raw("GET", "/r/ping")
        self.assertEqual(json.loads(out)["features"], ["studio"])
        with self.assertRaises(ValueError):
            self.svc.set_settings({"studio": "yes"})

    def test_logout(self):
        self.login()
        st, hd, out = self.req("POST", "/s/logout")
        self.assertEqual(st, 200)
        self.assertIn("Max-Age=0", self.hget(hd, "Set-Cookie")[0])
        self.assertEqual(self.jreq("GET", "/api/state")[1]["code"], "none")


class GateHttpTest(StudioBase):
    def test_no_session_401_and_lock_page(self):
        st, j = self.jreq("GET", "/api/state")
        self.assertEqual((st, j["code"], j["site"]), (401, "none", self.svc.site()))
        st, hd, out = self.raw("GET", "/editor?name=x", headers={"Sec-Fetch-Dest": "document", "Accept": "text/html"})
        self.assertEqual(st, 401)
        self.assertIn(b'name="fs-lock" content="none"', out)
        self.assertIn("script-src 'self'", self.hget(hd, "Content-Security-Policy")[0])
        st, j = self.jreq("GET", "/api/state", headers={"Cookie": "fs_s=" + "A" * 43})
        self.assertEqual((st, j["code"]), (401, "none"))

    def test_session_page_and_hardening(self):
        self.login()
        st, hd, out = self.req("GET", "/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(st, 200)
        self.assertEqual(self.hget(hd, "Content-Encoding"), ["gzip"])
        html = out.decode("utf-8")
        nonce = re.search(r"'nonce-([A-Za-z0-9_-]+)'", self.hget(hd, "Content-Security-Policy")[0]).group(1)
        self.assertTrue(all(f'nonce="{nonce}"' in t for t in re.findall(r"<script\b[^>]*>", html)))
        self.assertIn(f'<meta name="fs-csrf" content="{self.csrf}">', html)
        for k, v in (("X-Frame-Options", "SAMEORIGIN"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                     ("Cross-Origin-Opener-Policy", "same-origin"), ("Cross-Origin-Resource-Policy", "same-origin"),
                     ("Cache-Control", "private, no-store")):
            self.assertEqual(self.hget(hd, k), [v], k)
        self.assertIn("Cookie", self.hget(hd, "Vary")[0])
        st, hd2, out2 = self.req("GET", "/", headers={"Accept-Encoding": "gzip"})
        self.assertNotEqual(re.search(r"'nonce-([^']+)'", self.hget(hd2, "Content-Security-Policy")[0]).group(1), nonce)  # 응답마다 새 nonce
        st, hd, js = self.req("GET", "/s/remote.js")
        self.assertEqual((st, self.hget(hd, "Cache-Control")), (200, [rstudio.STATIC_AGE]))
        st, hd, _ = self.req("GET", "/api/state")
        self.assertEqual(self.hget(hd, "Cache-Control"), ["private, no-store"])
        self.assertEqual(self.hget(hd, "X-Frame-Options"), ["DENY"])  # 화면이 아닌 응답은 틀에 못 넣음 (keep-alive 로 다음 요청에 남지 않음)
        self.assertFalse([k for k, _ in hd if k.lower().startswith("access-control-")])

    def test_state_gzip_and_scrub(self):
        self.login()
        with mock.patch.object(rstudio.Path, "home", return_value=self.work.parent):
            st, hd, out = self.req("GET", "/api/state", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(st, 200)
        j = json.loads(out)
        self.assertEqual(j["workspace"], "~/작업 폴더")
        self.assertEqual(j["remote"]["studio"][0]["device"], DEV)

    def test_post_needs_csrf_origin_and_same_origin(self):
        self.login()
        ok = self.jreq("POST", "/api/crash/dismiss", {})
        self.assertEqual(ok, (200, {"ok": True}))
        st, j = self.jreq("POST", "/api/crash/dismiss", {}, csrf=False)
        self.assertEqual((st, j["code"]), (403, "csrf"))
        st, j = self.jreq("POST", "/api/crash/dismiss", {}, csrf="x" * 43)
        self.assertEqual((st, j["code"]), (403, "csrf"))
        st, j = self.jreq("POST", "/api/crash/dismiss", {}, origin="https://evil.example")
        self.assertEqual((st, j["code"]), (403, "origin"))
        st, j = self.jreq("POST", "/api/crash/dismiss", {}, origin=False)
        self.assertEqual((st, j["code"]), (403, "origin"))
        st, j = self.jreq("POST", "/api/crash/dismiss", {}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual((st, j["code"]), (403, "cross"))
        st, j = self.jreq("GET", "/api/state", headers={"Sec-Fetch-Site": "same-site"})
        self.assertEqual(st, 403)
        st, j = self.jreq("POST", "/api/crash/dismiss", raw=b"{}", ctype="text/plain")
        self.assertEqual(st, 415)
        st, j = self.jreq("POST", "/api/crash/dismiss", raw=b"[1]", ctype="application/json")
        self.assertEqual(st, 400)

    def test_pc_only_and_unknown(self):
        self.login()
        for m, p in (("GET", "/api/remote"), ("GET", "/api/ping"), ("GET", "/api/browsers"), ("GET", "/api/update/check"),
                     ("POST", "/api/update"), ("POST", "/api/restart"), ("POST", "/api/focus"), ("POST", "/api/remote/off"),
                     ("POST", "/api/remote/session/revoke"), ("POST", "/api/claude/login"), ("POST", "/api/youtube/login"),
                     ("POST", "/api/youtube/client")):
            st, j = self.jreq(m, p, {} if m == "POST" else None)
            self.assertEqual((st, j.get("code")), (403, "pc_only"), p)
        self.assertEqual(self.jreq("POST", "/api/list", {"cookies": "firefox"})[1]["code"], "pc_only")
        self.assertEqual(self.jreq("GET", "/api/" + quote("새것"))[1]["code"], "not_remote")
        self.assertEqual(self.jreq("GET", "/nothing")[0], 404)
        for m in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
            self.assertIn(self.req(m, "/api/state")[0], (403, 405), m)

    def test_revoke_from_pc_and_device_unpair(self):
        self.login()
        sid = self.svc.studio.sessions.all()[0]["id"]
        self.assertEqual(self.svc.pc_status()["studio"]["sessions"][0]["device"], DEV)
        self.assertTrue(self.svc._awake_wanted())
        self.assertEqual(self.svc.studio_revoke([sid]), 1)
        st, j = self.jreq("GET", "/api/state")
        self.assertEqual((st, j["code"]), (401, "revoked"))
        st, hd, out = self.raw("GET", "/", headers={"Cookie": self.cookie, "Sec-Fetch-Dest": "document"})
        self.assertIn(b'content="revoked"', out)
        self.login()
        self.svc.revoke([self.cred["device"]["id"]])
        self.assertEqual(self.jreq("GET", "/api/state")[1]["code"], "revoked")

    def test_idle_expiry_and_url_change(self):
        self.login()
        self.clock.tick(rstudio.IDLE + 1)
        self.assertEqual(self.jreq("GET", "/api/state")[1]["code"], "expired")
        self.login()
        self.svc._tunnel_event(self.svc._gen, None, "on", url="http://127.0.0.1:1")  # 주소가 바뀜
        self.assertEqual(self.svc.studio.sessions.all(), [])

    def test_jobs_tagged_and_no_self_update(self):
        self.login()
        seen = {}

        def fake(b):
            seen["update"] = core.self_update_allowed()
            return {"ok": True}
        with mock.patch.object(self.app.Handler, "_analyze", staticmethod(fake)):
            st, j = self.jreq("POST", "/api/analyze", {"names": [NAME]})
            self.assertEqual(st, 200, j)
            self.wait_idle()
        done = self.app.DONE[j["jobId"]]
        self.assertEqual(done["by"], f"밖에서 · {DEV}")
        self.assertIs(seen["update"], False)
        self.assertTrue(any(x.startswith(f"원격 · {DEV} · 편집점 찾기") for x in self.lines))
        self.assertIsNone(getattr(self.app._REMOTE, "who", None))  # 요청이 끝나면 표시도 끝

    def test_autorun_behind_gate(self):
        """끝까지 자동으로 (D-200~D-207): 길은 모두 세션·CSRF 뒤 · 밖에서 시작한 작업은 '밖에서 · 기기' · 엔진 스스로 고치기 없음 ·
        썸네일 그리기 요청은 밖의 화면도 맡을 수 있음 (보이지 않는 틀 · 화면은 같은 출처 틀만 허용)."""
        import autorun
        self.addCleanup(lambda: autorun._RQ.update(want=None, claim=None, got={}))
        self.assertEqual(self.jreq("GET", "/api/auto/pending")[0], 401)  # 세션 없이
        self.login()
        st, j = self.jreq("GET", "/api/auto?name=" + quote(NAME))
        self.assertEqual(st, 200, j)
        self.assertIn("shortsMax", j)
        self.assertEqual(self.jreq("GET", "/api/auto/pending")[1], {"ok": True, "want": None})
        self.assertEqual(self.jreq("POST", "/api/auto/claim", {"id": "r1"}, csrf=False)[0], 403)
        self.assertEqual(self.jreq("POST", "/api/auto/claim", {"id": "r1"})[0], 409)  # 요청 없음
        with autorun._RQC:
            autorun._RQ.update(want={"id": "r1", "name": NAME, "fmt": "long"}, claim=None, got={})
        self.assertEqual(self.jreq("GET", "/api/auto/pending")[1]["want"], {"id": "r1"})
        st, j = self.jreq("POST", "/api/auto/claim", {"id": "r1"})
        self.assertEqual((st, j["name"], j["fmt"]), (200, NAME, "long"), j)
        self.assertTrue(self.jreq("POST", "/api/auto/release", {"id": "r1"})[1]["ok"])
        # 보이지 않는 썸네일 틀이 여는 화면: 같은 출처 틀만
        st, hd, _ = self.req("GET", "/thumb?name=" + quote(NAME) + "&autothumb=r1&fmt=long")
        self.assertEqual(st, 200)
        self.assertEqual(self.hget(hd, "X-Frame-Options"), ["SAMEORIGIN"])
        self.assertIn("frame-ancestors 'self'", self.hget(hd, "Content-Security-Policy")[0])
        seen = {}

        def fake_run(n, opts, log, analyze, resume):
            seen.update(name=n, resume=resume, update=core.self_update_allowed())
            return {"status": "done"}
        with mock.patch.object(autorun, "run", fake_run):
            st, j = self.jreq("POST", "/api/auto", {"name": NAME, "resume": True})
            self.assertEqual(st, 200, j)
            self.wait_idle()
        self.assertEqual((seen["name"], seen["resume"], seen["update"]), (NAME, True, False))
        self.assertEqual(self.app.DONE[j["jobId"]]["by"], f"밖에서 · {DEV}")
        self.assertTrue(any(x.startswith(f"원격 · {DEV} · 끝까지 자동으로") for x in self.lines), self.lines[-5:])

    def test_youtube_confirm_428(self):
        self.login()
        plan = {"problems": [], "kit": {"title": "퍼스트 터치 꿀팁"}, "codes": []}
        body = {"name": NAME, "seq": None, "opts": {"privacy": "unlisted"}}
        with mock.patch.object(youtube_upload, "plan", return_value=plan), \
                mock.patch.object(youtube_upload, "run_upload", return_value={"ok": True}) as run, \
                mock.patch("upload.files_for", return_value={"video": Path("완성본.mp4")}):
            st, j = self.jreq("POST", "/api/youtube/upload", body)
            self.assertEqual(st, 428)
            text, tok = j["confirm"]["text"], j["confirm"]["token"]
            for want in ("퍼스트 터치 꿀팁", "일부 공개", "완성본.mp4"):
                self.assertIn(want, text)
            self.assertEqual(self.jreq("POST", "/api/youtube/upload", dict(body, again=True), headers={"X-FS-Confirm": tok})[0], 428)  # 다른 본문
            st, j = self.jreq("POST", "/api/youtube/upload", body)
            tok = j["confirm"]["token"]
            st, j = self.jreq("POST", "/api/youtube/upload", body, headers={"X-FS-Confirm": tok})
            self.assertEqual(st, 200, j)
            self.wait_idle()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(self.jreq("POST", "/api/youtube/upload", body, headers={"X-FS-Confirm": tok})[0], 428)  # 한 번뿐

    def test_adapt_open(self):
        self.login()
        self.assertEqual(self.jreq("POST", "/api/open", {"which": "out"})[1], {"ok": True, "remote": "outputs"})
        self.assertEqual(self.jreq("POST", "/api/open", {"which": "videos"})[1], {"ok": True, "remote": "upload"})
        self.assertFalse(self.jreq("POST", "/api/open", {"which": "work"})[1]["ok"])
        self.assertEqual(self.jreq("POST", "/api/upload/open", {"what": "studio"})[1]["openUrl"], "https://studio.youtube.com")
        (self.out / "키트_올리기.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.jreq("POST", "/api/upload/open", {"what": "file", "file": "키트_올리기.txt"})[1]["download"],
                         "/s/dl?f=%ED%82%A4%ED%8A%B8_%EC%98%AC%EB%A6%AC%EA%B8%B0.txt")
        self.assertEqual(self.jreq("POST", "/api/upload/open", {"what": "file", "file": "../x.txt"})[0], 400)
        self.assertEqual(self.jreq("POST", "/api/upload/open", {"what": "template"})[1]["code"], "pc_only")
        self.assertEqual(self.jreq("POST", "/api/youtube/open", {"what": "watch", "videoId": "abcdefghijk"})[1]["openUrl"],
                         "https://youtu.be/abcdefghijk")
        self.assertEqual(self.jreq("POST", "/api/youtube/open", {"what": "watch", "videoId": "../evil"})[0], 400)

    def test_media_proxy_only(self):
        self.login()
        st, j = self.jreq("GET", "/media?name=" + quote(NAME))
        self.assertEqual((st, j["code"]), (403, "need_proxy"))
        st, j = self.jreq("GET", "/api/edit/file?src=videos&f=" + quote(NAME))
        self.assertEqual((st, j["code"]), (403, "need_proxy"))
        pp = editor.proxy_path(NAME)
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_bytes(b"P" * 1000)
        st, hd, out = self.req("GET", "/media?name=" + quote(NAME), headers={"Range": "bytes=0-9"})
        self.assertEqual((st, out), (206, b"P" * 10))
        self.assertEqual(self.hget(hd, "Cache-Control"), ["private, no-store"])
        st, hd, out = self.req("GET", "/api/edit/file?src=videos&f=" + quote(NAME))
        self.assertEqual((st, out[:3]), (200, b"PPP"))  # 원본 대신 미리보기 파일
        self.assertEqual(self.jreq("POST", "/s/prefs", {"fullRes": True})[1]["prefs"], {"fullRes": True})
        st, hd, out = self.req("GET", "/media?name=" + quote(NAME))
        self.assertEqual((st, out[:3]), (200, b"\x00\x00\x00"))  # 원본 화질을 켜면 원본
        (self.work / "edit_media" / "a.png").write_bytes(b"\x89PNG")
        self.assertEqual(self.req("GET", "/api/edit/file?src=assets&f=a.png")[0], 200)  # 그림은 그대로


def quote(s):
    from urllib.parse import quote as q
    return q(s, safe="")


if __name__ == "__main__":
    unittest.main()
