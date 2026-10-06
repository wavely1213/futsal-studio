"""전환 내보내기 회귀 테스트 — 실제 ffmpeg 로 내보냄 (v1.8.0: 검정·흰색 전환, 위 트랙 전환이 'Conversion failed' 로 실패했음).
저장소 폴더에서 python3 -m unittest tests.test_export_trans"""
import concurrent.futures
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor as ed  # noqa: E402

FF = core.ffmpeg()
NAME = "20200320_전환 테스트 영상.mp4"


def ff(*args):
    r = core.run([FF, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)])
    if r.returncode:
        raise RuntimeError(r.stderr)


def gray_at(path, t, w=64, h=36):
    r = subprocess.run([FF, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, **core.NO_WINDOW)
    return float(np.frombuffer(r.stdout, np.uint8).mean())


def frame_count(path):
    r = subprocess.run([FF, "-v", "error", "-i", str(path), "-map", "0:v", "-f", "framecrc", "-"], capture_output=True, **core.NO_WINDOW)
    return sum(1 for ln in r.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("0,"))


def duration(path):
    import re
    r = core.run([FF, "-hide_banner", "-i", str(path)])
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr or "")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


class ExportTransitions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="전환 테스트 "))
        work = cls.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        cls.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        cls.patches += [mock.patch.object(ed, "PROJECTS", work / "projects"), mock.patch.object(ed, "ASSETS", work / "edit_media")]
        for p in cls.patches:
            p.start()
        cls.out = dirs["OUT"]
        # 회색 바탕(밝기 128)에 시계 → 검정/흰색 전환 가운데가 확실히 구분됨
        ff("-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=40", "-f", "lavfi", "-i", "sine=f=440:r=48000:d=40",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", dirs["VIDEOS"] / NAME)
        info = ed.media_info(NAME)
        cls.info = info
        cls.main = {"id": "main", "kind": "video", "src": "videos", "file": NAME, "dur": info["duration"], "w": info["width"],
                    "h": info["height"], "fps": info["fps"], "audio": True}

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def export(self, typ, align, track="V1", fmt="long"):
        A, B = ed._pair(5, 9, start=0), ed._pair(20, 24, start=4)
        items = A + B
        if track == "V2":
            for it in items:
                it["track"] = "V2" if it["track"] == "V1" else "A2"
            items = ed._pair(30, 38, start=0) + items  # 아래 트랙에 깔린 영상
        proj = {"id": "m", "name": f"{typ}_{align}_{track}", "format": fmt, "v": 2, "captionsOn": False, "captionStyle": dict(ed.LONG_STYLE),
                "titles": [], "shapes": [], "layout": {"mode": "fill"}, "master": {"volume": 1, "normalize": False, "lufs": -14},
                "duck": {"on": False, "amount": -14}, "tracks": ed.default_tracks(), "items": items,
                "trans": [{"id": "t1", "track": track, "a": A[0]["id"], "b": B[0]["id"], "type": typ, "dur": 1.0, "align": align}],
                "markers": [], "captions": [], "info": self.info, "source": NAME, "media": [self.main]}
        ed.export(NAME, proj, {"preset": "small", "fps": 30}, lambda *_: None)
        mp4 = sorted(self.out.glob(f"*{typ}_{align}_{track}*.mp4"))
        self.assertTrue(mp4, "내보낸 영상이 없어요")
        self.assertAlmostEqual(duration(mp4[0]), 8.0, delta=0.15)
        self.assertEqual(frame_count(mp4[0]), 240, "전환 구간에서 프레임이 빠지면 뒤쪽 화면·소리 싱크가 밀려요")
        return mp4[0]

    def test_dip_black_center_v1(self):  # Ctrl+D 기본 전환 · 가운데(4초)는 검정
        p = self.export("black", "center")
        self.assertLess(gray_at(p, 4.0), 25)
        self.assertGreater(gray_at(p, 2.0), 100)

    def test_dip_white_start_v1(self):  # 시작 맞춤 → 4.5초가 흰색
        p = self.export("white", "start")
        self.assertGreater(gray_at(p, 4.5), 225)

    def test_dissolve_center_v2_over_v1(self):
        self.export("dissolve", "center", "V2")

    def test_wipe_start_v2_over_v1(self):
        self.export("wipe", "start", "V2")

    def test_dissolve_center_v1(self):  # 1.8.0 에서 되던 경우도 프레임 수 그대로
        self.export("dissolve", "center")

    def test_white_center_v2_over_v1(self):
        self.export("white", "center", "V2")

    def test_black_center_shorts(self):
        p = self.export("black", "center", fmt="shorts")
        self.assertLess(gray_at(p, 4.0), 25)

    def test_future_timeout_caught_on_old_python(self):
        # Python 3.10 이하에선 concurrent.futures.TimeoutError 가 내장 TimeoutError 와 다른 오류 → 둘 다 잡아야 함
        self.assertIs(ed.FutTimeout, concurrent.futures.TimeoutError)
        src = Path(ed.__file__).read_text(encoding="utf-8")
        self.assertIn("except (TimeoutError, FutTimeout):", src)


if __name__ == "__main__":
    unittest.main()
