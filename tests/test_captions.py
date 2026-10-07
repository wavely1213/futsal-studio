"""단어 단위 받아쓰기·용어 사전·자막 나누기 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_captions

captions(chunk · from_segments · apply_dict · fix_words · 사전 파일) · core.analyze/analyze_many(단어 시각·사전 힌트·모델 한 번만·절전 막기)
· editor(새 프로젝트 자막 · 노래방 \\kf · 예전 받아쓰기) · takes.find_fillers · /api/dict.
받아쓰기 모델은 흉내(가짜 faster_whisper)만 쓰고, 시험 영상은 ffmpeg lavfi 로 만듦. 인터넷은 쓰지 않음."""
import ctypes
import inspect
import json
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from collections import namedtuple
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import captions  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import takes  # noqa: E402

FILLER = "추임새"
# core.analyze 가 함수 안에서 처음 불러오면 mock.patch.dict(sys.modules) 가 끝날 때 지워져 다시 못 불러옴 → 미리 불러 둠
PRELOADED = (ctypes, numpy)


def W(w, s, e, p=0.9):
    return {"w": w, "s": s, "e": e, "p": p}


def make_words(n=60, seed=7):
    """그럴듯한 한국어 말 60단어 (1~5글자 · 가끔 문장부호 · 짧은 쉼과 0.8초 쉼) + 혼자서도 너무 긴 단어 둘."""
    rnd, syl = random.Random(seed), "공발패스터치슈팅받아요돌고빠르게몸을열어서앞으로보세"
    t, out = 0.4, []
    for k in range(n):
        if k == 21:
            w, d = "가나다라마바사아자차카타파하", 2.6  # 14글자: 쇼츠(12글자·2초)보다 김
        elif k == 47:
            w, d = "가나다라마바사아자차카타파하가나다라마바사아자차", 3.8  # 24글자·3.8초: 롱폼보다도 김
        else:
            w = "".join(rnd.choice(syl) for _ in range(rnd.randint(1, 5))) + (rnd.choice(".,?") if rnd.random() < 0.15 else "")
            d = 0.09 * len(w) + rnd.uniform(0.05, 0.2)
        out.append(W(w, round(t, 2), round(t + d, 2)))
        t += d + rnd.choice([0.03, 0.08, 0.12, 0.2, 0.3, 0.8])
    return out


class ChunkTest(unittest.TestCase):
    """captions.chunk — 단어 사이에서만 나누고 글자 수·길이 한도를 지킴."""

    def check(self, words, fmt, strict=True):
        """strict=False: 0.6초보다 짧은 자막은 앞뒤 어느 쪽과 합쳐도 한도를 넘을 때만 (너무 긴 단어·긴 쉼 옆)."""
        mc, md = captions.LIMITS[fmt]
        chunks = captions.chunk(words, fmt)
        self.assertTrue(chunks)
        got = [(w["w"], w["s"], w["e"]) for c in chunks for w in c["words"]]
        self.assertEqual(got, [(w["w"], w["s"], w["e"]) for w in words])  # 모든 단어가 순서대로 한 번씩

        def mergeable(a, b):
            return (len(re.sub(r"\s+", "", a["text"] + b["text"])) <= mc and b["end"] - a["start"] <= md
                    and b["start"] - a["end"] < captions.GAP_MAX)
        for k, c in enumerate(chunks):
            ws, dur = c["words"], c["end"] - c["start"]
            self.assertEqual((c["start"], c["end"]), (ws[0]["s"], ws[-1]["e"]))  # 첫 단어 시작 ~ 마지막 단어 끝
            self.assertEqual(c["text"].split(), [w["w"] for w in ws])  # 단어 안에서는 안 나눔
            chars = len(re.sub(r"\s+", "", c["text"]))
            long_word = len(ws) == 1 and (chars > mc or dur > md)
            if not long_word:
                self.assertLessEqual(chars, mc, c)
                self.assertLessEqual(dur, md + 1e-9, c)
                stuck = not strict and not (k and mergeable(chunks[k - 1], c)) and not (k + 1 < len(chunks) and mergeable(c, chunks[k + 1]))
                if not stuck:
                    self.assertGreaterEqual(dur, captions.MIN_DUR - 1e-9, c)
            self.assertNotIn("\n", c["text"])  # 쇼츠·롱폼 모두 언제나 한 줄
        return chunks

    def test_sixty_words_shorts_and_long(self):
        words = make_words()
        for fmt in ("shorts", "long"):
            with self.subTest(fmt):
                chunks = self.check(words, fmt)
                longs = [c for c in chunks if len(c["words"]) == 1 and c["words"][0]["w"].startswith("가나다")]
                self.assertEqual(len(longs), 2 if fmt == "shorts" else 1)  # 너무 긴 단어는 혼자
        for seed in range(1, 8):  # 다른 말들로도
            for fmt in ("shorts", "long"):
                with self.subTest(seed=seed, fmt=fmt):
                    self.check(make_words(seed=seed), fmt, strict=False)

    def test_long_pause_splits(self):
        words = [W("공을", 0.0, 0.3), W("받고", 0.35, 0.7), W("돌아서", 2.0, 2.4), W("슈팅", 2.45, 2.9)]  # 1.3초 쉼
        chunks = captions.chunk(words, "long")
        self.assertEqual([c["text"] for c in chunks], ["공을 받고", "돌아서 슈팅"])

    def test_sentence_end_and_terms_kept(self):
        words = [W("오늘은", 0.0, 0.3), W("퍼스트", 0.35, 0.65), W("터치를", 0.7, 1.0), W("배워볼게요.", 1.05, 1.8),
                 W("공이", 2.3, 2.5), W("오면", 2.55, 2.8), W("발", 2.85, 3.0), W("안쪽으로", 3.05, 3.5), W("받아요.", 3.55, 4.2)]
        shorts = [c["text"] for c in captions.chunk(words, "shorts", terms=["퍼스트 터치"])]
        self.assertEqual(shorts, ["오늘은 퍼스트 터치를", "배워볼게요.", "공이 오면 발 안쪽으로 받아요."])  # 11글자·1.9초는 한 줄에
        long = [c["text"] for c in captions.chunk(words, "long", terms=["퍼스트 터치"])]
        self.assertEqual(long, ["오늘은 퍼스트 터치를 배워볼게요.", "공이 오면 발 안쪽으로 받아요."])  # 문장마다 한 줄
        self.assertEqual(captions.chunk([], "long"), [])
        self.assertEqual(captions.chunk([W(" ", 0, 1)], "shorts"), [])

    def test_tiny_tail_merged(self):
        # '요.' 하나만 남는 0.2초 자투리는 앞과 합침
        words = [W("빠르게", 0.0, 0.4), W("돌아서", 0.45, 0.9), W("패스해", 0.95, 1.5), W("요.", 1.52, 1.72)]
        self.assertEqual([c["text"] for c in captions.chunk(words, "shorts")], ["빠르게 돌아서 패스해 요."])

    def test_from_segments(self):
        old = [{"start": 1.0, "end": 3.0, "text": "예전 받아쓰기"}, {"start": 3.5, "end": 4.0, "text": "  "}]
        self.assertEqual(captions.from_segments(old, "long"), [{"start": 1.0, "end": 3.0, "text": "예전 받아쓰기"}])  # 예전과 똑같이
        segs = [{"start": 0.0, "end": 1.0, "text": "공을 받고", "words": [W("공을", 0.0, 0.4), W("받고", 0.45, 1.0)]},
                {"start": 1.1, "end": 2.5, "text": "단어 시각이 없는 구간"},
                {"start": 2.6, "end": 3.0, "text": "슛", "words": [W("슛", 2.6, 2.8)]}]
        caps = captions.from_segments(segs, "shorts")
        self.assertEqual([c["text"] for c in caps], ["공을 받고", "단어 시각이 없는 구간", "슛"])
        self.assertEqual(caps[0]["end"], 1.1)  # 0.1초 빈틈은 메움 (깜빡임 방지)
        self.assertNotIn("words", caps[1])
        self.assertEqual((caps[2]["start"], caps[2]["end"]), (2.6, 3.2))  # 0.2초짜리는 화면에 0.6초는 남김
        self.assertEqual(caps[2]["words"], [{"w": "슛", "s": 2.6, "e": 2.8}])


