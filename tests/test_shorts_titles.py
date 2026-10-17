"""쇼츠 제목 새 말투 · 올리는 제목 끝 이모지 (BR-250 · BR-251 · D-435 · D-436) — 저장소 폴더에서 python3 -m unittest tests.test_shorts_titles

- 새 말투(감독님 한마디 · 묻고 바로 답 · 반전 꼬리 '(?)' · '알아도 못 따라 하는 …')는 받아쓰기·주제어·쇼츠 길이로만 (말하지 않은 숫자 X)
- 글자 수는 쇼츠 위 큰 제목 셈(editor._norm · 16자 · 묻고 답하는 꼴 20자) · 이모지 없음
- 쇼츠 키트의 첫 후보(기본 제목)는 그대로 · 새 말투는 2·3번째 · 이모지는 첫 후보에 하나만(이미 있으면 안 붙임) · 화면 큰 제목에는 없음
"""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor  # noqa: E402
import hooks  # noqa: E402
import upload  # noqa: E402
from tests.test_upload import KitBase  # noqa: E402

NUM = re.compile(r"\d+|한|두|세|네|다섯")

# 트래핑 쇼츠: 시키는 말 · 물음 + 바로 답 · 인사·홍보 말
TRAP = ["자, 오늘은 트래핑 알려 드릴게요.", "공 받을 때 발로 하지 마세요.", "공이 자꾸 튄다고요?", "발목 힘 빼세요.",
        "구독 꼭 해 주세요."]
SHOOT = ["슈팅은 발등으로 낮게 차세요.", "슈팅이 왜 뜰까요?", "몸이 뒤로 눕기 때문이에요.", "이건 3초 만에 고칠 수 있어요."]


def hook_len(t):
    return len(editor._norm(t))


