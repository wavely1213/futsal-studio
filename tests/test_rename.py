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
import thumbcopy  # noqa: E402
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
        # 옛 이름으로 열어 둔 편집실이 저장해도 옛 이름 편집본을 새로 만들지 않음 (스튜디오에서 다시 열라고 안내)
        code, j = self.call("/api/edit/save", {"name": OLD, "project": proj, "rev": 7})
        self.assertEqual((code, j.get("gone")), (404, True))
        self.assertIn("이름이 바뀌었거나", j["error"])
        self.assertFalse((editor.PROJECTS / "IMG_4830.json").exists())

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


def held(lock):
    """다른 스레드에서 lock 을 바로 못 잡으면 참 (지금 스레드가 잡고 있음)."""
    import threading
    got = []

    def t():
        ok = lock.acquire(timeout=0)
        got.append(ok)
        if ok:
            lock.release()
    th = threading.Thread(target=t)
    th.start()
    th.join()
    return not got[0]


class ReviewFixTests(Base):
    """E10 검토 고침 (D-073): 반쪽으로 남는 경우 · 다른 영상 백업 · 되돌리기 실패 · 지문 폴더 · 대소문자 · 잠금 · 작업 자리 · 완성본 파일 안내."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(rename, "TRIES", 2, create=True)  # 잠금 흉내에서 오래 기다리지 않게
        p.start()
        self.addCleanup(p.stop)

    def project_locked(self):
        """편집본을 새 이름으로 쓰지 못함 (Windows 잠금 흉내)."""
        real = rename._rewrite

        def rw(src, dst, *a, **k):
            if Path(dst) == editor.PROJECTS / f"{NEW_TITLE}.json":
                raise PermissionError(32, "잠김")
            return real(src, dst, *a, **k)
        return mock.patch.object(rename, "_rewrite", rw)

    def test_ansi_timeline_line_does_not_split_work(self):
        """검토 재현: 메모장이 ANSI(cp949)로 저장한 줄이 타임라인에 있으면 예전에는 UnicodeDecodeError 가 빠져나와 영상만 되돌리고
        분석 폴더·편집본은 새 이름에 남음('이름을 되돌렸어요'). 이제 첫 줄만 바이트로 바꾸고 나머지는 그대로."""
        self.make_work()
        tl = core.adir(OLD) / "transcript_timeline.md"
        tail = "\n[00:05] 메모 · ".encode("utf-8") + "드리블".encode("cp949") + b"\r\n"
        tl.write_bytes(tl.read_bytes() + tail)
        code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual((code, j.get("ok"), j.get("project")), (200, True, True), j)
        raw = (core.adir(NEW) / "transcript_timeline.md").read_bytes()
        self.assertTrue(raw.startswith(f"# 타임라인: {NEW}\n".encode("utf-8")))
        self.assertTrue(raw.endswith(tail), "ANSI 줄은 손대지 않음")
        self.assertTrue(self.local(NEW)["analyzed"])

    def test_any_side_step_error_keeps_rename(self):
        """곁가지(출처 기록)가 OSError·ValueError 가 아닌 오류를 내도 이름 바꾸기는 그대로 · 기록만 (예전: 영상만 되돌림)."""
        self.make_work()
        with mock.patch.object(rename.source, "rename_file", side_effect=RuntimeError("뜻밖의 오류")):
            code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual((code, j.get("ok")), (200, True), j)
        self.assertTrue(self.local(NEW)["analyzed"] and (core.VIDEOS / NEW).exists())
        self.assertTrue(any("출처 기록은 옮기지 못했어요" in m and "RuntimeError" in m for m in app.LOG[-10:]), app.LOG[-10:])

    def test_backups_of_longer_named_video_stay(self):
        """검토 재현: a.mp4 를 바꾸면 a__b.mp4 의 백업(a__b__<시각>.json)까지 zz__b__… 로 옮겨 a__b 가 백업을 잃었다."""
        self.make_work("a.mp4")
        self.make_work("a__b.mp4")
        self.assertEqual((len(editor.backups("a.mp4")), len(editor.backups("a__b.mp4"))), (1, 1))
        self.assertEqual(self.call("/api/rename", {"name": "a.mp4", "to": "zz"})[0], 200)
        self.assertEqual((len(editor.backups("zz.mp4")), len(editor.backups("a__b.mp4"))), (1, 1))
        self.assertEqual(sorted(f.name for f in (editor.PROJECTS / "backup").iterdir()),
                         ["a__b__20261007_101010.json", "zz__20261007_101010.json"])

    def test_failed_rollback_rolls_forward(self):
        """검토 재현: 편집본을 못 옮긴 뒤 분석 폴더 되돌리기도 실패하면 예전에는 그 오류가 원래 오류를 덮고 영상만 되돌려
        분석 폴더가 새 이름에 숨음(첫 줄이 옛 영상 → 이어 붙이기도 안 뜸). 이제 되돌리지 말고 새 이름으로 마저."""
        self.make_work()
        real = os.rename

        def fake(a, b):
            if Path(a) == core.ANALYSIS / NEW_TITLE and Path(b) == core.ANALYSIS / "IMG_4830":
                raise PermissionError(32, "다른 프로세스가 사용 중")
            return real(a, b)
        with self.project_locked(), mock.patch.object(rename.os, "rename", fake):
            code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual((code, j.get("ok"), j.get("analysis"), j.get("project")), (200, True, True, True), j)
        self.assertTrue((core.VIDEOS / NEW).exists() and not (core.VIDEOS / OLD).exists())
        self.assertTrue(self.local(NEW)["analyzed"])
        self.assertEqual(core._folder_owner(core.adir(NEW)), NEW, "첫 줄도 새 이름")
        self.assertEqual(len(editor.load_project(NEW)["sequences"]), 3, "편집본 파일도 새 이름으로 (안은 그대로)")

    def test_video_rollback_failure_is_reported_and_attachable(self):
        """작업은 되돌렸는데 영상 이름을 못 되돌리면 '되돌렸어요'라고 하지 않고 [예전 작업 이어 붙이기]를 안내 · 실제로 붙음."""
        self.make_work()
        real = os.rename

        def fake(a, b):
            if Path(a) == core.VIDEOS / NEW and Path(b) == core.VIDEOS / OLD:
                raise PermissionError(32, "플레이어가 열어 둠")
            return real(a, b)
        with self.project_locked(), mock.patch.object(rename.os, "rename", fake):
            code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual(code, 400)
        self.assertIn("예전 작업 이어 붙이기", j["error"])
        self.assertNotIn("되돌렸어요", j["error"])
        row = self.local(NEW)
        self.assertEqual(row["renamedFrom"]["old"], OLD)
        with mock.patch.object(editor, "probe", return_value={"duration": 12.5}):
            self.assertEqual(self.call("/api/rename/attach", {"name": NEW, "old": OLD})[0], 200)
        self.assertTrue(self.local(NEW)["analyzed"])

    def test_stale_alt_folder(self):
        """검토 재현: 지운 영상이 남긴 '<새 이름>_<지문>' 폴더가 있으면 core.library_dir 가 새 이름을 그 폴더로 찾아 옮긴 작업이 안 보였다.
        받아쓴 폴더면 거절(덮어쓰지 않음) · 캐시만 있으면 지우고 바꿈."""
        self.make_work()
        alt = core.ANALYSIS / core._alt_key(NEW)
        alt.mkdir()
        (alt / "transcript.json").write_text("[]", encoding="utf-8")
        code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual(code, 400)
        self.assertIn("예전 작업", j["error"])
        self.assertTrue((core.VIDEOS / OLD).exists())
        (alt / "transcript.json").unlink()
        (alt / "frames").mkdir()
        self.assertEqual(self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})[0], 200)
        self.assertFalse(alt.exists())
        self.assertEqual(core.adir(NEW), core.ANALYSIS / NEW_TITLE)
        self.assertTrue(self.local(NEW)["analyzed"])

    def test_case_only_rename_on_case_insensitive_disk(self):
        """대소문자만 바꾸기(img → IMG): 대소문자를 안 가리는 디스크(macOS)에서 새로 쓴 파일과 옛 이름이 같은 파일이라
        예전에는 쓰고 나서 옛 이름을 지우며 편집본·백업·썸네일을 지웠다 → samefile 이면 지우지 않음."""
        self.make_work("img.mp4")
        same = lambda a, b: str(a).casefold() == str(b).casefold()  # noqa: E731 — 대소문자를 안 가리는 디스크 흉내
        removed = []
        real_unlink = Path.unlink

        def unlink(self_, *a, **k):
            removed.append(self_.name)
            return real_unlink(self_, *a, **k)
        with mock.patch.object(rename.os.path, "samefile", side_effect=same), mock.patch.object(Path, "unlink", unlink):
            code, j = self.call("/api/rename", {"name": "img.mp4", "to": "IMG"})
        self.assertEqual((code, j.get("name")), (200, "IMG.mp4"), j)
        self.assertFalse([n for n in removed if n.casefold() in ("img.json", "img__20261007_101010.json")], removed)

    def test_thumb_docs_and_other_projects_under_save_locks(self):
        """썸네일 디자인은 썸네일 저장 잠금 안에서 · 다른 편집본은 읽기부터 쓰기까지 편집본 저장 잠금 안에서 (그 사이 저장을 덮지 않게)."""
        self.make_work()
        other = {"v": 2, "source": "다른 영상.mp4", "media": [{"id": "m2", "kind": "video", "src": "videos", "file": OLD}], "sequences": [], "rev": 2}
        (editor.PROJECTS / "다른 영상.json").write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
        seen = {}
        real_rw, real_read = rename._rewrite, rename._read

        def rw(src, dst, *a, **k):
            if Path(dst).parent == thumb.THUMBS:
                seen["thumb"] = held(thumb._SAVE_LOCK)
            return real_rw(src, dst, *a, **k)

        def rd(p_):
            if Path(p_).name == "다른 영상.json":
                seen["other"] = held(editor._SAVE_LOCK)
            return real_read(p_)
        with mock.patch.object(rename, "_rewrite", rw), mock.patch.object(rename, "_read", rd):
            self.assertEqual(self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})[0], 200)
        self.assertEqual(seen, {"thumb": True, "other": True})

    def test_old_name_thumb_save_is_gone(self):
        """옛 이름으로 열린 썸네일 창이 저장해도 옛 이름 디자인을 새로 만들지 않음 (404 gone · 편집실과 같게)."""
        self.make_work()
        self.assertEqual(self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})[0], 200)
        code, j = self.call("/api/thumb/save", {"name": OLD, "docs": {"designs": [{"layers": []}]}})
        self.assertEqual((code, j.get("gone")), (404, True))
        self.assertIn("스튜디오에서 다시 열어", j["error"])
        self.assertFalse((thumb.THUMBS / "IMG_4830.json").exists())
        self.assertEqual(self.call("/api/thumb/save", {"name": NEW, "docs": {"designs": [{"layers": []}]}})[0], 200)

    def test_old_name_thumb_export_and_ab_are_gone(self):
        """검토 재현(합침 뒤): 옛 이름 썸네일 창의 저장은 404 gone 인데 그림 내보내기·A/B 묶음은 200 으로 'IMG_4830_썸네일_A.jpg' 등을
        완성본 폴더에 썼다 (새 이름 '썸네일 ✓'·7단계 확인에 안 잡힘) → 같은 404 gone · 새 이름은 그대로 저장."""
        import base64
        self.make_work()
        self.assertEqual(self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})[0], 200)
        jpg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0 jpg").decode()
        for path, body in (("/api/thumb/ab", {"items": [jpg, jpg], "mobile": jpg}), ("/api/thumb/export", {"data": jpg, "fmt": "jpg"})):
            code, j = self.call(path, dict(body, name=OLD))
            self.assertEqual((code, j.get("gone")), (404, True), path)
            self.assertIn("스튜디오에서 다시 열어", j["error"])
        self.assertEqual([f.name for f in core.OUT.iterdir() if f.name.startswith("IMG_4830")], [], "옛 이름 그림을 만들지 않음")
        code, j = self.call("/api/thumb/ab", {"name": NEW, "items": [jpg, jpg]})
        self.assertEqual((code, j["files"]), (200, [f"{NEW_TITLE}_썸네일_A.jpg", f"{NEW_TITLE}_썸네일_B.jpg"]), j)
        code, j = self.call("/api/thumb/export", {"name": NEW, "data": jpg, "fmt": "jpg"})
        self.assertEqual((code, j["file"]), (200, f"{NEW_TITLE}_썸네일_1.jpg"), j)

    def test_rename_holds_the_job_slot(self):
        """검토 재현: 작업 중인지 확인만 하고 잠금을 놓아 그 틈에 휴대폰이 옛 이름으로 편집점 찾기를 시작할 수 있었다 →
        바꾸는 동안 작업 자리를 잡아 둠 (다른 작업은 409) · 끝나면 놓음 (실패해도)."""
        self.make_work()
        during = {}
        real = rename.rename

        def spy(*a, **k):
            during["job"] = app.JOB["name"]
            during["start"] = app.start_job("편집점 찾기", lambda: None)
            return real(*a, **k)
        with mock.patch.object(rename, "rename", spy):
            self.assertEqual(self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})[0], 200)
        self.assertEqual(during, {"job": "이름 바꾸기", "start": False})
        self.assertIsNone(app.JOB["name"])
        self.assertEqual(self.call("/api/rename", {"name": "없는.mp4", "to": "x"})[0], 404)
        self.assertIsNone(app.JOB["name"], "실패해도 자리를 놓음")

    def test_out_files_left_with_old_name_are_counted(self):
        """완성본 폴더의 내보낸 영상·썸네일 그림은 옛 이름 그대로(I-070) → 몇 개인지 알려 화면이 안내 (이름이 더 긴 다른 영상 것은 빼고)."""
        self.make_work()
        self.video("IMG_4830_2.mp4")
        for n in ("IMG_4830_롱폼 가편집.mp4", "IMG_4830_썸네일_1.jpg", "IMG_4830_2_롱폼 가편집.mp4"):
            (core.OUT / n).write_bytes(b"x")
        code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual((code, j.get("outKept")), (200, 2), j)
        self.assertTrue((core.OUT / "IMG_4830_롱폼 가편집.mp4").exists(), "완성본 파일은 그대로")



class ThumbAiArtefactTests(Base):
    """합침(D-078): AI 추천 썸네일이 남기는 것도 함께 — 장면 후보 캐시(thumb.CANDIDATES · 지문에 이름이 없어 그대로 맞음 · 장면 주소만 새 이름) ·
    클로드 장면 점수(분석 폴더 안) · 자동 누끼(thumbnails/assets · 키에 분석 폴더 이름 → 새 키로 옮기고 디자인 안 주소도).
    클로드 문구 캐시(thumbcopy.CACHE)는 폴더째 옮기지만 지문 규칙 그대로 — 제목이 바뀌면 안 맞음(클로드에게 제목을 보여 주고 받은 문구) ·
    날짜·영상 ID 앞머리만 바뀌면 그대로. 브랜드 키트는 영상과 상관없는 하나라 그대로.
    재현(합치기 전 rename.py): 이름을 바꾸면 thumb.cached_analysis 가 None → 'AI 추천'이 누끼를 다시 땀(장면마다 누끼 모델)."""
    T, BOX = 3.2, [0.3, 0.2, 0.25, 0.6]
    COPY = [{"l1": "패스 비법", "l2": "딱 하나만", "emph": "", "sub": "", "q": "패스"}]

    def make_ai(self, name=OLD):
        self.make_work(name)
        d = core.adir(name)
        item = {"t": self.T, "url": f"/frame?name={name}&t={self.T}", "score": 1.0, "persons": [self.BOX + [0.9]], "main": 0, "ball": None,
                "blur": 0.1, "flags": [], "hash": "0f0f0f0f0f0f0f0f", "text": 0.0}
        (d / "frames" / thumb.CANDIDATES).write_text(json.dumps({"sig": thumb._cand_sig(name), "items": [item]}, ensure_ascii=False), encoding="utf-8")
        (d / thumb.AI_FRAMES).write_text(json.dumps({"sig": [self.T], "items": {str(self.T): {"score": 8, "why": "슈팅 순간"}}}, ensure_ascii=False),
                                         encoding="utf-8")
        (d / thumbcopy.CACHE).write_text(json.dumps({"sig": thumbcopy._sig(name), "items": self.COPY}, ensure_ascii=False), encoding="utf-8")
        cut = thumb.cut_file(d.name, self.T, self.BOX)
        cut.write_bytes(b"\x89PNG cut")
        cut.with_suffix(".json").write_text(json.dumps({"q": 0.8, "ok": True}), encoding="utf-8")
        url = thumb.asset_url(cut)
        doc = json.loads(thumb._doc_path(name).read_text(encoding="utf-8"))
        doc["designs"].append({"layers": [{"type": "image", "src": url, "orig": frame(name, self.T)}, {"type": "image", "src": url + "?v=2"}]})
        for suf in (".json", ".json.bak"):
            thumb._doc_path(name).with_suffix(suf).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        before = thumb.cached_analysis(name)
        self.assertIsNotNone(before, "이름을 바꾸기 전엔 분석이 다 돼 있음 (AI 추천이 바로 뜸)")
        self.assertEqual(before["cuts"][str(self.T)]["cut"], url)
        return cut, url

    def check_moved(self, cut, url):
        new_cut = thumb.cut_file(core.adir(NEW).name, self.T, self.BOX)
        self.assertNotEqual(new_cut, cut)
        self.assertFalse(cut.exists() or cut.with_suffix(".json").exists(), "옛 키 파일은 옮김 (두 벌로 남지 않음)")
        self.assertEqual(new_cut.read_bytes(), b"\x89PNG cut")
        self.assertTrue(new_cut.with_suffix(".json").is_file(), "누끼 품질도 함께")
        a = thumb.cached_analysis(NEW)
        self.assertIsNotNone(a, "새 이름으로도 분석이 그대로 (누끼를 다시 따지 않음)")
        self.assertEqual(a["cuts"][str(self.T)]["cut"], thumb.asset_url(new_cut))
        self.assertEqual(a["frames"][0]["url"], f"/frame?name={NEW}&t={self.T}")
        self.assertEqual(a["frames"][0]["ai"], 8, "클로드 장면 점수도 그대로")
        self.assertFalse(a["copy"]["ai"], "'IMG_4830' 제목으로 받은 클로드 문구를 새 제목 문구로 쓰지 않음 (규칙 문구 · 새로 받기는 버튼·다음 분석)")
        self.assertIsNone(thumbcopy.load_ai(NEW))
        self.assertFalse(thumbcopy.failed_recently(NEW))
        for suf in (".json", ".json.bak"):
            doc = json.loads(thumb._doc_path(NEW).with_suffix(suf).read_text(encoding="utf-8"))
            srcs = [l["src"] for l in doc["designs"][-1]["layers"]]
            self.assertEqual(srcs, [thumb.asset_url(new_cut), thumb.asset_url(new_cut) + "?v=2"], suf)
            self.assertNotIn(url, json.dumps(doc), suf)

    def test_rename_moves_ai_thumbnail_work(self):
        cut, url = self.make_ai()
        brand = thumb.THUMBS / "brand.json"
        brand.write_text(json.dumps({"series": "풋살 레슨"}, ensure_ascii=False), encoding="utf-8")
        code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual((code, j["ok"], j["thumbs"], j["cuts"]), (200, True, True, 1), j)
        self.check_moved(cut, url)
        self.assertEqual(json.loads(brand.read_text(encoding="utf-8")), {"series": "풋살 레슨"}, "브랜드 키트는 하나라 그대로")

    def test_attach_moves_ai_thumbnail_work(self):
        cut, url = self.make_ai()
        os.rename(core.VIDEOS / OLD, core.VIDEOS / NEW)  # 탐색기에서 이름 바꾸기
        core._STEMS["key"] = None
        with mock.patch.object(editor, "probe", return_value={"duration": 12.5}):
            code, j = self.call("/api/rename/attach", {"name": NEW, "old": OLD})
        self.assertEqual((code, j["cuts"]), (200, 1), j)
        self.check_moved(cut, url)

    def ai_ran_under(self, name):
        """그 이름으로 '✨ AI 추천'만 눌렀을 때 남는 것 (받아쓰기 없이: 장면 후보 · 클로드 장면 점수 · 클로드 문구) → 그 폴더."""
        d = core.ANALYSIS / core._stem_key(name)
        (d / "frames").mkdir(parents=True)
        (d / "frames" / thumb.CANDIDATES).write_text(json.dumps({"sig": [1], "items": []}), encoding="utf-8")
        (d / "frames" / "h_000003.200.jpg").write_bytes(b"jpg")
        (d / thumb.AI_FRAMES).write_text(json.dumps({"sig": [1.0], "items": {"1.0": {"score": 2, "why": "새 이름"}}}), encoding="utf-8")
        (d / thumbcopy.CACHE).write_text(json.dumps({"sig": [0, "새 이름 문구", thumbcopy.PROMPT_VER], "items": self.COPY}, ensure_ascii=False),
                                         encoding="utf-8")
        return d

    def test_attach_after_ai_ran_under_new_name(self):
        """검토 재현(합침 뒤): 탐색기로 바꾼 뒤 5 썸네일에서 새 이름으로 'AI 추천'을 누르면 새 이름 폴더에 thumb_copy.json·thumb_frames_ai.json 이
        생겨 '캐시만 있는 폴더'가 아니게 됨 → [예전 작업 이어 붙이기]가 '새 이름으로 된 예전 작업이 이미 있어요'(400)로 막혔다 →
        둘 다 다시 만들 수 있는 캐시 · 붙이면 옛 폴더의 AI 작업(장면 점수·누끼)이 이김."""
        cut, url = self.make_ai()
        os.rename(core.VIDEOS / OLD, core.VIDEOS / NEW)  # 탐색기에서 이름 바꾸기
        core._STEMS["key"] = None
        self.ai_ran_under(NEW)
        self.assertEqual(self.local(NEW)["renamedFrom"], {"old": OLD, "seqs": 3})
        with mock.patch.object(editor, "probe", return_value={"duration": 12.5}):
            code, j = self.call("/api/rename/attach", {"name": NEW, "old": OLD})
        self.assertEqual((code, j.get("cuts")), (200, 1), j)
        self.check_moved(cut, url)
        self.assertFalse((core.adir(NEW) / "frames" / "h_000003.200.jpg").exists(), "새 이름 캐시는 지우고 옛 작업을 붙임")

    def test_plain_rename_replaces_cache_only_folder(self):
        """BR-032: 새 이름 자리에 다시 만들 수 있는 캐시만 있으면(지운 같은 이름 영상이 남긴 장면 후보·클로드 기록) 지우고 바꿈 —
        예전엔 [예전 작업 이어 붙이기]만 그랬고 앱 안 [이름 바꾸기]는 거절 · 받아쓴 폴더면 여전히 거절."""
        self.make_work()
        d = self.ai_ran_under(NEW)
        (d / "transcript.json").write_text("[]", encoding="utf-8")
        code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual(code, 400)
        self.assertIn("예전 작업", j["error"])
        (d / "transcript.json").unlink()
        self.assertEqual(self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})[0], 200)
        self.assertTrue(self.local(NEW)["analyzed"])
        self.assertFalse((d / "frames" / "h_000003.200.jpg").exists())
        self.assertEqual(len(editor.load_project(NEW)["sequences"]), 3)

    def test_copy_cache_follows_title(self):
        """클로드 문구 캐시는 지문 규칙 그대로: 제목이 바뀌면 문구도 실패 기록(1시간 쉬기)도 새 제목엔 안 맞음 ·
        날짜·영상 ID 앞머리만 바뀌어 제목이 같으면 그대로 (thumbcopy.nice_title)."""
        import time
        self.make_work()
        d = core.adir(OLD)
        (d / thumbcopy.CACHE).write_text(json.dumps({"failSig": thumbcopy._sig(OLD), "failAt": int(time.time())}), encoding="utf-8")
        self.assertTrue(thumbcopy.failed_recently(OLD))
        self.assertEqual(self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})[0], 200)
        self.assertFalse(thumbcopy.failed_recently(NEW), "새 제목은 바로 클로드에게 물을 수 있음")
        a, b = "20261001_AbCdEfGhIjK_패스 레슨.mp4", "20261008_AbCdEfGhIjK_패스 레슨.mp4"
        self.assertEqual(thumbcopy.nice_title(a), thumbcopy.nice_title(b))
        self.make_work(a)
        (core.adir(a) / thumbcopy.CACHE).write_text(json.dumps({"sig": thumbcopy._sig(a), "items": self.COPY}, ensure_ascii=False), encoding="utf-8")
        self.assertIsNotNone(thumbcopy.load_ai(a))
        self.assertEqual(self.call("/api/rename", {"name": a, "to": b[:-4]})[0], 200)
        self.assertEqual([x["l1"] for x in thumbcopy.load_ai(b)], ["패스 비법"], "제목이 같으면 클로드 문구 그대로")

    def test_locked_cut_keeps_rename_and_design(self):
        """누끼 파일을 못 옮기면(Windows 잠금) 이름 바꾸기는 그대로 · 디자인은 옛 주소 그대로 (그 그림이 옛 자리에 있음) · 다음 분석이 새로 땀."""
        cut, url = self.make_ai()
        real = os.rename

        def locked(a, b):
            if Path(a).name.startswith("cut_auto_"):
                raise PermissionError(13, "다른 프로그램이 사용 중")
            return real(a, b)
        with mock.patch.object(rename.os, "rename", side_effect=locked), mock.patch.object(rename.updater, "_retry", lambda f, *a, **k: f(*a)):
            code, j = self.call("/api/rename", {"name": OLD, "to": NEW_TITLE})
        self.assertEqual((code, j["ok"], j["cuts"]), (200, True, 0), j)
        self.assertTrue(cut.is_file())
        doc = json.loads(thumb._doc_path(NEW).read_text(encoding="utf-8"))
        self.assertEqual(doc["designs"][-1]["layers"][0]["src"], url)
        self.assertTrue(any("자동 누끼 하나는 옮기지 못했어요" in m for m in app.LOG[-8:]), app.LOG[-8:])


if __name__ == "__main__":
    unittest.main()