class DictTest(unittest.TestCase):
    """captions.apply_dict · fix_words · 사전 파일."""
    FIX = {"피버": "피벗", "풋살 사관학교": "풋살사관학교", "퍼스트터치": "퍼스트 터치"}

    def test_fix_only_on_word_boundaries(self):
        cases = {"피버": "피벗", "피버 자리에서 받아요": "피벗 자리에서 받아요", "피버, 픽소": "피벗, 픽소", "(피버)": "(피벗)",
                 "피버를 보고": "피벗을 보고", "피버가 내려와요": "피벗이 내려와요", "피버로": "피벗으로", "피버에서도": "피벗에서도",
                 "피버트": "피버트", "풋살피버": "풋살피버", "피버트를 쳐요": "피버트를 쳐요", "피버링": "피버링",
                 "오늘 풋살 사관학교 영상": "오늘 풋살사관학교 영상", "퍼스트터치를": "퍼스트 터치를"}
        for src, want in cases.items():
            self.assertEqual(captions.apply_dict(src, self.FIX), want, src)
        self.assertEqual(captions.apply_dict("피버", {}), "피버")
        self.assertEqual(captions.apply_dict("", self.FIX), "")

    def test_fix_words_keeps_times_and_one_word_per_item(self):
        ws = [W("오늘", 0.0, 0.3), W("풋살", 0.4, 0.7, 0.6), W("사관학교", 0.75, 1.3, 0.8), W("퍼스트터치를", 1.4, 2.0), W("피버로", 2.1, 2.5)]
        out, n = captions.fix_words(ws, self.FIX)
        self.assertEqual(n, 3)
        self.assertEqual([w["w"] for w in out], ["오늘", "풋살사관학교", "퍼스트", "터치를", "피벗으로"])
        self.assertEqual((out[1]["s"], out[1]["e"], out[1]["p"]), (0.4, 1.3, 0.6))  # 합친 단어: 처음 ~ 끝, 낮은 확신
        self.assertEqual((out[2]["s"], out[3]["e"]), (1.4, 2.0))  # 나눈 단어: 원래 시간 안에서
        self.assertEqual((out[4]["s"], out[4]["e"]), (2.1, 2.5))
        self.assertEqual(ws[1]["w"], "풋살")  # 넘긴 목록은 그대로

    def test_dict_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="용어 사전 "))
        self.addCleanup(shutil.rmtree, tmp, True)
        p = tmp / "풋살 작업" / "dict.json"
        d = captions.load_dict(p)  # 없으면 기본 사전
        self.assertGreaterEqual(len(d["terms"]), 30)
        for t in ("풋살사관학교", "최경진 감독", "피벗", "픽소", "아라", "고레이로", "토킥", "2대1 패스", "수비 라인"):
            self.assertIn(t, d["terms"])
        self.assertEqual(d["fix"]["피버"], "피벗")
        saved = captions.save_dict(p, {"terms": [" 파라렐라 ", "파라렐라", "", "가" * 31, 3, "피벗"],
                                       "fix": {"피버": "피벗", "같음": "같음", "빈칸": " "}})
        self.assertEqual(saved, {"terms": ["파라렐라", "피벗"], "fix": {"피버": "피벗"}})
        self.assertEqual(captions.load_dict(p), saved)
        self.assertFalse(p.with_name("dict.json.tmp").exists())
        captions.save_dict(p, {"terms": [], "fix": {}})  # 다 지우고 저장하면 빈 사전 그대로 (기본으로 안 돌아감)
        self.assertEqual(captions.load_dict(p), {"terms": [], "fix": {}})
        p.write_text("{깨진", encoding="utf-8")
        self.assertEqual(captions.load_dict(p), captions.default_dict())
        with self.assertRaises(ValueError):
            captions.save_dict(p, {"terms": "피벗"})
        real, calls = os.replace, []

        def flaky(a, b):  # Windows: 백신이 잠깐 잡고 있음 → 잠깐 뒤 다시
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("잠김")
            return real(a, b)
        with mock.patch.object(captions.os, "replace", flaky), mock.patch.object(captions.time, "sleep"):
            captions.save_dict(p, {"terms": ["킥인"]})
        self.assertEqual((len(calls), captions.load_dict(p)["terms"]), (3, ["킥인"]))

    def test_echo_of_hint_list(self):
        # 실제 tiny 모델이 말소리 없는 곳에서 낸 것: 힌트 순서 그대로 · 확신 0.03
        heard = "풋살사관학교 최경진 감독 피벗 픽소 아라 고레이로 토킥 인사이로 토킥 인사이로".split()
        terms = captions.DEFAULT_TERMS
        self.assertTrue(captions.echo([W(w, k, k + 0.1, 0.03) for k, w in enumerate(heard)], terms))
        listing = [W(w, k, k + 0.1, 0.92) for k, w in enumerate("포지션은 피벗 픽소 아라 고레이로 이렇게 네 개예요".split())]
        self.assertFalse(captions.echo(listing, terms))  # 코치가 정말 말한 것 (확신 높음)
        self.assertFalse(captions.echo([dict(w, p=0.3) for w in listing[:3]], terms))  # 이어진 용어가 3개뿐
        normal = [W(w, k, k + 0.1, 0.2) for k, w in enumerate("피벗 자리에서 공을 받고 돌아서 슈팅".split())]
        self.assertFalse(captions.echo(normal, terms))
        self.assertFalse(captions.echo(None, terms))
        self.assertFalse(captions.echo([W(w, 0, 1, 0.01) for w in heard], []))

    def test_echo_keeps_coach_listing_positions(self):
        # 리뷰: 코치가 포지션을 사전 순서대로 늘어놓고 낯선 외래어라 확신이 낮아도 문장을 통째로 버리면 안 됨
        terms = captions.DEFAULT_TERMS
        pos = [W(w, k, k + 0.1, p) for k, (w, p) in enumerate(
            [("포지션은", .9), ("피벗", .45), ("픽소", .3), ("아라", .5), ("고레이로", .4), ("네", .9), ("가지예요.", .9)])]
        self.assertFalse(captions.echo(pos, terms))
        five = [W(w, k, k + 0.1, 0.2) for k, w in enumerate("피벗 픽소 아라 고레이로 토킥 순서로 볼게요".split())]
        self.assertFalse(captions.echo(five, terms))  # 용어 5개 연속 + 진짜 말
        intro = [W(w, k, k + 0.1, 0.3) for k, w in enumerate("안녕하세요 풋살사관학교 최경진 감독입니다".split())]
        self.assertFalse(captions.echo(intro, terms))

    def test_prompt_budget(self):
        terms = captions.DEFAULT_TERMS
        p = captions.prompt(terms, 120, lambda s: 2 * len(s))
        self.assertTrue(p.startswith("풋살 강의예요. 풋살사관학교, 최경진 감독"))
        self.assertLessEqual(2 * len(p), 120 + 4)
        self.assertEqual(captions.hotwords(terms, 1000), " ".join(terms))
        self.assertEqual(captions.prompt([], 120), "")


