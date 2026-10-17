"""은평 스타일 쇼츠 (BR-240~BR-243 · D-420~D-423) — 저장소 폴더에서 python3 -m unittest tests.test_eunpyeong

- 설정: 스타일 화면 '쇼츠 화면 배치'(styles/설정/쇼츠 화면 배치.json · 기본 지금처럼 · 모르는 값·깨진 파일은 지금처럼) · 진짜 app.Handler GET/POST
- 가편집 쇼츠: 파란 띠 box 배치(영상 창 0.28~0.70) · 흰 상자(도형 plan card) 안 한 줄 검은 큰 제목(처음부터 끝까지) · 그 아래 흰 사실 줄(끝까지 ·
  바꿔 끼우기 없음) · 말 자막은 아래 띠 · 설정을 끄면 예전과 똑같음
- MSG 쇼츠: 모든 기본 스타일 × 보통·듬뿍 — 큰 제목·사실 줄·흰 상자가 편집본 끝까지 · 다른 화면 글자(상황 자막·다시 보기 ▶·점수판·강조)는
  제목 상자·사실 줄·말 자막 띠와 안 겹침 · 전술 그림 배치도 같은 화면 구성
- 실제 내보내기(720×1280): 띠 파랑 · 흰 상자 자리 · 제목 글자는 어둡게 상자 가운데 · 영상 창 자리 · 사실 줄 · 말 자막은 아래 띠
- 편집실(editor.html 함수를 node 로 그대로): 템플릿 '은평 스타일' = 서버와 같은 모양·자리 · 다른 템플릿을 고르면 흰 상자를 지우고 큰 제목을 보통으로."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import msg  # noqa: E402
import style  # noqa: E402
from test_shorts_topic import FIX, WorkMixin, run_js, video_band  # noqa: E402

W, H = editor.frame_size("shorts")
FF = shutil.which("ffmpeg")
BLUE = (0x1F, 0x4F, 0xD1)


def ebox(t):
    """편집실·내보내기 글 상자 (화면 비율 왼, 위, 오른, 아래)."""
    return editor.text_box(t["text"], t["style"], W, H)


def inter(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > 0 and min(a[3], b[3]) - max(a[1], b[1]) > 0


class SettingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="은평 설정 "))
        self.s = mock.patch.object(style, "STYLES", self.tmp / "styles")
        self.s.start()

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_set_and_bad_values(self):
        self.assertEqual(style.shorts_layout(), "")
        self.assertFalse(editor.shorts_card_on())
        self.assertEqual(style.set_shorts_layout("eunpyeong"), "eunpyeong")
        self.assertEqual(style.shorts_layout(), "eunpyeong")
        self.assertTrue(editor.shorts_card_on())
        with self.assertRaises(ValueError):
            style.set_shorts_layout("blue")
        self.assertEqual(style.shorts_layout(), "eunpyeong")
        self.assertEqual(style.set_shorts_layout(""), "")
        self.assertFalse(editor.shorts_card_on())
        f = style._shorts_layout_file()
        for bad in ("{", '{"layout": "kangwon"}', '["eunpyeong"]'):
            f.write_text(bad, encoding="utf-8")
            self.assertEqual(style.shorts_layout(), "", bad)


class EndpointTest(unittest.TestCase):
    def test_get_follows_set(self):
        from test_windows_compat import Server

        class T(Server):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            self.assertEqual(t.call("/api/style/shorts_layout"), (200, {"layout": ""}))
            self.assertEqual(t.call("/api/style/shorts_layout", {"layout": "eunpyeong"}), (200, {"ok": True, "layout": "eunpyeong"}))
            self.assertEqual(t.call("/api/style/shorts_layout"), (200, {"layout": "eunpyeong"}))
            self.assertEqual(t.call("/api/style/list")[1]["shortsLayout"], "eunpyeong")
            self.assertEqual(t.call("/api/style/shorts_layout", {"layout": "x"})[0], 400)
            self.assertEqual(t.call("/api/style/shorts_layout"), (200, {"layout": "eunpyeong"}))
            self.assertEqual(t.call("/api/style/shorts_layout", {"layout": ""}), (200, {"ok": True, "layout": ""}))
        finally:
            t.doCleanups()

    def test_remote_route_table_has_it(self):
        import rstudio
        txt = Path(rstudio.__file__).read_text(encoding="utf-8")
        self.assertEqual(txt.count('"/api/style/shorts_layout"'), 2)   # 읽기(GET) · 바꾸기(POST)


class TitleStyleTest(unittest.TestCase):
    def test_fits_box_one_line_centered(self):
        room = editor.EP_BOX["w"] * W - 2 * editor.EP_PAD
        for text, size in (("트래핑 꿀팁", 72), ("퍼스트 터치 첫 번째 포인트", 72), ("가나다라마바사아자차카타파하거너", 60),
                           ("패스하고 그 자리에 서 있으면 왜 안될까요?", 48)):
            st = editor.ep_title_style(text)
            self.assertEqual(st["size"], size, text)
            self.assertIn(st["size"], editor.EP_SIZES)
            if size > editor.EP_SIZES[-1]:
                self.assertLessEqual(editor._text_w(text, st), room, text)
            b = ebox({"text": text, "style": st})
            mid = editor.EP_BOX["y"] + editor.EP_BOX["h"] / 2
            self.assertAlmostEqual((b[1] + b[3]) / 2, mid, places=3, msg=text)
            self.assertEqual((st["fill"], st["strokeW"], st["bgOn"], st["effect"], st["weight"]), ("#111111", 0, False, "none", "Black"))

    def test_window_is_the_reference_size(self):
        top, bot = video_band(editor.EP_LAYOUT)
        self.assertAlmostEqual(top, 0.28, delta=0.005)
        self.assertAlmostEqual(bot, 0.697, delta=0.005)
        self.assertAlmostEqual(bot - top, 0.417, delta=0.01)   # 화면 높이 약 41~42%
        self.assertLess(editor.EP_BOX["y"] + editor.EP_BOX["h"], editor.EP_FACT["y"] - editor.EP_FACT["size"] * editor.TXT_LINE / H)
        self.assertLess(editor.EP_FACT["y"], top)
        self.assertGreaterEqual(editor.EP_BOX["y"], editor.TOPIC_TOP)


class RoughCardTest(WorkMixin, unittest.TestCase):
    INFO = {"duration": FIX["MSGRAW01"]["duration"], "width": 1920, "height": 1080, "fps": 30.0}

    def strip(self, qs):
        s = json.dumps(qs, ensure_ascii=False, sort_keys=True)
        ids = {x for q in qs for k in ("titles", "shapes", "items") for x in [o.get("id") for o in q.get(k) or []] + [o.get("link") for o in q.get(k) or []]
               if x} | {q["id"] for q in qs} | {t.get("under") for q in qs for t in q["titles"] if t.get("under")}
        for i in ids:
            s = s.replace(f'"{i}"', '"_"')
        return s

    def test_off_is_exactly_as_before(self):
        name = self.fix("MSGRAW01")
        before = self.strip(editor.auto_sequences(name, self.INFO, None, ("shorts",)))
        style.set_shorts_layout("")
        self.assertEqual(self.strip(editor.auto_sequences(name, self.INFO, None, ("shorts",))), before)
        for q in json.loads(before):
            self.assertEqual(q["layout"]["mode"], "box")
            self.assertEqual(q["layout"]["bar"], "#000000")
            self.assertEqual(q["shapes"], [])

    def test_card_rough_shorts(self):
        name = self.fix("MSGRAW01")
        style.set_shorts_layout("eunpyeong")
        n_fact = 0
        for st in (None, {"captionPos": "top"}, {"captionPos": "middle"}, {"captionPos": "bottom"}, {"captionColor": "#1A1A1A"}, {"captionColor": "#FFD400"}):
            for q in editor.auto_sequences(name, self.INFO, st, ("shorts",)):
                L = editor.seq_total(q)
                # 파란 띠 위 말 자막: 어두운 색(배운 스타일)은 흰 글씨 · 밝은 색은 그대로
                self.assertEqual(q["captionStyle"]["fill"], "#FFD400" if st and st.get("captionColor") == "#FFD400" else "#FFFFFF")
                self.assertEqual(q["layout"], editor.EP_LAYOUT)
                self.assertGreaterEqual(q["captionStyle"]["y"], editor.EP_CAP_Y)
                if st and st.get("captionPos") == "bottom":
                    self.assertEqual(q["captionStyle"]["y"], editor.CAP_Y["bottom"])
                top = q["titles"][0]
                self.assertNotIn("\n", top["text"])
                self.assertEqual((top["start"], top["dur"]), (0.0, round(L, 2)))
                self.assertEqual(top["style"], editor.ep_title_style(top["text"]))
                [box] = q["shapes"]
                self.assertEqual((box["plan"], box["full"], box["color"], box["opacity"], box["radius"]), ("card", True, "#FFFFFF", 1.0, 0))
                bb = (box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"])
                tb = ebox(top)
                self.assertTrue(bb[0] < tb[0] and tb[2] < bb[2] and bb[1] < tb[1] and tb[3] < bb[3], (tb, bb))
                cb = editor._cap_band(q["captionStyle"], H, 2)
                vb = video_band(q["layout"])
                self.assertGreaterEqual(cb[0], vb[1], "말 자막(두 줄)은 아래 띠")
                facts = [t for t in q["titles"] if t.get("plan") == "topic"]
                for kt in facts:
                    n_fact += 1
                    self.assertNotIn("swap", kt)
                    self.assertEqual((kt["start"], kt["dur"], kt["under"]), (0.0, round(L, 2), top["id"]))
                    self.assertEqual(kt["style"], editor.EP_FACT)
                    kb = ebox(kt)
                    self.assertGreater(kb[1], bb[3])
                    self.assertLess(kb[3], vb[0])
                    self.assertTrue(editor._one_line(kt["text"], kt["style"], W))
        self.assertGreater(n_fact, 0)

    def test_topic_setting_off_has_no_fact_line(self):
        name = self.fix("MSGRAW01")
        style.set_shorts_layout("eunpyeong")
        style.set_shorts_topic(False)
        for q in editor.auto_sequences(name, self.INFO, None, ("shorts",)):
            self.assertFalse([t for t in q["titles"] if t.get("plan") == "topic"])
            self.assertEqual(len(q["shapes"]), 1)


class MsgCardTest(unittest.TestCase):
    """시험 원본(make_msg_fixture)으로 MSG 쇼츠를 기본 스타일 × 보통·듬뿍 으로 (은평 스타일 켬)."""

    @classmethod
    def setUpClass(cls):
        from make_msg_fixture import MsgWork
        cls.w = MsgWork()
        style.set_shorts_layout("eunpyeong")
        cls.qs = []
        specs = [{"kind": "preset", "name": n} for n in msg.PRESETS]
        for lv in ("보통", "듬뿍"):
            for grp in (specs[:3], specs[3:]):
                cls.qs += msg.build_variants(cls.w.name, grp, lv, ("shorts",), lambda m: None)["sequences"]

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_card_frame_and_titles(self):
        self.assertGreaterEqual(len(self.qs), 6)
        kinds = set()
        for q in self.qs:
            L = round(editor.seq_total(q), 3)
            self.assertEqual(q["layout"], editor.EP_LAYOUT, q["name"])
            self.assertGreaterEqual(q["captionStyle"]["y"], editor.EP_CAP_Y)
            ev = next(e for e in q["msg"]["events"] if e["kind"] == "hook")
            [box] = [g for g in q["shapes"] if g.get("plan") == "card"]
            self.assertEqual(ev["refs"]["shapes"], [box["id"]])
            self.assertEqual((box["start"], box["dur"], box["full"]), (0.0, L, True))
            hk = next(t for t in q["titles"] if t["id"] == ev["refs"]["titles"][0])
            self.assertEqual((hk["start"], hk["dur"]), (0.0, L), q["name"])
            self.assertEqual(hk["style"], editor.ep_title_style(hk["text"]))
            fixed = [box["x"] * W, box["y"] * H, (box["x"] + box["w"]) * W, (box["y"] + box["h"]) * H]
            obst = [fixed]
            for tid in ev["refs"]["titles"][1:]:
                kt = next(t for t in q["titles"] if t["id"] == tid)
                self.assertEqual((kt["plan"], kt["start"], kt["dur"], kt["style"]), ("topic", 0.0, L, editor.EP_FACT))
                obst.append(msg.text_box(kt["text"], kt["style"], W, H))
            capb = msg.text_box("가나다\n라마바", q["captionStyle"], W, H)   # 두 줄 말 자막
            vtop = video_band(q["layout"])[0] * H
            self.assertGreaterEqual(capb[1], video_band(q["layout"])[1] * H - 2)   # 두 줄 말 자막도 영상 창 아래 (글꼴 줄 높이 어림 ±2화소)
            for t in q["titles"]:
                if t["id"] in ev["refs"]["titles"]:
                    continue
                kind = next((e["kind"] for e in q["msg"]["events"] if t["id"] in (e.get("refs") or {}).get("titles", [])), "")
                kinds.add(kind)
                b = msg.text_box(t["text"], t["style"], W, H)
                for o in obst:
                    self.assertFalse(inter(b, o), (q["name"], kind, t["text"], b, o))
                if kind in ("situ", "replay", "score"):
                    self.assertGreater(b[1], obst[-1][3], (q["name"], t["text"]))   # 사실 줄·상자 아래
                    self.assertLessEqual(b[3], vtop + 1) if t["style"]["y"] == msg.EP_ROW else None
        self.assertIn("situ", kinds)

    def test_tactics_follow_card_window(self):
        for q in self.qs:
            for g in q.get("tactics") or []:
                self.assertTrue(g.get("pts") or g.get("follow"), q["name"])
        seq = {"format": "shorts", "layout": msg.seq_layout("shorts", True)}
        self.assertEqual(seq["layout"], editor.EP_LAYOUT)
        self.assertEqual(msg.seq_layout("shorts")["mode"], "blur")   # 끄면 예전 그대로

    def test_replay_label_row(self):
        B = msg._Build("x.mp4", {"duration": 10}, "shorts")
        B.card = True
        lk = msg.text_looks(msg.PRESETS[next(iter(msg.PRESETS))], "shorts", 0.8, card=True)
        self.assertEqual(lk["situ"]["y"], msg.EP_ROW)
        self.assertEqual(lk["score"]["y"], 0.27)
        old = msg.text_looks(msg.PRESETS[next(iter(msg.PRESETS))], "shorts", 0.8)
        self.assertEqual(old["situ"]["y"], 0.18)


@unittest.skipUnless(FF, "ffmpeg 가 없어요")
class ExportPixelTest(WorkMixin, unittest.TestCase):
    """실제 내보내기 (가벼운 720p · 720×1280) 프레임 화소."""

    def test_render(self):
        import numpy as np
        name = self.fix("MSGRAW01")
        d = 4.0
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s=640x360:r=30:d={d}", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                            "-t", str(d), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(core.VIDEOS / name)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        info = {"duration": d, "width": 640, "height": 360, "fps": 30.0}
        items = editor._items_from_cuts([{"in": 0.0, "out": d}])
        top = {"id": "t", "text": "트래핑 꿀팁", "start": 0.0, "dur": d, "style": dict(editor.TITLE_STYLE)}
        kt = {"id": "k", "text": "퍼스트 터치 레슨 · 포인트 3", "plan": "topic"}
        shapes = editor.ep_card(top, kt, d)
        q = editor._new_seq("쇼츠 1", "shorts", items, captionStyle=editor.ep_caption(dict(editor.SHORTS_STYLE)), layout=dict(editor.EP_LAYOUT),
                            titles=[top, kt], shapes=shapes)
        caps = [{"id": "c1", "start": 0.0, "end": d, "text": "공을 멈추는 게 아니라"}]
        proj = dict(q, captions=caps, info=info, source=name, media=[], master={"volume": 1, "normalize": False, "lufs": -14})
        out = editor.export(name, proj, {"preset": "small", "hw": False, "xml": False, "srt": False}, lambda m: None)
        raw = subprocess.run([FF, "-v", "error", "-ss", "1.0", "-i", str(core.OUT / out[0]), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        a = np.frombuffer(raw, np.uint8).reshape(1280, 720, 3).astype(int)
        Hh, Ww = 1280, 720

        def near(p, c, tol=24):
            return all(abs(int(p[i]) - c[i]) <= tol for i in range(3))
        for y in (0.03, 0.2, 0.25, 0.72, 0.9, 0.97):   # 띠
            self.assertTrue(near(a[int(y * Hh), 10], BLUE), (y, a[int(y * Hh), 10]))
        for x in (0.07, 0.5, 0.92):                     # 흰 상자 (글자 사이 빈 곳 · 양 끝)
            self.assertTrue(near(a[int(0.1 * Hh), int(x * Ww)], (255, 255, 255)), x)
            self.assertTrue(near(a[int(0.15 * Hh), int(x * Ww)], (255, 255, 255)), x)
        self.assertTrue(near(a[int(0.09 * Hh), int(0.5 * Ww)], BLUE))   # 상자 위 끝 바깥
        self.assertTrue(near(a[int(0.16 * Hh), int(0.03 * Ww)], BLUE))  # 상자 왼쪽 바깥
        box = a[int(0.097 * Hh):int(0.153 * Hh), int(0.06 * Ww):int(0.94 * Ww)]
        rows = np.where((box.sum(axis=2) < 150).any(axis=1))[0] + int(0.097 * Hh)
        self.assertGreater(len(rows), 10, "제목 글자가 어두움")
        self.assertAlmostEqual((rows.min() + rows.max()) / 2 / Hh, editor.EP_BOX["y"] + editor.EP_BOX["h"] / 2, delta=0.006)
        col = a[:, int(0.02 * Ww)]   # 흰 상자·글자 바깥 세로줄
        gray = np.where((abs(col - 128) < 20).all(axis=1))[0]
        self.assertAlmostEqual(gray.min() / Hh, 0.28, delta=0.006)        # 영상 창
        self.assertAlmostEqual(gray.max() / Hh, 0.697, delta=0.006)
        fact = a[int(0.16 * Hh):int(0.21 * Hh)]
        frows = np.where((fact.min(axis=2) > 225).any(axis=1))[0] + int(0.16 * Hh)
        self.assertGreater(len(frows), 5, "사실 줄 흰 글씨")
        self.assertLess(frows.max() / Hh, 0.2)
        low = a[int(0.70 * Hh):]
        crows = np.where((low.min(axis=2) > 225).sum(axis=1) > 3)[0] + int(0.70 * Hh)
        self.assertGreater(len(crows), 5, "말 자막 흰 글씨")
        self.assertGreater(crows.min() / Hh, 0.70)
        self.assertLess(crows.max() / Hh, 0.81)


@unittest.skipUnless(shutil.which("node"), "node 가 없어요")
class EditorTemplateTest(unittest.TestCase):
    """editor.html 템플릿 '은평 스타일'·epTitleStyle 를 node 로 그대로 실행 → 서버(editor.ep_title_style·ep_card)와 같은 값."""
    SHORT = """