class VoiceTitleTests(unittest.TestCase):
    """hooks.voice_titles: 말투마다 받아쓰기에서만 · 글자 수 · 숫자 지어내지 않음."""

    def test_shapes_from_transcript(self):
        out = hooks.voice_titles(TRAP, ["트래핑"], 40, n=4)
        self.assertEqual(out[0], '"트래핑, 공 받을 때 발로 하지 마세요"')  # 감독님 한마디 · 하지 말라는 말 먼저 · 주제어를 앞에
        self.assertEqual(out[1], "공이 자꾸 튄다고요? 발목 힘 빼세요")  # 물음 + 바로 다음 문장
        self.assertEqual(out[2], "트래핑 1분 만에 배우기(?)")  # 40초 쇼츠 = '1분 만에'
        self.assertEqual(len(hooks.voice_titles(TRAP, ["트래핑"], 40)), hooks.VOICE_MAX)

    def test_grounded_in_transcript(self):
        """따옴표 말·물음·답은 받아쓰기 문장 그대로 (앞 군말·끝 마침표만 뗌) · 숫자는 받아쓰기에 나온 것만."""
        body = " ".join(SHOOT)
        out = hooks.voice_titles(SHOOT, ["슈팅"], 50, n=4)
        self.assertEqual(out[:3], ['"슈팅은 발등으로 낮게 차세요"', "슈팅이 왜 뜰까요? 몸이 뒤로 눕기 때문이에요", "슈팅 3초 만에 고치기(?)"])
        for t in out:
            for piece in re.split(r"\?\s+", t.strip('"').replace("(?)", "")):
                words = [w for w in re.findall(r"[가-힣A-Za-z0-9]+", piece) if w not in ("슈팅", "만에", "고치기", "배우기")]
                self.assertTrue(all(w in body for w in words), (t, words))
            for n in re.findall(r"\d+", t):
                self.assertIn(n, body, t)  # 말하지 않은 숫자 X

    def test_no_invented_number_over_one_minute(self):
        """길이를 모르거나 1분 넘는 쇼츠는 '1분 만에'를 붙이지 않음 · 받아쓰기에 걸린 시간이 없으면 반전 꼬리 없음."""
        texts = ["드리블할 때 발바닥을 쓰세요.", "드리블은 몸을 낮추는 게 핵심이에요."]
        for dur in (None, 0, 90.0):
            out = hooks.voice_titles(texts, ["드리블"], dur, n=4)
            self.assertFalse(any("(?)" in t for t in out), out)
            self.assertFalse(any(re.search(r"\d", t) for t in out), out)

    def test_char_limits(self):
        """쇼츠 위 큰 제목 셈(띄어쓰기·. , ! ? ~ … 안 셈 · 따옴표·괄호는 셈): 16자 · 묻고 답하는 꼴 20자."""
        long_order = ["공을 받을 때는 절대로 발바닥으로 세게 밟지 마세요."]  # 21자 → 통째로 안 씀
        self.assertEqual(hooks.voice_titles(long_order, ["트래핑"], 90, n=4), [])
        self.assertEqual(hook_len('"트래핑, 공 받을 때 발로 하지 마세요"'), 16)  # 따옴표 2자 셈
        self.assertEqual(hook_len("트래핑 1분 만에 배우기(?)"), 12)  # '(?)' 의 괄호 2자 셈 · 물음표는 안 셈
        for texts, tp in ((TRAP, ["트래핑"]), (SHOOT, ["슈팅"]), (["팬텀 드리블 알려 드릴게요."], ["팬텀 드리블"]),
                          (["스네이크 드리블은 공을 몸 가까이 두세요."], ["스네이크 드리블"])):
            for t in hooks.voice_titles(texts, tp, 40, n=4):
                self.assertLessEqual(hook_len(t), editor.HOOK_ASK if "? " in t else editor.HOOK_MAX, t)
        # 쉼표 뒤 마지막 마디만 (감독님 말 그대로 · 가운데서 자르지 않음)
        self.assertEqual(hooks.voice_titles(["공을 받을 때 몸이 앞으로 쏠리면, 무릎을 굽히세요"], ["트래핑"], 90),
                         ['"트래핑, 무릎을 굽히세요"'])

    def test_skips_plain_and_promo_lines(self):
        """뜻 없는 시키는 말('꼭 해 보세요')·인사·홍보·확인만 하는 물음('아시겠죠?')·사람 이름 부른 말은 안 씀."""
        texts = ["이 드리블 꿀팁 꼭 해 보세요!", "구독과 좋아요 눌러 주세요.", "아시겠죠?", "네 좋아요.", "영재 선수 따라 하세요."]
        self.assertEqual(hooks.voice_titles(texts, ["풋살"], 90, n=4), [])
        self.assertEqual(hooks.voice_titles(["어때요?", "발목 힘을 빼세요."], ["트래핑"], 90, n=4), ['"트래핑, 발목 힘을 빼세요"'])

    def test_cant_copy_only_for_skills(self):
        self.assertIn("알아도 못 따라 하는 팬텀 드리블", hooks.voice_titles(["팬텀 드리블 알려 드릴게요."], ["팬텀 드리블"], 90, n=4))
        self.assertFalse(any(t.startswith("알아도") for t in hooks.voice_titles(["패스 알려 드릴게요."], ["패스"], 90, n=4)))
        self.assertFalse(any(t.startswith("알아도") for t in hooks.voice_titles(["1대1 알려 드릴게요."], ["1대1"], 90, n=4)))

    def test_no_topic_words_without_real_topic(self):
        """주제어가 풋살 용어가 아니면('풋살'·'기본 자세') 주제어가 드는 말투(반전 꼬리·알아도…)는 안 만들고 따옴표 말에 앞말도 안 붙임."""
        for tp in (["풋살"], ["기본 자세"], [], ["시선"]):
            out = hooks.voice_titles(TRAP, tp, 40, n=4)
            self.assertEqual(out, ['"공 받을 때 발로 하지 마세요"', "공이 자꾸 튄다고요? 발목 힘 빼세요"], (tp, out))

    def test_no_emoji_in_voice(self):
        for t in hooks.voice_titles(["⚽ 공 받을 때 발로 하지 마세요 🔥"], ["트래핑"], 40, n=4):
            self.assertFalse(hooks.has_emoji(t), t)


class EmojiTitleTests(unittest.TestCase):
    def test_topic_mapped(self):
        self.assertEqual(hooks.emoji_title("슈팅 1분 꿀팁", "슈팅"), "슈팅 1분 꿀팁 🔥")
        self.assertEqual(hooks.emoji_title("수비 이렇게 하면 안 돼요", "수비"), "수비 이렇게 하면 안 돼요 🧱")
        self.assertEqual(hooks.emoji_title("골키퍼 선방 비결", "골키퍼"), "골키퍼 선방 비결 🧱")
        self.assertEqual(hooks.emoji_title("트래핑!", "트래핑"), "트래핑! ⚽")
        self.assertEqual(hooks.emoji_title("풋살 꿀팁", None), "풋살 꿀팁 ⚽")

    def test_not_doubled(self):
        for t in ('🔥풋살기술🔥 "팬텀 드리블" 속성강의', "패스 꿀팁 ⚽", "골 ⚽️ 모음", "★ 슈팅 꿀팁"):
            self.assertIsNone(hooks.emoji_title(t, "슈팅"), t)
        self.assertIsNone(hooks.emoji_title("가" * 99, "패스"))  # 100자 넘으면 X
        self.assertIsNone(hooks.emoji_title("", "패스"))


