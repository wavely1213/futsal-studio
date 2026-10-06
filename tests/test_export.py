"""내보내기 테스트 (editor.export · 실제 ffmpeg) — 저장소 폴더에서 python3 -m unittest tests.test_export
파이썬 3.9·3.10 에서도 돌려 볼 것 (소리 기다리기의 시간 초과 예외가 3.11 과 다름)."""
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402

FF = core.ffmpeg()


def make_video(path, src, dur, extra=()):
    r = core.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}{':' if '=' in src else '='}s=320x180:r=30:d={dur}", "-f", "lavfi", "-i", f"sine=f=440:d={dur}",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", *extra, "-c:a", "aac", "-shortest", str(path)])
    assert r.returncode == 0, r.stderr[-300:]


def frames(path, w=32, h=18):
    """모든 프레임 (작게, 회색) → 바이트 목록."""
    return subprocess.run([FF, "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                          capture_output=True).stdout


def nframes(path):
    err = core.run([FF, "-hide_banner", "-i", str(path), "-map", "0:v", "-f", "null", "-"]).stderr
    m = re.findall(r"frame=\s*(\d+)", err)
    return int(m[-1]) if m else 0


def item(iid, media, track, start, a, b):
    return {"id": iid, "track": track, "media": media, "start": start, "in": a, "out": b, "speed": 1, "rev": False, "link": None, "fit": "auto",
            "fx": {}, "color": {}, "reframe": 0.5}


class ExportBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="내보내기 테스트 "))
        work = cls.tmp / "작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in list(dirs.values()) + [work / "edit_media"]:
            d.mkdir(parents=True, exist_ok=True)
        cls.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(editor, "ASSETS", work / "edit_media")]
        for p in cls.patches:
            p.start()
        cls.assets = work / "edit_media"
        make_video(cls.assets / "red.mp4", "color=c=red", 6)
        make_video(cls.assets / "pat.mp4", "testsrc2", 6)
        cls.media = [{"id": k, "kind": "video", "src": "assets", "file": f"{k}.mp4", "dur": 6.0, "w": 320, "h": 180, "fps": 30.0, "audio": True}
                     for k in ("red", "pat")]

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def proj(self, name, items, trans=(), **kw):
        p = {"id": "s", "name": name, "format": "long", "v": 2, "captionsOn": False, "captionStyle": dict(editor.LONG_STYLE), "titles": [], "shapes": [],
             "layout": {"mode": "fill"}, "master": {"volume": 1, "normalize": False, "lufs": -14}, "duck": {"on": False, "amount": -14},
             "tracks": editor.default_tracks(), "items": items, "trans": list(trans), "markers": [], "captions": [],
             "info": {"duration": 6.0, "width": 320, "height": 180, "fps": 30.0}, "source": "테스트.mp4", "media": self.media}
        p.update(kw)
        return p

    def export(self, proj, **opts):
        logs = []
        out = editor.export("테스트.mp4", proj, {"preset": "small", "hw": False, "xml": False, "srt": False, **opts}, logs.append)
        return out, logs


class TransitionExport(ExportBase):
    """가운데 맞춘 검정·흰색 거치기, V2 위 디졸브: ffmpeg 7 에서 xfade 가 '1/0 프레임 수'로 실패하던 것 (v1.8.0)."""

    def two(self, typ, align="center", track="V1", dur=1.0, below=False):
        a, b = item("a", "red", track, 0, 1, 3), item("b", "pat", track, 2, 2, 4)
        items = [a, b]
        if below:
            items.insert(0, item("lo", "pat", "V1", 0, 0, 4))
            for x in (a, b):
                x["fx"] = {"scale": {"v": 60, "k": []}}
        return self.proj(f"전환 {typ} {align} {track} {dur}", items, [{"id": "t", "track": track, "a": "a", "b": "b", "type": typ, "dur": dur, "align": align}])

    def check(self, proj, mid=None, dark=None):
        out, _ = self.export(proj)
        f = core.OUT / out[0]
        self.assertEqual(nframes(f), 120, out)  # 4초 · 30fps
        if mid is not None:
            fr = frames(f)
            m = sum(fr[mid * 576:(mid + 1) * 576]) / 576
            self.assertTrue(m < 20 if dark else m > 230, f"가운데 밝기 {m:.0f}")

    def test_dip_center_v1(self):
        self.check(self.two("black"), 60, dark=True)
        self.check(self.two("white"), 60, dark=False)

    def test_dip_start_and_one_sided(self):
        self.check(self.two("black", "start"), 75, dark=True)
        p = self.two("white")
        p["trans"][0]["a"] = None  # 한쪽만 (B 앞)
        self.check(p, 75, dark=False)

    def test_v2_dissolve_wipe_over_v1(self):
        self.check(self.two("dissolve", below=True, track="V2"))
        self.check(self.two("wipe", below=True, track="V2"))
        self.check(self.two("dissolve", "start", below=True, track="V2"))
        self.check(self.two("black", below=True, track="V2"), 60, dark=True)

    def test_center_transitions_use_xfade(self):
        """가운데 전환(홀수 프레임 길이·한쪽만 검정 포함)은 한 구간 xfade (픽셀마다 식을 계산하는 blend 아님)."""
        media = {m["id"]: m for m in self.media}
        for typ, dur, side in (("dissolve", 1.0, None), ("dissolve", 0.5, None), ("black", 1.0, None), ("black", 1.0, "a")):
            p = self.two(typ, dur=dur)
            if side:
                p["trans"][0][side] = None
            tr = editor.valid_trans(p)
            r0, r1 = editor.trans_range(p, tr[0])
            segs = [s for s in editor._segments(p, 0, 4, 30, tr, media) if s[0] >= round(r0 * 30) and s[1] <= round(r1 * 30)]
            self.assertTrue(segs, (typ, dur, side))
            tmp = Path(tempfile.mkdtemp(dir=self.tmp))
            for k, (f0, f1) in enumerate(segs):
                editor._build_segment(p, media, 320, 180, 30, f0, f1, tr, tmp, k)
                fc = (tmp / f"fc{k}.txt").read_text(encoding="utf-8")
                self.assertIn("xfade", fc, (typ, dur, side))
                self.assertNotIn("blend=", fc, (typ, dur, side))


