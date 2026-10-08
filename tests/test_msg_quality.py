"""MSG 올리기 품질 (round1 판정에서 나온 문제의 회귀 시험) — 저장소 폴더에서 python3 -m unittest tests.test_msg_quality
받아쓰기: 전체 듣기·말소리 없는 곳 버림·멈추면 마저 듣기 · MSG 다시 듣기·클로드 다듬기(켰을 때만) · 단어 시각을 소리에 맞춤
정리: 붙어 버린 슬레이트 말 떼기 · 소리로 찾는 추임새 · 컷 가장자리 · 쉼 자르기 양
소리: 효과음 파일 크기 맞춤·실제 크기로 레벨 · 효과음 간격 · 소리가 모자란 스타일 채우기 · 배경음악은 말보다 충분히 작게 · 소리 크기 목표 -14
글자: 안전 영역·말 자막 줄·겹침·한꺼번에 2개 · 숫자 세기 · 짧은 자막 · 낱말 조각 강조 안 함 · 무례한 문구 없음
양: 화면 사건 수·멈춘 화면 한도·컷과 같은 때 뜬 글자 빼기 · 담백 레슨형은 다른 자막 모양 · 챌린지 최종 결과."""
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import msg  # noqa: E402
import proofread  # noqa: E402
import sfxlib  # noqa: E402
from make_msg_fixture import MsgWork  # noqa: E402


def W(w, s, e, p=0.9):
    return {"w": w, "s": s, "e": e, "p": p}


class ProofreadTest(unittest.TestCase):
    def test_align_keeps_times_for_same_words_and_splits_changed(self):
        ws = [W("왜", 8.5, 8.7), W("다들", 8.7, 9.0), W("공으로", 9.9, 10.4), W("칠까요?", 10.4, 11.1)]
        out = proofread.align_words(ws, "왜 다들 공을 놓칠까요?")
        self.assertEqual([w["w"] for w in out], ["왜", "다들", "공을", "놓칠까요?"])
        self.assertEqual((out[0]["s"], out[1]["e"]), (8.5, 9.0))
        self.assertEqual((out[2]["s"], out[3]["e"]), (9.9, 11.1))  # 바뀐 묶음은 그 시각 안에서 나눔
        self.assertLess(out[2]["e"], out[3]["e"])
        merged = proofread.align_words([W("골", 1.0, 1.3), W("때", 1.3, 1.5), W("맞았어요.", 1.5, 2.0)], "골대 맞았어요.")
        self.assertEqual([(w["w"], w["s"], w["e"]) for w in merged], [("골대", 1.0, 1.5), ("맞았어요.", 1.5, 2.0)])
        ins = proofread.align_words([W("수비가", 1.0, 1.3), W("붙어요", 1.3, 1.8)], "수비가 바로 붙어요")
        self.assertEqual([w["w"] for w in ins], ["수비가", "바로", "붙어요"])
        self.assertTrue(all(a["e"] <= b["s"] + 1e-6 for a, b in zip(ins, ins[1:])))
        gone = proofread.align_words([W("어...", 7.1, 7.6), W("왜", 8.5, 8.7)], "왜")
        self.assertEqual([(w["w"], w["s"]) for w in gone], [("왜", 8.5)])

    def test_acceptable_rejects_rewrites(self):
        self.assertTrue(proofread.acceptable("수비가 바로 부터원이니까요.", "수비가 바로 붙어 버리니까요."))
        self.assertTrue(proofread.acceptable("이거 넣으면 세 개예요", "이거 넣으면 세 개예요."))  # 문장 부호만
        self.assertFalse(proofread.acceptable("하나 둘 셋", "오늘은 정말 좋은 날씨네요 하하하"))
        self.assertFalse(proofread.acceptable("같은 말", "같은 말"))

    def test_proofread_with_fake_claude(self):
        segs = [{"start": 0.0, "end": 2.0, "text": "왜 다들 공으로 칠까요?", "words": [W("왜", 0.0, 0.3), W("다들", 0.3, 0.8), W("공으로", 0.8, 1.4), W("칠까요?", 1.4, 2.0)]},
                {"start": 3.0, "end": 3.5, "text": "", "words": []},
                {"start": 4.0, "end": 5.0, "text": "골 때 맞았어요.", "words": [W("골", 4.0, 4.2), W("때", 4.2, 4.4), W("맞았어요.", 4.4, 5.0)]}]
        seen = []

        def run(prompt, timeout=None, cancel=None):
            seen.append(prompt)
            self.assertIn("1: 왜 다들 공으로 칠까요?", prompt)
            self.assertIn("2: 골 때 맞았어요.", prompt)  # 빈 줄은 안 보냄
            self.assertIn("「퍼스트 터치 레슨」", prompt)
            return {"text": '좋아요 {"fix":[{"i":1,"t":"왜 다들 공을 놓칠까요?"},{"i":2,"t":"골대 맞았어요."},{"i":9,"t":"없는 줄"}]}', "model": "m"}
        new, n, model = proofread.proofread(segs, "퍼스트 터치 레슨", ["디딤발"], run=run)
        self.assertEqual((n, model, len(seen)), (2, "m", 1))
        self.assertEqual([s["text"] for s in new], ["왜 다들 공을 놓칠까요?", "", "골대 맞았어요."])
        self.assertEqual(new[2]["words"][0], {"w": "골대", "s": 4.0, "e": 4.4, "p": 0.9})
        with self.assertRaises(proofread.ProofError):
            proofread.proofread(segs, run=lambda p, timeout=None, cancel=None: {"text": "모르겠어요"})


