"""채널 전략(8단계 · strategy.py) 테스트 — 인터넷은 쓰지 않는다 (yt-dlp 목록·RSS 는 가짜 · 고정 자료 tests/fixtures/strategy).

저장(바꿔 끼우기·깨진 파일 .bad·잠기면 안 덮어씀·기록 줄) · RSS 파서(우리·쪼살·쌈바 원본, DOCTYPE·빈·깨진 RSS) · 목록 정리(기본·한국어·탭 묶음·
잘못된 줄) · 새로 고침(채널마다 저장·예절 대기·3일 건너뛰기·막히면 쉼+RSS 계속·연달아 실패·멈추기·전체 훑기 주기·쇼츠 탭 없음·404·429 한 번 더·
다시 시작한 날) · 통계·주기·활동 상태 · 한국어 제목·주제어·제목 공식·시리즈·고정 해시태그 · 가져올 점(분류·점수 범위·바뀌지 않는 id)·할 일(멱등·쓰임) ·
방향 초안·전략 검사 · 30/60/90 · 점검 피드백·알림 · Claude 붙여 넣기 형식·프롬프트 · /api/strategy/* (경로·잘못된 주소 400·409·Host·Origin) ·
core.channel_listing · 함께 배포하는 비교 데이터.
실행: 저장소 폴더에서 python3 -m unittest tests.test_strategy
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import forecast  # noqa: E402
import strategy  # noqa: E402

FX = Path(__file__).resolve().parent / "fixtures" / "strategy"
OWN_ID = "UCRYziLOw2T6BF6fXtpUby-g"
DAY = 86400


def fx_json(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def info(cid, handle, name, subs, rows, desc=""):
    """yt-dlp 목록 원본 흉내: rows = [(id, 제목, 조회수, 길이)]."""
    return {"_type": "playlist", "id": cid, "channel": name, "channel_id": cid, "uploader_id": handle, "channel_follower_count": subs,
            "description": desc, "entries": [{"_type": "url", "id": i, "title": t, "view_count": v, "duration": d} for i, t, v, d in rows]}


def vid(n, p="v"):
    return (p + str(n)).rjust(11, "x")[:11]


def rss_xml(cid, entries):
    """RSS 원본 흉내: entries = [(id, 제목, 올린 시각, 쇼츠?, 조회수, 좋아요)]."""
    body = []
    for i, t, ts, sh, v, lk in entries:
        pub = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))
        link = f"https://www.youtube.com/shorts/{i}" if sh else f"https://www.youtube.com/watch?v={i}"
        body.append(f"""<entry><id>yt:video:{i}</id><yt:videoId>{i}</yt:videoId><yt:channelId>{cid}</yt:channelId><title>{t}</title>
<link rel="alternate" href="{link}"/><published>{pub}</published><media:group><media:title>{t}</media:title>
<media:community><media:starRating count="{lk}" average="5.00" min="1" max="5"/><media:statistics views="{v}"/></media:community></media:group></entry>""")
    return ("""<?xml version="1.0" encoding="UTF-8"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