Word = namedtuple("Word", "start end word probability")


class FakeModel:
    """faster_whisper.WhisperModel 흉내: 만든 횟수·받은 옵션을 기록하고 정해 둔 구간을 돌려줌."""
    made, calls = [], []

    def __init__(self, *a, **k):
        FakeModel.made.append((a, k))
        self.hf_tokenizer = None

    def transcribe(self, audio, language=None, vad_filter=False, word_timestamps=False, initial_prompt=None, hotwords=None):
        FakeModel.calls.append({"word_timestamps": word_timestamps, "initial_prompt": initial_prompt, "hotwords": hotwords})
        ws = [Word(0.2, 0.6, " 피버를", 0.81), Word(0.65, 0.9, " 보고", 0.95), Word(0.95, 1.5, " 패스해요.", 0.9)]
        seg = types.SimpleNamespace(start=0.2, end=1.5, text=" 피버를 보고 패스해요.", words=ws if word_timestamps else None)
        return iter([seg]), types.SimpleNamespace(duration=2.0)


class EchoModel(FakeModel):
    """말소리가 불분명한 곳에서 힌트 목록을 따라 쓴 구간 + 진짜 말."""

    def transcribe(self, audio, **k):
        FakeModel.calls.append(k)
        bad = [Word(0.0 + 0.1 * i, 0.1 + 0.1 * i, " " + w, 0.04) for i, w in enumerate("풋살사관학교 최경진 감독 피벗 픽소 아라".split())]
        good = [Word(1.0, 1.4, " 피벗이", 0.9), Word(1.45, 1.9, " 받아요", 0.95)]
        segs = [types.SimpleNamespace(start=0.0, end=0.7, text=" ".join(w.word for w in bad), words=bad),
                types.SimpleNamespace(start=1.0, end=1.9, text=" 피벗이 받아요", words=good)]
        return iter(segs), types.SimpleNamespace(duration=2.0)


