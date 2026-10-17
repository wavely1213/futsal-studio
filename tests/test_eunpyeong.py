"""은평 스타일 쇼츠 (BR-240~BR-243 · D-420~D-423) — 저장소 폴더에서 python3 -m unittest tests.test_eunpyeong

- 설정: 스타일 화면 '쇼츠 화면 배치'(styles/설정/쇼츠 화면 배치.json · 기본 지금처럼 · 모르는 값·깨진 파일은 지금처럼) · 진짜 app.Handler GET/POST
- 가편집 쇼츠: 파란 띠 box 배치(영상 창 0.28~0.70) · 흰 상자(도형 plan card) 안 한 줄 검은 큰 제목(처음부터 끝까지) · 그 아래 흰 사실 줄(끝까지 ·
  바꿔 끼우기 없음) · 말 자막은 아래 띠 · 설정을 끄면 예전과 똑같음
- MSG 쇼츠: 모든 기본 스타일 × 보통·듬뿍 — 큰 제목·사실 줄·흰 상자가 편집본 끝까지 · 다른 화면 글자(상황 자막·다시 보기 ▶·점수판·강조)는
  제목 상자·사실 줄·말 자막 띠와 안 겹침 · 전술 그림 배치도 같은 화면 구성
- 실제 내보내기(720×1280): 띠 파랑 · 흰 상자 자리 · 제목 글자는 어둡게 상자 가운데 · 영상 창 자리 · 사실 줄 · 말 자막은 아래 띠
- 편집실(editor.html 함수를 node 로 그대로): 템플릿 '은평 스타일' = 서버와 같은 모양·자리 · 다른 템플릿을 고르면 흰 상자를 지우고 큰 제목을 보통으로.
- 덧붙임(BR-245 · BR-246): 설정 · 시리즈 번호 셈(정하기·다시 쓰기·사용자 번호·겹치지 않음·채널마다) · 가편집·MSG 쇼츠의 이름표·전체 강좌 줄 자리 ·
  실제 내보내기(번호를 정해 이름표에 · 다시 내보내도 같은 번호) · 편집실 템플릿 · 길(/api/style/card_extras · 내보내기 응답) · 올리기 키트 제목 회차·설명 링크."""
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
from test_shorts_topic import FIX, JS_DEFS, WorkMixin, js_defs, run_js, video_band  # noqa: E402

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
        for bad in ("{", '{"layout": "orange"}', '["eunpyeong"]'):
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
            self.assertEqual(big["style"], editor.ep_title_style(big["text"]))   # 모양 통째로 서버와 같음 (D-426)
            self.assertEqual((kt["start"], kt["dur"], kt.get("swap")), (0, 30, None))
            self.assertEqual(kt["style"], editor.EP_FACT)
            [box] = r["shapes"]
            py = editor.ep_box(30)
            for key in ("x", "y", "w", "h", "color", "opacity", "radius", "start", "dur", "full", "plan"):
                self.assertEqual(box[key], py[key], key)
            self.assertEqual(got["runs"][k + 1]["n"], 1)
            after = got["runs"][k + 2]
            self.assertEqual(after["shapes"], [])
            self.assertEqual((after["big"]["size"], after["big"]["effect"], after["big"]["fill"]), (100, "pop", "#FFE14D"))
            self.assertEqual(after["layout"]["bar"], "#000000")
            self.assertNotIn("win", after["layout"])   # 영상 창 밖 자르기도 은평 스타일에서만 (D-424)
            self.assertEqual((after["k"]["dur"], after["k"]["style"]["size"], after["k"]["style"]["weight"]), (editor.TOPIC_SEC, 72, "Black"))   # 다시 첫머리 주제 줄
            kb, tb = editor.text_box(after["k"]["text"], after["k"]["style"], W, H), editor.text_box("트래핑 꿀팁", after["big"], W, H)
            self.assertFalse(inter(kb, tb))

    def test_msg_hook_font_is_replaced(self):
        """검토: MSG 로 만든 쇼츠의 큰 줄은 강조 글꼴(Black Han Sans)·stamp·회전을 가짐 → 템플릿 뒤에도 남으면 Pretendard 폭으로 고른 크기와
        그림이 어긋나고 자동으로 만든 은평 스타일과 달라짐 · 이제 서버 ep_title_style·EP_FACT 와 똑같은 모양 (D-426)."""
        got = run_js(self.SHORT + """
S = rough(false);
S.titles[0].style = { ...BIG, font: "Black Han Sans", size: 96, effect: "stamp", rot: 3 };
S.titles[1].style = { ...TOPIC_STYLE, font: "Black Han Sans", size: 64, y: 0.133 };
TEMPLATES["은평 스타일"]();
out = { big: S.titles.find(t => t.id === "big"), k: S.titles.find(t => t.id === "k") };""")
        self.assertEqual(got["big"]["style"], editor.ep_title_style(got["big"]["text"]))
        self.assertNotIn("font", got["big"]["style"])
        self.assertEqual(got["k"]["style"], editor.EP_FACT)


