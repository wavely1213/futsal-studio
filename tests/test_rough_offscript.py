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
        self.assertEqual((by["pre"]["a"], by["pre"]["b"]), (0.0, 7.94))  # 준비 말 끝('네, 찍히고 있어요.' 7.64 + 0.3)까지 — 그 뒤 빈 곳은 가편집이 판단
        self.assertIn("녹화", by["pre"]["text"])
        self.assertIn("찍히고 있어요", by["pre"]["text"])  # 수강생 대답도 함께
        self.assertTrue(by["post"]["text"].startswith("자 컷 수고했어요"))
        self.assertGreaterEqual(by["post"]["b"], v["duration"])  # 영상 끝까지
        self.assertIn("지금 다음 영상에서 만나요", by["post"]["text"])  # 촬영 뒤 지어낸 끝말도 그 안에
        self.assertLessEqual(by["post"]["a"], 159.05)  # 끝인사 '감사합니다'(받아쓰기 ~158.50) 뒤 0.5초 · 실제 '자, 컷!'(160.47) 앞
        self.assertIn("인스타그램", by["promo"]["text"])
        self.assertIn("주말판 아직 자리 있어요", by["promo"]["text"])  # 바로 옆 '자리 있어요'도 홍보로
        self.assertEqual(by["cta"]["text"], "구독이랑 좋아요 한 번씩 눌러주시고요")

    def test_hook_before_greeting_is_kept(self):
        sents = [S(0.5, 3.0, "녹화 됐어?"), S(3.4, 3.8, "네"), S(4.5, 8.0, "여러분 이 기술 하나면 수비 다 뚫습니다"),
                 S(9.0, 11.0, "안녕하세요 풋살사관학교 최경진입니다"), S(12.0, 20.0, "오늘은 턴 동작을 알려 드릴게요")]
        off = takes.find_offscript(sents)
        self.assertEqual([(o["kind"], o["a"], o["b"]) for o in off], [("pre", 0.0, 4.1)])
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
        self.assertEqual(by["pre"]["b"], 7.96)
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
        self.assertLessEqual(r["tidy"][-1]["out"], 159.05)  # 마지막 '감사합니다' 뒤 '자 컷 수고했어요'(실제 160.47~)·'네 좋아요' 없음
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
        self.assertLessEqual(max(it["out"] for it in v1), 159.05)


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
        # 그 문장에서 끝나는 후보는 '다음 방향으로 보내는 거예요.'(143.68)까지 5초 안에서 늘림
        sents = captions.merge_ghosts(captions.split_sentences(FIX["MSGRAW01"]["transcript"], guess=False))
        k = next(x for x, t in enumerate(sents) if t["start"] == 139.38)
        i, j, _ = editor._short_edges(sents, set(), k - 10, k)
        self.assertEqual(sents[j]["end"], 143.68)
        self.assertLessEqual(sents[j]["end"] - 141.58, editor.SHORT_END_MAX)

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
        s = next(s for s in r["shorts"] if s["start"] <= 61.21 < s["end"])  # '아이고, 공이 조금 뒤로 갔네요.'로 여는 쇼츠
        demo_a, demo_b, kicks = FIX["MSGRAW02"]["demos"][1]  # 정답 실패 시범 53.97~60.97 · 공 소리 56.63·59.17
        self.assertLessEqual(s["start"], kicks[0] - 1.0)  # 공 소리(봉우리 56) 1초 앞부터 — 실패 장면이 반응 말 앞에
        # E2(BR-092): 그 포인트의 첫 문장('두 번째 동작은 …' 48.12)부터 시작할 수 있음 — 아니면 앞 설명 문장 끝(53.34) 뒤 시범부터
        self.assertTrue(s["start"] >= 53.34 or abs(s["start"] - 48.12) < 0.05, s["start"])
        c = next(c for c in s["cuts"] if c["in"] <= kicks[0] <= c["out"])
        self.assertGreaterEqual(c["out"], 61.21)  # 시범 → 반응 말이 한 컷으로 이어짐

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
        self.assertEqual(editor._short_edges(demo, set(), 1, 2), (1, 2, (3.1, 9.0)))  # 큰 소리가 없어도 3~15초 빈 곳은 시범 (확실한 반응 말)
        self.assertEqual(editor._short_edges(demo, set(), 1, 2, peaks=[5.0])[2], (4.0, 9.0))  # 공 소리 1초 앞부터 (빈 곳 앞쪽 1초는 뺌)
        quiet = [{"start": 3.0, "end": 9.0}]  # 그 빈 곳이 조용하면(무음) 시범이 아님
        self.assertEqual(editor._short_edges(demo, set(), 1, 2, silences=quiet), (2, 2, None))


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


