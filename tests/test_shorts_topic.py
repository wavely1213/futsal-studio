"""쇼츠 첫머리 주제 줄 (BR-120 · D-220) — 저장소 폴더에서 python3 -m unittest tests.test_shorts_topic

- 주제 정하기: 영상 주제(영상 이름 · 용어 사전 말) + 이 쇼츠 포인트(순서 말 · 'X이 핵심' · 소개한 용어) → 위 큰 줄에 없는 것만 한 줄
  (좋은 주제 · 주제 없음 · 받아쓰기 헛말이 든 고정 자료 — 헛말·조각은 안 씀)
- 자리: 위 큰 제목(BR-093) 바로 위 · 위쪽 안전 영역 · 말 자막·영상(얼굴) 띠와 안 겹침 · 처음 2.5초 · 길이는 그대로 ·
  큰 제목이 두 줄·대사 그대로·위에 자리가 없으면 큰 제목 자리에 처음 2.5초 바꿔 끼우기 (큰 제목은 그 뒤부터)
- 편집실: 미리보기(editor.html topicTitle·videoTop)와 서버(editor.topic_title·video_top)가 같은 자리 · 저장/다시 열기 · 내보내기 ASS 에 그대로 ·
  editor.html 함수를 node 로 그대로 실행: 앞을 잘라도(Q·대사 잘라내기·군더더기 정리) 주제 줄은 맨 앞 그대로 · 바꿔 끼운 주제 줄을 지우면 큰 제목이 다시
  처음부터 · 레이아웃 틀을 바꿔도 자기 큰 줄(MSG 쇼츠 '다시 보기 ▶' 말고)에 맞춰 안 겹침
- MSG 쇼츠: 모든 스타일·양에서 큰 줄(포인트) + 주제 줄(큰 줄과 같은 글꼴) · 순서 말뿐인 큰 줄·말 자막을 되풀이하는 큰 줄은 안 씀 ·
  주제 줄을 못 놓으면 주제 낱말을 뺀 큰 줄 대신 다음 후보 · 첫머리가 말한 순서의 상황 자막은 뺌 · 설정을 끄면 예전 훅 그대로
고정 자료: tests/fixtures/rough_e12.json (실제 받아쓴 MSGRAW01~03·LESSON04 · '디딤밤'·'골 때' 같은 헛말이 그대로 있음)."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import msg  # noqa: E402
import style  # noqa: E402

FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "rough_e12.json").read_text(encoding="utf-8"))["videos"]
NAMES = {"MSGRAW01": "20261008_MSGRAW01_퍼스트 터치 레슨.mp4", "MSGRAW02": "20261008_MSGRAW02_패스 앤 무브 드릴.mp4",
         "MSGRAW03": "20261008_MSGRAW03_슈팅 챌린지.mp4", "LESSON04": "20261008_LESSON04_촬영본 0912.mp4", "MSGRAW01_nocond": "v1n.mp4",
         "LESSON04_nocond": "v4n.mp4"}
W, H = editor.frame_size("shorts")


def seg(t, text):
    return {"start": float(t), "end": float(t) + 3.0, "text": text}


def inter(a, b, gap=0.0):
    """두 상자 (왼, 위, 오른, 아래) 가 gap 안으로 겹치는지."""
    return min(a[2], b[2]) - max(a[0], b[0]) > -gap and min(a[3], b[3]) - max(a[1], b[1]) > -gap


def allowed_words(vt):
    """주제 줄에 나와도 되는 낱말: 영상 주제 · 용어 사전 말 · 요점 낱말 · 순서 낱말·숫자 (그 밖의 말 = 받아쓰기에서 온 헛말·조각)."""
    ok = set(re.findall(r"[가-힣A-Za-z0-9]+", vt or ""))
    for t in editor._point_terms():
        ok |= set(t.split())
    return ok | set(editor.POINT_NOUNS) | {"포인트", "동작", "기술", "방법", "팁", "단계", "챌린지"} | {str(k) for k in range(1, 8)}


class WorkMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="주제 줄 "))
        work = self.tmp / "작업"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.p = mock.patch.multiple(core, **dirs)
        self.p.start()
        self.s = mock.patch.object(style, "STYLES", work / "styles")
        self.s.start()
        self.o = mock.patch.object(editor, "_sound_onsets", lambda n, w: [])
        self.o.start()

    def tearDown(self):
        self.o.stop()
        self.s.stop()
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, segs, analysis=None):
        d = core.adir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps(analysis or {"silences": [], "loud_peaks": []}, ensure_ascii=False), encoding="utf-8")
        return name

    def fix(self, key):
        return self.put(NAMES[key], FIX[key]["transcript"], FIX[key]["analysis"])


class VideoTopicTest(unittest.TestCase):
    def test_user_title_that_matches_speech(self):
        segs = FIX["MSGRAW01"]["transcript"]
        self.assertEqual(editor.video_topic(NAMES["MSGRAW01"], segs), "퍼스트 터치 레슨")
        self.assertEqual(editor.video_topic(NAMES["MSGRAW02"], FIX["MSGRAW02"]["transcript"]), "패스 앤 무브 드릴")
        self.assertEqual(editor.video_topic("20261008_ABCD1234_[풋살 꿀팁] 슈팅 챌린지.mp4", FIX["MSGRAW03"]["transcript"]), "슈팅 챌린지")

    def test_camera_date_or_unrelated_names_fall_back_to_terms(self):
        segs = FIX["MSGRAW01"]["transcript"]
        for name in ("IMG_1234.mp4", "20261008_LESSON04_촬영본 0912.mp4", "20261008_X_9월 12일 레슨.mp4", "20261008_X_오늘 연습.mp4",
                     "20261008_X_드리블 꿀팁.mp4", "20261008_X_퍼스트 터치 레슨 완전 정복 풀버전 영상.mp4"):
            self.assertEqual(editor.video_topic(name, segs), "퍼스트 터치", name)

    def test_name_must_match_the_lesson_as_whole_words(self):
        """검토: 이름의 낱말이 대사 속 글자로만 들어 있으면('바로 슈팅까지' 한 번 · '원터치') 엉뚱한 주제가 모든 쇼츠에 찍힘 → 대사 용어로."""
        segs = FIX["LESSON04"]["transcript"]
        for name in ("20261008_X_슈팅 연습.mp4", "20261008_X_퍼스트 터치 레슨.mp4", "20261008_X_패스.mp4", "20261008_X_수비 연습.mp4"):
            self.assertEqual(editor.video_topic(name, segs), "2대1 패스", name)
        self.assertEqual(editor.video_topic("20261008_X_2대1 패스 레슨.mp4", segs), "2대1 패스 레슨")         # 가장 많이 나온 용어
        self.assertEqual(editor.video_topic("20261008_X_디딤발 연습.mp4", FIX["MSGRAW01"]["transcript"]), "디딤발 연습")  # 대사에 두 번
        self.assertEqual(editor.video_topic("20261008_X_패스 앤 무브 드릴.mp4", segs), "2대1 패스")     # '패스'는 흔한 말 · 1위 용어 아님

    def test_challenge_and_generic_terms(self):
        self.assertEqual(editor.video_topic("v.mp4", FIX["MSGRAW03"]["transcript"]), "슈팅 챌린지")
        self.assertEqual(editor.video_topic("v.mp4", FIX["LESSON04"]["transcript"]), "2대1 패스")
        plain = [seg(0, "패스를 주고 바로 뛰세요."), seg(5, "패스는 정확하게 하세요."), seg(10, "패스 연습 많이 하세요.")]
        self.assertEqual(editor.video_topic("v.mp4", plain), "")                     # '패스' 하나뿐 → 주제 없음
        self.assertEqual(editor.video_topic("v.mp4", plain + [seg(15, "오늘은 패스 챌린지예요.")]), "패스 챌린지")

    def test_garbage_or_empty_transcript_gives_nothing(self):
        junk = [seg(0, "처치 공바람 이력 파스..."), seg(4, "paced...Pacific..."), seg(8, "음 어 그 저기 뭐지")]
        self.assertEqual(editor.video_topic("v.mp4", junk), "")
        self.assertEqual(editor.video_topic("v.mp4", []), "")
        self.assertEqual(editor.video_topic("", junk), "")


class ShortPointTest(unittest.TestCase):
    def point(self, *lines):
        segs = [seg(10 + 4 * k, x) for k, x in enumerate(lines)]
        return editor.short_point({"start": 10.0, "end": 10.0 + 4 * len(lines)}, segs)

    def test_ordinal_point_and_term(self):
        self.assertEqual(self.point("자, 두 번째 포인트는 디딤발이에요.", "디딤발은 공 옆에 두세요."), {"n": 2, "unit": "포인트", "phrase": "디딤발"})
        self.assertEqual(self.point("세번째 포인트 패스 방향이에요", "오른발 앞쪽으로 넣어 주세요."), {"n": 3, "unit": "포인트", "phrase": "패스 방향"})
        self.assertEqual(self.point("세 번째 포인트. 트래핑할 때는 공을 보내세요."), {"n": 3, "unit": "포인트", "phrase": "트래핑"})
        self.assertEqual(self.point("두번째 동작은 패스하고 옆으로 빠지는 거예요."), {"n": 2, "unit": "동작", "phrase": None})

    def test_key_phrase_anywhere_in_short(self):
        p = self.point("네번째, 슛.", "아이고, 빗나갔어요.", "들어갔다. 성공입니다.", "디딤발 방향이 핵심이에요.")
        self.assertEqual(p, {"n": None, "unit": "포인트", "phrase": "디딤발 방향"})
        self.assertEqual(self.point("슈팅은 디딤발 방향이 핵심이에요.")["phrase"], "디딤발 방향")

    def test_garbage_and_fragments_are_not_points(self):
        self.assertIsNone(self.point("자, 두번째 포인트는 디딤밤이 공이랑 너무 가까우면")["phrase"])   # 받아쓰기 헛말 '디딤밤'
        self.assertIsNone(self.point("이 차이가 정말 중요해요.")["phrase"])
        self.assertEqual(self.point("월패스는 이 타이밍이 생명이에요.")["phrase"], "월패스")         # '이 타이밍' 조각 말고 소개한 용어
        self.assertIsNone(self.point("공바람 처치가 핵심이에요.")["phrase"])
        self.assertIsNone(self.point("디딤발 공바람이 핵심이에요.")["phrase"])                         # 사전 밖 낱말을 붙인 말
        self.assertEqual(self.point("자, 첫 번째 갑니다.", "아, 아깝다.")["n"], None)                 # 슛 세기는 포인트가 아님


class TopicLineTest(unittest.TestCase):
    def test_compose_without_repeating_main_words(self):
        P = editor.topic_line
        self.assertEqual(P("퍼스트 터치 레슨", {"n": 2, "unit": "포인트", "phrase": "디딤발"}, "디딤발 꿀팁"), "퍼스트 터치 레슨 · 포인트 2")
        self.assertEqual(P("슈팅 챌린지", {"phrase": "디딤발 방향"}, "슈팅 꿀팁"), "디딤발 방향")
        self.assertEqual(P("슈팅 챌린지", {"phrase": "디딤발 방향"}, "과연 몇 개나 들어갈까요?"), "슈팅 챌린지 · 디딤발 방향")
        self.assertEqual(P("패스 앤 무브 드릴", {"n": 2, "unit": "동작"}, "패스\n두 번째 동작"), "패스 앤 무브 드릴")  # '패스' 하나는 같은 말 아님
        self.assertEqual(P("패스 앤 무브 드릴", {"n": 2, "unit": "동작"}, "두 번째 동작"), "패스 앤 무브 드릴")   # 번호는 큰 줄에 이미 있음
        self.assertEqual(P("패스 앤 무브 드릴", {"n": 2, "unit": "동작"}, "패스 앤 무브 드릴"), "동작 2")        # 큰 줄이 영상 주제 그대로
        self.assertEqual(P("패스 앤 무브 드릴", {}, "패스하고 그 자리에 서 있으면 왜 안될까요?"), "패스 앤 무브 드릴")
        self.assertEqual(P("2대1 패스", {"n": 2, "unit": "포인트", "phrase": "리턴 패스"}, "리턴 패스 꿀팁"), "2대1 패스 · 포인트 2")  # 검토 LESSON04
        self.assertEqual(P("슈팅 챌린지", {}, "슛!"), "")                     # '슛' = '슈팅'
        self.assertEqual(P("", {"n": 3, "unit": "포인트"}, "트래핑 꿀팁"), "")  # 번호 하나는 뜻이 없음
        self.assertEqual(P("퍼스트 터치 레슨", {"n": 1, "unit": "포인트"}, "퍼스트 터치\n첫 번째 포인트"), "")
        self.assertEqual(P("첫 터치 레슨", {"n": 1, "unit": "포인트"}, "첫 터치 레슨"), "포인트 1")   # 큰 줄이 주제를 말함 → 순서만
        self.assertEqual(P("첫 터치 레슨", {"n": 1, "phrase": "디딤발"}, "첫 터치 레슨"), "디딤발")
        self.assertEqual(P("퍼스트 터치 레슨", {"n": 2, "unit": "포인트"}, "디딤발 포인트!"), "퍼스트 터치 레슨")   # '포인트'가 큰 줄에 있음

    def test_bare_number_only_under_the_whole_topic(self):
        """번호만('포인트 2')은 큰 줄에 영상 주제가 글자 그대로 있을 때만 — 낱말 하나만 같으면 번호만 남기지 않음 (검토)."""
        P = editor.topic_line
        self.assertEqual(P("퍼스트 터치 레슨", {"n": 2, "unit": "포인트"}, "퍼스트 터치 꿀팁"), "")
        self.assertEqual(P("2대1 패스", {"n": 2, "unit": "포인트"}, "2대1 패스!"), "포인트 2")
        for vt in ("2대1 패스", "퍼스트 터치 레슨", "슈팅 챌린지", ""):
            for main in ("리턴 패스 꿀팁", "퍼스트 터치 꿀팁", "슈팅!", "2대1 패스!", "디딤발 꿀팁", "패스하고 왜 안될까요?"):
                out = P(vt, {"n": 3, "unit": "포인트"}, main)
                if re.fullmatch(r"\S+ \d", out):
                    self.assertIn(editor._letters(vt), editor._letters(main), (vt, main, out))

    def test_one_phone_line(self):
        out = editor.topic_line("퍼스트 터치 레슨 완전 정복", {"phrase": "디딤발 방향"}, "과연 될까요?")
        self.assertEqual(out, "퍼스트 터치 레슨 완전 정복")      # 둘 다 넣으면 14자를 넘음 → 영상 주제만
        self.assertLessEqual(len(editor._norm(out.replace("·", ""))), editor.TOPIC_MAX)
        self.assertEqual(editor.topic_line("가" * 15, {}, "디딤발 꿀팁"), "")

    def test_shares_word(self):
        self.assertTrue(editor._shares_word("퍼스트 터치 레슨", "퍼스트 터치\n첫 번째 포인트"))
        self.assertFalse(editor._shares_word("퍼스트 터치 레슨", "디딤발 꿀팁"))
        self.assertTrue(editor._shares_word("슈팅 챌린지", "슛!"))               # '슛' = '슈팅'
        self.assertTrue(editor._shares_word("패스", "2대1 패스!"))                # 한쪽 글 전체가 다른 쪽에
        self.assertTrue(editor._shares_word("드리블 연습", "드리블하고 슛"))     # 흔한 말이 아니면 앞이 같은 낱말도
        # 흔한 말('패스' · HOOK_NOT_TOPIC) 하나만 같으면 같은 말이 아님 (검토: 주제 줄이 빠지거나 '포인트 2'만 남음)
        self.assertFalse(editor._shares_word("패스 앤 무브 드릴", "패스하고 그 자리에"))
        self.assertFalse(editor._shares_word("2대1 패스", "리턴 패스 꿀팁"))


class RecommendTopicTest(WorkMixin, unittest.TestCase):
    def test_fixture_shorts(self):
        got = {}
        for key in ("MSGRAW01", "MSGRAW02", "MSGRAW03", "LESSON04", "MSGRAW01_nocond", "LESSON04_nocond"):
            rec = editor.recommend(self.fix(key))
            vt = rec["videoTopic"]
            ok = allowed_words(vt)
            for r in rec["shorts"]:
                self.assertIn("point", r)
                t = r["topic"]
                got[(key, r["hook"])] = t
                if not t:
                    continue
                self.assertFalse(editor._shares_word(t, r["hook"]), (key, t, r["hook"]))      # 위 큰 제목과 같은 낱말 없음
                self.assertLessEqual(len(editor._norm(t.replace("·", ""))), editor.TOPIC_MAX)
                bad = [w for w in re.findall(r"[가-힣A-Za-z0-9]+", t) if w not in ok]
                self.assertFalse(bad, (key, t, "받아쓰기에서 온 낱말"))
                if re.fullmatch(r"\S+ \d", t):   # 번호만은 큰 제목에 영상 주제가 그대로 있을 때만
                    self.assertIn(editor._letters(vt), editor._letters(r["hook"]), (key, t))
        self.assertEqual(got[("MSGRAW01", "트래핑 꿀팁")], "퍼스트 터치 레슨 · 포인트 3")
        self.assertEqual(got[("MSGRAW01_nocond", "디딤발 꿀팁")], "퍼스트 터치 · 포인트 2")
        self.assertEqual(got[("MSGRAW01", "퍼스트 터치\n첫 번째 포인트")], "")   # 위 큰 제목이 이미 주제·순서를 말함
        self.assertEqual(got[("MSGRAW02", "패스\n두 번째 동작")], "패스 앤 무브 드릴")       # 검토: '패스' 하나로 주제가 있다고 보지 않음
        self.assertEqual(got[("MSGRAW02", "패스하고 그 자리에 서 있으면 왜 안될까요?")], "패스 앤 무브 드릴")
        self.assertEqual(got[("LESSON04_nocond", "리턴 패스 꿀팁")], "2대1 패스 · 포인트 2")   # 검토: '포인트 2'만 남지 않음
        self.assertIn(("MSGRAW01", "퍼스트 터치\n두 번째 포인트"), got)          # '디딤밤'(헛말) → 포인트 말 없음
        self.assertEqual(got[("MSGRAW01", "퍼스트 터치\n두 번째 포인트")], "")

    def test_two_line_or_said_top_title_swaps_in(self):
        """검토: MSGRAW02 가편집 쇼츠 둘 다 주제 줄이 없었음 (두 줄 큰 제목 · 대사 그대로인 질문 큰 제목) → 처음 2.5초 큰 제목 자리에 바꿔 끼움."""
        name = self.fix("MSGRAW02")
        rec = editor.recommend(name)
        said = {r["hook"]: r["hookSaid"] for r in rec["shorts"]}
        self.assertTrue(said["패스하고 그 자리에 서 있으면 왜 안될까요?"])
        self.assertFalse(said["패스\n두 번째 동작"])
        seqs = editor.auto_sequences(name, {"duration": FIX["MSGRAW02"]["duration"], "width": 1920, "height": 1080, "fps": 30.0}, kinds=("shorts",))
        self.assertEqual(len(seqs), 2)
        for q in seqs:
            kt = next(t for t in q["titles"] if t.get("plan") == "topic")
            top = next(t for t in q["titles"] if t["id"] == kt["under"])
            self.assertEqual((kt["text"], kt["start"], kt["dur"], kt["swap"]), ("패스 앤 무브 드릴", 0.0, editor.TOPIC_SEC, True))
            self.assertAlmostEqual(top["start"], editor.TOPIC_SEC)                    # 큰 제목은 주제 줄 뒤부터 · 끝은 그대로
            self.assertAlmostEqual(top["start"] + top["dur"], editor.seq_total(q), places=1)
            self.assertEqual([kt["style"].get(k) for k in ("y", "x", "align", "fill", "font")], [top["style"].get(k) for k in ("y", "x", "align", "fill", "font")])
            self.assertTrue(editor._one_line(kt["text"], kt["style"], W))

    def test_setting_off(self):
        name = self.fix("MSGRAW01")
        self.assertTrue(style.shorts_topic_on())
        style.set_shorts_topic(False)
        self.assertFalse(style.shorts_topic_on())
        rec = editor.recommend(name)
        self.assertEqual([r["topic"] for r in rec["shorts"]], [""] * len(rec["shorts"]))
        seqs = editor.auto_sequences(name, {"duration": FIX["MSGRAW01"]["duration"], "width": 1920, "height": 1080, "fps": 30.0}, kinds=("shorts",))
        self.assertFalse([t for q in seqs for t in q["titles"] if t.get("plan") == "topic"])
        style.set_shorts_topic(True)
        self.assertTrue(any(r["topic"] for r in editor.recommend(name)["shorts"]))

    def test_broken_setting_file_means_on(self):
        f = style._shorts_topic_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{깨진", encoding="utf-8")
        self.assertTrue(style.shorts_topic_on())


def video_band(layout, scale=1.0):
    """쇼츠 화면에서 가로 영상(16:9)이 차지하는 위·아래 (editor._fit 과 같은 셈 · scale: 확대 컷)."""
    ch = min(H, W * float(layout.get("zoom") or 1) * 9 / 16 * scale)
    top = (H - ch) * float(layout.get("vpos", 0.5)) / H
    return top, top + ch / H


class LayoutTest(WorkMixin, unittest.TestCase):
    INFO = {"duration": FIX["MSGRAW01"]["duration"], "width": 1920, "height": 1080, "fps": 30.0}

    def check_seq(self, q):
        kts = [t for t in q["titles"] if t.get("plan") == "topic"]
        if not kts:
            return None
        self.assertEqual(len(kts), 1)
        kt, top = kts[0], q["titles"][0]
        self.assertNotEqual(top.get("plan"), "topic")
        self.assertEqual(kt["under"], top["id"])
        self.assertEqual(kt["start"], 0.0)
        self.assertLessEqual(kt["dur"], editor.TOPIC_SEC)
        self.assertGreaterEqual(kt["style"]["size"], min(editor.TOPIC_SIZES))
        kb = editor.text_box(kt["text"], kt["style"], W, H)
        tb = editor.text_box(top["text"], top["style"], W, H)
        if kt.get("swap"):   # 바꿔 끼우기: 큰 제목 자리 · 큰 제목은 주제 줄 뒤부터 (같은 때 안 뜸)
            self.assertAlmostEqual(top["start"], kt["start"] + kt["dur"])
            self.assertEqual(kt["style"]["y"], top["style"]["y"])
        else:
            self.assertLessEqual(kb[3], tb[1] - editor.TXT_GAP + 1e-6, "위 큰 제목 바로 위 · 틈")
        self.assertTrue(editor._one_line(kt["text"], kt["style"], W))
        self.assertGreaterEqual(kb[1], editor.TOPIC_TOP - 1e-6, "위쪽 안전 영역")
        self.assertGreaterEqual(kb[0], 0.06)
        self.assertLessEqual(kb[2], 0.94)
        vtop = video_band(q["layout"], 1.6)[0]    # 확대 컷(최대 1.6배)까지 넣어도 영상(얼굴) 위
        self.assertLess(kb[3], vtop)
        cb = editor._cap_band(q["captionStyle"], H)
        self.assertFalse(cb[0] < kb[3] and kb[1] < cb[1], "말 자막 띠와 안 겹침")
        self.assertFalse(editor._shares_word(kt["text"], top["text"]))
        self.assertAlmostEqual(editor.seq_total(q), max(editor.i_end(it) for it in q["items"]))  # 길이는 그대로 (글자만)
        return kt

    def test_auto_rough_shorts(self):
        name = self.fix("MSGRAW01")
        made = []
        for st in (None, {"captionPos": "top"}, {"captionPos": "middle", "captionColor": "#1A1A1A"}, {"captionColor": "#FFD400"}):
            for q in editor.auto_sequences(name, self.INFO, st, ("shorts",)):
                kt = self.check_seq(q)
                if kt:
                    made.append((st, kt))
        self.assertTrue(made)
        by = {json.dumps(s, sort_keys=True): k for s, k in made}
        dark = by[json.dumps({"captionPos": "middle", "captionColor": "#1A1A1A"}, sort_keys=True)]
        self.assertEqual((dark["style"]["fill"], dark["style"]["stroke"]), ("#1A1A1A", "#FFFFFF"))  # 어두운 자막 색 → 흰 테두리
        self.assertEqual(by[json.dumps({"captionColor": "#FFD400"}, sort_keys=True)]["style"]["fill"], "#FFD400")  # 배운 스타일 색
        self.assertEqual(by["null"]["style"]["fill"], "#FFFFFF")
        self.assertEqual(by["null"]["style"]["effect"], "pop")

    def test_no_room_or_two_line_top_title_swaps_in(self):
        """두 줄·긴 질문·대사 그대로인 큰 제목, 위에 자리가 없는 큰 제목 → 큰 제목 자리·모양에 처음 2.5초 바꿔 끼우기 (쇼츠가 3.5초보다 짧으면 없음)."""
        T = editor.TITLE_STYLE
        for top in ({"id": "a", "text": "퍼스트 터치\n첫 번째 포인트", "start": 0, "dur": 30, "style": dict(T)},
                    {"id": "a", "text": "패스하고 그 자리에 서 있으면 왜 안될까요?", "start": 0, "dur": 30, "style": dict(T)},
                    {"id": "a", "text": "디딤발 꿀팁", "start": 0, "dur": 30, "style": dict(T, y=0.15)}):   # ('꽉 채우기' 틀 자리)
            kt = editor.topic_title("퍼스트 터치 레슨 · 포인트 2", top, editor.SHORTS_STYLE, 30.0)
            self.assertEqual((kt["swap"], kt["under"], kt["start"], kt["dur"], kt["style"]["y"], kt["style"]["fill"]),
                             (True, "a", 0.0, editor.TOPIC_SEC, top["style"]["y"], T["fill"]), top["text"])
            self.assertTrue(editor._one_line(kt["text"], kt["style"], W))
            self.assertIsNone(editor.topic_title("퍼스트 터치 레슨 · 포인트 2", top, editor.SHORTS_STYLE, 3.2))   # 짧은 쇼츠는 안 함
            editor.swap_in(top, kt)
            self.assertEqual((top["start"], top["dur"]), (editor.TOPIC_SEC, 30 - editor.TOPIC_SEC))
        high = {"id": "a", "text": "디딤발 꿀팁", "start": 0, "dur": 30, "style": dict(T, y=0.12)}
        self.assertIsNone(editor.topic_title("퍼스트 터치 레슨 · 포인트 2", high, None, 30.0))   # 큰 제목 자리도 위쪽 안전 영역 밖 → 없음
        said = editor.topic_title("슈팅 챌린지", {"id": "b", "text": "디딤발 꿀팁", "style": dict(T)}, None, 20.0, said=True)
        self.assertTrue(said["swap"])                                                   # 대사 그대로인 큰 제목은 위에 더하지 않음
        ok = editor.topic_title("퍼스트 터치 레슨 · 포인트 2", {"id": "c", "text": "디딤발 꿀팁", "style": dict(T)}, length=1.2)
        self.assertEqual((ok["dur"], ok["plan"], ok["style"]["size"], ok.get("swap")), (1.2, "topic", 72, None))

    def test_caption_band_and_video_are_avoided(self):
        """검토: 큰 제목을 옮긴 쇼츠에서 '새 쇼츠 편집본으로 추가'가 주제 줄을 말 자막 위에 놓음 → 말 자막 띠(+틈)·영상 위 끝과 겹치면 다른 자리·없음."""
        top = {"id": "a", "text": "디딤발 꿀팁", "style": dict(editor.TITLE_STYLE, y=0.62)}
        cap = dict(editor.SHORTS_STYLE, y=0.5)
        kt = editor.topic_title("퍼스트 터치 레슨", top, cap, 30.0)        # 위는 말 자막과 겹침 → 큰 제목 자리 (꽉 채우기: 영상 검사 없음)
        self.assertTrue(kt["swap"])
        cb = editor._cap_band(cap, H)
        kb = editor.text_box(kt["text"], kt["style"], W, H)
        self.assertFalse(editor._overlap(cb, (kb[1], kb[3]), editor.TXT_GAP))
        vt = editor.video_top(editor.BOX_LAYOUT, [], 1920, 1080)
        self.assertIsNone(editor.topic_title("퍼스트 터치 레슨", top, cap, 30.0, vtop=vt))   # 위아래 박스: 영상(얼굴) 위라 자리 없음
        self.assertIsNone(editor.topic_title("퍼스트 터치 레슨", top, cap, 3.0))

    def test_video_top(self):
        V = editor.video_top
        self.assertAlmostEqual(V(editor.BOX_LAYOUT, [], 1920, 1080), (1920 - 607.5) / 2 / 1920, places=4)
        zoomed = [{"track": "V1", "start": 0.0, "fx": {"scale": {"v": 100, "k": [{"t": 1, "v": 160}]}}}]
        self.assertAlmostEqual(V(editor.BOX_LAYOUT, zoomed, 1920, 1080), (960 - 607.5 * 1.6 / 2) / 1920, places=4)
        later = [{"track": "V1", "start": 3.0, "fx": {"scale": {"v": 160, "k": []}}}, {"track": "V2", "start": 0.0, "fx": {"scale": {"v": 200, "k": []}}}]
        self.assertAlmostEqual(V(editor.BOX_LAYOUT, later, 1920, 1080), V(editor.BOX_LAYOUT, [], 1920, 1080))  # 2.5초 뒤·위 트랙은 안 봄
        self.assertIsNone(V({"mode": "fill"}, [], 1920, 1080))
        self.assertEqual(V(dict(editor.BOX_LAYOUT, mode="blur"), [], 1080, 1920), 0.0)   # 세로 원본은 화면 전체

    def test_long_topic_uses_smaller_size(self):
        top = {"id": "a", "text": "디딤발 꿀팁", "start": 0, "dur": 30, "style": dict(editor.TITLE_STYLE)}
        kt = editor.topic_title("가나다라마바사아자차카타파하", top)
        self.assertEqual(kt["style"]["size"], 64)
        self.assertIsNone(editor.topic_title("가나다라마바사아자차카타파하가나다", top))


JS_DEFS = ("FPS,EPS,fr,sp,iEnd,iLen,tlOf,mt,DEF,prm,emOf,EPF,epFontOn,fmNow,cutText,isMainAV,r6,mergeIv,tlRemap,cutPieces,moveItem,remapSeq,tidySeq,"
           "TOPIC,TOPIC_STYLE,txtBox,textW,oneLine,topicFill,capBand,videoTop,topicTitle,r3,swapIn,topicUnder,headTopic,epFrame,epFacts,topicAfter,topicGone,"
           "placeTopic,gfxCut,EP,EPX,epSeriesFit,epExtras,epCapClear,epTitleStyle,epOn,epCard,epOff,tplTitle,TEMPLATES").split(",")


def js_defs(names=JS_DEFS):
    """editor.html 의 맨 바깥 함수·상수 정의를 이름으로 꺼냄 (한 줄 정의 · '}' 줄로 끝나는 여러 줄 정의)."""
    lines = (Path(__file__).resolve().parents[1] / "editor.html").read_text(encoding="utf-8").split("\n")
    out = []
    for nm in names:
        pat = re.compile(rf"^(?:async )?function {re.escape(nm)}\(|^(?:const|let) {re.escape(nm)} =")
        i = next(k for k, ln in enumerate(lines) if pat.match(ln))
        if lines[i].count("{") <= lines[i].count("}") and lines[i].count("(") <= lines[i].count(")"):
            out.append(lines[i])
        else:
            j = next(k for k in range(i + 1, len(lines)) if lines[k] in ("}", "};"))
            out.append("\n".join(lines[i:j + 1]))
    return "\n".join(out)


def run_js(body):
    """editor.html 함수를 node 로 그대로 실행 — body 가 out 에 담은 값을 JSON 으로 돌려받음 (S·P·total 은 시험 흉내)."""
    prog = ("let N = 0; const nid = () => 'n' + (++N);\nlet S = null, P = { info: { width: 1920, height: 1080 } }, out = null;\n"
            "const total = () => Math.max(0, ...S.items.map(iEnd));\nconst tacFollowCut = g => g, tacRefollow = g => g.follow;\n"
            + js_defs() + "\n" + body + "\nprocess.stdout.write(JSON.stringify(out));")
    r = subprocess.run([shutil.which("node"), "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "node 가 없어요")
class PreviewPairTest(unittest.TestCase):
    """editor.html topicTitle·txtBox·videoTop 를 node 로 그대로 실행 → editor.py topic_title·text_box·video_top 와 같은 값 (미리보기 = 내보내기 = 서버)."""

    def test_same_as_server(self):
        T = dict(editor.TITLE_STYLE)
        tops = [{"text": "디딤발 꿀팁", "style": T}, {"text": "트래핑 꿀팁", "style": dict(T, x=0.3, align="left")},
                {"text": "Shooting 꿀팁!", "style": dict(T, size=90, strokeW=4, weight="Bold")}, {"text": "두 줄\n제목", "style": T},
                {"text": "디딤발 꿀팁", "style": dict(T, y=0.12)}, {"text": "디딤발 꿀팁", "style": dict(T, y=0.15)},
                {"text": "디딤발", "style": dict(T, y=0.3, align="right", x=0.9)}, {"text": "디딤발 꿀팁", "style": dict(T, y=0.62)},
                {"text": "패스하고 그 자리에 서 있으면 왜 안될까요?", "style": T}]
        texts = ["퍼스트 터치 레슨 · 포인트 2", "디딤발 방향", "가나다라마바사아자차카타파하", "2대1 패스 ABC xyz"]
        caps = [dict(editor.SHORTS_STYLE), dict(editor.SHORTS_STYLE, fill="#1A1A1A"), {"fill": "bad", "y": 0.8, "size": 72, "strokeW": 8},
                dict(editor.SHORTS_STYLE, y=0.5), dict(editor.SHORTS_STYLE, y=0.3), None]
        vtops = [None, editor.video_top(editor.BOX_LAYOUT, [], 1920, 1080), 0.2]
        cases, want, k = [], [], 0
        for ti, top in enumerate(tops):
            for text in texts:
                for said in (False, True):
                    k += 1
                    cap, vt, ln = caps[k % len(caps)], vtops[k % len(vtops)], (1.7, 3.2, 30.0)[k % 3]
                    cases.append({"text": text, "top": dict(top, id=f"t{ti}"), "cap": cap, "len": ln, "said": said, "vtop": vt})
                    py = editor.topic_title(text, dict(top, id=f"t{ti}"), cap, ln, said=said, vtop=vt)
                    want.append(None if py is None else {kk: py.get(kk) for kk in ("text", "start", "dur", "style", "plan", "under", "swap")})
            cases.append({"box": True, "text": top["text"], "st": top["style"]})
            want.append(list(editor.text_box(top["text"], top["style"], W, H)))
        lays = [editor.BOX_LAYOUT, dict(editor.BOX_LAYOUT, mode="blur", zoom=1.3, vpos=0.4, cropTop=0.1), {"mode": "fill"}, {}]
        items = [[], [{"track": "V1", "start": 0.0, "fx": {"scale": {"v": 120, "k": [{"t": 0.5, "v": 150}]}, "anchor": {"v": [0.5, 0.3], "k": []}}}],
                 [{"track": "V1", "start": 1.0, "fx": {"scale": {"v": 108, "k": []}}}, {"track": "V1", "start": 2.6, "fx": {"scale": {"v": 160, "k": []}}}],
                 [{"track": "V1", "start": 0.0, "fit": "contain", "fx": {"scale": {"v": 160, "k": []}}}]]
        for lay in lays:
            for its in items:
                for wh in ((1920, 1080), (1080, 1920), (3840, 2160), (None, None)):
                    cases.append({"vt": True, "lay": lay, "items": its, "w": wh[0], "h": wh[1]})
                    want.append(editor.video_top(lay, its, *wh))
        got = run_js(f"""const cases = {json.dumps(cases, ensure_ascii=False)};
