"""컷 리듬 맞추기 (#7) — 저장소 폴더에서 python3 -m unittest tests.test_rhythm

단어 시각이 있는 코치 영상 받아쓰기(합성)로 스타일 가편집을 만들어 봄: 긴 말 컷을 배운 컷 길이로 단어 경계에서 나누고
(번갈아 확대), 말이 촘촘한 곳만 조금 빠르게. 영상 파일·인터넷·받아쓰기 모델은 쓰지 않음."""
import json
import random
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402
import style  # noqa: E402

NAME = "코치 설명 영상.mp4"
SYL = "가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허고노도로모보소오조초코토포호구누두루무부수우주추쿠투푸후"
DEMO = [(52.0, 60.0), (121.0, 129.0)]  # 시범(말이 드문 구간) — 앞뒤로 1.2초 쉼


def coach_transcript(seed=7):
    """약 3분짜리 코치 설명: 촘촘한 말(문장 사이 0.2~0.35초) · 25초쯤마다 1.2초 쉼 · 시범 구간 둘(8초에 단어 3개)."""
    rnd = random.Random(seed)
    segs, t, k, since = [], 0.5, 0, 0.0
    demo = list(DEMO)
    while t < 185.0:
        if demo and t >= demo[0][0] - 1.2:  # 시범 앞뒤로 쉼
            a, b = demo.pop(0)
            ws, x = [], a + 0.3
            for j in range(3):
                w = f"자{SYL[(k + j) % len(SYL)]}"
                ws.append({"w": w, "s": round(x, 2), "e": round(x + 0.4, 2)})
                x += 2.6
            segs.append({"start": a, "end": b, "text": " ".join(w["w"] for w in ws), "words": ws})
            t, since, k = b + 1.2, 0.0, k + 1
            continue
        n, ws, x = rnd.randint(6, 10), [], t
        for j in range(n):
            ln = rnd.randint(2, 3)
            w = (f"{k + 1}번" if j == 0 else "") + "".join(SYL[rnd.randrange(len(SYL))] for _ in range(ln))
            d = 0.12 * len(w) + rnd.uniform(0.0, 0.08)
            ws.append({"w": w, "s": round(x, 2), "e": round(x + d, 2)})
            x += d + (rnd.uniform(-0.03, 0.0) if rnd.random() < 0.15 else rnd.uniform(0.02, 0.12))  # 가끔 단어끼리 살짝 겹침
        segs.append({"start": ws[0]["s"], "end": ws[-1]["e"], "text": " ".join(w["w"] for w in ws), "words": ws})
        k += 1
        since += ws[-1]["e"] - t
        t = ws[-1]["e"] + (1.2 if since > 25 else rnd.uniform(0.2, 0.35))
        if since > 25:
            since = 0.0
    return segs


def ref_events(med=3.0, head=None, tail=None, cps=None, dur=300.0, seed=3):
    """레퍼런스 영상 기록 (컷 길이는 med 둘레로 흩어짐 · 도입/마무리만 다르게도)."""
    rnd = random.Random(seed)
    cuts, t = [], 0.0
    while True:
        m = head if head and t < 30 else tail if tail and t > dur - 20 else med
        t += m * rnd.uniform(0.75, 1.3)
        if t >= dur - 0.5:
            break
        cuts.append(round(t, 2))
    sil = [[round(x, 2), round(x + 0.3 + 0.2 * rnd.random(), 2)] for x in range(5, int(dur) - 5, 7)]
    return {"source": "레퍼런스.mp4", "duration": dur, "cuts": cuts, "zooms": [{"t": c, "scale": 1.2, "ox": 0, "oy": 0} for c in cuts[::4]],
            "silences": sil, "lufs": -14.0, "text": [], "capColors": [],
            "speech": {"talk": 200.0, "chars": 200.0 * cps} if cps else None}


class RhythmBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="컷 리듬 테스트 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        ps = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(style, "STYLES", work / "styles")]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.segs = coach_transcript()
        core.adir(NAME).mkdir(parents=True, exist_ok=True)
        (core.adir(NAME) / "transcript.json").write_text(json.dumps(self.segs, ensure_ascii=False), encoding="utf-8")
        self.words = sorted((w["s"], w["e"]) for s in self.segs for w in s["words"])
        self.dur = self.segs[-1]["end"] + 1.0
        self.info = {"duration": self.dur, "width": 1920, "height": 1080, "fps": 30.0}
        self.coach = editor._coach_cps(self.segs)

    def seq(self, params, kinds=("long",)):
        return editor.auto_sequences(NAME, self.info, params, kinds)

    def proj(self):
        return {"source": NAME, "info": self.info, "captions": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in self.segs],
                "media": [{"id": "main", "kind": "video", "src": "videos", "file": NAME, "dur": self.dur, "w": 1920, "h": 1080, "fps": 30.0,
                           "audio": True}]}

    @staticmethod
    def v1(seq):
        return sorted((it for it in seq["items"] if it["track"] == "V1"), key=lambda x: x["start"])

    @staticmethod
    def cover(seq):
        out = []
        for it in sorted((it for it in seq["items"] if it["track"] == "V1"), key=lambda x: x["in"]):
            if out and it["in"] <= out[-1][1] + 1e-3:
                out[-1][1] = max(out[-1][1], it["out"])
            else:
                out.append([it["in"], it["out"]])
        return out


