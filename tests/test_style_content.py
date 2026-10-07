"""영상 기획 분석(plan.py) · 클로드 계정 경로(claude_cli.py) · 화면 글자/소리 모델(avmodels.py) 테스트.
저장소 폴더에서: python3 -m unittest tests.test_style_content

- 판단 규칙은 순수 함수로 (제목·대사 → 장르, 합성 신호 → 인트로 유형 5가지, 자막 6종 분류, 재미 요소 정도, 여러 영상 합치기).
- 정답 영상(tests/make_fixture.make_plan_fixture: 티저·타이틀·예능 자막·정지 화면·슬로 리플레이)으로 끝까지.
  글자 읽기·소리 모델(~/.futsal-studio/models)이 없으면 그 단언은 건너뛰고 '어림' 표시만 확인 (인터넷은 쓰지 않음).
- 클로드 CLI 는 가짜 claude(파이썬 스크립트, FUTSAL_CLAUDE)로: 성공·로그인 안 됨·한도·시간 초과·깨진 JSON·예전 판 옵션.
- 이전 판 스타일 JSON(plan 없음)도 그대로 동작하는지, 가편집 새 값이 꺼져 있으면 예전과 같은지."""
import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
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
import avmodels  # noqa: E402
import claude_cli  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import face  # noqa: E402
import plan  # noqa: E402
import style  # noqa: E402
import thumb  # noqa: E402
import make_fixture as fx  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
OCR_OK = avmodels.ready("ocr")
AUDIO_OK = avmodels.ready("audio")
NEED_OCR = unittest.skipUnless(OCR_OK, "글자 읽기 모델이 없어요 (~/.futsal-studio/models)")
POSIX = unittest.skipIf(sys.platform == "win32", "가짜 claude 는 실행 권한이 있는 스크립트 (Linux·Mac)")


def quiet(*a, **k):
    pass


def work_dirs(root):
    work = Path(root) / "풋살 작업 폴더"
    dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


M = {}  # 정답 영상 (모듈 전체가 같이 씀 · 만들기가 느려서)


def setUpModule():
    tmp = Path(tempfile.mkdtemp(prefix="기획 분석 테스트 "))
    dirs = work_dirs(tmp)
    M["tmp"], M["dirs"] = tmp, dirs
    M["video"] = tmp / "src" / fx.PLAN_NAME
    M["truth"] = fx.make_plan_fixture(M["video"])


def tearDownModule():
    shutil.rmtree(M.get("tmp", ""), ignore_errors=True)


