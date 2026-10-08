"""받기를 튼튼하게: 유튜브 영상 형식 고르기(H.264 먼저).

- 영상: yt-dlp 기본 정렬은 AV1 > VP9 > H.264 라 같은 1080p 면 '399+140'(AV1)을 골랐다 → 편집실 미리보기·내보내기가 무거움.
  이제 같은 해상도·fps 면 H.264(avc1) 먼저, 없으면 지금 규칙 그대로 (진짜 yt-dlp 의 형식 고르기에 가짜 형식 목록을 넣어 확인).
인터넷은 쓰지 않는다.
실행: python3 -m unittest tests.test_downloads
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402

try:
    import yt_dlp
except ImportError:  # 개발 PC 에 없으면 형식 고르기 시험은 건너뜀
    yt_dlp = None


@unittest.skipUnless(yt_dlp, "yt-dlp 가 없어요")
class FormatSortTests(unittest.TestCase):
    """진짜 yt-dlp 의 형식 고르기에 가짜 형식 목록 (137 avc1 · 399 av01 · 248 vp9 · 136 avc1 720p · 140 m4a)."""

    FORMATS = [
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "width": 1920, "fps": 30, "tbr": 4400},
        {"format_id": "399", "ext": "mp4", "vcodec": "av01.0.08M.08", "acodec": "none", "height": 1080, "width": 1920, "fps": 30, "tbr": 2100},
        {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 1080, "width": 1920, "fps": 30, "tbr": 2600},
        {"format_id": "136", "ext": "mp4", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "width": 1280, "fps": 30, "tbr": 2000},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128},
    ]

    def pick(self, opts, drop=()):
        fmts = [dict(f, url=f"http://127.0.0.1:1/{f['format_id']}", protocol="https") for f in self.FORMATS if f["format_id"] not in drop]
        info = {"id": "abcdefghijk", "title": "t", "formats": fmts, "extractor": "youtube", "extractor_key": "Youtube",
                "webpage_url": "http://127.0.0.1:1/w"}
        with yt_dlp.YoutubeDL(dict(opts, quiet=True, simulate=True)) as y:
            return y.process_ie_result(info, download=False)["format_id"]

    def test_old_rule_picked_av1(self):
        """재현: 예전 형식 고르기(정렬 없음)는 AV1."""
        old = {"format": core.format_opts()["format"]}
        self.assertEqual(self.pick(old), "399+140")

    def test_h264_first_at_same_resolution(self):
        self.assertEqual(self.pick(core.format_opts()), "137+140")

    def test_no_h264_at_top_resolution_keeps_resolution(self):
        """1080p H.264 가 없으면 지금 규칙 그대로 (720p H.264 로 떨어지지 않음)."""
        self.assertEqual(self.pick(core.format_opts(), drop=("137",)), "399+140")

    def test_height_limit_still_applies(self):
        self.assertEqual(self.pick(core.format_opts(720)), "136+140")

    def test_download_passes_sort_to_ytdlp(self):
        seen = {}

        class Y:
            def __init__(self, opts):
                seen.update(opts)

            def download(self, urls):
                pass

            def close(self):
                pass
        tmp = Path(tempfile.mkdtemp(prefix="받기 "))
        try:
            with mock.patch.object(core, "_yt", return_value=type("M", (), {"YoutubeDL": Y})), mock.patch.object(core, "ensure_deno"), \
                    mock.patch.object(core, "VIDEOS", tmp):
                core.download(["abcdefghijk"], lambda m: None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(seen["format_sort"], ["res", "fps", "vcodec:h264"])


if __name__ == "__main__":
    unittest.main()
