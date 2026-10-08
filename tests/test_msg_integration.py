"""MSG 편집본과 다른 화면 잇기 — 저장소 폴더에서 python3 -m unittest tests.test_msg_integration
썸네일 장면 후보가 MSG 추천 장면(seq.msg.thumb)을 먼저 넣음 · 올리기 키트 제목 후보에 MSG 훅 자막 · 챕터는 MSG 마커 이름으로 · 내보내기 기록에 MSG 요약."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import editor  # noqa: E402
import face  # noqa: E402
import msg  # noqa: E402
import thumb  # noqa: E402
import upload  # noqa: E402
from make_msg_fixture import MsgWork  # noqa: E402


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()
        cls.res = msg.build_variants(cls.w.name, [{"kind": "preset", "name": "예능 MSG형"}], "보통", ("long",), lambda m: None)
        proj = editor.load_project(cls.w.name)
        proj["media"] += cls.res["media"]
        proj["sequences"] += cls.res["sequences"]
        editor.save_project(cls.w.name, proj, force=True)
        cls.seq = cls.res["sequences"][0]

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_thumb_candidates_put_msg_picks_first(self):
        picks = [x["t_src"] for x in self.seq["msg"]["thumb"]]
        self.assertTrue(picks)
        self.assertEqual(thumb._msg_thumb_times(self.w.name), [round(t, 1) for t in picks][:8])
        with mock.patch.object(face, "ensure", return_value=False):
            items = thumb.frame_candidates(self.w.name, n=6)
        tagged = [it for it in items if it.get("why") == "MSG 추천 장면"]
        self.assertTrue(tagged, items)
        self.assertTrue(all(any(abs(it["t"] - p) <= 0.45 for p in picks) for it in tagged))

    def test_upload_kit_uses_msg_hook_and_markers(self):
        kit = upload.build_kit(self.w.name, self.seq["id"], save=False)
        self.assertIn(self.seq["msg"]["hook"].split("?")[0][:8], json.dumps(kit["titles"], ensure_ascii=False), kit["titles"])

    def test_chapters_from_msg_markers(self):
        """3분 넘는 MSG 편집본의 챕터는 MSG 마커 이름(미리 보기·장 나눔·엔드)으로."""
        marks = [{"t": 0.0, "name": "미리 보기"}, {"t": 12.0, "name": "퍼스트 터치 레슨"}, {"t": 70.0, "name": "두 번째 포인트"}, {"t": 182.0, "name": "다음 영상"}]
        segs = [{"start": t, "end": t + 3, "text": f"말 {k}"} for k, t in enumerate(range(0, 190, 15))]
        chs = upload.chapters(segs, marks, None, 195.0)
        self.assertEqual([c["title"] for c in chs], ["미리 보기", "퍼스트 터치 레슨", "두 번째 포인트", "다음 영상"])
        self.assertEqual(chs[0]["time"], "00:00")


if __name__ == "__main__":
    unittest.main()
