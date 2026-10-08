"""올리기 키트 맞춤 (E11) — 저장소 폴더에서 python3 -m unittest tests.test_kit_fit
우리 채널 72편 제목 틀(D-080) · 쇼츠 주제어 풀이말 X(D-081) · 제목 겹침(D-082) · 전략 할 일·시리즈(D-083) · 설명 첫 줄 인용(D-084) · 게스트(D-085)."""
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402
import hooks  # noqa: E402
import strategy  # noqa: E402
import upload  # noqa: E402
from tests.test_upload import KitBase, fixture_segments  # noqa: E402

OUR = "https://www.youtube.com/channel/UCRYziLOw2T6BF6fXtpUby-g"

# 팔로우 스루 쇼츠 (인사이드 패스 레슨에서 잘라 낸 102~156초 · 기술 이름은 '팔로우 스루'·'디딤발'뿐, '차고'가 가장 많이 나옴)
FOLLOW = ["봤죠? 회전이 하나도 없어요.", "자 이제 수강생분이랑 같이 해 볼게요.", "근데 지금 디딤발이 조금 뒤에 있었어요.",
          "세 번째 포인트 팔로우 스루.", "공을 차고 나서 발이 목표 방향으로 따라가야 돼요.", "차고 바로 멈추면 공이 짧게 끊겨요.",
          "프로 선수들은 차고 나서 몸 전체가 앞으로 나가요.", "넓은 쪽으로 차고 넓은 공간을 봐요.", "이 차이가 정확도를 만듭니다."]


# 검토에서 쓴 실제 꼴의 인사이드 패스 레슨 쇼츠 3개 (내보낸 .srt 대사) — 기술 이름이 없어서 예전엔 셋 다 '인사이드 패스' 제목·해시태그
LESSON01 = {
    "디딤발": ["자 첫 번째 포인트. 디딤발은 공 옆에 한 뼘 정도 떨어져서 놓으세요.", "그리고 발끝이 가고 싶은 방향을 봐야 돼요.",
             "음 디딤발이 공 뒤에 있으면 공이 떠요.", "한번 보여 드릴게요.", "이렇게 디딤발이 방향을 잡아 주면 공이 똑바로 가죠.", "하나 둘 셋. 나이스!"],
    "발목": ["두 번째 포인트는 발목 고정이에요.", "발목이 흔들리면 공에 힘이 안 실려요.", "발목을 딱 잠그고 발 안쪽 넓은 면으로 밀어 준다는 느낌이에요.",
           "이게 진짜 핵심이에요.", "발목이 고정되면 공이 회전 없이 깔끔하게 가요.", "봤죠? 회전이 하나도 없어요."],
    "팔로우 스루": ["세 번째 포인트 팔로우 스루.", "공을 차고 나서 발이 목표 방향으로 따라가야 돼요.", "차고 바로 멈추면 공이 짧게 끊겨요.",
               "프로 선수들은 차고 나서 몸 전체가 앞으로 나가요.", "이 차이가 정확도를 만듭니다."],
}
# LESSON04 쇼츠 2 (앱이 내보낸 .srt 그대로 · '왼 아니 오른발' 말을 고친 문장)
LESSON04_S2 = ["세번째 포인트 패스 방향이에요", "패스는 왼 아니 오른발 앞쪽으로 넣어주세요", "그래야 바로 슈팅까지 갈 수 있어요", "이 차이가 진짜 커요",
               "10번 하면 8번은 성공해요", "구독이랑 좋아요 한 번씩 눌러주시고요"]