class TestSplitRhythm(RhythmBase):
    def setUp(self):
        super().setUp()
        self.ref = style.summarize(ref_events(3.0, cps=self.coach * 1.06))
        self.params = style.edit_params(self.ref)
        self.after = self.seq(self.params)[0]
        self.before = self.seq(dict(self.params, splitShot=0, tempo=0))[0]

    def test_params(self):
        p = self.params
        self.assertEqual(p["splitShot"], p["targetShot"])
        self.assertEqual(len(p["curve3"]), 3)
        self.assertAlmostEqual(p["tempo"], round(self.coach * 1.06, 2), delta=0.01)
        self.assertIn("도입 30초는", style.describe(self.ref))
        self.assertRegex(style.describe(self.ref), r"도입 30초는 [\d.]+초마다 컷")

    def test_split_points_at_word_boundaries(self):
        v = self.v1(self.after)
        splits = [b["in"] for a, b in zip(v, v[1:]) if abs(a["out"] - b["in"]) < 1e-6]
        self.assertGreater(len(splits), 20, "긴 말 컷이 나뉨")
        for x in splits:
            inside = [(s, e) for s, e in self.words if s + 0.05 < x < e - 0.05]
            self.assertFalse(inside, f"{x}초에서 단어를 자름 {inside}")
            prev = max((e for s, e in self.words if s < x - 0.05), default=None)  # 바로 앞 단어 끝 · 바로 뒤 단어 시작
            nxt = min((s for s, e in self.words if e > x + 0.05), default=None)
            self.assertIsNotNone(prev)
            self.assertIsNotNone(nxt)
            self.assertTrue(prev - 0.05 <= x <= nxt + 0.05, (x, prev, nxt))

    def test_alternating_framing(self):
        v = self.v1(self.after)
        sc = [float(((it.get("fx") or {}).get("scale") or {}).get("v", 100.0)) for it in v]
        for k, (x, y) in enumerate(zip(v, v[1:])):
            if abs(x["out"] - y["in"]) < 1e-6:  # 나눈 곳: 원래 크기 ↔ 확대 번갈아
                self.assertNotEqual(sc[k], sc[k + 1])
        for k, it in enumerate(v):  # 원래 컷의 첫 조각(안 나눈 컷·시범 포함)은 원래 화면 그대로
            if k == 0 or abs(v[k - 1]["out"] - it["in"]) >= 1e-6:
                self.assertEqual(sc[k], 100.0, it)
        self.assertEqual(max(sc), round(self.params["zoomScale"] * 100, 1))

    def test_no_zoom_style_stays_mild(self):
        """확대 컷이 없는 레퍼런스: 안 나눈 컷은 그대로, 나눈 곳만 살짝(1.08배) — 스타일 설명과 어긋나지 않게."""
        ev = ref_events(3.0, cps=self.coach * 1.06)
        ev["zooms"] = []
        ref = style.summarize(ev)
        p = style.edit_params(ref)
        self.assertEqual(p["zoomEvery"], 0)
        self.assertIn("확대 컷은 거의 없어요", style.describe(ref))
        self.assertIn("살짝(1.08배)", style.describe(ref))
        v = self.v1(self.seq(p)[0])
        sc = [float(((it.get("fx") or {}).get("scale") or {}).get("v", 100.0)) for it in v]
        self.assertEqual(set(sc), {100.0, 108.0})
        whole = [k for k, it in enumerate(v) if (k == 0 or abs(v[k - 1]["out"] - it["in"]) >= 1e-6)
                 and (k == len(v) - 1 or abs(it["out"] - v[k + 1]["in"]) >= 1e-6)]
        self.assertTrue(whole, "안 나눈 컷도 있음")
        self.assertTrue(all(sc[k] == 100.0 for k in whole), "안 나눈 컷은 확대 안 함")

    def test_median_shot_near_target(self):
        prof = style.profile_from_sequence(self.proj(), self.after)
        t = self.params["targetShot"]
        self.assertLessEqual(abs(prof["medianShot"] - t) / t, 0.2, (prof["medianShot"], t))

    def test_shot_distance_lower(self):
        pb = style.profile_from_sequence(self.proj(), self.before)
        pa = style.profile_from_sequence(self.proj(), self.after)
        db, da = style._w1log(self.ref["shotDeciles"], pb["shotDeciles"]), style._w1log(self.ref["shotDeciles"], pa["shotDeciles"])
        self.assertLess(da, db, (pb["shotDeciles"], pa["shotDeciles"]))
        self.assertGreater(style.distance(self.ref, pa)["parts"]["컷 리듬"], style.distance(self.ref, pb)["parts"]["컷 리듬"])

    def test_all_words_kept(self):
        cb, ca = self.cover(self.before), self.cover(self.after)
        self.assertEqual(len(cb), len(ca))
        for (a, b), (c, d) in zip(cb, ca):
            self.assertAlmostEqual(a, c, delta=1e-3)
            self.assertAlmostEqual(b, d, delta=1e-3)
        kept = [(s, e) for s, e in self.words if any(a - 1e-3 <= s and e <= b + 1e-3 for a, b in cb)]
        self.assertGreater(len(kept), 0.95 * len(self.words))
        v = self.v1(self.after)
        for s, e in kept:  # 단어 하나는 한 컷 안에 (0.05초까지)
            self.assertTrue(any(it["in"] - 0.05 <= s and e <= it["out"] + 0.05 for it in v), (s, e))
        a1 = sorted((it for it in self.after["items"] if it["track"] == "A1"), key=lambda x: x["start"])
        self.assertEqual([(x["in"], x["out"], x["start"], x.get("speed")) for x in a1], [(x["in"], x["out"], x["start"], x.get("speed")) for x in v])

    def test_speed_range_and_demo_untouched(self):
        f = min(1.12, max(1.0, round(self.coach * 1.06, 2) / self.coach))
        v = self.v1(self.after)
        for it in v:
            sp = float(it.get("speed") or 1.0)
            self.assertTrue(1.0 <= sp <= 1.12, sp)
            mid = (it["in"] + it["out"]) / 2
            if any(a <= mid <= b for a, b in DEMO):
                self.assertEqual(sp, 1.0, "시범 부분은 그대로")
        self.assertTrue(any(any(a <= (it["in"] + it["out"]) / 2 <= b for a, b in DEMO) for it in v), "시범도 가편집에 남음")
        talk = [float(it.get("speed") or 1.0) for it in v if not any(a - 1 <= it["in"] and it["out"] <= b + 1 for a, b in DEMO)]
        self.assertGreater(sum(abs(x - round(f, 3)) < 1e-6 for x in talk), 0.9 * len(talk))
        for a, b in zip(v, v[1:]):  # 타임라인은 빈틈·겹침 없이
            self.assertAlmostEqual(editor.i_end(a), b["start"], delta=1e-3)

    def test_demo_cut_not_split(self):
        v = self.v1(self.after)
        for a, b in DEMO:  # 시범(첫 단어 ~ 마지막 단어)은 앞 말 컷에 붙어 있어도 한 컷 그대로
            ws = [w for s in self.segs if s["start"] == a for w in s["words"]]
            inside = [it for it in v if it["in"] < ws[-1]["e"] and it["out"] > ws[0]["s"]]
            self.assertEqual(len(inside), 1, "말이 드문 시범은 한 컷 그대로")
            self.assertEqual(float(inside[0].get("speed") or 1.0), 1.0)


