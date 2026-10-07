"""작업 오류 안내(trouble.py) + 로그인 정보 브라우저 고르기 테스트.

자주 나는 오류(주소 틀림·인터넷 끊김·YouTube 막힘·디스크 가득·파일 잠김 WinError 32·ffmpeg 실패·빈 파일)와
로그인 정보(쿠키) 오류(크롬이 켜져 있음 #7271 · App-Bound 암호화 DPAPI #10927 · 브라우저 없음)를 해요체 한 줄 + 할 일로 ·
우리 한국어 안내는 그대로 · 사용자가 멈춘 것은 실패가 아님 · 브라우저 값 검사(목록에 있는 것만) ·
core.download(가짜 yt-dlp): 실패 이유를 영상마다 돌려주고 기록에는 한국어 · 학습용 영상은 이 화면용 막힘 안내(refs.BLOCKED_MSG)
· 작업 실패(/api/state 의 error·fail) · /api/download 결과의 why · 고른 브라우저가 yt-dlp 와 안내 문구에 들어감 · 잘못된 브라우저 거절.
인터넷은 쓰지 않는다. 실행: 저장소 폴더에서 python3 -m unittest tests.test_trouble
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import refs  # noqa: E402
import source  # noqa: E402
import trouble  # noqa: E402

LOCKED = "ERROR: Could not copy Chrome cookie database. See  https://github.com/yt-dlp/yt-dlp/issues/7271  for more info"
DPAPI = "ERROR: Failed to decrypt with DPAPI. See  https://github.com/yt-dlp/yt-dlp/issues/10927  for more info"
BOT = "ERROR: [youtube] abcdefghijk: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication."


class DownloadError(Exception):
    pass


class ExplainTests(unittest.TestCase):
    def kind(self, err, **kw):
        info = trouble.explain(err, **kw)
        self.assertRegex(info["msg"], r"요[.)]?(?: \([^()]*\))?$", "해요체 한 줄")
        self.assertNotRegex(info["msg"], r"ERROR|Traceback|WinError|HTTP Error", "영어 원문은 화면에 안 보임")
        return info["kind"], info

    def test_common_errors_to_plain_korean_and_actions(self):
        cases = [
            ("ERROR: [youtube:tab] @없는채널: Unable to download API page: HTTP Error 404: Not Found (caused by <HTTPError 404>)", "url", "url"),
            ("ERROR: [generic] 'asdf' is not a valid URL. Set --default-search", "url", "url"),
            ("ERROR: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>", "net", "retry"),
            ("ERROR: Unable to download webpage: <urlopen error [Errno -3] Temporary failure in name resolution>", "net", "retry"),
            (BOT, "blocked", "cookies"),
            ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "blocked", "update"),
            ("ERROR: [youtube] x: Video unavailable. This content isn't available, try again later.", "blocked", "cookies"),
            ("[Errno 28] No space left on device", "disk", "folder"),
            ("[WinError 112] 디스크 공간이 부족합니다", "disk", "folder"),
            ("[WinError 32] 다른 프로세스가 파일을 사용 중이기 때문에 프로세스가 액세스 할 수 없습니다: 'C:\\풋살\\a.mp4'", "locked", "folder"),
            ("[WinError 32] The process cannot access the file because it is being used by another process", "locked", "retry"),
            ("ERROR: Postprocessing: Conversion failed!", "ffmpeg", "update"),
            ("ERROR: The downloaded file is empty", "empty", "retry"),
            ("ERROR: [youtube] x: Private video. Sign in if you've been granted access to this video", "unavailable", None),
            ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.", "login", "cookies"),
            ("ERROR: [youtube] x: Requested format is not available", "engine", "update"),
            ("HTTP Error 503: Service Unavailable", "server", "retry"),
        ]
        for text, want, action in cases:
            with self.subTest(text=text):
                k, info = self.kind(text)
                self.assertEqual(k, want)
                if action:
                    self.assertIn(action, info["actions"])
                else:
                    self.assertEqual(info["actions"], [])

    def test_cookie_errors_name_the_chosen_browser(self):
        k, info = self.kind(LOCKED, browser="edge")
        self.assertEqual(k, "cookie_locked")
        self.assertIn("엣지가 켜져 있어서", info["msg"])
        self.assertIn("엣지 창을 모두 닫고", info["msg"])
        self.assertEqual(info["browser"], "edge")
        k, info = self.kind(LOCKED, browser="chrome")
        self.assertIn("크롬이 켜져 있어서", info["msg"])
        self.assertIn("크롬을 열지 말고", info["msg"])
        k, info = self.kind(DPAPI, browser="chrome")
        self.assertEqual((k, info["suggest"]), ("cookie_dpapi", "edge"))
        self.assertIn("엣지나 파이어폭스", info["msg"])
        self.assertEqual(self.kind(DPAPI, browser="edge")[1]["suggest"], "firefox", "엣지도 안 되면 파이어폭스")
        k, info = self.kind('ERROR: could not find whale cookies database in "C:\\Users\\a\\AppData"', browser="whale")
        self.assertEqual(k, "cookie_missing")
        self.assertIn("웨일에서 로그인 정보를 찾지 못했어요", info["msg"])
        self.assertNotIn("Users", info["msg"], "경로는 화면에 안 보임")
        k, info = self.kind(BOT, browser="firefox")
        self.assertIn("파이어폭스 로그인 정보로도", info["msg"])

    def test_blocked_uses_screen_message(self):
        info = trouble.explain(BOT, blocked=refs.BLOCKED_MSG)
        self.assertEqual((info["kind"], info["msg"]), ("blocked", refs.BLOCKED_MSG))
        info = trouble.explain(RuntimeError(core.BLOCKED_MSG), blocked=refs.BLOCKED_MSG)
        self.assertEqual((info["kind"], info["msg"]), ("blocked", refs.BLOCKED_MSG), "소재 찾기용 안내 → 이 화면용")
        self.assertEqual(trouble.explain(RuntimeError(core.BLOCKED_MSG))["kind"], "blocked")
        self.assertIn("cookies", trouble.explain(RuntimeError(core.BLOCKED_MSG))["actions"])

    def test_our_korean_message_kept_with_cause_advice(self):
        info = trouble.explain(RuntimeError("영상을 만들지 못했어요 · [Errno 28] No space left on device"))
        self.assertEqual(info["kind"], "disk")
        self.assertTrue(info["msg"].startswith("영상을 만들지 못했어요 · 저장 공간이 부족해요"), info["msg"])
        info = trouble.explain(RuntimeError("영상에서 소리를 꺼내지 못했어요 (파일이 깨졌거나 소리가 없는 영상일 수 있어요) · moov atom not found"))
        self.assertEqual(info["kind"], "broken")
        self.assertEqual(info["msg"], "영상에서 소리를 꺼내지 못했어요 (파일이 깨졌거나 소리가 없는 영상일 수 있어요)")
        info = trouble.explain(RuntimeError("이 채널에서 영상을 찾지 못했어요. 주소를 확인해 주세요"))
        self.assertEqual((info["kind"], info["msg"]), ("other", "이 채널에서 영상을 찾지 못했어요. 주소를 확인해 주세요"))
        self.assertEqual(trouble.explain(RuntimeError("내보내기를 멈췄어요"))["kind"], "cancelled", "멈춘 것은 실패 카드 아님")

    def test_unknown_error_points_to_studio_log(self):
        info = trouble.explain(KeyError("ids"))
        self.assertEqual(info["kind"], "other")
        self.assertIn("studio.log", info["msg"])
        self.assertEqual(trouble.explain(MemoryError())["kind"], "memory")
        info = trouble.explain(trouble.Trouble("copying", "아직 복사 중이에요", ["retry"]))
        self.assertEqual(info, {"kind": "copying", "msg": "아직 복사 중이에요", "actions": ["retry"]})

    def test_korean_head_ignores_windows_and_english_text(self):
        self.assertEqual(trouble.korean_head("[WinError 32] 다른 프로세스가 파일을 사용 중이기 때문에 …: 'a'"), "")
        self.assertEqual(trouble.korean_head("No such file: '풋살.mp4'"), "")
        self.assertEqual(trouble.korean_head("'a.mp4'은 아직 복사 중이에요 · x"), "'a.mp4'은 아직 복사 중이에요")

    def test_browser_value_checked(self):
        self.assertIsNone(trouble.browser(None))
        self.assertIsNone(trouble.browser(""))
        for b in ("chrome", "edge", "whale", "firefox"):
            self.assertEqual(trouble.browser(b), b)
        for bad in ("chrome:Profile 1", "safari", "../x", 3, ["chrome"]):
            with self.assertRaises(ValueError):
                trouble.browser(bad)

    def test_josa(self):
        self.assertEqual(trouble._j("크롬", "을", "를"), "크롬을")
        self.assertEqual(trouble._j("엣지", "을", "를"), "엣지를")
        self.assertEqual(trouble._j("웨일", "이", "가"), "웨일이")
        self.assertEqual(trouble._j("파이어폭스", "이", "가"), "파이어폭스가")


class FakeYDL:
    """yt_dlp.YoutubeDL 흉내: fail 의 영상은 그 오류로 실패, 나머지는 파일을 만듦."""
    fail, seen = {}, []

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.seen.append(opts)

    def download(self, urls):
        for u in urls:
            vid = u.rsplit("=", 1)[-1]
            if vid in self.fail:
                raise self.fail[vid]
            info = {"id": vid, "title": "제목", "upload_date": "20240101", "ext": "mp4"}
            for h in self.opts["progress_hooks"]:
                h({"status": "finished", "info_dict": info})
            Path(self.opts["outtmpl"].replace("%(upload_date)s", "20240101").replace("%(id)s", vid)
                 .replace("%(title).60B", "제목").replace("%(ext)s", "mp4")).write_bytes(b"\0" * 10)

    def close(self):
        pass


class Work(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="오류 안내 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.videos = dirs["VIDEOS"]
        FakeYDL.fail, FakeYDL.seen = {}, []
        fake = types.SimpleNamespace(YoutubeDL=FakeYDL)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        self.patches += [mock.patch.object(core, "_yt", return_value=fake), mock.patch.object(core, "ensure_deno"),
                         mock.patch.object(core, "update_engine"), mock.patch.object(source, "_online", return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class DownloadTests(Work):
    def test_failures_reported_per_video_in_korean(self):
        FakeYDL.fail = {"emptyvid001": DownloadError("ERROR: The downloaded file is empty"),
                        "lockedvid01": DownloadError(LOCKED)}
        logs, why = [], {}
        failed = core.download(["emptyvid001", "goodvid0001", "lockedvid01"], logs.append, "chrome", why=why)
        self.assertEqual(failed, ["emptyvid001", "lockedvid01"])
        self.assertEqual(why["emptyvid001"]["kind"], "empty")
        self.assertEqual(why["lockedvid01"]["kind"], "cookie_locked")
        self.assertIn("크롬이 켜져 있어서", why["lockedvid01"]["msg"])
        fails = [m for m in logs if "담지 못했어요" in m]
        self.assertEqual(len(fails), 2)
        self.assertFalse(any("ERROR" in m or "downloaded file" in m for m in logs), logs)
        self.assertEqual(FakeYDL.seen[0]["cookiesfrombrowser"], ("chrome",))

    def test_refs_fetch_uses_this_screen_blocked_message(self):
        """학습용 영상 받기가 막히면 '이 화면'의 로그인 정보 설정을 가리킴 (소재 찾기 화면이 아니라 · D-022 (8))."""
        FakeYDL.fail = {"blockvid001": DownloadError(BOT)}
        logs = []
        res = refs.fetch([{"id": "blockvid001"}], logs.append)
        self.assertEqual(res["failed"], ["blockvid001"])
        self.assertEqual(res["why"]["blockvid001"]["kind"], "blocked")
        self.assertEqual(res["why"]["blockvid001"]["msg"], refs.BLOCKED_MSG)
        self.assertTrue(any(refs.BLOCKED_MSG in m for m in logs))
        self.assertFalse(any(core.BLOCKED_MSG in m for m in logs), "소재 찾기 화면을 가리키는 안내가 아님")


class RouteTests(Work):
    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.tmp / "studio.log")]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.wait()
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.p2:
            p.stop()
        super().tearDown()

    def wait(self):
        for _ in range(400):
            if not self.app.JOB["name"]:
                return
            time.sleep(0.05)

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def state(self):
        self.wait()
        return self.call("/api/state?since=0")[1]

    def test_missing_channel_job_fails_with_plain_message_and_url_action(self):
        err = DownloadError("ERROR: [youtube:tab] @nothere: Unable to download API page: HTTP Error 404: Not Found")
        with mock.patch.object(core, "list_videos", side_effect=err):
            self.assertEqual(self.call("/api/list", {"url": "@nothere"})[0], 200)
            s = self.state()
        self.assertEqual(s["fail"]["kind"], "url")
        self.assertEqual(s["error"], s["fail"]["msg"])
        self.assertIn("주소가 맞는지", s["error"])
        self.assertEqual(s["fail"]["job"], "채널 불러오기")
        self.assertTrue(any("문제가 생겼어요 · 채널이나 영상을 찾지 못했어요" in m for m in s["log"]), s["log"])
        self.assertFalse(any("HTTP Error 404" in m for m in s["log"]), "화면 기록에 영어 원문 없음")
        log = (self.tmp / "studio.log").read_text(encoding="utf-8")
        self.assertIn("HTTP Error 404", log, "원문은 studio.log 에만")

    def test_cookie_error_names_browser_from_request(self):
        with mock.patch.object(core, "list_videos", side_effect=DownloadError(LOCKED)) as lv:
            self.call("/api/list", {"url": "@x", "cookies": "whale"})
            s = self.state()
        self.assertEqual(lv.call_args[0][1], "whale")
        self.assertEqual(s["fail"]["kind"], "cookie_locked")
        self.assertIn("웨일이 켜져 있어서", s["fail"]["msg"])

    def test_wrong_browser_value_refused(self):
        code, j = self.call("/api/download", {"ids": ["abcdefghijk"], "cookies": "chrome:Default"})
        self.assertEqual(code, 400)
        self.assertIn("브라우저", j["error"])
        self.assertIsNone(self.app.JOB["name"])

    def test_download_result_has_reasons_and_job_ok(self):
        FakeYDL.fail = {"emptyvid001": DownloadError("ERROR: The downloaded file is empty")}
        self.assertEqual(self.call("/api/download", {"ids": ["emptyvid001", "goodvid0001"], "cookies": "edge"})[0], 200)
        s = self.state()
        self.assertIsNone(s["error"])
        self.assertEqual(s["result"]["failed"], ["emptyvid001"])
        self.assertEqual(s["result"]["why"]["emptyvid001"]["kind"], "empty")
        self.assertEqual(FakeYDL.seen[0]["cookiesfrombrowser"], ("edge",))

    def test_refs_blocked_listing_gives_fail_info(self):
        with mock.patch.object(core, "list_videos", side_effect=RuntimeError(core.BLOCKED_MSG)):
            self.call("/api/refs/add", {"url": "@shootforlovekorea", "count": 3})
            r = self.state()["result"]
        self.assertEqual((r["ok"], r["blocked"], r["error"]), (False, True, refs.BLOCKED_MSG))
        self.assertEqual((r["fail"]["kind"], r["fail"]["msg"]), ("blocked", refs.BLOCKED_MSG))


if __name__ == "__main__":
    unittest.main()
