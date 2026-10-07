"""채널 전략 '가능성(%)' 모델(forecast.py) 테스트 — 정확한 숫자는 비교하지 않는다 (numpy 판에 따라 흐름이 달라질 수 있음).

알려진 매개변수로 만든 가짜 비교 채널 40곳에서 a·b·τ·σ·cL·cS 되찾기 + 뒤로 빼고 시험한 적중률 70~90% · 단조성(공통 난수:
구독자 목표 확률은 주당 개수가 늘면 줄지 않음 · 많이 올리면 한 편 보통 조회는 늘지 않음) · 범위(0≤lo≤p≤hi≤100, 5 단위, 극단 문구, 이미 달성) ·
같은 입력 → 같은 출력 · 민감도 부호 · 새 채널 · 비교 채널 없음/1곳 · 이상한 값 · 기록 거꾸로 시험의 퍼짐 배수 [1,2] · 올리지 않으면 · numpy 없음.
실행: 저장소 폴더에서 python3 -m unittest tests.test_strategy_forecast
"""
import builtins
import math
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import forecast  # noqa: E402

TRUE = {"L": (1.6, 0.73, 1.3, 0.85), "S": (4.9, 0.44, 1.3, 0.95)}  # a, b, τ, σ
CL, CS = 0.008, 0.0002
BOOT, SIMS = 20, 40


def make_peers(n=40, seed=7, vids=30):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        subs = math.exp(rng.uniform(math.log(500), math.log(2e6)))
        p = {"key": f"p{i}", "group": "풋살 특화" if i % 2 else "축구 레슨·기술", "subs": subs}
        for f in ("L", "S"):
            a, b, tau, sig = TRUE[f]
            mu = a + b * math.log(subs) + rng.gauss(0, tau)
            p[f] = [math.exp(mu + rng.gauss(0, sig)) for _ in range(vids)]
        x = rng.uniform(0.1, 30)
        y = (CL + CS * x) * math.exp(rng.gauss(0, 0.1))
        L = subs / y
        p["life"] = {"L": L, "S": x * L, "complete": True}
        out.append(p)
    return out


def own(subs=7710):
    vids = [{"k": "S", "v": v, "age": a} for v, a in ((1522, 17), (8631, 19), (1912, 230), (2675, 400), (30553, 401), (9532, 425))]
    vids += [{"k": "L", "v": v, "age": a} for v, a in ((12463, 398), (2852, 404))]
    return {"subs": subs, "videos": vids, "life": {"L": 544635, "S": 441244, "complete": True}, "g0": 0}


class Calibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.peers = make_peers()
        cls.cal = forecast.calibrate(cls.peers, "풋살 특화", boot=BOOT)

    def test_recovers_reach_parameters(self):
        for f in ("L", "S"):
            a, b, tau, sig = TRUE[f]
            c = self.cal[f]
            self.assertFalse(c["defaults"])
            self.assertLess(abs(c["b"] - b), 0.3, f)
            self.assertLess(abs((c["a"] + c["b"] * 10) - (a + b * 10)), 0.6, f)  # 구독자 2.2만 근처 예측
            self.assertTrue(0.9 <= c["tau"] <= 1.7, (f, c["tau"]))
            self.assertLess(abs(c["sigma"] - sig), 0.15, (f, c["sigma"]))

    def test_recovers_conversion(self):
        self.assertFalse(self.cal["conv_defaults"])
        self.assertLess(abs(self.cal["cL"] / CL - 1), 0.3)
        self.assertLess(abs(self.cal["cS"] / CS - 1), 0.3)

    def test_loo_coverage_near_80(self):
        for f in ("L", "S"):
            hit, n = self.cal[f]["loo"]
            self.assertEqual(n, 40)
            self.assertTrue(0.7 <= hit / n <= 0.9, (f, hit, n))

    def test_bootstrap_has_spread(self):
        bs = [x[1] for x in self.cal["boot"]["L"]]
        self.assertEqual(len(bs), BOOT)
        self.assertGreater(max(bs) - min(bs), 0.01)


def zero_slope(cal, s0=7710):
    """b = 0 · e = 0 으로 바꾼 보정값 (되먹임 없이 구조만 볼 때)."""
    c = {**cal, "boot": dict(cal["boot"])}
    for f in ("L", "S"):
        c["boot"][f] = [(a + b * math.log(s0), 0.0, t, s, adj) for a, b, t, s, adj in cal["boot"][f]]
        c[f] = dict(cal[f], e=0.0)
    return c


class Simulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.peers = make_peers()
        cls.cal = forecast.calibrate(cls.peers, "풋살 특화", boot=BOOT)

    def p_goal(self, cal, plan, goal=10000, wk=forecast.H12, **kw):
        dr = forecast.draws(BOOT, SIMS, {"L": 4, "S": 10})
        sim = forecast.simulate(cal, own(), plan, dr, **kw)
        return float((sim["traj"][wk] >= goal).mean()), sim

    def test_monotonic_in_shorts_and_long(self):
        for cal, tol in ((self.cal, 0.02), (zero_slope(self.cal), 0.0)):
            ps = [self.p_goal(cal, {"L": 1, "S": s})[0] for s in (0, 1, 2, 4, 8)]
            pl = [self.p_goal(cal, {"L": r, "S": 3})[0] for r in (0, 0.5, 1, 2, 3)]
            for seq in (ps, pl):
                self.assertTrue(all(b >= a - tol for a, b in zip(seq, seq[1:])), seq)
            self.assertGreater(pl[-1], pl[0])

    def test_per_video_median_not_increasing_with_more_uploads(self):
        """e ≤ 0: 많이 올리면 한 편 보통 조회는 늘지 않음 (화면 KPI '영상 보통 조회수')."""
        cal = dict(self.cal, L=dict(self.cal["L"], e=-0.2))
        meds = [float(self.p_goal(cal, {"L": r, "S": 3})[1]["med0"]["L"].mean()) for r in (0.5, 1, 2, 3)]
        self.assertTrue(all(b <= a + 1e-9 for a, b in zip(meds, meds[1:])), meds)

    def test_no_uploads_no_growth_unless_baseline(self):
        p, sim = self.p_goal(self.cal, {"L": 0, "S": 0})
        self.assertEqual(p, 0.0)
        self.assertTrue((sim["traj"][-1] == 7710).all())
        p2, _ = self.p_goal(self.cal, {"L": 0, "S": 0}, g0=100.0)
        self.assertEqual(p2, 1.0)


class RunOutput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.peers = make_peers()
        cls.out = forecast.run(cls.peers, own(), {"L": 1, "S": 3}, {"m12": 15000}, boot=BOOT, sims=SIMS)

    def test_ranges_and_labels(self):
        ms = self.out["milestones"]
        self.assertTrue(any(m.get("user") for m in ms))  # 우리 목표 줄
        for m in ms:
            if m.get("none"):
                continue
            self.assertTrue(0 <= m["lo"] <= m["p"] <= m["hi"] <= 100, m)
            self.assertTrue(all(x % 5 == 0 for x in (m["lo"], m["p"], m["hi"])), m)
            self.assertIn(m["confidence"], ("높음", "보통", "낮음"))
            if m["text"].startswith("약"):
                self.assertTrue(5 <= m["p"] <= 95)
        one = next(m for m in ms if m["id"] == "subs1000_6")
        self.assertTrue(one["achieved"])
        big = next(m for m in ms if m["id"] == "subs100000_6")
        self.assertEqual(big["text"], "5% 미만")
        tr = self.out["trajectory"]
        self.assertEqual(len(tr["p50"]), 52)
        self.assertTrue(all(a <= b <= c <= d <= e for a, b, c, d, e in zip(tr["p10"], tr["p25"], tr["p50"], tr["p75"], tr["p90"])))
        self.assertIn("명", self.out["subs12"]["text"])
        self.assertTrue(len(self.out["assumptions"]) >= 9)

    def test_same_input_same_output(self):
        again = forecast.run(self.peers, own(), {"L": 1, "S": 3}, {"m12": 15000}, boot=BOOT, sims=SIMS)
        self.assertEqual([(m["id"], m["p"], m["lo"], m["hi"]) for m in again["milestones"]], [(m["id"], m["p"], m["lo"], m["hi"]) for m in self.out["milestones"]])
        self.assertEqual(again["trajectory"], self.out["trajectory"])

    def test_sensitivity_signs(self):
        s = {x["id"]: x for x in self.out["sensitivity"]}
        self.assertEqual(set(s), {"S+2", "L+1", "L/2", "S/2", "u+", "c+"})
        for k in ("S+2", "L+1", "u+", "c+"):
            self.assertGreaterEqual(s[k]["raw12"], -0.02, k)
        for k in ("L/2", "S/2"):
            self.assertLessEqual(s[k]["raw12"], 0.02, k)
        self.assertGreater(s["L+1"]["raw12"], 0)
        self.assertTrue(all(x["text"] for x in self.out["sensitivity"]))
        absd = [abs(x["raw12"]) for x in self.out["sensitivity"]]
        self.assertEqual(absd, sorted(absd, reverse=True))

    def test_calibration_report(self):
        c = self.out["calibration"]
        self.assertEqual((c["n_peers"], c["nL"], c["boot"]), (40, 40, BOOT))
        self.assertFalse(c["defaults_used"])
        self.assertTrue(c["loo"]["L"]["pct"])
        self.assertFalse(c["backtest"]["enough"])
        self.assertEqual(c["inflate"], 1.0)