class WorkBase(unittest.TestCase):
    """테스트마다 새 작업 폴더 (한글·띄어쓰기 이름) + 스타일 폴더 + 정답 영상 복사."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="기획 작업 "))
        dirs = work_dirs(self.tmp)
        self.work = dirs["WORK"]
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(style, "STYLES", self.work / "styles")]
        for p in self.patches:
            p.start()
        editor.CANCEL.clear()

    def tearDown(self):
        editor.CANCEL.clear()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_video(self, transcript=True):
        name = fx.PLAN_NAME
        shutil.copy2(M["video"], core.VIDEOS / name)
        if transcript:
            core.adir(name).mkdir(parents=True, exist_ok=True)
            (core.adir(name) / "transcript.json").write_text(json.dumps(fx.PLAN_TRANSCRIPT, ensure_ascii=False), encoding="utf-8")
        return name


# ---------- 순수 판단 규칙 ----------

class TestCaptionRules(unittest.TestCase):
    def test_kind_table(self):
        """자막 6종: 이름표·숫자는 정보, 대사와 같으면 말, 의성어는 효과, 괄호는 속마음, 명사형 끝은 상황, 크거나 색 있으면 강조."""
        C = plan.classify_caption
        self.assertEqual(C("최경진 감독", 0.04, "white", "top", (0.03, 0.03, 0.15, 0.1), 5), "info")
        self.assertEqual(C("기록 12초", 0.05, "white", "top"), "info")
        self.assertEqual(C("3:2", 0.06, "white", "top"), "info")
        self.assertEqual(C("오늘은 슈팅 연습을 해볼게요", 0.05, "white", "bottom", speech_sim=0.95), "speech")
        self.assertEqual(C("쾅!", 0.15, "orange", "middle", speech_sim=0.0), "sfx")
        self.assertEqual(C("ㅋㅋㅋㅋ", 0.08, "white", "middle", speech_sim=0.0), "sfx")
        self.assertEqual(C("(당황)", 0.08, "white", "top", speech_sim=0.0), "inner")
        self.assertEqual(C("이거 실화냐…", 0.05, "white", "middle", speech_sim=0.1), "inner")
        self.assertEqual(C("감독님 등장", 0.05, "white", "middle", speech_sim=0.1), "situ")
        self.assertEqual(C("결국 성공", 0.05, "white", "middle", speech_sim=0.1), "situ")
        self.assertEqual(C("이게 들어간다고?", 0.13, "yellow", "middle", speech_sim=0.1), "emph")
        self.assertEqual(C("레전드", 0.1, "white", "middle", speech_sim=0.0), "emph")

    def test_no_transcript_bottom_white_is_speech(self):
        self.assertEqual(plan.classify_caption("공을 끝까지 보세요", 0.05, "white", "bottom", dur=3, speech_sim=None, has_transcript=False), "speech")

    def test_speech_wins_over_misread_number(self):
        """OCR 이 '이번에는'을 '1번에는'으로 읽어도 대사와 같으면 말 자막 (숫자 정보로 보지 않음)."""
        self.assertEqual(plan.classify_caption("1번에는 왼발로 차 볼게요", 0.08, "white", "bottom", speech_sim=0.91), "speech")

    def test_color_names(self):
        self.assertEqual(plan.color_name((255, 225, 77)), "yellow")
        self.assertEqual(plan.color_name((250, 250, 250)), "white")
        self.assertEqual(plan.color_name((230, 40, 40)), "red")
        self.assertEqual(plan.color_name((255, 150, 30)), "orange")
        self.assertEqual(plan.color_name((60, 130, 255)), "blue")
        self.assertEqual(plan.color_name((10, 10, 10)), "black")
        self.assertEqual(plan.color_name((240, 210, 150)), "yellow", "화질에 묽어진 노랑")

    def test_text_color_outline_and_box(self):
        import numpy as np
        rng = np.random.default_rng(3)
        img = rng.integers(0, 255, (60, 200, 3)).astype(np.uint8)  # 어지러운 배경
        img[20:40, 20:180] = (20, 20, 20)                             # 검은 테두리
        for x in range(30, 170, 12):
            img[24:36, x:x + 6] = (255, 225, 77)                      # 노란 획
        self.assertEqual(plan.text_color(img, (15, 15, 185, 45))[0], "yellow")
        box = np.full((40, 200, 3), 250, np.uint8)                    # 흰 상자 위 빨간 글씨
        for x in range(20, 180, 14):
            box[12:28, x:x + 6] = (220, 30, 30)
        nm, hx, boxed = plan.text_color(box, (0, 0, 200, 40))
        self.assertEqual(nm, "red")
        self.assertTrue(boxed)

    def test_ocr_junk_filter(self):
        self.assertTrue(plan.ocr_line_ok({"text": "이게 들어간다고?", "h": 0.12, "conf": 0.9}))
        self.assertFalse(plan.ocr_line_ok({"text": "bel", "h": 0.53, "conf": 0.9}), "화면 절반 크기 글자는 무늬")
        self.assertFalse(plan.ocr_line_ok({"text": "!be", "h": 0.2, "conf": 0.7}))
        self.assertTrue(plan.ocr_line_ok({"text": "GOAL", "h": 0.1, "conf": 0.95}))


def fake_pe(n=120, ocr=None, audio=None, title="", models=None, **kw):
    """신호 기록 흉내 (지문은 초마다 다른 값 → 티저·리플레이 없음)."""
    import numpy as np
    rng = np.random.default_rng(7)
    hs = rng.integers(0, 2 ** 63, n, dtype=np.int64).astype(np.uint64)
    pe = {"v": plan.PLAN_VER, "source": "x.mp4", "duration": float(n), "title": title, "models": models or {"ocr": True, "audio": False},
          "hash": {"fps": 1, "data": plan.pack(hs, "uint64")}, "flat": plan.pack(np.full(n, 60)), "green": plan.pack(np.zeros(n)),
          "sat": plan.pack(np.full(n, 60)), "hist": plan.pack(np.full(n * 27, 9)), "ocr": ocr if ocr is not None else [],
          "audio": audio, "speakers": None, "faces": None, "nface": None}
    pe.update(kw)
    return pe


def seg(a, b, text):
    return {"start": a, "end": b, "text": text}


class TestIntroRules(unittest.TestCase):
    def judge(self, pe, segs, ev=None):
        d = plan.detect(pe, {"cuts": [], "motion": [2.0] * (2 * int(pe["duration"]))}, segs)
        if ev:
            d["ev"].update(ev)
        return plan.judge_intro(pe, d, segs, {})

    def test_teaser(self):
        j = self.judge(fake_pe(), [seg(6, 30, "오늘은 슈팅을 해볼게요")], {"teaser": [[0, 4, 80]]})
        self.assertEqual(j["type"], "teaser")
        self.assertIn("티저", j["label"])
        self.assertEqual(j["greeting"], "none")
        self.assertIn("인사 없음", j["label"])
        self.assertNotRegex(j["label"], r"\d{2}:\d{2}", "판단 문장에 시각을 쓰지 않음")
        self.assertTrue(any("먼저 나와요" in e["why"] for e in j["ev"]))

    def test_hook_line(self):
        j = self.judge(fake_pe(), [seg(0.5, 3, "이 슈팅 진짜 들어갈까요?"), seg(3.5, 30, "자 그럼 시작합니다 오늘은 인사이드 킥")])
        self.assertEqual(j["type"], "hook_line")
        self.assertEqual(j["hook"], "질문형")

    def test_title_first(self):
        pe = fake_pe(ocr=[{"t": 0, "d": 3, "lines": [{"text": "인사이드 킥", "box": [0.3, 0.4, 0.7, 0.55], "h": 0.12, "conf": 0.95, "cname": "white"}]}])
        j = self.judge(pe, [seg(4, 40, "오늘은 인사이드 킥을 차 볼 건데요")])
        self.assertEqual(j["type"], "title_first")
        self.assertTrue(j["titleCard"])

    def test_greeting(self):
        j = self.judge(fake_pe(), [seg(0.5, 4, "안녕하세요 풋살사관학교의 최경진입니다"), seg(4.2, 30, "오늘은 패스를 배워 볼게요 공을 차는")])
        self.assertEqual(j["type"], "greeting")
        self.assertEqual(j["greeting"], "early")
        self.assertIn("인사", j["label"])

    def test_cold_open(self):
        j = self.judge(fake_pe(), [seg(0.3, 40, "자 공을 이렇게 놓고 발 안쪽으로 차면 됩니다")])
        self.assertEqual(j["type"], "cold_open")
        self.assertIn("바로 본론", j["label"])

    def test_late_greeting(self):
        j = self.judge(fake_pe(), [seg(0.5, 12, "자 공을 놓고"), seg(15, 18, "안녕하세요 여러분"), seg(18.5, 40, "오늘은 패스 연습을 해요 공을")],
                       {"teaser": [[0, 3, 70]]})
        self.assertEqual(j["greeting"], "late")
        self.assertIn("인사는 뒤에 짧게", j["label"])


class TestGenreRules(unittest.TestCase):
    def genre(self, title, segs=()):
        pe = fake_pe(title=title)
        d = plan.detect(pe, {}, list(segs))
        fun = plan.judge_fun(pe, d, list(segs), {})
        caps_j = plan.judge_captions(pe, d, list(segs), {})
        return plan.judge_genre(pe, d, list(segs), title, fun, caps_j, None)

    def test_challenge(self):
        g = self.genre("슈팅 1대1 대결! 지면 벌칙")
        self.assertEqual(g["key"], "challenge")
        self.assertTrue(g["label"].startswith("축구 "), g["label"])
        self.assertTrue(any("제목" in e["why"] for e in g["ev"]))

    def test_lesson(self):
        self.assertEqual(self.genre("인사이드 패스 차는 법 (기본기 꿀팁)")["key"], "lesson")

    def test_review(self):
        self.assertEqual(self.genre("신상 축구화 언박싱 후기")["key"], "review")

    def test_unknown_when_no_clue(self):
        g = self.genre("ㅁㄴㅇㄹ")
        self.assertEqual(g["key"], "unknown")
        self.assertLess(g["conf"], 0.5)

    def test_combo_label(self):
        """두 장르 점수가 비슷하고 어울리면 합친 라벨 (예능 + 챌린지)."""
        g = self.genre("레전드 반응 ㅋㅋ 챌린지 도전", [seg(0, 10, "ㅋㅋ 레전드 반응 웃긴 도전 실패 벌칙")])
        self.assertIn("챌린지", g["label"])


class TestFunAndMerge(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(plan._level(1.2), "자주")
        self.assertEqual(plan._level(0.5), "가끔")
        self.assertEqual(plan._level(0.1), "한두 번")
        self.assertEqual(plan._level(0), "")

    def test_fun_without_audio_says_unknown(self):
        pe = fake_pe(n=60)
        d = plan.detect(pe, {}, [])
        d["ev"]["replay"] = [[30, 38, 10, 14, 2.0]]
        f = plan.judge_fun(pe, d, [], {})
        self.assertEqual(f["items"][0]["key"], "slowmo_replay")
        self.assertEqual(f["items"][0]["level"], "자주")
        self.assertIn("소리 모델이 없어", f["label"])
        self.assertNotIn("laugh", [x["key"] for x in f["items"]])

    def _plan(self, typ, dur, genre="challenge"):
        return {"duration": dur, "models": {"ocr": True, "audio": True},
                "intro": {"type": typ, "label": f"{typ} 라벨", "conf": 0.8, "lenSec": 8, "ev": [], "greeting": "none", "teaserSec": 4},
                "genre": {"key": genre, "label": plan.GENRE_LABEL[genre], "conf": 0.7, "ev": []},
                "format": {"labels": ["2인 대화", "야외 현장"], "conf": 0.5, "ev": [], "talkRatio": 0.5},
                "captions": {"style": "variety", "label": "예능 자막 위주", "conf": 0.7, "colors": ["#FFE14D"], "perMin": {"emph": 2}, "keywordPerMin": 2, "ev": []},
                "fun": {"items": [{"key": "sfx", "label": "효과음", "perMin": 1.5, "level": "자주", "ev": []}], "conf": 0.6, "ev": []}}

    def test_merge_disagree(self):
        """레퍼런스 2개의 인트로가 다르면 '영상마다 달라요: A 또는 B' · 'n개 중 m개'."""
        m = plan.merge_plans([self._plan("teaser", 600), self._plan("greeting", 600)])
        self.assertTrue(m["intro"]["label"].startswith("영상마다 달라요"), m["intro"]["label"])
        self.assertEqual(m["agree"]["intro"], "2개 중 1개")
        self.assertEqual(m["refs"], 2)

    def test_merge_agree(self):
        m = plan.merge_plans([self._plan("teaser", 600), self._plan("teaser", 300), self._plan("greeting", 100)])
        self.assertEqual(m["intro"]["type"], "teaser")
        self.assertEqual(m["agree"]["intro"], "3개 중 2개")
        self.assertEqual(m["format"]["labels"], ["2인 대화", "야외 현장"])
        self.assertEqual(m["fun"]["items"][0]["key"], "sfx")
        self.assertTrue(m["summary"])
        self.assertTrue(plan.plan_params(m)["introTeaser"]["on"])

    def test_plan_params_default_off(self):
        self.assertEqual(plan.plan_params(None), {"introTeaser": {"on": False, "sec": 0}, "emphasisTitles": {"perMin": 0, "color": "#FFE14D"}})
        p = self._plan("teaser", 600)
        pp = plan.plan_params(p)
        self.assertEqual(pp["introTeaser"], {"on": True, "sec": 4})
        self.assertEqual(pp["emphasisTitles"]["color"], "#FFE14D")
        self.assertGreater(pp["emphasisTitles"]["perMin"], 0)
        p["captions"]["style"] = "speech"
        self.assertEqual(plan.plan_params(p)["emphasisTitles"]["perMin"], 0)


class TestSpeakers(unittest.TestCase):
    def test_cluster_two_groups(self):
        import numpy as np
        rng = np.random.default_rng(0)
        X = np.concatenate([rng.normal(0, 0.2, (6, 13)), rng.normal(3, 0.2, (5, 13))])
        lab = plan.cluster(X)
        self.assertEqual(len(set(lab)), 2)
        self.assertEqual(len(set(lab[:6])), 1)

    def test_cluster_one_group_and_small_noise_merged(self):
        import numpy as np
        rng = np.random.default_rng(1)
        X = np.concatenate([rng.normal(0, 0.2, (12, 13)), rng.normal(5, 0.1, (1, 13))])
        lab = plan.cluster(X, weights=[5.0] * 12 + [0.5])
        self.assertEqual(set(lab), {0}, "전체 말의 8%가 안 되는 묶음(기침 등)은 큰 묶음으로")

    def test_two_synthetic_voices(self):
        """낮은 목소리·높은 목소리 합성 소리 → 2명, 같은 목소리만 → 1명 (어림이지만 뚜렷한 차이는 가려냄)."""
        import numpy as np
        sr, rng = 16000, np.random.default_rng(2)

        def voice(f0, tilt, form, dur):
            t = np.arange(int(dur * sr)) / sr
            ph = 2 * np.pi * np.cumsum(f0 * (1 + 0.05 * np.sin(2 * np.pi * 0.7 * t))) / sr
            k = (t / 0.25).astype(int)
            F1, F2 = rng.uniform(300, 800, k.max() + 1) * form, rng.uniform(900, 2200, k.max() + 1) * form
            out = sum((h ** -tilt) * (np.exp(-((h * f0 - F1[k]) / 200) ** 2) + 0.7 * np.exp(-((h * f0 - F2[k]) / 300) ** 2) + 0.05)
                      * np.sin(h * ph) for h in range(1, 25))
            return (out / np.abs(out).max() * 0.3 * 32767).astype(np.int16)
        wave, segs, t = [], [], 0.0
        for i in range(8):
            x = voice(*((120, 1.0, 1.0) if i % 2 == 0 else (210, 0.6, 1.18)), 3.0)
            wave.append(x)
            segs.append({"start": t, "end": t + 3.0, "text": "말"})
            t += 3.0
        r = plan.speakers(np.concatenate(wave), segs)
        self.assertEqual(r["n"], 2, r)
        self.assertGreater(r["turnsPerMin"], 5)
        one = plan.speakers(np.concatenate(wave[0::2]), [{"start": i * 3.0, "end": i * 3.0 + 3, "text": "말"} for i in range(4)])
        self.assertEqual(one["n"], 1, one)


class TestPromptAndParse(unittest.TestCase):
    def test_parse_fenced_json(self):
        ai = plan.parse_ai('좋아요!\n```json\n{"intro": "티저형 훅", "genre": "축구 예능", "format": "2인 대결", "captions": "예능 자막", '
                           '"fun": "슬로 리플레이", "summary": "요약", "apply": ["하나", "둘"]}\n```', by="claude-cli", model="claude-opus-5-5")
        self.assertEqual(ai["genre"], "축구 예능")
        self.assertEqual(ai["apply"], ["하나", "둘"])
        self.assertEqual(ai["by"], "claude-cli")

    def test_parse_rejects_garbage(self):
        for bad in ("그냥 글", "{깨진 json", '{"intro": "하나만"}'):
            with self.assertRaises(ValueError):
                plan.parse_ai(bad)

    def test_parse_strips_timecodes_and_keeps_fix(self):
        """판단 글에 시각이 섞여 오면 지움 (화면 본문에 시각을 쓰지 않음) · 고친 점은 fix 로 따로."""
        ai = plan.parse_ai(json.dumps({"intro": "00:50쯤의 결정적 슈팅 장면을 맨 앞에 보여 줘요", "genre": "g", "format": "f",
                                       "apply": ["1:05 장면처럼 느리게"], "fix": "실내가 아니에요"}, ensure_ascii=False))
        self.assertEqual(ai["intro"], "결정적 슈팅 장면을 맨 앞에 보여 줘요")
        self.assertEqual(ai["apply"], ["장면처럼 느리게"])
        self.assertEqual(ai["fix"], "실내가 아니에요")

    def test_parse_truncates(self):
        ai = plan.parse_ai(json.dumps({"intro": "가" * 2000, "genre": "g", "format": "f", "apply": ["x" * 999] * 9}))
        self.assertLessEqual(len(ai["intro"]), 300)
        self.assertLessEqual(len(ai["apply"]), 5)
        self.assertLessEqual(len(ai["apply"][0]), 200)


# ---------- 정답 영상으로 끝까지 ----------

class TestFixtureEndToEnd(WorkBase):
    def run_plan(self, transcript=True):
        name = self.add_video(transcript)
        sev = style.extract_events(name, quiet)
        pe = plan.extract_plan(name, sev, quiet)
        return name, sev, pe

    def test_visual_events(self):
        """티저(0~4초 = 50~54초 장면) · 정지 화면(40~42초) · 2배 느린 리플레이(56~64초) — 모델 없이도 화면 지문으로."""
        name, sev, pe = self.run_plan()
        d = plan.detect(pe, sev, plan._transcript(name))
        ev = d["ev"]
        self.assertTrue(ev["teaser"], ev)
        a, b, src = ev["teaser"][0]
        self.assertLessEqual(a, 1)
        self.assertGreaterEqual(b, 3)
        self.assertAlmostEqual(src, 50, delta=2)
        self.assertTrue(any(39.5 <= x[0] <= 41 and x[1] >= 41.5 for x in ev["freeze"]), ev["freeze"])
        slow = [r for r in ev["replay"] if r[4] >= 1.5]
        self.assertTrue(slow, ev["replay"])
        self.assertAlmostEqual(slow[0][0], 56, delta=1.5)
        self.assertAlmostEqual(slow[0][2], 50, delta=1.5)
        self.assertFalse([r for r in ev["replay"] if r[2] < 5], "인트로 티저를 원본으로 본 리플레이는 없음")
        p = plan.judge(pe, sev)
        self.assertEqual(p["intro"]["type"], "teaser")
        self.assertEqual(p["intro"]["greeting"], "none")
        self.assertIn("slowmo_replay", [x["key"] for x in p["fun"]["items"]])
        self.assertIn("챌린지", p["genre"]["label"])
        self.assertTrue(plan.plan_params(p)["introTeaser"]["on"])

    @NEED_OCR
    def test_captions_with_ocr(self):
        name, sev, pe = self.run_plan()
        self.assertTrue(pe["models"]["ocr"])
        self.assertLessEqual(pe["ocrN"], plan.OCR_CAP)
        caps = plan.detect(pe, sev, plan._transcript(name))["caps"]
        kinds = {c["kind"] for c in caps}
        self.assertTrue({"speech", "emph", "info", "inner"} <= kinds, caps)
        emph = [c for c in caps if c["kind"] == "emph" and plan._sim(c["text"], "이게 들어간다고?") >= 0.75]
        self.assertTrue(emph and emph[0]["cname"] == "yellow" and emph[0]["h"] >= plan.BIG_H, caps)
        self.assertTrue(any(c["kind"] == "info" and "감독" in c["text"] for c in caps))
        # 굵은 '쾅!' (OCR 은 '광!'으로도 읽음) — 큰 효과 글자도 놓치지 않음
        self.assertTrue(any(32.5 <= c["a"] <= 35 and c["kind"] in ("sfx", "emph") and "!" in c["text"] for c in caps), caps)
        p = plan.judge(pe, sev)
        self.assertIn(p["captions"]["style"], ("variety", "mixed"))
        self.assertIn("#FFE14D", p["captions"]["colors"])
        self.assertTrue(p["intro"]["titleCard"])
        self.assertIn("타이틀", p["intro"]["label"])
        self.assertGreater(plan.plan_params(p)["emphasisTitles"]["perMin"], 0)

    def test_without_models_says_approx(self):
        """글자 읽기·소리 모델을 못 받으면 조용히 어림 규칙으로 (인터넷 시도 없음) · 판단 문장에 '모름/어림' 표시."""
        with mock.patch.object(avmodels, "ensure", return_value=False), mock.patch.object(face, "ready", return_value=False):
            name, sev, pe = self.run_plan()
        self.assertEqual(pe["models"], {"ocr": False, "audio": False, "faces": False})
        self.assertIsNone(pe["ocr"])
        p = plan.judge(pe, sev)
        self.assertIn("글자 읽기 모델이 없어", p["captions"]["label"])
        self.assertIn("소리 모델이 없어", p["fun"]["label"])
        self.assertEqual(p["intro"]["type"], "teaser", "화면 지문은 모델 없이도")

    def test_cache_and_model_upgrade(self):
        """같은 파일이면 다시 안 봄 · 예전에 모델 없이 만든 기록은 지금 모델이 있으면 다시 봄."""
        with mock.patch.object(avmodels, "ensure", return_value=False), mock.patch.object(face, "ready", return_value=False):
            name, sev, pe = self.run_plan()
        with mock.patch.object(plan, "_model_state", return_value={"ocr": False, "audio": False, "faces": False}), \
                mock.patch.object(plan, "_video_pass", side_effect=AssertionError("다시 보면 안 됨")):
            self.assertEqual(plan.extract_plan(name, sev, quiet)["sig"], pe["sig"])
        with mock.patch.object(plan, "_model_state", return_value={"ocr": True, "audio": False, "faces": False}):
            self.assertIsNone(plan.load_events(name), "그때 없던 글자 읽기 모델이 지금은 있음 → 다시")
        os.utime(core.VIDEOS / name, ns=(time.time_ns(), time.time_ns() + 10 ** 9))
        self.assertIsNone(plan.load_events(name), "파일이 바뀌면 다시")

    def test_cancel(self):
        name = self.add_video()
        sev = style.extract_events(name, quiet)
        editor.CANCEL.set()
        with self.assertRaises(plan.PlanCancelled):
            plan.extract_plan(name, sev, quiet)
        self.assertFalse(plan._plan_file(name).exists())

    def test_learn_saves_plan_and_old_keys(self):
        name = self.add_video()
        r = style.learn("기획 스타일", [name], quiet)
        d = json.loads((style.STYLES / "기획 스타일.json").read_text(encoding="utf-8"))
        self.assertIn("plan", d)
        self.assertIn("plan", d["refs"][0])
        self.assertIn("cutsPerMin", d, "예전 구조 수치도 그대로")
        st = style.list_styles()[0]
        self.assertTrue(st["plan_desc"].startswith("이 채널은"))
        self.assertEqual(r["params"]["introTeaser"]["on"], True)
        for k in ("intro", "genre", "format", "captions", "fun"):
            lab = d["plan"][k].get("label") or " ".join(d["plan"][k].get("labels") or [])
            self.assertNotRegex(lab, r"\b\d{1,2}:\d{2}\b", f"{k}: 판단 문장에 시각을 쓰지 않음")

    def test_learn_cancel_stops_everything(self):
        name = self.add_video()
        style.extract_events(name, quiet)
        with mock.patch.object(plan, "extract_plan", side_effect=plan.PlanCancelled()):
            with self.assertRaises(style.StyleCancelled):
                style.learn("멈춘 스타일", [name], quiet)
        self.assertFalse((style.STYLES / "멈춘 스타일.json").exists())

    def test_learn_plan_failure_keeps_structure(self):
        name = self.add_video()
        with mock.patch.object(plan, "extract_plan", side_effect=RuntimeError("깨짐")):
            style.learn("구조만", [name], quiet)
        d = json.loads((style.STYLES / "구조만.json").read_text(encoding="utf-8"))
        self.assertNotIn("plan", d)
        self.assertIn("cutsPerMin", d)


class TestBackwardCompat(WorkBase):
    def test_old_style_json(self):
        """plan 이 없는 예전 스타일(v1.7): 목록·가편집 값·설명·점수 비교가 그대로, 새 가편집 값은 꺼짐."""
        style.STYLES.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FIX / "style_v17.json", style.STYLES / "예전.json")
        st = style.list_styles()[0]
        self.assertIsNone(st["plan"])
        self.assertEqual(st["plan_desc"], "")
        self.assertNotIn("introTeaser", st["params"], "예전 스타일의 가편집 값은 예전과 똑같이 (새 값 없음 = 꺼짐)")
        self.assertNotIn("emphasisTitles", st["params"])
        self.assertTrue(style.describe(st["profile"]))
        r = style.distance(st["profile"], st["profile"])
        self.assertEqual(r["score"], 100)


class TestEditorWiring(WorkBase):
    def setUp(self):
        super().setUp()
        self.name = self.add_video()
        self.info = editor.media_info(self.name)
        self.base = {"keepPause": 0.4, "targetShot": 3.0, "zoomEvery": 0, "zoomScale": 1.1, "captions": True, "captionPos": "bottom",
                     "captionColor": "#FFFFFF", "lufs": -14.0, "splitShot": 0, "curve3": [0, 0, 0], "tempo": 0}

    @staticmethod
    def strip(seqs):
        s = json.dumps(seqs, ensure_ascii=False, sort_keys=True)
        import re
        return re.sub(r'"(id|link)": "[0-9a-f]{8}"', '"\\1": "-"', s)

    def test_off_keys_same_as_before(self):
        old = editor.auto_sequences(self.name, self.info, dict(self.base), ("long",))
        new = editor.auto_sequences(self.name, self.info, dict(self.base, **plan.plan_params(None)), ("long",))
        self.assertEqual(self.strip(old), self.strip(new))

    def test_teaser_and_emphasis(self):
        params = dict(self.base, introTeaser={"on": True, "sec": 4}, emphasisTitles={"perMin": 4, "color": "#FF4D4D"})
        plain = editor.auto_sequences(self.name, self.info, dict(self.base), ("long",))[0]
        seq = editor.auto_sequences(self.name, self.info, params, ("long",))[0]
        v1 = sorted([it for it in seq["items"] if it["track"] == "V1"], key=lambda it: it["start"])
        self.assertEqual(v1[0]["start"], 0.0)
        teaser = v1[0]["out"] - v1[0]["in"]
        self.assertAlmostEqual(teaser, 4.0, delta=0.6)
        pv1 = sorted([it for it in plain["items"] if it["track"] == "V1"], key=lambda it: it["start"])
        self.assertEqual(len(v1), len(pv1) + 1, "원래 컷은 지우지 않고 앞에 더함")
        self.assertAlmostEqual(v1[1]["start"], teaser, delta=0.01)
        t0 = seq["titles"][0]
        self.assertEqual((t0["start"], t0["dur"]), (0.0, round(teaser, 2)))
        em = seq["titles"][1:]
        total = max(editor.i_end(it) for it in seq["items"])
        self.assertLessEqual(len(em), int(4 * total / 60))
        self.assertTrue(em, "강조 낱말(대박·진짜)이 있는 말")
        for a, b in zip(em, em[1:]):
            self.assertGreaterEqual(b["start"] - a["start"], editor.EMPH_GAP)
        for t in em:
            self.assertEqual(t["style"]["fill"], "#FF4D4D")
            self.assertGreaterEqual(t["start"], teaser)
        self.assertEqual(seq["auto"], "style")

    def test_emphasis_skips_cut_out_places(self):
        items = editor._items_from_cuts([{"in": 0.0, "out": 10.0}, {"in": 30.0, "out": 40.0}])
        segs = [seg(15, 18, "진짜 대박이에요"), seg(32, 34, "무조건 이렇게")]
        out = editor._emphasis_titles(items, segs, 4.0, "#FFE14D")
        self.assertEqual([t["text"] for t in out], ["무조건 이렇게"], "잘려 나간 15초 말에는 넣지 않음")
        self.assertAlmostEqual(out[0]["start"], 12.0, delta=0.01, msg="원본 32초 → 타임라인 12초")


# ---------- 모델 층 (avmodels) ----------

class TestAvmodels(unittest.TestCase):
    def test_ensure_failure_marks_and_returns_false(self):
        """못 받으면 조용히 False · 10분 동안 다시 시도하지 않음 (앱을 다시 켜도)."""
        tmp = Path(tempfile.mkdtemp(prefix="모델 없음 "))
        try:
            with mock.patch.object(thumb, "MODELS", tmp), mock.patch.dict(avmodels._FAIL, {}, clear=True), \
                    mock.patch.dict(avmodels._SESS, {}, clear=True), \
                    mock.patch.object(thumb, "fetch_model", side_effect=OSError("인터넷 없음")) as fm:
                self.assertFalse(avmodels.ensure("audio"))
                self.assertTrue((tmp / ".plan-audio-download-failed").exists())
                avmodels._FAIL.clear()
                self.assertFalse(avmodels.ensure("audio"))
                self.assertEqual(fm.call_count, 1, "표시가 있으면 10분 동안 다시 받지 않음")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_specs_pinned(self):
        for kind, files in avmodels.SPECS.items():
            for fname, urls in files.values():
                for u, size, sha in urls:
                    self.assertTrue(u.startswith("https://"))
                    self.assertGreater(size, 1000)
                    self.assertRegex(sha, r"^[0-9a-f]{64}$")
        self.assertIn(0, avmodels.Y_SPEECH)
        self.assertIn(132, avmodels.Y_MUSIC)
        self.assertTrue(set(avmodels.Y_LAUGH) <= set(range(13, 19)))

    @NEED_OCR
    def test_ocr_reads_korean_caption(self):
        import numpy as np
        out = M["tmp"] / "ocr_frame.png"
        core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", "20.5", "-i", str(M["video"]), "-frames:v", "1", str(out)])
        from PIL import Image
        self.assertTrue(avmodels.ensure("ocr"))
        lines = avmodels.ocr(np.asarray(Image.open(out).convert("RGB")))
        texts = [plan._norm(x["text"]) for x in lines]
        self.assertTrue(any(plan._sim(t, "이게 들어간다고?") >= 0.8 for t in texts), texts)  # 굵은 글꼴은 한 글자쯤 틀리기도 함

    @unittest.skipUnless(AUDIO_OK, "소리 모델이 없어요 (~/.futsal-studio/models)")
    def test_tags_shapes(self):
        import numpy as np
        self.assertTrue(avmodels.ensure("audio"))
        r = avmodels.tags(np.zeros(16000 * 61, np.float32))
        self.assertEqual(set(r), {"speech", "laugh", "cheer", "music", "sfx"})
        self.assertEqual(len(r["speech"]), len(r["sfx"]))
        self.assertTrue(125 <= len(r["speech"]) <= 128, len(r["speech"]))  # 60초 = 125칸 + 끝 1초 조각


# ---------- 클로드 계정 경로 (가짜 claude) ----------

FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time
    args = sys.argv[1:]
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    def logit(extra):
        p = os.environ.get("FAKE_CLAUDE_LOG")
        if p:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(extra, args=args, cwd=os.getcwd(), pid=os.getpid(), files=sorted(os.listdir(".")),
                    api_key="ANTHROPIC_API_KEY" in os.environ, auth_token="ANTHROPIC_AUTH_TOKEN" in os.environ,
                    oauth=os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")), ensure_ascii=False) + "\\n")
    if args[:1] == ["--help"]:
        if mode == "slowhelp":
            time.sleep(5)
        print("Usage: claude [options]\\n  -p, --print\\n  --output-format <f>\\n  --tools <tools...>\\n  --no-session-persistence\\n  --strict-mcp-config\\n  --setting-sources <s>")
        if mode != "old":
            print("  --permission-prompts <target>\\n  --restricted")
        print("Commands:\\n  auth   Manage authentication")
        sys.exit(0)
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": mode != "login", "authMethod": "claude.ai"}))
        sys.exit(0 if mode != "login" else 1)
    if "-p" in args:
        if mode == "unknown":
            sys.stderr.write("error: unknown option '--strict-mcp-config'\\n")
            sys.exit(1)
        prompt = sys.stdin.read()
        logit({"prompt_len": len(prompt)})
        if mode == "noisy":  # 표준 오류를 파이프 버퍼보다 훨씬 많이 쓴 뒤 대답
            sys.stderr.write("경고 " * 400000)
            sys.stderr.flush()
        if mode == "hang":
            time.sleep(60)
        if mode == "login":
            print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"}))
            sys.exit(1)
        if mode == "limit":
            print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429, "result": "Claude AI usage limit reached"}))
            sys.exit(1)
        if mode == "bad":
            print("잘못된 출력")
            sys.exit(0)
        ans = {"intro": "결과를 먼저 보여 주는 티저형 훅 → 바로 본론", "genre": "축구 예능 챌린지", "format": "2~3인 대결 + 현장 리액션",
               "captions": "예능 자막 위주", "fun": "슬로모션 리플레이", "summary": "이 채널은 축구 예능 챌린지예요.", "apply": ["티저 4초", "강조 자막"]}
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "```json\\n" + json.dumps(ans, ensure_ascii=False) + "\\n```",
                          "modelUsage": {"claude-haiku-4-5": {}, "claude-opus-5-5": {}}}, ensure_ascii=False))
        sys.exit(0)
    sys.exit(2)
''')


@POSIX
class TestClaudeCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="가짜 클로드 "))
        self.exe = self.tmp / "claude"
        self.exe.write_text(FAKE, encoding="utf-8")
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "calls.jsonl"
        self.env = mock.patch.dict(os.environ, {"FUTSAL_CLAUDE": str(self.exe), "FAKE_CLAUDE_LOG": str(self.log),
                                                "ANTHROPIC_API_KEY": "sk-ant-api-should-not-pass", "ANTHROPIC_AUTH_TOKEN": "x"})
        self.env.start()
        self.home = mock.patch.object(claude_cli, "HOME", self.tmp / "home")
        self.home.start()
        claude_cli._FEAT.clear()
        claude_cli.reset_status()

    def tearDown(self):
        self.env.stop()
        self.home.stop()
        claude_cli._FEAT.clear()
        claude_cli.reset_status()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def mode(self, m):
        os.environ["FAKE_CLAUDE_MODE"] = m

    def test_success_env_and_cwd(self):
        self.mode("ok")
        claude_cli.save_token("sk-ant-oat01-" + "a" * 40)
        img = self.tmp / "장면.jpg"
        img.write_bytes(b"\xff\xd8jpg")
        r = claude_cli.run("프롬프트 본문", images=[(img, "scene1.jpg")])
        self.assertEqual(r["model"], "claude-opus-5-5")
        self.assertIn("축구 예능 챌린지", r["text"])
        c = self.calls()[-1]
        self.assertFalse(c["api_key"] or c["auth_token"], "API 키 변수는 자식에 안 감")
        self.assertEqual(c["oauth"], "sk-ant-oat01-" + "a" * 40, "로그인 코드는 CLAUDE_CODE_OAUTH_TOKEN 으로만")
        self.assertEqual(c["files"], ["scene1.jpg"], "빈 임시 폴더에 그림만")
        self.assertFalse(Path(c["cwd"]).exists(), "끝나면 임시 폴더를 지움")
        self.assertEqual(c["prompt_len"], len("프롬프트 본문"), "프롬프트는 stdin 으로")
        self.assertNotIn("프롬프트 본문", " ".join(c["args"]))
        a = c["args"]
        self.assertEqual(a[a.index("--tools") + 1], "Read")
        self.assertIn("--restricted", a)
        self.assertEqual(a[a.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(a[a.index("--model") + 1], "opus")
        self.assertNotIn("--allowedTools", a, "Read 를 폴더 밖까지 미리 허락하지 않음")

    def test_no_images_no_tools(self):
        self.mode("ok")
        claude_cli.run("x")
        a = self.calls()[-1]["args"]
        self.assertEqual(a[a.index("--tools") + 1], "")

    def test_old_cli_drops_unknown_flags(self):
        self.mode("old")
        claude_cli.run("x")
        a = self.calls()[-1]["args"]
        self.assertNotIn("--permission-prompts", a)
        self.assertNotIn("--restricted", a)

    def test_errors_are_korean(self):
        for m, kind, word in (("login", "login", "로그인"), ("limit", "limit", "한도"), ("bad", "error", "대답하지 못했어요")):
            self.mode(m)
            with self.assertRaises(claude_cli.ClaudeError) as cm:
                claude_cli.run("x")
            self.assertEqual(cm.exception.kind, kind)
            self.assertIn(word, str(cm.exception))

    def test_timeout_kills(self):
        self.mode("hang")
        t = time.time()
        with self.assertRaises(claude_cli.ClaudeError) as cm:
            claude_cli.run("x", timeout=1.5)
        self.assertEqual(cm.exception.kind, "timeout")
        self.assertLess(time.time() - t, 30)
        pid = self.calls()[-1]["pid"]
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_cancel(self):
        self.mode("hang")
        ev = threading.Event()
        threading.Timer(0.8, ev.set).start()
        with self.assertRaises(claude_cli.ClaudeError) as cm:
            claude_cli.run("x", cancel=ev)
        self.assertEqual(cm.exception.kind, "cancel")

    def test_status_and_missing(self):
        self.mode("ok")
        self.assertEqual(claude_cli.status(refresh=True)["state"], "ready")
        self.mode("login")
        self.assertEqual(claude_cli.status()["state"], "ready", "60초 동안은 기억")
        self.assertEqual(claude_cli.status(refresh=True)["state"], "login")
        with mock.patch.dict(os.environ, {"FUTSAL_CLAUDE": str(self.tmp / "없음")}):
            self.assertEqual(claude_cli.status(refresh=True)["state"], "missing")
            with self.assertRaises(claude_cli.ClaudeError) as cm:
                claude_cli.run("x")
            self.assertEqual(cm.exception.kind, "missing")

    def test_token_format_and_clear(self):
        with self.assertRaises(claude_cli.ClaudeError):
            claude_cli.save_token("sk-ant-api03-wrong")
        claude_cli.save_token("  sk-ant-oat01-" + "b" * 30 + "  ")
        self.assertTrue(claude_cli.has_token())
        self.assertEqual(stat.S_IMODE(claude_cli.token_file().stat().st_mode), 0o600)
        claude_cli.clear_token()
        self.assertFalse(claude_cli.has_token())

    def test_explain(self):
        self.assertEqual(claude_cli.explain(1, "", "Error: getaddrinfo ENOTFOUND api.anthropic.com")[0], "network")
        self.assertEqual(claude_cli.explain(1, json.dumps({"type": "result", "is_error": True, "api_error_status": 401, "result": "x"}), "")[0], "login")
        self.assertEqual(claude_cli.explain(0, json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok"}), "")[0], "ok")


@POSIX
class TestPlanRoutes(TestClaudeCli):
    """app.py 경로: 상태 · 물어볼 내용 · 붙여 넣기 · 클로드로 더 깊게 보기(작업) · 기록에 프롬프트·토큰이 안 남음."""

    def setUp(self):
        super().setUp()
        self.wtmp = Path(tempfile.mkdtemp(prefix="경로 작업 "))
        dirs = work_dirs(self.wtmp)
        self.work = dirs["WORK"]
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(style, "STYLES", self.work / "styles"),
                                                                             mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log")]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"
        style.STYLES.mkdir(parents=True, exist_ok=True)
        old = json.loads((FIX / "style_v17.json").read_text(encoding="utf-8"))
        (style.STYLES / "예전.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        newp = dict(old, plan=TestFunAndMerge()._plan("teaser", 300))
        newp["plan"]["summary"] = "요약"
        newp["plan"]["ai"] = None
        (style.STYLES / "기획.json").write_text(json.dumps(newp, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.p2:
            p.stop()
        shutil.rmtree(self.wtmp, ignore_errors=True)
        super().tearDown()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_job(self):
        for _ in range(600):  # 바쁜 PC 에서도 (가짜 claude 는 금방 끝남)
            if not self.app.JOB["name"]:
                return self.app.JOB
            time.sleep(0.1)
        self.fail("작업이 끝나지 않았어요")

    def test_status_route(self):
        self.mode("ok")
        code, j = self.call("/api/claude/status?refresh=1")
        self.assertEqual((code, j["state"]), (200, "ready"))

    def test_prompt_route(self):
        from urllib.parse import quote
        code, j = self.call("/api/style/plan_prompt?name=" + quote("기획"))
        self.assertEqual(code, 200)
        self.assertIn("JSON 형식으로만", j["prompt"])
        self.assertIn("토막", j["prompt"])
        self.assertEqual(self.call("/api/style/plan_prompt?name=" + quote("예전"))[0], 400)
        self.assertEqual(self.call("/api/style/plan_prompt?name=" + quote("../../x"))[0], 404)

    def test_paste_route(self):
        code, j = self.call("/api/style/plan_paste", {"name": "기획", "text": "그냥 글"})
        self.assertEqual(code, 400)
        self.assertIn("JSON", j["error"])
        ans = json.dumps({"intro": "티저", "genre": "축구 예능", "format": "2인", "captions": "예능", "fun": "효과음", "apply": ["a"]}, ensure_ascii=False)
        code, j = self.call("/api/style/plan_paste", {"name": "기획", "text": ans})
        self.assertEqual(code, 200, j)
        d = json.loads((style.STYLES / "기획.json").read_text(encoding="utf-8"))
        self.assertEqual(d["plan"]["ai"]["by"], "paste")
        self.assertEqual(d["captionPos"], json.loads((FIX / "style_v17.json").read_text(encoding="utf-8"))["captionPos"], "다른 값은 그대로")
        code, j = self.call("/api/style/plan_paste", {"name": "예전", "text": ans})
        self.assertEqual(code, 400, "기획 분석이 없는 예전 스타일")

    def test_ai_job_success_and_log_clean(self):
        self.mode("ok")
        claude_cli.save_token("sk-ant-oat01-" + "c" * 40)
        code, j = self.call("/api/style/plan_ai", {"name": "기획"})
        self.assertEqual((code, j["ok"]), (200, True))
        res = self.wait_job()["result"]
        self.assertTrue(res["ok"], res)
        d = json.loads((style.STYLES / "기획.json").read_text(encoding="utf-8"))
        self.assertEqual(d["plan"]["ai"]["genre"], "축구 예능 챌린지")
        self.assertEqual(d["plan"]["ai"]["by"], "claude-cli")
        log = (self.work / "studio.log").read_text(encoding="utf-8")
        self.assertNotIn("sk-ant-oat01", log)
        self.assertNotIn("경력 많은 유튜브 PD", log, "프롬프트는 기록에 남기지 않음")
        self.assertIn("클로드 판단 · 저장했어요", log)

    def test_ai_job_login_error_keeps_local(self):
        self.mode("login")
        self.call("/api/style/plan_ai", {"name": "기획"})
        res = self.wait_job()["result"]
        self.assertFalse(res["ok"])
        self.assertEqual(res["kind"], "login")
        self.assertIn("로그인", res["error"])
        d = json.loads((style.STYLES / "기획.json").read_text(encoding="utf-8"))
        self.assertIsNone(d["plan"]["ai"])
        self.assertEqual(self.call("/api/style/plan_ai", {"name": "없는 스타일"})[0], 404)

    def test_token_route(self):
        code, j = self.call("/api/claude/token", {"token": "nope"})
        self.assertEqual(code, 400)
        code, j = self.call("/api/claude/token", {"token": "sk-ant-oat01-" + "d" * 40})
        self.assertEqual(code, 200)
        self.assertTrue(claude_cli.has_token())
        self.assertNotIn("sk-ant-oat01", (self.work / "studio.log").read_text(encoding="utf-8"))
        self.call("/api/claude/token", {"clear": True})
        self.assertFalse(claude_cli.has_token())


# ---------- 검토 지적 회귀 테스트 (티저·자막·장르·강조 자막·인트로·합치기·형식·클로드 CLI·기록 재사용·멈추기) ----------

def shots_pe(bounds, dur, same=None, seed=2):
    """컷 경계(bounds)마다 다른 화면 지문 (same={토막 번호: 같은 화면을 쓸 토막 번호}) → (pe, sev)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    bases = [int(rng.integers(0, 2 ** 63)) for _ in bounds]
    for k, v in (same or {}).items():
        bases[k] = bases[v]
    hs = []
    for t in range(dur):
        k = max(i for i, b in enumerate(bounds) if b <= t)
        h = bases[k]
        for b in rng.choice(64, int(rng.integers(0, 4)), replace=False):
            h ^= 1 << int(b)
        hs.append(h)
    pe = fake_pe(n=dur, hash={"fps": 1, "data": plan.pack(np.array(hs, dtype=np.uint64), "uint64")},
                 flat=plan.pack(rng.integers(40, 90, dur).astype(np.uint8)), ocr=[])
    sev = {"cuts": [float(b) for b in bounds[1:]], "motion": [5.0] * (2 * dur), "rms": [-20.0] * (10 * dur), "rmsStep": 0.1}
    return pe, sev


