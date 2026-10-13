"""글자·그림 줄 여러 개 (자막 C · 타이틀 T · 도형 G · D-296~D-305) — 저장소 폴더에서 python3 -m unittest tests.test_overlay_tracks

- editor.overlay_tracks(줄 목록 정리) · track_of · cap_trk · cap_track_default · cap_style_of · track_visible
- golden: 줄 기능 전(기준 커밋)에 만든 tests/fixtures/ovstack 의 ASS·framemd5 와 지금 build_ass 가 같은 그림인지 (예전 프로젝트는 그대로)
- 층(Layer) = 줄 차례 · 숨긴 줄 · C2 자막 스타일·높이 · timeline_captions 줄마다 이어 붙이기·최소 시간
- 자막 서명(trk 포함: _cap_text · _merge_auto · autorun) · 한 줄씩 나누기가 trk 를 지킴 · export_saved 묶음
- node 가 있으면 editor.html 의 줄 블록(otList · ensureOt · spreadTitles · capTrackDefault · otPlace · otSettle)을 그대로 실행해 파이썬과 견줌"""
import copy
import gzip
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import core  # noqa: E402
import editor  # noqa: E402

NODE = shutil.which("node")
FIX = ROOT / "tests" / "fixtures" / "ovstack"
FF = core.ffmpeg()


def fixtures():
    for f in sorted(FIX.glob("*.json")):
        yield f.stem, json.loads(f.read_text(encoding="utf-8"))


def ass_of(proj, W=1920, H=1080):
    return editor.build_ass(editor.migrate_seq(copy.deepcopy(proj)), W, H, 30)


def events(ass):
    """[(층, 시작, 끝, 스타일, 본문)] — 파일 차례대로."""
    out = []
    for ln in ass.splitlines():
        if ln.startswith("Dialogue: "):
            layer, s, e, style, rest = ln[len("Dialogue: "):].split(",", 4)
            out.append((int(layer), s, e, style, rest))
    return out


def styles(ass):
    return [ln for ln in ass.splitlines() if not ln.startswith("Dialogue")]


def framemd5(ass, r):
    """subs.ass 를 단색 바탕에 태운 프레임들의 md5 (내보내기와 같은 subtitles 필터 · fontsdir = editor.FONTS)."""
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "subs.ass").write_text(ass, encoding="utf-8")
        p = subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", f"color=c=0x404040:s={r['w']}x{r['h']}:r={r['rate']}:d={r['dur']}",
                            "-vf", f"format=yuv420p,subtitles=subs.ass:fontsdir={editor.FONTS}", "-f", "framemd5", "-"],
                           cwd=td, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-800:]
    return [ln.rsplit(",", 1)[1].strip() for ln in p.stdout.splitlines() if ln and not ln.startswith("#")]


def item(i, start, a, b, track="V1"):
    return {"id": i, "track": track, "media": "main", "start": start, "in": a, "out": b, "speed": 1, "rev": False,
            "reframe": 0.5, "fit": "auto", "fx": {}, "color": {}}


def seq(captions=(), titles=(), shapes=(), tactics=(), fmt="long", otracks=None, on=True, style=None, items=None):
    q = {"v": 2, "tracks": editor.default_tracks(), "format": fmt, "captionStyle": dict(style or (editor.LONG_STYLE if fmt == "long" else editor.SHORTS_STYLE)), "captionsOn": on,
         "titles": [copy.deepcopy(t) for t in titles], "shapes": [copy.deepcopy(s) for s in shapes], "tactics": [copy.deepcopy(t) for t in tactics],
         "items": items if items is not None else [item("v", 0, 0, 30)], "captions": [copy.deepcopy(c) for c in captions]}
    if otracks is not None:
        q["otracks"] = copy.deepcopy(otracks)
    return q


def T(i, start, dur, trk=None, **st):
    t = {"id": i, "text": f"타이틀 {i}", "start": start, "dur": dur, "style": dict(editor.TITLE_STYLE, **st)}
    if trk:
        t["trk"] = trk
    return t