def run_js2(body, extra=("newPair", "fullSeq", "winClip", "fitRect")):
    """run_js 와 같되 함수 몇 개 더 (원본 전체로 fullSeq · 영상 창 자르기 winClip · fitRect)."""
    prog = ("let N = 0; const nid = () => 'n' + (++N);\nlet S = null, P = { info: { width: 1920, height: 1080 } }, out = null;\n"
            "const total = () => Math.max(0, ...S.items.map(iEnd));\nconst tacFollowCut = g => g, tacRefollow = g => g.follow;\n"
            "const BW = () => 1080, BH = () => 1920;\n" + js_defs(list(JS_DEFS) + list(extra)) + "\n" + body + "\nprocess.stdout.write(JSON.stringify(out));")
    r = subprocess.run([shutil.which("node"), "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "node 가 없어요")
class EditorRemapTest(unittest.TestCase):
    """검토: 은평 스타일 사실 줄(끝까지)은 '맨 앞 주제 줄 = 앞이 줄거나 늘어도 그대로(BR-120)' 규칙 때문에 원본 전체로·군더더기 정리·잘라내기 뒤
    큰 제목·흰 상자와 길이가 어긋났음 (사실 줄만 먼저 사라지거나 영상 끝 뒤까지 남음) · 이제 큰 제목처럼 줄고 늚 (D-425)."""
    SHORT = EditorTemplateTest.SHORT

    def test_fact_line_follows_title(self):
        got = run_js2(self.SHORT + """
const row = () => { const b = S.titles.find(t => t.id === "big"), k = S.titles.find(t => t.id === "k"); return { b: [b.start, b.dur], k: [k.start, k.dur], L: total() }; };
const ep = () => { S = rough(false); TEMPLATES["은평 스타일"](); };
const apply = r => { S = { ...S, ...r }; };
out = {};
ep(); apply(fullSeq(S, null, null)); out.full = row();                                   // 원본 전체로: 가운데 10초가 들어감 → 40초
ep(); apply(tidySeq(S, [{ in: 10, out: 15 }, { in: 30, out: 50 }])); out.tidy = row();   // 군더더기 정리: 5초 빠짐 → 25초
ep(); gfxCut(0, 3); out.cut0 = row();                                                     // 잘라내기(글자·도형): 앞 3초
ep(); gfxCut(12, 16); out.cut1 = row();
S = rough(false); apply(fullSeq(S, null, null)); out.plain = row();                       // 은평 스타일이 아니면 예전처럼 맨 앞 2.5초 그대로
""")
        self.assertEqual(got["full"], {"b": [0, 40], "k": [0, 40], "L": 40})
        self.assertEqual(got["tidy"], {"b": [0, 25], "k": [0, 25], "L": 25})
        self.assertEqual(got["cut0"]["b"], [0, 27])
        self.assertEqual(got["cut0"]["k"], [0, 27])
        self.assertEqual(got["cut1"]["k"], got["cut1"]["b"])
        self.assertEqual(got["plain"]["k"], [0, 2.5])
        self.assertEqual(got["plain"]["b"], [0, 40])

    def test_win_clip_polygon(self):
        """미리보기 영상 창 자르기(winClip): 다각형을 클립 transform 으로 옮기면 정확히 영상 창 네 모서리 · 움직임 없으면 자르지 않음 ·
        fitRect 의 win 은 box 배치 + layout.win 일 때만 (editor._fit 과 같음)."""
        got = run_js2("""
const f = { bx: 0, by: 539, bw: 1080, bh: 802, win: true }, k = 0.5;
out = { cases: [], none: winClip(f, { sc: 1, rot: 0, pos: [0.5, 0.5], anc: [0.5, 0.5] }, k, 1080, 1920) };
for (const mo of [{ sc: 1.6, rot: 0, pos: [0.5, 0.5], anc: [0.5, 0.5] }, { sc: 1.35, rot: 12, pos: [0.53, 0.46], anc: [0.3, 0.7] }, { sc: 0.8, rot: 0, pos: [0.5, 0.52], anc: [0.5, 0.5] }])
  out.cases.push({ mo, poly: winClip(f, mo, k, 1080, 1920) });
const md = { kind: "video", w: 1920, h: 1080 }, it = { track: "V1", fit: "auto" };
S = { format: "shorts", layout: { ...EP_L, win: true } }; out.on = fitRect(it, md);
S = { format: "shorts", layout: { ...EP_L } }; out.off = fitRect(it, md).win;
S = { format: "shorts", layout: { ...EP_L, mode: "blur", win: true } }; out.blur = fitRect(it, md).win;
""".replace("EP_L", json.dumps(dict(editor.EP_LAYOUT, win=None))))
        import math
        import re
        self.assertEqual(got["none"], "none")
        w, h = 1080 * 0.5, 802 * 0.5
        for c in got["cases"]:
            mo = c["mo"]
            pts = [tuple(map(float, re.findall(r"-?[\d.]+", p))) for p in c["poly"][len("polygon("):-1].split(",")]
            ox, oy = mo["anc"][0] * w, mo["anc"][1] * h
            tx, ty = (mo["pos"][0] - 0.5) * 1080 * 0.5, (mo["pos"][1] - 0.5) * 1920 * 0.5
            a = math.radians(mo["rot"])
            fwd = [(ox + tx + mo["sc"] * ((x - ox) * math.cos(a) - (y - oy) * math.sin(a)), oy + ty + mo["sc"] * ((x - ox) * math.sin(a) + (y - oy) * math.cos(a)))
                   for x, y in pts]
            for (x, y), (ex, ey) in zip(fwd, [(0, 0), (w, 0), (w, h), (0, h)]):
                self.assertAlmostEqual(x, ex, delta=0.02, msg=c)
                self.assertAlmostEqual(y, ey, delta=0.02, msg=c)
        self.assertTrue(got["on"]["win"])
        py = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1920, "h": 1080}, {"format": "shorts", "layout": editor.EP_LAYOUT}, 1080, 1920)
        self.assertTrue(py["win"])
        self.assertAlmostEqual(got["on"]["by"], py["by"], delta=1)
        self.assertAlmostEqual(got["on"]["bh"], py["bh"], delta=1)
        self.assertFalse(got["off"])
        self.assertFalse(got["blur"])
        self.assertFalse(editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1920, "h": 1080},
                                     {"format": "shorts", "layout": dict(editor.EP_LAYOUT, win=False)}, 1080, 1920)["win"])


