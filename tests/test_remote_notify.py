"""원격 접속 비콘·알림(remote.Publisher · Service.job_hook·tick·revoke) 시험 — 가짜 ntfy(FUTSAL_NTFY 대신 서비스 인자)로
— 저장소 폴더에서 python3 -m unittest tests.test_remote_notify"""
import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote  # noqa: E402
from remote_fixture import Clock, FakeNtfy, dev_env, make_home, pair_device, service  # noqa: E402


def open_beacon(msg, cred):
    outer = json.loads(remote._unb64u(msg[5:]))
    item = next((i for i in outer["items"] if i["d"] == cred["device"]["id"]), None)
    if item is None:
        return None
    return json.loads(remote.gcm_open(remote._unb64u(cred["keys"]["beacon"]), item["c"], f"fsb1|{outer['pc']}|{item['d']}|{outer['seq']}"))


class NotifyBase(unittest.TestCase):
    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)
        self.ntfy = FakeNtfy()
        self.addCleanup(self.ntfy.close)
        p = mock.patch.dict(os.environ, dev_env())
        p.start()
        self.addCleanup(p.stop)
        for k, v in (("RETRY_DELAYS", (0.01, 0.01)),):
            pp = mock.patch.object(remote, k, v)
            pp.start()
            self.addCleanup(pp.stop)
        self.clock = Clock(time.time())
        self.svc, self.fb = service(self.home, self.clock, ntfy=self.ntfy.url)
        self.a = pair_device(self.svc, "폰 A")
        self.b = pair_device(self.svc, "폰 B")
        self.topics = dict(self.svc.store.data["topics"])
        self.assertTrue(self._paired_pushes(2))
        with self.ntfy.lock:
            self.ntfy.posts.clear()  # 짝짓기 알림·비콘은 따로 시험 (test_new_device_push)

    def _paired_pushes(self, n):
        end = time.time() + 8
        while time.time() < end:
            if len(self.ntfy.wait(lambda p: p[1] == remote.NOTE_TEXT["paired"], 0.1)) >= n:
                time.sleep(0.2)  # 함께 보낸 비콘까지
                return True
        return False

    def turn_on(self):
        self.assertTrue(self.svc.turn_on())
        end = time.time() + 10
        while self.svc.state != "on" and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(self.svc.state, "on")
        self.addCleanup(lambda: self.svc.state != "off" and self.svc.turn_off())

    def beacons(self, topic=None, n=1, timeout=8):
        t = topic or self.svc.store.data["topics"]["beacon"]
        end = time.time() + timeout
        while time.time() < end:
            got = self.ntfy.topic(t)
            if len(got) >= n:
                return got
            time.sleep(0.03)
        return self.ntfy.topic(t)

    def notes(self, n=1, timeout=8):
        t = self.svc.store.data["topics"]["notify"]
        end = time.time() + timeout
        while time.time() < end:
            got = self.ntfy.topic(t)
            if len(got) >= n:
                return got
            time.sleep(0.03)
        return self.ntfy.topic(t)


