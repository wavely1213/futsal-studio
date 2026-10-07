"""영상 출처 구분(source.py) 테스트 — 풋살사관학교 · 다른 채널 · 내 촬영본.

채널 주소 꼴별 판단 · 받을 때 기록(가짜 yt-dlp) · 예전 영상 출처 찾기(채널 목록 기억·영상 정보 조회) · 직접 고르기 ·
채널별 묶기(개수·이름 바뀜·색·채널 직접 고르기) · 촬영본 · 깨진 기록 파일 · 한글·띄어쓰기 이름 · 이름 바꾸기·지우기 · /api/source·/api/state·/api/list.
인터넷은 쓰지 않는다 (yt-dlp·연결 확인은 가짜).
실행: 저장소 폴더에서 python3 -m unittest tests.test_source
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

import numpy  # noqa: F401 — sys.modules 를 잠시 바꾸는 시험 전에 먼저 불러 둠 (되돌릴 때 지워지면 다시 못 불러옴)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import hooks  # noqa: E402
import source  # noqa: E402

OWN_ID = "UCRYziLOw2T6BF6fXtpUby-g"
OTHER_ID = "UCabcdefghijklmnopqrstuv"
OWN_INFO = {"channel": "풋살사관학교", "channel_id": OWN_ID, "channel_url": f"https://www.youtube.com/channel/{OWN_ID}",
            "uploader_id": "@풋살사관학교", "uploader_url": "https://www.youtube.com/@풋살사관학교"}
OTHER_INFO = {"channel": "슛포러브", "channel_id": OTHER_ID, "channel_url": f"https://www.youtube.com/channel/{OTHER_ID}",
              "uploader_id": "@shootforlove", "uploader_url": "https://www.youtube.com/@shootforlove"}


def yt_name(vid, title="패스 [꿀팁] 잘하는 법"):
    return f"20240101_{vid}_{title}.mp4"


class Base(unittest.TestCase):
    CHANNEL = f"https://www.youtube.com/channel/{OWN_ID}"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="출처 테스트 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        self.patches.append(mock.patch.dict(core.CONFIG, {"channel_url": self.CHANNEL}))
        self.patches.append(mock.patch.object(source, "_online", return_value=False))  # 인터넷은 쓰지 않음
        self.patches.append(mock.patch.object(source, "LOOKUP_GAP", 0))
        for p in self.patches:
            p.start()
        source._BF.update(thread=None, stop=None, offline=False)
        self.videos = dirs["VIDEOS"]

    def tearDown(self):
        source.stop_backfill()
        self.wait_backfill()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait_backfill(self):
        t = source._BF["thread"]
        if t:
            t.join(10)

    def touch(self, name):
        (self.videos / name).write_bytes(b"\0" * 10)
        return name

    def kind(self, name):
        return source.describe(name)["kind"]


class ClassifyTests(Base):
    """우리 채널 주소를 어떤 꼴로 넣어 두었든 같은 채널을 알아봄."""

    def check(self, channel_url, info, want):
        with mock.patch.dict(core.CONFIG, {"channel_url": channel_url}):
            self.assertEqual(source.classify(source.meta_of(info)), want, (channel_url, info))

    def test_channel_id_url(self):
        self.check(f"https://www.youtube.com/channel/{OWN_ID}", OWN_INFO, "own")
        self.check(f"https://www.youtube.com/channel/{OWN_ID}/videos?si=x", {"channel_id": OWN_ID}, "own")
        self.check(f"https://www.youtube.com/channel/{OWN_ID}", OTHER_INFO, "other")

    def test_handle_url_including_korean_and_encoded(self):
        self.check("https://www.youtube.com/@풋살사관학교", {"uploader_id": "@풋살사관학교"}, "own")
        self.check("https://www.youtube.com/@%ED%92%8B%EC%82%B4%EC%82%AC%EA%B4%80%ED%95%99%EA%B5%90", {"uploader_id": "@풋살사관학교"}, "own")
        self.check("youtube.com/@FutsalAcademy/", {"uploader_url": "https://www.youtube.com/@futsalacademy"}, "own")
        self.check("@FutsalAcademy", {"uploader_id": "@futsalacademy"}, "own")
        self.check("https://www.youtube.com/@FutsalAcademy", OTHER_INFO, "other")

    def test_custom_and_user_urls(self):
        self.check("https://www.youtube.com/c/FutsalAcademy", {"uploader_url": "https://www.youtube.com/c/futsalacademy", "channel": "x"}, "own")
        self.check("https://www.youtube.com/user/futsal1", {"uploader_url": "http://www.youtube.com/user/Futsal1"}, "own")
        self.check("https://www.youtube.com/user/futsal1", {"uploader_id": "futsal1"}, "own")  # 옛 yt-dlp 의 uploader_id
        self.check("https://www.youtube.com/FutsalAcademy", {"uploader_url": "https://www.youtube.com/c/FutsalAcademy"}, "own")
        self.check("https://www.youtube.com/c/FutsalAcademy", OTHER_INFO, "other")

    def test_no_channel_info_is_unknown(self):
        self.assertIsNone(source.classify({}))
        self.assertEqual(source.classify({"channel": "누군가"}), "other")

    def test_learned_own_id_from_own_listing(self):
        """주소가 /c/ 꼴이라 id 를 모를 때: 우리 채널 목록을 불러오면 채널 id 를 배워 둠."""
        with mock.patch.dict(core.CONFIG, {"channel_url": "https://www.youtube.com/c/FutsalAcademy"}):
            self.assertEqual(source.classify({"channelId": OWN_ID}), "other")
            rows = [{"id": "aaaaaaaaaaa", "title": "t", "channelId": OWN_ID, "uploaderId": "@futsal"}]
            source.annotate_listing(rows, "")
            self.assertEqual(rows[0]["source"]["kind"], "own")
            self.assertEqual(source.classify({"channelId": OWN_ID}), "own")
            self.assertEqual(source.classify({"uploaderId": "@Futsal"}), "own")

    def test_listing_of_other_channel(self):
        rows = [{"id": "bbbbbbbbbbb", "title": "t", **source.meta_of(OTHER_INFO, channel_only=True)}]
        source.annotate_listing(rows, "https://www.youtube.com/@shootforlove")
        self.assertEqual({k: rows[0]["source"][k] for k in ("kind", "channel", "channelKey")}, {"kind": "other", "channel": "슛포러브", "channelKey": OTHER_ID})
        rows = [{"id": "ccccccccccc", "title": "t"}]  # 채널 정보가 없으면 주소로
        source.annotate_listing(rows, self.CHANNEL + "/shorts")
        self.assertEqual(rows[0]["source"]["kind"], "own")
        rows = [{"id": "ddddddddddd", "title": "t"}]
        source.annotate_listing(rows, "https://www.youtube.com/@누군가")
        self.assertEqual(rows[0]["source"]["kind"], "other")


class FakeYDL:
    """yt_dlp.YoutubeDL 흉내: download 하면 진행 훅을 부르고 보관함에 파일을 만듦."""
    infos = {}

    def __init__(self, opts):
        self.opts = opts

    def download(self, urls):
        for u in urls:
            vid = u.rsplit("=", 1)[-1]
            info = dict(self.infos[vid], id=vid, title="패스 잘하는 법", upload_date="20240101", ext="mp4")
            for h in self.opts["progress_hooks"]:
                h({"status": "downloading", "info_dict": info, "downloaded_bytes": 5, "total_bytes": 10})
                h({"status": "finished", "info_dict": info})
            (Path(self.opts.get("paths", {}).get("home", "")) / self.opts["outtmpl"].replace("%(upload_date)s", "20240101")  # 폴더는 paths
             .replace("%(id)s", vid).replace("%(title).60B", "패스 잘하는 법").replace("%(ext)s", "mp4")).write_bytes(b"\0")

    def close(self):
        pass


class DownloadTests(Base):
    def download(self, ids, infos):
        FakeYDL.infos = infos
        fake = types.SimpleNamespace(YoutubeDL=FakeYDL)
        with mock.patch.object(core, "_yt", return_value=fake), mock.patch.object(core, "ensure_deno"):
            return core.download(ids, lambda m: None)

    def test_download_hook_stores_channel_metadata(self):
        self.assertEqual(self.download(["ownvid00001", "othvid00001"], {"ownvid00001": OWN_INFO, "othvid00001": OTHER_INFO}), [])
        own, other = yt_name("ownvid00001", "패스 잘하는 법"), yt_name("othvid00001", "패스 잘하는 법")
        self.assertEqual(self.kind(own), "own")
        d = source.describe(other)
        self.assertEqual((d["kind"], d["channel"], d["manual"]), ("other", "슛포러브", False))
        saved = json.loads(source.store_path().read_text(encoding="utf-8"))
        self.assertEqual(saved["files"][other]["channelId"], OTHER_ID)
        self.assertEqual(saved["ids"]["othvid00001"]["uploaderId"], "@shootforlove")
        self.assertEqual(saved["files"][own]["how"], "download")
        self.assertFalse(source.store_path().with_name(source.STORE + ".tmp").exists())

    def test_hint_from_listing_counts_before_info_arrives(self):
        """소재 찾기에서 고른 출처를 먼저 기록 → 정보 없이 끝나도(이미 받은 영상 등) 출처가 맞음."""
        source.remember_hints({"hintvid0001": {"kind": "other", "channel": "다른 유튜버"}, "../bad": {"kind": "own"}})
        name = self.touch(yt_name("hintvid0001"))
        d = source.describe(name)
        self.assertEqual((d["kind"], d["channel"]), ("other", "다른 유튜버"))
        self.assertNotIn("../bad", source.load()["ids"])
        # 받는 중에 온 진짜 정보가 이김
        self.download(["hintvid0001"], {"hintvid0001": OWN_INFO})
        self.assertEqual(self.kind(yt_name("hintvid0001", "패스 잘하는 법")), "own")
        self.assertEqual(self.kind(name), "own")  # 같은 영상 id

    def test_store_failure_does_not_fail_download(self):
        with mock.patch.object(source, "_write", side_effect=PermissionError("잠김")):
            self.assertEqual(self.download(["ownvid00002"], {"ownvid00002": OWN_INFO}), [])
        self.assertTrue((self.videos / yt_name("ownvid00002", "패스 잘하는 법")).exists())


class FootageTests(Base):
    def test_plain_file_is_footage(self):
        self.assertEqual(self.kind(self.touch("내 촬영 01 .mov")), "footage")
        self.assertEqual(self.kind(self.touch("IMG_1234.MP4")), "footage")

    def test_add_local_marks_footage(self):
        src = self.tmp / "가져올 곳" / "20240101_abcdefghijk_원본처럼 보이는 이름.mp4"
        src.parent.mkdir()
        src.write_bytes(b"\0")
        core.add_local([str(src)], lambda m: None)
        d = source.describe(src.name)
        self.assertEqual((d["kind"], d["auto"]), ("footage", "footage"))

    def test_bundle_is_footage(self):
        import bundle
        ff = core.ffmpeg()
        for i in (1, 2):
            r = core.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=1",
                          "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                          "-c:a", "aac", str(self.videos / f"촬영 {i}.mp4")])
            self.assertEqual(r.returncode, 0, r.stderr)
        out = bundle.make_bundle(["촬영 1.mp4", "촬영 2.mp4"], "연습 경기", lambda m: None)
        d = source.describe(out["name"])
        self.assertEqual(d["kind"], "footage")
        self.assertEqual(source.load()["files"][out["name"]]["how"], "bundle")


class ManualTests(Base):
    def test_manual_override_wins_and_persists(self):
        source.record_download([dict(OTHER_INFO, id="othvid00009")])
        name = self.touch(yt_name("othvid00009"))
        self.assertEqual(self.kind(name), "other")
        d = source.set_manual(name, "own")
        self.assertEqual((d["kind"], d["auto"], d["manual"]), ("own", "other", True))
        source._CACHE["key"] = None  # 앱을 다시 켠 것처럼 파일에서 다시 읽음
        self.assertEqual(self.kind(name), "own")
        # 다시 받아 자동 정보가 바뀌어도 직접 고른 것이 이김
        source.record_download([dict(OTHER_INFO, id="othvid00009")])
        self.assertEqual(self.kind(name), "own")
        self.assertEqual(source.set_manual(name, "auto")["kind"], "other")

    def test_manual_footage_and_unknown_override(self):
        name = self.touch(yt_name("unkvid00001"))
        self.assertEqual(self.kind(name), "unknown")
        self.assertEqual(source.set_manual(name, "footage")["kind"], "footage")
        self.assertEqual(source.pending(), [])

    def test_bad_input(self):
        name = self.touch("내 영상.mp4")
        with self.assertRaises(ValueError):
            source.set_manual(name, "friend")
        with self.assertRaises(FileNotFoundError):
            source.set_manual("없는 영상.mp4", "own")
        with self.assertRaises(FileNotFoundError):
            source.set_manual(source.STORE, "own")  # 기록 파일 자체는 영상이 아님


class StoreTests(Base):
    def test_corrupt_store_falls_back_and_keeps_bad_copy(self):
        source.store_path().write_text("{ 깨진 파일", encoding="utf-8")
        name = self.touch(yt_name("unkvid00002"))
        self.assertEqual(self.kind(name), "unknown")
        bad = source.store_path().with_name(source.STORE + ".bad")
        self.assertEqual(bad.read_text(encoding="utf-8"), "{ 깨진 파일")
        source.set_manual(name, "other")
        self.assertEqual(json.loads(source.store_path().read_text(encoding="utf-8"))["files"][name]["manual"], "other")
        self.assertEqual(bad.read_text(encoding="utf-8"), "{ 깨진 파일")

    def test_wrong_shape_store_is_ignored(self):
        source.store_path().write_text(json.dumps({"files": []}), encoding="utf-8")
        self.assertEqual(source.load()["files"], {})
        self.assertTrue(source.store_path().with_name(source.STORE + ".bad").exists())

    def test_locked_file_retries(self):
        real = source.os.replace
        calls = []

        def flaky(a, b):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("백신이 잡고 있음")
            return real(a, b)
        with mock.patch.object(source.os, "replace", side_effect=flaky):
            source.mark_footage("촬영.mp4")
        self.assertEqual(len(calls), 3)
        self.assertEqual(source.load()["files"]["촬영.mp4"]["kind"], "footage")

    def test_rename_and_delete_do_not_crash(self):
        source.record_download([dict(OTHER_INFO, id="renvid00001")])
        old = self.touch(yt_name("renvid00001", "원래 이름"))
        source.set_manual(old, "own")
        new = yt_name("renvid00001", "바꾼 이름 [최종]")
        (self.videos / old).rename(self.videos / new)
        self.assertEqual(self.kind(new), "own")  # 이름을 바꿔도 영상 id 기록으로
        plain = "내가 바꾼 이름.mp4"
        (self.videos / new).rename(self.videos / plain)
        self.assertEqual(self.kind(plain), "footage")
        (self.videos / plain).unlink()
        vids = source.annotate(core.local_videos())
        self.assertEqual(vids, [])
        with self.assertRaises(FileNotFoundError):
            source.set_manual(plain, "own")
        self.assertEqual(source.pending(), [])


class BackfillTests(Base):
    def test_backfill_from_channel_cache_by_id_and_title(self):
        by_id = self.touch(yt_name("cachevid001", "아무 제목"))
        by_title = self.touch(yt_name("cachevid002", "퍼스트 터치 꿀팁 ⧸ 풋살사관학교"))
        unknown = self.touch(yt_name("cachevid003", "다른 영상"))
        hooks._write_json(hooks.cache_path(), {"videos": [{"id": "cachevid001", "title": "x", "views": 1},
                                                          {"title": "퍼스트 터치 꿀팁 / 풋살사관학교 최경진", "views": 2}]})
        self.assertTrue(source.start_backfill(lambda m: None))
        self.wait_backfill()
        self.assertEqual(self.kind(by_id), "own")
        self.assertEqual(self.kind(by_title), "own")
        d = source.describe(unknown)
        self.assertEqual((d["kind"], d["checking"]), ("unknown", False))  # 인터넷이 안 되면 '알 수 없음'
        self.assertTrue(source._BF["offline"])

    def test_backfill_lookup_with_fake_ytdlp(self):
        a, b, c = (self.touch(yt_name(v)) for v in ("lookvid0001", "lookvid0002", "lookvid0003"))
        calls = []

        class YDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=True, process=True):
                calls.append((url, download, process))
                vid = url.rsplit("=", 1)[-1]
                if vid == "lookvid0003":
                    raise RuntimeError("Video unavailable")
                return dict(OWN_INFO if vid == "lookvid0001" else OTHER_INFO, id=vid)
        with mock.patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace(YoutubeDL=YDL)}), \
                mock.patch.object(source, "_online", return_value=True):
            self.assertTrue(source.start_backfill(lambda m: None))
            self.wait_backfill()
        self.assertEqual([c[1:] for c in calls], [(False, False)] * 3)  # 받지 않고 정보만
        self.assertEqual(self.kind(a), "own")
        self.assertEqual(source.describe(b)["channel"], "슛포러브")
        self.assertEqual(self.kind(c), "unknown")
        self.assertIn("Video unavailable", source.load()["lookup"]["lookvid0003"]["why"])
        # 실패한 영상은 하루 동안 다시 묻지 않음
        source.start_backfill(lambda m: None)
        self.wait_backfill()
        self.assertEqual(len(calls), 3)
        free = threading.Thread(target=lambda: calls.append(core._ENGINE_LOCK.acquire(blocking=False) and core._ENGINE_LOCK.release()))
        free.start()
        free.join()
        self.assertIsNone(calls[-1])  # 엔진 잠금을 놓았음 (다른 스레드가 잡을 수 있음)

    def test_backfill_stops_after_repeated_failures_and_can_be_cancelled(self):
        for i in range(6):
            self.touch(yt_name(f"failvid000{i}"))
        n = []

        class YDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, **kw):
                n.append(url)
                raise OSError("인터넷 끊김")
        logs = []
        with mock.patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace(YoutubeDL=YDL)}), \
                mock.patch.object(source, "_online", return_value=True):
            source.start_backfill(logs.append)
            self.wait_backfill()
        self.assertEqual(len(n), source.LOOKUP_FAILS)
        self.assertTrue(any("멈췄어요" in m for m in logs))
        self.assertTrue(source._paused(source.load()))
        # 잠시 멈춤 동안은 다음 영상도 묻지 않고, 알림도 다시 남기지 않음 (보관함을 10분마다 열어도)
        logs.clear()
        with mock.patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace(YoutubeDL=YDL)}), \
                mock.patch.object(source, "_online", return_value=True):
            source.start_backfill(logs.append)
            self.wait_backfill()
        self.assertEqual((len(n), logs), (source.LOOKUP_FAILS, []))
        source._update(lambda d: d["lookup"].pop(source.PAUSE_KEY))  # 멈춤 시간이 지난 셈
        # 멈추기: 기다리는 중에 멈추면 바로 끝남
        for i in range(3):
            self.touch(yt_name(f"stopvid000{i}"))
        n.clear()
        with mock.patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace(YoutubeDL=YDL)}), \
                mock.patch.object(source, "_online", return_value=True), mock.patch.object(source, "LOOKUP_GAP", 30):
            source.start_backfill(lambda m: None)
            for _ in range(100):
                if n:
                    break
                time.sleep(0.02)
            self.assertTrue(source.backfill_running())
            self.assertEqual(source.describe(yt_name("stopvid0001"))["checking"], True)  # '확인 중'
            source.stop_backfill()
            self.wait_backfill()
        self.assertFalse(source.backfill_running())
        self.assertEqual(len(n), 1)

    def test_backfill_skips_while_engine_busy(self):
        """영상 받기·엔진 바꾸기 중이면(엔진 잠금) 조회하지 않고 이번에는 그만 (다음에 보관함을 열면 다시)."""
        name = self.touch(yt_name("busyvid0001"))
        held, done = threading.Event(), threading.Event()

        def hold():
            with core._ENGINE_LOCK:
                held.set()
                done.wait(5)
        threading.Thread(target=hold, daemon=True).start()
        held.wait(2)
        try:
            real = source._engine_free
            with mock.patch.object(source, "_online", return_value=True), mock.patch.object(source, "_lookup") as lk, \
                    mock.patch.object(source, "_engine_free", side_effect=lambda stop: real(stop, wait=0.1)):
                source.start_backfill(lambda m: None)
                self.wait_backfill()
            lk.assert_not_called()
            self.assertEqual(self.kind(name), "unknown")
            self.assertNotIn("busyvid0001", source.load()["lookup"])  # 실패로 치지 않음
        finally:
            done.set()


class RouteTests(Base):
    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.patches2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.tmp / "studio.log")]
        for p in self.patches2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.patches2:
            p.stop()
        super().tearDown()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_job(self):
        for _ in range(200):
            if not self.app.JOB["name"]:
                return
            time.sleep(0.05)

    def test_state_and_manual_route(self):
        name = self.touch(yt_name("routvid0001", "한글 이름 [꿀팁]"))
        foot = self.touch("촬영 원본.mp4")
        local = {v["name"]: v for v in self.call("/api/state?since=0")[1]["local"]}
        self.assertEqual(local[name]["source"]["kind"], "unknown")
        self.assertEqual(local[foot]["source"]["kind"], "footage")
        self.assertIn("size_mb", local[name])  # 예전 칸은 그대로
        code, j = self.call("/api/source", {"name": name, "kind": "other"})
        self.assertEqual((code, j["source"]["kind"], j["source"]["manual"]), (200, "other", True))
        local = {v["name"]: v for v in self.call("/api/state?since=0")[1]["local"]}
        self.assertEqual(local[name]["source"]["kind"], "other")
        self.assertEqual(self.call("/api/source", {"name": name, "kind": "x"})[0], 400)
        self.assertEqual(self.call("/api/source", {"name": "..\\x.mp4", "kind": "own"})[0], 400)
        self.assertEqual(self.call("/api/source", {"name": "없음.mp4", "kind": "own"})[0], 404)

    def test_check_route_starts_and_stops_in_background(self):
        with mock.patch.object(source, "start_backfill") as st, mock.patch.object(source, "stop_backfill") as sp:
            self.assertEqual(self.call("/api/source/check", {})[0], 200)
            st.assert_called_once()
            self.assertEqual(self.call("/api/source/check", {"stop": True})[1]["ok"], True)
            sp.assert_called_once()
        self.assertIsNone(self.app.JOB["name"])  # 작업(한 번에 하나)을 차지하지 않음

    def test_list_and_download_carry_source(self):
        rows = [{"id": "listvid0001", "title": "남의 영상", "views": 5, "duration": 60, "kind": "videos",
                 **source.meta_of(OTHER_INFO, channel_only=True)}]
        with mock.patch.object(core, "list_videos", return_value=rows):
            self.assertEqual(self.call("/api/list", {"kind": "videos", "url": "https://www.youtube.com/@shootforlove"})[0], 200)
            self.wait_job()
        res = self.call("/api/state?since=0")[1]["result"]
        self.assertEqual((res[0]["source"]["kind"], res[0]["source"]["channel"], res[0]["source"]["channelKey"]), ("other", "슛포러브", OTHER_ID))
        self.assertEqual(res[0]["title"], "남의 영상")

        def fake_download(ids, log, ck=None):
            for v in ids:
                (self.videos / yt_name(v)).write_bytes(b"\0")
            return []
        with mock.patch.object(core, "download", side_effect=fake_download):
            self.call("/api/download", {"ids": ["listvid0001"], "sources": {"listvid0001": res[0]["source"]}})
            self.wait_job()
        local = {v["name"]: v for v in self.call("/api/state?since=0")[1]["local"]}
        self.assertEqual(local[yt_name("listvid0001")]["source"]["kind"], "other")
        self.assertEqual(local[yt_name("listvid0001")]["source"]["channel"], "슛포러브")


DOB_ID = "UCdoblockdoblockdoblock1"
DOB_INFO = {"channel": "도블락", "channel_id": DOB_ID, "channel_url": f"https://www.youtube.com/channel/{DOB_ID}",
            "uploader_id": "@doblock", "uploader_url": "https://www.youtube.com/@doblock"}


class ChannelTests(Base):
    """다른 채널을 채널별로 (슛포러브 · 도블락 …): 묶기 · 개수 · 이름 바뀜 · 색 · 직접 고르기."""

    def add(self, vid, info, title="패스 잘하는 법"):
        source.record_download([dict(info, id=vid)])
        return self.touch(yt_name(vid, title))

    def test_group_counts_by_channel_sorted(self):
        for i in range(3):
            self.add(f"sfl{i}xxxxxxx"[:11], OTHER_INFO)
        for i in range(5):
            self.add(f"dob{i}xxxxxxx"[:11], DOB_INFO)
        self.add("ownxxxxxxx1", OWN_INFO)
        self.touch("내 촬영 1.mp4")
        self.touch(yt_name("unkxxxxxxx1"))
        vids = source.annotate(core.local_videos())
        s = source.summary(vids)
        self.assertEqual({k: s[k] for k in ("all", "own", "other", "footage", "unknown")},
                         {"all": 11, "own": 1, "other": 8, "footage": 1, "unknown": 1})
        self.assertEqual([(c["channel"], c["count"], c["channelKey"]) for c in s["channels"]],
                         [("도블락", 5, DOB_ID), ("슛포러브", 3, OTHER_ID)])
        self.assertNotEqual(s["channels"][0]["color"], s["channels"][1]["color"])
        by = {v["name"]: v["source"] for v in vids}
        self.assertEqual(by[yt_name("dob0xxxxxxx", "패스 잘하는 법")]["channel"], "도블락")
        self.assertNotIn("channelKey", by[yt_name("ownxxxxxxx1", "패스 잘하는 법")])  # 우리 채널·촬영본은 채널 묶음 없음

    def test_renamed_channel_stays_one_group_with_latest_name(self):
        a = self.add("renamevid01", OTHER_INFO)
        b = self.add("renamevid02", dict(OTHER_INFO, channel="슛포러브 SHOOT FOR LOVE"))
        da, db = source.describe(a), source.describe(b)
        self.assertEqual(da["channelKey"], db["channelKey"])
        self.assertEqual((da["channel"], db["channel"]), ("슛포러브 SHOOT FOR LOVE",) * 2)
        self.assertEqual(da["color"], db["color"])
        s = source.summary(source.annotate(core.local_videos()))
        self.assertEqual([(c["channel"], c["count"]) for c in s["channels"]], [("슛포러브 SHOOT FOR LOVE", 2)])

    def test_name_only_hint_then_download_merges_into_channel_id(self):
        """소재 찾기 목록이 채널 이름만 준 경우: 받을 때 채널 id 를 알게 되면 같은 묶음 (색 그대로)."""
        source.remember_hints({"hintonly001": {"kind": "other", "channel": "도블락"}})
        name = self.touch(yt_name("hintonly001"))
        d = source.describe(name)
        self.assertEqual(d["channelKey"], "n:도블락")
        color = d["color"]
        other = self.add("dobfull0001", DOB_INFO)
        d1, d2 = source.describe(name), source.describe(other)
        self.assertEqual((d1["channelKey"], d2["channelKey"]), (DOB_ID, DOB_ID))
        self.assertEqual((d1["color"], d2["color"]), (color, color))
        self.assertNotIn("n:도블락", source.load()["channels"])

    def test_colors_are_stable_and_distinct_for_palette_size(self):
        ids = [f"UC{i:022d}" for i in range(source.PALETTE)]
        for i, cid in enumerate(ids):
            self.add(f"colvid{i:05d}", {"channel": f"채널{i}", "channel_id": cid})
        colors = [source.channel_view(cid)["color"] for cid in ids]
        self.assertEqual(sorted(colors), list(range(source.PALETTE)))
        source._CACHE["key"] = None
        self.assertEqual([source.channel_view(cid)["color"] for cid in ids], colors)  # 다시 켜도 같은 색
        self.add("colvid99999", {"channel": "채널0 새 이름", "channel_id": ids[0]})
        self.assertEqual(source.channel_view(ids[0])["color"], colors[0])

    def test_manual_pick_existing_channel(self):
        self.add("dobvid00001", DOB_INFO)
        name = self.add("sflvid00001", OTHER_INFO)
        d = source.set_manual(name, "other", channel_key_=DOB_ID)
        self.assertEqual((d["kind"], d["channel"], d["channelKey"], d["manual"]), ("other", "도블락", DOB_ID, True))
        self.assertNotEqual(d.get("channelUrl"), OTHER_INFO["channel_url"])
        source._CACHE["key"] = None
        self.assertEqual(source.describe(name)["channelKey"], DOB_ID)
        s = source.summary(source.annotate(core.local_videos()))
        self.assertEqual([(c["channel"], c["count"]) for c in s["channels"]], [("도블락", 2)])
        # 다시 받아도 직접 고른 채널이 이김
        source.record_download([dict(OTHER_INFO, id="sflvid00001")])
        self.assertEqual(source.describe(name)["channelKey"], DOB_ID)
        self.assertEqual(source.set_manual(name, "auto")["channelKey"], OTHER_ID)
        with self.assertRaises(ValueError):
            source.set_manual(name, "other", channel_key_="UCnotknownnotknownnotkn")

    def test_manual_typed_channel_name(self):
        unknown = self.touch(yt_name("typedvid001"))
        d = source.set_manual(unknown, "other", channel="  새 채널   이름 ")
        self.assertEqual((d["kind"], d["channel"], d["channelKey"]), ("other", "새 채널 이름", "n:새채널이름"))
        # 이미 아는 채널 이름을 적으면 (대소문자·띄어쓰기 달라도) 그 채널로
        self.add("sflvid00002", OTHER_INFO)
        foot = self.touch("촬영 원본 2.mp4")
        d = source.set_manual(foot, "other", channel="슛포 러브")
        self.assertEqual(d["channelKey"], OTHER_ID)
        # 다른 출처로 바꾸면 채널 고른 것은 지움
        d = source.set_manual(foot, "own")
        self.assertNotIn("channelKey", d)
        self.assertNotIn("manualChannel", source.load()["files"][foot])

    def test_manual_other_without_channel_keeps_unnamed_group(self):
        name = self.touch(yt_name("noname00001"))
        d = source.set_manual(name, "other")
        self.assertEqual(d["kind"], "other")
        self.assertNotIn("channelKey", d)
        s = source.summary(source.annotate(core.local_videos()))
        self.assertEqual(s["channels"], [{"channelKey": "", "channel": "", "color": None, "count": 1}])

    def test_add_local_with_youtube_like_name_ignores_id_record(self):
        source.record_download([dict(OTHER_INFO, id="dupvid00001")])
        src = self.tmp / "밖" / yt_name("dupvid00001")
        src.parent.mkdir()
        src.write_bytes(b"\0")
        core.add_local([str(src)], lambda m: None)
        self.assertEqual(self.kind(src.name), "footage")
        source.record_download([dict(OTHER_INFO, id="dupvid00001")])
        self.assertEqual(self.kind(src.name), "footage")


class ChannelRouteTests(RouteTests):
    """/api/source 로 채널 고르기 · /api/state 의 sources(고르기 칩 개수). RouteTests 의 서버 준비를 그대로 씀."""
    test_state_and_manual_route = test_check_route_starts_and_stops_in_background = test_list_and_download_carry_source = None

    def test_route_manual_channel_and_state_summary(self):
        source.record_download([dict(DOB_INFO, id="rdobvid0001")])
        a = self.touch(yt_name("rdobvid0001"))
        b = self.touch(yt_name("rnewvid0001", "한글 [제목]"))
        code, j = self.call("/api/source", {"name": b, "kind": "other", "channel": "도블락"})
        self.assertEqual((code, j["source"]["channelKey"]), (200, DOB_ID))
        code, j = self.call("/api/source", {"name": b, "kind": "other", "channelKey": "n:없는채널"})
        self.assertEqual(code, 400)
        st = self.call("/api/state?since=0")[1]
        self.assertEqual(st["sources"]["channels"][0]["count"], 2)
        self.assertEqual(st["sources"]["other"], 2)
        self.assertEqual({v["name"] for v in st["local"]}, {a, b})


if __name__ == "__main__":
    unittest.main()


class ReviewRegressionTests(Base):
    """검토에서 나온 문제의 재발 방지."""

    def _ydl(self, handler, calls):
        class YDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=True, process=True):
                calls.append(url)
                return handler(url)
        return types.SimpleNamespace(YoutubeDL=YDL)

    # --- 높음: 잠깐 못 읽었다고 기록을 지우면 안 됨 ---
    def test_read_failure_does_not_wipe_store(self):
        source.record_download([dict(OTHER_INFO, id=f"keep{i:07d}") for i in range(50)])
        name = self.touch(yt_name("keep0000001"))
        source.set_manual(name, "own")
        before = json.loads(source.store_path().read_text(encoding="utf-8"))
        real = Path.read_bytes
        fails = {"n": 0}

        def locked(path):
            if path.name == source.STORE and fails["n"] > 0:
                fails["n"] -= 1
                raise PermissionError("백신이 잡고 있음")
            return real(path)
        with mock.patch.object(Path, "read_bytes", locked), mock.patch.object(source.time, "sleep"):
            # 계속 잠겨 있음: 읽기는 빈 기록(기억하지 않음) · 쓰기는 하지 않음
            fails["n"] = 10 ** 6
            source._CACHE["key"] = None
            self.assertEqual(source.load()["ids"], {})
            source.mark_footage("새 촬영.mp4")
            source.remember_hints({"hintvid0001": {"kind": "other", "channel": "도블락"}})
            with self.assertRaises(OSError):
                source.set_manual(name, "footage")
            with self.assertRaises(source.StoreBusy):
                source.record_download([dict(OWN_INFO, id="newvid00001")])
            self.assertEqual(json.loads(source.store_path().read_text(encoding="utf-8")), before)
            self.assertFalse(source.store_path().with_name(source.STORE + ".bad").exists())
            # 한 번만 잠김: 다시 읽어서 기존 기록 위에 씀
            fails["n"] = 1
            source._CACHE["key"] = None
            source.mark_footage("새 촬영.mp4")
        after = source.load()
        self.assertEqual(len(after["ids"]), 50)
        self.assertEqual(after["files"]["새 촬영.mp4"]["kind"], "footage")
        self.assertEqual(self.kind(name), "own")  # 직접 고른 것도 그대로

    def test_state_route_survives_locked_store(self):
        name = self.touch(yt_name("lockvid0001"))
        source.set_manual(name, "own")
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("잠김")), mock.patch.object(source.time, "sleep"):
            source._CACHE["key"] = None
            vids = source.annotate(core.local_videos())
        self.assertEqual(vids[0]["source"]["kind"], "unknown")
        self.assertEqual(self.kind(name), "own")

    # --- 중간: 제목 짐작은 확실한 조회보다 뒤 ---
    def test_title_match_is_only_a_guess_and_online_lookup_wins(self):
        n = self.touch(yt_name("guessvid001", "Futsal Skills Training"))
        hooks._write_json(hooks.cache_path(), {"videos": [{"title": "Futsal Skills Training 2편", "views": 1}]})  # 예전 기억 (id 없음)
        source.start_backfill(lambda m: None)  # 인터넷이 안 됨 → 짐작
        self.wait_backfill()
        d = source.describe(n)
        self.assertEqual((d["kind"], d.get("guess")), ("own", True))
        self.assertEqual(source.pending(), [])
        self.assertEqual(source.guessed(), [n])
        calls = []
        fake = self._ydl(lambda url: dict(OTHER_INFO, id=url.rsplit("=", 1)[-1]), calls)
        with mock.patch.dict(sys.modules, {"yt_dlp": fake}), mock.patch.object(source, "_online", return_value=True):
            self.assertTrue(source.start_backfill(lambda m: None))  # 짐작한 영상도 다시 확인
            self.wait_backfill()
        d = source.describe(n)
        self.assertEqual((d["kind"], d.get("channel"), d.get("guess")), ("other", "슛포러브", None))
        self.assertEqual(source.guessed(), [])

    def test_online_lookup_runs_before_title_guess(self):
        n = self.touch(yt_name("guessvid002", "풋살 드리블 기술 모음"))
        hooks._write_json(hooks.cache_path(), {"videos": [{"title": "풋살 드리블 기술 모음 1탄", "views": 1}]})
        calls = []
        fake = self._ydl(lambda url: dict(OTHER_INFO, id=url.rsplit("=", 1)[-1]), calls)
        with mock.patch.dict(sys.modules, {"yt_dlp": fake}), mock.patch.object(source, "_online", return_value=True):
            source.start_backfill(lambda m: None)
            self.wait_backfill()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.kind(n), "other")
        self.assertNotIn("guess", source.describe(n))

    def test_new_cache_with_ids_does_not_match_titles(self):
        n = self.touch(yt_name("guessvid003", "풋살 드리블 기술 모음"))
        hooks._write_json(hooks.cache_path(), {"videos": [{"id": "ownvid00009", "title": "풋살 드리블 기술 모음 1탄", "views": 1}]})
        self.assertEqual(source.from_channel_cache([n]), {})
        source.start_backfill(lambda m: None)
        self.wait_backfill()
        self.assertEqual(self.kind(n), "unknown")

    def test_failed_lookup_falls_back_to_title_guess(self):
        n = self.touch(yt_name("guessvid004", "퍼스트 터치 꿀팁 모음"))
        hooks._write_json(hooks.cache_path(), {"videos": [{"title": "퍼스트 터치 꿀팁 모음 / 풋살사관학교", "views": 1}]})

        def boom(url):
            raise RuntimeError("Video unavailable")
        with mock.patch.dict(sys.modules, {"yt_dlp": self._ydl(boom, [])}), mock.patch.object(source, "_online", return_value=True):
            source.start_backfill(lambda m: None)
            self.wait_backfill()
        self.assertEqual(source.describe(n).get("guess"), True)

    # --- 중간: /c/·/user/·옛 주소 꼴 우리 채널 ---
    def test_custom_url_config_with_modern_info_resolves_to_own(self):
        modern = dict(OWN_INFO, uploader_id="@futsalacademy", uploader_url="https://www.youtube.com/@futsalacademy")
        for cfg in ("https://www.youtube.com/c/FutsalAcademy", "https://www.youtube.com/user/futsalacademy",
                    "https://www.youtube.com/FutsalAcademy"):
            with self.subTest(cfg=cfg):
                source.store_path().unlink(missing_ok=True)
                source._CACHE["key"] = None
                with mock.patch.dict(core.CONFIG, {"channel_url": cfg}):
                    source.record_download([dict(modern, id="ownmodern01")])
                    name = self.touch(yt_name("ownmodern01"))
                    self.assertEqual(self.kind(name), "other")  # 요즘 yt-dlp 정보에는 /c/ 주소가 없음
                    calls = []
                    fake = self._ydl(lambda url: {"_type": "playlist", "channel_id": OWN_ID, "uploader_id": "@futsalacademy",
                                                  "entries": []}, calls)
                    with mock.patch.dict(sys.modules, {"yt_dlp": fake}), mock.patch.object(source, "_online", return_value=True):
                        self.assertTrue(source.start_backfill(lambda m: None))  # 모르는 영상이 없어도 채널 주소를 바꿔 봄
                        self.wait_backfill()
                    self.assertEqual(len(calls), 1)
                    self.assertTrue(calls[0].endswith("/videos"))
                    self.assertEqual(self.kind(name), "own")
                    self.assertEqual(source.summary(source.annotate(core.local_videos()))["channels"], [])
                    self.assertFalse(source.start_backfill(lambda m: None))  # 한 번 바꿨으면 다시 묻지 않음
                    (self.videos / name).unlink()

    def test_custom_url_resolve_failure_retries_next_day(self):
        with mock.patch.dict(core.CONFIG, {"channel_url": "https://www.youtube.com/c/FutsalAcademy"}):
            calls = []

            def boom(url):
                raise RuntimeError("HTTP Error 404")
            with mock.patch.dict(sys.modules, {"yt_dlp": self._ydl(boom, calls)}), \
                    mock.patch.object(source, "_online", return_value=True):
                self.assertTrue(source.start_backfill(lambda m: None))
                self.wait_backfill()
                self.assertFalse(source.start_backfill(lambda m: None))
            self.assertEqual(len(calls), 1)

    # --- 낮음: 막히면 한동안 묻지 않음 ---
    def test_blocked_lookup_pauses_immediately_and_logs_once(self):
        for i in range(5):
            self.touch(yt_name(f"blockvid00{i}"))
        calls, logs = [], []

        def blocked(url):
            raise RuntimeError("Sign in to confirm you're not a bot")
        fake = self._ydl(blocked, calls)
        with mock.patch.dict(sys.modules, {"yt_dlp": fake}), mock.patch.object(source, "_online", return_value=True):
            for _ in range(3):
                source.start_backfill(logs.append)
                self.wait_backfill()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len([m for m in logs if "멈췄어요" in m]), 1)

    # --- 낮음: 이상한 기록 한 줄 ---
    def test_malformed_record_does_not_hide_other_sources(self):
        good = self.touch(yt_name("goodvid0001"))
        bad = self.touch(yt_name("badvid00001"))
        source.record_download([dict(OTHER_INFO, id="goodvid0001")])
        d = json.loads(source.store_path().read_text(encoding="utf-8"))
        d["files"][bad] = "own"
        d["ids"]["badvid00001"] = ["x"]
        d["channels"]["broken"] = 3
        d["own"]["ids"] = "UCabc"
        source.store_path().write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        vids = {v["name"]: v["source"] for v in source.annotate(core.local_videos())}
        self.assertEqual((vids[good]["kind"], vids[good]["channel"]), ("other", "슛포러브"))
        self.assertEqual(vids[bad]["kind"], "unknown")
        self.assertEqual(source.summary(list({"source": s} for s in vids.values()))["other"], 1)
        source.set_manual(bad, "footage")  # 그 영상도 다시 고칠 수 있음
        self.assertEqual(self.kind(bad), "footage")

    def test_deleted_videos_do_not_pause_lookup(self):
        for i in range(5):
            self.touch(yt_name(f"gonevid000{i}"))
        calls, logs = [], []

        def gone(url):
            raise RuntimeError("ERROR: [youtube] x: This video is unavailable")
        with mock.patch.dict(sys.modules, {"yt_dlp": self._ydl(gone, calls)}), mock.patch.object(source, "_online", return_value=True):
            source.start_backfill(logs.append)
            self.wait_backfill()
        self.assertEqual(len(calls), 5)  # 지운 영상이 연달아 있어도 끝까지 물어봄
        self.assertFalse(source._paused(source.load()))
        self.assertFalse(any("멈췄어요" in m for m in logs))