out = cases.map(c => c.box ? txtBox(c.text, c.st, 1080, 1920) : c.vt ? videoTop(c.lay, c.items, c.w, c.h)
  : topicTitle(c.text, c.top, c.cap, c.len, c.said, c.vtop));""")
        for c, g, w in zip(cases, got, want):
            if c.get("box"):
                for a_, b_ in zip(g, w):
                    self.assertAlmostEqual(a_, b_, places=6, msg=c)
                continue
            if c.get("vt"):
                if w is None:
                    self.assertIsNone(g, c)
                else:
                    self.assertAlmostEqual(g, w, places=6, msg=c)
                continue
            if w is None:
                self.assertIsNone(g, c)
                continue
            self.assertIsNotNone(g, c)
            g = {k_: g.get(k_) for k_ in ("text", "start", "dur", "style", "plan", "under", "swap")}
            self.assertEqual(g, w, c)
        made = [w for c, w in zip(cases, want) if "top" in c]
        self.assertTrue(any(w is None for w in made) and any(w and w.get("swap") for w in made) and any(w and not w.get("swap") for w in made))


@unittest.skipUnless(shutil.which("node"), "node 가 없어요")
class EditorJsTest(unittest.TestCase):
    """편집실 고치기에서 주제 줄 (editor.html 함수를 node 로 그대로 실행 · 검토: 앞을 자르면 주제 줄이 사라지거나 0.5초만 남고, MSG 쇼츠에 레이아웃 틀을
    씌우면 끝의 '다시 보기 ▶'에 맞춰 큰 줄 위에 오른쪽 정렬로 겹침)."""
    SETUP = """