# ---------- 검토 고침 (E12 2차): 실제 같은 한국어 레슨 대사로 만든 경우 ----------

def lines(*rows):
    """(시작, 끝, 대사) … → 문장 구간 (단어 시각은 글자 수대로 나눔)."""
    out = []
    for a, b, t in rows:
        toks = t.split()
        n = sum(len(x) for x in toks)
        ws, x = [], a
        for w in toks:
            y = x + (b - a) * len(w) / n
            ws.append({"w": w, "s": round(x, 2), "e": round(y, 2), "p": 0.9})
            x = y
        out.append(S(a, b, t, ws))
    return out


TWO_SIGNOFFS = [(0.5, 4.0, "안녕하세요, 풋살사관학교 최경진입니다."), (4.5, 9.0, "오늘은 수비를 벗겨내는 컷백 드리블을 알려 드릴게요."),
                (10, 15, "자, 첫 번째 포인트는 공을 몸 앞에 두는 거예요."), (15.5, 20, "공이 몸 뒤에 있으면 방향을 바꿀 수가 없어요."),
                (20.5, 23, "한번 보여 드릴게요."), (30.5, 34, "봤죠? 공이 항상 앞에 있어요."), (35, 40, "두 번째 포인트는 디딤발 위치예요."),
                (40.5, 46, "디딤발을 공 옆에 딱 붙여야 바로 꺾을 수 있어요."), (46.5, 52, "발목에 힘을 주고 안쪽으로 끌어 주세요."),
                (52.5, 55, "다시 한번 보여 드릴게요."), (62.5, 65, "나이스! 이거예요."), (66, 72, "세 번째 포인트는 꺾고 나서 바로 속도를 올리는 거예요."),
                (72.5, 78, "꺾기만 하고 멈추면 수비가 다시 따라와요."), (78.5, 84, "이 세 가지만 기억하면 컷백 무조건 됩니다."),
                (85, 89, "오늘은 여기까지입니다. 감사합니다."), (91, 94, "아, 그리고 하나 더 알려 드릴게요."), (94.5, 100, "컷 백 할 때 시선은 반대쪽을 보세요."),
                (100.5, 106, "그러면 수비가 시선 쪽으로 먼저 움직여요."), (106.5, 110, "이게 진짜 마지막 꿀팁이에요."), (111, 114, "진짜 끝! 다음 영상에서 만나요."),
                (116, 119, "자 컷, 수고했어요."), (119.5, 121, "네, 수고하셨습니다.")]


