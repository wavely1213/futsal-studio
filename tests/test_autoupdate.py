"""자동 업데이트(D-311) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_autoupdate

core.auto_update_once: 주소 없음·끔(config "auto_update": false · 개발 폴더)·같은 버전·되돌렸던 버전은 아무것도 안 함 · 스튜디오를 쓰는
중이면 설치하지 않고 5분 뒤 다시(말은 한 번) · 실패(인터넷·구성요소·그 밖)는 버전·종류마다 기록에 한 번, 다음 확인 때 다시 ·
update_app 이 False 면 다시 시작 안 함 · 화면 알림(take_notice)은 가져가지 않음 · 성공하면 '자동' 표시 → 다시 시작 한 번
(가짜 앱 폴더 + test_update 의 이 컴퓨터 안 http.server 로 진짜 설치 · 같은 버전으로 두 번 다시 시작하지 않음 · 새 버전의 finish 가
'자동으로 …' 알림) · 뒤에서 도는 확인은 한 번만 · 꺼 두면 시작 안 함.
app: 쉬는 중 판단(하는 작업·다시 시작·편집실 ffmpeg·PC 화면 30분·밖에서 작업 세션 10분) · 작업 자리 잡기(그동안 새 작업 409) ·
다시 시작은 자리를 놓으며 RESTARTING 을 정한 뒤 · 화면이 저절로 묻는 요청(상태·핑·업데이트 확인·출처 찾기 등)은 '쓰는 중'으로 안 셈.
인터넷은 쓰지 않음.
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import remote  # noqa: E402
import rstudio  # noqa: E402
import source  # noqa: E402
import studiolog  # noqa: E402
import updater  # noqa: E402
from tests import test_update as tu  # noqa: E402


def setUpModule():
    tu.setUpModule()  # 가짜 manifest·zip 을 내려 줄 이 컴퓨터 안의 서버 (8851~8855)


def tearDownModule():
    tu.tearDownModule()


NET_ERR = "새 버전을 받지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요 · timed out"
PIP_ERR = "새 버전에 필요한 구성요소를 설치하지 못해 업데이트를 취소했어요 (이전 버전 그대로예요). 인터넷 연결을 확인하고 다시 시도해 주세요"


class Gate:
    """app 의 claim·done 흉내: why 가 있으면 '쓰는 중' · 부른 기록."""

    def __init__(self, why=None):
        self.why, self.claims, self.dones = why, 0, []

    def claim(self):
        self.claims += 1
        return self.why

    def done(self, restart):
        self.dones.append(restart)


class OnceBase(tu.Base):
    """core 를 임시 폴더에 복사해 불러온 것(tu.load_core)으로 · 인터넷 대신 가짜 manifest."""

    def setUp(self):
        super().setUp()
        self.c = self.core
        self.c.APP_DIR = Path(self.tmp) / "앱 폴더"
        self.c.APP_DIR.mkdir()
        self.c.CONFIG = {"update_manifest_url": "http://127.0.0.1:9/manifest.json"}
        self.c.VERSION = "1.0.0"
        self.gate = Gate()

    def once(self):
        return self.c.auto_update_once(self.logs.append, self.gate.claim, self.gate.done)

    def manifest(self, ver="1.1.0", **kw):
        return mock.patch.object(updater, "fetch_manifest", return_value=({"version": ver}, b"{}"), **kw)

    def said(self, part):
        return sum(1 for x in self.logs if part in x)


class NothingToDoTests(OnceBase):
    def test_empty_manifest_url_does_nothing(self):
        self.c.CONFIG = {"update_manifest_url": ""}
        self.c.DEFAULT_MANIFEST = ""
        with self.manifest() as fm, mock.patch.object(self.c, "update_app") as ua:
            self.assertEqual(self.once(), self.c.AUTO_EVERY)
        fm.assert_not_called()
        ua.assert_not_called()
        self.assertEqual((self.gate.claims, self.gate.dones, self.logs), (0, [], []))

    def test_opt_out_in_config(self):
        """config.json "auto_update": false (메모장으로 "false" 라 적어도) → 묻지도 않음 · 화면 단추는 '받기' 그대로."""
        for off in (False, "false", " FALSE ", 0, "off"):
            self.c.CONFIG = {"update_manifest_url": "http://127.0.0.1:9/manifest.json", "auto_update": off}
            self.assertFalse(self.c.auto_update_on(), off)
            with self.manifest() as fm:
                self.assertEqual(self.once(), self.c.AUTO_EVERY)
            fm.assert_not_called()
        self.assertEqual(self.gate.claims, 0)
        self.c.CONFIG["auto_update"] = True
        self.assertTrue(self.c.auto_update_on())

    def test_git_checkout_never_autoupdates(self):
        """개발 폴더(git)에서는 안 함 — 작업 중인 코드를 배포본으로 덮지 않게."""
        (self.c.APP_DIR / ".git").write_text("gitdir: 어딘가\n", encoding="utf-8")  # git worktree 는 .git 이 파일
        self.assertFalse(self.c.auto_update_on())
        with self.manifest() as fm:
            self.once()
        fm.assert_not_called()

    def test_same_or_older_version_does_not_claim(self):
        for ver in ("1.0.0", "0.9.9"):
            with self.manifest(ver), mock.patch.object(self.c, "update_app") as ua:
                self.assertEqual(self.once(), self.c.AUTO_EVERY)
            ua.assert_not_called()
        self.assertEqual(self.gate.claims, 0)

    def test_rolled_back_version_is_skipped(self):
        """이 PC 에서 안 열려 되돌렸던 버전(updater.skipped)은 자동으로도 받지 않음 · 더 새 버전은 받음."""
        updater._write_json(self.c.APP_DIR / updater.SKIP, {"version": "1.1.0"})
        with self.manifest(), mock.patch.object(self.c, "update_app") as ua:
            self.assertEqual(self.once(), self.c.AUTO_EVERY)
        ua.assert_not_called()
        self.assertEqual(self.gate.claims, 0)
        with self.manifest("1.2.0"), mock.patch.object(self.c, "update_app", return_value=False) as ua:
            self.once()
        ua.assert_called_once()

    def test_does_not_take_ui_notice(self):
        """방금 업데이트·되돌림 알림은 화면(check_update)이 가져갈 몫 — 뒤에서 확인해도 그대로."""
        updater._NOTICES.append({"text": "업데이트 완료 · v0.9 → v1.0.0", "warn": False})
        with self.manifest(), mock.patch.object(self.c, "update_app", return_value=False):
            self.once()
        self.assertEqual(updater.take_notice(), {"text": "업데이트 완료 · v0.9 → v1.0.0", "warn": False})


class WaitAndFailTests(OnceBase):
    def test_busy_waits_without_installing_and_says_once(self):
        self.gate.why = "PC 화면을 쓰는 중"
        with self.manifest(), mock.patch.object(self.c, "update_app") as ua:
            self.assertEqual(self.once(), self.c.AUTO_WAIT, "새 버전이 기다리면 5분 뒤 다시")
            self.assertEqual(self.once(), self.c.AUTO_WAIT)
        ua.assert_not_called()
        self.assertEqual(self.gate.dones, [], "자리를 잡지 않았으니 놓지도 않음")
        self.assertEqual(self.said("새 버전(v1.1.0)이 나왔어요"), 1, self.logs)

    def test_failure_logged_once_per_kind_and_retried(self):
        with self.manifest(), mock.patch.object(self.c, "update_app", side_effect=updater.UpdateError(NET_ERR)) as ua:
            for _ in range(3):
                self.assertEqual(self.once(), self.c.AUTO_EVERY, "실패하면 한 시간 뒤 다시")
        self.assertEqual(ua.call_count, 3, "다음 확인 때마다 다시 해 봄")
        self.assertEqual(self.gate.dones, [False] * 3, "자리는 늘 놓음 · 다시 시작 안 함")
        self.assertEqual(self.said("자동 업데이트를 마치지 못했어요"), 1, self.logs)
        self.assertEqual(self.said("자동 업데이트 · 스튜디오를 쓰지 않는 동안"), 1, self.logs)
        self.assertIn("인터넷 연결을 확인", self.logs[-1])

        def pip_fail(log):
            log("프로그램 업데이트 · v1.0.0 → v1.1.0")
            raise RuntimeError(PIP_ERR)
        with self.manifest(), mock.patch.object(self.c, "update_app", side_effect=pip_fail):
            self.once()
            self.once()
        self.assertEqual(self.said("구성요소를 설치하지 못해"), 1, "종류가 다르면 한 번 더 · 같은 종류는 한 번")
        self.assertEqual(self.said("프로그램 업데이트 · v1.0.0 → v1.1.0"), 1)

        with self.manifest(), mock.patch.object(self.c, "update_app", side_effect=KeyError("zip")), \
                mock.patch.object(studiolog, "trace") as tr:
            self.once()
        tr.assert_called_once()  # 예상 못 한 오류는 studio.log 에 위치도
        self.assertEqual(self.gate.dones, [False] * 6)
        self.assertFalse(self.c._auto_tried("1.1.0"))

    def test_manifest_failure_logged_once_until_it_works_again(self):
        err = updater.UpdateError("업데이트 정보를 받지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요 · refused")
        with mock.patch.object(updater, "fetch_manifest", side_effect=err):
            self.once()
            self.once()
        self.assertEqual(self.said("자동 업데이트 확인을 못 했어요"), 1)
        with self.manifest("1.0.0"):
            self.once()
        with mock.patch.object(updater, "fetch_manifest", side_effect=err):
            self.once()
        self.assertEqual(self.said("자동 업데이트 확인을 못 했어요"), 2, "다시 끊기면 한 번 더")
        self.assertEqual(self.gate.claims, 0)

    def test_no_restart_when_update_app_returns_false(self):
        with self.manifest(), mock.patch.object(self.c, "update_app", return_value=False):
            self.once()
        self.assertEqual(self.gate.dones, [False])
        self.assertFalse(self.c._auto_tried("1.1.0"))
        self.assertFalse(any("다시 시작해요" in x for x in self.logs), self.logs)

    def test_progress_cleared_after_install(self):
        def upd(log):
            self.c.set_progress(label="업데이트 받는 중", pct=40)
            return False
        with self.manifest(), mock.patch.object(self.c, "update_app", side_effect=upd):
            self.once()
        self.assertEqual(self.c.PROGRESS, {})


class InstallTests(OnceBase):
    def test_success_marks_auto_restarts_once_and_new_version_says_auto(self):
        case = tu.Case(self.tmp)
        self.c.APP_DIR = case.app
        self.c.CONFIG = {"update_manifest_url": case.url}
        self.assertTrue(self.c.check_update().get("auto"), "화면 단추: '쉴 때 자동으로 받아요'")
        self.assertEqual(self.once(), self.c.AUTO_EVERY)
        self.assertEqual(self.gate.dones, [True], "설치했으면 다시 시작")
        self.assertEqual(case.read("version.txt"), b"1.1.0\n")
        self.assertIs(updater._read_json(case.app / updater.PENDING).get("auto"), True)
        self.assertTrue(self.c._auto_tried("1.1.0"))
        self.assertEqual(self.logs[-1], "자동 업데이트로 다시 시작해요 (v1.0.0 → v1.1.0)")
        self.assertIn("  완료 · 프로그램을 다시 시작해요", self.logs)

        # 다시 시작을 못 했거나 새 버전이 되돌려짐 (이 프로세스는 아직 1.0.0) → 같은 버전으로 또 다시 시작하지 않음
        self.assertEqual(self.once(), self.c.AUTO_EVERY)
        self.once()
        self.assertEqual((self.gate.claims, self.gate.dones), (1, [True]))
        self.assertEqual(self.said("한 번 다시 시작했지만 마치지 못했어요"), 1)
        u = self.c.check_update()
        self.assertTrue(u["available"])
        self.assertNotIn("auto", u, "그 버전은 손으로 받기")

        # 새 버전이 켜짐 → 실행기 확인 → 앱이 켜진 뒤 finish: '자동으로 …' 한 줄
        self.assertEqual(updater.check(case.app, python=sys.executable), "ok")
        self.assertEqual(updater.finish(case.app), ["업데이트 완료 · v1.0.0 → v1.1.0 (자동)"])
        self.assertEqual(updater.take_notice(), {"text": "자동으로 새 버전(v1.1.0)으로 업데이트했어요", "warn": False})

    def test_manual_update_notice_unchanged(self):
        case = tu.Case(self.tmp)
        self.assertTrue(self.update(case))
        self.assertEqual(updater.check(case.app, python=sys.executable), "ok")
        self.assertEqual(updater.finish(case.app), ["업데이트 완료 · v1.0.0 → v1.1.0"])
        self.assertEqual(updater.take_notice()["text"], "업데이트 완료 · v1.0.0 → v1.1.0")

    def test_check_update_without_auto_when_opted_out(self):
        case = tu.Case(self.tmp)
        self.c.APP_DIR = case.app
        self.c.CONFIG = {"update_manifest_url": case.url, "auto_update": False}
        u = self.c.check_update()
        self.assertTrue(u["available"])
        self.assertNotIn("auto", u)


class LoopTests(OnceBase):
    def test_background_loop_starts_once_and_not_when_off(self):
        self.c.CONFIG["auto_update"] = False
        self.c.app_autoupdate(self.logs.append, self.gate.claim, self.gate.done)
        self.assertIsNone(self.c._AUTO["thread"], "꺼 두면 시작 안 함")
        self.c.CONFIG["auto_update"] = True
        self.c.AUTO_FIRST = 10 ** 6  # 시험 동안 깨지 않게 (데몬 스레드라 끝날 때 같이 사라짐)
        self.c.app_autoupdate(self.logs.append, self.gate.claim, self.gate.done)
        t = self.c._AUTO["thread"]
        self.assertTrue(t.is_alive() and t.daemon and t.name == "app-autoupdate")
        self.c.app_autoupdate(self.logs.append, self.gate.claim, self.gate.done)
        self.assertIs(self.c._AUTO["thread"], t, "한 번만")


class AppGateTests(unittest.TestCase):
    """app 의 쉬는 중 판단·작업 자리 (D-311)."""

    def setUp(self):
        self.addCleanup(app.RESTARTING.clear)
        old = dict(app.JOB)
        self.addCleanup(lambda: (app.JOB.clear(), app.JOB.update(old)))
        app.JOB["name"] = None
        app.RESTARTING.clear()
        for p in (mock.patch.dict(app._SEEN, {"local": time.time() - app.AUTO_LOCAL_QUIET - 5}),
                  mock.patch.object(editor, "_PROCS", set()), mock.patch.object(remote.SVC, "studio", None)):
            p.start()
            self.addCleanup(p.stop)

    def test_idle_when_nothing_is_going_on(self):
        self.assertIsNone(app._auto_why())

    def test_job_running_blocks_and_claim_leaves_job_alone(self):
        app.JOB["name"] = "내보내기"
        self.assertIn("내보내기", app._auto_why())
        self.assertTrue(app._auto_claim())
        self.assertEqual(app.JOB["name"], "내보내기")

    def test_restarting_blocks(self):
        app.RESTARTING.set()
        self.assertEqual(app._auto_why(), "다시 시작하는 중")

    def test_editor_exporting_outside_job_blocks(self):
        editor._PROCS.add(object())  # 작업 밖 미리보기·내보내기 ffmpeg
        self.assertEqual(app._auto_why(), "편집실이 파일을 만드는 중")

    def test_recent_local_action_blocks_for_30_minutes(self):
        app._SEEN["local"] = time.time() - 60
        self.assertEqual(app._auto_why(), "PC 화면을 쓰는 중")
        app._SEEN["local"] = time.time() - app.AUTO_LOCAL_QUIET + 30
        self.assertEqual(app._auto_why(), "PC 화면을 쓰는 중")
        app._SEEN["local"] = time.time() - app.AUTO_LOCAL_QUIET - 1
        self.assertIsNone(app._auto_why())

    def test_active_remote_session_blocks_for_10_minutes(self):
        t = [1_000_000.0]
        sessions = rstudio.Sessions(clock=lambda: t[0])
        sessions.create({"id": "dev1", "name": "노트북"}, "203.0.113.0/24")
        remote.SVC.studio = types.SimpleNamespace(sessions=sessions)
        self.assertEqual(app._auto_why(), "밖에서 작업 중")
        t[0] += app.AUTO_REMOTE_QUIET - 30
        self.assertEqual(app._auto_why(), "밖에서 작업 중")
        t[0] += 60
        self.assertIsNone(app._auto_why(), "10분 넘게 요청이 없으면 (세션이 아직 살아 있어도) 쉬는 중")

    def test_claim_holds_job_slot_until_done(self):
        self.assertIsNone(app._auto_claim())
        self.assertEqual(app.JOB["name"], app.AUTO_JOB)
        self.assertFalse(app.start_job("편집점 찾기", lambda: None), "설치하는 동안 새 작업은 409")
        self.assertIn(app.AUTO_JOB, app._auto_why())
        with mock.patch.object(app, "restart") as rs:
            app._auto_done(False)
        rs.assert_not_called()
        self.assertIsNone(app.JOB["name"])
        self.assertFalse(app.RESTARTING.is_set())

    def test_done_with_restart_sets_restarting_before_restart(self):
        self.assertIsNone(app._auto_claim())
        seen = {}

        def rs():
            seen.update(restarting=app.RESTARTING.is_set(), job=app.JOB["name"])
        with mock.patch.object(app, "restart", side_effect=rs) as m:
            app._auto_done(True)
        m.assert_called_once()
        self.assertEqual(seen, {"restarting": True, "job": None}, "자리를 놓는 순간 다시 시작이 정해짐 → 끼어드는 작업 없음")
        self.assertFalse(app.start_job("편집점 찾기", lambda: None))

    def test_after_start_starts_background_check(self):
        with mock.patch.object(updater, "finish", return_value=[]), mock.patch.object(core, "CONFIG_NOTES", []), \
                mock.patch.object(app, "_session_start"), mock.patch.object(app, "_place_kept"), \
                mock.patch.object(core, "engine_autoupdate"), mock.patch.object(core, "app_autoupdate") as au:
            app._after_start()
        au.assert_called_once_with(app.log, app._auto_claim, app._auto_done)

    def test_remote_studio_requests_are_not_pc_activity(self):
        before = app._SEEN["local"]
        app.StudioHandler._seen(object.__new__(app.StudioHandler), "GET", "/api/edit/library")
        self.assertEqual(app._SEEN["local"], before)


class ActivityRouteTests(unittest.TestCase):
    """실제 서버(app.Handler)로: 화면이 저절로 묻는 요청은 'PC 화면을 쓰는 중'으로 세지 않음."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="자동 업데이트 시험 "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        work = self.tmp / "작업"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        patches += [mock.patch.object(app, "PORT", port), mock.patch.dict(app._SEEN, {"local": 1000.0}),
                    mock.patch.object(core, "check_update", return_value={"current": "1.0.0", "available": False}),
                    mock.patch.object(source, "start_backfill"), mock.patch.object(source, "backfill_running", return_value=False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{port}"

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_polls_do_not_count_but_user_requests_do(self):
        for path in ("/api/state?since=0", "/api/ping", "/api/auto/pending", "/api/update/check", "/api/remote"):
            self.call(path)
        self.call("/api/source/check", {})
        self.assertEqual(app._SEEN["local"], 1000.0, "상태·핑·썸네일 요청·업데이트 확인·원격 상태·출처 찾기는 사람이 한 일이 아님")
        t0 = time.time()
        self.assertEqual(self.call("/api/dict"), 200)
        self.assertGreaterEqual(app._SEEN["local"], t0, "용어 사전 열기는 사람이 한 일")
        app._SEEN["local"] = 1000.0
        self.assertEqual(self.call("/api/dict", {"terms": [], "fix": {}}), 200)
        self.assertGreaterEqual(app._SEEN["local"], t0, "저장(POST)도")

    def test_forbidden_host_does_not_count(self):
        req = urllib.request.Request(self.base + "/api/dict", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=30)
        self.assertEqual(app._SEEN["local"], 1000.0)


if __name__ == "__main__":
    unittest.main()