const pair = (a, b, start) => [{ id: nid(), track: "V1", media: "main", in: a, out: b, start }, { id: nid(), track: "A1", media: "main", in: a, out: b, start }];
const BIG = { weight: "Black", size: 100, fill: "#FFE14D", stroke: "#111111", strokeW: 9, y: 0.22, x: 0.5, align: "center", effect: "pop" };
const rough = swap => ({ format: "shorts", name: "쇼츠 1", layout: { mode: "box", bar: "#000000", zoom: 1, vpos: 0.5 },
  captionStyle: { y: 0.8, size: 72, strokeW: 8, fill: "#FFFFFF", stroke: "#000000" }, items: [...pair(10, 20, 0), ...pair(30, 50, 10)],
  shapes: [], tactics: [], markers: [], trans: [],
  titles: [{ id: "big", text: swap ? "패스\\n두 번째 동작" : "트래핑 꿀팁", start: swap ? 2.5 : 0, dur: swap ? 27.5 : 30, style: { ...BIG } },
           { id: "k", text: "퍼스트 터치 레슨 · 포인트 3", start: 0, dur: 2.5, plan: "topic", under: "big", swap: swap || undefined,
             style: swap ? { ...BIG } : { ...TOPIC_STYLE, y: 0.133 } }] });
const row = () => S.titles.map(t => [t.id, Math.round(t.start * 1000) / 1000, Math.round(t.dur * 1000) / 1000]).sort();
"""

    def test_head_cuts_keep_topic_at_start(self):
        """Q 로 앞 3초 잘라내기 · 첫 대사(0~3초) 잘라내기 = gfxCut(0, 3) · 0~2초 · 1~2초 · 군더더기 정리(tidySeq) · 원본 순서 밖 주제 줄은 예전처럼."""
        got = run_js(self.SETUP + """
