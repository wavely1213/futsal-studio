"""올릴 날·이번 주 카드(D-086) · 저장 안 한 '우리 전략' 초안(D-087) — 저장소 폴더에서 python3 -m unittest tests.test_upload_plan"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402
import strategy  # noqa: E402
import upload  # noqa: E402

OUR = "https://www.youtube.com/channel/UCRYziLOw2T6BF6fXtpUby-g"
KST = strategy.KST


def kst(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=KST).timestamp()


NOW = kst(2026, 10, 7, 10, 0)  # 수요일 오전 10시 (한국 시간)


def plan_c(days=("월", "수", "금")):
    d = strategy.preset("C")  # 쇼츠 주 5 · 롱폼 2주에 1
    d["days"] = list(days)
    return strategy.validate_strategy(d)


class SlotTests(unittest.TestCase):
    """요일·주당 개수·이미 올린 수로 자리를 만듦 (한국 시간 · 개발 PC 시간대와 상관없이)."""

    def test_two_a_day_and_remaining_week(self):
        p = strategy.upload_slots(plan_c(), "S", NOW, [], (18, 0, "x"))
        self.assertEqual(p["perDay"], {"월": 2, "수": 2, "금": 1})  # 요일 3개에 주 5개 → 월·수는 하루 2개
        self.assertEqual((p["q"], p["used"], p["left"], p["weekSlots"]), (5, 0, 5, 3))
        self.assertEqual([strategy.slot_text(t) for t in p["slots"][:4]], ["10-07(수) 12:00", "10-07(수) 18:00", "10-09(금) 18:00", "10-12(월) 12:00"])
        self.assertEqual(len(p["slots"]), 3 + 5 + 5)

    def test_many_a_day_spread(self):
        d = strategy.validate_strategy(dict(strategy.preset("C"), days=["토"], formats=[{"id": "f", "name": "쇼츠", "kind": "shorts", "perWeek": 4}]))
        p = strategy.upload_slots(d, "S", NOW, [], (18, 0, "x"))
        self.assertEqual([strategy.slot_text(t) for t in p["slots"][:4]], ["10-10(토) 12:00", "10-10(토) 14:00", "10-10(토) 16:00", "10-10(토) 18:00"])
        self.assertEqual(len(set(p["slots"])), len(p["slots"]))  # 같은 시각 두 번 X

    def test_uploads_fill_earliest_slots(self):
        ups = [(kst(2026, 10, 5, 12, 5), "S", "a"), (kst(2026, 10, 5, 18, 1), "S", "b"), (kst(2026, 10, 6, 9, 0), "L", "c")]
        p = strategy.upload_slots(plan_c(), "S", NOW, ups, (18, 0, "x"))
        self.assertEqual((p["used"], p["left"], p["weekSlots"]), (2, 3, 3))
        p = strategy.upload_slots(plan_c(), "S", kst(2026, 10, 9, 19, 0), ups, (18, 0, "x"))  # 금요일 저녁: 이번 주 자리 없음
        self.assertEqual(p["weekSlots"], 0)
        self.assertEqual(strategy.slot_text(p["slots"][0]), "10-12(월) 12:00")

    def test_half_per_week_alternates(self):
        p = strategy.upload_slots(plan_c(), "L", NOW, [], (18, 0, "x"))
        self.assertEqual(p["q"], 1)  # 지난주에 안 올렸으니 이번 주 1개 (월요일 자리는 지남)
        self.assertEqual(p["weekSlots"], 0)
        self.assertEqual(strategy.slot_text(p["slots"][0]), "10-12(월) 18:00")
        last_week = [(kst(2026, 9, 30, 18, 0), "L", "x")]
        self.assertEqual(strategy.upload_slots(plan_c(), "L", NOW, last_week, (18, 0, "x"))["q"], 0)  # 지난주에 올렸으면 이번 주 쉼

    def test_default_days_when_not_set(self):
        d = plan_c(days=())
        days, chosen = strategy.plan_days(d)
        self.assertFalse(chosen)
        self.assertEqual(days, list("월화수목금토"))  # 쇼츠 5 + 롱폼 2주에 1(올려서 1) = 6 → 월~토 (DEFAULT_DAYS)
        self.assertEqual(strategy.upload_slots(strategy.validate_strategy(dict(strategy.preset("A"), days=[])), "S", NOW, [], (18, 0, ""))["days"], list("월화목금"))

    def test_recent_uploads_only_published(self):
        tmp = Path(tempfile.mkdtemp(prefix="올린 기록 "))
        try:
            with mock.patch.object(core, "WORK", tmp):
                (tmp / "youtube").mkdir()
                items = [{"videoId": "aaaaaaaaaaa", "at": NOW - 3600, "privacy": "public", "shorts": True},
                         {"videoId": "bbbbbbbbbbb", "at": NOW - 3600, "privacy": "private", "preAudit": True, "locked": True, "shorts": True},
                         {"videoId": "ccccccccccc", "at": NOW - 3600, "privacy": "private", "publishAt": "2026-10-09T09:00:00Z", "shorts": False}]
                (tmp / "youtube" / "history.json").write_text(json.dumps({"v": 1, "items": items}), encoding="utf-8")
                ups = strategy.recent_uploads({"videos": {"aaaaaaaaaaa": {"k": "S", "pub": NOW - 1800}}}, NOW)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual([(k, i) for _, k, i in ups], [("S", "aaaaaaaaaaa"), ("L", "ccccccccccc")])  # 같은 영상은 한 번 · 잠긴 비공개 X · 예약은 공개 시각

    def test_post_time(self):
        own = {"videos": {f"l{i}": {"k": "L", "pub": t} for i, t in enumerate((kst(2024, 5, 9, 18), kst(2025, 8, 29, 18, 4), kst(2025, 9, 4, 18)))}}
        own["videos"].update({"s1": {"k": "S", "pub": kst(2026, 9, 18, 13, 9)}, "s2": {"k": "S", "pub": kst(2026, 9, 20, 23, 20)}})
        peers = {}
        for c in range(14):
            ids = [f"c{c}v{i}" for i in range(15)]
            hours = [17, 17, 18, 18, 19, 12, 12, 9, 21, 22, 13, 14, 15, 16, 20]
            peers[f"c{c}"] = {"rss": {"ids": ids}, "videos": {i: {"pub": kst(2026, 9, 1 + n % 20, hours[n])} for n, i in enumerate(ids)}}
        chans = dict(peers, own=own)
        self.assertEqual(strategy.post_time("L", chans)[:2], (18, 0))  # 우리 롱폼은 늘 18시
        self.assertIn("늘 올리던", strategy.post_time("L", chans)[2])
        h, m, why = strategy.post_time("S", chans)  # 쇼츠는 제각각 → 비슷한 채널 흔한 시각대 (17~19시)
        self.assertEqual((h, m), (18, 0))
        self.assertIn("210편", why)
        self.assertEqual(strategy.post_time("S", {"own": {}})[:2], (18, 0))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="올릴 날 "))
        dirs = {"WORK": self.tmp, "VIDEOS": self.tmp / "videos", "ANALYSIS": self.tmp / "analysis", "OUT": self.tmp / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        (self.tmp / "projects").mkdir()
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [
            mock.patch.object(editor, "PROJECTS", self.tmp / "projects"), mock.patch.dict(core.CONFIG, {"channel_url": OUR}),
            mock.patch.object(strategy, "post_time", lambda kind, chans=None: (18, 0, "저녁 6시"))]
        for p in self.patches:
            p.start()
        strategy._CACHE.clear()
        strategy._SLIM.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        strategy._CACHE.clear()
        strategy._SLIM.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def video(self, name, seqs, exported=(), at=None):
        """보관함 영상 + 편집본 (seqs: [(id, 이름, 형식)]) + 내보낸 영상 (exported: 편집본 id · 파일 시각 at)."""
        (core.VIDEOS / name).write_bytes(b"x")
        out = []
        for sid, nm, fmt in seqs:
            items = [{"id": "v" + sid, "track": "V1", "media": "main", "in": 0.0, "out": 40.0, "start": 0.0, "speed": 1.0, "link": "l" + sid}]
            out.append({"id": sid, "name": nm, "format": fmt, "v": 2, "items": items, "markers": [], "titles": [], "tracks": editor.default_tracks()})
        proj = {"source": name, "info": {"duration": 300.0, "width": 160, "height": 90}, "captions": [], "v": 2, "rev": 1, "sequences": out}
        editor._ppath(name).write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        import os
        for sid, nm, fmt in seqs:
            if sid in exported:
                p = core.OUT / f"{core.adir(name).name}_{nm}.mp4"
                p.write_bytes(b"x")
                if at:
                    os.utime(p, (at, at))

    def history(self, items):
        (self.tmp / "youtube").mkdir(exist_ok=True)
        (self.tmp / "youtube" / "history.json").write_text(json.dumps({"v": 1, "items": items}, ensure_ascii=False), encoding="utf-8")


class StockTests(Base):
    """만들어 둔 편집본: 내보냈고 아직 안 올린 것 (유튜브 올린 기록 · 우리 채널 제목으로 올렸는지 봄)."""

    def test_ready_drafts_uploaded(self):
        self.video("a.mp4", [("s1", "쇼츠 1", "shorts"), ("s2", "쇼츠 2", "shorts"), ("s3", "쇼츠 3", "shorts"), ("l1", "롱폼 가편집", "long")],
                   exported=("s1", "s2", "s3"), at=NOW - 3600)
        st = upload.stock(NOW)
        self.assertEqual([x["seq"] for x in st["ready"]], ["s1", "s2", "s3"])
        self.assertEqual(st["drafts"], {"S": 0, "L": 1})
        locked = {"videoId": "abcdefghijk", "name": "a.mp4", "seq": "s1", "file": "a_쇼츠 1.mp4", "at": NOW, "title": "x", "shorts": True,
                  "privacy": "private", "preAudit": True, "locked": True}
        self.history([locked])
        self.assertEqual(len(upload.stock(NOW)["ready"]), 3)  # 감사 전 잠긴 비공개는 아직 안 올린 것
        self.history([dict(locked, privacy="public", preAudit=False, locked=False)])
        self.assertEqual([x["seq"] for x in upload.stock(NOW)["ready"]], ["s2", "s3"])  # 바로 올린 기록 (공개)
        kit = {"version": 1, "made": "x", "name": "a.mp4", "source": {"id": "s2", "label": "쇼츠 2"}, "format": "shorts", "duration": 40,
               "topics": [], "titles": ["x"], "title": "잔디풋살 VS 인도어 풋살", "chapters": [], "hashtags": [], "tags": [], "description": ""}
        upload.save_kit(kit, "a_쇼츠 2")
        self.assertEqual([x["seq"] for x in upload.stock(NOW)["ready"]], ["s3"])  # 키트 제목이 이미 올린 우리 영상 제목과 같음 (비교 데이터)
        self.assertEqual(upload.stock(NOW + 61 * 86400)["ready"], [])  # 60일 넘게 지난 내보내기는 세지 않음


class KitSlotTests(Base):
    def test_kit_slot_order_and_line(self):
        self.assertIsNone(strategy.kit_slot("a.mp4", "s1", "S", NOW))  # 전략을 저장하기 전에는 없음
        strategy.save_strategy(dict(strategy.preset("C"), days=["월", "수", "금"]))
        self.video("a.mp4", [("s1", "쇼츠 1", "shorts"), ("s2", "쇼츠 2", "shorts"), ("s4", "쇼츠 4", "shorts")], exported=("s1", "s2"), at=NOW - 3600)
        with mock.patch.object(strategy, "recent_uploads", return_value=[]):
            a = strategy.kit_slot("a.mp4", "s1", "S", NOW)
            b = strategy.kit_slot("a.mp4", "s2", "S", NOW)
            c = strategy.kit_slot("a.mp4", "s4", "S", NOW)  # 아직 안 내보냄 → 준비된 것 다음
        self.assertEqual((a["text"], b["text"], c["text"]), ("10-07(수) 12:00", "10-07(수) 18:00", "10-09(금) 18:00"))
        self.assertEqual(a["line"], "다음 올릴 날: 10-07(수) 12:00 · 이번 주 남은 자리 3")
        self.assertEqual(a["behind"], 2)  # 월요일 두 자리를 놓침
        self.assertTrue(a["local"].startswith("2026-10-07T12:00"))
        self.assertIn("월·수·금", a["why"])

    def test_kit_has_schedule_and_text_line(self):
        strategy.save_strategy(dict(strategy.preset("C"), days=["월", "수", "금"]))
        kit = {"version": 1, "made": "2026-10-07 10:00", "name": "a.mp4", "source": {"id": "", "label": "원본 영상 그대로"}, "format": "shorts",
               "duration": 40.0, "topics": ["패스"], "titles": ["패스 꿀팁"], "title": "패스 꿀팁", "chapters": [], "hashtags": [], "tags": [], "description": ""}
        upload.annotate(kit)
        self.assertTrue(kit["schedule"]["line"].startswith("다음 올릴 날: "))
        self.assertIn(kit["schedule"]["line"], upload.kit_text(kit))


class WeekPlanTests(Base):
    def week(self, now=NOW, ups=()):
        st = strategy.load_state()
        with mock.patch.object(strategy, "recent_uploads", return_value=list(ups)):
            return strategy.week_plan(st, {}, now, {})

    def test_not_saved(self):
        self.assertIsNone(self.week())

    def test_blocked_on_upload_with_stock(self):
        strategy.save_strategy(dict(strategy.preset("C"), days=["월", "수", "금"]))
        self.video("a.mp4", [("s1", "쇼츠 1", "shorts"), ("s2", "쇼츠 2", "shorts"), ("s3", "쇼츠 3", "shorts")], exported=("s1", "s2", "s3"), at=NOW - 3600)
        w = self.week()
        self.assertTrue(w["isDay"])  # 수요일 = 올리는 날
        self.assertEqual(w["ready"], {"S": 3, "L": 0})
        self.assertEqual((w["blocked"], w["action"]["go"], w["action"]["label"], w["action"]["seq"]), ("upload", "upload", "7 올리기", "s1"))
        self.assertEqual(w["next"]["text"], "10-07(수) 12:00")
        self.assertEqual(w["warns"], [])  # 이번 주 남은 자리 3 = 준비된 쇼츠 3
        self.assertTrue(any("월·수요일은 하루 쇼츠 2개" in n for n in w["notes"]), w["notes"])
        self.assertTrue(any("2개가 밀렸어요" in n for n in w["notes"]), w["notes"])

    def test_warn_when_stock_runs_out_and_blocked_edit_or_shoot(self):
        strategy.save_strategy(dict(strategy.preset("C"), days=["월", "수", "금"]))
        self.video("a.mp4", [("s1", "쇼츠 1", "shorts"), ("s2", "쇼츠 2", "shorts")], exported=("s1",), at=NOW - 3600)
        w = self.week()
        self.assertEqual(len(w["warns"]), 1)
        self.assertIn("쇼츠 더 만들기", w["warns"][0]["text"])
        self.assertIn("10-07(수) 18:00 전에 2개를", w["warns"][0]["text"])
        (core.OUT / "a_쇼츠 1.mp4").unlink()
        w = self.week()
        self.assertEqual((w["blocked"], w["action"]["go"]), ("edit", "cut"))  # 안 내보낸 편집본만 있음
        editor._ppath("a.mp4").unlink()
        w = self.week()
        self.assertEqual((w["blocked"], w["action"]["go"]), ("shoot", "library"))

    def test_shorts_only_plan(self):
        strategy.save_strategy(dict(strategy.preset("C"), days=["화"], formats=[{"id": "f", "name": "쇼츠", "kind": "shorts", "perWeek": 2}]))
        w = self.week()
        self.assertIsNotNone(w)  # 롱폼 0개 계획도 카드가 그대로
        self.assertEqual((w["left"]["L"], w["slots"]["L"]), (0, 0))
        self.assertIsNone(strategy.kit_slot("a.mp4", "", "L", NOW))

    def test_week_done(self):
        strategy.save_strategy(dict(strategy.preset("A"), days=["화", "목"]))  # 롱폼 1 · 쇼츠 3
        ups = [(kst(2026, 10, 5, 18), "S", "a"), (kst(2026, 10, 6, 18), "S", "b"), (kst(2026, 10, 6, 19), "S", "c"), (kst(2026, 10, 6, 20), "L", "d")]
        w = self.week(ups=ups)
        self.assertEqual(w["left"], {"S": 0, "L": 0})
        self.assertIsNone(w["blocked"])
        self.assertFalse(w["isDay"])  # 수요일은 화·목 계획 밖

    def test_overview_week_has_plan(self):
        strategy.save_strategy(dict(strategy.preset("C"), days=["월", "수", "금"]))
        ov = strategy.overview()
        self.assertIn("plan", ov["week"])
        self.assertEqual(ov["week"]["plan"]["days"], ["월", "수", "금"])


class DraftTests(Base):
    """D-087: '우리 전략'에 적던 내용은 저장하기 전에도 서버에 남고, [저장]하면 지워짐."""

    def test_save_load_clear(self):
        self.assertIsNone(strategy.load_draft())
        d = dict(strategy.preset("C"), differentiation="감독님과 정한 우리만의 차별화 " * 3, goals={"m6": 3000, "m12": None}, days=["월", "x"])
        strategy.save_draft(d)
        got = strategy.load_draft()
        self.assertEqual(got["strategy"]["goals"]["m6"], 3000)
        self.assertEqual(got["strategy"]["days"], ["월"])
        self.assertTrue(got["strategy"]["differentiation"].startswith("감독님과 정한"))
        self.assertIsNotNone(strategy.overview()["draft"])
        strategy.save_strategy(strategy.preset("C"))  # 저장하면 초안은 지움
        self.assertIsNone(strategy.load_draft())
        self.assertIsNone(strategy.overview()["draft"])

    def test_clean_draft_tolerates_anything(self):
        d = strategy._clean_draft({"direction": "Z", "formats": [{"perWeek": "많이"}, "x", {"kind": "shorts", "perWeek": 99}],
                                   "series": "x", "goals": {"m6": "3,000", "m12": float("nan")}, "differentiation": "가" * 5000, "startedAt": "2026-10-01"})
        self.assertEqual(d["direction"], "custom")
        self.assertEqual([f["perWeek"] for f in d["formats"]], [0.0, 14.0])
        self.assertEqual(d["series"], [])
        self.assertEqual(d["goals"], {"m6": "3,000", "m12": None})
        self.assertEqual(len(d["differentiation"]), strategy.LONG_MAX)
        self.assertEqual(d["startedAt"], "2026-10-01")
        with self.assertRaises(strategy.StrategyError):
            strategy.save_draft("x")

    def test_routes(self):
        import app
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        with mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.tmp / "studio.log"):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                def post(body, ctype="application/json"):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/strategy/draft", data=json.dumps(body).encode(),
                                                 headers={"Content-Type": ctype}, method="POST")
                    try:
                        with urllib.request.urlopen(req, timeout=30) as r:
                            return r.status, json.loads(r.read())
                    except urllib.error.HTTPError as e:
                        return e.code, json.loads(e.read())
                code, j = post({"strategy": dict(strategy.preset("B"), differentiation="적던 글")}, "text/plain;charset=UTF-8")  # sendBeacon 꼴
                self.assertEqual((code, j["draft"]["differentiation"]), (200, "적던 글"))
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/strategy", timeout=60) as r:
                    self.assertEqual(json.loads(r.read())["draft"]["strategy"]["direction"], "B")
                self.assertEqual(post({"strategy": "x"})[0], 400)
                self.assertEqual(post({"clear": True})[0], 200)
                self.assertIsNone(strategy.load_draft())
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
