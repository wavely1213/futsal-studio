"""학습용 영상 보관함(refs.py) 테스트 — 스타일 배우기 전용 영상을 편집용 보관함(videos)과 따로 둔다.

기록 파일(임시 파일 → 교체 · 깨진 파일 · 잠김) · 보관함·편집실 목록과 섞이지 않음 · 이름으로 파일·분석 폴더 찾기(core.video_file·adir)
· 학습용 영상으로 스타일 배우기(진짜 ffmpeg 짧은 영상 · 분석 기록은 학습용 폴더에) · 배운 뒤 파일 지우기(기록으로 다시 배움)
· 보관함 → 학습용 옮기기(한글·띄어쓰기·쪼개진 자모 이름 · 분석 폴더 함께 · 잠긴 파일 다시 시도·되돌림 · 우리 채널은 확인 뒤에만)
· 추천 채널 목록(ref_channels.json) · 채널 인기 영상 받기(가짜 yt-dlp · 받는 곳·출처 기록 분리 · 자동 배우기) · YouTube 막힘 안내 · /api/refs/*.
인터넷은 쓰지 않는다.
실행: 저장소 폴더에서 python3 -m unittest tests.test_refs_library
"""
import json
import os
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
import avmodels  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import face  # noqa: E402
import plan  # noqa: E402
import refs  # noqa: E402
import source  # noqa: E402
import style  # noqa: E402

OTHER_ID = "UCabcdefghijklmnopqrstuv"
OTHER_INFO = {"channel": "슛포러브", "channel_id": OTHER_ID, "channel_url": f"https://www.youtube.com/channel/{OTHER_ID}",
              "uploader_id": "@shootforlovekorea", "uploader_url": "https://www.youtube.com/@shootforlovekorea"}
DOB_ID = "UCdoblockdoblockdoblock1"
DOB_INFO = {"channel": "도블락", "channel_id": DOB_ID, "channel_url": f"https://www.youtube.com/channel/{DOB_ID}",
            "uploader_id": "@gkdoblock", "uploader_url": "https://www.youtube.com/@gkdoblock"}
JAMO = "정형ᄃ"  # 제목이 잘려 쪼개진 한글 자모가 남은 이름 (v1.9.2 사례)
M = {}


def quiet(*a):
    pass


def yt_name(vid, title="패스 [꿀팁] 잘하는 법"):
    return f"20240101_{vid}_{title}.mp4"


def setUpModule():
    M["tmp"] = Path(tempfile.mkdtemp(prefix="학습용 영상 시험 "))
    M["video"] = M["tmp"] / "clip.mp4"
    # 6초 · 320×180 · 3초에 장면이 바뀜 + 소리 (스타일 기록이 생길 만큼)
    r = core.run([core.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=3",
                  "-f", "lavfi", "-i", "smptebars=size=320x180:rate=15:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                  "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(M["video"])])
    assert r.returncode == 0, r.stderr


