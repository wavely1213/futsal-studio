"""프리미어와 주고받기 (D-345~D-349) — 저장소 폴더에서 python3 -m unittest tests.test_interop
· SRT 가져오기: 인코딩(UTF-8·BOM·CP949·UTF-16) · CRLF · 번호 없는 파일 · 꾸밈 글자 · 겹침(끝 줄이기·다른 트랙) · 프레임 맞추기
· 트랙마다 SRT 내보내기: 숨긴 트랙 빼기 · 다 숨겼으면 자막 1 · 트랙마다 따로 · 구간에서 잘린 2프레임 미만 조각 빼기
· 프리미어 XML(FCP7): 잘 만든 XML · 시퀀스 크기 · 디졸브(start/end -1 · 손잡이) · 연결 · 스테레오 한 쌍 · 마커 · 투명 영상 트랙 · OTIO 로 읽기 ·
  Basic Motion·Crop 이 미리보기·내보내기 화면과 같은지 (점 대응 + 실제 그림 비교) · 빠진 것 목록
· 글자·도형만 투명 영상: 알파(반투명 그대로) · 같은 프레임 · PNG 묶음으로 바꾸기 · 지금 화면 그림."""
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402

FF = core.ffmpeg()


def srt_bytes(text, enc="utf-8", bom=False):
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode(enc)


SAMPLE = ("1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>첫 번째</i> 자막\r\n\r\n"
          "2\r\n00:00:02,400 --> 00:00:04,000\r\n{\\an8}두 번째 &amp; 줄\r\n둘째 줄\r\n\r\n"
          "3\r\n00:00:05,000 --> 00:00:09,000\r\n<font color=\"#ff0\">긴 자막</font>\r\n\r\n"
          "4\r\n00:00:06,000 --> 00:00:07,500\r\n동시에 나오는 자막\r\n")


class SrtParse(unittest.TestCase):
    def test_encodings(self):
        for data, enc in ((srt_bytes(SAMPLE), "utf-8"), (srt_bytes(SAMPLE, bom=True), "utf-8-sig"), (srt_bytes(SAMPLE, "cp949"), "cp949"),
                          (b"\xff\xfe" + SAMPLE.encode("utf-16-le"), "utf-16"), (SAMPLE.encode("utf-16-le"), "utf-16")):
            text, got = editor.srt_decode(data)
            self.assertEqual(got, enc)
            cues, bad = editor.parse_srt(text)
            self.assertEqual([c["text"] for c in cues], ["첫 번째 자막", "두 번째 & 줄\n둘째 줄", "긴 자막", "동시에 나오는 자막"], enc)
            self.assertEqual(bad, 0)

    def test_line_endings_numbers_and_oddities(self):
        cr = SAMPLE.replace("\r\n", "\r")
        self.assertEqual(len(editor.parse_srt(cr)[0]), 4)
        # 번호 없음 · 시각 앞뒤 빈 줄 · 시 없는 시각 · 점 소수 · 거꾸로 된 시각·빈 글은 버림 · 숫자뿐인 글은 그대로
        t = ("\n\n00:01.5 --> 00:03.250\n3\n\n00:00:04.000 --> 00:00:05.000\n두 번째\n\n"
             "00:00:09,000 --> 00:00:08,000\n거꾸로\n\n00:00:10,000 --> 00:00:11,000\n\n\n")
        cues, bad = editor.parse_srt(t)
        self.assertEqual([(c["start"], c["end"], c["text"]) for c in cues], [(1.5, 3.25, "3"), (4.0, 5.0, "두 번째")])
        self.assertEqual(bad, 2)
        # 번호가 있으면 글 끝의 숫자 한 줄은 다음 자막 번호 (빈 줄이 빠진 파일도)
        t2 = "1\n00:00:01,000 --> 00:00:02,000\n하나\n2\n00:00:03,000 --> 00:00:04,000\n둘\n"
        self.assertEqual([c["text"] for c in editor.parse_srt(t2)[0]], ["하나", "둘"])
        # 정렬: 시작 순
        t3 = "1\n00:00:05,000 --> 00:00:06,000\n뒤\n\n2\n00:00:01,000 --> 00:00:02,000\n앞\n"
        self.assertEqual([c["text"] for c in editor.parse_srt(t3)[0]], ["앞", "뒤"])

    def test_lanes_trim_and_frames(self):
        cues, _ = editor.parse_srt(SAMPLE)
        out, trimmed, moved = editor.srt_lanes(cues, 30)
        self.assertEqual([(c["start"], c["end"], c["lane"]) for c in out],
                         [(1.0, 2.4, 0), (2.4, 4.0, 0), (5.0, 9.0, 0), (6.0, 7.5, 1)])
        self.assertEqual((trimmed, moved), (1, 1))
        # 프레임에 맞춤 (프리미어처럼 시퀀스 프레임) · 1프레임도 안 되면 1프레임
        out, *_ = editor.srt_lanes([{"start": 1.016, "end": 1.02, "text": "a"}, {"start": 2.049, "end": 3.001, "text": "b"}], 30)
        self.assertEqual([(round(c["start"] * 30), round(c["end"] * 30)) for c in out], [(30, 31), (61, 90)])
        # 9개가 다 차면 마지막 트랙에 겹친 채로
        many = [{"start": 0.0, "end": 10.0, "text": str(k)} for k in range(11)]
        out, _, moved = editor.srt_lanes(many, 30)
        self.assertEqual(max(c["lane"] for c in out), editor.SRT_LANES - 1)

    def test_import_errors(self):
        with self.assertRaises(ValueError):
            editor.srt_import(b"hello")
        with self.assertRaises(ValueError):
            editor.srt_import(b"x" * (editor.SRT_MAX + 1))
        r = editor.srt_import(srt_bytes(SAMPLE, "cp949"), "30")
        self.assertEqual((r["encoding"], r["lanes"], r["trimmed"], r["moved"], len(r["cues"])), ("cp949", 2, 1, 1, 4))