class WorkBase(unittest.TestCase):
    """빈 작업 폴더 (채널 전략 자료 없음 · 비교 데이터는 저장소의 strategy_seed.json)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="키트 맞춤 "))
        dirs = {"WORK": self.tmp, "VIDEOS": self.tmp / "videos", "ANALYSIS": self.tmp / "analysis", "OUT": self.tmp / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        (self.tmp / "projects").mkdir()
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [
            mock.patch.object(editor, "PROJECTS", self.tmp / "projects"), mock.patch.dict(core.CONFIG, {"channel_url": OUR})]
        for p in self.patches:
            p.start()
        strategy._CACHE.clear()
        strategy._SLIM.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        strategy._CACHE.clear()
        strategy._SLIM.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)


class OwnRowsTests(WorkBase):
    """D-080: channel_cache.json 이 없어도 채널 전략의 우리 채널 자료(새로 고친 자료 · 없으면 비교 데이터)로 제목 틀을 배움."""

    def test_fresh_install_learns_from_seed(self):
        self.assertIsNone(hooks.load_cache())
        rows = hooks.own_rows()
        self.assertEqual((len(rows["videos"]), len(rows["shorts"])), (30, 24))
        out = hooks.title_candidates(["퍼스트 터치"], None, "shorts", dur=45)
        self.assertIn('🔥풋살기술🔥 "퍼스트 터치" 속성강의', out)  # 1위 30만 회 쇼츠의 틀
        self.assertIn("풋살 국가대표 퍼스트 터치 강좌", out)
        self.assertNotIn("600만뷰", " ".join(out))

    def test_other_channel_does_not_use_our_seed(self):
        with mock.patch.dict(core.CONFIG, {"channel_url": "https://www.youtube.com/@다른채널"}):
            self.assertEqual(hooks.own_rows(), {})
            out = hooks.title_candidates(["퍼스트 터치"], None, "shorts", dur=45)
        self.assertNotIn('🔥풋살기술🔥 "퍼스트 터치" 속성강의', out)

    def test_newer_side_wins_per_kind(self):
        cache = {"videos": [{"title": "패스를 잘하는 3가지 방법", "views": 9000}], "saved": time.strftime("%Y-%m-%d %H:%M")}
        hooks._write_json(hooks.cache_path(), cache)
        rows = hooks.own_rows()
        self.assertEqual(rows["videos"][0]["title"], "패스를 잘하는 3가지 방법")  # 방금 불러온 목록이 비교 데이터보다 새로움
        self.assertEqual(len(rows["shorts"]), 24)  # 목록에 없는 형식은 채널 전략 자료로 채움
        cache["saved"] = "2020-01-01 00:00"  # 오래된 목록 → 채널 전략 자료
        hooks._write_json(hooks.cache_path(), cache)
        self.assertEqual(len(hooks.own_rows()["videos"]), 30)
        self.assertIn("own_rows", hooks.title_candidates.__code__.co_names)

    def test_live_strategy_data_preferred_over_seed(self):
        seed = strategy.seed()["channels"]["own"]
        live = json.loads(json.dumps(seed))
        live.update(src="live", at=time.time(), listedAt=time.time())
        vid = live["tabs"]["shorts"]["ids"][0]
        live["videos"][vid]["t"] = "드리블 꿀팁 대방출"
        strategy.save_channel(live)
        rows = hooks.own_rows()
        self.assertIn("드리블 꿀팁 대방출", [r["title"] for r in rows["shorts"]])

    def test_broken_strategy_data_falls_back_to_cache(self):
        hooks._write_json(hooks.cache_path(), {"videos": [{"title": "슈팅이 안 되는 이유", "views": 5}], "saved": "2020-01-01 00:00"})
        with mock.patch.object(strategy, "own_listing", side_effect=OSError("잠김")):
            self.assertEqual(hooks.own_rows()["videos"][0]["title"], "슈팅이 안 되는 이유")


class TopicTests(WorkBase):
    """D-081: 기술 이름이 없는 쇼츠는 원본 영상 주제를 물려받고, '차고'·'넓은' 같은 풀이말로는 절대 채우지 않음."""

    def test_follow_through_shorts(self):
        self.assertNotIn("차고", hooks.topic_keywords(FOLLOW))
        self.assertNotIn("넓은", hooks.topic_keywords(FOLLOW))
        own = hooks.kit_topics(FOLLOW)
        self.assertIn("팔로우 스루", own)  # 기본 레슨 낱말
        self.assertIn("디딤발", own)
        got = hooks.kit_topics(FOLLOW, ["인사이드 패스"])
        self.assertEqual(got[0], "인사이드 패스")  # 원본 영상 주제가 먼저
        self.assertIn("팔로우 스루", got)
        self.assertNotIn("차고", got)
        called = []
        hooks.kit_topics(["드리블은 몸을 낮춰요", "드리블 연습"], lambda: called.append(1) or ["패스"])
        self.assertEqual(called, [])  # 자체 용어가 있으면 원본 대사를 읽지 않음

    def test_frequent_words_never_predicates(self):
        texts = ["넓은 쪽으로 차고 빠르게", "넓은 공간으로 차고 빠르게", "넓은 곳 차고 빠르게", "고깔 사이를 지나요", "고깔을 세워요", "고깔 하나"]
        out = hooks.topic_keywords(texts)
        for w in ("넓은", "차고", "빠르게"):
            self.assertNotIn(w, out)
        self.assertIn("고깔", out)  # 이름말은 남음 (세 번 넘게 · 조사 뗌)
        self.assertEqual(hooks.kit_topics(["넓은 차고", "넓은 차고", "넓은 차고"], ["차고"]), [])  # 물려받은 것도 풀이말이면 안 씀

    def test_terms_still_win(self):
        self.assertEqual(hooks.kit_topics(["오늘은 인사이드 패스 알려 드릴게요", "디딤발은 공 옆에"], ["슈팅"])[0], "인사이드 패스")

    def test_lesson_shorts_lead_with_their_own_point(self):
        """검토: 같은 레슨에서 자른 쇼츠 셋이 저마다의 요점(디딤발·팔로우 스루)을 앞에 두고 원본 주제는 둘째 → 제목·해시태그가 서로 다름."""
        got = {}
        for k, texts in LESSON01.items():
            info = {}
            got[k] = (hooks.kit_topics(texts, ["인사이드 패스"], focus=True, info=info), info)
        self.assertEqual(got["디딤발"][0][:2], ["디딤발", "인사이드 패스"])
        self.assertEqual(got["팔로우 스루"][0][:2], ["팔로우 스루", "인사이드 패스"])  # 한 번만 나와도 '세 번째 포인트 X'로 소개
        self.assertEqual(got["디딤발"][1], {"parent": "인사이드 패스"})
        self.assertEqual(got["발목"][0][0], "인사이드 패스")  # 몸 부위(도움 낱말)는 주제 자리에 안 씀 → 원본 주제
        self.assertEqual(len({v[0][0] for v in got.values()}), 3)
        self.assertEqual(hooks.kit_topics(LESSON01["디딤발"], ["인사이드 패스"])[0], "인사이드 패스")  # 롱폼·편집본(focus X)은 예전처럼
        own = ["인사이드 패스는 디딤발이 중요해요", "디딤발을 공 옆에", "디딤발 다시"]
        self.assertEqual(hooks.kit_topics(own, ["인사이드 패스"], focus=True)[:2], ["디딤발", "인사이드 패스"])  # 구간 용어가 원본 주제뿐
        self.assertEqual(hooks.kit_topics(own, ["슈팅"], focus=True)[0], "인사이드 패스")  # 구간만의 용어가 있으면 그것
        self.assertEqual(hooks.kit_topics(["퍼스트 터치 다시", "퍼스트 터치 한 번 더", "터치가 길어요"], ["퍼스트 터치"], focus=True)[0], "퍼스트 터치")  # 용어 안의 '터치'는 안 셈

    def test_helper_words_never_fill_title_slot(self):
        """검토: '시선·중심·무릎'만 나온 기본 자세 레슨 → 주제 자리는 '기본 자세' · 질문형·정리 틀에 도움 낱말 X."""
        texts = ["오늘은 기본 자세 이야기예요", "시선은 앞을 보세요", "시선이 내려가면 안 돼요", "무릎을 굽히고 중심을 낮추세요", "중심이 높으면 넘어져요", "시선 앞, 중심 낮게"]
        topics = hooks.topic_keywords(texts)
        self.assertEqual(topics[0], "기본 자세")
        self.assertIn("시선", topics)
        titles = hooks.title_candidates(topics, None, "long", n=8, flow=hooks.in_order(topics[:3], texts), dur=60)
        titles += hooks.question_titles(topics, 4)
        for t in titles:
            for w in ("시선", "중심", "무릎"):
                self.assertNotIn(w, t, t)
        self.assertEqual(hooks.lesson_topics(["발목을 고정해요", "발목이 흔들려요"])[0], "기본기")
        self.assertFalse(hooks.slot_ok("시선"))
        self.assertTrue(hooks.slot_ok("팔로우 스루"))

    def test_inherited_terms_ending_like_predicates(self):
        """검토: 원본 주제 '킥인'·'스크린'·'수비 라인'은 끝 글자가 풀이말 같아도 물려받음."""
        for t in ("킥인", "스크린", "수비 라인"):
            self.assertEqual(hooks.kit_topics(["이렇게 돌아요", "한 번 더 해 볼게요"], [t])[0], t)


class QuoteTests(unittest.TestCase):
    """D-084: 설명 첫 줄 인용은 마무리·순서 말이 아니고, 같은 촬영본 안에서는 서로 다른 문장."""

    def segs(self, texts):
        return [{"start": i * 3.0, "end": i * 3.0 + 2, "text": t} for i, t in enumerate(texts)]

    def test_closing_and_order_words(self):
        line = upload.hook_lines(self.segs(["다음 영상에서 만나요", "연습해보시고 궁금한건 댓글로 남겨주세요", "오늘은 여기까지에요 감사합니다"]),
                                 ["슈팅"], "shorts", dur=30)[0]
        self.assertFalse(line.startswith("“"), line)  # 인용할 문장이 없으면 기본 문장
        line = upload.hook_lines(self.segs(["자, 두번째 포인트는 디딤발이 공이랑 너무 가까우면 터치가 무조건 길어져요"]), ["퍼스트 터치"])[0]
        self.assertEqual(line, "“디딤발이 공이랑 너무 가까우면 터치가 무조건 길어져요”")
        line = upload.hook_lines(self.segs(["세 번째 포인트 팔로우 스루.", "고개들기 디딤발걸이 이", "공을 차고 나서 발이 목표 방향으로 따라가야 돼요"]), ["패스"])[0]
        self.assertEqual(line, "“공을 차고 나서 발이 목표 방향으로 따라가야 돼요”")  # 순서 말만 남은 짧은 말·끊긴 조각은 안 씀

    def test_slip_and_run_on_not_quoted(self):
        """검토: 말을 고친 문장('왼 아니 오른발')·마침표 없이 이어 붙은 받아쓰기는 첫 줄로 인용하지 않음."""
        line = upload.hook_lines(self.segs(LESSON04_S2), ["패스"], "shorts", dur=30)[0]
        self.assertEqual(line, "“이 차이가 진짜 커요”")
        run_on = "이 차이가 정말 중요해요 터치가 조금 길었네요 그렇죠 이게 완벽한 퍼스트 터치 에요"
        line = upload.hook_lines(self.segs([run_on, "그렇죠 이게 완벽한 퍼스트 터치에요"]), ["퍼스트 터치"])[0]
        self.assertEqual(line, "“이게 완벽한 퍼스트 터치에요”")  # 맞장구는 떼고
        self.assertFalse(upload._quotable("아 잠깐만요 말이 꼬였네 다시 할게요"))
        self.assertTrue(upload._quotable("이게 진짜 핵심이에요 몸을 먼저 여세요"))

    def test_emphasis_first_and_avoid(self):
        segs = self.segs(["오늘 날씨가 정말 좋네요 여러분", "이게 진짜 핵심이에요 몸을 먼저 여세요", "패스는 받을 사람 앞발로 주세요"])
        self.assertEqual(upload.hook_lines(segs, ["패스"])[0], "“이게 진짜 핵심이에요 몸을 먼저 여세요”")
        line = upload.hook_lines(segs, ["패스"], avoid=["이게 진짜 핵심이에요 몸을 먼저 여세요 그리고 더"])[0]
        self.assertEqual(line, "“패스는 받을 사람 앞발로 주세요”")  # 다른 키트가 쓴 문장(이 문장을 품은 문장도)은 건너뜀


class OverlapTests(WorkBase):
    """D-082: 다른 키트·이미 올린 우리 제목과 겹치는 후보는 뒤로 · 주제어만 바꾼 같은 틀은 한 칸 뒤."""

    def kit(self, name, seq, title, topics, fmt="shorts"):
        k = {"version": 1, "made": "2026-10-08 10:00", "name": name, "source": {"id": seq, "label": seq or "원본 영상 그대로"}, "format": fmt, "duration": 40.0,
             "topics": topics, "titles": [title], "title": title, "chapters": [], "hashtags": [], "tags": [], "description": ""}
        upload.save_kit(k, f"{Path(name).stem}_{seq or '원본'}")
        return k

    def test_norm_and_skeleton(self):
        self.assertEqual(hooks.norm_title("퍼스트 터치 꿀팁 (최경진 감독)"), hooks.norm_title("퍼스트 터치 꿀팁"))
        self.assertEqual(hooks.norm_title("[1분 풋살 기술] 퍼스트 터치 꿀팁 #shorts"), hooks.norm_title("퍼스트터치 꿀팁!"))
        self.assertNotEqual(hooks.norm_title("퍼스트 터치 꿀팁 (feat. 이한울)"), hooks.norm_title("퍼스트 터치 꿀팁"))
        self.assertEqual(hooks.title_skeleton("패스가 안 된다면?", ["패스"]), hooks.title_skeleton("슈팅이 안 된다면?", ["슈팅"]))
        self.assertIsNone(hooks.title_skeleton("[1분 풋살 기술] 패스", ["패스"]))  # 주제어만 남으면 틀이 아님
        self.assertEqual(hooks.title_skeleton("이 패스 가능?", ["패스"]), "이{}가능")  # '가능'의 '가'는 조사가 아님 (검토)
        self.assertNotEqual(hooks.title_skeleton("패스, 이것만 바꾸세요", ["패스"]), hooks.title_skeleton("패스 것만 바꾸세요", ["패스"]))

    def test_every_template_has_one_skeleton_across_topics(self):
        """검토: 질문형·기본 틀 모두 주제어만 바꾸면 같은 틀 열쇠 (받침 있는 말·없는 말·띄어 쓴 말·숫자)."""
        tpls = list(hooks.QUESTION_TPL) + [f[2] for f in hooks.FAMILIES] + [f[3] for f in hooks.FAMILIES if f[3]]
        sets = (["패스", "퍼스트 터치", "슈팅"], ["2대1 패스", "리턴 패스", "수비"], ["팔로우 스루", "디딤발", "턴"], ["1대1", "슛", "킥인"])
        for tpl in tpls:
            keys = {hooks.title_skeleton(hooks.fill(tpl, t), t) for t in sets}
            self.assertEqual(len(keys), 1, (tpl, keys))
            self.assertIsNotNone(next(iter(keys)), tpl)

    def test_duplicate_goes_last_and_same_pattern_one_step(self):
        self.kit("a.mp4", "s1", "퍼스트 터치 꿀팁", ["퍼스트 터치"])
        self.kit("b.mp4", "s2", "패스, 이것만 바꾸세요", ["패스"])
        k = {"name": "c.mp4", "source": {"id": "s3"}, "format": "shorts", "duration": 40.0, "topics": ["퍼스트 터치"], "hashtags": [],
             "titles": ["퍼스트 터치 꿀팁", "퍼스트 터치, 이것만 바꾸세요", "프로처럼 퍼스트 터치 하는 법", "퍼스트 터치 제대로 하는 법"]}
        upload.shape_titles(k, upload.strategy_hints(), upload.other_kits("c.mp4", "s3"))
        self.assertEqual(k["titles"][-1], "퍼스트 터치 꿀팁")  # 같은 글자 → 맨 뒤
        self.assertEqual(k["title"], "프로처럼 퍼스트 터치 하는 법")  # 같은 틀('{주제}, 이것만 바꾸세요')도 한 칸 뒤
        upload.annotate(k)
        tags = dict(zip(k["titles"], [i["tags"] for i in k["titleInfo"]]))
        self.assertIn("겹쳐요", tags["퍼스트 터치 꿀팁"])
        self.assertIn("같은 틀", tags["퍼스트 터치, 이것만 바꾸세요"])
        self.assertIn("퍼스트터치꿀팁", [x["k"] for x in k["taken"]])

    def test_uploaded_own_title_is_taken(self):
        k = {"name": "c.mp4", "source": {"id": ""}, "format": "long", "duration": 300.0, "topics": ["발바닥"], "hashtags": [],
             "titles": ["풋살에 기본 중에 기본 중에 기본 중에 기본! 발바닥 배우기", "발바닥 꿀팁 (최경진 감독)", "발바닥이 안 되는 진짜 이유"]}
        upload.shape_titles(k, {"series": [], "hashtags": []}, [])
        self.assertEqual(k["titles"][-1], "풋살에 기본 중에 기본 중에 기본 중에 기본! 발바닥 배우기")  # 이미 올린 우리 영상 (비교 데이터)

    def test_near_copy_of_uploaded_title(self):
        """검토: 우리 인기 제목에서 배운 틀로 같은 기술 영상이면 옛 제목과 거의 같아짐 → '겹쳐요' ('거의 같아요')."""
        taken = upload.taken_titles([])
        m = upload._marks('🔥풋살기술🔥 "영재 플랩" 속성강의', {"topics": ["영재 플랩"]}, {}, taken)
        self.assertEqual(m.get("near"), "이미 올린 우리 영상")  # 올린 '🔥600만뷰 풋살기술🔥 "영재 플랩" 속성강의 #풋살 #국가대표'
        m = upload._marks("풋살 국가대표 플립플랩 강좌", {"topics": ["플립플랩"]}, {}, taken)
        self.assertEqual(m.get("near"), "이미 올린 우리 영상")  # 올린 '풋살 국가대표 in-in 플립플랩 강좌'
        self.assertEqual(upload._marks("잔디풋살 VS 인도어 풋살", {"topics": []}, {}, taken).get("dup"), "이미 올린 우리 영상")
        for t, tp in (('🔥풋살기술🔥 "퍼스트 터치" 속성강의', "퍼스트 터치"), ("풋살 국가대표 퍼스트 터치 강좌", "퍼스트 터치"), ("영재 플랩 꿀팁", "영재 플랩")):
            m = upload._marks(t, {"topics": [tp]}, {}, taken)
            self.assertFalse(m.get("near") or m.get("dup"), (t, m))  # 다른 기술 · 짧은 열쇠는 아님
        k = {"name": "c.mp4", "source": {"id": "s"}, "format": "shorts", "duration": 40.0, "topics": ["플립플랩"], "hashtags": [],
             "titles": ["풋살 국가대표 플립플랩 강좌", "플립플랩, 이것만 바꾸세요", "플립플랩 제대로 하는 법"]}
        upload.shape_titles(k, {"series": [], "hashtags": []}, [])
        self.assertEqual(k["titles"][-1], "풋살 국가대표 플립플랩 강좌")
        upload.annotate(k, {"series": [], "hashtags": []}, [])
        self.assertIn("거의 같아요", k["titleInfo"][-1]["why"])

    def test_two_shorts_of_same_source_get_different_titles(self):
        """d10eb5b3·4060f84d: 같은 촬영본의 두 쇼츠 키트를 차례로 만들어도 기본 제목이 같지 않음 (BR-040)."""
        k1 = {"name": "x.mp4", "source": {"id": "a"}, "format": "shorts", "duration": 40.0, "topics": ["퍼스트 터치"], "hashtags": [],
              "titles": hooks.title_candidates(["퍼스트 터치"], "퍼스트 터치 꿀팁", "shorts", dur=40, n=8)}
        upload.shape_titles(k1, {"series": [], "hashtags": []}, [])
        self.kit("x.mp4", "a", k1["title"], ["퍼스트 터치"])
        k2 = {"name": "x.mp4", "source": {"id": "b"}, "format": "shorts", "duration": 40.0, "topics": ["퍼스트 터치"], "hashtags": [],
              "titles": hooks.title_candidates(["퍼스트 터치"], "퍼스트 터치 꿀팁", "shorts", dur=40, n=8)}
        upload.shape_titles(k2, {"series": [], "hashtags": []}, upload.other_kits("x.mp4", "b"))
        self.assertNotEqual(hooks.norm_title(k1["title"]), hooks.norm_title(k2["title"]))
        self.assertEqual(len(k2["titles"]), 5)

    def test_live_keys_not_saved(self):
        k = self.kit("a.mp4", "s1", "퍼스트 터치 꿀팁", ["퍼스트 터치"])
        k.update(titleInfo=[{"tags": ["겹쳐요"]}], taken=[{"k": "x"}], applied=["x"], schedule={"line": "x"}, guestBook=[{"name": "x"}])
        upload.save_kit(k, "a_s1")
        saved = json.loads((core.OUT / "a_s1_올리기.json").read_text(encoding="utf-8"))
        for key in ("titleInfo", "taken", "applied", "schedule", "guestBook", "alerts"):
            self.assertNotIn(key, saved)


class StrategyFitTests(WorkBase):
    """D-083: 켜진 전략 할 일(질문형·N자·해시태그)과 저장한 시리즈 이름이 제목 후보·꼬리표·해시태그에 바로 반영."""

    def todos(self):
        for text, cat in (("짧은 질문형 제목·썸네일 문구를 써요 (예: ‘이 터치 가능?’)", "썸네일·제목"), ("제목을 20자 안쪽으로 짧게 써요", "썸네일·제목"),
                          ("쇼츠마다 같은 해시태그(예: #풋살사관학교)를 붙여 묶어요", "쇼츠 운영")):
            strategy.todo_add(text=text, category=cat)

    def test_hashtag_todo_reaches_upload_step(self):
        self.todos()
        t = [x for x in strategy.load_state()["todos"] if "해시태그" in x["text"]][0]
        self.assertEqual(t["use"], ["edit", "shorts"])  # 저장된 쓰임은 그대로
        self.assertIn("title", strategy.todo_uses(t))
        self.assertIn(t["id"], [x["id"] for x in strategy.todos_for("title")])  # 7단계 상자에 보임

    def test_hints(self):
        self.todos()
        d = strategy.preset("C")
        strategy.save_strategy(d)
        h = upload.strategy_hints()
        self.assertTrue(h["question"])
        self.assertEqual(h["maxLen"], 20)
        self.assertEqual([x["tag"] for x in h["hashtags"]], ["#풋살사관학교"])
        self.assertEqual([s["name"] for s in h["series"]], ["[1분 풋살 기술]", "[N가지 총정리]"])
        strategy.todo_update(strategy.load_state()["todos"][1]["id"], True)
        self.assertIsNone(upload.strategy_hints()["maxLen"])  # 끝낸 할 일은 안 봄

    def test_shape_with_todos_and_series(self):
        self.todos()
        strategy.save_strategy(strategy.preset("C"))
        hints = upload.strategy_hints()
        base = hooks.title_candidates(["퍼스트 터치"], "자, 두번째 포인트는 디딤발이 공이랑 너무 가까우면", "shorts", dur=45, n=8)
        k = {"name": "x.mp4", "source": {"id": "a"}, "format": "shorts", "duration": 45.0, "topics": ["퍼스트 터치"], "titles": base,
             "hashtags": ["#shorts", "#풋살", "#퍼스트터치"]}
        upload.shape_titles(k, hints, [])
        k["hashtags"] = upload.todo_hashtags(k["hashtags"], hints, "shorts")
        upload.annotate(k, hints, [])
        self.assertEqual(k["title"], "[1분 풋살 기술] 퍼스트 터치")  # 저장한 시리즈 이름
        self.assertTrue(any(hooks.is_question(t) for t in k["titles"][:2]), k["titles"])  # 질문형 먼저
        long_ = [t for t in k["titles"] if len(t) > 20]
        self.assertTrue(all(k["titles"].index(t) >= len(k["titles"]) - len(long_) for t in long_), k["titles"])  # 20자 넘는 후보는 뒤로
        info = dict(zip(k["titles"], k["titleInfo"]))
        for t in long_:
            self.assertIn("길어요", info[t]["tags"])
        self.assertIn("시리즈", info["[1분 풋살 기술] 퍼스트 터치"]["tags"])
        self.assertIn("#풋살사관학교", k["hashtags"])
        self.assertTrue(any("질문형" in a for a in k["applied"]) and any("시리즈" in a for a in k["applied"]))
        self.assertEqual(k["maxLen"], 20)

    def test_series_rules(self):
        hints = {"series": [{"name": "[1분 풋살 기술]", "desc": "같은 틀의 기술 한 개 쇼츠"}, {"name": "[N가지 총정리]", "desc": "쇼츠로 낸 기술을 모은 롱폼"},
                            {"name": "[국대 vs 국대]", "desc": "대결"}, {"name": "[풋살사관학교 기초반 EP01~]", "desc": "강좌"},
                            {"name": "[최경진 감독을 뚫어라] N호 도전자", "desc": "참여"}]}
        self.assertEqual(upload.series_titles(hints, ["퍼스트 터치"], "shorts", 45), ["[1분 풋살 기술] 퍼스트 터치"])
        self.assertEqual(upload.series_titles(hints, ["퍼스트 터치"], "shorts", 90), [])  # 1분 넘는 쇼츠에 '1분' X
        self.assertEqual(upload.series_titles(hints, ["퍼스트 터치", "패스"], "long", 600, ["퍼스트 터치", "패스"]), ["[2가지 총정리] 퍼스트 터치·패스"])
        self.assertEqual(upload.series_titles(hints, ["패스"], "long", 600, ["패스"]), [])  # 주제 하나면 총정리 X
        c = {"series": strategy.preset("C")["series"]}  # 방향 C 초안 그대로 ('쇼츠로 낸 기술을 모은 롱폼'은 롱폼 시리즈)
        self.assertEqual(upload.series_titles(c, ["퍼스트 터치", "패스"], "long", 600, ["퍼스트 터치", "패스"]), ["[2가지 총정리] 퍼스트 터치·패스"])
        self.assertEqual(upload.series_titles(c, ["퍼스트 터치"], "shorts", 45), ["[1분 풋살 기술] 퍼스트 터치"])
        a = {"series": strategy.preset("A")["series"]}  # 기초반 EP01~ · N호 도전자 · 국대 vs 국대
        self.assertEqual(upload.series_titles(a, ["슈팅"], "shorts", 45), [])
        self.assertEqual(upload.series_titles(a, ["슈팅"], "shorts", 45, guests=[{"name": "이한울"}]), ["[국대 vs 국대] 이한울과 1대1 슈팅"])
        self.assertEqual(upload.series_titles({"series": hints["series"][2:]}, ["슈팅"], "long", 600, guests=[{"name": "이한울"}]),
                         ["[국대 vs 국대] 이한울과 1대1 슈팅"])  # 대결 시리즈는 게스트가 있을 때만 · 누구와 붙었는지 · 회차 번호 자리는 안 씀
        no = {"sum": False, "vs": False}
        self.assertEqual(upload.series_titles(a, ["슈팅"], "shorts", 45, guests=[{"name": "이한울"}], fit=no), [])  # 대결 영상이 아니면 X
        self.assertEqual(upload.series_titles(hints, ["2대1 패스", "수비"], "long", 600, ["2대1 패스", "수비"], fit=no), [])

    def test_series_fit(self):
        """검토: '[N가지 총정리]'는 4분 넘는 롱폼(또는 여러 영상 모음)에서 기술 이름이 저마다 두 번 넘게 나올 때만 · 대결은 대사·영상 이름으로."""
        texts = ["오늘은 2대1 패스 알려 드릴게요", "2대1 패스는 주고 바로 뛰어요", "수비가 따라와요", "리턴 패스는 원터치", "리턴 패스 한 번 더"]
        self.assertFalse(upload.series_fit(texts, ["2대1 패스", "수비", "리턴 패스"], 169)["sum"])  # 3분 레슨 하나 · '수비'는 한 번 스침
        self.assertFalse(upload.series_fit(texts, ["2대1 패스", "수비", "리턴 패스"], 600)["sum"])
        self.assertTrue(upload.series_fit(texts, ["2대1 패스", "리턴 패스"], 600)["sum"])
        self.assertFalse(upload.series_fit(["시선 앞", "시선 아래", "중심 낮게", "중심"], ["기본 자세", "시선", "중심"], 600)["sum"])
        self.assertTrue(upload.series_fit(texts, ["2대1 패스", "리턴 패스"], 100, sq={"items": [{"track": "V1", "media": "main"}, {"track": "V1", "media": "m2"}]})["sum"])
        self.assertTrue(upload.series_fit(["골!"], [], 20, about="국가대표 1대1 대결")["vs"])
        self.assertFalse(upload.series_fit(["패스 연습"], [], 20, about="LESSON01_인사이드 패스")["vs"])

    def test_question_pool_skips_taken_and_says_so(self):
        """검토: 질문형 틀 4개 중 이미 다른 키트가 쓴 틀은 건너뛰고 · 넣지 못했으면 '0개를 넣었어요'라고 하지 않음."""
        self.todos()
        hints = upload.strategy_hints()
        others = [{"name": f"o{i}.mp4", "source": {"id": "x"}, "title": t, "topics": [tp]}
                  for i, (t, tp) in enumerate((("패스가 안 된다면?", "패스"), ("이 슈팅 가능?", "슈팅")))]
        k = {"name": "x.mp4", "source": {"id": "a"}, "format": "shorts", "duration": 40.0, "topics": ["퍼스트 터치"], "hashtags": [],
             "titles": ["퍼스트 터치 꿀팁", "퍼스트 터치 제대로 하는 법"]}
        upload.shape_titles(k, hints, others)
        qs = [t for t in k["titles"] if hooks.is_question(t)]
        self.assertEqual(qs, ["퍼스트 터치, 왜 자꾸 안 될까?", "퍼스트 터치 제대로 하고 있나요?"])
        others += [{"name": f"p{i}.mp4", "source": {"id": "x"}, "title": hooks.fill(t, ["드리블"]), "topics": ["드리블"]} for i, t in enumerate(hooks.QUESTION_TPL)]
        k = {"name": "x.mp4", "source": {"id": "a"}, "format": "long", "duration": 300.0, "topics": ["퍼스트 터치"], "hashtags": [], "titles": ["퍼스트 터치 꿀팁"]}
        upload.shape_titles(k, hints, others)
        upload.annotate(k, hints, others)
        msg = [a for a in k["applied"] if "질문형" in a][0]
        self.assertNotIn("0개", msg)
        self.assertIn("같은 틀", msg)
        self.assertIn("해시태그 할 일 → #풋살사관학교를 넣었어요", upload._applied({"titles": [], "hashtags": ["#풋살사관학교"]}, {"hashtags": [{"tag": "#풋살사관학교"}]}))
        self.assertIn("#풋살을 넣었어요", upload._applied({"titles": [], "hashtags": ["#풋살"]}, {"hashtags": [{"tag": "#풋살"}]})[0])
        self.assertTrue(upload._is_series("[3가지 총정리] 패스·슈팅·턴", "[N가지 총정리]"))

    def test_guest_titles_respect_length_todo(self):
        g = [{"name": "이한울", "bio": "현 풋살 국가대표 · 강원FS"}]
        self.assertEqual(upload.guest_titles(g, ["퍼스트 터치"], 45), ["현 풋살 국가대표 이한울의 퍼스트 터치 1분 꿀팁", "퍼스트 터치 1분 꿀팁 (feat. 이한울)"])
        short = upload.guest_titles(g, ["퍼스트 터치"], 45, max_len=20)
        self.assertTrue(all(len(t) <= 20 for t in short), short)  # 할 일 '제목 20자 안쪽'이면 짧은 꼴
        self.assertTrue(all("이한울" in t for t in short))

    def test_build_kit_without_strategy_unchanged(self):
        k = {"name": "x.mp4", "source": {"id": ""}, "format": "long", "duration": 300.0, "topics": ["패스"], "hashtags": ["#풋살"],
             "titles": ["패스 꿀팁 (최경진 감독)", "패스, 이것만 알면 달라집니다", "패스가 안 되는 진짜 이유"]}
        upload.shape_titles(k, upload.strategy_hints(), [])
        self.assertEqual(k["titles"][0], "패스 꿀팁 (최경진 감독)")
        self.assertEqual(k["hashtags"], ["#풋살"])


class KitBuildTests(KitBase):
    """build_kit 전체: 쇼츠 주제어 물려받기 · 같은 촬영본 키트끼리 다른 첫 줄·제목 · 게스트."""

    def setUp(self):
        super().setUp()
        self.cfg = mock.patch.dict(core.CONFIG, {"channel_url": OUR})
        self.cfg.start()
        strategy._CACHE.clear()
        strategy._SLIM.clear()

    def tearDown(self):
        self.cfg.stop()
        super().tearDown()

    def project(self, name, seqs, segs=None):
        caps = [{"id": str(i), "start": s["start"], "end": s["end"], "text": s["text"]} for i, s in enumerate(segs or fixture_segments())]
        out = []
        for sid, nm, fmt, a, b, ttl in seqs:
            items = [{"id": "v" + sid, "track": "V1", "media": "main", "in": a, "out": b, "start": 0.0, "speed": 1.0, "link": "l" + sid},
                     {"id": "a" + sid, "track": "A1", "media": "main", "in": a, "out": b, "start": 0.0, "speed": 1.0, "link": "l" + sid}]
            out.append({"id": sid, "name": nm, "format": fmt, "v": 2, "items": items, "markers": [], "titles": ttl, "tracks": editor.default_tracks()})
        proj = {"source": name, "info": {"duration": 720.0, "width": 160, "height": 90}, "captions": caps, "v": 2, "rev": 1, "sequences": out}
        editor._ppath(name).write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")

    def test_shorts_without_terms_inherit_source_topic(self):
        segs = [{"start": 0.0, "end": 3.0, "text": "오늘은 인사이드 패스 알려 드릴게요."}, {"start": 4.0, "end": 7.0, "text": "인사이드 패스는 발 안쪽으로 차요."}]
        segs += [{"start": 100.0 + i * 5, "end": 104.0 + i * 5, "text": t} for i, t in enumerate(FOLLOW)]
        name = self.add_video(self.NAME, segs=segs)
        self.project(name, [("f1", "쇼츠 1 · 팔로우 스루", "shorts", 100.0, 145.0, []), ("l1", "롱폼", "long", 100.0, 145.0, [])], segs)
        kit = upload.build_kit(name, "f1")
        self.assertEqual(kit["topics"][:2], ["팔로우 스루", "인사이드 패스"])  # 쇼츠는 그 구간의 요점이 먼저 · 원본 주제가 둘째
        blob = " ".join(kit["titles"] + kit["tags"] + kit["hashtags"])
        for w in ("차고", "넓은"):
            self.assertNotIn(w, blob)
        self.assertEqual(kit["hashtags"][:4], ["#shorts", "#풋살", "#팔로우스루", "#인사이드패스"])  # 두 해시태그 다
        self.assertEqual(upload.build_kit(name, "l1")["topics"][0], "인사이드 패스")  # 편집본(롱폼)은 원본 주제가 먼저

    def test_overlay_hook_cut_is_not_default_title(self):
        """검토: 자동 가편집의 큰 제목 글자가 첫 문장 16자 조각·인사·순서 말이면 기본 제목으로 쓰지 않음 (손으로 쓴 글·온전한 문장은 그대로)."""
        cases = [("아이고, 공이 조금 뒤로 갔네", ["아이고, 공이 조금 뒤로 갔네요.", "패스는 받는 사람 발 쪽으로"]),
                 ("오늘은 슈팅 챌린지예요. 다섯", ["오늘은 슈팅 챌린지예요.", "다섯 번 차서 몇 개 넣는지 볼게요."]),
                 ("안녕하세요. 오늘은 패스하고", ["안녕하세요. 오늘은 패스하고 움직이는 연습이에요."]),
                 ("네 번째 빗나갔어요. 네번째", ["네 번째 빗나갔어요.", "네번째도 아쉽네요."]),
                 ("자, 두번째 포인트는 디딤밤이", ["자, 두번째 포인트는 디딤밤이 공이랑 너무 가까우면 안 돼요."])]
        for hook, texts in cases:
            self.assertIsNone(upload._overlay_hook(hook, texts), hook)
        self.assertEqual(upload._overlay_hook("디딤발 하나로 끝", ["디딤발은 공 옆에"]), "디딤발 하나로 끝")  # 손으로 쓴 글
        self.assertEqual(upload._overlay_hook("이 차이가 정말 중요해요", ["이 차이가 정말 중요해요.", "터치가 길었네요."]), "이 차이가 정말 중요해요")
        self.assertEqual(upload._overlay_hook("퍼스트 터치 꿀팁", ["퍼스트 터치는"]), "퍼스트 터치 꿀팁")
        segs = [{"start": 0.0, "end": 3.0, "text": "아이고, 공이 조금 뒤로 갔네요."}, {"start": 3.5, "end": 7.0, "text": "패스는 받는 사람 발 쪽으로 정확하게 주세요."},
                {"start": 8.0, "end": 11.0, "text": "패스하고 바로 움직이세요."}]
        name = self.add_video(self.NAME, segs=segs)
        self.project(name, [("s1", "쇼츠 1", "shorts", 0.0, 12.0, [{"id": "t1", "text": "아이고, 공이 조금 뒤로 갔네", "start": 0, "dur": 3}])], segs)
        kit = upload.build_kit(name, "s1")
        self.assertNotIn("아이고, 공이 조금 뒤로 갔네", kit["titles"])

    def test_same_source_kits_differ(self):
        name = self.add_video(self.NAME)
        self.project(name, [("s1", "쇼츠 1", "shorts", 0.0, 45.0, []), ("s2", "쇼츠 2", "shorts", 60.0, 105.0, [])])
        k1 = upload.build_kit(name, "s1")
        k2 = upload.build_kit(name, "s2")
        self.assertNotEqual(hooks.norm_title(k1["title"]), hooks.norm_title(k2["title"]))
        q1, q2 = (k["description"].split("\n")[0] for k in (k1, k2))
        if q1.startswith("“") and q2.startswith("“"):
            self.assertNotEqual(q1, q2)
        self.check_rules(k2)
        loaded = upload.load_kit(name, "s2", live=True)
        self.assertEqual(len(loaded["titleInfo"]), len(loaded["titles"]))  # 열 때마다 꼬리표

    def test_guests(self):
        name = self.add_video(self.NAME)
        kit = upload.build_kit(name)
        first = kit["title"]
        kit = upload.set_guests(name, None, [{"name": "이한울", "bio": "현 풋살 국가대표 · 강원FS"}, {"name": "최경진"}, {"name": "<b>"}])
        self.assertEqual(kit["guests"], [{"name": "이한울", "bio": "현 풋살 국가대표 · 강원FS"}])  # 감독님·글자 없는 이름은 뺌
        self.assertIn("이한울", kit["title"])
        self.assertTrue(any("(feat. 이한울)" in t for t in kit["titles"]))
        self.assertIn("이한울", kit["tags"])
        self.assertIn("현 풋살 국가대표", kit["tags"])  # 이력은 '·'로 나눠 따로 (검토)
        self.assertIn("강원FS", kit["tags"])
        self.assertIn("#이한울", kit["hashtags"])
        self.assertIn("#이한울", kit["description"])
        rows = kit["description"].split("\n")
        self.assertEqual(rows[rows.index("최경진 감독 (풋살사관학교)") + 1], "이한울 (현 풋살 국가대표 · 강원FS)")  # 감독님 줄 바로 뒤
        self.check_rules(kit)
        self.assertLessEqual(upload.tags_len(kit["tags"]), 500)
        self.assertIn("게스트", kit["titleInfo"][0]["tags"])
        # 바꾸면 예전 게스트는 남지 않음
        kit = upload.set_guests(name, None, [{"name": "김영권", "bio": ""}])
        blob = kit["description"] + " ".join(kit["titles"] + kit["tags"] + kit["hashtags"])
        self.assertNotIn("이한울", blob)
        self.assertIn("김영권", kit["description"])
        kit = upload.set_guests(name, None, [])
        blob = kit["description"] + " ".join(kit["titles"] + kit["tags"] + kit["hashtags"])
        self.assertNotIn("김영권", blob)
        self.assertEqual(kit["title"], first)
        # 손으로 고른 제목은 게스트를 넣어도 그대로
        upload.save_edits(name, None, title="내가 쓴 제목")
        kit = upload.set_guests(name, None, [{"name": "이한울", "bio": ""}])
        self.assertEqual(kit["title"], "내가 쓴 제목")
        # 다시 만들어도 게스트는 그대로
        kit = upload.build_kit(name)
        self.assertEqual([g["name"] for g in kit["guests"]], ["이한울"])
        self.assertIn("이한울", kit["description"])

    def test_guest_book_and_episode(self):
        """검토: 같은 촬영본의 롱폼·쇼츠는 한 회 (쇼츠 기본 제목이 '2탄'이 되지 않음) · 다른 날 찍은 영상부터 2탄."""
        name = self.add_video(self.NAME)
        self.project(name, [("s1", "쇼츠 1", "shorts", 0.0, 45.0, []), ("s2", "쇼츠 2", "shorts", 60.0, 105.0, [])])
        upload.build_kit(name)
        upload.set_guests(name, None, [{"name": "이한울", "bio": "현 풋살 국가대표"}])
        book = upload.guest_book()
        self.assertEqual((book[0]["name"], book[0]["bio"], book[0]["n"]), ("이한울", "현 풋살 국가대표", 1))
        for sid in ("s1", "s2"):
            upload.build_kit(name, sid)
            kit = upload.set_guests(name, sid, [{"name": "이한울", "bio": ""}])
            self.assertFalse(any("탄" in t for t in kit["titles"]), kit["titles"])
        self.assertEqual(upload.guest_book()[0]["bio"], "현 풋살 국가대표")  # 이력은 비워도 적어 둔 것 그대로
        self.assertEqual(upload.guest_episode("이한울", name, "s1"), 1)
        other = self.add_video("20261009_VS02_국가대표 2차전.mp4")
        upload.build_kit(other)
        kit = upload.set_guests(other, None, [{"name": "이한울 선수", "bio": ""}])  # 부름말을 붙여 써도 같은 사람
        self.assertTrue(kit["titles"][0].startswith("이한울 선수와 함께하는 "), kit["titles"])
        self.assertTrue(kit["titles"][0].endswith(" 2탄"), kit["titles"])
        self.assertIn("#이한울", kit["hashtags"])
        self.assertNotIn("#이한울선수", kit["hashtags"])
        self.assertEqual([g["name"] for g in upload.guest_book()], ["이한울"])  # 적어 둔 게스트도 한 사람
        upload.set_guests(name, "s2", [])  # 잘못 넣었다 빼면 그 영상에 나온 기록도 지움
        upload.set_guests(name, "s1", [])
        upload.set_guests(name, None, [])
        self.assertEqual(upload.guest_episode("이한울", other), 1)
        self.assertEqual(upload.guest_key("이한울 선수"), "이한울")
        self.assertEqual(upload.clean_guests([{"name": "이한울"}, {"name": "이한울 선수"}, {"name": "최경진 감독"}]), [{"name": "이한울", "bio": ""}])

    def test_guest_removal_refills_candidates(self):
        name = self.add_video(self.NAME)
        kit = upload.build_kit(name)
        n = len(kit["titles"])
        upload.set_guests(name, None, [{"name": "이한울", "bio": ""}])
        kit = upload.set_guests(name, None, [])
        self.assertEqual(len(kit["titles"]), n)  # 게스트 제목을 뺀 자리를 다시 채움

    def test_save_edits_reuses_live_fields(self):
        """검토: 1초마다 저장하는 고친 글은 다른 키트·편집본·채널 목록을 다시 읽지 않음 (열 때 붙인 꼬리표·올릴 날을 그대로)."""
        name = self.add_video(self.NAME)
        upload.build_kit(name)
        opened = upload.load_kit(name, None, live=True)
        with mock.patch.object(upload, "taken_titles", side_effect=AssertionError("다시 읽음")), \
                mock.patch.object(upload, "stock", side_effect=AssertionError("다시 읽음")):
            kit = upload.save_edits(name, None, title="고친 제목")
            self.assertIsNone(upload.load_kit(name, None).get("titleInfo"))  # 기본 load_kit 은 가볍게
        self.assertEqual(kit["title"], "고친 제목")
        self.assertEqual(kit["titleInfo"], opened["titleInfo"])
        self.assertEqual(kit["taken"], opened["taken"])

    def test_guest_hint_from_speech(self):
        self.assertEqual(upload.guest_hint(["이한울 선수가 먼저 해요", "자 이한울 선수 한 번 더", "최경진 감독이에요 최경진 감독"]), ["이한울"])
        self.assertEqual(upload.guest_hint(["박영재 선수"]), [])  # 한 번만 부른 이름은 안 씀

    def test_route_guests(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        import app
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        with mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log"):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                name = self.add_video(self.NAME)

                def post(body):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/upload/kit", data=json.dumps(body).encode(),
                                                 headers={"Content-Type": "application/json"}, method="POST")
                    try:
                        with urllib.request.urlopen(req, timeout=30) as r:
                            return r.status, json.loads(r.read())
                    except urllib.error.HTTPError as e:
                        return e.code, json.loads(e.read())
                self.assertEqual(post({"name": name, "seq": "", "guests": [{"name": "이한울"}]})[0], 400)  # 키트가 먼저
                self.assertEqual(post({"name": name, "seq": ""})[0], 200)
                code, j = post({"name": name, "seq": "", "guests": [{"name": "이한울", "bio": "현 국가대표"}]})
                self.assertEqual(code, 200, j)
                self.assertIn("#이한울", j["kit"]["hashtags"])
                self.assertIn("titleInfo", j["kit"])
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
