"""자동 업데이트(D-311) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_autoupdate

core.auto_update_once: 주소 없음·끔(config "auto_update": false · 켜 둔 채 바꿔도 다음 확인부터 · 개발 폴더)·같은 버전·되돌렸던 버전은
아무것도 안 함 · 스튜디오를 쓰는 중이면 설치하지 않고 5분 뒤 다시(말은 한 번) · 실패(인터넷·구성요소·그 밖)는 버전·종류마다 기록에 한 번
(쉬운 한 줄 · '다시 시도해 주세요' 없음 · 원문·오류 위치도 한 번), 다음 확인 때 다시 · update_app 이 False 면 다시 시작 안 함 ·
화면 알림(take_notice)은 가져가지 않음 · 성공하면 '자동' 표시 → 다시 시작 한 번 (가짜 앱 폴더 + test_update 의 이 컴퓨터 안
http.server 로 진짜 설치 · 같은 버전으로 두 번 다시 시작하지 않음 · 새 버전의 finish 가 '자동으로 …' 알림 → 화면은 남는 카드) ·
뒤에서 도는 확인은 한 번만 · 개발 폴더면 시작 안 함 · 뒤 스레드 오류 위치는 같은 오류면 한 번.
app: 쉬는 중 판단(하는 작업·다시 시작·편집실 ffmpeg·밖에서 주고받는 파일(처리 중인 원격 요청·올리기 조각)·막 끝난 작업 2분·
PC 화면 30분·밖에서 작업 세션·원격 홈 10분·Windows 키보드·마우스 10분) · 작업 자리 잡기(새 번호 · 그동안 새 작업 409) ·
설치 뒤 그사이 시작된 일을 기다린 뒤(파일·ffmpeg 는 끝까지 · 사람은 길어야 10분) 자리를 놓으며 RESTARTING → 내려 둔 창으로 다시 시작 ·
휴대폰 '마지막 작업' 칸 · 화면이 저절로 묻는 요청(상태·핑·업데이트 확인·출처 찾기 · X-FS-Auto 표시)은 '쓰는 중'으로 안 셈.
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
    """app 의 claim·done 흉내: why 가 있으면 '쓰는 중' · 부른 기록 (dones: 다시 시작 여부 · fails: 실패 한 줄)."""

    def __init__(self, why=None):
        self.why, self.claims, self.dones, self.fails = why, 0, [], []

    def claim(self):
        self.claims += 1
        return self.why

    def done(self, restart, fail=None):
        self.dones.append(restart)
        self.fails.append(fail)


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
        self.raw = []  # studio.log 에만 쓰는 줄 (진짜 작업 폴더의 기록을 건드리지 않게)
        p = mock.patch.object(studiolog, "write", side_effect=self.raw.append)
        p.start()
        self.addCleanup(p.stop)

    def config(self, **kw):
        """앱 폴더의 config.json (사용자가 메모장으로 고치는 그 파일)."""
        (self.c.APP_DIR / "config.json").write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")

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
            self.config(auto_update=off)
            self.assertFalse(self.c.auto_update_on(), off)
            with self.manifest() as fm:
                self.assertEqual(self.once(), self.c.AUTO_EVERY)
            fm.assert_not_called()
        self.assertEqual(self.gate.claims, 0)
        self.config(auto_update=True)
        self.assertTrue(self.c.auto_update_on())

    def test_opt_out_takes_effect_while_running(self):
        """켜 둔 채 메모장으로 넣어도 다음 확인부터 (켤 때 읽은 CONFIG 가 아니라 그때의 파일) · 빼면 다시 켬."""
        self.c.CONFIG = {"update_manifest_url": "http://127.0.0.1:9/manifest.json"}  # 켤 때는 켬
        self.assertTrue(self.c.auto_update_on())
        self.config(channel_url="x", auto_update=False)
        with self.manifest() as fm, mock.patch.object(self.c, "update_app") as ua:
            self.once()
        fm.assert_not_called()
        ua.assert_not_called()
        self.config(channel_url="x")
        self.assertTrue(self.c.auto_update_on(), "키를 빼면 다시 켬 (기본 켬)")
        (self.c.APP_DIR / "config.json").write_text('{"auto_update": false,,', encoding="utf-8")  # 메모장으로 고치다 깨짐
        self.assertTrue(self.c.auto_update_on(), "지금 못 읽으면 켤 때 읽은 값")
        self.c.CONFIG["auto_update"] = False
        self.assertFalse(self.c.auto_update_on())
        (self.c.APP_DIR / "config.json").write_text('\ufeff{"auto_update": "false"}', encoding="utf-8")  # 메모장의 BOM
        self.c.CONFIG["auto_update"] = True
        self.assertFalse(self.c.auto_update_on())

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
        self.assertEqual(self.logs[-1], "  자동 업데이트를 마치지 못했어요 · 새 버전을 받지 못했어요. 인터넷 연결을 확인해 주세요 · "
                                        "1시간 뒤에 저절로 다시 해 봐요", "다시 시도하라는 말 없이 · 영어 원문 없이")
        self.assertEqual(self.raw, ["  원문 · " + NET_ERR], "원문은 studio.log 에만 한 번")
        self.assertEqual(self.gate.fails, ["새 버전을 받지 못했어요. 인터넷 연결을 확인해 주세요"] * 3, "휴대폰 '마지막 작업' 칸")

        def pip_fail(log):
            log("프로그램 업데이트 · v1.0.0 → v1.1.0")
            raise RuntimeError(PIP_ERR)
        with self.manifest(), mock.patch.object(self.c, "update_app", side_effect=pip_fail):
            self.once()
            self.once()
        self.assertEqual(self.said("구성요소를 설치하지 못해"), 1, "종류가 다르면 한 번 더 · 같은 종류는 한 번")
        self.assertEqual(self.said("프로그램 업데이트 · v1.0.0 → v1.1.0"), 1)
        self.assertFalse([x for x in self.logs if "다시 시도해 주세요" in x], self.logs)

        with self.manifest(), mock.patch.object(self.c, "update_app", side_effect=OSError(28, "No space left on device")), \
                mock.patch.object(studiolog, "trace") as tr:
            for _ in range(5):  # 디스크가 찬 채로 1시간마다 다시
                self.once()
        tr.assert_called_once()  # 예상 못 한 오류는 studio.log 에 위치도 — 그 버전·그 종류는 처음 한 번만
        line = [x for x in self.logs if "마치지 못했어요" in x][-1]
        self.assertIn("저장 공간이 부족해요", line, "그 밖의 오류는 쉬운 말 (trouble.explain)")
        self.assertNotIn("No space", line)
        self.assertEqual(self.gate.dones, [False] * 10)
        self.assertFalse(self.c._auto_tried("1.1.0"))

    def test_manifest_failure_logged_once_until_it_works_again(self):
        err = updater.UpdateError("업데이트 정보를 받지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요 · refused")
        with mock.patch.object(updater, "fetch_manifest", side_effect=err):
            self.once()
            self.once()
        self.assertEqual(self.said("자동 업데이트 확인을 못 했어요"), 1)
        self.assertEqual(self.logs[-1], "자동 업데이트 확인을 못 했어요 · 업데이트 정보를 받지 못했어요. 인터넷 연결을 확인해 주세요 · "
                                        "1시간 뒤에 저절로 다시 해 봐요")
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
        self.assertEqual(updater.take_notice(), {"text": "자동으로 새 버전(v1.1.0)으로 업데이트했어요", "warn": False, "auto": True})
        updater._NOTICES.append({"text": "자동으로 새 버전(v1.1.0)으로 업데이트했어요", "warn": False, "auto": True})
        u = self.c.check_update()
        self.assertEqual((u["notice"], u["notice_warn"], u.get("notice_auto")), ("자동으로 새 버전(v1.1.0)으로 업데이트했어요", False, True),
                         "화면은 사라지는 알림 대신 남는 카드")

    def test_manual_update_notice_unchanged(self):
        case = tu.Case(self.tmp)
        self.assertTrue(self.update(case))
        self.assertEqual(updater.check(case.app, python=sys.executable), "ok")
        self.assertEqual(updater.finish(case.app), ["업데이트 완료 · v1.0.0 → v1.1.0"])
        n = updater.take_notice()
        self.assertEqual(n["text"], "업데이트 완료 · v1.0.0 → v1.1.0")
        self.assertNotIn("auto", n)

    def test_check_update_without_auto_when_opted_out(self):
        case = tu.Case(self.tmp)
        self.c.APP_DIR = case.app
        self.c.CONFIG = {"update_manifest_url": case.url}
        (case.app / "config.json").write_text('{"auto_update": false}', encoding="utf-8")
        u = self.c.check_update()
        self.assertTrue(u["available"])
        self.assertNotIn("auto", u)


class LoopTests(OnceBase):
    def test_background_loop_starts_once_not_in_dev_folder(self):
        (self.c.APP_DIR / ".git").mkdir()
        self.c.app_autoupdate(self.logs.append, self.gate.claim, self.gate.done)
        self.assertIsNone(self.c._AUTO["thread"], "개발 폴더는 시작 안 함")
        (self.c.APP_DIR / ".git").rmdir()
        self.config(auto_update=False)  # 꺼 두었어도 돌며 확인할 때마다 설정을 읽음 (켜 둔 채 다시 켤 수 있게)
        self.c.AUTO_FIRST = 10 ** 6  # 시험 동안 깨지 않게 (데몬 스레드라 끝날 때 같이 사라짐)
        self.c.app_autoupdate(self.logs.append, self.gate.claim, self.gate.done)
        t = self.c._AUTO["thread"]
        self.assertTrue(t.is_alive() and t.daemon and t.name == "app-autoupdate")
        self.c.app_autoupdate(self.logs.append, self.gate.claim, self.gate.done)
        self.assertIs(self.c._AUTO["thread"], t, "한 번만")

    def test_loop_error_traced_once_per_error(self):
        """뒤 스레드에서 같은 오류가 1시간마다 되풀이돼도 오류 위치는 한 번 · 달라지거나 한 번 잘 되면 다시."""
        errs = [RuntimeError("고장 1")] * 3 + [RuntimeError("고장 2"), None, RuntimeError("고장 2")]

        def once(*_a):
            e = errs.pop(0)
            if e:
                raise e
            return self.c.AUTO_WAIT
        with mock.patch.object(self.c, "auto_update_once", side_effect=once), mock.patch.object(studiolog, "trace") as tr:
            waits = [self.c._auto_step(self.logs.append, self.gate.claim, self.gate.done) for _ in range(6)]
        self.assertEqual(waits, [self.c.AUTO_EVERY] * 4 + [self.c.AUTO_WAIT, self.c.AUTO_EVERY])
        self.assertEqual([str(c.args[0]) for c in tr.call_args_list], ["고장 1", "고장 2", "고장 2"])


class GateBase(unittest.TestCase):
    """app 의 쉬는 중 판단을 '아무 일도 없음'으로 맞춰 둠 (PC 화면·원격 홈 조용 · 작업·ffmpeg·원격 요청 없음 · 키보드 모름)."""

    def setUp(self):
        self.addCleanup(app.RESTARTING.clear)
        old = dict(app.JOB)
        self.addCleanup(lambda: (app.JOB.clear(), app.JOB.update(old)))
        app.JOB["name"] = None
        app.RESTARTING.clear()
        for p in (mock.patch.dict(app._SEEN, {"local": time.time() - app.AUTO_LOCAL_QUIET - 5, "job": 0.0}),
                  mock.patch.object(editor, "_PROCS", set()), mock.patch.object(remote.SVC, "studio", None),
                  mock.patch.object(remote.SVC, "last_auth", time.time() - app.AUTO_REMOTE_QUIET - 5),
                  mock.patch.object(remote.SVC, "_busy", 0), mock.patch.object(remote.SVC, "last", None),
                  mock.patch.object(core, "input_idle_secs", return_value=None)):
            p.start()
            self.addCleanup(p.stop)


class AppGateTests(GateBase):
    """app 의 쉬는 중 판단·작업 자리 (D-311)."""

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
        remote.SVC.studio = types.SimpleNamespace(sessions=sessions, uploads=rstudio.Uploads(types.SimpleNamespace()))
        self.assertEqual(app._auto_why(), "밖에서 작업 중")
        t[0] += app.AUTO_REMOTE_QUIET - 30
        self.assertEqual(app._auto_why(), "밖에서 작업 중")
        t[0] += 60
        self.assertIsNone(app._auto_why(), "10분 넘게 요청이 없으면 (세션이 아직 살아 있어도) 쉬는 중")

    def test_claim_holds_job_slot_until_done(self):
        app.JOB.update(id=41, error="앞 작업의 오류", result={"앞": 1}, fail={"kind": "other"})
        self.assertIsNone(app._auto_claim())
        self.assertEqual(app.JOB["name"], app.AUTO_JOB)
        self.assertEqual((app.JOB["id"], app.JOB["error"], app.JOB["result"], app.JOB["fail"]), (42, None, None, None),
                         "새 번호 · 앞 작업의 결과·오류를 이 자리가 다시 내보이지 않음")
        self.assertFalse(app.start_job("편집점 찾기", lambda: None), "설치하는 동안 새 작업은 409")
        self.assertIn(app.AUTO_JOB, app._auto_why())
        with mock.patch.object(app, "restart") as rs:
            app._auto_done(False, "새 버전을 받지 못했어요. 인터넷 연결을 확인해 주세요")
        rs.assert_not_called()
        self.assertIsNone(app.JOB["name"])
        self.assertFalse(app.RESTARTING.is_set())
        last = remote.SVC.last
        self.assertEqual((last["name"], last["ok"], last["warn"], last["by"], last["error"]),
                         (app.AUTO_JOB, False, True, None, "새 버전을 받지 못했어요. 인터넷 연결을 확인해 주세요"),
                         "휴대폰 '마지막 작업' 칸 → 원격 홈이 그 전 작업의 끝을 또 알리지 않음")

    def test_auto_job_has_phone_label(self):
        self.assertIn(app.AUTO_JOB, remote.JOB_LABELS)
        self.assertEqual(remote._label(app.AUTO_JOB), app.AUTO_JOB, "비콘·알림에 '작업' 대신")

    def test_done_with_restart_sets_restarting_before_restart(self):
        self.assertIsNone(app._auto_claim())
        seen = {}

        def rs(auto=False):
            seen.update(restarting=app.RESTARTING.is_set(), job=app.JOB["name"], auto=auto, last=dict(remote.SVC.last))
        with mock.patch.object(app, "restart", side_effect=rs) as m:
            app._auto_done(True)
        m.assert_called_once()
        self.assertEqual({k: seen[k] for k in ("restarting", "job", "auto")}, {"restarting": True, "job": None, "auto": True},
                         "자리를 놓는 순간 다시 시작이 정해짐 → 끼어드는 작업 없음 · 새 창은 내려 둔 채로")
        self.assertEqual((seen["last"]["name"], seen["last"]["ok"]), (app.AUTO_JOB, True))
        self.assertFalse(app.start_job("편집점 찾기", lambda: None))

    def test_remote_transfer_in_flight_blocks(self):
        """휴대폰이 완성본을 받는 중(/r/m · /s/dl 하나로 긴 요청) — 화면을 잠가 원격 홈이 묻지 않아도 끝날 때까지."""
        remote.SVC._busy = 1
        self.assertEqual(app._auto_why(), "밖에서 파일을 주고받는 중")
        remote.SVC._busy = 0
        self.assertIsNone(app._auto_why())

    def test_remote_listener_counts_requests_in_flight(self):
        svc = remote.Service()
        seen = []

        def handle(method, s):
            seen.append(s.in_flight())
            if method == "POST":
                raise ConnectionResetError("휴대폰이 끊음")
        h = types.SimpleNamespace(server=types.SimpleNamespace(svc=svc), _handle=handle, _json=lambda *a, **k: None)
        remote.RemoteHandler._route(h, "GET")
        remote.RemoteHandler._route(h, "POST")
        self.assertEqual(seen, [1, 1], "처리하는 동안 1")
        self.assertEqual(svc.in_flight(), 0, "끝나면(끊겨도) 0")

    def test_upload_between_chunks_blocks(self):
        ups = rstudio.Uploads(types.SimpleNamespace())
        ups.items = {"a" * 32: {"updated": time.time() - 30}, "b" * 32: {"updated": time.time() - 3600}}
        remote.SVC.studio = types.SimpleNamespace(uploads=ups, sessions=rstudio.Sessions())
        self.assertEqual(app._auto_why(), "밖에서 파일을 올리는 중")
        self.assertEqual(ups.active(app.AUTO_UPLOAD_GAP), 1, "멈춰 둔 올리기는 안 셈")
        ups.items["a" * 32]["updated"] = time.time() - app.AUTO_UPLOAD_GAP - 1
        self.assertIsNone(app._auto_why())
        self.assertEqual(rstudio.Uploads(types.SimpleNamespace()).active(60), 0, "아직 안 읽은 목록")

    def test_phone_home_recently_used_blocks(self):
        """원격 홈(휴대폰 · 화면이 보일 때만 10초마다 물음)의 마지막 요청 뒤 10분 — 세션 없는 원격 홈도 셈."""
        remote.SVC.last_auth = time.time() - 60
        self.assertEqual(app._auto_why(), "원격 홈을 보는 중")
        remote.SVC.last_auth = time.time() - app.AUTO_REMOTE_QUIET - 1
        self.assertIsNone(app._auto_why())

    def test_just_finished_job_blocks_for_two_minutes(self):
        """편집실 내보내기가 끝나고 화면이 0.7초 뒤 자동 검수를 시킴 — 그 틈에 자리를 잡지 않음."""
        done = threading.Event()
        self.assertTrue(app.start_job("내보내기", done.wait))
        done.set()
        for _ in range(200):
            if not app.JOB["name"]:
                break
            time.sleep(0.01)
        self.assertEqual(app._auto_claim(), "작업이 막 끝남")
        self.assertTrue(app.start_job("영상 검수", lambda: None), "이어서 시킨 작업은 그대로 시작")
        for _ in range(200):
            if not app.JOB["name"]:
                break
            time.sleep(0.01)
        app._SEEN["job"] = time.time() - app.AUTO_AFTER_JOB - 1
        self.assertIsNone(app._auto_why())

    def test_windows_input_idle(self):
        """다른 프로그램을 쓰는 중(키보드·마우스)이면 창이 그 앞에 다시 뜨지 않게 기다림 · 못 읽으면(None) 건너뜀."""
        with mock.patch.object(core, "input_idle_secs", return_value=30.0):
            self.assertEqual(app._auto_why(), "PC를 쓰는 중")
        with mock.patch.object(core, "input_idle_secs", return_value=app.AUTO_INPUT_QUIET + 1.0):
            self.assertIsNone(app._auto_why())


class SettleTests(GateBase):
    """설치를 마친 뒤 다시 시작하기 전: 그사이 시작된 일을 기다림 (가짜 시계·잠)."""

    def run_settle(self, step):
        t = [1000.0]
        n = [0]

        def sleep(sec):
            self.assertEqual(sec, app.AUTO_SETTLE_STEP)
            self.assertEqual(app.JOB["name"], app.AUTO_JOB, "기다리는 동안 자리는 그대로 (새 작업 409)")
            t[0] += sec
            n[0] += 1
            step(n[0])
            self.assertLess(n[0], 10_000)
        logs = []
        with mock.patch.object(app, "log", side_effect=logs.append):
            app._auto_settle(sleep=sleep, clock=lambda: t[0])
        return n[0], logs

    def setUp(self):
        super().setUp()
        self.assertIsNone(app._auto_claim())

    def test_nothing_started_restarts_at_once(self):
        n, logs = self.run_settle(lambda i: None)
        self.assertEqual((n, logs), (0, []))

    def test_waits_for_editor_ffmpeg_even_past_cap(self):
        editor._PROCS.add(object())
        cap = app.AUTO_SETTLE_MAX // app.AUTO_SETTLE_STEP
        n, logs = self.run_settle(lambda i: editor._PROCS.clear() if i == cap + 5 else None)
        self.assertEqual(n, cap + 5, "파일을 만드는 중이면 끝까지")
        self.assertEqual(logs, ["  새 버전을 받았어요 · 하던 일이 끝나면 다시 시작해요"], "한 번만")
        self.assertFalse(app.start_job("편집점 찾기", lambda: None))

    def test_waits_for_remote_transfer(self):
        remote.SVC._busy = 1
        n, _ = self.run_settle(lambda i: setattr(remote.SVC, "_busy", 0) if i == 3 else None)
        self.assertEqual(n, 3)

    def test_person_came_back_waits_until_quiet(self):
        app._SEEN["local"] = time.time()  # 설치하는 사이 PC 창에서 무언가 함

        def step(i):
            if i == 4:  # 손을 뗌
                app._SEEN["local"] = time.time() - app.AUTO_SETTLE_QUIET - 1
        n, logs = self.run_settle(step)
        self.assertEqual((n, len(logs)), (4, 1))

    def test_person_keeps_using_gives_up_after_cap(self):
        def step(i):
            app._SEEN["local"] = time.time()
            remote.SVC.last_auth = time.time()
        step(0)
        n, _ = self.run_settle(step)
        self.assertEqual(n, app.AUTO_SETTLE_MAX // app.AUTO_SETTLE_STEP, "사람이 계속 쓰면 길어야 10분 (그동안 새 작업은 막힘)")

    def test_done_waits_before_restart(self):
        order = []
        with mock.patch.object(app, "_auto_settle", side_effect=lambda: order.append(("settle", app.JOB["name"]))), \
                mock.patch.object(app, "restart", side_effect=lambda auto=False: order.append(("restart", app.JOB["name"]))):
            app._auto_done(True)
        self.assertEqual(order, [("settle", app.AUTO_JOB), ("restart", None)])


class RestartWindowTests(unittest.TestCase):
    """자동 업데이트의 다시 시작 → 새 프로세스에 FUTSAL_AUTO → 창은 내려 둔 채로 · 키보드·마우스 쉼은 Windows 에서만."""

    @unittest.skipIf(sys.platform == "win32", "Windows 밖에서만")
    def test_input_idle_unknown_outside_windows(self):
        self.assertIsNone(core.input_idle_secs())

    def test_restart_passes_auto_flag(self):
        envs = []
        with mock.patch.object(app.subprocess, "Popen", side_effect=lambda *a, **k: envs.append(k["env"])), \
                mock.patch.object(app, "_quit"), mock.patch.object(remote.SVC, "shutdown"), mock.patch.object(app, "log"):
            app.restart(auto=True)
            app.restart()
        self.assertEqual([e.get("FUTSAL_AUTO") for e in envs], ["1", None])
        self.assertTrue(all(e["FUTSAL_RESTART"] == "1" for e in envs))

    def test_window_minimized_after_auto_restart(self):
        calls = []

        class Web:
            @staticmethod
            def create_window(title, url, **kw):
                calls.append(kw)
                return "창"
        self.assertEqual(app._create_window(Web, "http://x/", True), "창")
        app._create_window(Web, "http://x/")
        self.assertEqual([c.get("minimized") for c in calls], [True, None])
        self.assertEqual(calls[1], {"width": 1320, "height": 860, "min_size": (960, 640)})

        class OldWeb:  # 예전 pywebview: minimized 를 모름
            @staticmethod
            def create_window(title, url, width, height, min_size):
                return (width, height)
        self.assertEqual(app._create_window(OldWeb, "http://x/", True), (1320, 860))

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

    def test_auto_marked_requests_do_not_count(self):
        """화면이 저절로 보낸 요청 (X-FS-Auto: 1 · 7단계 처리 확인 뒤 새로 고침) — 길이 같아도 사람이 누른 것과 가름."""
        req = urllib.request.Request(self.base + "/api/dict", headers={app.AUTO_HEADER: "1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(app._SEEN["local"], 1000.0)
        self.assertNotIn(("POST", "/api/youtube/check"), app.QUIET_REQS, "[확인]을 누른 것은 셈 (저절로 확인은 표시로)")

    def test_forbidden_host_does_not_count(self):
        req = urllib.request.Request(self.base + "/api/dict", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=30)
        self.assertEqual(app._SEEN["local"], 1000.0)


if __name__ == "__main__":
    unittest.main()