def seq(caps=(), **kw):
    p = {"id": "s", "name": "쇼츠 1", "format": "shorts", "v": 2, "captionsOn": True, "captionStyle": dict(editor.SHORTS_STYLE), "titles": [], "shapes": [],
         "layout": {"mode": "fill"}, "master": {"volume": 1, "normalize": False, "lufs": -14}, "duck": {"on": False, "amount": -14},
         "tracks": editor.default_tracks(), "items": editor._items_from_cuts([{"in": 0.0, "out": 10.0}]), "trans": [], "markers": [],
         "captions": json.loads(json.dumps(list(caps))), "info": {"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0}, "source": "x.mp4",
         "media": [{"id": "main", "kind": "video", "src": "videos", "file": "x.mp4", "dur": 10.0, "w": 1920, "h": 1080, "fps": 30.0, "audio": True}]}
    p.update(json.loads(json.dumps(kw)))
    return editor.migrate_seq(p)


class SrtExport(unittest.TestCase):
    CAPS = [{"id": "a", "start": 1.0, "end": 2.0, "text": "하나"}, {"id": "b", "start": 3.0, "end": 4.0, "text": "둘", "trk": "C2"},
            {"id": "c", "start": 5.0, "end": 6.0, "text": "셋", "trk": "C3"}]
    OT = [{"id": "G1", "k": "g"}, {"id": "C1", "k": "c"}, {"id": "C2", "k": "c", "name": "설명"}, {"id": "C3", "k": "c", "hide": True}, {"id": "T1", "k": "t"}]

    def files(self, p, **kw):
        return [(fn, [ln for ln in txt.splitlines() if "-->" in ln or ln and not ln[0].isdigit()], t)
                for fn, txt, t in editor.srt_files(p, "S", kw.pop("lo", 0.0), kw.pop("hi", 10.0), 30, **kw)]

    def test_hidden_tracks_excluded_by_default_and_merge(self):
        p = seq(self.CAPS, otracks=self.OT)
        out = self.files(p)
        self.assertEqual([(f, t) for f, _, t in out], [("S.srt", ["C1", "C2"])])
        self.assertNotIn("셋", "".join(out[0][1]))
        self.assertEqual(editor.srt_default_tracks(p), ["C1", "C2"])

    def test_all_hidden_keeps_c1_for_youtube(self):
        p = seq(self.CAPS, otracks=self.OT, captionsOn=False)
        p["otracks"][2]["hide"] = True
        self.assertEqual(editor.srt_default_tracks(p), ["C1"])
        self.assertEqual([(f, t) for f, _, t in self.files(p)], [("S.srt", ["C1"])])

    def test_split_per_track_names(self):
        p = seq(self.CAPS, otracks=self.OT)
        out = self.files(p, tracks=["C3", "C1", "C2"], split=True)
        self.assertEqual([(f, t) for f, _, t in out], [("S.srt", ["C1"]), ("S_설명.srt", ["C2"]), ("S_자막 3.srt", ["C3"])])
        # 첫 트랙에 자막이 없으면 다음 트랙이 '<이름>.srt'
        out = self.files(p, tracks=["C2", "C3"], split=True, lo=2.5, hi=10.0)
        self.assertEqual([f for f, _, _ in out], ["S.srt", "S_자막 3.srt"])

    def test_range_slivers(self):
        caps = [{"id": "a", "start": 1.0, "end": 2.0, "text": "잘림", "tm": 1}, {"id": "b", "start": 2.0, "end": 2.06, "text": "짧음", "tm": 1},
                {"id": "c", "start": 2.5, "end": 3.0, "text": "안"}]
        p = seq(caps, otracks=self.OT[:2] + self.OT[4:])
        # 구간 2-1/30 ~ : '잘림'은 1프레임만 남음 → 뺌 · '짧음'(손으로 맞춘 1.8프레임 · 안 잘림)은 그대로
        out = editor.srt_files(p, "S", 2.0 - 1 / 30, 10.0, 30)
        self.assertNotIn("잘림", out[0][1])
        self.assertIn("짧음", out[0][1])
        self.assertTrue(out[0][1].startswith("1\n00:00:00,033 --> "), out[0][1][:60])


class FitParity(unittest.TestCase):
    """fit_rect(XML·미리보기 셈) = _fit(내보내기 셈)을 짝수 반올림만 빼고 같음."""

    def test_same_as_export_fit(self):
        md = {"w": 1920, "h": 1080, "kind": "video"}
        for lay in ({"mode": "fill", "cropTop": 0.1, "cropBottom": 0.05}, {"mode": "box", "zoom": 1.3, "vpos": 0.4}, {"mode": "blur"}):
            for rf in (0.0, 0.3, 1.0):
                it = {"track": "V1", "reframe": rf, "fit": "auto"}
                q = {"format": "shorts", "layout": lay}
                a, b = editor.fit_rect(it, md, q, 1080, 1920), editor._fit(it, md, q, 1080, 1920)
                for k in ("bw", "bh", "bx", "by"):
                    self.assertAlmostEqual(a[k], b[k], delta=2.0, msg=(lay, rf, k))


def premiere_point(mo, sw, sh, W, H, u, v):
    """프리미어가 Basic Motion 을 읽는 대로: 원본 가운데가 center 자리 · 확대·회전은 원본 가운데 기준."""
    s, r = mo["scale"] / 100, math.radians(mo["rot"])
    dx, dy = (u - sw / 2) * s, (v - sh / 2) * s
    return (W / 2 + mo["center"][0] * W + math.cos(r) * dx - math.sin(r) * dy, H / 2 + mo["center"][1] * H + math.sin(r) * dx + math.cos(r) * dy)


def our_point(it, md, q, W, H, u, v):
    """미리보기(editor.html showItem)·내보내기(_build_segment)의 셈: 원본 점 → 맞춘 상자 → 효과(기준점 둘레 회전·확대 + 이동)."""
    f = editor.fit_rect(it, md, q, W, H)
    s = f["mw"] / md["w"]
    x, y = f["bx"] + f["mx"] + u * s, f["by"] + f["my"] + v * s
    m = editor.i_mt(it, (it["start"] + editor.i_end(it)) / 2)
    pos, sc, rot = editor.kf_at(editor.param(it, "pos"), m), editor.kf_at(editor.param(it, "scale"), m) / 100, math.radians(editor.kf_at(editor.param(it, "rot"), m))
    anc = editor.param(it, "anchor")["v"]
    ax, ay = f["bx"] + anc[0] * f["bw"], f["by"] + anc[1] * f["bh"]
    dx, dy = sc * (x - ax), sc * (y - ay)
    return (ax + (pos[0] - 0.5) * W + math.cos(rot) * dx - math.sin(rot) * dy, ay + (pos[1] - 0.5) * H + math.sin(rot) * dx + math.cos(rot) * dy)


class MotionMath(unittest.TestCase):
    def test_points_match(self):
        cases = [  # (형식, 트랙, 화면 채우기, 원본 크기, 효과)
            ("shorts", "V1", {"mode": "fill", "cropTop": 0.08, "cropBottom": 0.04}, (1920, 1080), {}),
            ("shorts", "V1", {"mode": "fill"}, (1920, 1080), {"scale": {"v": 118, "k": []}, "pos": {"v": [0.55, 0.47], "k": []}}),
            ("shorts", "V1", {"mode": "box", "zoom": 1.4, "vpos": 0.3, "cropTop": 0.1}, (1920, 1080), {"rot": {"v": 12, "k": []}}),
            ("shorts", "V2", {"mode": "fill"}, (1920, 1080), {"scale": {"v": 50, "k": []}, "anchor": {"v": [0.2, 0.7], "k": []}, "rot": {"v": -30, "k": []},
                                                            "pos": {"v": [0.3, 0.6], "k": []}}),
            ("long", "V1", {"mode": "fill"}, (1080, 1920), {}),
            ("long", "V2", {"mode": "fill"}, (3840, 2160), {"scale": {"v": 35, "k": []}, "pos": {"v": [0.8, 0.2], "k": []}}),
        ]
        for fmt, trk, lay, (sw, sh), fx in cases:
            W, H = (1080, 1920) if fmt == "shorts" else (1920, 1080)
            it = {"id": "v", "track": trk, "media": "main", "start": 0.0, "in": 0.0, "out": 4.0, "speed": 1, "rev": False, "fit": "auto", "reframe": 0.3, "fx": fx}
            md = {"w": sw, "h": sh, "kind": "video"}
            q = {"format": fmt, "layout": lay}
            mo = editor.premiere_motion(it, md, q, W, H)
            for u, v in ((0, 0), (sw, 0), (sw / 2, sh / 2), (sw * 0.3, sh * 0.9), (sw, sh)):
                a, b = premiere_point(mo, sw, sh, W, H, u, v), our_point(it, md, q, W, H, u, v)
                self.assertAlmostEqual(a[0], b[0], delta=1e-6, msg=(fmt, trk, lay, fx, u, v))
                self.assertAlmostEqual(a[1], b[1], delta=1e-6, msg=(fmt, trk, lay, fx, u, v))
            # Crop 은 보이는 상자 테두리 = 원본에서 잘린 곳 (줄이거나 돌려도 같은 곳만 보임)
            f = editor.fit_rect(it, md, q, W, H)
            l, r, t, b = mo["crop"]
            s = f["mw"] / sw
            self.assertAlmostEqual(f["bx"] + f["mx"] + (l / 100 * sw) * s, max(f["bx"], f["bx"] + f["mx"]), delta=1e-6)
            self.assertAlmostEqual(f["by"] + f["my"] + (t / 100 * sh) * s, max(f["by"], f["by"] + f["my"]), delta=1e-6)
            self.assertGreaterEqual(min(l, r, t, b), 0.0)

    def test_default_center_is_zero(self):
        it = {"id": "v", "track": "V1", "media": "main", "start": 0.0, "in": 0.0, "out": 4.0, "fit": "auto", "fx": {}}
        mo = editor.premiere_motion(it, {"w": 1920, "h": 1080, "kind": "video"}, {"format": "long", "layout": {}}, 1920, 1080)
        self.assertEqual((round(mo["scale"], 6), mo["center"], mo["crop"]), (100.0, (0.0, 0.0), [0.0, 0.0, 0.0, 0.0]))
        mo = editor.premiere_motion(dict(it, reframe=0.5), {"w": 1920, "h": 1080, "kind": "video"}, {"format": "shorts", "layout": {"mode": "fill"}}, 1080, 1920)
        self.assertAlmostEqual(mo["scale"], 1920 / 1080 * 100, places=6)
        self.assertEqual(mo["center"], (0.0, 0.0))


class Work(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="주고받기 시험 "))
        work = cls.tmp / "작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in list(dirs.values()) + [work / "edit_media", work / "projects"]:
            d.mkdir(parents=True, exist_ok=True)
        cls.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(editor, "ASSETS", work / "edit_media"),
                                                                                 mock.patch.object(editor, "PROJECTS", work / "projects")]
        for p in cls.patches:
            p.start()
        r = core.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "smptehdbars=s=640x360:r=30:d=8,drawgrid=w=40:h=40:t=2:c=white", "-f", "lavfi", "-i", "sine=f=440:d=8",
                      "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-shortest", str(dirs["VIDEOS"] / "원본.mp4")])
        assert r.returncode == 0, r.stderr[-300:]

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for f in core.OUT.glob("*"):
            shutil.rmtree(f) if f.is_dir() else f.unlink()

    def proj(self, **kw):
        items = editor._items_from_cuts([{"in": 0.5, "out": 3.0}, {"in": 4.0, "out": 7.0}])
        p = {"id": "s", "name": "쇼츠 1", "format": "shorts", "v": 2, "captionsOn": True, "captionStyle": dict(editor.SHORTS_STYLE), "titles": [], "shapes": [],
             "layout": {"mode": "fill", "cropTop": 0.08, "cropBottom": 0.04}, "master": {"volume": 1, "normalize": False, "lufs": -14},
             "duck": {"on": False, "amount": -14}, "tracks": editor.default_tracks(), "items": items,
             "trans": [{"id": "x", "track": "V1", "a": items[0]["id"], "b": items[2]["id"], "type": "dissolve", "dur": 0.4, "align": "center"}],
             "markers": [{"t": 1.0, "name": "여기"}, {"t": 9.0, "name": "밖"}],
             "captions": [{"id": "c1", "start": 1.0, "end": 2.0, "text": "자막 하나"}],
             "info": {"duration": 8.0, "width": 640, "height": 360, "fps": 30.0}, "source": "원본.mp4",
             "media": [{"id": "main", "kind": "video", "src": "videos", "file": "원본.mp4", "dur": 8.0, "w": 640, "h": 360, "fps": 30.0, "audio": True}]}
        items[0]["reframe"] = 0.25
        p.update(kw)
        return p