class BeaconTests(NotifyBase):
    def test_on_beacon_per_device_and_only_own_key(self):
        self.turn_on()
        # 켜는 동안 먼저 나간 비콘('켜는 중' 등)이 있을 수 있음 → '켜짐' 비콘이 올 때까지 기다림 (바쁜 PC 에서 순서 경쟁)
        end = time.time() + 20
        while True:
            msg = self.beacons()[-1][1]
            a = open_beacon(msg, self.a)
            if (a and a.get("state") == "on") or time.time() > end:
                break
            time.sleep(0.05)
        self.assertTrue(msg.startswith("fsb1."))
        a, b = open_beacon(msg, self.a), open_beacon(msg, self.b)
        self.assertEqual(a["state"], "on")
        self.assertEqual(a["url"], self.svc.url)
        self.assertEqual(a["api"], 1)
        self.assertEqual(a["pc"], self.svc.store.data["pc"]["id"])
        self.assertEqual(a["seq"], b["seq"])
        wrong = dict(self.a, keys=self.b["keys"])
        with self.assertRaises(ValueError):
            open_beacon(msg, wrong)
        self.assertNotIn(self.svc.url, msg)  # 주소는 잠긴 채로만
        self.assertLess(len(msg), 4000)

    def test_five_devices_fit_under_4kb_with_moved(self):
        for i in range(3):
            pair_device(self.svc, f"폰{i}")
        self.svc.state, self.svc.url = "on", "https://" + "-".join(["abcdefghij"] * 5) + ".trycloudflare.com"
        body = self.svc.beacon_body(moved=dict(self.svc.store.data["topics"]))
        self.assertLess(len(body), 4096)

    def test_seq_increments_and_persists(self):
        self.svc.state = "on"
        s1 = json.loads(remote._unb64u(self.svc.beacon_body()[5:]))["seq"]
        s2 = json.loads(remote._unb64u(self.svc.beacon_body()[5:]))["seq"]
        self.assertEqual(s2, s1 + 1)
        again = remote.Store(self.home / "remote.json")
        self.assertEqual(again.data["seq"], s2)

    def test_off_beacon_with_reason(self):
        self.turn_on()
        self.beacons()
        self.svc.turn_off("user")
        got = self.beacons(n=2)
        last = open_beacon(got[-1][1], self.a)
        self.assertEqual((last["state"], last["reason"], last["url"]), ("off", "원격 접속을 껐어요", None))

    def test_shutdown_sends_app_off_beacon_synchronously(self):
        self.turn_on()
        self.beacons()
        t0 = time.time()
        self.svc.shutdown(timeout=2)
        self.assertLess(time.time() - t0, 3)
        last = open_beacon(self.beacons(n=2)[-1][1], self.a)
        self.assertEqual((last["state"], last["reason"]), ("off", "앱을 껐어요"))
        self.assertTrue(self.svc.store.data["enabled"])  # 다음에 앱을 켜면 이어서 켬

    def test_job_change_beacon_coalesced(self):
        with mock.patch.object(remote, "BEACON_GAP", 0.6):
            self.turn_on()
            n0 = len(self.beacons())
            for i in range(5):
                self.svc.pub.beacon()
            time.sleep(0.2)
            mid = len(self.ntfy.topic(self.svc.store.data["topics"]["beacon"]))
            self.assertLessEqual(mid - n0, 1)
            # 같은 꼴은 마지막 것만 60초(여기서는 0.6초)에 한 번
            self.clock.tick(1)
            got = self.beacons(n=n0 + 1, timeout=3)
            self.assertLessEqual(len(got) - n0, 2)

    def test_job_start_triggers_beacon_via_tick(self):
        self.turn_on()
        n0 = len(self.beacons())
        self.fb.job_state["name"] = "편집점 찾기"
        self.fb.job_state["progress"] = {"pct": 37}
        self.clock.tick(61)
        self.svc.tick()
        got = self.beacons(n=n0 + 1)
        inner = open_beacon(got[-1][1], self.a)
        self.assertEqual(inner["job"], {"name": "편집점 찾기", "pct": 37})

    def test_heartbeat_after_20_minutes(self):
        self.turn_on()
        n0 = len(self.beacons())
        self.svc._job_seen = None
        self.clock.tick(remote.HEARTBEAT + 1)
        self.svc.tick(last_hb=self.clock() - remote.HEARTBEAT - 1)
        self.assertGreaterEqual(len(self.beacons(n=n0 + 1)), n0 + 1)

    def test_rendezvous_on_pair_start(self):
        self.turn_on()
        pair = self.svc.pair_start()
        code = pair["code"].replace("-", "")
        topic, key, _ = remote.derive_pair(code)
        got = self.ntfy.wait(lambda p: p[0] == topic)
        self.assertTrue(got)
        m = json.loads(remote.gcm_open(key, got[-1][1][5:], "fsp1|" + topic))
        self.assertEqual(m["url"], self.svc.url)
        self.assertEqual(m["pc"]["name"], "내 PC")
        self.assertNotIn(code, got[-1][1])


