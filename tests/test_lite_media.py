"""밖에서 작업용 가벼운 소리·그림 (editor.lite_file · D-363 · D-366): 만들고 다시 쓰고, 원본이 바뀌면 다시 만들고, 못 만들면 원본 그대로."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core  # noqa: E402
import editor  # noqa: E402


class LiteMediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="가벼운 미리보기 "))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.assets = self.tmp / "edit_media"
        self.assets.mkdir()
        for p in (mock.patch.object(core, "ANALYSIS", self.tmp / "analysis"), mock.patch.object(editor, "ASSETS", self.assets),
                  mock.patch.dict(editor._LITE_SKIP, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def wav(self, name="배경음악_a.wav", secs=3):
        p = self.assets / name
        r = core.run([core.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=330:d={secs}:r=48000", "-ac", "2", "-c:a", "pcm_s16le", str(p)])
        self.assertEqual(r.returncode, 0, r.stderr)
        return p

    def test_audio_made_once_and_remade_when_source_changes(self):
        p = self.wav()
        out = editor.lite_file(p.name, "assets", "opus")
        self.assertEqual(out, editor.lite_path(p.name, "assets", "opus"))
        self.assertEqual(out.read_bytes()[4:8], b"ftyp")
        self.assertLess(out.stat().st_size, p.stat().st_size / 5)
        info = editor.probe(out)
        self.assertAlmostEqual(info["duration"], 3.0, delta=0.05)  # 길이 그대로 (앞을 당기거나 늘리지 않음)
        m = out.stat().st_mtime_ns
        self.assertEqual(editor.lite_file(p.name, "assets", "opus"), out)
        self.assertEqual(out.stat().st_mtime_ns, m)  # 다시 만들지 않음
        time.sleep(0.05)
        self.wav(secs=2)  # 같은 이름으로 다시 가져옴 (더 새 파일)
        os.utime(p, (time.time() + 5, time.time() + 5))
        out2 = editor.lite_file(p.name, "assets", "opus")
        self.assertAlmostEqual(editor.probe(out2)["duration"], 2.0, delta=0.05)

    def test_opus_falls_back_to_aac(self):
        p = self.wav()
        with mock.patch.dict(editor.LITE_AUDIO, {"opus": ["-c:a", "no_such_encoder_x"]}):
            out = editor.lite_file(p.name, "assets", "opus")
        self.assertEqual(out, editor.lite_path(p.name, "assets", "aac"))  # Opus 를 못 만드는 ffmpeg → AAC (그 자리에)
        self.assertEqual(editor.lite_file(p.name, "assets", "opus"), out)  # 다음 요청도 그 AAC

    def test_not_for_video_or_small_or_broken(self):
        v = self.assets / "clip.mp4"
        v.write_bytes(b"\x00" * 100)
        self.assertIsNone(editor.lite_file(v.name, "assets", "opus"))  # 영상은 미리보기 파일(proxy) 길
        self.assertIsNone(editor.lite_file(v.name, "assets", "webp"))
        from PIL import Image
        Image.new("RGB", (32, 32)).save(self.assets / "s.png")
        self.assertIsNone(editor.lite_file("s.png", "assets", "webp"))  # 작은 그림은 원본
        bad = self.assets / "깨진.wav"
        bad.write_bytes(b"RIFF....nope")
        self.assertIsNone(editor.lite_file(bad.name, "assets", "opus"))  # 못 만들면 원본 그대로 (재생은 되게)
        self.assertIsNone(editor.lite_file(bad.name, "assets", "opus"))  # 다시 해 보지 않음
        self.assertIsNone(editor.lite_file(bad.name, "assets", "evil"))
        with self.assertRaises(FileNotFoundError):
            editor.lite_file("없음.wav", "assets", "opus")

    def test_big_image_to_webp_keeps_alpha(self):
        from PIL import Image
        import random
        rnd = random.Random(3)
        im = Image.frombytes("RGBA", (800, 600), bytes(rnd.getrandbits(8) for _ in range(800 * 600 * 4)))
        im.save(self.assets / "누끼.png")
        out = editor.lite_file("누끼.png", "assets", "webp")
        self.assertIsNotNone(out)
        with Image.open(out) as w:
            self.assertEqual((w.format, w.size, w.mode), ("WEBP", (800, 600), "RGBA"))


if __name__ == "__main__":
    unittest.main()
