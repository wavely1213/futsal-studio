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


class YoutubeOnlyUnitTest(unittest.TestCase):
    """밖에서 온 YouTube 받기·불러오기 주소 (D-235 · SSRF): YouTube 표준 주소로만 · 그 밖은 ValueError."""

    GOOD = {"": "", "@futsal_school": "https://www.youtube.com/@futsal_school", "futsal_school": "https://www.youtube.com/@futsal_school",
            "youtube.com/@futsal/videos": "https://www.youtube.com/@futsal", "https://m.youtube.com/@futsal/shorts": "https://www.youtube.com/@futsal",
            "https://www.youtube.com/channel/UCRYziLOw2T6BF6fXtpUby-g": "https://www.youtube.com/channel/UCRYziLOw2T6BF6fXtpUby-g",
            "https://youtu.be/AbCdEfGhIjK?si=x": "https://www.youtube.com/watch?v=AbCdEfGhIjK",
            "https://www.youtube.com/watch?v=AbCdEfGhIjK&list=PLx": "https://www.youtube.com/watch?v=AbCdEfGhIjK",
            "https://www.youtube.com/shorts/AbCdEfGhIjK": "https://www.youtube.com/watch?v=AbCdEfGhIjK",
            "https://www.youtube.com/playlist?list=PLabc_DEF-123": "https://www.youtube.com/playlist?list=PLabc_DEF-123",
            "youtube.com/playlist?list=PLabc": "https://www.youtube.com/playlist?list=PLabc",
            "169.254.169.254": "https://www.youtube.com/@169.254.169.254"}  # 점이 든 글은 핸들 (YouTube 로만 감)
    BAD = ("http://169.254.169.254/latest/meta-data/", "http://169.254.169.254/playlist", "http://10.0.0.1/playlist?list=PLx",
           "https://evil.example/playlist?list=PLx", "http://127.0.0.1:9497/playlist?probe=list", "https://www.youtube.com.evil.example/@x",
           "https://user@www.youtube.com/playlist?list=PLx", "https://www.youtube.com:8443/playlist?list=PLx", "file:///etc/passwd",
           "ftp://youtube.com/@x", "localhost:8080", "https://www.youtube.com/playlist?list=", "https://www.youtube.com/playlist?list=a/b",
           "http://[::1]/playlist", "https://youtube.com/redirect?q=http://10.0.0.1", "x" * 301)

    def test_listing_url(self):
        for raw, want in self.GOOD.items():
            self.assertEqual(remote.listing_url(raw), want, raw)
        for raw in self.BAD:
            with self.assertRaises(ValueError, msg=raw):
                remote.listing_url(raw)
        for raw in self.GOOD:
            out = remote.listing_url(raw)
            self.assertTrue(out == "" or out.startswith("https://www.youtube.com/"), raw)

    def test_yt_guard(self):
        svc = remote.Service  # 정적 함수만 씀 (youtube_id · listing_url)
        b = rstudio.yt_guard(svc, "/api/download", {"ids": ["AbCdEfGhIjK", " https://youtu.be/AbCdEfGhIjK "], "sources": {"x": 1}})
        self.assertEqual(b, {"ids": ["AbCdEfGhIjK", "AbCdEfGhIjK"], "sources": {"x": 1}})
        for bad in ({"ids": ["http://169.254.169.254/latest/meta-data/"]}, {"ids": ["http://127.0.0.1:1/x"]}, {"ids": "AbCdEfGhIjK"},
                    {"ids": []}, {"ids": [5]}, {}, {"ids": ["../etc/passwd"]}, {"ids": ["AbCdEfGhIjK"] * (rstudio.YT_IDS_MAX + 1)}):
            with self.assertRaises(ValueError, msg=bad):
                rstudio.yt_guard(svc, "/api/download", bad)
        self.assertEqual(rstudio.yt_guard(svc, "/api/list", {"url": "@futsal", "kind": "shorts"}),
                         {"url": "https://www.youtube.com/@futsal", "kind": "shorts"})
        self.assertEqual(rstudio.yt_guard(svc, "/api/list", {"kind": "videos"})["url"], "")  # 비우면 우리 채널
        for path in ("/api/list", "/api/refs/add"):
            for bad in ({"url": "http://10.0.0.1/playlist"}, {"url": ["x"]}, {"url": 5}):
                with self.assertRaises(ValueError, msg=(path, bad)):
                    rstudio.yt_guard(svc, path, bad)