class TestReviewTeaser(unittest.TestCase):
    def test_long_shot_crossing_intro_window_is_not_teaser(self):
        """인트로 창(60초)을 넘어 이어지는 한 장면은 '뒤에 다시 나온 화면'이 아님 → 티저 아님 · 가편집 티저 꺼짐."""
        bounds = [0, 15, 55] + list(range(82, 600, 27))
        pe, sev = shots_pe(bounds, 600)
        segs = [seg(float(t), t + 3.0, "디딤발을 공 옆에 두고 차요") for t in range(2, 600, 4)]
        self.assertEqual(plan.detect(pe, sev, segs)["ev"]["teaser"], [])
        p = plan.judge(pe, sev, segs, "인사이드 패스 차는 법")
        self.assertNotEqual(p["intro"]["type"], "teaser")
        self.assertFalse(plan.plan_params(p)["introTeaser"]["on"])

    def test_same_spot_intro_and_outro_is_not_teaser(self):
        """처음과 끝을 같은 자리에서 찍은 것(A-B-A) · 같은 자리로 돌아온 고정 화면은 티저도, 느린 리플레이도 아님."""
        bounds = [0, 20] + [20 + 47 * k for k in range(1, 12)] + [585]
        pe, sev = shots_pe(bounds, 600, same={len(bounds) - 1: 0}, seed=1)
        segs = [seg(float(t), t + 3.0, "디딤발을 공 옆에 두고 차요") for t in range(2, 600, 4)]
        segs[0]["text"] = "안녕하세요 풋살사관학교 최경진입니다"
        d = plan.detect(pe, sev, segs)
        self.assertEqual(d["ev"]["teaser"], [])
        self.assertEqual(d["ev"]["replay"], [])
        # 인사 없이 12초 인트로여도 끝인사 자리(마지막 8%·30초)는 티저 원본으로 보지 않음
        bounds2 = [0, 12] + [12 + 48 * k for k in range(1, 12)] + [585]
        pe2, sev2 = shots_pe(bounds2, 600, same={len(bounds2) - 1: 0}, seed=4)
        self.assertEqual(plan.detect(pe2, sev2, [])["ev"]["teaser"], [])

    def test_real_teaser_still_found(self):
        """맨 앞 4초가 본편의 한 장면(컷으로 떨어진 곳)과 같으면 티저."""
        bounds = [0, 4, 30, 90, 150, 154, 200]
        pe, sev = shots_pe(bounds, 300, same={0: 4}, seed=5)
        ev = plan.detect(pe, sev, [seg(6, 10, "오늘은 슈팅을 해 볼게요")])["ev"]
        self.assertEqual(len(ev["teaser"]), 1, ev["teaser"])
        a, b, src = ev["teaser"][0]
        self.assertEqual((a, b), (0, 4))
        self.assertTrue(150 <= src < 154)


