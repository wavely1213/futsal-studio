"""AI 추천 썸네일 × v2.8.0 합침(D-067): 새 썸네일 작업도 jobId·실패 카드(trouble) · 휴대폰에는 이름만 ·
브랜드 키트 안전 저장 · A/B '모바일 비교'는 7단계 썸네일로 고르지 않음 · 편집기 PNG 한도 = 올리기 키트 한도."""
import json
import re
import time
import unittest
from pathlib import Path
from unittest import mock

import core
import remote
import thumb
import thumbcopy
import updater
import upload
from tests.test_thumb_pro import ServerBase

ROOT = Path(__file__).resolve().parent.parent


class ThumbJobTests(ServerBase):
    def add_video(self, name="20261007_ABCDEFGHIJK_발바닥 드래그.mp4"):
        (self.work / "videos" / name).write_bytes(b"0")
        a = core.adir(name)
        a.mkdir(parents=True, exist_ok=True)
        (a / "transcript.json").write_text(json.dumps([{"start": 1, "end": 3, "text": "발바닥 드래그 이렇게 하세요"}], ensure_ascii=False), encoding="utf-8")
        return name

    def wait_done(self, jid):
        for _ in range(200):
            code, s = self.call(f"/api/state?since=999999&job={jid}")
            if s.get("done"):
                return s["done"]
            time.sleep(0.05)
        self.fail("작업이 끝나지 않았어요")

    def test_analyze_job_id_and_failure_card(self):
        name = self.add_video()
        with mock.patch.object(thumb, "analyze", return_value={"frames": [{"t": 1}], "cuts": {}, "copy": {}}):
            code, j = self.call("/api/thumb/analyze", {"name": name})
            self.assertEqual((code, j["ok"], j["job"]), (200, True, True))
            self.assertIsInstance(j["jobId"], int)
            d = self.wait_done(j["jobId"])
        self.assertEqual((d["name"], d["error"], d["result"]["frames"]), (thumb.JOB_ANALYZE, None, [{"t": 1}]))
        with mock.patch.object(thumb, "analyze", side_effect=OSError(28, "No space left on device")):
            code, j = self.call("/api/thumb/analyze", {"name": name})
            d = self.wait_done(j["jobId"])
        self.assertEqual(d["fail"]["kind"], "disk")
        self.assertIn("저장 공간", d["error"])  # 영어 원문 대신 쉬운 한 줄 (trouble.explain)
        self.assertNotIn("No space", d["error"])

    def test_claude_jobs_reply_job_id(self):
        name = self.add_video()
        with mock.patch.object(thumbcopy, "run_ai", return_value={"ok": True, "items": []}):
            code, j = self.call("/api/thumb/copy", {"name": name, "ai": True})
            self.assertIsInstance(j["jobId"], int)
            self.assertEqual(self.wait_done(j["jobId"])["name"], thumbcopy.JOB_AI)
        img = "data:image/jpeg;base64,AAAA"
        with mock.patch.object(thumbcopy, "judge", return_value={"ok": True}):
            code, j = self.call("/api/thumb/judge", {"name": name, "small": img, "full": img})
            self.assertIsInstance(j["jobId"], int)
            self.assertEqual(self.wait_done(j["jobId"])["name"], thumbcopy.JOB_JUDGE)
        with mock.patch.object(self.app, "start_job", return_value=False):
            code, j = self.call("/api/thumb/judge", {"name": name, "small": img, "full": img})
        self.assertEqual((code, j["jobId"], j["error"]), (409, None, self.app.BUSY_MSG))

    def test_ocr_error_is_plain_and_traced(self):
        with mock.patch.object(thumb, "read_text", side_effect=RuntimeError("onnx secret boom")):
            code, j = self.call("/api/thumb/ocr", {"data": "data:image/png;base64,AAAA"})
        self.assertEqual((code, j["ok"]), (200, False))
        self.assertNotIn("boom", j["error"])  # 원문은 studio.log 에만
        self.assertIn("글자를 읽지 못했어요", j["error"])

    def test_ab_disk_error_answers(self):
        name = self.add_video()
        img = "data:image/jpeg;base64,AAAA"
        with mock.patch.object(updater, "write_atomic", side_effect=OSError(28, "No space left on device")):
            code, j = self.call("/api/thumb/ab", {"name": name, "items": [img, img]})
        self.assertEqual((code, j["ok"]), (500, False))
        self.assertIn("저장 공간", j["error"])