class TestTempo(RhythmBase):
    def speeds(self, prof):
        return [float(it.get("speed") or 1.0) for it in self.seq(style.edit_params(prof), ("long", "shorts"))[0]["items"]]

    def test_clamp(self):
        fast = style.summarize(ref_events(3.0, cps=self.coach * 1.5))
        sp = self.speeds(fast)
        self.assertEqual(max(sp), 1.12)
        self.assertTrue(all(1.0 <= x <= 1.12 for x in sp))
        slow = style.summarize(ref_events(3.0, cps=self.coach * 0.8))
        self.assertTrue(all(x == 1.0 for x in self.speeds(slow)), "코치님이 더 빠르면 그대로")
        off = dict(fast, tempoOn=False)
        self.assertEqual(style.edit_params(off)["tempo"], 0)
        self.assertTrue(all(x == 1.0 for x in self.speeds(off)), "끄면 원본 빠르기")

    def test_shorts_too(self):
        fast = style.summarize(ref_events(3.0, cps=self.coach * 1.5))
        shorts = self.seq(style.edit_params(fast), ("shorts",))
        self.assertTrue(shorts)
        for q in shorts:
            items = [it for it in q["items"] if it["track"] == "V1"]
            self.assertTrue(all(1.0 <= float(it.get("speed") or 1.0) <= 1.12 for it in items))
            self.assertAlmostEqual(q["titles"][0]["dur"], round(editor.seq_total(q), 2), delta=0.01, msg="제목은 끝까지")

    def test_no_style_unchanged(self):
        """스타일 없이 만든 가편집(그냥 가편집)은 예전 그대로: 나누지 않고 빠르기 1."""
        rough = self.seq(None)[0]
        self.assertTrue(all(float(it.get("speed") or 1.0) == 1.0 for it in rough["items"]))
        self.assertTrue(all(not (it.get("fx") or {}).get("scale") for it in rough["items"]))
        rec = editor.recommend(NAME)
        self.assertEqual(len(self.v1(rough)), len(rec["tidy"]))


