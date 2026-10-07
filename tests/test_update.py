"""안전한 업데이트(updater.py) · 다운로드 엔진 자동 관리(core) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_update
가짜 앱 폴더 + 이 컴퓨터 안의 http.server(포트 8851~8855)로 manifest·zip 을 내려 줌. 인터넷은 쓰지 않음."""
import functools
import hashlib
import http.server
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import updater  # noqa: E402

ROOT = "futsal-studio-0123abc"  # GitHub zip 의 맨 위 폴더 이름 흉내
UPD = (REPO / "updater.py").read_text(encoding="utf-8")  # 설치 전에 새 실행기를 시험하므로 가짜 앱에도 진짜 실행기


def sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode("utf-8")).hexdigest()


def _bytes(v):
    return v if isinstance(v, bytes) else v.encode("utf-8")


# 가짜 앱: 이전 버전(1.0.0) → 새 버전(1.1.0)
# app.py 는 실행될 때만(import 때는 말고) 어떤 버전이 켜졌는지 launched.txt 에 남김
OLD = {
    "app.py": "if __name__ == '__main__':\n    open('launched.txt', 'w').write('old')\n",
    "core.py": "VERSION = '1.0.0'\n",
    "editor.py": "", "thumb.py": "", "style.py": "", "qa.py": "",
    "ui.html": "<p>화면</p>\n",
    "old_only.py": "# 다음 버전에서 없어질 파일\n",
    "fonts/a.otf": b"OLDFONT\x00\x01",
    "requirements.txt": "yt-dlp[default]\npillow\n",
    "version.txt": "1.0.0\n",
    "시작하기 (Windows).bat": "start old\r\n",
    "updater.py": UPD,
}
NEW = {
    "app.py": "if __name__ == '__main__':\n    open('launched.txt', 'w').write('new')\n",
    "core.py": "VERSION = '1.1.0'\n",
    "editor.py": "", "thumb.py": "", "style.py": "", "qa.py": "",
    "ui.html": "<p>화면</p>\n",  # 그대로
    "new_feature.py": "X = 1\n",
    "assets/새 그림.png": b"\x89PNG-new",
    "fonts/a.otf": b"NEWFONT\x00\x02",
    "requirements.txt": "yt-dlp[default]\npillow\n",
    "version.txt": "1.1.0\n",
    "config.json": '{"channel_url": "기본값"}\n',
    "시작하기 (Windows).bat": "start new\r\n",
    "updater.py": UPD,
}


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


SERVE = HTTPD = BASE = None


def setUpModule():
    global SERVE, HTTPD, BASE
    SERVE = Path(tempfile.mkdtemp(prefix="upd-serve-"))
    for port in range(8851, 8856):
        try:
            HTTPD = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=str(SERVE)))
            break
        except OSError:
            continue
    else:
        raise unittest.SkipTest("포트 8851~8855 가 모두 사용 중이에요")
    BASE = f"http://127.0.0.1:{HTTPD.server_address[1]}"
    threading.Thread(target=HTTPD.serve_forever, daemon=True).start()


def tearDownModule():
    if HTTPD:
        HTTPD.shutdown()
        HTTPD.server_close()
    shutil.rmtree(SERVE, ignore_errors=True)


