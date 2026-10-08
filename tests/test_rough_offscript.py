"""E12 가편집을 그대로 올릴 수 있게 — 저장소 폴더에서 python3 -m unittest tests.test_rough_offscript

- 문장 단위로 나누기 (captions.split_sentences) · 영상 밖 말 찾기 (takes.find_offscript: 촬영 준비 말·촬영 끝 말·구독/홍보 안내)
- editor.recommend: 롱폼 가편집은 첫 인사 앞·마지막 끝인사 뒤를 자르고, 쇼츠는 안내 말까지 빼고, 티저는 안내 말을 고르지 않음 (BR-060)
- 쇼츠 문장 경계: 끝은 문장 끝까지(최대 +5초) 늘리거나 앞 문장 끝에서 자르고, 반응 말 앞에는 그 원인인 시범을 넣음 (BR-061)
- 올리기 키트: 영상에서 말한 레슨 홍보 → 설명 '▶ 레슨 문의' (BR-062)
고정 자료 tests/fixtures/rough_e12.json: v2.9.0 앱 그대로 받아쓴 실제 한국어 받아쓰기(large-v3-turbo) 4개 + 무음·큰 소리 + 정답 대사·시범."""
import json
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
import takes  # noqa: E402
import upload  # noqa: E402

FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "rough_e12.json").read_text(encoding="utf-8"))["videos"]


def S(a, b, t, words=None):
    s = {"start": a, "end": b, "text": t}
    if words is not None:
        s["words"] = words
    return s


def W(*items):
    """('말', 시작, 끝) … → 단어 시각 목록."""
    return [{"w": w, "s": a, "e": b, "p": 0.9} for w, a, b in items]


def text_in(segs, a, b):
    """원본 [a, b] 안에 든 낱말 (단어 가운데 시각 기준)."""
    return " ".join(w["w"] for s in segs for w in s.get("words") or () if a <= (w["s"] + w["e"]) / 2 <= b)