class EdgeCases(unittest.TestCase):
    def test_new_channel(self):
        out = forecast.run(make_peers(12), {"subs": 0, "videos": [], "life": {}}, {"L": 1, "S": 3}, boot=BOOT, sims=SIMS)
        m = next(x for x in out["milestones"] if x["id"] == "subs1000_12")
        self.assertFalse(m["achieved"])
        self.assertTrue(0 <= m["lo"] <= m["p"] <= m["hi"] <= 100)
        self.assertEqual(out["goal"], 1000)
        self.assertGreaterEqual(out["trajectory"]["p10"][0], 0)

    def test_no_peers_uses_defaults(self):
        out = forecast.run([], own(), {"L": 1, "S": 3}, boot=BOOT, sims=SIMS)
        c = out["calibration"]
        self.assertTrue(c["defaults_used"] and c["conv_defaults"])
        self.assertEqual(c["fit"]["L"]["b"], forecast.DEFAULTS["L"]["b"])
        self.assertTrue(out["milestones"])

    def test_one_peer_blends_with_defaults(self):
        p = make_peers(1)
        cal = forecast.calibrate(p, "풋살 특화", boot=BOOT)
        self.assertTrue(cal["L"]["defaults"])
        self.assertEqual(cal["L"]["b"], forecast.DEFAULTS["L"]["b"])
        self.assertNotEqual(cal["L"]["a"], forecast.DEFAULTS["L"]["a"])  # 그 채널 쪽으로 조금 옮김

    def test_bad_values_are_ignored(self):
        peers = make_peers(10)
        peers[0]["L"] = [None, 0, -5, "많이", float("nan"), True] + peers[0]["L"]
        peers[1]["subs"] = None
        peers[2]["subs"] = 0
        peers[3]["life"] = {"L": 0, "S": None, "complete": True}
        peers.append({"key": "이상", "subs": "x"})
        peers.append("문자")
        o = own()
        o["videos"].append({"k": "L", "v": None, "age": 3})
        out = forecast.run(peers, o, {"L": 99, "S": -3}, boot=BOOT, sims=SIMS)
        self.assertEqual(out["plan"], {"L": forecast.MAX_RATE, "S": 0.0})
        self.assertEqual(out["calibration"]["nL"], 8)

    def test_backtest_inflate_in_range(self):
        peers = make_peers(8)
        for p in peers:
            p["cad"] = {"L": 1, "S": 3}
            p["history"] = [{"at": 0, "subs": 1000}, {"at": 8 * 7 * 86400, "subs": 500000}]  # 모델보다 훨씬 빨리 큼
        bt = forecast.backtest(peers, forecast.calibrate(peers, "풋살 특화", boot=BOOT))
        self.assertTrue(bt["enough"])
        self.assertLess(bt["hit"], 70)
        self.assertTrue(1.0 < bt["inflate"] <= 2.0)
        few = forecast.backtest(peers[:2], forecast.calibrate(peers, "풋살 특화", boot=BOOT))
        self.assertEqual((few["enough"], few["inflate"]), (False, 1.0))

    def test_show_rules(self):
        self.assertEqual(forecast.show(0.03, 0.0, 0.1)["text"], "5% 미만")
        self.assertEqual(forecast.show(0.97, 0.9, 1.0)["text"], "95% 이상")
        d = forecast.show(0.52, 0.1, 0.9)
        self.assertEqual((d["text"], d["p"], d["wide"], d["confidence"]), ("약 50%", 50, True, "낮음"))
        d = forecast.show(0.6, 0.62, 0.58)  # 반올림 뒤에도 lo ≤ p ≤ hi
        self.assertTrue(d["lo"] <= d["p"] <= d["hi"])
        self.assertEqual(forecast.show(0.6, 0.5, 0.7)["confidence"], "높음")
        self.assertEqual(forecast.sig2(9431), 9400)
        self.assertEqual(forecast.sig2(87), 87)

    def test_hash_changes(self):
        h1 = forecast.inputs_hash([{"a": 1}], {"subs": 1}, {"L": 1, "S": 3})
        self.assertEqual(h1, forecast.inputs_hash([{"a": 1}], {"subs": 1}, {"L": 1, "S": 3}))
        self.assertNotEqual(h1, forecast.inputs_hash([{"a": 1}], {"subs": 1}, {"L": 2, "S": 3}))
        self.assertNotEqual(h1, forecast.inputs_hash([{"a": 2}], {"subs": 1}, {"L": 1, "S": 3}))

    def test_numpy_missing_message(self):
        real = builtins.__import__

        def no_np(name, *a, **k):
            if name == "numpy":
                raise ImportError("없음")
            return real(name, *a, **k)
        with mock.patch.object(builtins, "__import__", no_np), mock.patch.dict(sys.modules, {"numpy": None}):
            with self.assertRaises(forecast.ForecastError) as e:
                forecast.run([], own(), {"L": 1, "S": 1})
        self.assertIn("numpy", str(e.exception))
        self.assertIn("다시 실행해 주세요", str(e.exception))

    def test_brier(self):
        self.assertEqual(forecast.brier([(1.0, 1), (0.0, 0)]), 0.0)
        self.assertEqual(forecast.brier([(0.5, 1)]), 0.25)
        self.assertIsNone(forecast.brier([]))


