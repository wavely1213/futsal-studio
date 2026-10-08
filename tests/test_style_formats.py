"""E12 스타일을 형식(쇼츠·긴 영상)마다 따로 — 저장소 폴더에서 python3 -m unittest tests.test_style_formats

한 채널의 긴 영상(나누기 6.25초·확대 100초마다·자막 거의 없음)과 쇼츠(2초·6초마다·자막 위)를 한 스타일로 배우면
예전에는 한 분포로 섞여 롱폼·쇼츠 가편집 모두 어느 쪽도 아닌 값(약 4초·11초마다·자막 끔·위치 '위')을 썼다 (D-102).
- style.split_formats · edit_params(prof, fmt) · byFormat → editor.auto_sequences 가 형식마다 그 값
- learn: 레퍼런스마다 형식을 적고 formats 를 저장 · 예전 스타일 파일(형식 없음)은 그대로 열리고, 섞인 예전 스타일은 그 자리에서 나눔
- ref_format: 학습용 영상 기록의 받은 탭(쇼츠·긴 영상) → 영상 파일(세로·3분 이하) 순서."""
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
import plan  # noqa: E402
import style  # noqa: E402

FIXD = Path(__file__).resolve().parent / "fixtures"
LESSON = json.loads((FIXD / "rough_e12.json").read_text(encoding="utf-8"))["videos"]["LESSON04"]


def ev(src, dur, shot, zoom_every, top_caps=False, pause=0.5):
    """스타일 기록 흉내: shot 초마다 컷 · zoom_every 초마다 확대 컷 · 자막(위 띠) · 말 사이 쉼."""
    cuts = [round(shot * k, 2) for k in range(1, int(dur / shot))]
    zooms = [{"t": t, "scale": 1.25, "ox": 0.5, "oy": 0.5} for t in cuts if int(t / zoom_every) != int((t - shot) / zoom_every)]
    n = int(dur * style.EV_FPS)
    row = [200, 200, 10, 10, 10, 10] if top_caps else [10, 10, 10, 10, 10, 10]
    sil = [[round(t, 2), round(t + pause, 2)] for t in range(3, int(dur) - 1, 4)]
    return {"v": style.EV_VER, "source": src, "duration": float(dur), "cuts": cuts, "zooms": zooms, "fps": style.EV_FPS, "text": [row] * n,
            "capColors": [], "silences": sil, "rmsStep": style.RMS_STEP, "rms": [], "motion": [0.0] * n, "lufs": -16.0,
            "speech": {"talk": dur * 0.6, "chars": dur * 0.6 * 5.0}}


LONG1, LONG2, SHORT1 = "20261001_LONGREF001_긴 영상 1.mp4", "20261002_LONGREF002_긴 영상 2.mp4", "20261003_SHORTREF01_쇼츠 1.mp4"
EVS = {LONG1: ev(LONG1, 600, 6.25, 100), LONG2: ev(LONG2, 480, 6.25, 100), SHORT1: ev(SHORT1, 40, 2.0, 6, top_caps=True, pause=0.3)}
FMTS = {LONG1: "long", LONG2: "long", SHORT1: "shorts"}