@unittest.skipUnless(FF, "ffmpeg 가 없어요")
class ZoomWindowExportTest(WorkMixin, unittest.TestCase):
    """검토: 줌 컷·MSG 펀치 확대가 영상 창(0.28~0.70) 밖 파란 띠·사실 줄·상황 자막 줄·말 자막 띠로 넘쳤음 → 은평 스타일(layout.win)이면
    확대·이동·회전해도 영상 창 안에서만 (실제 내보내기 720×1280 · 고정 확대 160% · 움직이는 확대+이동 · 회전 · D-424).
    win 을 빼면(예전 box 배치) 그대로 넘침 = 시험이 실제로 차이를 봄."""

    def render(self, name, win):
        d = 1.2
        items = []
        for i, fx in enumerate(({}, {"scale": {"v": 160, "k": []}},
                                {"scale": {"v": 100, "k": [{"t": 2 * d, "v": 100}, {"t": 3 * d, "v": 175}]}, "pos": {"v": [0.5, 0.5], "k": [{"t": 2 * d, "v": [0.5, 0.5]}, {"t": 3 * d, "v": [0.55, 0.44]}]}},
                                {"scale": {"v": 140, "k": []}, "rot": {"v": 8, "k": []}})):
            v, a = editor._pair(i * d, (i + 1) * d, start=i * d)
            v["fx"] = fx
            items += [v, a]
        lay = dict(editor.EP_LAYOUT) if win else {k: v for k, v in editor.EP_LAYOUT.items() if k != "win"}
        q = editor._new_seq("쇼츠 1", "shorts", items, captionStyle=dict(editor.SHORTS_STYLE), layout=lay, titles=[], shapes=[])
        proj = dict(q, captions=[], info=self.info, source=name, media=[], master={"volume": 1, "normalize": False, "lufs": -14})
        out = editor.export(name, proj, {"preset": "small", "hw": False, "xml": False, "srt": False}, lambda m: None)
        frames = []
        for t in (0.6, 1.8, 3.3, 4.2):
            raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(core.OUT / out[0]), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 capture_output=True).stdout
            frames.append(raw)
        return frames

    def test_zoom_stays_in_window(self):
        import numpy as np
        name = self.fix("MSGRAW01")
        d = 4.8
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s=640x360:r=30:d={d}", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                            "-t", str(d), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(core.VIDEOS / name)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.info = {"duration": d, "width": 640, "height": 360, "fps": 30.0}
        Hh, Ww = 1280, 720
        fit = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 640, "h": 360}, {"format": "shorts", "layout": editor.EP_LAYOUT}, Ww, Hh)
        wy0, wy1 = int(fit["by"]) & ~1, (int(fit["by"]) & ~1) + fit["bh"]

        def gray_rows(raw, x):
            a = np.frombuffer(raw, np.uint8).reshape(Hh, Ww, 3).astype(int)
            g = np.where((abs(a[:, int(x * Ww)] - 128) < 20).all(axis=1))[0]
            return a, g
        on = self.render(name, True)
        for i, raw in enumerate(on):
            a, g = gray_rows(raw, 0.5)
            self.assertGreater(len(g), 100, i)
            self.assertGreaterEqual(g.min(), wy0 - 1, i)          # 영상은 창 안에서만
            self.assertLessEqual(g.max(), wy1, i)
            for y in (0.05, 0.2, 0.26, 0.71, 0.8, 0.95):          # 띠는 파랑 그대로 (가운데·양 끝)
                for x in (0.02, 0.5, 0.98):
                    p = a[int(y * Hh), int(x * Ww)]
                    self.assertTrue(all(abs(int(p[c]) - BLUE[c]) <= 24 for c in range(3)), (i, y, x, p))
            if i in (1, 2):
                self.assertLessEqual(abs(g.min() - wy0), 2, i)    # 확대한 클립도 창을 꽉 채움 (창 크기는 그대로)
                self.assertLessEqual(abs(g.max() - (wy1 - 1)), 2, i)
        off = self.render(name, False)
        _, g = gray_rows(off[1], 0.5)
        self.assertLess(g.min(), wy0 - 40, "예전 box 배치는 확대가 띠로 넘침 (시험이 차이를 봄)")


# ---------- 덧붙임: 시리즈 이름표 · 전체 강좌 줄 (BR-245 · BR-246 · D-428~D-430) ----------

