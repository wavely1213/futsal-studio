"""효과음·배경음악 (sfxlib.py, D-141) — 저장소 폴더에서 python3 -m unittest tests.test_msg_sfx
실은 효과음의 출처 기록(sfx/LICENSE.txt) · PC에서 만드는 소리가 늘 같은지 · 길이·크기 · 편집실 미디어 폴더에 준비 · 배경음악이 음악으로 들리는지(소리 모델이 있을 때)."""
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sfxlib  # noqa: E402
import thumb  # noqa: E402


def read_wav(p):
    import numpy as np
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == sfxlib.SR and w.getnchannels() == 2 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(np.float64) / 32767.0


class ShippedLicense(unittest.TestCase):
    def test_every_shipped_file_is_listed_with_cc0(self):
        """sfx/ 에 실은 파일은 모두 LICENSE.txt 에 원래 파일과 함께 적혀 있고, CC0 문구·받은 주소·sha256 이 있다."""
        lic = (sfxlib.SHIP / "LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("Creative Commons Zero, CC0", lic)
        self.assertIn("http://creativecommons.org/publicdomain/zero/1.0/", lic)
        self.assertEqual(lic.count("zip sha256"), 4)
        on_disk = sorted(p.name for p in sfxlib.SHIP.glob("*.ogg"))
        self.assertEqual(on_disk, sfxlib.shipped_files())
        for f in on_disk:
            self.assertRegex(lic, rf"\n  {f}\s+<- (Interface Sounds|Impact Sounds|Digital Audio|Music Jingles) · Audio/")

    def test_shipped_folder_is_small(self):
        self.assertLess(sum(p.stat().st_size for p in sfxlib.SHIP.iterdir()), 1_000_000)


class Synth(unittest.TestCase):
    def test_deterministic_length_peak_no_dc(self):
        """만드는 효과음: 같은 시드면 같은 바이트 · 0.05~2.5초 · 최대 -3 dBFS 근처 · 직류 없음."""
        import numpy as np
        for kind in sfxlib._GEN:
            a, b = sfxlib.synth(kind), sfxlib.synth(kind)
            self.assertEqual(a, b, kind)
            tmp = Path(tempfile.mkdtemp())
            try:
                (tmp / "x.wav").write_bytes(a)
                x = read_wav(tmp / "x.wav")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            self.assertTrue(0.05 <= len(x) / sfxlib.SR <= 2.5, kind)
            pk = 20 * np.log10(np.abs(x).max())
            want = sfxlib.PEAK_DB + sfxlib.SOFTER.get(kind, 0.0)  # 딸깍 소리는 조금 작게 (SOFTER)
            self.assertTrue(want - 0.2 <= pk <= want + 0.2, f"{kind} {pk:.2f}")
            self.assertLess(abs(float(x.mean())), 2e-3, kind)


class Assets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="효과음 시험 "))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ensure_all_sfx_into_media_folder(self):
        """모든 효과음이 '효과음_<이름>.wav' 로 준비되고, 두 번째는 다시 만들지 않는다."""
        got = sfxlib.ensure_assets(list(sfxlib.CATALOG), self.tmp)
        self.assertEqual(set(got), set(sfxlib.CATALOG))
        for name, fn in got.items():
            self.assertEqual(fn, f"효과음_{name}.wav")
            x = read_wav(self.tmp / fn)
            self.assertGreater(len(x), 0.003 * sfxlib.SR, name)
        mt = (self.tmp / got["딩동"]).stat().st_mtime_ns
        sfxlib.ensure_sfx("딩동", self.tmp)
        self.assertEqual((self.tmp / got["딩동"]).stat().st_mtime_ns, mt)

    def test_unknown_names_are_refused(self):
        with self.assertRaises(KeyError):
            sfxlib.ensure_sfx("없는소리", self.tmp)
        with self.assertRaises(KeyError):
            sfxlib.ensure_bgm("없는분위기", 1, self.tmp)

    def test_bgm_loops_seamlessly_and_levels(self):
        """배경음악: 같은 분위기·시드면 같은 파일 · 16마디 길이 · 평균 -21 dBFS 근처 · 끝과 처음이 이어짐(이음새 튐 없음)."""
        import numpy as np
        for mood in ("신남", "잔잔", "경쾌", "감성"):
            fn, sec = sfxlib.ensure_bgm(mood, 5, self.tmp)
            bpm = sfxlib.MOODS[mood][0]
            self.assertAlmostEqual(sec, 16 * 4 * 60 / bpm, delta=0.01)
            x = read_wav(self.tmp / fn)
            rms = 20 * np.log10(np.sqrt((x ** 2).mean()))
            self.assertTrue(-23 <= rms <= -19, f"{mood} {rms:.1f}")
            self.assertLessEqual(np.abs(x).max(), 10 ** (-2.9 / 20))
            jump = np.abs(x[0] - x[-1]).max()
            typical = np.percentile(np.abs(np.diff(x, axis=0)), 99.9)
            self.assertLess(jump, 4 * typical + 1e-3, mood)
        a = sfxlib.bgm_render("잔잔", 9)
        b = sfxlib.bgm_render("잔잔", 9)
        self.assertTrue((a == b).all())

    @unittest.skipUnless((thumb.MODELS / "yamnet.onnx").is_file(), "소리 모델(YAMNet)이 없어요")
    def test_bgm_sounds_like_music(self):
        import numpy as np
        import onnxruntime as ort
        import avmodels
        old = avmodels._SESS.get("audio")
        avmodels._SESS["audio"] = {"yamnet": ort.InferenceSession(str(thumb.MODELS / "yamnet.onnx"), providers=["CPUExecutionProvider"])}
        try:
            for mood in ("신남", "잔잔", "경쾌", "감성"):
                x = sfxlib.bgm_render(mood, 2).mean(axis=1)[::3].astype(np.float32)  # 48k → 16k (대충)
                r = avmodels.tags(x)
                self.assertGreater(float(np.median(r["music"])), 0.6, mood)
                self.assertLess(float(np.median(r["speech"])), 0.1, mood)
        finally:
            if old is None:
                avmodels._SESS.pop("audio", None)
            else:
                avmodels._SESS["audio"] = old


if __name__ == "__main__":
    unittest.main()
