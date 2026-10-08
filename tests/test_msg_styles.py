"""MSG 스타일 (기본 스타일 · 배운 스타일 부분 값 · 스타일 섞기, D-023) — 저장소 폴더에서 python3 -m unittest tests.test_msg_styles
기본 스타일 4개 · 배운 값을 범위로 묶기 · 섞은 스타일 저장(styles/섞기)·'좋아요' 고르기·최근 바꾼 것 · 지운 스타일은 기본값으로 ·
배운 스타일 목록(list_styles)과 섞이지 않음 · /api/style/presets·mix·mix_pick·/api/edit/msg 경로."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import msg  # noqa: E402
import style  # noqa: E402


def learned(name="이상한 채널", **params):
    p = {"keepPause": 3.0, "zoomEvery": 0.5, "zoomScale": 2.5, "splitShot": 99, "curve3": [99, 99, 99], "tempo": 40, "captions": True,
         "captionPos": "top", "captionColor": "#ff00aa", "lufs": -3}
    p.update(params)
    plan = {"intro": {"type": "teaser", "teaserSec": 30, "titleCard": True, "bgmAtStart": True},
            "captions": {"style": "variety", "colors": ["#FF4D4D"], "keywordPerMin": 50, "perMin": {"inner": 9, "situ": 8, "sfx": 7}},
            "fun": {"items": [{"key": "punch_zoom", "perMin": 40}, {"key": "sfx", "perMin": 30}, {"key": "slowmo_replay", "perMin": 5}]}}
    return {"name": name, "params": p, "plan": plan, "desc": "시험", "profile": {}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="MSG 스타일 시험 "))
        (self.tmp / "styles").mkdir()
        self.p = [mock.patch.object(style, "STYLES", self.tmp / "styles"), mock.patch.object(core, "WORK", self.tmp)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Presets(Base):
    def test_four_presets_resolve_with_all_aspects(self):
        self.assertEqual(list(msg.PRESETS), ["담백 레슨형", "예능 MSG형", "쇼츠 하이텐션형", "다큐 감성형"])
        for nm in msg.PRESETS:
            st = msg.resolve({"kind": "preset", "name": nm})
            for a in msg.ASPECTS:
                self.assertIn(a, st)
                self.assertTrue(msg.describe_aspect(a, st[a]))
            self.assertEqual(st["sources"], {a: nm for a in msg.ASPECTS})
        with self.assertRaises(msg.MsgError):
            msg.resolve({"kind": "preset", "name": "없는 스타일"})

    def test_learned_values_are_clamped_into_bands(self):
        """이상한 레퍼런스 하나가 터무니없는 양을 만들지 않게: 말 사이 0.8초·확대 1.3배·1분 3개까지."""
        a = msg.learned_aspects(learned())
        self.assertEqual(a["rhythm"]["keepPause"], 0.8)
        self.assertEqual(a["rhythm"]["zoomEvery"], 0)          # 0.5초마다 확대 → 너무 잦음 → 안 함
        self.assertLessEqual(a["rhythm"]["zoomScale"], 1.3)
        self.assertLessEqual(a["rhythm"]["splitShot"], 12.0)
        self.assertTrue(all(v <= 3.0 for v in a["fun"]["perMin"].values()), a["fun"]["perMin"])  # 글자 양은 재미 부분 (D-031)
        self.assertTrue(all(v <= 1.6 for k, v in a["fun"].items() if isinstance(v, float)), a["fun"])
        self.assertTrue(-16 <= a["sound"]["lufs"] <= -12)
        self.assertEqual(a["intro"]["type"], "teaser")
        self.assertLessEqual(a["intro"]["teaserSec"], 6.0)
        self.assertEqual(a["captions"]["emphColor"], "#FF4D4D")
        self.assertEqual(a["captions"]["color"], "#FF00AA")


class Mixes(Base):
    def test_save_pick_history_and_separate_folder(self):
        m = msg.save_mix(msg.DRAFT_NAME, {"intro": {"kind": "preset", "name": "다큐 감성형"}}, "듬뿍")
        self.assertEqual(m["aspects"]["intro"], {"kind": "preset", "name": "다큐 감성형"})
        self.assertEqual(m["aspects"]["captions"], {"kind": "preset", "name": "예능 MSG형"})
        self.assertEqual(m["intensity"], "듬뿍")
        f = self.tmp / "styles" / "섞기" / f"{msg.DRAFT_NAME}.json"
        self.assertTrue(f.is_file())
        self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["v"], 1)
        self.assertEqual(style.list_styles(), [], "섞은 스타일은 배운 스타일 목록에 안 나옴")
        m = msg.save_mix(msg.DRAFT_NAME, pick=("captions", {"kind": "preset", "name": "쇼츠 하이텐션형"}))
        self.assertEqual(m["aspects"]["captions"]["name"], "쇼츠 하이텐션형")
        self.assertEqual(m["history"][-1]["aspect"], "captions")
        st = msg.resolve({"kind": "mix", "name": msg.DRAFT_NAME})
        self.assertEqual(st["sources"]["intro"], "다큐 감성형")
        self.assertEqual(st["sources"]["captions"], "쇼츠 하이텐션형")
        self.assertTrue(st["captions"]["karaoke"])
        self.assertTrue(st["intro"]["letterbox"])
        self.assertEqual([x["name"] for x in msg.list_mixes()], [msg.DRAFT_NAME])

    def test_deleted_learned_style_falls_back_with_note(self):
        with mock.patch.object(style, "list_styles", return_value=[learned("슛포러브 스타일")]):
            msg.save_mix("섞기 시험", {"fun": {"kind": "style", "name": "슛포러브 스타일"}})
            st = msg.resolve({"kind": "mix", "name": "섞기 시험"})
            self.assertEqual(st["sources"]["fun"], "슛포러브 스타일")
        st = msg.resolve({"kind": "mix", "name": "섞기 시험"})  # 스타일을 지움
        self.assertEqual(st["sources"]["fun"], "예능 MSG형")
        self.assertTrue(any("지워져" in n for n in st["notes"]))
        v = msg.mix_view("섞기 시험")
        self.assertTrue(v["aspects"]["fun"]["fallback"])

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            msg.save_mix("  ", None, "보통")
        with self.assertRaises(ValueError):
            msg.save_mix(msg.DRAFT_NAME, pick=("없는부분", {"kind": "preset", "name": "예능 MSG형"}))
        m = msg.save_mix("../밖으로", None, "아주많이")
        self.assertEqual(m["intensity"], "보통")
        self.assertTrue((self.tmp / "styles" / "섞기" / "밖으로.json").is_file())  # 경로 글자는 빼서 섞기 폴더 안에만
        self.assertFalse((self.tmp / "styles" / "밖으로.json").exists())
        with self.assertRaises(ValueError):
            msg.build_variants("x.mp4", [], "보통")
        with self.assertRaises(ValueError):
            msg.build_variants("x.mp4", [{"kind": "preset", "name": "예능 MSG형"}], "많이")


class Routes(Base):
    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.tmp / "studio.log")]
        for x in self.p2:
            x.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for x in self.p2:
            x.stop()
        super().tearDown()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_style_routes(self):
        code, j = self.call("/api/style/presets")
        self.assertEqual(code, 200)
        self.assertEqual([x["name"] for x in j["presets"]], list(msg.PRESETS))
        self.assertEqual(j["draft"], msg.DRAFT_NAME)
        code, j = self.call("/api/style/mix", {"name": msg.DRAFT_NAME, "aspects": {"sound": {"kind": "preset", "name": "다큐 감성형"}}, "intensity": "담백"})
        self.assertEqual((code, j["ok"], j["intensity"]), (200, True, "담백"))
        self.assertEqual(j["aspects"]["sound"]["used"], "다큐 감성형")
        code, j = self.call("/api/style/mix_pick", {"name": msg.DRAFT_NAME, "aspect": "intro", "source": {"kind": "preset", "name": "담백 레슨형"}})
        self.assertEqual(j["aspects"]["intro"]["used"], "담백 레슨형")
        self.assertEqual(j["history"][0]["aspect"], "intro")
        code, j = self.call("/api/style/mix_pick", {"name": msg.DRAFT_NAME, "aspect": "x", "source": {}})
        self.assertEqual(code, 400)
        code, j = self.call("/api/style/presets")
        self.assertEqual([x["name"] for x in j["mixes"]], [msg.DRAFT_NAME])

    def test_msg_route_validation(self):
        code, j = self.call("/api/edit/msg", {"name": "없는 영상.mp4", "styles": [{"kind": "preset", "name": "예능 MSG형"}]})
        self.assertEqual(code, 400)
        code, j = self.call("/api/edit/msg", {"name": "../x.mp4", "styles": []})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