class TranscribeTest(unittest.TestCase):
    """core.transcribe_audio — 전체를 앞 문장에 기대지 않고 듣고, 말소리가 없는 곳에서 지어낸 글은 버림 · 멈추면 그 뒤를 말 찾기로."""

    def seg(self, a, b, text, p=0.9):
        toks = text.split()
        step = (b - a) / len(toks)
        ws = [types.SimpleNamespace(start=a + k * step, end=a + (k + 1) * step, word=" " + t, probability=p) for k, t in enumerate(toks)]
        return types.SimpleNamespace(start=a, end=b, text=" " + text, words=ws)

    def test_unheard_dropped_and_options(self):
        calls = []
        segs = [self.seg(0.5, 2.0, "어? 이것도 들어갔어요."), self.seg(28.6, 30.0, "다음 영상에서 만나요.")]

        class M:
            hf_tokenizer = None

            def transcribe(self, audio, language=None, vad_filter=None, word_timestamps=None, initial_prompt=None, hotwords=None,
                           condition_on_previous_text=True):
                calls.append({"vad": vad_filter, "cond": condition_on_previous_text})
                return iter(segs), None
        import numpy as np
        with mock.patch.object(core, "speech_regions", return_value=[(0.3, 2.3)]):
            out, fixed, echoed, unheard = core.transcribe_audio(M(), np.zeros(16000 * 31, np.float32), {"terms": [], "fix": {}})
        self.assertEqual([s["text"] for s in out], ["어? 이것도 들어갔어요."])
        self.assertEqual(unheard, 1)
        self.assertEqual(calls, [{"vad": False, "cond": False}])

    def test_crash_resumes_with_vad(self):
        calls = []
        first = [self.seg(0.5, 2.0, "첫 번째 갑니다.")]

        class M:
            hf_tokenizer = None

            def transcribe(self, audio, language=None, vad_filter=None, word_timestamps=None, condition_on_previous_text=True):
                calls.append((vad_filter, round(len(audio) / 16000, 1)))

                def gen():
                    if not vad_filter:
                        yield from first
                        raise IndexError("boolean index did not match")
                    yield TranscribeTest.seg(self_t, 1.0, 2.0, "두 번째 슛.")
                return gen(), None
        self_t = self
        import numpy as np
        with mock.patch.object(core, "speech_regions", return_value=[(0.0, 10.0)]):
            out, *_ = core.transcribe_audio(M(), np.zeros(16000 * 10, np.float32), {"terms": [], "fix": {}})
        self.assertEqual([s["text"] for s in out], ["첫 번째 갑니다.", "두 번째 슛."])
        self.assertEqual(calls, [(False, 10.0), (True, 8.0)])  # 멈춘 곳(2초) 뒤부터 말 찾기로
        self.assertEqual(out[1]["start"], 3.0)  # 다시 들은 곳의 시각은 원본 기준

    def test_hotwords_are_punctuated(self):
        import captions
        self.assertEqual(captions.hotwords(["피벗", "픽소"], 60), "피벗, 픽소.")