const pair = (a, b, start) => [{ id: nid(), track: "V1", media: "main", in: a, out: b, start }, { id: nid(), track: "A1", media: "main", in: a, out: b, start }];
const BIG = { weight: "Black", size: 100, fill: "#FFE14D", stroke: "#111111", strokeW: 9, y: 0.22, x: 0.5, align: "center", effect: "pop" };
const rough = swap => ({ format: "shorts", name: "쇼츠 1", layout: { mode: "box", bar: "#000000", zoom: 1, vpos: 0.5, cropTop: 0, cropBottom: 0 },
  captionStyle: { y: 0.3, size: 72, strokeW: 8, fill: "#FFFFFF", stroke: "#000000", weight: "Black" }, items: [...pair(10, 20, 0), ...pair(30, 50, 10)],
  shapes: [], tactics: [], markers: [], trans: [],
  titles: [{ id: "big", text: swap ? "패스\\n두 번째 동작" : "트래핑 꿀팁", start: swap ? 2.5 : 0, dur: swap ? 27.5 : 30, style: { ...BIG } },
           { id: "k", text: "퍼스트 터치 레슨 · 포인트 3", start: 0, dur: 2.5, plan: "topic", under: "big", swap: swap || undefined,
             style: swap ? { ...BIG } : { ...TOPIC_STYLE, y: 0.133 } }] });
