"""쇼츠 첫머리 주제 줄 (BR-120 · D-220) — 저장소 폴더에서 python3 -m unittest tests.test_shorts_topic

- 주제 정하기: 영상 주제(영상 이름 · 용어 사전 말) + 이 쇼츠 포인트(순서 말 · 'X이 핵심' · 소개한 용어) → 위 큰 줄에 없는 것만 한 줄
  (좋은 주제 · 주제 없음 · 받아쓰기 헛말이 든 고정 자료 — 헛말·조각은 안 씀)
- 자리: 위 큰 제목(BR-093) 바로 위 · 위쪽 안전 영역 · 말 자막·영상(얼굴) 띠와 안 겹침 · 처음 2.5초 · 길이는 그대로
- 편집실: 미리보기(editor.html topicTitle)와 서버(editor.topic_title)가 같은 자리 · 저장/다시 열기 · 내보내기 ASS 에 그대로
- MSG 쇼츠: 모든 스타일·양에서 큰 줄(포인트) + 주제 줄 · 설정을 끄면 예전 훅 그대로
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
         "MSGRAW03": "20261008_MSGRAW03_슈팅 챌린지.mp4", "LESSON04": "20261008_LESSON04_촬영본 0912.mp4", "MSGRAW01_nocond": "v1n.mp4"}
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
        self.assertEqual(P("패스 앤 무브 드릴", {"n": 2, "unit": "동작"}, "패스\n두 번째 동작"), "")
        self.assertEqual(P("패스 앤 무브 드릴", {"n": 2, "unit": "동작"}, "두 번째 동작"), "패스 앤 무브 드릴")   # 번호는 큰 줄에 이미 있음
        self.assertEqual(P("슈팅 챌린지", {}, "슛!"), "")                     # '슛' = '슈팅'
        self.assertEqual(P("", {"n": 3, "unit": "포인트"}, "트래핑 꿀팁"), "")  # 번호 하나는 뜻이 없음
        self.assertEqual(P("퍼스트 터치 레슨", {"n": 1, "unit": "포인트"}, "퍼스트 터치\n첫 번째 포인트"), "")
        self.assertEqual(P("첫 터치 레슨", {"n": 1, "unit": "포인트"}, "첫 터치 레슨"), "포인트 1")   # 큰 줄이 주제를 말함 → 순서만
        self.assertEqual(P("첫 터치 레슨", {"n": 1, "phrase": "디딤발"}, "첫 터치 레슨"), "디딤발")
        self.assertEqual(P("퍼스트 터치 레슨", {"n": 2, "unit": "포인트"}, "디딤발 포인트!"), "퍼스트 터치 레슨")   # '포인트'가 큰 줄에 있음

    def test_one_phone_line(self):
        out = editor.topic_line("퍼스트 터치 레슨 완전 정복", {"phrase": "디딤발 방향"}, "과연 될까요?")
        self.assertEqual(out, "퍼스트 터치 레슨 완전 정복")      # 둘 다 넣으면 14자를 넘음 → 영상 주제만
        self.assertLessEqual(len(editor._norm(out.replace("·", ""))), editor.TOPIC_MAX)
        self.assertEqual(editor.topic_line("가" * 15, {}, "디딤발 꿀팁"), "")

    def test_shares_word(self):
        self.assertTrue(editor._shares_word("패스 앤 무브 드릴", "패스하고 그 자리에"))
        self.assertTrue(editor._shares_word("퍼스트 터치 레슨", "퍼스트 터치\n첫 번째 포인트"))
        self.assertFalse(editor._shares_word("퍼스트 터치 레슨", "디딤발 꿀팁"))


class RecommendTopicTest(WorkMixin, unittest.TestCase):
    def test_fixture_shorts(self):
        got = {}
        for key in ("MSGRAW01", "MSGRAW02", "MSGRAW03", "LESSON04", "MSGRAW01_nocond"):
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
        self.assertEqual(got[("MSGRAW01", "트래핑 꿀팁")], "퍼스트 터치 레슨 · 포인트 3")
        self.assertEqual(got[("MSGRAW01_nocond", "디딤발 꿀팁")], "퍼스트 터치 · 포인트 2")
        self.assertEqual(got[("MSGRAW01", "퍼스트 터치\n첫 번째 포인트")], "")   # 위 큰 제목이 이미 주제·순서를 말함
        self.assertEqual(got[("MSGRAW02", "패스\n두 번째 동작")], "")
        self.assertIn(("MSGRAW01", "퍼스트 터치\n두 번째 포인트"), got)          # '디딤밤'(헛말) → 포인트 말 없음
        self.assertEqual(got[("MSGRAW01", "퍼스트 터치\n두 번째 포인트")], "")

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
        self.assertEqual(kt["start"], 0.0)
        self.assertLessEqual(kt["dur"], editor.TOPIC_SEC)
        self.assertGreaterEqual(kt["style"]["size"], min(editor.TOPIC_SIZES))
        kb = editor.text_box(kt["text"], kt["style"], W, H)
        tb = editor.text_box(top["text"], top["style"], W, H)
        self.assertLessEqual(kb[3], tb[1] - editor.TXT_GAP + 1e-6, "위 큰 제목 바로 위 · 틈")
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

    def test_no_room_or_two_line_top_title(self):
        top = {"id": "a", "text": "퍼스트 터치\n첫 번째 포인트", "start": 0, "dur": 30, "style": dict(editor.TITLE_STYLE)}
        self.assertIsNone(editor.topic_title("퍼스트 터치 레슨", top))
        long_q = {"id": "a", "text": "패스하고 그 자리에 서 있으면 왜 안될까요?", "start": 0, "dur": 30, "style": dict(editor.TITLE_STYLE)}
        self.assertIsNone(editor.topic_title("패스 앤 무브 드릴", long_q))
        high = {"id": "a", "text": "디딤발 꿀팁", "start": 0, "dur": 30, "style": dict(editor.TITLE_STYLE, y=0.12)}
        self.assertIsNone(editor.topic_title("퍼스트 터치 레슨 · 포인트 2", high))   # 위에 자리 없음 → 위 큰 제목만
        ok = editor.topic_title("퍼스트 터치 레슨 · 포인트 2", dict(high, style=dict(editor.TITLE_STYLE)), length=1.2)
        self.assertEqual((ok["dur"], ok["plan"], ok["style"]["size"]), (1.2, "topic", 72))

    def test_long_topic_uses_smaller_size(self):
        top = {"id": "a", "text": "디딤발 꿀팁", "start": 0, "dur": 30, "style": dict(editor.TITLE_STYLE)}
        kt = editor.topic_title("가나다라마바사아자차카타파하", top)
        self.assertEqual(kt["style"]["size"], 64)
        self.assertIsNone(editor.topic_title("가나다라마바사아자차카타파하가나다", top))


@unittest.skipUnless(shutil.which("node"), "node 가 없어요")
class PreviewPairTest(unittest.TestCase):
    """editor.html topicTitle·txtBox 를 node 로 그대로 실행 → editor.py topic_title·text_box 와 같은 값 (미리보기 = 내보내기 = 서버)."""

    def run_js(self, cases):
        src = (Path(__file__).resolve().parents[1] / "editor.html").read_text(encoding="utf-8")
        parts = []
        i = src.index("const emOf =")
        parts.append(src[i:src.index("\n", i)])
        i = src.index("const TOPIC = {")
        parts.append(src[i:src.index("function placeTopic(", i)])
        prog = "let N = 0; const nid = () => 'n' + (++N);\n" + "\n".join(parts) + f"""