class EnsureTranscriptTest(unittest.TestCase):
    def setUp(self):
        self.w = MsgWork()
        self.d = core.adir(self.w.name)
        (self.d / "asr.json").unlink()

    def tearDown(self):
        self.w.close()

    def test_relisten_once_backup_and_proofread_only_when_asked(self):
        n = []

        def fake_analyze(name, log, model="large-v3-turbo", step="1/1", label=""):
            n.append(label)
            segs = json.loads((self.d / "transcript.json").read_text(encoding="utf-8"))
            segs[1] = dict(segs[1], text="왜 다들 첫 터치에서 공으로 칠까요?",
                           words=[dict(w, w=t) for w, t in zip(segs[1]["words"], "왜 다들 첫 터치에서 공으로 칠까요?".split())])
            core.write_transcript(name, segs, asr={"v": core.ASR_VER, "model": model})
        old = (self.d / "transcript.json").read_text(encoding="utf-8")
        with mock.patch.object(core, "analyze", fake_analyze), mock.patch.object(msg, "_align", lambda name, log, info: False):
            self.assertTrue(msg.ensure_transcript(self.w.name, lambda m: None))
            self.assertEqual(n, [msg.RELISTEN_LABEL])
            self.assertEqual((self.d / msg.ORIG_TRANSCRIPT).read_text(encoding="utf-8"), old)
            msg.ensure_transcript(self.w.name, lambda m: None)
            self.assertEqual(len(n), 1)  # 한 번만
            import claude_cli
            asked = []

            def run(prompt, images=None, timeout=None, cancel=None, on_tick=None):
                asked.append(prompt)
                return {"text": '{"fix":[{"i":2,"t":"왜 다들 첫 터치에서 공을 놓칠까요?"}]}', "model": "m"}
            with mock.patch.object(claude_cli, "status", return_value={"state": "login"}), mock.patch.object(claude_cli, "run", run):
                self.assertFalse(msg.ensure_transcript(self.w.name, lambda m: None, proofread=True))
            self.assertEqual(asked, [])
            with mock.patch.object(claude_cli, "status", return_value={"state": "ready"}), mock.patch.object(claude_cli, "run", run):
                self.assertTrue(msg.ensure_transcript(self.w.name, lambda m: None, proofread=True))
                msg.ensure_transcript(self.w.name, lambda m: None, proofread=True)
            self.assertEqual(len(asked), 1)  # 같은 받아쓰기는 한 번만 보냄
            self.assertNotIn("감사합니다", asked[0].split("\n\n", 1)[0])
        segs = editor._segments_of(self.w.name)
        self.assertEqual(segs[1]["text"], "왜 다들 첫 터치에서 공을 놓칠까요?")
        self.assertEqual(core.asr_info(self.w.name)["proof"]["by"], "claude")
        self.assertIn("공을 놓칠까요?", (self.d / "subtitles.srt").read_text(encoding="utf-8"))