class PostRollReviewTest(unittest.TestCase):
    """촬영 끝 말은 끝에서 거꾸로: 마지막 내용 문장 뒤 마무리 묶음에서 첫 촬영 끝 말 앞의 마지막 끝인사 뒤만 (BR-060)."""

    def post(self, rows):
        return [(o["a"], o["text"]) for o in takes.find_offscript(captions.split_sentences(lines(*rows))) if o["kind"] == "post"]

    def test_two_signoffs_keep_the_bonus(self):
        post = self.post(TWO_SIGNOFFS)
        self.assertEqual([t for _, t in post], ["자 컷, 수고했어요. 네, 수고하셨습니다."])  # 첫 끝인사 뒤 덤 꿀팁·두 번째 끝인사는 그대로
        self.assertGreaterEqual(post[0][0], 114.0)

    def test_helper_thanks_after_60_percent(self):
        rows = [(0.5, 4, "안녕하세요, 풋살사관학교입니다."), (4.5, 9, "오늘은 원터치 패스를 연습해 볼게요."),
                (10, 15, "원터치 패스는 공이 오기 전에 몸을 열어 두는 게 핵심이에요."), (21.5, 27, "수강생 민수 씨랑 같이 해 볼게요."),
                (35.5, 39, "좋습니다. 이렇게 하는 거예요."), (40, 46, "두 번째는 받는 순간 무릎을 살짝 굽히는 거예요."),
                (52.5, 58, "세 번째는 시선이에요. 공만 보지 말고 동료를 보세요."), (64.5, 69, "오늘 시범은 민수 씨가 도와주셨어요."),
                (69.5, 72, "도와주셔서 감사합니다."), (73, 79, "자, 이제 세 가지를 한 번에 해 볼게요."), (79.5, 85, "몸 열고, 무릎 굽히고, 고개 들고."),
                (93.5, 97, "완벽해요. 이게 원터치 패스예요."), (98, 104, "이 세 가지만 기억하시면 경기에서 바로 쓸 수 있어요."),
                (105, 108, "감사합니다."), (110, 111.5, "컷!")]
        self.assertEqual([t for _, t in self.post(rows)], ["컷!"])
        # 도움 준 사람에게 한 '감사합니다' 뒤 '준비 운동은 끝났으니까' · 학생에게 '수고했어요'가 있어도 레슨은 그대로 (끝인사가 하나뿐)
        rows2 = [(1.0, 4.0, "안녕하세요 풋살사관학교 최경진입니다."), (4.5, 8.5, "오늘은 인사이드 패스를 정확하게 차는 법을 알려 드릴게요."),
                 (9.0, 40.0, "첫 번째 포인트는 디딤발 방향이에요."), (55.0, 57.5, "시범 도와준 민수 씨 감사합니다."),
                 (58.0, 62.5, "자 이제 기본 동작은 끝났으니까 실전처럼 움직이면서 해 볼게요."), (63.0, 66.0, "민수 씨 수고했어요 패스하고 바로 앞으로 뛰어 나가세요."),
                 (71.0, 75.0, "좋아요 이렇게 움직이면서도 디딤발은 똑같아요."), (75.5, 78.0, "마지막으로 한 번 더 정리할게요."),
                 (78.5, 82.0, "이 세 가지만 지키면 패스가 확 달라져요."), (82.5, 85.5, "꼭 연습해 보세요. 감사합니다.")]
        self.assertEqual(self.post(rows2), [])

    def test_lesson_words_are_not_postroll(self):
        base = [(0.5, 4, "안녕하세요, 풋살사관학교입니다."), (5, 40, "오늘은 컷 동작을 알려 드릴게요."), (41, 45, "오늘은 여기까지입니다. 감사합니다.")]
        for tail in ("컷 백 할 때 시선은 반대쪽을 보세요.", "인사이드 컷으로 방향을 바꿔요.", "컷 드리블은 무게중심이 낮아야 해요.",
                     "끝났다고 멈추지 마세요.", "한 번 더 찍어 차세요."):
            self.assertEqual(self.post(base + [(46, 50, tail), (50.5, 54, "이게 진짜 마지막 꿀팁이에요.")]), [], tail)
        # '수고하셨습니다 여러분' 뒤에 끝인사가 또 오면 영상 끝인사 (약한 말은 자르지 않음)
        self.assertEqual(self.post(base[:2] + [(41, 44, "오늘은 여기까지입니다."), (44.5, 47, "수고하셨습니다 여러분."), (47.5, 50, "다음 영상에서 만나요.")]), [])
        # 외친 '컷'은 짧은 줄이거나 '컷'으로 시작할 때 (부호 없는 받아쓰기도)
        self.assertEqual([t for _, t in self.post(base + [(46, 48, "자 컷 수고했어요"), (48.5, 49, "네")])], ["자 컷 수고했어요 네"])

    def test_closing_tail_is_kept(self):
        # 끝인사 뒤 0.5초까지 남김 (받아쓰기 낱말 끝이 소리보다 이름 · 다음 말이 그 안에 끝나면 거기까지)
        post = self.post([(0.5, 4, "안녕하세요"), (5, 40, "오늘은 패스 연습이에요."), (41, 43.0, "감사합니다."), (45, 46, "컷!")])
        self.assertEqual(post[0][0], 43.5)
        post = self.post([(0.5, 4, "안녕하세요"), (5, 40, "오늘은 패스 연습이에요."), (41, 43.0, "감사합니다."), (43.0, 43.3, "컷!")])
        self.assertEqual(post[0][0], 43.3)