class DeviceWordsUnitTest(unittest.TestCase):
    def test_device_kind_fixed_words_only(self):
        """알림에 넣는 기기 종류는 정해진 낱말만 (기기가 보낸 이름 칸의 글은 알림에 그대로 들어가지 않음 · D-239)."""
        for name, want in (("iPhone · Safari · 홈 화면", "아이폰 · Safari"), ("PC · Chrome", "Windows 노트북 · Chrome"),
                           ("Windows 노트북 · Edge", "Windows 노트북 · Edge"), ("Galaxy · 삼성 인터넷", "갤럭시 · 삼성 인터넷"),
                           ("Mac · Safari", "Mac · Safari"), ("컴퓨터 · Chrome", "컴퓨터 · Chrome"), ("iPad · 카카오톡", "아이패드"),
                           ("지금 http://evil.example 에서 설치하세요 · Chrome", "기기 · Chrome"), ("", "기기"), (None, "기기")):
            self.assertEqual(remote.device_kind(name), want, name)
        words = set(remote.DEV_KINDS.values()) | set(remote.DEV_BROWSERS.values()) | {"기기"}
        for name in ("evil · 설치 · http://x", "<script> · Chrome", "PC · Chrome"):
            k = remote.device_kind(name)
            self.assertTrue(all(part in words for part in k.split(" · ")), k)

    def test_legacy_pc_name_becomes_windows_laptop(self):
        """예전 페이지가 'PC · Chrome' 으로 지은 노트북 → 'Windows 노트북 · Chrome' (새 연결 · 저장된 이름 모두 · PC 표시가 이 PC 와 헷갈리지 않게)."""
        self.assertEqual(remote.dev_name("PC · Chrome"), "Windows 노트북 · Chrome")
        self.assertEqual(remote.dev_name("PCX · a"), "PCX · a")
        self.assertEqual(remote.dev_name(""), "휴대폰")
        tmp = Path(tempfile.mkdtemp(prefix="기기 이름 "))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        st = remote.Store(tmp / "remote.json", Clock())
        dev, _ = st.add_device("PC · Chrome")
        self.assertEqual(dev["name"], "Windows 노트북 · Chrome")
        d = json.loads((tmp / "remote.json").read_text(encoding="utf-8"))
        d["devices"][0]["name"] = "PC · Edge"  # 예전 판이 저장한 이름
        (tmp / "remote.json").write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(remote.Store(tmp / "remote.json", Clock()).data["devices"][0]["name"], "Windows 노트북 · Edge")

    def test_alert_says_what_to_do_from_outside(self):
        """새 기기·새 밖에서 작업 알림: 밖에서 할 수 있는 것(원격 홈 [더보기] → [실행기 끄기])과 PC 의 [끊기] · 기기 종류."""
        for kind in ("studio", "paired"):
            t = remote.note_text(kind, "iPhone · Safari")
            self.assertIn("(아이폰 · Safari)", t)
            self.assertIn("내가 한 게 아니면", t)
            self.assertIn("[더보기] → [실행기 끄기]", t)
            self.assertIn("[끊기]", t)
            self.assertNotIn("{", t)
            self.assertIn("기기", remote.note_text(kind, "evil http://x"))
            self.assertNotIn("evil", remote.note_text(kind, "evil http://x"))
        pwa = Path(os.environ.get("FUTSAL_SITE_DIR") or "/nonexistent") / "app.js"
        if pwa.is_file():  # 그 단추 이름이 원격 홈에 그대로 있는지
            self.assertIn('"실행기 끄기")', pwa.read_text(encoding="utf-8"))