class SoundAlignTest(unittest.TestCase):
    def test_align_to_sound(self):
        blobs = [[9.17, 9.75], [10.54, 11.06], [11.2, 12.2], [58.62, 59.11], [59.49, 61.0], [136.6, 137.18], [137.62, 138.2]]
        segs = [{"start": 9.12, "end": 12.2, "text": "자, 첫 번째 갑니다.", "words": [W("자,", 9.12, 9.28), W("첫", 9.58, 10.15), W("번째", 10.15, 11.0),
                                                                                W("갑니다.", 11.0, 12.2)]},
                {"start": 58.46, "end": 60.1, "text": "그래서 인사이드로", "words": [W("그래서", 58.46, 59.3), W("인사이드로", 59.3, 60.1)]},
                {"start": 136.78, "end": 138.2, "text": "세 번째", "words": [W("세", 136.78, 137.78), W("번째", 137.78, 138.2)]}]
        out, n = msg.align_to_sound(segs, blobs)
        a, b, c = (s["words"] for s in out)
        self.assertEqual(a[0]["s"], 9.12)            # 추임새 낱말은 그대로
        self.assertEqual(a[1]["s"], 10.54)           # 추임새 덩어리에서 시작한 낱말 → 다음 소리
        self.assertEqual(b[0]["s"], 58.62)           # 조용한 틈에서 시작 → 바로 다음 소리
        self.assertEqual(b[1]["s"], 59.49)
        self.assertEqual(c[0]["s"], 137.62)          # 앞 추임새 소리까지 늘여 적은 한 글자 낱말
        self.assertEqual(out[2]["start"], 137.62)
        self.assertTrue(all(x["s"] < x["e"] for s in out for x in s["words"]))
        self.assertGreaterEqual(n, 4)
        self.assertEqual(msg.align_to_sound(segs, []), (segs, 0))

    def test_blob_fillers_and_snap(self):
        blobs = [[5.0, 6.7], [7.32, 7.89], [8.51, 9.6], [58.62, 59.11], [59.49, 61.0], [70.0, 70.4], [71.0, 72.0]]
        words = [(5.0, 6.7, "알아볼게요.", 0), (7.32, 7.9, "어...", 1), (8.51, 9.0, "왜", 1), (9.0, 9.6, "다들", 1),
                 (58.62, 59.3, "그래서", 2), (59.49, 60.1, "인사이드로", 2), (71.0, 72.0, "좋아요", 3)]
        f = msg.blob_fillers(blobs, words, onsets=[70.1])
        self.assertEqual([(a, b) for a, b, _ in f], [(7.29, 8.45)])  # '어...' 덩어리만 (공 소리 덩어리·'그래서'는 아님)
        cuts = msg.snap_edges([{"in": 58.3, "out": 61.2}], blobs, words, [(56.0, 58.4, "슬레이트 말")])
        self.assertEqual(cuts[0]["in"], 58.62 - msg.SNAP_PRE)  # 첫소리 앞 여유 (round4: 문장 사이 숨)

    def test_split_glued_slate(self):
        segs = [{"start": 41.8, "end": 46.9, "text": "두 번째 동작은 패스하고 옆으로, 아니다, 다시 할게요.",
                 "words": [W(t, 41.8 + k * 0.6, 42.3 + k * 0.6) for k, t in enumerate("두 번째 동작은 패스하고 옆으로, 아니다, 다시 할게요.".split())]},
                {"start": 48.2, "end": 53.3, "text": "두 번째 동작은 패스하고 옆으로 빠지면서 다시 공을 받는 거예요.",
                 "words": [W(t, 48.2 + k * 0.5, 48.6 + k * 0.5) for k, t in enumerate("두 번째 동작은 패스하고 옆으로 빠지면서 다시 공을 받는 거예요.".split())]}]
        lines = msg._split_slates(msg.lines_of(segs))
        self.assertEqual([x["text"] for x in lines][:2], ["두 번째 동작은 패스하고 옆으로,", "아니다, 다시 할게요."])
        import takes
        junk = takes.find_junk(lines)
        self.assertEqual(junk[0][:2], (41.8, 48.2))

    def test_pace_pauses(self):
        cuts = [{"in": 0.0, "out": 5.0}, {"in": 5.6, "out": 10.0}, {"in": 11.5, "out": 20.0}, {"in": 20.8, "out": 30.0}, {"in": 34.0, "out": 60.0}]
        out = msg.pace_pauses(cuts, [(20.1, 20.7, "추임새")], 1.0)
        # 꼭 자를 곳: 군말이 든 쉼(20.0~20.8) · 긴 쉼(30~34) / 1분 1곳 → 나머지 중 가장 긴 쉼은 예산 밖이라 이어 붙임
        self.assertEqual([(c["in"], c["out"]) for c in out], [(0.0, 20.0), (20.8, 30.0), (34.0, 60.0)])
        out = msg.pace_pauses(cuts, [], 6.0)
        self.assertEqual(len(out), len(cuts))