"""

    def test_template_matches_server(self):
        texts = ["트래핑 꿀팁", "퍼스트 터치 첫 번째 포인트", "가나다라마바사아자차카타파하거너", "패스하고 그 자리에 서 있으면 왜 안될까요?"]
        got = run_js(self.SHORT + f"""
out = {{ sizes: {json.dumps(texts, ensure_ascii=False)}.map(t => epTitleStyle(t)), runs: [] }};
for (const swap of [false, true]) {{
  S = rough(swap); TEMPLATES["은평 스타일"]();
  const cp = x => JSON.parse(JSON.stringify(x));
  out.runs.push(cp({{ layout: S.layout, cap: S.captionStyle.y, titles: S.titles, shapes: S.shapes }}));
  TEMPLATES["은평 스타일"]();   // 두 번 눌러도 상자 하나
  out.runs.push({{ n: S.shapes.length }});
  TEMPLATES["위아래 검정 박스"]();
  out.runs.push(cp({{ shapes: S.shapes, big: S.titles.find(t => t.id === "big").style, layout: S.layout, k: S.titles.find(t => t.id === "k") }}));
}}""")
        for t, st in zip(texts, got["sizes"]):
            self.assertEqual(st, {k: v for k, v in editor.ep_title_style(t).items() if k in st}, t)
            self.assertEqual(st["size"], editor.ep_title_style(t)["size"])
            self.assertEqual(st["y"], editor.ep_title_style(t)["y"])
        for k in (0, 3):
            r = got["runs"][k]
            for key, v in editor.EP_LAYOUT.items():
                self.assertEqual(r["layout"][key], v, key)
            self.assertEqual(r["cap"], editor.EP_CAP_Y)
            big = next(t for t in r["titles"] if t["id"] == "big")
            kt = next(t for t in r["titles"] if t["id"] == "k")
            self.assertNotIn("\n", big["text"])
            self.assertEqual((big["start"], big["dur"]), (0, 30))
            ref = editor.ep_title_style(big["text"])
            for key in ("size", "y", "fill", "strokeW", "bgOn", "effect", "weight", "x", "align"):
                self.assertEqual(big["style"][key], ref[key], key)
            self.assertEqual((kt["start"], kt["dur"], kt.get("swap")), (0, 30, None))
            for key, v in editor.EP_FACT.items():
                if key in ("size", "y", "fill", "strokeW", "bgOn", "effect", "weight", "x", "align"):
                    self.assertEqual(kt["style"][key], v, key)
            [box] = r["shapes"]
            py = editor.ep_box(30)
            for key in ("x", "y", "w", "h", "color", "opacity", "radius", "start", "dur", "full", "plan"):
                self.assertEqual(box[key], py[key], key)
            self.assertEqual(got["runs"][k + 1]["n"], 1)
            after = got["runs"][k + 2]
            self.assertEqual(after["shapes"], [])
            self.assertEqual((after["big"]["size"], after["big"]["effect"], after["big"]["fill"]), (100, "pop", "#FFE14D"))
            self.assertEqual(after["layout"]["bar"], "#000000")
            self.assertEqual((after["k"]["dur"], after["k"]["style"]["size"], after["k"]["style"]["weight"]), (editor.TOPIC_SEC, 72, "Black"))   # 다시 첫머리 주제 줄
            kb, tb = editor.text_box(after["k"]["text"], after["k"]["style"], W, H), editor.text_box("트래핑 꿀팁", after["big"], W, H)
            self.assertFalse(inter(kb, tb))


if __name__ == "__main__":
    unittest.main()