const cases = {json.dumps(cases, ensure_ascii=False)};
process.stdout.write(JSON.stringify(cases.map(c => c.box ? txtBox(c.text, c.st, 1080, 1920) : topicTitle(c.text, c.top, c.cap, c.len))));"""
        r = subprocess.run([shutil.which("node"), "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_same_as_server(self):
        T = dict(editor.TITLE_STYLE)
        tops = [{"text": "디딤발 꿀팁", "style": T}, {"text": "트래핑 꿀팁", "style": dict(T, x=0.3, align="left")},
                {"text": "Shooting 꿀팁!", "style": dict(T, size=90, strokeW=4, weight="Bold")}, {"text": "두 줄\n제목", "style": T},
                {"text": "디딤발 꿀팁", "style": dict(T, y=0.12)}, {"text": "디딤발", "style": dict(T, y=0.3, align="right", x=0.9)}]
        texts = ["퍼스트 터치 레슨 · 포인트 2", "디딤발 방향", "가나다라마바사아자차카타파하", "2대1 패스 ABC xyz"]
        caps = [dict(editor.SHORTS_STYLE), dict(editor.SHORTS_STYLE, fill="#1A1A1A"), {"fill": "bad"}]
        cases, want = [], []
        for top in tops:
            for k, text in enumerate(texts):
                cap = caps[k % len(caps)]
                cases.append({"text": text, "top": top, "cap": cap, "len": 1.7 + k})
                py = editor.topic_title(text, dict(top, id="t"), cap, 1.7 + k)
                want.append(None if py is None else {kk: py[kk] for kk in ("text", "start", "dur", "style", "plan")})
            cases.append({"box": True, "text": top["text"], "st": top["style"]})
            want.append(list(editor.text_box(top["text"], top["style"], W, H)))
        got = self.run_js(cases)
        for c, g, w in zip(cases, got, want):
            if c.get("box"):
                for a, b in zip(g, w):
                    self.assertAlmostEqual(a, b, places=6, msg=c)
                continue
            if w is None:
                self.assertIsNone(g, c)
                continue
            self.assertIsNotNone(g, c)
            g = {k: g[k] for k in ("text", "start", "dur", "style", "plan")}
            self.assertEqual(g, w, c)
        self.assertTrue(any(w is not None for w in want) and any(w is None for w in want))


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
        M = msg._shorts_main
        self.assertEqual(M("퍼스트 터치 레슨", {"hook": "트래핑 꿀팁", "point": {"n": 3, "phrase": "트래핑"}}), "트래핑 꿀팁")
        self.assertEqual(M("패스 앤 무브 드릴", {"hook": "패스\n두 번째 동작", "point": {"n": 2}}), "두 번째 동작")
        self.assertEqual(M("", {"hook": "패스\n두 번째 동작", "point": {"n": 2}}), "패스\n두 번째 동작")
        self.assertEqual(M("슈팅 챌린지", {"hook": "슈팅 챌린지", "point": {"phrase": "디딤발 방향"}}), "디딤발 방향")
        self.assertIsNone(M("슈팅 챌린지", {"hook": "슈팅 꿀팁", "point": {}}))      # 영상 주제뿐 → 예전 훅 + 주제 줄
        self.assertIsNone(M("퍼스트 터치 레슨", {"hook": "왜 다들 처치에서 공을 놓칠까요?", "point": {}}))  # 문장 제목(헛말 '처치')은 안 씀
        self.assertEqual(M("퍼스트 터치 레슨", {"hook": "이것도 들어갔어요", "point": {"phrase": "디딤발"}}), "디딤발")
        self.assertIsNone(M("퍼스트 터치 레슨", None))

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
            self.assertEqual(kt["text"], "퍼스트 터치 레슨 · 포인트 3")             # 글꼴에 '·' 가 없으면 기본 글꼴 (글자는 그대로)
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
            ev = next(e for e in q["msg"]["events"] if e["kind"] == "hook")
            self.assertEqual(set(ev["refs"]["titles"]), {kt["id"], main["id"]})     # MSG '빼기'는 둘을 함께
            kb, mb = msg.text_box(kt["text"], kt["style"], W, H), msg.text_box(main["text"], main["style"], W, H)
            self.assertFalse(inter(kb, mb), q["name"])
            self.assertGreaterEqual(kb[1], editor.TOPIC_TOP * H - 1.0)
            for t in q["titles"]:   # 첫 2.5초에 같이 뜬 다른 글자와 안 겹침
                if t["id"] not in (kt["id"], main["id"]) and t["start"] < kt["start"] + kt["dur"]:
                    self.assertFalse(inter(kb, msg.text_box(t["text"], t["style"], W, H)), (q["name"], t["text"]))
            self.assertLessEqual(kt["dur"], msg.SHORTS_HOOK_SEC)

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


if __name__ == "__main__":
    unittest.main()