class AwakeUnitTest(unittest.TestCase):
    """PC 잠들지 않게 (D-238): 켜 둔 원격이 터널 오류 뒤 다시 켜 보는 사이에도 · 살아 있는 밖에서 작업 세션이 있으면 (2분 넘게 조용해도)."""

    def make(self, state, enabled=True, keep="always", err="tunnel", at=100.0, job=None, sessions=0, quiet=0):
        svc = remote.Service.__new__(remote.Service)
        svc.state, svc._err_kind, svc._retry = state, err, {"n": 1, "at": at}
        svc.store = mock.Mock()
        svc.store.data = {"enabled": enabled, "settings": {"keepAwake": keep}}
        svc.bridge = mock.Mock()
        svc.bridge.job.return_value = {"name": job}
        clock = Clock(1000.0)
        svc.studio = mock.Mock()
        svc.studio.sessions = rstudio.Sessions(clock)
        for i in range(sessions):
            svc.studio.sessions.create({"id": f"d{i}", "name": "iPhone · Safari"}, "n")
        clock.tick(quiet)
        return svc

    def test_retrying_keeps_awake(self):
        self.assertTrue(self.make("on")._awake_wanted())
        self.assertTrue(self.make("restarting")._awake_wanted())
        self.assertTrue(self.make("error")._awake_wanted())           # 다음 다시 켜기가 잡혀 있음
        self.assertTrue(self.make("starting")._awake_wanted())        # 다시 켜 보는 중
        self.assertTrue(self.make("preparing")._awake_wanted())
        self.assertTrue(self.make("error", err="start")._awake_wanted())
        self.assertFalse(self.make("error", enabled=False)._awake_wanted())  # 꺼 둔 원격
        self.assertFalse(self.make("error", at=None)._awake_wanted())        # 다시 켜기 없음
        self.assertFalse(self.make("error", err="crypto")._awake_wanted())   # 고칠 수 없는 오류
        self.assertFalse(self.make("off")._awake_wanted())
        self.assertFalse(self.make("starting", enabled=False)._awake_wanted())

    def test_job_or_sessions_when_keep_is_job(self):
        self.assertFalse(self.make("on", keep="job")._awake_wanted())
        self.assertTrue(self.make("on", keep="job", job="내보내기")._awake_wanted())
        self.assertTrue(self.make("error", keep="job", job="내보내기")._awake_wanted())
        self.assertTrue(self.make("on", keep="job", sessions=1, quiet=rstudio.ACTIVE_SECS + 300)._awake_wanted())  # 조용해도 세션은 살아 있음
        self.assertFalse(self.make("on", keep="job", sessions=1, quiet=rstudio.IDLE + 5)._awake_wanted())   # 30분 지나면 세션도 끝


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
        self.assertEqual(studio_notes(), [remote.note_text("studio", DEV)])  # 새 세션 알림 (정해진 문장 + 정해진 낱말의 기기 종류)
        self.assertIn("(아이폰 · Safari)", studio_notes()[0])
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
        self.assertIn(f'<meta name="fs-site" content="{self.svc.site()}">'.encode(), out)  # 표가 없어도 원격 홈으로 가는 길·주소 (D-236)
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
        """연결 끝내기: 'logout' 으로 끝 · 쿠키는 그대로(죽은 표) → 같은 브라우저의 다른 탭도 'logout' 을 받아 저절로 다시 연결하지 않음 (D-236)."""
        self.login()
        st, hd, out = self.req("POST", "/s/logout")
        self.assertEqual(st, 200)
        self.assertEqual(self.hget(hd, "Set-Cookie"), [])
        self.assertEqual(self.svc.studio.sessions.all(), [])
        st, j = self.jreq("GET", "/api/state")  # 다른 탭 (같은 쿠키)
        self.assertEqual((st, j["code"]), (401, "logout"))
        self.assertIn("연결을 끝냈어요", j["error"])
        st, hd, out = self.raw("GET", "/editor?name=x", headers={"Cookie": self.cookie, "Sec-Fetch-Dest": "document"})
        self.assertEqual(st, 401)
        self.assertIn(b'name="fs-lock" content="logout"', out)
        self.assertIn("연결을 끝냈어요".encode(), out)
        js = (ROOT / "hello.js").read_text(encoding="utf-8") + (ROOT / "remote_ui.js").read_text(encoding="utf-8")
        self.assertIn('code === "none" || code === "expired"', js)  # 저절로 다시 연결하는 것은 끊김·시간 지남뿐
        self.assertIn("logout:", js)
        self.assertTrue(any("밖에서 작업 끝냄" in x and "밖에서 연결 끝냄" in x for x in self.lines))


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
        self.assertIs(j["remote"]["studio"][0]["active"], True)
        # 2분 넘게 조용해도 세션이 살아 있는 동안은 PC 표시에 남음 ('밖에서 연결됨 · N분 전' · D-238)
        self.clock.tick(rstudio.ACTIVE_SECS + 60)
        b = self.svc.brief()["studio"]
        self.assertEqual([(x["device"], x["active"]) for x in b], [(DEV, False)])
        self.assertLessEqual(b[0]["lastSeen"], self.clock() - rstudio.ACTIVE_SECS)

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

    def test_youtube_only_no_ssrf(self):
        """밖에서 온 받기·채널 불러오기·학습용 받기 주소는 YouTube 만 (D-235 · SSRF): 내부망·다른 사이트 주소는 400 yt_only 이고
        PC 가 그곳에 요청하지 않음(작업도 안 시작) · YouTube 주소는 표준 주소·영상 id 로 바꿔 앱에 넘김."""
        import http.server
        import threading
        hits = []

        class Sink(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        sink = f"http://127.0.0.1:{srv.server_address[1]}"
        self.login()
        jobs0 = self.app.JOB["id"]
        for path, body in (("/api/download", {"ids": [sink + "/meta?probe=dl"]}),
                           ("/api/download", {"ids": ["AbCdEfGhIjK", "http://169.254.169.254/latest/meta-data/"]}),
                           ("/api/list", {"url": sink + "/playlist?probe=list"}), ("/api/list", {"url": "http://10.0.0.1/playlist"}),
                           ("/api/refs/add", {"url": sink + "/playlist?probe=refs"}), ("/api/refs/add", {"url": "https://evil.example/@x"})):
            st, j = self.jreq("POST", path, body)
            self.assertEqual((st, j.get("code")), (400, "yt_only"), (path, body, j))
            self.assertIn("YouTube", j["error"])
        time.sleep(0.8)
        self.assertEqual(hits, [])
        self.assertEqual(self.app.JOB["id"], jobs0)  # 작업이 시작되지 않음
        seen = {}

        def lv(kind, ck, url, log):
            seen.setdefault("list", []).append(url)
            return []

        def dl(ids, log, ck, why=None):
            seen["dl"] = list(ids)
            return []
        with mock.patch.object(core, "list_videos", lv), mock.patch.object(core, "download", dl):
            for path, body in (("/api/download", {"ids": ["AbCdEfGhIjK", "https://youtu.be/ZyXwVuTsRqP?si=1"]}),
                               ("/api/list", {"url": "youtube.com/@futsal/videos", "kind": "videos"}), ("/api/list", {"url": ""}),
                               ("/api/refs/add", {"url": "https://www.youtube.com/playlist?list=PLx1", "learn": False})):
                st, j = self.jreq("POST", path, body)
                self.assertEqual(st, 200, (path, j))
                self.wait_idle()
        self.assertEqual(seen["dl"], ["AbCdEfGhIjK", "ZyXwVuTsRqP"])
        self.assertEqual(seen["list"], ["https://www.youtube.com/@futsal", "", "https://www.youtube.com/playlist?list=PLx1"])
        self.assertEqual(hits, [])

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

    def test_media_lite_sound_image(self):
        """밖에서 작업: 배경음악·효과음(WAV)과 큰 그림은 화면이 lite=… 로 달라고 하면 PC 가 만든 가벼운 파일 (D-363 · D-366) ·
        영상 파일은 lite 를 붙여도 미리보기 파일 길(원본을 흘리지 않음) · 원본 화질·lite 없음은 원본 그대로 · 늘 private, no-store."""
        self.login()
        wav = self.work / "edit_media" / "배경음악_시험.wav"
        r = core.run([core.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=4:r=48000", "-ac", "2", "-c:a", "pcm_s16le", str(wav)])
        self.assertEqual(r.returncode, 0, r.stderr)
        u = "/api/edit/file?src=assets&f=" + quote(wav.name)
        for kind in ("opus", "aac"):
            st, hd, out = self.req("GET", u + "&lite=" + kind, headers={"Range": "bytes=0-"})
            self.assertEqual(st, 206, kind)
            self.assertEqual(self.hget(hd, "Content-Type"), ["audio/mp4"])
            self.assertEqual(self.hget(hd, "Cache-Control"), ["private, no-store"])
            self.assertEqual(out[4:8], b"ftyp")
            self.assertLess(len(out), wav.stat().st_size / 5)
            self.assertTrue(editor.lite_path(wav.name, "assets", kind).is_file())
        st, hd, out = self.req("GET", u)  # lite 없음(PC 화면과 같은 길) → 원본 WAV
        self.assertEqual((st, out[:4]), (200, b"RIFF"))
        st, j = self.jreq("GET", "/api/edit/file?src=videos&f=" + quote(NAME) + "&lite=opus")
        self.assertEqual((st, j["code"]), (403, "need_proxy"))  # 영상 원본은 lite 로도 안 나감
        from PIL import Image
        import random
        rnd = random.Random(7)
        im = Image.frombytes("RGB", (700, 700), bytes(rnd.getrandbits(8) for _ in range(700 * 700 * 3)))
        im.save(self.work / "edit_media" / "정지_큰.png")
        Image.new("RGB", (64, 64), (10, 20, 30)).save(self.work / "edit_media" / "작은.png")
        st, hd, out = self.req("GET", "/api/edit/file?src=assets&f=" + quote("정지_큰.png") + "&lite=webp")
        self.assertEqual((st, out[:4], out[8:12]), (200, b"RIFF", b"WEBP"))
        self.assertEqual(self.hget(hd, "Content-Type"), ["image/webp"])
        st, hd, out = self.req("GET", "/api/edit/file?src=assets&f=" + quote("작은.png") + "&lite=webp")
        self.assertEqual((st, out[:4]), (200, b"\x89PNG"))  # 작은 그림은 원본 그대로
        st, hd, out = self.req("GET", u + "&lite=evil")
        self.assertEqual((st, out[:4]), (200, b"RIFF"))  # 모르는 종류는 무시

    def test_media_range_edges(self):
        """구간 요청 끝 쪽: 받을 수 없는 구간은 416 + 'bytes */크기' · 여러 구간은 무시하고 전체 (예전엔 길이 0·음수 206 이나 답 없음 · I-363)."""
        self.login()
        pp = editor.proxy_path(NAME)
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_bytes(b"P" * 1000)
        u = "/media?name=" + quote(NAME)
        for rng in ("bytes=1000-", "bytes=5000-6000", "bytes=-0", "bytes=50-10"):
            st, hd, out = self.req("GET", u, headers={"Range": rng})
            self.assertEqual((st, self.hget(hd, "Content-Range"), out), (416, ["bytes */1000"], b""), rng)
        st, hd, out = self.req("GET", u, headers={"Range": "bytes=0-1,5-6"})
        self.assertEqual((st, len(out)), (200, 1000))
        st, hd, out = self.req("GET", u, headers={"Range": "bytes=-10"})
        self.assertEqual((st, self.hget(hd, "Content-Range"), out), (206, ["bytes 990-999/1000"], b"P" * 10))
        st, hd, out = self.req("GET", u, headers={"Range": "bytes=990-5000"})
        self.assertEqual((st, len(out)), (206, 10))

    def test_media_send_timeout(self):
        """미디어를 보내는 동안은 소켓 기다림 120초 (요청을 읽는 20초에 끊겨 8MB 중 3~4MB 만 가지 않게 · D-368) · 자리는 꼭 돌려줌."""
        seen = []
        h = mock.Mock()
        h.connection.settimeout.side_effect = seen.append
        sess = {"device": "d1"}
        self.assertEqual(rstudio._media_file(h, self.svc, sess, lambda: "sent"), "sent")
        self.assertEqual(seen, [rstudio.MEDIA_SEND_TIMEOUT])
        self.assertEqual(self.svc.limiter.media["d1"], 0)

    def test_fonts_gzip_private(self):
        """글꼴은 gzip 으로 (자막 글꼴 4MB → 2.4MB · 처음 열 때 첫 소리와 망을 덜 다툼 · D-366) · 7일 보관은 그대로 private."""
        self.login()
        f = editor.FONTS / "Pretendard-Bold.otf"
        st, hd, out = self.req("GET", "/fonts/Pretendard-Bold.otf", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(st, 200)
        self.assertEqual(self.hget(hd, "Content-Encoding"), ["gzip"])
        self.assertEqual(self.hget(hd, "Cache-Control"), [rstudio.STATIC_AGE])
        self.assertIn("Accept-Encoding", self.hget(hd, "Vary")[0])
        self.assertLess(int(self.hget(hd, "Content-Length")[0]), f.stat().st_size)
        self.assertEqual(out, f.read_bytes())  # raw() 가 풀어 줌
        st, hd, out = self.req("GET", "/fonts/Pretendard-Bold.otf")  # gzip 을 모르는 곳은 그대로
        self.assertEqual((st, self.hget(hd, "Content-Encoding"), len(out)), (200, [], f.stat().st_size))
        self.assertEqual(self.req("GET", "/fonts/nope.otf", headers={"Accept-Encoding": "gzip"})[0], 404)


def quote(s):
    from urllib.parse import quote as q
    return q(s, safe="")


class GuestStudioTest(StudioBase):
    """'이번만' 기기 (아이디·비밀번호로 연결 · D-256): 세션 쿠키(Max-Age 없음) · 그 기기가 끝나는 때를 넘지 않음 · 나가기."""

    def guest(self, exp_in=None):
        g, _ = self.svc.store.add_guest("Windows 노트북 · Chrome")
        if exp_in is not None:
            g["exp"] = int(self.clock() + exp_in)
        return g, {"device": {"id": g["id"], "name": g["name"]}, "keys": {"auth": g["auth"], "beacon": g["beacon"]}, "host": self.svc.host()}

    def guest_login(self, gcred):
        self.cred, keep = gcred, self.cred
        try:
            st, hd, j, _ = self.handoff("/")
        finally:
            self.cred = keep
        self.assertEqual(st, 200, j)
        return hd

    def test_guest_cookie_no_max_age(self):
        _, gc = self.guest()
        c = self.hget(self.guest_login(gc), "Set-Cookie")[0]
        self.assertNotIn("Max-Age", c)
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Strict", c)

    def test_remembered_cookie_max_age(self):
        st, hd, j, _ = self.handoff("/")
        self.assertIn(f"Max-Age={rstudio.ABSOLUTE}", self.hget(hd, "Set-Cookie")[0])

    def test_hardend_caps_session(self):
        g, gc = self.guest(exp_in=600)
        hd = self.guest_login(gc)
        self.cookie = self.hget(hd, "Set-Cookie")[0].split(";")[0]
        st, j = self.jreq("GET", "/s/session")
        self.assertEqual(st, 200)
        self.assertEqual(j["expiresAt"], g["exp"])
        self.clock.tick(601)
        st, j = self.jreq("GET", "/s/session")
        self.assertEqual((st, j["code"]), (401, "expired"))

    def test_session_view_guest(self):
        _, gc = self.guest()
        hd = self.guest_login(gc)
        self.cookie = self.hget(hd, "Set-Cookie")[0].split(";")[0]
        st, j = self.jreq("GET", "/s/session")
        self.assertTrue(j["guest"])
        self.cookie = None
        self.login()
        st, j = self.jreq("GET", "/s/session")
        self.assertFalse(j["guest"])

    def test_guest_logout_drops_device(self):
        g, gc = self.guest()
        hd = self.guest_login(gc)
        self.cookie = self.hget(hd, "Set-Cookie")[0].split(";")[0]
        st, j = self.jreq("GET", "/s/session")
        self.csrf = j["csrf"]
        st, j = self.jreq("POST", "/s/logout", {})
        self.assertEqual(st, 200)
        self.assertIsNone(self.svc.store.device(g["id"]))
        st, j = self.jreq("GET", "/s/session")
        self.assertEqual((st, j["code"]), (401, "logout"))
        self.assertIsNotNone(self.svc.store.device(self.cred["device"]["id"]))   # QR 기기는 그대로

    def test_guest_lock_texts_say_relogin_not_open_studio(self):
        """검토 고침: 이번만 기기는 원격 홈에 [PC 스튜디오 열기]가 남지 않음 → 끝난 뒤 문장은 '아이디·비밀번호로 다시 연결' (JSON·잠금 쪽·스크립트)."""
        relogin = "아이디·비밀번호로 다시 연결해 주세요"
        g, gc = self.guest()
        self.cookie = self.hget(self.guest_login(gc), "Set-Cookie")[0].split(";")[0]
        st, j = self.jreq("GET", "/s/session")
        self.csrf = j["csrf"]
        self.assertEqual(self.jreq("POST", "/s/logout", {})[0], 200)
        st, j = self.jreq("GET", "/api/state")
        self.assertEqual((st, j["code"], j.get("guest")), (401, "logout", True))
        self.assertIn(relogin, j["error"])
        st, hd, out = self.raw("GET", "/editor?name=x", headers={"Cookie": self.cookie, "Sec-Fetch-Dest": "document"})
        self.assertEqual(st, 401)
        self.assertIn(b'name="fs-guest" content="1"', out)
        self.assertIn(relogin.encode(), out)
        self.assertNotIn("[PC 스튜디오 열기]".encode(), out)
        # PC 가 이번만 기기를 버림 (주소 바뀜·비밀번호 바꿈·끊기) → 'revoked' 도 같은 문장
        g2, gc2 = self.guest()
        self.cookie = self.hget(self.guest_login(gc2), "Set-Cookie")[0].split(";")[0]
        self.svc._drop_guests({g2["id"]}, "revoked", "시험")
        st, j = self.jreq("GET", "/api/state")
        self.assertEqual((st, j["code"], j.get("guest")), (401, "revoked", True))
        self.assertIn(relogin, j["error"])
        # QR 기기의 연결 끝내기는 예전 문장 그대로 (guest 칸 없음)
        self.cookie = self.csrf = None
        self.login()
        self.assertEqual(self.jreq("POST", "/s/logout", {})[0], 200)
        st, j = self.jreq("GET", "/api/state")
        self.assertEqual((st, j["code"]), (401, "logout"))
        self.assertNotIn("guest", j)
        self.assertNotIn(relogin, j["error"])
        js = (ROOT / "remote_ui.js").read_text(encoding="utf-8")
        hello = (ROOT / "hello.js").read_text(encoding="utf-8")
        self.assertIn("LOCK_GUEST", js)
        self.assertIn("(R.guest || guest) && LOCK_GUEST[code]", js)
        self.assertIn('meta("fs-guest") === "1" && TEXT_GUEST[key]', hello)
        for src in (js, hello):
            self.assertIn(relogin, src)
        self.assertIn('getElementById("fsr-logout")', js)   # 이번만 기기: [연결 끝내기] 를 뺌 ([이 기기에서 나가기] 와 같은 일)
        self.assertIn("if (o) o.remove();", js)


if __name__ == "__main__":
    unittest.main()