class RevokeTests(NotifyBase):
    def test_revoke_moves_topics_for_remaining_devices_only(self):
        self.turn_on()
        self.beacons()
        old = dict(self.svc.store.data["topics"])
        self.svc.revoke([self.a["device"]["id"]])
        new = self.svc.store.data["topics"]
        self.assertNotEqual(new["beacon"], old["beacon"])
        self.assertNotEqual(new["notify"], old["notify"])
        moved = self.ntfy.wait(lambda p: p[0] == old["beacon"] and bool((open_beacon(p[1], self.b) or {}).get("moved")))
        self.assertTrue(moved)
        last = moved[-1][1]
        self.assertEqual(open_beacon(last, self.b)["moved"], new)
        self.assertIsNone(open_beacon(last, self.a))  # 끊은 휴대폰 몫은 없음
        self.assertTrue(self.beacons(new["beacon"]))  # 새 주제에도 바로
        self.assertIn("휴대폰 연결을 끊었어요 · 폰 A (PC에서)", self.fb.lines)
        # 검토(높음): ntfy 앱은 옛 알림 주제를 계속 구독 → 조용히 끊기지 않게 옛 주제에 정해진 안내를 한 번
        old_notes = self.ntfy.wait(lambda p: p[0] == old["notify"])
        self.assertEqual([p[1] for p in old_notes], [remote.NOTE_TEXT["moved"]])
        self.assertIn("다시 구독", old_notes[0][1])
        self.assertIn("설치하라고 하지 않아요", old_notes[0][1])
        self.assertNotIn(new["notify"], old_notes[0][1] + json.dumps(old_notes[0][2]))  # 새 주제는 옛 주제(끊은 휴대폰도 봄)에 안 씀


