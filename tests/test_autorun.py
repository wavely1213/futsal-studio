"""끝까지 자동으로 (E7 · D-200~D-203) — 저장소 폴더에서 python3 -m unittest tests.test_autorun

설정 정리 · 단계 흐름(실패해도 다음 · 앞 두 단계만 멈춤 · 멈추기 · 이어서 하기) · 썸네일 그리기 다리(화면이 맡고 돌려줌) ·
확인할 것 목록 · 실제 MSG 시험 영상으로 끝까지(롱폼·쇼츠 내보내기·썸네일·키트 · 유튜브 올리기 없음) · MSG 쇼츠 구간 정해 주기 ·
키트가 그 편집본 썸네일을 고름 · 가편집 추천 넘겨주기 · 소리 장면(#9) · 앱 경로."""
import base64
import io
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import autorun  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import msg  # noqa: E402
import thumb  # noqa: E402
import upload  # noqa: E402


def jpeg(size):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, (20, 110, 60)).save(b, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


class FakePage:
    """스튜디오 화면 흉내: 요청을 맡아 그 크기 그림을 돌려줌 (score·weak 를 정할 수 있음)."""

    def __init__(self, score=82.0, weak=False, within=True, error=None):
        self.stop, self.n = threading.Event(), 0
        self.score, self.weak, self.within, self.error = score, weak, within, error
        self.th = threading.Thread(target=self.loop, daemon=True)
        self.th.start()

    def loop(self):
        while not self.stop.is_set():
            p = autorun.pending()
            if p:
                c = autorun.claim(p["id"])
                if c:
                    self.n += 1
                    body = {"error": self.error} if self.error else {"data": jpeg((1080, 1920) if c["fmt"] == "short" else (1280, 720)),
                                                                     "score": self.score, "weak": self.weak, "within": self.within, "gates": []}
                    autorun.deliver(c["id"], body)
            time.sleep(0.05)

    def close(self):
        self.stop.set()
        self.th.join(2)


class Work:
    """시험 작업 폴더 (영상 없이 · 기록 파일만)."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="자동 시험 "))
        w = self.tmp / "작업"
        dirs = {"WORK": w, "VIDEOS": w / "videos", "ANALYSIS": w / "analysis", "OUT": w / "out"}
        for d in dirs.values():
            d.mkdir(parents=True)
        self.p = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        for x in self.p:
            x.start()
        self.name = "레슨 영상.mp4"
        (dirs["VIDEOS"] / self.name).write_bytes(b"x")

    def close(self):
        for x in self.p:
            x.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Options(unittest.TestCase):
    def test_defaults_and_clamps(self):
        with mock.patch.object(autorun, "default_style", return_value={"kind": "preset", "name": "담백 레슨형"}):
            o = autorun.options({"intensity": "아주 많이", "shorts": 99, "style": {"kind": "evil", "name": "x"}, "preset": "4k", "proofread": "yes"})
            self.assertEqual(o["intensity"], "보통")
            self.assertEqual(o["shorts"], autorun.SHORTS_MAX)
            self.assertEqual(o["style"], {"kind": "preset", "name": "담백 레슨형"})
            self.assertEqual(o["preset"], "youtube")
            self.assertFalse(o["proofread"])  # True 만 켬 (화면이 보낸 체크 그대로)
            self.assertEqual(autorun.options({"shorts": True})["shorts"], autorun.SHORTS_DEFAULT)
            self.assertEqual(autorun.options({"shorts": -3})["shorts"], 0)
            self.assertEqual(autorun.options(None)["shorts"], autorun.SHORTS_DEFAULT)
        o = autorun.options({"intensity": "듬뿍", "shorts": "2", "style": {"kind": "mix", "name": "내 섞기"}, "writer": True, "model": "small"})
        self.assertEqual((o["intensity"], o["shorts"], o["style"], o["writer"], o["model"]), ("듬뿍", 2, {"kind": "mix", "name": "내 섞기"}, True, "small"))

    def test_phone_uses_pc_prefs(self):
        """휴대폰은 클로드 오타·재미 자막·받아쓰기 방식을 안 보냄 → PC 화면에서 마지막으로 고른 것 (E7 검토)."""
        w = Work()
        self.addCleanup(w.close)
        with mock.patch.object(autorun, "default_style", return_value={"kind": "preset", "name": "담백 레슨형"}):
            o = autorun.options({"intensity": "듬뿍"})
            self.assertEqual((o["proofread"], o["writer"]), (False, False), "남긴 것이 없으면 끔")
            autorun.options({"proofread": True, "writer": True, "model": "small"}, remember=True)  # PC 스튜디오에서 시작
            o = autorun.options({"intensity": "듬뿍", "shorts": 2})  # 휴대폰
            self.assertEqual((o["proofread"], o["writer"], o["model"]), (True, True, "small"))
            autorun.save_prefs({"writer": False})  # 편집실 MSG 체크를 끔
            o = autorun.options({})
            self.assertEqual((o["proofread"], o["writer"]), (True, False))
            o = autorun.options({"proofread": False})  # 화면이 보낸 값이 먼저
            self.assertFalse(o["proofread"])
            self.assertEqual(autorun.load_prefs()["proofread"], True, "remember 없이는 남기지 않음")

    def test_default_style_order(self):
        """첫 가편집 배운 스타일 → 섞은 스타일(초안) 파일 → 기본 스타일 첫째."""
        import style
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(style, "rough_default", return_value="쪼살"):
            self.assertEqual(autorun.default_style(), {"kind": "style", "name": "쪼살"})
        with mock.patch.object(style, "rough_default", return_value=None), mock.patch.object(msg, "mix_dir", return_value=tmp):
            self.assertEqual(autorun.default_style(), {"kind": "preset", "name": next(iter(msg.PRESETS))})
            (tmp / f"{msg.DRAFT_NAME}.json").write_text("{}", encoding="utf-8")
            self.assertEqual(autorun.default_style(), {"kind": "mix", "name": msg.DRAFT_NAME})


class Flow(unittest.TestCase):
    """단계 흐름 (단계 함수는 흉내)."""

    def setUp(self):
        self.w = Work()
        self.addCleanup(self.w.close)
        self.calls = []
        editor.CANCEL.clear()

    def fns(self, fail=(), stop_at=None, fatal=None):
        def mk(key):
            def f(c):
                self.calls.append(key)
                if key == stop_at:
                    editor.CANCEL.set()
                    raise RuntimeError("내보내기를 멈췄어요")
                if key == fatal or key in fail:
                    raise autorun.AutoError(f"{key} 안 됨")
                return "done", key
            return f
        return {k: mk(k) for k, _ in autorun.STEPS if k != "check"}  # '확인할 것 정리'는 진짜 (목록을 만듦)

    def run_(self, resume=False, **kw):
        with mock.patch.dict(autorun.STEP_FN, self.fns(**kw)), mock.patch.object(autorun, "caption_checks", return_value=[]):
            return autorun.run(self.w.name, autorun.options({"style": {"kind": "preset", "name": "담백 레슨형"}}), lambda m: None, resume=resume)

    def test_all_steps_in_order_and_state_saved(self):
        r = self.run_()
        self.assertEqual(self.calls, [k for k, _ in autorun.STEPS][:-1])
        self.assertEqual(r["status"], "done")
        self.assertEqual([s["state"] for s in r["steps"]], ["done"] * len(autorun.STEPS))
        self.assertTrue(autorun.state_path(self.w.name).is_file())
        self.assertEqual(autorun.view(self.w.name)["resumable"], False)
        self.assertEqual(autorun.LIVE, {})

    def test_failed_step_does_not_stop_and_is_listed(self):
        r = self.run_(fail=("thumbs",))
        self.assertEqual(r["status"], "done")
        self.assertIn("kits", self.calls)
        st = {s["key"]: s for s in r["steps"]}
        self.assertEqual(st["thumbs"]["state"], "fail")
        self.assertTrue(any(c["kind"] == "fail" and "썸네일" in c["text"] for c in r["check"]), r["check"])

    def test_fatal_step_stops_and_keeps_record(self):
        with self.assertRaises(autorun.AutoError) as e:
            self.run_(fatal="analyze")
        self.assertIn("편집점 찾기", str(e.exception))
        self.assertEqual(self.calls, ["analyze"])
        st = autorun.load(self.w.name)
        self.assertEqual(st["status"], "failed")
        self.assertTrue(autorun.view(self.w.name)["resumable"])

    def test_stop_then_resume_skips_done_steps(self):
        r = self.run_(stop_at="export_long")
        self.assertEqual(r["status"], "stopped")
        st = {s["key"]: s["state"] for s in r["steps"]}
        self.assertEqual(st["msg_long"], "done")
        self.assertEqual(st["export_long"], "todo")  # 하던 단계는 다음에 처음부터
        self.assertTrue(any("아직 안 했어요" in c["text"] for c in r["check"]))
        run_id = r["id"]
        editor.CANCEL.clear()
        self.calls.clear()
        r2 = self.run_(resume=True)
        self.assertEqual(r2["id"], run_id, "이어서 하기는 같은 기록")
        self.assertEqual(self.calls, ["export_long", "msg_shorts", "export_shorts", "thumbs", "kits"])
        self.assertEqual(r2["status"], "done")

    def test_resume_reruns_failed_steps_and_check(self):
        with self.assertRaises(autorun.AutoError):
            self.run_(fatal="rough")
        self.calls.clear()
        r = self.run_(resume=True)
        self.assertEqual(self.calls[0], "rough")
        self.assertEqual(r["status"], "done")
        self.calls.clear()
        r2 = self.run_(resume=True)  # 끝난 기록 → 새로 (이어서 할 것이 없음)
        self.assertNotEqual(r2["id"], r["id"])
        self.assertEqual(self.calls, [k for k, _ in autorun.STEPS][:-1])

    def test_done_with_failed_step_can_redo_only_missing(self):
        """다 했지만 실패한 단계(화면이 없어 못 그린 썸네일 등)가 있으면 [못 한 것 다시 하기] — 그 단계부터 뒤만 다시 (E7 검토)."""
        r = self.run_(fail=("thumbs",))
        self.assertEqual(r["status"], "done")
        self.assertTrue(r["redo"])
        self.assertTrue(autorun.view(self.w.name)["resumable"])
        self.calls.clear()
        r2 = self.run_(resume=True)
        self.assertEqual(r2["id"], r["id"], "같은 기록에 이어서")
        self.assertEqual(self.calls, ["thumbs", "kits"], "앞 단계는 건너뛰고 썸네일과 그 뒤(키트)만")
        self.assertFalse(r2["redo"])
        self.assertFalse(autorun.view(self.w.name)["resumable"])

    def test_fresh_start_archives_previous_record(self):
        """D-207: '처음부터 다시'(또는 끝난 기록에 [시작])는 지난 기록을 'auto_run_<기록 id>.json' 으로 남김 (화면 '지난번 기록은 두고' ·
        예전: 같은 파일에 덮어써 결과물 목록·확인할 것이 사라짐 · 새 작업이 첫 단계에서 실패해도)."""
        r = self.run_(stop_at="export_long")
        old_id = r["id"]
        editor.CANCEL.clear()
        with self.assertRaises(autorun.AutoError):
            self.run_(fatal="analyze")  # 새 작업이 첫 단계에서 실패
        arch = core.adir(self.w.name) / f"auto_run_{old_id}.json"
        self.assertTrue(arch.is_file())
        old = json.loads(arch.read_text(encoding="utf-8"))
        self.assertEqual((old["id"], old["status"]), (old_id, "stopped"))
        self.assertTrue(any("아직 안 했어요" in c["text"] for c in old["check"]), "지난 확인할 것 그대로")
        self.assertNotEqual(autorun.load(self.w.name)["id"], old_id)
        with mock.patch.object(autorun, "ARCHIVE_KEEP", 2):
            for _ in range(3):
                self.run_()
        self.assertEqual(len(list(core.adir(self.w.name).glob("auto_run_*.json"))), 2, "최근 몇 개만")

    def test_crash_marker_names_the_video(self):
        got = []
        with mock.patch.object(autorun.studiolog, "target", lambda **w: got.append(w)):
            self.run_()
        self.assertEqual(got[0], {"path": "/api/auto", "names": [self.w.name]})


class Bridge(unittest.TestCase):
    """썸네일 그리기: 작업이 요청 → 화면 하나가 맡음 → 그린 그림을 서버가 정한 이름으로 저장."""

    def setUp(self):
        self.w = Work()
        self.addCleanup(self.w.close)
        editor.CANCEL.clear()

    def test_page_draws_and_file_named_by_server(self):
        pg = FakePage(score=77)
        try:
            r = autorun.request_thumb(self.w.name, "short", "자동 쇼츠 1 · 퍼스트 터치_썸네일", [10.0, 40.0])
        finally:
            pg.close()
        self.assertEqual(r["file"], "레슨 영상_자동 쇼츠 1 · 퍼스트 터치_썸네일_1.jpg")
        self.assertTrue((core.OUT / r["file"]).is_file())
        self.assertEqual((r["score"], r["weak"], r["within"]), (77.0, False, True))
        self.assertIsNone(autorun.pending())

    def test_avoid_copy_passed_and_recorded(self):
        got = {}

        def page():
            for _ in range(200):
                p = autorun.pending()
                if p:
                    c = autorun.claim(p["id"])
                    got.update(c)
                    autorun.deliver(c["id"], {"data": jpeg((1280, 720)), "copy": "퍼스트 터치/세 가지면 돼요", "t": 12.3})
                    return
                time.sleep(0.02)
        th = threading.Thread(target=page)
        th.start()
        r = autorun.request_thumb(self.w.name, "long", "x_썸네일", None, ["첫 터치에서/다 뺏기죠?", ""])
        th.join(2)
        self.assertEqual(got["avoid"], ["첫 터치에서/다 뺏기죠?"])
        self.assertEqual((r["copy"], r["t"]), ("퍼스트 터치/세 가지면 돼요", 12.3))

    def test_claim_once_and_stale_deliver(self):
        out = {}
        t = threading.Thread(target=lambda: out.update(e=self.catch(lambda: autorun.request_thumb(self.w.name, "long", "x_썸네일"))))
        with mock.patch.object(autorun, "DRAW_WAIT", 0.6):
            t.start()
            for _ in range(100):
                if autorun.pending():
                    break
                time.sleep(0.02)
            rid = autorun.pending()["id"]
            self.assertEqual(autorun.claim(rid)["fmt"], "long")
            self.assertIsNone(autorun.claim(rid), "두 번째 화면은 못 맡음")
            self.assertIsNone(autorun.pending(), "맡은 요청은 다른 화면에 안 보임")
            t.join(5)  # 맡고 아무것도 안 옴 → 기다리다 건너뜀
        self.assertIn("너무 오래", str(out["e"]))
        with self.assertRaises(LookupError):
            autorun.deliver(rid, {"data": jpeg((1280, 720))})
        self.assertEqual(list(core.OUT.glob("*.jpg")), [])

    def test_bad_or_unsaved_delivery_fails_now_with_reason(self):
        """D-207 (E7 재검토): 그림이 이상하거나(400) 저장을 못 하면(500 · 디스크 가득·잠김) 기다리는 작업이 그 까닭으로 바로 실패
        (예전: 화면은 닫히고 작업은 5분 기다린 뒤 '너무 오래 걸려'로 건너뜀 · 진짜 까닭은 사라짐)."""
        for bad, exc, why in (({"data": "data:text/html;base64,AAAA"}, ValueError, "형식이 다르거나"),
                              ({"data": "data:image/jpeg;base64," + "A" * (autorun.DATA_MAX + 10)}, ValueError, "너무 커요"),
                              ({"data": jpeg((1280, 720))}, OSError, "저장하지 못했어요")):
            out = {}
            t = threading.Thread(target=lambda: out.update(e=self.catch(lambda: autorun.request_thumb(self.w.name, "long", "x_썸네일"))))
            with mock.patch.object(autorun, "DRAW_WAIT", 30), \
                    mock.patch.object(thumb, "export_image", side_effect=OSError(28, "No space left on device")):
                t.start()
                for _ in range(200):
                    if autorun.pending():
                        break
                    time.sleep(0.02)
                rid = autorun.pending()["id"]
                autorun.claim(rid)
                t0 = time.time()
                with self.assertRaises(exc):
                    autorun.deliver(rid, bad)
                t.join(5)
            self.assertLess(time.time() - t0, 3, "기다리지 않고 바로")
            self.assertIsInstance(out["e"], autorun.AutoError)
            self.assertIn(why, str(out["e"]))
            self.assertNotIn("너무 오래", str(out["e"]))
            self.assertIsNone(autorun.pending())

    def catch(self, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            return e

    def test_page_leaving_releases_claim(self):
        """D-207: 그리던 화면이 떠나면(pagehide 신호 /api/auto/release) 맡기를 풀어 다른 화면이 바로 이어서 그림 —
        예전: 다른 화면은 5분 동안 'pending 없음'만 보고 작업은 '너무 오래 걸려'로 건너뜀."""
        out, got = {}, []
        t = threading.Thread(target=lambda: out.update(r=self.catch(lambda: autorun.request_thumb(self.w.name, "long", "x_썸네일"))))
        with mock.patch.object(autorun, "DRAW_WAIT", 30), mock.patch.object(core, "set_progress", lambda **k: got.append(k)):
            t.start()
            for _ in range(200):
                if autorun.pending():
                    break
                time.sleep(0.02)
            rid = autorun.pending()["id"]
            self.assertTrue(autorun.claim(rid))
            self.assertIsNone(autorun.pending())
            t0 = time.time()
            self.assertTrue(autorun.release(rid))
            self.assertFalse(autorun.release(rid), "맡은 화면이 없으면 풀 것도 없음")
            self.assertEqual(autorun.pending(), {"id": rid}, "다른 화면이 맡을 수 있음")
            time.sleep(0.8)
            self.assertTrue(autorun.claim(rid))
            autorun.deliver(rid, {"data": jpeg((1280, 720)), "score": 81})
            t.join(5)
        self.assertLess(time.time() - t0, 5)
        self.assertTrue(out["r"]["file"].endswith(".jpg"), out)
        self.assertTrue(any("닫혔어요" in g.get("detail", "") for g in got), [g.get("detail") for g in got])
        self.assertFalse(autorun.release("없는 요청"))

    def test_waiting_for_page_tells_owner(self):
        """화면이 맡지 않으면 진행 표시에 '창을 열어 주세요' · 숨은 창이 1분에 한 번 묻는 것보다 넉넉히 기다림 (E7 검토)."""
        self.assertGreaterEqual(autorun.PAGE_WAIT, 300)
        got = []
        with mock.patch.object(autorun, "PAGE_WAIT", 0.6), mock.patch.object(autorun, "PAGE_NOTE", 0.1), \
                mock.patch.object(core, "set_progress", lambda **k: got.append(k)):
            with self.assertRaises(autorun.AutoError):
                autorun.request_thumb(self.w.name, "long", "x_썸네일")
        self.assertEqual(len(got), 1)
        self.assertIn("창을 열어", got[0]["detail"])
        # 창을 다시 열어 화면이 맡으면 그 안내를 거두고 그림을 받음
        got.clear()
        rq = {}

        def page():
            while not autorun.pending():
                time.sleep(0.02)
            time.sleep(1.2)  # 안내가 뜬 뒤에 맡음 (작업은 0.5초마다 봄)
            rq["id"] = autorun.pending()["id"]
            autorun.claim(rq["id"])
            time.sleep(0.6)
            autorun.deliver(rq["id"], {"data": jpeg((1280, 720)), "score": 80})
        th = threading.Thread(target=page)
        th.start()
        with mock.patch.object(autorun, "PAGE_NOTE", 0.1), mock.patch.object(core, "set_progress", lambda **k: got.append(k)):
            r = autorun.request_thumb(self.w.name, "long", "x_썸네일")
        th.join()
        self.assertTrue(r["file"].endswith(".jpg"))
        self.assertEqual([("창을 열어" in g["detail"]) for g in got], [True, False], [g["detail"] for g in got])

    def test_no_page_and_error_and_stop(self):
        with mock.patch.object(autorun, "PAGE_WAIT", 0.3):
            with self.assertRaises(autorun.AutoError) as e:
                autorun.request_thumb(self.w.name, "long", "x_썸네일")
            self.assertEqual(str(e.exception), autorun.NO_PAGE)
        pg = FakePage(error="장면을 찾지 못했어요")
        try:
            with self.assertRaises(autorun.AutoError) as e:
                autorun.request_thumb(self.w.name, "long", "x_썸네일")
            self.assertIn("장면을 찾지 못했어요", str(e.exception))
        finally:
            pg.close()
        editor.CANCEL.set()
        try:
            with self.assertRaises(autorun.Stopped):
                autorun.request_thumb(self.w.name, "long", "x_썸네일")
        finally:
            editor.CANCEL.clear()


class Checklist(unittest.TestCase):
    def st(self, outputs, steps=None, status="done"):
        return {"name": "x.mp4", "status": status, "steps": steps or {"rough": {"state": "done"}}, "outputs": outputs}

    def test_items(self):
        long = {"key": "long", "kind": "long", "name": "자동 롱폼", "seq": "a", "msg": True, "export": "a.mp4",
                "qa": [{"lv": "warn", "title": "소리가 작아요", "msg": "-20.1 LUFS", "t": None}, {"lv": "warn", "title": "멈춘 화면", "msg": "00:03", "t": 3},
                       {"lv": "bad", "title": "검은 화면", "msg": "00:10 부터", "t": 10}],
                "thumb": "a.jpg", "thumbInfo": {"score": 55, "weak": False, "gates": []},
                "kit": {"txt": "a.txt", "chapters": 1, "duration": 400, "foreign": ["남의 채널"]}}
        short = {"key": "short1", "kind": "shorts", "name": "자동 쇼츠 1", "seq": "b", "msg": False, "msgFail": "재미 순간이 모자라요", "export": None,
                 "exportFail": "디스크가 가득 찼어요", "qa": [], "thumb": None, "thumbFail": autorun.NO_PAGE, "kit": None}
        short2 = {"key": "short2", "kind": "shorts", "name": "자동 쇼츠 2", "seq": "c", "msg": True, "export": "c.mp4", "qa": [],
                  "thumb": "c.jpg", "thumbInfo": {"score": 88, "within": False}, "kit": {"txt": "c.txt", "chapters": 0, "duration": 40}}
        with mock.patch.object(autorun, "caption_checks", return_value=[{"kind": "caption", "lv": "warn", "text": "자막 확인 00:12 '파벨' ·", "t": 12}]):
            items = autorun.checklist(self.st([long, short, short2], {k: {"state": "done"} for k in ("rough", "export_long", "export_shorts", "kits")}
                                              | {"thumbs": {"state": "fail", "note": "화면 없음"}}))
        kinds = [c["kind"] for c in items]
        texts = "\n".join(c["text"] for c in items)
        self.assertEqual(items[-1]["kind"], "upload")
        self.assertIn("유튜브에는 아직 올리지 않았어요", items[-1]["text"])
        self.assertEqual([c["lv"] for c in items[:-1]], sorted([c["lv"] for c in items[:-1]], key=["bad", "warn"].index), "막힌 것 먼저")
        for k in ("fail", "caption", "loud", "qa", "thumb", "source", "chapters", "msg", "export", "kit"):
            self.assertIn(k, kinds, k)
        self.assertNotIn("멈춘 화면", texts, "검수의 가벼운 경고(멈춘 화면)는 빼고 소리·막힌 것만")
        self.assertIn("55점", texts)
        self.assertIn("쇼츠 구간에는 썸네일로 쓸 장면이 없어", texts)
        self.assertNotIn("자동 쇼츠 2 · 챕터", texts, "쇼츠는 챕터를 안 셈")

    def test_weak_scenes_one_line(self):
        outs = [{"key": k, "kind": "shorts", "name": k, "seq": k, "msg": True, "export": k, "qa": [], "thumb": k + ".jpg",
                 "thumbInfo": {"score": 60, "weak": True}, "kit": {"txt": "x"}} for k in ("short1", "short2", "short3")]
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            items = autorun.checklist(self.st(outs))
        th = [c for c in items if c["kind"] == "thumb"]
        self.assertEqual(len(th), 1, "약한 장면은 영상 탓이라 한 줄")
        self.assertIn("결과물 3개", th[0]["text"])
        self.assertIn("60점", th[0]["text"], "왜 떴는지 (점수)")
        for o in outs:  # 점수가 높으면 장면이 약하다는 표시가 있어도 쓸 만함 → 안 띄움 (E7 검토: 95.8점에 '약해요')
            o["thumbInfo"] = {"score": 95.8, "weak": True}
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            items = autorun.checklist(self.st(outs))
        self.assertEqual([c["kind"] for c in items], ["upload"])

    def test_no_page_one_line_with_names(self):
        """D-207: 롱폼 + 쇼츠 6개가 모두 '그릴 화면이 없어요'면 그 긴 안내를 결과물마다(8번) 되풀이하지 않고 결과물 이름을 모은 한 줄 ·
        안내는 결과 카드의 [못 한 것 다시 하기] · 결과물마다 [썸네일] (썸네일 만들기에서 아무 이름으로 저장하면 모든 키트가 같은 그림)."""
        outs = [{"key": k, "kind": "long" if k == "long" else "shorts", "name": f"자동 {k}", "seq": k, "msg": True, "export": k + ".mp4", "qa": [],
                 "thumb": None, "thumbFail": autorun.NO_PAGE, "kit": {"txt": k + ".txt"}} for k in ["long"] + [f"short{i}" for i in range(1, 7)]]
        steps = {k: {"state": "done"} for k in ("rough", "export_long", "export_shorts", "kits")} | {"thumbs": {"state": "fail", "note": autorun.NO_PAGE}}
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            items = autorun.checklist(self.st(outs, steps))
        hits = [c for c in items if autorun.NO_PAGE in c["text"]]
        self.assertEqual(len(hits), 1, [c["text"] for c in items])
        self.assertEqual((hits[0]["kind"], hits[0]["lv"], hits[0]["outs"]), ("thumb", "bad", [o["key"] for o in outs]))
        self.assertIn("자동 long, 자동 short1, 자동 short2 외 4개", hits[0]["text"])
        self.assertIn("[못 한 것 다시 하기]", autorun.NO_PAGE)
        self.assertIn("[썸네일]", autorun.NO_PAGE)
        self.assertNotIn("AI 추천 썸네일'로 직접", autorun.NO_PAGE)
        self.assertEqual([c["kind"] for c in items], ["thumb", "upload"])

    def test_generic_short_title_listed(self):
        o = {"key": "short1", "kind": "shorts", "name": "자동 쇼츠 1 · 퍼스트 터치 꿀팁", "seq": "a", "msg": True, "export": "a.mp4", "qa": [],
             "thumb": "a.jpg", "thumbInfo": {"score": 90}, "kit": {"txt": "a"}, "hook": "퍼스트 터치 꿀팁", "hookCheck": True}
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            items = autorun.checklist(self.st([o]))
        self.assertEqual(items[0]["kind"], "title")
        self.assertIn("'퍼스트 터치 꿀팁'", items[0]["text"])

    def test_stopped_lists_steps_not_each_missing_file(self):
        o = {"key": "long", "kind": "long", "name": "롱", "seq": "a", "msg": True, "export": None, "qa": [], "thumb": None, "kit": None}
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            items = autorun.checklist(self.st([o], {"rough": {"state": "done"}, "msg_long": {"state": "done"}, "export_long": {"state": "todo"}},
                                              status="stopped"))
        self.assertNotIn("export", [c["kind"] for c in items])
        self.assertNotIn("kit", [c["kind"] for c in items])
        self.assertTrue(any("롱폼 내보내기 · 아직 안 했어요" in c["text"] for c in items))

    def test_short_long_video_has_no_chapter_item(self):
        o = {"key": "long", "kind": "long", "name": "롱", "seq": "a", "msg": True, "export": "a.mp4", "qa": [], "thumb": "a.jpg", "thumbInfo": {"score": 90},
             "kit": {"txt": "a.txt", "chapters": 0, "duration": 120}}
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            items = autorun.checklist(self.st([o]))
        self.assertEqual([c["kind"] for c in items], ["upload"], "3분 안 되는 영상은 챕터를 안 넣음 → 확인할 것 없음")

    def test_caption_checks_lists_flagged_and_low_confidence(self):
        import captions
        w = Work()
        self.addCleanup(w.close)
        d = core.adir(w.name)
        d.mkdir(parents=True)
        words = [{"w": "파벨로", "s": 5.0, "e": 5.5, "p": 0.21}, {"w": "어?", "s": 5.55, "e": 5.6, "p": 0.03}, {"w": "받아요", "s": 5.6, "e": 6.0, "p": 0.95},
                 {"w": "감사합니다", "s": 20.0, "e": 20.6, "p": 0.3}]
        (d / "transcript.json").write_text(json.dumps([{"start": 5, "end": 6, "text": "파벨로 어? 받아요", "words": words[:3]},
                                                       {"start": 20, "end": 20.6, "text": "감사합니다", "words": words[3:]}], ensure_ascii=False), encoding="utf-8")
        rec = {"junk_list": [{"a": 20.0, "b": 20.6, "why": captions.HALLU_WHY}, {"a": 1, "b": 2, "why": "군말"}]}
        segs = [{"start": 5, "end": 6, "text": "파벨로 어? 받아요", "words": words[:3]}]
        with mock.patch.object(editor, "recommend", return_value=rec), mock.patch.object(editor, "_segments_of", return_value=segs):
            out = autorun.caption_checks(w.name)
        self.assertEqual([c["t"] for c in out], [5.0, 20.0], "한 글자 '어?'는 빼고")
        self.assertIn("'파벨로'", out[0]["text"])
        self.assertIn("21%", out[0]["text"])
        self.assertIn("지어낸 말 같아서 뺐어요", out[1]["text"])
        self.assertIn("'감사합니다'", out[1]["text"])
        self.assertIn("원본 00:05", out[0]["text"], "결과물이 없으면 원본 시각")

    def test_caption_checks_only_inside_outputs_with_their_times(self):
        """결과물에 들어간 말(또는 그 사이에서 지운 말)만 · '결과물 이름 + 그 편집본 시각' (E7 검토: 원본 시각·모든 결과물에서 잘린 말)."""
        import captions
        w = Work()
        self.addCleanup(w.close)
        d = core.adir(w.name)
        d.mkdir(parents=True)
        words = [{"w": "파벨로", "s": 5.0, "e": 5.5, "p": 0.21}, {"w": "디딤발", "s": 40.0, "e": 40.5, "p": 0.2},
                 {"w": "감사합니다", "s": 20.0, "e": 20.6, "p": 0.3}, {"w": "구독", "s": 90.0, "e": 90.5, "p": 0.1}]
        (d / "transcript.json").write_text(json.dumps([{"start": x["s"], "end": x["e"], "text": x["w"], "words": [x]} for x in words],
                                                      ensure_ascii=False), encoding="utf-8")
        rec = {"junk_list": [{"a": 20.0, "b": 20.6, "why": captions.HALLU_WHY}, {"a": 90.0, "b": 90.5, "why": captions.HALLU_WHY}]}
        segs = [{"start": x["s"], "end": x["e"], "text": x["w"], "words": [x]} for x in words if x["w"] in ("파벨로", "디딤발")]
        long_items = editor._pair(0.0, 10.0, start=0.0) + editor._pair(25.0, 30.0, start=10.0)  # 20초 '감사합니다'는 두 컷 사이에서 지움
        short_items = editor._pair(38.0, 45.0, start=0.0)
        proj = {"sequences": [{"id": "L", "items": long_items}, {"id": "S", "items": short_items}]}
        outs = [{"key": "long", "kind": "long", "name": "자동 롱폼", "seq": "L"}, {"key": "short1", "kind": "shorts", "name": "자동 쇼츠 1", "seq": "S"}]
        with mock.patch.object(editor, "recommend", return_value=rec), mock.patch.object(editor, "_segments_of", return_value=segs), \
                mock.patch.object(editor, "load_project", return_value=proj):
            out = autorun.caption_checks(w.name, outs)
        texts = [c["text"] for c in out]
        self.assertEqual(len(out), 3, texts)
        self.assertFalse(any("구독" in t for t in texts), "모든 결과물 밖(90초)은 뺌")
        self.assertIn("자동 롱폼 00:05 '파벨로'", texts[0])
        self.assertIn("자동 롱폼 00:10 '감사합니다'", texts[1], "지운 말은 그 이음매 시각")
        self.assertIn("자동 쇼츠 1 00:02 '디딤발'", texts[2])
        self.assertEqual((out[2]["seq"], out[2]["out"], out[2]["t"], out[2]["src"]), ("S", "short1", 2.25, 40.0))


class Pieces(unittest.TestCase):
    def setUp(self):
        self.w = Work()
        self.addCleanup(self.w.close)

    def test_kit_prefers_this_sequences_own_thumbnail(self):
        from PIL import Image
        pre = core.adir(self.w.name).name
        for nm, size, age in ((f"{pre}_자동 쇼츠 1 · A_썸네일_1.jpg", (1080, 1920), 50), (f"{pre}_자동 쇼츠 2 · B_썸네일_1.jpg", (1080, 1920), 10),
                              (f"{pre}_디자인 1_1.jpg", (1280, 720), 0)):
            Image.new("RGB", size).save(core.OUT / nm)
            t = time.time() - age
            import os
            os.utime(core.OUT / nm, (t, t))
        a = upload.thumbnail_check(self.w.name, "shorts", {"name": "자동 쇼츠 1 · A"})
        self.assertEqual(a["file"], f"{pre}_자동 쇼츠 1 · A_썸네일_1.jpg")
        self.assertTrue(a["ok"], a)
        self.assertEqual(upload.thumbnail_check(self.w.name, "shorts")["file"], f"{pre}_자동 쇼츠 2 · B_썸네일_1.jpg", "편집본을 안 주면 예전처럼 가장 최근")
        # 제 썸네일이 없는 편집본은 다른 편집본의 자동 썸네일을 쓰지 않고 따로 저장한 것만 (D-207)
        self.assertEqual(upload.thumbnail_check(self.w.name, "shorts", {"name": "다른 쇼츠"})["file"], f"{pre}_디자인 1_1.jpg")

    def test_kit_prefers_thumbnail_remade_after_auto(self):
        """자동 썸네일 뒤에 썸네일 편집기에서 다시 저장하면 키트는 다시 만든 것을 고름 (E7 검토 · 사람이 고친 것이 이김)."""
        from PIL import Image
        import os
        pre = core.adir(self.w.name).name
        own, other, manual, old = (f"{pre}_자동 쇼츠 1 · A_썸네일_1.jpg", f"{pre}_자동 쇼츠 2 · B_썸네일_1.jpg",
                                   f"{pre}_디자인 1_쇼츠_1.jpg", f"{pre}_디자인 1_쇼츠_2.jpg")
        for nm, age in ((old, 100), (own, 50), (other, 5), (manual, 10)):
            Image.new("RGB", (1080, 1920)).save(core.OUT / nm)
            t = time.time() - age
            os.utime(core.OUT / nm, (t, t))
        sq = {"name": "자동 쇼츠 1 · A", "format": "shorts"}
        self.assertEqual(upload.thumbnail_check(self.w.name, "shorts", sq)["file"], manual, "나중에 직접 만든 것")
        t = time.time() - 60
        os.utime(core.OUT / manual, (t, t))
        self.assertEqual(upload.thumbnail_check(self.w.name, "shorts", sq)["file"], own, "직접 만든 것이 더 예전이면 자동 썸네일")
        self.assertEqual(upload.thumbnail_check(self.w.name, "shorts", {"name": "자동 쇼츠 2 · B"})["file"], other, "다른 쇼츠의 자동 썸네일보다 제 것")
        # 결과 카드 [썸네일]이 연 편집기는 그 편집본 이름(label)으로 저장 → 그 편집본 것으로 잡힘
        f = thumb.export_image(self.w.name, "data:image/jpeg;base64," + base64.b64encode((core.OUT / own).read_bytes()).decode(),
                               "jpg", autorun.public({"name": self.w.name, "id": "x", "steps": {}, "outputs": [
                                   {"key": "short1", "kind": "shorts", "seq": "s1", "name": "자동 쇼츠 1 · A"}]})["outputs"][0]["thumbLabel"])
        self.assertEqual(upload.thumbnail_check(self.w.name, "shorts", sq)["file"], f.name)
        self.assertEqual(upload.thumbnail_check(self.w.name, "shorts", {"name": "자동 쇼츠 2 · B"})["file"], other, "다른 쇼츠는 그대로")

    def test_output_without_own_thumbnail_never_takes_another_outputs(self):
        """D-207: 썸네일을 못 그린 쇼츠 2 의 키트가 쇼츠 1 의 자동 썸네일을 '문제없음'으로 쓰지 않음 · 새 롱폼이 예전 롱폼 편집본의
        자동 썸네일을 쓰지 않음 (유튜브 올리기는 긴 영상 썸네일을 기본으로 올림) — 따로 저장한 썸네일만."""
        from PIL import Image
        import os
        pre = core.adir(self.w.name).name
        for nm, size, age in ((f"{pre}_자동 쇼츠 1 · 퍼스트 터치 꿀팁_썸네일_1.jpg", (1080, 1920), 30),
                              (f"{pre}_자동 롱폼 · 담백 레슨형 · 보통_썸네일_1.jpg", (1280, 720), 20)):
            Image.new("RGB", size).save(core.OUT / nm)
            t = time.time() - age
            os.utime(core.OUT / nm, (t, t))
        s2 = upload.thumbnail_check(self.w.name, "shorts", {"name": "자동 쇼츠 2 · 디딤발 꿀팁", "format": "shorts"})
        self.assertEqual((s2["file"], s2["ok"]), (None, False), s2)
        lb = upload.thumbnail_check(self.w.name, "long", {"name": "자동 롱폼 · 예능 MSG형 · 보통", "format": "long"})
        self.assertEqual((lb["file"], lb["ok"]), (None, False), lb)
        Image.new("RGB", (1080, 1920)).save(core.OUT / f"{pre}_디자인 1_쇼츠_1.jpg")
        self.assertEqual(upload.thumbnail_check(self.w.name, "shorts", {"name": "자동 쇼츠 2 · 디딤발 꿀팁", "format": "shorts"})["file"],
                         f"{pre}_디자인 1_쇼츠_1.jpg", "따로 저장한 썸네일은 써도 됨")

    def test_generic_hooks_fall_back(self):
        """순서 말뿐인 추천 제목('퍼스트 터치 첫 번째 포인트')은 예전 규칙('주제어 꿀팁')으로 · 사람이 확인 (E7 검토)."""
        self.assertTrue(autorun.generic_hook("퍼스트 터치 첫 번째 포인트"))
        self.assertTrue(autorun.generic_hook("패스 두 번째 동작"))
        self.assertFalse(autorun.generic_hook("디딤발 꿀팁"))
        self.assertFalse(autorun.generic_hook("두 번째 슛은 왜 안 들어갈까?"))
        self.assertEqual(autorun.short_hook({"hook": "디딤발 꿀팁", "keywords": [], "title": ""}), ("디딤발 꿀팁", False))
        h, chk = autorun.short_hook({"hook": "퍼스트 터치 첫 번째 포인트", "keywords": ["퍼스트 터치", "포인트"], "title": "퍼스트 터치 첫 번째 포인트는"})
        self.assertEqual((h, chk), ("퍼스트 터치 꿀팁", True))
        self.assertEqual(autorun.short_hook({"hook": "패스 두 번째 동작", "keywords": [], "title": "두 번째 동작은"}), ("패스 꿀팁", True))
        # 예전 규칙이 첫 문장을 글자 수로 자른 조각('… 고개를 들어서 주변을')이면 주제어 + 꿀팁 (E7 재렌더: MSGRAW01 쇼츠 1)
        r = {"hook": "퍼스트 터치 첫 번째 포인트", "keywords": ["핵심", "포인트", "첫번째"], "title": "공이 오기 전에 고개를 들어서 주변을 먼저 보세요."}
        with mock.patch.object(editor, "_hook", return_value=r["hook"]):
            self.assertEqual(autorun.short_hook(r), ("퍼스트 터치 꿀팁", True))
        r = {"hook": "첫 번째 포인트", "keywords": [], "title": "공이 오기 전에 고개를 들어서 주변을 먼저 보세요."}
        with mock.patch.object(editor, "_hook", return_value=r["hook"]):
            h, chk = autorun.short_hook(r)
        self.assertTrue(chk)
        self.assertTrue(h.startswith("공이 오기 전에"), h)  # 주제어가 없으면 조각이라도 (사람이 확인)
        self.assertFalse(autorun._cut_off("디딤발 꿀팁", "자, 두 번째 포인트는 디딤발이에요."))

    def test_short_name_cut_at_phrase(self):
        """파일 이름이 될 쇼츠 이름: 질문 한가운데('… 있으면 왜')서 끊지 않고 구절 끝 + '…' (E7 검토)."""
        n = autorun._short_name(0, {"hook": "패스하고 그 자리에 서 있으면 왜 안 될까요?"})
        self.assertEqual(n, "자동 쇼츠 1 · 패스하고 그 자리에 서 있으면…")
        self.assertEqual(autorun._short_name(1, {"hook": "디딤발\n꿀팁"}), "자동 쇼츠 2 · 디딤발 꿀팁")
        self.assertEqual(autorun._name_cut("왜 다들 첫 터치에서, 공을 놓칠까요 이거 알면 돼요"), "왜 다들 첫 터치에서…")

    def test_new_sequence_drops_old_outputs(self):
        """이어서 할 때 가편집 결과물이 MSG 편집본으로 바뀌면 예전 완성본·썸네일·키트 기록은 버림 (다시 만들게)."""
        o = {"key": "long", "seq": "rough", "name": "롱폼 가편집", "msg": False, "msgFail": "x", "export": "a.mp4", "qa": [{"lv": "warn"}],
             "thumb": "a.jpg", "thumbInfo": {}, "kit": {"txt": "a"}}
        autorun._set_seq(o, "rough", "롱폼 가편집", False)
        self.assertEqual(o["export"], "a.mp4", "같은 편집본이면 그대로")
        autorun._set_seq(o, "m1", "자동 롱폼", True)
        self.assertEqual((o["seq"], o["msg"], o.get("export"), o.get("thumb"), o.get("kit"), o["qa"], o.get("msgFail")), ("m1", True, None, None, None, [], None))

    def test_auto_sequences_takes_given_recommendation(self):
        r = {"start": 1.0, "end": 25.0, "cuts": [{"in": 1.0, "out": 12.0}, {"in": 14.0, "out": 25.0}], "hook": "디딤발 꿀팁", "title": "디딤발", "keywords": ["디딤발"]}
        info = {"duration": 60.0, "width": 1920, "height": 1080, "fps": 30.0}
        with mock.patch.object(editor, "recommend", side_effect=AssertionError("추천을 다시 하면 안 됨")), \
                mock.patch.object(editor, "_segments_of", return_value=[]), mock.patch.object(editor, "silences_of", return_value=[]):
            seqs = editor.auto_sequences(self.w.name, info, None, ("shorts",), rec={"shorts": [r], "tidy": []})
        self.assertEqual(len(seqs), 1)
        self.assertEqual(seqs[0]["titles"][0]["text"], "디딤발 꿀팁")
        self.assertAlmostEqual(editor.seq_total(seqs[0]), 22.0, delta=0.5)

    def test_points_keep_topic_line_of_their_own_window(self):
        """쇼츠 첫머리 주제 줄 (BR-120 · 합침): 포인트마다 그 구간의 포인트(MSG 쇼츠가 씀)·주제 줄 글(가편집 쇼츠가 씀)을 남김 —
        제목을 바꾼 포인트는 바꾼 제목으로 다시 셈 · MSG 를 못 넣은 가편집 쇼츠에도 주제 줄 (설정을 끄면 없음) · 확인할 것은 화면 큰 줄."""
        r1 = {"start": 1.0, "end": 25.0, "cuts": [{"in": 1.0, "out": 25.0}], "hook": "디딤발 첫 번째 포인트", "title": "자, 첫 번째 포인트는 디딤발이에요.",
              "keywords": ["디딤발", "포인트"], "point": {"n": 1, "unit": "포인트", "phrase": "디딤발"}, "topicText": "슈팅 챌린지", "hookSaid": False}
        r2 = {"start": 30.0, "end": 55.0, "cuts": [{"in": 30.0, "out": 55.0}], "hook": "트래핑 꿀팁", "title": "트래핑은 이렇게 해요.",
              "keywords": ["트래핑"], "point": {"n": 2, "unit": "포인트", "phrase": "트래핑"}, "topicText": "슈팅 챌린지 · 포인트 2", "hookSaid": True}
        rec = {"videoTopic": "슈팅 챌린지", "shorts": [r2, r1], "tidy": []}
        segs = [{"start": 1.0, "end": 3.0, "text": "디딤발 꿀팁 하나 알려 드릴게요."}]
        st = autorun.new_state(self.w.name, autorun.options({"shorts": 2}))
        c = autorun._Ctx(st, lambda m: None, None)
        with mock.patch.object(editor, "load_project", return_value={"sequences": []}), mock.patch.object(editor, "recommend", return_value=rec), \
                mock.patch.object(editor, "_segments_of", return_value=segs):
            autorun._step_rough(c)
        p1, p2 = st["points"]
        self.assertEqual((p1["hook"], p1["hookCheck"]), ("디딤발 꿀팁", True))   # 순서 말뿐인 추천 제목 → 바꿈
        self.assertEqual(p1["point"], r1["point"])
        self.assertEqual(p1["topic"], editor.topic_line("슈팅 챌린지", r1["point"], "디딤발 꿀팁"))
        self.assertEqual(p1["topic"], "슈팅 챌린지 · 포인트 1", "바꾼 제목에 없는 번호까지 (추천 제목 기준 글 '슈팅 챌린지' 아님)")
        self.assertTrue(p1["hookSaid"], "바꾼 제목을 첫머리에 그대로 말함")
        self.assertEqual((p2["hook"], p2["point"], p2["topic"], p2["hookSaid"]), ("트래핑 꿀팁", r2["point"], "슈팅 챌린지 · 포인트 2", True))
        json.dumps(st)   # 기록 파일에 그대로 저장됨
        info = {"duration": 60.0, "width": 1920, "height": 1080, "fps": 30.0}
        with mock.patch.object(editor, "media_info", return_value=info), mock.patch.object(editor, "recommend", side_effect=AssertionError("추천을 다시 하면 안 됨")), \
                mock.patch.object(editor, "_segments_of", return_value=[]), mock.patch.object(editor, "silences_of", return_value=[]), \
                mock.patch.object(editor, "default_style_params", return_value=None):
            with mock.patch.object(editor, "shorts_topic_on", return_value=True):
                q = autorun._rough_short(c, 0, p1)
            with mock.patch.object(editor, "shorts_topic_on", return_value=False):
                q_off = autorun._rough_short(c, 0, p1)
        tops = [t for t in q["titles"] if t.get("plan") == "topic"]
        self.assertEqual([t["text"] for t in tops], ["슈팅 챌린지 · 포인트 1"])
        self.assertTrue(tops[0].get("swap"), "첫머리에 말하는 제목이면 처음 2.5초 바꿔 끼우기")
        self.assertFalse([t for t in q_off["titles"] if t.get("plan") == "topic"])
        o = {"key": "short1", "kind": "shorts", "name": "자동 쇼츠 1 · 디딤발 꿀팁", "seq": "a", "msg": True, "export": "a.mp4", "qa": [],
             "thumb": "a.jpg", "thumbInfo": {"score": 90}, "kit": {"txt": "a"}, "hook": "디딤발 꿀팁", "hookCheck": True, "opening": "디딤발 방향"}
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            items = autorun.checklist({"name": "x.mp4", "status": "done", "steps": {"rough": {"state": "done"}}, "outputs": [o]})
        self.assertIn("'디딤발 방향'", items[0]["text"], "MSG 첫머리 큰 줄(= 키트 제목)을 이 쇼츠 포인트로 바꿨으면 그 글")

    def test_shorts_window_uses_given_window(self):
        rec = {"shorts": [{"start": 50.0, "end": 80.0, "cuts": [{"in": 50.0, "out": 80.0}]}], "tidy": []}
        moms = [{"t": 60.0, "score": 9, "kind": "play", "a": 59, "b": 61}]
        win = {"start": 5.0, "end": 30.0, "cuts": [{"in": 5.0, "out": 30.0}]}
        self.assertEqual(msg._shorts_window(rec, moms, 120.0, [], win), [{"in": 5.0, "out": 30.0}])
        self.assertEqual(msg._shorts_window(rec, moms, 120.0, []), [{"in": 50.0, "out": 80.0}], "구간을 안 주면 v2.16.0 그대로")

    def test_sound_scenes_from_msg_tags_and_words(self):
        d = core.adir(self.w.name)
        d.mkdir(parents=True)
        lg = [0.0] * 20
        lg[4:7] = [0.3, 0.5, 0.2]
        ch = [0.0] * 20
        ch[15] = 0.4
        (d / "msg_signals.json").write_text(json.dumps({"tags": {"laugh": lg, "cheer": ch}, "tagStep": 0.48}), encoding="utf-8")
        segs = [{"start": 0.5, "end": 1.0, "text": "안녕하세요"}, {"start": 6.0, "end": 6.6, "text": "하하 그렇죠"}]
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": [{"time": 2.3}, {"time": 30.0}]}), encoding="utf-8")
        sc = core.sound_scenes(d, segs)
        self.assertEqual([k for _, _, k in sc], ["laugh", "laugh_txt", "cheer"])
        self.assertAlmostEqual(sc[0][0], 1.92, places=2)
        ev = [e for e in core.timeline_events(self.w.name) if e["type"] == "peak"]
        texts = [e["text"] for e in ev]
        self.assertIn("웃음이 터짐 (소리 인식)", texts)
        self.assertIn("환호·박수 (소리 인식)", texts)
        self.assertIn("웃음 (받아쓰기)", texts)
        self.assertEqual(sum("갑자기 커짐" in t for t in texts), 1, "웃음 자리의 '소리가 갑자기 커짐'은 하나로 (30초 것만 남음)")
        self.assertEqual(core.SCENE_THR, {"laugh": msg.LAUGH_THR, "cheer": msg.CHEER_THR})
        (d / "msg_signals.json").unlink()
        self.assertEqual([k for _, _, k in core.sound_scenes(d, segs)], ["laugh_txt"], "소리 모델 기록이 없어도 받아쓰기 웃음은")


class EndToEnd(unittest.TestCase):
    """실제 MSG 시험 영상(34초)으로 끝까지 — 롱폼·쇼츠 MSG·내보내기(작은 설정)·검수·썸네일(화면 흉내)·키트."""

    @classmethod
    def setUpClass(cls):
        from make_msg_fixture import MsgWork
        cls.w = MsgWork()
        editor.CANCEL.clear()
        cls.page = FakePage(score=84)
        cls.logs = []
        with mock.patch.object(thumb, "cached_analysis", lambda n: {"frames": []}):
            cls.res = autorun.run(cls.w.name, autorun.options({"preset": "small", "shorts": 3, "style": {"kind": "preset", "name": "예능 MSG형"}}),
                                  cls.logs.append)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.w.close()

    def test_outputs_made_and_not_uploaded(self):
        r = self.res
        self.assertEqual(r["status"], "done", r["steps"])
        outs = [o for o in r["outputs"] if o.get("seq")]
        self.assertEqual(outs[0]["kind"], "long")
        self.assertGreaterEqual(len(outs), 2)
        for o in outs:
            self.assertTrue(o["msg"], o)
            self.assertTrue((core.OUT / o["export"]).is_file())
            self.assertTrue((core.OUT / o["srt"]).is_file())
            self.assertTrue((core.OUT / o["thumb"]).is_file())
            self.assertEqual(o["kit"]["thumb"], o["thumb"], "키트는 그 결과물의 썸네일")
            self.assertTrue((core.OUT / o["kit"]["txt"]).is_file())
        self.assertFalse(any("올렸어요" in m and "않" not in m for m in self.logs))
        self.assertEqual(r["check"][-1]["kind"], "upload")

    def test_project_has_tagged_msg_sequences(self):
        proj = editor.load_project(self.w.name)
        tagged = [q for q in proj["sequences"] if q.get("autoRun") == self.res["id"]]
        self.assertEqual(sorted(q["autoKey"] for q in tagged), sorted(o["key"] for o in self.res["outputs"] if o.get("seq")))
        self.assertTrue(all(q.get("auto") == "msg" and q.get("msg") for q in tagged))
        shorts = [q for q in tagged if q["format"] == "shorts"]
        self.assertTrue(all(q["name"].startswith("자동 쇼츠") for q in shorts))
        long = next(q for q in tagged if q["format"] == "long")
        self.assertTrue(long["name"].startswith("자동 롱폼 · 예능 MSG형 · 보통"))
        self.assertTrue(all(m["id"] in {x["id"] for x in proj["media"]} for q in tagged for it in q["items"] for m in [{"id": it["media"]}]))

    def test_resume_after_crash_does_not_duplicate(self):
        st = autorun.load(self.w.name)
        st["status"] = "running"
        for k in ("export_long", "msg_shorts", "export_shorts", "thumbs", "kits", "check"):
            st["steps"][k] = {"state": "run" if k == "export_long" else "todo"}
        long = next(o for o in st["outputs"] if o["key"] == "long")
        long["seq"] = None  # 꺼지기 직전: MSG 를 프로젝트에 더했지만 기록에는 못 남김
        st["steps"]["msg_long"] = {"state": "run"}
        autorun.save(st)
        n_before = len(editor.load_project(self.w.name)["sequences"])
        with mock.patch.object(thumb, "cached_analysis", lambda n: {"frames": []}):
            r = autorun.run(self.w.name, autorun.options({}), lambda m: None, resume=True)
        self.assertEqual(r["id"], self.res["id"])
        self.assertEqual(r["status"], "done")
        self.assertEqual(len(editor.load_project(self.w.name)["sequences"]), n_before, "같은 MSG 편집본을 또 더하지 않음")
        self.assertEqual(next(o for o in r["outputs"] if o["key"] == "long")["seq"], next(o for o in self.res["outputs"] if o["key"] == "long")["seq"])


class MsgFallback(unittest.TestCase):
    """MSG 를 못 넣으면 가편집 그대로 내보내고 '확인할 것'에 남김."""

    def test_rough_used_when_msg_fails(self):
        from make_msg_fixture import MsgWork
        w = MsgWork()
        self.addCleanup(w.close)
        editor.CANCEL.clear()
        pg = FakePage()
        self.addCleanup(pg.close)
        with mock.patch.object(msg, "build_variants", side_effect=msg.MsgError("말소리를 거의 찾지 못했어요")), \
                mock.patch.object(thumb, "cached_analysis", lambda n: {"frames": []}):
            r = autorun.run(w.name, autorun.options({"preset": "small", "shorts": 1}), lambda m: None)
        outs = {o["key"]: o for o in r["outputs"]}
        self.assertFalse(outs["long"]["msg"])
        self.assertTrue((core.OUT / outs["long"]["export"]).is_file())
        proj = editor.load_project(w.name)
        self.assertEqual(next(q for q in proj["sequences"] if q["id"] == outs["long"]["seq"])["auto"], "rough")
        if "short1" in outs:
            q = next(q for q in proj["sequences"] if q["id"] == outs["short1"]["seq"])
            self.assertTrue(q["name"].endswith("(가편집)"))
            self.assertEqual(q["auto"], "user", "자동 쇼츠 가편집은 '가편집 다시 만들기'로 바뀌지 않음")
        steps = {s["key"]: s for s in r["steps"]}
        self.assertEqual(steps["msg_long"]["state"], "fail")
        self.assertTrue(any(c["kind"] == "msg" for c in r["check"]))


class MsgWindows(unittest.TestCase):
    def test_one_short_per_window_with_its_hook(self):
        from make_msg_fixture import MsgWork
        w = MsgWork()
        self.addCleanup(w.close)
        wins = [{"start": 8.0, "end": 31.0, "cuts": [{"in": 8.0, "out": 31.0}], "hook": "고개 들기 꿀팁"},
                {"start": 0.4, "end": 21.0, "cuts": [{"in": 0.4, "out": 21.0}], "hook": "첫 터치 비밀"}]
        res = msg.build_variants(w.name, [{"kind": "preset", "name": "예능 MSG형"}], "보통", ("shorts",), lambda m: None, windows=wins)
        self.assertEqual(sorted(q["msgWin"] for q in res["sequences"]), [0, 1])
        for q in res["sequences"]:
            hook = next(e for e in q["msg"]["events"] if e["kind"] == "hook")
            # 첫머리 '훅' 사건 = 큰 줄 (+ 그 위 주제 줄 · BR-120) — 큰 줄은 그 구간의 제목 (주제 줄은 tests.test_shorts_topic 이 봄)
            main = next(t for t in q["titles"] if t["id"] in hook["refs"]["titles"] and t.get("plan") != "topic")
            self.assertEqual(main["text"].replace("\n", " "), wins[q["msgWin"]]["hook"])
            self.assertEqual(q["msg"]["hook"], wins[q["msgWin"]]["hook"])
        self.assertNotEqual(*[editor.seq_total(q) for q in res["sequences"]])


class Routes(unittest.TestCase):
    """앱 경로: 시작(이름 검사·복사 중)·기록 읽기·썸네일 맡기·돌려주기 · /api/state 의 auto·autoThumb."""

    def setUp(self):
        import app
        self.app = app
        self.w = Work()
        self.addCleanup(self.w.close)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.w.tmp / "studio.log")]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        for _ in range(200):
            if not self.app.JOB["name"]:
                break
            time.sleep(0.05)
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.p2:
            p.stop()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_start_passes_options_and_resume(self):
        got = []
        with mock.patch.object(autorun, "run", lambda n, o, log, an, resume: got.append((n, o, resume, an)) or {"status": "done"}):
            code, j = self.call("/api/auto", {"name": self.w.name, "intensity": "담백", "shorts": 2, "resume": True, "model": "small", "writer": True})
            self.assertEqual(code, 200)
            self.assertTrue(j["jobId"])
            for _ in range(100):
                if got and not self.app.JOB["name"]:
                    break
                time.sleep(0.05)
        n, o, resume, an = got[0]
        self.assertEqual((n, o["intensity"], o["shorts"], o["model"], o["writer"], resume), (self.w.name, "담백", 2, "small", True, True))
        self.assertIs(an, self.app.Handler._analyze)
        self.assertEqual(self.call("/api/auto", {"name": "../x.mp4"})[0], 400)
        self.assertEqual(self.call("/api/auto", {"name": "없는.mp4"})[0], 404)
        self.assertEqual(autorun.load_prefs(), {"writer": True, "model": "small"}, "PC 화면이 시작하면 그 설정을 남김 (휴대폰이 씀)")
        code, j = self.call("/api/auto/prefs", {"proofread": True, "writer": "yes", "model": "large"})  # 편집실 MSG 체크
        self.assertEqual((code, j["prefs"]), (200, {"writer": True, "model": "small", "proofread": True}), "체크 두 가지만 · 참/거짓만")

    def test_page_gets_analysis_while_auto_job_holds_slot(self):
        """D-207: 누끼를 못 따 썸네일 분석 캐시가 비어도(cached_analysis None) 그리는 화면의 /api/thumb/analyze 가 409 를 기다리지 않고
        끝까지 자동으로가 막 한 분석을 받음 (예전: 그 작업이 작업 자리를 잡고 있어 결과물마다 5분 기다린 뒤 '너무 오래') ·
        누끼를 못 딴 까닭은 '확인할 것'에."""
        ana = {"frames": [{"t": 1.0, "url": "/frame?t=1"}], "cuts": {}, "copy": {"items": []},
               "cutFail": "자동 누끼를 따지 못했어요 · Visual C++ 런타임이 없어요"}
        st = autorun.new_state(self.w.name, autorun.options({"style": {"kind": "preset", "name": "담백 레슨형"}}))
        o = autorun._out(st, "long", kind="long")
        autorun._set_seq(o, "S1", "자동 롱폼", True)
        ctx = autorun._Ctx(st, lambda m: None, None)
        got = {}

        def page():
            for _ in range(400):
                if autorun.pending():
                    break
                time.sleep(0.05)
            rid = autorun.pending()["id"]
            got["claim"] = self.call("/api/auto/claim", {"id": rid})[0]
            got["analyze"] = self.call("/api/thumb/analyze", {"name": self.w.name})
            got["thumb"] = self.call("/api/auto/thumb", {"id": rid, "data": jpeg((1280, 720)), "score": 88})[0]

        def job():
            autorun.LIVE.update(name=self.w.name)
            try:
                return autorun._step_thumbs(ctx)
            finally:
                autorun.LIVE.clear()
        with mock.patch.object(thumb, "cached_analysis", return_value=None), mock.patch.object(thumb, "analyze", return_value=ana), \
                mock.patch.object(editor, "load_project", return_value={"sequences": []}), mock.patch.object(autorun, "DRAW_WAIT", 20):
            th = threading.Thread(target=page)
            th.start()
            self.assertTrue(self.app.start_job(autorun.JOB, job))
            th.join(30)
            for _ in range(200):
                if not self.app.JOB["name"]:
                    break
                time.sleep(0.05)
        code, j = got["analyze"]
        self.assertEqual((got["claim"], code, got["thumb"]), (200, 200, 200), j)
        self.assertEqual(j["frames"], ana["frames"])
        self.assertTrue(o["thumb"])
        self.assertIn("Visual C++", st["thumbCut"])
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            items = autorun.checklist(dict(st, steps={"rough": {"state": "done"}, "thumbs": {"state": "done"}}))
        self.assertTrue(any("누끼를 따지 못해" in c["text"] and "Visual C++" in c["text"] for c in items), items)
        self.assertIsNone(autorun.analysis_for(self.w.name), "작업이 끝나면 내놓지 않음")

    def test_state_and_thumb_routes(self):
        code, j = self.call("/api/auto?name=" + urllib.request.quote(self.w.name))
        self.assertEqual((code, j["run"]), (200, None))
        self.assertIn(j["style"]["kind"], ("preset", "style", "mix"))
        out = {}
        t = threading.Thread(target=lambda: out.update(r=autorun.request_thumb(self.w.name, "long", "롱폼_썸네일")))
        t.start()
        for _ in range(100):
            s = self.call("/api/state?since=0")[1]
            if s.get("autoThumb"):
                break
            time.sleep(0.05)
        rid = s["autoThumb"]["id"]
        self.assertEqual(self.call("/api/auto/pending")[1]["want"], {"id": rid})
        code, c = self.call("/api/auto/claim", {"id": rid})
        self.assertEqual((code, c["fmt"], c["name"]), (200, "long", self.w.name))
        self.assertEqual(self.call("/api/auto/claim", {"id": rid})[0], 409)
        self.assertEqual(self.call("/api/auto/release", {"id": rid})[1]["ok"], True, "맡은 화면이 떠남 → 다른 화면이 다시 맡음 (D-207)")
        self.assertEqual(self.call("/api/auto/pending")[1]["want"], {"id": rid})
        self.assertEqual(self.call("/api/auto/claim", {"id": rid})[0], 200)
        self.assertEqual(self.call("/api/auto/release", {"id": "다른 요청"})[1]["ok"], False)
        code, j = self.call("/api/auto/thumb", {"id": rid, "data": jpeg((1280, 720)), "score": 90, "label": "../../나쁜", "file": "../../나쁜.jpg"})
        self.assertEqual(code, 200)
        t.join(5)
        self.assertEqual(out["r"]["file"], "레슨 영상_롱폼_썸네일_1.jpg", "파일 이름은 서버가 정함")
        self.assertEqual(self.call("/api/auto/thumb", {"id": rid, "data": jpeg((1280, 720))})[0], 410)
        # 이상한 그림(400)은 작업에도 그 까닭이 바로 감 (D-207)
        out.clear()
        t = threading.Thread(target=lambda: out.update(e=Bridge.catch(None, lambda: autorun.request_thumb(self.w.name, "long", "롱폼_썸네일"))))
        t.start()
        for _ in range(100):
            if autorun.pending():
                break
            time.sleep(0.05)
        rid = autorun.pending()["id"]
        self.assertEqual(self.call("/api/auto/claim", {"id": rid})[0], 200)
        self.assertEqual(self.call("/api/auto/thumb", {"id": rid, "data": "data:image/svg+xml;base64,AAAA"})[0], 400)
        t.join(5)
        self.assertIn("형식이 다르거나", str(out["e"]))


class ReviewFixes(unittest.TestCase):
    """D-207 (E7 재검토): 이어서 하기 · 검수 · 직접 만든 썸네일 · 결과 카드."""

    def setUp(self):
        self.w = Work()
        self.addCleanup(self.w.close)
        editor.CANCEL.clear()
        self.addCleanup(editor.CANCEL.clear)

    def state(self):
        return autorun.new_state(self.w.name, autorun.options({"style": {"kind": "preset", "name": "담백 레슨형"}}))

    def test_resume_retries_msg_for_rough_short(self):
        """MSG 를 못 넣어 가편집으로 둔 쇼츠도 [못 한 것 다시 하기]가 MSG 를 다시 넣음 (예전: '이미 만들었어요' · 롱폼만 다시) ·
        또 안 되면 있던 가편집 그대로 (가편집을 또 더하지 않음) · MSG 편집본이 생기면 그것을 그 결과물로."""
        st = self.state()
        pt = {"start": 1.0, "end": 20.0, "hook": "디딤발 꿀팁", "hookCheck": False, "title": "", "keywords": [], "cuts": [{"in": 1.0, "out": 20.0}]}
        st["points"] = [pt]
        rough = {"id": "R1", "name": "자동 쇼츠 1 · 디딤발 꿀팁 (가편집)", "auto": "user", "autoRun": st["id"], "autoKey": "short1", "format": "shorts"}
        proj = {"rev": 3, "sequences": [rough], "media": []}
        o = autorun._out(st, "short1", kind="shorts", point=0, hook="디딤발 꿀팁", src=[1.0, 20.0])
        autorun._set_seq(o, "R1", rough["name"], False)
        o.update(export="r1.mp4", thumb="r1.jpg", kit={"txt": "r1.txt"}, msgFail="클로드 응답이 늦어요")
        c = autorun._Ctx(st, lambda m: None, None)
        added = []
        no_new_rough = mock.patch.object(autorun, "_rough_short", side_effect=AssertionError("가편집을 또 만들면 안 됨"))
        # 또 안 됨 → 있던 가편집 그대로
        with mock.patch.object(editor, "load_project", return_value=proj), no_new_rough, \
                mock.patch.object(msg, "build_variants", side_effect=msg.MsgError("클로드 응답이 늦어요")), \
                mock.patch.object(autorun, "add_to_project", lambda n, seqs, *a, **k: added.extend(seqs) or seqs):
            state, note = autorun._step_msg_shorts(c)
        self.assertEqual((state, o["seq"], o["msg"], o["export"], added), ("fail", "R1", False, "r1.mp4", []))
        self.assertIn("쇼츠 1개는 MSG 를 넣지 못해", note)
        # 이번엔 됨 → MSG 편집본으로 · 가편집으로 만든 완성본·썸네일·키트 기록은 버림 (다시 만들게)
        calls = []

        def bv(name, specs, lv, kinds, **kw):
            calls.append(kw.get("windows"))
            return {"sequences": [{"id": "M1", "name": "x", "msg": {"summary": {}}, "msgWin": 0, "format": "shorts"}], "media": []}
        with mock.patch.object(editor, "load_project", return_value=proj), mock.patch.object(msg, "build_variants", bv), \
                mock.patch.object(autorun, "add_to_project", lambda n, seqs, *a, **k: added.extend(seqs) or seqs):
            state, note = autorun._step_msg_shorts(c)
        self.assertEqual(state, "done")
        self.assertEqual(calls, [[pt]])
        self.assertEqual((o["seq"], o["msg"], o.get("export"), o.get("thumb"), o.get("kit"), o.get("msgFail")), ("M1", True, None, None, None, None))
        proj["sequences"].append(dict(added[-1], autoRun=st["id"], autoKey="short1"))
        self.assertEqual(autorun._tagged(proj, st["id"], "short1")["id"], "M1", "가편집과 MSG 편집본이 함께 있으면 MSG 것")

    def test_stop_during_qa_runs_qa_on_resume(self):
        """검수 중에 멈추면 이어서 할 때 검수만 다시 (예전: 내보낸 파일이 있다고 검수를 영영 건너뜀 · 확인할 것에 소리 문제가 안 뜸) ·
        내보낸 파일은 검수 전에 기록 (검수 중에 꺼져도 다시 내보내지 않음)."""
        import qa
        st = self.state()
        o = autorun._out(st, "long", kind="long")
        autorun._set_seq(o, "S1", "자동 롱폼", True)
        c = autorun._Ctx(st, lambda m: None, None)
        exports, runs = [], []

        def exp(name, seq, preset, log):
            f = f"레슨 영상_자동 롱폼_{len(exports) + 1}.mp4"
            (core.OUT / f).write_bytes(b"x")
            exports.append(f)
            return [f]

        def check(path, fmt, master, run):
            runs.append(fmt)
            if len(runs) == 1:
                editor.CANCEL.set()
                raise RuntimeError("멈췄어요")
            return {"score": 70, "items": [{"lv": "warn", "title": "소리가 커요", "msg": "-8 LUFS", "t": None}, {"lv": "ok", "title": "화면", "msg": ""}]}
        with mock.patch.object(editor, "export_saved", exp), mock.patch.object(qa, "check_video", check):
            with self.assertRaises(autorun.Stopped):
                autorun._export_one(c, o)
            saved = autorun.load(self.w.name)["outputs"][0]
            self.assertEqual((saved["export"], saved["qaDone"]), (exports[0], False), "검수 전에 내보낸 파일을 기록")
            editor.CANCEL.clear()
            autorun._export_one(c, o)
            autorun._export_one(c, o)
        self.assertEqual(len(exports), 1, "다시 내보내지 않음")
        self.assertEqual(runs, ["long", "long"], "검수만 다시 (한 번)")
        self.assertEqual((o["qaDone"], o["qaScore"], [q["title"] for q in o["qa"]]), (True, 70, ["소리가 커요"]))
        self.assertTrue(autorun._qa_done({"qa": [], "qaScore": 90}), "예전 기록: 점수가 있으면 마친 것")
        self.assertFalse(autorun._qa_done({"qa": [], "qaScore": None}))

    def test_owner_thumbnail_kept_and_shown(self):
        """결과 카드 [썸네일]로 사람이 그 결과물 이름으로 저장한 썸네일: 결과 카드에 '썸네일 ✓'(그 결과물의 '썸네일이 없어요' 줄도 거둠) ·
        [못 한 것 다시 하기]는 그것을 쓰고 새로 그려 덮지 않음 (예전: AI 썸네일 _2 가 더 최근이라 키트가 바꿔 씀)."""
        st = self.state()
        st["status"] = "done"
        st["steps"] = {k: {"state": "done"} for k in ("analyze", "rough", "msg_long", "export_long", "msg_shorts", "export_shorts", "kits", "check")}
        st["steps"]["thumbs"] = {"state": "fail", "note": autorun.NO_PAGE}
        for key, kind, nm in (("long", "long", "자동 롱폼 · 담백 레슨형 · 보통"), ("short1", "shorts", "자동 쇼츠 1 · 디딤발 꿀팁")):
            o = autorun._out(st, key, kind=kind)
            autorun._set_seq(o, key.upper(), nm, True)
            o.update(thumbFail=autorun.NO_PAGE, src=[1.0, 20.0] if kind == "shorts" else None)
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            st["check"] = autorun.checklist(st)
        autorun.save(st)
        v = autorun.view(self.w.name)
        lab = v["outputs"][0]["thumbLabel"]
        own = thumb.export_image(self.w.name, jpeg((1280, 720)), "jpg", lab)
        v = autorun.view(self.w.name)
        outs = {o["key"]: o for o in v["outputs"]}
        self.assertEqual((outs["long"]["thumb"], outs["long"]["thumbOwn"], outs["short1"]["thumb"]), (own.name, True, None))
        self.assertTrue(any(autorun.NO_PAGE in c["text"] for c in v["check"]), "쇼츠는 아직 없음 → 그 줄은 남김")
        self.assertTrue(v["redo"])
        pg = FakePage(score=80)
        try:
            with mock.patch.object(thumb, "cached_analysis", lambda n: {"frames": []}), \
                    mock.patch.object(editor, "load_project", return_value={"sequences": []}), \
                    mock.patch.object(autorun, "caption_checks", return_value=[]):
                r = autorun.run(self.w.name, autorun.options({}), lambda m: None, resume=True)
        finally:
            pg.close()
        outs = {o["key"]: o for o in r["outputs"]}
        self.assertEqual(pg.n, 1, "쇼츠만 그림")
        self.assertEqual((outs["long"]["thumb"], outs["long"]["thumbInfo"]), (own.name, {"own": True}))
        self.assertTrue(outs["short1"]["thumb"].startswith("레슨 영상_자동 쇼츠 1 · 디딤발 꿀팁_썸네일_"))
        self.assertEqual(upload.thumbnail_check(self.w.name, "long", {"name": "자동 롱폼 · 담백 레슨형 · 보통", "format": "long"})["file"], own.name)
        self.assertFalse(any(c["kind"] == "thumb" for c in r["check"]), r["check"])


class ResumeKeepsHumanWork(unittest.TestCase):
    """D-207: 실제 MSG 시험 영상 — [못 한 것 다시 하기]가 사람이 고친 키트·직접 만든 썸네일을 덮지 않고, 가편집으로 둔 쇼츠에도 MSG 를 다시 넣음."""

    def test_resume_keeps_edits_and_retries_msg(self):
        from make_msg_fixture import MsgWork
        w = MsgWork()
        self.addCleanup(w.close)
        editor.CANCEL.clear()
        name = w.name
        proj = editor.load_project(name)
        rough = next(q for q in proj["sequences"] if q.get("format") == "long" and q.get("auto") in ("rough", "style"))
        upload.build_kit(name, rough["id"])  # 끝까지 자동으로를 쓰기 전에 코치가 자기 가편집 키트를 고쳐 둠
        upload.save_edits(name, rough["id"], title="코치가 미리 고친 롱폼 제목", desc="코치가 쓴 설명")
        opts = autorun.options({"preset": "small", "shorts": 1, "style": {"kind": "preset", "name": "예능 MSG형"}})

        def nomsg():
            return mock.patch.object(msg, "build_variants", side_effect=msg.MsgError("클로드 응답이 늦어요"))

        def cached():
            return mock.patch.object(thumb, "cached_analysis", lambda n: {"frames": []})
        # 1) MSG 가 잠깐 안 되고(롱폼은 코치의 가편집 · 쇼츠는 가편집) 썸네일 그릴 화면도 없음
        with nomsg(), cached(), mock.patch.object(autorun, "PAGE_WAIT", 0.5), mock.patch.object(autorun, "PAGE_NOTE", 0.2):
            r1 = autorun.run(name, opts, lambda m: None)
        steps = {s["key"]: s["state"] for s in r1["steps"]}
        self.assertEqual((r1["status"], steps["msg_long"], steps["msg_shorts"], steps["thumbs"], steps["kits"]), ("done", "fail", "fail", "fail", "done"))
        outs = {o["key"]: o for o in r1["outputs"]}
        self.assertEqual(outs["long"]["seq"], rough["id"])
        self.assertIn("short1", outs)
        self.assertEqual(upload.load_kit(name, rough["id"])["title"], "코치가 미리 고친 롱폼 제목", "자동이 만들지 않은 가편집의 고친 키트는 그대로")
        self.assertTrue(outs["long"]["kit"]["edited"])
        self.assertEqual(sum(autorun.NO_PAGE in c["text"] for c in r1["check"]), 1, "그릴 화면이 없었다는 안내는 한 줄")
        # 2) 코치가 7 올리기에서 쇼츠 키트를 고치고, 결과 카드 [썸네일]로 롱폼 썸네일을 직접 저장
        s1 = outs["short1"]["seq"]
        upload.save_edits(name, s1, title="코치가 고친 쇼츠 제목", tags=["코치태그"])
        own = thumb.export_image(name, jpeg((1280, 720)), "jpg", outs["long"]["thumbLabel"])
        self.assertEqual(next(o for o in autorun.view(name)["outputs"] if o["key"] == "long")["thumb"], own.name)
        # 3) [못 한 것 다시 하기] — MSG 는 아직 안 되고 화면은 있음
        page = FakePage(score=84)
        try:
            with nomsg(), cached():
                r2 = autorun.run(name, autorun.options({}), lambda m: None, resume=True)
        finally:
            page.close()
        outs2 = {o["key"]: o for o in r2["outputs"]}
        self.assertEqual(page.n, 1, "직접 만든 롱폼 썸네일은 다시 그리지 않음 (쇼츠 하나만)")
        self.assertEqual(outs2["long"]["thumb"], own.name)
        k = upload.load_kit(name, rough["id"])
        self.assertEqual((k["title"], k["thumbnail"]["file"]), ("코치가 미리 고친 롱폼 제목", own.name))
        k = upload.load_kit(name, s1)
        self.assertEqual((k["title"], k["tags"], bool(k["edited"])), ("코치가 고친 쇼츠 제목", ["코치태그"], True), "고친 키트를 새로 만든 내용으로 덮지 않음")
        self.assertEqual(json.loads((core.OUT / k["files"]["json"]).read_text(encoding="utf-8"))["thumbnail"]["file"], outs2["short1"]["thumb"],
                         "고친 키트도 썸네일 칸은 새 썸네일로")
        self.assertEqual(sum(1 for q in editor.load_project(name)["sequences"] if q.get("autoKey") == "short1"), 1, "가편집 쇼츠를 또 더하지 않음")
        # 4) MSG 가 되면 [못 한 것 다시 하기]가 롱폼·쇼츠 모두 MSG 로 (예전: 쇼츠는 '이미 만들었어요'로 가편집 그대로)
        calls, real = [], msg.build_variants

        def spy(*a, **kw):
            calls.append("shorts" if kw.get("windows") else "long")
            return real(*a, **kw)
        page = FakePage(score=84)
        try:
            with mock.patch.object(msg, "build_variants", spy), cached():
                r3 = autorun.run(name, autorun.options({}), lambda m: None, resume=True)
        finally:
            page.close()
        self.assertEqual(sorted(calls), ["long", "shorts"])
        outs3 = {o["key"]: o for o in r3["outputs"]}
        self.assertEqual((outs3["long"]["msg"], outs3["short1"]["msg"]), (True, True))
        self.assertNotEqual(outs3["short1"]["seq"], s1)
        self.assertEqual({s["key"]: s["state"] for s in r3["steps"]}["msg_shorts"], "done")
        self.assertTrue((core.OUT / outs3["short1"]["export"]).is_file())
        self.assertFalse(r3["redo"])
        self.assertEqual(upload.load_kit(name, s1)["title"], "코치가 고친 쇼츠 제목", "가편집 쇼츠의 고친 키트는 그대로 남음")
        self.assertEqual(upload.load_kit(name, rough["id"])["title"], "코치가 미리 고친 롱폼 제목")


class EditorOpenDuringRun(unittest.TestCase):
    """D-207: 작업하는 동안 열어 둔 편집실 창이 저장해도 끝까지 자동으로가 더한 편집본이 지워지지 않음 (충돌 창 없이 합침)."""

    def setUp(self):
        from make_msg_fixture import MsgWork
        self.w = MsgWork()
        self.addCleanup(self.w.close)

    def disk(self):
        return json.loads(editor._ppath(self.w.name).read_text(encoding="utf-8"))

    def test_window_save_merges_auto_sequences(self):
        name = self.w.name
        win = json.loads(json.dumps(editor.load_project(name)))  # 편집실이 연 내용
        base = win["rev"]
        seq = dict(json.loads(json.dumps(win["sequences"][0])), id="AUTO1", name="자동 롱폼 · 시험", autoRun="r1", autoKey="long", auto="msg")
        media = [{"id": "sfx_dingdong", "kind": "audio", "src": "assets", "file": "효과음_딩동.wav", "dur": 0.5}]
        new_caps = [dict(c, text=c["text"] + " (다시 들음)") for c in win["captions"]]
        self.assertTrue(new_caps)
        autorun.add_to_project(name, [seq], media, new_caps, win["captions"])
        # 창: 작업 중 편집본 이름을 고쳐 자동 저장 (예전: 409 → '이 창 내용으로 저장'이 자동 편집본을 지움)
        win["sequences"][0]["name"] = "코치가 고친 이름"
        merged = {}
        rev = editor.save_project(name, win, base, False, "창1", 1, merged=merged)
        d = self.disk()
        self.assertIn("AUTO1", [q["id"] for q in d["sequences"]])
        self.assertEqual(d["sequences"][0]["name"], "코치가 고친 이름")
        self.assertIn("sfx_dingdong", [m["id"] for m in d["media"]])
        self.assertEqual(d["captions"], new_caps, "창이 자막을 안 고쳤으면 다시 들은 자막으로")
        self.assertEqual(([q["id"] for q in merged["sequences"]], [m["id"] for m in merged["media"]], merged["captions"]),
                         (["AUTO1"], ["sfx_dingdong"], new_caps), "창에도 알려 줌")
        # 그 뒤 창에서 자동 편집본을 지우면 지워진 그대로 (다시 붙이지 않음)
        win2 = json.loads(json.dumps(d))
        win2["sequences"] = [q for q in win2["sequences"] if q["id"] != "AUTO1"]
        rev = editor.save_project(name, win2, rev, False, "창1", 2)
        self.assertNotIn("AUTO1", [q["id"] for q in self.disk()["sequences"]])
        # 사람(다른 창)도 그 사이 저장했으면 예전처럼 충돌 — 그래도 [이 창 내용으로 저장]은 자동 편집본을 남김 · 고친 자막은 그대로
        stale = json.loads(json.dumps(self.disk()))
        stale["captions"] = [dict(c, text="창이 고친 자막") for c in stale["captions"]]
        autorun.add_to_project(name, [dict(seq, id="AUTO2", autoKey="short1")], [], [dict(c, text="또 다시 들음") for c in new_caps], new_caps)
        other = json.loads(json.dumps(self.disk()))
        other["sequences"][0]["name"] = "다른 창"
        editor.save_project(name, other, other["rev"], False, "창2", 1)
        with self.assertRaises(editor.Conflict):
            editor.save_project(name, stale, rev, False, "창1", 3)
        merged = {}
        editor.save_project(name, stale, rev, True, "창1", 4, merged=merged)
        d = self.disk()
        self.assertIn("AUTO2", [q["id"] for q in d["sequences"]])
        self.assertEqual(d["captions"][0]["text"], "창이 고친 자막", "창이 고친 자막은 그대로")
        self.assertNotIn("captions", merged)

    def test_browser_cannot_claim_auto_writer(self):
        """화면(HTTP)이 보낸 '자동' 저장 표시는 받지 않음 (작업만 씀) · 저장 응답에 합친 편집본(autoAdded)."""
        import app
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        p = mock.patch.object(app, "PORT", port)
        p.start()
        self.addCleanup(p.stop)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        def save(body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/edit/save", data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        name = self.w.name
        win = json.loads(json.dumps(editor.load_project(name)))
        base = win["rev"]
        code, j = save({"name": name, "project": dict(win, sequences=win["sequences"] + [dict(win["sequences"][0], id="FAKE")]), "rev": base,
                        "client": editor.AUTO_CLIENT, "seq": 1})
        self.assertEqual(code, 200)
        self.assertFalse(editor._AUTO_ADDS.get(str(editor._ppath(name))), "화면 저장은 자동 작업으로 치지 않음")
        win = self.disk()
        autorun.add_to_project(name, [dict(win["sequences"][0], id="AUTO9", autoRun="r", autoKey="long")])
        win["sequences"][0]["name"] = "고침"
        code, j = save({"name": name, "project": win, "rev": win["rev"], "client": "창9", "seq": 1})
        self.assertEqual(code, 200, j)
        self.assertEqual([q["id"] for q in j["autoAdded"]["sequences"]], ["AUTO9"])


from test_windows_compat import Server  # noqa: E402


class RenamedThenResumed(Server):
    """D-207: 이름을 바꾼 뒤 [이어서 하기]는 새 이름으로 (예전: 옛 이름으로 단계를 돌려 '영상을 찾지 못했어요' · 옛 분석 폴더를 다시 만듦 ·
    새 이름의 기록은 영영 '멈춤')."""

    def test_resume_after_rename(self):
        old, new = "레슨 원본.mp4", "패스 레슨.mp4"
        self.video(old, b"\1" * 3000)
        d = core.adir(old)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps([{"start": 0, "end": 1, "text": "패스"}], ensure_ascii=False), encoding="utf-8")
        st = autorun.new_state(old, autorun.options({"style": {"kind": "preset", "name": "담백 레슨형"}}))
        st["status"] = "stopped"
        st["steps"] = {k: {"state": "done"} for k, _ in autorun.STEPS[:-2]}
        st["steps"]["kits"] = {"state": "todo", "note": "멈춤"}
        autorun.save(st)
        code, j = self.call("/api/rename", {"name": old, "to": "패스 레슨"})
        self.assertEqual((code, j.get("name")), (200, new), j)
        v = autorun.view(new)
        self.assertEqual((v["name"], v["resumable"]), (new, True))
        seen = []
        fns = {k: (lambda c, k=k: seen.append((k, c.name)) or ("done", "")) for k, _ in autorun.STEPS if k != "check"}
        with mock.patch.dict(autorun.STEP_FN, fns), mock.patch.object(autorun, "caption_checks", return_value=[]):
            r = autorun.run(new, autorun.options({}), lambda m: None, resume=True)
        self.assertEqual((r["id"], r["name"], r["status"]), (st["id"], new, "done"))
        self.assertEqual(seen, [("kits", new)])
        self.assertFalse(core.adir(old).exists(), "옛 이름 분석 폴더를 다시 만들지 않음")
        self.assertEqual(autorun.load(new)["status"], "done")
        self.assertFalse(autorun.view(new)["resumable"])


if __name__ == "__main__":
    unittest.main()