class TestCurve3(RhythmBase):
    def test_summarize_curve3(self):
        ev = {"duration": 100.0, "cuts": [2.0 * k for k in range(1, 15)] + [30.0 + 5 * k for k in range(1, 10)] + [80.0 + 4 * k for k in range(1, 5)],
              "silences": [], "text": [], "capColors": []}
        self.assertEqual(style.summarize(ev)["curve3"], [2.0, 5.0, 4.0])
        short = style.summarize({"duration": 8.0, "cuts": [], "silences": []})  # 컷이 없으면 전체 길이 하나
        self.assertEqual(short["curve3"], [8.0, 8.0, 8.0])

    def test_merge_old_profiles(self):
        a = style.summarize(ref_events(2.0, dur=100.0))
        b = style.summarize(ref_events(4.0, dur=300.0, seed=5))
        m = style.merge([a, b])  # 기록 없이 합치기: 길이만큼 무게
        for j in range(3):
            self.assertAlmostEqual(m["curve3"][j], (a["curve3"][j] * 100 + b["curve3"][j] * 300) / 400, delta=0.01)
        old = {k: v for k, v in a.items() if k != "curve3"}
        self.assertNotIn("curve3", style.merge([old, dict(old)]))
        self.assertEqual(style.edit_params(old)["curve3"], [old["medianShot"]] * 3)

    def test_intro_follows_head(self):
        ref = style.summarize(ref_events(4.0, head=1.8, tail=3.0))
        p = style.edit_params(ref)
        self.assertLess(p["curve3"][0], p["curve3"][1])
        self.assertIn(f"도입 30초는 {p['curve3'][0]}초마다 컷", style.describe(ref))
        seq = self.seq(dict(p, tempo=0))[0]
        v = self.v1(seq)
        head = [editor.i_len(it) for it in v if editor.i_end(it) <= 30.0]
        body = [editor.i_len(it) for it in v if 40.0 <= it["start"] and editor.i_end(it) <= editor.seq_total(seq) - 25]
        md = lambda x: sorted(x)[len(x) // 2]  # noqa: E731
        self.assertLessEqual(abs(md(head) - p["curve3"][0]) / p["curve3"][0], 0.25, head)
        self.assertLessEqual(abs(md(body) - p["curve3"][1]) / p["curve3"][1], 0.25, body)

    def test_split_without_words_falls_back(self):
        """단어 시각이 없는 예전 받아쓰기: 나누지 않고 예전 줌 컷 규칙 그대로."""
        segs = [{k: v for k, v in s.items() if k != "words"} for s in self.segs]
        (core.adir(NAME) / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        p = style.edit_params(style.summarize(ref_events(3.0)))
        got = self.v1(self.seq(dict(p, tempo=0))[0])
        want = editor._punch(editor.recommend(NAME, keep_pause=p["keepPause"])["tidy"], segs, p["zoomEvery"])
        self.assertEqual([(it["in"], it["out"]) for it in got], [(c["in"], c["out"]) for c in want])


class TestTempoAudio(unittest.TestCase):
    """빠르게 한 컷이 이어질 때 내보내기 소리에 무음 틈(틱 소리)이 없어야 함 — atempo 가 끝을 몇 ms 덜 내보냄."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="말 빠르기 소리 "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(editor, "ASSETS", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        r = core.run([core.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=10",
                      "-ac", "2", str(self.tmp / "톤 소리.wav")])
        self.assertEqual(r.returncode, 0)
        self.media = {"main": {"id": "main", "file": "톤 소리.wav", "src": "assets", "audio": True, "dur": 10.0, "kind": "audio"}}

    def mix(self, sp, rev=False):
        import numpy as np
        cuts = [{"in": 1.0, "out": 3.137, "speed": sp}, {"in": 3.137, "out": 5.5, "speed": sp}, {"in": 5.5, "out": 8.0, "speed": sp}]
        items = [x for x in editor._items_from_cuts(cuts) if x["track"] == "A1"]
        for x in items:
            x["rev"] = rev
        seq = {"tracks": editor.default_tracks(), "items": items, "trans": []}
        tot = max(editor.i_end(i) for i in items)
        out = self.tmp / f"mix_{sp}_{rev}"
        out.mkdir()
        editor._mix_audio_impl(seq, self.media, 0.0, tot, out, [], lambda *a, **k: None, None)
        x = np.fromfile(out / "dialog.f32", np.float32).reshape(-1, 2)[:, 0]
        return x, [editor.i_end(i) for i in items[:-1]]

    def zero_runs(self, x):
        import numpy as np
        z = np.abs(x[480:-480]) < 1e-4  # 처음·끝 10ms 빼고
        runs, k = [], 0
        while k < len(z):
            if z[k]:
                j = k
                while j < len(z) and z[j]:
                    j += 1
                if j - k > 8:  # 220Hz 톤이 0을 지나는 몇 샘플은 괜찮음
                    runs.append(((k + 480) / editor.SR, j - k))
                k = j
            else:
                k += 1
        return runs

    def test_no_gap_between_sped_up_cuts(self):
        import numpy as np
        for rev in (False, True):
            x, bounds = self.mix(1.12, rev)
            self.assertEqual(self.zero_runs(x), [], f"빠르게 한 컷 사이 무음 틈 (rev={rev})")
            for t in bounds:  # 이은 곳에서 소리가 겹쳐 커지지도 않음
                i = int(t * editor.SR)
                self.assertLess(float(np.max(np.abs(x[i - 480:i + 480]))), 1.05 * float(np.max(np.abs(x[48000:52000]))))
        x, _ = self.mix(1.0)
        self.assertEqual(self.zero_runs(x), [])


class TestTempoSwitch(RhythmBase):
    def test_set_tempo_and_route(self):
        from http.server import ThreadingHTTPServer
        import app
        style.STYLES.mkdir(parents=True, exist_ok=True)
        prof = style.summarize(ref_events(3.0, cps=8.0))
        (style.STYLES / "빠른 스타일.json").write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        def call(body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/style/tempo", method="POST", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        with mock.patch.object(app, "PORT", port):
            self.assertEqual(call({"name": "빠른 스타일", "on": False}), (200, {"ok": True}))
            st = next(s for s in style.list_styles() if s["name"] == "빠른 스타일")
            self.assertEqual((st["profile"]["tempoOn"], st["params"]["tempo"]), (False, 0))
            self.assertEqual(call({"name": "빠른 스타일", "on": True})[0], 200)
            st = next(s for s in style.list_styles() if s["name"] == "빠른 스타일")
            self.assertEqual(st["params"]["tempo"], 8.0)
            code, j = call({"name": "없는 스타일", "on": True})
            self.assertEqual((code, j["error"]), (404, "스타일을 찾지 못했어요"))
            self.assertEqual(call({"name": "..\\..\\config", "on": True})[0], 404)
        self.assertFalse(list(style.STYLES.glob("*.tmp")))

    def test_relearn_keeps_switch(self):
        style.STYLES.mkdir(parents=True, exist_ok=True)
        (style.STYLES / "다시 배울 스타일.json").write_text(json.dumps({"tempoOn": False}), encoding="utf-8")
        ev = ref_events(3.0, cps=8.0)
        with mock.patch.object(style, "extract_events", return_value=ev):
            r = style.learn("다시 배울 스타일", ["레퍼런스.mp4"], lambda m: None)
        self.assertIs(r["profile"]["tempoOn"], False)
        self.assertEqual(r["params"]["tempo"], 0)


if __name__ == "__main__":
    unittest.main()