class PhoneLabelTests(unittest.TestCase):
    def test_thumb_jobs_visible_not_startable(self):
        """휴대폰: 썸네일 작업은 진행·알림 이름만 (시작은 PC 썸네일 편집기에서만)."""
        names = {thumb.JOB_ANALYZE, thumbcopy.JOB_AI, thumbcopy.JOB_JUDGE}
        self.assertTrue(names <= remote.JOB_LABELS)
        self.assertEqual({remote._label(n) for n in names}, names)
        self.assertTrue({thumbcopy.JOB_AI, thumbcopy.JOB_JUDGE} <= remote.STOPPABLE)
        self.assertNotIn(thumb.JOB_ANALYZE, remote.STOPPABLE)  # 누끼 중에는 멈추기를 보지 않음
        self.assertFalse([a for a in remote.ACTIONS if "thumb" in a.lower()])


class SaveTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="풋살 합침 "))
        self.p = [mock.patch.object(thumb, "THUMBS", self.tmp / "thumbnails"), mock.patch.object(core, "OUT", self.tmp / "out"),
                  mock.patch.object(core, "VIDEOS", self.tmp / "videos"), mock.patch.object(core, "ANALYSIS", self.tmp / "analysis")]
        for p in self.p:
            p.start()
        (self.tmp / "videos").mkdir()
        (self.tmp / "out").mkdir()

    def tearDown(self):
        import shutil
        for p in self.p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_brand_save_atomic_with_backup(self):
        thumb.save_brand({"series": "첫 시리즈"})
        thumb.save_brand({"series": "둘째 시리즈"})
        p = thumb.THUMBS / "brand.json"
        self.assertEqual(json.loads(p.with_suffix(".json.bak").read_text(encoding="utf-8"))["series"], "첫 시리즈")
        real = updater.write_atomic

        def boom(path, data, *a, **k):
            if Path(path) == p:
                raise OSError(28, "No space left on device")
            return real(path, data, *a, **k)
        with mock.patch.object(updater, "write_atomic", side_effect=boom), self.assertRaises(OSError):
            thumb.save_brand({"series": "셋째 시리즈"})
        self.assertEqual(thumb.load_brand()["series"], "둘째 시리즈")  # 저장이 실패해도 예전 것이 그대로
        self.assertEqual([f.name for f in thumb.THUMBS.iterdir() if ".tmp" in f.name], [])

    def test_ab_mobile_sheet_never_picked_as_thumbnail(self):
        from io import BytesIO
        import base64
        from PIL import Image
        name = "20261007_ABCDEFGHIJK_발바닥 드래그.mp4"
        (self.tmp / "videos" / name).write_bytes(b"0")

        def url(w, h):
            b = BytesIO()
            Image.new("RGB", (w, h), (200, 30, 30)).save(b, "JPEG")
            return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
        files = thumb.export_ab(name, [url(1280, 720), url(1280, 720)], url(400, 160))
        self.assertTrue(any(thumb.AB_SHEET in f for f in files))
        sheet = core.OUT / next(f for f in files if thumb.AB_SHEET in f)
        later = time.time() + 5
        import os
        os.utime(sheet, (later, later))  # 비교 한 장이 가장 최근이어도
        th = upload.thumbnail_check(name, "long")
        self.assertNotIn(thumb.AB_SHEET, th["file"])
        self.assertEqual((th["w"], th["h"]), (1280, 720))


class LimitTests(unittest.TestCase):
    def test_editor_png_limit_matches_upload_check(self):
        """편집기 PNG 경고(PNG_LIMIT)와 7단계 썸네일 확인(upload.THUMB_MAX)은 같은 기준 · thumb.html 은 thumb_src 로 만든 그대로."""
        for f in (ROOT / "thumb_src" / "parts" / "p7_auto.js", ROOT / "thumb.html"):
            m = re.search(r"const PNG_LIMIT = ([0-9 *]+);", f.read_text(encoding="utf-8"))
            self.assertIsNotNone(m, f)
            self.assertEqual(eval(m.group(1)), upload.THUMB_MAX)  # noqa: S307 — 숫자·곱셈만 (위 정규식)


if __name__ == "__main__":
    unittest.main()
