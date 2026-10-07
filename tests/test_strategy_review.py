"""채널 전략 검토 보강 시험 (2026-10-07 검토 항목) — 인터넷은 쓰지 않는다.

새로 고침: 막힌 뒤·RSS 만 받은 채널은 목록 기준으로 다시 받을 차례 · 그날 구독자가 아니면 기록에 남기지 않음 · 처음 막혀도 조사 숫자 유지 ·
인터넷 끊김은 6시간 쉬지 않음 · 없는 채널은 연달아 실패로 세지 않음 · 429 는 막힘 · 채널 하나의 뜻밖의 오류는 그 채널만 · 원제 목록이 막히면 바로 쉼 ·
'이 채널만'은 10분 안에 받은 것만 건너뜀 · [지금 다시 시도] · RSS 응답이 끊김.
실행: 저장소 폴더에서 python3 -m unittest tests.test_strategy_review
"""
import http.client
import json
import math
import statistics
import sys
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import strategy  # noqa: E402
from tests.test_strategy import DAY, OWN_ID, Base, info, rss_xml, vid  # noqa: E402

CHOSAL = "UCl4yBpHVaZsjxwXwnYj4kDA"   # 비교 데이터에 있는 채널 (쪼살 · 구독자 10,500)
A, B_, C_ = "UCaaaaaaaaaaaaaaaaaaaaaa", "UCbbbbbbbbbbbbbbbbbbbbbb", "UCcccccccccccccccccccccc"
NET_ERR = "ERROR: [youtube:tab] x: Unable to download API page: <urlopen error [Errno -3] Temporary failure in name resolution>"