class SplitSentencesTest(unittest.TestCase):
    def test_splits_run_on_segment_by_word_endings(self):
        seg = S(98.72, 115.21, "나이스 이거예요 이거 아 오늘 바람 진짜 많이 부네요 아참 레슨 문의는 인스타그램 dm으로 주시면 돼요",
                W(("나이스", 98.72, 99.4), ("이거예요", 99.5, 101.26), ("이거", 101.26, 101.6), ("아", 101.7, 101.8), ("오늘", 101.9, 102.3),
                  ("바람", 102.4, 102.8), ("진짜", 102.9, 103.3), ("많이", 103.4, 103.8), ("부네요", 103.9, 105.38),
                  ("아참", 107.49, 108.0), ("레슨", 108.1, 108.5), ("문의는", 108.6, 109.1), ("인스타그램", 109.2, 110.0),
                  ("dm으로", 110.1, 110.6), ("주시면", 110.7, 111.2), ("돼요", 111.3, 115.21)))
        out = captions.split_sentences([seg])
        self.assertEqual([s["text"] for s in out], ["나이스 이거예요", "이거 아 오늘 바람 진짜 많이 부네요", "아참 레슨 문의는 인스타그램 dm으로 주시면 돼요"])
        self.assertEqual((out[0]["start"], out[-1]["end"]), (98.72, 115.21))  # 바깥 경계는 원래 구간 그대로
        self.assertEqual([bool(s.get("cont")) for s in out], [False, True, True])  # 같은 구간의 뒷 문장 표시
        self.assertEqual(sum(len(s["words"]) for s in out), 16)

    def test_like_button_is_a_noun(self):
        seg = S(139.08, 142.68, "구독이랑 좋아요 한 번씩 눌러주시고요",
                W(("구독이랑", 139.08, 140.2), ("좋아요", 140.2, 141.0), ("한", 141.0, 141.3), ("번씩", 141.3, 141.7), ("눌러주시고요", 141.7, 142.68)))
        self.assertEqual([s["text"] for s in captions.split_sentences([seg])], ["구독이랑 좋아요 한 번씩 눌러주시고요"])

    def test_single_sentence_and_old_transcripts_unchanged(self):
        one = S(1.0, 3.0, "공을 멈추는 게 아니라 다음 방향으로 보내는 거예요.",
                W(("공을", 1.0, 1.3), ("멈추는", 1.3, 1.6), ("게", 1.6, 1.7), ("아니라", 1.7, 2.0), ("다음", 2.0, 2.2), ("방향으로", 2.2, 2.5),
                  ("보내는", 2.5, 2.7), ("거예요.", 2.7, 3.0)))
        old = S(5.0, 9.0, "구독과 좋아요 부탁드립니다")  # 단어 시각이 없는 짧은 구간 (예전 받아쓰기)
        out = captions.split_sentences([one, old])
        self.assertEqual(out, [one, old])
        self.assertFalse(captions.ends_sentence("필요"))
        self.assertFalse(captions.ends_sentence("아니라"))
        self.assertTrue(captions.ends_sentence("봤죠?"))
        self.assertTrue(captions.ends_sentence("됩니다"))

    def test_dangling_syllable_joins_next_sentence(self):
        # 받아쓰기가 '세'의 시각을 시범 앞으로 당겨 써서 4.9초 쉼으로 떨어짐 → '세 번째 포인트 …'로 (nocond MSGRAW01 실측)
        seg = S(128.45, 143.69, "오늘 진짜 물 좀 마시고 할게요 세 번째 포인트 트래핑 할 때는",
                W(("오늘", 128.45, 128.9), ("진짜", 128.9, 129.4), ("물", 129.4, 129.7), ("좀", 129.7, 130.0), ("마시고", 130.0, 130.8),
                  ("할게요", 130.8, 131.85), ("세", 131.85, 132.93), ("번째", 137.79, 138.3), ("포인트", 138.3, 139.0), ("트래핑", 139.2, 140.0),
                  ("할", 140.0, 140.3), ("때는", 140.3, 143.69)))
        self.assertEqual([s["text"] for s in captions.split_sentences([seg])], ["오늘 진짜 물 좀 마시고 할게요", "세 번째 포인트 트래핑 할 때는"])

    def test_long_segment_without_words_is_guessed(self):
        seg = S(141.82, 158.24, "정리할게요 주고 바로 뛰기 원터치 리턴 이 세가지만 지키면 무조건 됩니다 오늘은 여기까지 다음 영상에서 만나요 감사합니다")
        out = captions.split_sentences([seg])
        self.assertEqual([s["text"] for s in out], ["정리할게요", "주고 바로 뛰기 원터치 리턴 이 세가지만 지키면 무조건 됩니다",
                                                     "오늘은 여기까지 다음 영상에서 만나요", "감사합니다"])
        self.assertTrue(all("words" not in s for s in out))  # 어림으로 나눈 시각은 단어 시각인 척 하지 않음
        self.assertEqual((out[0]["start"], out[-1]["end"]), (141.82, 158.24))