class NotifyTests(NotifyBase):
    def test_done_notification_fixed_text_without_names(self):
        self.turn_on()
        title = "20260101_AbCdEfGhIjK_비밀 제목 [꿀팁].mp4"
        self.svc.job_hook("내보내기", None, [f"{title}_롱폼.mp4", "x.srt"], "휴대폰 · 폰 A", 754)
        self.svc.job_hook("영상 검수", None, {"file": f"/home/x/{title}", "score": 90, "items": []}, None, 61)
        self.svc.job_hook("편집점 찾기", f"영상을 못 열었어요 · {title}", None, None, 30)
        got = self.notes(n=3)
        texts = [p[1] for p in got]
        self.assertEqual(texts, ["작업이 끝났어요 · 내보내기 (12분)", "작업이 끝났어요 · 영상 검수 (1분)", "작업이 멈췄어요 · 편집점 찾기 — 휴대폰에서 자세히 보기"])
        for _, msg, hdr, _ in got:
            self.assertNotIn("비밀", msg + json.dumps(hdr, ensure_ascii=False))
            self.assertEqual(hdr["title"], "풋살 스튜디오")
            self.assertEqual(hdr["click"], "https://mulgyeol.kr/futsal")
        self.assertEqual(got[2][2]["priority"], 4)
        self.assertEqual(got[2][2]["tags"], ["warning"])

    def test_short_local_jobs_do_not_notify(self):
        self.turn_on()
        self.svc.job_hook("장면 고르기", None, {}, None, 4)
        self.svc.job_hook("누끼 따기", "x", None, None, 2)
        time.sleep(0.5)
        self.assertEqual(self.ntfy.topic(self.svc.store.data["topics"]["notify"]), [])

    def test_unknown_job_label_hidden(self):
        self.turn_on()
        self.svc.job_hook("비밀 영상 이름", None, None, "휴대폰 · x", 5)
        self.assertEqual(self.notes()[0][1], "작업이 끝났어요 · 작업 (5초)")

    def test_strategy_job_names_in_notification(self):
        """채널 전략과 합침(D-028): PC 가 시킨 긴 전략 작업은 '작업' 대신 그 이름으로 (정해진 이름이라 파일 이름·제목 없음)."""
        import strategy
        self.turn_on()
        self.svc.job_hook(strategy.JOB_REFRESH, None, {"ok": True, "done": 15}, None, 360)
        self.svc.job_hook(strategy.JOB_AI, None, {"ok": False, "error": "로그인이 필요해요"}, None, 20)
        self.assertEqual([p[1] for p in self.notes(n=2)], ["작업이 끝났어요 · 채널 전략 새로 고침 (6분)",
                                                           "작업이 멈췄어요 · 클로드로 전략 보기 — 휴대폰에서 자세히 보기"])

    def test_strategy_stopped_blocked_net_not_shown_as_done(self):
        """합침 검토(D-028): 채널 전략 새로 고침·점검은 멈춰도·막혀도 ok:true → 휴대폰이 '끝 ✓'·'작업이 끝났어요'로 보이면 안 됨.
        멈춤 = '멈췄어요' · YouTube 막힘·인터넷 끊김 = '확인이 필요해요' · 그대로 끝나면 '끝났어요'."""
        import strategy
        self.turn_on()
        self.svc.job_hook(strategy.JOB_REFRESH, None, {"ok": True, "done": 1, "failed": 1, "todo": 18, "blocked": False, "net": False, "stopped": True},
                          None, 166)
        last = self.svc.last
        self.assertEqual((last["ok"], last["warn"], last["error"]), (False, False, remote.STRATEGY_MSG["stopped"]))
        self.svc.job_hook(strategy.JOB_CHECK, None, {"ok": True, "checkup": {}, "stopped": True, "blocked": False, "net": False}, None, 40)
        self.assertEqual((self.svc.last["ok"], self.svc.last["error"]), (False, remote.STRATEGY_MSG["stopped_check"]))
        self.svc.job_hook(strategy.JOB_OWN, None, {"ok": True, "done": 1, "blocked": True, "net": False, "stopped": False}, None, 30)
        last = self.svc.last
        self.assertEqual((last["ok"], last["warn"], last["blocked"], last["error"]), (False, True, True, remote.STRATEGY_MSG["blocked"]))
        self.svc.job_hook(strategy.JOB_CHECK, None, {"ok": True, "checkup": {}, "stopped": False, "blocked": False, "net": True}, None, 30)
        last = self.svc.last
        self.assertEqual((last["ok"], last["warn"], last["blocked"], last["error"]), (False, True, False, remote.STRATEGY_MSG["net"]))
        self.svc.job_hook(strategy.JOB_AI, None, {"ok": True, "blocked": True, "stopped": True}, None, 60)  # 클로드는 멈추면 ok:false (따로 봄)
        self.assertEqual((self.svc.last["ok"], self.svc.last["warn"]), (True, False))
        texts = [p[1] for p in self.notes(n=5)]
        self.assertEqual(texts, ["작업이 멈췄어요 · 채널 전략 새로 고침 — 휴대폰에서 자세히 보기", "작업이 멈췄어요 · 채널 점검 — 휴대폰에서 자세히 보기",
                                 remote.NOTE_TEXT["blocked"], remote.NOTE_TEXT["net"], "작업이 끝났어요 · 클로드로 전략 보기 (1분)"])
        self.assertFalse(any(t.startswith("작업이 끝났어요 · 채널") for t in texts))

    def test_youtube_jobs_fixed_text_never_url_or_token(self):
        """유튜브 바로 올리기와 합침(D-037): PC 에서 시킨 올리기도 끝·실패는 늘 알림 · 휴대폰에는 정해진 문장만
        (영상 주소·번호·Google 오류 글·토큰이 결과에 있어도 넣지 않음) · 진행·[멈추기]는 보이고 시작은 휴대폰 허용 목록에 없음."""
        import youtube_upload as yu
        self.assertTrue({yu.JOB_NAME, yu.JOB_FINISH} <= remote.JOB_LABELS)
        self.assertTrue({yu.JOB_NAME, yu.JOB_FINISH} <= remote.STOPPABLE)
        self.assertFalse([a for a in remote.ACTIONS if "youtube" in a.lower() or "upload" in a.lower()])
        self.turn_on()
        url, tok = "https://youtu.be/AbCdEfGhIjK", "ya29.SECRETTOKEN"
        self.svc.job_hook(yu.JOB_NAME, None, {"ok": True, "videoId": "AbCdEfGhIjK", "url": url, "warnings": [], "locked": False}, None, 3)
        self.assertEqual((self.svc.last["ok"], self.svc.last["error"]), (True, None))
        self.svc.job_hook(yu.JOB_NAME, None, {"ok": True, "videoId": "AbCdEfGhIjK", "url": url, "warnings": ["썸네일 · 실패"], "locked": False}, None, 90)
        self.assertEqual((self.svc.last["ok"], self.svc.last["warn"], self.svc.last["error"]), (False, True, remote.YOUTUBE_MSG["warn"]))
        self.svc.job_hook(yu.JOB_NAME, None, {"ok": False, "paused": True, "error": f"멈췄어요 {url}", "key": "k"}, None, 2)
        self.assertEqual(self.svc.last["error"], remote.YOUTUBE_MSG["paused"])
        self.svc.job_hook(yu.JOB_NAME, None, {"ok": False, "relogin": True, "error": f"다시 연결 {tok}"}, None, 40)
        self.svc.job_hook(yu.JOB_FINISH, f"boom {tok} {url}?upload_id=XYZ", None, None, 70)
        self.assertEqual(self.svc.last["error"], remote.YOUTUBE_MSG["failed"])
        self.svc.job_hook(yu.JOB_FINISH, None, {"ok": True, "videoId": "AbCdEfGhIjK", "url": url, "warnings": []}, None, 1)
        self.assertEqual(self.svc.last["ok"], True)  # PC 에서 누른 짧은 마무리: '마지막 작업' 칸만, 알림은 없음
        self.svc.job_hook(yu.JOB_FINISH, None, {"ok": True, "videoId": "AbCdEfGhIjK", "url": url, "warnings": []}, None, 90)
        got = self.notes(n=5)
        time.sleep(0.3)
        got = self.notes(n=5)
        # 멈춤(PC·휴대폰에서 사람이 누름)은 알림 없이 칸만 · 짧은 마무리도 조용히
        self.assertEqual([p[1] for p in got], [remote.YOUTUBE_MSG[k] for k in ("done", "warn", "relogin", "failed", "finish")])
        blob = json.dumps(got, ensure_ascii=False) + json.dumps(self.svc.last, ensure_ascii=False)
        for bad in ("youtu", "AbCdEfGhIjK", "ya29", "SECRET", "upload_id", "http"):
            self.assertNotIn(bad, "\n".join(p[1] for p in got) + json.dumps(self.svc.last, ensure_ascii=False), bad)
        self.assertNotIn("SECRETTOKEN", blob)

    def test_blocked_and_missed_are_attention(self):
        self.turn_on()
        self.svc.job_hook("학습용 영상 받기", None, {"ok": False, "error": "막힘", "blocked": True}, "휴대폰 · x", 30)
        self.assertEqual((self.svc.last["ok"], self.svc.last["warn"]), (False, True))
        self.assertIn("크롬 로그인 정보로 받기", self.svc.last["error"])
        self.svc.job_hook("보관함에 담기", None, ["AbCdEfGhIjK"], "휴대폰 · x", 30)
        texts = [p[1] for p in self.notes(n=2)]
        self.assertEqual(texts, ["확인이 필요해요 · YouTube가 막았어요", "확인이 필요해요 · 받지 못한 영상이 있어요"])

    def test_failed_download_is_not_shown_as_done(self):
        """검토(높음): 받지 못한 영상이 있는데 휴대폰 '마지막 작업'이 '보관함에 담기 끝 ✓' → 확인이 필요해요 + 할 일."""
        self.svc.job_hook("보관함에 담기", None, ["AbCdEfGhIjK"], "휴대폰 · x", 30)
        last = self.svc.last
        self.assertEqual((last["ok"], last["warn"]), (False, True))
        self.assertEqual(last["error"], remote.MISSED_MSG.format(n=1))
        self.assertIn("크롬 로그인 정보로 받기", last["error"])
        self.svc.job_hook("보관함에 담기", None, [], "휴대폰 · x", 30)  # 다 받음
        self.assertEqual((self.svc.last["ok"], self.svc.last["warn"]), (True, False))

    def test_settings_turn_off_kinds(self):
        self.turn_on()
        self.svc.set_settings({"notify": {"done": False, "failed": True, "attention": True}})
        self.svc.job_hook("내보내기", None, [], "휴대폰 · x", 100)
        self.svc.job_hook("내보내기", "실패", None, "휴대폰 · x", 100)
        got = self.notes()
        time.sleep(0.3)
        self.assertEqual([p[1] for p in self.ntfy.topic(self.svc.store.data["topics"]["notify"])], ["작업이 멈췄어요 · 내보내기 — 휴대폰에서 자세히 보기"])
        self.assertTrue(got)

    def test_new_device_push(self):
        self.turn_on()
        pair_device(self.svc, "새 폰")
        texts = [p[1] for p in self.notes()]
        self.assertIn(remote.NOTE_TEXT["paired"], texts)
        self.assertIn("내가 한 게 아니면", remote.NOTE_TEXT["paired"])  # 소유자가 받는 유일한 보안 신호 → 할 일까지
        self.assertIn("[끊기]", remote.NOTE_TEXT["paired"])

    def test_daily_budget(self):
        with mock.patch.object(remote, "DAILY_BUDGET", 6), mock.patch.object(remote, "BUDGET_SOFT", 3):
            self.svc.pub.used, self.svc.pub.day = 0, time.strftime("%Y-%m-%d", time.localtime(self.clock()))
            for i in range(5):
                self.svc.pub.notify("done", f"끝 {i}")
            for i in range(5):
                self.svc.pub.notify("attention", f"확인 {i}")
            time.sleep(1.0)
            texts = [p[1] for p in self.ntfy.topic(self.svc.store.data["topics"]["notify"])]
        self.assertEqual(texts, ["끝 0", "끝 1", "끝 2", "확인 0", "확인 1", "확인 2"])
        self.svc.pub.day = "1999-01-01"  # 자정이 지나면 다시
        self.svc.pub.notify("done", "다음 날")
        self.assertTrue(self.ntfy.wait(lambda p: p[1] == "다음 날"))

    def test_network_failure_logged_once_and_dropped(self):
        self.ntfy.fail = True
        self.svc.pub.notify("attention", "a")
        self.svc.pub.notify("attention", "b")
        end = time.time() + 5
        while time.time() < end and "  휴대폰 알림을 보내지 못했어요 · 인터넷 연결을 확인해 주세요" not in self.fb.lines:
            time.sleep(0.05)
        time.sleep(0.3)
        self.assertEqual(self.fb.lines.count("  휴대폰 알림을 보내지 못했어요 · 인터넷 연결을 확인해 주세요"), 1)

    def test_slow_ntfy_never_blocks_jobs_or_http(self):
        hole = socket.socket()
        hole.bind(("127.0.0.1", 0))
        hole.listen(50)  # 연결은 받되 대답하지 않는 서버
        self.addCleanup(hole.close)
        self.svc._ntfy = f"http://127.0.0.1:{hole.getsockname()[1]}"
        self.turn_on()
        t0 = time.time()
        for _ in range(3):
            self.assertTrue(self.fb.start_job("내보내기", lambda: None, by="휴대폰 · x"))
            self.assertTrue(self.fb.wait_idle(5))
        self.svc.test_notify()
        self.assertIsNotNone(self.svc.r_status(self.svc.store.device(self.a["device"]["id"]), 0))
        self.assertLess(time.time() - t0, 3)


