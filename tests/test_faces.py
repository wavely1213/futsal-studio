"""얼굴·표정 점수(face.py)와 썸네일 장면 고르기(thumb.frame_candidates) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_faces

웃는 얼굴 사진(fixtures/smile_closeup.jpg): NASA 공식 초상(Eileen Collins, 퍼블릭 도메인 — scikit-image 의 'astronaut' 예제 그림)을
16:9 로 자른 것. 빈 경기장·관중석 무늬는 테스트가 직접 그림.
인터넷은 쓰지 않음(내려받기는 모두 실패하게 막음). 얼굴·표정 모델(~/.futsal-studio/models)이 없으면 모델이 필요한 확인은 건너뜀
— 앱에서 썸네일 장면 고르기를 한 번 하면 생겨요."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import face  # noqa: E402
import thumb  # noqa: E402
import updater  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]


def _models_ok():
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return face.ready()


MODELS_OK = _models_ok()
NEED_MODELS = unittest.skipUnless(MODELS_OK, "얼굴·표정 모델이 없어요 (~/.futsal-studio/models)")


def _offline(*a, **k):
    raise OSError("인터넷 없음 (테스트)")


def make_images(d):
    """웃는 얼굴 클로즈업 · 빈 경기장 · 얼굴 없이 선명하고 살색이 많은 관중석 무늬 (모두 640×360)."""
    import numpy as np
    from PIL import Image, ImageDraw
    close = d / "웃는 얼굴.jpg"
    shutil.copy(FIX / "smile_closeup.jpg", close)
    field = d / "빈 경기장.jpg"
    im = Image.new("RGB", (640, 360), (46, 139, 60))
    dr = ImageDraw.Draw(im)
    dr.line([(320, 0), (320, 360)], fill=(240, 240, 240), width=4)
    dr.ellipse([260, 120, 380, 240], outline=(240, 240, 240), width=4)
    dr.rectangle([0, 100, 70, 260], outline=(240, 240, 240), width=4)
    dr.rectangle([570, 100, 640, 260], outline=(240, 240, 240), width=4)
    im.save(field, quality=90)
    crowd = d / "관중석.jpg"
    rng = np.random.default_rng(7)
    im = Image.new("RGB", (640, 360), (120, 110, 100))
    dr = ImageDraw.Draw(im)
    cols = [(224, 172, 140), (200, 140, 110), (40, 40, 40), (230, 230, 230), (190, 60, 50), (60, 80, 160)]
    for _ in range(900):
        x, y = (int(v) for v in rng.integers(0, (640, 360)))
        w, h = (int(v) for v in rng.integers(4, 22, 2))
        dr.rectangle([x, y, x + w, y + h], fill=cols[int(rng.integers(0, len(cols)))])
    im.save(crowd, quality=90)
    return close, field, crowd


def make_video(out, images, sec=2):
    """사진 여러 장을 sec 초씩 이어 붙인 영상 (lavfi 없이 ffmpeg 만)."""
    args = []
    for p in images:
        args += ["-loop", "1", "-t", str(sec), "-i", str(p)]
    n = len(images)
    chain = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p[v]"
    r = core.run([core.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args, "-filter_complex", chain, "-map", "[v]",
                  "-r", "10", "-c:v", "libx264", "-preset", "ultrafast", str(out)])
    if r.returncode:
        raise RuntimeError(r.stderr)
    return out


class FaceBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = Path(tempfile.mkdtemp(prefix="얼굴 테스트 공용 "))
        cls.close, cls.field, cls.crowd = make_images(cls.shared)
        # 관중석(0~2초) · 빈 경기장(2~4초) · 웃는 얼굴(4~6초) — 클로즈업이 맨 앞이 아니게
        cls.video = make_video(cls.shared / "얼굴 시험 영상.mp4", [cls.crowd, cls.field, cls.close])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.shared, ignore_errors=True)

    def setUp(self):
        # 한글·띄어쓰기가 들어간 작업 폴더 (Windows 사용자 폴더처럼)
        self.tmp = Path(tempfile.mkdtemp(prefix="얼굴 테스트 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        # 인터넷은 절대 쓰지 않음 (모델이 이미 있으면 내려받기 자체를 안 함)
        self.patches += [mock.patch.object(urllib.request, "urlopen", side_effect=_offline),
                         mock.patch.object(urllib.request, "urlretrieve", side_effect=_offline)]
        self.patches += [mock.patch.dict(face._SESS), mock.patch.dict(face._FAIL)]  # 모델 상태는 테스트마다 원래대로
        if not MODELS_OK:  # 모델이 없으면 사용자 폴더에 빈 모델 폴더도 만들지 않게
            self.patches.append(mock.patch.object(thumb, "MODELS", self.tmp / "models"))
        for p in self.patches:
            p.start()
        face._FAIL["t"] = 0.0
        self.name = "얼굴 시험 영상.mp4"
        shutil.copy(self.video, core.VIDEOS / self.name)
        core.set_progress()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def segment(self, t):
        """영상 몇 번째 사진인지: 0 관중석 · 1 빈 경기장 · 2 웃는 얼굴."""
        return int(t // 2)


class TestFaceScore(unittest.TestCase):
    """모델 없이: 배율 계산."""

    def test_boost(self):
        big = {"box": [0.3, 0.2, 0.25, 0.45], "emo": {"happiness": 0.9, "surprise": 0.05}, "sharp": 1.0}
        small = {"box": [0.1, 0.1, 0.05, 0.09], "emo": {"neutral": 0.9, "happiness": 0.02}, "sharp": 0.3}
        self.assertEqual(face.boost([]), 0.6)
        self.assertEqual(face.boost(None), 0.6)
        self.assertGreater(face.boost([big]), 2.5)
        self.assertLess(face.boost([small]), 1.0)
        self.assertIs(face.main([small, big]), big)
        self.assertEqual(face.boost([small, big]), face.weight(big))
        surprised = dict(big, emo={"surprise": 0.95})
        neutral = dict(big, emo={"neutral": 0.95})
        self.assertGreater(face.weight(surprised), face.weight(neutral) * 1.6)


class TestFaces(FaceBase):
    @NEED_MODELS
    def test_smiling_closeup(self):
        self.assertTrue(face.ensure())
        fs = face.faces(self.close)
        self.assertEqual(len(fs), 1, fs)
        f = fs[0]
        x, y, w, h = f["box"]
        self.assertGreater(h, 0.3)
        self.assertTrue(0 <= x <= 1 and 0 <= y <= 1 and x + w <= 1.0001 and y + h <= 1.0001, f["box"])
        self.assertGreaterEqual(f["score"], face.THRESH)
        top = max(f["emo"], key=f["emo"].get)
        self.assertIn(top, ("happiness", "surprise"))
        self.assertAlmostEqual(sum(f["emo"].values()), 1.0, delta=0.01)
        self.assertEqual(set(f["emo"]), set(face.EMOTIONS))
        self.assertTrue(0 <= f["sharp"] <= 1)
        self.assertGreater(f["sharp"], 0.6, "또렷한 사진")

    @NEED_MODELS
    def test_empty_field_and_crowd(self):
        self.assertTrue(face.ensure())
        self.assertEqual(face.faces(self.field), [])
        self.assertEqual(face.faces(self.crowd), [], "얼굴 없는 무늬를 얼굴로 착각하면 안 됨")

    @NEED_MODELS
    def test_blurry_face_is_less_sharp(self):
        from PIL import Image, ImageFilter
        self.assertTrue(face.ensure())
        sharp = face.faces(self.close)[0]
        blurred = face.faces(Image.open(self.close).filter(ImageFilter.GaussianBlur(4)))
        self.assertTrue(blurred, "조금 흐려도 얼굴은 찾아야 함")
        self.assertLess(blurred[0]["sharp"], sharp["sharp"] - 0.3)

    @NEED_MODELS
    def test_shorts_portrait_and_small_face(self):
        """세로(쇼츠) 화면도 얼굴을 찾고, 화면 높이 8% 보다 작은 얼굴은 무시."""
        from PIL import Image
        self.assertTrue(face.ensure())
        im = Image.open(self.close)
        tall = Image.new("RGB", (1080, 1920), (30, 30, 30))
        tall.paste(im.resize((1080, 608)), (0, 600))
        fs = face.faces(tall)
        self.assertEqual(len(fs), 1, fs)
        self.assertGreater(fs[0]["box"][3], 0.1)
        wide = Image.new("RGB", (1920, 1080), (46, 139, 60))
        wide.paste(im.resize((240, 135)), (800, 450))  # 얼굴 높이 약 5%
        self.assertEqual(face.faces(wide), [])

    def test_faces_none_without_models(self):
        face._SESS.clear()
        self.assertIsNone(face.faces(self.close))


class TestFrameCandidates(FaceBase):
    @NEED_MODELS
    def test_closeup_ranked_first(self):
        # 예전 점수(선명도·살색)만으로는 관중석 무늬가 더 높음 → 얼굴 점수 덕분에 순위가 바뀌는지 확인하는 테스트
        self.assertGreater(thumb._sharpness(self.crowd), thumb._sharpness(self.close))
        items = thumb.frame_candidates(self.name)
        self.assertGreaterEqual(len(items), 3)
        top = items[0]
        self.assertEqual(self.segment(top["t"]), 2, items)
        self.assertGreater(top["face"], 0.3)
        self.assertIn(max(top["emo"], key=top["emo"].get), ("happiness", "surprise"))
        self.assertEqual(len(top["faces"]), 1)
        self.assertEqual(top["url"], f"/frame?name={self.name}&t={top['t']}")
        self.assertEqual([i["score"] for i in items], sorted((i["score"] for i in items), reverse=True), "좋은 순")
        self.assertEqual(len(items), 8)
        ts = sorted(i["t"] for i in items)
        self.assertTrue(all(b - a > 6 * 0.05 for a, b in zip(ts, ts[1:])), f"표정 다듬기 뒤에도 장면끼리 떨어져 있어야 함 · {ts}")
        for i in items:
            if self.segment(i["t"]) != 2:
                self.assertNotIn("faces", i, i)
        # 저장된 캐시를 그대로 돌려줌
        self.assertTrue((core.adir(self.name) / "frames" / "candidates3.json").is_file())
        self.assertEqual(thumb.cached_candidates(self.name), items)
        with mock.patch.object(thumb, "_score_frames", side_effect=AssertionError("다시 고르면 안 됨")):
            self.assertEqual(thumb.frame_candidates(self.name), items)
        json.dumps(items)  # 화면으로 보낼 수 있어야 함

    def test_analysis_mtime_invalidates_cache(self):
        a = core.adir(self.name)
        a.mkdir(parents=True, exist_ok=True)
        ana = a / "analysis.json"
        ana.write_text(json.dumps({"silences": [], "loud_peaks": [{"time": 5.1, "rms_db": -3}]}), encoding="utf-8")
        self.assertIsNone(thumb.cached_candidates(self.name))
        items = thumb.frame_candidates(self.name)
        self.assertTrue(items)
        self.assertEqual(thumb.cached_candidates(self.name), items)
        st = ana.stat()
        os.utime(ana, (st.st_atime, st.st_mtime + 120))  # 편집점 찾기를 다시 한 것처럼
        self.assertIsNone(thumb.cached_candidates(self.name))
        with mock.patch.object(thumb, "_score_frames", wraps=thumb._score_frames) as sf:
            again = thumb.frame_candidates(self.name)
        self.assertTrue(sf.called, "분석이 바뀌면 다시 골라야 함")
        self.assertTrue(again)
        self.assertEqual(thumb.cached_candidates(self.name), again)
        # 영상 파일이 바뀌어도 다시
        v = core.VIDEOS / self.name
        st = v.stat()
        os.utime(v, (st.st_atime, st.st_mtime + 120))
        self.assertIsNone(thumb.cached_candidates(self.name))
        # 깨진 캐시 파일 → None (오류 없이)
        (a / "frames" / "candidates3.json").write_text("{깨짐", encoding="utf-8")
        self.assertIsNone(thumb.cached_candidates(self.name))
        # 없는 영상 → None
        self.assertIsNone(thumb.cached_candidates("없는 영상.mp4"))

    def test_download_failure_falls_back(self):
        """모델을 못 받으면(인터넷 없음) 조용히 예전 점수로 장면을 고름."""
        empty = self.tmp / "모델 없음"
        face._SESS.clear()
        with mock.patch.object(thumb, "MODELS", empty):
            self.assertFalse(face.ready())
            items = thumb.frame_candidates(self.name)
            self.assertTrue(items)
            self.assertGreaterEqual(urllib.request.urlopen.call_count, 1, "내려받기를 시도했어야 함")
            self.assertEqual([p.name for p in empty.glob("*") if p.name != face._MARK], [], "반쪽 파일이 남으면 안 됨")
            for i in items:
                self.assertNotIn("faces", i)
                self.assertEqual(i["score"], round(float(thumb._sharpness(thumb.grab(self.name, i["t"]))), 3), "예전 점수 그대로")
            self.assertEqual(thumb.cached_candidates(self.name), items)
            # 바로 다시 열어도 또 기다리지 않음 (10분 동안 다시 시도 안 함)
            n = urllib.request.urlopen.call_count
            self.assertFalse(face.ensure())
            self.assertEqual(urllib.request.urlopen.call_count, n)
            # 앱을 다시 켜도(메모리 기록이 사라져도) 10분 동안은 다시 기다리지 않음 — 모델 폴더에 남긴 표시
            self.assertTrue((empty / face._MARK).is_file())
            face._FAIL["t"] = 0.0
            self.assertFalse(face.ensure())
            self.assertEqual(urllib.request.urlopen.call_count, n, "다시 켠 뒤에도 바로 포기")
            # 10분이 지나면 다시 시도
            old = time.time() - face.RETRY - 5
            os.utime(empty / face._MARK, (old, old))
            self.assertFalse(face.ensure())
            self.assertGreater(urllib.request.urlopen.call_count, n, "10분 뒤엔 다시 받아 봄")
        # 모델이 생기면 캐시 지문이 달라져서 얼굴 점수로 다시 고름
        if MODELS_OK:
            self.assertIsNone(thumb.cached_candidates(self.name))

    def test_stalled_network_gives_up_fast_with_progress(self):
        """대답 없는 인터넷(방화벽): 첫 주소에서 시간이 다 되면 다른 주소는 건너뜀. 기다리는 동안 안내 글이 보임."""
        empty = self.tmp / "모델 없음"
        face._SESS.clear()
        tried, shown = [], []

        def stall(url, dest, progress=None, timeout=30):
            tried.append((url, timeout))
            shown.append(dict(core.PROGRESS))
            raise urllib.error.URLError(socket.timeout("timed out"))

        with mock.patch.object(thumb, "MODELS", empty), mock.patch.object(updater, "download", side_effect=stall):
            self.assertFalse(face.ensure(item=self.name))
        self.assertEqual(len(tried), 1, f"시간이 다 되면 다음 주소로 또 기다리지 않음 · {tried}")
        self.assertIn(face._REV, tried[0][0], "고정된 판(커밋) 주소를 먼저")
        self.assertLessEqual(tried[0][1], 10, "대답 없는 연결은 10초 안에 포기")
        self.assertEqual(shown[0].get("label"), "장면 고르는 중")
        self.assertEqual(shown[0].get("item"), self.name)
        self.assertIn("얼굴·표정 모델", shown[0].get("detail") or "", "받기 시작 전에도 무엇을 기다리는지 보여야 함")
        self.assertEqual([p.name for p in empty.glob("*")], [face._MARK])

    def test_keyword_times(self):
        a = core.adir(self.name)
        a.mkdir(parents=True, exist_ok=True)
        (a / "transcript.json").write_text(json.dumps([
            {"start": 0.5, "end": 1.5, "text": "안녕하세요"},
            {"start": 2.0, "end": 4.0, "text": "오늘은 퍼스트 터치 꿀팁"},
            {"start": 4.2, "end": 5.0, "text": "와 대박"},
        ], ensure_ascii=False), encoding="utf-8")
        ts = thumb._keyword_times(self.name)
        self.assertIn(2.3, [round(x, 2) for x in ts])
        self.assertIn(3.0, [round(x, 2) for x in ts])
        self.assertIn(4.5, [round(x, 2) for x in ts])
        self.assertNotIn(0.8, [round(x, 2) for x in ts])
        (a / "transcript.json").write_text("깨진 파일", encoding="utf-8")
        self.assertEqual(thumb._keyword_times(self.name), [])


class TestModelSource(unittest.TestCase):
    def test_pinned_commit_first_main_last(self):
        """모델 모음 저장소 main 의 폴더가 바뀌어도 받을 수 있게 고정된 커밋 주소를 먼저, main 은 마지막."""
        self.assertRegex(face._REV, r"^[0-9a-f]{40}$")
        self.assertTrue(face._HOSTS[0].startswith("https://media.githubusercontent.com/media/onnx/models/" + face._REV + "/"))
        self.assertTrue(all(face._REV in h for h in face._HOSTS[:2]))
        self.assertTrue(all(h.endswith("/main/") for h in face._HOSTS[-2:]))
        seen = []

        def fake(fname, urls, *a, **k):
            seen.append(urls)
            raise OSError("테스트")

        with mock.patch.dict(face._SESS), mock.patch.dict(face._FAIL, {"t": 0.0}), \
                mock.patch.object(thumb, "MODELS", Path(tempfile.mkdtemp(prefix="모델 주소 "))) as m, \
                mock.patch.object(thumb, "fetch_model", side_effect=fake):
            face._SESS.clear()
            self.assertFalse(face.ensure())
            shutil.rmtree(m, ignore_errors=True)
        self.assertEqual(len(seen[0]), 4)
        self.assertTrue(seen[0][0].endswith("/" + face._REV + "/validated/vision/body_analysis/ultraface/models/version-RFB-320.onnx"), seen[0][0])


class TestStripBadge(unittest.TestCase):
    """썸네일 편집기 영상 장면 줄: 배지(😆 웃음 / 😮 놀람 / 얼굴)와 '표정 좋은 순'이 같은 기준이어야 함."""

    @unittest.skipUnless(shutil.which("node"), "node 가 없어요")
    def test_badge_matches_expression_sort(self):
        html = (ROOT / "thumb.html").read_text(encoding="utf-8")
        ex = re.search(r"^const exprOf = .*$", html, re.M).group(0)
        fb = re.search(r"^function faceBadge\(f\) \{.*?^\}", html, re.M | re.S).group(0)
        frames = [
            {"t": 1, "face": 0.3, "emo": {"happiness": 0.35, "surprise": 0.30}},  # 웃음·놀람이 반씩 → 합은 큼
            {"t": 2, "face": 0.3, "emo": {"happiness": 0.50, "surprise": 0.02}},
            {"t": 3, "face": 0.3, "emo": {"happiness": 0.10, "surprise": 0.05, "neutral": 0.8}},
            {"t": 4, "face": 0.2, "emo": {"happiness": 0.05, "surprise": 0.60}},
            {"t": 5},
        ]
        js = f"{ex}\n{fb}\nconst F = {json.dumps(frames)};\nconsole.log(JSON.stringify(F.map(f => [f.t, exprOf(f), faceBadge(f)])));"
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {t: (e, b) for t, e, b in json.loads(r.stdout)}
        self.assertIn("😆 웃음", rows[1][1])
        self.assertIn("😆 웃음", rows[2][1])
        self.assertIn("😮 놀람", rows[4][1])
        self.assertTrue(rows[3][1].startswith("<b") and "얼굴 30%" in rows[3][1])
        self.assertEqual(rows[5][1], "")
        # 표정 좋은 순으로 놓으면 웃음·놀람 배지가 모두 '얼굴' 배지보다 앞
        order = [rows[t][1] for t in sorted(rows, key=lambda t: -rows[t][0])]
        tagged = ["웃음" in b or "놀람" in b for b in order if b]
        self.assertEqual(tagged, sorted(tagged, reverse=True), order)


class TestFetchModel(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="모델 폴더 "))
        self.p = mock.patch.object(thumb, "MODELS", self.tmp / "models")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_checks_size_and_hash_then_places(self):
        import hashlib
        body = b"onnx-model-bytes" * 100
        good = hashlib.sha256(body).hexdigest()
        urls = []

        def fake(url, dest, progress=None, timeout=30):
            urls.append(url)
            Path(dest).write_bytes(body if "good" in url else body[:-1] + b"X")
            if progress:
                progress(len(body), len(body))
            return dest

        with mock.patch.object(updater, "download", side_effect=fake):
            with self.assertRaises(OSError):
                thumb.fetch_model("m.onnx", ["https://bad.example/m"], "준비 중", "받는 중", len(body), good)
            self.assertEqual(list((self.tmp / "models").glob("*")), [], "확인에 실패한 파일은 남기지 않음")
            p = thumb.fetch_model("m.onnx", ["https://bad.example/m", "https://good.example/m"], "준비 중", "받는 중", len(body), good)
            self.assertEqual(p.read_bytes(), body)
            self.assertEqual(urls[-2:], ["https://bad.example/m", "https://good.example/m"], "첫 주소가 안 되면 다음 주소")
            n = len(urls)
            self.assertEqual(thumb.fetch_model("m.onnx", ["https://good.example/m"], "준비 중", "받는 중", len(body), good), p)
            self.assertEqual(len(urls), n, "이미 있으면 다시 받지 않음")
        self.assertEqual(sorted(x.name for x in (self.tmp / "models").iterdir()), ["m.onnx"])

    def test_timeout_stops_trying_other_urls(self):
        """대답 없이 시간이 다 되면 다음 주소로 넘어가지 않음 (같은 인터넷이라 또 기다리게 될 뿐). 다른 오류는 다음 주소로."""
        urls = []

        def fake(url, dest, progress=None, timeout=30):
            urls.append((url, timeout))
            Path(dest).write_bytes(b"half")
            if "read" in url:
                raise TimeoutError("The read operation timed out")
            if "connect" in url:
                raise urllib.error.URLError(socket.timeout("timed out"))
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with mock.patch.object(updater, "download", side_effect=fake):
            for first in ("https://read.example/m", "https://connect.example/m"):
                urls.clear()
                with self.assertRaises(OSError):
                    thumb.fetch_model("m.onnx", [first, "https://next.example/m"], "준비 중", "받는 중", timeout=7)
                self.assertEqual(urls, [(first, 7)])
            urls.clear()
            with self.assertRaises(OSError):
                thumb.fetch_model("m.onnx", ["https://gone.example/m", "https://read.example/m", "https://never.example/m"], "준비 중", "받는 중")
            self.assertEqual([u for u, _ in urls], ["https://gone.example/m", "https://read.example/m"])
        self.assertEqual(list((self.tmp / "models").glob("*")), [], "반쪽 파일 없음")


if __name__ == "__main__":
    unittest.main()