class PositionsModel(FakeModel):
    """코치가 포지션을 늘어놓는 진짜 문장 (외래어라 확신이 낮음)."""

    def transcribe(self, audio, **k):
        FakeModel.calls.append(k)
        ws = [Word(0.2 + 0.4 * i, 0.5 + 0.4 * i, " " + w, p) for i, (w, p) in enumerate(
            [("포지션은", .9), ("피벗", .45), ("픽소", .3), ("아라", .5), ("고레이로", .4), ("네", .9), ("가지예요.", .9)])]
        return iter([types.SimpleNamespace(start=0.2, end=3.0, text="".join(w.word for w in ws), words=ws)]), \
            types.SimpleNamespace(duration=3.0)


class OldModel(FakeModel):
    """hotwords 를 모르는 예전 faster-whisper."""

    def transcribe(self, audio, language=None, vad_filter=False, word_timestamps=False, initial_prompt=None):
        return FakeModel.transcribe(self, audio, language, vad_filter, word_timestamps, initial_prompt)


class WorkDir(unittest.TestCase):
    """한글·띄어쓰기 작업 폴더 (core.WORK·VIDEOS·ANALYSIS·OUT · editor.PROJECTS·ASSETS)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="자막 테스트 "))
        self.work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": self.work, "VIDEOS": self.work / "videos", "ANALYSIS": self.work / "analysis", "OUT": self.work / "out"}
        for d in list(dirs.values()) + [self.work / "projects", self.work / "edit_media"]:
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        self.patches += [mock.patch.object(editor, "PROJECTS", self.work / "projects"), mock.patch.object(editor, "ASSETS", self.work / "edit_media")]
        for p in self.patches:
            p.start()
        FakeModel.made, FakeModel.calls = [], []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        core.set_progress()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def video(self, name, size="160x90", dur=2.0):
        r = core.run([core.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={size}:r=10:d={dur}",
                      "-f", "lavfi", "-i", f"sine=f=440:sample_rate=16000:d={dur}", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", str(core.VIDEOS / name)])
        self.assertEqual(r.returncode, 0, r.stderr)
        return name

    def transcript(self, name, segs):
        d = core.adir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": []}), encoding="utf-8")

    def whisper(self, model=FakeModel):
        return mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=model)})


class AnalyzeTest(WorkDir):
    """core.analyze · analyze_many — 단어 시각 · 용어 사전 · 모델 한 번만 · 절전 막기."""

    def test_words_and_dict(self):
        name = self.video("코치 설명 [풋살 꿀팁].mp4")
        with self.whisper():
            core.analyze(name, lambda m: None)
        seg = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(set(seg), {"start", "end", "text", "words"})
        self.assertEqual(seg["words"][0], {"w": "피벗을", "s": 0.2, "e": 0.6, "p": 0.81})  # 기본 사전으로 고침 + 조사도
        self.assertEqual(seg["text"], " ".join(w["w"] for w in seg["words"]).strip())
        self.assertEqual(seg["text"], "피벗을 보고 패스해요.")
        self.assertIn("피벗을 보고", (core.adir(name) / "subtitles.srt").read_text(encoding="utf-8"))
        call = FakeModel.calls[0]
        self.assertTrue(call["word_timestamps"])
        self.assertIn("풋살사관학교", call["initial_prompt"])
        self.assertIn("최경진 감독", call["hotwords"])  # 설치된 버전이 hotwords 를 받으면
        self.assertEqual(FakeModel.made[0][1]["cpu_threads"], min(8, os.cpu_count() or 4))
        self.assertEqual(core._WHISPER, {})  # 끝나면 모델을 내려놓음

    def test_hotwords_only_when_supported_and_user_dict(self):
        name = self.video("촬영 1.mp4")
        captions.save_dict(core.dict_path(), {"terms": ["파라렐라"], "fix": {"패스해요": "패스!"}})
        self.assertTrue(core.dict_path().is_relative_to(core.WORK))
        with self.whisper(OldModel):
            core.analyze(name, lambda m: None)
        self.assertNotIn("hotwords", inspect.signature(OldModel.transcribe).parameters)
        self.assertIsNone(FakeModel.calls[0]["hotwords"])
        self.assertEqual(FakeModel.calls[0]["initial_prompt"], "풋살 강의예요. 파라렐라.")
        seg = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(seg["text"], "피버를 보고 패스!.")  # 내 사전엔 '피버'가 없음

    def test_hint_echo_dropped(self):
        name = self.video("운동장 소리.mp4")
        logs = []
        with self.whisper(EchoModel):
            core.analyze(name, logs.append)
        segs = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual([s["text"] for s in segs], ["피벗이 받아요"])
        self.assertIn("용어 목록만 잘못 받아쓴 1곳은 뺐어요", logs[-1])
        self.assertTrue(all(type(w["s"]) is float for w in segs[0]["words"]))

    def test_positions_sentence_kept(self):
        name = self.video("포지션 설명.mp4")
        logs = []
        with self.whisper(PositionsModel):
            core.analyze(name, logs.append)
        segs = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual([s["text"] for s in segs], ["포지션은 피벗 픽소 아라 고레이로 네 가지예요."])
        self.assertNotIn("뺐어요", logs[-1])
        srt = (core.adir(name) / "subtitles.srt").read_text(encoding="utf-8")
        self.assertIn("고레이로", srt)

    def test_analyze_many_loads_model_once_and_keeps_pc_awake(self):
        names = [self.video(f"촬영 {k}.mp4", dur=1.0) for k in (1, 2, 3)]
        state = []
        fake = types.SimpleNamespace(kernel32=types.SimpleNamespace(SetThreadExecutionState=mock.Mock(side_effect=lambda v: state.append(v) or 0x80000000)))
        with self.whisper(), mock.patch("ctypes.windll", fake, create=True):
            out = core.analyze_many(names, lambda m: None)
        self.assertEqual(len(out), 3)
        self.assertEqual(len(FakeModel.made), 1)  # 세 영상에 모델은 한 번
        self.assertEqual(len(FakeModel.calls), 3)
        self.assertEqual(state, [core.ES_CONTINUOUS | core.ES_SYSTEM_REQUIRED, core.ES_CONTINUOUS])  # 묶음 처음에 켜고 끝에 원래대로
        self.assertEqual(core._WHISPER, {})
        with self.whisper():  # 다음 묶음은 다시 불러옴
            core.analyze_many(names[:1], lambda m: None)
        self.assertEqual(len(FakeModel.made), 2)

    def test_awake_restored_on_failure(self):
        state = []
        fake = types.SimpleNamespace(kernel32=types.SimpleNamespace(SetThreadExecutionState=mock.Mock(side_effect=lambda v: state.append(v) or 0)))
        with self.whisper(), mock.patch("ctypes.windll", fake, create=True), self.assertRaises(RuntimeError):
            core.analyze_many(["없는 영상.mp4"], lambda m: None)
        self.assertEqual(state, [core.ES_CONTINUOUS | core.ES_SYSTEM_REQUIRED, core.ES_CONTINUOUS])
        self.assertEqual(getattr(core._SESSION, "depth", 0), 0)
        self.assertIsNone(core._keep_awake(True))  # Linux 개발 PC: 아무 일도 안 함


class EditorCaptionTest(WorkDir):
    """editor — 새 프로젝트 자막 · 편집점 다시 찾기 · 노래방 \\kf · 예전 받아쓰기."""
    SEGS = [{"start": 0.5, "end": 3.75, "text": "오늘은 퍼스트 터치를 배워볼게요. 공이 오면",
             "words": [W("오늘은", 0.5, 0.8), W("퍼스트", 0.85, 1.15), W("터치를", 1.2, 1.5), W("배워볼게요.", 1.55, 2.3),
                       W("공이", 2.6, 2.8), W("오면", 2.85, 3.1)]},
            {"start": 3.2, "end": 4.6, "text": "발 안쪽으로 받아요.", "words": [W("발", 3.2, 3.35), W("안쪽으로", 3.4, 3.9), W("받아요.", 3.95, 4.6)]}]

    def test_new_project_captions_are_chunked(self):
        name = self.video("코치 설명.mp4", "320x180", 5.0)
        self.transcript(name, self.SEGS)
        caps = editor.load_project(name)["captions"]
        self.assertEqual([c["text"] for c in caps], ["오늘은 퍼스트 터치를 배워볼게요.", "공이 오면 발 안쪽으로 받아요."])  # 한 줄씩
        self.assertTrue(all(c["id"] and len(c["wt"]) == 2 * len(c["text"].split()) and "words" not in c for c in caps))  # 단어 시각은 작게
        self.assertEqual(caps[0]["wt"], [50, 30, 5, 30, 5, 30, 5, 75])  # 첫 단어 시작 0.5초, 그 뒤로는 차이 (1/100초)
        tall = self.video("세로 촬영.mp4", "180x320", 5.0)  # 세로 영상은 쇼츠형 (13글자 · 한 줄)
        self.transcript(tall, self.SEGS)
        caps = editor.load_project(tall)["captions"]
        self.assertEqual([c["text"] for c in caps], ["오늘은 퍼스트 터치를", "배워볼게요.", "공이 오면 발 안쪽으로 받아요."])
        # 편집점 다시 찾기: 자막도 새로 나눔 (편집본은 그대로)
        self.transcript(tall, [dict(self.SEGS[0], words=self.SEGS[0]["words"][:4], text="오늘은 퍼스트 터치를 배워볼게요.")])
        proj, added, changed = editor.reanalyze_project(tall)
        self.assertTrue(changed)
        self.assertEqual([c["text"] for c in proj["captions"]], ["오늘은 퍼스트 터치를", "배워볼게요."])

    def test_old_transcript_without_words(self):
        name = self.video("예전 영상.mp4", "320x180", 5.0)
        segs = [{"start": 0.5, "end": 2.0, "text": "오늘은 퍼스트 터치를 배워볼게요"}, {"start": 2.4, "end": 4.6, "text": "공이 오면 발 안쪽으로 받아요"}]
        self.transcript(name, segs)
        r = editor.recommend(name, min_len=2.0, max_len=10.0)
        self.assertEqual(r["tidy"], [{"in": 0.35, "out": 4.85}])
        self.assertEqual((r["junk"], r["junk_list"]), (0, []))
        self.assertEqual(takes.find_fillers(segs), [])
        seqs = editor.auto_sequences(name, editor.media_info(name))
        self.assertEqual(seqs[0]["format"], "long")
        caps = editor.load_project(name)["captions"]
        self.assertEqual([{k: c[k] for k in ("start", "end", "text")} for c in caps], segs)  # 짧은 구간은 그대로 (한 줄 한도 안)
        self.assertTrue(all(set(c) <= {"id", "start", "end", "text", "sh", "shn"} for c in caps))  # 단어 시각(wt)은 지어내지 않음

    def ass_kf(self, proj):
        lines = [ln for ln in editor.build_ass(proj, 1920, 1080).splitlines() if ln.startswith("Dialogue: 1,") and ",Cap," in ln]

        def secs(t):
            h, m, s = t.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        out = []
        for ln in lines:
            f = ln.split(",", 9)
            out.append((secs(f[1]), secs(f[2]), [int(x) for x in re.findall(r"\\kf(\d+)", f[9])], f[9]))
        return out

    def seq(self, caps, item_in=0.0, item_start=0.0, speed=1.0):
        st = dict(editor.LONG_STYLE, effect="karaoke")
        v = {"id": "v1", "track": "V1", "media": "main", "start": item_start, "in": item_in, "out": item_in + 10.0, "speed": speed}
        return {"captionStyle": st, "titles": [], "shapes": [], "items": [v], "captions": caps, "captionsOn": True}

    def test_karaoke_uses_word_times(self):
        chunks = captions.chunk(make_words(30, seed=3), "long")
        caps = [dict(c, id=str(k)) for k, c in enumerate(chunks) if c["end"] < 9.5]
        for item_in, item_start in ((0.0, 0.0), (0.0, 2.5)):  # 타임라인에서 2.5초 뒤에 놓아도 단어 시각을 따라감
            got = self.ass_kf(self.seq(caps, item_in, item_start))
            self.assertEqual(len(got), len(caps))
            for (a, b, kf, body), cap in zip(got, caps):
                self.assertLessEqual(abs(sum(kf) - (b - a) * 100), 2, body)  # \kf 합 = 자막 길이 (±2cs)
                self.assertEqual(len(kf), len(cap["words"]))
                acc = 0
                for k, w in enumerate(cap["words"][1:], 1):  # 단어 k 는 그 단어를 말한 때부터 채움
                    acc += kf[k - 1]
                    self.assertLessEqual(abs(acc - (w["s"] - cap["start"]) * 100), 1.01, body)
                self.assertEqual(body.count(r"\N"), cap["text"].count("\n"))

    def test_karaoke_falls_back_when_text_edited(self):
        cap = captions.chunk([W("공을", 1.0, 1.3), W("받고", 1.4, 1.6), W("돌아서", 1.7, 2.5)], "long")[0]
        word_kf = self.ass_kf(self.seq([dict(cap, id="a")]))[0][2]
        self.assertEqual(word_kf, [40, 30, 80])  # 1.0·1.4·1.7초에 말함, 자막 1.0~2.5초
        edited = dict(cap, id="a", text="공을 받아서 돌고 슈팅")  # 낱말 수가 바뀜 → 글자 수 비례 (예전 방식)
        a, b, kf, _ = self.ass_kf(self.seq([edited]))[0]
        self.assertEqual(kf, [max(1, int(150 * n / 9)) for n in (2, 3, 2, 2)])
        self.assertEqual(editor._karaoke("공을 받고", 1.0), editor._karaoke("공을 받고", 1.0, None))
        self.assertIsNone(editor._cap_words({"start": 0, "end": 1, "text": "x", "words": [{"s": "?", "e": 1}]}))

    def test_recommend_cuts_lonely_fillers(self):
        name = self.video("추임새.mp4", "320x180", 6.0)
        segs = [{"start": 0.5, "end": 5.0, "text": "공을 어 받고 그 공을 음~ 돌아서 패스해요",
                 "words": [W("공을", 0.5, 0.9), W("어", 1.2, 1.4), W("받고", 1.7, 2.1), W("그", 2.15, 2.3), W("공을", 2.32, 2.7),
                           W("음~", 3.0, 3.5), W("돌아서", 3.8, 4.3), W("패스해요", 4.35, 5.0)]}]
        self.transcript(name, segs)
        self.assertEqual(takes.find_fillers(segs), [(1.1, 1.5, FILLER), (2.9, 3.6, FILLER)])  # 말에 붙은 '그'는 그대로
        r = editor.recommend(name, min_len=2.0, max_len=10.0)
        self.assertEqual(r["tidy"], [{"in": 0.35, "out": 1.1}, {"in": 1.5, "out": 2.9}, {"in": 3.6, "out": 5.25}])
        self.assertEqual([j["why"] for j in r["junk_list"]], [FILLER, FILLER])
        self.assertEqual(r["junk"], 2)
        for s in r["shorts"]:
            for c in s["cuts"]:
                self.assertFalse(c["in"] < 1.5 and c["out"] > 1.1, c)
        seq = editor.auto_sequences(name, editor.media_info(name), kinds=("long",))[0]
        self.assertEqual([(i["in"], i["out"]) for i in seq["items"] if i["track"] == "V1"], [(0.35, 1.1), (1.5, 2.9), (3.6, 5.25)])
        # 첫 단어 앞은 아무것도 없으니 뒤만 비면 됨 · 붙어 있으면 안 뺌
        self.assertEqual(takes.find_fillers([{"words": [W("어어", 0.0, 0.3), W("오늘은", 0.5, 0.9)]}]), [(0.0, 0.4, FILLER)])
        self.assertEqual(takes.find_fillers([{"words": [W("아", 0.0, 0.3), W("오늘은", 0.35, 0.9)]}]), [])


class DictRouteTest(WorkDir):
    """app.py /api/dict (실제 HTTP 서버로)."""

    def setUp(self):
        super().setUp()
        import app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log")]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.p2:
            p.stop()
        super().tearDown()

    def call(self, body=None):
        req = urllib.request.Request(self.base + "/api/dict", data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_get_save_get(self):
        code, j = self.call()
        self.assertEqual(code, 200)
        self.assertEqual({k: j[k] for k in ("terms", "fix")}, captions.default_dict())
        self.assertEqual(j["defaults"], captions.default_dict())
        code, j = self.call({"terms": ["파라렐라", "파라렐라", " 피벗 "], "fix": {"피버": "피벗"}})
        self.assertEqual((code, j["ok"], j["terms"]), (200, True, ["파라렐라", "피벗"]))
        self.assertEqual(json.loads(core.dict_path().read_text(encoding="utf-8"))["terms"], ["파라렐라", "피벗"])
        self.assertEqual(self.call()[1]["terms"], ["파라렐라", "피벗"])
        code, j = self.call({"terms": "피벗"})
        self.assertEqual((code, j["ok"]), (400, False))
        self.assertTrue(j["error"])


# ---------- 검토에서 나온 것 (#5): 올리기 키트 문장 · 쇼츠 편집본 자막 · 작은 단어 시각 · 사전 조사 · 힌트 길이 ----------

SENTS = ["오늘은 퍼스트 터치를 정확하게 하는 방법을 알려드릴게요.", "공이 오는 방향을 먼저 보고 발 안쪽으로 부드럽게 받아요.",
         "그다음에 몸을 열어서 앞으로 바로 패스를 이어 가면 돼요.", "이 연습을 매일 십 분씩 하면 확실히 달라질 거예요."]


def sentence_segs(t=0.5):
    """문장마다 받아쓰기 구간 하나 (단어 0.35초 + 0.05초 쉼, 문장 사이 0.6초)."""
    out = []
    for text in SENTS:
        ws = []
        for w in text.split():
            ws.append(W(w, round(t, 2), round(t + 0.35, 2)))
            t += 0.4
        out.append({"start": ws[0]["s"], "end": ws[-1]["e"], "text": text, "words": ws})
        t += 0.6
    return out


class ReviewFixTest(WorkDir):
    def landscape(self, name="가로 강의.mp4"):
        name = self.video(name, "320x180", 15.0)
        self.transcript(name, sentence_segs())
        return name, editor.load_project(name)

    def test_upload_kit_quotes_whole_sentence(self):
        import upload
        name, proj = self.landscape()
        self.assertGreater(len(proj["captions"]), len(SENTS))  # 자막은 짧게 나뉨
        long_seq = next(q for q in proj["sequences"] if q["format"] == "long")
        kit = upload.build_kit(name, long_seq["id"], save=False)
        quote = re.search(r"“(.+?)”", kit["description"])
        self.assertIsNotNone(quote, kit["description"])
        self.assertIn(quote.group(1), [x.rstrip(".") for x in SENTS])  # 반쪽 문장이 아니라 문장 그대로
        segs = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in proj["captions"]]
        self.assertEqual([s["text"] for s in upload.sentences(upload._segments(segs))], SENTS)
        # 예전처럼 한 문장씩인 자막·문장 끝이 아닌데 오래 쉰 곳은 그대로
        whole = [{"start": 0.0, "end": 2.0, "text": "공을 받고"}, {"start": 4.0, "end": 5.0, "text": "돌아요"}, {"start": 5.1, "end": 6.0, "text": "끝."}]
        self.assertEqual([s["text"] for s in upload.sentences(whole)], ["공을 받고", "돌아요", "끝."])

    def test_shorts_sequence_from_landscape_gets_shorts_captions(self):
        name, proj = self.landscape()
        caps = proj["captions"]
        self.assertTrue(any(c.get("sh") for c in caps))
        self.assertFalse(any("\n" in c["text"] for c in caps))  # 프로젝트 자막은 롱폼형 한 줄 (롱폼 편집본·미리보기)
        v = {"id": "v", "track": "V1", "media": "main", "start": 0.0, "in": 0.0, "out": 15.0, "speed": 1.0}
        base = {"items": [v], "captions": caps, "titles": [], "shapes": [], "captionsOn": True}
        longs = editor.timeline_captions(dict(base, format="long"))
        self.assertEqual([c["text"] for c in longs], [c["text"] for c in caps])
        # 쇼츠 편집본은 쇼츠형으로 나눔 (sh) — 편집실 미리보기(capParts)도 같은 방식 (test_caption_oneline 의 node 비교)
        self.assertTrue(editor.SHORTS_SPLIT)
        shorts = editor.timeline_captions(dict(base, format="shorts"))
        self.assertGreater(len(shorts), len(caps))
        self.assertEqual(" ".join(c["text"] for c in shorts).split(), " ".join(c["text"] for c in caps).split())  # 낱말 그대로, 순서대로
        for c in shorts:
            self.assertNotIn("\n", c["text"])
            self.assertLessEqual(len(c["text"].replace(" ", "")), captions.LIMITS["shorts"][0], c)
            self.assertLessEqual(c["end"] - c["start"], captions.LIMITS["shorts"][1] + 0.65, c)  # 다음 자막 시작까지 이어 보임
        for a, b in zip(shorts, shorts[1:]):
            self.assertLessEqual(a["end"], b["start"] + 1e-6)
        # 쇼츠 편집본 내보내기(노래방): 나눈 자막마다 \kf 합 = 그 자막 길이
        st = dict(editor.SHORTS_STYLE, effect="karaoke")
        got = EditorCaptionTest.ass_kf(self, dict(base, format="shorts", captionStyle=st))
        self.assertEqual(len(got), len(shorts))
        for a, b, kf, body in got:
            self.assertLessEqual(abs(sum(kf) - (b - a) * 100), 2, body)
        # 자동 쇼츠 편집본(영상이 짧으면 안 생김)도 쇼츠형으로
        for sq in (q for q in proj["sequences"] if q["format"] == "shorts"):
            for c in editor.timeline_captions(dict(sq, captions=caps)):
                self.assertLessEqual(len(c["text"].replace(" ", "")), captions.LIMITS["shorts"][0], c)
        # 글을 고쳤으면(낱말 수가 바뀜) 나누지 않고 그대로
        cap = next(c for c in caps if c.get("sh"))
        edited = dict(cap, text=cap["text"].replace("\n", " ") + " 추가")
        self.assertEqual([c["text"] for c in editor.timeline_captions(dict(base, format="shorts", captions=[edited]))], [edited["text"]])
        self.assertEqual(len(editor._shorts_parts(dict(cap, sh=[0]))), 1)  # 이상한 나눌 곳은 무시

    def test_compact_word_times(self):
        segs = []
        for k in range(40):
            segs += sentence_segs(0.5 + 15 * k)
        verbose = captions.from_segments(segs, "long", captions.DEFAULT_TERMS)
        with mock.patch.object(core, "dict_path", return_value=self.work / "dict.json"):
            compact = editor._captions_of(segs, {"width": 1920, "height": 1080})
        self.assertEqual([c["text"] for c in compact], [c["text"] for c in verbose])
        size = lambda x: len(json.dumps(x, ensure_ascii=False).encode("utf-8"))
        plain = [{k: c[k] for k in ("id", "start", "end", "text")} for c in compact]
        verbose_ids = [dict(v, id=c["id"]) for c, v in zip(compact, verbose)]
        self.assertLess(size(compact) - size(plain), 0.35 * (size(verbose_ids) - size(plain)))  # 단어 시각 몫이 1/3 아래로
        for c, v in zip(compact, verbose):  # 읽어 낸 단어 시각은 예전 모양과 같음
            self.assertEqual([(w["w"], round(w["s"], 2), round(w["e"], 2)) for w in editor._cap_words(c)],
                             [(w["w"], w["s"], w["e"]) for w in v["words"]])
        self.assertIsNone(editor._cap_words({"start": 0, "end": 1, "text": "공을 받고", "wt": [0, 50]}))
        self.assertIsNone(editor._cap_words({"start": 0, "end": 1, "text": "공을", "wt": ["x", 50]}))
        self.assertEqual(editor._cap_words({"start": 0, "end": 1, "text": "공을 받고", "wt": [10, 30, 5, 40]}),
                         [{"w": "공을", "s": 0.1, "e": 0.4}, {"w": "받고", "s": 0.45, "e": 0.85}])

    def test_dict_does_not_eat_verb_endings_or_short_keys(self):
        d = captions.DEFAULT_FIX
        for src, want in {"피버가요": "피버가요", "피버가야": "피버가야", "피버요": "피벗이요", "피버야": "피벗이야",
                          "피버죠": "피벗이죠", "피버를": "피벗을", "피버예요": "피벗이에요"}.items():
            self.assertEqual(captions.apply_dict(src, d), want, src)
        self.assertEqual(captions.apply_dict("아이 아 아요", {"아": "어"}), "아이 어 아요")  # 한 글자 말은 낱말 그대로일 때만

    def test_prompt_fits_default_terms(self):
        tj = sorted(Path.home().glob(".cache/huggingface/hub/models--Systran--faster-whisper-tiny/snapshots/*/tokenizer.json"))
        if not tj:
            self.skipTest("tiny 모델 토크나이저가 없어요")
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(str(tj[0]))
        m = types.SimpleNamespace(hf_tokenizer=tok, transcribe=lambda audio, hotwords=None, **k: None)
        opts = core._whisper_opts(m, captions.default_dict())
        for t in captions.DEFAULT_TERMS:  # 기본 용어는 모두 첫머리 힌트에
            self.assertIn(t, opts["initial_prompt"])
        n = len(tok.encode(" " + opts["initial_prompt"], add_special_tokens=False).ids)
        h = len(tok.encode(" " + opts["hotwords"], add_special_tokens=False).ids)
        self.assertLessEqual(n, 223)  # faster-whisper 첫머리 힌트 한도 (max_length // 2 - 1)
        self.assertLessEqual(n + h + 4, 448 - 180)  # 받아쓸 자리가 넉넉히 남음

    def test_log_says_how_many_terms_were_sent(self):
        name = self.video("로그.mp4", "160x90", 2.0)
        logs = []
        with self.whisper():
            core.analyze(name, logs.append)
        sent = FakeModel.calls[0]["initial_prompt"].count(", ") + 1
        n = len(captions.DEFAULT_TERMS)
        self.assertLess(sent, n)  # 흉내 모델은 토크나이저가 없어 넉넉히 셈 → 다 못 들어감
        self.assertIn(f"용어 사전의 말 {n}개 중 앞의 {sent}개", "\n".join(logs))


if __name__ == "__main__":
    unittest.main()
