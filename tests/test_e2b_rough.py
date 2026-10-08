"""E2 자동 편집 결과물 품질 묶음 — 저장소 폴더에서 python3 -m unittest tests.test_e2b_rough

- 받아쓰기 헛것 거르기 (captions.hallucinations · drop_hallucinations · BR-090)
- 낱말 단위 가편집 + 말 없는 시범·환호 살리기 · 한 줄 안의 NG (editor.recommend · gap_demos · BR-091)
- 쇼츠 = 포인트 하나 · 인사/끝인사 빼기 (BR-092) · 쇼츠 위 큰 제목 (short_hooks · BR-093)
- 첫 가편집에 쓰는 스타일 (style.rough_default · BR-094)
- 배경음악 줄이기는 받아쓴 말 자리만 (editor.speech_spans · 내보내기 · BR-095)
- 썸네일 장면은 원본 크기 (thumb.grab · BR-096) · 다른 채널 영상 키트 (upload.other_sources · BR-097)
고정 자료: tests/fixtures/rough_e12.json (v2.9.0 앱으로 실제 받아쓴 MSGRAW01~03·LESSON04 · 정답 시범 구간)."""
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import captions  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import style  # noqa: E402
import upload  # noqa: E402

FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "rough_e12.json").read_text(encoding="utf-8"))["videos"]
FF = core.ffmpeg()


def W(*items, p=0.9):
    """('말', 시작, 끝[, 확신]) … → 낱말 목록."""
    return [{"w": x[0], "s": x[1], "e": x[2], "p": x[3] if len(x) > 3 else p} for x in items]


def S(words):
    return {"start": words[0]["s"], "end": words[-1]["e"], "text": " ".join(w["w"] for w in words), "words": words}


