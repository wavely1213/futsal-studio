"""원격 접속 늘 켜기 (D-295 · 소유자 결정 '스튜디오 켜져 있으면 접속 되도록'): 새 기록·예전 기록의 기본값 · 설정 · 앱을 켤 때 저절로 켜기."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote  # noqa: E402
from remote_fixture import Clock, FakeBridge, make_home  # noqa: E402


class AlwaysOnTests(unittest.TestCase):
    def setUp(self):
        self.home, cleanup = make_home()
        self.addCleanup(cleanup)

    def store(self):
        return remote.Store(self.home / "remote.json", Clock())

    def test_fresh_store_is_always_on_without_auto_off(self):
        s = self.store().data["settings"]
        self.assertIs(s["alwaysOn"], True)
        self.assertEqual(s["autoOffHours"], 0)

    def test_old_record_migrates_once(self):
        """늘 켜기 전 판(v2.18.1)의 remote.json: 한 번만 늘 켜기 + 자동으로 끄기 안 함 · 그 뒤 고른 값은 그대로."""
        st = self.store()
        st.data["settings"].pop("alwaysOn")
        st.data["settings"]["autoOffHours"] = 12
        st.data["devices"] = []
        st.save()
        raw = json.loads((self.home / "remote.json").read_text(encoding="utf-8"))
        self.assertNotIn("alwaysOn", raw["settings"])
        st2 = self.store()
        self.assertEqual((st2.data["settings"]["alwaysOn"], st2.data["settings"]["autoOffHours"]), (True, 0))
        st2.data["settings"].update(alwaysOn=False, autoOffHours=3)
        st2.save()
        st3 = self.store()
        self.assertEqual((st3.data["settings"]["alwaysOn"], st3.data["settings"]["autoOffHours"]), (False, 3))

    def svc(self):
        svc = remote.Service(home=self.home, clock=Clock(), ntfy="http://127.0.0.1:1")
        svc.init(FakeBridge().bridge())
        self.addCleanup(svc.close)  # 뒤 스레드(remote-watch·remote-publisher)를 다음 시험으로 남기지 않음 (I-510)
        return svc

    def test_settings_validate_and_turn_on_now(self):
        svc = self.svc()
        with mock.patch.object(svc, "turn_on") as on:
            self.assertIs(svc.set_settings({"alwaysOn": False})["alwaysOn"], False)
            on.assert_not_called()
            self.assertIs(svc.set_settings({"alwaysOn": True})["alwaysOn"], True)
            on.assert_called_once_with()   # 꺼져 있으면 지금도 켬
        for bad in ("yes", 1, None):
            with self.assertRaises(ValueError):
                svc.set_settings({"alwaysOn": bad})

    def test_app_start_turns_on_when_always_on_or_enabled(self):
        for always, enabled, want in ((True, False, True), (False, True, True), (False, False, False)):
            svc = remote.Service(home=self.home, clock=Clock(), ntfy="http://127.0.0.1:1")
            st = remote.Store(self.home / "remote.json", Clock())
            st.data["settings"]["alwaysOn"], st.data["enabled"] = always, enabled
            st.save()
            with mock.patch.object(remote, "SVC", svc), mock.patch.object(svc, "turn_on") as on:
                remote.init(FakeBridge().bridge())
            svc.close()
            self.assertEqual(on.called, want, (always, enabled))


class SwitchTests(unittest.TestCase):
    """실행기 ON/OFF 스위치 (D-310 · 소유자 '실행기 ON/OFF 버튼 정도로만'): ON·OFF 가 지금과 다음에 켤 때 모두 같은 뜻."""

    def setUp(self):
        self.home, cleanup = make_home()
        self.addCleanup(cleanup)

    def svc(self):
        svc = remote.Service(home=self.home, clock=Clock(), ntfy="http://127.0.0.1:1")
        svc.init(FakeBridge().bridge())
        self.addCleanup(svc.close)  # 뒤 스레드(remote-watch·remote-publisher)를 다음 시험으로 남기지 않음 (I-510)
        return svc

    def test_on_saves_always_on_and_turns_on(self):
        svc = self.svc()
        svc.store.set(settings=dict(svc.store.data["settings"], alwaysOn=False))
        with mock.patch.object(svc, "turn_on", return_value=True) as on, mock.patch.object(svc, "turn_off") as off:
            self.assertIs(svc.switch(True), True)
        on.assert_called_once_with()
        off.assert_not_called()
        self.assertIs(remote.Store(self.home / "remote.json", Clock()).data["settings"]["alwaysOn"], True)

    def test_off_saves_off_and_turns_off_as_user(self):
        svc = self.svc()
        with mock.patch.object(svc, "turn_on") as on, mock.patch.object(svc, "turn_off") as off:
            self.assertIs(svc.switch(False), True)
        off.assert_called_once_with("user")
        on.assert_not_called()
        self.assertIs(remote.Store(self.home / "remote.json", Clock()).data["settings"]["alwaysOn"], False)

    def test_off_then_restart_stays_off_and_on_then_restart_comes_back(self):
        """OFF 를 누르면 스튜디오를 다시 켜도 꺼진 채 · ON 을 누르면 다시 켤 때 저절로 켜짐 (예전엔 끄기 뒤에도 늘 켜기라 다시 켜졌음)."""
        for on_, want in ((False, False), (True, True)):
            svc = self.svc()
            with mock.patch.object(svc, "turn_on", return_value=True), mock.patch.object(svc, "turn_off"):
                svc.switch(on_)
            svc.close()
            svc2 = remote.Service(home=self.home, clock=Clock(), ntfy="http://127.0.0.1:1")
            with mock.patch.object(remote, "SVC", svc2), mock.patch.object(svc2, "turn_on") as start:
                remote.init(FakeBridge().bridge())
            svc2.close()
            self.assertEqual(start.called, want, on_)

    def test_other_settings_survive_switch(self):
        svc = self.svc()
        svc.set_settings({"autoOffHours": 3, "keepAwake": "job"})
        with mock.patch.object(svc, "turn_on", return_value=True), mock.patch.object(svc, "turn_off"):
            svc.switch(False)
            svc.switch(True)
        s = remote.Store(self.home / "remote.json", Clock()).data["settings"]
        self.assertEqual((s["alwaysOn"], s["autoOffHours"], s["keepAwake"]), (True, 3, "job"))


class SwitchWordsTests(unittest.TestCase):
    """PC 화면·메시지에 예전 이름 '휴대폰으로 보기'가 남지 않음 (주석·기록 문서 빼고) · 사이드바는 '실행기' + ON/OFF 스위치."""
    ROOT = Path(__file__).resolve().parents[1]

    def test_no_old_name_in_user_text(self):
        import re
        for fn in ("ui.html", "remote.py", "rstudio.py", "app.py", "remote_ui.js", "hello.js"):
            text = (self.ROOT / fn).read_text(encoding="utf-8")
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)  # CSS·JS 덩어리 주석
            text = re.sub(r'"""(?:.|\n)*?"""', "", text)  # 파이썬 설명 글
            lines = [ln for ln in text.splitlines() if "휴대폰으로 보기" in ln.split("#")[0].split("//")[0]]
            self.assertEqual(lines, [], fn)

    def test_sidebar_has_switch(self):
        ui = (self.ROOT / "ui.html").read_text(encoding="utf-8")
        self.assertIn('<i class="rdot" id="rmDot"></i>실행기</button>', ui)
        self.assertIn('id="rmSw" role="switch"', ui)
        self.assertNotIn('data-rm="alwaysOn"', ui)  # 늘 켜기 칸은 스위치로 합침


if __name__ == "__main__":
    unittest.main()