class TestReviewCaptions(unittest.TestCase):
    def test_quoted_big_color_caption_is_emphasis(self):
        """대사를 따라 크게·색으로 띄운 예능 자막은 '말 자막'이 아니라 강조."""
        C = plan.classify_caption
        self.assertEqual(C("진짜 미쳤다!!", 0.10, "yellow", "middle", speech_sim=0.9), "emph")
        self.assertEqual(C("들어갔다!!", 0.12, "red", "middle", speech_sim=1.0), "emph")
        self.assertEqual(C("이게 되네", 0.11, "white", "bottom", speech_sim=1.0), "emph", "아주 큰 글씨")
        self.assertEqual(C("오늘은 슈팅 연습을 해볼게요", 0.05, "white", "bottom", speech_sim=0.95), "speech")
        self.assertEqual(C("공을 끝까지 보세요", 0.06, "yellow", "bottom", speech_sim=0.95), "speech", "늘 노란 작은 말 자막")

    def test_variety_channel_reads_as_variety(self):
        """대사를 따라 큰 노랑·빨강으로 강조하는 채널 → '예능 자막 위주' + 핵심 단어 강조 · 가편집 강조 자막 켜짐."""
        n = 120
        segs, ocr = [], []
        for k in range(0, n - 4, 6):
            segs.append(seg(k, k + 3, "와 진짜 들어갔다"))
            ocr.append({"t": k, "d": 2, "lines": [{"text": "진짜 들어갔다!!", "box": [0.3, 0.4, 0.7, 0.52], "h": 0.11, "conf": 0.9,
                                                    "cname": "yellow" if k % 12 else "red", "color": "#FFE14D"}]})
        pe = fake_pe(n=n, ocr=ocr)
        p = plan.judge(pe, {"cuts": [], "motion": [2.0] * (2 * n)}, segs, "슈팅 대결")
        self.assertEqual(p["captions"]["style"], "variety", p["captions"])
        self.assertIn("핵심 단어", p["captions"]["label"])
        self.assertGreater(plan.plan_params(p)["emphasisTitles"]["perMin"], 0)