def tearDownModule():
    shutil.rmtree(M.get("tmp", ""), ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="학습용 작업 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        self.patches += [mock.patch.object(style, "STYLES", work / "styles"), mock.patch.object(source, "_online", return_value=False),
                         mock.patch.object(refs, "WAIT", 0.001)]
        for p in self.patches:
            p.start()
        refs._CACHE.update(key=None, data=None)
        editor.CANCEL.clear()
        self.videos, self.work = dirs["VIDEOS"], work

    def tearDown(self):
        for p in self.patches:
            p.stop()
        refs._CACHE.update(key=None, data=None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lib_video(self, name, real=False):
        p = self.videos / name
        if real:
            shutil.copy2(M["video"], p)
        else:
            p.write_bytes(b"\0" * 1000)
        return name

    def ref_video(self, name, info=OTHER_INFO, real=False):
        """학습용 영상 하나를 기록과 함께 넣음 (받기를 거치지 않음)."""
        meta = source.meta_of(info, channel_only=True)

        def fn(d):
            key = refs._register(d, meta)
            folder = d["channels"][key]["folder"]
            (refs.root() / folder).mkdir(parents=True, exist_ok=True)
            p = refs.root() / folder / name
            shutil.copy2(M["video"], p) if real else p.write_bytes(b"\0" * 2000)
            d["files"][name] = {"channelKey": key, "folder": folder, "videoId": source.video_id(name), "how": "download"}
        refs._update(fn)
        return name


# ---------- 기록 파일 ----------

class StoreTests(Base):
    def test_atomic_write_and_reload(self):
        self.ref_video(yt_name("aaaaaaaaaa1"))
        self.assertTrue(refs.store_path().is_file())
        self.assertFalse(list(refs.root().glob("*.tmp")), "임시 파일이 남지 않음")
        refs._CACHE.update(key=None, data=None)
        self.assertIn(yt_name("aaaaaaaaaa1"), refs.load()["files"])

    def test_corrupt_store_kept_as_bad_and_empty(self):
        refs.root().mkdir(parents=True)
        refs.store_path().write_text("{깨짐", encoding="utf-8")
        self.assertEqual(refs.load()["files"], {})
        self.assertTrue(refs.store_path().with_name(refs.STORE + ".bad").exists())

    def test_bad_rows_dropped_others_kept(self):
        refs.root().mkdir(parents=True)
        refs.store_path().write_text(json.dumps({"files": {"ok.mp4": {"folder": "슛포러브"}, "../x.mp4": {"folder": "a"},
                                                           "b.mp4": {"folder": "../밖"}, "c.mp4": "문자"}}), encoding="utf-8")
        self.assertEqual(list(refs.load()["files"]), ["ok.mp4"])

    def test_locked_store_is_not_overwritten(self):
        self.ref_video(yt_name("aaaaaaaaaa2"))
        before = refs.store_path().read_bytes()
        refs._CACHE.update(key=None, data=None)
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("잠김")):
            with self.assertRaises(refs.StoreBusy):
                refs._update(lambda d: d["files"].clear())
        self.assertEqual(refs.store_path().read_bytes(), before)


# ---------- 편집용 보관함과 섞이지 않음 · 이름으로 찾기 ----------

class IsolationTests(Base):
    def test_refs_not_in_library_lists(self):
        lib = self.lib_video(yt_name("libvid00001"))
        ref = self.ref_video(yt_name("refvid00001"))
        self.assertEqual([v["name"] for v in core.local_videos()], [lib])
        self.assertEqual([v["file"] for v in editor.library()["videos"]], [lib])
        self.assertEqual(source._library(), [lib])
        with self.assertRaises(FileNotFoundError):
            editor.video_path(ref)  # 편집실·썸네일·올리기가 쓰는 확인
        self.assertEqual([v["name"] for v in refs.listing()["videos"]], [ref])

    def test_resolver_for_refs_and_library_unchanged(self):
        lib = self.lib_video(yt_name("libvid00002"))
        ref = self.ref_video(yt_name("refvid00002", "한글 제목 [꿀팁] "))
        self.assertEqual(core.video_file(lib), self.videos / lib)
        self.assertEqual(core.adir(lib), core.ANALYSIS / Path(lib).stem)
        folder = refs.find(ref)["folder"]
        self.assertEqual(folder, "슛포러브")
        self.assertEqual(core.video_file(ref), refs.root() / "슛포러브" / ref)
        self.assertEqual(core.adir(ref), refs.root() / "슛포러브" / refs.ANALYSIS_DIR / Path(ref).stem.rstrip(" ."))
        # 어디에도 없는 이름은 예전처럼 analysis/<이름>
        self.assertEqual(core.adir("없는 영상.mp4"), core.ANALYSIS / "없는 영상")
        self.assertEqual(core.video_file("없는 영상.mp4"), self.videos / "없는 영상.mp4")
        # 같은 이름이 보관함에도 있으면 보관함이 먼저 (편집 동작은 그대로)
        self.lib_video(ref)
        self.assertEqual(core.video_file(ref), self.videos / ref)
        self.assertEqual(core.adir(ref), core.ANALYSIS / Path(ref).stem.rstrip(" ."))

    def test_broken_refs_store_does_not_break_library(self):
        lib = self.lib_video(yt_name("libvid00003"))
        with mock.patch.object(refs, "find", side_effect=RuntimeError("깨짐")):
            self.assertEqual(core.adir("없는.mp4"), core.ANALYSIS / "없는")
            self.assertEqual(core.video_file(lib), self.videos / lib)

    def test_channel_folder_names_are_windows_safe_and_unique(self):
        d = refs._empty()
        a = refs._register(d, {"channel": 'CON'})
        b = refs._register(d, {"channel": '슛:포*러브?. '})
        c = refs._register(d, {"channelId": "UCzzzzzzzzzzzzzzzzzzzzzz", "channel": "슛포러브"})
        e = refs._register(d, {})
        folders = [d["channels"][k]["folder"] for k in (a, b, c, e)]
        self.assertEqual(folders, ["CON 채널", "슛포러브", "슛포러브 (2)", "채널 모름"])
        self.assertTrue(all(isinstance(d["channels"][k].get("color"), int) for k in (a, b, c)))

    def test_color_matches_library_channel(self):
        """보관함(sources.json)이 아는 채널이면 같은 색."""
        source.record_download([dict(OTHER_INFO, id="libvid00004")])
        want = source.load()["channels"][OTHER_ID]["color"]
        self.ref_video(yt_name("refvid00004"))
        self.assertEqual(refs.listing()["channels"][0]["color"], want)

    def test_orphan_file_in_channel_folder_is_adopted(self):
        self.ref_video(yt_name("refvid00005"))
        (refs.root() / "슛포러브" / yt_name("refvid00006")).write_bytes(b"\0")
        names = {v["name"]: v for v in refs.listing()["videos"]}
        self.assertEqual(names[yt_name("refvid00006")]["channel"], "슛포러브")
        self.assertIn(yt_name("refvid00006"), refs.load()["files"])