out = {};
for (const [a, b] of [[0, 3], [0, 2], [1, 2], [2, 4]]) { S = rough(false); gfxCut(a, b); out[`cut${a}-${b}`] = row(); }
S = rough(false); out.tidy = (r => r.titles.map(t => [t.id, t.start, t.dur]).sort())(tidySeq(S, [{ in: 13, out: 20 }, { in: 30, out: 50 }]));
S = rough(false); S.titles[1].start = 5; gfxCut(4, 6); out.moved = row();
S = rough(true); gfxCut(0, 3); out.swapCut = row();
S = rough(true); out.swapTidy = (r => r.titles.map(t => [t.id, t.start, t.dur]).sort())(tidySeq(S, [{ in: 14, out: 20 }, { in: 30, out: 50 }]));""")
        for key in ("cut0-3", "cut0-2", "cut1-2", "cut2-4"):
            self.assertIn(["k", 0, 2.5], got[key], key)                       # 맨 앞 0~2.5초 그대로 (예전: 없어지거나 0.5·1.5초)
        self.assertEqual(got["cut0-3"], [["big", 0, 27], ["k", 0, 2.5]])
        self.assertEqual(got["tidy"], [["big", 0, 27], ["k", 0, 2.5]])
        self.assertEqual(got["moved"], [["big", 0, 28], ["k", 4, 1.5]])     # 맨 앞이 아닌 주제 줄(옮긴 것)은 다른 글자와 같게
        self.assertEqual(got["swapCut"], [["big", 2.5, 24.5], ["k", 0, 2.5]])   # 바꿔 끼운 큰 제목은 주제 줄 뒤부터 (같은 자리 두 글자 X)
        self.assertEqual(got["swapTidy"], [["big", 2.5, 23.5], ["k", 0, 2.5]])

    def test_delete_swapped_topic_restores_big_title(self):
        got = run_js(self.SETUP + """