class SfxTopUpTest(unittest.TestCase):
    def test_quiet_style_gets_soft_sounds_up_to_band(self):
        """효과음을 아끼는 스타일(다큐)도 보통이면 소리 사건이 1분 범위 아래쪽까지는 있게 — 소리 없는 확대 순간에 휙을 고르게 더함 (담백은 그대로)."""
        def make():
            ev = [{"id": f"p{k}", "kind": "punch", "t": t, "src": None, "refs": {"items": []}} for k, t in enumerate((10.0, 11.0, 24.0, 55.0))]
            ev.append({"id": "s", "kind": "situ", "t": 40.0, "src": None, "refs": {"titles": ["t1"], "sfx": [0]}})  # 이미 소리 있음
            return types.SimpleNamespace(pos=60.0, titles=[{"id": "t1", "start": 40.0}], events=ev, sfx=[(40.0, "딸깍", None, "situ")])
        B = make()
        kept = [40.0]
        n = msg._top_up_sfx(B, {"palette": "cinematic"}, "보통", 1, kept, ())
        self.assertEqual(n, 2)  # 1분 3.3 → 4개 = 곡 1 + 있던 효과음 1 + 더한 2
        added = sorted(x[0] for x in B.sfx[1:])
        self.assertEqual(added, [10.0, 55.0])  # 다른 소리에서 가장 먼 곳부터 (11초는 10초 바로 옆이라 안 고름)
        self.assertTrue(all(x[1] == "휙" and x[2] < 0 for x in B.sfx[1:]))
        self.assertTrue(all(abs(a - b) >= msg.TOPUP_GAP for a in kept for b in kept if a != b))
        self.assertEqual(sum(1 for e in B.events if (e["refs"].get("sfx") or []) and e["kind"] == "punch"), 2)  # 사건에 묶여 함께 빠짐
        B = make()
        self.assertEqual(msg._top_up_sfx(B, {"palette": "cinematic"}, "담백", 1, [40.0], ()), 0)


class SfxLevelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="효과음 크기 "))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_quiet_shipped_sound_is_normalized_and_old_cache_redone(self):
        fn = sfxlib.ensure_sfx("뻥", self.tmp)  # kick_1.ogg 는 원래 -50 dBFS 안팎
        self.assertAlmostEqual(sfxlib.peak_db(self.tmp / fn), sfxlib.PEAK_DB, delta=0.3)
        # 예전 판이 만든 작은 파일 → 다시 만듦
        x = [[0.003, 0.003]] * 4800
        (self.tmp / fn).write_bytes(sfxlib._wav_bytes(x))
        self.assertLess(sfxlib.peak_db(self.tmp / fn), -40)
        sfxlib.ensure_sfx("뻥", self.tmp)
        self.assertAlmostEqual(sfxlib.peak_db(self.tmp / fn), sfxlib.PEAK_DB, delta=0.3)

    def test_level_uses_speech_peak_and_file_peak(self):
        # 기준은 목소리 보정을 거친 말소리 크기(voicePk · round4: 효과음은 보정 없이 섞음) · 없으면 원본 말소리에서 어림(_voice_est)
        sig = {"speechPk": -14.0, "voicePk": -10.0, "peakDb": -2.0}
        self.assertEqual(msg._sfx_level(sig, "휙", "replay", -3.0), round(-10.0 - msg.SFX_GAP + 3.0, 1))
        self.assertEqual(msg._sfx_level(sig, "짠", "title", -20.0), round(-10.0 - msg.SFX_SOFT + 20.0, 1))  # 작은 파일은 그만큼 키움
        self.assertEqual(msg._sfx_level({"speechPk": -14.0}, "휙", "replay", -3.0), round(msg._voice_est(-14.0) - msg.SFX_GAP + 3.0, 1))
        self.assertEqual(msg._sfx_level({"peakDb": -4.0}, "휙", "replay", -3.0), round(msg._voice_est(-4.0 - 2.0) - msg.SFX_GAP + 3.0, 1))  # 예전 신호