class ReviewForecast(unittest.TestCase):
    """검토 보강: 믿을 만함(근거) · 한 편 빼 보기 · 지금 속도 · 방향 비교 · 반올림한 끝으로 차이 · 쇼츠 전환 범위 끝 · 흔들림·기준점 보정."""

    @classmethod
    def setUpClass(cls):
        cls.peers = make_peers()
        for i, p in enumerate(cls.peers):
            p["cad"] = {"L": 0.25 * (1 + i % 8), "S": 0.5 * (1 + i % 6)}
        cls.out = forecast.run(cls.peers, own(), {"L": 1, "S": 3}, boot=BOOT, sims=SIMS, pace={"L": 0, "S": 0.5},
                               plans={"A": {"L": 1, "S": 3}, "B": {"L": 1, "S": 3}, "C": {"L": 0.5, "S": 5}, "X": {"L": 2, "S": 3}})

    def test_change_text_uses_rounded_endpoints(self):
        self.assertEqual(forecast.change_text("x", "1만", 0.68, 0.66)[0], "x: 1만 약 70% → 거의 차이 없어요")
        self.assertEqual(forecast.change_text("x", "1만", 0.72, 0.685), ("x: 1만 약 70% → 거의 차이 없어요", 0))  # 둘 다 약 70%
        t, d = forecast.change_text("x", "1만", 0.62, 0.70)
        self.assertEqual((t, d), ("x: 1만 약 60% → 약 70% (+10%포인트 안팎)", 10))
        self.assertEqual(forecast.change_text("x", "1만", 0.6, 0.02)[1], -60)
        for s in self.out["sensitivity"]:
            a, b = s["text"].split(": ", 1)[1].split(" → ")[0].split(" ", 1)[1], s["text"].split(" → ")[1]
            self.assertFalse(b.startswith(a) and "포인트" in b, s["text"])  # '약 70% → 약 70% (−5%p)' 같은 줄 없음

    def test_few_own_videos_mean_low_trust_with_reason(self):
        m = next(x for x in self.out["milestones"] if x["id"] == "subs10000_12")
        self.assertEqual(m["confidence"], "낮음")
        self.assertTrue(any("롱폼 근거가 2편" in w for w in m["why"]), m["why"])
        self.assertEqual(self.out["own"]["L"], {"n": 2, "w": 1.0})
        self.assertIn("롱폼 2편", self.out["assumptions"][0])
        self.assertIn("loo", m)  # 우리 롱폼 한 편씩 빼고 다시 계산한 범위
        self.assertIn("한 편을 빼고", m["loo"])

    def test_many_own_videos_can_be_trusted(self):
        o = own()
        o["videos"] = [{"k": k, "v": 3000 + 100 * i, "age": 30 + 20 * i} for i in range(10) for k in ("L", "S")]
        out = forecast.run(self.peers, o, {"L": 1, "S": 3}, boot=BOOT, sims=SIMS)
        ms = [m for m in out["milestones"] if m["kind"] == "subs" and not m["achieved"]]
        self.assertTrue(all(not any("근거가" in w for w in m["why"]) for m in ms))
        self.assertTrue(any(m["confidence"] in ("높음", "보통") for m in ms))

    def test_new_channel_and_no_peers_are_low(self):
        out = forecast.run([], {"subs": 0, "videos": [], "life": {}}, {"L": 1, "S": 3}, boot=BOOT, sims=SIMS)
        live = [m for m in out["milestones"] if not m.get("achieved") and not m.get("none")]
        self.assertTrue(live and all(m["confidence"] == "낮음" for m in live))
        self.assertIn("비교 자료가 없어 기본값으로 계산했어요", out["trustWhy"])
        self.assertNotIn("0곳", out["assumptions"][0])
        self.assertTrue(any("새 채널" in w for w in out["trustWhy"]))

    def test_pace_and_plans_share_luck(self):
        pace = self.out["pace"]
        g12 = next(m for m in self.out["milestones"] if m["id"] == f"subs{self.out['goal']}_12")
        self.assertLessEqual(pace["m12"]["p"], g12["p"])
        self.assertTrue(pace["text"].startswith("지금 속도(롱폼 주 0개 · 쇼츠 주 0.5개)대로면"))
        pl = self.out["plans"]
        self.assertEqual(pl["A"]["m12"]["p"], pl["B"]["m12"]["p"])  # 개수가 같으면 같은 확률 (같은 운)
        self.assertEqual(pl["A"]["m12"]["p"], round(g12["p"]) if not g12.get("loo") else pl["A"]["m12"]["p"])
        self.assertGreaterEqual(pl["X"]["m12"]["p"], pl["A"]["m12"]["p"])  # 롱폼을 더 올리면 줄지 않음
        self.assertIn("지금 속도", self.out["assumptions"][7])
        same = forecast.run(self.peers, own(), {"L": 1, "S": 3}, boot=BOOT, sims=SIMS, pace={"L": 1, "S": 3})
        self.assertNotIn("pace", same)  # 계획과 같은 속도면 따로 보여 주지 않음

    def test_conv_sd_and_elasticity_center_are_fitted(self):
        cal = forecast.calibrate(self.peers, "풋살 특화", boot=BOOT)
        self.assertTrue(forecast.CONV_SD <= cal["conv_sd"] <= forecast.CONV_SD_MAX)
        lc = [math.log(p["cad"]["L"]) for p in self.peers]
        self.assertAlmostEqual(cal["L"]["lc0"], sum(lc) / len(lc), places=6)
        noisy = make_peers(30, seed=3)
        for p in noisy:  # 같은 조회 구성에서 구독 전환만 채널마다 크게 다름 (ln 표준편차 약 1)
            k = math.exp(random.Random(p["key"]).gauss(0, 1.0))
            p["life"]["L"] *= k
            p["life"]["S"] *= k
        self.assertGreater(forecast.calibrate(noisy, "풋살 특화", boot=BOOT)["conv_sd"], 0.6)
        self.assertEqual(forecast.calibrate([], "풋살 특화", boot=BOOT)["conv_sd"], forecast.CONV_SD)

    def test_shorts_conversion_at_bound_is_flagged(self):
        peers = make_peers(20, seed=11)
        for p in peers:  # 쇼츠가 구독과 전혀 상관없게 → 기울기가 범위 끝(0.002%)에 닿음
            p["life"]["S"] = p["life"]["L"] * random.Random(p["key"]).uniform(0.1, 30)
            p["life"]["L"] = p["subs"] / CL
        out = forecast.run(peers, own(), {"L": 0.5, "S": 10}, boot=BOOT, sims=SIMS, plans={"C": {"L": 0.5, "S": 10}})
        c = out["calibration"]
        self.assertTrue(c["convHit"])
        self.assertTrue(any("데이터로 정해지지 않아" in a for a in out["assumptions"]))
        self.assertIn("cS", c["conv_range"])


if __name__ == "__main__":
    unittest.main()