class PreRollReviewTest(unittest.TestCase):
    def kinds(self, rows):
        return [o["kind"] for o in takes.find_offscript(captions.split_sentences(lines(*rows)))]

    def test_hook_with_football_words_is_kept(self):
        greet = [(9, 12, "안녕하세요, 풋살사관학교입니다."), (12.5, 17, "오늘은 페이크 슛을 알려 드릴게요.")]
        for hook in ("페이크 액션 하나로 수비가 완전히 무너집니다.", "이렇게 차면 슛 들어갑니다.", "슛 들어가는 타이밍만 알면 돼요.",
                     "공이 찍히는 각도가 중요해요.", "하나 둘 셋 하면 뛰어요.", "준비 됐나요? 오늘 진짜 쉬워요."):
            self.assertEqual(self.kinds([(0.5, 4.5, hook)] + greet), [], hook)
        self.assertEqual(self.kinds([(0.5, 4.5, "페이크 액션 하나로 수비가 완전히 무너집니다."), (5, 8, "이렇게 차면 슛 들어갑니다.")] + greet), [])
        # 짧은 촬영 말은 그대로 준비 말
        for crew in ("레디 액션!", "슛 들어갈게요.", "자, 슛 들어갑니다.", "마이크 됐어?", "카메라 돌아가요?", "준비 됐어요?", "됐어?"):
            self.assertEqual(self.kinds([(0.5, 2.5, crew), (3, 3.5, "네")] + greet), ["pre"], crew)

    def test_cold_open_demo_before_greeting_survives(self):
        """'녹화 되고 있지?' → 시범(공 소리) → '나이스! 이게 오늘 배울 슛이에요.' → 인사: 준비 말만 자르고 시범·훅은 롱폼에 남음."""
        tmp = Path(tempfile.mkdtemp(prefix="E12 콜드오픈 "))
        work = tmp / "작업"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        try:
            with mock.patch.multiple(core, **dirs):
                d = core.adir("콜드오픈.mp4")
                d.mkdir(parents=True)
                rows = [(0.5, 2.5, "녹화 되고 있지?"), (3, 3.6, "네."), (11.5, 15, "나이스! 이게 오늘 배울 슛이에요."),
                        (16, 19, "안녕하세요, 풋살사관학교 최경진입니다."), (19.5, 24, "오늘은 발등 슈팅을 알려 드릴게요."),
                        (25, 60, "첫 번째는 디딤발을 공 옆에 두는 거예요."), (61, 65, "오늘은 여기까지. 다음 영상에서 만나요.")]
                (d / "transcript.json").write_text(json.dumps(lines(*rows), ensure_ascii=False), encoding="utf-8")
                (d / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": [{"time": 6.2}, {"time": 9.1}]}), encoding="utf-8")
                r = editor.recommend("콜드오픈.mp4")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual([(o["kind"], o["b"]) for o in r["offscript"]], [("pre", 3.9)])
        first = r["tidy"][0]
        self.assertLessEqual(first["in"], 5.2)  # 첫 공 소리(6.2) 1초 앞부터
        self.assertGreater(first["in"], 3.6)  # 준비 말('네.')은 빠짐
        self.assertGreaterEqual(first["out"], 15.0)  # 시범 → '나이스!'가 한 컷


class NoticeReviewTest(unittest.TestCase):
    """홍보·구독 안내는 부탁 꼴이 있을 때만 — 레슨 설명에 나온 '인스타 DM'·'레슨 받는 친구들'·'좋아요'는 아님 (BR-060 · BR-062)."""

    def test_lesson_lines_are_not_promo_or_cta(self):
        for t in ("인스타 DM으로 많이 물어보셔서 오늘은 인사이드 킥을 준비했어요.", "레슨 받는 친구들이 제일 많이 하는 실수가 이거예요.",
                  "카톡으로 영상 보내 주신 분도 이것만 고치면 돼요.", "문의하신 분이 있어서 준비했어요.", "레슨 신청한 친구들이 다 이걸 어려워해요."):
            self.assertFalse(takes.is_promo(t), t)
        for t in ("좋아요 한 번 더 해 볼게요", "좋아요 하고 바로 패스", "좋아요, 이렇게 하는 거예요", "구독자 여러분 안녕하세요"):
            self.assertIsNone(takes.CTA_RX.search(t), t)
        for t in ("레슨 문의는 인스타그램 DM으로 주세요.", "아참 레슨 문의는 인스타그램 dm으로 주시면 돼요", "수강 신청은 프로필 링크로 해 주세요.",
                  "문의는 카톡 채널로 주세요."):
            self.assertTrue(takes.is_promo(t), t)
        for t in ("구독이랑 좋아요 한 번씩 눌러주시고요", "좋아요 꾹 눌러 주세요", "알림 설정까지 부탁드려요"):
            self.assertIsNotNone(takes.CTA_RX.search(t), t)

    def test_promolike_lesson_keeps_its_setup_line(self):
        rows = [(0.5, 4, "안녕하세요, 풋살사관학교입니다."), (4.5, 10, "인스타 DM으로 많이 물어보셔서 오늘은 인사이드 킥을 준비했어요."),
                (10.5, 16, "레슨 받는 친구들이 제일 많이 하는 실수가 이거예요."), (16.5, 22, "발목에 힘을 빼고 차는 거예요."),
                (22.5, 28, "발목을 단단하게 고정해야 공이 똑바로 가요."), (28.5, 31, "좋아요, 한 번 더 해 볼게요."),
                (55.5, 61, "카톡으로 영상 보내 주신 분도 이것만 고치면 돼요."), (68, 72, "오늘은 여기까지입니다. 감사합니다.")]
        segs = lines(*rows)
        self.assertEqual(takes.find_offscript(captions.split_sentences(segs)), [])
        self.assertEqual(upload.promo_lines(segs), [])  # 설명 '▶ 레슨 문의'에도 안 들어감

    def test_promo_after_signoff_goes_to_kit(self):
        rows = [(0.5, 4, "안녕하세요, 풋살사관학교입니다."), (4.5, 60, "오늘은 볼 컨트롤 기초를 알려 드릴게요."),
                (65, 69, "오늘은 여기까지입니다. 감사합니다."), (70, 76, "아 참, 레슨 문의는 인스타그램 DM으로 주세요. 주말반 아직 자리 있어요."),
                (78, 80, "자 컷!")]
        segs = lines(*rows)
        off = takes.find_offscript(captions.split_sentences(segs))
        self.assertEqual([o["kind"] for o in off], ["post"])  # 영상에서는 촬영 끝 말과 함께 잘리고
        self.assertIn("레슨 문의", off[0]["text"])
        self.assertEqual(upload.promo_lines(segs), ["레슨 문의는 인스타그램 DM으로 주세요.", "주말반 아직 자리 있어요."])  # 설명에는 남음

    def test_template_with_own_lesson_block(self):
        tpl = "{훅}\n\n▶ 레슨 문의\n인스타그램 @futsal_academy DM\n\n{해시태그}\n"
        notes = []
        out = upload.add_promo(upload.description(["훅"], [], ["#풋살"], template=tpl), ["레슨 문의는 인스타그램 DM으로 주세요."], notes)
        self.assertEqual(out.count("▶ 레슨 문의"), 1)  # 감독님이 쓴 연락처 묶음 그대로 · 두 번째 머리글 없음
        self.assertTrue(any("이미" in n for n in notes))
        slot = "{훅}\n\n▶ 레슨 문의\n인스타그램 @futsal_academy DM\n\n{레슨 문의}\n\n{해시태그}\n"
        self.assertEqual(upload.add_promo(upload.description(["훅"], [], ["#풋살"], template=slot), ["레슨 문의는 인스타그램 DM으로 주세요."], []).count("▶ 레슨 문의"), 1)


class RecMixin:
    """고정 자료 받아쓰기로 editor.recommend 를 돌림 (임시 작업 폴더)."""

    def _rec(self, key, segs=None):
        tmp = Path(tempfile.mkdtemp(prefix="E12 검토 "))
        work = tmp / "작업"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        try:
            with mock.patch.multiple(core, **dirs):
                d = core.adir("v.mp4")
                d.mkdir(parents=True)
                (d / "transcript.json").write_text(json.dumps(segs or FIX[key]["transcript"], ensure_ascii=False), encoding="utf-8")
                (d / "analysis.json").write_text(json.dumps(FIX[key]["analysis"], ensure_ascii=False), encoding="utf-8")
                return editor.recommend("v.mp4")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class GhostAndWordsTest(RecMixin, unittest.TestCase):
    def test_ghost_duplicate_merges_into_the_real_sentence(self):
        sents = [S(153.38, 157.44, "오늘은 여기까지 다음 영상에서 만나요"), S(157.44, 158.32, "감사합니다", W(("감사합니다", 157.44, 158.32))),
                 S(158.32, 158.50, "감사합니다", W(("감사합니다", 158.32, 158.50)))]
        out = captions.merge_ghosts(sents)
        self.assertEqual([(s["start"], s["end"], s["text"]) for s in out[1:]], [(157.44, 158.5, "감사합니다")])
        # 둘 다 제대로 된 길이면 진짜 되풀이 (말더듬 규칙이 판단)
        two = [S(1.0, 2.0, "빠르게"), S(2.1, 3.0, "빠르게")]
        self.assertEqual(len(captions.merge_ghosts(two)), 2)

    def test_lesson04_long_cut_ends_on_the_whole_closing(self):
        for key in ("LESSON04", "LESSON04_nocond", "LESSON04_nocond_vad35"):
            r = self._rec(key)
            last = r["tidy"][-1]
            self.assertGreaterEqual(last["out"], 158.7, key)  # '감사합니다' 소리 끝(≈158.75)까지
            self.assertLessEqual(last["out"], 159.05, key)  # '자 컷'(160.47) 앞
            self.assertGreaterEqual(last["out"] - last["in"], 1.0, key)  # 0.3초짜리 잘린 조각으로 끝나지 않음
            self.assertLessEqual(last["in"], 157.5, key)
            closing = [sh for sh in r["shorts"] if sh["start"] < 157.0 < sh["end"]]  # 끝인사로 끝나는 쇼츠도 낱말 꼬리까지
            for sh in closing:
                self.assertGreaterEqual(sh["cuts"][-1]["out"], 158.7, key)

    def test_msgraw03_zero_length_word_is_not_a_sentence_end(self):
        segs = FIX["MSGRAW03"]["transcript"]
        sents = captions.split_sentences(segs, guess=False)
        self.assertIn("네번째 빗나갔어요. 오면 세 개예요.", [s["text"] for s in sents])  # 61.23–61.23 '빗나갔어요.'에서 끊지 않음
        r = self._rec("MSGRAW03")
        for s in r["shorts"]:
            self.assertFalse(61.0 <= s["end"] <= 61.5, s)

    def test_old_transcript_without_words_is_judged_per_segment(self):
        segs = [{k: v for k, v in s.items() if k != "words"} for s in FIX["LESSON04"]["transcript"]]
        r = self._rec("LESSON04", segs)
        bounds = sorted({round(x, 2) for s in segs for x in (s["start"], s["end"])})
        cuts = [c for c in r["tidy"]] + [c for sh in r["shorts"] for c in sh["cuts"]]
        for c in cuts:  # 어림한 문장 시각으로 구간 안을 자르지 않음: 컷 경계는 받아쓰기 구간 경계(±여유) 근처
            for t in (c["in"], c["out"]):
                self.assertLess(min(abs(t - b) for b in bounds), 1.05, (t, c))


def covered(cuts, a, b):
    return sum(max(0.0, min(b, c["out"]) - max(a, c["in"])) for c in cuts) / (b - a)


class DemoKeptTest(RecMixin, unittest.TestCase):
    """시범을 부르는 말('다시 해볼게요')·뺄 문장이 같은 받아쓰기 구간 안에 있어도 그 뒤 시범은 남음 (I-100 · 검토 1·15)."""

    def test_msgraw02_success_demo_in_long(self):
        for key in ("MSGRAW02", "MSGRAW02_nocond"):
            r = self._rec(key)
            a, b, kicks = FIX[key]["demos"][2]  # 성공 시범 71.99–77.49 · 공 소리 74.26 → '와, 이거죠. 완벽해요.'
            self.assertTrue(any(c["in"] <= kicks[0] <= c["out"] for c in r["tidy"]), key)
            self.assertGreaterEqual(covered(r["tidy"], a, b), 0.95, key)
            whys = [j for j in r["junk_list"] if j["why"] == "슬레이트 말" and j["a"] > 70]
            self.assertEqual(whys, [], key)  # 시범 앞 '다시 해볼게요.'는 슬레이트가 아님

    def test_lesson04_retry_demo_after_merge_with_vad(self):
        r = self._rec("LESSON04_nocond_vad35")  # 성능 묶음(VAD 0.35)과 합친 뒤 받아쓰기 모양: '…주세요 다시 한번 갈게요 [6초 시범] 나이스 이거예요'
        a, b, kicks = FIX["LESSON04_nocond_vad35"]["demos"][2]
        self.assertGreaterEqual(covered(r["tidy"], 94.0, 98.0), 0.99)
        for k in kicks:
            self.assertTrue(any(c["in"] <= k <= c["out"] for c in r["tidy"]), k)

    def test_lesson04_long_keeps_demos_before_reactions(self):
        for key in ("LESSON04", "LESSON04_nocond"):
            r = self._rec(key)
            for a, b, kicks in FIX[key]["demos"]:
                self.assertGreaterEqual(covered(r["tidy"], a, b), 0.85, (key, a, b))

    def test_lesson04_short_keeps_demo_before_cta(self):
        for key in ("LESSON04", "LESSON04_nocond"):
            r = self._rec(key)
            a, b, kicks = FIX[key]["demos"][3]  # 3번 차는 시범 131.73–139.73 ('10번 하면 8번은 성공해요'의 증거) 바로 뒤 '구독이랑 좋아요…'
            s = next(s for s in r["shorts"] if s["start"] <= a < s["end"])
            self.assertGreaterEqual(covered(s["cuts"], a, b), 0.85, key)
            txt = " ".join(text_in(FIX[key]["transcript"], c["in"], c["out"]) for c in s["cuts"])
            self.assertNotIn("구독이랑", txt, key)

    def test_shorts_drop_asides_and_reach_the_payoff(self):
        """쇼츠는 여담('오늘 진짜 물 좀 마시고 할게요')을 빼고, 구령·시범 바로 앞에서 끊지 않음 (결과 장면까지 · 검토 13·10)."""
        segs = FIX["MSGRAW01"]["transcript"]
        r = self._rec("MSGRAW01")
        for sh in r["shorts"]:
            self.assertNotIn("마시고", " ".join(text_in(segs, c["in"], c["out"]) for c in sh["cuts"]), sh["title"])
        self.assertIn("마시고", " ".join(text_in(segs, c["in"], c["out"]) for c in r["tidy"]))  # 롱폼은 그대로 (중간 여담 · 백로그 103)
        two = next(sh for sh in r["shorts"] if sh["start"] <= 87.6 < sh["end"])  # '자, 두번째 포인트는 …' 쇼츠
        self.assertGreaterEqual(two["end"], 126.4)  # '하나, 둘, 셋.' → 시범 → '나이스! 깔끔하게 들어갔어요.'까지
        r3 = self._rec("MSGRAW03")  # '과연 몇 개나 들어갈까요?' 챌린지: 마지막 슛의 '들어갔다. 성공입니다.'가 든 쇼츠가 있음
        kick = FIX["MSGRAW03"]["demos"][-1][2][0]
        self.assertTrue(any(c["in"] <= kick <= c["out"] for sh in r3["shorts"] for c in sh["cuts"]), [(sh["start"], sh["end"]) for sh in r3["shorts"]])
        self.assertTrue(any("성공입니다" in " ".join(text_in(FIX["MSGRAW03"]["transcript"], c["in"], c["out"]) for c in sh["cuts"]) for sh in r3["shorts"]))

    def test_retry_call_unit(self):
        segs = [S(60.0, 63.0, "저도 처음엔 이거 잘 못했어요."), S(63.5, 64.5, "다시 해볼게요."), S(71.0, 73.0, "와, 이거죠. 완벽해요.")]
        self.assertEqual(takes.find_junk(segs, [], [67.0]), [])  # 뒤에 공 소리 → 시범을 부르는 말
        self.assertEqual([w for *_, w in takes.find_junk(segs[:2] + [S(65.0, 67.0, "와, 이거죠.")], [], [])], ["슬레이트 말"])  # 바로 이어 말하면 슬레이트
        oops = [S(60.0, 63.0, "두 번째 동작은 패스하고 옆으로"), S(63.5, 64.5, "아니다, 다시 할게요."), S(71.0, 73.0, "두 번째 동작은 패스하고 옆으로 빠져요.")]
        self.assertTrue(any(a <= 63.5 and 64.5 <= b for a, b, _ in takes.find_junk(oops, [], [67.0])))  # 실수 말이 붙으면 시범이 와도 지움 (다시 찍기)
        self.assertTrue(takes.find_junk(oops[:2], [], [67.0]))


class ReactionReviewTest(unittest.TestCase):
    def test_reaction_words(self):
        for t in ("나이스!", "나이스 이거예요", "아이고, 공이 조금 뒤로 갔네요.", "들어갔어요! 이렇게 차는 거예요.", "다시, 또 놓쳤네.", "봤죠? 이렇게 하는 거예요.",
                  "와, 이거죠. 완벽해요.", "괜찮아요."):
            self.assertEqual(editor._reaction(t), "strong", t)
        for t in ("좋아요!", "좋습니다.", "오케이"):
            self.assertEqual(editor._reaction(t), "weak", t)
        for t in ("그렇지 않으면 공이 떠요.", "완벽하게 하려면 디딤발이 중요해요.", "깔끔하게 차는 방법은 이거예요.", "좋아요 그럼 시작해 볼게요",
                  "좋습니다 그럼 두 번째 포인트는 몸의 방향이에요.", "오케이 이제 세 번째는 첫 터치 방향이에요.", "골대 맞았어요."):
            self.assertIsNone(editor._reaction(t), t)

    def test_demo_lead_follows_the_ball_sounds(self):
        segs = lines((28.5, 34, "자, 제가 드리블로 세 명을 제쳐 볼게요."), (56.5, 60, "봤죠? 이렇게 하는 거예요."))
        a, b = editor._demo_before(segs, 1, [36.0, 39.0, 42.0])
        self.assertEqual(a, 35.5)  # 첫 공 소리 1초 앞(35.0)부터 — 10초까지라 35.5
        self.assertEqual(b, 45.5)  # 마지막 공 소리 뒤 12초 걸어 돌아온 곳은 자름
        self.assertIsNone(editor._demo_before(segs, 1, []))  # 공 소리가 없고 22초 빈 곳 → 시범으로 안 봄
        dead = lines((22.5, 28, "발을 빼면서 받으면 공이 발 앞에 딱 멈춰요."), (36, 42, "좋습니다 그럼 두 번째 포인트는 몸의 방향이에요."))
        self.assertIsNone(editor._demo_before(dead, 1, []))  # 반응 말이 아님 → 8초 쉼은 그대로 자름
        weak = lines((10, 14, "패스하고 바로 뛰세요."), (20, 21, "좋아요!"))
        self.assertIsNone(editor._demo_before(weak, 1, []))  # 약한 반응은 공 소리가 있어야
        self.assertEqual(editor._demo_before(weak, 1, [16.0]), (15.0, 20.0))

    def test_short_drops_dangling_end_and_titles(self):
        segs = lines((0, 10, "자, 첫 번째 포인트는 공을 몸 앞에 두는 거예요."), (10.5, 22, "공이 몸 뒤에 있으면 방향을 바꿀 수가 없어요."),
                     (22.5, 26, "오늘 진짜 물 좀 마시고 할게요."))
        self.assertEqual(editor._short_edges(segs, set(), 0, 2, min_len=20.0)[:2], (0, 1))
        self.assertEqual(editor._short_edges(segs, set(), 0, 2, min_len=25.0)[:2], (0, 2))  # 줄이면 너무 짧아지면 그대로
        heads = lines((0, 1, "괜찮아요."), (1.2, 3, "풋살사관학교 최경진입니다."), (3.5, 8, "패스하고 바로 앞으로 두 걸음 뛰어 나가세요."))
        self.assertEqual(editor._short_title(heads, set(), 0, 2), "패스하고 바로 앞으로 두 걸음 뛰어 나가세요.")

    def test_reaction_chain_starts_at_the_cause(self):
        segs = lines((48, 53.3, "두 번째 동작은 패스하고 옆으로 빠지면서 다시 공을 받는 거예요."), (61.2, 64, "아이고, 공이 조금 뒤로 갔네요."),
                     (64.5, 65.1, "괜찮아요."), (65.5, 67.5, "저도 처음엔 이거 잘 못했어요."))
        i, j, lead = editor._short_edges(segs, set(), 2, 3, peaks=[56.0])
        self.assertEqual((i, lead), (1, (55.0, 61.2)))  # '괜찮아요'로 시작하는 쇼츠 → 그 원인 '아이고…'와 실패 시범부터


if __name__ == "__main__":
    unittest.main()