class ExtrasSettingTest(unittest.TestCase):
    setUp, tearDown = SettingTest.setUp, SettingTest.tearDown
    def test_defaults_set_and_clean(self):
        self.assertEqual(style.card_extras(), {"series": False, "seriesName": "1분 레슨", "longLine": False})
        self.assertEqual(editor.ep_extras(10.0), ([], []))
        self.assertEqual(style.set_card_extras(series=True), {"series": True, "seriesName": "1분 레슨", "longLine": False})
        self.assertEqual(style.set_card_extras(long_line=True)["longLine"], True)
        self.assertTrue(style.card_extras()["series"])   # 준 값만 바뀜
        for raw, want in (("  기본기  (특강) ", "기본기 특강"), ("[원 #3]", "원 3"), ("", "1분 레슨"), ("가" * 20, "가" * 12), ("줄\n바꿈", "줄 바꿈")):
            self.assertEqual(style.clean_series_name(raw), want, raw)
        self.assertEqual(style.set_card_extras(name="2분  특강")["seriesName"], "2분 특강")
        f = style._card_extra_file()
        for bad in ("{", "[1]", '{"series": "yes", "longLine": 1}'):
            f.write_text(bad, encoding="utf-8")
            self.assertEqual(style.card_extras(), {"series": False, "seriesName": "1분 레슨", "longLine": False}, bad)

    def test_counter_assign_reuse_edit_no_dup(self):
        self.assertEqual(style.series_peek(), 1)
        self.assertEqual(style.series_peek(ahead=2), 3)
        self.assertEqual(style.series_assign("a.mp4", "s1"), 1)
        self.assertEqual(style.series_assign("a.mp4", "s1"), 1)        # 다시 내보내도 그대로
        self.assertEqual(style.series_assign("a.mp4", "s2"), 2)
        self.assertEqual(style.series_assign("b.mp4", "s1"), 3)        # 다른 영상의 같은 id 도 따로
        self.assertEqual(style.series_peek("a.mp4", "s2"), 2)
        self.assertEqual(style.series_assign("a.mp4", "s1", want=10), 10)   # 사용자가 고친 번호
        self.assertEqual(style.series_assign("c.mp4", "s9"), 11)       # 다음 번호는 가장 큰 번호 뒤 (겹치지 않음)
        self.assertEqual(style.series_numbers("a.mp4"), {"s1": 10, "s2": 2})
        d = json.loads(style._series_file().read_text(encoding="utf-8"))
        d["note"] = "다른 판이 더한 칸"
        style._series_file().write_text(json.dumps(d), encoding="utf-8")
        style.series_assign("d.mp4", "s1")
        self.assertEqual(json.loads(style._series_file().read_text(encoding="utf-8"))["note"], "다른 판이 더한 칸")   # 모르는 칸은 그대로
        with mock.patch.dict(core.CONFIG, {"channel_url": "https://www.youtube.com/@another"}):   # 채널마다 따로 셈
            self.assertEqual(style.series_peek(), 1)
            self.assertEqual(style.series_assign("a.mp4", "s1"), 1)
        self.assertEqual(style.series_peek("a.mp4", "s1"), 10)
        style._series_file().write_text("{", encoding="utf-8")
        self.assertEqual(style.series_peek(), 1)                       # 깨진 파일 = 처음부터 (화면은 열림)

    def test_apply_and_view(self):
        style.set_card_extras(series=True)
        t, g = editor.ep_series("1분 레슨", 5, 30)
        seq = {"id": "s1", "format": "shorts", "titles": [t], "shapes": [g]}
        r = editor.series_apply("a.mp4", seq)
        self.assertEqual((r["n"], t["text"], t["seriesN"]), (1, "1분 레슨 (1)", 1))   # 보기 번호 5 → 처음 내보낼 때 정한 1
        self.assertEqual((r["x"], r["w"]), (g["x"], g["w"]))
        t["text"] = "1분 레슨 (7)"                                       # 편집실에서 번호를 고침
        self.assertEqual(editor.series_apply("a.mp4", seq)["n"], 7)
        self.assertEqual(style.series_numbers("a.mp4"), {"s1": 7})
        self.assertEqual(editor.series_apply("a.mp4", seq)["n"], 7)    # 다시 내보내도 그대로
        t["text"] = "기본기 (7)"                                         # 이름만 고침 → 번호 그대로 · 이름도 그대로
        self.assertEqual(editor.series_apply("a.mp4", seq)["text"], "기본기 (7)")
        w0 = g["w"]
        t["text"] = "기본기 특강 (7)"
        editor.series_apply("a.mp4", seq)
        self.assertGreater(g["w"], w0)                                  # 상자는 글 폭에 맞춤
        self.assertIsNone(editor.series_apply("a.mp4", {"id": "s2", "format": "long", "titles": [dict(t)]}))
        self.assertIsNone(editor.series_apply("a.mp4", {"id": "s3", "format": "shorts", "titles": []}))
        # 편집실을 열 때: 다른 곳에서 정한 번호를 보여 줌 (보기 번호 그대로인 이름표만 · 고친 번호는 그대로)
        t2, g2 = editor.ep_series("1분 레슨", 3, 30)
        t3, g3 = editor.ep_series("1분 레슨", 4, 30)
        t3["text"] = "1분 레슨 (40)"
        style.series_assign("a.mp4", "s4")
        style.series_assign("a.mp4", "s5")
        proj = {"sequences": [{"id": "s4", "format": "shorts", "titles": [t2], "shapes": [g2]}, {"id": "s5", "format": "shorts", "titles": [t3], "shapes": [g3]}]}
        editor.series_view("a.mp4", proj)
        self.assertEqual((t2["text"], t2["seriesN"]), ("1분 레슨 (8)", 8))
        self.assertEqual(t3["text"], "1분 레슨 (40)")

    def test_learned_title_skeleton_still_drops_episode(self):
        """hooks._SERIES 는 배운 제목 틀에서 회차를 일부러 지움 — 업로드 제목 앞 '[1분 레슨 #12]' 도 같이 지워지고 겹침 열쇠에도 안 들어감."""
        import hooks
        self.assertEqual(hooks._clean_title("[1분 레슨 #12] 트래핑 꿀팁"), "트래핑 꿀팁")
        self.assertEqual(hooks.norm_title("[1분 레슨 #12] 트래핑 꿀팁"), hooks.norm_title("트래핑 꿀팁"))