class XmlWriter(Work):
    def xml(self, p, **kw):
        q = editor.migrate_seq(dict(p))
        rep = {}
        x = editor._xmeml(q, 1080, 1920, 30, "시험", report=rep, **kw)
        return ET.fromstring(x.split("\n", 2)[2]), x, rep

    def test_structure(self):
        root, x, rep = self.xml(self.proj())
        self.assertTrue(x.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'))
        if shutil.which("xmllint"):
            r = subprocess.run(["xmllint", "--noout", "-"], input=x.encode(), capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        sq = root.find("sequence")
        sc = sq.find("media/video/format/samplecharacteristics")
        self.assertEqual((sc.findtext("width"), sc.findtext("height"), sc.findtext("rate/timebase"), sc.findtext("pixelaspectratio")), ("1080", "1920", "30", "square"))
        self.assertEqual(sq.findtext("duration"), "165")  # 2.5 + 3 초
        v1 = sq.findall("media/video/track")[0]
        kids = [(e.tag, e.findtext("start"), e.findtext("end"), e.findtext("in"), e.findtext("out")) for e in v1 if e.tag in ("clipitem", "transitionitem")]
        # 디졸브 0.4초 = 12프레임 · 편집점 75 가운데 · 앞 클립 끝·뒤 클립 시작은 -1 · 손잡이 6프레임만큼 out·in 이 넓어짐
        self.assertEqual(kids, [("clipitem", "0", "-1", "15", "96"), ("transitionitem", "69", "81", None, None), ("clipitem", "-1", "165", "114", "210")])
        tr = v1.find("transitionitem")
        self.assertEqual((tr.findtext("alignment"), tr.findtext("cutPointTicks"), tr.findtext("effect/name")),
                         ("center", str(round(6 / 30 * editor.PPRO_TICKS)), "Cross Dissolve"))
        # 소리: A1 = 스테레오 한 쌍 (L·R 두 트랙) · 영상·소리 연결 · 마커는 구간 안만
        au = sq.findall("media/audio/track")
        self.assertEqual(len(au), 6)
        self.assertEqual([(t.get("currentExplodedTrackIndex"), t.get("premiereTrackType")) for t in au[:2]], [("0", "Stereo"), ("1", "Stereo")])
        a0 = au[0].find("clipitem")
        self.assertEqual((a0.get("premiereChannelType"), a0.findtext("sourcetrack/trackindex"), au[1].find("clipitem").findtext("sourcetrack/trackindex")),
                         ("stereo", "1", "2"))
        links = [(ln.findtext("linkclipref"), ln.findtext("mediatype"), ln.findtext("trackindex"), ln.findtext("clipindex")) for ln in v1.find("clipitem").findall("link")]
        self.assertEqual(links, [("clipitem-1", "video", "1", "1"), ("clipitem-3", "audio", "1", "1"), ("clipitem-5", "audio", "2", "1")])
        self.assertEqual([(m.findtext("name"), m.findtext("in"), m.findtext("out")) for m in sq.findall("marker")], [("여기", "30", "-1")])
        # Basic Motion·Crop (쇼츠 화면 채우기 · 위 8% 아래 4% 자르기 · 왼쪽으로 치우침 0.25)
        bm = {p.findtext("parameterid"): p for p in v1.find("clipitem").findall("filter/effect[name='Basic Motion']/parameter")}
        self.assertEqual(set(bm), {"scale", "rotation", "center", "centerOffset"})
        mo = editor.premiere_motion(editor.migrate_seq(self.proj())["items"][0], {"w": 640, "h": 360, "kind": "video"}, self.proj(), 1080, 1920)
        self.assertAlmostEqual(float(bm["scale"].findtext("value")), mo["scale"], places=3)
        self.assertGreater(float(bm["center"].findtext("value/horiz")), 0.1)  # 원본 왼쪽을 보여 줌 → 원본 가운데는 화면 오른쪽
        crop = {p.findtext("parameterid"): float(p.findtext("value")) for p in v1.find("clipitem").findall("filter/effect[name='Crop']/parameter")}
        self.assertAlmostEqual(crop["top"], 8.0, places=3)
        self.assertAlmostEqual(crop["bottom"], 4.0, places=3)
        self.assertGreater(crop["left"], 0)
        self.assertEqual(rep["clips"], 6)

    def test_one_sided_and_range_and_overlay_and_tracks(self):
        p = self.proj()
        ids = [it["id"] for it in p["items"] if it["track"] == "V1"]
        p["trans"] = [{"id": "x", "track": "V1", "a": None, "b": ids[0], "type": "black", "dur": 0.5}, {"id": "y", "track": "V1", "a": ids[1], "b": None, "type": "wipe", "dur": 0.5}]
        p["tracks"] = [dict(t, hide=True) if t["id"] == "V2" else dict(t, mute=True) if t["id"] == "A2" else t for t in p["tracks"]]
        p["items"][1]["fadeIn"] = 0.2  # 소리 페이드 인 → start-black 0dB 섞기
        root, x, rep = self.xml(p, overlay=core.OUT / "시험_글자도형.mov")
        sq = root.find("sequence")
        v1 = sq.findall("media/video/track")[0]
        al = [(e.findtext("alignment"), e.findtext("start"), e.findtext("end")) for e in v1.findall("transitionitem")]
        self.assertEqual(al, [("start-black", "0", "15"), ("end-black", "150", "165")])
        cl = v1.findall("clipitem")
        self.assertEqual((cl[0].findtext("start"), cl[0].findtext("in"), cl[1].findtext("end"), cl[1].findtext("out")), ("-1", "15", "-1", "210"))
        self.assertEqual([k for k, _ in rep["trans_changed"]], ["wipe"])  # 한쪽만 검정 = 검정에서 디졸브 (그대로) · 닦아내기는 디졸브로
        vt = sq.findall("media/video/track")
        self.assertEqual([t.findtext("enabled") for t in vt], ["TRUE", "FALSE", "TRUE", "TRUE"])
        top = vt[-1].find("clipitem")
        self.assertEqual((top.findtext("name"), top.findtext("alphatype"), top.findtext("end"), top.findtext("file/media/video/samplecharacteristics/width")),
                         ("시험_글자도형.mov", "straight", "165", "1080"))
        au = sq.findall("media/audio/track")
        self.assertEqual([t.findtext("enabled") for t in au], ["TRUE", "TRUE", "FALSE", "FALSE", "TRUE", "TRUE"])
        fade = au[0].find("transitionitem")
        self.assertEqual((fade.findtext("alignment"), fade.findtext("start"), fade.findtext("end"), fade.findtext("effect/effectid")),
                         ("start-black", "0", "6", "KGAudioTransCrossFade0dB"))
        # 구간: 전환이 구간 끝에 걸리면 빼고 알림 · 마커·클립은 구간 시작 기준
        root, x, rep = self.xml(self.proj(), t_lo=2.4, t_hi=5.0)
        sq = root.find("sequence")
        self.assertEqual(sq.findtext("duration"), "78")
        self.assertEqual(len(rep.get("trans_cut") or []), 1)
        self.assertEqual(sq.findall("marker"), [])
        v1 = sq.findall("media/video/track")[0]
        self.assertEqual([(c.findtext("start"), c.findtext("end"), c.findtext("in")) for c in v1.findall("clipitem")], [("0", "3", "87"), ("3", "78", "120")])

    def test_otio_round_trip(self):
        try:
            import opentimelineio as otio  # noqa: F401
        except ImportError:
            self.skipTest("opentimelineio 가 없음 (pip install opentimelineio otio-fcp-adapter)")
        _, x, _ = self.xml(self.proj())
        f = core.OUT / "rt.xml"
        f.write_text(x, encoding="utf-8")
        tl = otio.adapters.read_from_file(str(f), adapter_name="fcp_xml")
        v1 = tl.tracks[0]
        clips = [(v1.range_of_child(c).start_time.value, v1.range_of_child(c).duration.value, c.source_range.start_time.value) for c in v1 if isinstance(c, otio.schema.Clip)]
        self.assertEqual(clips, [(0, 75, 15), (75, 90, 120)])
        self.assertEqual([m.marked_range.start_time.value for m in tl.tracks.markers], [30])

    def test_report(self):
        p = self.proj(titles=[{"id": "t", "text": "제목", "start": 0, "dur": 2, "style": dict(editor.TITLE_STYLE)}], layout={"mode": "blur"})
        p["items"][0]["color"] = {"exp": 1}
        p["items"][2]["fx"] = {"scale": {"v": 100, "k": [{"t": 4.0, "v": 100}, {"t": 6.0, "v": 130}]}}
        q = editor.migrate_seq(dict(p))
        rep = {"W": 1080, "H": 1920}
        editor._xmeml(q, 1080, 1920, 30, "시험", report=rep)
        txt = editor._xml_report(q, rep, "시험", 30, 0.0, 5.5, srt=["시험.srt"])
        for want in ("자막 1개", "타이틀 1개", "'글자·도형만 투명 영상'을 같이 켜면", "'시험.srt'", "흐린 배경", "색 보정 1개", "움직임(키프레임) 1개"):
            self.assertIn(want, txt)
        txt = editor._xml_report(q, rep, "시험", 30, 0.0, 5.5, overlay=core.OUT / "시험_글자도형.mov")
        self.assertIn("'시험_글자도형.mov'(투명 배경 영상)", txt)


def rgba_frame(path, n):
    from PIL import Image
    out = core.OUT / f"_f{n}.png"
    subprocess.run([FF, "-v", "error", "-y", "-i", str(path), "-vf", f"select=eq(n\\,{n}),format=rgba", "-frames:v", "1", str(out)], check=True)
    im = Image.open(out).copy()
    out.unlink()
    return im


class OverlayAndFrame(Work):
    def overlay_proj(self):
        return self.proj(format="long", captions=[], captionsOn=False, trans=[],
                         shapes=[{"id": "sa", "name": "사각형", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.2, "color": "#FF0000", "opacity": 0.5, "radius": 0,
                                  "full": False, "start": 0.2, "dur": 1.0}])

    def test_overlay_alpha_and_timing(self):
        p = self.overlay_proj()
        out = editor.export("원본.mp4", p, {"video": False, "srt": False, "xml": True, "overlay": True, "range": [0.0, 0.5]}, lambda *a: None)
        mov = next(core.OUT / f for f in out if f.endswith("_글자도형.mov"))
        info = core.run([FF, "-hide_banner", "-i", str(mov)]).stderr
        self.assertIn("prores (4444)", info)
        self.assertIn("1920x1080", info)
        a5, a6 = rgba_frame(mov, 5), rgba_frame(mov, 6)  # 도형 시작 0.2초 = 프레임 6
        self.assertIsNone(a5.split()[3].getbbox())
        px = a6.getpixel((int(0.25 * 1920), int(0.2 * 1080)))
        self.assertAlmostEqual(px[3], 128, delta=4)  # 불투명도 0.5 그대로 (subtitles 필터 alpha 는 0.25 로 흐려짐)
        self.assertGreater(px[0], 240)  # 곱하기 전 색 (straight) = 빨강
        self.assertLess(px[1] + px[2], 20)
        self.assertEqual(a6.getpixel((1800, 1000))[3], 0)
        xml = (core.OUT / next(f for f in out if f.endswith("_premiere.xml"))).read_text(encoding="utf-8")
        self.assertIn(mov.name, xml)

    def test_png_fallback(self):
        p = self.overlay_proj()
        logs = []
        with mock.patch.object(editor, "OVERLAY_CODECS", ("없는_인코더",)):
            out = editor.export("원본.mp4", p, {"video": False, "srt": False, "xml": True, "overlay": True, "range": [0.0, 0.4]}, logs.append)
        z = next((core.OUT / f for f in out if f.endswith("_png.zip")), None)
        self.assertIsNotNone(z, out)
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            self.assertEqual(len(names), 12)
            self.assertEqual(names[0], "글자도형_000000.png")
        txt = (core.OUT / next(f for f in out if f.endswith(".txt"))).read_text(encoding="utf-8-sig")
        self.assertIn("PNG 연속 그림", txt)
        self.assertNotIn("_png.zip", (core.OUT / next(f for f in out if f.endswith(".xml"))).read_text(encoding="utf-8"))  # 묶음은 트랙에 못 걸음

    def test_export_frame_matches_premiere_reading(self):
        """지금 화면 그림(내보내기와 같은 그리기) = XML 의 Crop·Basic Motion 으로 원본을 놓은 그림 (프리미어가 읽는 대로 ffmpeg 로 그려 비교)."""
        from PIL import Image
        import numpy as np
        # 위아래 자르기는 빼고 (내보내기가 원본 1px 단위로 내려 자름 · 6배 확대면 6px 남짓 — 미리보기·XML 은 소수 그대로 · I-344) 치우침·확대·이동만
        p = self.proj(captions=[], captionsOn=False, trans=[], markers=[], layout={"mode": "fill"})
        p["items"][0]["fx"] = {"scale": {"v": 115, "k": []}, "pos": {"v": [0.55, 0.48], "k": []}}
        editor.CANCEL.set()  # 멈추기 표시가 남아 있어도 지금 화면 저장은 됨
        try:
            r = editor.export_frame("원본.mp4", p, 1.0)
        finally:
            editor.CANCEL.clear()
        self.assertEqual((r["w"], r["h"], r["file"]), (1080, 1920, "원본_쇼츠 1_프레임 00-00-01-00.png"))
        ours = Image.open(core.OUT / r["file"]).convert("RGB")
        self.assertEqual(editor.export_frame("원본.mp4", p, 1.0)["file"], "원본_쇼츠 1_프레임 00-00-01-00 (2).png")
        q = editor.migrate_seq(dict(p))
        mo = editor.premiere_motion(q["items"][0], {"w": 640, "h": 360, "kind": "video"}, q, 1080, 1920)
        l, rr, t, b = (x / 100 for x in mo["crop"])
        s = mo["scale"] / 100
        cw, ch = 640 * (1 - l - rr), 360 * (1 - t - b)
        x0 = 540 + mo["center"][0] * 1080 - 320 * s + 640 * l * s
        y0 = 960 + mo["center"][1] * 1920 - 180 * s + 360 * t * s
        ref = core.OUT / "prem.png"
        src_t = 0.5 + 1.0  # 첫 클립 원본 0.5초부터
        # 원본 전체를 확대한 뒤(소수 자리 자르기가 짝수로 내려가지 않게 RGB 에서) Crop 영역만 잘라 그 왼쪽 위를 놓음
        vf = (f"[0:v]setpts=PTS-STARTPTS,format=rgb24,scale={round(640 * s)}:{round(360 * s)}:flags=bicubic,"
              f"crop={round(cw * s)}:{round(ch * s)}:{round(640 * l * s)}:{round(360 * t * s)}:exact=1[c];"
              f"color=c=black:s=1080x1920:r=30,format=rgb24[k];[k][c]overlay={round(x0)}:{round(y0)}:shortest=1,format=rgb24")
        subprocess.run([FF, "-v", "error", "-y", "-ss", f"{src_t - 1 / 60:.4f}", "-i", str(core.VIDEOS / "원본.mp4"), "-filter_complex", vf, "-frames:v", "1", str(ref)], check=True)
        # 가장 잘 맞는 자리가 제자리 ±2px (내보내기의 짝수 크기 반올림) — 가운데 값을 반으로 읽는 식의 잘못(수백 px)·자르기 빠짐은 여기서 걸림
        a, bb = np.asarray(ours.convert("L")).astype(float), np.asarray(Image.open(ref).convert("L")).astype(float)
        y = a[60:1860, 60:1020]
        best = min((((bb[60 + dy:1860 + dy, 60 + dx:1020 + dx] - y) ** 2).mean(), dx, dy) for dy in range(-6, 7) for dx in range(-6, 7))
        print("  프리미어처럼 놓은 그림과 가장 잘 맞는 자리 (MSE, dx, dy):", tuple(round(float(x), 2) for x in best))
        self.assertLessEqual(max(abs(best[1]), abs(best[2])), 2, best)
        self.assertGreater(10 * math.log10(255 ** 2 / max(best[0], 1e-9)), 24, best)

    def test_side_files_and_names(self):
        p = self.proj()
        out = editor.export("원본.mp4", p, {"video": False, "srt": True, "xml": True}, lambda *a: None)
        self.assertEqual(out, ["원본_쇼츠 1.srt", "원본_쇼츠 1_premiere.xml", "원본_쇼츠 1_프리미어_빠진 것.txt"])
        self.assertTrue((core.OUT / out[2]).read_bytes().startswith(b"\xef\xbb\xbf"))
        out = editor.export("원본.mp4", p, {"video": False, "srt": True, "xml": True}, lambda *a: None)
        self.assertEqual(out[0], "원본_쇼츠 1 (2).srt")


class Routes(unittest.TestCase):
    def test_srt_route(self):
        import threading
        import urllib.request
        import urllib.error
        import base64
        from http.server import ThreadingHTTPServer
        import app
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with mock.patch.object(app, "PORT", port):
                def post(body):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/edit/srt", data=json.dumps(body).encode(),
                                                 headers={"Content-Type": "application/json"}, method="POST")
                    try:
                        with urllib.request.urlopen(req, timeout=30) as r:
                            return r.status, json.loads(r.read())
                    except urllib.error.HTTPError as e:
                        return e.code, json.loads(e.read())
                code, j = post({"name": "x.mp4", "data": base64.b64encode(srt_bytes(SAMPLE, "cp949")).decode(), "fps": 30})
                self.assertEqual((code, j["ok"], j["encoding"], len(j["cues"])), (200, True, "cp949", 4))
                self.assertEqual(post({"name": "x.mp4", "data": "!!!"})[0], 400)
                code, j = post({"name": "x.mp4", "data": base64.b64encode(b"nothing").decode()})
                self.assertEqual((code, j["ok"]), (400, False))
                self.assertEqual(post({"name": "../밖.mp4", "data": ""})[0], 400)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
