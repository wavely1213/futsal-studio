"""올리기 키트(upload.py · hooks.py) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_upload"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402
import hooks  # noqa: E402
import upload  # noqa: E402

FF = core.ffmpeg()

# 12분 · 주제 3개 (퍼스트 터치 → 패스 → 슈팅, 4분씩): 주제가 바뀔 때 4초 쉼, 주제 가운데(2분쯤)에 2.5초 쉼, 문장 사이는 0.6초
TOPICS = [
    ("자, 오늘은 퍼스트 터치부터 해 볼게요.", ["퍼스트 터치는 공을 받기 전에 시작돼요.", "발바닥으로 공을 멈추는 퍼스트 터치를 해 볼게요.",
                                     "퍼스트 터치가 길면 바로 뺏겨요.", "몸을 열고 퍼스트 터치를 하세요 이게 핵심이에요."]),
    ("자 이제 패스 연습을 해 볼게요.", ["패스는 인사이드로 정확하게 차야 해요.", "패스를 받을 사람의 앞발로 주세요.",
                               "패스가 느리면 수비가 다 따라와요.", "패스 방향을 몸으로 숨기는 게 중요해요."]),
    ("마지막으로 골키퍼를 이기는 슈팅을 알려 드릴게요.", ["슈팅은 발등으로 낮게 차세요.", "슈팅 전에 골키퍼 위치를 꼭 보세요.",
                                "슈팅이 뜨는 이유는 몸이 뒤로 눕기 때문이에요.", "슈팅 타이밍을 한 박자 빠르게 가져가세요."]),
]


def fixture_segments():
    segs = []

    def add(t, d, text):
        segs.append({"start": round(t, 2), "end": round(t + d, 2), "text": text})

    for k, (intro, lines) in enumerate(TOPICS):
        base = 240.0 * k
        t = base + (1.0 if k == 0 else 0.0)
        if k == 0:
            add(t, 3.5, "안녕하세요 풋살사관학교 최경진입니다.")
            t += 4.1
        add(t, 4.0, intro)
        t += 4.6
        i, mid = 0, False
        while True:
            if not mid and t + 5.0 > base + 117.5:
                t, mid = base + 120.0, True
            if t + 5.0 > base + 236.0:
                break
            add(t, 5.0, lines[i % len(lines)])
            i += 1
            t += 5.6
    return segs


def srt_of(segs):
    return "".join(f"{i}\n{core._ts(s['start'])} --> {core._ts(s['end'])}\n{s['text']}\n\n" for i, s in enumerate(segs, 1))


def make_video(path, dur, size="160x90"):
    r = core.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=green:s={size}:r=2:d={dur}",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)])
    if r.returncode:
        raise RuntimeError(r.stderr)
    return path


def make_image(path, size):
    r = core.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={size}", "-frames:v", "1", str(path)])
    if r.returncode:
        raise RuntimeError(r.stderr)
    return path


def check_chapters(tc, chs, dur):
    tc.assertGreaterEqual(len(chs), 3)
    tc.assertEqual(chs[0]["t"], 0)
    tc.assertEqual(chs[0]["time"], "00:00")
    for a, b in zip(chs, chs[1:]):
        tc.assertGreater(b["t"], a["t"])
        tc.assertGreaterEqual(b["t"] - a["t"], 10)
    tc.assertGreaterEqual(dur - chs[-1]["t"], 10)
    for c in chs:
        tc.assertTrue(c["title"].strip())
        tc.assertNotRegex(c["title"], r"[<>]")


class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.segs = fixture_segments()

    def test_auto_chapters_12min_three_topics(self):
        notes = []
        chs = upload.chapters(self.segs, dur=720.0, notes=notes)
        check_chapters(self, chs, 720)
        ts = [c["t"] for c in chs]
        for topic_start in (240, 480):  # 주제가 바뀌는 곳(가장 오래 쉰 곳)에서 나뉨
            self.assertIn(topic_start, ts)
        for a, b in zip(ts + [720], ts[1:] + [720]):
            if b > a:
                self.assertLessEqual(b - a, 150 + 30)
        self.assertEqual(chs[0]["title"], "인트로")
        self.assertTrue(all(len(c["title"]) <= 20 for c in chs))
        by_t = {c["t"]: c["title"] for c in chs}
        self.assertEqual(by_t[240], "패스 연습을 해 볼게요")  # '자 이제' 같은 군말은 빠짐
        self.assertEqual(by_t[480], "마지막으로 골키퍼를 이기는 슈팅을")  # 20자 안에서 낱말 단위로 자름
        self.assertFalse(any(c["named"] for c in chs))
        self.assertEqual(notes, [])

    def test_named_markers_win(self):
        markers = [{"t": 4.2, "name": "오늘 배울 것"}, {"t": 250.7, "name": "패스 연습"}, {"t": 300.0, "name": ""},
                   {"t": 490.3, "name": "슈팅 <마무리>"}]
        chs = upload.chapters(self.segs, markers, None, 720.0)
        check_chapters(self, chs, 720)
        self.assertEqual([(c["t"], c["title"]) for c in chs], [(0, "오늘 배울 것"), (250, "패스 연습"), (490, "슈팅 마무리")])
        self.assertTrue(all(c["named"] for c in chs))

    def test_named_marker_after_intro_keeps_zero(self):
        chs = upload.chapters(self.segs, [{"t": 60, "name": "준비 운동"}, {"t": 240, "name": "패스"}, {"t": 480, "name": "슈팅"}], None, 720)
        check_chapters(self, chs, 720)
        self.assertEqual([c["t"] for c in chs], [0, 60, 240, 480])
        self.assertEqual(chs[0]["title"], "인트로")

    def test_too_few_markers_are_filled_with_pauses(self):
        notes = []
        chs = upload.chapters(self.segs, [{"t": 240.2, "name": "패스 연습"}], None, 720, notes)
        check_chapters(self, chs, 720)
        self.assertIn((240, "패스 연습"), [(c["t"], c["title"]) for c in chs])
        self.assertTrue(any("마커가 적어서" in n for n in notes))

    def test_close_and_late_markers_dropped(self):
        ms = [{"t": 100, "name": "A"}, {"t": 105, "name": "B"}, {"t": 300, "name": "C"}, {"t": 715, "name": "끝"}]
        chs = upload.chapters(self.segs, ms, None, 720)
        check_chapters(self, chs, 720)
        names = [c["title"] for c in chs]
        self.assertIn("A", names)
        self.assertNotIn("B", names)   # 10초 안에 붙은 마커
        self.assertNotIn("끝", names)  # 마지막 챕터가 10초가 안 됨

    def test_numbered_graphic_titles_used_when_no_markers(self):
        titles = [{"text": "1. 퍼스트 터치", "start": 8.0, "dur": 3}, {"text": "대박!", "start": 100, "dur": 2},
                  {"text": "2. 패스", "start": 241, "dur": 3}, {"text": "3. 슈팅", "start": 481, "dur": 3}]
        chs = upload.chapters(self.segs, [], titles, 720)
        self.assertEqual([(c["t"], c["title"]) for c in chs], [(0, "1. 퍼스트 터치"), (241, "2. 패스"), (481, "3. 슈팅")])

    def test_under_three_minutes_no_chapters(self):
        short = [s for s in self.segs if s["end"] < 170]
        notes = []
        self.assertEqual(upload.chapters(short, dur=170, notes=notes), [])
        self.assertTrue(notes)
        self.assertEqual(upload.chapters(short, [{"t": 50, "name": "x"}, {"t": 100, "name": "y"}], None, 179.9), [])

    def test_srt_input_and_long_silence(self):
        segs = [s for s in self.segs if not 300 <= s["start"] < 600]  # 5분 동안 말이 없음 (경기 장면)
        chs = upload.chapters(srt_of(segs), dur=720)
        check_chapters(self, chs, 720)
        self.assertIn(600, [c["t"] for c in chs])  # 긴 침묵 뒤 처음 말하는 곳

    def test_hour_long_stamps(self):
        self.assertEqual(upload.stamp(0), "00:00")
        self.assertEqual(upload.stamp(3725.9), "1:02:05")


class SrtTests(unittest.TestCase):
    def test_parse_srt_bom_crlf_tags(self):
        text = "﻿1\r\n00:00:01,500 --> 00:00:03,000\r\n<i>안녕</i> 하세요\r\n\r\n2\r\n00:01:02.25 --> 00:01:04,000\r\n두 줄\r\n자막\r\n\r\n3\r\n깨진 칸\r\n"
        segs = upload.parse_srt(text)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0], {"start": 1.5, "end": 3.0, "text": "안녕 하세요"})
        self.assertAlmostEqual(segs[1]["start"], 62.25)
        self.assertEqual(segs[1]["text"], "두 줄 자막")

    def test_first_sentence(self):
        self.assertEqual(upload.first_sentence("자, 이제 패스. 다음 문장"), "패스")
        self.assertEqual(upload.first_sentence("어 음 슈팅이 왜 뜰까요? 그건"), "슈팅이 왜 뜰까요?")
        self.assertLessEqual(len(upload.first_sentence("가나다라마바사아자차카타파하가나다라마바사아자차")), 20)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="훅 테스트 "))
        self.p = mock.patch.object(core, "WORK", self.tmp)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_josa(self):
        self.assertEqual(hooks.fill("{주제:을} 잘하는 법 · {주제:이} 안 돼요 · {주제:으로}", ["패스"]), "패스를 잘하는 법 · 패스가 안 돼요 · 패스로")
        self.assertEqual(hooks.fill("{주제:을} · {주제:으로} · {주제:와}", ["슈팅"]), "슈팅을 · 슈팅으로 · 슈팅과")
        self.assertEqual(hooks.fill("{주제:으로}", ["드리블"]), "드리블로")
        self.assertEqual(hooks.fill("{주제:이}", ["1대1"]), "1대1이")

    def test_topic_keywords(self):
        texts = [s["text"] for s in fixture_segments()]
        self.assertEqual(set(hooks.topic_keywords(texts)[:3]), {"퍼스트 터치", "패스", "슈팅"})
        self.assertEqual(hooks.topic_keywords(["패턴을 바꿔요 패턴", "리턴"]), [])  # '턴'으로 잘못 세지 않음

    def test_cache_only_own_channel(self):
        rows = [{"id": "a", "title": "패스를 잘하는 3가지 방법", "views": 9000}]
        self.assertIs(hooks.remember_listing(rows, "videos", "https://www.youtube.com/@다른채널"), rows)
        self.assertFalse(hooks.cache_path().exists())
        hooks.remember_listing(rows, "videos", "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertFalse(hooks.cache_path().exists())
        hooks.remember_listing(rows, "videos", "")
        self.assertEqual(hooks.load_cache()["videos"][0]["title"], "패스를 잘하는 3가지 방법")
        hooks.remember_listing([{"title": "쇼츠", "views": 1}], "shorts", core.CONFIG["channel_url"] + "/shorts")
        c = hooks.load_cache()
        self.assertIn("videos", c)
        self.assertIn("shorts", c)

    def test_title_candidates_from_channel_patterns(self):
        cache = {"videos": [{"title": "패스를 잘하는 3가지 방법 | 풋살사관학교", "views": 90000},
                            {"title": "슈팅이 안 되는 이유 | 풋살사관학교", "views": 50000},
                            {"title": "드리블 꿀팁 #shorts | 풋살사관학교", "views": 40000},
                            {"title": "기본기 총정리 | 풋살사관학교", "views": 30000},
                            {"title": "국가대표의 턴과 패스 | 풋살사관학교", "views": 20000}]}
        out = hooks.title_candidates(["퍼스트 터치", "패스", "슈팅"], "퍼스트 터치 꿀팁", "long", cache)
        self.assertTrue(3 <= len(out) <= 5)
        self.assertIn("퍼스트 터치를 잘하는 3가지 방법 | 풋살사관학교", out)  # 인기 제목 틀 + 조사
        self.assertTrue(all(len(t) <= 100 and "<" not in t for t in out))
        self.assertEqual(len(set(out)), len(out))

    def test_title_candidates_builtin(self):
        for fmt in ("long", "shorts"):
            for topics in ([], ["패스"], ["퍼스트 터치", "패스", "슈팅"]):
                out = hooks.title_candidates(topics, None, fmt, {})
                self.assertTrue(3 <= len(out) <= 5, (fmt, topics, out))
                self.assertTrue(all(0 < len(t) <= 100 for t in out))


# 풋살사관학교 실제 목록(2026-10-07 · 롱폼 48 · 쇼츠 24): 회차 번호·행사·영어 풀이·조회수 자랑이 든 제목이 많음
OWN_CACHE = Path(__file__).resolve().parent / "fixtures" / "channel_cache_own.json"


class TitlePatternTests(unittest.TestCase):
    """제목 틀 배우기 (D-030): 옛 회차 번호·행사 이름·그 영상만의 기술 이름을 새 제목에 베끼지 않음."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="제목 틀 테스트 "))
        self.p = mock.patch.object(core, "WORK", self.tmp)
        self.p.start()
        self.cache = json.loads(OWN_CACHE.read_text(encoding="utf-8"))

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sk(self, title):
        return hooks.skeletons([{"title": title, "views": 1}], 1)

    def test_real_channel_skeletons_have_no_series_or_event(self):
        for kind in ("videos", "shorts"):
            for sk in hooks.skeletons(hooks._top(self.cache, kind), 10):
                self.assertNotRegex(sk, r"차시|\d\s*부|\d\s*탄|#\d|ep\.|\(\s*[A-Za-z]|20\d\d|CUP|대회|만뷰|feat", sk)
                self.assertNotRegex(sk, r"스네이크 \{|각\{|시저스 \{", sk)  # 기술 이름의 반쪽만 바꾸지 않음

    def test_real_channel_long_titles(self):
        out = hooks.title_candidates(["퍼스트 터치", "패스", "슈팅"], None, "long", self.cache)
        self.assertTrue(3 <= len(out) <= 5, out)
        self.assertNotIn("풋살 퍼스트 터치로 중앙을 파괴?", out)  # '중앙을 파괴'는 드리블 영상의 말 → 퍼스트 터치(공 다루기)에는 안 씀
        self.assertNotRegex(out[0], r"차시|\(\d부\)|스네이크")  # 훅 없는 롱폼은 첫 후보가 기본 제목
        for t in out:
            self.assertNotRegex(t, r"\[\d차시\]|\(\d부\)|스네이크 퍼스트|FK CUP|농락|사이드에서", t)
        out = hooks.title_candidates(["시저스"], None, "long", self.cache)  # 같은 갈래(드리블 기술)면 우리 채널 틀을 씀
        self.assertIn("풋살 시저스로 중앙을 파괴?", out)
        self.assertNotEqual(out[0], "풋살 시저스로 중앙을 파괴?", "첫 후보(기본 제목)는 기본 틀")

    def test_default_title_never_from_other_topic_episode(self):
        """패스·슈팅·수비 영상에 '농락'·'파괴'·'골 넣어요' 같은 옛 드리블·전술 영상의 말이 들어간 제목을 주지 않음 (D-033)."""
        cache = {"videos": self.cache["videos"] + [{"title": "피보 플레이 이렇게 하면 골 넣어요", "views": 30000}]}
        for topic in ("패스", "슈팅", "수비", "볼 키핑", "골키퍼"):
            out = hooks.title_candidates([topic], None, "long", cache, dur=600)
            self.assertTrue(3 <= len(out) <= 5, out)
            for t in out:
                self.assertNotRegex(t, r"농락|파괴|골 넣어요|사이드에서|-\s*" + topic, (topic, t))
            self.assertIn(out[0], [hooks.fill(f[2], [topic]) for f in hooks.FAMILIES if f[2]], "첫 후보는 기본 틀")
        out = hooks.title_candidates(["로테이션"], None, "long", cache, dur=600)
        self.assertIn("로테이션 이렇게 하면 골 넣어요", out, "같은 갈래(전술)면 씀")

    def test_trailing_label_templates_dropped(self):
        for t in ("[1차시] 사이드에서 상대방 농락해 버리기?! (1부)-스네이크 드리블", "사이드 농락하기 -각드리블 (2부)",
                  "풋살 초보자의 필수 아이템 / 발바닥 방향전환 (Sole Turn)", "이렇게 하면 무조건 이겨요! 패스", "꼭 보세요 | 슈팅"):
            self.assertEqual(self.sk(t), [], t)
        self.assertEqual(self.sk("패스 | 이것만 알면 돼요"), ["{주제} | 이것만 알면 돼요"], "주제가 앞이면 꼬리표가 아님")

    def test_generic_and_category_of_templates(self):
        info = {x["sk"]: x for x in hooks.skeleton_info(hooks._top(self.cache, "shorts"), 10)}
        self.assertTrue(info['🔥풋살기술🔥 "{주제}" 속성강의']["generic"])
        self.assertTrue(info["풋살 국가대표 {주제} 강좌"]["generic"])
        self.assertFalse(info["테크노 댄스를 접목한 풋살 {주제}"]["generic"])
        self.assertEqual(info["테크노 댄스를 접목한 풋살 {주제}"]["cat"], "fitness")
        self.assertEqual([hooks.category(t) for t in ("팬텀 드리블", "스루패스", "토킥", "퍼스트 터치", "맨투맨", "리턴 패스", "라보나")],
                         ["dribble", "pass", "shoot", "control", "defense", "pass", None])

    def test_one_minute_in_learned_template(self):
        cache = {"shorts": [{"title": "팬텀 드리블 1분 꿀팁", "views": 9000}, {"title": "1분 만에 배우는 시저스", "views": 8000}]}
        long_ = hooks.title_candidates(["드리블"], None, "shorts", cache, dur=150)
        self.assertNotIn("1분", " ".join(long_), long_)
        self.assertIn("드리블 꿀팁 하나", long_)
        short = hooks.title_candidates(["드리블"], None, "shorts", cache, dur=45)
        self.assertIn("드리블 1분 꿀팁", short)

    def test_doubled_topic_dropped(self):
        out = hooks.title_candidates([], None, "shorts", self.cache, dur=40)  # 주제어를 못 찾으면 '풋살'
        self.assertTrue(3 <= len(out) <= 5, out)
        for t in out:
            self.assertNotIn("풋살 풋살", t)
            self.assertLessEqual(t.count("풋살"), 1, t)
        self.assertNotIn("테크노 댄스를 접목한 풋살 풋살", out)

    def test_shorts_titles_keep_pattern_with_technique(self):
        out = hooks.title_candidates(["팬텀 드리블"], None, "shorts", self.cache, dur=40)
        self.assertIn('🔥풋살기술🔥 "팬텀 드리블" 속성강의', out)  # '🔥600만뷰 풋살기술🔥 "영재 플랩" 속성강의' — 조회수 자랑은 뺌
        self.assertIn("풋살 국가대표 팬텀 드리블 강좌", out)  # 'in-in 플립플랩' 통째로 주제 자리

    def test_series_markers_removed(self):
        self.assertEqual(self.sk("[3차시] 패스 이렇게 하세요 (2부)"), ["{주제} 이렇게 하세요"])
        self.assertEqual(self.sk('"1탄" 패스의 기본'), ["{주제}의 기본"])
        self.assertEqual(self.sk("드리블 꿀팁 2탄"), ["{주제} 꿀팁"])
        self.assertEqual(self.sk("슈팅 잘하는 법 #3"), ["{주제} 잘하는 법"])
        self.assertEqual(self.sk("[풋린이들 과외하기#2] 슈팅 잘하는 법 ep.2"), ["{주제} 잘하는 법"])
        self.assertEqual(self.sk("[풋살사관학교] 패스 잘하는 법 (Part 1)"), ["[풋살사관학교] {주제} 잘하는 법"])

    def test_event_and_guest_titles_skipped(self):
        for t in ("[풋살사관학교] 아쉽게 마무리된 2019 FK CUP 대회. 감사드립니다.", "드리블 대회 우승!", "강원FC 슈팅 훈련",
                  "현역 국가대표가 말하는 슈팅의 중요성!! (feat. 김영권)", "패스 이벤트 당첨자 발표", "프로 vs 아마 드리블"):
            self.assertEqual(self.sk(t), [], t)

    def test_english_gloss_and_other_brackets_removed(self):
        self.assertEqual(self.sk("바디 페인팅이란? (Body Feint)"), ["{주제:이란}?"])
        self.assertEqual(hooks.fill("{주제:이란}?", ["슈팅"]), "슈팅이란?")
        self.assertEqual(hooks.fill("{주제:이란}?", ["패스"]), "패스란?")
        self.assertEqual(self.sk("드리블 꿀팁 (1인칭 시점)"), ["{주제} 꿀팁"])

    def test_technique_name_is_one_slot(self):
        self.assertEqual(self.sk("스네이크 드리블 꿀팁"), ["{주제} 꿀팁"])
        self.assertEqual(self.sk("각드리블 이렇게 하세요 (2부)"), ["{주제} 이렇게 하세요"])
        self.assertEqual(self.sk("풋살 시저스 드리블로 중앙을 파괴?"), ["풋살 {주제:으로} 중앙을 파괴?"])
        self.assertEqual(self.sk("프로처럼 드리블 하는 법"), ["프로처럼 {주제} 하는 법"])  # 꾸밈말은 그대로
        self.assertEqual(self.sk("드리블러가 되는 법"), [])  # 주제어가 다른 낱말의 일부
        self.assertEqual(self.sk("발바닥 연습 꿀팁"), ["{주제} 연습 꿀팁"])
        self.assertEqual(self.sk("발바닥 위치 바꾸기"), [])  # 주제어가 뒤 이름말을 꾸밈 ('발바닥 위치')


