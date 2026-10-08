"""영상 한 편 '다음 할 일' 길잡이 (D-075): 스튜디오 4단계 카드가 영상마다 가편집·내보냄·썸네일·올리기를 보여 줌.

예전에는 '편집실 열기 →'만 있어 내보냈는지·썸네일을 저장했는지 안 보였다 (올리기 화면만 '아직 안 내보냄'을 앎).
upload.progress 가 완성본 폴더·편집본을 읽기만 해서 영상마다 {seqs, rough, exported, thumb, kit} · /api/progress 는 유튜브에 올린 것도.
이름이 더 긴 다른 영상(A_2.mp4)의 완성본·썸네일은 A 의 것으로 세지 않는다 (upload._images_of 와 같은 규칙).
실행: python3 -m unittest tests.test_next_steps
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import upload  # noqa: E402
import youtube_upload  # noqa: E402
from test_windows_compat import Server  # noqa: E402


class ProgressTests(Server):
    def make(self, name, seqs=("롱폼 가편집", "쇼츠 1")):
        self.video(name)
        d = core.adir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript_timeline.md").write_text(f"# 타임라인: {name}\n", encoding="utf-8")
        proj = {"v": 2, "source": name, "info": {"duration": 30}, "media": [],
                "sequences": [{"id": f"s{i}", "name": n, "format": "shorts" if "쇼츠" in n else "long", "items": []} for i, n in enumerate(seqs)]}
        editor._ppath(name).write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")

    def out(self, *names):
        for n in names:
            (core.OUT / n).write_bytes(b"x")

    def test_nothing_yet(self):
        self.make("레슨 [꿀팁].mp4")
        self.assertEqual(upload.progress(["레슨 [꿀팁].mp4"])["레슨 [꿀팁].mp4"],
                         {"seqs": 2, "rough": True, "exported": 0, "thumb": False, "kit": False})

    def test_steps_from_out_folder(self):
        self.make("A.mp4")
        self.make("A_2.mp4", ("롱폼 가편집",))
        self.out("A_롱폼 가편집 (2).mp4", "A_롱폼 가편집.srt", "A_썸네일_1.jpg",
                 "A_2_롱폼 가편집.mp4", "A_2_썸네일_1.png")  # A_2 의 것은 A 로 세지 않음
        kit = {"name": "A.mp4", "source": {"id": "s0", "label": "롱폼 가편집"}}
        (core.OUT / "A_롱폼 가편집_올리기.json").write_text(json.dumps(kit, ensure_ascii=False), encoding="utf-8")
        p = upload.progress(["A.mp4", "A_2.mp4"])
        self.assertEqual(p["A.mp4"], {"seqs": 2, "rough": True, "exported": 1, "thumb": True, "kit": True})
        self.assertEqual(p["A_2.mp4"], {"seqs": 1, "rough": True, "exported": 1, "thumb": True, "kit": False})

    def test_range_export_and_other_kit_do_not_count(self):
        """구간 내보내기(다른 이름)·다른 영상 이름이 적힌 키트는 세지 않음 · 편집본이 없으면 가편집도 아님."""
        self.make("B.mp4", ())
        self.out("B_롱폼 가편집_구간.mp4")
        (core.OUT / "B_올리기.json").write_text(json.dumps({"name": "C.mp4", "source": {"id": ""}}), encoding="utf-8")
        self.assertEqual(upload.progress(["B.mp4"])["B.mp4"], {"seqs": 0, "rough": False, "exported": 0, "thumb": False, "kit": False})

    def test_kit_of_original_video_is_not_the_upload_kit(self):
        """검토 재현: '원본 영상 그대로'로 만든 키트도 '올리기 ✓'로 셌다 (올릴 완성본의 키트가 아님) → 편집본으로 만든 키트만."""
        self.make("E.mp4")
        self.out("E_롱폼 가편집.mp4")
        (core.OUT / "E_올리기.json").write_text(json.dumps({"name": "E.mp4", "source": {"id": "", "label": "원본 영상 그대로"}}), encoding="utf-8")
        self.assertFalse(upload.progress(["E.mp4"])["E.mp4"]["kit"])
        kit = {"name": "E.mp4", "source": {"id": "s0", "label": "롱폼 가편집", "export": "E_롱폼 가편집.mp4"}}
        (core.OUT / "E_롱폼 가편집_올리기.json").write_text(json.dumps(kit, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(upload.progress(["E.mp4"])["E.mp4"]["kit"])
        (core.OUT / "E_롱폼 가편집_올리기.json").write_text(json.dumps({"name": "E.mp4", "source": {"id": ["s0"]}}), encoding="utf-8")
        self.assertFalse(upload.progress(["E.mp4"])["E.mp4"]["kit"], "손으로 고쳐 깨진 칸도 오류 없이")

    def test_route_adds_youtube_uploads(self):
        self.make("A.mp4")
        self.make("D.mp4")
        self.video("아직.mp4")  # 편집점을 안 찾은 영상은 빼고
        with mock.patch.object(youtube_upload, "_history", return_value=[{"name": "D.mp4", "videoId": "abcdefghijk"}]):
            code, j = self.call("/api/progress")
        self.assertEqual((code, j["ok"]), (200, True))
        self.assertEqual(sorted(j["videos"]), ["A.mp4", "D.mp4"])
        self.assertTrue(j["videos"]["D.mp4"]["uploaded"])
        self.assertFalse(j["videos"]["A.mp4"]["uploaded"])

    def test_route_survives_broken_files(self):
        self.make("A.mp4")
        editor._ppath("A.mp4").write_text("{깨짐", encoding="utf-8")
        (core.OUT / "A_올리기.json").write_text("{깨짐", encoding="utf-8")
        code, j = self.call("/api/progress")
        self.assertEqual(code, 200)
        self.assertEqual(j["videos"]["A.mp4"]["seqs"], 0)


if __name__ == "__main__":
    unittest.main()
