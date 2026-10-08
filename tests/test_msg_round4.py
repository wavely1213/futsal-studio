"""MSG round4 판정에서 나온 문제의 회귀 시험 — 저장소 폴더에서 python3 -m unittest tests.test_msg_round4
소리: 효과음은 목소리 보정(압축기) 없이 섞고 크기는 보정한 말소리에 맞춤(띠로리가 말보다 큼) · 긴 효과음 꼬리는 말 밑에서 줄임 · 소리 크기 최대 여유 ·
다시 보기: 반응이 아닌 다음 말(설명·마무리) 앞 · 이미 컷인 자리 · 첫 질문: 쉼으로 잘린 문장 앞머리까지(‘패스하고 그’) · 티저 끝에 감독님 질문 ·
글자: 같은 때 말 자막을 그대로 옮긴 강조·상황 자막 · 받아쓰기 첫 낱말 시각 · NG 자리의 문장 사이 숨 · 확대: 바로 앞뒤 확대·컷과 붙은 확대 덜기 ·
머리가 잘리지 않는 확대 기준점 · 양 범위: 같은 순간을 겹쳐 보여 주는 것부터 빼고 하나뿐인 재미 순간은 나중에."""
import math
import re
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import msg  # noqa: E402
from make_msg_fixture import MsgWork  # noqa: E402


def M(kind, t, text="", a=None, b=None, score=1.0, why=""):
    return {"kind": kind, "t": t, "a": t if a is None else a, "b": t + 1.0 if b is None else b, "score": score, "text": text, "why": why}


def L(a, b, text):
    return {"start": a, "end": b, "text": text, "words": []}


def build(src_dur=200.0, clips=((10.0, 60.0), (62.0, 106.0))):
    b = msg._Build("x.mp4", {"duration": src_dur, "width": 1920, "height": 1080}, "long")
    for a, c in clips:
        b.clip(a, c, main=True)
    return b