def SH(i, start, dur, trk=None, **kw):
    s = dict({"id": i, "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.2, "color": "#FF0000", "opacity": 1, "radius": 0, "start": start, "dur": dur}, **kw)
    if trk:
        s["trk"] = trk
    return s


def C(i, start, end, text="자막 하나", trk=None):
    c = {"id": i, "start": start, "end": end, "text": text}
    if trk:
        c["trk"] = trk
    return c


def TAC(i, kind, start, dur, trk=None):
    pts = {"arrow": [[0.1, 0.8], [0.35, 0.55]], "ring": [[0.5, 0.8]], "spot": [[0.6, 0.5]]}[kind]
    t = {"id": i, "kind": kind, "pts": pts, "start": start, "dur": dur}
    if trk:
        t["trk"] = trk
    return t


def ids(ots):
    return [o["id"] for o in ots]


class OverlayTracksTest(unittest.TestCase):
    """editor.overlay_tracks — 줄 목록 정리 (editor.html otList 와 같은 규칙)."""

    def test_defaults(self):
        for v in (None, "x", 3, {}, []):
            self.assertEqual(editor.overlay_tracks({"otracks": v} if v is not None else {}), [{"id": "G1", "k": "g"}, {"id": "C1", "k": "c"}, {"id": "T1", "k": "t"}])

    def test_bad_entries_dropped_and_c1_stripped(self):
        q = {"otracks": [{"id": "T1", "k": "t"}, {"id": "T1", "k": "t", "name": "겹침"}, {"id": "X1", "k": "x"}, {"id": "C2", "k": "t"},
                         {"id": "C10", "k": "c"}, "G2", None, {"id": "C1", "k": "c", "style": {"size": 99}, "hide": True, "name": "기본"},
                         {"id": "G2", "k": "g", "lock": True}]}
        out = editor.overlay_tracks(q)
        self.assertEqual(ids(out), ["G1", "T1", "C1", "G2"])  # G1 맨 아래 · 저장 차례 그대로
        c1 = next(o for o in out if o["id"] == "C1")
        self.assertEqual(c1, {"id": "C1", "k": "c", "name": "기본"})
        self.assertIn("style", q["otracks"][7], "seq 는 바꾸지 않음")

    def test_missing_bases_reinserted(self):
        self.assertEqual(ids(editor.overlay_tracks({"otracks": [{"id": "T2", "k": "t"}]})), ["G1", "T2", "C1", "T1"])
        self.assertEqual(ids(editor.overlay_tracks({"otracks": [{"id": "C1", "k": "c"}, {"id": "G1", "k": "g"}]})), ["C1", "G1", "T1"])

    def test_caption_tracks_synthesised(self):
        caps = [C("a", 0, 1, trk="C3"), C("b", 1, 2, trk="C2"), C("c", 2, 3, trk="C1"), C("d", 3, 4, trk="T2"), C("e", 4, 5)]
        out = editor.overlay_tracks({"captions": caps, "captionsOn": False})
        self.assertEqual(ids(out), ["G1", "C1", "C2", "C3", "T1"])
        self.assertEqual([o.get("hide") for o in out if o["id"] in ("C2", "C3")], [True, True], "자막을 끈 편집본은 새 자막 줄도 숨김")
        self.assertEqual([o.get("hide") for o in editor.overlay_tracks({"captions": caps}) if o["id"] == "C2"], [False])
        # 줄 목록이 있으면 맨 위 C 줄 바로 위
        out = editor.overlay_tracks({"captions": caps, "otracks": [{"id": "G1", "k": "g"}, {"id": "T1", "k": "t"}, {"id": "C1", "k": "c"}, {"id": "G2", "k": "g"}]})
        self.assertEqual(ids(out), ["G1", "T1", "C1", "C2", "C3", "G2"])
        # MSG 후보 자막(msgCaps)이 있으면 그것을 봄
        self.assertEqual(ids(editor.overlay_tracks({"captions": caps, "msgCaps": [C("m", 0, 1)]})), ["G1", "C1", "T1"])

    def test_title_shape_repair_only_with_list(self):
        q = {"titles": [T("a", 0, 2, "T3")], "shapes": [SH("s", 0, 2, "G2")], "tactics": [TAC("x", "ring", 0, 2, "G4")]}
        self.assertEqual(ids(editor.overlay_tracks(q)), ["G1", "C1", "T1"], "줄 목록이 없는 편집본(쇼츠 만들기 복사 등)은 trk 무시")
        self.assertEqual(ids(editor.overlay_tracks(dict(q, otracks=[]))), ["G1", "G2", "G4", "C1", "T1", "T3"])
        ots = editor.overlay_tracks(q)
        self.assertEqual(editor.track_of(q["titles"][0], "T", set(ids(ots))), "T1")
        self.assertEqual(editor.track_of({"trk": "G2"}, "T", {"G2", "T1"}), "T1", "종류가 다른 줄은 첫 줄")

    def test_cap_trk(self):
        self.assertEqual(editor.cap_trk({"trk": "C2"}), "C2")
        self.assertEqual(editor.cap_trk({"trk": "C2"}, "oth"), "C1", "보관함의 다른 영상 자막은 늘 C1")
        for bad in ("C1", "C0", "T2", "C10", 2, None):
            self.assertEqual(editor.cap_trk({"trk": bad}), "C1")


class GoldenTest(unittest.TestCase):
    """줄 기능 전(기준 커밋 · tests/fixtures/ovstack)과 같은 그림 — 줄 목록이 없는 예전 프로젝트·MSG·자동 가편집."""

    def test_styles_and_events_same(self):
        for name, fx in fixtures():
            old = gzip.decompress((FIX / f"{name}.ass.gz").read_bytes()).decode("utf-8")
            new = ass_of(fx["proj"], fx["W"], fx["H"])
            self.assertEqual(styles(new), styles(old), name)
            eo, en = events(old), events(new)
            self.assertEqual(sorted(e[1:] for e in en), sorted(e[1:] for e in eo), name)
            # 시간이 겹친 두 대사의 앞뒤 (층, 파일 차례) 가 예전과 같음
            ko = {e[1:]: (e[0], i) for i, e in enumerate(eo)}
            kn = {e[1:]: (e[0], i) for i, e in enumerate(en)}
            uniq = [k for k in ko if sum(1 for e in eo if e[1:] == k) == 1]
            secs = lambda t: sum(float(x) * m for x, m in zip(t.split(":"), (3600, 60, 1)))  # noqa: E731
            bad = 0
            for i, a in enumerate(uniq):
                for b in uniq[i + 1:]:
                    if secs(a[0]) < secs(b[1]) and secs(b[0]) < secs(a[1]) and (ko[a] < ko[b]) != (kn[a] < kn[b]):
                        bad += 1
            self.assertEqual(bad, 0, name)

    def test_framemd5_same(self):
        for name, fx in fixtures():
            gold = (FIX / f"{name}.framemd5").read_text(encoding="utf-8").split()
            got = framemd5(ass_of(fx["proj"], fx["W"], fx["H"]), fx["render"])
            self.assertEqual(len(got), len(gold), name)
            self.assertEqual([i for i, (a, b) in enumerate(zip(got, gold)) if a != b], [], name)

    def test_default_layers(self):
        fx = json.loads((FIX / "a_long_ed2.json").read_text(encoding="utf-8"))
        ev = events(ass_of(fx["proj"]))
        self.assertEqual({e[0] for e in ev if e[3] in ("Shape", "Board")}, {0})
        self.assertEqual({e[0] for e in ev if e[3] == "Cap"}, {1})
        self.assertEqual({e[0] for e in ev if e[3].startswith("T")}, {2})


class LayerTest(unittest.TestCase):
    """층(Layer) = 줄 차례 · 같은 줄 안은 배열 차례(전술 그림은 스포트라이트 먼저)."""

    def test_reordered(self):
        q = seq([C("c", 1, 3)], [T("t", 0, 5)], [SH("s", 0, 5)], otracks=[{"id": "T1", "k": "t"}, {"id": "C1", "k": "c"}, {"id": "G1", "k": "g"}])
        ev = events(ass_of(q))
        self.assertEqual({e[3]: e[0] for e in ev}, {"Tt": 0, "Cap": 1, "Shape": 2})

    def test_tactics_on_two_tracks(self):
        q = seq(tactics=[TAC("a", "arrow", 0, 1), TAC("r", "ring", 0, 1, "G2"), TAC("p", "spot", 0, 1, "G2")], titles=[T("t", 0, 1)],
                otracks=[{"id": "G1", "k": "g"}, {"id": "C1", "k": "c"}, {"id": "T1", "k": "t"}, {"id": "G2", "k": "g"}])
        ass = ass_of(q)
        layers = sorted({e[0] for e in events(ass) if e[3] == "Board"})
        self.assertEqual(layers, [0, 3], "G2 는 타이틀(2) 위")
        self.assertEqual({e[0] for e in events(ass) if e[3] == "Tt"}, {2})
        g2 = [e for e in events(ass) if e[0] == 3]
        spot_first = [e for e in g2 if "\\blur" in e[4] or "&H000000&" in e[4].upper()]
        self.assertTrue(g2 and spot_first, "G2 에 스포트라이트·원")
        # 같은 줄 안: 스포트라이트(어둡게)가 먼저 (tactic.ordered)
        import tactic
        self.assertEqual([t["kind"] for t in tactic.ordered(q["tactics"][1:])], ["spot", "ring"])

    def test_stale_trk_without_list(self):
        q = seq(titles=[T("a", 0, 2, "T3"), T("b", 0, 2)], shapes=[SH("s", 0, 2, "G5")])
        ev = events(ass_of(q))
        self.assertEqual([(e[0], e[3]) for e in ev], [(0, "Shape"), (2, "Ta"), (2, "Tb")], "줄 목록 없으면 trk 는 무시 · 배열 차례")


class HideTest(unittest.TestCase):
    def test_captions_off_removes_only_cap(self):
        q = seq([C("c", 1, 3)], [T("t", 0, 5)], [SH("s", 0, 5)], on=False)
        self.assertEqual(sorted({e[3] for e in events(ass_of(q))}), ["Shape", "Tt"])

    def test_hidden_rows(self):
        q = seq([C("c", 1, 3), C("d", 4, 6, trk="C2")], [T("a", 0, 5), T("b", 0, 5, "T2")], [SH("s", 0, 5, "G2")], [TAC("r", "ring", 0, 1, "G2")],
                otracks=[{"id": "G1", "k": "g"}, {"id": "G2", "k": "g", "hide": True}, {"id": "C1", "k": "c"}, {"id": "C2", "k": "c", "hide": True},
                         {"id": "T1", "k": "t"}, {"id": "T2", "k": "t", "hide": True}])
        st = {e[3] for e in events(ass_of(q))}
        self.assertEqual(st, {"Cap", "Ta"}, "숨긴 줄(G2·C2·T2)은 내보내기에서 빠짐")
        self.assertIn("Style: Cap2,", ass_of(q), "스타일 줄은 남아도 됨")
        # SRT 는 숨긴 줄·자막 끄기와 상관없이 모든 자막 줄
        q2 = dict(q, captionsOn=False)
        self.assertEqual([c["text"] for c in editor.timeline_captions(editor.migrate_seq(copy.deepcopy(q2)))], ["자막 하나", "자막 하나"])
        srt = editor._srt(editor.timeline_captions(editor.migrate_seq(copy.deepcopy(q2))))
        self.assertEqual(srt.count("-->"), 2)


class CaptionTrackTest(unittest.TestCase):
    def test_cap2_style_and_events(self):
        q = seq([C("c", 1, 3), C("d", 1.5, 3.5, "둘째 줄", trk="C2")])
        ass = ass_of(q)
        ev = events(ass)
        self.assertEqual([(e[0], e[3]) for e in ev], [(1, "Cap"), (2, "Cap2")], "C2 는 C1 바로 위 층 · 타이틀은 3")
        st = editor.LONG_STYLE
        y2 = editor.cap_track_default(st, 2, "long")
        self.assertIn(f"\\pos(960,{int(1080 * y2)})", [e for e in ev if e[3] == "Cap2"][0][4])
        self.assertLess(y2, st["y"], "C1 이 아래쪽이면 위로 한 줄")

    def test_derived_y(self):
        for fmt, H in (("shorts", 1920), ("long", 1080)):
            for st in (editor.SHORTS_STYLE, editor.LONG_STYLE, dict(editor.DEFAULT_STYLE, y=0.31, size=88, strokeW=11), dict(editor.LONG_STYLE, y=0.04)):
                band = (1.25 * st["size"] + 2 * st["strokeW"]) / H + 0.012
                for n in range(2, 10):
                    y = editor.cap_track_default(st, n, fmt)
                    want = max(0.1, st["y"] - (n - 1) * band) if st["y"] >= 0.5 else min(0.95, st["y"] + (n - 1) * band)
                    self.assertEqual(y, want)
                    self.assertTrue(y >= 0.1 if st["y"] >= 0.5 else y <= 0.95)
        self.assertEqual(editor.cap_track_default(dict(editor.LONG_STYLE, y=0.9), 9, "long"), max(0.1, 0.9 - 8 * ((1.25 * 52 + 2 * editor.LONG_STYLE["strokeW"]) / 1080 + 0.012)))
        self.assertEqual(editor.cap_track_default(dict(editor.LONG_STYLE, y=0.4), 9, "long"), 0.95, "위쪽 자막은 아래로 · 0.95 까지")

    def test_explicit_style_wins(self):
        q = seq(otracks=[{"id": "C2", "k": "c", "style": {"size": 33, "fill": "#00FF00"}}])
        o = editor.overlay_tracks(q)[1]
        st = editor.cap_style_of(q, o)
        self.assertEqual((st["size"], st["fill"], st["y"], st["effect"]), (33, "#00FF00", editor.LONG_STYLE["y"], editor.LONG_STYLE["effect"]))
        self.assertIs(editor.cap_style_of(q, {"id": "C1", "k": "c", "style": {"size": 1}}), q["captionStyle"])

    def test_other_media_caption_on_c1(self):
        oth = [C("o", 0.5, 2.5, "다른 영상 말", trk="C2")]
        q = seq([C("c", 1, 3, trk="C2")], items=[item("v", 0, 0, 5), dict(item("w", 5, 0, 4), media="oth"), dict(item("wa", 5, 0, 4, "A1"), media="oth")])
        q["media"] = [{"id": "oth", "kind": "video", "src": "videos", "file": "oth.mp4", "dur": 10}]
        with mock.patch.object(editor, "media_captions", lambda f, src="videos": oth):
            tl = editor.timeline_captions(q)
        self.assertEqual([(c["text"], c.get("trk")) for c in tl], [("자막 하나", "C2"), ("다른 영상 말", None)])


class TimelineCaptionsTest(unittest.TestCase):
    def test_c1_dict_identical(self):
        q = seq([C("c", 1, 3), C("d", 3.2, 5)])
        tl = editor.timeline_captions(q)
        self.assertTrue(all("trk" not in c for c in tl))
        self.assertEqual(tl, editor.timeline_captions(dict(q, captions=[dict(c, trk="C1") for c in q["captions"]])))

    def test_join_per_track(self):
        # C1 자막 하나가 컷으로 나뉘고(같은 원본이 이어짐) 그 사이에 C2 자막 조각이 있어도 C1 은 한 덩어리
        items = [item("a", 0, 0, 2), item("b", 2, 2, 4)]
        q = seq([C("c", 0.5, 3.5, "길게 이어지는 말"), C("x", 1.95, 2.3, "끼어듦", trk="C2")], items=items)
        tl = editor.timeline_captions(q)
        self.assertEqual([(c["text"], round(c["start"], 2), round(c["end"], 2), c.get("trk")) for c in tl],
                         [("길게 이어지는 말", 0.5, 3.5, None), ("끼어듦", 1.95, 2.65, "C2")])

    def test_cap_min_per_track(self):
        # 짧은 C2 자막이 C1 자막을 줄이거나 늦추지 않음 (예전 한 줄이면 C1 다음 자막 시작을 늦춤)
        q = seq([C("a", 1.0, 1.2, "가"), C("b", 1.3, 3.0, "나"), C("z", 1.1, 1.2, "다", trk="C2")])
        tl = {c["text"]: (round(c["start"], 3), round(c["end"], 3)) for c in editor.timeline_captions(q)}
        base = {c["text"]: (round(c["start"], 3), round(c["end"], 3)) for c in editor.timeline_captions(seq([C("a", 1.0, 1.2, "가"), C("b", 1.3, 3.0, "나")]))}
        self.assertEqual((tl["가"], tl["나"]), (base["가"], base["나"]))
        self.assertEqual(tl["다"], (1.1, 1.8), "C2 자막은 자기 줄에서 CAP_MIN")


class SignatureTest(unittest.TestCase):
    """줄만 옮긴 자막도 '고친 자막' — 자동 작업(MSG·끝까지 자동으로·저장 합치기)이 몰래 바꾸지 않음."""

    def test_cap_text(self):
        a = [C("a", 0, 1, "가"), C("b", 1, 2, "나")]
        b = [C("a", 0, 1, "가"), C("b", 1, 2, "나", trk="C2")]
        self.assertNotEqual(editor._cap_text(a), editor._cap_text(b))
        self.assertEqual(editor._cap_text(a), "가\n나", "줄이 없으면 예전과 같은 서명")

    def test_merge_auto_keeps_moved_caption(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        p = tmp / "p.json"
        old = [C("a", 0, 1, "가")]
        p.write_text(json.dumps({"captions": [C("a", 0, 1, "자동이 다시 들은 글")], "sequences": [], "media": []}, ensure_ascii=False), encoding="utf-8")
        merged = {}
        win = {"captions": [C("a", 0, 1, "가", trk="C2")], "sequences": [], "media": []}
        editor._merge_auto(p, win, [(0, [], [], editor._cap_text(old))], merged)
        self.assertEqual(win["captions"][0].get("trk"), "C2", "창이 줄을 옮긴 자막은 그대로")
        self.assertNotIn("captions", merged)
        win2 = {"captions": copy.deepcopy(old), "sequences": [], "media": []}
        editor._merge_auto(p, win2, [(0, [], [], editor._cap_text(old))], merged)
        self.assertEqual(win2["captions"][0]["text"], "자동이 다시 들은 글", "안 고친 자막은 예전처럼 새 글로")

    def test_oneline_keeps_trk(self):
        cap = dict(C("a", 0.0, 6.0, "오늘은 퍼스트 터치를 배워 볼게요 공이 오면 발 안쪽으로 부드럽게 받아요 그리고 바로 앞으로", trk="C2"))
        out, n = editor.oneline_captions([cap], {"width": 1920, "height": 1080}, terms=[])
        self.assertGreaterEqual(n, 1)
        self.assertGreater(len(out), 1)
        self.assertTrue(all(c.get("trk") == "C2" for c in out), out)


class ExportPathsTest(unittest.TestCase):
    def test_export_saved_bundle_synthesises_c2(self):
        proj = {"captions": [C("a", 1, 3), C("b", 1, 3, "둘째", trk="C2")], "info": {"duration": 30, "width": 1920, "height": 1080}, "source": "x.mp4",
                "media": [], "speech": [], "sequences": [seq(fmt="long")]}
        proj["sequences"][0]["id"] = "S1"
        proj["sequences"][0].pop("captions")
        got = {}
        with mock.patch.object(editor, "load_project", lambda n: copy.deepcopy(proj)), \
                mock.patch.object(editor, "export", lambda name, merged, opts, log: got.update(merged=merged) or []):
            editor.export_saved("x.mp4", "S1")
        ass = editor.build_ass(editor.migrate_seq(got["merged"]), 1920, 1080)
        self.assertIn("Style: Cap2,", ass)
        self.assertEqual([e[3] for e in events(ass)], ["Cap", "Cap2"])

    def test_xmeml_track_count_unchanged(self):
        base = editor.migrate_seq(seq([C("a", 1, 3)], [T("t", 0, 2)]))
        with_ot = dict(copy.deepcopy(base), otracks=[{"id": "G1", "k": "g"}, {"id": "C1", "k": "c"}, {"id": "T1", "k": "t"}, {"id": "T2", "k": "t"}])
        for q in (base, with_ot):
            q["info"] = {"duration": 30, "width": 1920, "height": 1080, "fps": 30}
            q["media"] = [{"id": "main", "kind": "video", "src": "videos", "file": "x.mp4", "dur": 30, "w": 1920, "h": 1080, "fps": 30, "audio": True}]
        a = editor._xmeml(base, 1920, 1080, 30, "x")
        b = editor._xmeml(with_ot, 1920, 1080, 30, "x")
        self.assertEqual(a.count("<track>"), b.count("<track>"))
        self.assertEqual(a, b)


def js_block():
    """editor.html 의 줄 블록 (ensureSeq · defaultTracks · 글자·그림 줄 블록) — 화면(DOM) 없이 S · P · EPS 만 씀."""
    src = (ROOT / "editor.html").read_text(encoding="utf-8")
    i = src.index("const EPS =")
    parts = [src[i:src.index("\n", i)]]
    i = src.index("function ensureSeq(")
    j = src.index("function bindS(", i)
    blk = src[i:j]
    assert "// 글자·그림 줄 (D-296)" in blk and "// 글자·그림 줄 끝" in blk
    assert "document" not in blk and "$(" not in blk, "줄 블록은 화면을 쓰지 않음"
    parts.append(blk)
    return "const FPS = 30; let S = null, P = null;\n" + "\n".join(parts)


def run_node(body, inp):
    prog = js_block() + "\nconst INP = JSON.parse(require('fs').readFileSync(0, 'utf8'));\nlet OUT = null;\n" + body + "\nprocess.stdout.write(JSON.stringify(OUT));"
    r = subprocess.run([NODE, "-e", prog], input=json.dumps(inp), capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode:
        raise AssertionError(r.stderr[-3000:])
    return json.loads(r.stdout)


def rand_seq(rnd, k):
    """무작위 편집본: 틀린 줄·겹친 id 섞인 줄 목록(있거나 없음) · 자막·타이틀·도형·전술 그림 trk 섞음."""
    ids = [f"{c}{n}" for c in "CTG" for n in range(1, 10)]
    q = {"id": f"s{k}", "format": rnd.choice(["long", "shorts"]), "captionsOn": rnd.random() < 0.7,
         "captionStyle": dict(editor.LONG_STYLE), "items": [], "titles": [], "shapes": [], "tactics": []}
    pick = lambda: rnd.choice(ids + ["X1", "C0", "", None, "T10"])  # noqa: E731
    for j in range(rnd.randint(0, 5)):
        t = T(f"t{k}_{j}", round(rnd.uniform(0, 20), 2), round(rnd.uniform(0.3, 8), 2))
        if rnd.random() < 0.5:
            t["trk"] = pick()
        q["titles"].append(t)
    for j in range(rnd.randint(0, 3)):
        q["shapes"].append(dict(SH(f"g{k}_{j}", 1, 2), **({"trk": pick()} if rnd.random() < 0.5 else {})))
    for j in range(rnd.randint(0, 3)):
        q["tactics"].append(dict(TAC(f"a{k}_{j}", "ring", 1, 2), **({"trk": pick()} if rnd.random() < 0.5 else {})))
    if rnd.random() < 0.6:
        ots = []
        for _ in range(rnd.randint(0, 8)):
            tid = pick()
            o = {"id": tid, "k": (tid or "x")[0].lower() if rnd.random() < 0.9 else "q"}
            if rnd.random() < 0.3:
                o["hide"] = True
            if rnd.random() < 0.2:
                o["style"] = {"size": 40}
            ots.append(o)
        q["otracks"] = ots
    caps = [C(f"c{k}_{j}", j, j + 0.5, trk=rnd.choice(["C2", "C3", "C9", "C1", "T2", None])) for j in range(rnd.randint(0, 4))]
    if rnd.random() < 0.2:
        q["msgCaps"] = [C("m", 0, 1, trk="C4")]
    return q, caps


@unittest.skipUnless(NODE, "node 가 없어요")
class NodeParityTest(unittest.TestCase):
    """editor.html 줄 블록(미리보기·타임라인) = editor.py (내보내기) — node 로 그대로 실행."""

    def test_otlist_parity_random(self):
        rnd = random.Random(296)
        cases = [rand_seq(rnd, k) for k in range(300)]
        for _, fx in fixtures():
            cases.append((fx["proj"], fx["proj"].get("captions") or []))
        js = run_node("OUT = INP.map(([q, caps]) => { P = { captions: caps, sequences: [] }; return otList(q, (q.msgCaps && q.msgCaps.length) ? q.msgCaps : caps); });", cases)
        for (q, caps), j in zip(cases, js):
            py = editor.overlay_tracks(dict(q, captions=caps))
            self.assertEqual(py, j, q["id"] if "id" in q else "fixture")

    def test_ensure_idempotent_and_stale_trk(self):
        q = seq(titles=[T("a", 0, 5, "T7"), T("b", 1, 2), T("c", 1.5, 1)], shapes=[SH("s", 0, 1, "G3")])
        out = run_node("""P = { captions: [], sequences: [INP] }; ensureSeq(INP); const a = JSON.stringify(INP.otracks);
            ensureSeq(INP); OUT = { q: INP, same: a === JSON.stringify(INP.otracks) };""", q)
        self.assertTrue(out["same"])
        self.assertEqual(ids(out["q"]["otracks"]), ["G1", "C1", "T1", "T2", "T3"])
        self.assertEqual([t.get("trk") for t in out["q"]["titles"]], [None, "T2", "T3"], "예전 trk(T7)는 버리고 겹침으로 나눔")
        self.assertNotIn("trk", out["q"]["shapes"][0])

    def test_spread_keeps_export_order(self):
        rnd = random.Random(7)
        seqs = []
        for k in range(300):
            n = rnd.randint(1, 12)
            titles = [T(f"t{k}_{j}", round(rnd.uniform(0, 20), 2), round(rnd.uniform(0.3, 15), 2)) for j in range(n)]
            seqs.append(seq(titles=titles, fmt="shorts", on=False))
        out = run_node("P = { captions: [], sequences: INP }; for (const q of INP) ensureSeq(q); OUT = INP;", seqs)

        def order(q):
            pos = {}
            for i, e in enumerate(events(ass_of(q, 1080, 1920))):
                pos[e[3][1:]] = (e[0], i)
            return pos
        bad, rows = 0, 0
        for a, b in zip(seqs, out):
            pa, pb = order(a), order(b)
            ts = a["titles"]
            for i in range(len(ts)):
                for j in range(i):
                    x, y = ts[i], ts[j]
                    if x["start"] < y["start"] + y["dur"] - 1e-3 and y["start"] < x["start"] + x["dur"] - 1e-3 and (pa[x["id"]] > pa[y["id"]]) != (pb[x["id"]] > pb[y["id"]]):
                        bad += 1
            rows = max(rows, sum(1 for o in b["otracks"] if o["k"] == "t"))
            for g in b["titles"]:  # 같은 줄에 겹친 타이틀은 9줄을 넘을 때만
                same = [h for h in b["titles"] if h is not g and h.get("trk") == g.get("trk") and g["start"] < h["start"] + h["dur"] - 1e-3 and h["start"] < g["start"] + g["dur"] - 1e-3]
                if same:
                    self.assertEqual(g.get("trk"), "T9")
        self.assertEqual(bad, 0)
        self.assertLessEqual(rows, 9)

    def test_old_projects_opened_same_pixels(self):
        """예전 프로젝트를 새 편집실로 열어(ensureSeq · 타이틀 나누기) 저장한 편집본도 golden 과 같은 그림."""
        fxs = list(fixtures())
        out = run_node("OUT = INP.map(fx => { P = { captions: fx.proj.captions || [], sequences: [fx.proj] }; ensureSeq(fx.proj); return fx.proj; });", [fx for _, fx in fxs])
        moved = 0
        for (name, fx), q in zip(fxs, out):
            moved += sum(1 for t in q["titles"] if t.get("trk"))
            gold = (FIX / f"{name}.framemd5").read_text(encoding="utf-8").split()
            self.assertEqual(framemd5(ass_of(q, fx["W"], fx["H"]), fx["render"]), gold, name)
        self.assertGreaterEqual(moved, 3, "겹친 타이틀(주제 줄·셋 겹침·MSG)은 T2·T3 로 나뉨")

    def test_cap_track_default_bit_identical(self):
        cases = []
        for fmt in ("shorts", "long"):
            for st in (editor.SHORTS_STYLE, editor.LONG_STYLE, dict(editor.DEFAULT_STYLE, size=88, strokeW=11, y=0.31), dict(editor.LONG_STYLE, y=0.5, size=47.5, strokeW=3.3)):
                for n in range(2, 10):
                    cases.append([st, n, fmt])
        self.assertGreaterEqual(len(cases), 48)
        js = run_node("OUT = INP.map(([st, n, f]) => capTrackDefault(st, n, f));", cases)
        py = [editor.cap_track_default(st, n, f) for st, n, f in cases]
        self.assertEqual(js, py)
        # capStyleOf = cap_style_of
        q = seq(otracks=[{"id": "C2", "k": "c"}, {"id": "C3", "k": "c", "style": {"size": 33}}])
        js = run_node("S = INP; OUT = INP.otracks.map(o => capStyleOf(o));", dict(q, otracks=editor.overlay_tracks(q)))
        self.assertEqual(js, [editor.cap_style_of(q, o) for o in editor.overlay_tracks(q)])

    def place(self, q, caps, body):
        return run_node("S = INP.q; P = { captions: INP.caps, sequences: [S] }; ensureSeq(S);\n" + body, {"q": q, "caps": caps})

    def test_ot_place(self):
        # T: 겹친 타이틀 앞(위) 줄 · 빈 줄은 다시 씀 · 9줄 한도
        q = seq(titles=[T("a", 0, 5), T("b", 6, 2)], otracks=[])
        r = self.place(q, [], """const r1 = otPlace("t", [1, 3]); const r2 = otPlace("t", [5.5, 6]); const r3 = otPlace("t", [7, 7.5]);
            OUT = { r: [r1, r2, r3], ids: S.otracks.map(o => o.id) };""")
        self.assertEqual(r["r"], [{"id": "T2", "made": True}, {"id": "T1"}, {"id": "T2"}])
        self.assertEqual(r["ids"], ["G1", "C1", "T1", "T2"])
        # 9줄이 다 차면 맨 위 겹친 줄에 함께
        q = seq(titles=[T(f"t{j}", 0, 5, f"T{j}" if j > 1 else None) for j in range(1, 10)], otracks=[{"id": f"T{j}", "k": "t"} for j in range(1, 10)])
        r = self.place(q, [], 'OUT = [otPlace("t", [1, 2]), S.otracks.length];')
        self.assertEqual(r, [{"id": "T9", "full": True}, 11])
        # G: 새 도형은 겹친 전술 그림 아래 · 스포트라이트는 화살표 아래, 도형 위
        q = seq(tactics=[TAC("r", "ring", 0, 5)], otracks=[])
        r = self.place(q, [], 'const a = otPlace("g", [1, 2], { sub: "shape" }); OUT = { a, ids: S.otracks.map(o => o.id) };')
        self.assertEqual(r["a"], {"id": "G2", "made": True})
        self.assertEqual(r["ids"], ["G2", "G1", "C1", "T1"], "도형 줄이 전술 그림 줄 아래")
        q = seq(shapes=[SH("s", 0, 5)], tactics=[TAC("x", "arrow", 0, 5, "G2")], otracks=[{"id": "G1", "k": "g"}, {"id": "G2", "k": "g"}, {"id": "C1", "k": "c"}, {"id": "T1", "k": "t"}])
        r = self.place(q, [], 'const a = otPlace("g", [1, 2], { sub: "spot" }); OUT = { a, ids: S.otracks.map(o => o.id) };')
        self.assertEqual(r["a"], {"id": "G3", "made": True})
        self.assertEqual(r["ids"][:3], ["G1", "G3", "G2"], "스포트라이트는 도형(G1) 위 · 화살표(G2) 아래")
        # 잠긴 줄·숨긴 줄은 건너뜀 · 고른 줄(prefer)
        q = seq(titles=[T("a", 0, 5)], otracks=[{"id": "G1", "k": "g"}, {"id": "C1", "k": "c"}, {"id": "T1", "k": "t"}, {"id": "T2", "k": "t", "lock": True}, {"id": "T3", "k": "t"}, {"id": "T4", "k": "t"}])
        r = self.place(q, [], 'OUT = [otPlace("t", [1, 2]), otPlace("t", [6, 7], { prefer: "T4" }), otPlace("t", [6, 7], { prefer: "T2" })];')
        self.assertEqual(r, [{"id": "T3"}, {"id": "T4"}, {"id": "T1"}])
        # C: 원본 시각으로 프로젝트 자막과 겹치면 새 자막 줄 (자막을 끈 편집본은 숨김)
        q = seq(otracks=[], on=False)
        r = self.place(q, [C("a", 10, 12)], 'OUT = otPlace("c", [11, 13]);')
        self.assertEqual(r, {"id": "C2", "made": True})
        r = self.place(seq(otracks=[]), [C("a", 10, 12), C("b", 10, 12, trk="C3")], 'OUT = [otPlace("c", [11, 13]), S.otracks.find(o => o.id === "C2")];')
        self.assertEqual(r[0], {"id": "C2", "made": True}, "C3 가 다른 편집본에서 쓰여도 가장 작은 빈 번호")

    def test_ot_settle(self):
        q = seq(titles=[T("a", 0, 5), T("b", 6, 2)], otracks=[{"id": "G1", "k": "g"}, {"id": "C1", "k": "c"}, {"id": "T1", "k": "t"}, {"id": "T2", "k": "t"}, {"id": "T3", "k": "t"}])
        r = self.place(q, [], """const b = S.titles[1]; const r0 = otSettle(b, "t"); b.start = 4; const r1 = otSettle(b, "t");
            const a = S.titles[0]; a.trk = "T3"; const r2 = otSettle(a, "t"); OUT = [r0, r1, b.trk, r2];""")
        self.assertEqual(r, [None, {"id": "T2"}, "T2", None], "자기 줄에서 겹칠 때만 위로")
        q = seq(titles=[T("a", 0, 5), T("b", 1, 2), T("c", 1.5, 2, "T2")], otracks=[{"id": "G1", "k": "g"}, {"id": "C1", "k": "c"}, {"id": "T1", "k": "t"}, {"id": "T2", "k": "t"}])
        r = self.place(q, [], 'const b = S.titles[1]; OUT = [otSettle(b, "t"), S.otracks.map(o => o.id)];')
        self.assertEqual(r, [{"id": "T3", "made": True}, ["G1", "C1", "T1", "T3", "T2"]], "바로 위(T2)가 차 있고 더 위가 없으면 자기 줄 바로 위 새 줄")


if __name__ == "__main__":
    unittest.main()