LESSON_LINES = ["안녕하세요 풋살사관학교 최경진입니다", "오늘은 같이 인사이드 패스를 해 볼게요", "선수들이 많이 하는 실수가 있어요",
                "디딤발을 공 옆에 두고 같이 차 볼게요", "자 같이 해 보시죠", "선수 시절에 저도 이걸 많이 했는데요", "이렇게 하면 성공이에요",
                "실패하면 다시 해 보세요", "무릎을 살짝 굽히고", "시선은 공을 봅니다"] * 12


class TestReviewGenre(unittest.TestCase):
    def genre(self, title, lines, spk=None, fun_items=()):
        segs = [seg(i * 4.0, i * 4 + 3.5, t) for i, t in enumerate(lines)]
        pe = {"duration": 600, "ocr": [], "audio": None}
        fun = {"items": [{"key": k, "perMin": v} for k, v in fun_items]}
        return plan.judge_genre(pe, {}, segs, title, fun, {"style": "speech", "perMin": {}}, spk)

    def test_lesson_not_vlog_or_interview(self):
        """'같이'·'선수'·'성공/실패' 가 자주 나오는 평범한 레슨 → 레슨 (브이로그·인터뷰·챌린지 아님)."""
        one = {"n": 1, "share": [1.0], "turnsPerMin": 0}
        for t in ("인사이드 패스 제대로 차는 법", "초보자 트래핑 이것만 알면 끝", "슈팅 정확도 올리는 3가지", "[풋살] 인사이드 패스"):
            g = self.genre(t, LESSON_LINES, one)
            self.assertEqual(g["key"], "lesson", (t, g["scores"]))
            self.assertIn("레슨", g["label"])

    def test_transcript_only_is_not_sure(self):
        """제목에 단서가 없고 대사만으로 정하면 '확실해요'로 보이지 않음."""
        g = self.genre("오늘의 영상", ["오늘은 패스 연습을 해요 자세를 잡고"] * 30, {"n": 1, "share": [1.0], "turnsPerMin": 0})
        self.assertEqual(g["key"], "lesson")
        self.assertLess(g["conf"], 0.75)

    def test_challenge_titles(self):
        many = {"n": 3, "share": [0.4, 0.35, 0.25], "turnsPerMin": 8}
        ch = ["이거 진짜 들어가면 치킨 쏜다", "와 대박", "아 실패", "들어갔다!!"] * 20
        for t in ("골대 맞추기 10번 하면 100만원", "프로선수 vs 일반인 슈팅 속도 대결"):
            self.assertEqual(self.genre(t, ch, many, [("laugh", 1.2)])["key"], "challenge", t)


