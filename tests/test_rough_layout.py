"""자동 가편집 글자 자리 (E6) — 저장소 폴더에서 python3 -m unittest tests.test_rough_layout

· 위쪽 자막 스타일(도블락 같은 예능형 captionPos=top)의 쇼츠: 대사 자막이 쇼츠 내내 떠 있는 훅 제목과 같은 자리에 겹치던 것
  → 훅 아래(영상 위 칸)로 · 티저를 켠 롱폼은 티저 제목을 자막 아래로
· 자동 강조 큰 글씨(화면 위 30% 고정)가 감독님 얼굴(눈)을 가리던 것 → 그 장면 얼굴 상자를 피해 머리 위·옆·아래 빈자리로
글 상자는 미리보기(editor.html .txt)·내보내기(ASS)와 같은 셈(y 는 글 아래 끝 · 줄 높이 1.25)으로 봄. 영상·인터넷은 쓰지 않음
(얼굴 모델로 실제 사진을 보는 시험만 모델이 있을 때)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
TALKING_FACE = [0.434, 0.12, 0.13, 0.27]  # MSGRAW01 47.53초 '인사이드!' 장면의 얼굴 (화면 높이 12~39%)


def band(box):
    return box[1], box[3]


def inter(a, b):
    """두 (왼, 위, 오른, 아래) 상자가 겹치는 넓이."""
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def fbox(f):
    return (f[0], f[1], f[0] + f[2], f[1] + f[3])


class TextBoxTest(unittest.TestCase):
    def test_hook_two_lines(self):
        # 재현 수치: 2줄 노란 훅 '오늘은 슈팅 / 챌린지예요. 다섯' 은 화면 높이 약 0.08~0.22
        W, H = editor.frame_size("shorts")
        b = editor.text_box("오늘은 슈팅 챌린지예요. 다섯", editor.TITLE_STYLE, W, H)
        self.assertAlmostEqual(b[3], 0.22)
        self.assertAlmostEqual(b[1], 0.22 - (2 * 1.25 * 100 + 18) / 1920, places=4)
        one = editor.text_box("오늘의 꿀팁", editor.TITLE_STYLE, W, H)
        self.assertAlmostEqual(one[1], 0.22 - (1.25 * 100 + 18) / 1920, places=4)
        self.assertLess(one[2] - one[0], b[2] - b[0])
        left = editor.text_box("왼쪽", dict(editor.TITLE_STYLE, x=0.1, align="left"), W, H)
        self.assertAlmostEqual(left[0], 0.1)

    def test_caption_band(self):
        top, bottom = editor._cap_band(dict(editor.SHORTS_STYLE, y=0.15), 1920)
        self.assertAlmostEqual(bottom, 0.15)
        self.assertAlmostEqual(top, 0.15 - (1.25 * 72 + 16) / 1920, places=4)


class AutoSeqBase(unittest.TestCase):
    name = "슈팅 챌린지 [쇼츠].mp4"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="글자 자리 테스트 "))
        work = self.tmp / "작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        ps = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        lines = ["오늘은 슈팅 챌린지예요. 다섯 번 차서 몇 개 넣는지 볼게요.", "자 첫 번째 슈팅 갑니다.", "인사이드 패스가 핵심이에요!",
                 "무조건 발목을 고정하세요.", "두 번째도 같은 자세로 차 볼게요.", "골! 들어갔어요.", "세 번째는 왼발로 해 볼게요.",
                 "퍼스트 터치가 중요해요 공을 앞에 두세요.", "네 번째 슈팅은 낮게 깔아서 차요.", "마지막 다섯 번째예요.",
                 "인사이드로 강하게 차는 게 포인트예요.", "오늘 결과는 네 개 성공이에요. 구독 부탁드려요."]
        segs, t = [], 1.0
        for k, x in enumerate(lines * 3):
            segs.append({"start": round(t, 2), "end": round(t + 4.0, 2), "text": x})
            t += 6.5
        d = core.adir(self.name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": []}), encoding="utf-8")
        self.info = {"duration": t + 2.0, "width": 1920, "height": 1080, "fps": 30.0}


class ShortsHookTest(AutoSeqBase):
    def test_top_captions_go_below_hook(self):
        # 재현: 도블락 스타일(captionPos=top)로 쇼츠 → 흰 자막(y=0.15)이 노란 훅(0.08~0.22) 위에 그대로 겹침
        W, H = editor.frame_size("shorts")
        seqs = [q for q in editor.auto_sequences(self.name, self.info, {"captionPos": "top"}) if q["format"] == "shorts"]
        self.assertTrue(seqs)
        for q in seqs:
            hook = q["titles"][0]
            hb = editor.text_box(hook["text"], hook["style"], W, H)
            cb = editor._cap_band(q["captionStyle"], H)
            self.assertGreaterEqual(cb[0], hb[3] + editor.TXT_GAP - 1e-6, (q["name"], hook["text"], cb, hb))
            self.assertLess(cb[1], 0.33, "영상 위 검은 칸 안 (상자 배치 영상은 0.34 부터)")
            self.assertAlmostEqual(q["captionStyle"]["y"], 0.288, places=3)  # 훅 아래 끝 0.22 + 틈 + 한 줄 (≈ 0.30)

    def test_bottom_and_middle_captions_unchanged(self):
        for pos in ("bottom", "middle"):
            for q in editor.auto_sequences(self.name, self.info, {"captionPos": pos}, ("shorts",)):
                self.assertEqual(q["captionStyle"]["y"], editor.CAP_Y[pos])
        for q in editor.auto_sequences(self.name, self.info, None, ("shorts",)):
            self.assertEqual(q["captionStyle"]["y"], editor.SHORTS_STYLE["y"])

    def test_long_teaser_title_below_top_captions(self):
        # 롱폼 티저도 같은 제목 자리 → 위쪽 자막 스타일이면 티저 제목을 자막 아래로
        W, H = editor.frame_size("long")
        st = {"captionPos": "top", "introTeaser": {"on": True, "sec": 4}}
        q = editor.auto_sequences(self.name, self.info, st, ("long",))[0]
        teaser = [t for t in q["titles"] if t.get("plan") == "teaser"]
        self.assertTrue(teaser)
        tb = editor.text_box(teaser[0]["text"], teaser[0]["style"], W, H)
        cb = editor._cap_band(q["captionStyle"], H)
        self.assertGreaterEqual(tb[1], cb[1] + editor.TXT_GAP - 1e-3, (tb, cb))
        q2 = editor.auto_sequences(self.name, self.info, dict(st, captionPos="bottom"), ("long",))[0]
        self.assertEqual([t["style"]["y"] for t in q2["titles"] if t.get("plan") == "teaser"], [editor.TITLE_STYLE["y"]])


class EmphasisSpotTest(unittest.TestCase):
    W, H = editor.frame_size("long")
    ST = dict(editor.TITLE_STYLE, size=88, y=0.3)

    def spot(self, text, faces, avoid=(), st=None, fmt="long"):
        W, H = editor.frame_size(fmt)
        st = dict(st or self.ST)
        st["x"], st["y"] = editor.emphasis_spot(text, st, W, H, faces, avoid)
        return editor.text_box(text, st, W, H), st

    def test_talking_face_not_covered(self):
        # 재현: '인사이드!' 가 y=0.3 에 놓여 눈 위에 겹침 (말하는 장면 얼굴은 화면 높이 15~35%)
        old = editor.text_box("인사이드!", self.ST, self.W, self.H)
        self.assertGreater(inter(old, fbox(TALKING_FACE)), 0)
        cap = editor._cap_band(dict(editor.LONG_STYLE, y=0.85), self.H)
        for face_box in (TALKING_FACE, [0.2, 0.1, 0.14, 0.3], [0.68, 0.15, 0.12, 0.28], [0.43, 0.4, 0.14, 0.3]):
            for text in ("인사이드!", "퍼스트 터치 차이!", "인사이드 패스 핵심!"):
                with self.subTest(face=face_box, text=text):
                    b, _ = self.spot(text, [face_box], [cap])
                    self.assertEqual(inter(b, fbox(face_box)), 0.0)
                    self.assertFalse(b[1] < cap[1] and cap[0] < b[3], "대사 자막 줄과도 안 겹침")
                    self.assertTrue(0.0 <= b[0] and b[2] <= 1.0 and 0.0 <= b[1] and b[3] <= 1.0, b)

    def test_keeps_style_spot_when_free(self):
        _, st = self.spot("인사이드!", [])
        self.assertEqual((st["x"], st["y"]), (0.5, 0.3))
        _, st = self.spot("인사이드!", [[0.1, 0.5, 0.1, 0.2]])  # 얼굴이 아래쪽 옆에 있으면 원래 자리
        self.assertEqual((st["x"], st["y"]), (0.5, 0.3))

    def test_guess_without_face_model(self):
        b, _ = self.spot("인사이드!", editor.FACE_GUESS)
        self.assertEqual(inter(b, fbox(editor.FACE_GUESS[0])), 0.0)

    def test_shorts_avoid_hook_and_captions(self):
        W, H = editor.frame_size("shorts")
        hook = editor.text_box("오늘은 슈팅 챌린지예요. 다섯", editor.TITLE_STYLE, W, H)
        cap = editor._cap_band(dict(editor.SHORTS_STYLE, y=0.8), H)
        face_box = [0.42, 0.36, 0.16, 0.12]  # 상자 배치 영상 속 얼굴
        b, _ = self.spot("인사이드!", [face_box], [band(hook), cap], st=dict(editor.TITLE_STYLE, size=88, y=0.3), fmt="shorts")
        self.assertEqual(inter(b, fbox(face_box)), 0.0)
        for bd in (band(hook), cap):
            self.assertFalse(b[1] < bd[1] and bd[0] < b[3], (b, bd))

    def test_crowded_frame_picks_least_overlap(self):
        faces = [[0.05 + 0.15 * k, 0.05 + 0.3 * r, 0.12, 0.25] for k in range(6) for r in range(3)]
        b, st = self.spot("인사이드 패스 핵심!", faces)
        self.assertTrue(0.0 <= b[1] and b[3] <= 1.0)


class EmphasisAutoSeqTest(AutoSeqBase):
    name = "퍼스트 터치 레슨.mp4"

    def test_rough_cut_emphasis_avoids_face_at_that_scene(self):
        W, H = editor.frame_size("long")
        calls = []

        def fake_faces(name, items, a, b):
            calls.append((a, b))
            return [TALKING_FACE]
        st = {"emphasisTitles": {"perMin": 3.3, "color": "#FFE14D"}, "captionPos": "bottom"}
        with mock.patch.object(editor, "_faces_on_screen", side_effect=fake_faces):
            q = editor.auto_sequences(self.name, self.info, st, ("long",))[0]
        em = [t for t in q["titles"] if t.get("plan") == "emphasis"]
        self.assertTrue(em)
        self.assertEqual(len(calls), len(em))
        cb = editor._cap_band(q["captionStyle"], H)
        for t in em:
            b = editor.text_box(t["text"], t["style"], W, H)
            self.assertEqual(inter(b, fbox(TALKING_FACE)), 0.0, t)
            self.assertFalse(b[1] < cb[1] and cb[0] < b[3], t)

    def test_no_face_model_uses_common_face_spot(self):
        import face
        st = {"emphasisTitles": {"perMin": 3.3, "color": "#FFE14D"}}
        with mock.patch.object(face, "ready", return_value=False), mock.patch.object(face, "ensure") as ens:
            q = editor.auto_sequences(self.name, self.info, st, ("long",))[0]
        ens.assert_not_called()  # 가편집 때문에 모델을 내려받지 않음
        W, H = editor.frame_size("long")
        for t in [t for t in q["titles"] if t.get("plan") == "emphasis"]:
            self.assertEqual(inter(editor.text_box(t["text"], t["style"], W, H), fbox(editor.FACE_GUESS[0])), 0.0)

    def test_zoomed_clip_face_boxes(self):
        # 확대한 클립의 얼굴은 화면에서 그만큼 크게·바깥쪽으로 (가운데 기준)
        import face
        import thumb
        items = editor._items_from_cuts([{"in": 0.0, "out": 10.0, "zoom": True}], zoom=1.2)
        img = self.tmp / "frame.jpg"
        img.write_bytes(b"x")
        with mock.patch.object(face, "ready", return_value=True), mock.patch.object(face, "ensure", return_value=True), \
                mock.patch.object(thumb, "grab", return_value=img), \
                mock.patch.object(face, "faces", return_value=[{"box": [0.45, 0.1, 0.1, 0.2]}]):
            fs = editor._faces_on_screen(self.name, items, 2.0, 3.5)
        self.assertEqual(len(fs), 2)
        for x, y, w, h in fs:
            self.assertAlmostEqual(x, 0.44)
            self.assertAlmostEqual(y, 0.02)
            self.assertAlmostEqual(w, 0.12)
            self.assertAlmostEqual(h, 0.24)
        with mock.patch.object(face, "ready", return_value=True), mock.patch.object(face, "ensure", return_value=True), \
                mock.patch.object(thumb, "grab", side_effect=OSError("잠김")):
            self.assertIsNone(editor._faces_on_screen(self.name, items, 2.0, 3.5), "장면을 못 보면 모름(None) → 흔한 얼굴 자리")


@unittest.skipUnless(__import__("face").ready(), "얼굴·표정 모델이 없어요 (~/.futsal-studio/models)")
class RealFaceTest(unittest.TestCase):
    def test_closeup_photo(self):
        import face
        self.assertTrue(face.ensure())
        fs = [f["box"] for f in face.faces(FIX / "smile_closeup.jpg")]
        self.assertTrue(fs)
        W, H = editor.frame_size("long")
        st = dict(editor.TITLE_STYLE, size=88, y=0.3)
        st["x"], st["y"] = editor.emphasis_spot("인사이드!", st, W, H, fs)
        b = editor.text_box("인사이드!", st, W, H)
        for f in fs:
            self.assertEqual(inter(b, fbox(f)), 0.0)


if __name__ == "__main__":
    unittest.main()