class ShortsKitTitleTests(KitBase):
    """쇼츠 키트: 첫 후보 그대로 · 새 말투 2·3번째 · 이모지 후보 하나 · 화면 큰 제목에는 이모지 없음."""
    SEGS = [{"start": 1 + 4 * i, "end": 4 + 4 * i, "text": t} for i, t in enumerate(TRAP[:4] + ["트래핑은 공을 죽이는 거예요."])]

    def kit_without_new(self, name, seq=None):
        with mock.patch.object(hooks, "voice_titles", return_value=[]), mock.patch.object(hooks, "emoji_title", return_value=None):
            return upload.build_kit(name, seq, save=False)

    def test_first_title_unchanged_and_new_shapes_second_third(self):
        name = self.add_video("트래핑 쇼츠.mp4", "short", self.SEGS)
        old = self.kit_without_new(name)
        kit = upload.build_kit(name)
        self.check_rules(kit)
        self.assertEqual(kit["title"], old["title"])
        self.assertEqual(kit["titles"][0], old["titles"][0])
        self.assertEqual(kit["titleNew"], ['"트래핑, 공 받을 때 발로 하지 마세요"', "공이 자꾸 튄다고요? 발목 힘 빼세요"])
        self.assertEqual(kit["titles"][1:3], kit["titleNew"])
        self.assertEqual(kit["titles"][3], hooks.emoji_title(kit["titles"][0], kit["topics"][0]))
        self.assertEqual(kit["titles"][4], old["titles"][1])  # 예전 2번째가 그 뒤
        self.assertEqual(upload._emoji_twins(kit), {kit["titles"][3]})  # 이모지를 붙인 후보는 하나 (우리 채널 틀의 '🔥…🔥'은 원래 있던 것)
        self.assertFalse(hooks.has_emoji(kit["title"]))
        self.assertFalse(hooks.has_emoji(kit["description"].split("\n")[0]))
        saved = json.loads(next(self.out.glob("*_올리기.json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["titleNew"], kit["titleNew"])  # 저장 (게스트를 바꿔 다시 세울 때 씀)

    def test_long_kit_unchanged(self):
        kit = upload.build_kit(self.add_video(self.NAME))
        self.assertNotIn("titleNew", kit)
        self.assertFalse(any(hooks.has_emoji(t) for t in kit["titles"] if not t.startswith("🔥")), kit["titles"])

    def test_emoji_not_doubled_on_emoji_first(self):
        k = {"name": "x.mp4", "source": {"id": "a"}, "format": "shorts", "duration": 40.0, "topics": ["슈팅"], "hashtags": [],
             "titles": ["🔥슈팅🔥 1분 꿀팁", "슈팅 제대로 하는 법"], "titleNew": ['"슈팅은 발등으로 낮게 차세요"']}
        upload.shape_titles(k, {}, [])
        self.assertEqual(k["titles"], ["🔥슈팅🔥 1분 꿀팁", '"슈팅은 발등으로 낮게 차세요"', "슈팅 제대로 하는 법"])
        k["titles"][0] = "슈팅 1분 꿀팁"
        upload.shape_titles(k, {}, [])
        self.assertEqual(k["titles"], ["슈팅 1분 꿀팁", '"슈팅은 발등으로 낮게 차세요"', "슈팅 1분 꿀팁 🔥", "슈팅 제대로 하는 법"])
        upload.shape_titles(k, {}, [])  # 다시 세워도 같음 (이모지 후보·새 말투가 두 번 들어가지 않음)
        self.assertEqual(k["titles"], ["슈팅 1분 꿀팁", '"슈팅은 발등으로 낮게 차세요"', "슈팅 1분 꿀팁 🔥", "슈팅 제대로 하는 법"])

    def test_new_shapes_follow_strategy_and_overlap(self):
        """전략 할 일(N자 안쪽)·다른 키트와 겹침은 예전 꼬리표대로 — 긴 새 말투는 뒤로 · 이미 올린 제목과 같은 새 말투는 안 넣음."""
        k = {"name": "x.mp4", "source": {"id": "a"}, "format": "shorts", "duration": 40.0, "topics": ["트래핑"], "hashtags": [],
             "titles": ["트래핑 꿀팁", "트래핑 제대로 하는 법"], "titleNew": ['"트래핑, 공 받을 때 발로 하지 마세요"', "트래핑 1분 만에 배우기(?)"]}
        upload.shape_titles(k, {"maxLen": 16}, [])
        self.assertEqual(k["titles"][0], "트래핑 꿀팁")
        self.assertEqual(k["titles"][-1], '"트래핑, 공 받을 때 발로 하지 마세요"')  # 16자 넘는 후보는 뒤로
        others = [{"name": "o.mp4", "source": {"id": "x"}, "title": "트래핑 1분 만에 배우기(?)", "topics": ["트래핑"]}]
        k["titles"] = ["트래핑 꿀팁", "트래핑 제대로 하는 법"]
        upload.shape_titles(k, {}, others)
        self.assertNotIn("트래핑 1분 만에 배우기(?)", k["titles"])
        self.assertEqual(k["titles"][0], "트래핑 꿀팁")

    def test_question_todo_keeps_first(self):
        """'짧은 질문형 제목' 할 일이 켜져도 첫 후보는 예전과 같음 (새 말투·이모지 후보가 첫 자리로 오지 않음)."""
        hints = {"question": True}
        base = {"name": "x.mp4", "source": {"id": "a"}, "format": "shorts", "duration": 40.0, "topics": ["트래핑"], "hashtags": [],
                "titles": ["트래핑 꿀팁", "트래핑 제대로 하는 법"]}
        old = upload.shape_titles(dict(base, titles=list(base["titles"])), hints, [])
        new = upload.shape_titles(dict(base, titles=list(base["titles"]), titleNew=["공이 자꾸 튄다고요? 발목 힘 빼세요"]), hints, [])
        self.assertEqual(new["title"], old["title"])
        self.assertEqual(new["titles"][1], "공이 자꾸 튄다고요? 발목 힘 빼세요")  # 묻고 답하는 꼴은 질문형으로 앞
        applied = upload._applied(upload.annotate(new, hints, []), hints)
        self.assertTrue(any("질문형 후보 3개" in a for a in applied), applied)  # 이모지만 붙인 후보는 안 셈

    def test_guest_change_keeps_new_shapes(self):
        name = self.add_video("트래핑 쇼츠.mp4", "short", self.SEGS)
        kit = upload.build_kit(name)
        kit2 = upload.set_guests(name, None, [{"name": "이한울", "bio": "현 풋살 국가대표"}])
        n = len(kit2["guestAuto"]["titles"])
        self.assertTrue(n >= 1 and all("이한울" in t for t in kit2["titles"][:n]), kit2["titles"])  # 게스트 제목이 먼저 (예전 그대로)
        self.assertEqual(kit2["titles"][n:n + 2], kit["titleNew"])  # 새 말투는 그 바로 뒤
        self.assertEqual(upload._emoji_twins(kit2), {hooks.emoji_title(kit2["titles"][0], "트래핑")})
        kit3 = upload.set_guests(name, None, [])
        self.assertEqual(kit3["titles"][:4], kit["titles"][:4])

    def test_on_screen_titles_have_no_emoji(self):
        """쇼츠 위 큰 제목(editor.short_hooks)·MSG 첫머리에 이모지가 들어가지 않음 — 이모지는 올리는 제목 후보에만."""
        segs = [{"start": 1 + 4 * i, "end": 4 + 4 * i, "text": t} for i, t in enumerate(TRAP + SHOOT)]
        shorts = [{"start": 0, "end": 22, "title": TRAP[1], "keywords": ["트래핑"]}, {"start": 22, "end": 40, "title": SHOOT[0], "keywords": ["슈팅"]}]
        for h in editor.short_hooks(shorts, segs):
            self.assertFalse(hooks.has_emoji(h), h)
        self.assertNotIn("emoji", Path(editor.__file__).read_text(encoding="utf-8").lower())
        import msg
        self.assertNotIn("emoji_title", Path(msg.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