class TopicTermTests(unittest.TestCase):
    """주제어 (D-030): 기술 이름 · 용어 사전 · 붙여 쓰기 · 긴 이름 먼저."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="주제어 테스트 "))
        self.p = mock.patch.object(core, "WORK", self.tmp)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skill_names_found(self):
        for text, want in (("피벗 플레이를 해 봐요", "피벗 플레이"), ("패스하고 바로 움직이세요", "패스"), ("바디 페인팅이란 뭘까요", "바디 페인팅"),
                           ("체크백", "체크백"), ("드래그백", "드래그백"), ("스쿱턴", "스쿱턴"), ("엘라스티코", "엘라스티코"),
                           ("팬텀 드리블", "팬텀 드리블"), ("팬텀드리블 꿀팁", "팬텀 드리블"), ("패스해 주세요 패스받고 돌아요", "패스"),
                           ("피보라고 불러요", "피보")):
            self.assertEqual(hooks.topic_keywords([text]), [want], text)

    def test_longer_name_wins_over_part(self):
        self.assertEqual(hooks.topic_keywords(["팬텀 드리블 알려 드릴게요.", "드리블할 때 공을 안 건드려요.", "팬텀 드리블 꼭 해 보세요."]), ["팬텀 드리블"])
        many = ["드리블은 몸을 낮춰요."] * 9 + ["팬텀 드리블도 있어요.", "드리블 연습!"]
        self.assertEqual(hooks.topic_keywords(many), ["드리블"])  # 한 번 스친 기술 이름이 영상 전체 주제가 되지 않음

    def test_attached_compound_is_other_technique_not_merged(self):
        """'토킥' ⊃ '킥' · '백패스' ⊃ '패스' 는 다른 기술 — 적게 나온 쪽이 흔한 말을 먹지 않음 (D-033)."""
        self.assertEqual(hooks.topic_keywords(["킥할 때 발목을 고정해요"] * 6 + ["토킥으로 차면 안 돼요"] * 2), ["킥", "토킥"])
        self.assertEqual(hooks.topic_keywords(["패스는 정확하게 해요"] * 10 + ["백패스도 있어요"] * 4), ["패스", "백패스"])
        self.assertEqual(hooks.topic_keywords(["턴은 몸을 낮춰요"] * 6 + ["스쿱턴도 있어요"] * 2)[0], "턴")

    def test_announced_skill_kept(self):
        """'오늘은 팬텀 드리블 알려 드릴게요' 뒤로는 '드리블'만 — 소개한 기술 이름이 주제가 됨 (많으면 둘째로라도)."""
        self.assertEqual(hooks.topic_keywords(["오늘은 팬텀 드리블 알려 드릴게요"] + ["드리블할 때 공을 보지 마세요"] * 4), ["팬텀 드리블"])
        self.assertEqual(hooks.topic_keywords(["오늘은 팬텀 드리블 알려 드릴게요"] + ["드리블할 때 공을 보지 마세요"] * 8), ["드리블", "팬텀 드리블"])
        self.assertEqual(hooks.topic_keywords(["엘라스티코란 뭘까요?", "패스하고 슈팅", "패스 연습", "슈팅 연습"])[0], "엘라스티코")

    def test_spaced_skill_names(self):
        """받아쓰기가 띄어 쓴 기술 이름도 같은 이름 ('드래그 백' = '드래그백')."""
        for text, want in (("드래그 백으로 수비를 벗겨요. 드래그 백 하고 바로 슈팅", "드래그백"), ("체크 백 해서 받아요. 체크 백!", "체크백"),
                           ("스쿱 턴은 쉬워요. 스쿱 턴 해 봐요", "스쿱턴"), ("플립 플랩은 이렇게. 플립 플랩 연습", "플립플랩"),
                           ("방향 전환을 빨리. 방향 전환!", "방향전환"), ("스루 패스 넣어요. 스루 패스는", "스루패스"),
                           ("코너 킥 차는 법. 코너 킥!", "코너킥")):
            self.assertEqual(hooks.topic_keywords(text.split(". ")), [want], text)

    def test_determiner_not_skill(self):
        """'각 드리블 사이에'(각각의)·'생각 드리블'은 '각 드리블' 기술이 아님 · 붙여 쓴 '각드리블'은 기술."""
        self.assertEqual(hooks.topic_keywords(["드리블 연습을 할 거예요", "각 드리블 사이에 터치를 줄여요", "제 생각 드리블은 자신감이에요"]), ["드리블"])
        self.assertEqual(hooks.topic_keywords(["각드리블로 벗겨요", "각드리블 꼭 해요"]), ["각 드리블"])
        self.assertEqual(hooks.topic_keywords(["재생각 드리블", "드리블"])[0], "드리블")

    def test_dictionary_terms_and_names(self):
        captions = __import__("captions")
        captions.save_dict(core.dict_path(), {"terms": ["라보나", "박영재 선수", "풋살사관학교", "최경진 감독"], "fix": {"라보너": "라보나"}})
        terms = hooks.topic_terms()
        self.assertIn("라보나", terms)
        self.assertNotIn("박영재 선수", terms)
        self.assertNotIn("풋살사관학교", terms)
        self.assertNotIn("최경진 감독", terms)
        self.assertEqual(hooks.topic_keywords(["라보나 차는 법", "라보나는 다리를 꼬아요"]), ["라보나"])
        lens = [len(t.replace(" ", "")) for t in terms]
        self.assertEqual(lens, sorted(lens, reverse=True))  # 긴 이름부터

    def test_dictionary_person_and_team_names_not_topics(self):
        """용어 사전의 사람·팀 이름은 주제어가 아님 — 대사에서 '선수'로 부른 말 · 팀(FS·FC) · 흔한 성 세 글자 (BR-021)."""
        captions = __import__("captions")
        captions.save_dict(core.dict_path(), {"terms": ["박영재", "강원FS", "한지성", "라보나"], "fix": {}})
        terms = hooks.topic_terms()
        self.assertNotIn("박영재", terms)
        self.assertNotIn("강원FS", terms)
        self.assertIn("라보나", terms)
        texts = ["한지성 선수가 드리블해요", "한지성 선수 최고", "한지성 선수처럼 해 봐요", "드리블 연습"]
        self.assertEqual(hooks.topic_keywords(texts), ["드리블"])
        kit_titles = hooks.title_candidates(hooks.topic_keywords(texts), None, "shorts", {}, dur=40)
        self.assertFalse(any("한지성" in t for t in kit_titles), kit_titles)
        self.assertEqual(hooks.topic_keywords(["라보나 차요", "라보나!"]), ["라보나"])

    def test_default_dictionary_without_file(self):
        self.assertFalse(core.dict_path().exists())
        self.assertIn("파라렐라", hooks.topic_terms())  # 기본 사전 (captions.DEFAULT_TERMS)
        self.assertEqual(hooks.topic_keywords(["파라렐라로 돌아 들어가요"]), ["파라렐라"])

    def test_no_false_hits(self):
        self.assertEqual(hooks.topic_keywords(["패턴을 바꿔요 패턴", "리턴"]), [])
        self.assertEqual(hooks.topic_keywords(["킥보드 타고 왔어요"]), [])


class KitBase(unittest.TestCase):
    NAME = "풋살 레슨 [꿀팁] 1편.mp4"

    @classmethod
    def setUpClass(cls):
        cls.media = Path(tempfile.mkdtemp(prefix="키트 미디어 "))
        make_video(cls.media / "long.mp4", 720)
        make_video(cls.media / "short.mp4", 40, "90x160")
        make_video(cls.media / "export.mp4", 700)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.media, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="올리기 테스트 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        (work / "projects").mkdir()
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(editor, "PROJECTS", work / "projects")]
        for p in self.patches:
            p.start()
        self.work, self.out = work, dirs["OUT"]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_video(self, name, kind="long", segs=None, srt=True):
        shutil.copy(self.media / f"{kind}.mp4", core.VIDEOS / name)
        d = core.adir(name)
        d.mkdir(parents=True, exist_ok=True)
        segs = fixture_segments() if segs is None else segs
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        if srt:
            (d / "subtitles.srt").write_text(srt_of(segs), encoding="utf-8")
        return name

    def check_rules(self, kit):
        self.assertTrue(3 <= len(kit["titles"]) <= 5)
        self.assertTrue(all(len(t) <= 100 for t in kit["titles"]))
        self.assertLessEqual(len(kit["title"]), 100)
        self.assertLessEqual(len(kit["description"]), 5000)
        self.assertLessEqual(upload.tags_len(kit["tags"]), 500)
        self.assertLessEqual(len(upload._HASHTAG.findall(kit["description"])), 15)
        self.assertNotRegex(kit["description"] + kit["title"], r"[<>]")


class KitTests(KitBase):
    def test_long_kit(self):
        name = self.add_video(self.NAME)
        kit = upload.build_kit(name)
        self.check_rules(kit)
        self.assertEqual(kit["format"], "long")
        check_chapters(self, kit["chapters"], 720)
        self.assertIn("\n00:00 인트로\n", kit["description"])
        self.assertIn("최경진 감독", kit["description"])
        self.assertEqual(len(kit["hashtags"]), 3)
        self.assertNotIn("#shorts", kit["description"])
        self.assertEqual(set(kit["topics"][:3]), {"퍼스트 터치", "패스", "슈팅"})
        self.assertEqual(kit["notes"], [])
        # 파일: 완성본 폴더 <이름>_올리기.txt / .json · 작업 폴더에 고칠 수 있는 설명 틀
        txt, js = self.out / "풋살 레슨 [꿀팁] 1편_올리기.txt", self.out / "풋살 레슨 [꿀팁] 1편_올리기.json"
        self.assertTrue(txt.exists() and js.exists())
        self.assertIn(kit["title"], txt.read_text(encoding="utf-8-sig"))
        self.assertEqual(json.loads(js.read_text(encoding="utf-8"))["description"], kit["description"])
        self.assertTrue((self.work / "upload_template.txt").exists())
        self.assertIn("최경진 감독", (self.work / "upload_template.txt").read_text(encoding="utf-8-sig"))
        # Claude 에게 물어볼 내용
        self.assertIn("받아쓰기", kit["prompt"])
        self.assertIn("퍼스트 터치", kit["prompt"])
        self.assertIn("00:00 인트로", kit["prompt"])

    def test_shorts_kit(self):
        segs = [{"start": 1, "end": 5, "text": "드리블할 때 발바닥을 쓰세요."}, {"start": 6, "end": 12, "text": "드리블은 몸을 낮추는 게 핵심이에요."},
                {"start": 13, "end": 20, "text": "이 드리블 꿀팁 꼭 해 보세요!"}]
        name = self.add_video("드리블 꿀팁 쇼츠.mp4", "short", segs)
        kit = upload.build_kit(name)
        self.check_rules(kit)
        self.assertEqual(kit["format"], "shorts")
        self.assertEqual(kit["chapters"], [])
        self.assertIn("#shorts", kit["description"])
        self.assertEqual(kit["hashtags"][0], "#shorts")
        self.assertIn("shorts", kit["tags"])
        self.assertNotIn("00:00", kit["description"])
        self.assertNotIn("목차", kit["description"])

    def test_short_long_video_has_no_chapters(self):
        segs = [s for s in fixture_segments() if s["end"] < 170]
        shutil.copy(self.media / "long.mp4", self.media / "x.mp4")
        name = self.add_video(self.NAME, "long", segs)
        with mock.patch.object(editor, "media_info", return_value={"duration": 170.0, "width": 1920, "height": 1080, "fps": 30, "hdr": None}):
            kit = upload.build_kit(name)
        self.assertEqual(kit["format"], "long")
        self.assertEqual(kit["chapters"], [])
        self.assertNotIn("00:00", kit["description"])
        self.assertTrue(any("3분" in n for n in kit["notes"]))

    def test_missing_srt_falls_back_to_transcript(self):
        name = self.add_video(self.NAME, srt=False)
        kit = upload.build_kit(name)
        check_chapters(self, kit["chapters"], 720)
        self.assertTrue(any("transcript.json" in n for n in kit["notes"]))

    def test_srt_preferred_over_transcript(self):
        name = self.add_video(self.NAME)
        d = core.adir(name)
        segs = [dict(s, text=s["text"].replace("퍼스트 터치", "드리블")) for s in fixture_segments()]
        (d / "subtitles.srt").write_text(srt_of(segs), encoding="utf-8")  # 사용자가 고친 자막
        kit = upload.build_kit(name)
        self.assertIn("드리블", kit["topics"])
        self.assertNotIn("퍼스트 터치", kit["topics"])
        self.assertEqual(kit["notes"], [])

    def test_no_transcript_still_makes_kit(self):
        name = self.NAME
        shutil.copy(self.media / "long.mp4", core.VIDEOS / name)
        kit = upload.build_kit(name)
        self.check_rules(kit)
        self.assertEqual(kit["chapters"], [])
        self.assertTrue(any("받아쓰기" in n for n in kit["notes"]))

    def test_template_customization(self):
        name = self.add_video(self.NAME)
        tpl = "## 안내\n{제목}\n{훅}\n" + " ".join(f"#태그{i}" for i in range(20)) + "\n{해시태그}\n<b>굵게</b>\n"
        (self.work / "upload_template.txt").write_bytes(tpl.encode("cp949"))  # 옛 메모장(ANSI)으로 저장한 틀
        kit = upload.build_kit(name)
        self.check_rules(kit)
        self.assertNotIn("안내", kit["description"])
        self.assertTrue(kit["description"].startswith(kit["title"]))
        self.assertIn("00:00", kit["description"])  # {챕터} 자리가 없으면 맨 아래에 붙임
        self.assertTrue(any("{챕터}" in n for n in kit["notes"]))
        self.assertTrue(any("15개" in n for n in kit["notes"]))

    def test_tags_limit(self):
        many = [f"아주 긴 주제어 이름 {i}번" for i in range(60)]
        tags = upload.make_tags(many, "shorts")
        self.assertLessEqual(upload.tags_len(tags), 500)
        self.assertEqual(upload.tags_len(["a b", "c"]), 7)  # "a b",c

    def test_save_edits(self):
        name = self.add_video(self.NAME)
        upload.build_kit(name)
        kit = upload.save_edits(name, None, title="고친 제목", desc="고친 설명\n#풋살", tags="패스, 슈팅,\n풋살 레슨")
        self.assertEqual(kit["tags"], ["패스", "슈팅", "풋살 레슨"])
        again = upload.load_kit(name)
        self.assertEqual(again["title"], "고친 제목")
        self.assertEqual(again["description"], "고친 설명\n#풋살")
        self.assertIn("고친 제목", (self.out / "풋살 레슨 [꿀팁] 1편_올리기.txt").read_text(encoding="utf-8-sig"))
        with self.assertRaises(LookupError):
            upload.save_edits(self.add_video("다른 영상.mp4"), None, title="x")


class TopicKitTests(KitBase):
    """주제어가 키트 전체(제목·해시태그·태그·설명 둘째 줄)에 들어감 · 우리 채널 실제 목록으로 만든 첫 제목 (D-030)."""

    def test_shorts_kit_uses_technique_name(self):
        segs = [{"start": 1, "end": 5, "text": "오늘은 팬텀 드리블 알려 드릴게요."}, {"start": 6, "end": 12, "text": "드리블할 때 공을 건드리는 척만 하세요."},
                {"start": 13, "end": 20, "text": "팬텀드리블은 몸을 먼저 속이는 게 핵심이에요!"}]
        name = self.add_video("팬텀 쇼츠.mp4", "short", segs)
        kit = upload.build_kit(name)
        self.check_rules(kit)
        self.assertEqual(kit["topics"][0], "팬텀 드리블")
        self.assertTrue(any("팬텀 드리블" in t for t in kit["titles"]), kit["titles"])
        self.assertIn("#팬텀드리블", kit["hashtags"])
        self.assertIn("팬텀 드리블", kit["tags"])
        self.assertIn("팬텀 드리블 꿀팁", kit["description"].split("\n")[1])

    def test_long_kit_first_title_from_real_channel_is_clean(self):
        shutil.copy(OWN_CACHE, self.work / "channel_cache.json")
        kit = upload.build_kit(self.add_video(self.NAME))
        self.check_rules(kit)
        self.assertEqual(kit["title"], kit["titles"][0])
        for t in kit["titles"]:
            self.assertNotRegex(t, r"차시|\(\d부\)|스네이크|FK CUP|대회|농락|사이드에서|파괴", t)

    def test_pass_and_shooting_lessons_get_clean_default_title(self):
        """훅 없는 패스·슈팅 레슨: 기본 제목(첫 후보)이 옛 드리블 회차 제목이 아님 (실제 채널 목록으로)."""
        shutil.copy(OWN_CACHE, self.work / "channel_cache.json")
        for nm, line in (("패스 앤 무브 드릴.mp4", "패스하고 바로 움직여요. 패스는 인사이드로 정확하게!"),
                         ("슈팅 챌린지.mp4", "슈팅은 발등으로 낮게 차요. 슈팅 전에 골키퍼를 보세요.")):
            segs = [{"start": 10.0 * i, "end": 10.0 * i + 8, "text": t} for i, t in enumerate(line.split(". ") * 10)]
            kit = upload.build_kit(self.add_video(nm, segs=segs))
            self.check_rules(kit)
            for t in kit["titles"]:
                self.assertNotRegex(t, r"농락|파괴|사이드에서", (nm, t))

    def test_hook_uses_kit_topic_word(self):
        """편집실 훅('국가대표 꿀팁' · editor.KEYWORDS)이 아니라 키트 주제어로 ('팬텀 드리블 꿀팁')."""
        self.assertEqual(upload._topic_hook("국가대표 꿀팁", ["팬텀 드리블"]), "팬텀 드리블 꿀팁")
        self.assertEqual(upload._topic_hook("국가대표!", ["팬텀 드리블"]), "팬텀 드리블!")
        self.assertIsNone(upload._topic_hook("국가대표 꿀팁", ["연습"]), "주제어가 풋살 용어가 아니면 훅의 말이 용어일 때만")
        self.assertEqual(upload._topic_hook("퍼스트 터치 꿀팁", ["연습"]), "퍼스트 터치 꿀팁")
        self.assertIsNone(upload._topic_hook(None, ["패스"]))
        segs = [{"start": 1, "end": 5, "text": "오늘은 팬텀 드리블 알려 드릴게요."}, {"start": 6, "end": 12, "text": "국가대표 꿀팁이에요 드리블할 때 공을 안 봐요."},
                {"start": 13, "end": 20, "text": "팬텀 드리블은 몸을 먼저 속이는 게 핵심이에요!"}]
        name = self.add_video("국가대표 쇼츠.mp4", "short", segs)
        with mock.patch.object(editor, "recommend", return_value={"shorts": [{"keywords": ["국가대표", "꿀팁"], "title": "x"}]}), \
                mock.patch.object(editor, "_hook", return_value="국가대표 꿀팁"):
            kit = upload.build_kit(name)
        self.assertEqual(kit["title"], "팬텀 드리블 꿀팁")
        self.assertFalse(any("국가대표 꿀팁" == t for t in kit["titles"]), kit["titles"])


class ThumbTests(KitBase):
    def test_newest_thumbnail_checked(self):
        name = self.add_video(self.NAME)
        self.add_video("풋살 레슨 [꿀팁] 1편_2.mp4")  # 이름이 더 긴 다른 영상
        stem = core.adir(name).name
        self.assertEqual(upload.thumbnail_check(name)["file"], None)
        make_image(self.out / f"{stem}_디자인 1_1.jpg", "1280x720")
        res = upload.thumbnail_check(name)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["w"], res["h"]), (1280, 720))
        import os
        import time
        later = time.time() + 5
        make_image(self.out / f"{stem}_2_디자인_1.png", "1920x1080")  # 다른 영상의 것 → 무시
        os.utime(self.out / f"{stem}_2_디자인_1.png", (later, later))
        self.assertEqual(upload.thumbnail_check(name)["file"], f"{stem}_디자인 1_1.jpg")
        big = make_image(self.out / f"{stem}_디자인 2_1.jpg", "1280x720")
        with open(big, "ab") as f:
            f.write(b"\0" * (2 * 1024 * 1024))
        os.utime(big, (later, later))
        res = upload.thumbnail_check(name)
        self.assertEqual(res["file"], big.name)
        self.assertFalse(res["ok"])
        self.assertTrue(any("2MB" in p for p in res["problems"]))
        odd = make_image(self.out / f"{stem}_디자인 3_1.png", "640x360")
        os.utime(odd, (later + 1, later + 1))
        self.assertTrue(any("640×360" in p for p in upload.thumbnail_check(name)["problems"]))
        sh = make_image(self.out / f"{stem}_디자인 4_쇼츠_1.jpg", "1080x1920")
        os.utime(sh, (later + 2, later + 2))
        self.assertEqual(upload.thumbnail_check(name, "long")["file"], odd.name)  # 긴 영상은 가로 썸네일 중 최근 것
        self.assertTrue(upload.thumbnail_check(name, "shorts")["ok"])

    def test_image_size_parser(self):
        p = make_image(self.out / "a.jpg", "322x124")
        self.assertEqual(upload.image_size(p), (322, 124))
        p = make_image(self.out / "a.png", "64x32")
        self.assertEqual(upload.image_size(p), (64, 32))
        (self.out / "x.jpg").write_bytes(b"not an image")
        self.assertIsNone(upload.image_size(self.out / "x.jpg"))


class SequenceKitTests(KitBase):
    def write_project(self, name, markers, fmt="long", seq_name="롱폼 가편집"):
        caps = [{"id": str(i), "start": s["start"], "end": s["end"], "text": s["text"]} for i, s in enumerate(fixture_segments())]
        items = [{"id": "v", "track": "V1", "media": "main", "in": 0.0, "out": 720.0, "start": 0.0, "speed": 1.0, "link": "l"},
                 {"id": "a", "track": "A1", "media": "main", "in": 0.0, "out": 720.0, "start": 0.0, "speed": 1.0, "link": "l"}]
        proj = {"source": name, "info": {"duration": 720.0, "width": 160, "height": 90}, "captions": caps, "v": 2, "rev": 3,
                "sequences": [{"id": "s1", "name": seq_name, "format": fmt, "v": 2, "items": items, "markers": markers, "titles": [],
                               "tracks": editor.default_tracks()}]}
        p = editor._ppath(name)
        p.write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        return p

    def test_sequence_with_export_uses_markers_and_export_srt(self):
        name = self.add_video(self.NAME)
        p = self.write_project(name, [{"t": 0, "name": "시작"}, {"t": 250, "name": "패스 연습"}, {"t": 500, "name": "슈팅"}])
        before = p.read_bytes()
        stem = f"{core.adir(name).name}_롱폼 가편집"
        shutil.copy(self.media / "export.mp4", self.out / f"{stem}.mp4")
        segs = [dict(s, text=s["text"].replace("패스", "월패스")) for s in fixture_segments() if s["end"] < 700]
        (self.out / f"{stem}.srt").write_text(srt_of(segs), encoding="utf-8")
        src = upload.sources(name)
        self.assertEqual(src["default"], "s1")
        self.assertEqual(src["sources"][1]["export"], f"{stem}.mp4")
        kit = upload.build_kit(name, "s1")
        self.check_rules(kit)
        self.assertEqual([(c["t"], c["title"]) for c in kit["chapters"]], [(0, "시작"), (250, "패스 연습"), (500, "슈팅")])
        self.assertAlmostEqual(kit["duration"], 700, delta=1)
        self.assertIn("월패스", kit["topics"])  # 내보낸 자막(.srt) 기준
        self.assertTrue((self.out / f"{stem}_올리기.json").exists())
        self.assertEqual(p.read_bytes(), before)  # 편집실 프로젝트는 읽기만
        self.assertEqual(upload.load_kit(name, "s1")["title"], kit["title"])
        self.assertIsNone(upload.load_kit(name))  # 원본 영상 키트는 따로

    def test_sequence_without_export_maps_captions(self):
        name = self.add_video(self.NAME)
        self.write_project(name, [])
        kit = upload.build_kit(name, "s1")
        check_chapters(self, kit["chapters"], 720)
        self.assertTrue(any("편집본 자막" in n for n in kit["notes"]))
        self.assertTrue(any("내보내지 않았어요" in n for n in kit["notes"]))
        self.assertTrue((self.out / f"{core.adir(name).name}_롱폼 가편집_올리기.txt").exists())

    def test_shorts_sequence_uses_title_hook(self):
        name = self.add_video(self.NAME)
        p = self.write_project(name, [], "shorts", "쇼츠 1 · 퍼스트 터치")
        proj = json.loads(p.read_text(encoding="utf-8"))
        proj["sequences"][0]["items"] = [dict(it, out=45.0) for it in proj["sequences"][0]["items"]]
        proj["sequences"][0]["titles"] = [{"id": "t", "text": "퍼스트 터치\n꿀팁", "start": 0, "dur": 45}]
        p.write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        kit = upload.build_kit(name, "s1")
        self.assertEqual(kit["format"], "shorts")
        self.assertEqual(kit["titles"][0], "퍼스트 터치 꿀팁")
        self.assertEqual(kit["chapters"], [])
        self.assertIn("#shorts", kit["description"])

    def test_unknown_sequence(self):
        name = self.add_video(self.NAME)
        with self.assertRaises(LookupError):
            upload.build_kit(name, "없는편집본")
        self.write_project(name, [])
        with self.assertRaises(LookupError):
            upload.build_kit(name, "없는편집본")


class RouteTests(KitBase):
    """app.py 의 /api/upload/* (실제 HTTP 서버로)."""

    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.patches2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log")]
        for p in self.patches2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.patches2:
            p.stop()
        super().tearDown()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_routes(self):
        from urllib.parse import quote
        name = self.add_video(self.NAME)
        code, j = self.call(f"/api/upload/kit?name={quote(name)}")
        self.assertEqual(code, 200)
        self.assertIsNone(j["kit"])
        self.assertEqual(j["sources"][0]["id"], "")
        code, j = self.call("/api/upload/kit", {"name": name, "seq": ""})
        self.assertEqual(code, 200, j)
        self.assertLessEqual(len(j["kit"]["title"]), 100)
        code, j = self.call(f"/api/upload/kit?name={quote(name)}")
        self.assertEqual(j["kit"]["files"]["txt"], "풋살 레슨 [꿀팁] 1편_올리기.txt")
        code, j = self.call("/api/upload/kit", {"name": name, "seq": "", "edits": {"title": "새 제목"}})
        self.assertEqual(j["kit"]["title"], "새 제목")
        # 잘못된 이름 · 없는 영상
        self.assertEqual(self.call("/api/upload/kit", {"name": "..\\x.mp4"})[0], 400)
        self.assertEqual(self.call(f"/api/upload/kit?name={quote('없음.mp4')}")[0], 404)
        # 열기 (실제로 창을 띄우지 않게)
        with mock.patch.object(upload, "open_studio") as st, mock.patch.object(upload, "reveal") as rv, \
                mock.patch.object(upload, "open_template") as tp:
            self.assertEqual(self.call("/api/upload/open", {"what": "studio"})[0], 200)
            st.assert_called_once()
            self.assertEqual(self.call("/api/upload/open", {"what": "file", "file": "풋살 레슨 [꿀팁] 1편_올리기.txt"})[0], 200)
            self.assertEqual(rv.call_args[0][0], self.out / "풋살 레슨 [꿀팁] 1편_올리기.txt")
            self.assertEqual(self.call("/api/upload/open", {"what": "file", "file": "../../x.txt"})[0], 400)
            self.assertEqual(self.call("/api/upload/open", {"what": "template"})[0], 200)
            tp.assert_called_once()
        # 썸네일 미리보기는 완성본 폴더의 이미지만
        make_image(self.out / f"{core.adir(name).name}_디자인 1_1.jpg", "1280x720")
        with urllib.request.urlopen(self.base + "/api/upload/thumb?file=" + quote(f"{core.adir(name).name}_디자인 1_1.jpg")) as r:
            self.assertEqual(r.headers["Content-Type"], "image/jpeg")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/api/upload/thumb?file=" + quote("풋살 레슨 [꿀팁] 1편_올리기.txt"))

    def test_channel_listing_is_cached_for_own_channel(self):
        import time
        rows = [{"id": "abcdefghijk", "title": "패스를 잘하는 3가지 방법", "views": 9000, "duration": 300, "kind": "videos"}]

        def run(url):
            with mock.patch.object(core, "list_videos", return_value=list(rows)):
                self.assertEqual(self.call("/api/list", {"kind": "videos", "url": url})[0], 200)
                for _ in range(100):
                    if not self.app.JOB["name"]:
                        break
                    time.sleep(0.05)
            return self.call("/api/state?since=0")[1]

        self.assertEqual(run("https://www.youtube.com/@다른유튜버")["result"], rows)  # 목록은 그대로
        self.assertFalse(hooks.cache_path().exists())
        self.assertEqual(run("")["result"], rows)
        self.assertEqual(hooks.load_cache()["videos"][0]["title"], "패스를 잘하는 3가지 방법")
        self.assertIn("패스를 잘하는 3가지 방법", hooks.title_candidates(["패스"], None, "long")[:3])


class ReviewRegressionTests(KitBase):
    """리뷰에서 나온 문제: 다시 내보내기·이름 바꾸기 · 군말 '네' · 메모 마커 · 쇼츠 '1분'."""
    write_project = SequenceKitTests.write_project

    def stem(self, name, label="롱폼 가편집"):
        return f"{core.adir(name).name}_{label}"

    def export(self, name, suffix="", label="롱폼 가편집"):
        import os
        import time
        p = self.out / f"{self.stem(name, label)}{suffix}.mp4"
        shutil.copy(self.media / "export.mp4", p)
        later = time.time() + len(list(self.out.iterdir()))  # 새로 내보낸 것이 가장 최근
        os.utime(p, (later, later))
        return p

    def kit_files(self):
        return sorted(p.name for p in self.out.iterdir() if "_올리기" in p.name)

    def test_reexport_keeps_edited_kit(self):
        name = self.add_video(self.NAME)
        self.write_project(name, [{"t": 0, "name": "시작"}, {"t": 250, "name": "패스 연습"}, {"t": 500, "name": "슈팅"}])
        self.export(name)
        upload.build_kit(name, "s1")
        upload.save_edits(name, "s1", title="고친 제목", desc="고친 설명", tags="패스, 슈팅")
        files = self.kit_files()
        self.assertEqual(files, [f"{self.stem(name)}_올리기.json", f"{self.stem(name)}_올리기.txt"])
        # 고쳐서 다시 내보내면 편집실이 '(2)'를 붙임 → 키트는 그대로 보여야 함
        self.export(name, " (2)")
        kit = upload.load_kit(name, "s1")
        self.assertIsNotNone(kit)
        self.assertEqual((kit["title"], kit["description"], kit["tags"]), ("고친 제목", "고친 설명", ["패스", "슈팅"]))
        self.assertTrue(any("다시 내보냈어요" in a and "(2).mp4" in a for a in kit["alerts"]), kit["alerts"])
        # 세 번째로 내보낸 뒤에도 자동 저장이 됨 · 파일 이름은 그대로 하나
        self.export(name, " (3)")
        kit = upload.save_edits(name, "s1", title="또 고친 제목")
        self.assertEqual(kit["title"], "또 고친 제목")
        self.assertEqual(upload.load_kit(name, "s1")["description"], "고친 설명")
        self.assertEqual(self.kit_files(), files)
        saved = json.loads((self.out / files[0]).read_text(encoding="utf-8"))
        self.assertNotIn("alerts", saved)  # 알림은 열 때마다 새로 봄
        self.assertEqual(saved["source"]["export"], f"{self.stem(name)}.mp4")  # 키트를 만든 영상
        # 다시 만들면 새로 내보낸 영상 기준 · 알림 없음 · 여전히 같은 파일
        kit = upload.build_kit(name, "s1")
        self.assertEqual(kit["source"]["export"], f"{self.stem(name)} (3).mp4")
        self.assertEqual(upload.load_kit(name, "s1")["alerts"], [])
        self.assertEqual(self.kit_files(), files)

    def test_export_after_kit_without_export(self):
        name = self.add_video(self.NAME)
        self.write_project(name, [])
        upload.build_kit(name, "s1")
        upload.save_edits(name, "s1", title="내보내기 전에 고친 제목")
        self.export(name)
        kit = upload.load_kit(name, "s1")
        self.assertEqual(kit["title"], "내보내기 전에 고친 제목")
        self.assertTrue(any("내보냈어요" in a for a in kit["alerts"]))
        self.assertFalse(any("아직 내보내지 않았어요" in n for n in kit["notes"]))

    def test_renamed_sequence_keeps_kit_and_moves_file(self):
        name = self.add_video(self.NAME)
        p = self.write_project(name, [])
        self.export(name)
        upload.build_kit(name, "s1")
        upload.save_edits(name, "s1", title="이름 바꾸기 전 제목")
        proj = json.loads(p.read_text(encoding="utf-8"))
        proj["sequences"][0]["name"] = "최종본"
        p.write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        kit = upload.load_kit(name, "s1")
        self.assertEqual(kit["title"], "이름 바꾸기 전 제목")
        self.assertEqual(kit["source"]["label"], "최종본")
        upload.save_edits(name, "s1", title="이름 바꾼 뒤 제목")
        new = self.stem(name, "최종본")
        self.assertEqual(self.kit_files(), [f"{new}_올리기.json", f"{new}_올리기.txt"])  # 예전 이름 파일은 정리
        self.assertEqual(upload.load_kit(name, "s1")["title"], "이름 바꾼 뒤 제목")

    def test_kit_saved_under_locked_fallback_name_is_found(self):
        import os
        import time
        name = self.add_video(self.NAME)
        upload.build_kit(name)
        base = core.adir(name).name
        js = self.out / f"{base}_올리기.json"
        alt = self.out / f"{base}_올리기 (2).json"  # 원래 파일이 잠겨 있어 다른 이름으로 저장됐던 경우
        k = json.loads(js.read_text(encoding="utf-8"))
        k["title"] = "잠겨서 따로 저장된 제목"
        alt.write_text(json.dumps(k, ensure_ascii=False), encoding="utf-8")
        later = time.time() + 5
        os.utime(alt, (later, later))
        self.assertEqual(upload.load_kit(name)["title"], "잠겨서 따로 저장된 제목")
        upload.save_edits(name, None, desc="다시 저장")
        self.assertFalse(alt.exists())  # 원래 이름으로 저장되면 따로 저장됐던 파일은 정리
        kit = upload.load_kit(name)
        self.assertEqual((kit["title"], kit["description"]), ("잠겨서 따로 저장된 제목", "다시 저장"))

    def test_same_file_name_from_other_video_not_overwritten(self):
        name = self.add_video(self.NAME)
        other = self.add_video(f"{core.adir(name).name}_롱폼 가편집.mp4")  # 원본 키트 이름이 편집본 키트와 같아지는 영상
        upload.build_kit(other)
        upload.save_edits(other, None, title="다른 영상 제목")
        self.write_project(name, [])
        upload.build_kit(name, "s1")
        upload.save_edits(name, "s1", title="편집본 제목")
        self.assertEqual(upload.load_kit(other)["title"], "다른 영상 제목")
        self.assertEqual(upload.load_kit(name, "s1")["title"], "편집본 제목")
        self.assertIsNone(upload.load_kit(name))

    def test_route_after_reexport(self):
        from urllib.parse import quote
        import app
        name = self.add_video(self.NAME)
        self.write_project(name, [])
        self.export(name)
        upload.build_kit(name, "s1")
        upload.save_edits(name, "s1", title="고친 제목")
        self.export(name, " (2)")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log"):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/upload/kit?name={quote(name)}&seq=s1", timeout=30) as r:
                    j = json.loads(r.read())
                self.assertEqual(j["kit"]["title"], "고친 제목")
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/upload/kit", method="POST",
                                             data=json.dumps({"name": name, "seq": "s1", "edits": {"title": "또 고침"}}).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    self.assertEqual(json.loads(r.read())["kit"]["title"], "또 고침")
        finally:
            srv.shutdown()
            srv.server_close()

    # ---- 군말: '네'(넷) · '막' · '그'는 지우지 않음 ----
    def test_first_sentence_keeps_numeral_ne(self):
        fs = upload.first_sentence
        self.assertEqual(fs("네 번째는 아웃사이드 패스예요."), "네 번째는 아웃사이드 패스예요")
        self.assertEqual(fs("네 가지만 기억하세요."), "네 가지만 기억하세요")
        self.assertEqual(fs("막 차면 안 돼요."), "막 차면 안 돼요")
        self.assertEqual(fs("그 공을 잡을 때 몸을 여세요."), "그 공을 잡을 때 몸을 여세요")
        self.assertEqual(fs("네, 이제 패스를 볼게요."), "패스를 볼게요")  # 대답 '네,'는 군말
        self.assertEqual(fs("그 음 패스는요"), "패스는요")

    def test_numbered_sections_chapter_titles(self):
        lines = ["안녕하세요 풋살사관학교 최경진입니다.", "첫 번째는 인사이드 패스예요.", "두 번째는 발바닥 트래핑이에요.",
                 "세 번째는 퍼스트 터치예요.", "네 번째는 아웃사이드 패스예요."]
        segs, fill = [], ["공을 끝까지 보세요.", "몸을 낮추고 차세요.", "네 가지만 기억하면 퍼스트 터치가 좋아져요."]
        for k in range(4):
            t = 120.0 * k
            for txt in ([lines[0]] if k == 0 else []) + [lines[k + 1]]:
                segs.append({"start": t, "end": t + 4, "text": txt})
                t += 4.6
            i = 0
            while t + 5 < 120.0 * (k + 1) - 4:
                segs.append({"start": t, "end": t + 5, "text": fill[i % 3]})
                i += 1
                t += 5.6
        chs = upload.chapters(segs, dur=480)
        check_chapters(self, chs, 480)
        self.assertEqual([c["t"] for c in chs], [0, 120, 240, 360])
        self.assertEqual(chs[-1]["title"], "네 번째는 아웃사이드 패스예요")
        self.assertFalse(any(c["title"].startswith(("번째", "가지")) for c in chs))
        hook = upload.hook_lines([{"start": 0, "end": 4, "text": "네 가지만 기억하면 퍼스트 터치가 좋아져요."}], ["퍼스트 터치"])
        self.assertEqual(hook[0], "“네 가지만 기억하면 퍼스트 터치가 좋아져요”")

    # ---- 메모로 붙인 마커 이름 ----
    def test_memo_markers_skipped_and_marker_note(self):
        segs = fixture_segments()
        notes = []
        ms = [{"t": 0, "name": "시작"}, {"t": 130, "name": "BGM 바꾸기"}, {"t": 250, "name": "패스 연습"},
              {"t": 400, "name": "여기 다시 확인"}, {"t": 500, "name": "방향 바꾸기"}]
        chs = upload.chapters(segs, ms, None, 720, notes)
        self.assertEqual([c["title"] for c in chs], ["시작", "패스 연습", "방향 바꾸기"])
        self.assertTrue(any("메모처럼" in n and "BGM 바꾸기" in n for n in notes), notes)
        self.assertTrue(any("마커 이름으로 챕터" in n for n in notes), notes)
        notes = []
        upload.chapters(segs, None, None, 720, notes)  # 자동으로 나눈 챕터에는 마커 알림 없음
        self.assertFalse(any("마커" in n for n in notes))

    def test_missing_thumbnail_hint_names_export_button(self):
        res = upload.thumbnail_check(self.add_video(self.NAME))
        self.assertTrue(any("'이미지로 저장'" in p for p in res["problems"]), res)  # 썸네일 편집기의 버튼 이름 그대로

    # ---- 쇼츠 '1분' ----
    def test_shorts_over_one_minute_not_called_one_minute(self):
        segs = [{"start": 1, "end": 5, "text": "드리블할 때 발바닥을 쓰세요."}, {"start": 6, "end": 12, "text": "드리블은 몸을 낮추는 게 핵심이에요."}]
        name = self.add_video("드리블 쇼츠 2분.mp4", "short", segs)
        with mock.patch.object(editor, "media_info", return_value={"duration": 150.0, "width": 1080, "height": 1920, "fps": 30, "hdr": None}):
            kit = upload.build_kit(name)
        self.assertEqual(kit["format"], "shorts")
        self.assertNotIn("1분", kit["description"] + " ".join(kit["titles"]))
        kit = upload.build_kit(name)  # 40초짜리
        self.assertIn("1분 안에", kit["description"])


if __name__ == "__main__":
    unittest.main()