S = rough(true); const k = S.titles.find(t => t.id === "k"); S.titles = S.titles.filter(t => t !== k); topicGone(k); out = { swap: row() };
S = rough(false); const k2 = S.titles.find(t => t.id === "k"); S.titles = S.titles.filter(t => t !== k2); topicGone(k2); out.above = row();""")
        self.assertEqual(got["swap"], [["big", 0, 30]])     # 첫머리가 비지 않음
        self.assertEqual(got["above"], [["big", 0, 30]])

    def msg_short(self, under=True):
        return self.SETUP + f"""
const msgS = () => ({{ format: "shorts", name: "MSG 후보 A · 담백 레슨형 · 보통 · 쇼츠", layout: {{ mode: "blur", zoom: 1, vpos: 0.5 }},
  captionStyle: {{ y: 0.8, size: 72, strokeW: 8, fill: "#FFFFFF", stroke: "#000000" }}, items: [...pair(10, 60, 0)], shapes: [], tactics: [], markers: [], trans: [],
  titles: [{{ id: "rep", text: "다시 보기 ▶", start: 41, dur: 1.5, msg: true, style: {{ weight: "Black", size: 58, fill: "#FFFFFF", strokeW: 6, y: 0.3, x: 0.93, align: "right" }} }},
           {{ id: "hk", text: "트래핑 꿀팁", start: 0, dur: 2.5, msg: true, style: {{ weight: "Black", size: 96, fill: "#FFFFFF", stroke: "#111111", strokeW: 8, y: 0.22, x: 0.5, align: "center", font: "Black Han Sans" }} }},
           {{ id: "kt", text: "퍼스트 터치 레슨 | 포인트 3", start: 0, dur: 2.5, plan: "topic", {'under: "hk", ' if under else ''}msg: true,
             style: {{ weight: "Black", size: 64, fill: "#FFE14D", stroke: "#111111", strokeW: 8, y: 0.133, x: 0.5, align: "center", font: "Black Han Sans" }} }}] }});