class AudioLate(ExportBase):
    def test_audio_finishes_after_video(self):
        """영상 구간이 다 된 뒤에도 소리가 아직이면 기다렸다가 합침 (파이썬 3.10 이하: concurrent.futures.TimeoutError 가 내장 것과 다름)."""
        orig, seen = editor._mix_audio, []

        def slow_mix(seq, media, t_lo, t_hi, tmp, *a, **k):
            r = orig(seq, media, t_lo, t_hi, tmp, *a, **k)
            t0 = time.time()
            while not (tmp / "list.txt").exists() and time.time() - t0 < 120:
                time.sleep(0.05)
            time.sleep(1.0)
            return r

        osp = core.set_progress

        def sp(**kw):
            seen.append(kw.get("detail") or "")
            osp(**kw)

        with mock.patch.object(editor, "_mix_audio", slow_mix), mock.patch.object(core, "set_progress", sp):
            out, _ = self.export(self.proj("소리 늦음", [item("v", "pat", "V1", 0, 0, 2), item("au", "pat", "A1", 0, 0, 2)],
                                           master={"volume": 1, "normalize": True, "lufs": -14}))
        self.assertTrue(out[0].endswith(".mp4"))
        self.assertTrue(any("소리 마무리" in d for d in seen))
        self.assertEqual(editor.EXPORT_META[out[0]]["master"], {"normalize": True, "lufs": -14.0})


class SideFiles(ExportBase):
    def test_failed_render_leaves_no_xml(self):
        """화면 만들기가 실패하면 프리미어 XML·자막도 안 남김 → 다시 내보내면 원래 이름 그대로."""
        (self.assets / "broken.mp4").write_bytes(b"\0" * 2048)
        bad = dict(self.media[0], id="bk", file="broken.mp4")
        p = self.proj("깨짐", [item("v", "bk", "V1", 0, 0, 2)], media=self.media + [bad])
        with self.assertRaises(RuntimeError):
            self.export(p, xml=True, srt=True)
        self.assertEqual([f.name for f in core.OUT.glob("*깨짐*")], [])
        out, _ = self.export(self.proj("깨짐", [item("v", "pat", "V1", 0, 0, 2)]), xml=True)
        self.assertEqual(out, ["테스트_깨짐.mp4", "테스트_깨짐_premiere.xml"])


class HwErrors(unittest.TestCase):
    def test_classification(self):
        no = ["Stream mapping:\n  Stream #0:0 (h264) -> scale\n  setsar:default -> Stream #0:0 (h264_nvenc)\n[mp4 @ 0x2] Error writing trailer: No space left on device\n",
               "[vost#0:0/h264_nvenc @ 0x2] Could not open encoder before EOF\n[vost#0:0/h264_nvenc @ 0x2] Task finished with error code: -22\n",
               "[out#0/mp4 @ 0x1] Could not write header (incorrect codec parameters ?): No space left on device\n"]
        for msg in no:
            self.assertFalse(editor._hw_fail(msg, "h264_nvenc"), msg)
        self.assertTrue(editor._hw_fail("[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: out of memory (10)\n", "h264_nvenc"))
        self.assertTrue(editor._hw_fail("[h264_qsv @ 0x1] Error initializing an internal MFX session: unsupported (-3)\n", "h264_qsv"))
        self.assertTrue(editor._hw_fail("[h264_amf @ 0x1] DLL amfrt64.dll failed to open\n", "h264_amf"))


class Thumbs(unittest.TestCase):
    def test_sparse_keyframes(self):
        """키프레임이 맨 앞 하나뿐인 영상: 썸네일 칸들이 같은 그림만 반복되지 않음."""
        tmp = Path(tempfile.mkdtemp(prefix="썸네일 "))
        try:
            work = tmp / "w"
            with mock.patch.object(core, "VIDEOS", work / "videos"), mock.patch.object(core, "ANALYSIS", work / "analysis"):
                (work / "videos").mkdir(parents=True)
                make_video(work / "videos" / "드문.mp4", "testsrc2", 60, ["-g", "99999", "-keyint_min", "99999", "-sc_threshold", "0"])
                th = editor.thumbs("드문.mp4")
                from PIL import Image
                im = Image.open(editor.thumbs_file("드문.mp4")).convert("L")
                tw = im.width // th["count"]
                tiles = [im.crop((i * tw, 0, (i + 1) * tw, im.height)).tobytes() for i in range(th["count"])]
                same = sum(a == b for a, b in zip(tiles, tiles[1:]))
                self.assertGreater(th["count"], 10)
                self.assertLess(same, 3, f"{same}/{th['count'] - 1} 칸이 앞 칸과 같음")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
