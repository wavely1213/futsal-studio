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
            with self.assertRaises(ValueError):
                autorun.deliver(rid, {"data": "data:text/html;base64,AAAA"})
            t.join(5)
        self.assertIn("너무 오래", str(out["e"]))
        with self.assertRaises(LookupError):
            autorun.deliver(rid, {"data": jpeg((1280, 720))})
        self.assertEqual(list(core.OUT.glob("*.jpg")), [])

    def catch(self, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            return e

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
                 "thumbInfo": {"score": 90, "weak": True}, "kit": {"txt": "x"}} for k in ("short1", "short2", "short3")]
        with mock.patch.object(autorun, "caption_checks", return_value=[]):
            items = autorun.checklist(self.st(outs))
        th = [c for c in items if c["kind"] == "thumb"]
        self.assertEqual(len(th), 1, "약한 장면은 영상 탓이라 한 줄")
        self.assertIn("결과물 3개 모두", th[0]["text"])

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
        self.assertEqual(upload.thumbnail_check(self.w.name, "shorts", {"name": "다른 쇼츠"})["file"], f"{pre}_자동 쇼츠 2 · B_썸네일_1.jpg")

    def test_auto_sequences_takes_given_recommendation(self):
        r = {"start": 1.0, "end": 25.0, "cuts": [{"in": 1.0, "out": 12.0}, {"in": 14.0, "out": 25.0}], "hook": "디딤발 꿀팁", "title": "디딤발", "keywords": ["디딤발"]}
        info = {"duration": 60.0, "width": 1920, "height": 1080, "fps": 30.0}
        with mock.patch.object(editor, "recommend", side_effect=AssertionError("추천을 다시 하면 안 됨")), \
                mock.patch.object(editor, "_segments_of", return_value=[]), mock.patch.object(editor, "silences_of", return_value=[]):
            seqs = editor.auto_sequences(self.w.name, info, None, ("shorts",), rec={"shorts": [r], "tidy": []})
        self.assertEqual(len(seqs), 1)
        self.assertEqual(seqs[0]["titles"][0]["text"], "디딤발 꿀팁")
        self.assertAlmostEqual(editor.seq_total(seqs[0]), 22.0, delta=0.5)

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
            self.assertEqual(hook["text"].replace("\n", " "), wins[q["msgWin"]]["hook"])
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
        self.assertEqual(self.call("/api/auto/thumb", {"id": rid, "data": "data:image/svg+xml;base64,AAAA"})[0], 400)
        code, j = self.call("/api/auto/thumb", {"id": rid, "data": jpeg((1280, 720)), "score": 90, "label": "../../나쁜", "file": "../../나쁜.jpg"})
        self.assertEqual(code, 200)
        t.join(5)
        self.assertEqual(out["r"]["file"], "레슨 영상_롱폼_썸네일_1.jpg", "파일 이름은 서버가 정함")
        self.assertEqual(self.call("/api/auto/thumb", {"id": rid, "data": jpeg((1280, 720))})[0], 410)


if __name__ == "__main__":
    unittest.main()