class WorkMixin:
    """임시 작업 폴더 (받아쓰기·분석 파일만 · 영상은 없음)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="E2 가편집 "))
        work = self.tmp / "작업"
        self.dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.p = mock.patch.multiple(core, **self.dirs)
        self.p.start()
        self.s = mock.patch.object(style, "STYLES", work / "styles")
        self.s.start()

    def tearDown(self):
        self.s.stop()
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, segs, analysis=None):
        d = core.adir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps(analysis or {"silences": [], "loud_peaks": []}, ensure_ascii=False), encoding="utf-8")
        return name


def kept(cuts, a, b):
    """[a, b] 중 컷 안에 든 길이(초)."""
    return sum(max(0.0, min(b, c["out"]) - max(a, c["in"])) for c in cuts)


class HallucinationTest(unittest.TestCase):
    def test_repeated_word_run_keeps_two(self):
        ws = W(("하나,", 115.0, 115.4), ("둘,", 115.5, 115.9), ("셋,", 116.0, 116.4)) + \
            [{"w": "다섯,", "s": 117.0 + 0.04 * k, "e": 117.04 + 0.04 * k, "p": 0.5} for k in range(25)]
        bad = captions.hallucinations(ws)
        self.assertEqual(sorted(bad), list(range(5, 28)))  # '다섯' 25번 중 앞 2개만 남김 · 구령 '하나, 둘, 셋'은 그대로

    def test_low_confidence_collapsed_word(self):
        ws = W(("다시", 71.58, 71.58, 0.0), ("해볼게요.", 71.6, 72.3, 0.95))
        self.assertEqual(captions.hallucinations(ws), {0})

    def test_collapsed_but_confident_real_speech_stays(self):
        """MSGRAW02 '이렇게 패스와 동시에' 는 시각만 무너진 진짜 말 (가까운 곳에 같은 글이 이어져 있지 않음)."""
        segs = FIX["MSGRAW02"]["transcript"]
        out, flags = captions.drop_hallucinations(segs, FIX["MSGRAW02"]["analysis"]["silences"])
        self.assertNotIn("동시에", " ".join(f[2] for f in flags))
        self.assertIn("paced...Pacific...", [f[2] for f in flags])  # 조용한 끝 + 영어 찌꺼기
        self.assertIn("다시", [f[2] for f in flags])                # 길이 0 · 확신 0 의 그림자
        self.assertTrue(any("동시에" in s["text"] for s in out))

    def test_ghost_cluster_duplicating_nearby_words(self):
        """LESSON04 60.83초 한 점에 몰린 13낱말 = 바로 뒤 문장을 겹쳐 쓴 그림자 → 통째로 뺌."""
        segs = FIX["LESSON04"]["transcript"]
        out, flags = captions.drop_hallucinations(segs, FIX["LESSON04"]["analysis"]["silences"])
        ghost = [f for f in flags if f[0] >= 60.8 and f[1] <= 60.9]
        self.assertTrue(ghost and "원터치로" in " ".join(f[2] for f in ghost))
        self.assertFalse([w for s in out for w in s.get("words") or () if abs(w["s"] - 60.83) < 1e-3 and w["e"] - w["s"] < 1e-3])

    def test_latin_and_silence_words(self):
        ws = W(("functioning.", 112.78, 113.64, 0.0), ("OK", 114.0, 114.3, 0.2), ("감사합니다", 201.2, 201.38, 0.4), ("나이스!", 123.9, 124.5, 0.95))
        sil = [{"start": 200.3, "end": 202.3}]
        self.assertEqual(captions.hallucinations(ws, sil), {0, 2})  # 'OK'(2글자)는 남김 · 조용한 곳 위 '감사합니다'는 뺌

    def test_word_at_silence_edge_stays(self):
        """조용한 곳 가장자리에 걸친 낱말은 시각이 조금 어긋난 진짜 말일 수 있음 ('세' … '번째 포인트')."""
        ws = W(("세", 131.85, 132.93, 0.19))
        self.assertEqual(captions.hallucinations(ws, [{"start": 132.03, "end": 136.62}]), set())

    def test_drop_rebuilds_text_and_drops_empty(self):
        segs = [S(W(("나이스!", 1.0, 1.5), ("다섯", 2.0, 2.0, 0.0))), S(W(("paced...", 5.0, 6.0, 0.1)))]
        out, flags = captions.drop_hallucinations(segs)
        self.assertEqual([s["text"] for s in out], ["나이스!"])
        self.assertEqual(len(flags), 2)

    def test_segments_without_words_latin_line(self):
        out, flags = captions.drop_hallucinations([{"start": 0, "end": 2, "text": "paced...Pacific..."}, {"start": 3, "end": 4, "text": "좋아요"}])
        self.assertEqual([s["text"] for s in out], ["좋아요"])
        self.assertEqual(flags[0][2], "paced...Pacific...")


class GapDemoTest(unittest.TestCase):
    def test_gap_with_sound_is_a_demo(self):
        words = [(0.0, 5.0), (12.0, 13.0), (20.0, 21.0)]
        d = editor.gap_demos(words, [9.0, 10.5])
        self.assertEqual(d, [(6.0, 11.9)])  # 첫 소리 3초 앞(준비 동작) ~ 마지막 소리 2초 뒤, 틈 안으로 · 소리 없는 13~20초 쉼은 없음

    def test_far_apart_sounds_make_two_demos(self):
        d = editor.gap_demos([(0.0, 1.0), (40.0, 41.0)], [5.0, 30.0])
        self.assertEqual(len(d), 2)

    def test_no_demo_before_first_or_after_last_word(self):
        self.assertEqual(editor.gap_demos([(10.0, 11.0), (12.5, 13.0)], [2.0, 20.0]), [])


class WordCutTest(WorkMixin, unittest.TestCase):
    def rec(self, key, onsets):
        name = self.put("v.mp4", FIX[key]["transcript"], FIX[key]["analysis"])
        with mock.patch.object(editor, "_sound_onsets", lambda n, w: onsets):
            return editor.recommend(name)

    def test_idle_gaps_cut_demos_kept(self):
        """MSGRAW03: 한 받아쓰기 구간(0.82~42초) 안의 말 없는 틈 — 공 소리가 난 시범은 남기고 나머지 쉼은 자름."""
        demos = FIX["MSGRAW03"]["demos"]
        r = self.rec("MSGRAW03", [k for _, _, ks in demos for k in ks])
        for a, b, _ in demos:
            self.assertGreaterEqual(kept(r["tidy"], a, b), 0.8 * (b - a) - 0.5, (a, b))
        self.assertLess(kept(r["tidy"], 45.6, 49.4), 0.3)  # 공 소리 없는 '공 가져오기' 쉼 (42.4~45초 웃음은 큰 소리 봉우리로 남음)
        self.assertTrue(r["demos"])

    def test_without_sound_keeps_old_behaviour(self):
        """영상 소리를 못 읽으면(파형 없음) 같은 받아쓰기 구간 안 빈 곳은 예전처럼 남김 (시범을 못 찾으니 자르지 않음)."""
        r = self.rec("MSGRAW03", None)
        self.assertGreater(kept(r["tidy"], 12.7, 16.7), 3.5)
        self.assertGreater(kept(r["tidy"], 28.5, 37.5), 8.5)  # 같은 구간 안 '나이스.' 뒤 시범
        self.assertEqual(r["demos"], [])

    def test_restart_inside_one_line_is_cut(self):
        """MSGRAW02 '이렇게 패스와 동작. 이렇게 패스와 동시에 …' — 고쳐 다시 한 앞말은 뺌 (한 받아쓰기 구간 안)."""
        r = self.rec("MSGRAW02", [27.0, 28.93, 56.63, 59.17, 74.26, 101.5])
        whys = {j["why"] for j in r["junk_list"]}
        self.assertIn("고쳐 다시 한 말", whys)
        self.assertIn(captions.HALLU_WHY, whys)
        j = next(j for j in r["junk_list"] if j["why"] == "고쳐 다시 한 말")
        self.assertLess(kept(r["tidy"], j["a"] + 0.2, j["b"] - 0.1), 0.05)

    def test_slate_inside_line(self):
        """'두 번째 동작은 패스하고 옆으로 아니다. 다시 할게요.' 뒤 다시 찍은 테이크 → 앞 테이크와 슬레이트 말을 뺌."""
        r = self.rec("MSGRAW02", [])
        self.assertLess(kept(r["tidy"], 42.0, 46.8), 0.3)
        self.assertGreater(kept(r["tidy"], 48.2, 53.3), 5.0)

    def test_hallucinated_latin_line_not_in_rough_cut(self):
        r = self.rec("MSGRAW02", [])
        self.assertLess(kept(r["tidy"], 114.2, 115.7), 0.05)
        self.assertTrue(all(c["out"] <= 114.2 for s in r["shorts"] for c in s["cuts"]))

    def test_ghost_repeat_sentence(self):
        """MSGRAW03 '네 번째' '빗나갔어요.' 뒤 길이 0 낱말로 겹쳐 쓴 '네번째 빗나갔어요.' 는 군더더기."""
        r = self.rec("MSGRAW03", [])
        self.assertLess(kept(r["tidy"], 60.95, 61.05), 0.05)

    def test_style_pause_applies_inside_a_line(self):
        """배운 스타일의 말 사이 공백(keepPause)이 받아쓰기 구간 안에서도 반영됨 (예전에는 구간 사이만)."""
        name = self.put("v.mp4", FIX["MSGRAW01"]["transcript"], FIX["MSGRAW01"]["analysis"])
        with mock.patch.object(editor, "_sound_onsets", lambda n, w: []):
            a = sum(c["out"] - c["in"] for c in editor.recommend(name)["tidy"])
            b = sum(c["out"] - c["in"] for c in editor.recommend(name, keep_pause=0.36)["tidy"])
        self.assertLess(b, a - 1.0)


class ShortsTest(WorkMixin, unittest.TestCase):
    def rec(self, key):
        name = self.put("v.mp4", FIX[key]["transcript"], FIX[key]["analysis"])
        with mock.patch.object(editor, "_sound_onsets", lambda n, w: []):
            return editor.recommend(name), FIX[key]["transcript"]

    def test_one_point_per_short(self):
        """MSGRAW01 의 쇼츠 3개 = 포인트 3개 (각 쇼츠가 '첫/두/세 번째 포인트'로 시작 · 다음 포인트로 넘어가지 않음)."""
        r, _ = self.rec("MSGRAW01")
        starts = sorted(round(s["start"]) for s in r["shorts"])
        self.assertEqual(starts, [24, 88, 138])
        for s in r["shorts"]:
            self.assertTrue(editor.SECTION.match(s["title"]) or s["start"] in (24.24, 87.54, 137.62), s["title"])

    def test_no_greeting_or_closing_in_shorts(self):
        for key in ("MSGRAW01", "MSGRAW02", "MSGRAW03", "LESSON04"):
            r, segs = self.rec(key)
            words = [w for x in segs for w in x.get("words") or ()]
            for s in r["shorts"]:
                text = " ".join(w["w"] for w in words if w["e"] - w["s"] > 0.01 and any(c["in"] <= (w["s"] + w["e"]) / 2 <= c["out"] for c in s["cuts"]))
                for bad in ("안녕하세요", "감사합니다", "여기까지"):
                    self.assertNotIn(bad, text, (key, s["start"]))

    def test_hooks_are_unique_and_whole(self):
        for key in ("MSGRAW01", "MSGRAW02", "MSGRAW03", "LESSON04"):
            r, _ = self.rec(key)
            hs = [s["hook"] for s in r["shorts"]]
            self.assertEqual(len(hs), len(set(hs)), hs)
            for h in hs:
                self.assertNotIn("안녕하세요", h)
                self.assertNotIn(editor._norm(h), {editor._norm(g) for g in editor.GENERIC}, h)
        r, _ = self.rec("MSGRAW01")
        self.assertIn("퍼스트 터치\n첫 번째 포인트", [s["hook"] for s in r["shorts"]])

    def test_hook_cut_at_word_boundary(self):
        self.assertEqual(editor._hook_cut("공이 오기 전에 고개를 들어서 주변을 먼저 보세요."), "공이 오기 전에 고개를 들어서 주변을")  # 16자 안 낱말 경계
        self.assertEqual(editor._hook_cut("패스하고 그 자리에 서 있으면 왜 안될까요?"), "패스하고 그 자리에 서 있으면 왜 안될까요?")
        self.assertEqual(editor._hook_cut("네 번째 빗나갔어요."), "네 번째 빗나갔어요")
        self.assertEqual(editor._hook_cut("자, 첫 번째 갑니다."), "첫 번째 갑니다")

    def test_aside_ball_fetch_not_in_shorts(self):
        self.assertTrue(editor.ASIDE.search("자, 공 좀 가져올게요."))
        self.assertFalse(editor.ASIDE.search("공을 가볍게 가져가세요"))

    def test_old_hook_without_recommend_hook(self):
        self.assertEqual(editor._hook({"keywords": ["퍼스트 터치", "꿀팁"], "title": "x"}), "퍼스트 터치 꿀팁")
        self.assertEqual(editor._hook({"keywords": [], "title": "안녕하세요 오늘은 패스하고 바로 움직이는 연습"}), "안녕하세요 오늘은 패스하고 바로")


class RoughStyleTest(WorkMixin, unittest.TestCase):
    def mkstyle(self, name):
        prof = {"pauseP75": 0.4, "zoomCutsPerMin": 0, "avgZoom": 1.0, "medianShot": 4.0, "captionRatio": 0.6, "captionPos": "bottom",
                "captionColor": "#FFFFFF", "lufs": -27, "avgShot": 4, "cutsPerMin": 15}
        style.STYLES.mkdir(parents=True, exist_ok=True)
        (style.STYLES / f"{name}.json").write_text(json.dumps(prof), encoding="utf-8")

    def test_set_and_clear(self):
        self.assertIsNone(style.rough_default())
        self.mkstyle("도블락")
        self.assertEqual(style.set_rough_default("도블락"), "도블락")
        self.assertEqual(style.rough_default(), "도블락")
        n, p = style.rough_params()
        self.assertEqual(n, "도블락")
        self.assertEqual(p["keepPause"], 0.36)
        self.assertEqual(p["lufs"], -14.0)  # 배운 -27 이 아니라 유튜브 기준 (E5 · D-045)
        self.assertIsNone(style.set_rough_default(None))
        self.assertIsNone(style.rough_default())
        with self.assertRaises(style.StyleMissing):
            style.set_rough_default("없는 스타일")

    def test_deleted_style_falls_back(self):
        self.mkstyle("도블락")
        style.set_rough_default("도블락")
        (style.STYLES / "도블락.json").unlink()
        self.assertIsNone(style.rough_default())
        self.assertIsNone(editor.default_style_params())

    def test_settings_file_not_a_style(self):
        self.mkstyle("도블락")
        style.set_rough_default("도블락")
        self.assertEqual([s["name"] for s in style.list_styles()], ["도블락"])

    def test_first_learned_style_becomes_default_once(self):
        prof = {"pauseP75": 0.4, "zoomCutsPerMin": 0, "avgZoom": 1.0, "medianShot": 4.0, "captionRatio": 0.6, "captionPos": "bottom",
                "captionColor": "#FFFFFF", "lufs": -27, "avgShot": 4, "cutsPerMin": 15}
        with mock.patch.object(style, "extract_events", lambda n, log: {}), mock.patch.object(style, "summarize", lambda ev: dict(prof)), \
                mock.patch.object(style.plan, "extract_plan", side_effect=RuntimeError("x")), mock.patch.object(style, "merge", lambda p, e: dict(prof)), \
                mock.patch.object(style, "split_formats", lambda p, e: None), mock.patch.object(style, "ref_format", lambda n: "long"):
            r1 = style.learn("첫 스타일", ["a.mp4"], lambda m: None)
            r2 = style.learn("둘째", ["a.mp4"], lambda m: None)
        self.assertTrue(r1["rough"])
        self.assertFalse(r2["rough"])
        self.assertEqual(style.rough_default(), "첫 스타일")
        style.set_rough_default(None)  # 끈 뒤로는 새로 배워도 저절로 켜지지 않음
        with mock.patch.object(style, "extract_events", lambda n, log: {}), mock.patch.object(style, "summarize", lambda ev: dict(prof)), \
                mock.patch.object(style.plan, "extract_plan", side_effect=RuntimeError("x")), mock.patch.object(style, "merge", lambda p, e: dict(prof)), \
                mock.patch.object(style, "split_formats", lambda p, e: None), mock.patch.object(style, "ref_format", lambda n: "long"):
            self.assertFalse(style.learn("셋째", ["a.mp4"], lambda m: None)["rough"])
        self.assertIsNone(style.rough_default())

    def test_first_rough_cut_uses_default_style(self):
        self.mkstyle("도블락")
        style.set_rough_default("도블락")
        name = self.put("v.mp4", FIX["MSGRAW03"]["transcript"], FIX["MSGRAW03"]["analysis"])
        info = {"duration": 82.98, "width": 1920, "height": 1080, "fps": 30.0}
        with mock.patch.object(editor, "_sound_onsets", lambda n, w: []):
            seqs = editor.auto_sequences(name, info, editor.default_style_params())
        self.assertTrue(seqs)
        for q in seqs:
            self.assertEqual(q["auto"], "rough")  # '가편집 다시 만들기' 대상 그대로
            self.assertEqual(q["style"], "도블락")
            self.assertEqual(q["master"]["lufs"], -14.0)
        with mock.patch.object(editor, "_sound_onsets", lambda n, w: []):
            plain = editor.auto_sequences(name, info, editor.default_style_params() and None)
        self.assertNotIn("style", plain[0])


def _tone(path, freq, dur):
    r = core.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={freq}:d={dur}", "-ar", "48000", str(path)])
    assert r.returncode == 0, r.stderr


class DuckingTest(unittest.TestCase):
    def seq(self, caps):
        items = [{"id": "d", "track": "A1", "media": "main", "start": 2.0, "in": 10.0, "out": 16.0, "speed": 1, "rev": False, "fx": {}, "gain": 0},
                 {"id": "m", "track": "A2", "media": "bgm", "start": 0.0, "in": 0.0, "out": 8.0, "speed": 1, "rev": False, "fx": {}, "gain": 0}]
        return {"tracks": editor.default_tracks(), "items": items, "captions": caps, "duck": {"on": True, "amount": -14.0}}

    def test_spans_follow_captions_through_clip(self):
        sp, other = editor.speech_spans(self.seq([{"start": 11.0, "end": 12.0, "text": "말"}, {"start": 30.0, "end": 31.0, "text": "밖"}]))
        self.assertEqual(other, [])
        self.assertEqual(len(sp), 1)
        self.assertAlmostEqual(sp[0][0], 2.8, 3)  # 원본 11초 = 타임라인 3초, 앞뒤 0.2초
        self.assertAlmostEqual(sp[0][1], 4.2, 3)

    def test_no_captions_means_old_rule(self):
        self.assertIsNone(editor.speech_spans(self.seq([])))

    def test_sfx_track_and_other_media(self):
        q = self.seq([{"start": 11.0, "end": 12.0, "text": "말"}])
        q["items"].append({"id": "o", "track": "A1", "media": "clip", "start": 9.0, "in": 0, "out": 2, "speed": 1, "rev": False})
        q["tracks"].append({"id": "A3", "k": "a", "role": "dialog", "voiceFx": False})
        q["items"].append({"id": "x", "track": "A3", "media": "main", "start": 0.0, "in": 10.0, "out": 12.0, "speed": 1, "rev": False})
        sp, other = editor.speech_spans(q)
        self.assertEqual(other, [[9.0, 11.0]])
        self.assertEqual(len(sp), 1)  # 효과음 트랙(voiceFx false)의 원본은 말로 안 봄

    def test_export_mix_keeps_music_up_in_silent_demo(self):
        """대사 트랙이 계속 크게 울려도(공 소리·현장 소리) 받아쓴 말 자리에서만 음악을 줄임."""
        import numpy as np
        tmp = Path(tempfile.mkdtemp(prefix="E2 덕킹 "))
        try:
            _tone(tmp / "main.wav", 440, 20)
            _tone(tmp / "bgm.wav", 1000, 8)
            media = {"main": {"id": "main", "file": "main.wav", "src": "x", "dur": 20.0, "audio": True},
                     "bgm": {"id": "bgm", "file": "bgm.wav", "src": "x", "dur": 8.0, "audio": True}}
            q = self.seq([{"start": 10.0, "end": 12.0, "text": "말하는 중"}])
            with mock.patch.object(editor, "media_path", lambda f, src="videos": tmp / f):
                path, _ = editor._mix_audio(q, media, 0.0, 8.0, tmp, [], lambda x: None)
            y = np.fromfile(path, np.float32).reshape(-1, 2)[:, 0]

            def amp(a, b, f):
                x = y[int(a * 48000):int(b * 48000)]
                spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
                k = int(round(f * len(x) / 48000))
                return spec[k - 2:k + 3].max()
            talk, demo = amp(2.6, 3.6, 1000), amp(5.0, 6.0, 1000)
            self.assertLess(20 * math.log10(talk / demo), -10.0)  # 말하는 동안만 약 -14dB
            self.assertGreater(amp(5.0, 6.0, 440), amp(2.6, 3.6, 440) * 0.5)  # 대사 트랙은 그대로 울림 (시범 소리)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ThumbGrabTest(WorkMixin, unittest.TestCase):
    def test_source_resolution_and_separate_files(self):
        import thumb
        v = core.VIDEOS / "big.mp4"
        r = core.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=2560x1440:r=10:d=1", "-c:v", "libx264", "-preset", "ultrafast",
                      "-pix_fmt", "yuv420p", str(v)])
        self.assertEqual(r.returncode, 0, r.stderr)
        from PIL import Image
        small = thumb.grab("big.mp4", 0.5, 640)
        full = thumb.grab("big.mp4", 0.5)
        work = thumb.grab("big.mp4", 0.5, thumb.WORK_W)
        self.assertEqual(len({small, full, work}), 3)  # 크기마다 다른 파일 (예전엔 먼저 만든 640 이 배경으로 쓰임)
        with Image.open(full) as im:
            self.assertEqual(im.size, (2560, 1440))
        with Image.open(work) as im:
            self.assertEqual(im.size, (1920, 1080))
        with Image.open(small) as im:
            self.assertEqual(im.size[0], 640)

    def test_template_crop_only_with_band(self):
        js = (Path(__file__).resolve().parents[1] / "thumb_src" / "parts" / "p7_auto.js").read_text(encoding="utf-8")
        self.assertNotIn("cropB: 0.26", js)
        self.assertNotIn("subCrop()", js)
        self.assertIn('f.band === "bottom"', js)


class OtherChannelKitTest(WorkMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (core.VIDEOS / "20260101_AbCdEfGhIjK_쌈바 풋살.mp4").write_bytes(b"x")
        self.name = "20260101_AbCdEfGhIjK_쌈바 풋살.mp4"

    def fake(self, kinds):
        import source
        return mock.patch.object(source, "describe", lambda f, data=None, running=None: dict(kinds.get(f, {"kind": "footage"})))

    def test_original_of_other_channel_is_blocked(self):
        with self.fake({self.name: {"kind": "other", "channel": "쌈바 풋살 클래스", "channelKey": "UCx", "channelUrl": "https://youtube.com/@samba"}}):
            with self.assertRaises(ValueError) as e:
                upload.build_kit(self.name)
        self.assertIn("저작권", str(e.exception))

    def test_own_footage_not_blocked_in_other_sources(self):
        with self.fake({}):
            self.assertEqual(upload.other_sources(self.name), [])

    def test_edit_with_imported_other_clip(self):
        proj = {"media": [{"id": "m1", "file": "남의 영상.mp4", "src": "videos"}, {"id": "m2", "file": "bgm.mp3", "src": "assets"}]}
        sq = {"items": [{"media": "main"}, {"media": "m1"}, {"media": "m2"}]}
        with self.fake({"남의 영상.mp4": {"kind": "other", "channel": "쌈바", "channelKey": "UCs", "channelUrl": "https://youtube.com/@s"}}):
            o = upload.other_sources("내 촬영.mp4", sq, proj)
        self.assertEqual([(x["channel"], x["main"]) for x in o], [("쌈바", False)])

    def test_credit_block(self):
        desc = "첫 줄\n\n▶ 출연\n최경진 감독 (풋살사관학교)\n\n#풋살 #풋살사관학교\n"
        o = [{"channel": "쌈바 풋살 클래스", "url": "https://youtube.com/@samba", "main": True}]
        out = upload._credit(desc, o, True)
        self.assertNotIn("▶ 출연", out)
        self.assertNotIn("최경진 감독", out)
        self.assertIn("▶ 출처\n쌈바 풋살 클래스 (https://youtube.com/@samba)", out)
        self.assertLess(out.index("▶ 출처"), out.index("#풋살"))
        keep = upload._credit(desc, [dict(o[0], main=False)], False)
        self.assertIn("▶ 출연", keep)


if __name__ == "__main__":
    unittest.main()