class TextLayoutTest(unittest.TestCase):
    def build(self, titles, fmt="long"):
        B = msg._Build("x.mp4", {"duration": 60.0}, fmt)
        for t in titles:
            tt = B.title(t[0], t[1], t[2], dict(editor.TITLE_STYLE, **t[3]))
            B.event(t[4], t[1], t[0], refs={"titles": [tt["id"]]})
        return B

    def test_safe_area_overlap_and_two_at_once(self):
        B = self.build([("2/5 · 1골", 10.0, 2.4, dict(size=56, x=0.94, y=0.14, align="right", bgOn=True, bg="#111111", bgOpacity=0.75), "score"),
                        ("다시 보기 ▶", 10.5, 4.0, dict(size=44, x=0.95, y=0.12, align="right", bgOn=True, bg="#000000", bgOpacity=0.6, strokeW=10), "replay"),
                        ("1", 20.0, 0.4, dict(size=120, y=0.5), "count"), ("핵심!", 20.1, 1.6, dict(size=96, y=0.5), "emphasis"),
                        ("나이스!!", 20.2, 1.1, dict(size=132, y=0.5), "fx")])
        caps = [{"start": 0.0, "end": 60.0, "text": "말 자막이 계속 나와요"}]
        gone = msg.layout_titles(B, caps, dict(editor.LONG_STYLE, size=58, strokeW=7), 60.0)
        boxes = {t["text"]: (t, msg.text_box(t["text"], t["style"], 1920, 1080)) for t in B.titles}
        for t, b in boxes.values():
            self.assertGreaterEqual(b[0], 0.05 * 1920 - 0.5, t["text"])
            self.assertLessEqual(b[2], 0.95 * 1920 + 0.5, t["text"])
            self.assertGreaterEqual(t["dur"], msg.MIN_TEXT - 1e-6)
        sb, rp = boxes["2/5 · 1골"][1], boxes["다시 보기 ▶"][1]
        self.assertLess(msg._overlap(sb, rp), 0.05)  # 점수판 밑에 가려지지 않음
        on = [t for t in B.titles if t["start"] <= 20.25 < t["start"] + t["dur"]]
        self.assertLessEqual(len(on), 2)
        self.assertTrue(gone or any(t["start"] > 20.2 for t in B.titles if t["text"] == "나이스!!"))

    def test_caption_band_avoided(self):
        B = self.build([("오오!", 5.0, 1.0, dict(size=132, y=0.6), "fx")])
        caps = [{"start": 4.0, "end": 7.0, "text": "가운데 노래방 자막"}]
        msg.layout_titles(B, caps, dict(editor.LONG_STYLE, size=76, y=0.62), 60.0)
        cb = msg.text_box("가운데 노래방 자막", dict(editor.LONG_STYLE, size=76, y=0.62), 1920, 1080)
        tb = msg.text_box("오오!", B.titles[0]["style"], 1920, 1080)
        self.assertLessEqual(min(tb[3], cb[3]) - max(tb[1], cb[1]), 0)

    def test_short_timeline_caption_extended(self):
        seq = {"items": editor._pair(0.0, 10.0), "captions": [{"id": "a", "start": 1.0, "end": 1.4, "text": "완벽해요."},
                                                             {"id": "b", "start": 1.4, "end": 4.0, "text": "다음 말이에요."}], "format": "long"}
        tl = editor.timeline_captions(seq)
        self.assertGreaterEqual(tl[0]["end"] - tl[0]["start"], editor.CAP_MIN - 1e-6)
        self.assertLessEqual(tl[0]["end"], tl[1]["start"] + 1e-6)

    def test_msg_caps_override(self):
        seq = {"items": editor._pair(0.0, 10.0), "captions": [{"id": "a", "start": 1.0, "end": 3.0, "text": "옛 자막"}],
               "msgCaps": [{"id": "b", "start": 1.0, "end": 3.0, "text": "새 자막"}], "format": "long"}
        self.assertEqual([c["text"] for c in editor.timeline_captions(seq)], ["새 자막"])