def _wav(path, x):
    import numpy as np
    y = (np.clip(np.stack([x, x], axis=1), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(y.tobytes())


class VoiceBusTest(unittest.TestCase):
    """판정: '띠로리'(실수 효과음)가 말보다 큼(-0.8dB · 기준 -1dB) — 효과음이 대사 트랙이라 목소리 압축기(+6dB 키움)를 함께 지나
    정한 크기보다 커지고, 말과 겹치면 말소리도 같이 눌림. 효과음 트랙(voiceFx: false)은 보정 없이 섞고, 크기는 보정한 말소리에 맞춤."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        cls.tmp = Path(tempfile.mkdtemp(prefix="소리 버스 시험 "))
        work = cls.tmp / "작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in list(dirs.values()) + [work / "edit_media"]:
            d.mkdir(parents=True, exist_ok=True)
        cls.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(editor, "ASSETS", work / "edit_media")]
        for p in cls.patches:
            p.start()
        t = np.arange(int(4.0 * 48000)) / 48000.0
        env = (0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)) * ((t > 0.2) & (t < 3.8))
        speech = 0.25 * env * (np.sin(2 * np.pi * 200 * t) + 0.5 * np.sin(2 * np.pi * 400 * t) + 0.25 * np.sin(2 * np.pi * 800 * t)) / 1.75
        _wav(work / "edit_media" / "말.wav", speech)
        cls.raw_pk = 20 * math.log10(float(np.abs(speech).max()))
        tb = t[: int(0.5 * 48000)]
        blip = 10 ** (-3 / 20) * np.sin(2 * np.pi * 660 * tb) * np.minimum(1, (0.5 - tb) / 0.05)
        _wav(work / "edit_media" / "효과음_삐.wav", blip)
        cls.speech_file = work / "edit_media" / "말.wav"
        cls.media = {"sp": {"id": "sp", "kind": "audio", "src": "assets", "file": "말.wav", "dur": 4.0, "audio": True},
                     "bp": {"id": "bp", "kind": "audio", "src": "assets", "file": "효과음_삐.wav", "dur": 0.5, "audio": True}}

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _seq(self, voice_fx_off, mute_speech=False):
        tracks = editor.default_tracks()
        for tr in tracks:
            if tr["id"] == "A2":
                tr.update(role="dialog", name="효과음")
                if voice_fx_off:
                    tr["voiceFx"] = False
        items = [{"id": "a", "track": "A1", "media": "sp", "start": 0.0, "in": 0.0, "out": 4.0, "speed": 1.0, "rev": False, "link": None, "fx": {}, "gain": 0.0,
                  "fadeIn": 0.0, "fadeOut": 0.0, "mute": mute_speech},
                 {"id": "b", "track": "A2", "media": "bp", "start": 1.0, "in": 0.0, "out": 0.5, "speed": 1.0, "rev": False, "link": None,
                  "fx": {"level": {"v": -10.0, "k": []}}, "gain": 0.0, "fadeIn": 0.0, "fadeOut": 0.0, "mute": False}]
        return {"tracks": tracks, "items": items, "voice": dict(msg.VOICE), "duck": {"on": False}}

    def _mix(self, seq):
        import numpy as np
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        f, _ = editor._mix_audio_impl(seq, self.media, 0.0, 4.0, d, [], lambda x: None, None)
        x = np.fromfile(f, np.float32).reshape(-1, 2).copy()
        shutil.rmtree(d, ignore_errors=True)
        return x

    def test_sound_effect_skips_the_voice_compressor(self):
        import numpy as np
        both = self._mix(self._seq(True))
        speech = self._mix(self._seq(True, mute_speech=False) | {"items": [self._seq(True)["items"][0]]})
        fx = both - speech  # 효과음만 (말과 겹친 자리에서도 말소리는 그대로)
        pk = 20 * math.log10(float(np.abs(fx).max()))
        self.assertAlmostEqual(pk, -13.0, delta=0.3)  # 파일 -3 dBFS + 레벨 -10dB 그대로
        old = self._mix(self._seq(False)) - speech  # 예전: 효과음도 압축기를 지남 → 크기가 바뀌고 말소리도 함께 바뀜
        self.assertGreater(abs(20 * math.log10(float(np.abs(old).max())) - pk), 2.0)

    def test_voice_peak_matches_the_compressed_dialog(self):
        """효과음 크기 기준(voicePk) = 편집본의 보정한 말소리 최대 크기 (원본 말소리보다 압축기만큼 다름)."""
        import numpy as np
        words = [(0.25, 3.75, "말", 0)]
        vp = msg._voice_peak(self.speech_file, words)
        x = self._mix({"tracks": editor.default_tracks(), "items": [self._seq(True)["items"][0]], "voice": dict(msg.VOICE), "duck": {"on": False}})
        hop = 4800
        n = len(x) // hop
        pk = np.abs(x[: n * hop]).reshape(n, hop * 2).max(axis=1)
        mixed = 20 * math.log10(float(np.percentile(pk[3:37], 99)))
        self.assertIsNotNone(vp)
        self.assertAlmostEqual(vp, mixed, delta=0.5)
        self.assertGreater(vp, self.raw_pk + 1.0)  # 압축기 키움 (+6dB 에서 누른 만큼 뺌) → 원본 말소리보다 큼 (원본 크기에 맞추면 효과음이 너무 작음)

    def test_sfx_level_uses_the_processed_speech(self):
        sig = {"speechPk": -18.1, "voicePk": -13.2}
        self.assertAlmostEqual(msg._sfx_level(sig, "띠로리", "fail", -3.0), -13.2 - msg.SFX_GAP + 3.0, places=1)
        est = msg._sfx_level({"speechPk": -18.1}, "띠로리", "fail", -3.0)  # 예전 신호: 압축기 어림
        self.assertAlmostEqual(est, msg._voice_est(-18.1) - msg.SFX_GAP + 3.0, places=1)
        self.assertGreater(msg._voice_est(-18.1), -18.1)

    def test_long_sfx_tail_ducks_under_the_next_word(self):
        """판정: 듬뿍의 효과음·음악이 말을 가려 받아쓰기가 틀림 → 긴 효과음 꼬리가 말 첫소리와 겹치면 그 앞에서 줄임 (앞부분 크기는 그대로)."""
        keys = msg._sfx_duck([10.0, 12.6], 11.8, 2.0, -15.0)
        self.assertEqual([k["t"] for k in keys], [0.65, 0.8])
        self.assertEqual([k["v"] for k in keys], [-15.0, -15.0 - msg.SFX_DUCK])
        self.assertEqual(msg._sfx_duck([12.0], 11.8, 2.0, -15.0), [])   # 효과음이 시작한 바로 뒤 말은 줄이지 않음 (첫소리가 효과음 크기)
        self.assertEqual(msg._sfx_duck([12.2], 11.8, 0.42, -15.0), [])  # 짧은 휙은 그대로 (최대 크기가 줄어 안 들림)
        self.assertEqual(msg._sfx_duck([20.0], 11.8, 2.0, -15.0), [])

    def test_export_true_peak_has_headroom(self):
        """판정: 최대 -0.9 dBTP (유튜브 기준 -1) — AAC 로 줄이면 0.5~1.5dB 커짐 → 최대를 여유 있게 · 줄인 소리가 넘으면 더 낮춰 한 번 더."""
        self.assertLessEqual(editor.LOUD_TP, -2.0)
        work = core.WORK
        r = core.run([core.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=4", "-f", "lavfi", "-i",
                      "aevalsrc='0.05*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*3*t))+0.9*lt(mod(t,2),0.01)*sin(2*PI*90*t)':s=48000:d=4",
                      "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", str(work / "edit_media" / "peaky.mp4")])
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        md = [{"id": "pk", "kind": "video", "src": "assets", "file": "peaky.mp4", "dur": 4.0, "w": 320, "h": 180, "fps": 30.0, "audio": True}]
        v, a = editor._pair(0.0, 4.0)
        v["media"] = a["media"] = "pk"
        p = {"id": "s", "name": "순간 최대", "format": "long", "v": 2, "captionsOn": False, "captionStyle": dict(editor.LONG_STYLE), "titles": [], "shapes": [],
             "layout": {"mode": "fill"}, "master": {"volume": 1, "normalize": True, "lufs": -14}, "duck": {"on": False}, "tracks": editor.default_tracks(),
             "items": [v, a], "trans": [], "markers": [], "captions": [], "info": {"duration": 4.0, "width": 320, "height": 180, "fps": 30.0},
             "source": "시험.mp4", "media": md}
        seen, tps = [], iter([-0.4, -1.9])
        real = editor._run_ff

        def spy(args, *x, **k):
            for a_ in args:
                if "loudnorm=" in str(a_):
                    seen.append(str(a_))
            return real(args, *x, **k)
        with mock.patch.object(editor, "_run_ff", spy), mock.patch.object(editor, "_true_peak", lambda *x, **k: next(tps)):
            editor.export("시험.mp4", p, {"preset": "small", "hw": False, "xml": False, "srt": False}, lambda m: None)
        tpv = [float(re.search(r"TP=(-?[\d.]+)", x)[1]) for x in seen]
        self.assertEqual(len(tpv), 2, seen)
        self.assertAlmostEqual(tpv[0], editor.LOUD_TP, places=2)
        self.assertAlmostEqual(tpv[1], editor.LOUD_TP - (-0.4 - editor.LOUD_TP_OUT) - 0.5, places=2)


class ReplayPlaceTest(unittest.TestCase):
    def test_replay_does_not_jump_over_the_next_instruction(self):
        """판정: 시범 뒤 '그리고 마지막으로 …' 설명을 반응으로 보고 그 뒤(마무리 인사 바로 앞)로 다시 보기가 밀려남 → 엉뚱."""
        lines = [L(94.03, 97.82, "하나, 둘, 셋 리듬으로 움직여 보세요."), L(103.67, 108.79, "그리고 마지막으로, 받는 사람 발 쪽으로 정확하게 패스해 주세요."),
                 L(110.27, 113.69, "오늘 영상은 여기까지입니다. 감사합니다.")]
        at = msg._after_reaction(lines, 103.6)
        self.assertLess(at, 103.67)
        self.assertGreaterEqual(at, 103.0)
        lines = [L(30.0, 31.0, "좋아요."), L(31.2, 33.0, "두 번째 동작은 옆으로 빠지는 거예요.")]  # 반응 뒤 바로 새 순서 → 반응까지만
        self.assertAlmostEqual(msg._after_reaction(lines, 29.9), 31.15, places=2)
        lines = [L(30.0, 31.0, "나이스!"), L(31.2, 32.0, "오늘은 여기까지예요.")]
        self.assertAlmostEqual(msg._after_reaction(lines, 29.9), 31.15, places=2)
        # 마지막 골 뒤 반응 '들어갔다! 성공입니다!' 다음의 새 설명은 반응이 아님 (마무리 인사 바로 앞으로 밀려나지 않게)
        lines = [L(69.17, 71.56, "들어갔다! 성공입니다!"), L(72.96, 75.92, "슈팅할 때는 디딤발 방향이 핵심이에요."), L(76.48, 80.79, "오늘은 여기까지.")]
        self.assertAlmostEqual(msg._after_reaction(lines, 68.87), 71.71, places=2)
        lines = [L(160.62, 162.4, "아, 아깝다."), L(163.42, 164.7, "터치가 조금 길었네요.")]  # 시도에 붙은 말은 반응으로 이음
        self.assertAlmostEqual(msg._after_reaction(lines, 160.5), 164.85, places=2)

    def test_replay_goes_into_an_existing_cut(self):
        """이어진 장면 가운데에 끼우면 화면이 두 번 더 바뀜 → 말 없는 쉼 뒤 이미 컷인 자리로 (양 범위를 넘어 재미 글자가 빠지지 않게)."""
        pieces = [{"in": 113.62, "out": 128.34}, {"in": 137.69, "out": 166.25}]
        words = [(123.95, 126.57, "깔끔하게", 0), (137.7, 138.2, "세", 1)]
        self.assertEqual(msg._snap_insert(126.72, pieces, words), 137.69)
        words2 = words + [(127.2, 127.9, "그리고", 0)]  # 사이에 말이 있으면 그대로
        self.assertEqual(msg._snap_insert(126.72, pieces, words2), 126.72)
        self.assertEqual(msg._snap_insert(120.0, pieces, words), 120.0)  # 컷이 멀면 그대로


class HookSentenceTest(unittest.TestCase):
    def test_question_split_by_a_long_gap_is_one_sentence(self):
        """판정: 첫 질문을 앞으로 옮기고 본편에서 뺐더니 '패스하고 그'만 남음 — 받아쓰기가 '자리에 서 있으면 왜'를 몰아 적어 쉼으로 잘린 문장."""
        segs = [{"start": 0.0, "end": 9.72, "text": "오늘은 연습을 해볼게요. 패스하고 그 자리에 서 있으면 왜 안 될까요?",
                 "words": [{"w": "오늘은", "s": 2.72, "e": 3.02}, {"w": "연습을", "s": 3.02, "e": 3.6}, {"w": "해볼게요.", "s": 3.6, "e": 5.92},
                           {"w": "패스하고", "s": 6.87, "e": 7.38}, {"w": "그", "s": 7.38, "e": 7.64}, {"w": "자리에", "s": 8.91, "e": 8.96},
                           {"w": "서", "s": 8.96, "e": 9.01}, {"w": "있으면", "s": 9.01, "e": 9.06}, {"w": "왜", "s": 9.06, "e": 9.11},
                           {"w": "안", "s": 9.11, "e": 9.24}, {"w": "될까요?", "s": 9.24, "e": 9.72}]}]
        sig = {"name": "x", "duration": 60.0, "tidy": [{"in": 0.0, "out": 60.0}], "junk": []}
        ms = msg.moments(sig, segs)
        hk = next(m for m in ms if m["kind"] == "hook_line")
        self.assertEqual(hk["a"], 6.87)
        self.assertTrue(hk["text"].startswith("패스하고 그 자리에"), hk["text"])
        hook = msg._hook_text(ms, segs, "long")
        self.assertEqual(hook, "패스하고 그 자리에 서 있으면 왜 안 될까요?")
        self.assertEqual(msg._wrap2(hook).count("\n"), 1)
        sig["junk"] = [[6.8, 7.7, "끊긴 말"]]  # 앞머리가 정리할 곳이면 잇지 않음
        hk = next(m for m in msg.moments(sig, segs) if m["kind"] == "hook_line")
        self.assertEqual(hk["a"], 8.91)

    def test_wrap_keeps_modifier_and_short_words(self):
        self.assertEqual(msg._wrap2("왜 다들 첫 터치에서 공을 놓칠까요?"), "왜 다들 첫 터치에서\n공을 놓칠까요?")
        self.assertEqual(msg._wrap2("과연 몇 개나 들어갈까요?"), "과연 몇 개나 들어갈까요?")
        two = msg._wrap2("패스하고 그 자리에 서 있으면 왜 안 될까요?")
        self.assertFalse(two.split("\n")[0].endswith(" 그"), two)
        self.assertFalse(two.split("\n")[0].endswith(" 서"), two)


class CaptionDupTest(unittest.TestCase):
    def test_overlay_repeating_the_caption_is_changed(self):
        """판정: 강조 '완전히 달라져요!' = 같은 때 말 자막 '완전히 달라져요.' · 상황 자막 '첫 번째 동작' = 말 자막 '첫 번째 동작.'."""
        caps = [{"start": 10.0, "end": 12.0, "text": "완전히 달라져요."}, {"start": 20.0, "end": 21.5, "text": "첫 번째 동작."},
                {"start": 30.0, "end": 33.0, "text": "이 차이가 정말\n중요해요."}]
        self.assertTrue(msg._cap_dup("완전히 달라져요!", 10.5, 12.0, caps))
        self.assertTrue(msg._cap_dup("첫 번째 동작", 20.0, 22.0, caps))
        self.assertTrue(msg._cap_dup("정말 중요!", 30.0, 32.0, caps))        # round5: 말 자막 안에 그대로 든 낱말도 되풀이 (판정)
        self.assertFalse(msg._cap_dup("완전히 달라져요!", 40.0, 42.0, caps))  # 다른 때
        self.assertEqual(msg._situ_badge("첫 번째 동작"), "동작 ①")
        self.assertEqual(msg._situ_badge("마지막! 세 번째 포인트"), "마지막! 포인트 ③")
        self.assertIsNone(msg._situ_badge("시범 들어갑니다"))
        self.assertIsNone(msg._dedupe_text("emphasis", "완전히 달라져요!", 10.5, 1.0, caps))  # round5: 새 정보가 없으면 안 띄움 (확대가 맡음)
        self.assertIsNone(msg._dedupe_text("emphasis", "완전히 달라져요!", 10.5, 1.0, caps, term="디딤발"))  # round5 최종: 근처 기술 이름으로 안 바꿈


class AlignTest(unittest.TestCase):
    def test_first_word_written_from_zero_moves_to_its_sound(self):
        """판정: 첫 자막이 '여러분.'만 (받아쓰기가 '안녕하세요.'를 앞 무음 0초부터 적어 컷이 그 소리 바로 앞에서 시작해도 낱말 가운데가 클립 앞)."""
        segs = [{"start": 0.0, "end": 2.6, "text": "안녕하세요. 여러분.", "words": [{"w": "안녕하세요.", "s": 0.0, "e": 1.54}, {"w": "여러분.", "s": 2.29, "e": 2.59}]}]
        blobs = [[1.21, 2.12], [2.29, 2.64]]
        out, n = msg.align_to_sound(segs, blobs)
        self.assertEqual(out[0]["words"][0]["s"], 1.21)
        self.assertGreaterEqual(n, 1)
        self.assertGreaterEqual(msg.ALIGN_VER, 3)
        far = [{"start": 0.0, "end": 0.5, "text": "네.", "words": [{"w": "네.", "s": 0.0, "e": 0.4}]}]  # 제 시각 밖의 먼 소리로는 안 옮김
        self.assertEqual(msg.align_to_sound(far, [[1.5, 2.0]])[0][0]["words"][0]["s"], 0.0)

    def test_sentences_keep_a_breath_at_an_ng_splice(self):
        """판정: NG 를 뺀 자리에서 두 문장이 0.2초 만에 붙어 한 자막(11초)처럼 보임 → 문장 사이 숨 0.3초 남짓."""
        words = [(46.0, 50.62, "해요.", 0), (51.02, 52.0, "그래서", 1), (58.62, 59.3, "그래서", 2)]
        blobs = [[46.0, 50.6], [51.0, 52.1], [58.6, 59.4]]
        junk = [(51.0, 58.4, "NG")]
        cuts = msg.snap_edges([{"in": 40.0, "out": 51.0}, {"in": 58.4, "out": 70.0}], blobs, words, junk)
        gap = (cuts[0]["out"] - 50.6) + (58.6 - cuts[1]["in"])
        self.assertGreaterEqual(gap, 0.25)
        self.assertLess(cuts[0]["out"], 51.0)  # NG 첫소리는 넣지 않음


class ZoomSpacingTest(unittest.TestCase):
    def test_zoom_right_after_another_change_is_removed(self):
        """판정: '하나, 둘' 구령에 1.0초 간격 확대 두 번 · 0.6초 간격 확대 — 확대가 바로 앞뒤 확대·컷과 ZOOM_GAP 안이면 덜어 냄."""
        b = build(200.0, ((0.0, 40.0),))
        it = b.main[0]
        n1 = msg._split_main(b, it, 10.0, [0.5, 0.4])
        b.event("punch", 10.0, "", "", refs={"items": [n1["id"]]})
        n2 = msg._split_main(b, n1, 11.0, [0.5, 0.4])
        b.event("punch", 11.0, "", "", refs={"items": [n2["id"]]})
        before = [t for t, k in msg.visual_events(b.items, b.titles, b.shapes, b.pos) if k == "zoom"]
        self.assertEqual(before, [10.0, 11.0])
        msg.DENSITY["_시험"] = (0.0, 30.0, 60.0)
        try:
            msg.govern(b, "_시험", [0.5, 0.4], b.pos)
        finally:
            del msg.DENSITY["_시험"]
        zs = [t for t, k in msg.visual_events(b.items, b.titles, b.shapes, b.pos) if k == "zoom"]
        self.assertTrue(all(y - x >= msg.ZOOM_GAP for x, y in zip(zs, zs[1:])), zs)
        self.assertLessEqual(len([t for t in zs if 9.0 <= t <= 12.0]), 1, zs)

    def test_zoom_anchor_keeps_the_head_in_frame(self):
        """판정: 130% 확대에서 머리 윗부분이 잘림 → 기준점을 올려 가장 큰 확대에서도 머리 꼭대기가 화면 안."""
        fb = [0.44, 0.106, 0.557, 0.357]
        anc = msg.zoom_anchor([0.5, 0.23], fb)
        top = fb[1] - 0.25 * (fb[3] - fb[1])
        self.assertGreaterEqual(anc[1] + msg.ZOOM_MAX * (top - anc[1]), 0.0)
        self.assertGreaterEqual(anc[1] + 1.08 * (top - anc[1]), 0.0)
        self.assertEqual(msg.zoom_anchor([0.5, 0.45], [0.4, 0.35, 0.6, 0.55]), [0.5, 0.45])  # 얼굴이 아래쪽이면 그대로
        self.assertEqual(msg.zoom_anchor([0.5, 0.38], None), [0.5, 0.38])


class TrimOrderTest(unittest.TestCase):
    def test_trim_keeps_the_only_event_of_a_moment(self):
        """판정(순간 재현율): 양 범위를 맞추느라 펀치라인의 하나뿐인 속마음 글자가 빠짐 → 같은 순간을 다른 사건이 이미 보여 주는 것부터 뺌."""
        b = build(400.0, ((0.0, 120.0),))
        moms = [M("punchline", 60.0, a=58.0, b=60.5), M("success", 30.0, a=29.0, b=31.0)]
        t1 = b.title("(뿌듯)", 59.0, 1.5, dict(editor.TITLE_STYLE))
        only = b.event("inner", 59.0, "(뿌듯)", "", src=60.0, refs={"titles": [t1["id"]]})
        t2 = b.title("나이스!!", 30.0, 1.5, dict(editor.TITLE_STYLE))
        dup = b.event("fx", 30.0, "나이스!!", "", src=30.0, refs={"titles": [t2["id"]]})
        t3 = b.title("2/5 · 1골", 30.5, 2.0, dict(editor.TITLE_STYLE))
        b.event("score", 30.5, "2/5 · 1골", "", src=30.5, refs={"titles": [t3["id"]]})
        for t in (5.0, 15.0, 45.0, 75.0, 90.0, 105.0):
            tt = b.title(f"강조 {t}", t, 1.5, dict(editor.TITLE_STYLE))
            b.event("emphasis", t, tt["text"], "", src=t, refs={"titles": [tt["id"]]})
        msg.DENSITY["_시험"] = (0.0, 4.0, 60.0)
        try:
            msg.govern(b, "_시험", [0.5, 0.4], b.pos, moms)
        finally:
            del msg.DENSITY["_시험"]
        ids = {e["id"] for e in b.events}
        self.assertIn(only["id"], ids)
        self.assertNotIn(dup["id"], ids)


class CompiledTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()
        names = ("예능 MSG형", "담백 레슨형", "다큐 감성형")
        cls.res = {k: msg.build_variants(cls.w.name, [{"kind": "preset", "name": n} for n in names], k, ("long",), lambda m: None) for k in ("보통", "듬뿍")}

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def _caps(self, r, q):
        return editor.timeline_captions(dict(q, captions=r["captions"]))

    def test_sound_effect_tracks_skip_voice_processing(self):
        for r in self.res.values():
            for q in r["sequences"]:
                fx = [t for t in q["tracks"] if t.get("name") == "효과음"]
                self.assertTrue(fx and all(t.get("voiceFx") is False and t.get("role") == "dialog" for t in fx), q["name"])
                self.assertEqual(q["voice"], msg.VOICE)

    def test_opening_question_is_said_once_and_whole(self):
        """판정: 티저에 띄운 질문을 본편 5초 뒤에 또 함(반복) · 질문을 앞으로 옮긴 스타일은 앞머리 조각만 남김."""
        for r in self.res.values():
            for q in r["sequences"]:
                tz = next((e for e in q["msg"]["events"] if e["kind"] == "teaser" and e.get("ins")), None)
                if tz is None:
                    continue
                end = tz["ins"]["start"] + tz["ins"]["len"]
                body = [c for c in self._caps(r, q) if c["start"] >= end - 0.05]
                said = " ".join(c["text"] for c in body[:4]).replace("\n", " ")
                self.assertNotIn("놓칠까요", said, (q["name"], said))
                self.assertNotIn("\n", tz["text"])

    def test_documentary_starts_on_moving_picture_at_every_amount(self):
        """판정: 다큐 듬뿍이 말 없는 2초 정지 화면 제목 카드로 시작해 첫 3초 훅이 약함 → 본편 첫 장면 위 작은 제목."""
        q = next(q for q in self.res["듬뿍"]["sequences"] if "다큐" in q["name"])
        first = min((it for it in q["items"] if it["track"] == "V1"), key=lambda x: x["start"])
        self.assertEqual(first["media"], "main")


if __name__ == "__main__":
    unittest.main()