class IdleTests(NotifyBase):
    def test_auto_off_after_idle_hours(self):
        self.turn_on()
        self.svc.set_settings({"autoOffHours": 1})
        self.clock.tick(3599)
        self.svc.tick()
        self.assertEqual(self.svc.state, "on")
        self.clock.tick(2)
        self.svc.tick()
        self.assertEqual(self.svc.state, "off")
        self.assertFalse(self.svc.store.data["enabled"])
        self.assertIn("원격 접속을 껐어요 (1시간 동안 안 써서)", [p[1] for p in self.notes()])
        last = open_beacon(self.beacons(n=2)[-1][1], self.a)
        self.assertEqual(last["reason"], "오래 쓰지 않아서 껐어요")

    def test_never_auto_off_while_job_runs_or_never_setting(self):
        self.turn_on()
        self.svc.set_settings({"autoOffHours": 1})
        self.fb.job_state["name"] = "내보내기"
        self.clock.tick(7200)
        self.svc.tick()
        self.assertEqual(self.svc.state, "on")
        self.fb.job_state["name"] = None
        self.svc.set_settings({"autoOffHours": 0})
        self.clock.tick(99999)
        self.svc.tick()
        self.assertEqual(self.svc.state, "on")

    def test_phone_use_resets_idle_timer(self):
        self.turn_on()
        self.svc.set_settings({"autoOffHours": 1})
        self.clock.tick(3000)
        self.svc.touch(self.svc.store.device(self.a["device"]["id"]))
        self.clock.tick(3000)
        self.svc.tick()
        self.assertEqual(self.svc.state, "on")
        self.assertEqual(self.svc.pc_status()["autoOffAt"], int(self.clock() - 3000 + 3600))

    def test_old_devices_expire_via_tick(self):
        old = dict(self.svc.store.data["topics"])
        self.clock.tick(remote.DEVICE_TTL + 10)
        self.svc.touch(self.svc.store.device(self.b["device"]["id"]))
        self.svc.tick()
        self.assertEqual([d["name"] for d in self.svc.store.data["devices"]], ["폰 B"])
        self.assertIn("휴대폰 연결을 끊었어요 · 폰 A (90일 동안 안 씀)", self.fb.lines)
        # 검토(높음): 오래 안 쓴 기기 정리가 쓰고 있는 휴대폰의 ntfy 알림을 조용히 끊지 않게 — 알림 주제는 그대로, 비콘 주제만
        self.assertEqual(self.svc.store.data["topics"]["notify"], old["notify"])
        self.assertNotEqual(self.svc.store.data["topics"]["beacon"], old["beacon"])
        time.sleep(0.3)
        self.assertEqual(self.ntfy.topic(old["notify"]), [])


class ThreadTests(unittest.TestCase):
    def test_keep_awake_thread_only_on_windows(self):
        home, cleanup = make_home()
        self.addCleanup(cleanup)
        def count(n):
            return sum(t.name == n for t in threading.enumerate())
        pub, awake = count("remote-publisher"), count("remote-awake")
        service(home)
        self.assertEqual(count("remote-publisher"), pub + 1)
        self.assertEqual(count("remote-awake") - awake, 1 if sys.platform == "win32" else 0)


if __name__ == "__main__":
    unittest.main()
