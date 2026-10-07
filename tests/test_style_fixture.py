"""스타일 이벤트 기록·스타일 일치 점수·정답 영상 회귀 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_style_fixture

정답 영상(tests/make_fixture.py)은 ffmpeg lavfi 로 만들어 컷 시각·확대 컷·자막 위치와 색·말 사이 공백을 미리 알고 있음.
인터넷·받아쓰기 모델은 쓰지 않음 (받아쓰기는 transcript.json 을 직접 씀)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import style  # noqa: E402
import make_fixture as fx  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
TRUTH = fx.truth()
MAIN, TOP, COPY = "정답 영상 (아래 자막).mp4", "정답 영상 (위 자막).mp4", "정답 영상 다시 인코딩.mp4"
OLD_KEYS = {"source", "duration", "analyzed", "cutsPerMin", "avgShot", "medianShot", "zoomCutsPerMin", "zoomRatio", "avgZoom",
            "silenceRatio", "pauseP75", "pauses", "captionRatio", "captionPos", "captionColor", "captionBands", "lufs", "charsPerSec"}


def speech_segments():
    """정답 영상의 소리 구간(공백 사이) = 받아쓰기 흉내."""
    out, t = [], 0.0
    words = ["풋살 첫 터치", "몸을 열어요", "기본기 연습", "시선은 앞", "패스 받고 돌아서기", "실수해도 괜찮아요", "꿀팁 정리"]
    for k, (a, n) in enumerate(fx.GAPS + [(fx.DURATION, 0)]):
        if a - t > 0.2:
            out.append({"start": round(t, 2), "end": round(a, 2), "text": words[k % len(words)]})
        t = a + n
    return out


M = {}  # 모듈 전체가 같이 쓰는 정답 영상·기록 (느린 화면 분석을 테스트마다 반복하지 않게)


def setUpModule():
    tmp = Path(tempfile.mkdtemp(prefix="스타일 테스트 "))
    work = tmp / "풋살 작업 폴더"
    dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    M["tmp"] = tmp
    M["patches"] = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(style, "STYLES", work / "styles")]
    for p in M["patches"]:
        p.start()
    v = dirs["VIDEOS"]
    fx.make_fixture(v / MAIN)
    fx.make_fixture(v / TOP, caption_pos="top", white_note=True)
    fx.reencode(v / MAIN, v / COPY)
    M["ev"] = style.extract_events(MAIN, lambda m: None)
    M["prof"] = style.summarize(M["ev"])


def tearDownModule():
    for p in M.get("patches", []):
        p.stop()
    shutil.rmtree(M.get("tmp", ""), ignore_errors=True)


class StyleFixtureBase(unittest.TestCase):
    def setUp(self):
        self.ev, self.prof = M["ev"], M["prof"]


class TestGroundTruth(StyleFixtureBase):
    def test_cuts_recall_precision(self):
        got, want = self.ev["cuts"], TRUTH["cuts"]
        hit = lambda t, xs: any(abs(t - x) <= 0.25 for x in xs)
        recall = sum(hit(t, got) for t in want) / len(want)
        precision = sum(hit(t, want) for t in got) / max(1, len(got))
        self.assertGreaterEqual(recall, 0.9, (got, want))
        self.assertGreaterEqual(precision, 0.9, (got, want))
        self.assertAlmostEqual(self.prof["cutsPerMin"], len(want) * 60 / TRUTH["duration"], delta=0.6)

    def test_punch_in(self):
        z = [z for z in self.ev["zooms"] if abs(z["t"] - TRUTH["punch"]["t"]) <= 0.25]
        self.assertEqual(len(z), 1, self.ev["zooms"])
        self.assertAlmostEqual(z[0]["scale"], TRUTH["punch"]["scale"], delta=0.05)
        self.assertEqual(set(z[0]), {"t", "scale", "ox", "oy"})
        self.assertLessEqual(abs(z[0]["ox"]) + abs(z[0]["oy"]), 0.05, "가운데를 확대")
        self.assertEqual(len(self.ev["zooms"]), 1, "다른 컷은 확대 컷이 아님")

    def test_captions_bottom_yellow(self):
        self.assertEqual(self.prof["captionPos"], "bottom")
        self.assertEqual(self.prof["captionColor"], TRUTH["captionColor"])
        cover = sum(b - a for a, b in TRUTH["captions"]) / TRUTH["duration"]
        self.assertAlmostEqual(self.prof["captionRatio"], cover, delta=0.1)
        self.assertLess(self.prof["captionBands"]["top"], 0.05)

    def test_caption_color_from_detected_band(self):
        """자막이 위에 있고 아래에는 흰 글자(채널 이름)가 가끔 → 색은 자막이 있는 위쪽에서 뽑아야 노랑 (예전에는 늘 아래에서 뽑아 흰색)."""
        p = style.summarize(style.extract_events(TOP, lambda m: None))
        self.assertEqual(p["captionPos"], "top")
        self.assertEqual(p["captionColor"], "#FFE14D")
        self.assertGreater(p["captionBands"]["bottom"], 0.2, "흰 글자도 글자로는 보임")

    def test_pauses(self):
        self.assertAlmostEqual(self.prof["pauseP75"], TRUTH["pauseP75"], delta=0.1)
        self.assertEqual(self.prof["pauses"], len(TRUTH["gaps"]))
        for (a, n), (s, e) in zip(TRUTH["gaps"], self.ev["silences"]):
            self.assertAlmostEqual(s, a, delta=0.05)
            self.assertAlmostEqual(e - s, n, delta=0.05)

    def test_event_record(self):
        f = core.adir(MAIN) / "style_events.json"
        self.assertTrue(f.exists())
        ev = json.loads(f.read_text(encoding="utf-8"))
        n2 = int(TRUTH["duration"] * 2)
        self.assertEqual(ev["sig"][0], (core.VIDEOS / MAIN).stat().st_size)
        self.assertLessEqual(abs(len(ev["text"]) - n2), 1)
        self.assertTrue(all(len(r) == 6 and all(isinstance(x, int) and 0 <= x <= 255 for x in r) for r in ev["text"]))
        self.assertLessEqual(abs(len(ev["motion"]) - n2), 1)
        self.assertLessEqual(abs(len(ev["rms"]) - TRUTH["duration"] * 10), 2)
        self.assertTrue(all(-90 <= x <= 0 for x in ev["rms"]))
        quiet = [ev["rms"][int((a + n / 2) * 10)] for a, n in TRUTH["gaps"] if n >= 0.3]
        self.assertTrue(all(x <= -60 for x in quiet), quiet)
        self.assertTrue(ev["capColors"] and all({"t", "rgb"} <= set(c) for c in ev["capColors"]))
        self.assertTrue(all(len(c["rgb"]) == 3 for c in ev["capColors"]))
        self.assertGreater(max(ev["motion"]), 16, "컷 자리에서 크게 움직임")

    def test_cache_hit_and_invalidation(self):
        boom = mock.Mock(side_effect=AssertionError("다시 살펴보면 안 됨"))
        with mock.patch.object(style, "_frames", boom), mock.patch.object(style, "_audio_events", boom):
            t = time.time()
            ev = style.extract_events(MAIN, lambda m: None)
            self.assertLess(time.time() - t, 1.0)
        self.assertEqual(ev["cuts"], self.ev["cuts"])
        # 받아쓰기가 나중에 생기면 말 빠르기만 새로 (화면은 다시 안 봄)
        tj = core.adir(MAIN) / "transcript.json"
        tj.write_text(json.dumps(speech_segments(), ensure_ascii=False), encoding="utf-8")
        try:
            with mock.patch.object(style, "_frames", boom), mock.patch.object(style, "_audio_events", boom):
                p = style.summarize(style.extract_events(MAIN, lambda m: None))
            self.assertIsNotNone(p["charsPerSec"])
        finally:
            tj.unlink()
        # 영상 파일이 바뀌면(수정 시각) 기록을 다시 씀
        path = core.VIDEOS / MAIN
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        try:
            self.assertIsNone(style._load_events(MAIN))
        finally:
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertIsNotNone(style._load_events(MAIN))

    def test_summarize_keys(self):
        self.assertEqual(set(self.prof), OLD_KEYS | {"shotDeciles", "pauseDeciles", "curve3"})
        for k in ("shotDeciles", "pauseDeciles"):
            q = self.prof[k]
            self.assertEqual(len(q), 10)
            self.assertEqual(q, sorted(q))
        self.assertAlmostEqual(self.prof["shotDeciles"][4], 3.5, delta=0.3)
        self.assertEqual(style.edit_params(self.prof)["captionColor"], "#FFE14D")

    def test_distance_identity_and_reencoded_copy(self):
        r = style.distance(self.prof, self.prof)
        self.assertEqual(r["score"], 100)
        self.assertEqual(r["distance"], 0)
        self.assertEqual(set(r["parts"]), {"컷 리듬", "확대", "공백", "자막", "소리"})
        self.assertTrue(all(v == 100 for v in r["parts"].values()))
        cp = style.summarize(style.extract_events(COPY, lambda m: None))
        r = style.distance(self.prof, cp)
        self.assertGreaterEqual(r["score"], 95, r)
        self.assertEqual(r["basis"], "분포")

    def test_distance_sees_differences(self):
        slow = dict(self.prof, shotDeciles=[q * 2 for q in self.prof["shotDeciles"]])
        r = style.distance(self.prof, slow)
        self.assertLess(r["parts"]["컷 리듬"], 40)  # 컷 길이가 두 배
        self.assertEqual(r["parts"]["자막"], 100)
        top = dict(self.prof, captionPos="top", captionColor="#FFFFFF")
        self.assertLess(style.distance(self.prof, top)["parts"]["자막"], 70)
        quiet = dict(self.prof, lufs=self.prof["lufs"] - 5)
        self.assertEqual(style.distance(self.prof, quiet)["parts"]["소리"], 50)
        no_lufs = dict(self.prof, lufs=None)
        r = style.distance(self.prof, no_lufs)
        self.assertIsNone(r["parts"]["소리"])
        self.assertEqual(r["score"], 100, "모르는 부분은 빼고 매김")

    def test_weights_from_reference_spread(self):
        """레퍼런스끼리 컷 리듬은 제각각, 자막은 한결같으면 → 자막 무게가 더 큼 (1/표준편차)."""
        a = dict(self.prof)
        b = dict(self.prof, shotDeciles=[q * 1.8 for q in self.prof["shotDeciles"]])
        target = dict(style.merge([a, b]), refs=[a, b])
        w = style.distance(target, a)["weights"]
        self.assertGreater(w["자막"], w["컷 리듬"])
        self.assertAlmostEqual(sum(w.values()), 1.0, delta=0.01)
        w1 = style.distance(self.prof, a)["weights"]  # 레퍼런스 하나 → 기본 무게
        self.assertEqual(w1, style.BASE_W)

    def test_merge_concatenates_events(self):
        ev2 = style.extract_events(COPY, lambda m: None)
        p2 = style.summarize(ev2)
        m = style.merge([self.prof, p2], [self.ev, ev2])
        self.assertEqual(set(m), OLD_KEYS | {"shotDeciles", "pauseDeciles", "curve3"})
        self.assertEqual(m["source"], [MAIN, COPY])
        n = len(self.ev["cuts"]) + len(ev2["cuts"])
        dur = self.ev["duration"] + ev2["duration"]
        self.assertAlmostEqual(m["cutsPerMin"], round(n * 60 / dur, 2), delta=0.01)
        self.assertEqual(m["pauses"], self.prof["pauses"] + p2["pauses"])
        # 영상 경계는 컷이 아님: 컷 수 + 영상 수 만큼의 장면
        self.assertAlmostEqual(m["avgShot"], round(dur / (n + 2), 2), delta=0.01)
        self.assertEqual(m["captionPos"], "bottom")
        self.assertEqual(m["captionColor"], "#FFE14D")
        self.assertAlmostEqual(m["captionRatio"], (self.prof["captionRatio"] + p2["captionRatio"]) / 2, delta=0.03)
        # 기록이 없는 프로필끼리(예전 방식)는 분포를 증거만큼 무게 두고 평균
        m2 = style.merge([self.prof, p2])
        self.assertEqual(len(m2["shotDeciles"]), 10)
        self.assertAlmostEqual(m2["shotDeciles"][4], self.prof["shotDeciles"][4], delta=0.05)


class TestOldStyleCompat(StyleFixtureBase):
    """v1.7 에 배운 스타일 JSON (기록·분포 없음) 도 그대로 열리고 같은 값을 내야 함."""

    def setUp(self):
        super().setUp()
        style.STYLES.mkdir(parents=True, exist_ok=True)
        self.f = style.STYLES / "슛포러브 스타일.json"
        shutil.copy2(FIX / "style_v17.json", self.f)
        self.old = json.loads(self.f.read_text(encoding="utf-8"))

    def tearDown(self):
        self.f.unlink(missing_ok=True)

    def test_list_and_params_identical(self):
        st = next(s for s in style.list_styles() if s["name"] == "슛포러브 스타일")
        self.assertEqual(st["profile"], self.old)
        # git 998a5ee(v1.8.0) style.py 의 edit_params·describe 결과 그대로
        # 컷 리듬 맞추기(#7) 값은 새로 더해짐 (예전 스타일: 3구간 모두 가운데 컷 길이 · 말 빠르기는 배운 값)
        self.assertEqual({k: st["params"].pop(k) for k in ("splitShot", "curve3", "tempo")}, {"splitShot": 2.5, "curve3": [2.5] * 3, "tempo": 7.12})
        self.assertEqual(st["params"], {"keepPause": 0.41, "targetShot": 2.5, "zoomEvery": 16.8, "zoomScale": 1.22, "captions": True,
                                        "captionPos": "bottom", "captionColor": "#FFE14D", "lufs": -14.45})
        self.assertEqual(st["desc"], "컷이 3.16초마다 바뀌고(1분에 19.23번), 16.8초마다 확대 컷(약 1.22배)이 나와요. "
                                     "말 사이 0.41초 넘게 쉬면 잘라요. 자막이 화면의 65%에 아래쪽으로 깔려요.")
        self.assertEqual({k: v for k, v in style.edit_params(self.old["refs"][0]).items() if k not in ("splitShot", "curve3", "tempo")}, {"keepPause": 0.38, "targetShot": 2.25, "zoomEvery": 14.3, "zoomScale": 1.24,
                                                                  "captions": True, "captionPos": "bottom", "captionColor": "#FFE14D", "lufs": -13.8})

    def test_merge_of_old_profiles_unchanged(self):
        m = style.merge(self.old["refs"])
        want = {k: v for k, v in self.old.items() if k not in ("refs", "analyzed")}
        self.assertEqual({k: v for k, v in m.items() if k != "analyzed"}, want)

    def test_score_from_means(self):
        r = style.distance(self.old, self.old)
        self.assertEqual((r["score"], r["basis"]), (100, "평균"))
        r = style.distance(self.old, self.prof)  # 새 프로필과 비교 (분포 없음 → 평균으로)
        self.assertEqual(r["basis"], "평균")
        self.assertTrue(0 <= r["score"] < 100)
        self.assertTrue(all(isinstance(v, int) for v in r["parts"].values()))
        self.assertAlmostEqual(sum(r["weights"].values()), 1.0, delta=0.01)


class TestSequenceProfile(StyleFixtureBase):
    def _proj(self, name=MAIN):
        import editor
        info = editor.media_info(name)
        return editor, {"source": name, "info": info, "captions": [],
                        "media": [{"id": "main", "kind": "video", "src": "videos", "file": name, "dur": info["duration"],
                                   "w": info["width"], "h": info["height"], "fps": info["fps"], "audio": True}]}

    def test_sequence_vs_rendered_result(self):
        """편집본 JSON 으로 바로 잰 컷 리듬 ≈ 그 편집본을 실제로 내보낸 영상을 화면 분석한 컷 리듬 (10% 안)."""
        editor, proj = self._proj()
        # 정답 영상의 서로 다른 장면을 골라 이어 붙이고, 무늬 장면 중간에서 125% 확대(펀치인)
        segs = [(0.5, 3.5, 100), (4.6, 5.8, 100), (5.8, 7.3, 125), (11.5, 13.6, 100), (14.8, 17.8, 100), (19.0, 20.8, 100),
                (21.6, 24.4, 100), (25.5, 27.5, 100), (8.0, 10.5, 100)]
        items, pos = [], 0.0
        for a, b, sc in segs:
            pr = editor._pair(a, b, start=pos)
            if sc != 100:
                pr[0]["fx"] = {"scale": {"v": float(sc), "k": []}}
            items += pr
            pos += b - a
        seq = editor._new_seq("정답 편집본", "long", items, captionStyle=dict(editor.LONG_STYLE), captionsOn=False,
                              layout={"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0},
                              master={"volume": 1.0, "normalize": True, "lufs": -14.0})
        sp = style.profile_from_sequence(proj, seq)
        self.assertEqual(set(sp), OLD_KEYS | {"shotDeciles", "pauseDeciles", "curve3"})
        self.assertEqual(sp["cutsPerMin"], round(8 * 60 / pos, 2))
        self.assertEqual(sp["zoomCutsPerMin"], round(60 / pos, 2))
        self.assertEqual(sp["avgZoom"], 1.25)
        self.assertEqual(sp["lufs"], -14.0)
        out = editor.export(MAIN, dict(seq, **proj), {"preset": "small", "srt": False, "xml": False, "hw": False}, lambda m: None)
        shutil.move(str(core.OUT / out[0]), str(core.VIDEOS / "내보낸 편집본.mp4"))
        rp = style.summarize(style.extract_events("내보낸 편집본.mp4", lambda m: None))
        self.assertLessEqual(abs(sp["cutsPerMin"] - rp["cutsPerMin"]) / rp["cutsPerMin"], 0.10, (sp, rp))
        self.assertAlmostEqual(rp["avgZoom"], 1.25, delta=0.05)
        self.assertEqual(rp["pauses"], sp["pauses"], (sp["pauses"], rp["pauses"]))
        self.assertAlmostEqual(rp["pauseP75"], sp["pauseP75"], delta=0.1)
        self.assertAlmostEqual(rp["lufs"], sp["lufs"], delta=1.5)
        self.assertGreaterEqual(style.distance(rp, sp)["parts"]["컷 리듬"], 85)

    def test_static_source_jump_cuts(self):
        """한 자리에서 계속 찍은 촬영본의 점프 컷·밋밋한 벽 확대는 화면 분석에 컷으로 안 보임 → 편집본 JSON 으로 잰 컷도 똑같이 (검토 #2).
        예전: 클립 경계 8곳을 모두 컷으로 셈(1분에 20.9번) ↔ 내보낸 영상 화면 분석은 4번(10.4번)."""
        import editor
        name = "정지 촬영본.mp4"
        fx.make_static(core.VIDEOS / name)
        style.extract_events(name, lambda m: None)  # 원본 기록(작은 흑백 화면 포함)
        editor_, proj = self._proj(name)
        # 앞 장면(밋밋한 벽) 안 점프 컷 ×4(그중 120% 확대·되돌림 2곳), 벽이 바뀜(12), 무늬 벽 125% 확대(15)·되돌림(17), 다시 앞 벽(20)
        segs = [(0.5, 3.0, 100), (4.0, 7.0, 100), (8.0, 10.5, 100), (11.0, 13.0, 120), (14.0, 16.0, 100), (21.0, 24.0, 100),
                (25.0, 27.0, 125), (28.0, 31.0, 100), (5.0, 8.0, 100)]
        items, pos = [], 0.0
        for a, b, sc in segs:
            pr = editor._pair(a, b, start=pos)
            if sc != 100:
                pr[0]["fx"] = {"scale": {"v": float(sc), "k": []}}
            items += pr
            pos += b - a
        seq = editor._new_seq("점프 컷", "long", items, captionStyle=dict(editor.LONG_STYLE), captionsOn=False,
                              layout={"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0},
                              master={"volume": 1.0, "normalize": True, "lufs": -14.0})
        with mock.patch.object(style, "summarize", wraps=style.summarize) as spy:
            sp = style.profile_from_sequence(proj, seq)
            sev = spy.call_args[0][0]
        self.assertEqual(sev["cuts"], [12.0, 15.0, 17.0, 20.0])
        self.assertEqual([(z["t"], z["scale"]) for z in sev["zooms"]], [(15.0, 1.25), (17.0, 0.8)])
        # 원본 기록이 없으면: 같은 영상 안은 크기가 바뀔 때만 컷 (벽이 바뀐 12·20 은 모름 → 점수 매길 때는 먼저 원본을 살펴봄)
        with mock.patch.object(style, "_cached_events", lambda n: None), mock.patch.object(style, "summarize", wraps=style.summarize) as spy:
            style.profile_from_sequence(proj, seq)
            self.assertEqual(spy.call_args[0][0]["cuts"], [8.0, 10.0, 15.0, 17.0])
        out = editor_.export(name, dict(seq, **proj), {"preset": "small", "srt": False, "xml": False, "hw": False}, lambda m: None)
        shutil.move(str(core.OUT / out[0]), str(core.VIDEOS / "내보낸 점프 컷.mp4"))
        rev = style.extract_events("내보낸 점프 컷.mp4", lambda m: None)
        rp = style.summarize(rev)
        self.assertEqual(len(rev["cuts"]), len(sev["cuts"]), (sev["cuts"], rev["cuts"]))
        self.assertTrue(all(any(abs(c - x) <= 0.3 for x in rev["cuts"]) for c in sev["cuts"]), (sev["cuts"], rev["cuts"]))
        self.assertLessEqual(abs(sp["cutsPerMin"] - rp["cutsPerMin"]) / rp["cutsPerMin"], 0.10, (sp, rp))
        self.assertEqual(sp["zoomCutsPerMin"], rp["zoomCutsPerMin"])

    def test_sequence_rules(self):
        """빈 곳·이어지는 원본·키프레임 순간 확대·B롤·제목·받아쓰기 공백 (파일 없이 JSON 만)."""
        import editor
        p = lambda a, b, start: editor._pair(a, b, start=start)
        v = []
        v += p(0.0, 4.0, 0.0)           # 0~4
        v += p(4.0, 8.0, 4.0)           # 원본이 그대로 이어짐 → 컷 아님
        z = p(8.0, 12.0, 8.0)           # 원본은 이어지지만 120% → 확대 컷
        z[0]["fx"] = {"scale": {"v": 120.0, "k": []}}
        v += z
        v += p(20.0, 24.0, 12.0)        # 원본이 건너뜀 → 컷 (12) · 120%→100% 라 축소 컷도 (원본 기록이 없으면 같은 장면으로 봄)
        k = p(30.0, 36.0, 16.0)         # 같은 크기로 원본만 건너뜀(16) → 원본 기록이 없으면 한 자리에서 찍은 촬영본으로 보고 컷 아님
        #                                 · 키프레임으로 0.1초 만에 100→130% (19초)
        k[0]["fx"] = {"scale": {"v": 100.0, "k": [{"t": 32.9, "v": 100.0}, {"t": 33.0, "v": 130.0}]}}
        v += k
        v += p(40.0, 44.0, 22.5)        # 22~22.5 빈 곳(검은 화면) → 22 · 22.5 둘 다 컷 (화면 분석도 0.5초 간격이면 둘 다 잡음)
        broll = {"id": "b1", "media": "m2", "track": "V2", "start": 5.0, "in": 0.0, "out": 2.0, "speed": 1.0, "fx": {}}
        logo = {"id": "lg", "media": "m3", "track": "V3", "start": 0.0, "in": 0.0, "out": 26.5, "speed": 1.0, "fx": {}}
        seq = editor._new_seq("규칙", "long", v + [broll, logo], captionStyle=dict(editor.LONG_STYLE, y=0.9, fill="#FFD400"),
                              titles=[{"id": "t", "text": "제목", "start": 0.0, "dur": 3.0, "style": dict(editor.TITLE_STYLE)}])
        caps = [{"start": 1.0, "end": 3.0, "text": "가나다라마바"}, {"start": 3.5, "end": 7.0, "text": "말 사이 공백 0.5초"},
                {"start": 20.5, "end": 23.5, "text": "점프 컷 뒤"}]
        proj = {"source": "없는 영상.mp4", "info": {"duration": 60.0}, "captions": caps,
                "media": [{"id": "main", "kind": "video", "file": "없는 영상.mp4"}, {"id": "m2", "kind": "video", "file": "b.mp4", "src": "media"},
                          {"id": "m3", "kind": "image", "file": "logo.png", "src": "media"}]}
        with mock.patch.object(style, "summarize", wraps=style.summarize) as spy:
            prof = style.profile_from_sequence(proj, seq)
            ev = spy.call_args[0][0]
        self.assertEqual(ev["cuts"], [5.0, 7.0, 8.0, 12.0, 19.0, 22.0, 22.5])
        self.assertEqual([(z["t"], z["scale"]) for z in ev["zooms"]], [(8.0, 1.2), (12.0, 0.833), (19.0, 1.3)])
        self.assertEqual(prof["avgZoom"], 1.25)
        self.assertAlmostEqual(ev["duration"], 26.5)
        # 자막: 받아쓰기(아래 · 노랑 계열) 1~3, 3.5~7, 12.5~15.5 / 제목(위) 0~3
        self.assertAlmostEqual(ev["captionBands"]["bottom"], (2.0 + 3.5 + 3.0) / 26.5, places=3)
        self.assertAlmostEqual(ev["captionBands"]["top"], 3.0 / 26.5, places=3)
        self.assertEqual((prof["captionPos"], prof["captionColor"]), ("bottom", "#FFD400"))  # 편집본 색은 설정값 그대로 (어림하지 않음)
        # 공백(받아쓰기 기준): 0~1, 3~3.5, 7~12.5, 15.5~22, 22.5~26.5 (22~22.5 는 빈 곳이라 어차피 조용)
        self.assertEqual(ev["silences"], [[0.0, 1.0], [3.0, 3.5], [7.0, 12.5], [15.5, 26.5]])
        self.assertEqual(prof["lufs"], -14.0)
        self.assertAlmostEqual(prof["charsPerSec"], round((6 + 9 + 4) / (2.0 + 3.5 + 3.0), 2), delta=0.01)
        # 숨긴 트랙·음소거는 빼고 봄
        seq2 = dict(seq, tracks=[dict(t, hide=True) if t["id"] == "V2" else dict(t, mute=True) if t["id"] == "A1" else t for t in seq["tracks"]])
        with mock.patch.object(style, "summarize", wraps=style.summarize) as spy:
            style.profile_from_sequence(proj, seq2)
            ev2 = spy.call_args[0][0]
        self.assertEqual(ev2["cuts"], [8.0, 12.0, 19.0, 22.0, 22.5])
        self.assertEqual(ev2["silences"], [[0.0, 26.5]])


class TestScoreRoute(StyleFixtureBase):
    def test_api_style_score(self):
        from http.server import ThreadingHTTPServer
        import app
        style.learn("정답 스타일", [MAIN, COPY], lambda m: None)
        saved = json.loads((style.STYLES / "정답 스타일.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved["refs"]), 2)
        self.assertNotIn("text", json.dumps(saved), "기록(이벤트)은 스타일 파일에 넣지 않음")
        (core.adir(MAIN) / "transcript.json").write_text(json.dumps(speech_segments(), ensure_ascii=False), encoding="utf-8")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        def call(body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/style/score", method="POST", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        with mock.patch.object(app, "PORT", port):
            code, j = call({"style": "정답 스타일", "name": MAIN})
            self.assertEqual(code, 200, j)
            self.assertTrue(j["ok"])
            self.assertTrue(0 <= j["score"] <= 100)
            self.assertEqual(list(j["parts"]), ["컷 리듬", "확대", "공백", "자막", "소리"])
            self.assertGreaterEqual(j["parts"]["자막"], 90, "가편집 자막이 스타일대로 아래·노랑")
            self.assertEqual((j["rough"]["captionPos"], j["rough"]["captionColor"]), ("bottom", "#FFE14D"))
            self.assertEqual(call({"style": "없는 스타일", "name": MAIN})[0], 404)
            code, j = call({"style": "정답 스타일", "name": TOP})  # 편집점 찾기 안 한 영상
            self.assertEqual(code, 400)
            self.assertIn("편집점 찾기", j["error"])
            self.assertEqual(call({"style": "정답 스타일", "name": "..\\밖.mp4"})[0], 400)
            self.assertEqual(call({"style": "정답 스타일", "name": "없는 영상.mp4"})[0], 400)



def serve_api(tc):
    """앱 서버를 빈 포트로 띄우고 POST 호출 함수를 돌려줌 (테스트가 끝나면 닫음)."""
    from http.server import ThreadingHTTPServer
    import app
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tc.addCleanup(srv.server_close)
    tc.addCleanup(srv.shutdown)
    p = mock.patch.object(app, "PORT", port)
    p.start()
    tc.addCleanup(p.stop)

    def call(path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    return app, call


class TestScoreFixes(StyleFixtureBase):
    """검토에서 나온 점수 문제들."""

    def test_loudness_cannot_dominate(self):
        """#1 레퍼런스끼리 소리 크기만 한결같으면 1/표준편차 무게가 소리에 몰림 → 한 부분 무게는 W_CAP 까지.
        가편집은 소리 크기를 스타일 값으로 맞추기만 하므로(늘 100점) 보여 주기만 하고 전체 점수에서는 뺌."""
        p = self.prof
        a = dict(p, lufs=-14.2)
        b = dict(p, lufs=-13.8, shotDeciles=[q * 1.7 for q in p["shotDeciles"]], zoomCutsPerMin=0.0, captionRatio=0.45,
                 pauseDeciles=[q * 1.6 for q in p["pauseDeciles"]], silenceRatio=p["silenceRatio"] * 0.5)
        c = dict(p, lufs=-14.5, shotDeciles=[q * 0.6 for q in p["shotDeciles"]], zoomCutsPerMin=p["zoomCutsPerMin"] * 3,
                 captionRatio=0.2, captionPos="top", pauseDeciles=[q * 0.7 for q in p["pauseDeciles"]])
        target = dict(style.merge([a, b, c]), refs=[a, b, c])
        w = style.distance(target, a)["weights"]
        self.assertAlmostEqual(w["소리"], style.W_CAP, delta=0.002, msg=w)  # 상한이 없으면 0.5 넘게 소리 혼자
        self.assertLessEqual(max(w.values()), style.W_CAP + 0.002)
        self.assertAlmostEqual(sum(w.values()), 1.0, delta=0.01)
        # 같은 영상 둘(소리 크기 거의 같음)로 배운 스타일 → 가편집 점수에서 소리는 '스타일대로'
        style.learn("소리 스타일", [MAIN, COPY], lambda m: None)
        (core.adir(MAIN) / "transcript.json").write_text(json.dumps(speech_segments(), ensure_ascii=False), encoding="utf-8")
        r = style.score_video("소리 스타일", MAIN)
        self.assertEqual(r["fixed"], ["소리"])
        self.assertEqual((r["parts"]["소리"], r["weights"]["소리"]), (100, 0.0))
        used = [k for k, v in r["weights"].items() if v]
        self.assertAlmostEqual(sum(r["weights"].values()), 1.0, delta=0.01)
        self.assertAlmostEqual(r["score"], sum(r["weights"][k] * r["parts"][k] for k in used), delta=1.0)

    def test_rough_cut_follows_style_switches(self):
        """#3 스타일이 '자막 적게'·'확대 거의 없음'이면 가편집은 안 함 → 안 한 것이 맞음. 짧은 가편집은 확대 컷 한 번 차이를 봐줌."""
        st = dict(self.prof, captionRatio=0.2, zoomCutsPerMin=0.2)
        prm = style.edit_params(st)
        self.assertEqual((prm["captions"], prm["zoomEvery"]), (False, 0))
        rough = dict(self.prof, captionRatio=0.0, captionBands={"top": 0.0, "middle": 0.0, "bottom": 0.0}, zoomCutsPerMin=0.0, duration=28.0)
        r = style.distance(st, rough, params=prm)
        self.assertEqual((r["parts"]["자막"], r["parts"]["확대"]), (100, 100))
        self.assertEqual(style.distance(st, rough)["parts"]["자막"], 0, "가편집이 아닌 영상끼리는 그대로 비교")
        st2 = dict(self.prof, zoomCutsPerMin=round(60 / 58.3, 2))  # 58초마다 확대
        p2 = style.edit_params(st2)
        self.assertGreater(p2["zoomEvery"], 0)
        self.assertEqual(style.distance(st2, rough, params=p2)["parts"]["확대"], 100, "28초에 0번 = 기대 0.5번")
        self.assertLess(style.distance(st2, dict(rough, duration=600.0), params=p2)["parts"]["확대"], 20, "10분에 0번은 다름")

    def test_merge_caption_positions(self):
        """#4 영상마다 자막 위치가 달라도 합친 스타일의 자막 비율은 영상마다 잰 비율의 길이 평균 (위치별로 나눠 줄지 않음)."""
        top = style.extract_events(TOP, lambda m: None)
        none = dict(self.ev, duration=45.0, text=[[0] * 6 for _ in self.ev["text"]], capColors=[])  # 자막 없는 45초 영상
        evs = [self.ev, top, none, dict(none)]
        profs = [style.summarize(e) for e in evs]
        m = style.merge(profs, evs)
        want = sum(p["captionRatio"] * e["duration"] for p, e in zip(profs, evs)) / sum(e["duration"] for e in evs)
        self.assertAlmostEqual(m["captionRatio"], want, delta=0.01)
        self.assertLess(max(m["captionBands"].values()), 0.25, "위치별로는 25%가 안 됨 (예전에는 이 값으로 자막을 껐음)")
        self.assertTrue(style.edit_params(m)["captions"])

    def test_api_friendly_errors_and_first_look_job(self):
        """#5 화면에는 한국어 안내만 · 원본을 처음 보는 영상은 작업으로 살펴본 뒤 점수 (#2)."""
        app, call = serve_api(self)
        style.learn("오류 스타일", [MAIN], lambda m: None)
        bad = "받아쓰기 깨진 영상.mp4"
        shutil.copy2(core.VIDEOS / MAIN, core.VIDEOS / bad)
        core.adir(bad).mkdir(parents=True, exist_ok=True)
        (core.adir(bad) / "transcript.json").write_text('[{"start": 1.0, "end"', encoding="utf-8")  # 편집점 찾기가 쓰는 도중
        code, j = call("/api/style/score", {"style": "오류 스타일", "name": bad})
        self.assertEqual((code, j["error"]), (400, "받아쓰기 파일을 읽지 못했어요. 편집점 찾기를 다시 해 주세요"))
        with mock.patch.object(style, "score_video", side_effect=RuntimeError("Expecting value: line 1 column 1 (char 0)")), \
                mock.patch.object(app.traceback, "print_exc"), mock.patch.object(app, "log") as lg:
            code, j = call("/api/style/score", {"style": "오류 스타일", "name": MAIN})
        self.assertIn("Expecting value", lg.call_args[0][0], "자세한 내용은 작업 기록에")
        self.assertEqual(code, 500)
        self.assertNotIn("Expecting", j["error"])
        self.assertIn("작업 기록", j["error"])
        # 원본 기록이 없는 영상: 바로 매기지 않고 작업으로 (원본 화면 살펴보기 → 점수), 결과는 작업 결과로
        fresh = "처음 보는 영상.mp4"
        shutil.copy2(core.VIDEOS / MAIN, core.VIDEOS / fresh)
        core.adir(fresh).mkdir(parents=True, exist_ok=True)
        (core.adir(fresh) / "transcript.json").write_text(json.dumps(speech_segments(), ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(style._load_events(fresh))
        code, j = call("/api/style/score", {"style": "오류 스타일", "name": fresh})
        self.assertEqual((code, j), (200, {"ok": True, "job": True, "error": None}))
        t0 = time.time()
        while app.JOB["name"] and time.time() - t0 < 180:
            time.sleep(0.2)
        r = app.JOB["result"]
        self.assertTrue(r["ok"], r)
        self.assertTrue(0 <= r["score"] <= 100)
        self.assertEqual(r["fixed"], ["소리"])
        self.assertIsNotNone(style._load_events(fresh), "원본 기록이 남음 → 다음에는 바로")
        code, j = call("/api/style/score", {"style": "오류 스타일", "name": fresh})
        self.assertEqual((code, j["score"], j.get("job")), (200, r["score"], None))


class TestScoreReview2(StyleFixtureBase):
    """두 번째 검토: 소리 '스타일대로' 판정 · 가편집 자막 색 그대로 · 멈추기와 실패 기록."""

    def _style(self, nm, **kw):
        style.learn(nm, [MAIN], lambda m: None)
        f = style.STYLES / f"{nm}.json"
        d = json.loads(f.read_text(encoding="utf-8"))
        d.update(kw)
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        (core.adir(MAIN) / "transcript.json").write_text(json.dumps(speech_segments(), ensure_ascii=False), encoding="utf-8")

    def test_loudness_fixed_on_rounding_and_clamp(self):
        """반올림 경계(-16.45·-19.95)·범위 밖(-7.5·-26)이어도 가편집은 스타일 값을 그대로 쓰므로 '스타일대로'."""
        for lufs in (-16.45, -19.95, -19.75, -7.5, -26.0):
            self._style("소리 경계 스타일", lufs=lufs)
            r = style.score_video("소리 경계 스타일", MAIN)
            self.assertEqual(r["fixed"], ["소리"], lufs)
            self.assertEqual(r["weights"]["소리"], 0.0, lufs)

    def test_rough_caption_color_not_snapped(self):
        """예전 스타일의 노르스름한 색(#E6D98C)을 가편집이 그대로 쓰면 자막 색도 맞음 (어림 색으로 바꾸지 않음)."""
        self._style("집 노랑 스타일", captionPos="bottom")
        base = style.score_video("집 노랑 스타일", MAIN)
        self._style("옅은 노랑 스타일", captionColor="#E6D98C", captionPos="bottom")
        r = style.score_video("옅은 노랑 스타일", MAIN)
        self.assertEqual(r["rough"]["captionColor"], "#E6D98C")
        self.assertEqual(r["parts"]["자막"], base["parts"]["자막"], "색 차이로 깎이지 않음 (자막 비율 차이만)")

    def test_cancel_and_failure_marker(self):
        """처음 보는 영상 살펴보기: 멈추기(✕)로 그만둠 · 실패하면 같은 파일은 다시 긴 작업을 돌리지 않음."""
        import editor
        style.learn("멈춤 스타일", [MAIN], lambda m: None)
        nm = "멈춤 테스트 영상.mp4"
        shutil.copy2(core.VIDEOS / MAIN, core.VIDEOS / nm)
        core.adir(nm).mkdir(parents=True, exist_ok=True)
        (core.adir(nm) / "transcript.json").write_text(json.dumps(speech_segments(), ensure_ascii=False), encoding="utf-8")
        editor.CANCEL.set()
        try:
            with self.assertRaises(style.StyleCancelled):
                style.score_video("멈춤 스타일", nm, analyze=True)
        finally:
            editor.CANCEL.clear()
        self.assertIsNone(style._load_events(nm))
        self.assertFalse(style._failed_before(nm), "멈춘 것은 실패로 남기지 않음")
        with self.assertRaises(style.NeedsAnalysis):
            style.score_video("멈춤 스타일", nm)
        with mock.patch.object(style, "extract_events", side_effect=RuntimeError("화면을 읽지 못했어요")) as ex:
            r = style.score_video("멈춤 스타일", nm, log=lambda m: None, analyze=True)
            self.assertTrue(0 <= r["score"] <= 100)
            self.assertEqual(ex.call_count, 1)
            r2 = style.score_video("멈춤 스타일", nm)  # 실패 기록 → 작업 없이 바로 어림으로
            self.assertEqual(r2["score"], r["score"])
            self.assertEqual(ex.call_count, 1)
        self.assertTrue(style._failed_before(nm))
        os.utime(core.VIDEOS / nm, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))  # 파일이 바뀌면 다시 살펴봄
        self.assertFalse(style._failed_before(nm))


class TestScoreReview3(StyleFixtureBase):
    """리뷰 3차: 컷이 거의 없는 영상끼리의 컷 리듬 · 실패 기록이 영원히 남지 않음 · 어림 점수 표시."""

    @staticmethod
    def _prof(dur, cuts):
        return style.summarize({"source": "x.mp4", "duration": dur, "cuts": cuts, "zooms": [], "silences": [], "text": []})

    def test_no_cut_videos_match_regardless_of_length(self):
        """컷 없는 60초 ↔ 컷 없는 25초(가편집이 더 짧아도): 컷 리듬은 맞음 (길이 차이로 깎이지 않음)."""
        a, b = self._prof(60, []), self._prof(25, [])
        r = style.distance(a, b)
        self.assertGreaterEqual(r["parts"]["컷 리듬"], 90)
        self.assertGreaterEqual(r["score"], 90)
        self.assertGreaterEqual(style.distance(self._prof(3600, []), b)["parts"]["컷 리듬"], 90, "60분 한 컷 스타일")
        self.assertGreaterEqual(style.distance(a, self._prof(25, [12.0]))["parts"]["컷 리듬"], 90, "짧은 영상의 컷 한 번 차이는 봐줌")
        busy = self._prof(60, [k * 2.0 for k in range(1, 30)])  # 2초마다 컷
        self.assertLess(style.distance(busy, b)["parts"]["컷 리듬"], 30, "컷 많은 스타일 ↔ 컷 없는 가편집은 여전히 낮음")
        self.assertLess(style.distance(a, self._prof(60, [k * 2.0 for k in range(1, 30)]))["parts"]["컷 리듬"], 30)
        old = {k: v for k, v in a.items() if k not in ("shotDeciles", "pauseDeciles")}  # 예전 프로필(평균값만)도 같게
        self.assertGreaterEqual(style.distance(old, b)["parts"]["컷 리듬"], 90)

    def test_failure_marker_transient_and_expiry(self):
        """파일이 잠깐 잠김(OSError)은 실패로 남기지 않음 · 실패 기록은 FAIL_TTL 뒤 잊음 · 어림 점수는 guessed."""
        style.learn("잠김 스타일", [MAIN], lambda m: None)
        nm = "잠김 테스트 영상.mp4"
        shutil.copy2(core.VIDEOS / MAIN, core.VIDEOS / nm)
        core.adir(nm).mkdir(parents=True, exist_ok=True)
        (core.adir(nm) / "transcript.json").write_text(json.dumps(speech_segments(), ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(style, "extract_events", side_effect=PermissionError(13, "다른 프로그램이 쓰는 중")) as ex:
            r = style.score_video("잠김 스타일", nm, log=lambda m: None, analyze=True)
            self.assertTrue(r["guessed"])
            self.assertFalse(style._failed_before(nm), "잠김은 잠깐일 수 있음")
            with self.assertRaises(style.NeedsAnalysis):
                style.score_video("잠김 스타일", nm)
        with mock.patch.object(style, "extract_events", side_effect=RuntimeError("영상 화면을 읽지 못했어요")):
            style.score_video("잠김 스타일", nm, log=lambda m: None, analyze=True)
        self.assertTrue(style._failed_before(nm))
        self.assertTrue(style.score_video("잠김 스타일", nm)["guessed"])
        with mock.patch.object(style.time, "time", return_value=time.time() + style.FAIL_TTL + 5):
            self.assertFalse(style._failed_before(nm), "시간이 지나면 다시 살펴봄")
        (core.adir(MAIN) / "transcript.json").write_text(json.dumps(speech_segments(), ensure_ascii=False), encoding="utf-8")
        self.assertFalse(style.score_video("잠김 스타일", MAIN)["guessed"], "살펴본 영상은 어림이 아님")


if __name__ == "__main__":
    unittest.main()