class TestReviewEmphasis(unittest.TestCase):
    def test_no_word_fragments(self):
        """낱말 줄기·부분 일치('잘하!'·'힘들!'·'아라!'(잡아라)·'턴!'(패턴))·흔한 말('진짜!'·'어떻게!')은 띄우지 않음."""
        lines = ["자 이번에는 다들 잘하시는 분들도 많이 틀리는 부분이에요", "생각보다 이거 힘들어요 여러분 다리가 후들후들",
                 "공을 잡아라 그리고 바로 돌아라 이렇게", "이게 진짜 되는 건지 저도 궁금했어요", "제가 어떻게 하는지 한번 보세요 천천히 할게요",
                 "패턴을 익히는 게 먼저입니다 반복 또 반복"]
        segs = [seg(20.0 * i + 5, 20.0 * i + 9, t) for i, t in enumerate(lines)]
        items = [{"track": "V1", "media": "main", "in": 0.0, "out": 200.0, "start": 0.0, "speed": 1}]
        self.assertEqual(editor._emphasis_titles(items, segs, 4.0, "#FFE14D"), [])

    def test_meaningful_labels(self):
        self.assertEqual(editor.emphasis_label("인사이드 패스는 디딤발 위치가 핵심이에요")[0], "인사이드 패스 핵심!")
        self.assertEqual(editor.emphasis_label("슈팅할 때 실수 많이 하시는 부분이에요 여기서 실수하면 안 돼요")[0], "슈팅 실수!")
        self.assertEqual(editor.emphasis_label("무조건 이렇게")[0], "무조건 이렇게")
        self.assertIsNone(editor.emphasis_label("패턴을 익히는 게 먼저입니다 반복 또 반복")[0])
        self.assertEqual(editor.emphasis_label("돌파는 타이밍이 중요해요 상대 중심을 보세요")[0], "돌파 중요!")
        self.assertEqual(editor.emphasis_label("트래핑을 연습해 볼 건데요 공을 잘 보세요")[0], "트래핑!")