class ExtrasLayoutTest(unittest.TestCase):
    def test_tag_and_line_places(self):
        t, g = editor.ep_series("1분 레슨", 12, 30)
        self.assertEqual((t["plan"], t["text"], t["seriesN"], t["start"], t["dur"]), ("series", "1분 레슨 (12)", 12, 0.0, 30.0))
        self.assertEqual((g["plan"], g["full"], g["color"], g["opacity"], g["radius"]), ("series", True, "#000000", 1.0, 0))
        tb = ebox(t)
        self.assertLess(g["y"] + g["h"], editor.EP_BOX["y"])                   # 흰 상자 위
        self.assertGreaterEqual(g["y"], 0.05)                                  # 화면 맨 위 아이콘 줄은 비움
        self.assertTrue(g["x"] < tb[0] and tb[2] < g["x"] + g["w"] and g["y"] <= tb[1] and tb[3] <= g["y"] + g["h"], (tb, g))   # (글 상자는 화면 비율)
        self.assertAlmostEqual(g["x"] + g["w"] / 2, 0.5, places=3)
        self.assertAlmostEqual((tb[1] + tb[3]) / 2, g["y"] + g["h"] / 2, places=3)
        ln = editor.ep_longline(30)
        lb = ebox(ln)
        cap = editor._cap_band(editor.ep_caption(dict(editor.SHORTS_STYLE)), H, 2)
        self.assertGreater(lb[1] * H, cap[1])                                  # 말 자막(두 줄 · 화소) 아래
        self.assertGreater(lb[1], video_band(editor.EP_LAYOUT)[1])             # 아래 띠
        self.assertLess(lb[3], 0.9)
        self.assertEqual(ln["text"], "전체 강좌는 채널에서 ▶")


