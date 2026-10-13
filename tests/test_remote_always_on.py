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
        self.addCleanup(svc._ctl_stop.set)
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
            svc._ctl_stop.set()
            self.assertEqual(on.called, want, (always, enabled))


if __name__ == "__main__":
    unittest.main()