class FindOffscriptTest(unittest.TestCase):
    def kinds(self, sents, dur=None):
        return [(o["kind"], o["text"]) for o in takes.find_offscript(sents, dur)]

    def test_lesson04_real_transcript(self):
        v = FIX["LESSON04"]
        off = takes.find_offscript(captions.split_sentences(v["transcript"]), v["duration"])
        by = {o["kind"]: o for o in off}
        self.assertEqual(sorted(by), ["cta", "post", "pre", "promo"])
        self.assertEqual((by["pre"]["a"], by["pre"]["b"]), (0.0, 10.5))  # 첫 인사 '안녕하세요.'(10.50) 시작까지
        self.assertIn("녹화", by["pre"]["text"])
        self.assertIn("찍히고 있어요", by["pre"]["text"])  # 수강생 대답도 함께
        self.assertTrue(by["post"]["text"].startswith("자 컷 수고했어요"))
        self.assertGreaterEqual(by["post"]["b"], v["duration"])  # 영상 끝까지
        self.assertIn("지금 다음 영상에서 만나요", by["post"]["text"])  # 촬영 뒤 지어낸 끝말도 그 안에
        self.assertLess(by["post"]["a"], 158.6)
        self.assertIn("인스타그램", by["promo"]["text"])
        self.assertIn("주말판 아직 자리 있어요", by["promo"]["text"])  # 바로 옆 '자리 있어요'도 홍보로
        self.assertEqual(by["cta"]["text"], "구독이랑 좋아요 한 번씩 눌러주시고요")

    def test_hook_before_greeting_is_kept(self):
        sents = [S(0.5, 3.0, "녹화 됐어?"), S(3.4, 3.8, "네"), S(4.5, 8.0, "여러분 이 기술 하나면 수비 다 뚫습니다"),
                 S(9.0, 11.0, "안녕하세요 풋살사관학교 최경진입니다"), S(12.0, 20.0, "오늘은 턴 동작을 알려 드릴게요")]
        off = takes.find_offscript(sents)
        self.assertEqual([(o["kind"], o["a"], o["b"]) for o in off], [("pre", 0.0, 4.5)])
        self.assertEqual(self.kinds([S(0.5, 3.0, "여러분 이 기술 하나면 수비 다 뚫습니다"), S(4.0, 6.0, "안녕하세요")]), [])
        # 질문형 훅('왜 안 되죠?')은 '됐어?' 같은 준비 말이 아님 · 그 말뿐인 '준비 됐어?'만
        self.assertEqual(self.kinds([S(0.5, 2.0, "이게 왜 안 되죠?"), S(2.5, 4.0, "안녕하세요")]), [])
        self.assertEqual([k for k, _ in self.kinds([S(0.5, 1.5, "준비 됐어?"), S(1.8, 2.2, "네"), S(3.0, 5.0, "안녕하세요")])], ["pre"])

    def test_no_greeting_needs_setup_words(self):
        sents = [S(0.5, 2.0, "네"), S(2.5, 6.0, "자 오늘은 패스 연습이에요"), S(7.0, 9.0, "공을 멈추지 마세요")]
        self.assertEqual(self.kinds(sents), [])

    def test_thanks_mid_video_and_bonus_after_closing(self):
        sents = [S(1.0, 3.0, "안녕하세요"), S(10.0, 12.0, "감사합니다"), S(20.0, 25.0, "패스는 발 안쪽으로 하세요"),
                 S(30.0, 33.0, "오늘은 여기까지 다음 영상에서 만나요"),
                 S(34.0, 40.0, "그리고 보너스로 컷백 하나만 더 보여 드릴게요"), S(41.0, 60.0, "공을 끌고 와서 뒤로 빼 주는 거예요")]
        self.assertEqual(self.kinds(sents), [])  # 중간의 '감사합니다'는 끝인사가 아니고, 끝인사 뒤 덤 설명('컷백')은 촬영 끝 말이 아님

    def test_short_reply_after_closing(self):
        sents = [S(1.0, 3.0, "안녕하세요"), S(5.0, 30.0, "패스는 발 안쪽으로 하세요"), S(31.0, 33.0, "감사합니다"), S(34.0, 34.6, "네")]
        self.assertEqual(self.kinds(sents), [("post", "네")])

    def test_praise_is_not_cta(self):
        sents = [S(1.0, 3.0, "안녕하세요"), S(5.0, 6.0, "좋아요!"), S(7.0, 9.0, "좋아요 그렇게 하는 거예요"),
                 S(10.0, 12.0, "구독 좋아요 부탁드려요"), S(13.0, 18.0, "자 두 번째 포인트")]
        self.assertEqual(self.kinds(sents), [("cta", "구독 좋아요 부탁드려요")])

    def test_promo_needs_strong_words(self):
        self.assertEqual(self.kinds([S(1.0, 3.0, "안녕하세요"), S(5.0, 8.0, "주말반 친구들이랑 같이 해 볼게요")]), [])
        self.assertEqual(self.kinds([S(1.0, 3.0, "안녕하세요"), S(5.0, 8.0, "수강 문의는 프로필 링크로 주세요"), S(8.5, 10.0, "평일반도 모집 중이에요")]),
                         [("promo", "수강 문의는 프로필 링크로 주세요 평일반도 모집 중이에요")])


