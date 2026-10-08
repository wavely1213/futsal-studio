"""편집실 '전체 영상 군더더기 정리'·'원본 전체로 되돌리기' (editor.html tidySeq·fullSeq, node 로 그대로 실행).

예전에는 V1·A1 을 영상 전체 기준으로 다시 깔아 쇼츠가 롱폼(영상 전체)이 되고, 스타일 가편집의 컷 리듬·확대·빠르기·전환이 사라지고,
제목·강조 글씨·표시·위 트랙은 옛 시각에 남아 엉뚱한 장면 위에 떴다. 이제는 편집본 구조를 그대로 두고 타임라인을 당기거나 민다.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def _js():
    src = (ROOT / "editor.html").read_text(encoding="utf-8")
    out = []
    for name in ("const FPS =", "const EPS =", "const nid =", "const sp =", "const iEnd =", "const iLen =", "const mt =", "const tlOf ="):
        i = src.index(name)
        out.append(src[i:src.index("\n", i)])
    i = src.index("function newPair(")
    out.append(src[i:src.index("\n}", i) + 2])
    i = src.index("/* ---------- 군더더기 정리 · 원본 전체로")
    out.append(src[i:src.index("/* ---------- /군더더기 정리 ---------- */", i)])
    return "\n".join(out)


def pair(a, b, start, speed=1.0, link=None, fx=None, **kw):
    link = link or f"L{a}"
    base = {"media": "main", "start": start, "in": a, "out": b, "speed": speed, "rev": False, "link": link}
    v = dict(base, id=f"v{a}", track="V1", reframe=0.3, fit="auto", fx=fx or {}, color={}, **kw)
    au = dict(base, id=f"a{a}", track="A1", fx={}, gain=-2, fadeIn=0.2, fadeOut=0.3, mute=False)
    return [v, au]


def seq(items, **kw):
    q = {"id": "s", "format": "shorts", "items": items, "trans": [], "titles": [], "markers": [], "shapes": []}
    q.update(kw)
    return q


def end(it):
    return it["start"] + (it["out"] - it["in"]) / (it.get("speed") or 1)


@unittest.skipUnless(NODE, "node 가 없어요")
class TidyRemapTest(unittest.TestCase):
    def run_js(self, calls):
        prog = _js() + "\nconst inp = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
            "process.stdout.write(JSON.stringify(inp.map(c => c.op === 'tidy' ? tidySeq(c.q, c.keep) : fullSeq(c.q, c.lo, c.hi))));"
        r = subprocess.run([NODE, "-e", prog], input=json.dumps(calls), capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def main_v(self, res):
        return sorted((i for i in res["items"] if i["track"] == "V1" and i["media"] == "main"), key=lambda i: i["start"])

    def assert_contiguous(self, items, t0=0.0):
        t = t0
        for it in items:
            self.assertAlmostEqual(it["start"], t, places=4, msg=it)
            t = end(it)
        return t

    def short(self):
        """쇼츠: [10,20] 확대 · [25,35] 1.25배속 + 위 트랙·제목·강조 글씨·표시·전환."""
        zoom = {"scale": {"v": 100, "k": [{"t": 11.0, "v": 100, "e": "lin"}, {"t": 19.0, "v": 120, "e": "lin"}]}}
        items = pair(10, 20, 0.0, fx=zoom) + pair(25, 35, 10.0, speed=1.25)
        items.append({"id": "b", "track": "V2", "media": "m2", "start": 9.0, "in": 0.0, "out": 6.0, "speed": 1, "rev": False, "fx": {}})
        return seq(items, trans=[{"id": "t1", "track": "V1", "a": "v10", "b": "v25", "type": "dissolve", "dur": 0.5, "align": "center"}],
                   titles=[{"id": "h", "text": "훅", "start": 0.0, "dur": 18.0},
                           {"id": "e1", "text": "꿀팁!", "start": 2.5, "dur": 1.5, "plan": "emphasis"},
                           {"id": "e2", "text": "포인트!", "start": 6.0, "dur": 1.5, "plan": "emphasis"}],
                   markers=[{"id": "m", "t": 12.0}], shapes=[{"id": "sh", "start": 1.0, "dur": 3.0}, {"id": "full", "full": True, "start": 0, "dur": 1}])

    def test_tidy_keeps_shorts_structure(self):
        # 원본 12~14 가 군더더기 (첫 클립 가운데) · 그 밖은 남김
        res, = self.run_js([{"op": "tidy", "q": self.short(), "keep": [{"in": 0, "out": 12}, {"in": 14, "out": 200}]}])
        self.assertAlmostEqual(res["cut"], 2.0, places=4)
        v = self.main_v(res)
        self.assertEqual([(x["in"], x["out"]) for x in v], [(10, 12), (14, 20), (25, 35)])
        self.assertAlmostEqual(self.assert_contiguous(v), 16.0, places=4)  # 쇼츠 구간 안에서만 줄어듦 (영상 전체가 아님)
        self.assertEqual(v[0]["id"], "v10")
        for x in v[:2]:  # 확대(원본 시각 키프레임)·화면 위치가 두 조각 모두에 그대로
            self.assertEqual(x["fx"]["scale"]["k"][1]["v"], 120)
            self.assertEqual(x["reframe"], 0.3)
        self.assertEqual(v[2]["speed"], 1.25)
        a = sorted((i for i in res["items"] if i["track"] == "A1"), key=lambda i: i["start"])
        self.assertEqual([(x["link"], x["start"]) for x in a], [(x["link"], x["start"]) for x in v])  # 소리도 같은 조각·같은 짝
        self.assertEqual(len({x["link"] for x in v}), 3)
        self.assertEqual((a[0]["fadeIn"], a[0]["fadeOut"], a[1]["fadeIn"], a[1]["fadeOut"]), (0.2, 0, 0, 0.3))
        b = next(i for i in res["items"] if i["id"] == "b")  # 위 트랙: 같이 당김 (빠진 곳이 없으니 길이 그대로)
        self.assertAlmostEqual(b["start"], 7.0, places=4)
        self.assertEqual(b["out"], 6.0)
        self.assertEqual(res["trans"][0]["a"], v[1]["id"])  # 전환: 첫 클립의 마지막 조각 ↔ 둘째 클립
        self.assertEqual(res["trans"][0]["b"], "v25")
        t = {x["id"]: x for x in res["titles"]}
        self.assertEqual(set(t), {"h", "e2"})  # 말이 잘려 나간 강조 글씨(2.5초 = 원본 12.5)는 뺌
        self.assertAlmostEqual(t["h"]["dur"], 16.0, places=4)
        self.assertAlmostEqual(t["e2"]["start"], 4.0, places=4)  # 같은 원본(16초) 위
        self.assertAlmostEqual(res["markers"][0]["t"], 10.0, places=4)
        sh = {x["id"]: x for x in res["shapes"]}
        self.assertAlmostEqual(sh["sh"]["start"], 1.0, places=4)
        self.assertAlmostEqual(sh["sh"]["dur"], 1.0, places=4)  # 1~4초 가운데 2~4초가 빠짐
        self.assertTrue(sh["full"]["full"])

    def test_tidy_cuts_upper_track_and_nothing_to_do(self):
        q = self.short()
        q["items"][-1].update(start=1.0, out=6.0)  # 위 트랙이 빠지는 곳(2~4초)을 덮음 → 끝을 줄임
        res, none = self.run_js([{"op": "tidy", "q": q, "keep": [{"in": 0, "out": 12}, {"in": 14, "out": 200}]},
                                 {"op": "tidy", "q": self.short(), "keep": [{"in": 0, "out": 200}]}])
        b = next(i for i in res["items"] if i["id"] == "b")
        self.assertAlmostEqual(b["start"], 1.0, places=4)
        self.assertAlmostEqual(b["out"] - b["in"], 4.0, places=4)  # 1~7초 가운데 2~4초가 빠짐
        self.assertIsNone(none)  # 뺄 군더더기가 없으면 아무것도 안 바꿈

    def test_tidy_tiny_slivers_and_speed(self):
        # 1.25배속 클립 안 원본 30~32 군더더기 → 타임라인 1.6초 · 그 사이 2프레임도 안 남는 조각(30.5~30.53)은 같이 뺌
        res, tail = self.run_js([{"op": "tidy", "q": self.short(), "keep": [{"in": 0, "out": 30}, {"in": 30.5, "out": 30.53}, {"in": 32, "out": 99}]},
                                 {"op": "tidy", "q": self.short(), "keep": [{"in": 0, "out": 30}, {"in": 32, "out": 34.97}]}])
        self.assertEqual([(x["in"], x["out"]) for x in self.main_v(res)], [(10, 20), (25, 30), (32, 35)])
        self.assertAlmostEqual(res["cut"], 1.6, places=4)
        self.assertEqual([(x["in"], x["out"]) for x in self.main_v(tail)], [(10, 20), (25, 30), (32, 34.97)])
        self.assertAlmostEqual(tail["cut"], 1.6 + 0.03 / 1.25, places=4)

    def test_tidy_reversed_clip(self):
        q = seq(pair(10, 20, 0.0))
        q["items"][0]["rev"] = q["items"][1]["rev"] = True  # 뒤집은 클립: 타임라인 0초 = 원본 20초
        res, = self.run_js([{"op": "tidy", "q": q, "keep": [{"in": 0, "out": 12}, {"in": 14, "out": 99}]}])
        v = self.main_v(res)
        self.assertEqual([(x["in"], x["out"], x["start"]) for x in v], [(14, 20, 0), (10, 12, 6)])
        self.assertTrue(all(x["rev"] for x in v))

    def test_full_short_stays_in_its_range(self):
        q = self.short()
        res, = self.run_js([{"op": "full", "q": q, "lo": None, "hi": 999}])
        v = self.main_v(res)
        self.assertEqual([(x["in"], x["out"]) for x in v], [(10, 20), (20, 25), (25, 35)])  # 쇼츠 구간 10~35 만 (영상 전체 아님)
        self.assertAlmostEqual(self.assert_contiguous(v), 23.0, places=4)
        self.assertEqual(v[0]["fx"]["scale"]["k"][1]["v"], 120)  # 남아 있던 확대·빠르기 그대로
        self.assertEqual(v[2]["speed"], 1.25)
        self.assertEqual(v[1]["reframe"], 0.3)  # 다시 넣은 장면도 쇼츠 화면 위치는 이웃과 같게
        self.assertAlmostEqual(res["added"], 5.0, places=4)
        t = {x["id"]: x for x in res["titles"]}
        self.assertAlmostEqual(t["h"]["dur"], 23.0, places=4)  # 쇼츠 내내 뜨던 훅 제목은 끝까지
        self.assertEqual((t["e1"]["dur"], t["e2"]["dur"]), (1.5, 1.5))  # 짧은 강조 글씨는 늘리지 않음
        tr = res["trans"][0]  # 사이에 장면이 들어가 두 클립이 붙어 있지 않음 → 화면의 fixTrans 가 뺌
        self.assertEqual((tr["a"], tr["b"]), ("v10", "v25"))
        self.assertGreater(abs(end(v[0]) - v[2]["start"]), 0.02)
        self.assertAlmostEqual(res["markers"][0]["t"], 17.0, places=4)
        b = next(i for i in res["items"] if i["id"] == "b")
        self.assertAlmostEqual(b["start"], 9.0, places=4)  # 9초(첫 클립 안) — 그 뒤에 넣은 것과 무관
        self.assertEqual(b["out"], 6.0)

    def test_full_long_drops_teaser_and_fills_to_end(self):
        items = pair(100, 103, 0.0) + pair(0, 50, 3.0) + pair(52, 120, 53.0)
        q = seq(items, format="long", titles=[{"id": "tz", "text": "티저", "start": 0, "dur": 3, "plan": "teaser"},
                                              {"id": "e", "text": "강조!", "start": 60.0, "dur": 1.5, "plan": "emphasis"},
                                              {"id": "all", "text": "끝까지", "start": 3.0, "dur": 118.0}])
        res, = self.run_js([{"op": "full", "q": q, "lo": 0, "hi": 130.0}])
        v = self.main_v(res)
        self.assertEqual([(x["in"], x["out"]) for x in v], [(0, 50), (50, 52), (52, 120), (120, 130)])
        self.assertAlmostEqual(self.assert_contiguous(v), 130.0, places=4)
        t = {x["id"]: x for x in res["titles"]}
        self.assertNotIn("tz", t)  # 티저 장면을 뺐으니 티저 제목도
        self.assertAlmostEqual(t["e"]["start"], 59.0, places=4)  # 원본 59초 그대로 (옛 60초 = 원본 59)
        self.assertAlmostEqual(t["all"]["start"], 0.0, places=4)
        self.assertAlmostEqual(t["all"]["dur"], 120.0, places=4)  # 끝까지 덮던 제목: 다시 넣은 50~52 만큼 (끝 120~130 은 그 뒤)
        self.assertEqual(len([i for i in res["items"] if i["track"] == "A1"]), 4)

    def test_full_then_tidy_round_trip(self):
        q = self.short()
        full, = self.run_js([{"op": "full", "q": q, "lo": None, "hi": 0}])
        back, = self.run_js([{"op": "tidy", "q": dict(q, **{k: full[k] for k in ("items", "trans", "titles", "markers", "shapes")}),
                              "keep": [{"in": 0, "out": 20}, {"in": 25, "out": 99}]}])
        v = self.main_v(back)
        self.assertEqual([(x["in"], x["out"], round(x["start"], 4)) for x in v], [(10, 20, 0.0), (25, 35, 10.0)])
        self.assertEqual({x["id"]: round(x["start"], 4) for x in back["titles"]}, {"h": 0.0, "e1": 2.5, "e2": 6.0})
        self.assertAlmostEqual(back["markers"][0]["t"], 12.0, places=4)


class ButtonWiringTest(unittest.TestCase):
    def test_no_silent_long_switch(self):
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        i = src.index('$("recTidy").onclick')
        body = src[i:src.index("document.querySelectorAll(\".evrow\")", i)]
        self.assertNotIn('S.format = "long"', body)  # 쇼츠를 말없이 롱폼으로 바꾸지 않음
        self.assertIn("tidySeq(S, r.tidy)", body)
        self.assertIn('fullSeq(S, S.format === "shorts" ? null : 0', body)


if __name__ == "__main__":
    unittest.main()