class Work(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="E12 스타일 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(style, "STYLES", work / "styles"),
                                                                                    mock.patch.dict(style._FMT_MEMO, clear=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def learn(self, name="섞은 스타일", names=(LONG1, LONG2, SHORT1)):
        with mock.patch.object(style, "extract_events", side_effect=lambda n, log=print: json.loads(json.dumps(EVS[n]))), \
                mock.patch.object(plan, "extract_plan", side_effect=RuntimeError("기획 분석은 이 시험에서 안 함")), \
                mock.patch.object(style, "ref_format", side_effect=lambda n: FMTS.get(n)):
            logs = []
            res = style.learn(name, list(names), logs.append)
        return res, logs


class SplitTest(Work):
    def test_mixed_profile_was_neither(self):
        """문제 재현: 한 분포로 섞으면 롱폼·쇼츠 어느 쪽 값도 아님."""
        profs = [style.summarize(EVS[n]) for n in (LONG1, LONG2, SHORT1)]
        mixed = style.edit_params(style.merge(profs, [EVS[n] for n in (LONG1, LONG2, SHORT1)]))
        lp, sp = style.edit_params(profs[0]), style.edit_params(profs[2])
        self.assertEqual((lp["splitShot"], lp["zoomEvery"], lp["captions"]), (6.25, 120.0, False))  # 10분에 확대 5번
        self.assertEqual((sp["splitShot"], sp["zoomEvery"], sp["captions"], sp["captionPos"]), (2.0, 6.7, True, "top"))
        # 섞으면: 컷 길이도 확대 간격도 둘 사이 어딘가 · 자막은 긴 영상 쪽(끔)인데 위치는 쇼츠 쪽('위')
        self.assertTrue(2.0 < mixed["splitShot"] < 6.25 or mixed["zoomEvery"] not in (6.7, 120.0), mixed)
        self.assertEqual((mixed["captions"], mixed["captionPos"]), (False, "top"))

    def test_learn_keeps_formats_apart(self):
        res, logs = self.learn()
        d = json.loads((style.STYLES / "섞은 스타일.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(d["formats"]), ["long", "shorts"])
        self.assertEqual((d["formats"]["long"]["count"], d["formats"]["shorts"]["count"]), (2, 1))
        self.assertEqual([r["format"] for r in d["refs"]], ["long", "long", "shorts"])
        self.assertNotIn("format", d)  # 맨 위(예전 판이 읽는 값)는 예전처럼 전체를 합친 값
        self.assertEqual(d["source"], [LONG1, LONG2, SHORT1])
        lp, sp = style.edit_params(d, "long"), style.edit_params(d, "shorts")
        self.assertEqual((lp["splitShot"], lp["zoomEvery"], lp["captions"], lp["captionPos"]), (6.25, 120.0, False, "bottom"))
        self.assertEqual((sp["splitShot"], sp["zoomEvery"], sp["captions"], sp["captionPos"]), (2.0, 6.7, True, "top"))
        both = [style.summarize(EVS[n]) for n in (LONG1, LONG2)]  # 형식별 값 = 그 형식 영상만으로 배운 값과 같음
        self.assertEqual({k: v for k, v in lp.items() if k != "tempo"},
                         {k: v for k, v in style.edit_params(style.merge(both, [EVS[LONG1], EVS[LONG2]])).items() if k != "tempo"})
        self.assertEqual(res["params"]["byFormat"], {"long": lp, "shorts": sp})
        self.assertIn("긴 영상 2개·쇼츠 1개를 따로 배웠어요", "\n".join(logs))
        st = style.list_styles()[0]
        self.assertEqual((st["formats"]["long"]["count"], st["formats"]["shorts"]["count"]), (2, 1))
        self.assertEqual(st["formats"]["shorts"]["params"], sp)
        self.assertIn("롱폼 가편집:", st["desc"])
        self.assertIn("쇼츠 가편집:", st["desc"])

    def test_one_format_style_is_unchanged(self):
        res, _ = self.learn("긴 영상만", (LONG1, LONG2))
        d = json.loads((style.STYLES / "긴 영상만.json").read_text(encoding="utf-8"))
        self.assertNotIn("formats", d)
        self.assertNotIn("byFormat", res["params"])
        self.assertEqual(style.edit_params(d, "shorts"), style.edit_params(d))  # 없는 형식은 있는 쪽 값
        self.assertIsNone(style.list_styles()[0]["formats"])

    def test_tempo_setting_applies_to_both(self):
        self.learn()
        style.set_tempo("섞은 스타일", False)
        d = json.loads((style.STYLES / "섞은 스타일.json").read_text(encoding="utf-8"))
        self.assertEqual(style.edit_params(d, "long")["tempo"], 0)
        self.assertEqual(style.edit_params(d, "shorts")["tempo"], 0)
        self.assertEqual(style.edit_params(d)["byFormat"]["shorts"]["tempo"], 0)


class MigrationTest(Work):
    def test_old_v17_style_loads_same(self):
        style.STYLES.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FIXD / "style_v17.json", style.STYLES / "예전.json")
        d = json.loads((FIXD / "style_v17.json").read_text(encoding="utf-8"))
        st = style.list_styles()[0]
        self.assertIsNone(st["formats"])  # 레퍼런스 영상이 없어 형식을 모름 → 예전처럼 한 벌
        self.assertEqual(st["params"], style._params(d))
        self.assertNotIn("byFormat", st["params"])

    def test_old_mixed_style_is_split_on_load(self):
        """formats 가 없는 예전 스타일 파일이라도 레퍼런스 형식을 알면(학습용 영상 기록) 다시 배우지 않고 형식마다."""
        profs = [style.summarize(EVS[n]) for n in (LONG1, LONG2, SHORT1)]
        old = style.merge(profs, [EVS[n] for n in (LONG1, LONG2, SHORT1)])
        old["refs"] = profs  # 예전 learn 이 남긴 모양 (refs 에 format 없음)
        style.STYLES.mkdir(parents=True, exist_ok=True)
        (style.STYLES / "예전 섞임.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(style, "ref_format", side_effect=lambda n: FMTS.get(n)):
            st = style.list_styles()[0]
        self.assertEqual(sorted(st["formats"]), ["long", "shorts"])
        self.assertEqual(st["formats"]["shorts"]["params"]["splitShot"], 2.0)
        self.assertEqual(st["formats"]["long"]["params"]["zoomEvery"], 120.0)
        with mock.patch.object(style, "ref_format", return_value=None):  # 형식을 모르면 예전 값 그대로
            self.assertEqual(style.list_styles()[0]["params"], style._params(old))


class EditorWiringTest(Work):
    name = "20261008_LESSON04_2대1 패스 레슨.mp4"

    def test_auto_sequences_use_format_values(self):
        d = core.adir(self.name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(LESSON["transcript"], ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps(LESSON["analysis"], ensure_ascii=False), encoding="utf-8")
        res, _ = self.learn()
        info = {"duration": 170.14, "width": 1920, "height": 1080, "fps": 30.0}
        with mock.patch.object(editor, "_captions_of", return_value=None), mock.patch.object(editor, "emphasis_placer", return_value=None):
            seqs = editor.auto_sequences(self.name, info, res["params"])
            alone_long = editor.auto_sequences(self.name, info, res["params"]["byFormat"]["long"], ("long",))
            alone_short = editor.auto_sequences(self.name, info, res["params"]["byFormat"]["shorts"], ("shorts",))
        long_ = [q for q in seqs if q["format"] == "long"]
        shorts = [q for q in seqs if q["format"] == "shorts"]
        self.assertEqual(len(long_), 1)
        self.assertTrue(shorts)
        self.assertFalse(long_[0]["captionsOn"])  # 긴 영상은 자막을 거의 안 씀
        self.assertTrue(all(q["captionsOn"] for q in shorts))  # 쇼츠는 자막 위
        self.assertTrue(all(q["auto"] == "style" for q in seqs))
        import re
        strip = lambda qs: re.sub(r'"(id|link)": "[0-9a-f]{8}"', '"\\1": "-"', json.dumps(qs, sort_keys=True, ensure_ascii=False))  # noqa: E731
        self.assertEqual(strip(long_), strip(alone_long))  # 롱폼 = 긴 영상 값만으로 만든 것과 똑같음
        self.assertEqual(strip(shorts), strip(alone_short))  # 쇼츠 = 쇼츠 값만으로 만든 것과 똑같음

    def test_score_compares_long_values(self):
        self.learn()
        st = style.list_styles()[0]
        seen = {}

        def dist(target, cand, params=None, fixed=()):
            seen["target"], seen["params"] = target, params
            return {"score": 50}
        with mock.patch.object(style, "_score_inputs", return_value=(st, [])), mock.patch.object(style, "_cached_events", return_value={}), \
                mock.patch.object(editor, "media_info", return_value={"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0}), \
                mock.patch.object(editor, "auto_sequences", return_value=[{"master": {}}]), \
                mock.patch.object(style, "profile_from_sequence", return_value={k: 0 for k in ("cutsPerMin", "avgShot", "zoomCutsPerMin", "pauseP75",
                                                                                                 "captionRatio", "captionPos", "captionColor", "lufs")}), \
                mock.patch.object(style, "distance", side_effect=dist):
            style.score_video("섞은 스타일", "x.mp4")
        self.assertEqual(seen["target"]["count"], 2)  # 롱폼 가편집은 긴 영상 값과 비교
        self.assertEqual(seen["params"]["splitShot"], 6.25)


class RefFormatTest(Work):
    def video(self, name, size, dur):
        r = core.run([core.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={size}:r=1:d={dur}",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", str(core.VIDEOS / name)])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_by_file_shape(self):
        self.video("세로 짧은.mp4", "90x160", 3)
        self.video("가로.mp4", "160x90", 3)
        self.video("세로 긴.mp4", "90x160", 200)
        self.assertEqual(style.ref_format("세로 짧은.mp4"), "shorts")
        self.assertEqual(style.ref_format("가로.mp4"), "long")
        self.assertEqual(style.ref_format("세로 긴.mp4"), "long")  # 3분 넘는 세로 영상은 쇼츠가 아님 (BR-001)
        self.assertIsNone(style.ref_format("없는 영상.mp4"))

    def test_refs_record_kind_first(self):
        import refs
        self.video("가로인데 쇼츠 탭.mp4", "160x90", 3)
        with mock.patch.object(refs, "find", return_value={"kind": "shorts"}):
            self.assertEqual(style.ref_format("가로인데 쇼츠 탭.mp4"), "shorts")


if __name__ == "__main__":
    unittest.main()