class NoCondTranscriptTest(unittest.TestCase):
    """condition_on_previous_text=False 로 받아쓴 같은 소리 (문장부호가 적고 한 구간이 30초 넘음) — 그래도 같은 판단."""

    def test_lesson04_offscript_found(self):
        v = FIX["LESSON04_nocond"]
        self.assertTrue(any(s["end"] - s["start"] > 30 for s in v["transcript"]))
        by = {o["kind"]: o for o in takes.find_offscript(captions.split_sentences(v["transcript"]), v["duration"])}
        self.assertEqual(sorted(by), ["cta", "post", "pre", "promo"])
        self.assertEqual(by["pre"]["b"], 10.42)
        self.assertIn("자 컷 수고했어요", by["post"]["text"])
        self.assertIn("DM", by["promo"]["text"])


class Work(unittest.TestCase):
    name = "20261008_LESSON04_2대1 패스 레슨.mp4"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="E12 가편집 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, key, name=None):
        name = name or self.name
        d = core.adir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(FIX[key]["transcript"], ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps(FIX[key]["analysis"], ensure_ascii=False), encoding="utf-8")
        return name


class RecommendOffscriptTest(Work):
    def test_long_rough_cut_starts_at_greeting_and_ends_at_closing(self):
        self.put("LESSON04")
        segs = FIX["LESSON04"]["transcript"]
        r = editor.recommend(self.name)
        self.assertGreaterEqual(r["tidy"][0]["in"], 10.3)  # '안녕하세요'(10.50) 바로 앞 여유만
        self.assertLessEqual(r["tidy"][-1]["out"], 158.6)  # 마지막 '감사합니다' 뒤 '자 컷 수고했어요'·'네 좋아요' 없음
        kept = " ".join(text_in(segs, c["in"], c["out"]) for c in r["tidy"])
        for gone in ("녹화되고", "빨간불", "찍히고", "수고했어요", "마시고"):
            self.assertNotIn(gone, kept)
        for stay in ("안녕하세요.", "구독이랑", "인스타그램", "만나요"):  # 롱폼 중간 안내 말은 남김 (표시만)
            self.assertIn(stay, kept)
        whys = [j["why"] for j in r["junk_list"]]
        self.assertIn(takes.PRE, whys)
        self.assertIn(takes.POST, whys)
        self.assertEqual(sorted((o["kind"], o["cut"]) for o in r["offscript"]),
                         [("cta", "shorts"), ("post", "long"), ("pre", "long"), ("promo", "shorts")])
        json.dumps(r)

    def test_shorts_drop_offscript_talk(self):
        self.put("LESSON04")
        segs = FIX["LESSON04"]["transcript"]
        r = editor.recommend(self.name)
        self.assertTrue(r["shorts"])
        for s in r["shorts"]:
            txt = " ".join(text_in(segs, c["in"], c["out"]) for c in s["cuts"])
            for gone in ("녹화되고", "구독이랑", "눌러주시고요", "인스타그램", "자리", "수고했어요"):
                self.assertNotIn(gone, txt, s["title"])

    def test_teaser_skips_offscript(self):
        self.put("LESSON04")
        rec = editor.recommend(self.name)
        cta = next(o for o in rec["offscript"] if o["kind"] == "cta")
        promo = next(o for o in rec["offscript"] if o["kind"] == "promo")
        # 큰 소리가 홍보·구독 말 안에만 있어도 티저는 그 말을 고르지 않음
        per = 50
        pk = [0.0] * int(171 * per)
        for o in (cta, promo):
            for t in range(int(o["a"] * per), int(o["b"] * per)):
                pk[t] = 1.0
        items = editor._items_from_cuts(rec["tidy"])
        _, titles = editor._intro_teaser(items, rec, rec["tidy"], 4.0, FIX["LESSON04"]["transcript"], {"peaks": pk, "per_sec": per})
        self.assertTrue(titles)
        v1 = editor._intro_teaser(items, rec, rec["tidy"], 4.0, FIX["LESSON04"]["transcript"], {"peaks": pk, "per_sec": per})[0][0]
        for o in (cta, promo):
            self.assertTrue(v1["out"] <= o["a"] or v1["in"] >= o["b"], (v1["in"], v1["out"], o))

    def test_auto_sequences_long_starts_with_greeting(self):
        self.put("LESSON04")
        seq = editor.auto_sequences(self.name, {"duration": 170.14, "width": 1920, "height": 1080}, kinds=("long",))[0]
        v1 = sorted((it for it in seq["items"] if it["track"] == "V1"), key=lambda it: it["start"])
        self.assertGreaterEqual(v1[0]["in"], 10.3)
        self.assertLessEqual(max(it["out"] for it in v1), 158.6)