class TestReviewIntro(unittest.TestCase):
    def judge(self, lines):
        segs = [seg(i * 3.0, i * 3 + 2.8, t) for i, t in enumerate(lines)]
        pe = fake_pe(n=600)
        d = {"ev": {"teaser": [], "title": []}, "aux": {"W": 60}}
        return plan.judge_intro(pe, d, segs, {})

    def test_greeting_then_routine_question(self):
        """인사 뒤의 '뭘 배워 볼까요?' 는 훅이 아님 → 인사로 시작 · '훅 멘트' 가 두 번 나오지 않음."""
        j = self.judge(["안녕하세요 풋살사관학교 최경진입니다", "오늘은 뭘 배워 볼까요?", "바로 인사이드 패스입니다"] + ["디딤발은 공 옆에 두고 차요"] * 30)
        self.assertEqual(j["type"], "greeting")
        self.assertTrue(j["label"].startswith("인사"), j["label"])
        self.assertLessEqual(j["label"].count("훅"), 1)

    def test_yeoreobun_claim_is_hook(self):
        """'여러분 이 기술 하나면 수비 다 뚫습니다' 는 인사가 아니라 단언형 훅."""
        j = self.judge(["여러분 이 기술 하나면 수비 다 뚫습니다", "보여 드릴게요"] + ["디딤발은 공 옆에 두고 차요"] * 30)
        self.assertEqual((j["type"], j["hook"]), ("hook_line", "단언형"))
        self.assertEqual(j["greeting"], "none")

    def test_label_follows_real_order(self):
        """질문 훅 → 인사 순서면 라벨도 그 순서."""
        j = self.judge(["이 슈팅 왜 안 들어갈까요?", "안녕하세요 풋살사관학교 최경진입니다"] + ["디딤발은 공 옆에 두고 차요"] * 30)
        self.assertEqual(j["type"], "hook_line")
        self.assertIn("질문형 훅 멘트로 시작 → 인사·자기소개 → 본론", j["label"])


class TestReviewMergeAndFormat(TestFunAndMerge):
    def test_split_votes_summary_and_params(self):
        """레퍼런스가 반반으로 갈리면: 요약은 '영상마다 달라요 (티저형 1개, 인사형 1개)' · 가편집 티저·강조 자막은 끔 · 팁은 '직접 해 보세요'."""
        a, b = self._plan("teaser", 600), self._plan("greeting", 600, genre="vlog")
        b["captions"] = dict(b["captions"], style="speech", label="말을 그대로 받아쓴 자막 위주")
        m = plan.merge_plans([a, b])
        self.assertIn("인트로는 영상마다 달라요 (", m["summary"])
        self.assertIn("티저형 1개", m["summary"])
        self.assertIn("자막은 영상마다 달라요 (", m["summary"])
        self.assertNotIn("또는", m["summary"])
        self.assertNotIn("영상마다 달라요:", m["headline"])
        pp = plan.plan_params(m)
        self.assertFalse(pp["introTeaser"]["on"])
        self.assertEqual(pp["emphasisTitles"]["perMin"], 0)
        self.assertFalse(any(x["auto"] for x in m["apply"]), m["apply"])

    def test_headline_is_one_synthesized_sentence(self):
        p = self._plan("teaser", 600)
        p["genre"]["label"] = "축구 예능 챌린지"
        p["fun"]["items"].append({"key": "slowmo_replay", "label": plan.FUN_LABEL["slowmo_replay"], "perMin": 0.8, "level": "가끔", "ev": []})
        h = plan.headline(p)
        self.assertTrue(h.startswith("이 채널은 결과 장면을 먼저 보여 주며 시작해"), h)
        self.assertEqual(h.count("."), 1)
        self.assertNotIn("+", h)
        self.assertIn("축구 예능 챌린지 채널이에요", h)

    def test_format_follows_challenge_and_turf(self):
        """제목·장르가 대결이면 목소리 묶음이 하나여도 '1인 진행'이라 하지 않음 · 초록 바닥은 '잔디 구장'(야외/실내라고 하지 않음)."""
        import numpy as np
        pe = fake_pe(n=120, title="슈팅 챌린지 1대1 대결", green=plan.pack(np.full(120, 200)))
        d = plan.detect(pe, {}, [])
        fun = plan.judge_fun(pe, d, [], {})
        caps_j = plan.judge_captions(pe, d, [], {})
        genre = {"key": "lesson", "keys": ["lesson", "challenge"], "label": "축구 레슨·챌린지"}
        f = plan.judge_format(pe, d, [], {}, genre, fun, caps_j, {"n": 1, "share": [1.0], "turnsPerMin": 0})
        self.assertNotIn("1인 진행", f["labels"])
        self.assertIn("대결·챌린지 진행", f["labels"])
        self.assertIn("잔디 구장 위주", f["labels"])
        self.assertFalse(any(x in " ".join(f["labels"]) for x in ("야외", "실내")))

    test_levels = test_fun_without_audio_says_unknown = test_merge_disagree = test_merge_agree = test_plan_params_default_off = None  # 부모 것은 한 번만


class TestReviewTeaserPick(unittest.TestCase):
    def test_teaser_prefers_quiet_loud_demo(self):
        """티저는 가장 긴 컷의 가운데(설명 중)가 아니라 말이 없고 소리가 큰 시범 장면에서."""
        tidy = [{"in": 0.0, "out": 40.0}, {"in": 50.0, "out": 60.0}]
        segs = [seg(0, 40, "길게 설명하는 중이에요")]
        peaks = {"per_sec": 10, "peaks": [0.1] * 520 + [0.9] * 30 + [0.1] * 100}
        items = editor._items_from_cuts(tidy)
        out, titles = editor._intro_teaser(items, {"shorts": []}, tidy, 4.0, segs, peaks)
        v = out[0]
        self.assertTrue(50.0 <= v["in"] and v["out"] <= 60.0, v)
        self.assertEqual(titles[0]["start"], 0.0)
        a1 = [it for it in out if it["track"] == "A1"][0]
        self.assertEqual(a1["fx"], {}, "말이 없는 곳이면 소리는 그대로")
        # 말이 많은 곳밖에 없으면 그 소리를 줄임
        out2, _ = editor._intro_teaser(editor._items_from_cuts(tidy[:1]), {"shorts": []}, tidy[:1], 4.0, segs, None)
        self.assertTrue([it for it in out2 if it["track"] == "A1"][0]["fx"].get("level"))