"""

    def test_templates_on_msg_short_follow_its_main_line(self):
        """MSG 쇼츠: titles 맨 앞이 '다시 보기 ▶'(41초 · 오른쪽 정렬)여도 주제 줄은 자기 큰 줄('트래핑 꿀팁') 위·가운데 (예전 판 under 없음도 같은 때 큰 줄로)."""
        for under in (True, False):
            got = run_js(self.msg_short(under) + """
out = {};
for (const name of Object.keys(TEMPLATES).filter(k => k !== "은평 스타일")) {  // (은평 스타일은 큰 제목 아래 사실 줄 · tests.test_eunpyeong)
  S = msgS(); TEMPLATES[name]();
  const kt = S.titles.find(t => t.id === "kt"), hk = S.titles.find(t => t.id === "hk");
  out[name] = { k: txtBox(kt.text, kt.style, 1080, 1920), h: txtBox(hk.text, hk.style, 1080, 1920), kx: [kt.style.x, kt.style.align], hx: [hk.style.x, hk.style.align],
    cap: capBand(S.captionStyle), swap: !!kt.swap, under: kt.under, font: kt.style.font, hs: [hk.start, hk.dur] };
}""")
            self.assertEqual(len(got), 4)
            for name, r in got.items():
                k, h = r["k"], r["h"]
                self.assertFalse(inter(k, h), (under, name, r))
                self.assertEqual(r["kx"], r["hx"], (under, name))
                self.assertEqual(r["under"], "hk", name)
                self.assertGreaterEqual(k[1], editor.TOPIC_TOP - 1e-6, name)
                self.assertFalse(r["cap"][0] < k[3] + editor.TXT_GAP and k[1] < r["cap"][1] + editor.TXT_GAP, name)
                self.assertEqual(r["font"], "Black Han Sans")
                self.assertEqual(r["hs"], [0, 2.5])

    def test_place_topic_swaps_when_above_and_below_are_taken(self):
        """큰 제목 위에 자리가 없고 아래는 말 자막 → 바꿔 끼우기 (큰 제목은 주제 줄 뒤부터)."""
        got = run_js(self.SETUP + """