class TextSemanticsTest(unittest.TestCase):
    def test_no_fragment_emphasis_and_no_rude_texts(self):
        self.assertEqual(msg._emph_short("완전히 달라져요!", "이 세 가지만 바꿔도 첫 터치가 완전히 달라져요."), "완전히 달라져요!")
        lab = msg._emph_short("이 세 가지만 바꿔도 첫 터치가 완전히 달라져요.", "이 세 가지만 바꿔도 첫 터치가 완전히 달라져요.")
        self.assertNotEqual(lab, "달라!")
        self.assertTrue(lab.endswith("달라져요!"), lab)
        texts = [t for v in msg.INNER_TEXTS.values() for t in v] + [t for v in msg.FX_TEXTS.values() for t in v]
        self.assertNotIn("(웃음 참는 중)", texts)
        self.assertNotIn("슝~", texts)

    def test_topic_uses_short_title_with_keyword(self):
        """제목 카드 주제: 주제어('패스')가 든 짧은 영상 제목이면 제목 그대로 · 아니면 주제어."""
        segs = [{"text": "패스하고 바로 움직여요. 패스를 하는 순간이 중요해요. 패스 연습이에요."}]
        self.assertEqual(msg._topic(segs, "20261007_MSGRAW02_패스 앤 무브 드릴.mp4"), "패스 앤 무브 드릴")
        self.assertEqual(msg._topic(segs, "IMG_1234.mp4"), "패스")
        self.assertEqual(msg._topic(segs, "20261007_X_[꿀팁] 풋살 패스 이것만 알면 실력이 달라집니다.mp4"), "패스")  # 긴 제목은 그대로 안 씀

    def test_retry_label_only_after_fail(self):
        """시범 상황 자막 '다시 도전'은 바로 앞에 실패가 있을 때만 (패스 드릴: 실패 37초 뒤 리듬 시범에 '다시 도전' 이 붙었음)."""
        pl = lambda a: {"kind": "play", "t": a + 2, "a": a, "b": a + 5}  # noqa: E731
        plays = [pl(26.0), pl(54.0), pl(72.0), pl(98.4)]
        moms = plays + [{"kind": "fail", "t": 61.3, "a": 61.3, "b": 64.1}]
        lab = msg._demo_labels(plays, moms)
        self.assertEqual([lab[id(m)] for m in plays], ["실전 시범", "한 번 더!", "다시 도전", None])  # 다 쓰면 더 안 붙임 (round2: 시범 중간의 예고 말)

    def test_scoreboard_final_and_last_try(self):
        self.assertTrue(msg.SECTION.search("마지막 다섯 번째."))
        moms = [{"kind": "total", "t": 1.0, "text": "다섯", "a": 1, "b": 2, "score": 1}]
        t = 10.0
        for k, (said, res) in enumerate([("첫 번째 갑니다.", "fail"), ("두 번째 슛.", "success"), (None, "success"), ("네 번째 슛.", "fail"),
                                         ("마지막 다섯 번째.", "success")]):
            if said:
                moms.append({"kind": "section", "t": t, "text": said, "a": t, "b": t + 1, "score": 1})
            moms.append({"kind": res, "t": t + 5.0, "text": "들어갔어요." if res == "success" else "아깝다.", "a": t + 5, "b": t + 6, "score": 1})
            t += 10.0
        board = msg._scoreboard(sorted(moms, key=lambda m: m["t"]))
        self.assertEqual([b["text"] for b in board], ["1/5 · 0골", "2/5 · 1골", "3/5 · 2골", "4/5 · 2골", "5/5 · 3골"])
        self.assertEqual(msg._situ_text({"kind": "section", "text": "마지막 다섯 번째."}, "슛 차서 넣으면"), "마지막! 다섯 번째 도전")