def free_port():
    """지금 아무도 듣지 않는 포트 (앱이 '아직 안 켜짐' 상태를 흉내 낼 때)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_once(payload):
    """8851~8855 중 빈 포트에서 한 번만 응답: payload 를 보내고 연결을 끊음 (받다 끊기는 상황)."""
    for port in range(8851, 8856):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            break
        except OSError:
            s.close()
    else:
        raise unittest.SkipTest("포트 8851~8855 가 모두 사용 중이에요")
    s.listen(1)

    def run():
        with s:
            c, _ = s.accept()
            with c:
                c.recv(65536)
                c.sendall(payload)
    threading.Thread(target=run, daemon=True).start()
    return port


def wait_text(path, t=10):
    for _ in range(int(t * 10)):
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(0.1)
    return None


def load_core(tmp):
    """core.py 를 임시 폴더에 복사해서 불러옴 (진짜 작업 폴더를 건드리지 않게, 다른 테스트의 core 와 섞이지 않게)."""
    d = Path(tmp) / "corecopy"
    d.mkdir()
    shutil.copy(REPO / "core.py", d / "core.py")
    (d / "config.json").write_text(json.dumps({"channel_url": "https://www.youtube.com/@x", "workspace": str(Path(tmp) / "cwork")}),
                                   encoding="utf-8")
    (d / "version.txt").write_text("1.0.0\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("core_under_test", d / "core.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._FF = "ffmpeg"  # 다운로드 테스트에서 ffmpeg 를 찾지 않게
    m.ENGINE_HOME = Path(tmp) / "home" / ".futsal-studio"
    return m


def snapshot(root, skip=("__pycache__",)):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        rel = p.relative_to(root).as_posix()
        if any(rel == s or rel.startswith(s + "/") or f"/{s}/" in f"/{rel}/" for s in skip):
            continue
        out[rel + ("/" if p.is_dir() else "")] = p.read_bytes() if p.is_file() else None
    return out


class Case:
    """가짜 앱 폴더 하나 + 내려 줄 manifest·zip."""

    def __init__(self, tmp, new=None, files=None, legacy=False, zip_bytes=None, extra_zip=None, zip_version=None):
        self.dir = Path(tempfile.mkdtemp(dir=tmp))
        self.app = self.dir / "app"
        self.work = self.dir / "work"
        for rel, v in OLD.items():
            self._put(self.app / rel, v)
        self.config = json.dumps({"workspace": str(self.work), "내 설정": True}, ensure_ascii=False).encode("utf-8")
        (self.app / "config.json").write_bytes(self.config)
        self._put(self.app / ".venv" / "pyvenv.cfg", "home = C:\\Python311\n")
        self._put(self.work / "videos" / "내 영상.mp4", b"VIDEO")
        old_files = {k: sha(_bytes(v)) for k, v in OLD.items()}
        old_files["config.json"] = sha(self.config)
        (self.app / "manifest.json").write_text(json.dumps({"version": "1.0.0", "files": old_files}), encoding="utf-8")

        new = dict(NEW if new is None else new)
        if zip_version:
            new["version.txt"] = zip_version + "\n"
        name = self.dir.name
        (SERVE / name).mkdir()
        if zip_bytes is None:
            zp = SERVE / name / "app.zip"
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
                for rel, v in new.items():
                    z.writestr(f"{ROOT}/{rel}", _bytes(v))
                z.writestr(f"{ROOT}/manifest.json", '{"version": "0.9.0"}')  # 저장소 안의 (오래된) manifest
                z.writestr(f"{ROOT}/tests/test_x.py", "assert False\n")      # 배포 목록 밖 → 설치하지 않음
                for zname, v in (extra_zip or {}).items():
                    z.writestr(zname, _bytes(v))
        else:
            (SERVE / name / "app.zip").write_bytes(zip_bytes)
        manifest = {"version": "1.1.0", "notes": "테스트", "zip": f"{BASE}/{name}/app.zip"}
        if not legacy:
            manifest["files"] = files if files is not None else {k: sha(_bytes(v)) for k, v in NEW.items()}
        self.manifest_raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        (SERVE / name / "manifest.json").write_bytes(self.manifest_raw)
        self.url = f"{BASE}/{name}/manifest.json"
        self.before = snapshot(self.app)

    @staticmethod
    def _put(p, v):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_bytes(v))

    def read(self, rel):
        return (self.app / rel).read_bytes()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="upd-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.core = load_core(self.tmp)
        self.logs = []
        self.pip = []
        self.pip_rc = 0

        def fake_run(cmd):
            self.pip.append(cmd)
            return subprocess.CompletedProcess(cmd, self.pip_rc, "", "ERROR: 네트워크 없음" if self.pip_rc else "")
        p = mock.patch.object(self.core, "run", side_effect=fake_run)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.core, "_engine_needs_newer_python", return_value=None)  # PyPI 에 묻지 않게 (인터넷 없이)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"FUTSAL_RESTART": "1"})  # 다른 사람이 8765 에 앱을 켜 둬도 확인을 건너뛰지 않게
        p.start()
        self.addCleanup(p.stop)
        updater.take_notice()  # 앞 테스트의 화면 알림이 남지 않게

    def update(self, case):
        self.core.APP_DIR = case.app
        self.core.CONFIG = {"update_manifest_url": case.url}
        self.core.VERSION = "1.0.0"
        return self.core.update_app(self.logs.append)

    def assertUnchanged(self, case, skip=("__pycache__",)):
        self.assertEqual(snapshot(case.app, skip), {k: v for k, v in case.before.items()
                                                    if not any(k == s or k.startswith(s + "/") for s in skip)})


class InstallTests(Base):
    def test_success_replaces_adds_deletes_and_keeps_user_files(self):
        c = Case(self.tmp)
        self.assertTrue(self.update(c))
        for rel in ("app.py", "core.py", "new_feature.py", "assets/새 그림.png", "fonts/a.otf", "시작하기 (Windows).bat", "version.txt"):
            self.assertEqual(c.read(rel), _bytes(NEW[rel]), rel)
        self.assertFalse((c.app / "old_only.py").exists(), "새 목록에 없는 예전 파일은 지움")
        self.assertEqual(c.read("config.json"), c.config, "사용자 설정은 그대로")
        self.assertEqual(c.read(".venv/pyvenv.cfg"), b"home = C:\\Python311\n")
        self.assertEqual((c.work / "videos" / "내 영상.mp4").read_bytes(), b"VIDEO")
        self.assertFalse((c.app / "tests").exists(), "배포 목록 밖 파일은 설치하지 않음")
        self.assertEqual(c.read("manifest.json"), c.manifest_raw, "다음 업데이트를 위해 확인한 목록을 그대로 저장")
        self.assertFalse((c.app / updater.STAGING).exists())
        pend = json.loads(c.read(updater.PENDING))
        self.assertEqual((pend["from"], pend["to"], pend["state"]), ("1.0.0", "1.1.0", "installed"))
        rb = c.app / updater.ROLLBACK / "v1.0.0"
        self.assertEqual((rb / "files" / "app.py").read_bytes(), _bytes(OLD["app.py"]))
        self.assertEqual((rb / "files" / "old_only.py").read_bytes(), _bytes(OLD["old_only.py"]))
        self.assertFalse((rb / "files" / "ui.html").exists(), "안 바뀐 파일은 복사하지 않음")
        self.assertEqual(self.pip, [], "구성요소 목록이 그대로면 pip 를 부르지 않음")
        self.assertEqual(c.read(updater.REQ_HASH).decode(), sha(NEW["requirements.txt"]))

    def test_unchanged_requirements_zero_pip_calls(self):
        c = Case(self.tmp)
        (c.app / updater.REQ_HASH).write_text(sha(OLD["requirements.txt"]), encoding="utf-8")
        self.assertTrue(self.update(c))
        self.assertEqual(self.pip, [])

    def test_changed_requirements_pip_once(self):
        new = dict(NEW, **{"requirements.txt": "yt-dlp[default]\npillow\nnumpy\n"})
        c = Case(self.tmp, new=new, files={k: sha(_bytes(v)) for k, v in new.items()})
        self.assertTrue(self.update(c))
        self.assertEqual(len(self.pip), 1)
        self.assertEqual(self.pip[0][-2:], ["-r", str(c.app / "requirements.txt")])
        self.assertEqual(c.read(updater.REQ_HASH).decode(), sha(new["requirements.txt"]))

    def test_pip_failure_rolls_back(self):
        new = dict(NEW, **{"requirements.txt": "없는-패키지\n"})
        c = Case(self.tmp, new=new, files={k: sha(_bytes(v)) for k, v in new.items()})
        self.pip_rc = 1
        with self.assertRaises(RuntimeError) as cm:
            self.update(c)
        self.assertIn("이전 버전 그대로", str(cm.exception))
        self.assertUnchanged(c, skip=("__pycache__", updater.ROLLBACK, updater.REQ_HASH))
        self.assertEqual(c.read(updater.REQ_HASH), b"failed", "다음 업데이트 때 다시 설치하게")

    def test_hash_mismatch_leaves_app_unchanged(self):
        files = {k: sha(_bytes(v)) for k, v in NEW.items()}
        files["app.py"] = sha("배포 뒤 main 에 새 커밋이 올라온 상황")
        c = Case(self.tmp, files=files)
        with self.assertRaises(updater.UpdateError) as cm:
            self.update(c)
        self.assertIn("배포 목록과 달라요", str(cm.exception))
        self.assertUnchanged(c)

    def test_listed_file_missing_from_zip_leaves_app_unchanged(self):
        files = {k: sha(_bytes(v)) for k, v in NEW.items()}
        files["사라진.py"] = sha("x")
        c = Case(self.tmp, files=files)
        with self.assertRaises(updater.UpdateError):
            self.update(c)
        self.assertUnchanged(c)

    def test_corrupt_zip_leaves_app_unchanged(self):
        c = Case(self.tmp, zip_bytes=b"PK\x03\x04" + "이건 zip 이 아니에요".encode() * 50)
        with self.assertRaises(updater.UpdateError) as cm:
            self.update(c)
        self.assertIn("깨졌어요", str(cm.exception))
        self.assertUnchanged(c)

    def test_crc_error_zip_leaves_app_unchanged(self):
        c = Case(self.tmp)
        zp = SERVE / c.dir.name / "app.zip"
        data = zp.read_bytes()
        i = data.index(b"NEWFONT")
        zp.write_bytes(data[:i] + b"XEWFONT" + data[i + 7:])  # 내용 한 바이트 손상 → CRC 불일치
        with self.assertRaises(updater.UpdateError) as cm:
            self.update(c)
        self.assertIn("깨졌어요", str(cm.exception))
        self.assertUnchanged(c)

    def test_version_mismatch_leaves_app_unchanged(self):
        c = Case(self.tmp, zip_version="1.2.0")
        with self.assertRaises(updater.UpdateError) as cm:
            self.update(c)
        self.assertIn("v1.2.0", str(cm.exception))
        self.assertUnchanged(c)

    def test_bad_manifest_file_list_rejected(self):
        files = {k: sha(_bytes(v)) for k, v in NEW.items()}
        files["../../밖으로.py"] = sha("x")
        c = Case(self.tmp, files=files)
        with self.assertRaises(updater.UpdateError):
            self.update(c)
        self.assertUnchanged(c)

    def test_zip_slip_entries_ignored(self):
        c = Case(self.tmp, legacy=True, extra_zip={f"{ROOT}/../../evil.txt": "x", f"{ROOT}/C:/evil2.txt": "y"})
        self.assertTrue(self.update(c))
        self.assertFalse(list(Path(self.tmp).rglob("evil*.txt")))

    def test_old_manifest_without_files_still_updates(self):
        c = Case(self.tmp, legacy=True)
        self.assertTrue(self.update(c))
        self.assertEqual(c.read("app.py"), _bytes(NEW["app.py"]))
        self.assertEqual(c.read("config.json"), c.config)
        self.assertTrue((c.app / "old_only.py").exists(), "목록이 없으면 지울 파일을 알 수 없으니 남겨 둠")
        self.assertFalse((c.app / "tests").exists())
        self.assertEqual(json.loads(c.read(updater.PENDING))["state"], "installed")
        self.assertTrue((c.app / updater.ROLLBACK / "v1.0.0" / "rollback.json").exists())
        self.assertFalse((c.app / updater.STAGING).exists())

    def test_config_added_only_when_missing(self):
        c = Case(self.tmp)
        (c.app / "config.json").unlink()
        c.work.mkdir(exist_ok=True)
        self.assertTrue(self.update(c))
        self.assertEqual(c.read("config.json"), _bytes(NEW["config.json"]))

    def test_second_click_before_restart_keeps_rollback(self):
        c = Case(self.tmp)
        self.assertTrue(self.update(c))
        self.assertTrue(self.update(c))  # 다시 시작하기 전에 또 누름
        pend = json.loads(c.read(updater.PENDING))
        self.assertEqual(pend["from"], "1.0.0")
        self.assertEqual((c.app / updater.ROLLBACK / "v1.0.0" / "files" / "app.py").read_bytes(), _bytes(OLD["app.py"]))

    def test_exception_mid_replace_rolls_back_immediately(self):
        c = Case(self.tmp)
        orig, n = updater._replace, [0]

        def flaky(src, dst):
            if updater.STAGING in str(src):
                n[0] += 1
                if n[0] == 3:
                    raise RuntimeError("디스크 오류 흉내")
            return orig(src, dst)
        with mock.patch.object(updater, "_replace", side_effect=flaky):
            with self.assertRaises(updater.UpdateError) as cm:
                self.update(c)
        self.assertIn("이전 버전 그대로", str(cm.exception))
        self.assertUnchanged(c, skip=("__pycache__", updater.ROLLBACK))
        self.assertFalse((c.app / updater.PENDING).exists())

    def test_crash_mid_replace_then_check_restores(self):
        class Crash(BaseException):  # 전원이 꺼지거나 프로세스가 죽은 상황 (except Exception 으로 못 잡음)
            pass
        c = Case(self.tmp)
        orig, n = updater._replace, [0]

        def dies(src, dst):
            if updater.STAGING in str(src):
                n[0] += 1
                if n[0] == 4:
                    raise Crash()
            return orig(src, dst)
        with mock.patch.object(updater, "_replace", side_effect=dies):
            with self.assertRaises(Crash):
                self.update(c)
        self.assertNotEqual(snapshot(c.app), c.before, "몇 개는 이미 바뀐 상태")
        self.assertEqual(json.loads(c.read(updater.PENDING))["state"], "applying")
        self.assertEqual(updater.check(c.app, python=sys.executable), "rolled_back")
        self.assertUnchanged(c, skip=("__pycache__", updater.ROLLBACK, updater.RESULT))
        self.assertIn("되돌려요", (c.work / "studio.log").read_text(encoding="utf-8"))
        msgs = updater.finish(c.app)
        self.assertTrue(any("이전 버전(v1.0.0)으로 되돌렸어요" in m for m in msgs), msgs)
        self.assertFalse((c.app / updater.RESULT).exists())

    def test_interrupted_update_is_undone_before_next_one(self):
        class Crash(BaseException):
            pass
        c = Case(self.tmp)
        orig = updater._replace

        def dies(src, dst):
            if updater.STAGING in str(src) and Path(dst).name == "core.py":
                raise Crash()
            return orig(src, dst)
        with mock.patch.object(updater, "_replace", side_effect=dies):
            with self.assertRaises(Crash):
                self.update(c)
        self.assertTrue(self.update(c), "끊긴 업데이트를 되돌린 뒤 새로 설치")
        self.assertEqual(c.read("core.py"), _bytes(NEW["core.py"]))
        self.assertEqual(json.loads(c.read(updater.PENDING))["from"], "1.0.0")
        self.assertEqual((c.app / updater.ROLLBACK / "v1.0.0" / "files" / "app.py").read_bytes(), _bytes(OLD["app.py"]))

    def test_resume_after_crash_past_version_txt_keeps_real_from(self):
        """version.txt 까지 바뀐 뒤 끊긴 업데이트를 다시 하면, 되돌린 뒤의 진짜 이전 버전(1.0.0)을 기록 (검수 재현)."""
        class Crash(BaseException):
            pass
        c = Case(self.tmp)
        orig = updater._replace

        def dies(src, dst):
            if updater.STAGING in str(src) and Path(dst).name.endswith(".bat"):  # version.txt 다음 차례
                raise Crash()
            return orig(src, dst)
        with mock.patch.object(updater, "_replace", side_effect=dies):
            with self.assertRaises(Crash):
                self.update(c)
        self.assertEqual(c.read("version.txt"), b"1.1.0\n", "version.txt 는 이미 바뀐 상태")
        self.assertTrue(self.update(c))
        pend = json.loads(c.read(updater.PENDING))
        self.assertEqual((pend["from"], pend["to"], pend["rollback"]), ("1.0.0", "1.1.0", "v1.0.0"))
        self.assertEqual(sorted(x.name for x in (c.app / updater.ROLLBACK).iterdir()), ["v1.0.0"])
        self.assertEqual((c.app / updater.ROLLBACK / "v1.0.0" / "files" / "version.txt").read_bytes(), b"1.0.0\n")
        self.assertEqual(updater.finish(c.app), ["업데이트 완료 · v1.0.0 → v1.1.0"])

    def test_case_only_rename_not_deleted(self):
        new = dict(NEW)
        new["old_only.PY"] = new.pop("new_feature.py")  # Windows 에서는 old_only.py 와 같은 파일
        c = Case(self.tmp, new=new, files={k: sha(_bytes(v)) for k, v in new.items()})
        self.assertTrue(self.update(c))
        self.assertEqual(c.read("old_only.PY"), _bytes(new["old_only.PY"]))
        self.assertTrue((c.app / "old_only.py").exists(), "대소문자만 다른 예전 이름을 지우면 Windows 에서는 새 파일이 지워짐")

    def test_missing_rollback_copy_does_not_loop(self):
        c = Case(self.tmp)
        (c.app / updater.PENDING).write_text(json.dumps({"from": "1.0.0", "to": "1.1.0", "state": "applying",
                                                         "rollback": "v1.0.0"}), encoding="utf-8")
        self.assertEqual(updater.check(c.app, python=sys.executable), "failed")
        self.assertFalse((c.app / updater.PENDING).exists())
        self.assertEqual(updater.check(c.app, python=sys.executable), "none")

    def test_windows_lock_retry(self):
        calls = []

        def locked(*a):
            calls.append(a)
            if len(calls) < 3:
                raise PermissionError(13, "다른 프로세스가 파일을 사용 중")
            return "ok"
        with mock.patch.object(updater.time, "sleep"):
            self.assertEqual(updater._retry(locked), "ok")
        self.assertEqual(len(calls), 3)


class LaunchTests(Base):
    def installed(self, new=None):
        new = new or NEW
        c = Case(self.tmp, new=new, files={k: sha(_bytes(v)) for k, v in new.items()})
        self.assertTrue(self.update(c))
        return c

    def test_import_failure_rolls_back(self):
        new = dict(NEW, **{"core.py": "raise ImportError('새 버전 고장')\n"})
        c = self.installed(new)
        self.assertEqual(updater.check(c.app, python=sys.executable), "rolled_back")
        self.assertUnchanged(c, skip=("__pycache__", updater.ROLLBACK, updater.RESULT, updater.REQ_HASH, updater.SKIP))
        self.assertFalse((c.app / "new_feature.py").exists())
        self.assertFalse((c.app / "assets").exists(), "새로 생긴 빈 폴더도 정리")
        log = (c.work / "studio.log").read_text(encoding="utf-8")
        self.assertIn("새 버전 고장", log)
        self.assertEqual(json.loads(c.read(updater.RESULT))["to"], "1.1.0")

    def test_import_ok_then_app_start_clears_pending(self):
        c = self.installed()
        self.assertEqual(updater.check(c.app, python=sys.executable), "ok")
        self.assertEqual(json.loads(c.read(updater.PENDING))["launches"], 1)
        self.assertEqual(updater.finish(c.app), ["업데이트 완료 · v1.0.0 → v1.1.0"])
        self.assertFalse((c.app / updater.PENDING).exists())
        self.assertEqual(updater.check(c.app, python=sys.executable), "none")

    def age_last_launch(self, c, sec=updater.GRACE + 1):
        """마지막으로 센 실행이 sec 초 전이었던 것처럼."""
        p = json.loads(c.read(updater.PENDING))
        p["last_launch"] -= sec
        (c.app / updater.PENDING).write_text(json.dumps(p), encoding="utf-8")

    def test_quick_relaunches_while_starting_do_not_roll_back(self):
        """업데이트 직후 창이 늦게 뜨는 동안 아이콘을 여러 번 눌러도 멀쩡한 새 버전을 되돌리지 않음."""
        c = self.installed()
        self.assertEqual(updater.check(c.app, python=sys.executable), "ok")  # 다시 시작 (restart)
        with mock.patch.dict(os.environ, {"FUTSAL_PORT": "1"}):  # 아직 포트를 못 잡음
            os.environ.pop("FUTSAL_RESTART")
            for _ in range(5):
                self.assertEqual(updater.check(c.app, python=sys.executable), "starting")
        self.assertEqual(c.read("app.py"), _bytes(NEW["app.py"]))
        p = json.loads(c.read(updater.PENDING))
        self.assertEqual((p["state"], p["launches"]), ("installed", 1), "기다리는 동안 누른 것은 세지 않음")
        self.assertFalse((c.app / updater.RESULT).exists())
        self.assertEqual(updater.skipped(c.app), "")

    def test_never_started_rolls_back_only_after_grace(self):
        c = self.installed()
        self.assertEqual(updater.check(c.app, python=sys.executable), "ok")
        self.assertEqual(updater.check(c.app, python=sys.executable), "starting")
        self.age_last_launch(c)  # 1분이 지나도 창이 안 열림 → 실패 1번
        self.assertEqual(updater.check(c.app, python=sys.executable), "ok")
        self.assertEqual(c.read("app.py"), _bytes(NEW["app.py"]))
        self.age_last_launch(c)  # 또 실패 → 되돌림
        self.assertEqual(updater.check(c.app, python=sys.executable), "rolled_back")
        self.assertEqual(c.read("app.py"), _bytes(OLD["app.py"]))
        self.assertEqual(updater.skipped(c.app), "1.1.0")
        self.assertIn("여러 번", (c.work / "studio.log").read_text(encoding="utf-8"))

    def test_launch_cli_end_to_end(self):
        """python updater.py --launch: 새 버전이 안 열리면 되돌린 뒤 (이전) 앱을 실행."""
        new = dict(NEW, **{"editor.py": "import 없는_모듈\n"})
        c = self.installed(new)
        shutil.copy(REPO / "updater.py", c.app / "updater.py")
        r = subprocess.run([sys.executable, str(c.app / "updater.py"), "--launch"], cwd=str(self.tmp),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        marker = c.app / "launched.txt"
        for _ in range(100):
            if marker.exists() and marker.read_text():
                break
            time.sleep(0.1)
        self.assertEqual(marker.read_text(), "old", "되돌린 이전 버전이 켜져야 함")
        self.assertIn("없는_모듈", (c.work / "studio.log").read_text(encoding="utf-8"))
        self.assertFalse((c.app / updater.PENDING).exists())

    def test_launch_cli_new_version_ok(self):
        c = self.installed()
        shutil.copy(REPO / "updater.py", c.app / "updater.py")
        subprocess.run([sys.executable, str(c.app / "updater.py"), "--launch"], timeout=120, check=True)
        marker = c.app / "launched.txt"
        for _ in range(100):
            if marker.exists() and marker.read_text():
                break
            time.sleep(0.1)
        self.assertEqual(marker.read_text(), "new")


    def test_launch_cli_quick_relaunches_keep_new_version(self):
        """진짜 실행기 4번: 새 버전이 2초 걸려 뜨는 동안 아이콘을 3번 더 눌러도 되돌리지 않음 (검수 재현)."""
        new = dict(NEW, **{"app.py": "import time\nif __name__ == '__main__':\n    time.sleep(2)\n    import updater\n"
                                     "    updater.finish()\n    open('launched.txt', 'a').write('new\\n')\n"})
        c = self.installed(new)
        env = dict(os.environ, FUTSAL_PORT=str(free_port()))
        env.pop("FUTSAL_RESTART", None)
        cmd = [sys.executable, str(c.app / "updater.py"), "--launch"]
        procs = [subprocess.Popen(cmd, env=dict(env, FUTSAL_RESTART="1"))]  # 업데이트 뒤 다시 시작
        time.sleep(0.6)
        for _ in range(3):  # 창이 안 보여서 바탕화면 아이콘을 또 누름
            procs.append(subprocess.Popen(cmd, env=env))
            time.sleep(0.3)
        for pr in procs:
            self.assertEqual(pr.wait(60), 0)
        self.assertEqual(c.read("app.py"), _bytes(new["app.py"]), "새 버전 그대로")
        self.assertEqual(c.read("launched.txt").decode().split(), ["new"] * 4)
        self.assertFalse((c.app / updater.PENDING).exists(), "먼저 뜬 새 버전이 마무리")
        self.assertFalse((c.app / updater.RESULT).exists())
        self.assertEqual(updater.skipped(c.app), "")
        log = c.work / "studio.log"
        self.assertFalse(log.exists() and "되돌려요" in log.read_text(encoding="utf-8"))

    def test_launch_runs_app_in_the_launcher_process(self):
        """아이콘이 띄운 그 프로세스가 곧 앱 (Windows 작업 표시줄이 창을 그 아이콘과 묶어 두게)."""
        new = dict(NEW, **{"app.py": "import os, sys\nif __name__ == '__main__':\n    open('launched.txt', 'w').write("
                                     "'%d %r %s' % (os.getpid(), sys.argv[1:], os.environ.get('FUTSAL_VIA_UPDATER')))\n"})
        c = self.installed(new)
        pr = subprocess.Popen([sys.executable, str(c.app / "updater.py"), "--launch", "--browser"], cwd=self.tmp)
        self.assertEqual(pr.wait(60), 0)
        self.assertEqual(c.read("launched.txt").decode(), f"{pr.pid} ['--browser'] 1", "같은 프로세스·인자·앱 폴더에서")

    def test_launch_cli_crash_at_start_rolls_back_and_opens_old(self):
        """불러오기는 되지만 켜다가 오류로 멈추는 새 버전: 그 자리에서 되돌리고 이전 버전을 새 프로세스로 켬."""
        new = dict(NEW, **{"app.py": "if __name__ == '__main__':\n    raise RuntimeError('새 버전 시작 고장')\n"})
        c = self.installed(new)
        r = subprocess.run([sys.executable, str(c.app / "updater.py"), "--launch"], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(wait_text(c.app / "launched.txt"), "old", "되돌린 이전 버전이 켜져야 함")
        self.assertEqual(c.read("app.py"), _bytes(OLD["app.py"]))
        self.assertFalse((c.app / updater.PENDING).exists())
        self.assertEqual(updater.skipped(c.app), "1.1.0")
        self.assertIn("새 버전 시작 고장", (c.work / "studio.log").read_text(encoding="utf-8"))


class VerifyBeforeInstallTests(Base):
    """새 버전이 이 PC의 Python 에서 안 돌아가면 (특히 실행기) 설치 전에 멈춤.
    아이콘·.bat·다시 시작이 모두 updater.py 를 거치므로, 실행기가 망가지면 앱이 아예 안 켜지고 되돌릴 수도 없음."""

    def rejected(self, new):
        c = Case(self.tmp, new=new, files={k: sha(_bytes(v)) for k, v in new.items()})
        with self.assertRaises(updater.UpdateError) as cm:
            self.update(c)
        self.assertIn("새 버전 파일에 문제가 있어서", str(cm.exception))
        self.assertUnchanged(c)
        return str(cm.exception)

    def test_syntax_error_in_new_updater(self):
        self.assertIn("updater.py", self.rejected(dict(NEW, **{"updater.py": UPD + "\ndef 고장(:\n"})))

    @unittest.skipIf(sys.version_info >= (3, 12), "3.12 부터는 맞는 문법")
    def test_newer_python_only_syntax_in_new_updater(self):
        """개발 PC(3.12+)에서는 되지만 사용자 PC(3.11)에서는 문법 오류인 f-string (검수 재현)."""
        bad = UPD.replace("def finish(app_dir=APP_DIR):", 'def finish(app_dir=APP_DIR):\n    x = f"{json.dumps({"a": 1})["a"]}"', 1)
        self.assertNotEqual(bad, UPD)
        self.rejected(dict(NEW, **{"updater.py": bad}))

    def test_new_updater_that_fails_on_launch(self):
        bad = UPD.replace("    return run_app(app_dir, args)", "    return run_ap(app_dir, args)", 1)
        self.assertNotEqual(bad, UPD)
        self.assertIn("NameError", self.rejected(dict(NEW, **{"updater.py": bad})))

    def test_syntax_error_in_other_module(self):
        self.assertIn("editor.py", self.rejected(dict(NEW, **{"editor.py": "def (:\n"})))

    def test_release_without_updater(self):
        new = dict(NEW)
        new.pop("updater.py")
        self.rejected(new)

    def test_selftest_cli(self):
        tmp = Path(self.tmp) / "임시 폴더"
        tmp.mkdir()
        r = subprocess.run([sys.executable, str(REPO / "updater.py"), "--selftest"], capture_output=True, text=True,
                           timeout=120, cwd=self.tmp, env=dict(os.environ, TMPDIR=str(tmp), TEMP=str(tmp), TMP=str(tmp)))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "selftest ok"), r.stderr)
        self.assertEqual(os.listdir(tmp), [], "시험 폴더는 남기지 않음")


class SkipAndNoticeTests(Base):
    """이 PC에서 열리지 않아 되돌린 버전은 더 새 버전이 나올 때까지 다시 권하지 않고, 켜진 뒤 화면에 한 번 알림."""

    def rolled_back_case(self):
        new = dict(NEW, **{"core.py": "raise ImportError('새 버전 고장')\n"})
        c = Case(self.tmp, new=new, files={k: sha(_bytes(v)) for k, v in new.items()})
        self.assertTrue(self.update(c))
        self.assertEqual(updater.check(c.app, python=sys.executable), "rolled_back")
        return c

    def test_rolled_back_version_not_offered_again(self):
        c = self.rolled_back_case()
        u = self.core.check_update()
        self.assertFalse(u["available"])
        self.assertIn("이 PC에서 열리지 않아", u["note"])
        before = snapshot(c.app)
        self.assertFalse(self.update(c), "버튼을 또 눌러도 다시 받지 않음")
        self.assertEqual(snapshot(c.app), before)
        self.assertTrue(any("건너뜀" in m for m in self.logs), self.logs)

    def test_newer_version_is_offered_again(self):
        c = self.rolled_back_case()
        m = json.loads(c.manifest_raw)
        m["version"] = "1.2.0"
        (SERVE / c.dir.name / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        u = self.core.check_update()
        self.assertEqual((u["available"], u["latest"]), (True, "1.2.0"))

    def test_rollback_notice_shown_once_after_start(self):
        c = self.rolled_back_case()
        msgs = updater.finish(c.app)  # 이전 버전 앱이 켜진 뒤 (app._after_start)
        self.assertTrue(any("되돌렸어요" in m for m in msgs), msgs)
        u = self.core.check_update()
        self.assertTrue(u["notice_warn"])
        self.assertIn("v1.1.0", u["notice"])
        self.assertIn("관리자에게 알려 주세요", u["notice"])
        self.assertNotIn("notice", self.core.check_update(), "한 번만")

    def test_interrupted_update_is_not_skipped(self):
        class Crash(BaseException):
            pass
        c = Case(self.tmp)
        orig = updater._replace

        def dies(src, dst):
            if updater.STAGING in str(src) and Path(dst).name == "core.py":
                raise Crash()
            return orig(src, dst)
        with mock.patch.object(updater, "_replace", side_effect=dies):
            with self.assertRaises(Crash):
                self.update(c)
        self.assertEqual(updater.check(c.app, python=sys.executable), "rolled_back")
        self.assertEqual(updater.skipped(c.app), "", "고장 난 버전이 아니라 끊긴 것 → 다시 권함")
        updater.finish(c.app)
        self.assertIn("다시 업데이트하면 돼요", self.core.check_update()["notice"])
        self.assertTrue(self.core.check_update()["available"])

    def test_success_notice_and_clears_old_skip(self):
        c = Case(self.tmp)
        (c.app / updater.SKIP).write_text(json.dumps({"version": "1.0.5"}), encoding="utf-8")
        self.assertTrue(self.update(c))
        self.assertEqual(updater.check(c.app, python=sys.executable), "ok")
        updater.finish(c.app)
        self.assertEqual(updater.skipped(c.app), "")
        u = self.core.check_update()
        self.assertEqual((u["notice"], u["notice_warn"]), ("업데이트 완료 · v1.0.0 → v1.1.0", False))


class NetworkErrorTests(Base):
    """받다 끊기거나 인터넷이 없을 때: 영어 오류 대신 한국어 안내, 앱 폴더는 그대로."""

    def test_connection_drop_during_download_is_friendly(self):
        port = serve_once(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: application/zip\r\n\r\n"
                          b"1000\r\n" + b"x" * 100)  # GitHub zip 처럼 길이 없이 오다가 끊김
        c = Case(self.tmp)
        m = json.loads(c.manifest_raw)
        m["zip"] = f"http://127.0.0.1:{port}/a.zip"
        with self.assertRaises(updater.UpdateError) as cm:
            updater.download_and_install(m, None, c.app, self.logs.append)
        self.assertIn("받는 도중 연결이 끊겼어요", str(cm.exception))
        self.assertIn("인터넷 연결을 확인", str(cm.exception))
        self.assertUnchanged(c)

    def test_offline_manifest_is_friendly(self):
        url = f"http://127.0.0.1:{free_port()}/manifest.json"
        with self.assertRaises(updater.UpdateError) as cm:
            updater.fetch_manifest(url, timeout=3)
        self.assertIn("업데이트 정보를 받지 못했어요", str(cm.exception))
        self.core.CONFIG = {"update_manifest_url": url}
        u = self.core.check_update()
        self.assertFalse(u["available"])
        self.assertIn("인터넷 연결을 확인", u["note"])
        with self.assertRaises(updater.UpdateError):
            self.core.update_app(self.logs.append)


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "git·bash 필요")
class ReleaseScriptTests(unittest.TestCase):
    """release.sh 가 적은 지문이 GitHub zip(= git archive)과 맞는지, 그 zip 으로 실제 설치가 되는지."""

    def setUp(self):
        tmp = self.tmp = Path(tempfile.mkdtemp(prefix="upd-rel-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.repo, self.origin = tmp / "repo", tmp / "origin.git"
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("remote", "add", "origin", str(self.origin))
        for rel, v in NEW.items():
            Case._put(self.repo / rel, v)
        Case._put(self.repo / "version.txt", "1.0.0\n")  # release.sh 가 1.1.0 으로 올림
        shutil.copy(REPO / "release.sh", self.repo / "release.sh")
        shutil.copy(REPO / "updater.py", self.repo / "updater.py")
        Case._put(self.repo / "tests" / "test_a.py", "")
        Case._put(self.repo / "manifest.json", '{"version": "1.0.0"}')  # 예전 형식 (파일 목록 없음)

    def git(self, *a, cwd=None):
        return subprocess.run(["git", *a], cwd=cwd or self.repo, env=self.env, check=True, capture_output=True).stdout

    def release(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        return subprocess.run(["bash", "release.sh", "1.1.0", "테스트 배포"], cwd=self.repo, env=self.env, capture_output=True, text=True)

    def test_release_manifest_matches_archive(self):
        r = self.release()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((self.repo / "manifest.json").read_text(encoding="utf-8"))
        code = self.git("rev-parse", "HEAD~1").decode().strip()
        self.assertEqual(m["version"], "1.1.0")
        self.assertTrue(m["zip"].endswith(f"/archive/{code}.zip"), m["zip"])
        self.assertNotIn("manifest.json", m["files"])
        self.assertFalse(any(k.startswith("tests/") for k in m["files"]))
        self.assertIn("시작하기 (Windows).bat", m["files"])
        self.assertEqual(self.git("rev-parse", "main", cwd=self.origin), self.git("rev-parse", "HEAD"), "푸시까지")
        # 예전 실행기(v1.7.1 이하)는 zip 속 manifest.json 을 그대로 가져감 → 배포 커밋 안에도 이번 파일 목록이 있어야 함
        c1 = json.loads(self.git("show", f"{code}:manifest.json"))
        self.assertEqual((c1["version"], c1["files"]), ("1.1.0", m["files"]))
        # GitHub 의 archive/<커밋>.zip 과 같은 모양
        zp = self.tmp / "a.zip"
        self.git("archive", "--format=zip", f"--prefix=futsal-studio-{code}/", "-o", str(zp), code)
        c = Case(self.tmp)
        out = updater.install(m, zp, c.app, lambda s: None, (self.repo / "manifest.json").read_bytes())
        self.assertIn("app.py", out["changed"])
        self.assertEqual(c.read("updater.py"), (REPO / "updater.py").read_bytes())
        self.assertEqual(c.read("config.json"), c.config)

    def test_failing_update_tests_stop_release(self):
        Case._put(self.repo / "tests" / "__init__.py", "")
        Case._put(self.repo / "tests" / "test_update.py", "import unittest\n\nclass T(unittest.TestCase):\n"
                                                          "    def test_x(self):\n        self.fail('실행기 고장')\n")
        r = self.release()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("배포를 멈췄어요", r.stdout + r.stderr)
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), b"1", "아무것도 커밋하지 않음")
        self.assertEqual((self.repo / "version.txt").read_text(), "1.0.0\n")
        self.assertEqual(self.git("ls-remote", str(self.origin)).strip(), b"", "푸시하지 않음")


def fake_ytdlp(script):
    """script: 차례대로 낼 오류 메시지 (None = 성공). calls 에 시도가 쌓임."""
    mod = types.ModuleType("yt_dlp_fake")

    class DownloadError(Exception):
        pass
    calls, seen_opts = [], []

    def step(x):
        calls.append(x)
        msg = script.pop(0) if script else None
        if msg:
            raise DownloadError(msg)

    class YoutubeDL:
        def __init__(self, opts):
            self.opts = opts
            seen_opts.append(dict(opts))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

        def close(self):
            pass

        def download(self, urls):
            step(urls)

        def extract_info(self, url, download=False):
            step(url)
            return {"entries": [{"id": "abcdefghijk", "title": "영상", "view_count": 10, "duration": 60}]}

    mod.DownloadError, mod.YoutubeDL, mod.calls, mod.opts = DownloadError, YoutubeDL, calls, seen_opts
    return mod


BOT = "ERROR: [youtube] abcdefghijk: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication."


class EngineTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(self.core, "ensure_deno", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def engine_pips(self):
        return [c for c in self.pip if "yt-dlp[default]" in c and "-U" in c]

    def test_bot_block_upgrades_once_and_retries_once(self):
        yt = fake_ytdlp([BOT])
        with mock.patch.object(self.core, "_yt", return_value=yt):
            failed = self.core.download(["abcdefghijk"], self.logs.append)
        self.assertEqual(failed, [])
        self.assertEqual(len(self.engine_pips()), 1, self.pip)
        self.assertEqual(len(yt.calls), 2)
        self.assertTrue((self.core.ENGINE_HOME / "engine_upgrade.json").exists())

    def test_still_blocked_after_upgrade_reports_friendly(self):
        yt = fake_ytdlp([BOT, BOT, BOT])
        with mock.patch.object(self.core, "_yt", return_value=yt):
            failed = self.core.download(["abcdefghijk"], self.logs.append)
        self.assertEqual(failed, ["abcdefghijk"])
        self.assertEqual(len(self.engine_pips()), 1)
        self.assertEqual(len(yt.calls), 2)
        self.assertTrue(any("로그인해 둔 브라우저를 골라" in m for m in self.logs), self.logs)  # 브라우저 고르기 (크롬만이 아님 · 쉬운 안내)

    def test_many_videos_upgrade_only_once(self):
        yt = fake_ytdlp(["HTTP Error 403: Forbidden", None, "HTTP Error 403: Forbidden"])
        with mock.patch.object(self.core, "_yt", return_value=yt):
            failed = self.core.download(["aaaaaaaaaaa", "bbbbbbbbbbb"], self.logs.append)
        self.assertEqual(len(self.engine_pips()), 1)
        self.assertEqual(len(yt.calls), 3)  # a: 막힘 → 최신화 → 성공, b: 막힘 (다시 최신화하지 않음)
        self.assertEqual(failed, ["bbbbbbbbbbb"])

    def test_other_errors_do_not_upgrade(self):
        yt = fake_ytdlp(["ERROR: [youtube] a403b: Video unavailable"])  # 영상 ID 속 403 은 막힘이 아님
        with mock.patch.object(self.core, "_yt", return_value=yt):
            failed = self.core.download(["a403bcdefgh"], self.logs.append)
        self.assertEqual(failed, ["a403bcdefgh"])
        self.assertEqual(self.engine_pips(), [])
        self.assertEqual(len(yt.calls), 1)

    def test_list_videos_bot_block_retries_once(self):
        yt = fake_ytdlp([BOT])
        with mock.patch.object(self.core, "_yt", return_value=yt):
            rows = self.core.list_videos("videos", None, "https://youtu.be/abcdefghijk", self.logs.append)
        self.assertEqual([r["id"] for r in rows], ["abcdefghijk"])
        self.assertEqual(len(self.engine_pips()), 1)
        self.assertEqual(len(yt.calls), 2)

    def test_list_videos_still_blocked_friendly_error(self):
        yt = fake_ytdlp([BOT, BOT])
        with mock.patch.object(self.core, "_yt", return_value=yt):
            with self.assertRaises(RuntimeError) as cm:
                self.core.list_videos("videos", None, "@채널", self.logs.append)
        self.assertIn("'로그인 정보로 받기'에서 고른 뒤", str(cm.exception))
        self.assertEqual(len(self.engine_pips()), 1)

    def test_startup_upgrade_every_3_days(self):
        stamp = self.core.ENGINE_HOME / "engine_upgrade.json"
        self.core.engine_autoupdate(self.logs.append)  # 기록 없음 → 최신화
        self.assertEqual(len(self.engine_pips()), 1)
        self.core.engine_autoupdate(self.logs.append)  # 방금 함 → 건너뜀
        self.assertEqual(len(self.engine_pips()), 1)
        stamp.write_text(json.dumps({"t": time.time() - 4 * 86400}), encoding="utf-8")
        self.core.engine_autoupdate(self.logs.append)
        self.assertEqual(len(self.engine_pips()), 2)

    def test_pip_failure_logged_not_raised(self):
        self.pip_rc = 1
        self.assertFalse(self.core.update_engine(self.logs.append))
        self.assertTrue(any("인터넷 연결" in m for m in self.logs))
        self.assertEqual(self.core.engine_age(), float("inf"), "성공 기록은 없음")
        self.assertLess(self.core._since("engine_upgrade", "t_fail"), 60)

    def test_startup_failure_backs_off_a_day(self):
        """인터넷이 막힌 곳: 켤 때마다 pip 를 다시 붙잡지 않음 (하루 뒤에 다시). 직접 누르면 바로 다시 해 봄."""
        self.pip_rc = 1
        self.core.engine_autoupdate(self.logs.append)
        self.core.engine_autoupdate(self.logs.append)
        self.assertEqual(len(self.engine_pips()), 1)
        self.core.update_engine(self.logs.append)
        self.assertEqual(len(self.engine_pips()), 2)
        self.core._stamp("engine_upgrade", t_fail=time.time() - 2 * 86400)
        self.pip_rc = 0
        self.core.engine_autoupdate(self.logs.append)
        self.assertEqual(len(self.engine_pips()), 3)
        self.assertLess(self.core.engine_age(), 60)
        self.assertEqual(self.core._since("engine_upgrade", "t_fail"), float("inf"), "성공하면 실패 기록은 지움")


    def test_waiting_for_engine_shows_label_then_clears(self):
        """엔진을 바꾸는 동안 기다린 채널 불러오기: 기다릴 때만 '엔진 준비 중', 끝나면 원래 작업 이름으로."""
        held, release, out = threading.Event(), threading.Event(), {}

        def holder():
            with self.core._ENGINE_LOCK:
                held.set()
                release.wait(10)
        h = threading.Thread(target=holder)
        h.start()
        self.assertTrue(held.wait(5))
        yt = fake_ytdlp([])
        with mock.patch.object(self.core, "_yt", return_value=yt):
            t = threading.Thread(target=lambda: out.setdefault("rows", self.core.list_videos(
                "videos", None, "https://youtu.be/abcdefghijk", self.logs.append)))
            t.start()
            time.sleep(0.3)
            self.assertEqual(self.core.PROGRESS.get("label"), "다운로드 엔진 준비 중")
            self.assertEqual(yt.calls, [])
            release.set()
            t.join(10)
            h.join(5)
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(self.core.PROGRESS, {}, "기다림이 끝나면 '엔진 준비 중' 글자를 지움")


class DenoTests(Base):
    """Windows 자동 설치 흐름 (리눅스에서는 deno.exe 대신 같은 이름의 시험용 스크립트)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")})
        p.start()
        self.addCleanup(p.stop)
        self.core.DENO_AUTO = True
        self.core._DENO = None
        self.zip = Path(self.tmp) / "deno-src.zip"
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("deno.exe", "#!/bin/sh\necho 'deno 2.9.7 (stable, release, x86_64-pc-windows-msvc)'\n")
        self.sum = f"Algorithm : SHA256\nHash      : {updater.sha256(self.zip).upper()}\nPath      : C:\\a\\deno.zip\n"
        self.fetched = []

    def fake_download(self, url, dest, progress=None, timeout=30):
        self.fetched.append(url)
        shutil.copy(self.zip, dest)
        return dest

    @unittest.skipIf(sys.platform == "win32", "시험용 deno 는 셸 스크립트")
    def test_installs_into_user_bin_and_prepends_path(self):
        with mock.patch.object(self.core.shutil, "which", return_value=None), \
                mock.patch.object(self.core.updater, "download", side_effect=self.fake_download), \
                mock.patch.object(self.core, "_fetch_text", return_value=self.sum):
            path = self.core.ensure_deno(self.logs.append)
        self.assertEqual(Path(path), self.core.ENGINE_HOME / "bin" / "deno")
        self.assertTrue(Path(path).is_file())
        self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(self.core.ENGINE_HOME / "bin"))
        self.assertEqual(self.core._js_opts(), {"js_runtimes": {"deno": {"path": path}}})
        self.assertEqual(self.fetched, [self.core.DENO_ZIP])
        with mock.patch.object(self.core.updater, "download", side_effect=AssertionError("다시 받으면 안 됨")):
            self.core._DENO = None
            self.assertEqual(self.core.ensure_deno(self.logs.append), path)

    def blocking_download(self, gate, started):
        """43MB 중 10MB 쯤 받다가 gate 가 열릴 때까지 멈춰 있는 다운로드."""
        def dl(url, dest, progress=None, timeout=30):
            if progress:
                progress(10_000_000, 43_000_000)
            started.set()
            gate.wait(20)
            return self.fake_download(url, dest)
        return dl

    @unittest.skipIf(sys.platform == "win32", "시험용 deno 는 셸 스크립트")
    def test_startup_install_does_not_block_listing(self):
        """켤 때 뒤에서 Deno 를 받는 동안에도 채널 불러오기는 엔진 잠금에 막히지 않음 (검수 재현)."""
        self.core._stamp("engine_upgrade", t=time.time())  # 엔진 최신화는 이번엔 건너뜀
        gate, started = threading.Event(), threading.Event()
        with mock.patch.object(self.core.shutil, "which", return_value=None), \
                mock.patch.object(self.core.updater, "download", side_effect=self.blocking_download(gate, started)), \
                mock.patch.object(self.core, "_fetch_text", return_value=self.sum):
            t = threading.Thread(target=self.core.engine_autoupdate, args=(self.logs.append,))
            t.start()
            self.assertTrue(started.wait(10))
            yt = fake_ytdlp([])
            with mock.patch.object(self.core, "_yt", return_value=yt):
                rows = self.core.list_videos("videos", None, "https://youtu.be/abcdefghijk", self.logs.append)
            self.assertEqual(len(rows), 1)
            self.assertTrue(t.is_alive(), "Deno 는 아직 받는 중")
            self.assertEqual(self.core.PROGRESS, {}, "켤 때 뒤에서 받는 것은 화면 진행률을 건드리지 않음")
            gate.set()
            t.join(20)
        self.assertTrue((self.core.ENGINE_HOME / "bin" / "deno").is_file())
        self.assertEqual(self.pip, [])

    @unittest.skipIf(sys.platform == "win32", "시험용 deno 는 셸 스크립트")
    def test_download_waits_for_inflight_install_and_uses_it(self):
        """다운로드는 켤 때 받기 시작한 Deno 설치를 (진행률을 보여 주며) 기다렸다가 그것을 씀."""
        self.core._stamp("engine_upgrade", t=time.time())
        gate, started, out = threading.Event(), threading.Event(), {}
        with mock.patch.object(self.core.shutil, "which", return_value=None), \
                mock.patch.object(self.core.updater, "download", side_effect=self.blocking_download(gate, started)), \
                mock.patch.object(self.core, "_fetch_text", return_value=self.sum):
            t = threading.Thread(target=self.core.engine_autoupdate, args=(self.logs.append,))
            t.start()
            self.assertTrue(started.wait(10))
            yt = fake_ytdlp([])
            with mock.patch.object(self.core, "_yt", return_value=yt):
                d = threading.Thread(target=lambda: out.setdefault("failed", self.core.download(["abcdefghijk"], self.logs.append)))
                d.start()
                time.sleep(1.2)
                self.assertEqual(yt.calls, [], "설치가 끝날 때까지 받기 시작하지 않음")
                self.assertEqual((self.core.PROGRESS.get("label"), self.core.PROGRESS.get("pct")), ("YouTube 해석 도구 받는 중", 23))
                gate.set()
                d.join(20)
                t.join(20)
        self.assertEqual(out["failed"], [])
        self.assertEqual(len(yt.calls), 1)
        self.assertEqual(yt.opts[-1]["js_runtimes"], {"deno": {"path": str(self.core.ENGINE_HOME / "bin" / "deno")}})
        self.assertEqual(sum("설치하는 중" in m for m in self.logs), 1, "한 번만 설치")

    def test_startup_install_failure_backs_off(self):
        """인터넷이 막힌 곳: 켤 때마다 43MB 받기를 다시 시도하지 않음 (하루 뒤에). 직접 누르면 바로 다시."""
        self.core._stamp("engine_upgrade", t=time.time())
        tries = []

        def offline(url, *a, **k):
            tries.append(url)
            raise OSError("인터넷 없음")
        with mock.patch.object(self.core.shutil, "which", return_value=None), \
                mock.patch.object(self.core.updater, "download", side_effect=offline), \
                mock.patch.object(self.core, "_fetch_text", side_effect=OSError("인터넷 없음")):
            self.core.engine_autoupdate(self.logs.append)
            self.core.engine_autoupdate(self.logs.append)
            self.assertEqual(len(tries), 1)
            self.assertIsNone(self.core.ensure_deno(self.logs.append))  # 업데이트 확인 버튼 → 바로 다시
            self.assertEqual(len(tries), 2)
            self.core._stamp("deno_install", t_fail=time.time() - 2 * 86400)
            self.core.engine_autoupdate(self.logs.append)
            self.assertEqual(len(tries), 3)

    def test_sha_mismatch_is_graceful(self):
        bad = "Hash      : " + "0" * 64
        with mock.patch.object(self.core.shutil, "which", return_value=None), \
                mock.patch.object(self.core.updater, "download", side_effect=self.fake_download), \
                mock.patch.object(self.core, "_fetch_text", return_value=bad):
            self.assertIsNone(self.core.ensure_deno(self.logs.append))
        self.assertFalse((self.core.ENGINE_HOME / "bin" / "deno").exists())
        self.assertTrue(any("설치하지 못했어요" in m for m in self.logs), self.logs)

    def test_offline_is_graceful(self):
        with mock.patch.object(self.core.shutil, "which", return_value=None), \
                mock.patch.object(self.core.updater, "download", side_effect=OSError("인터넷 없음")), \
                mock.patch.object(self.core, "_fetch_text", side_effect=OSError("인터넷 없음")):
            self.assertIsNone(self.core.ensure_deno(self.logs.append))
        self.assertTrue(any("설치하지 못했어요" in m and "인터넷 연결" in m for m in self.logs), self.logs)
        self.assertIsNone(self.core._DENO)
        self.assertEqual(self.core._js_opts(), {})


if __name__ == "__main__":
    unittest.main()