class RefreshRobust(Base):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.calls = []

    def lister(self, fail=None):
        def fake(url, kind, limit, log=None, lang=None, sleep_requests=None):
            self.calls.append((url, kind, limit, lang))
            if fail:
                err = fail(url, kind, lang)
                if err:
                    raise err
            cid = next((c for c in (OWN_ID, A, B_, C_, CHOSAL) if c in url), A)
            k = "s" if kind == "shorts" else "l"
            rows = [(vid(i, cid[3] + k), f"풋살 기술 {i} 꿀팁", None if lang else 1000 * (i + 1), None if kind == "shorts" else 300) for i in range(8)]
            return info(cid, "@h" + cid[3], "채널 " + cid[3], None if lang else 5000, rows)
        return fake

    def rss_ok(self, cid):
        ents = [(vid(i, cid[3] + "s"), f"풋살 기술 {i}", self.now - (i + 1) * 3 * DAY, True, 2000 + i, 20) for i in range(4)]
        return strategy.parse_rss(rss_xml(cid, ents)), None

    def run_refresh(self, mode="normal", keys=None, fail=None, rss=None):
        with mock.patch.object(core, "channel_listing", self.lister(fail)), \
                mock.patch.object(strategy, "fetch_rss", side_effect=rss or self.rss_ok):
            return strategy.refresh(keys, mode, log=lambda m: None)

    def test_blocked_rss_only_is_listed_again_after_pause(self):
        """막혀서 RSS 만 받은 채널: 기록 줄에 구독자를 남기지 않고, 쉬는 시간이 끝나면 '새로 고침'이 다시 목록을 받음."""
        self.set_competitors([self.comp(A, "에이"), self.comp(B_, "비")])
        blk = lambda url, kind, lang: RuntimeError(core.BLOCKED_MSG) if OWN_ID in url else None  # noqa: E731
        r = self.run_refresh(fail=blk)
        self.assertTrue(r["blocked"])
        self.assertEqual((r["listed"], r["rssOnly"]), (0, 3))
        for k in ("own", A, B_):
            ch = strategy.load_channel(k)
            self.assertIsNone(strategy.listed_at(ch))
            self.assertTrue(ch["rss"]["ok"])
        hist = strategy.read_history()
        self.assertTrue(all(r_["subs"] is None and r_["listed"] is False for v in hist.values() for r_ in v))
        # 쉬는 동안: RSS 를 막 받았으니 건너뜀
        self.assertEqual(strategy.plan_refresh(None, "normal"), [])
        # 쉬는 시간이 끝나면: 목록을 받은 적이 없으니 모두 다시 받을 차례 · 화면도 '오래됨'
        strategy._update_state(lambda d: d.__setitem__("pause", dict(d["pause"], until=time.time() - 1)))
        self.assertEqual(len(strategy.plan_refresh(None, "normal")), 3)
        ov = strategy.overview()
        self.assertEqual(ov["refresh"]["staleN"], 3)
        self.assertTrue(ov["refresh"]["ownStale"])
        self.calls.clear()
        r = self.run_refresh()
        self.assertEqual((r["listed"], r["blocked"]), (3, False))
        self.assertTrue(any(OWN_ID in c[0] for c in self.calls))
        self.assertEqual(strategy.read_history()["own"][-1]["subs"], 5000)

    def test_first_blocked_refresh_keeps_seed_numbers(self):
        """처음 새로 고침이 막혀도 비교 데이터의 구독자·영상이 사라지지 않음 (그 채널은 '조사 자료' 그대로 · 날짜만 새로)."""
        self.set_competitors([self.comp(CHOSAL, "쪼살")])
        before = strategy.channel_stats(strategy.known_channels()[CHOSAL])
        blk = lambda url, kind, lang: RuntimeError(core.BLOCKED_MSG)  # noqa: E731
        self.run_refresh(keys=[CHOSAL, "own"], fail=blk)
        ch = strategy.load_channel(CHOSAL)
        self.assertEqual((ch["subs"], ch["src"]), (10500, "seed"))
        s = strategy.channel_stats(strategy.known_channels()[CHOSAL])
        self.assertEqual((s["subs"], s["L"]["median"], s["S"]["n"] >= before["S"]["n"]), (before["subs"], before["L"]["median"], True))
        self.assertTrue(s["life"]["complete"] == before["life"]["complete"])
        self.assertTrue(s["cadence"])  # RSS 날짜는 새로
        self.assertFalse(any("fullAt" in t for t in ch["tabs"].values()))  # 처음 목록은 전체 훑기
        strategy._update_state(lambda d: d.__setitem__("pause", None))
        self.calls.clear()
        self.run_refresh(keys=[CHOSAL])
        self.assertEqual(self.calls[0][2], strategy.FULL_LIMIT)
        self.assertEqual(strategy.load_channel(CHOSAL)["src"], "live")

    def test_network_down_does_not_pause_six_hours(self):
        """인터넷이 끊기면: 두 곳 연달아 실패한 뒤 이번 새로 고침만 멈추고, 6시간 쉬지 않음 · 결과에 net."""
        self.set_competitors([self.comp(A, "에이"), self.comp(B_, "비"), self.comp(C_, "씨")])
        net = lambda url, kind, lang: RuntimeError(NET_ERR)  # noqa: E731
        r = self.run_refresh(fail=net, rss=lambda cid: (None, strategy.NET_MSG))
        self.assertTrue(r["net"])
        self.assertEqual((r["done"], r["failed"]), (0, 2))
        self.assertIsNone(strategy.load_state()["pause"])
        self.assertEqual(r["reason"], None)
        self.assertFalse(r["paused"])

    def test_gone_channels_do_not_pause_and_429_is_a_block(self):
        self.set_competitors([self.comp(A, "에이"), self.comp(B_, "비"), self.comp(C_, "씨")])
        gone = lambda url, kind, lang: RuntimeError("ERROR: [youtube:tab] This channel does not exist.") if OWN_ID not in url else None  # noqa: E731
        r = self.run_refresh(fail=gone)
        self.assertFalse(r["paused"])
        self.assertIsNone(strategy.load_state()["pause"])
        self.assertEqual(strategy.load_channel(A)["errors"][-1]["error"], "채널을 찾지 못했어요")
        rate = lambda url, kind, lang: RuntimeError("ERROR: Unable to download webpage: HTTP Error 429: Too Many Requests")  # noqa: E731
        r = self.run_refresh("all", fail=rate)
        self.assertTrue(r["blocked"])
        self.assertEqual(strategy.load_state()["pause"]["reason"], "blocked")

    def test_unexpected_error_in_one_channel_continues(self):
        """채널 하나 저장이 잠겨 실패해도 그 채널만 실패 · 나머지는 받음 (작업 전체가 멈추지 않음)."""
        self.set_competitors([self.comp(A, "에이"), self.comp(B_, "비")])
        real = strategy.save_channel

        def locked(data):
            if data["key"] == A:
                raise PermissionError("백신이 잡고 있음")
            return real(data)
        with mock.patch.object(strategy, "save_channel", locked):
            r = self.run_refresh()
        self.assertEqual((r["done"], r["failed"]), (2, 1))
        self.assertIsNotNone(strategy.load_channel(B_))

    def test_pause_store_busy_does_not_stop_refresh(self):
        self.set_competitors([self.comp(A, "에이")])
        blk = lambda url, kind, lang: RuntimeError(core.BLOCKED_MSG)  # noqa: E731
        with mock.patch.object(strategy, "_update_state", side_effect=strategy.StoreBusy("잠김")):
            r = self.run_refresh(fail=blk)
        self.assertEqual(r["done"], 2)

    def test_ko_titles_block_pauses_right_away(self):
        """원제(한국어) 목록에서 막히면 바로 쉼 → 다음 채널은 목록을 묻지 않음 (엔진 최신화를 두 번 하지 않게)."""
        self.set_competitors([self.comp(A, "에이"), self.comp(B_, "비")])

        def fake(url, kind, limit, log=None, lang=None, sleep_requests=None):
            self.calls.append((url, kind, limit, lang))
            if lang:
                raise RuntimeError(core.BLOCKED_MSG)
            cid = A if A in url else B_ if B_ in url else OWN_ID
            rows = [(vid(i, cid[3] + kind[0]), f"Futsal tip {i}", 1000 * (i + 1), 300) for i in range(8)]
            return info(cid, "@h" + cid[3], "c", 5000, rows)
        with mock.patch.object(core, "channel_listing", fake), mock.patch.object(strategy, "fetch_rss", side_effect=lambda cid: (
                strategy.parse_rss(rss_xml(cid, [(vid(1, cid[3] + "v"), "풋살 기술 하나", self.now - DAY, False, 10, 1)])), None)):
            r = strategy.refresh([A, B_], "normal", log=lambda m: None)
        self.assertTrue(r["blocked"])
        self.assertFalse(any(B_ in c[0] for c in self.calls))
        self.assertEqual(strategy.load_state()["pause"]["reason"], "blocked")

    def test_this_channel_only_uses_short_gap(self):
        """'이 채널만 새로 고침'은 하루 전에 받은 채널도 다시 받음 (10분 안에 받은 것만 건너뜀)."""
        self.set_competitors([self.comp(A, "에이")])
        self.run_refresh(keys=[A])
        self.assertEqual(strategy.plan_refresh([A], "normal"), [])
        ch = strategy.load_channel(A)
        ch["listedAt"] -= DAY
        strategy.save_channel(ch)
        self.assertEqual(len(strategy.plan_refresh([A], "normal")), 1)
        self.assertEqual(len(strategy.plan_refresh(None, "normal")), 1)  # 우리 채널만 (A 는 3일 안)

    def test_clear_pause_only_for_fails(self):
        strategy._set_pause("fails", time.time())
        self.assertLessEqual(strategy.load_state()["pause"]["until"], time.time() + strategy.FAIL_PAUSE + 1)
        strategy.clear_pause()
        self.assertIsNone(strategy.load_state()["pause"])
        strategy._set_pause("blocked", time.time())
        with self.assertRaises(strategy.StrategyError):
            strategy.clear_pause()

    def test_fetch_rss_incomplete_read(self):
        class Resp:
            def read(self, n=-1):
                raise http.client.IncompleteRead(b"<feed")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None, context=None: Resp()):
            self.assertEqual(strategy.fetch_rss(A), (None, "RSS 를 받지 못했어요"))
        with mock.patch.object(urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertEqual(strategy.fetch_rss(A), (None, strategy.NET_MSG))

    def test_error_kinds(self):
        self.assertEqual(strategy._err_kind(NET_ERR), "net")
        self.assertEqual(strategy._err_kind("HTTP Error 429: Too Many Requests"), "blocked")
        self.assertEqual(strategy._err_kind("Sign in to confirm you're not a bot"), "blocked")
        self.assertEqual(strategy._err_kind("ERROR: This channel does not exist."), "gone")
        self.assertEqual(strategy._err_kind("이상한 오류"), "fail")


# ---------- 점검 ----------

def band(at, s0=7710, d10=6, d90=40):
    """예측 띠: 주마다 p10 +d10 · p50 +20 · p90 +d90 (index 0 = 1주 뒤)."""
    return {"at": at, "s0": s0, "p10": [s0 + d10 * (i + 1) for i in range(26)], "p50": [s0 + 20 * (i + 1) for i in range(26)],
            "p90": [s0 + d90 * (i + 1) for i in range(26)], "milestones": []}


FC = {"kpi": {"videoMedian": {"L": {"p25": 3000, "p50": 5000, "p75": 8000}, "S": {"p25": 2000, "p50": 4000, "p75": 7000}}},
      "trajectory": {k: [7710 + 10 * i for i in range(52)] for k in ("p10", "p50", "p90")}, "milestones": []}


class CheckupReview(Base):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.fc = mock.patch.object(strategy, "forecast_result", return_value=FC)
        self.fc.start()

    def tearDown(self):
        self.fc.stop()
        super().tearDown()

    def weekly(self, subs=7720, vids=None):
        now = self.now
        strategy.save_strategy(dict(strategy.preset("A"), startedAt=now - 21 * DAY))
        vids = vids if vids is not None else {
            vid(1, "L"): {"k": "L", "t": "롱폼 15분 레슨", "v": 1800, "pub": now - 3 * DAY, "d": 900},
            vid(2, "S"): {"k": "S", "t": "쇼츠 하나", "v": 900, "pub": now - 2 * DAY},
            vid(3, "S"): {"k": "S", "t": "쇼츠 둘", "v": 1500, "pub": now - 4 * DAY},
            vid(4, "S"): {"k": "S", "t": "쇼츠 셋", "v": 700, "pub": now - 6 * DAY}}
        self.own_channel(now, subs=subs, vids=vids)
        strategy.append_history({"k": "own", "at": now - 7 * DAY, "subs": 7710})
        strategy.append_history({"k": "own", "at": now, "subs": subs})
        strategy._save_checkups([{"id": "p", "at": now - 7 * DAY, "forecastBand": band(now - 7 * DAY), "good": [], "bad": []}])

    def test_weekly_checkup_on_plan_is_not_negative(self):
        """계획대로 올린 주간 점검: 아직 덜 쌓인 롱폼으로 '반응이 약해요'·'롱폼 줄이기'가 나오지 않고, 띠는 1주째로 비교 (반올림 단위 넣음)."""
        self.weekly()
        rec = strategy.evaluate(now=self.now)
        self.assertEqual(rec["bad"], [])
        self.assertFalse(any("8분" in x for x in rec["change"]))
        self.assertEqual((rec["band"]["week"], rec["band"]["p10"], rec["band"]["state"]), (1.0, 7716, "inside"))
        self.assertEqual(rec["actual"]["median"]["L"]["n"], 0)  # 3일 된 롱폼은 조회수 비교에 넣지 않음
        self.assertEqual(rec["period"]["days"], 7)
        self.assertTrue(any("계획대로" in x for x in rec["good"]))

    def test_rounded_subs_count_as_inside(self):
        """7,710명으로 보이면 실제는 7,710~7,719명 → 띠 아래 끝 7,716명이면 '안'."""
        self.assertEqual(strategy._band_state(7710, {"p10": 7716, "p90": 7800}), "inside")
        self.assertEqual(strategy._band_state(7700, {"p10": 7716, "p90": 7800}), "below")
        self.assertEqual(strategy._band_state(12300, {"p10": 12350, "p90": 13000}), "inside")  # 1만 넘으면 100명 단위
        self.assertEqual(strategy.sub_step(950), 1)

    def test_band_interpolates_by_days(self):
        b = strategy.band_at(band(0), 3.5 * DAY)
        self.assertEqual((b["week"], b["p10"], b["p90"]), (0.5, 7713, 7730))
        self.assertEqual(strategy.band_at(band(0), 2 * 7 * DAY)["p10"], 7722)
        self.assertIsNone(strategy.band_at(band(0), -DAY))

    def test_checkup_twice_replaces_and_no_false_band(self):
        """바로 다시 점검: 기록이 쌓이지 않고 바뀜 · 기간은 그 전 점검부터 · 띠는 5일 넘게 지나야 비교 · 거짓 '예상보다 적게' 없음."""
        self.weekly(subs=7710)
        with mock.patch.object(strategy, "refresh", return_value={"ok": True}):
            r1 = strategy.checkup(log=lambda m: None)["checkup"]
            r2 = strategy.checkup(log=lambda m: None)["checkup"]
        items = strategy.load_checkups()
        self.assertEqual(len(items), 2)  # 7일 전 + 이번 (두 번째는 첫 번째를 바꿈)
        self.assertTrue(r2.get("replaced"))
        self.assertEqual(r1["period"]["from"], r2["period"]["from"])
        self.assertFalse(any("예상 범위보다 적게" in x for x in r1["bad"] + r2["bad"]))
        # 3일 지난 점검 뒤 바로: 기간이 짧으면 올린 개수·띠는 판단하지 않음
        strategy._save_checkups([{"id": "a", "at": self.now - 4 * DAY, "forecastBand": band(self.now - 4 * DAY), "good": [], "bad": []}])
        rec = strategy.evaluate(now=self.now)
        self.assertTrue(rec["period"]["short"])
        self.assertIsNone(rec["band"])
        self.assertFalse(any("계획" in x for x in rec["bad"]))
        self.assertTrue(rec["notes"])

    def test_first_save_starts_today_not_on_revive_day(self):
        strategy._update_state(lambda d: d["own"].update(revivedAt=self.now - 19 * DAY))
        saved = strategy.save_strategy(strategy.preset("A"))
        self.assertLess(self.now - saved["startedAt"], 60)
        self.assertFalse(strategy.remind()["due"])
        rec = strategy.evaluate(now=self.now + DAY)
        self.assertEqual(rec["bad"], [])  # 시작하기 전 기간을 '적게 올렸어요'로 판단하지 않음
        self.assertIsNone(rec["subs"]["delta"])  # 시작할 때 기록이 없으면 구독자 변화를 지어내지 않음
        self.assertTrue(any("다음 점검부터" in n for n in rec["notes"]))

    def test_view_kpi_states_use_age_not_window(self):
        """조회수는 기간이 아니라 영상 나이로: 시작한 뒤 올린 롱폼 중 14일 지난 것 · P25 아래만 '안 되는 것' · P50 위면 '잘 되는 것'."""
        now = self.now
        mk = lambda vs: {vid(i, "L"): {"k": "L", "t": f"롱폼 {i}", "v": v, "pub": now - (15 + i) * DAY, "d": 400} for i, v in enumerate(vs)}  # noqa: E731
        for vs, state in (([1000, 1500, 2000], "down"), ([3500, 4000, 4500], "mid"), ([6000, 7000, 9000], "up")):
            self.weekly(vids=mk(vs))
            rec = strategy.evaluate(now=now)
            self.assertEqual(rec["actual"]["median"]["L"]["state"], state, vs)
            txt = " ".join(rec["good"] + rec["bad"])
            self.assertEqual("최소 목표" in txt, state == "down")
            self.assertEqual("가운데 예상(" in txt, state == "up")

    def test_studio_subs_used_when_period_matches(self):
        self.weekly()
        rec = strategy.evaluate(studio={"subs": 13, "views": 900, "days": 7}, now=self.now)
        self.assertEqual((rec["subs"]["delta"], rec["subs"]["src"]), (13, "studio"))
        rec = strategy.evaluate(studio={"subs": 40, "days": 28}, now=self.now)
        self.assertEqual((rec["subs"]["delta"], rec["subs"]["src"]), (10, "rounded"))  # 기간이 다르면 참고로만

    def test_short_title_word_boundary(self):
        t = strategy._short_title("전 풋살 국가대표 (강원FS 최경진 감독의 원포인트 레슨) 발바닥 드래그", 20)
        self.assertTrue(t.endswith("…"))
        self.assertEqual(t.count("("), t.count(")"))
        self.assertNotIn("감", t[-3:])

    def test_forward_check_skips_first_days_and_uses_step(self):
        items = [{"at": 0, "forecastBand": band(0)}]
        hist = [{"at": 2 * DAY, "subs": 7700}, {"at": 7 * DAY, "subs": 7710}]
        fw = strategy.forward_check(items, hist, now=8 * DAY)
        self.assertEqual((fw["n"], fw["inside"]), (1, 1))


# ---------- 가져올 점 품질 ----------

class TakeawayQuality(Base):
    def chan(self, key, name, subs, rows, group="풋살 특화"):
        """경쟁 채널 자료 바로 저장: rows = [(제목, 조회수)] 최신순 (롱폼)."""
        data = strategy._new_channel(self.comp(key, name, group))
        ids = [vid(i, key[3] + "t") for i in range(len(rows))]
        data.update(subs=subs, at=time.time(), listedAt=time.time(), src="live",
                    videos={i: {"k": "L", "t": t, "v": v, "d": 300} for i, (t, v) in zip(ids, rows)})
        data["tabs"] = {"long": {"ids": ids, "complete": True, "n": len(ids)}, "shorts": {"ids": [], "complete": True, "n": 0}}
        strategy.save_channel(data)

    def test_formula_regexes(self):
        auth, target = strategy.FORMULA_RX["auth"], strategy.FORMULA_RX["target"]
        for t in ("비선출이 이정도까지", "프로선수들의 풋살화는??", "선출 볼 뺏고 싶다면?!"):
            self.assertFalse(auth.search(t), t)
        self.assertTrue(auth.search("前 국가대표 감독이 알려주는 킥"))
        self.assertFalse(target.search("프로 선출 플랩 프로레벨을 만났어요"))
        self.assertTrue(target.search("풋살 초보라면 꼭"))

    def test_topics_need_two_channels_or_futsal_term_and_skip_stopwords(self):
        rows_a, rows_b = [], []
        for i in range(30):
            rows_a.append((f"트래핑 감각 키우기 {i}" if i % 4 == 0 else f"황희찬 출신 경기 {i}" if i % 4 == 1 else f"그냥 하이라이트 {i}", 9000 if i % 4 < 2 else 1000))
            rows_b.append((f"트래핑 감각 연습 {i}" if i % 3 == 0 else f"평범한 일상 {i}", 9000 if i % 3 == 0 else 1000))
        self.chan(A, "에이", 9000, rows_a)
        self.chan(B_, "비", 8000, rows_b)
        self.set_competitors([self.comp(A, "에이"), self.comp(B_, "비")])
        tks = strategy.takeaways()
        topics = {t["pattern"].split(":", 1)[1]: t for t in tks if t["rule"] == "R-TOPIC"}
        self.assertIn("트래핑", topics)
        self.assertEqual(topics["트래핑"]["nChannels"], 2)
        for bad in ("황희찬", "출신", "하이라이트"):
            self.assertNotIn(bad, topics)

    def test_de_aged_multiplier(self):
        """오래된 영상일수록 조회가 많은 채널: 오래된 쪽에만 쓴 공식은 '비슷한 때 올린 영상'과 견주면 배수가 거의 1."""
        rows = [(("꿀팁 " if i >= 10 else "경기 ") + str(i), 100 * (i + 1)) for i in range(20)]
        raw = math.exp(statistics.median(math.log(v) for t, v in rows if "꿀팁" in t) - statistics.median(math.log(v) for t, v in rows if "꿀팁" not in t))
        self.assertGreater(raw, 2.5)
        self.assertLess(strategy.formula_stats(rows)["howto"]["mult"], 1.6)

    def test_series_name_without_open_bracket(self):
        rows = [("패스 어디까지 해봤니 (2탄", 5000, vid(1)), ("패스 어디까지 해봤니 (3탄)", 6000, vid(2)), ("경기", 100, vid(3))]
        names = {s["name"] for s in strategy.series_stats(rows)}
        self.assertIn("패스 어디까지 해봤니 N탄", names)

    def test_research_tip_parts(self):
        parts = strategy.tip_parts("롱폼에서 핵심 1포인트를 잘라 쇼츠로(훅 자막, 화살표 라벨), 업로드는 거의 멈춤. 가장 직접적인 경쟁 채널")
        self.assertEqual(parts, ["롱폼에서 핵심 1포인트를 잘라 쇼츠로(훅 자막, 화살표 라벨)"])
        self.assertEqual(strategy.tip_parts("풋살 방향전환 쇼츠 9.2만 회. N가지 + 브랜드 태그 제목"), ["N가지 + 브랜드 태그 제목"])
        self.assertEqual(strategy.tip_parts("구독자의 9배가 넘는 조회수, 쇼츠 100만 회대"), [])

    def test_scores_spread_and_caps(self):
        """비교 데이터: 조사 메모는 '중'까지 · 맨 뒤 · 한 채널뿐인 제목·주제 규칙은 '상'이 아님 · 근거는 센 것부터."""
        tks = strategy.takeaways()
        notes = [t for t in tks if t["note"]]
        self.assertTrue(notes)
        self.assertTrue(all(t["fit"]["score"] <= strategy.TIP_MAX and t["rule"] == "R-RESEARCH-TIP" for t in notes))
        self.assertEqual(tks.index(notes[0]), len([t for t in tks if not t["note"] and not t["hidden"]]))
        for t in tks:
            if t["rule"] in strategy.MINED and (t["nChannels"] < 2 or t["nVideos"] < 5):
                self.assertLessEqual(t["fit"]["score"], strategy.THIN_MAX, t["text"])
            for x in ("업로드는 거의 멈춤", "경쟁 채널", "회대"):
                self.assertNotIn(x, t["text"])
            self.assertEqual(t["text"].count("("), t["text"].count(")"), t["text"])  # 괄호 안에서 잘린 조각 없음
        top = [t for t in tks if t["fit"]["score"] >= 95]
        self.assertTrue(all(t["nChannels"] >= 3 for t in top), [(t["text"], t["nChannels"]) for t in top])  # 꼭대기 점수는 여러 채널 근거만
        self.assertLess(len(top), len(tks) / 4)

    def test_plan_met_rules(self):
        self.assertTrue(strategy._plan_met("R-SHORTS-RATIO", {"L": 1, "S": 3}))
        self.assertFalse(strategy._plan_met("R-SHORTS-RATIO", {"L": 1, "S": 1}))
        self.assertTrue(strategy._plan_met("R-CADENCE", {"L": 1, "S": 1}))


# ---------- 성공 솔루션 ----------

class SolutionReview(Base):
    def test_priorities_have_no_percent_and_shorts_advice_consistent(self):
        sens = [{"id": "L+1", "d12": 20, "raw12": 0.2, "text": "롱폼…", "small": False}, {"id": "u+", "d12": 20, "raw12": 0.2, "text": "조회…", "small": False},
                {"id": "c+", "d12": 15, "raw12": 0.15, "text": "전환…", "small": False}, {"id": "S+2", "d12": 0, "raw12": 0.01, "text": "쇼츠…", "small": True}]
        st = strategy.load_state()
        tks = strategy.takeaways(st)
        sol = strategy.solution(st, tks, {"kpi": {}, "sensitivity": sens})
        self.assertTrue(sol["priorities"])
        for x in sol["priorities"]:
            self.assertNotIn("impact", x)
            self.assertNotEqual(x["category"], "쇼츠 운영")  # 쇼츠 +2 가 거의 차이 없으면 쇼츠 늘리기 조언을 하지 않음
        self.assertEqual({x["id"] for x in sol["scenarios"]}, {"L+1", "u+", "c+"})
        self.assertIn("쇼츠는 노출용", sol["note"])
        self.assertEqual(sol["leverMax"], {"u+": 20, "c+": 15})
        self.assertIsNone(strategy.LEVER["수익화·레슨 연계"][0])  # 레슨 문의 경로는 구독 전환으로 셈하지 않음
        self.assertFalse(any(t.get("planMet") for p in sol["phases"] for t in p["todos"]))


# ---------- 가능성 연결 · 캐시 · 메모리 ----------

class ForecastGlueReview(Base):
    def test_inputs_do_not_change_with_clock(self):
        strategy._update_state(lambda d: d.__setitem__("competitors", d["competitors"][:3]))
        t = time.time()
        h1 = strategy._fc_hash(strategy.forecast_inputs(now=t))
        strategy._ANA.clear()
        h2 = strategy._fc_hash(strategy.forecast_inputs(now=t + 3 * 3600))
        self.assertEqual(h1, h2)

    def test_forecast_computed_once_for_parallel_requests(self):
        import threading
        calls = []
        real = strategy.forecast.run

        def slow(*a, **k):
            calls.append(1)
            time.sleep(0.3)
            return real(*a, **k)
        with mock.patch.object(strategy.forecast, "run", slow):
            ths = [threading.Thread(target=strategy.forecast_result) for _ in range(3)]
            for x in ths:
                x.start()
            for x in ths:
                x.join()
        self.assertEqual(len(calls), 1)

    def test_solution_view_writes_cache_not_state(self):
        strategy.solution_view()
        files = {p.name for p in (self.work / "strategy").iterdir()}
        self.assertNotIn("state.json", files)
        self.assertIn("solution.json", files)
        self.assertFalse(strategy.solution_view()["changed"])
        strategy.save_strategy(dict(strategy.preset("A"), formats=[{"name": "롱폼", "kind": "long", "perWeek": 2}]))
        self.assertTrue(strategy.solution_view()["changed"])

    def test_slim_cache_keeps_recent_only(self):
        now = time.time()
        vids = {vid(i, "L"): {"k": "L", "t": f"롱폼 {i}", "v": 100 + i, "seen": now, "vAt": now, "te": "en"} for i in range(300)}
        self.own_channel(now, vids=vids, rss_ids=[])
        full = strategy.load_channel("own")
        slim = strategy.live_channels()["own"]
        self.assertEqual(len(full["videos"]), 300)
        self.assertEqual(len(slim["tabs"]["long"]["ids"]), strategy.SLIM_IDS)
        self.assertEqual(len(slim["videos"]), strategy.SLIM_IDS)  # 최근 120개 (+ RSS 영상)만
        self.assertTrue(all("seen" not in v and "te" not in v for v in slim["videos"].values()))
        self.assertNotIn(str(strategy._chan_path("own")), strategy._CACHE)  # 원본은 기억하지 않음
        slim["tabs"]["long"]["complete"] = False
        self.assertEqual(strategy.channel_stats(slim)["L"]["n"], 300)

    def test_preview_and_pace(self):
        r = strategy.forecast_preview(2, 3)
        self.assertEqual(r["rates"], {"L": 2.0, "S": 3.0})
        self.assertTrue(r["m12"]["text"])
        with self.assertRaises(strategy.StrategyError):
            strategy.forecast_preview("많이", 1)
        with self.assertRaises(strategy.StrategyError):
            strategy.forecast_preview(99, 1)
        now = time.time()
        vids = {vid(1, "S"): {"k": "S", "t": "쇼츠", "v": 900, "pub": now - 3 * DAY}, vid(2, "S"): {"k": "S", "t": "쇼츠2", "v": 900, "pub": now - 10 * DAY},
                vid(3, "L"): {"k": "L", "t": "롱폼", "v": 900, "pub": now - 60 * DAY}}
        oc = self.own_channel(now, vids=vids)
        self.assertEqual(strategy.own_pace(oc, now), {"L": 0.0, "S": 0.5})
        oc["rss"]["ok"] = False
        self.assertIsNone(strategy.own_pace(oc, now))


class RoutesReview(Base):
    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.tmp / "studio.log")]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.p2:
            p.stop()
        super().tearDown()

    def call(self, path, body=None):
        import urllib.error
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_get_forecast_does_not_write_user_state(self):
        self.assertEqual(self.call("/api/strategy/forecast")[0], 200)
        files = {p.name for p in (self.work / "strategy").iterdir()}
        self.assertNotIn("state.json", files)
        self.assertTrue({"forecast.json", "solution.json"} <= files)

    def test_preview_and_pause_routes(self):
        code, j = self.call("/api/strategy/preview?L=2&S=3")
        self.assertEqual((code, j["rates"]), (200, {"L": 2.0, "S": 3.0}))
        self.assertEqual(self.call("/api/strategy/preview?L=x&S=1")[0], 400)
        strategy._set_pause("fails", time.time())
        self.assertEqual(self.call("/api/strategy/pause", {})[0], 200)
        strategy._set_pause("blocked", time.time())
        self.assertEqual(self.call("/api/strategy/pause", {})[0], 400)


if __name__ == "__main__":
    unittest.main()