<title>채널</title>""" + "".join(body) + "</feed>").encode("utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="전략 테스트 "))
        self.work = self.tmp / "풋살 작업 폴더"
        self.work.mkdir()
        self.sleeps = []
        self.patches = [mock.patch.object(core, "WORK", self.work),
                        mock.patch.dict(core.CONFIG, {"channel_url": f"https://www.youtube.com/channel/{OWN_ID}"}),
                        mock.patch.object(strategy, "_sleep", lambda s: self.sleeps.append(s)),
                        mock.patch.object(strategy, "_jitter", lambda: 0.5),
                        mock.patch.object(core, "set_progress", lambda **kw: None),
                        mock.patch.object(forecast, "B", 12), mock.patch.object(forecast, "M", 20)]
        for p in self.patches:
            p.start()
        strategy._CACHE.clear()
        strategy._SLIM.clear()
        strategy._ANA.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        strategy._CACHE.clear()
        strategy._SLIM.clear()
        strategy._ANA.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_competitors(self, comps):
        strategy._update_state(lambda d: d.__setitem__("competitors", comps))

    def comp(self, key, name, group="풋살 특화", **kw):
        return dict({"key": key, "name": name, "url": f"https://www.youtube.com/channel/{key}" if key.startswith("UC") else None,
                     "handle": None, "channelId": key if key.startswith("UC") else None, "group": group, "from": "user", "addedAt": 1}, **kw)

    def own_channel(self, now, subs=7710, vids=None, rss_ids=None, at=None):
        """우리 채널 자료를 바로 저장 (vids = {id: {k, t, v, pub, d}})."""
        vids = vids or {}
        data = strategy._new_channel(strategy.own_entry())
        data.update(subs=subs, videos=vids, at=at or now, name="최경진 풋살사관학교")
        data["tabs"] = {"long": {"ids": [i for i, v in vids.items() if v["k"] == "L"], "complete": True, "n": 0},
                        "shorts": {"ids": [i for i, v in vids.items() if v["k"] == "S"], "complete": True, "n": 0}}
        data["rss"] = {"at": now, "ok": True, "ids": rss_ids if rss_ids is not None else list(vids)}
        strategy.save_channel(data)
        return data


# ---------- 저장 ----------

class StoreTests(Base):
    def test_state_round_trip_atomic(self):
        """상태 저장은 임시 파일 → 바꿔 끼우기 (남는 .tmp 없음) · 다시 읽으면 같음."""
        strategy.set_settings(remind=False)
        self.assertFalse(strategy.load_state()["settings"]["remind"])
        files = [p.name for p in (self.work / "strategy").iterdir()]
        self.assertIn("state.json", files)
        self.assertFalse([f for f in files if f.endswith(".tmp")])

    def test_broken_state_kept_as_bad(self):
        """깨진 state.json 은 .bad 로 남기고 기본값으로 (기본 경쟁 채널 15곳)."""
        p = self.work / "strategy" / "state.json"
        p.parent.mkdir(parents=True)
        p.write_text("{깨짐", encoding="utf-8")
        st = strategy.load_state()
        self.assertEqual(len(st["competitors"]), 15)
        self.assertTrue((p.parent / "state.json.bad").exists())

    def test_locked_state_is_not_overwritten(self):
        """잠겨서 못 읽으면 덮어쓰지 않음 (StoreBusy)."""
        strategy.set_settings(remind=False)
        p = self.work / "strategy" / "state.json"
        before = p.read_bytes()
        strategy._CACHE.clear()
        real = Path.read_bytes

        def locked(self_):
            if self_.name == "state.json":
                raise PermissionError("잠김")
            return real(self_)
        with mock.patch.object(Path, "read_bytes", locked):
            with self.assertRaises(strategy.StoreBusy):
                strategy.set_settings(remind=True)
        self.assertEqual(p.read_bytes(), before)

    def test_history_skips_broken_lines(self):
        strategy.append_history({"k": "own", "at": 1.0, "subs": 10})
        with open(self.work / "strategy" / "history.jsonl", "a", encoding="utf-8") as f:
            f.write("{깨진 줄\n[1,2]\n")
        strategy.append_history({"k": "own", "at": 2.0, "subs": 20})
        self.assertEqual([r["subs"] for r in strategy.read_history()["own"]], [10, 20])

    def test_channel_file_names_are_hashes(self):
        """대소문자만 다른 UC id · 한글 핸들도 서로 다른 안전한 파일 이름."""
        a, b, c = strategy.fid("UCabcdefghijklmnopqrstuv"), strategy.fid("UCABCDEFGHIJKLMNOPQRSTUV"), strategy.fid("h:@축정원")
        self.assertEqual(len({a, b, c}), 3)
        self.assertTrue(all(len(x) == 16 and x.isalnum() and x.isascii() for x in (a, b, c)))


# ---------- 파서 ----------

class ParserTests(Base):
    def test_parse_own_rss(self):
        r = strategy.parse_rss((FX / "rss_own.xml").read_bytes())
        self.assertEqual(r["channelId"], OWN_ID)
        self.assertEqual(len(r["entries"]), 15)
        e = r["entries"][0]
        self.assertEqual((e["id"], e["shorts"], e["views"], e["likes"]), ("zqsyBbK0RyM", True, 1522, 8))
        self.assertTrue(e["title"].startswith("풋살 초보자의 필수 아이템"))
        self.assertAlmostEqual(e["pub"], 1789914052, delta=86400)
        self.assertEqual(sum(1 for x in r["entries"] if not x["shorts"]), 7)

    def test_parse_other_fixtures(self):
        for f in ("rss_chosal.xml", "rss_samba.xml"):
            r = strategy.parse_rss((FX / f).read_bytes())
            self.assertEqual(len(r["entries"]), 15)
            self.assertTrue(all(isinstance(e["views"], int) for e in r["entries"]))

    def test_rejects_doctype_broken_big_and_not_feed(self):
        for raw in ((FX / "rss_doctype.xml").read_bytes(), (FX / "rss_broken.xml").read_bytes(), b"<!doctype html><html></html>",
                    b"<html><body>x</body></html>", b"x" * (strategy.RSS_MAX + 1), "글자".encode()):
            with self.assertRaises(ValueError):
                strategy.parse_rss(raw)

    def test_empty_feed(self):
        self.assertEqual(strategy.parse_rss((FX / "rss_empty.xml").read_bytes())["entries"], [])

    def test_normalize_flat_basic(self):
        n = strategy.normalize_flat(fx_json("flat_own_videos.json"), "videos")
        self.assertEqual((n["meta"]["subs"], n["meta"]["channelId"], n["meta"]["handle"]), (7710, OWN_ID, "@풋살사관학교"))
        self.assertEqual(len(n["rows"]), 6)
        self.assertEqual((n["rows"][0]["views"], n["rows"][0]["duration"]), (12000, 298.0))
        s = strategy.normalize_flat(fx_json("flat_own_shorts.json"), "shorts")
        self.assertIsNone(s["rows"][0]["duration"])

    def test_normalize_flat_ko_has_no_views(self):
        n = strategy.normalize_flat(fx_json("flat_chosal_videos_ko.json"), "videos")
        self.assertIsNone(n["meta"]["subs"])
        self.assertTrue(all(r["views"] is None for r in n["rows"]))
        self.assertTrue(strategy.is_ko(n["rows"][0]["title"]))
        en = strategy.normalize_flat(fx_json("flat_chosal_videos.json"), "videos")
        self.assertFalse(strategy.is_ko(en["rows"][0]["title"]))

    def test_tab_bundle_picks_that_tab(self):
        n = strategy.normalize_flat(fx_json("flat_tabs.json"), "shorts")
        self.assertEqual([r["id"] for r in n["rows"]], [e["id"] for e in fx_json("flat_own_shorts.json")["entries"]])

    def test_bad_rows_skipped(self):
        d = info(OWN_ID, "@x", "x", 5, [(vid(1), "a", 10, 60), (vid(1), "중복", 10, 60), ("짧은id", "b", 1, 1), (vid(2), "c", -5, None)])
        d["entries"].append("이상한 줄")
        rows = strategy.normalize_flat(d, "videos")["rows"]
        self.assertEqual([r["id"] for r in rows], [vid(1), vid(2)])
        self.assertIsNone(rows[1]["views"])


# ---------- 새로 고침 ----------

class RefreshTests(Base):
    A, B_ = "UCaaaaaaaaaaaaaaaaaaaaaa", "UCbbbbbbbbbbbbbbbbbbbbbb"

    def setUp(self):
        super().setUp()
        self.set_competitors([self.comp(self.A, "에이 풋살"), self.comp(self.B_, "비 풋살")])
        self.calls = []
        self.now = time.time()

    def lister(self, fail=None, notab=False, translated=False):
        def fake(url, kind, limit, log=None, lang=None, sleep_requests=None):
            self.calls.append((url, kind, limit, lang, sleep_requests))
            if fail and fail(url, kind):
                raise fail(url, kind)
            if notab and kind == "shorts":
                raise RuntimeError("ERROR: [youtube:tab] This channel does not have a shorts tab")
            cid = OWN_ID if OWN_ID in url else self.A if self.A in url else self.B_
            k = "s" if kind == "shorts" else "l"
            title = (lambda i: f"Futsal tip {i}") if translated and not lang else (lambda i: f"풋살 기술 {i} 꿀팁")
            rows = [(vid(i, cid[2] + k), title(i), None if lang else 1000 * (i + 1), None if kind == "shorts" else 300) for i in range(8)]
            return info(cid, "@h" + cid[2], "채널 " + cid[2], None if lang else 5000, rows, desc="레슨 문의: coach@example.com")
        return fake

    def rss_for(self, cid):
        k = cid[2]
        ents = [(vid(i, k + "s"), f"풋살 기술 {i} 꿀팁", self.now - (i + 1) * 3 * DAY, True, 2000 + i, 20) for i in range(4)]
        return strategy.parse_rss(rss_xml(cid, ents)), None

    def run_refresh(self, mode="normal", keys=None, **kw):
        with mock.patch.object(core, "channel_listing", self.lister(**kw)), mock.patch.object(strategy, "fetch_rss", side_effect=self.rss_for):
            return strategy.refresh(keys, mode, log=lambda m: None)

    def test_saves_each_channel_history_and_polite_waits(self):
        r = self.run_refresh()
        self.assertEqual((r["done"], r["failed"], r["blocked"]), (3, 0, False))
        files = list((self.work / "strategy" / "channels").glob("*.json"))
        self.assertEqual(len(files), 3)
        self.assertEqual(len(strategy.read_history()), 3)
        self.assertEqual(self.sleeps.count(strategy.GAP + 0.5 * strategy.JITTER), 2)  # 채널 사이 2초 + 흔들기
        self.assertTrue(all(c[4] == strategy.REQ_SLEEP for c in self.calls))  # 요청 사이 0.75초
        a = strategy.load_channel(self.A)
        self.assertEqual(a["subs"], 5000)
        self.assertEqual(a["descFlags"], {"lesson": True, "contact": True, "sponsor": False})
        self.assertNotIn("coach@example.com", json.dumps(a, ensure_ascii=False))  # 설명 원문·연락처는 저장하지 않음
        self.assertTrue(a["rss"]["ok"])
        self.assertTrue(all(isinstance(a["videos"][i].get("pub"), float) for i in a["rss"]["ids"]))

    def test_recent_channels_are_skipped_unless_all(self):
        self.run_refresh()
        self.calls.clear()
        r = self.run_refresh()
        self.assertEqual((r["todo"], self.calls), (0, []))
        r = self.run_refresh("all")
        self.assertEqual(r["todo"], 3)

    def test_blocked_pauses_and_rss_continues(self):
        blk = lambda url, kind: RuntimeError(core.BLOCKED_MSG) if self.A in url else None  # noqa: E731
        r = self.run_refresh(keys=[self.A, self.B_], fail=blk)
        self.assertTrue(r["blocked"])
        self.assertFalse(any(self.B_ in c[0] for c in self.calls))  # 막힌 뒤에는 목록을 묻지 않음
        b = strategy.load_channel(self.B_)
        self.assertTrue(b["rss"]["ok"])  # RSS 는 계속
        self.assertEqual(b["tabs"]["long"]["ids"], [])
        st = strategy.load_state()
        self.assertGreater(st["pause"]["until"], time.time() + 5 * 3600)
        self.calls.clear()
        self.run_refresh("all")  # 쉬는 동안에는 목록 단계 없이 RSS 만
        self.assertEqual(self.calls, [])

    def test_fail_streak_stops_listing(self):
        strategy._update_state(lambda d: d["competitors"].extend([self.comp("UCcccccccccccccccccccccc", "씨"), self.comp("UCdddddddddddddddddddddd", "디")]))
        boom = lambda url, kind: OSError("연결이 끊겼어요")  # noqa: E731
        r = self.run_refresh(fail=boom)
        self.assertTrue(r["paused"])
        self.assertEqual(len({c[0] for c in self.calls}), 3)  # 세 곳 연달아 실패 → 나머지는 목록을 묻지 않음

    def test_cancel_keeps_finished_channels(self):
        ev = threading.Event()
        orig = self.lister()

        def fake(url, kind, limit, log=None, lang=None, sleep_requests=None):
            out = orig(url, kind, limit, log, lang, sleep_requests)
            if self.A in url and kind == "shorts":
                ev.set()
            return out
        with mock.patch.object(core, "channel_listing", fake), mock.patch.object(strategy, "fetch_rss", side_effect=self.rss_for):
            r = strategy.refresh(None, "normal", log=lambda m: None, cancel=ev)
        self.assertIsNotNone(strategy.load_channel("own"))
        self.assertIsNotNone(strategy.load_channel(self.A))
        self.assertIsNone(strategy.load_channel(self.B_))
        self.assertTrue(r["ok"] and r["stopped"])  # 멈췄음을 알려 줌 → 휴대폰 '멈췄어요' (D-028)
        self.assertFalse(self.run_refresh(keys=[self.B_])["stopped"])

    def test_checkup_reports_refresh_stop_block_net(self):
        """합침 검토(D-028): 점검은 새로 고침이 멈춰도·막혀도 그때까지의 숫자로 점검하고, 멈춤·막힘·인터넷 끊김을 결과에 같이 알려 줌."""
        for rf, want in (({"ok": True, "stopped": True, "blocked": False, "net": False}, (True, False, False)),
                         ({"ok": True, "stopped": False, "blocked": True, "net": False}, (False, True, False)),
                         ({"ok": True, "stopped": False, "blocked": False, "net": True}, (False, False, True)),
                         ({"ok": True}, (False, False, False))):
            with mock.patch.object(strategy, "refresh", return_value=rf):
                r = strategy.checkup(log=lambda m: None)
            self.assertTrue(r["ok"] and r["checkup"])
            self.assertEqual((r["stopped"], r["blocked"], r["net"]), want)

    def test_full_scan_schedule_and_ko_titles(self):
        self.run_refresh(keys=[self.A], translated=True)
        limits = [c[2] for c in self.calls if c[3] is None]
        self.assertEqual(limits, [400, 400])  # 처음은 전체 훑기
        ko = [c for c in self.calls if c[3] == "ko"]
        self.assertTrue(ko and all(c[2] == strategy.KO_MAX for c in ko))
        a = strategy.load_channel(self.A)
        self.assertTrue(a["translated"])
        self.assertTrue(all(strategy.is_ko(v["t"]) for v in a["videos"].values()))
        self.assertTrue(any(not strategy.is_ko(v.get("te")) for v in a["videos"].values()))
        self.calls.clear()
        self.run_refresh("all", keys=[self.A], translated=True)
        self.assertEqual([c[2] for c in self.calls if c[3] is None], [60, 60])  # 그 뒤는 최근 60개 · 원제 목록 없음
        self.assertFalse([c for c in self.calls if c[3] == "ko"])
        a = strategy.load_channel(self.A)
        a["tabs"]["long"]["fullAt"] -= 31 * DAY
        strategy.save_channel(a)
        self.calls.clear()
        self.run_refresh("all", keys=[self.A])
        self.assertEqual([c[2] for c in self.calls if c[3] is None][:1], [400])  # 30일 지나면 다시 전체 훑기

    def test_missing_shorts_tab_is_zero(self):
        r = self.run_refresh(keys=[self.A], notab=True)
        self.assertEqual(r["failed"], 0)
        a = strategy.load_channel(self.A)
        self.assertEqual((a["tabs"]["shorts"]["n"], a["tabs"]["shorts"]["complete"]), (0, True))

    def test_rss_404_is_recorded(self):
        def nf(cid):
            return None, "채널을 찾지 못했어요"
        with mock.patch.object(core, "channel_listing", self.lister()), mock.patch.object(strategy, "fetch_rss", side_effect=nf):
            strategy.refresh([self.A], "normal", log=lambda m: None)
        a = strategy.load_channel(self.A)
        self.assertEqual((a["rss"]["ok"], a["rss"]["error"]), (False, "채널을 찾지 못했어요"))

    def test_fetch_rss_retries_429_once(self):
        raw = rss_xml(self.A, [(vid(1), "제목", self.now, False, 5, 1)])
        calls = []

        class Resp:
            def __init__(self, b):
                self.b = b

            def read(self, n=-1):
                return self.b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None, context=None):  # updater.urlopen 이 인증서 설정(context)을 넘김
            calls.append(req.full_url)
            self.assertTrue(req.get_header("User-agent").startswith("futsal-studio/"))
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "too many", {}, None)
            return Resp(raw)
        with mock.patch.object(urllib.request, "urlopen", fake):
            r, err = strategy.fetch_rss(self.A)
        self.assertEqual((len(calls), err, len(r["entries"])), (2, None, 1))
        self.assertIn(strategy.RSS_RETRY_WAIT, self.sleeps)
        with mock.patch.object(urllib.request, "urlopen", side_effect=urllib.error.HTTPError("u", 404, "nf", {}, None)):
            self.assertEqual(strategy.fetch_rss(self.A), (None, "채널을 찾지 못했어요"))
        self.assertEqual(strategy.fetch_rss("잘못된id")[0], None)

    def test_revived_day_detected_and_manual_kept(self):
        raw = (FX / "rss_own.xml").read_bytes()
        with mock.patch.object(core, "channel_listing", self.lister()), \
                mock.patch.object(strategy, "fetch_rss", return_value=(strategy.parse_rss(raw), None)):
            strategy.refresh(None, "own", log=lambda m: None)
        st = strategy.load_state()
        self.assertEqual(time.strftime("%Y-%m-%d", time.gmtime(st["own"]["revivedAt"])), "2026-09-18")
        self.assertEqual(st["own"]["name"], "채널 R")
        strategy.set_revived(strategy.parse_day("2026-09-01"))
        with mock.patch.object(core, "channel_listing", self.lister()), \
                mock.patch.object(strategy, "fetch_rss", return_value=(strategy.parse_rss(raw), None)):
            strategy.refresh(None, "all", log=lambda m: None)
        self.assertEqual(time.strftime("%Y-%m-%d", time.localtime(strategy.load_state()["own"]["revivedAt"])), "2026-09-01")


# ---------- 통계 · 패턴 ----------

class StatsTests(Base):
    def test_channel_stats_numbers(self):
        now = time.time()
        vids = {vid(i, "L"): {"k": "L", "t": f"롱폼 {i}", "v": v, "d": 400} for i, v in enumerate([1000, 2000, 3000, 4000, 10000])}
        vids.update({vid(i, "S"): {"k": "S", "t": f"쇼츠 {i}", "v": v, "pub": now - i * 2 * DAY, "likes": v // 100} for i, v in enumerate([5000, 6000, 7000])})
        ch = self.own_channel(now, subs=5000, vids=vids, rss_ids=[vid(i, "S") for i in range(3)])
        s = strategy.channel_stats(ch, now)
        self.assertEqual((s["L"]["median"], s["L"]["mean"], s["L"]["reach"], s["L"]["dur"]), (3000, 4000, 0.6, 400))
        self.assertEqual((s["S"]["median"], s["S"]["reach"]), (6000, 1.2))
        self.assertEqual(s["L"]["top"][0]["v"], 10000)
        self.assertEqual(s["likeRatio"], 0.01)
        self.assertEqual(s["shortsShare"]["count"], 0.38)
        self.assertEqual(s["cadence"]["activity"], "활발")

    def test_cadence_activity_and_lower_bound(self):
        now = 1_800_000_000
        c = strategy.cadence([(now - i * 3.5 * DAY, "S" if i % 2 else "L") for i in range(15)], now)
        self.assertEqual(c["perWeek"]["all"], round(15 / ((14 * 3.5) / 7), 2))
        self.assertTrue(c["atLeast"])
        self.assertLess(c["cv"], 0.1)
        old = strategy.cadence([(now - (200 + i) * DAY, "L") for i in range(5)], now)
        self.assertEqual((old["activity"], old["perWeek"]["all"]), ("멈춤", 0.0))
        self.assertEqual(strategy.cadence([(now - 50 * DAY, "L"), (now - 60 * DAY, "L"), (now - 70 * DAY, "S")], now)["activity"], "쉬는 중")
        self.assertIsNone(strategy.cadence([(now, "L")], now))

    def test_korean_titles(self):
        self.assertTrue(strategy.is_ko("풋살 잘하고 싶으면 “그냥 눌러”"))
        self.assertFalse(strategy.is_ko("If You Want to Play Futsal Better"))
        self.assertTrue(strategy.is_ko("ALA 움직임 완벽정리 [쌈풋강좌EP11]"))

    def test_formula_stats_need_three_each(self):
        rows = [("드리블 3가지 꿀팁", 9000), ("패스 5가지", 8000), ("슈팅 2가지", 7000), ("그냥 경기", 1000), ("훈련 영상", 1200), ("하이라이트", 900)]
        f = strategy.formula_stats(rows)
        self.assertTrue(f["number"]["ok"])
        self.assertGreater(f["number"]["mult"], 5)
        self.assertEqual(f["number"]["share"], 0.5)
        self.assertFalse(f["vs"]["ok"])
        self.assertEqual(strategy.formula_stats(rows[:5]), {})

    def test_topic_words_skip_verb_endings(self):
        w = strategy._words("수비수 만드는 방법 · 싶으면 보고 쉽게 #쪼살 [쌈풋강좌]", strategy._solid(["수비수를 제치는 팬텀"]))
        self.assertIn("수비수", w)
        for bad in ("만드", "싶으면", "보고", "쉽게", "쌈풋강좌", "쪼살"):
            self.assertNotIn(bad, w)

    def test_series_and_hashtags(self):
        rows = [("수비를 속이는 발바닥 드래그 [쌈풋강좌 EP03]", 20000, vid(1)), ("ALA 움직임 [쌈풋강좌EP11]", 19000, vid(2)),
                ("패스 어디까지 해봤니 2탄", 58000, vid(3)), ("패스 어디까지 해봤니 3탄", 30000, vid(4)), ("그냥 경기", 1000, vid(5))]
        rows += [("??? | 경기 1", 500, vid(6)), ("??? | 경기 2", 600, vid(7))]
        names = {s["name"]: s for s in strategy.series_stats(rows)}
        self.assertNotIn("??? |", names)
        self.assertEqual(names["[쌈풋강좌]"]["n"], 2)
        self.assertIn("패스 어디까지 해봤니 N탄", names)
        tags = strategy.fixed_hashtags([f"기술 {i} #쌈바풋살 #풋살" for i in range(5)] + ["그냥"])
        self.assertEqual((tags[0]["tag"], tags[0]["generic"]), ("#쌈바풋살", False))
        self.assertEqual(strategy.fixed_hashtags(["#a"] * 3), [])


# ---------- 비교 데이터를 쓰는 화면 묶음 · 가져올 점 · 할 일 ----------

class OverviewTests(Base):
    def test_seed_only_overview_and_takeaways(self):
        ov = strategy.overview()
        self.assertTrue(ov["refresh"]["seedOnly"])
        self.assertEqual(ov["own"]["stats"]["subs"], 7710)
        self.assertEqual(ov["own"]["revivedDay"], "2026-09-18")
        self.assertEqual(len(ov["competitors"]), 15)
        self.assertEqual(ov["strategy"]["direction"], "A")
        self.assertTrue(ov["strategy"].get("draft"))
        tks = ov["takeaways"]
        self.assertTrue(tks)
        for t in tks:
            self.assertIn(t["category"], strategy.CATS)
            self.assertTrue(0 <= t["fit"]["score"] <= 100)
            self.assertIn(t["effort"], ("쉬움", "보통", "큼"))
            self.assertTrue(t["evidence"])
        cats = {t["category"] for t in tks}
        self.assertGreaterEqual(len(cats), 5)
        strategy._ANA.clear()
        self.assertEqual([t["id"] for t in strategy.takeaways()], [t["id"] for t in tks])  # 다시 계산해도 같은 id

    def test_todo_idempotent_use_and_hide(self):
        tks = strategy.takeaways()
        t = next(x for x in tks if x["category"] == "썸네일·제목")
        a = strategy.todo_add(takeaway=t["id"])
        b = strategy.todo_add(takeaway=t["id"])
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(strategy.load_state()["todos"]), 1)
        self.assertEqual(a["use"], ["thumb", "title"])
        self.assertEqual([x["id"] for x in strategy.todos_for("thumb")], [a["id"]])
        self.assertEqual(strategy.todos_for("upload"), [])
        strategy.todo_update(a["id"], True)
        self.assertEqual(strategy.todos_for("thumb"), [])
        own = strategy.todo_add(text="  쇼츠 4개 몰아 찍기  ", category="쇼츠 운영")
        self.assertEqual((own["text"], own["use"]), ("쇼츠 4개 몰아 찍기", ["edit", "shorts"]))
        with self.assertRaises(strategy.StrategyError):
            strategy.todo_add(text="")
        strategy.todo_remove(own["id"])
        strategy.set_hidden(t["id"], True)
        self.assertTrue(next(x for x in strategy.takeaways() if x["id"] == t["id"])["hidden"])

    def test_add_remove_channel_rules(self):
        with self.assertRaises(strategy.StrategyError) as e:
            strategy.add_channel("https://evil.example.com/@x")
        self.assertEqual(str(e.exception), "채널 주소나 @핸들을 넣어 주세요")
        for bad in ("", "https://www.youtube.com/watch?v=abcdefghijk", "<script>", "a b c"):
            with self.assertRaises(strategy.StrategyError):
                strategy.add_channel(bad)
        with self.assertRaises(strategy.StrategyError):
            strategy.add_channel("@Cho_sal")  # 이미 기본 경쟁 채널
        with self.assertRaises(strategy.StrategyError):
            strategy.add_channel(f"https://www.youtube.com/channel/{OWN_ID}")  # 우리 채널
        e1 = strategy.add_channel("https://www.youtube.com/@KICK_OH?si=abc", "축구 레슨·기술")
        self.assertEqual((e1["key"], e1["name"], e1["group"], e1["from"]), ("h:@kick_oh", "유니팝(KICK OH)", "축구 레슨·기술", "recommended"))
        e2 = strategy.add_channel("@새채널테스트")
        self.assertEqual((e2["key"], e2["from"]), ("h:@새채널테스트", "user"))
        strategy.remove_channel(e2["key"])
        self.assertIn(e2["key"], strategy.load_state()["removed"])

    def test_presets_and_strategy_validation(self):
        names = [p["key"] for p in strategy.presets()]
        self.assertEqual(names, ["A", "B", "C"])
        a = strategy.preset("A")
        self.assertEqual(strategy.plan_rates(a), {"L": 1.0, "S": 3.0})
        self.assertEqual(strategy.plan_rates(strategy.preset("C")), {"L": 0.5, "S": 5.0})
        bad = [dict(a, formats=[{"name": "x", "kind": "long", "perWeek": 15}]), dict(a, formats=[{"name": "x", "kind": "long", "perWeek": 1}] * 13),
               dict(a, differentiation="가" * 2001), dict(a, series=[{"name": "s"}] * 21), dict(a, goals={"m6": "많이"}), "문자"]
        for b in bad:
            with self.assertRaises(strategy.StrategyError):
                strategy.validate_strategy(b)
        saved = strategy.save_strategy(dict(a, startedAt="2026-09-20"))
        self.assertEqual(time.strftime("%Y-%m-%d", time.localtime(saved["startedAt"])), "2026-09-20")
        self.assertEqual(strategy.current_strategy()["formats"][1]["perWeek"], 3.0)

    def test_solution_phases(self):
        fc = {"kpi": {"day30": {"subs": {"p25": 7800, "p50": 7900, "p75": 8000}}, "day60": {"subs": {"p25": 8000, "p50": 8200, "p75": 8600}},
                      "day90": {"subs": {"p25": 8100, "p50": 8500, "p75": 9100}},
                      "videoMedian": {"L": {"p25": 3100, "p50": 5100, "p75": 8000}, "S": {"p25": 2000, "p50": 4900, "p75": 9000}}},
              "sensitivity": [{"id": "L+1", "d12": 15, "raw12": 0.15, "text": "롱폼을 주 1→2개로 늘리면: …", "small": False},
                              {"id": "u+", "d12": 10, "raw12": 0.1, "text": "…", "small": False}]}
        st = strategy.load_state()
        tks = strategy.takeaways(st)
        sol = strategy.solution(st, tks, fc, strategy.group_summary(strategy.analyze_all(st)), ["팬텀"])
        self.assertEqual([p["span"] for p in sol["phases"]], ["1~30일", "31~60일", "61~90일"])
        p1 = sol["phases"][0]
        self.assertEqual({u["count"] for u in p1["uploads"]}, {4, 13})  # 주 1 → 30일 4개 · 주 3 → 13개
        self.assertTrue(all(t["effort"] == "쉬움" for t in p1["todos"]))
        self.assertTrue(any("최소 3,100회" in k["text"] and "잘 되면 5,100회" in k["text"] for k in p1["kpi"]))
        self.assertTrue(any("7,900명" in k["text"] for k in p1["kpi"]))
        self.assertTrue(sol["priorities"])
        self.assertTrue(all("impact" not in x for x in sol["priorities"]))  # 가져올 점에는 %를 붙이지 않음
        self.assertEqual([x["id"] for x in sol["scenarios"]], ["u+", "L+1"])  # %포인트 ÷ 일 크기 (u+ 10÷1.5 > L+1 15÷2.5)
        self.assertTrue(all(t["rule"] != "R-RESEARCH-TIP" for p in sol["phases"] for t in [x for x in tks if x["id"] in p["why"]]))


# ---------- 점검 · 알림 ----------

class CheckupTests(Base):
    def test_feedback_rules(self):
        now = time.time()
        start = now - 14 * DAY
        strategy.save_strategy(dict(strategy.preset("A"), startedAt=start))
        vids = {vid(1, "S"): {"k": "S", "t": "전 국가대표 vs 현 국가대표 대결 1탄", "v": 30000, "pub": now - 10 * DAY},
                vid(2, "S"): {"k": "S", "t": "발바닥 방향전환", "v": 1500, "pub": now - 9 * DAY},
                vid(3, "L"): {"k": "L", "t": "옛 롱폼", "v": 3000, "pub": now - 400 * DAY, "d": 300},
                vid(4, "L"): {"k": "L", "t": "옛 롱폼 2", "v": 4000, "pub": now - 500 * DAY, "d": 300}}
        vids.update({vid(i, "O"): {"k": "S", "t": f"옛 쇼츠 {i}", "v": 2000, "pub": now - 300 * DAY} for i in range(3)})
        self.own_channel(now, subs=7800, vids=vids)
        strategy.append_history({"k": "own", "at": start - DAY, "subs": 7710})
        strategy.append_history({"k": "own", "at": now, "subs": 7800})
        with mock.patch.object(strategy, "forecast_result", return_value={"kpi": {"videoMedian": {"L": {"p50": 5000}, "S": {"p50": 4000}}},
                                                                           "trajectory": {k: [7700 + 10 * i for i in range(52)] for k in ("p10", "p50", "p90")},
                                                                           "milestones": []}):
            rec = strategy.evaluate()
        self.assertEqual((rec["planN"]["L"], rec["actual"]["L"], rec["actual"]["S"]), (2, 0, 2))
        self.assertTrue(any(x.startswith("롱폼을 계획(약 2개)보다 적게") for x in rec["bad"]))
        self.assertTrue(any(x.startswith("쇼츠를 계획(약 6개)보다 적게") for x in rec["bad"]))
        self.assertTrue(any("2탄" in x for x in rec["change"]))
        self.assertTrue(any("채널 보통의" in x for x in rec["good"]))
        self.assertEqual((rec["subs"]["delta"], rec["subs"]["src"]), (90, "rounded"))
        self.assertEqual(len(rec["forecastBand"]["p50"]), 26)

    def test_band_compare_next_time_and_forward_check(self):
        now = time.time()
        band = {"at": now - 21 * DAY, "s0": 7700, "p10": [7700 + 5 * i for i in range(26)], "p50": [7750 + 10 * i for i in range(26)],
                "p90": [7800 + 20 * i for i in range(26)], "milestones": [{"id": "subs10000_6", "p": 40, "target": 10000, "horizon": 6}]}
        strategy._save_checkups([{"at": now - 21 * DAY, "forecastBand": band, "good": [], "bad": []}])
        self.own_channel(now, subs=7600)
        strategy.append_history({"k": "own", "at": now - 7 * DAY, "subs": 7720})
        with mock.patch.object(strategy, "forecast_result", side_effect=forecast.ForecastError("없음")):
            rec = strategy.evaluate()
        self.assertFalse(rec["band"]["inside"])
        self.assertTrue(any("예상 범위보다 적게" in x for x in rec["bad"]))
        fw = strategy.forward_check(strategy.load_checkups(), strategy.read_history()["own"], now)
        self.assertEqual((fw["n"], fw["inside"]), (1, 1))

    def test_remind_after_seven_days(self):
        self.assertFalse(strategy.remind()["due"])
        strategy.save_strategy(dict(strategy.preset("A"), startedAt=time.time() - 8 * DAY))
        r = strategy.remind()
        self.assertTrue(r["due"] and r["never"])
        strategy._save_checkups([{"at": time.time() - 2 * DAY}])
        self.assertFalse(strategy.remind()["due"])
        strategy.set_settings(remind=False)
        self.assertFalse(strategy.remind()["on"])

    def test_studio_numbers_checked(self):
        self.assertEqual(strategy._clean_studio({"views28": "12000", "subs28": ""}), {"views": 12000, "days": 28})  # 예전 꼴
        self.assertEqual(strategy._clean_studio({"views": "900", "subs": "-3", "days": 7}), {"views": 900, "subs": -3, "days": 7})
        self.assertEqual(strategy._clean_studio({"subs": 5, "days": 99})["days"], 7)
        self.assertIsNone(strategy._clean_studio({"views": ""}))
        with self.assertRaises(strategy.StrategyError):
            strategy._clean_studio({"views28": "많이"})


# ---------- 클로드 ----------

class ClaudeTests(Base):
    def test_parse_ai_formats(self):
        txt = "설명\n```json\n" + json.dumps({"summary": "요약 " * 300, "diagnosis": ["a", "b"], "actions": [{"text": "할 일", "why": "숫자", "priority": 3}, "두 번째"],
                                              "titles": ["제목"], "risks": "위험"}, ensure_ascii=False) + "\n```"
        ai = strategy.parse_ai(txt)
        self.assertLessEqual(len(ai["summary"]), 600)
        self.assertEqual([a["text"] for a in ai["actions"]], ["두 번째", "할 일"])
        self.assertEqual((ai["risks"], ai["by"]), (["위험"], "paste"))
        for bad in ("JSON 없음", "{깨짐}", "[1,2]", "{}"):
            with self.assertRaises(strategy.StrategyError):
                strategy.parse_ai(bad)

    def test_prompt_has_numbers_and_rules(self):
        p = strategy.claude_prompt()
        self.assertLessEqual(len(p), 6000)
        self.assertIn("숫자나 확률을 새로 지어내지 마세요", p)
        self.assertIn("구독자 7,710", p)
        self.assertIn('"actions"', p)

    def test_run_ai_saves_with_hash(self):
        fake = mock.Mock(return_value={"text": json.dumps({"summary": "좋아요", "actions": [{"text": "롱폼 늘리기", "why": "+15%p"}]}), "model": "opus"})
        import claude_cli
        with mock.patch.object(claude_cli, "run", fake):
            r = strategy.run_ai(log=lambda m: None)
        self.assertTrue(r["ok"])
        ai = strategy.load_state()["ai"]
        self.assertEqual((ai["by"], ai["summary"]), ("claude-cli", "좋아요"))
        self.assertTrue(ai["dataHash"])
        self.assertIn("숫자나 확률을 새로 지어내지", fake.call_args[0][0])
        with mock.patch.object(claude_cli, "run", side_effect=claude_cli.ClaudeError("login", claude_cli.MSG["login"])):
            r = strategy.run_ai(log=lambda m: None)
        self.assertEqual((r["ok"], r["kind"]), (False, "login"))


# ---------- 가능성 캐시 ----------

class ForecastGlueTests(Base):
    def test_cache_hash_changes_with_plan(self):
        r1 = strategy.forecast_result()
        r2 = strategy.forecast_result()
        self.assertEqual(r1["inputsHash"], r2["inputsHash"])
        self.assertEqual(r1["at"], r2["at"])  # 같은 입력이면 캐시
        strategy.save_strategy(dict(strategy.preset("A"), formats=[{"name": "롱폼", "kind": "long", "perWeek": 2}]))
        r3 = strategy.forecast_result()
        self.assertNotEqual(r1["inputsHash"], r3["inputsHash"])
        self.assertEqual(r3["plan"], {"L": 2.0, "S": 0.0})

    def test_g0_from_history_without_uploads(self):
        hist = [{"at": 0, "subs": 100}, {"at": 14 * DAY, "subs": 120}, {"at": 15 * DAY, "subs": 121}]
        self.assertAlmostEqual(strategy._g0(hist, {"videos": {}}), 10.0)
        self.assertEqual(strategy._g0(hist, {"videos": {"x": {"pub": 7 * DAY}}}), 0.0)
        self.assertEqual(strategy._g0([{"at": 0, "subs": 100}, {"at": 14 * DAY, "subs": 90}], {}), 0.0)


# ---------- API ----------

class RouteTests(Base):
    def setUp(self):
        super().setUp()
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
        self.app.JOB.update(name=None)
        super().tearDown()

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers=dict({"Content-Type": "application/json"}, **(headers or {})), method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_job(self):
        for _ in range(400):
            if not self.app.JOB["name"]:
                return
            time.sleep(0.05)

    def test_reads(self):
        code, j = self.call("/api/strategy")
        self.assertEqual((code, j["ok"], len(j["competitors"])), (200, True, 15))
        code, j = self.call("/api/strategy/forecast")
        self.assertEqual(code, 200)
        self.assertTrue(j["forecast"]["milestones"] and j["solution"]["phases"])
        self.assertEqual(self.call("/api/strategy/prompt")[0], 200)
        self.assertEqual(self.call("/api/strategy/todos?use=thumb")[1]["todos"], [])
        self.assertIn("due", self.call("/api/strategy/remind")[1])
        self.assertEqual(self.call("/api/strategy/nothing")[0], 404)

    def test_channel_add_errors_and_todo_flow(self):
        code, j = self.call("/api/strategy/channels", {"add": {"url": "http://evil.example.com/x"}})
        self.assertEqual((code, j["error"]), (400, "채널 주소나 @핸들을 넣어 주세요"))
        self.assertEqual(self.call("/api/strategy/channels", {"add": {"url": "@Cho_sal"}})[0], 400)
        code, j = self.call("/api/strategy/channels", {"add": {"url": "@새로운채널", "group": "리뷰·브이로그·분석"}})
        self.assertEqual((code, j["entry"]["group"], j["job"]), (200, "리뷰·브이로그·분석", False))
        tid = self.call("/api/strategy")[1]["takeaways"][0]["id"]
        code, j = self.call("/api/strategy/todo", {"add": {"takeaway": tid}})
        self.assertEqual(code, 200)
        self.assertTrue(self.call("/api/strategy/todo", {"update": {"id": j["todo"]["id"], "done": True}})[1]["todo"]["done"])
        self.assertEqual(self.call("/api/strategy/takeaway", {"id": tid, "hide": True})[0], 200)
        self.assertEqual(self.call("/api/strategy/save", {"strategy": {"formats": [{"name": "x", "perWeek": 99}]}})[0], 400)
        self.assertEqual(self.call("/api/strategy/save", {"strategy": strategy.preset("B")})[1]["strategy"]["direction"], "B")
        self.assertEqual(self.call("/api/strategy/settings", {"remind": False})[1]["settings"]["remind"], False)

    def test_jobs_409_and_paste_guard(self):
        self.app.JOB.update(name=strategy.JOB_AI)
        code, j = self.call("/api/strategy/refresh", {})
        self.assertEqual((code, j["error"]), (409, "다른 작업이 끝난 뒤에 다시 눌러 주세요"))
        self.assertEqual(self.call("/api/strategy/checkup", {})[0], 409)
        code, j = self.call("/api/strategy/ai_paste", {"text": json.dumps({"summary": "요약"})})
        self.assertEqual((code, j.get("busy")), (409, True))
        self.app.JOB.update(name=None)
        code, j = self.call("/api/strategy/ai_paste", {"text": json.dumps({"summary": "요약"})})
        self.assertEqual((code, j["ai"]["by"]), (200, "paste"))
        self.assertEqual(self.call("/api/strategy/ai_paste", {"text": "글"})[0], 400)

    def test_refresh_job_runs_with_fake_listing(self):
        with mock.patch.object(strategy, "refresh", return_value={"ok": True, "done": 1}) as rf:
            code, j = self.call("/api/strategy/refresh", {"own": True})
            self.wait_job()
        self.assertEqual(code, 200)
        self.assertEqual(rf.call_args[0][1], "own")
        self.assertEqual(self.call("/api/strategy/refresh", {"keys": [1, 2]})[0], 400)

    def test_job_start_replies_have_job_id(self):
        """원격 접속과 합침(D-028): 전략 작업 시작 응답에도 jobId → 8단계는 /api/state?job=<번호> 의 done 으로 자기 작업 결과만."""
        with mock.patch.object(strategy, "refresh", return_value={"ok": True, "done": 1}), \
                mock.patch.object(strategy, "checkup", return_value={"ok": True, "checkup": {}}), \
                mock.patch.object(strategy, "run_ai", return_value={"ok": False, "error": "x", "kind": "login"}):
            ids = []
            for path, body, want in (("/api/strategy/refresh", {}, {"ok": True, "done": 1}),
                                     ("/api/strategy/refresh", {"own": True}, {"ok": True, "done": 1}),
                                     ("/api/strategy/checkup", {}, {"ok": True, "checkup": {}}),
                                     ("/api/strategy/ai", {}, {"ok": False, "error": "x", "kind": "login"})):
                code, j = self.call(path, body)
                self.assertEqual((code, j["ok"], j["error"]), (200, True, None), path)
                self.assertIsInstance(j["jobId"], int)
                ids.append(j["jobId"])
                self.wait_job()
                self.assertEqual(self.call(f"/api/state?since=0&job={j['jobId']}")[1]["done"]["result"], want, path)
            self.assertEqual(ids, sorted(set(ids)))
            code, j = self.call("/api/strategy/channels", {"add": {"url": "@새로운채널", "refresh": True}})
            self.assertEqual(code, 200)
            self.assertIsInstance(j["jobId"], int)
            self.assertEqual(j["job"], j["jobId"])
            self.wait_job()
        self.app.JOB.update(name=strategy.JOB_REFRESH)
        code, j = self.call("/api/strategy/ai", {})
        self.assertEqual((code, j["ok"], j["jobId"]), (409, False, None))

    def test_host_and_origin_checked(self):
        self.assertEqual(self.call("/api/strategy", headers={"Host": "evil.example.com"})[0], 403)
        self.assertEqual(self.call("/api/strategy/settings", {"remind": False}, headers={"Origin": "http://evil.example.com"})[0], 403)


# ---------- core · 비교 데이터 ----------

class CoreAndSeedTests(unittest.TestCase):
    def test_channel_listing_options(self):
        seen = {}

        def fake(target, opts, log):
            seen.update(target=target, opts=dict(opts))
            return {"entries": []}
        with mock.patch.object(core, "_extract_flat", fake):
            core.channel_listing("https://www.youtube.com/@Cho_sal/shorts?si=x", "videos", 60, lambda m: None, lang="ko")
        self.assertEqual(seen["target"], "https://www.youtube.com/@Cho_sal/videos")
        self.assertEqual((seen["opts"]["playlistend"], seen["opts"]["sleep_interval_requests"], seen["opts"]["extractor_args"]),
                         (60, 0.75, {"youtube": {"lang": ["ko"]}}))
        with mock.patch.object(core, "_extract_flat", fake):
            core.channel_listing("@쪼살", "shorts", 400)
        self.assertEqual(seen["target"], "https://www.youtube.com/@쪼살/shorts")
        self.assertNotIn("extractor_args", seen["opts"])

    def test_list_videos_uses_same_target_rules(self):
        seen = {}

        def fake(target, opts, log):
            seen.update(target=target, opts=dict(opts))
            return {"entries": [{"id": "abcdefghijk", "title": "t", "view_count": 5, "duration": 3}]}
        with mock.patch.object(core, "_extract_flat", fake):
            rows = core.list_videos("shorts", None, "youtube.com/@x/videos", lambda m: None)
        self.assertEqual(seen["target"], "https://www.youtube.com/@x/shorts")
        self.assertEqual(seen["opts"], {"extract_flat": True, "quiet": True, "no_warnings": True, "noplaylist": True})
        self.assertEqual((rows[0]["id"], rows[0]["views"], rows[0]["kind"]), ("abcdefghijk", 5, "shorts"))

    def test_seed_file_valid(self):
        d = json.loads((core.APP_DIR / strategy.SEED_FILE).read_text(encoding="utf-8"))
        keys = {c["key"] for c in d["channels"]}
        rec = {strategy._rec_key(c) for c in json.loads((core.APP_DIR / "ref_channels.json").read_text(encoding="utf-8"))["channels"]}
        self.assertEqual(keys - {"own"}, rec)
        self.assertTrue(all(c["src"] == "seed" for c in d["channels"]))
        own = next(c for c in d["channels"] if c["key"] == "own")
        self.assertEqual(own["channelId"], OWN_ID)
        self.assertLess((core.APP_DIR / strategy.SEED_FILE).stat().st_size, 400_000)
        for c in d["channels"]:
            for t in c["tabs"].values():
                self.assertLessEqual(len(t["ids"]), 30)
                self.assertTrue(set(t["ids"]) <= set(c["videos"]))


if __name__ == "__main__":
    unittest.main()