class ShortsEdgesTest(Work):
    def last_sentence(self, key, s):
        sents = captions.split_sentences(FIX[key]["transcript"])
        out = s["cuts"][-1]["out"]
        return [x for x in sents if x["start"] < out - 0.05][-1]

    def test_msgraw01_short_ends_on_sentence(self):
        self.put("MSGRAW01", "MSGRAW01.mp4")
        r = editor.recommend("MSGRAW01.mp4")
        for s in r["shorts"]:
            last = self.last_sentence("MSGRAW01", s)
            self.assertTrue(captions.ends_sentence(last["text"].split()[-1]), (s["title"], last["text"]))
        # 예전: 87.54~141.58 '트래핑할 때는 공을 멈추는 게 아니라'에서 끝남 → 이제 '다음 방향으로 보내는 거예요.'(~143.68)까지
        hit = [s for s in r["shorts"] if s["start"] <= 139.38 < s["end"]]
        self.assertTrue(hit)
        self.assertGreaterEqual(hit[0]["end"], 143.6)
        self.assertLessEqual(hit[0]["end"] - 141.58, editor.SHORT_END_MAX)

    def test_nocond_shorts_start_and_end_on_sentences(self):
        self.put("MSGRAW01_nocond", "MSGRAW01.mp4")
        r = editor.recommend("MSGRAW01.mp4")
        sents = captions.split_sentences(FIX["MSGRAW01_nocond"]["transcript"])
        for s in r["shorts"]:
            first = [x for x in sents if x["end"] > s["cuts"][0]["in"] + 0.05][0]
            k = sents.index(first)
            self.assertTrue(k == 0 or captions.ends_sentence(sents[k - 1]["text"].split()[-1]) or editor.FRESH.match(first["text"]), first["text"])
            last = [x for x in sents if x["start"] < s["cuts"][-1]["out"] - 0.05][-1]
            self.assertTrue(captions.ends_sentence(last["text"].split()[-1]), last["text"])
        self.assertTrue(any(s["title"].startswith("세 번째 포인트") for s in r["shorts"]), [s["title"] for s in r["shorts"]])

    def test_msgraw02_reaction_starts_after_its_demo(self):
        self.put("MSGRAW02", "MSGRAW02.mp4")
        r = editor.recommend("MSGRAW02.mp4")
        s = next(s for s in r["shorts"] if s["title"].startswith("아이고"))
        demo_a, demo_b, kicks = FIX["MSGRAW02"]["demos"][1]  # 정답 실패 시범 53.97~60.97 · 공 소리 56.63·59.17
        self.assertLessEqual(s["start"], demo_a + 0.5)
        self.assertGreaterEqual(s["start"], 53.34)  # 앞 설명 문장 끝(53.34) 뒤 — 시범부터
        c0 = s["cuts"][0]
        self.assertLessEqual(c0["in"], kicks[0])
        self.assertGreaterEqual(c0["out"], 61.21)  # 시범 → 반응 말이 한 컷으로 이어짐

    def test_reaction_inside_short_keeps_its_demo(self):
        self.put("MSGRAW03", "MSGRAW03.mp4")
        r = editor.recommend("MSGRAW03.mp4")
        s = r["shorts"][0]
        cov = lambda t: any(c["in"] <= t <= c["out"] for c in s["cuts"])  # noqa: E731
        for a, b, kicks in FIX["MSGRAW03"]["demos"]:  # 쇼츠 안에서 '들어갔어요'·'빗나갔어요' 앞 슛 장면(공 소리)이 남음
            if s["start"] <= a and b <= s["end"]:
                for k in kicks:
                    self.assertTrue(cov(k), (k, s["cuts"]))

    def test_edges_unit(self):
        segs = [S(0.0, 2.0, "자 첫 번째 포인트는 공을 멈추는 게"), S(2.1, 4.0, "아니라 다음 방향으로 보내는 거예요."), S(4.5, 30.0, "공을 멈추려고 하면 튀어나가요."),
                S(30.5, 33.0, "그래서 발을"), S(40.0, 42.0, "뒤로 빼 주세요.")]
        self.assertEqual(editor._short_edges(segs, set(), 0, 0)[:2], (0, 1))  # 다음 문장 끝까지 늘림
        self.assertEqual(editor._short_edges(segs, set(), 0, 3)[:2], (0, 2))  # 다음 말이 7초 뒤 → 앞 문장 끝에서 자름
        self.assertEqual(editor._short_edges(segs, {1}, 0, 0)[:2], (0, 0))  # 뺄 문장은 넘어가지 않음 (늘릴 곳이 없으면 그대로)
        quick = [S(0.0, 3.0, "패스하고 바로 뛰세요."), S(3.2, 4.0, "나이스!"), S(4.5, 9.0, "이렇게 하는 거예요.")]
        self.assertEqual(editor._short_edges(quick, set(), 1, 2), (2, 2, None))  # 앞에 시범이 없는 짧은 반응 줄은 뺌
        demo = [S(0.0, 3.0, "한번 보여 드릴게요."), S(9.0, 10.0, "봤죠?"), S(10.5, 14.0, "주고 바로 뛰니까 못 따라와요.")]
        self.assertEqual(editor._short_edges(demo, set(), 1, 2), (1, 2, 3.1))  # 큰 소리가 없어도 3초 넘게 빈 곳은 시범
        self.assertEqual(editor._short_edges(demo, set(), 1, 2, peaks=[5.0])[2], 3.1)