class ExtrasRoughTest(WorkMixin, unittest.TestCase):
    INFO, strip = RoughCardTest.INFO, RoughCardTest.strip
    def test_rough_card_with_extras(self):
        name = self.fix("MSGRAW01")
        style.set_shorts_layout("eunpyeong")
        plain = self.strip(editor.auto_sequences(name, self.INFO, None, ("shorts",)))
        for q in json.loads(plain):   # 덧붙임을 고르지 않으면 R1 그대로 (이름표·줄 없음)
            self.assertFalse([t for t in q["titles"] if t.get("plan") in ("series", "longline")])
        style.set_card_extras(series=True, long_line=True)
        qs = editor.auto_sequences(name, self.INFO, None, ("shorts",))
        self.assertGreater(len(qs), 1)
        for i, q in enumerate(qs):
            L = round(editor.seq_total(q), 2)
            [tag] = [t for t in q["titles"] if t.get("plan") == "series"]
            [line] = [t for t in q["titles"] if t.get("plan") == "longline"]
            [g] = [x for x in q["shapes"] if x.get("plan") == "series"]
            self.assertEqual(tag["text"], f"1분 레슨 ({i + 1})")            # 보기 번호는 차례로 (아직 정하지 않음)
            self.assertEqual((tag["start"], tag["dur"], line["start"], line["dur"]), (0.0, L, 0.0, L))
            others = [ebox(t) for t in q["titles"] if t is not tag and t is not line]
            others.append([editor.EP_BOX["x"], editor.EP_BOX["y"], editor.EP_BOX["x"] + editor.EP_BOX["w"], editor.EP_BOX["y"] + editor.EP_BOX["h"]])
            cb = editor._cap_band(q["captionStyle"], H, 2)
            others.append([0, cb[0] / H, 1, cb[1] / H])   # 두 줄 말 자막
            for o in others:
                self.assertFalse(inter(ebox(tag), o))
                self.assertFalse(inter([g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"]], o))
                self.assertFalse(inter(ebox(line), o))
        self.assertFalse(style._series_file().exists())                       # 만들 때는 번호를 정하지 않음


class ExtrasMsgTest(unittest.TestCase):
    """MSG 쇼츠 (은평 스타일 + 이름표·전체 강좌 줄): 둘 다 끝까지 · 다른 MSG 글자는 피함 · 이름표는 첫머리 사건과 한 묶음."""

    @classmethod
    def setUpClass(cls):
        from make_msg_fixture import MsgWork
        cls.w = MsgWork()
        style.set_shorts_layout("eunpyeong")
        style.set_card_extras(series=True, long_line=True)
        specs = [{"kind": "preset", "name": n} for n in msg.PRESETS]
        cls.qs = msg.build_variants(cls.w.name, specs[:2], "듬뿍", ("shorts",), lambda m: None)["sequences"]

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_extras_fixed_and_avoided(self):
        self.assertTrue(self.qs)
        for q in self.qs:
            L = round(editor.seq_total(q), 3)
            ev = next(e for e in q["msg"]["events"] if e["kind"] == "hook")
            [tag] = [t for t in q["titles"] if t.get("plan") == "series"]
            [line] = [t for t in q["titles"] if t.get("plan") == "longline"]
            [g] = [x for x in q["shapes"] if x.get("plan") == "series"]
            self.assertIn(tag["id"], ev["refs"]["titles"])
            self.assertIn(g["id"], ev["refs"]["shapes"])
            self.assertNotIn(line["id"], ev["refs"]["titles"])
            for x in (tag, line, g):
                self.assertEqual((x["start"], x["dur"]), (0.0, L), q["name"])
            fixed = [msg.text_box(tag["text"], tag["style"], W, H), msg.text_box(line["text"], line["style"], W, H),
                     [g["x"] * W, g["y"] * H, (g["x"] + g["w"]) * W, (g["y"] + g["h"]) * H]]
            for t in q["titles"]:
                if t["id"] in ev["refs"]["titles"] or t is line:
                    continue
                b = msg.text_box(t["text"], t["style"], W, H)
                for o in fixed:
                    self.assertFalse(inter(b, o), (q["name"], t["text"], b, o))


@unittest.skipUnless(FF, "ffmpeg 가 없어요")
class ExtrasExportTest(WorkMixin, unittest.TestCase):
    """실제 내보내기 (720×1280): 처음 내보낼 때 번호를 정해 이름표에 · 다시 내보내도 같은 번호 · 다른 쇼츠는 다음 번호 · 화소 (검은 이름표 · 흰 글씨 · 전체 강좌 줄)."""

    def frame(self, out, t=1.0):
        import numpy as np
        raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(core.OUT / out), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1280, 720, 3).astype(int)

    def test_export_assigns_and_draws(self):
        import numpy as np
        name = self.fix("MSGRAW01")
        d = 3.0
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s=640x360:r=30:d={d}", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                            "-t", str(d), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(core.VIDEOS / name)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        style.set_card_extras(series=True, long_line=True)
        info = {"duration": d, "width": 640, "height": 360, "fps": 30.0}

        def make(label):
            top = {"id": "t" + label, "text": "트래핑 꿀팁", "start": 0.0, "dur": d, "style": dict(editor.TITLE_STYLE)}
            shapes = editor.ep_card(top, None, d)
            xt, xg = editor.ep_extras(d)
            q = editor._new_seq(label, "shorts", editor._items_from_cuts([{"in": 0.0, "out": d}]), captionStyle=editor.ep_caption(dict(editor.SHORTS_STYLE)),
                                layout=dict(editor.EP_LAYOUT), titles=[top] + xt, shapes=shapes + xg)
            return dict(q, captions=[], info=info, source=name, media=[], master={"volume": 1, "normalize": False, "lufs": -14})
        opts = {"preset": "small", "hw": False, "xml": False, "srt": False}
        a, b = make("쇼츠 1"), make("쇼츠 2")
        self.assertEqual(next(t for t in a["titles"] if t.get("plan") == "series")["text"], "1분 레슨 (1)")
        out_b = editor.export(name, b, opts, lambda m: None)                   # 두 번째 쇼츠를 먼저 내보냄 → 1
        out_a = editor.export(name, a, opts, lambda m: None)                   # 그다음 → 2 (보기 번호 1 은 이미 씀 · 겹치지 않음)
        out_a2 = editor.export(name, a, opts, lambda m: None)                  # 다시 → 그대로 2
        self.assertEqual(style.series_numbers(name), {b["id"]: 1, a["id"]: 2})
        self.assertNotEqual(out_a[0], out_a2[0])
        Hh, Ww = 1280, 720
        dark = []
        for out in (out_b, out_a, out_a2):
            f = self.frame(out[0])
            band = f[int(0.054 * Hh):int(0.086 * Hh)]
            blk = (band.max(axis=2) < 40)
            cols = np.where(blk.any(axis=0))[0]
            self.assertGreater(len(cols), 50, "검은 이름표 상자")
            self.assertAlmostEqual((cols.min() + cols.max()) / 2 / Ww, 0.5, delta=0.01)
            white = (band.min(axis=2) > 215)[:, cols.min():cols.max() + 1]
            self.assertGreater(white.sum(), 40, "이름표 흰 글씨")
            self.assertTrue(all(abs(int(f[int(0.07 * Hh), 20][c]) - BLUE[c]) <= 24 for c in range(3)))   # 이름표 옆은 파란 띠
            line = f[int(0.835 * Hh):int(0.87 * Hh)]
            lrows = np.where((line.min(axis=2) > 215).sum(axis=1) > 3)[0]
            self.assertGreater(len(lrows), 5, "전체 강좌 줄 흰 글씨")
            dark.append(cols.max() - cols.min())
        self.assertEqual(dark[1], dark[2])   # 다시 내보내도 같은 번호 = 같은 이름표


@unittest.skipUnless(shutil.which("node"), "node 가 없어요")
class ExtrasEditorTest(unittest.TestCase):
    """editor.html epCard·epExtras·epSeriesFit·epOff 를 node 로 그대로 → 서버 ep_series·ep_longline 과 같은 값."""

    def test_template_adds_same_as_server(self):
        got = run_js(EditorTemplateTest.SHORT + """
out = [];
EPX = { series: true, seriesName: "1분 레슨", longLine: true, next: 12 };
S = rough(false); TEMPLATES["은평 스타일"](); TEMPLATES["은평 스타일"]();
const cp = x => JSON.parse(JSON.stringify(x));
out.push(cp({ titles: S.titles, shapes: S.shapes }));
const tg = S.titles.find(t => t.plan === "series"); tg.text = "1분 레슨 (123)"; epSeriesFit(tg, S.shapes.find(g => g.plan === "series"));
out.push(cp(S.shapes.find(g => g.plan === "series")));
TEMPLATES["위아래 검정 박스"](); out.push(cp({ titles: S.titles, shapes: S.shapes }));
EPX = null; S = rough(false); TEMPLATES["은평 스타일"](); out.push(cp({ titles: S.titles, shapes: S.shapes }));""")
        on, wide, off, none = got
        tags = [t for t in on["titles"] if t.get("plan") == "series"]
        lines = [t for t in on["titles"] if t.get("plan") == "longline"]
        gs = [g for g in on["shapes"] if g.get("plan") == "series"]
        self.assertEqual((len(tags), len(lines), len(gs)), (1, 1, 1))          # 두 번 눌러도 하나
        pt, pg = editor.ep_series("1분 레슨", 12, 30)
        self.assertEqual((tags[0]["text"], tags[0]["seriesN"], tags[0]["style"]), (pt["text"], 12, pt["style"]))
        for k in ("x", "y", "w", "h", "color", "opacity", "radius", "full", "plan"):
            self.assertEqual(gs[0][k], pg[k], k)
        self.assertEqual(lines[0]["style"], editor.ep_longline(30)["style"])
        self.assertEqual(lines[0]["text"], editor.LONG_LINE)
        t2 = dict(pt, text="1분 레슨 (123)")
        g2 = dict(pg)
        editor.ep_series_fit(t2, g2)
        self.assertEqual((wide["x"], wide["w"]), (g2["x"], g2["w"]))
        self.assertGreater(wide["w"], gs[0]["w"])
        self.assertFalse([t for t in off["titles"] if t.get("plan") in ("series", "longline")])   # 다른 템플릿은 지움
        self.assertFalse([g for g in off["shapes"] if g.get("plan") == "series"])
        self.assertFalse([t for t in none["titles"] if t.get("plan") in ("series", "longline")])  # 고르지 않았으면 안 넣음
        big = next(t for t in on["titles"] if t["id"] == "big")
        self.assertEqual(big["style"], editor.ep_title_style(big["text"]))     # 큰 제목은 이름표가 아님 (가장 긴 타이틀 고르기에서 뺌)


class ExtrasRouteTest(unittest.TestCase):
    def test_routes(self):
        from test_windows_compat import Server

        class T(Server):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            self.assertEqual(t.call("/api/style/card_extras"), (200, {"series": False, "seriesName": "1분 레슨", "longLine": False}))
            self.assertEqual(t.call("/api/style/card_extras", {"series": True, "seriesName": "기본기 (특강)"}),
                             (200, {"ok": True, "series": True, "seriesName": "기본기 특강", "longLine": False}))
            self.assertEqual(t.call("/api/style/card_extras", {"longLine": True, "series": "yes"})[1]["series"], True)   # 잘못된 값은 안 바꿈
            self.assertEqual(t.call("/api/style/list")[1]["cardExtras"]["longLine"], True)
            # 내보내기: 작업을 시작하기 전에 번호를 정해 돌려줌 (편집실 이름표도 같은 번호)
            tg, g = editor.ep_series("기본기 특강", 9, 3)
            proj = {"id": "s1", "format": "shorts", "titles": [tg], "shapes": [g]}
            with mock.patch.object(editor, "export", lambda *a, **k: []):
                st, j = t.call("/api/edit/export", {"name": "a.mp4", "project": proj, "opts": {}})
                t.wait_job()
                self.assertEqual(st, 200)
                self.assertEqual((j["series"]["seq"], j["series"]["n"], j["series"]["text"]), ("s1", 1, "기본기 특강 (1)"))
                st, j = t.call("/api/edit/export", {"name": "a.mp4", "project": {"id": "s2", "format": "long", "titles": []}, "opts": {}})
                t.wait_job()
                self.assertNotIn("series", j)
        finally:
            t.doCleanups()
        import rstudio
        txt = Path(rstudio.__file__).read_text(encoding="utf-8")
        self.assertEqual(txt.count('"/api/style/card_extras"'), 2)


@unittest.skipUnless(FF, "ffmpeg 가 없어요")
class ExtrasKitTest(unittest.TestCase):
    """올리기 키트: 이름표가 있는 쇼츠 = 제목 후보 앞 '[이름 #n]' (내보낸 번호와 같음) · 전체 강좌 링크 (올린 롱폼 → 롱폼 키트 제목 → 채널)."""

    @classmethod
    def setUpClass(cls):
        from test_upload import KitBase
        cls.KB = KitBase
        KitBase.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.KB.tearDownClass.__func__(cls)

    def setUp(self):
        self.KB.setUp(self)
        self.st = mock.patch.object(style, "STYLES", self.work / "styles")
        self.st.start()

    def tearDown(self):
        self.st.stop()
        self.KB.tearDown(self)

    def project(self, name, tag=True):
        from test_upload import fixture_segments
        caps = [{"id": str(i), "start": s["start"], "end": s["end"], "text": s["text"]} for i, s in enumerate(fixture_segments())]
        items = [{"id": "v", "track": "V1", "media": "main", "in": 0.0, "out": 45.0, "start": 0.0, "speed": 1.0, "link": "l"},
                 {"id": "a", "track": "A1", "media": "main", "in": 0.0, "out": 45.0, "start": 0.0, "speed": 1.0, "link": "l"}]
        tg, g = editor.ep_series("1분 레슨", 1, 45)
        titles = [{"id": "t", "text": "퍼스트 터치 꿀팁", "start": 0, "dur": 45}] + ([tg] if tag else [])
        proj = {"source": name, "info": {"duration": 720.0, "width": 160, "height": 90}, "captions": caps, "v": 2, "rev": 3,
                "sequences": [{"id": "s1", "name": "쇼츠 1", "format": "shorts", "v": 2, "items": items, "markers": [], "titles": titles,
                               "shapes": [g] if tag else [], "tracks": editor.default_tracks()},
                              {"id": "L", "name": "롱폼 가편집", "format": "long", "v": 2, "items": [dict(it, out=720.0) for it in items], "markers": [],
                               "titles": [], "tracks": editor.default_tracks()}]}
        editor._ppath(name).write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")

    def test_series_title(self):
        import upload
        name = self.KB.add_video(self, self.KB.NAME)
        self.project(name)
        style.series_assign("other.mp4", "x")            # 다른 쇼츠가 1번을 이미 씀
        kit = upload.build_kit(name, "s1")
        self.assertEqual(kit["series"], {"n": 2, "tag": "[1분 레슨 #2]"})
        self.assertTrue(all(t.startswith("[1분 레슨 #2] ") for t in kit["titles"]), kit["titles"])
        self.assertEqual(kit["title"], kit["titles"][0])
        self.assertNotIn("1분 레슨 (", kit["title"])      # 큰 제목 고르기에서 이름표는 뺌
        self.assertEqual(upload.build_kit(name, "s1")["title"], kit["title"])   # 다시 만들어도 같은 번호 (두 번 붙지 않음)
        self.assertEqual(style.series_numbers(name), {"s1": 2})
        kit2 = upload.set_guests(name, "s1", [{"name": "김코치"}])
        self.assertTrue(all(t.startswith("[1분 레슨 #2] ") and not t.startswith("[1분 레슨 #2] [") for t in kit2["titles"]), kit2["titles"])
        self.assertNotIn("전체 강좌", kit["description"])   # 강좌 줄을 고르지 않으면 설명 그대로

    def test_no_tag_no_change(self):
        import upload
        name = self.KB.add_video(self, self.KB.NAME)
        self.project(name, tag=False)
        kit = upload.build_kit(name, "s1")
        self.assertIsNone(kit["series"])
        self.assertFalse(any(t.startswith("[") for t in kit["titles"]))
        self.assertFalse(style._series_file().exists())

    def test_long_link(self):
        import upload
        import youtube_upload
        name = self.KB.add_video(self, self.KB.NAME)
        self.project(name, tag=False)
        style.set_card_extras(long_line=True)
        with mock.patch.dict(core.CONFIG, {"channel_url": "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx"}):
            kit = upload.build_kit(name, "s1")
            self.assertEqual(kit["longLink"], {"url": "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx", "how": "channel"})
            head, block = kit["description"].split("\n\n")[:2]
            self.assertEqual(block, "▶ 전체 강좌는 채널에서\nhttps://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx")
            self.assertTrue(any("우리 채널 주소" in n for n in kit["notes"]))
            # 롱폼 키트 제목 = 우리 채널 영상 제목 → 그 영상
            lk = upload.build_kit(name, "L")
            self.assertNotIn("전체 강좌", lk["description"])     # 롱폼 키트는 그대로
            import hooks
            with mock.patch.object(hooks, "own_rows", lambda cache=None: {"videos": [{"title": lk["title"], "id": "abcdefghijk"}], "shorts": []}):
                self.assertEqual(upload.long_link(name), {"url": "https://youtu.be/abcdefghijk", "how": "kit"})
            # 유튜브에 바로 올린 롱폼 기록이 가장 먼저 (공개만 · 쇼츠·다른 영상은 아님)
            hist = {"items": [{"videoId": "zzzzzzzzzzz", "name": name, "shorts": True, "privacy": "public"},
                              {"videoId": "yyyyyyyyyyy", "name": "다른 영상.mp4", "shorts": False, "privacy": "public"},
                              {"videoId": "xxxxxxxxxxx", "name": name, "shorts": False, "privacy": "private"},
                              {"videoId": "wwwwwwwwwww", "name": name, "shorts": False, "privacy": "unlisted"}]}
            with mock.patch.object(youtube_upload, "history", lambda n=10: hist):
                kit = upload.build_kit(name, "s1")
            self.assertEqual(kit["longLink"], {"url": "https://youtu.be/wwwwwwwwwww", "how": "history"})
            self.assertIn("\n\n▶ 전체 강좌 보기\nhttps://youtu.be/wwwwwwwwwww\n\n", kit["description"])
            self.assertLessEqual(len(kit["description"]), upload.DESC_MAX)


if __name__ == "__main__":
    unittest.main()