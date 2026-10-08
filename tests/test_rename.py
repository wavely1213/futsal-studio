"""보관함 영상 이름 바꾸기 (rename.py · D-073) + 탐색기에서 바꾼 영상에 옛 이름 작업 이어 붙이기.

재현(백로그): 편집점을 찾은 IMG_4830.mp4(편집본 3개)를 탐색기에서 '패스 앤 무브 레슨.mp4'로 바꾸면 3초 뒤 /api/state 가
analyzed:false · 옛 폴더는 화면 어디에도 안 보였다 (분석·편집본·썸네일을 파일 이름으로 찾음).
이제 앱 안 [이름 바꾸기]가 영상·분석 폴더·편집본(+백업)·썸네일 디자인·출처를 함께 옮기고 안의 영상 이름도 고친다.
탐색기에서 이미 바꿨으면 크기·수정 시각(file_sig)이 같은 옛 작업을 보관함 줄에 알리고 [예전 작업 이어 붙이기] (길이도 확인).
실행: python3 -m unittest tests.test_rename
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import intake  # noqa: E402
import rename  # noqa: E402
import source  # noqa: E402
import thumb  # noqa: E402
from test_windows_compat import Server  # noqa: E402

OLD = "IMG_4830.mp4"
NEW_TITLE = "패스 앤 무브 레슨 [꿀팁]"
NEW = NEW_TITLE + ".mp4"
JS_SAFE = "-_.!~*'()"  # encodeURIComponent 가 그대로 두는 글자


def frame(name, t):
    return f"/frame?name={quote(name, safe=JS_SAFE)}&t={t}"


class Base(Server):
    def make_work(self, name=OLD, seqs=3, dur=12.5):
        """편집점을 찾고 편집본·백업·썸네일 디자인·출처까지 있는 영상."""
        v = self.video(name, b"\1" * 3000)
        d = core.adir(name)
        (d / "frames").mkdir(parents=True)
        (d / "transcript_timeline.md").write_text(f"# 타임라인: {name}\n\n[00:00] 패스\n", encoding="utf-8")
        (d / "transcript.json").write_text(json.dumps([{"start": 0, "end": 1, "text": "패스"}], ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": []}), encoding="utf-8")
        (d / "frames" / "candidates3.json").write_text(json.dumps({"sig": [1], "items": [{"t": 1.0, "url": f"/frame?name={name}&t=1.0"}]},
                                                                  ensure_ascii=False), encoding="utf-8")
        intake.remember(d, intake.sig(v))
        proj = {"v": 2, "rev": 7, "source": name, "info": {"duration": dur, "width": 320, "height": 240},
                "media": [{"id": "main", "kind": "video", "src": "videos", "file": name, "dur": dur}],
                "sequences": [{"id": f"s{i}", "name": f"편집본 {i}", "items": [], "markers": []} for i in range(seqs)], "captions": []}
        editor._ppath(name).write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        (editor.PROJECTS / "backup").mkdir(exist_ok=True)
        (editor.PROJECTS / "backup" / f"{editor._ppath(name).stem}__20261007_101010.json").write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        doc = {"designs": [{"layers": [{"type": "image", "src": frame(name, 3.2)}, {"type": "image", "src": "/asset/cut_1.png", "orig": frame(name, 3.2)}]}]}
        thumb._doc_path(name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        source.mark_footage(name, "local")
        return v

    def local(self, name):
        st = self.call("/api/state")[1]
        return next((r for r in st["local"] if r["name"] == name), None)


class RenameTests(Base):
    def test_rename_moves_video_and_all_work(self):
        self.make_work()
        other = {"v": 2, "source": "다른 영상.mp4", "media": [{"id": "m2", "kind": "video", "src": "videos", "file": OLD}], "sequences": [], "rev": 2}
        (editor.PROJECTS / "다른 영상.json").write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(self.local(OLD)["analyzed"])
        code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual((code, j["ok"], j["name"]), (200, True, NEW), j)
        self.assertEqual((j["analysis"], j["project"], j["backups"], j["thumbs"], j["refs"]), (True, True, 1, True, 1))
        self.assertFalse((core.VIDEOS / OLD).exists())
        row = self.local(NEW)
        self.assertTrue(row["analyzed"], "받아쓰기가 그대로 이어짐 (예전: '아직 안 함')")
        self.assertIsNone(self.local(OLD))
        proj = json.loads(editor._ppath(NEW).read_text(encoding="utf-8"))
        self.assertEqual((proj["source"], proj["media"][0]["file"], len(proj["sequences"]), proj["rev"]), (NEW, NEW, 3, 8))
        self.assertEqual(len(editor.load_project(NEW)["sequences"]), 3, "편집실이 그대로 엶")
        self.assertEqual(len(editor.backups(NEW)), 1, "자동 백업도 새 이름으로")
        self.assertEqual(json.loads((editor.PROJECTS / "backup" / editor.backups(NEW)[0]["file"]).read_text(encoding="utf-8"))["source"], NEW)
        doc = thumb.load_docs(NEW)
        for key in ("src", "orig"):
            url = next(l[key] for l in doc["designs"][0]["layers"] if key in l and l[key].startswith("/frame"))
            self.assertEqual(parse_qs(urlparse(url).query)["name"], [NEW], url)
        cand = json.loads((core.adir(NEW) / "frames" / "candidates3.json").read_text(encoding="utf-8"))
        self.assertEqual(cand["items"][0]["url"], f"/frame?name={NEW}&t=1.0")
        self.assertTrue((core.adir(NEW) / "transcript_timeline.md").read_text(encoding="utf-8").startswith(f"# 타임라인: {NEW}\n"))
        self.assertEqual(core._folder_owner(core.adir(NEW)), NEW)
        self.assertEqual(source.describe(NEW)["kind"], "footage")
        self.assertNotIn(OLD, source.load()["files"])
        self.assertEqual(json.loads((editor.PROJECTS / "다른 영상.json").read_text(encoding="utf-8"))["media"][0]["file"], NEW)
        for p in (core.ANALYSIS / "IMG_4830", editor.PROJECTS / "IMG_4830.json", thumb.THUMBS / "IMG_4830.json"):
            self.assertFalse(p.exists(), p)
        self.assertTrue(any("이름을 바꿨어요 · IMG_4830.mp4 → " in m for m in app.LOG[-5:]), app.LOG[-5:])

    def test_youtube_name_keeps_channel_source(self):
        """유튜브에서 받은 이름 꼴(영상 id)이 사라져도 출처(우리 채널)는 그대로."""
        yt = "20240101_abcdefghijk_패스 잘하는 법.mp4"
        self.video(yt)
        source._update(lambda d: d["ids"].setdefault("abcdefghijk", {}).update(channel="슛포러브", channelId="UCabcdefghijklmnopqrstuv", how="download"))
        before = source.describe(yt)
        self.assertEqual(before["kind"], "other")
        self.assertEqual(self.call("/api/rename", {"name": yt, "to": "패스 레슨"})[0], 200)
        after = source.describe("패스 레슨.mp4")
        self.assertEqual((after["kind"], after.get("channel")), ("other", "슛포러브"), "직접 넣은 촬영본으로 바뀌지 않음")

    def test_refuses_without_changing_anything(self):
        self.make_work()
        self.video("다른 영상.mp4")
        self.video("레슨.MOV")
        (core.ANALYSIS / "예전 영상").mkdir()
        (core.ANALYSIS / "예전 영상" / "transcript.json").write_text("[]", encoding="utf-8")
        cases = [("다른 영상", 400, "같은 이름 영상이 이미 있어요"), ("다른 영상.MP4", 400, "같은 이름 영상"), ("레슨", 400, "확장자만 다른"),
                 ("a/b", 400, "쓸 수 없어요"), ('a"b', 400, "쓸 수 없어요"), ("  .. ", 400, "새 이름을 적어 주세요"), ("CON", 400, "Windows 가 쓰는 이름"),
                 ("가" * 121, 400, "너무 길어요"), ("예전 영상", 400, "예전 작업")]
        for to, code, msg in cases:
            c, j = self.call("/api/rename", {"name": OLD, "to": to})
            self.assertEqual(c, code, (to, j))
            self.assertIn(msg, j["error"], to)
        self.assertEqual(self.call("/api/rename", {"name": "없는.mp4", "to": "x"})[0], 404)
        self.assertEqual(self.call("/api/rename", {"name": "..\\밖.mp4", "to": "x"})[0], 400)
        with mock.patch.dict(app.JOB, name="내보내기"):
            self.assertEqual(self.call("/api/rename", {"name": OLD, "to": "새 이름"})[0], 409)
        self.assertTrue((core.VIDEOS / OLD).exists() and self.local(OLD)["analyzed"], "하나도 안 바뀜")
        code, j = self.call("/api/rename", {"name": OLD, "to": "IMG_4830.mp4"})
        self.assertEqual((code, j.get("same")), (200, True), "같은 이름은 그대로")

    def test_locked_folder_rolls_back(self):
        """분석 폴더를 못 옮기면(탐색기·편집실이 열어 둠) 영상 이름도 되돌림 (반쪽만 바뀌지 않게)."""
        self.make_work()
        real = os.rename

        def fake(a, b):
            if Path(a).name == "IMG_4830" and Path(a).parent == core.ANALYSIS:
                raise PermissionError(13, "잠김")
            return real(a, b)
        with mock.patch.object(rename.os, "rename", fake):
            code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual(code, 400)
        self.assertIn("이름을 되돌렸어요", j["error"])
        self.assertTrue((core.VIDEOS / OLD).exists())
        self.assertFalse((core.VIDEOS / NEW).exists())
        self.assertTrue(self.local(OLD)["analyzed"])

    def test_locked_video_says_close_player(self):
        self.make_work()
        with mock.patch.object(rename.os, "rename", side_effect=PermissionError(32, "다른 프로세스가 파일을 사용 중")):
            code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual(code, 400)
        self.assertIn("다른 프로그램", j["error"])

    def test_clean_new(self):
        self.assertEqual(rename.clean_new("a.mp4", " 새  이름 .mp4"), "새 이름.mp4")
        self.assertEqual(rename.clean_new("a.MOV", "레슨."), "레슨.MOV")
        self.assertEqual(rename.clean_new("a.mp4", "1. 패스"), "1. 패스.mp4")


class AttachTests(Base):
    def explorer_rename(self):
        self.make_work()
        os.rename(core.VIDEOS / OLD, core.VIDEOS / NEW)  # 탐색기에서 이름 바꾸기 (크기·수정 시각 그대로)
        core._STEMS["key"] = None

    def test_backlog_repro_and_attach(self):
        self.explorer_rename()
        row = self.local(NEW)
        self.assertFalse(row["analyzed"], "재현: 옛 이름 작업이 끊김")
        self.assertEqual(row["renamedFrom"], {"old": OLD, "seqs": 3}, "옛 이름 작업을 알려 줌")
        with mock.patch.object(editor, "probe", return_value={"duration": 12.6}):
            code, j = self.call("/api/rename/attach", {"name": NEW, "old": OLD})
        self.assertEqual((code, j["ok"], j["project"]), (200, True, True), j)
        row = self.local(NEW)
        self.assertTrue(row["analyzed"])
        self.assertNotIn("renamedFrom", row)
        self.assertEqual(len(editor.load_project(NEW)["sequences"]), 3)
        self.assertEqual(json.loads(editor._ppath(NEW).read_text(encoding="utf-8"))["media"][0]["file"], NEW)

    def test_cache_folder_made_after_rename_is_replaced(self):
        """바꾼 이름으로 썸네일 장면만 열어 봤으면(캐시 폴더) 그 캐시는 지우고 붙임 · 받아쓴 폴더면 덮어쓰지 않음."""
        self.explorer_rename()
        (core.ANALYSIS / NEW_TITLE / "frames").mkdir(parents=True)
        with mock.patch.object(editor, "probe", return_value={"duration": 12.5}):
            self.assertEqual(self.call("/api/rename/attach", {"name": NEW, "old": OLD})[0], 200)
        self.assertTrue(self.local(NEW)["analyzed"])

    def test_different_length_is_refused(self):
        self.explorer_rename()
        with mock.patch.object(editor, "probe", return_value={"duration": 60.0}):
            code, j = self.call("/api/rename/attach", {"name": NEW, "old": OLD})
        self.assertEqual(code, 400)
        self.assertIn("길이", j["error"])
        self.assertFalse(self.local(NEW)["analyzed"])

    def test_different_file_not_offered(self):
        """크기가 다른 파일(다른 영상)에는 알리지 않고 붙이지도 않음 · 옛 영상이 아직 있으면(복사본) 알리지 않음."""
        self.make_work()
        self.video("새 영상.mp4", b"\2" * 999)
        os.remove(core.VIDEOS / OLD)
        core._STEMS["key"] = None
        self.assertNotIn("renamedFrom", self.local("새 영상.mp4"))
        self.assertEqual(self.call("/api/rename/attach", {"name": "새 영상.mp4", "old": OLD})[0], 400)
        import shutil
        shutil.rmtree(core.ANALYSIS / "IMG_4830")
        rename._ORPH["key"] = None
        self.make_work("복사본 원본.mp4")
        data = (core.VIDEOS / "복사본 원본.mp4").read_bytes()
        (core.VIDEOS / "복사본.mp4").write_bytes(data)
        st = (core.VIDEOS / "복사본 원본.mp4").stat()
        os.utime(core.VIDEOS / "복사본.mp4", ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertNotIn("renamedFrom", self.local("복사본.mp4"), "옛 영상이 그대로 있으면 이어 붙일 대상이 아님")


if __name__ == "__main__":
    unittest.main()