# ---------- 학습용 영상으로 배우기 · 배운 뒤 파일 지우기 ----------

class LearnTests(Base):
    def setUp(self):
        super().setUp()
        self.p2 = [mock.patch.object(avmodels, "ensure", return_value=False), mock.patch.object(face, "ready", return_value=False)]
        for p in self.p2:
            p.start()

    def tearDown(self):
        for p in self.p2:
            p.stop()
        super().tearDown()

    def test_style_events_and_plan_for_refs_live_in_refs_folder(self):
        ref = self.ref_video(yt_name("refvid00010", "도블락 [대결] 영상"), real=True)
        r = style.learn("슛포러브 스타일", [ref], quiet)
        self.assertEqual(r["name"], "슛포러브 스타일")
        d = refs.adir_of(ref)
        self.assertTrue((d / "style_events.json").is_file())
        self.assertTrue((d / "plan_events.json").is_file())
        self.assertFalse(any(core.ANALYSIS.iterdir()), "편집용 analysis 폴더에는 아무것도 안 생김")
        v = refs.listing()["videos"][0]
        self.assertTrue(v["learned"])
        self.assertEqual(v["styles"], ["슛포러브 스타일"])

    def test_prune_keeps_style_usable_and_relearn_uses_cached_events(self):
        ref = self.ref_video(yt_name("refvid00011", JAMO), real=True)
        style.learn("슛포러브 스타일", [ref], quiet)
        before = json.loads((style.STYLES / "슛포러브 스타일.json").read_text(encoding="utf-8"))
        res = refs.prune([ref], quiet)
        self.assertEqual(res["pruned"], [ref])
        self.assertFalse(refs.path_of(ref).exists())
        self.assertTrue(refs.find(ref)["pruned"])
        self.assertEqual(core.kept_sig(ref), refs.find(ref)["sig"])
        v = refs.listing()["videos"][0]
        self.assertEqual((v["present"], v["pruned"], v["usable"]), (False, True, True))
        # 스타일은 그대로 쓰임
        self.assertEqual(style.list_styles()[0]["name"], "슛포러브 스타일")
        # 다시 배우기: 화면을 다시 보지 않고 남은 기록으로 (영상 파일이 없어도)
        with mock.patch.object(style, "_frames", side_effect=AssertionError("다시 보면 안 됨")), \
                mock.patch.object(plan, "_video_pass", side_effect=AssertionError("다시 보면 안 됨")):
            r = style.learn("슛포러브 스타일", [ref], quiet)
        after = json.loads((style.STYLES / "슛포러브 스타일.json").read_text(encoding="utf-8"))
        self.assertEqual(after["cutsPerMin"], before["cutsPerMin"])
        self.assertIn("plan", after, "기획 분석도 남은 기록으로")
        self.assertEqual(r["profile"]["source"], ref)

    def test_prune_skips_unlearned_or_changed_file(self):
        ref = self.ref_video(yt_name("refvid00012"), real=True)
        self.assertEqual(refs.prune([ref], quiet)["kept"], [ref], "배운 기록이 없으면 지우지 않음")
        style.extract_events(ref, quiet)
        os.utime(refs.path_of(ref), ns=(time.time_ns(), time.time_ns() + 10 ** 9))  # 배운 뒤 파일이 바뀜
        self.assertEqual(refs.prune([ref], quiet)["kept"], [ref])
        self.assertTrue(refs.path_of(ref).exists())

    def test_prune_locked_file_keeps_file_and_record(self):
        """Windows: 파일이 잠겨 못 지우면 영상·기록 그대로 (지운 것으로 표시하지 않음)."""
        ref = self.ref_video(yt_name("refvid00019"), real=True)
        style.extract_events(ref, quiet)
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("잠김")):
            res = refs.prune([ref], quiet)
        self.assertEqual((res["pruned"], len(res["failed"])), ([], 1))
        self.assertTrue(refs.path_of(ref).is_file())
        self.assertNotIn("pruned", refs.find(ref))

    def test_missing_file_without_kept_sig_is_an_error(self):
        ref = self.ref_video(yt_name("refvid00013"), real=True)
        style.extract_events(ref, quiet)
        refs.path_of(ref).unlink()  # 앱 밖에서 지움 → 지문을 남기지 않았음
        with self.assertRaises(FileNotFoundError):
            style.extract_events(ref, quiet)
        self.assertFalse(refs.listing()["videos"][0]["usable"])

    def test_learn_channel_merges_previous_style_sources(self):
        a = self.ref_video(yt_name("refvid00014"), real=True)
        b = self.ref_video(yt_name("refvid00015"), real=True)
        lib = self.lib_video(yt_name("libvid00014"), real=True)
        style.learn("슛포러브 스타일", [lib], quiet)  # 예전에 보관함 영상으로 배운 같은 이름의 스타일
        out = refs.learn_channel(OTHER_ID, quiet)["learned"][0]
        self.assertEqual(out["style"], "슛포러브 스타일")
        self.assertEqual(sorted(out["names"]), sorted([lib, a, b]))
        d = json.loads((style.STYLES / "슛포러브 스타일.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(d["source"]), sorted([lib, a, b]))

    def test_delete_video_and_channel(self):
        a = self.ref_video(yt_name("refvid00016"), real=True)
        b = self.ref_video(yt_name("refvid00017"))
        c = self.ref_video(yt_name("refvid00018"), info=DOB_INFO)
        style.extract_events(a, quiet)
        self.assertEqual(refs.delete([a], log=quiet)["removed"], [a])
        self.assertFalse(refs.adir_of(b).parent.joinpath(Path(a).stem).exists())
        self.assertNotIn(a, refs.load()["files"])
        refs.delete(channel=OTHER_ID, log=quiet)
        self.assertFalse((refs.root() / "슛포러브").exists())
        self.assertEqual(list(refs.load()["files"]), [c])
        self.assertNotIn(OTHER_ID, refs.load()["channels"])
        with self.assertRaises(refs.RefsError):
            refs.delete(channel="UCnononononononononononon", log=quiet)


# ---------- 보관함 → 학습용으로 옮기기 ----------

class MoveTests(Base):
    def other(self, name, info=OTHER_INFO):
        self.lib_video(name)
        source.record_download([dict(info, id=source.video_id(name))])
        d = core.adir(name)
        d.mkdir(parents=True)
        (d / "transcript.json").write_text("[]", encoding="utf-8")
        (d / "style_events.json").write_text("{}", encoding="utf-8")
        return name

    def test_move_korean_space_jamo_names_with_analysis(self):
        names = [self.other(yt_name("movvid00001", "한글 띄어쓰기 [꿀팁] ")), self.other(yt_name("movvid00002", JAMO)),
                 self.other(yt_name("movvid00003", "도블락 영상"), DOB_INFO)]
        res = refs.move_from_library(names, quiet)
        self.assertEqual((sorted(res["moved"]), res["failed"], res["skipped"]), (sorted(names), [], []))
        self.assertEqual(core.local_videos(), [])
        for n in names:
            self.assertFalse((core.ANALYSIS / Path(n).stem.rstrip(" .")).exists())
            self.assertTrue(refs.path_of(n).is_file())
            self.assertTrue((core.adir(n) / "transcript.json").is_file(), "분석 기록이 따라옴")
            self.assertEqual(refs.find(n)["how"], "move")
        chans = {c["channel"]: c["count"] for c in refs.listing()["channels"]}
        self.assertEqual(chans, {"슛포러브": 2, "도블락": 1})
        self.assertEqual(refs.find(names[0])["channelKey"], OTHER_ID)

    def test_own_and_footage_need_confirmation(self):
        foot = self.lib_video("촬영 원본 1.mp4")
        res = refs.move_from_library([foot], quiet)
        self.assertEqual(res["skipped"], [foot])
        self.assertTrue((self.videos / foot).exists())
        res = refs.move_from_library([foot], quiet, allow_own=True)
        self.assertEqual(res["moved"], [foot])
        self.assertEqual(refs.find(foot)["folder"], "내 촬영본")

    def test_locked_file_retried_then_moved(self):
        name = self.other(yt_name("movvid00004"))
        real, calls = os.replace, {"n": 0}

        def flaky(a, b):
            if Path(a).name == name and calls["n"] < 3:
                calls["n"] += 1
                raise PermissionError("다른 프로그램이 쓰는 중")
            return real(a, b)
        with mock.patch.object(refs.os, "replace", side_effect=flaky):
            res = refs.move_from_library([name], quiet)
        self.assertEqual(res["moved"], [name])
        self.assertEqual(calls["n"], 3)

    def test_locked_file_gives_up_and_restores_analysis(self):
        name = self.other(yt_name("movvid00005"))
        real = os.replace

        def locked(a, b):
            if Path(a).name == name:
                raise PermissionError("잠김")
            return real(a, b)
        with mock.patch.object(refs.os, "replace", side_effect=locked):
            res = refs.move_from_library([name], quiet)
        self.assertEqual(res["moved"], [])
        self.assertIn("다른 프로그램", res["failed"][0]["error"])
        self.assertTrue((self.videos / name).is_file())
        self.assertTrue((core.ANALYSIS / Path(name).stem / "transcript.json").is_file(), "분석 폴더는 제자리로")
        self.assertNotIn(name, refs.load()["files"])

    def test_record_write_failure_is_recovered_by_listing(self):
        name = self.other(yt_name("movvid00006"))
        real = refs._update

        def fail_put(fn):
            if fn.__name__ == "put":
                raise PermissionError("잠김")
            return real(fn)
        with mock.patch.object(refs, "_update", side_effect=fail_put):
            refs.move_from_library([name], quiet)
        self.assertTrue(refs.root().joinpath("슛포러브", name).is_file())
        self.assertIn(name, [v["name"] for v in refs.listing()["videos"]], "채널 폴더에 있는 파일은 다시 기록됨")


# ---------- 추천 채널 목록 ----------

class RecommendedTests(unittest.TestCase):
    def test_json_shape(self):
        d = json.loads((core.APP_DIR / "ref_channels.json").read_text(encoding="utf-8"))
        self.assertEqual(d["groups"], ["풋살 특화", "축구 레슨·기술", "축구 예능·챌린지", "리뷰·브이로그·분석"])
        self.assertGreaterEqual(len(d["channels"]), 40)
        names = set()
        for c in d["channels"]:
            self.assertIn(c["group"], d["groups"])
            self.assertRegex(c["url"], r"^https://www\.youtube\.com/@[^/\s]+$")
            self.assertTrue(c["name"] and c["tip"] and c["subsText"])
            self.assertIsInstance(c["subs"], int)
            names.add(c["name"])
        for k in ("슛포러브", "도블락", "JK 아트사커", "꽁병지TV"):
            self.assertTrue(any(c["name"] == k and c.get("star") for c in d["channels"]), k)
        self.assertEqual([x["key"] for x in d["directions"]], ["A", "B", "C"])
        for x in d["directions"]:
            self.assertTrue(x["title"] and x["desc"].endswith((".", "요")))
            for ch in x["channels"]:
                self.assertIn(ch["channel"], names)
                self.assertTrue(ch["videos"])
                for v in ch["videos"]:
                    self.assertRegex(v["id"], r"^[A-Za-z0-9_-]{11}$")
                    self.assertIn(v["id"], v["url"])
        self.assertEqual(refs.recommended()["directions"][0]["key"], "A")

    def test_broken_file_gives_empty_list(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(refs, "channels_file", return_value=Path(t) / "x.json"):
            self.assertEqual(refs.recommended()["channels"], [])


# ---------- 받기 (가짜 yt-dlp) ----------

class FakeYDL:
    """yt_dlp.YoutubeDL 흉내: 진행 훅을 부르고 outtmpl 자리에 파일을 만듦 (받은 옵션은 기억)."""
    infos, seen, fail = {}, [], {}

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.seen.append(opts)

    def download(self, urls):
        for u in urls:
            vid = u.rsplit("=", 1)[-1]
            if vid in self.fail:
                raise self.fail[vid]
            info = dict(self.infos[vid], id=vid, title="인기 영상 제목", upload_date="20240101", ext="mp4",
                        webpage_url=f"https://www.youtube.com/watch?v={vid}")
            for h in self.opts["progress_hooks"]:
                h({"status": "downloading", "info_dict": info, "downloaded_bytes": 5, "total_bytes": 10})
                h({"status": "finished", "info_dict": info})
            Path(self.opts["outtmpl"].replace("%(upload_date)s", "20240101").replace("%(id)s", vid)
                 .replace("%(title).60B", "인기 영상 제목").replace("%(ext)s", "mp4")).write_bytes(b"\0" * 500)

    def close(self):
        pass


class DownloadTests(Base):
    def setUp(self):
        super().setUp()
        FakeYDL.seen, FakeYDL.fail = [], {}
        fake = types.SimpleNamespace(YoutubeDL=FakeYDL)
        self.p3 = [mock.patch.object(core, "_yt", return_value=fake), mock.patch.object(core, "ensure_deno")]
        for p in self.p3:
            p.start()

    def tearDown(self):
        for p in self.p3:
            p.stop()
        super().tearDown()

    def rows(self, ids, info=OTHER_INFO):
        return [{"id": v, "title": f"영상 {v}", "views": 100 - i, "duration": 60, "kind": "videos", **source.meta_of(info, channel_only=True)}
                for i, v in enumerate(ids)]

    def test_add_channel_downloads_into_channel_folder_and_learns(self):
        ids = [f"popvid0000{i}" for i in range(1, 8)]
        FakeYDL.infos = {v: OTHER_INFO for v in ids}
        self.lib_video(yt_name("popvid00002"))  # 편집용 보관함에 이미 있는 영상은 건너뜀
        with mock.patch.object(core, "list_videos", return_value=self.rows(ids)) as lv, \
                mock.patch.object(style, "learn", return_value={"name": "x"}) as learn:
            res = refs.add_channel("@shootforlovekorea", 3, "shorts", quiet, cookies=None, learn=True)
        lv.assert_called_once_with("shorts", None, "@shootforlovekorea", quiet)
        got = sorted(source.video_id(n) for n in res["got"])
        self.assertEqual(got, ["popvid00001", "popvid00003", "popvid00004"])
        for n in res["got"]:
            self.assertTrue((refs.root() / "슛포러브" / n).is_file())
            self.assertEqual(refs.find(n)["kind"], "shorts")
        self.assertEqual(FakeYDL.seen[0]["outtmpl"].split(os.sep)[-2], refs.INCOMING)
        self.assertNotIn("download_archive", FakeYDL.seen[0], "편집용 보관함의 archive.txt 를 쓰지 않음")
        self.assertFalse(any((refs.root() / refs.INCOMING).iterdir()))
        self.assertEqual([v["name"] for v in core.local_videos()], [yt_name("popvid00002")], "보관함에는 안 들어감")
        self.assertNotIn("popvid00001", source.load()["ids"], "보관함 출처 기록(sources.json)에 안 섞임")
        learn.assert_called_once()
        self.assertEqual(learn.call_args[0][0], "슛포러브 스타일")
        self.assertEqual(sorted(learn.call_args[0][1]), sorted(res["got"]))
        # 다시 누르면 이미 받은 것은 빼고 다음 인기 영상
        with mock.patch.object(core, "list_videos", return_value=self.rows(ids)):
            res2 = refs.add_channel("@shootforlovekorea", 2, "videos", quiet, learn=False)
        self.assertEqual(sorted(source.video_id(n) for n in res2["got"]), ["popvid00005", "popvid00006"])

    def test_direction_queues_recommended_videos(self):
        rec = {"groups": [], "channels": [], "directions": [{"key": "B", "title": "챌린지", "desc": "", "channels": [
            {"channel": "도블락", "url": "https://www.youtube.com/@gkdoblock", "videos": [
                {"id": "dirvid00001", "title": "피구부", "shorts": False, "url": "https://www.youtube.com/watch?v=dirvid00001"},
                {"id": "dirvid00002", "title": "쇼츠", "shorts": True, "url": "https://www.youtube.com/shorts/dirvid00002"}]}]}]}
        FakeYDL.infos = {"dirvid00001": DOB_INFO, "dirvid00002": DOB_INFO}
        with mock.patch.object(refs, "recommended", return_value=rec), mock.patch.object(style, "learn", return_value={}) as learn:
            res = refs.add_direction("B", quiet, learn=True)
            with self.assertRaises(refs.RefsError):
                refs.add_direction("Z", quiet)
        self.assertEqual(len(res["got"]), 2)
        self.assertEqual(res["channels"][0]["channel"], "도블락")
        self.assertEqual(learn.call_args[0][0], "도블락 스타일")
        self.assertEqual({refs.find(n)["kind"] for n in res["got"]}, {"videos", "shorts"})

    def test_blocked_download_reports_failure_and_keeps_others(self):
        class DownloadError(Exception):
            pass
        FakeYDL.infos = {"blkvid00001": OTHER_INFO, "blkvid00002": OTHER_INFO}
        FakeYDL.fail = {"blkvid00001": DownloadError("Sign in to confirm you're not a bot")}
        msgs = []
        with mock.patch.object(core, "update_engine") as up:
            res = refs.fetch([{"id": "blkvid00001"}, {"id": "blkvid00002"}], msgs.append)
        up.assert_called_once()  # 엔진을 한 번 최신으로 바꿔 다시 해 봄
        self.assertEqual(res["failed"], ["blkvid00001"])
        self.assertEqual([source.video_id(n) for n in res["got"]], ["blkvid00002"])
        self.assertTrue(any(core.BLOCKED_MSG in m for m in msgs), "쉬운 안내 문구")
        self.assertNotIn("cookiesfrombrowser", FakeYDL.seen[0], "쿠키는 사용자가 고를 때만")

    def test_cookies_only_when_picked(self):
        FakeYDL.infos = {"ckivid00001": OTHER_INFO}
        refs.fetch([{"id": "ckivid00001"}], quiet, cookies="chrome")
        self.assertEqual(FakeYDL.seen[0]["cookiesfrombrowser"], ("chrome",))

    def test_library_download_unchanged(self):
        """편집용 보관함 받기는 예전 그대로 (videos · archive.txt · sources.json)."""
        FakeYDL.infos = {"libdl000001": OTHER_INFO}
        self.assertEqual(core.download(["libdl000001"], quiet), [])
        self.assertEqual(FakeYDL.seen[0]["download_archive"], str(self.videos / "archive.txt"))
        self.assertTrue((self.videos / yt_name("libdl000001", "인기 영상 제목")).is_file())
        self.assertIn("libdl000001", source.load()["ids"])
        self.assertFalse(refs.root().exists())


# ---------- /api/refs/* ----------

class RouteTests(Base):
    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p4 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.tmp / "studio.log")]
        for p in self.p4:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.wait_job()
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.p4:
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
        for _ in range(400):
            if not self.app.JOB["name"]:
                return
            time.sleep(0.05)

    def result(self):
        self.wait_job()
        return self.call("/api/state?since=0")[1]["result"]

    def test_list_and_recommended(self):
        self.ref_video(yt_name("rtevid00001"))
        code, j = self.call("/api/refs")
        self.assertEqual((code, j["ok"], j["videos"][0]["channel"]), (200, True, "슛포러브"))
        self.assertEqual(self.call("/api/state?since=0")[1]["local"], [], "보관함 목록에는 없음")
        self.assertEqual(self.call("/api/refs/recommended")[1]["directions"][1]["key"], "B")

    def test_move_route_confirm_flow(self):
        other = self.lib_video(yt_name("rtevid00002"))
        source.record_download([dict(OTHER_INFO, id="rtevid00002")])
        foot = self.lib_video("내 촬영본 영상.mp4")
        code, j = self.call("/api/refs/move", {"names": [other, foot]})
        self.assertEqual((code, j["ok"], j["confirm"]), (200, False, [foot]))
        self.assertIsNone(self.app.JOB["name"], "확인 전에는 작업을 시작하지 않음")
        self.assertEqual(self.call("/api/refs/move", {"names": [other]})[1]["ok"], True)
        self.assertEqual(self.result()["moved"], [other])
        self.assertEqual(self.call("/api/refs/move", {"names": ["..\\x.mp4"]})[0], 400)
        self.assertEqual(self.call("/api/refs/move", {"names": ["없음.mp4"]})[0], 404)
        self.assertEqual(self.call("/api/refs/move", {"names": "문자"})[0], 400)

    def test_delete_and_prune_routes(self):
        a = self.ref_video(yt_name("rtevid00003"))
        self.assertEqual(self.call("/api/refs/delete", {"names": ["../밖.mp4"]})[0], 400)
        self.assertEqual(self.call("/api/refs/delete", {})[0], 400)
        self.assertEqual(self.call("/api/refs/prune", {"names": [a]})[0], 200)
        self.assertEqual(self.result()["kept"], [a])
        self.assertEqual(self.call("/api/refs/delete", {"names": [a]})[0], 200)
        self.assertEqual(self.result()["removed"], [a])

    def test_add_route_validation_and_blocked_message(self):
        self.assertEqual(self.call("/api/refs/add", {"url": "  "})[0], 400)
        with mock.patch.object(core, "list_videos", side_effect=RuntimeError(core.BLOCKED_MSG)):
            self.assertEqual(self.call("/api/refs/add", {"url": "@shootforlovekorea", "count": 5})[0], 200)
            r = self.result()
        self.assertEqual((r["ok"], r["blocked"], r["error"]), (False, True, core.BLOCKED_MSG))
        with mock.patch.object(refs, "add_channel", return_value={"got": []}) as add:
            self.call("/api/refs/add", {"url": "@x", "count": 3, "kind": "shorts", "learn": False, "prune": True, "cookies": "chrome"})
            self.assertEqual(self.result()["ok"], True)
        self.assertEqual(add.call_args[0][:3], ("@x", 3, "shorts"))
        self.assertEqual(add.call_args[0][4:7], ("chrome", False, True))
        with mock.patch.object(core, "list_videos", return_value=[]):
            self.call("/api/refs/add", {"url": "@x", "count": "다섯"})
            self.assertIn("숫자", self.result()["error"])

    def test_banner_dismiss_persists(self):
        self.assertFalse(self.call("/api/refs")[1]["bannerDismissed"])
        self.assertEqual(self.call("/api/refs/banner", {"dismiss": True})[1]["ok"], True)
        self.assertTrue(self.call("/api/refs")[1]["bannerDismissed"])


if __name__ == "__main__":
    unittest.main()
