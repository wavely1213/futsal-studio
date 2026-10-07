"""원격 리스너 HTTP(remote.RemoteHandler) + app 연결 시험 — 저장소 폴더에서 python3 -m unittest tests.test_remote_api

개발 모드(FUTSAL_REMOTE_DEV=1: Host 가 127.0.0.1:<포트> 여도 받음 · cloudflared 없이)로 진짜 리스너를 띄워 요청한다.
진짜 ffmpeg 로 4초짜리 시험 영상·완성본을 만들고, 받기·학습용 받기(YouTube)는 가짜로 바꾼다.
확인: 확인 순서(Host → 메서드 → 수 제한 → CORS/JSON → 서명/표 → 허용 목록) · 상태·목록 모양과 경로 감추기 · 허용 동작 전부와 409 ·
주소·이름 검사 · 확인 표 · 구간 요청 · 표 만료·끊김 · 로컬 서버(8765)는 그대로(터널 Host 403·CORS 없음) · 기록에 비밀 없음.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import refs  # noqa: E402
import remote  # noqa: E402
import style  # noqa: E402
from remote_fixture import ORIGIN, Clock, FakeBridge, dev_env, http, pair_device, signed  # noqa: E402

FF = core.ffmpeg()
NAME = "20260101_AbCdEfGhIjK_시험 영상 [꿀팁].mp4"


def make_video(path, dur=4, size="320x240"):
    r = core.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={size}:r=15:d={dur}",
                  "-f", "lavfi", "-i", f"sine=f=440:d={dur}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-shortest", str(path)])
    assert r.returncode == 0, r.stderr[-400:]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = Path(tempfile.mkdtemp(prefix="원격 미디어 "))
        make_video(cls.media / "v.mp4")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.media, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="원격 API "))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        for sub in ("projects", "styles", "refs"):
            (work / sub).mkdir()
        patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [
            mock.patch.object(editor, "PROJECTS", work / "projects"), mock.patch.object(style, "STYLES", work / "styles"),
            mock.patch.object(core, "ENGINE_HOME", self.tmp / ".futsal-studio"), mock.patch.dict(os.environ, dev_env())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.work, self.out = work, dirs["OUT"]
        shutil.copy(self.media / "v.mp4", dirs["VIDEOS"] / NAME)
        self.clock = Clock(time.time())
        self.fb = FakeBridge()
        self.svc = remote.Service(home=self.tmp / ".futsal-studio", clock=self.clock, ntfy="http://127.0.0.1:1")
        self.svc.init(self.fb.bridge())
        self.fb.hooks.append(self.svc.job_hook)
        self.assertTrue(self.svc.turn_on())
        end = time.time() + 10
        while self.svc.state != "on" and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(self.svc.state, "on")
        self.addCleanup(self.svc.turn_off)
        self.port = self.svc.port()
        self.cred = pair_device(self.svc, "iPhone · Safari")

    # 요청 도우미
    def call(self, method, path, body=None, cred=None, origin=ORIGIN, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        h = {"Origin": origin} if origin else {}
        if cred is not False:
            h["Authorization"] = signed(cred or self.cred, method, path, data, clock=self.clock)
        h.update(headers or {})
        st, hd, out = http(self.port, method, path, None, h, raw=data if method == "POST" else None)
        try:
            return st, hd, json.loads(out) if out and hd.get("Content-Type", "").startswith("application/json") else out
        except ValueError:
            return st, hd, out

    def act(self, action, args=None, confirm=None):
        self.clock.tick(2.5)  # 기기마다 2초에 한 번
        b = {"action": action, "args": args or {}}
        if confirm:
            b["confirm"] = confirm
        return self.call("POST", "/r/action", b)

    def make_project(self):
        segs = [{"start": 0.2, "end": 1.8, "text": "안녕하세요 풋살사관학교입니다"}, {"start": 2.2, "end": 3.8, "text": "오늘은 퍼스트 터치를 해 볼게요"}]
        d = core.adir(NAME)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (d / "transcript_timeline.md").write_text("x", encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": []}), encoding="utf-8")
        return editor.load_project(NAME)


class BoundaryTests(Base):
    def test_ping_open_without_origin(self):
        st, _, b = self.call("GET", "/r/ping", cred=False, origin=None)
        self.assertEqual(st, 200)
        self.assertEqual(b["api"], 1)
        self.assertEqual(set(b), {"api", "pc", "time"})

    def test_wrong_host_403_everywhere(self):
        for host in ("abc-def.trycloudflare.com", "127.0.0.1:8765", "localhost", "evil.com", ""):
            for path in ("/r/ping", "/r/status?since=0", "/r/m/xxxxxxxxxxxxxxxxxxxxxx"):
                st, _, _ = self.call("GET", path, headers={"Host": host})
                self.assertEqual(st, 403, (host, path))

    def test_sentinel_host_accepted_without_dev(self):
        with mock.patch.dict(os.environ, {"FUTSAL_REMOTE_DEV": "0"}):
            self.assertEqual(self.call("GET", "/r/ping", headers={"Host": "remote.futsal.invalid"})[0], 200)
            self.assertEqual(self.call("GET", "/r/ping")[0], 403)  # 개발 모드가 아니면 루프백 Host 도 거절

    def test_other_methods_rejected(self):
        for m in ("PUT", "DELETE", "PATCH"):
            self.assertEqual(self.call(m, "/r/status")[0], 405)

    def test_cors_preflight(self):
        st, hd, _ = self.call("OPTIONS", "/r/action", cred=False, headers={"Access-Control-Request-Method": "POST"})
        self.assertEqual(st, 204)
        self.assertEqual(hd["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(hd["Access-Control-Allow-Headers"], "Authorization, Content-Type")
        self.assertEqual(hd["Access-Control-Max-Age"], "600")
        self.assertNotIn("Access-Control-Allow-Credentials", hd)
        self.assertIn("Origin", hd["Vary"])
        st, hd, _ = self.call("OPTIONS", "/r/action", cred=False, origin="https://evil.example")
        self.assertEqual(st, 403)
        self.assertNotIn("Access-Control-Allow-Origin", hd)

    def test_www_origin_allowed_and_dev_origin_only_in_dev(self):
        self.assertEqual(self.call("GET", "/r/status?since=0", origin="https://www.mulgyeol.kr")[0], 200)
        self.assertEqual(self.call("GET", "/r/status?since=0", origin="http://127.0.0.1:8973")[0], 200)
        with mock.patch.dict(os.environ, {"FUTSAL_REMOTE_DEV": "0"}):
            st = self.call("GET", "/r/status?since=0", origin="http://127.0.0.1:8973", headers={"Host": "remote.futsal.invalid"})[0]
        self.assertEqual(st, 403)

    def test_api_needs_allowed_origin(self):
        for o in (None, "https://evil.example", "null", "https://mulgyeol.kr.evil.com", "http://mulgyeol.kr"):
            st, hd, _ = self.call("GET", "/r/status?since=0", origin=o)
            self.assertEqual(st, 403, o)
            self.assertNotIn("Access-Control-Allow-Origin", hd)

    def test_simple_post_types_rejected(self):
        for ct in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", ""):
            st, _, _ = self.call("POST", "/r/action", {"action": "cancel"}, headers={"Content-Type": ct})
            self.assertEqual(st, 415, ct)

    def test_body_limit_413(self):
        big = json.dumps({"x": "a" * (70 * 1024)}).encode()
        st, _, _ = self.call("POST", "/r/action", raw=big)
        self.assertEqual(st, 413)

    def test_unsigned_401_with_time(self):
        st, hd, b = self.call("GET", "/r/status?since=0", cred=False)
        self.assertEqual(st, 401)
        self.assertEqual(b["code"], "bad_header")
        self.assertAlmostEqual(b["time"], self.clock(), delta=2)
        self.assertEqual(hd["Access-Control-Allow-Origin"], ORIGIN)  # 페이지가 오류 글·시각을 읽을 수 있게

    def test_security_headers_on_every_response(self):
        for st, hd, _ in (self.call("GET", "/r/status?since=0"), self.call("GET", "/r/status?since=0", cred=False), self.call("GET", "/r/nope")):
            self.assertEqual(hd["X-Content-Type-Options"], "nosniff")
            self.assertEqual(hd["Referrer-Policy"], "no-referrer")
            self.assertEqual(hd["Content-Security-Policy"], "default-src 'none'; frame-ancestors 'none'")
            self.assertEqual(hd["Cache-Control"], "no-store")
            self.assertNotIn("Python", hd.get("Server", ""))

    def test_open_rate_limit(self):
        codes = [self.call("GET", "/r/ping", cred=False, headers={"Cf-Connecting-Ip": "203.0.113.9"})[0] for _ in range(11)]
        self.assertEqual(codes[:10], [200] * 10)
        self.assertEqual(codes[10], 429)
        self.assertEqual(self.call("GET", "/r/ping", cred=False, headers={"Cf-Connecting-Ip": "203.0.113.10"})[0], 200)

    def test_lockout_after_failed_auth(self):
        ip = {"Cf-Connecting-Ip": "198.51.100.7"}
        for _ in range(10):
            self.assertEqual(self.call("GET", "/r/status?since=0", cred=False, headers=ip)[0], 401)
        st, hd, _ = self.call("GET", "/r/status?since=0", headers=ip)
        self.assertEqual(st, 429)
        self.assertEqual(hd["Retry-After"], str(remote.LOCKOUT))
        self.assertIn("원격 접속 · 잘못된 연결 시도가 많아 잠시 막았어요", self.fb.lines)
        self.assertEqual(self.call("GET", "/r/status?since=0")[0], 200)  # 다른 주소(휴대폰)는 그대로

    def test_pair_over_http_and_wrong_code(self):
        self.svc.pairing.create()
        st, _, b = self.call("POST", "/r/pair", {"code": "0000-0000-00", "name": "x"}, cred=False)
        self.assertEqual(st, 403)
        self.assertIn("코드가 맞지 않거나", b["error"])
        code = self.svc.pairing.live()["code"]
        st, _, b = self.call("POST", "/r/pair", {"code": remote.fmt_code(code).lower(), "name": "Galaxy · Chrome"}, cred=False)
        self.assertEqual(st, 200)
        self.assertEqual(b["device"]["name"], "Galaxy · Chrome")
        self.assertEqual(self.call("GET", "/r/status?since=0", cred=b)[0], 200)

    def test_malformed_json_400(self):
        for raw in (b"{", b"[]", b"\xff\xfe", b'"x"'):
            self.assertEqual(self.call("POST", "/r/action", raw=raw)[0], 400, raw)


class StatusListTests(Base):
    def test_status_shape_and_home_scrubbed(self):
        home = str(Path.home())
        self.fb.log(f"내보내기 완료 · {home}/풋살사관학교_작업/out/x.mp4")
        st, _, b = self.call("GET", "/r/status?since=0")
        self.assertEqual(st, 200)
        for k in ("api", "ver", "time", "job", "last", "log", "logTotal", "remote", "notify", "beacon"):
            self.assertIn(k, b)
        self.assertEqual(b["remote"]["device"]["name"], "iPhone · Safari")
        self.assertTrue(any("~/풋살사관학교_작업/out/x.mp4" in x for x in b["log"]))
        self.assertFalse(any(home in x for x in b["log"]))
        st, _, b2 = self.call("GET", f"/r/status?since={b['logTotal']}")
        self.assertEqual(b2["log"], [])
        st, _, b3 = self.call("GET", "/r/status?since=99999")  # 앱이 다시 켜져 번호가 줄었으면 처음부터
        self.assertEqual(b3["log"], b["log"])

    def test_log_tail_200(self):
        for i in range(300):
            self.fb.log(f"줄 {i}")
        b = self.call("GET", "/r/status?since=0")[2]
        self.assertEqual(len(b["log"]), 200)
        self.assertEqual(b["log"][-1], "줄 299")

    def test_library_with_poster_and_sequences(self):
        self.make_project()
        st, _, b = self.call("GET", "/r/library")
        self.assertEqual(st, 200)
        v = b["videos"][0]
        self.assertEqual(v["name"], NAME)
        self.assertTrue(v["analyzed"])
        self.assertTrue(v["sequences"])
        self.assertTrue(all(set(q) == {"id", "name", "format"} for q in v["sequences"]))
        st, hd, img = self.call("GET", v["poster"], cred=False, origin=None)
        self.assertEqual(st, 200)
        self.assertEqual(hd["Content-Type"], "image/jpeg")
        self.assertTrue(img[:2] == b"\xff\xd8")

    def test_outputs_kinds_and_ticket_urls(self):
        shutil.copy(self.media / "v.mp4", self.out / "시험_롱폼.mp4")
        (self.out / "시험_올리기.txt").write_text("제목\n설명", encoding="utf-8")
        (self.out / "시험_썸네일.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
        (self.out / "시험_premiere.xml").write_text("<x/>", encoding="utf-8")
        (self.out / ".render_tmp").mkdir()
        b = self.call("GET", "/r/outputs")[2]
        kinds = {o["name"]: o["kind"] for o in b["outputs"]}
        self.assertEqual(kinds, {"시험_롱폼.mp4": "video", "시험_올리기.txt": "text", "시험_썸네일.jpg": "image"})
        vid = next(o for o in b["outputs"] if o["kind"] == "video")
        self.assertEqual(vid["preview"], {"exists": False, "url": None})
        self.assertNotIn(str(self.out), json.dumps(b, ensure_ascii=False))
        st, hd, txt = self.call("GET", next(o["url"] for o in b["outputs"] if o["kind"] == "text"), cred=False, origin=None)
        self.assertEqual(hd["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(txt.decode(), "제목\n설명")
        self.assertEqual(hd["Content-Disposition"], "inline")
        self.assertEqual(hd["Cache-Control"], "no-store")

    def test_choices(self):
        (self.work / "styles" / "빠른컷.json").write_text((Path(__file__).parent / "fixtures" / "style_v17.json").read_text(encoding="utf-8"), encoding="utf-8")
        with mock.patch.object(refs, "listing", return_value={"channels": [{"channelKey": "UCx", "channel": "슛포러브", "count": 3}, {"channelKey": ""}]}):
            b = self.call("GET", "/r/choices")[2]
        self.assertEqual(b["styles"], ["빠른컷"])
        self.assertEqual(b["refChannels"], [{"key": "UCx", "name": "슛포러브", "count": 3}])


class RangeTicketTests(Base):
    def setUp(self):
        super().setUp()
        self.data = bytes(range(256)) * 40  # 10240 바이트
        (self.out / "a.mp4").write_bytes(self.data)
        (self.out / "big.mp4").write_bytes(b"\0" * (remote.MEDIA_CHUNK + 1000))
        b = self.call("GET", "/r/outputs")[2]
        self.url = {o["name"]: o["url"] for o in b["outputs"]}

    def get(self, name, rng=None):
        return self.call("GET", self.url[name], cred=False, origin=None, headers={"Range": rng} if rng else None)

    def test_normal_open_suffix_ranges(self):
        st, hd, body = self.get("a.mp4", "bytes=0-99")
        self.assertEqual((st, hd["Content-Range"], body), (206, "bytes 0-99/10240", self.data[:100]))
        st, hd, body = self.get("a.mp4", "bytes=10000-")
        self.assertEqual((st, hd["Content-Range"], body), (206, "bytes 10000-10239/10240", self.data[10000:]))
        st, hd, body = self.get("a.mp4", "bytes=-50")
        self.assertEqual((st, hd["Content-Range"], body), (206, "bytes 10190-10239/10240", self.data[-50:]))
        st, hd, body = self.get("a.mp4", "bytes=100-999999")
        self.assertEqual((st, hd["Content-Range"]), (206, "bytes 100-10239/10240"))
        st, hd, body = self.get("a.mp4")
        self.assertEqual((st, body, hd["Accept-Ranges"]), (200, self.data, "bytes"))
        self.assertEqual(hd["Content-Type"], "video/mp4")

    def test_unsatisfiable_416(self):
        for rng in ("bytes=10240-", "bytes=999999-1000000", "bytes=-0", "bytes=50-10"):
            st, hd, _ = self.get("a.mp4", rng)
            self.assertEqual(st, 416, rng)
            self.assertEqual(hd["Content-Range"], "bytes */10240")

    def test_multi_range_and_garbage_ignored(self):
        for rng in ("bytes=0-1,5-6", "items=0-5", "bytes=a-b", "bytes=-"):
            st, _, body = self.get("a.mp4", rng)
            self.assertEqual((st, len(body)), (200, 10240), rng)

    def test_chunk_cap_4mb(self):
        st, hd, body = self.get("big.mp4", "bytes=0-")
        self.assertEqual(st, 206)
        self.assertEqual(len(body), remote.MEDIA_CHUNK)
        self.assertEqual(hd["Content-Range"], f"bytes 0-{remote.MEDIA_CHUNK - 1}/{remote.MEDIA_CHUNK + 1000}")

    def test_ticket_expired(self):
        self.clock.tick(remote.TICKET_TTL + 5)
        self.assertEqual(self.get("a.mp4")[0], 404)

    def test_ticket_dies_with_revoked_device(self):
        other = pair_device(self.svc, "다른 폰")
        b = self.call("GET", "/r/outputs", cred=other)[2]
        other_url = next(o["url"] for o in b["outputs"] if o["name"] == "a.mp4")
        self.svc.revoke([self.cred["device"]["id"]])
        self.assertEqual(self.get("a.mp4")[0], 404)
        self.assertEqual(self.call("GET", other_url, cred=False, origin=None)[0], 200)

    def test_bad_ticket_shapes(self):
        for t in ("x", "../../etc/passwd", "%2e%2e%2f", "a" * 200, ""):
            self.assertEqual(self.call("GET", "/r/m/" + t, cred=False, origin=None)[0], 404, t)

    def test_file_deleted_or_swapped_outside_404(self):
        (self.out / "a.mp4").unlink()
        self.assertEqual(self.get("a.mp4")[0], 404)

    def test_disallowed_extension_never_served(self):
        tid = self.svc.tickets.issue(self.cred["device"]["id"], "file", (self.out / "x.json").resolve())
        (self.out / "x.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.call("GET", "/r/m/" + tid, cred=False, origin=None)[0], 404)
        tid = self.svc.tickets.issue(self.cred["device"]["id"], "file", (self.tmp / "secret.mp4").resolve())
        (self.tmp / "secret.mp4").write_bytes(b"x")
        self.assertEqual(self.call("GET", "/r/m/" + tid, cred=False, origin=None)[0], 404)  # 허용 폴더 밖


class ActionTests(Base):
    def test_unknown_action_400(self):
        for a in ("delete", "settings", "update", "open", "upload", "__init__", "", None, 5):
            st, _, b = self.act(a)
            self.assertEqual(st, 400, a)
            self.assertFalse(b["ok"])

    def test_download_only_youtube_video_ids(self):
        bad = ["https://evil.com/watch?v=abcdefghijk", "https://www.youtube.com/playlist?list=PL123", "https://www.youtube.com/@channel",
               "file:///etc/passwd", "javascript:alert(1)", "https://www.youtube.com/watch?v=" + "a" * 5000, "",
               "https://youtube.com.evil.com/watch?v=abcdefghijk", "https://u:p@www.youtube.com/watch?v=abcdefghijk",
               "https://www.youtube.com:8443/watch?v=abcdefghijk", "ytsearch:풋살", "https://www.youtube.com/watch?v=abc"]
        for u in bad:
            st, _, b = self.act("download", {"url": u})
            self.assertEqual(st, 400, u)
        self.assertEqual(self.fb.started, [])
        got = []
        with mock.patch.object(core, "download", lambda ids, log, ck: got.append((ids, ck)) or []):
            for u, vid in (("https://www.youtube.com/watch?v=AbCdEfGhIjK&list=PLx&t=3", "AbCdEfGhIjK"), ("youtu.be/AbCdEfGhIj_", "AbCdEfGhIj_"),
                           ("https://m.youtube.com/shorts/Ab-dEfGhIjK?feature=x", "Ab-dEfGhIjK")):
                st, _, b = self.act("download", {"url": u})
                self.assertEqual((st, b), (200, {"ok": True, "job": "보관함에 담기"}), u)
                self.fb.wait_idle()
        self.assertEqual(got, [(["AbCdEfGhIjK"], None), (["AbCdEfGhIj_"], None), (["Ab-dEfGhIjK"], None)])  # id 만 · 쿠키 없음
        self.assertEqual(self.fb.started[0], ("보관함에 담기", "휴대폰 · iPhone · Safari"))
        self.assertIn("원격 · iPhone · Safari · 보관함에 담기 · AbCdEfGhIjK", self.fb.lines)

    def test_analyze_names_checked(self):
        for names in (["..\\x.mp4"], ["C:\\x.mp4"], ["\\\\srv\\x.mp4"], ["a\x00.mp4"], ["없는 영상.mp4"], ["../" + NAME], [NAME + "."],
                      [], "x", [NAME] * 11, [1]):
            st, _, b = self.act("analyze", {"names": names})
            self.assertIn(st, (400, 404), names)
        self.assertEqual(self.fb.started, [])
        st, _, b = self.act("analyze", {"names": [NAME]})
        self.assertEqual(st, 200)
        self.fb.wait_idle()
        self.assertEqual(self.fb.analyzed, [{"names": [NAME], "model": "large-v3-turbo"}])

    def test_analyze_confirm_token_flow(self):
        self.make_project()
        st, _, b = self.act("analyze", {"names": [NAME]})
        self.assertEqual(st, 200)
        self.assertIn("이미 편집점을 찾은 영상이 1개", b["confirm"]["text"])
        tok = b["confirm"]["token"]
        self.assertEqual(self.fb.started, [])
        other = pair_device(self.svc, "다른 폰")  # 다른 기기는 이 표를 못 씀
        self.clock.tick(3)
        st, _, b2 = self.call("POST", "/r/action", {"action": "analyze", "args": {"names": [NAME]}, "confirm": tok}, cred=other)
        self.assertIn("confirm", b2)
        st, _, b = self.act("analyze", {"names": [NAME]})
        tok = b["confirm"]["token"]
        st, _, b = self.act("analyze", {"names": [NAME]}, confirm=tok)
        self.assertEqual(b, {"ok": True, "job": "편집점 찾기"})
        self.fb.wait_idle()
        st, _, b = self.act("analyze", {"names": [NAME]}, confirm=tok)  # 한 번만
        self.assertIn("confirm", b)

    def test_confirm_token_expires(self):
        self.make_project()
        tok = self.act("analyze", {"names": [NAME]})[2]["confirm"]["token"]
        self.clock.tick(remote.CONFIRM_TTL + 1)
        self.assertIn("confirm", self.act("analyze", {"names": [NAME]}, confirm=tok)[2])

    def test_busy_409_and_action_gap(self):
        gate = threading.Event()
        self.fb.start_job("내보내기", gate.wait)
        try:
            st, _, b = self.act("analyze", {"names": [NAME]})
            self.assertEqual((st, b["error"]), (409, remote.BUSY_MSG))
        finally:
            gate.set()
            self.fb.wait_idle()
        self.clock.tick(3)
        b = {"action": "analyze", "args": {"names": [NAME]}}
        self.assertEqual(self.call("POST", "/r/action", b)[0], 200)
        self.fb.wait_idle()
        self.assertEqual(self.call("POST", "/r/action", b)[0], 429)  # 2초 안에 또

    def test_export_validation_and_real_export(self):
        proj = self.make_project()
        seq = proj["sequences"][0]
        for args, code in (({"name": NAME, "seq": "nope"}, 404), ({"name": NAME, "seq": seq["id"], "preset": "hq"}, 400),
                           ({"name": "..\\" + NAME, "seq": seq["id"]}, 404), ({"name": NAME}, 404)):
            self.assertEqual(self.act("export", args)[0], code, args)
        st, _, b = self.act("export", {"name": NAME, "seq": seq["id"], "preset": "small"})
        self.assertEqual(b, {"ok": True, "job": "내보내기"})
        self.assertTrue(self.fb.wait_idle(180))
        self.assertIsNone(self.svc.last["error"], self.fb.lines[-5:])
        mp4 = [p for p in self.out.glob("*.mp4")]
        self.assertEqual(len(mp4), 1)
        self.assertTrue(list(self.out.glob("*.srt")))
        self.assertFalse(list(self.out.glob("*_premiere.xml")))  # 휴대폰 내보내기는 XML 없이
        info = editor.probe(mp4[0])
        self.assertEqual((info["width"], info["height"]), (1280, 720) if seq["format"] == "long" else (720, 1280))
        self.assertEqual(self.svc.last["by"], "휴대폰 · iPhone · Safari")

    def test_qa_and_preview_jobs_then_outputs(self):
        shutil.copy(self.media / "v.mp4", self.out / "완성.mp4")
        self.assertEqual(self.act("qa", {"file": "../완성.mp4"})[0], 400)
        self.assertEqual(self.act("qa", {"file": "없음.mp4"})[0], 404)
        (self.out / "a.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.act("qa", {"file": "a.txt"})[0], 404)
        self.assertEqual(self.act("qa", {"file": "완성.mp4"})[2], {"ok": True, "job": "영상 검수"})
        self.assertTrue(self.fb.wait_idle(120))
        self.assertEqual(self.act("preview", {"file": "완성.mp4"})[2], {"ok": True, "job": "작은 미리보기 만들기"})
        self.assertTrue(self.fb.wait_idle(120))
        o = next(x for x in self.call("GET", "/r/outputs")[2]["outputs"] if x["name"] == "완성.mp4")
        self.assertIsInstance(o["qa"]["score"], int)
        self.assertTrue(o["preview"]["exists"])
        st, hd, body = self.call("GET", o["preview"]["url"], cred=False, origin=None, headers={"Range": "bytes=0-"})
        self.assertEqual((st, hd["Content-Type"]), (206, "video/mp4"))
        pv = editor.out_preview_path("완성.mp4")
        self.assertEqual(pv.parent.parent, core.ANALYSIS / "_remote")  # 완성본 폴더에는 안 넣음
        self.assertEqual(editor.probe(pv)["height"], 540)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["a.txt", "완성.mp4"])

    def test_preview_keeps_last_10(self):
        for i in range(12):
            d = editor.remote_previews() / f"old{i}"
            d.mkdir(parents=True)
            os.utime(d, (i, i))
        shutil.copy(self.media / "v.mp4", self.out / "완성.mp4")
        editor.out_preview("완성.mp4", self.fb.log)
        self.assertEqual(len([d for d in editor.remote_previews().iterdir()]), remote.PREVIEW_KEEP)
        self.assertTrue(editor.out_preview_path("완성.mp4").exists())

    def test_autoseq_adds_named_sequences(self):
        self.make_project()
        (self.work / "styles" / "빠른컷.json").write_text((Path(__file__).parent / "fixtures" / "style_v17.json").read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(self.act("autoseq", {"name": NAME, "style": "없는 스타일"})[0], 404)
        self.assertEqual(self.act("autoseq", {"name": NAME, "style": "빠른컷", "kinds": ["wide"]})[0], 400)
        before = editor.load_project(NAME)
        self.assertEqual(self.act("autoseq", {"name": NAME, "style": "빠른컷", "kinds": ["long"]})[2], {"ok": True, "job": "자동 가편집"})
        self.assertTrue(self.fb.wait_idle(60))
        after = editor.load_project(NAME)
        names = [q["name"] for q in after["sequences"]]
        self.assertEqual(len(after["sequences"]), len(before["sequences"]) + 1)
        self.assertIn("빠른컷 스타일 가편집", names)
        self.assertEqual(self.act("autoseq", {"name": NAME, "style": "빠른컷", "kinds": ["long"]})[0], 200)
        self.assertTrue(self.fb.wait_idle(60))
        self.assertIn("빠른컷 스타일 가편집 2", [q["name"] for q in editor.load_project(NAME)["sequences"]])
        self.assertGreater(after["rev"], before["rev"])

    def test_autoseq_needs_analysis(self):
        (self.work / "styles" / "빠른컷.json").write_text("{}", encoding="utf-8")
        self.assertIn("편집점 찾기", self.act("autoseq", {"name": NAME, "style": "빠른컷"})[2]["error"])

    def test_add_refs_channel_url_rebuilt(self):
        for u in ("https://evil.com/@x", "https://www.youtube.com/watch?v=AbCdEfGhIjK", "file:///x", "@", "https://www.youtube.com/channel/abc"):
            self.assertEqual(self.act("add_refs", {"url": u})[0], 400, u)
        self.assertEqual(self.act("add_refs", {"url": "@shootforlove", "count": 11})[0], 400)
        self.assertEqual(self.act("add_refs", {"url": "@shootforlove", "count": True})[0], 400)
        got = []
        with mock.patch.object(refs, "add_channel", lambda url, count, kind, log, ck, learn, prune, sn: got.append((url, count, kind, ck)) or {"got": []}):
            st, _, b = self.act("add_refs", {"url": "https://m.youtube.com/@shootforlove/videos?x=1", "count": 3})
            self.assertEqual(b, {"ok": True, "job": "학습용 영상 받기"})
            self.fb.wait_idle()
            b = self.act("add_refs", {"url": "youtube.com/channel/UC" + "a" * 22, "count": 8})[2]
            self.assertIn("8개", b["confirm"]["text"])
            self.assertEqual(self.act("add_refs", {"url": "youtube.com/channel/UC" + "a" * 22, "count": 8}, b["confirm"]["token"])[0], 200)
            self.fb.wait_idle()
        self.assertEqual(got, [("https://www.youtube.com/@shootforlove", 3, "videos", None), ("https://www.youtube.com/channel/UC" + "a" * 22, 8, "videos", None)])

    def test_learn_refs_needs_listed_channel(self):
        with mock.patch.object(refs, "listing", return_value={"channels": [{"channelKey": "UCx", "channel": "슛포러브"}]}), \
                mock.patch.object(refs, "learn_channel", lambda key, log, prune: {"learned": key}) as _:
            self.assertEqual(self.act("learn_refs", {"channel": "UCy"})[0], 404)
            self.assertEqual(self.act("learn_refs", {"channel": "UCx"})[2], {"ok": True, "job": "학습용 스타일 배우기"})
            self.fb.wait_idle()

    def test_cancel_audited(self):
        with mock.patch.object(editor, "cancel_export") as c:
            self.assertEqual(self.call("POST", "/r/cancel", {})[2], {"ok": True})
            self.assertEqual(self.act("cancel")[2], {"ok": True})
        self.assertEqual(c.call_count, 2)
        self.assertIn("원격 · iPhone · Safari · 멈추기", self.fb.lines)

    def test_forget_removes_own_device(self):
        self.assertEqual(self.call("POST", "/r/forget", {})[2], {"ok": True})
        st, _, b = self.call("GET", "/r/status?since=0")
        self.assertEqual((st, b["code"]), (401, "unknown_device"))

    def test_remote_off_from_phone(self):
        self.assertEqual(self.call("POST", "/r/remote-off", {})[2], {"ok": True})
        end = time.time() + 5
        while self.svc.state != "off" and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(self.svc.state, "off")
        self.assertFalse(self.svc.store.data["enabled"])

    def test_no_secrets_in_log(self):
        self.make_project()
        self.call("GET", "/r/status?since=0")
        self.call("GET", "/r/outputs")
        self.act("analyze", {"names": [NAME]})
        self.call("GET", "/r/status?since=0", cred=False)
        text = "\n".join(self.fb.lines)
        d = self.svc.store.data
        for secret in (self.cred["keys"]["auth"], self.cred["keys"]["beacon"], d["topics"]["beacon"], d["topics"]["notify"],
                       self.svc.url, "FSR1", *self.svc.tickets.items.keys()):
            self.assertNotIn(secret, text)


class LocalServerTests(Base):
    """로컬 화면 서버(app.Handler, 8765)는 그대로: 터널 Host 는 403 · CORS 헤더 없음 · /api/remote 는 이 PC 에서만."""

    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.lport = self.srv.server_address[1]
        for p in (mock.patch.object(app, "PORT", self.lport), mock.patch.object(app, "LOGFILE", self.work / "studio.log"),
                  mock.patch.object(remote, "SVC", self.svc)):
            p.start()
            self.addCleanup(p.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def local(self, method, path, body=None, host=None, origin=None):
        h = {"Host": host or f"127.0.0.1:{self.lport}"}
        if origin:
            h["Origin"] = origin
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.lport}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, dict(r.headers), json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read() or b"{}")

    def test_tunnel_host_rejected_and_no_cors(self):
        for host in ("abc-def.trycloudflare.com", "remote.futsal.invalid", "mulgyeol.kr"):
            self.assertEqual(self.local("GET", "/api/state", host=host)[0], 403)
            self.assertEqual(self.local("POST", "/api/remote/off", {}, host=host)[0], 403)
        st, hd, _ = self.local("GET", "/api/state", origin=ORIGIN)
        self.assertEqual(st, 200)
        self.assertNotIn("Access-Control-Allow-Origin", hd)
        self.assertEqual(self.local("POST", "/api/remote/off", {}, origin=ORIGIN)[0], 403)  # 다른 출처의 POST 는 그대로 막힘

    def test_state_has_remote_brief(self):
        st, _, b = self.local("GET", "/api/state")
        self.assertEqual(b["remote"], {"state": "on", "devices": 1})

    def test_api_remote_status_with_qr(self):
        st, _, b = self.local("POST", "/api/remote/pair", {})
        self.assertEqual(st, 200)
        self.assertTrue(b["pair"]["qr"] and all(set(r) <= {"0", "1"} for r in b["pair"]["qr"]))
        self.assertRegex(b["pair"]["link"], r"^https://mulgyeol\.kr/futsal#pair=[0-9A-Z]{10}&u=127\.0\.0\.1:\d+$")  # 개발 모드 힌트는 포트까지
        with mock.patch.dict(os.environ, {"FUTSAL_REMOTE_SITE": "http://127.0.0.1:8973/futsal"}):
            self.assertTrue(self.local("GET", "/api/remote")[2]["pair"]["link"].startswith("http://127.0.0.1:8973/futsal#pair="))
        with mock.patch.dict(os.environ, {"FUTSAL_REMOTE_SITE": "http://127.0.0.1:8973/futsal", "FUTSAL_REMOTE_DEV": "0"}):
            self.assertTrue(self.local("GET", "/api/remote")[2]["site"] == "https://mulgyeol.kr/futsal")  # 개발 모드가 아니면 늘 진짜 주소
        st, _, s = self.local("GET", "/api/remote")
        self.assertEqual(s["pair"]["code"], b["pair"]["code"])
        self.assertEqual(s["devices"][0]["name"], "iPhone · Safari")
        self.assertNotIn("auth", json.dumps(s["devices"]))
        self.assertEqual(self.local("POST", "/api/remote/pair/cancel", {})[2], {"ok": True})
        self.assertIsNone(self.local("GET", "/api/remote")[2]["pair"])

    def test_settings_revoke_test_off(self):
        st, _, b = self.local("POST", "/api/remote/settings", {"autoOffHours": 3, "keepAwake": "always"})
        self.assertEqual(b["settings"]["autoOffHours"], 3)
        self.assertEqual(self.local("POST", "/api/remote/settings", {"autoOffHours": 7})[0], 400)
        self.assertEqual(self.local("POST", "/api/remote/test", {})[2], {"ok": True})
        self.assertEqual(self.local("POST", "/api/remote/revoke", {})[0], 400)
        self.assertEqual(self.local("POST", "/api/remote/revoke", {"id": self.cred["device"]["id"]})[2], {"ok": True, "removed": 1})
        self.assertEqual(self.local("POST", "/api/remote/test", {})[0], 400)
        self.assertEqual(self.local("POST", "/api/remote/off", {})[2], {"ok": True})
        self.assertEqual(self.svc.state, "off")


if __name__ == "__main__":
    unittest.main()