class GovernTest(unittest.TestCase):
    def test_drops_text_that_sits_on_a_cut(self):
        """양을 넘으면 컷과 같은 때 뜬 속마음 글자는 빼도 멈춘 화면이 안 생기니 뺌 (예전: 근처 컷까지 없는 셈 치고 못 뺌 → 보통·듬뿍이 범위를 넘음)."""
        items = []
        for k in range(10):  # 6초마다 점프 컷 (원본에서 0.5초씩 건너뜀) → 컷 9번
            v, a = editor._pair(k * 6.5, k * 6.5 + 6.0, start=k * 6.0)
            items += [v, a]
        titles = [{"id": f"t{k}", "start": 6.0 * k + 0.05, "end": 6.0 * k + 1.5, "text": "(머쓱)"} for k in (1, 2, 3, 4)]
        events = [{"id": f"e{k}", "kind": "inner", "t": 6.0 * k + 0.05, "refs": {"titles": [f"t{k}"]}} for k in (1, 2, 3, 4)]
        B = types.SimpleNamespace(items=items, titles=titles, shapes=[], events=events, sfx=[], src_to_tl=lambda t: t)
        total = 60.0
        self.assertEqual(len(msg.visual_events(B.items, B.titles, B.shapes, total)), 13)
        msg.govern(B, "보통", [0.5, 0.4], total)
        ev = msg.visual_events(B.items, B.titles, B.shapes, total)
        self.assertLessEqual(len(ev), msg.DENSITY["보통"][1] * total / 60.0)
        self.assertEqual(sum(1 for _, k in ev if k == "cut"), 9)  # 컷은 그대로 (글자만 뺌)
        ts = [0.0] + [t for t, _ in ev] + [total]
        self.assertLessEqual(max(b - a for a, b in zip(ts, ts[1:])), msg.DENSITY["보통"][2])


class BuildTest(unittest.TestCase):
    """시험 원본으로 만든 후보: 효과음 간격·소리 사건 수 · 멈춘 화면 한도 · 담백 레슨형 자막 모양 · 자막(msgCaps)."""

    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()
        styles = [{"kind": "preset", "name": n} for n in ("예능 MSG형", "담백 레슨형")]
        cls.res = {k: msg.build_variants(cls.w.name, styles, k, ("long",), lambda m: None) for k in msg.INTENSITY}

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_sfx_spacing_and_audio_budget(self):
        for k, r in self.res.items():
            for q in r["sequences"]:
                sfx = sorted(it["start"] for it in q["items"] if it["track"] in ("A2", "A5"))
                self.assertTrue(all(b - a >= msg.SFX_MIN_GAP - 1e-6 for a, b in zip(sfx, sfx[1:])), (q["name"], sfx))
                mins = editor.seq_total(q) / 60.0
                self.assertLessEqual(len(sfx), max(0, int(msg.AUDIO_DENSITY[k][1] * mins) - len(q["msg"]["bgm"])), q["name"])
                self.assertLessEqual(len(q["msg"]["bgm"]), max(2, int(msg.AUDIO_DENSITY[k][1] * mins * 0.45)), q["name"])
                self.assertEqual(q["master"]["lufs"], -14.0)

    def test_static_limit_and_density_order(self):
        for k, r in self.res.items():
            for q in r["sequences"]:
                tot = editor.seq_total(q)
                ev = msg.visual_events(q["items"], q["titles"], q["shapes"], tot)
                ts = [0.0] + [t for t, _ in ev] + [tot]
                gaps = [(b - a, a) for a, b in zip(ts, ts[1:])]
                self.assertLessEqual(max(gaps)[0], msg.DENSITY[k][2] + 0.05, (q["name"], max(gaps)))
        n = {k: len(msg.visual_events(r["sequences"][0]["items"], r["sequences"][0]["titles"], r["sequences"][0]["shapes"],
                                      editor.seq_total(r["sequences"][0]))) for k, r in self.res.items()}
        self.assertLessEqual(n["담백"], n["보통"])
        self.assertLessEqual(n["보통"], n["듬뿍"])

    def test_clean_lesson_has_own_caption_look(self):
        q0, q1 = self.res["보통"]["sequences"]
        self.assertFalse(q0["captionStyle"].get("bgOn"))
        self.assertTrue(q1["captionStyle"].get("bgOn"))  # 담백 레슨형: 상자 말 자막

    def test_captions_travel_with_candidates(self):
        r = self.res["보통"]
        self.assertTrue(r["captions"])
        self.assertIsNone(r["captionsOld"])  # 다시 듣지 않은 받아쓰기
        for q in r["sequences"]:  # 자막은 결과에 한 번만 (후보마다 같은 자막을 붙이면 긴 원본에서 결과가 몇 MB · round5)
            self.assertNotIn("msgCaps", q)


if __name__ == "__main__":
    unittest.main()