S = rough(false); S.captionStyle.y = 0.26; const big = S.titles[0]; big.style.y = 0.15;
const k = S.titles[1]; placeTopic(k, big); out = { swap: !!k.swap, y: k.style.y, size: k.style.size, big: [big.start, big.dur], one: oneLine(k.text, k.style, 1080) };
S = rough(false); S.captionStyle.y = 0.8; S.titles[0].style.y = 0.15; placeTopic(S.titles[1], S.titles[0]);
out.below = { swap: !!S.titles[1].swap, k: txtBox(S.titles[1].text, S.titles[1].style, 1080, 1920), t: txtBox(S.titles[0].text, S.titles[0].style, 1080, 1920) };""")
        self.assertEqual((got["swap"], got["y"], got["big"], got["one"]), (True, 0.15, [2.5, 27.5], True))
        self.assertFalse(got["below"]["swap"])                                   # 아래가 비어 있으면 바로 아래 (큰 제목은 그대로 처음부터)
        self.assertGreaterEqual(got["below"]["k"][1], got["below"]["t"][3] + editor.TXT_GAP - 1e-6)


class SaveReopenExportTest(WorkMixin, unittest.TestCase):
    def test_project_keeps_topic_title_and_ass_has_it(self):
        name = self.fix("MSGRAW01")
        info = {"duration": FIX["MSGRAW01"]["duration"], "width": 1920, "height": 1080, "fps": 30.0}
        (core.VIDEOS / name).write_bytes(b"")
        with mock.patch.object(editor, "media_info", return_value=info):
            proj = editor.load_project(name)
            seq = next(q for q in proj["sequences"] if any(t.get("plan") == "topic" for t in q["titles"]))
            kt = next(t for t in seq["titles"] if t.get("plan") == "topic")
            kt["text"] = "고친 주제 줄"            # 편집실에서 글을 고치고 옮김 → 저장 → 다시 열기
            kt["style"]["y"] = 0.12
            editor.save_project(name, proj, force=True)
            again = editor.load_project(name)
        q2 = next(q for q in again["sequences"] if q["id"] == seq["id"])
        k2 = next(t for t in q2["titles"] if t.get("plan") == "topic")
        self.assertEqual((k2["text"], k2["style"]["y"], k2["start"], k2["dur"]), ("고친 주제 줄", 0.12, 0.0, kt["dur"]))
        ass = editor.build_ass(dict(q2, captions=[], info=info), W, H)
        line = next(x for x in ass.splitlines() if x.startswith("Dialogue") and x.endswith("고친 주제 줄"))
        self.assertIn(rf"\pos({int(W * 0.5)},{int(H * 0.12)})", line)
        self.assertIn("0:00:00.00,0:00:02.50", line)
        self.assertIn(r"\fscx70\fscy70", line)                          # 미리보기와 같은 팝 효과
        style_line = next(x for x in ass.splitlines() if x.startswith(f"Style: T{k2['id']},"))
        self.assertIn(f",{round(k2['style']['size'] * editor.FONT_K)},", style_line)
        q2["titles"] = [t for t in q2["titles"] if t.get("plan") != "topic"]   # 지우기 → 위 큰 제목만 그대로
        self.assertNotIn("고친 주제 줄", editor.build_ass(dict(q2, captions=[], info=info), W, H))


class MsgIntroTest(unittest.TestCase):
    """MSG 쇼츠 첫 2.5초: 큰 줄(이 쇼츠 포인트) + 주제 줄."""

    def test_main_line(self):
        O = msg._shorts_openings
        self.assertEqual(O("퍼스트 터치 레슨", {"hook": "트래핑 꿀팁", "point": {"n": 3, "phrase": "트래핑"}}), [("트래핑 꿀팁", False)])
        # 검토: 순서 말뿐인 '두 번째 동작'은 말 자막을 되풀이 → 요점 말(주제 줄 필요) → 영상 주제 (순서는 주제 줄 '동작 2')
        self.assertEqual(O("패스 앤 무브 드릴", {"hook": "패스\n두 번째 동작", "point": {"n": 2}}), [("패스 앤 무브 드릴", False)])
        self.assertEqual(O("패스 앤 무브 드릴", {"hook": "패스\n두 번째 동작", "point": {"n": 2, "phrase": "리턴 패스"}}),
                         [("리턴 패스", True), ("패스 앤 무브 드릴", False)])
        self.assertEqual(O("", {"hook": "패스\n두 번째 동작", "point": {"n": 2}}), [("패스\n두 번째 동작", False)])
        # 영상 주제와 같은 말뿐인 제목: 요점 말 → 예전 훅 (둘 다 주제 줄이 꼭 있어야) → 못 놓으면 주제를 말하는 그 제목 그대로 (검토)
        self.assertEqual(O("슈팅 챌린지", {"hook": "슈팅 챌린지", "point": {"phrase": "디딤발 방향"}}), [("디딤발 방향", True), (None, True), ("슈팅 챌린지", False)])
        self.assertEqual(O("슈팅 챌린지", {"hook": "슈팅 꿀팁", "point": {}}), [(None, True), ("슈팅 꿀팁", False)])
        self.assertEqual(O("퍼스트 터치 레슨", {"hook": "왜 다들 처치에서 공을 놓칠까요?", "point": {}}), [])   # 문장 제목(헛말 '처치')은 안 씀
        self.assertEqual(O("퍼스트 터치 레슨", {"hook": "이것도 들어갔어요", "point": {"phrase": "디딤발"}}), [("디딤발", True)])
        self.assertEqual(O("2대1 패스", {"hook": "리턴 패스 꿀팁", "point": {"n": 2, "phrase": "리턴 패스"}}), [("리턴 패스 꿀팁", False)])  # 검토 LESSON04
        self.assertEqual(O("퍼스트 터치 레슨", None), [])
        self.assertIsNone(msg._shorts_main("퍼스트 터치 레슨", None))
        for vt in ("패스 앤 무브 드릴", "퍼스트 터치 레슨", "2대1 패스", ""):
            for h in ("패스\n두 번째 동작", "퍼스트 터치\n첫 번째 포인트", "2대1 패스\n세 번째 포인트", "슈팅 챌린지"):
                for t, _ in O(vt, {"hook": h, "point": {"n": 2}}):
                    self.assertFalse(t and re.fullmatch(r"(?:첫|두|세|네|다섯) ?번째 ?\S+", t), (vt, h, t))   # 순서 말뿐인 큰 줄 없음

    def kicker(self, cap_pos="bottom", main="트래핑 꿀팁", zoom=False, preset="예능 MSG형"):
        st = msg.resolve({"kind": "preset", "name": preset})
        st["captions"]["pos"] = cap_pos
        cap_y = msg.caption_y(st, "shorts")
        looks = msg.text_looks(st, "shorts", cap_y)
        B = msg._Build("v.mp4", {"duration": 60.0}, "shorts")
        B.clip(10.0, 40.0, fx={"scale": {"v": 120.0, "k": []}} if zoom else None, main=True)
        hk = B.title(main, 0.0, msg.SHORTS_HOOK_SEC, dict(looks["emphasis"], y=0.22, size=96, fill="#FFFFFF", effect="pop"))
        kt = msg._topic_kicker(B, "퍼스트 터치 레슨 · 포인트 3", hk, st, cap_y)
        return B, hk, kt, st, cap_y

    def test_kicker_above_main_in_safe_area(self):
        for preset in msg.PRESETS:
            B, hk, kt, st, cap_y = self.kicker(preset=preset)
            self.assertIsNotNone(kt, preset)
            kb, mb = msg.text_box(kt["text"], kt["style"], W, H), msg.text_box(hk["text"], hk["style"], W, H)
            self.assertLessEqual(kb[3], mb[1] - editor.TXT_GAP * H + 1.0, preset)
            self.assertGreaterEqual(kb[1], editor.TOPIC_TOP * H - 1.0, preset)
            self.assertTrue(kb[0] >= 0.05 * W and kb[2] <= 0.95 * W, preset)
            self.assertEqual((kt["start"], kt["dur"], kt["plan"]), (0.0, msg.SHORTS_HOOK_SEC, "topic"))
            # 검토: 사이 점('·')이 없는 글꼴(검은고딕·도현)은 주제 줄만 기본 글꼴로 바뀌었음 → 글꼴마다 있는 사이 글자('|')로 · 큰 줄과 같은 글꼴
            sep = "|" if "·" in (msg._NO_GLYPH.get(hk["style"].get("font")) or {}) else "·"
            self.assertEqual(kt["text"], f"퍼스트 터치 레슨 {sep} 포인트 3", preset)
            self.assertEqual(kt["style"].get("font"), hk["style"].get("font"), preset)
            self.assertEqual(kt["under"], hk["id"])
            if kt["style"].get("font"):
                self.assertEqual(msg.glyph_safe(kt["text"], kt["style"]["font"]), kt["text"])
            if not hk["style"].get("bgOn"):
                self.assertEqual(kt["style"]["fill"], st["captions"].get("emphColor") or "#FFE14D")   # 스타일 강조 색

    def test_top_captions_put_kicker_below_main(self):
        B, hk, kt, st, cap_y = self.kicker("top")
        self.assertIsNotNone(kt)
        kb, mb = msg.text_box(kt["text"], kt["style"], W, H), msg.text_box(hk["text"], hk["style"], W, H)
        cb = msg.text_box("가나다라", msg._caption_style(st, "shorts", cap_y), W, H)
        self.assertGreaterEqual(kb[1], mb[3] + editor.TXT_GAP * H - 1.0)
        self.assertFalse(inter(kb, [0, cb[1], W, cb[3]]))
        self.assertLess(kb[3], video_band(msg.seq_layout("shorts"))[0] * H)
        B, hk, kt, st, cap_y = self.kicker("top", zoom=True)     # 첫 장면을 확대했으면 영상 위에 걸칠 수 있어 아래에 안 둠
        if kt is not None:
            kb, mb = msg.text_box(kt["text"], kt["style"], W, H), msg.text_box(hk["text"], hk["style"], W, H)
            self.assertLessEqual(kb[3], mb[1])

    def intro(self, r, vt, cap_pos="bottom", zoom=False, preset="담백 레슨형", caps=(), situ=None, hook="왜 다들 처치에서 공을 놓칠까요?"):
        st = msg.resolve({"kind": "preset", "name": preset})
        st["captions"]["pos"] = cap_pos
        cap_y = msg.caption_y(st, "shorts")
        looks = msg.text_looks(st, "shorts", cap_y)
        B = msg._Build("v.mp4", {"duration": 60.0}, "shorts")
        B.clip(10.0, 40.0, fx={"scale": {"v": 120.0, "k": []}} if zoom else None, main=True)
        if situ:
            tt = B.title(situ, 2.6, 1.0, dict(looks["situ"]))
            B.event("situ", 2.6, situ, "다음 순서로 넘어감", src=12.6, refs={"titles": [tt["id"]]})
        hk, kt, opening = msg._shorts_intro(B, hook, r, vt, True, looks, st, cap_y, "shorts", list(caps))
        return B, hk, kt, opening

    def test_intro_never_leaves_a_main_line_without_its_topic(self):
        """검토: 담백 레슨형 + 위쪽 말 자막(확대한 첫 장면) 등 주제 줄을 못 놓는 스타일 → 주제 낱말을 뺀 큰 줄('디딤발 방향')만 남지 않음."""
        r = {"hook": "슈팅 챌린지", "point": {"phrase": "디딤발 방향"}}
        placed = 0
        for preset in msg.PRESETS:
            for cap_pos, zoom in (("bottom", False), ("top", False), ("top", True)):
                B, hk, kt, opening = self.intro(r, "슈팅 챌린지", cap_pos, zoom, preset)
                said = hk["text"] + " " + (kt["text"] if kt else "")
                self.assertTrue(editor._shares_word("슈팅 챌린지", said), (preset, cap_pos, zoom, hk["text"]))
                self.assertEqual([t["id"] for t in B.titles], [hk["id"]] + ([kt["id"]] if kt else []))   # 못 쓴 큰 줄은 남지 않음
                if kt:
                    placed += 1
                    self.assertEqual((hk["text"], opening), ("디딤발 방향", "디딤발 방향"))
                else:   # 예전 훅도 주제 줄이 있어야 → 주제를 말하는 가편집 제목 그대로
                    self.assertEqual((hk["text"], opening), ("슈팅 챌린지", "슈팅 챌린지"))
        self.assertTrue(0 < placed < 3 * len(msg.PRESETS))

    def test_intro_skips_main_line_that_repeats_caption_and_drops_said_order(self):
        """검토: 큰 줄이 같은 때 말 자막을 그대로 되풀이 · 2.6초에 '동작 2' 상황 자막이 또 뜸."""
        caps = [{"start": 10.0, "end": 12.4, "text": "트래핑 꿀팁 하나 알려 드릴게요."}]
        B, hk, kt, opening = self.intro({"hook": "트래핑 꿀팁", "point": {"n": 3, "phrase": "트래핑"}}, "퍼스트 터치 레슨", caps=caps)
        self.assertEqual((hk["text"], opening), (msg._short_text("왜 다들 처치에서 공을 놓칠까요?", 14), ""))   # 다음 후보(예전 훅)
        B, hk, kt, opening = self.intro({"hook": "패스\n두 번째 동작", "point": {"n": 2, "unit": "동작"}}, "패스 앤 무브 드릴", situ="동작 ②",
                                        caps=[{"start": 10.0, "end": 13.0, "text": "두번째 동작은 패스하고 옆으로 빠지는 거예요."}])
        self.assertEqual((hk["text"], kt["text"], opening), ("패스 앤 무브 드릴", "동작 2", "패스 앤 무브 드릴"))
        self.assertEqual(msg._drop_said_order(B, hk, kt), 1)
        self.assertFalse([e for e in B.events if e["kind"] == "situ"])
        self.assertEqual([t["text"] for t in B.titles], ["패스 앤 무브 드릴", "동작 2"])
        self.assertEqual([msg._ord_num(x) for x in ("두 번째 동작", "동작 ②", "동작 2", "포인트 3", "슈팅 챌린지", "2대1 패스")], [2, 2, 2, 3, None, None])

    def test_two_line_main_moves_down_a_little_or_drops(self):
        B, hk, kt, st, cap_y = self.kicker(main="패스하고 그 자리에 서 있으면 왜 안될까요?")
        if kt is not None:
            kb, mb = msg.text_box(kt["text"], kt["style"], W, H), msg.text_box(hk["text"], hk["style"], W, H)
            self.assertGreaterEqual(kb[1], editor.TOPIC_TOP * H - 1.0)
            self.assertLessEqual(kb[3], mb[1])
            self.assertLess(mb[3], video_band(msg.seq_layout("shorts"))[0] * H)


class MsgShortsBuildTest(unittest.TestCase):
    """시험 원본(make_msg_fixture · 34초)으로 MSG 쇼츠를 모든 기본 스타일 × 양으로 만들어 첫 2.5초를 봄 (설정을 끄면 예전 그대로)."""

    @classmethod
    def setUpClass(cls):
        from make_msg_fixture import MsgWork
        cls.w = MsgWork()
        cls.s = mock.patch.object(style, "STYLES", core.WORK / "styles")
        cls.s.start()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()
        cls.w.close()

    def build(self, levels=("담백", "보통", "듬뿍")):
        out = []
        specs = [{"kind": "preset", "name": n} for n in msg.PRESETS]
        for lv in levels:
            for grp in (specs[:3], specs[3:]):
                out += msg.build_variants(self.w.name, grp, lv, ("shorts",), lambda m: None)["sequences"]
        return out

    def test_opening_tells_topic_without_extra_line(self):
        """시험 원본은 쇼츠 하나가 영상 전체(주제 = 포인트) → 예전 훅 '첫 터치 레슨'이 이미 영상 주제라 주제 줄을 더하지 않음."""
        for q in self.build(("보통",)):
            first = " ".join(t["text"] for t in q["titles"] if t["start"] < 0.05)
            self.assertTrue(editor._shares_word(first, "첫 터치 레슨"), (q["name"], first))

    def test_every_style_and_intensity(self):
        with mock.patch.object(editor, "video_topic", return_value="드리블 기초 레슨"):   # 큰 줄과 다른 영상 주제 → 주제 줄이 꼭 생김
            seqs = self.build()
        self.assertEqual(len(seqs), 3 * len(msg.PRESETS))
        for q in seqs:
            first = [t for t in q["titles"] if t["start"] < 0.05]
            kts = [t for t in first if t.get("plan") == "topic"]
            self.assertEqual(len(kts), 1, (q["name"], [t["text"] for t in first]))
            kt = kts[0]
            main = next(t for t in first if t.get("plan") != "topic" and t["dur"] <= msg.SHORTS_HOOK_SEC + 0.01)
            self.assertFalse(editor._shares_word(kt["text"], main["text"]), (kt["text"], main["text"]))
            self.assertEqual(q["msg"]["topicLine"], kt["text"])
            self.assertEqual(kt["under"], main["id"])                                  # 레이아웃 틀을 바꿀 때 맞출 큰 줄
            self.assertEqual(kt["style"].get("font"), main["style"].get("font"), q["name"])   # 같은 글꼴 (검토: 사이 점 하나로 기본 글꼴)
            self.assertIn(q["msg"]["opening"], ("", main["text"]))
            ev = next(e for e in q["msg"]["events"] if e["kind"] == "hook")
            self.assertEqual(set(ev["refs"]["titles"]), {kt["id"], main["id"]})     # MSG '빼기'는 둘을 함께
            kb, mb = msg.text_box(kt["text"], kt["style"], W, H), msg.text_box(main["text"], main["style"], W, H)
            self.assertFalse(inter(kb, mb), q["name"])
            self.assertGreaterEqual(kb[1], editor.TOPIC_TOP * H - 1.0)
            for t in q["titles"]:   # 첫 2.5초에 같이 뜬 다른 글자와 안 겹침
                if t["id"] not in (kt["id"], main["id"]) and t["start"] < kt["start"] + kt["dur"]:
                    self.assertFalse(inter(kb, msg.text_box(t["text"], t["style"], W, H)), (q["name"], t["text"]))
            self.assertLessEqual(kt["dur"], msg.SHORTS_HOOK_SEC)

    def test_given_window_gets_its_own_point(self):
        """끝까지 자동으로(정해 준 구간 win · 포인트 하나 = 쇼츠 하나 · D-200): 첫머리 큰 줄·주제 줄은 그 쇼츠가 보여 주는 구간의 것 —
        MSG 가 스스로 고르는 구간(_shorts_best · 이 원본은 영상 전체 '퍼스트 터치 꿀팁')의 것이 아님 (합침 검토: 다른 포인트의 주제 줄 ·
        두 쇼츠에 같은 큰 줄). 포인트를 주면(autorun 이 editor.recommend 에서 받아 둔 것) 그것을 씀."""
        wins = [{"start": 8.0, "end": 31.0, "cuts": [{"in": 8.0, "out": 31.0}], "hook": "고개 들기 꿀팁"},   # '자, 첫 번째 포인트는 고개 들기예요.'
                {"start": 0.4, "end": 21.0, "cuts": [{"in": 0.4, "out": 21.0}], "hook": "첫 터치 비밀"}]
        spec = [{"kind": "preset", "name": "예능 MSG형"}]
        own = []
        real_best = msg._shorts_best

        def best(rec, moms):
            own.append(real_best(rec, moms))
            return own[-1]
        with mock.patch.object(msg, "_shorts_best", best):
            msg.build_variants(self.w.name, spec, "보통", ("shorts",), lambda m: None)
        self.assertTrue(own and own[0] and own[0]["hook"] not in [w["hook"] for w in wins])   # MSG 가 고르는 구간은 정해 준 구간과 다름
        seen, real = [], msg._shorts_intro

        def intro(B, hook, short_r, *a):
            seen.append(short_r)
            return real(B, hook, short_r, *a)
        own.clear()
        with mock.patch.object(msg, "_shorts_intro", intro), mock.patch.object(msg, "_shorts_best", best):
            qs = msg.build_variants(self.w.name, spec, "보통", ("shorts",), lambda m: None, windows=wins)["sequences"]
        self.assertEqual(own, [], "정해 준 구간이면 MSG 가 쇼츠 구간을 고르지 않음")
        self.assertEqual({(r["start"], r["end"], r["hook"]) for r in seen}, {(w["start"], w["end"], w["hook"]) for w in wins})
        self.assertEqual({r["start"]: r["point"]["n"] for r in seen}, {8.0: 1, 0.4: None})   # 그 구간의 첫 두 문장에서 셈
        qs = sorted(qs, key=lambda q: q["msgWin"])
        self.assertEqual([q["msgWin"] for q in qs], [0, 1])
        for q, w in zip(qs, wins):
            first = [t for t in q["titles"] if t["start"] < 0.05]
            main = next(t for t in first if t.get("plan") != "topic" and t["dur"] <= msg.SHORTS_HOOK_SEC + 0.01)
            self.assertEqual(main["text"].replace("\n", " "), w["hook"], q["name"])     # 고치기 전: 두 번째 쇼츠 큰 줄이 MSG 구간의 '퍼스트 터치 꿀팁'
            self.assertEqual((q["msg"]["hook"], q["msg"]["opening"]), (w["hook"], ""))
        self.assertEqual(re.sub(r"\s*[|·]\s*", " · ", qs[0]["msg"]["topicLine"]), "첫 터치 레슨 · 포인트 1")  # 고치기 전: '첫 터치 레슨' (영상 전체 구간엔 번호 없음)
        self.assertEqual(qs[1]["msg"]["topicLine"], "")    # 큰 줄 '첫 터치 비밀'이 이미 주제를 말함
        wins[0]["point"] = {"n": 2, "unit": "동작", "phrase": None}
        q = msg.build_variants(self.w.name, spec, "보통", ("shorts",), lambda m: None, windows=wins[:1])["sequences"][0]
        self.assertEqual(re.sub(r"\s*[|·]\s*", " · ", q["msg"]["topicLine"]), "첫 터치 레슨 · 동작 2")
        self.assertIsNone(msg._win_short({"start": 0.0, "end": 9.0, "hook": "x"}, []), "컷이 없으면 앞쪽 정리 구간 (추천 구간 아님)")

    def test_setting_off_keeps_old_hook(self):
        style.set_shorts_topic(False)
        try:
            for q in self.build(("보통",)):
                self.assertFalse([t for t in q["titles"] if t.get("plan") == "topic"])
                self.assertEqual(q["msg"]["topicLine"], "")
                ev = next(e for e in q["msg"]["events"] if e["kind"] == "hook")
                self.assertEqual(ev["why"], "쇼츠 첫 2.5초 훅 자막")
        finally:
            style.set_shorts_topic(True)


class SettingEndpointTest(unittest.TestCase):
    """스타일 화면 설정을 진짜 app.Handler 로 켜고 끄고, 편집실이 누를 때 다시 묻는 GET 이 지금 값을 줌 (검토: 편집실을 연 뒤 끄면 안 따름)."""

    def test_get_follows_set(self):
        from test_windows_compat import Server

        class T(Server):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            self.assertEqual(t.call("/api/style/shorts_topic"), (200, {"on": True}))
            self.assertEqual(t.call("/api/style/shorts_topic", {"on": False}), (200, {"ok": True, "on": False}))
            self.assertEqual(t.call("/api/style/shorts_topic"), (200, {"on": False}))
            self.assertIs(t.call("/api/style/list")[1]["shortsTopic"], False)
        finally:
            t.doCleanups()


if __name__ == "__main__":
    unittest.main()