class KitPromoTest(Work):
    def test_promo_lines_from_lesson04(self):
        self.assertEqual(upload.promo_lines(FIX["LESSON04"]["transcript"]), ["레슨 문의는 인스타그램 DM으로 주시면 돼요.", "주말판 아직 자리 있어요."])
        self.assertEqual(upload.promo_lines(FIX["MSGRAW02"]["transcript"]), [])

    def test_add_promo_to_templates(self):
        lines = ["레슨 문의는 인스타그램 DM으로 주시면 돼요.", "주말반 아직 자리 있어요."]
        d = upload.description(["“훅”", "둘째 줄"], [], ["#풋살"], "제목", ["패스"], template=upload.DEFAULT_TEMPLATE)
        self.assertIn(upload.PROMO_SLOT, d)
        out = upload.add_promo(d, lines, [])
        self.assertIn("▶ 레슨 문의\n레슨 문의는 인스타그램 DM으로 주시면 돼요.\n주말반 아직 자리 있어요.", out)
        self.assertLess(out.index("▶ 출연"), out.index("▶ 레슨 문의"))
        self.assertNotIn("{", upload.add_promo(d, [], []))  # 말하지 않았으면 자리만 지움
        old = "{훅}\n\n▶ 출연\n최경진 감독 (풋살사관학교)\n\n{챕터}\n\n{해시태그}\n"  # 예전에 만들어 둔 설명 틀
        chs = [{"t": 0, "time": "00:00", "title": "인트로"}, {"t": 60, "time": "01:00", "title": "첫 번째"}, {"t": 120, "time": "02:00", "title": "두 번째"}]
        notes = []
        out = upload.add_promo(upload.description(["훅"], chs, ["#풋살"], template=old), lines, notes)
        self.assertLess(out.index("▶ 레슨 문의"), out.index("▶ 목차"))
        self.assertTrue(notes)

    def test_build_kit_puts_promo_in_description(self):
        name = self.put("LESSON04")
        r = core.run([core.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=10:d=3",
                      "-f", "lavfi", "-i", "sine=f=440:sample_rate=16000:d=3", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                      str(core.VIDEOS / name)])
        self.assertEqual(r.returncode, 0, r.stderr)
        kit = upload.build_kit(name, save=False)
        self.assertIn("▶ 레슨 문의\n레슨 문의는 인스타그램 DM으로 주시면 돼요.", kit["description"])
        self.assertEqual(kit["promo"], ["레슨 문의는 인스타그램 DM으로 주시면 돼요.", "주말판 아직 자리 있어요."])


if __name__ == "__main__":
    unittest.main()
