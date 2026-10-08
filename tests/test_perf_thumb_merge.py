"""성능 묶음 × v2.9.0(AI 추천 썸네일) 합침 (D-069) — python3 -m unittest tests.test_perf_thumb_merge

- 썸네일 분석의 자동 누끼는 따로 프로세스(cutout_worker.cut_auto → 자식이 thumb.cut_auto): 앱 프로세스는 누끼 모델을 안 부름 ·
  이미 딴 장면은 자식을 안 띄움 · 실패하면 앞에서 딴 누끼 + 쉬운 한 줄(cutFail) · ✕ 면 작업도 멈춤 · 실제 자식 프로세스의 실패 글
- 손 누끼(/api/thumb/cut 장면 주소): main 의 검사 뒤 작업 안은 cutout_worker.remove_bg · 끝내 실패하면 실패 카드가 쉬운 한 줄
- 쉬는 동안 내려놓기: 선수·공 찾기(detect)도 비움 · 썸네일 작업 넷은 '작업 중'이라 그동안 얼굴·선수 모델을 안 지움 · 검수 OCR 은 using
모델·인터넷 없이 돈다."""
import base64
import io
import json
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
import core  # noqa: E402
import cutout_worker  # noqa: E402
import idle  # noqa: E402
import thumb  # noqa: E402
import trouble  # noqa: E402
import worker  # noqa: E402
from tests.test_idle import _end_loop  # noqa: E402

NAME = "20261008_ABCDEFGHIJK_발바닥 드래그 기본기.mp4"


def _no_model(*a, **k):
    raise AssertionError("앱 프로세스에서 누끼 모델을 불렀어요")


def _died_oom():
    """메모리 부족으로 죽은 자식 → cutout_worker 가 바꿔 올리는 오류 (자동 누끼 꼴)."""
    e = worker.WorkerError("작업 프로세스가 끝까지 못 했어요 (종료 코드 -9)", -9, "died")
    return worker.WorkerError(cutout_worker.fail_msg(e, cutout_worker.AUTO_FAIL_MSG), -9, "died")


class WorkBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="풋살 합침 시험 "))
        w = self.work = self.tmp / "작업 폴더"
        self.patches = [mock.patch.object(core, "WORK", w), mock.patch.object(core, "VIDEOS", w / "videos"),
                        mock.patch.object(core, "ANALYSIS", w / "analysis"), mock.patch.object(core, "OUT", w / "out"),
                        mock.patch.object(thumb, "THUMBS", w / "thumbnails"), mock.patch.object(thumb, "ASSETS", w / "thumbnails" / "assets"),
                        mock.patch.object(thumb, "MODELS", self.tmp / "models"), mock.patch("updater.download", side_effect=OSError("인터넷 막음"))]
        for p in self.patches:
            p.start()
        for d in ("videos", "analysis", "out", "thumbnails/assets"):
            (w / d).mkdir(parents=True, exist_ok=True)
        (w / "videos" / NAME).write_bytes(b"0")
        a = core.adir(NAME)
        a.mkdir(parents=True, exist_ok=True)
        (a / "transcript.json").write_text(json.dumps([{"start": 0, "end": 2, "text": "발바닥 드래그로 수비를 속이세요"}], ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class AutoCutTests(WorkBase):
    """thumb.analyze 의 주인공 자동 누끼 → cutout_worker.cut_auto (따로 프로세스)."""

    def setUp(self):
        super().setUp()
        base = {"url": "/frame?x", "persons": [[0.4, 0.2, 0.2, 0.6, 0.9]], "main": 0, "blur": 0.1, "flags": [], "score": 5}
        self.items = [dict(base, t=3.0, hash="0000000000000000", tboxes=[[0.1, 0.1, 0.3, 0.1]]),
                      dict(base, t=9.0, hash="ffffffffffffffff", faces=[{"box": [0.45, 0.22, 0.1, 0.15]}])]
        for p in (mock.patch.object(thumb, "frame_candidates", return_value=self.items), mock.patch.object(thumb, "cached_candidates", return_value=self.items),
                  mock.patch.object(thumb, "_start_ai_copy", return_value=None), mock.patch.object(thumb, "_ai_on", return_value=False),
                  mock.patch.object(thumb, "_bg_mask", side_effect=_no_model), mock.patch.object(thumb, "remove_bg", side_effect=_no_model),
                  mock.patch.object(thumb, "cut_auto", side_effect=_no_model)):
            p.start()
            self.addCleanup(p.stop)
        self.calls = []

    def write_cut(self, t, box):
        p = thumb._cut_path(NAME, t, box)
        p.write_bytes(b"\x89PNG")
        q = {"ok": True, "q": 0.9}
        p.with_suffix(".json").write_text(json.dumps(q), encoding="utf-8")
        return p, q

    def fake_worker(self, fail_after=None, exc=None):
        def f(name, jobs, **kw):
            self.calls.append((name, [list(j) for j in jobs], kw))
            out = []
            for i, (t, box, faces, tboxes) in enumerate(jobs):
                if fail_after is not None and i >= fail_after:
                    raise exc
                p, q = self.write_cut(t, box)
                out.append((t, p, q))
            return out
        return f

    def test_analyze_cuts_in_worker_not_app(self):
        import editor
        with mock.patch.object(cutout_worker, "cut_auto", side_effect=self.fake_worker()):
            r = thumb.analyze(NAME, log=lambda m: None)
        self.assertEqual(len(self.calls), 1, "장면 여러 장을 자식 하나에서")
        name, jobs, kw = self.calls[0]
        self.assertEqual(name, NAME)
        box = self.items[0]["persons"][0][:4]
        self.assertEqual(jobs, [[3.0, box, None, [[0.1, 0.1, 0.3, 0.1]]], [9.0, box, [{"box": [0.45, 0.22, 0.1, 0.15]}], None]])
        self.assertIs(kw["cancel"], editor.CANCEL)
        self.assertIs(kw["procs"], editor._PROCS)
        self.assertEqual(sorted(r["cuts"]), ["3.0", "9.0"])
        self.assertEqual(r["cuts"]["9.0"]["q"]["q"], 0.9)
        self.assertNotIn("cutFail", r)
        self.assertIsNotNone(thumb.cached_analysis(NAME), "다 땄으면 다음엔 분석 작업 없이 바로")

    def test_cached_cuts_skip_worker(self):
        box = self.items[0]["persons"][0][:4]
        self.write_cut(3.0, box)
        with mock.patch.object(cutout_worker, "cut_auto", side_effect=self.fake_worker()):
            r = thumb.analyze(NAME, log=lambda m: None)
            self.assertEqual([j[0] for j in self.calls[0][1]], [9.0], "딴 장면은 다시 안 땀")
            self.assertEqual(sorted(r["cuts"]), ["3.0", "9.0"])
            self.calls.clear()
            r = thumb.analyze(NAME, log=lambda m: None)
        self.assertEqual(self.calls, [], "다 있으면 자식을 띄우지 않음")
        self.assertEqual(sorted(r["cuts"]), ["3.0", "9.0"])

    def test_failure_keeps_earlier_cuts_and_plain_line(self):
        logs = []
        with mock.patch.object(cutout_worker, "cut_auto", side_effect=self.fake_worker(1, _died_oom())), \
                mock.patch.object(thumb.studiolog, "trace") as tr:
            r = thumb.analyze(NAME, log=logs.append)
        self.assertEqual(sorted(r["cuts"]), ["3.0"], "실패 앞에서 딴 누끼는 씀 · 분석은 계속")
        line = r["cutFail"]
        self.assertTrue(line.startswith(cutout_worker.AUTO_FAIL_MSG), line)
        self.assertIn("메모리가 부족해요", line)
        for bad in ("작업 프로세스", "종료 코드", "MemoryError", "died"):
            self.assertNotIn(bad, line)
        self.assertNotIn(cutout_worker.AUTO_FAIL_MSG + " · " + cutout_worker.AUTO_FAIL_MSG, line, "같은 말 두 번 없음")
        self.assertIn("  " + line, logs)
        self.assertEqual(tr.call_args[0][1], "자동 누끼 오류 위치")
        self.assertIsNone(thumb.cached_analysis(NAME), "못 딴 장면이 있으면 다음에 다시 분석")

    def test_quality_file_not_written_still_used(self):
        """자식이 PNG 는 썼는데 품질 JSON 쓰기만 실패(Windows 잠금·백신 — thumb.cut_auto 가 삼킴) → 돌려받은 품질로 이번 분석엔 씀 (예전 앱 안 분석과 같음).
        다음 분석은 JSON 이 없어 다시 (cached_analysis 는 None — 예전과 같음). 다른 곳의 경로(다른 작업 폴더 등)는 쓰지 않음."""
        box = self.items[0]["persons"][0][:4]

        def f(name, jobs, **kw):
            out = []
            for t, b, faces, tboxes in jobs:
                p = thumb._cut_path(NAME, t, b)
                p.write_bytes(b"\x89PNG")  # JSON 은 못 씀
                out.append((t, p if t == 3.0 else self.tmp / "다른 작업 폴더" / p.name, {"ok": True, "q": t / 10}))
            return out
        with mock.patch.object(cutout_worker, "cut_auto", side_effect=f):
            r = thumb.analyze(NAME, log=lambda m: None)
        self.assertEqual(sorted(r["cuts"]), ["3.0"], "돌려받은 그 장면의 파일만")
        self.assertEqual(r["cuts"]["3.0"], {"cut": thumb.asset_url(thumb._cut_path(NAME, 3.0, box)), "src": "/frame?x", "box": box, "q": {"ok": True, "q": 0.3}})
        self.assertNotIn("cutFail", r)
        self.assertIsNone(thumb.cached_analysis(NAME))

    def test_other_error_gets_head(self):
        """자식을 띄우지도 못함(백신 차단 등) — 그래도 '자동 누끼를 따지 못했어요 · …' 한 줄."""
        with mock.patch.object(cutout_worker, "cut_auto", side_effect=PermissionError(13, "Access is denied")), \
                mock.patch.object(thumb.studiolog, "trace"):
            r = thumb.analyze(NAME, log=lambda m: None)
        self.assertEqual(r["cuts"], {})
        self.assertTrue(r["cutFail"].startswith(cutout_worker.AUTO_FAIL_MSG + " · "), r["cutFail"])
        self.assertNotIn("Access is denied", r["cutFail"])

    def test_cancel_stops_job(self):
        with mock.patch.object(cutout_worker, "cut_auto", side_effect=worker.Cancelled("멈췄어요")):
            with self.assertRaises(worker.Cancelled) as cm:
                thumb.analyze(NAME, log=lambda m: None)
        self.assertEqual(trouble.explain(cm.exception)["kind"], "cancelled")


class CutoutAutoTests(WorkBase):
    """cutout_worker.cut_auto (부모) · _child_cut_auto (자식)."""

    def test_wrapper_calls_child(self):
        with mock.patch.object(worker, "call", return_value=[[3.0, "/x/cut_auto_a.png", {"q": 0.8}]]) as call:
            out = cutout_worker.cut_auto(NAME, [(3.0, (0.4, 0.2, 0.2, 0.6), None, None)], cancel="C", procs="P")
        self.assertEqual(out, [(3.0, Path("/x/cut_auto_a.png"), {"q": 0.8})])
        self.assertEqual(call.call_args[0], ("cutout_worker:_child_cut_auto", NAME, [[3.0, (0.4, 0.2, 0.2, 0.6), None, None]], "fast"))
        self.assertEqual(call.call_args[1], {"cancel": "C", "procs": "P"})

    def test_hq_low_memory_goes_fast(self):
        with mock.patch.object(worker, "avail_mb", return_value=3000), mock.patch.object(worker, "call", return_value=[]) as call:
            cutout_worker.cut_auto(NAME, [], kind="hq")
        self.assertEqual(call.call_args[0][3], "fast")

    def test_failure_is_plain_korean(self):
        cases = [(worker.WorkerError("작업 프로세스가 끝까지 못 했어요 (종료 코드 -9)", -9, "died"), "memory", "메모리가 부족해요"),
                 (worker.WorkerError("작업 프로세스가 끝까지 못 했어요 (종료 코드 1) · ImportError: DLL load failed while importing x", 1, "died"),
                  "other", "Visual C++"),
                 (worker.WorkerError("onnxruntime: Failed to load model", 1, "RuntimeError"), "other", None)]
        for err, kind, extra in cases:
            with mock.patch.object(worker, "call", side_effect=err):
                with self.assertRaises(worker.WorkerError) as cm:
                    cutout_worker.cut_auto(NAME, [(1.0, None, None, None)])
            info = trouble.explain(cm.exception)
            self.assertEqual(info["kind"], kind, info)
            self.assertTrue(info["msg"].startswith(cutout_worker.AUTO_FAIL_MSG), info)
            self.assertNotIn("작업 프로세스", info["msg"])
            self.assertNotIn("onnxruntime", info["msg"])
            if extra:
                self.assertIn(extra, info["msg"])
        with mock.patch.object(worker, "call", side_effect=worker.Cancelled("멈췄어요")):
            with self.assertRaises(worker.Cancelled):
                cutout_worker.cut_auto(NAME, [(1.0, None, None, None)])

    def test_child_calls_thumb_cut_auto_with_progress(self):
        prog, seen = [], []

        def fake(name, t, box=None, kind="fast", faces=None, tboxes=None):
            seen.append((name, t, box, kind, faces, tboxes))
            if t == 2.0:
                raise MemoryError()
            return Path(f"/x/{t}.png"), {"q": t}
        jobs = [[1.0, [0.1, 0.2, 0.3, 0.4], [{"box": [0, 0, 1, 1]}], [[0, 0, 1, 1]]], [5.0, None, None, None]]
        with mock.patch.object(cutout_worker, "_arena_off"), mock.patch.object(thumb, "cut_auto", side_effect=fake), \
                mock.patch.object(core, "set_progress", side_effect=lambda **kw: prog.append(kw)):
            out = cutout_worker._child_cut_auto(NAME, jobs, "fast")
            self.assertEqual(out, [[1.0, "/x/1.0.png", {"q": 1.0}], [5.0, "/x/5.0.png", {"q": 5.0}]])
            self.assertEqual(seen[0], (NAME, 1.0, [0.1, 0.2, 0.3, 0.4], "fast", [{"box": [0, 0, 1, 1]}], [[0, 0, 1, 1]]))
            self.assertEqual([p["detail"] for p in prog], ["주인공 누끼 따는 중 1/2", "주인공 누끼 따는 중 2/2"])
            self.assertEqual({p["label"] for p in prog}, {"썸네일 분석"})
            seen.clear()
            with self.assertRaises(MemoryError):
                cutout_worker._child_cut_auto(NAME, [[1.0, None, None, None], [2.0, None, None, None], [3.0, None, None, None]])
            self.assertEqual([s[1] for s in seen], [1.0, 2.0], "한 장이 실패하면 거기서 멈춤 (예전 앱 안 분석과 같음)")

    def test_real_child_process_failure(self):
        """진짜 자식 프로세스(worker.py): 없는 영상 → 자식이 장면을 못 뽑아 실패 → 쉬운 한 줄 (영어 원문·'작업 프로세스' 없음).
        자식의 작업 폴더·모델 폴더는 임시 HOME 아래 (실제 작업 폴더를 건드리지 않음 · 모델은 받지 않음)."""
        home = self.tmp / "home"
        home.mkdir()
        real = worker.call

        def call(*a, **kw):
            return real(*a, env={"HOME": str(home), "USERPROFILE": str(home)}, timeout=240, **kw)
        logs = []
        with mock.patch.object(worker, "call", side_effect=call), mock.patch.object(worker.studiolog, "write", side_effect=logs.append):
            with self.assertRaises(worker.WorkerError) as cm:
                cutout_worker.cut_auto("20261008_NOPENOPENOP_없는 영상.mp4", [(1.0, None, None, None)])
        info = trouble.explain(cm.exception)
        self.assertTrue(info["msg"].startswith(cutout_worker.AUTO_FAIL_MSG), info)
        self.assertNotIn("Traceback", info["msg"])
        self.assertNotIn("작업 프로세스", info["msg"])
        self.assertTrue(any("cutout_worker:_child_cut_auto" in x for x in logs), "자식 오류 출력 끝은 studio.log 에")
        self.assertFalse((home / ".futsal-studio" / "models").exists() and any((home / ".futsal-studio" / "models").glob("*.onnx")),
                         "모델을 받지 않음")


class CutRouteFrameTests(WorkBase):
    """/api/thumb/cut 장면 주소: main 의 검사(작업 전 400·404) 뒤 작업 안은 cutout_worker.remove_bg (앱 안 누끼 없음)."""

    def setUp(self):
        super().setUp()
        import app
        import editor
        self.app, self.editor = app, editor
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        for p in (mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log"),
                  mock.patch.object(thumb, "_bg_mask", side_effect=_no_model), mock.patch.object(thumb, "remove_bg", side_effect=_no_model)):
            p.start()
            self.addCleanup(p.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{port}"
        self.frame = self.tmp / "장면.jpg"
        self.frame.write_bytes(b"jpg")

    def cut(self, body):
        req = urllib.request.Request(self.base + "/api/thumb/cut", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode())
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read())
        self.assertTrue(j["ok"], j)
        end = time.time() + 20
        while self.app.JOB["name"] and time.time() < end:
            time.sleep(0.05)
        return self.app.DONE[j["jobId"]]

    def test_frame_cut_goes_through_worker(self):
        from urllib.parse import quote
        seen = []

        def fake(sp, kind, cancel, procs, log):
            seen.append((sp, kind, cancel, procs))
            return thumb.ASSETS / "cut_9.png", "fast", cutout_worker.LOW_MEM_NOTE
        src = "/frame?name=" + quote(NAME) + "&t=1.5"
        with mock.patch.object(thumb, "grab", return_value=self.frame) as grab, mock.patch.object(cutout_worker, "remove_bg", side_effect=fake):
            done = self.cut({"src": src, "kind": "hq"})
        grab.assert_called_once_with(NAME, 1.5)
        self.assertEqual(seen, [(self.frame, "hq", self.editor.CANCEL, self.editor._PROCS)])
        self.assertIsNone(done["error"])
        self.assertEqual(done["result"], {"cut": "/asset/cut_9.png", "src": src, "kind": "fast", "note": cutout_worker.LOW_MEM_NOTE})

    def test_worker_death_is_plain_card(self):
        """진짜 cutout_worker.remove_bg (자식만 흉내): 고품질·빠른 누끼 둘 다 메모리 부족으로 죽음 → 카드는 '누끼를 따지 못했어요 · 메모리가…'."""
        (thumb.ASSETS / "올린 사진.png").write_bytes(b"png")
        dead = worker.WorkerError("작업 프로세스가 끝까지 못 했어요 (종료 코드 -9)", -9, "died")
        with mock.patch.object(worker, "avail_mb", return_value=None), mock.patch.object(worker, "call", side_effect=dead) as call, \
                mock.patch.object(cutout_worker.studiolog, "trace"):
            done = self.cut({"src": "/asset/올린 사진.png", "kind": "hq"})
        self.assertEqual([c[0][2] for c in call.call_args_list], ["hq", "fast"], "고품질이 죽으면 빠른 누끼로 한 번 더")
        self.assertEqual(done["fail"]["kind"], "memory")
        self.assertTrue(done["error"].startswith(cutout_worker.FAIL_MSG), done)
        self.assertIn("메모리가 부족해요", done["error"])
        for bad in ("작업 프로세스", "종료 코드"):
            self.assertNotIn(bad, done["error"])


class IdleThumbTests(WorkBase):
    """쉬는 동안 내려놓기 × 썸네일: detect 도 비움 · 썸네일 작업 중엔 안 지움 · 검수 OCR 은 using."""

    def _start(self, busy, idle_sec, lock):
        ended = {"v": False}  # 시험이 끝나면 이 루프는 늘 '작업 중' (다른 시험의 진짜 모델을 지우지 않게 — test_idle 과 같은 방법)
        self.addCleanup(_end_loop, ended)  # + 하던 내려놓기가 끝날 때까지 기다림 (다음 시험의 dirty 를 덮어쓰지 않게)
        idle.start(lock, lambda: ended["v"] or busy(), idle_sec=idle_sec)

    def test_release_clears_detect(self):
        import detect
        with mock.patch.dict(detect._SESS, {"det": object()}, clear=True), mock.patch.dict(idle._ST, {"dirty": True}):
            self.assertGreaterEqual(idle.release(), 1)
            self.assertEqual(detect._SESS, {})  # 다음 분석에서 detect.ensure() 가 다시 불러옴

    def test_read_text_holds_using(self):
        import avmodels
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 36), (20, 20, 20)).save(buf, "PNG")
        data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        inside = []

        def fake_ocr(rgb):
            inside.append((idle._USE["n"], idle.models_in_use()))
            return [{"text": "무조건 봐", "conf": 0.9, "box": [0, 0, 1, 1], "extra": 1}]
        with mock.patch.dict(idle._USE, {"n": 0}), mock.patch.dict(idle._ST, {"dirty": False}), \
                mock.patch.object(avmodels, "available", return_value=True), mock.patch.object(avmodels, "ocr", side_effect=fake_ocr):
            lines = thumb.read_text(data)
            self.assertEqual(lines, [{"text": "무조건 봐", "conf": 0.9, "box": [0, 0, 1, 1]}])
            self.assertEqual(inside, [(1, True)], "글자 읽기 모델을 쓰는 동안은 '쓰는 중'")
            self.assertEqual(idle._USE["n"], 0)
            self.assertTrue(idle._ST["dirty"], "끝나면 touch → 5분 뒤 내려놓음")

    def test_thumb_jobs_are_busy_for_idle(self):
        """썸네일 작업(장면 고르기·분석·클로드 문구·클로드 평가)이 도는 동안 내려놓기 루프(0.01초마다 · 쉬는 시간 0)가 돌아도
        얼굴·선수 모델이 안 지워지고, 작업이 끝나면 지워짐 (app.main 과 같은 연결: busy = JOB 이름 · JOB_HOOKS 에 idle.touch)."""
        import app
        import detect
        import face
        import thumbcopy
        names = ("장면 고르기", thumb.JOB_ANALYZE, thumbcopy.JOB_AI, thumbcopy.JOB_JUDGE)
        kept = {}

        def job(n):
            def fn():
                face._SESS.update(detect=object(), emotion=object())  # 작업 안에서 불러 둠 (ensure 흉내)
                detect._SESS["det"] = object()
                ok = True
                for _ in range(30):
                    time.sleep(0.02)
                    ok = ok and bool(face._SESS) and bool(detect._SESS)
                kept[n] = ok
                return {"ok": True}
            return fn
        with mock.patch.dict(face._SESS, clear=True), mock.patch.dict(detect._SESS, clear=True), \
                mock.patch.object(app, "LOGFILE", self.work / "studio.log"), mock.patch.object(app, "JOB_HOOKS", [idle.touch]), \
                mock.patch.object(idle, "CHECK_SEC", 0.01), mock.patch.dict(idle._USE, {"n": 0}), \
                mock.patch.dict(idle._ST, {"thread": None, "dirty": False}):
            self._start(lambda: bool(app.JOB["name"]), 0.0001, app.LOCK)
            for n in names:
                jid = app.start_job(n, job(n))
                self.assertTrue(jid, n)
                end = time.time() + 20
                while app.JOB["name"] and time.time() < end:
                    time.sleep(0.02)
                self.assertTrue(kept.get(n), f"'{n}' 작업 중에 모델이 지워짐")
                end = time.time() + 10  # 끝나면 내려놓음 = 루프가 내내 살아 있었음 (시험이 헛돌지 않았음 · 바쁜 PC 라 넉넉히)
                while (face._SESS or detect._SESS) and time.time() < end:
                    time.sleep(0.02)
                self.assertEqual((face._SESS, detect._SESS), ({}, {}), f"'{n}' 끝난 뒤 내려놓기")


if __name__ == "__main__":
    unittest.main()