class TestReviewPrompt(WorkBase):
    def test_prompt_has_body_samples_and_caption_texts(self):
        name = fx.PLAN_NAME
        core.adir(name).mkdir(parents=True, exist_ok=True)
        segs = [seg(float(t), t + 2.0, f"본편 대사 {t}") for t in range(0, 600, 5)]
        (core.adir(name) / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (core.adir(name) / "plan_events.json").write_text(json.dumps({"caps": [
            {"a": 100, "b": 104, "text": "들어갔다!!", "kind": "emph", "h": 0.11, "cname": "yellow", "pos": "middle"}]}, ensure_ascii=False), encoding="utf-8")
        p = TestFunAndMerge._plan(None, "teaser", 600)
        prof = {"plan": p, "refs": [{"source": name, "plan": dict(p, title="테스트", source=name)}]}
        txt = plan.claude_prompt(prof)
        self.assertIn("틀릴 수 있어요", txt)
        self.assertIn("본편에서 고르게 고른 대사", txt)
        self.assertIn("본편 대사 3", txt[txt.index("본편에서"):])  # 60초 뒤 대사가 들어감
        self.assertIn("'들어갔다!!' (강조·노랑·큰 글씨·가운데)", txt)
        self.assertIn("1분에 몇 번", txt)
        short = plan._body_windows([seg(64.5, 68.5, "구독 좋아요 부탁해요")], 70)
        self.assertEqual(len(short), 1, "짧은 영상에서 같은 대사를 되풀이하지 않음")


@POSIX
class TestReviewClaudeCli(TestClaudeCli):
    """--help 확인 실패 시 안전 옵션을 빼지 않음 · 모르는 옵션 → 업데이트 안내 · 표준 오류가 많아도 멈추지 않음."""

    def test_help_timeout_fails_closed(self):
        self.mode("slowhelp")
        with mock.patch.object(claude_cli, "HELP_TIMEOUTS", (0.5, 0.5)):
            f = claude_cli.features()
        self.assertEqual(f, {})
        self.assertNotIn(str(self.exe), claude_cli._FEAT, "실패한 확인은 기억하지 않음")
        for img in (True, False):
            cmd = claude_cli.build_cmd(str(self.exe), img, f)
            for flag in ("--no-session-persistence", "--strict-mcp-config", "--setting-sources", "--tools"):
                self.assertIn(flag, cmd)
            self.assertEqual(cmd[cmd.index("--tools") + 1], "Read" if img else "")
        self.mode("ok")
        self.assertTrue(claude_cli.features()["tools"], "다음에 다시 확인")

    def test_unknown_option_asks_update(self):
        self.mode("unknown")
        with self.assertRaises(claude_cli.ClaudeError) as cm:
            claude_cli.run("x")
        self.assertEqual(cm.exception.kind, "update")
        self.assertIn("새 판", str(cm.exception))

    def test_noisy_stderr_does_not_block(self):
        self.mode("noisy")
        t = time.time()
        r = claude_cli.run("x", timeout=60)
        self.assertIn("축구 예능 챌린지", r["text"])
        self.assertLess(time.time() - t, 30)

    # 부모 클래스 테스트는 다시 돌리지 않음
    test_success_env_and_cwd = test_no_images_no_tools = test_old_cli_drops_unknown_flags = None
    test_errors_are_korean = test_timeout_kills = test_cancel = test_status_and_missing = None
    test_token_format_and_clear = test_explain = None


class TestReviewCacheAndCancel(WorkBase):
    def silent_video(self, name="무음 it's.mp4", sec=20):
        import subprocess
        p = core.VIDEOS / name
        subprocess.run([core.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=10:duration={sec}",
                        "-an", "-pix_fmt", "yuv420p", str(p)], check=True)
        return name

    def test_silent_video_cache_hit(self):
        """소리 없는 영상: 소리 모델을 썼으면 다음에 기록을 그대로 씀 (예전엔 매번 다시 봄) · 기록에 '소리 없는 영상'."""
        name = self.silent_video()
        logs = []
        with mock.patch.object(avmodels, "ensure", side_effect=lambda kind, **k: kind == "audio"), \
                mock.patch.object(avmodels, "usable", side_effect=lambda kind: kind == "audio"), \
                mock.patch.object(face, "ready", return_value=False):
            pe = plan.extract_plan(name, None, logs.append)
            self.assertTrue(pe["models"]["audio"])
            self.assertFalse(pe["hasAudio"])
            self.assertIn("소리 없는 영상", logs[-1])
            with mock.patch.object(plan, "_video_pass", side_effect=AssertionError("다시 보면 안 됨")):
                plan.extract_plan(name, None, logs.append)
        self.assertIn("예전에 살펴본 기록", logs[-1])
        p = plan.judge(pe, style.extract_events(name, quiet), [])
        self.assertIn("소리가 없는 영상", p["fun"]["label"])

    def test_corrupt_model_file_is_dropped(self):
        """파일은 있는데 불러오지 못하면(깨진 파일) 지우고 10분 동안 다시 시도하지 않음 → 기록 재사용 판단도 '없음'."""
        tmp = Path(tempfile.mkdtemp(prefix="깨진 모델 "))
        try:
            with mock.patch.object(thumb, "MODELS", tmp), mock.patch.dict(avmodels._FAIL, {}, clear=True), \
                    mock.patch.dict(avmodels._SESS, {}, clear=True):
                (tmp / avmodels.SPECS["audio"]["yamnet"][0]).write_bytes(b"not a model")
                self.assertTrue(avmodels.usable("audio"))
                self.assertFalse(avmodels.ensure("audio"))
                self.assertFalse((tmp / avmodels.SPECS["audio"]["yamnet"][0]).exists())
                self.assertFalse(avmodels.usable("audio"))
                self.assertTrue(avmodels._mark("audio").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_download_cancel(self):
        """처음 내려받는 중에 ✕ → 바로 멈춤 (반쪽 파일·실패 표시 없음) · 기획 분석은 PlanCancelled."""
        tmp = Path(tempfile.mkdtemp(prefix="받기 멈춤 "))

        def slow_download(url, dest, progress=None, timeout=30):
            Path(dest).write_bytes(b"x" * 10)
            for k in range(100):
                progress(k, 100)
                time.sleep(0.01)
            return dest
        try:
            with mock.patch.object(thumb, "MODELS", tmp), mock.patch.dict(avmodels._FAIL, {}, clear=True), \
                    mock.patch.dict(avmodels._SESS, {}, clear=True), mock.patch.object(thumb.updater, "download", side_effect=slow_download):
                editor.CANCEL.set()
                with self.assertRaises(thumb.DownloadCancelled):
                    avmodels.ensure("audio", cancel=plan._cancelled)
                self.assertEqual(list(tmp.glob("*.part")), [])
                self.assertFalse(avmodels._mark("audio").exists())
                self.assertNotIn("audio", avmodels._FAIL)
                name = self.silent_video("멈춤.mp4", 4)
                with mock.patch.object(face, "ready", return_value=False):
                    with self.assertRaises(plan.PlanCancelled):
                        plan.extract_plan(name, {"duration": 4}, quiet)
        finally:
            editor.CANCEL.clear()
            shutil.rmtree(tmp, ignore_errors=True)


class TestReviewScoreIgnoresPlan(unittest.TestCase):
    def test_score_video_drops_plan_params(self):
        """스타일 일치 점수는 기획 분석 값(티저·강조 자막) 없이 만든 가편집으로 매김."""
        seen = {}
        st = {"params": {"keepPause": 0.4, "introTeaser": {"on": True, "sec": 4}, "emphasisTitles": {"perMin": 2, "color": "#FFE14D"}},
              "profile": {}}

        def auto(name, info, params, kinds):
            seen["params"] = params
            return [{"master": {}}]
        with mock.patch.object(style, "_score_inputs", return_value=(st, [])), mock.patch.object(style, "_cached_events", return_value={}), \
                mock.patch.object(editor, "media_info", return_value={"duration": 10, "width": 1920, "height": 1080}), \
                mock.patch.object(editor, "auto_sequences", side_effect=auto), \
                mock.patch.object(style, "profile_from_sequence", return_value={k: 0 for k in ("cutsPerMin", "avgShot", "zoomCutsPerMin", "pauseP75",
                                                                                                  "captionRatio", "captionPos", "captionColor", "lufs")}), \
                mock.patch.object(style, "distance", return_value={"score": 50}):
            style.score_video("s", "v.mp4")
        self.assertNotIn("introTeaser", seen["params"])
        self.assertNotIn("emphasisTitles", seen["params"])
        self.assertEqual(seen["params"]["keepPause"], 0.4)


@POSIX
class TestReviewPasteBusy(TestPlanRoutes):
    def test_paste_refused_while_style_job_runs(self):
        ans = json.dumps({"intro": "티저", "genre": "축구 예능", "format": "2인", "captions": "예능", "fun": "효과음"}, ensure_ascii=False)
        self.app.JOB["name"] = "스타일 배우기"
        try:
            code, j = self.call("/api/style/plan_paste", {"name": "기획", "text": ans})
        finally:
            self.app.JOB["name"] = None
        self.assertEqual(code, 409)
        self.assertIn("스타일 배우기", j["error"])
        d = json.loads((style.STYLES / "기획.json").read_text(encoding="utf-8"))
        self.assertIsNone(d["plan"]["ai"])
        self.assertEqual(self.call("/api/style/plan_paste", {"name": "기획", "text": ans})[0], 200)

    test_status_route = test_prompt_route = test_paste_route = test_ai_job_success_and_log_clean = None
    test_ai_job_login_error_keeps_local = test_token_route = None
    test_success_env_and_cwd = test_no_images_no_tools = test_old_cli_drops_unknown_flags = None
    test_errors_are_korean = test_timeout_kills = test_cancel = test_status_and_missing = None
    test_token_format_and_clear = test_explain = None


if __name__ == "__main__":
    unittest.main()
