"""Windows 에서만 깨지던 것들의 회귀 시험 (개발 PC 는 Linux → Windows 의 잠금·인코딩·포트·파일 이름 규칙을 흉내 내서 확인).

반쪽 이모지(JS .slice 가 자른 대리 문자) · config.json/dict.json 메모장 저장(BOM·ANSI·역슬래시) · 파이썬 자식 출력 인코딩 ·
자막 BOM · 막 만든 파일을 백신이 잡음(내보내기·묶기·미리보기·썸네일·편집본·분석) · 확장자만 다른 영상의 분석 폴더 ·
yt-dlp 중간 파일 · 스타일 이름(예약 이름·제어 문자) · 누끼 장면 이름 검사 · 포트(겹친 실행·예약 포트·프록시) · 절전 ·
작업 중 창 닫기 · pythonw 오류 기록 · 업데이트(뒤로 미룬 구성요소 설치·겹친 실행기) · 설치 확인(setup_check·bat) ·
바로가기 · 비공개 창 · 인증서 · claude 찾기 · NAS 경로(프리미어 XML).
인터넷은 쓰지 않는다. 실행: 저장소 폴더에서 python3 -m unittest tests.test_windows_compat
"""
import errno
import io
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import app  # noqa: E402
import bundle  # noqa: E402
import captions  # noqa: E402
import claude_cli  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import setup_check  # noqa: E402
import source  # noqa: E402
import style  # noqa: E402
import thumb  # noqa: E402
import updater  # noqa: E402
import winlink  # noqa: E402

FIRE = "20240101_abcdefghijk_풋살 꿀팁 모음집🔥🔥 무조건.mp4"  # JS: niceName(...).slice(0,12) 가 🔥 를 반으로 자름
HALF = "풋살 꿀팁 모음집🔥\ud83d"                               # 그렇게 서버에 온 이름 (짝 없는 대리 문자)
JAMO = "정형ᄃ"


def node():
    return shutil.which("node")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Work(unittest.TestCase):
    """작업 폴더를 임시 폴더로 (진짜 작업 폴더를 건드리지 않음)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="윈도우 시험 [꿀팁] "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        w = self.tmp / "작업 폴더"
        self.work = w
        for d in ("videos", "analysis", "out", "projects", "edit_media", "styles", "thumbnails/assets"):
            (w / d).mkdir(parents=True, exist_ok=True)
        pats = [mock.patch.object(core, "WORK", w), mock.patch.object(core, "VIDEOS", w / "videos"),
                mock.patch.object(core, "ANALYSIS", w / "analysis"), mock.patch.object(core, "OUT", w / "out"),
                mock.patch.object(editor, "PROJECTS", w / "projects"), mock.patch.object(editor, "ASSETS", w / "edit_media"),
                mock.patch.object(style, "STYLES", w / "styles"), mock.patch.object(thumb, "THUMBS", w / "thumbnails"),
                mock.patch.object(thumb, "ASSETS", w / "thumbnails" / "assets"), mock.patch.object(app, "LOGFILE", w / "studio.log"),
                mock.patch.object(source, "_online", return_value=False)]
        for p in pats:
            p.start()
            self.addCleanup(p.stop)
        core._STEMS["key"] = None

    def video(self, name, data=b"\0" * 100):
        p = core.VIDEOS / name
        p.write_bytes(data)
        return p


class Server(Work):
    """진짜 app.Handler 로 듣는 시험 서버 (같은 출처 요청만 받으므로 Host·Origin 을 맞춤)."""

    def setUp(self):
        super().setUp()
        self.port = free_port()
        p = mock.patch.object(app, "PORT", self.port)
        p.start()
        self.addCleanup(p.stop)
        self.srv = ThreadingHTTPServer(("127.0.0.1", self.port), app.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, body=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode("ascii"))
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="POST" if data is not None else "GET",
                                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"})
        try:
            with self.opener.open(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def wait_job(self, timeout=30):
        end = time.time() + timeout
        while app.JOB["name"] and time.time() < end:
            time.sleep(0.05)
        self.assertIsNone(app.JOB["name"], "작업이 끝나지 않음")


# ---------- 1. 반쪽 이모지 (짝 없는 대리 문자) ----------

class HalfEmoji(Server):
    def test_js_cut_by_code_points(self):
        """화면 세 곳의 자르기 도우미가 이모지를 반으로 자르지 않음 (예전 .slice(0,12) 는 '\\ud83d' 를 남김)."""
        if not node():
            self.skipTest("node 없음")
        for f in ("ui.html", "editor.html", "thumb.html"):
            src = (REPO / f).read_text(encoding="utf-8")
            line = next(x for x in src.splitlines() if x.startswith("const cutText"))
            js = line + ("\nconst niceName = n => n.replace(/^\\d{8}_[A-Za-z0-9_-]{11}_/, '').replace(/\\.[^.]+$/, '');"
                         f"\nconst n = niceName({json.dumps(FIRE)});"
                         "\nconst bad = s => /[\\ud800-\\udbff](?![\\udc00-\\udfff])|(?<![\\ud800-\\udbff])[\\udc00-\\udfff]/.test(s);"
                         "\nconsole.log(JSON.stringify([bad(n.slice(0, 12)), bad(cutText(n, 12)), cutText(n, 12), bad(cutText(n, 24))]));")
            out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
            self.assertEqual(out[0], True, "예전 방식은 반쪽을 남김 (흉내가 맞는지)")
            self.assertEqual(out[1:], [False, "풋살 꿀팁 모음집🔥🔥 ", False], f)

    def test_style_file_name_js_matches_python(self):
        """ui.html 의 styleFileName 과 style.clean_style_name 이 같은 이름을 만듦 (덮어쓰기 확인 창이 맞는 이름을 물음)."""
        if not node():
            self.skipTest("node 없음")
        src = (REPO / "ui.html").read_text(encoding="utf-8")
        start = src.index("const RESERVED = ")
        fn = src[start:src.index("$(\"btnLearn\").onclick", start)]
        cut = next(x for x in src.splitlines() if x.startswith("const cutText"))
        names = ["CON", "con.v2", "Nul", "a\tb 스타일", " .내 스타일. ", "가" * 70, HALF + " 스타일", "슛포러브 스타일", 'a:b*c?"<>|', ""]
        js = cut + "\n" + fn + f"\nconsole.log(JSON.stringify({json.dumps(names)}.map(styleFileName)));"
        got = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(got, [style.clean_style_name(n) for n in names])

    def test_body_repairs_lone_surrogates(self):
        b = core.clean_json(json.loads('{"name": "' + "풋살\\ud83d 스타일" + '", "n": [{"t": "\\udd25x"}], "ok": "🔥"}'))
        self.assertEqual(b, {"name": "풋살\ufffd 스타일", "n": [{"t": "\ufffdx"}], "ok": "🔥"})
        json.dumps(b, ensure_ascii=False).encode("utf-8")  # 이제 UTF-8 로 쓸 수 있음
        self.assertEqual(core.clean_text("\ud83d\udd25"), "🔥", "따로 온 짝은 이어 붙임")

    def test_log_survives_and_state_still_answers(self):
        """반쪽 글자가 든 기록 줄: studio.log 쓰기에서 작업이 죽지 않고, /api/state 가 계속 답함 (예전: 연결이 끊겨 화면이 멈춤)."""
        app.log(f"스타일 저장 · {HALF} 스타일")
        self.assertEqual(app.LOG[-1], "스타일 저장 · 풋살 꿀팁 모음집🔥\ufffd 스타일")
        self.assertIn("스타일 저장", (core.WORK / "studio.log").read_text(encoding="utf-8"))
        app.LOG.append("예전 기록에 남은 반쪽 \ud83d")  # 고치기 전에 들어간 줄이 있어도
        self.addCleanup(lambda: app.LOG.remove("예전 기록에 남은 반쪽 \ud83d"))
        code, st = self.call("/api/state?since=0")
        self.assertEqual(code, 200)
        self.assertIn("예전 기록에 남은 반쪽 \ud83d", st["log"], "\\uXXXX 로 보내 그대로 돌아옴")

    def test_style_learn_with_half_emoji_name(self):
        """JS 가 보낸 '…🔥\\ud83d 스타일' 이름: 반쪽은 빼고 저장 → 목록·화면 응답이 깨지지 않음."""
        self.video(FIRE)
        prof = {"avgShot": 2.0, "cutsPerMin": 30, "medianShot": 2.0, "avgZoom": 1.1, "captionRatio": 0.5, "captionPos": "bottom",
                "captionColor": "#FFFFFF", "zoomRate": 0.1, "pauseKeep": 0.5}
        with mock.patch.object(style, "extract_events", return_value={}), mock.patch.object(style, "summarize", return_value=dict(prof)), \
                mock.patch.object(style.plan, "extract_plan", side_effect=RuntimeError("건너뜀")), \
                mock.patch.object(style, "merge", return_value=dict(prof)), mock.patch.object(style, "_log_prof"), \
                mock.patch.object(style, "describe", return_value="설명"), mock.patch.object(style, "edit_params", return_value={}):
            raw = ('{"name": "풋살 꿀팁 모음집🔥\\ud83d 스타일", "names": [' + json.dumps(FIRE) + "]}").encode("utf-8")
            code, r = self.call("/api/style/learn", raw=raw)
            self.assertEqual(code, 200, r)
            self.wait_job()
        self.assertIsNone(app.JOB["error"], app.JOB["error"])
        files = [f.name for f in style.STYLES.glob("*.json")]
        self.assertEqual(files, ["풋살 꿀팁 모음집🔥 스타일.json"])
        with mock.patch.object(style, "edit_params", return_value={}), mock.patch.object(style, "describe", return_value=""):
            code, r = self.call("/api/style/list")
        self.assertEqual((code, [s["name"] for s in r["styles"]]), (200, ["풋살 꿀팁 모음집🔥 스타일"]))

    def test_repair_old_half_emoji_style_file(self):
        """Windows 에서 이미 반쪽 이름으로 저장된 스타일 파일 → 목록을 볼 때 고친 이름으로 바꿈 (그 이름은 화면에 못 보냄)."""
        class F:  # Linux 파일 이름에는 짝 없는 대리 문자를 쓸 수 없어 경로를 흉내
            stem, name = HALF, HALF + ".json"

            def with_name(self, n):
                return style.STYLES / n
        with mock.patch.object(style.os, "replace") as rep:
            new = style._repair_name(F())
        self.assertEqual(new.name, "풋살 꿀팁 모음집🔥.json")
        rep.assert_called_once()

    def test_thumb_save_and_export_with_half_emoji(self):
        self.video(FIRE)
        doc = {"designs": [{"layers": [{"type": "text", "text": "풋살 꿀팁\ud83d"}]}]}
        self.assertTrue(thumb.save_docs(FIRE, doc))
        self.assertIn("풋살 꿀팁\ufffd", thumb._doc_path(FIRE).read_text(encoding="utf-8"))
        out = thumb.export_image(FIRE, "data:image/png;base64,iVBORw0KGgo=", "png", "썸네일\ud83d\t")
        self.assertTrue(out.name.endswith("_썸네일_1.png"), out.name)


# ---------- 2. config.json · dict.json (메모장 저장) ----------

class NotepadJson(Work):
    def read(self, raw):
        d = self.tmp / "cfg"
        d.mkdir(exist_ok=True)
        (d / "config.json").write_bytes(raw)
        return updater.read_config(d)

    def test_bom_ansi_and_backslashes(self):
        cases = [
            ('{"workspace": "D:\\\\풋살작업"}'.encode("utf-8-sig"), "D:\\풋살작업"),            # UTF-8(BOM)
            ('{"workspace": "D:\\\\풋살작업"}'.encode("cp949"), "D:\\풋살작업"),                # ANSI (옛 메모장)
            ('{"workspace": "D:\\풋살작업"}'.encode("utf-8"), "D:\\풋살작업"),                   # 역슬래시 하나 (탐색기 주소 붙여 넣기)
            ('{"workspace": "D:\\new\\영상"}'.encode("utf-8"), "D:\\new\\영상"),                 # \n 이 줄바꿈으로 읽히던 것
            ('{"workspace": "E:\\풋살\\", "channel_url": "x"}'.encode("utf-8"), "E:\\풋살\\"),  # 끝이 역슬래시
            ('{"workspace": "C:\\\\Users\\\\홍길동\\\\작업"}'.encode("utf-8"), "C:\\Users\\홍길동\\작업"),  # 바르게 쓴 것은 그대로
        ]
        for raw, want in cases:
            cfg, note = self.read(raw)
            self.assertEqual((cfg.get("workspace"), note), (want, None), raw)

    def test_broken_config_uses_defaults_with_note(self):
        cfg, note = self.read(b'{"workspace": "D:\\x", oops}')
        self.assertEqual(cfg, {})
        self.assertIn("config.json", note)

    def test_core_starts_with_broken_config_and_bad_workspace(self):
        """깨진 config.json · 쓸 수 없는 작업 폴더(빠진 외장 드라이브): 예전에는 core import 에서 멈춰 앱이 아예 안 켜짐."""
        d = self.tmp / "앱"
        d.mkdir()
        shutil.copy(REPO / "core.py", d / "core.py")
        (d / "version.txt").write_text("9.9.9\n", encoding="utf-8")
        blocker = self.tmp / "파일이라 폴더를 못 만듦"
        blocker.write_text("x", encoding="utf-8")
        home = self.tmp / "home"
        for raw, why in ((b"\xef\xbb\xbf{ broken", "config.json"), (json.dumps({"workspace": str(blocker / "작업")}).encode("utf-8"), "작업 폴더")):
            (d / "config.json").write_bytes(raw)
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONPATH=str(REPO))
            r = subprocess.run([sys.executable, "-c", "import core, json; print(json.dumps([str(core.WORK), core.CONFIG_NOTES, core.CONFIG['channel_url']]))"],
                               cwd=str(d), capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            work, notes, ch = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(Path(work), home / "풋살사관학교_작업")
            self.assertTrue(notes and why in notes[0], notes)
            self.assertTrue(ch.startswith("https://www.youtube.com/"))

    def test_updater_log_follows_configured_workspace(self):
        """BOM 붙은 config.json 이어도 실행기가 사용자가 정한 작업 폴더에 studio.log 를 남김 (예전: 기본 폴더로 감)."""
        d = self.tmp / "앱2"
        d.mkdir()
        ws = self.tmp / "내 작업"
        (d / "config.json").write_bytes(json.dumps({"workspace": str(ws)}).encode("utf-8-sig"))
        updater.studio_log(d, f"시험 {HALF}")
        self.assertIn("시험 풋살", (ws / "studio.log").read_text(encoding="utf-8"))

    def test_dict_json_bom_ansi_and_broken_kept(self):
        p = self.tmp / "dict.json"
        d = {"terms": ["파라렐라", "피벗"], "fix": {"피버": "피벗"}}
        for raw in (json.dumps(d, ensure_ascii=False).encode("utf-8-sig"), json.dumps(d, ensure_ascii=False).encode("cp949")):
            p.write_bytes(raw)
            self.assertEqual(captions.load_dict(p)["terms"], ["파라렐라", "피벗"])
        p.write_bytes(b'{"terms": ["\xc6\xc4\xb6\xf3", }')  # 깨진 파일 → 기본 사전 + 내용은 .bad 로 남김
        self.assertEqual(captions.load_dict(p), captions.default_dict())
        self.assertEqual((self.tmp / "dict.json.bad").read_bytes(), p.read_bytes())


# ---------- 3. 파이썬 자식 출력 인코딩 · 자식 프로세스 ----------

class ChildProcess(unittest.TestCase):
    def test_python_child_gets_utf8_env(self):
        r = core.run([sys.executable, "-c", "import os,sys; sys.stderr.write(os.environ.get('PYTHONIOENCODING','') + '|' + "
                                            "os.environ.get('PYTHONUTF8','') + '|지정된 파일을 찾을 수 없습니다: C:\\\\Users\\\\홍길동')"])
        self.assertEqual(r.stderr, "utf-8|1|지정된 파일을 찾을 수 없습니다: C:\\Users\\홍길동")
        self.assertEqual(updater.py_env()["PYTHONUTF8"], "1")

    def test_children_are_tracked_for_kill_on_close(self):
        """core.run·popen 으로 띄운 자식은 '앱이 꺼지면 같이 꺼짐' 묶음에 들어감 (Windows Job Object · 여기서는 부름만 확인)."""
        with mock.patch.object(core, "track", side_effect=lambda p: p) as tr:
            core.run([sys.executable, "-c", "pass"])
            p = core.popen([sys.executable, "-c", "pass"], stdout=subprocess.DEVNULL)
            p.wait()
        self.assertEqual(tr.call_count, 2)
        self.assertIsNone(core._kill_on_close_job() if sys.platform != "win32" else None)

    def test_run_timeout_kills(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            core.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)


# ---------- 4. 자막 파일 BOM ----------

class SrtBom(Work):
    def test_export_srt_has_bom_and_reads_back(self):
        caps = [{"start": 0.0, "end": 1.0, "text": "집중을 해야 되고"}]
        p = core.OUT / "자막.srt"
        updater.write_atomic(p, editor._srt(caps), encoding=core.SRT_ENCODING)
        self.assertTrue(p.read_bytes().startswith(b"\xef\xbb\xbf"))
        import upload
        self.assertEqual([s["text"] for s in upload._segments(p)], ["집중을 해야 되고"])

    def test_export_side_files(self):
        """내보내기의 .srt 는 BOM, 프리미어 XML 은 그대로 (영상 없이 내보내기)."""
        self.video("테스트.mp4")
        proj = {"id": "s", "name": "롱폼", "format": "long", "v": 2, "captionsOn": True, "captionStyle": dict(editor.LONG_STYLE), "titles": [],
                "shapes": [], "layout": {"mode": "fill"}, "master": {"volume": 1, "normalize": False, "lufs": -14},
                "duck": {"on": False, "amount": -14}, "tracks": editor.default_tracks(), "items": [], "trans": [], "markers": [],
                "captions": [{"start": 0.0, "end": 1.0, "text": "한글 자막"}], "info": {"duration": 2.0, "width": 320, "height": 180, "fps": 30.0},
                "source": "테스트.mp4", "media": [{"id": "main", "kind": "video", "src": "videos", "file": "테스트.mp4", "dur": 2.0, "w": 320, "h": 180,
                                                  "fps": 30.0, "audio": True}]}
        proj["items"] = [{"id": "a", "track": "V1", "media": "main", "start": 0, "in": 0, "out": 2, "speed": 1, "rev": False, "link": None,
                          "fit": "auto", "fx": {}, "color": {}, "reframe": 0.5}]
        out = editor.export("테스트.mp4", proj, {"video": False, "srt": True, "xml": True}, lambda *a: None)
        srt = next(o for o in out if o.endswith(".srt"))
        xml = next(o for o in out if o.endswith(".xml"))
        self.assertTrue((core.OUT / srt).read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertTrue((core.OUT / xml).read_bytes().startswith(b"<?xml"))


# ---------- 5. 백신·탐색기 잠금 (막 만든 파일) ----------

def locked(pred, secs, real=os.replace):
    """pred(src) 인 파일을 처음 건드린 뒤 secs 초 동안 os.replace 를 PermissionError(WinError 32 흉내)로 막는 가짜."""
    first = {}

    def fake(src, dst):
        if pred(Path(src)):
            t0 = first.setdefault(str(src), time.monotonic())
            if time.monotonic() - t0 < secs:
                e = PermissionError(errno.EACCES, "다른 프로세스가 파일을 사용 중이기 때문에 프로세스가 액세스 할 수 없습니다")
                e.winerror = 32
                raise e
        return real(src, dst)
    return fake


class Locks(Work):
    def test_replace_retry_waits_then_gives_up(self):
        a, b = self.tmp / "a.mp4", self.tmp / "b.mp4"
        a.write_bytes(b"x")
        with mock.patch.object(updater.os, "replace", locked(lambda p: p.name == "a.mp4", 0.6)):
            updater.replace_retry(a, b, secs=5)
        self.assertTrue(b.exists())
        b.rename(a)
        with mock.patch.object(updater.os, "replace", locked(lambda p: p.name == "a.mp4", 99)):
            t0 = time.monotonic()
            with self.assertRaises(PermissionError):
                updater.replace_retry(a, b, secs=0.5)
            self.assertLess(time.monotonic() - t0, 3)
        with self.assertRaises(FileNotFoundError):  # 잠금이 아닌 오류는 바로
            updater.replace_retry(self.tmp / "없음", b, secs=5)

    def test_write_atomic_keeps_old_on_failure(self):
        p = self.tmp / "편집본.json"
        p.write_text("예전", encoding="utf-8")
        with mock.patch.object(updater.os, "replace", locked(lambda s: s.name.startswith("편집본.json."), 99)):
            with self.assertRaises(PermissionError):
                updater.write_atomic(p, "새 내용", secs=0.2)
        self.assertEqual(p.read_text(encoding="utf-8"), "예전")
        self.assertEqual([x.name for x in self.tmp.iterdir() if x.name.endswith(".tmp")], [], "임시 파일은 지움")

    @unittest.skipIf(sys.platform == "win32", "권한 비트는 POSIX 만")
    def test_write_atomic_secret_mode_from_creation(self):
        """유튜브 토큰·세션(yt.save_secret)과 합침(D-048): mode 를 주면 임시 파일이 처음부터 그 권한 — 바꿔 끼우기 전에도 남이 못 읽음."""
        seen = []
        real = updater.replace_retry

        def spy(src, dst, secs=None):
            seen.append(os.stat(src).st_mode & 0o777)
            return real(src, dst, secs)
        old = os.umask(0)
        try:
            with mock.patch.object(updater, "replace_retry", spy):
                updater.write_atomic(self.tmp / "token.bin", b"FSY1P\n{}", mode=0o600)
                updater.write_atomic(self.tmp / "글.json", "{}", mode=0o600)
        finally:
            os.umask(old)
        self.assertEqual(seen, [0o600, 0o600])
        self.assertEqual((self.tmp / "token.bin").read_bytes(), b"FSY1P\n{}")
        self.assertEqual((self.tmp / "글.json").stat().st_mode & 0o777, 0o600)

    def test_write_atomic_mode_fails_cleanly(self):
        """mode 경로: 파일을 연 뒤 글 쪽 준비가 실패해도(잘못된 인코딩 등) 손잡이가 새지 않고 임시 파일은 지움
        (open() 의 opener 로 열어 손잡이를 open() 이 맡음 · Windows 는 열린 채면 못 지움)."""
        before = len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None
        for _ in range(20):
            with self.assertRaises(LookupError):
                updater.write_atomic(self.tmp / "s.json", "{}", encoding="없는-인코딩", mode=0o600)
        if before is not None:
            self.assertLessEqual(len(os.listdir("/proc/self/fd")), before)
        self.assertEqual([x.name for x in self.tmp.iterdir() if x.name.endswith(".tmp")], [], "임시 파일은 지움")

    def test_export_final_waits_for_antivirus(self):
        """완성본(final.mp4)을 백신이 1초 잡음: 예전에는 0.25초 뒤 포기하고 임시 폴더째 지워 렌더링을 잃었음."""
        d = core.OUT / ".render_abc"
        d.mkdir()
        (d / "final.mp4").write_bytes(b"MP4")
        logs = []
        with mock.patch.object(updater.os, "replace", locked(lambda p: p.name == "final.mp4", 1.0)):
            out = editor._place_final(d / "final.mp4", core.OUT / "완성.mp4", logs.append)
        self.assertEqual((out.name, out.read_bytes()), ("완성.mp4", b"MP4"))

    def test_export_final_kept_when_still_locked(self):
        d = core.OUT / ".render_abc"
        d.mkdir()
        (d / "final.mp4").write_bytes(b"MP4")
        real = os.replace

        def fake(src, dst):
            if Path(src).name == "final.mp4":
                raise PermissionError(errno.EACCES, "잠김")
            return real(src, dst)
        with mock.patch.object(updater, "SETTLE_SECS", 0.3), mock.patch.object(updater.os, "replace", fake):
            with self.assertRaises(editor.KeptFinal) as cm:
                editor._place_final(d / "final.mp4", core.OUT / "완성.mp4", lambda *a: None)
        kept = [x for x in core.OUT.iterdir() if x.name.startswith(editor.KEEP_PREFIX)]
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0] / "final.mp4").read_bytes(), b"MP4", "완성본은 지우지 않음")
        self.assertIn(str(kept[0]), str(cm.exception))
        self.assertNotIn("같은 이름 파일", str(cm.exception), "대상 이름은 비어 있었음 (예전 안내는 틀렸음)")
        editor.sweep_temp(0)
        self.assertTrue((kept[0] / "final.mp4").exists(), "다음 내보내기의 정리에서도 지우지 않음")

    def test_export_final_target_open_uses_other_name(self):
        d = core.OUT / ".render_abc"
        d.mkdir()
        (d / "final.mp4").write_bytes(b"MP4")
        (core.OUT / "완성.mp4").write_bytes(b"OLD")  # 플레이어가 열어 둔 같은 이름
        real = os.replace

        def fake(src, dst):
            if Path(dst).name == "완성.mp4":
                raise PermissionError(errno.EACCES, "잠김")
            return real(src, dst)
        logs = []
        with mock.patch.object(updater, "SETTLE_SECS", 0.2), mock.patch.object(updater.os, "replace", fake):
            out = editor._place_final(d / "final.mp4", core.OUT / "완성.mp4", logs.append)
        self.assertEqual(out.name, "완성 (2).mp4")

    def test_bundle_keeps_file_when_move_fails(self):
        tmp = core.OUT / f"{bundle.TMP_PREFIX}x"
        tmp.mkdir()
        (tmp / "bundle.mp4").write_bytes(b"B")
        kept = bundle._keep_tmp(tmp)
        self.assertTrue(kept.name.startswith(bundle.KEEP_PREFIX))
        bundle._sweep_old()
        self.assertTrue((kept / "bundle.mp4").exists())

    def test_thumb_save_lock_keeps_current_and_answers(self):
        """썸네일 디자인 저장 중 잠김: 지금 파일은 그대로(예전: .bak 으로 옮겨진 채 사라짐) · 화면에는 500 JSON (예전: 연결 끊김)."""
        self.video(f"{JAMO}.mp4")
        thumb.save_docs(f"{JAMO}.mp4", {"designs": [{"v": 1}]})
        p = thumb._doc_path(f"{JAMO}.mp4")
        with mock.patch.object(updater, "REPLACE_SECS", 0.2), \
                mock.patch.object(updater.os, "replace", locked(lambda s: s.name.startswith(p.name + "."), 99)):
            with self.assertRaises(PermissionError):
                thumb.save_docs(f"{JAMO}.mp4", {"designs": [{"v": 2}]})
        self.assertEqual(thumb.load_docs(f"{JAMO}.mp4"), {"designs": [{"v": 1}]})
        self.assertTrue(p.exists())

    def test_save_project_backup_failure_is_not_save_failure(self):
        self.video("a.mp4")
        with mock.patch.object(editor, "_backup_copy", side_effect=PermissionError(errno.EACCES, "잠김")):
            rev = editor.save_project("a.mp4", {"sequences": []})
        self.assertEqual(rev, 1)
        self.assertEqual(json.loads(editor._ppath("a.mp4").read_text(encoding="utf-8"))["rev"], 1)

    def test_upload_partial_is_not_kept(self):
        """가져오기가 끊김(Windows: ConnectionAbortedError) · 덜 받음: 반쪽 파일이 미디어 목록에 남지 않음."""
        class Abort(io.BytesIO):
            def read(self, n=-1):
                if self.tell() >= 1 << 20:
                    raise ConnectionAbortedError(10053, "현재 연결은 사용자의 호스트 시스템의 소프트웨어의 의해 중단되었습니다")
                return super().read(n)
        with self.assertRaises(ConnectionAbortedError):
            editor.save_upload("내 브금 ᄃ.mp3", Abort(b"\0" * (3 << 20)), 3 << 20)
        with self.assertRaises(ValueError):
            editor.save_upload("clip.mp4", io.BytesIO(b"\0" * 100), 1000)
        self.assertEqual(list(editor.ASSETS.iterdir()), [])
        r = editor.save_upload("clip.mp4", io.BytesIO(b"\0" * 100), 100)
        self.assertEqual((r["file"], [x.name for x in editor.ASSETS.iterdir()]), ("clip.mp4", ["clip.mp4"]))

    def test_grab_and_freeze_do_not_leave_partial(self):
        """ffmpeg 가 반쯤 쓰다 실패(디스크 가득): 예전에는 반쪽 그림이 '있는 파일'로 남아 다음부터 그대로 쓰였음."""
        self.video("v.mp4")

        def half(cmd, timeout=None):
            Path(cmd[-1]).write_bytes(b"\xff\xd8partial")
            return subprocess.CompletedProcess(cmd, 1, "", "No space left on device")
        with mock.patch.object(core, "run", side_effect=half), mock.patch.object(editor, "probe", return_value={"hdr": None}):
            out = thumb.grab("v.mp4", 1.0)
            with self.assertRaises(RuntimeError):
                editor.freeze_frame("videos", "v.mp4", 1.0)
        self.assertFalse(out.exists())
        self.assertEqual([x.name for x in out.parent.iterdir()], [])
        self.assertEqual([x.name for x in editor.ASSETS.iterdir()], [])

    def test_make_proxy_waits_for_lock(self):
        self.video("v.mp4")
        out = editor.proxy_path("v.mp4")

        class P:
            returncode = 0
            stdout = io.BytesIO(b"out_time_us=1000000\n")

            def wait(self):
                out.with_suffix(".part.mp4").write_bytes(b"PROXY")
        with mock.patch.object(core, "popen", return_value=P()), mock.patch.object(editor, "probe", return_value={"duration": 2, "hdr": None}), \
                mock.patch.object(updater.os, "replace", locked(lambda p: p.name == "proxy.part.mp4", 0.5)):
            editor.make_proxy("v.mp4", "videos", lambda *a: None)
        self.assertEqual(out.read_bytes(), b"PROXY")


# ---------- 6. 편집점 찾기 결과 (잠긴 소리 파일 · 반쪽 결과) ----------

class AnalyzeResults(Work):
    def fake_model(self):
        class Seg:
            start, end, text, words = 0.0, 1.0, " 안녕하세요", None

        class Info:
            duration = 2.0

        class M:
            hf_tokenizer = None

            def transcribe(self, audio, **kw):
                return iter([Seg()]), Info()
        return M()

    def run_analyze(self, name):
        with mock.patch.object(core, "_whisper", return_value=self.fake_model()), mock.patch.object(core, "dict_path", return_value=self.tmp / "dict.json"):
            return core.analyze(name, lambda *a: None)

    def make(self, name):
        r = core.run([core.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=2", "-f", "lavfi", "-i", "color=c=red:s=64x36:d=2",
                      "-shortest", "-c:a", "aac", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(core.VIDEOS / name)])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_locked_wav_does_not_lose_transcript(self):
        """받아쓰기 뒤 audio.wav 를 탐색기·백신이 잡음: 예전에는 결과를 쓰기 전에 지우다 실패해 받아쓰기를 통째로 잃었음."""
        name = f"{JAMO} [꿀팁].mp4"
        self.make(name)
        real = Path.unlink

        def unlink(p, *a, **k):
            if p.name == "audio.wav":
                raise PermissionError(errno.EACCES, "잠김")
            return real(p, *a, **k)
        with mock.patch.object(Path, "unlink", unlink):
            d = self.run_analyze(name)
        self.assertTrue((d / "transcript_timeline.md").exists())
        self.assertEqual(json.loads((d / "transcript.json").read_text(encoding="utf-8"))[0]["text"], "안녕하세요")
        self.assertTrue((d / "subtitles.srt").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(core.local_videos()[0]["analyzed"], True)

    def test_interrupted_rewrite_keeps_old_results(self):
        name = "a.mp4"
        self.make(name)
        d = self.run_analyze(name)
        before = (d / "transcript.json").read_bytes()
        real = updater.write_atomic

        def boom(path, data, *a, **k):
            if Path(path).name == "analysis.json":
                raise OSError(errno.ENOSPC, "디스크 공간이 부족합니다")
            return real(path, data, *a, **k)
        with mock.patch.object(updater, "write_atomic", boom), self.assertRaises(OSError):
            self.run_analyze(name)
        self.assertEqual(json.loads((d / "transcript.json").read_bytes()), json.loads(before))
        core.timeline_events(name)  # 깨진 JSON 이 없음


# ---------- 7. 확장자만 다른 영상 · yt-dlp 중간 파일 ----------

class LibraryNames(Work):
    def analyzed(self, name):
        d = core.adir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript_timeline.md").write_text(f"# 타임라인: {name}\n\n", encoding="utf-8")
        return d

    def test_same_stem_different_extension(self):
        """IMG_1234.MOV(먼저 분석)와 IMG_1234.mp4: 분석 폴더·편집본·썸네일 문서가 섞이지 않음."""
        self.video("IMG_1234.MOV")
        mov = self.analyzed("IMG_1234.MOV")
        self.assertEqual(mov.name, "IMG_1234", "혼자일 때는 예전 그대로")
        self.video("IMG_1234.mp4")
        core._STEMS["key"] = None
        mp4 = core.adir("IMG_1234.mp4")
        self.assertEqual(core.adir("IMG_1234.MOV"), mov, "먼저 분석한 쪽이 예전 폴더를 그대로 씀")
        self.assertNotEqual(mp4, mov)
        self.assertNotEqual(editor._ppath("IMG_1234.mp4"), editor._ppath("IMG_1234.MOV"))
        self.assertNotEqual(thumb._doc_path("IMG_1234.mp4"), thumb._doc_path("IMG_1234.MOV"))
        vids = {v["name"]: v["analyzed"] for v in core.local_videos()}
        self.assertEqual(vids, {"IMG_1234.MOV": True, "IMG_1234.mp4": False}, "mp4 가 '분석함'으로 보이지 않음")
        mp4.mkdir(parents=True)
        (core.VIDEOS / "IMG_1234.MOV").unlink()  # 짝을 지워도 한 번 생긴 폴더는 계속 그 영상 것
        core._STEMS["key"] = None
        self.assertEqual(core.adir("IMG_1234.mp4"), mp4)

    def test_case_only_twins_and_nobody_analyzed(self):
        """Windows 는 폴더 이름의 대소문자를 안 가림: Clip.MP4 / clip.mov → 둘 다 분석 전이면 이름 순서로 (늘 같은 답)."""
        self.video("Clip.MP4")
        self.video("clip.mov")
        a, b = core.adir("Clip.MP4"), core.adir("clip.mov")
        self.assertNotEqual(a.name.lower(), b.name.lower())
        self.assertEqual((a.name, b.name), (core.adir("Clip.MP4").name, core.adir("clip.mov").name))

    def test_project_source_marks_owner(self):
        self.video("x.mkv")
        editor.save_project("x.mkv", {"source": "x.mkv", "sequences": []})
        self.video("x.mp4")
        core._STEMS["key"] = None
        self.assertEqual(core.adir("x.mkv").name, "x")
        self.assertNotEqual(core.adir("x.mp4").name, "x")

    def test_ytdlp_partials_hidden(self):
        base = "20240101_abcdefghijk_정형ᄃ 풋살 기본기"
        for n in (f"{base}.mp4", f"{base}.f137.mp4", f"{base}.f399-1.mp4", f"{base}.temp.mp4", f"{base}.f140.m4a.part", "레이싱.f1.mp4"):
            self.video(n)
        self.assertEqual([v["name"] for v in core.local_videos()], ["20240101_abcdefghijk_정형ᄃ 풋살 기본기.mp4", "레이싱.f1.mp4"])
        self.assertEqual(source._find_file("abcdefghijk"), f"{base}.mp4", ".f137.mp4 가 앞에 정렬되어도 진짜 영상")
        self.assertNotIn(f"{base}.f137.mp4", source._library())

    def test_listing_survives_file_vanishing(self):
        """yt-dlp 가 중간 파일을 지우는 순간 (목록엔 있고 stat 은 실패): 예전에는 /api/state 가 끊김."""
        self.video("a.mp4")
        self.video("b.mp4")
        real = Path.stat

        def stat(p, *a, **k):
            if p.name == "a.mp4":
                raise FileNotFoundError(2, "없음")
            return real(p, *a, **k)
        with mock.patch.object(Path, "stat", stat):
            self.assertEqual([v["name"] for v in core.local_videos()], ["b.mp4"])

    def test_download_dir_not_in_trimmed_template(self):
        """trim_file_name 은 outtmpl 전체를 자름 → 긴 작업 폴더면 제목이 사라지던 것: 폴더는 paths 로."""
        seen = {}

        class Y:
            def __init__(self, opts):
                seen.update(opts)

            def download(self, urls):
                pass

            def close(self):
                pass
        yt = mock.Mock(YoutubeDL=Y)
        with mock.patch.object(core, "_yt", return_value=yt), mock.patch.object(core, "ensure_deno", return_value=None), \
                mock.patch.object(core, "_FF", "ffmpeg"):
            core.download(["abcdefghijk"], lambda *a: None)
        self.assertEqual(seen["paths"], {"home": str(core.VIDEOS)})
        self.assertFalse(os.path.isabs(seen["outtmpl"]))


# ---------- 8. 스타일 이름 · 누끼 장면 이름 ----------

class Names(Server):
    def test_style_names_for_windows(self):
        self.assertEqual(style.clean_style_name("CON"), "CON 스타일")
        self.assertEqual(style.clean_style_name("con.v2"), "con.v2 스타일")
        self.assertEqual(style.clean_style_name("a\tb\x00 스타일"), "ab 스타일")
        self.assertEqual(len(style.clean_style_name("가" * 300)), 60)
        self.assertEqual(style.clean_style_name(HALF), "풋살 꿀팁 모음집🔥")
        self.assertEqual(style.clean_style_name(" .. "), "내 스타일")
        self.assertEqual(style.clean_style_name("슛포러브 스타일"), "슛포러브 스타일")

    def test_thumb_cut_checks_frame_name(self):
        """누끼 요청의 장면 주소(src)에 든 이름도 보관함 안 파일 이름만 (I-021)."""
        with mock.patch.object(thumb, "grab") as grab, mock.patch.object(thumb, "remove_bg") as rb:
            code, r = self.call("/api/thumb/cut", {"src": "/frame?name=..%5C..%5Cother%5Cx.mp4&t=1", "kind": "hq"})
            self.assertEqual(code, 200)
            self.wait_job()
            grab.assert_not_called()
            rb.assert_not_called()
            self.assertIn("잘못된 파일 이름", app.JOB["error"] or "")

    def test_style_delete_locked_answers(self):
        (style.STYLES / "a.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(Path, "unlink", side_effect=PermissionError(errno.EACCES, "잠김")):
            code, r = self.call("/api/style/delete", {"name": "a"})
        self.assertEqual((code, r["ok"]), (500, False))

    def test_thumb_save_failure_is_json(self):
        with mock.patch.object(thumb, "save_docs", side_effect=PermissionError(errno.EACCES, "잠김")):
            code, r = self.call("/api/thumb/save", {"name": "a.mp4", "docs": {"designs": [{}]}})
        self.assertEqual((code, r["ok"]), (500, False))
        with mock.patch.object(thumb, "export_image", side_effect=OSError(errno.ENOSPC, "디스크 공간이 부족합니다")):
            code, r = self.call("/api/thumb/export", {"name": "a.mp4", "data": "data:image/png;base64,AA==", "fmt": "png"})
        self.assertEqual((code, r["ok"]), (500, False))
        with mock.patch.object(thumb, "save_upload", side_effect=PermissionError(errno.EACCES, "잠김")):
            code, r = self.call("/api/thumb/upload", {"data": "data:image/png;base64,AA=="})
        self.assertEqual((code, r["ok"]), (500, False))

    def test_media_stream_abort(self):
        """미리보기를 앞뒤로 옮기면 WebView2 가 연결을 끊음 (Windows: ConnectionAbortedError 10053) → 요청 처리에서 새지 않음."""
        f = self.tmp / "m.bin"
        f.write_bytes(b"\0" * (3 << 20))
        h = app.Handler.__new__(app.Handler)
        h.headers = {}

        class W:
            def write(self, b):
                raise ConnectionAbortedError(10053, "끊김")
        h.wfile = W()
        h.send_response = h.send_header = h.end_headers = lambda *a: None
        h._file(f, "video/mp4")
        h._send(200, {"ok": True})


# ---------- 9. 포트 · 겹친 실행 · 프록시 ----------

class Ports(Work):
    def setUp(self):
        super().setUp()
        self.base = free_port()
        for p in (mock.patch.object(app, "PORT", self.base), mock.patch.object(app, "PORT_FALLBACK", range(self.base + 1, self.base + 6)),
                  mock.patch.dict(os.environ, {"HTTP_PROXY": "http://10.255.255.1:9", "http_proxy": "http://10.255.255.1:9",
                                               "NO_PROXY": "", "no_proxy": ""})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("FUTSAL_PORT", None)

    def serve(self, port, handler=None):
        """handler 가 없으면 '그 포트에서 켜진 이 앱' (같은 프로세스라 PORT 가 하나뿐 → 주소 확인만 풀어 줌)."""
        if handler is None:
            class handler(app.Handler):
                def _host_ok(self):
                    return True
        s = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.server_close)
        self.addCleanup(s.shutdown)
        return s

    def test_no_reuseaddr_on_windows(self):
        """Windows 의 SO_REUSEADDR 는 듣고 있는 포트도 같이 잡게 함 → 앱이 둘 뜸. Windows 에서는 끔."""
        self.assertEqual(app._Server.allow_reuse_address, sys.platform != "win32")

    def test_foreign_program_on_port_falls_back(self):
        """8765 를 다른 프로그램(AnkiConnect 등)이 씀 → 그 프로그램에 창 띄우기를 보내지 않고 다음 포트로 · 번호는 .port 에."""
        class Anki(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"AnkiConnect v.6")

            do_POST = do_GET
        self.serve(self.base, Anki)
        self.assertFalse(app._ours(self.base))
        self.assertFalse(app._focus_running())
        srv = app._bind()
        self.addCleanup(srv.server_close)
        self.assertEqual(app.PORT, self.base + 1)
        self.assertEqual((core.WORK / ".port").read_text(encoding="utf-8"), str(self.base + 1))

    def test_reserved_port_falls_back(self):
        """Windows 가 예약한 포트(Hyper-V·WSL: WinError 10013) → 10초 기다리지 않고 바로 다음 포트."""
        real = app._Server

        def make(addr, h):
            if addr[1] == self.base:
                e = PermissionError(errno.EACCES, "액세스 권한에 의해 숨겨진 소켓에 액세스를 시도했습니다")
                e.winerror = 10013
                raise e
            return real(addr, h)
        t0 = time.monotonic()
        with mock.patch.object(app, "_Server", side_effect=make):
            srv = app._bind()
        self.addCleanup(srv.server_close)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(app.PORT, self.base + 1)

    def test_our_app_running_is_focused_through_proxy_setting(self):
        """이미 켜진 이 앱(.port 의 다른 포트): 두 번째 실행은 서버를 또 띄우지 않고 그 창을 앞으로 · PC 에 프록시가 있어도."""
        other = self.base + 2
        (core.WORK / ".port").write_text(str(other), encoding="utf-8")
        self.serve(other)
        with mock.patch.object(app, "_BROWSER", [True]), mock.patch.object(app.webbrowser, "open") as wb:
            self.assertTrue(app._focus_running(), "브라우저로 쓰는 중이면 화면을 한 번 더 열고 ok")
            wb.assert_called_once()
        with mock.patch.object(app, "PORT", other):
            self.assertIsNone(app._bind(), "이 앱이 듣고 있으면 서버를 또 띄우지 않음")

    def test_updater_sees_app_on_saved_port(self):
        d = self.tmp / "앱"
        d.mkdir()
        (d / "config.json").write_text(json.dumps({"workspace": str(core.WORK)}), encoding="utf-8")
        other = self.base + 3
        (core.WORK / ".port").write_text(str(other), encoding="utf-8")
        with mock.patch.dict(os.environ, {"FUTSAL_PORT": str(self.base)}):
            self.assertEqual(updater._app_ports(d), [self.base], "포트를 정해 켠 시험 앱은 그 포트만")
        (core.WORK / ".port").write_text("8767", encoding="utf-8")
        self.assertEqual(updater._app_ports(d), [8765, 8767], "8765 를 못 써서 다른 포트로 켠 앱도 찾음")
        with mock.patch.object(updater, "_app_ports", return_value=[self.base, other]):
            self.assertFalse(updater._app_running(d))
            self.serve(other)
            self.assertTrue(updater._app_running(d))


# ---------- 10. 절전 · 창 닫기 · pythonw 오류 기록 · 창 저장소 ----------

class Session(Work):
    def test_every_job_keeps_pc_awake(self):
        calls = []
        with mock.patch.object(core, "_keep_awake", side_effect=lambda on, prev=None: calls.append(on) or (7 if on else None)):
            self.assertTrue(app.start_job("내보내기", lambda: calls.append("일")))
            end = time.time() + 10
            while app.JOB["name"] and time.time() < end:
                time.sleep(0.02)
        self.assertEqual(calls, [True, "일", False])

    def test_close_during_job_asks(self):
        class Win:
            def __init__(self, answer):
                self.answer, self.destroyed, self.asked = answer, False, []
                self.answered = threading.Event()

            def evaluate_js(self, js, callback=None):
                """pywebview 처럼: callback 은 값이 Promise 일 때만 부름 (바로 값이면 그 값을 돌려주기만 함)."""
                self.asked.append(js)
                value = self.answer if "confirm(" in js else True
                if callback and js.startswith("Promise.resolve("):
                    def later():
                        callback(value)
                        if "confirm(" in js:
                            self.answered.set()
                    threading.Thread(target=later, daemon=True).start()
                    return "true"
                return value

            def destroy(self):
                self.destroyed = True
        with mock.patch.dict(app.JOB, {"name": "편집점 찾기"}), mock.patch.object(editor, "wait_saves"):
            for answer, closed in ((False, False), (True, True)):
                w = Win(answer)
                h = app._on_closing(w)
                self.assertFalse(h())
                self.assertTrue(w.answered.wait(10), "확인 창의 대답이 돌아와야 함 (Promise 로 감싸지 않으면 callback 이 안 불림)")
                end = time.time() + 10
                while not w.destroyed and len(w.asked) < (2 if closed else 1) and time.time() < end:
                    time.sleep(0.02)
                time.sleep(0.3)
                self.assertEqual(w.destroyed, closed, answer)
                self.assertIn("편집점 찾기", w.asked[0])
                if not closed:
                    self.assertFalse(h(), "닫지 않기로 했으면 다음 닫기에서 다시 물음")

    @unittest.skipUnless(node(), "node 가 없음")
    def test_close_confirm_through_real_pywebview_wrapper(self):
        """pywebview 의 진짜 evaluate_js 가 만드는 글(Promise 일 때만 callback)을 node 로 돌려 봄: 확인·취소 모두 몇 초 안에 대답이 옴
        (예전 'confirm(...)' 은 callback 이 안 불려 600초 기다렸고, 그사이 두 번째 ✕ 는 묻지도·저장하지도 않고 닫았음)."""
        try:
            import webview.window as ww
        except ImportError:
            self.skipTest("pywebview 가 없음")
        raw = getattr(ww.Window.evaluate_js, "__wrapped__", None)
        if raw is None:
            self.skipTest("이 pywebview 는 evaluate_js 를 풀 수 없음")
        prelude = ("const ANSWER = %s; globalThis.confirm = () => ANSWER; globalThis.window = globalThis; const out = [];"
                   "globalThis.pywebview = {_isPromise: o => !!o && typeof o.then === 'function', stringify: v => JSON.stringify(v),"
                   " _asyncCallback: (r, id) => out.push([r, id])};"
                   "const r = eval(%s); setTimeout(() => console.log(JSON.stringify({r: r === undefined ? null : r, out})), 20);")

        class Gui:
            renderer = "edgechromium"

            def __init__(self, win, answer):
                self.win, self.answer = win, answer

            def evaluate_js(self, script, uid, parse):
                r = subprocess.run(["node", "-e", prelude % (json.dumps(self.answer), json.dumps(script))],
                                   capture_output=True, text=True, timeout=60)
                got = json.loads(r.stdout.strip().splitlines()[-1])
                for res, cid in got["out"]:  # js_bridge → window._callbacks[id](값)
                    cb = self.win._callbacks.pop(cid, None)
                    if cb:
                        threading.Thread(target=cb, args=(json.loads(res),), daemon=True).start()
                return got["r"]

        class Win:
            def __init__(self, answer):
                self._callbacks, self.uid = {}, "w"
                self.gui = Gui(self, answer)

            def evaluate_js(self, script, callback=None):
                return raw(self, script, callback)
        with mock.patch.dict(app.JOB, {"name": "작은 미리보기 만들기"}):
            for answer in (True, False):
                got = {}
                th = threading.Thread(target=lambda: got.setdefault("r", app._confirm_close(Win(answer))), daemon=True)
                th.start()
                th.join(60)
                self.assertFalse(th.is_alive(), "대답을 600초까지 기다리지 않음")
                self.assertIs(got.get("r"), answer)

    def test_pythonw_errors_go_to_file(self):
        old = sys.stderr
        try:
            with mock.patch.object(sys, "stderr", None):
                p = app._error_log()
                try:
                    raise ValueError(f"윈도우에서만 나는 오류 {JAMO}")
                except ValueError:
                    traceback.print_exc()
                t = threading.Thread(target=lambda: 1 / 0)
                t.start()
                t.join()
                sys.stderr.flush()
                err = sys.stderr
        finally:
            import faulthandler
            faulthandler.disable()
            sys.stderr = old
        text = p.read_text(encoding="utf-8")
        self.assertIn("ValueError: 윈도우에서만 나는 오류 정형ᄃ", text)
        self.assertIn("ZeroDivisionError", text)
        self.assertRegex(text, r"\d\d-\d\d \d\d:\d\d:\d\d Traceback")
        err.f.close()

    def test_start_failure_traceback_saved(self):
        """실행기에서 켜다 멈춘 오류: studio.log 에는 한 줄, studio-error.log 에는 traceback (pythonw 는 화면이 없음)."""
        d = self.tmp / "앱"
        d.mkdir()
        (d / "config.json").write_text(json.dumps({"workspace": str(self.work)}), encoding="utf-8")
        (d / "version.txt").write_text("1.0\n", encoding="utf-8")
        (d / "app.py").write_text("def f():\n    raise ImportError('DLL load failed while importing _ext: 지정된 모듈을 찾을 수 없습니다.')\nf()\n",
                                  encoding="utf-8")
        here = os.getcwd()
        try:
            with mock.patch.object(updater, "_alert"), mock.patch.object(updater.sys, "argv", ["updater.py"]):
                self.assertEqual(updater.run_app(d), 1)
        finally:
            os.chdir(here)
        self.assertIn("ImportError", (self.work / "studio.log").read_text(encoding="utf-8"))
        tb = (self.work / "studio-error.log").read_text(encoding="utf-8")
        self.assertIn("Traceback", tb)
        self.assertIn("app.py", tb)

    def test_vc_runtime_missing_message(self):
        e1 = ImportError("DLL load failed while importing onnxruntime_pybind11_state: 지정된 모듈을 찾을 수 없습니다.")
        e2 = FileNotFoundError("Could not find module 'C:\\v\\ctranslate2\\ctranslate2.dll' (or one of its dependencies).")
        self.assertTrue(core.dll_missing(e1) and core.dll_missing(e2))
        self.assertFalse(core.dll_missing(ImportError("No module named 'faster_whisper'")))
        def boom(name):
            raise e2
        with mock.patch.dict(sys.modules, {"faster_whisper": None}), mock.patch("builtins.__import__", side_effect=lambda n, *a, **k: boom(n) if n == "faster_whisper" else real_import(n, *a, **k)):
            with self.assertRaises(RuntimeError) as cm:
                core._whisper("tiny")
        self.assertIn("Visual C++", str(cm.exception))
        self.assertIn("시작하기 (Windows).bat", str(cm.exception))

    def test_webview_keeps_storage(self):
        seen = []

        class WV:
            @staticmethod
            def start(**kw):
                seen.append(kw)
        app._start_webview(WV)
        self.assertEqual(seen[0]["private_mode"], False)

        class Old:
            @staticmethod
            def start(*a, **kw):
                if kw:
                    raise TypeError("unexpected keyword")
                seen.append("old")
        app._start_webview(Old)
        self.assertEqual(seen[-1], "old")


# ---------- 11. 업데이트: 켜진 앱이 쓰는 구성요소 · 겹친 실행기 ----------

class Updates(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="업데이트 시험 "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = self.tmp / "앱"
        rb = self.app / updater.ROLLBACK / "v1.0"
        (rb / "files").mkdir(parents=True)
        (self.app / "config.json").write_text(json.dumps({"workspace": str(self.tmp / "작업")}), encoding="utf-8")
        (self.app / "version.txt").write_text("1.1\n", encoding="utf-8")
        (self.app / "requirements.txt").write_text("numpy\n", encoding="utf-8")
        for k in range(30):
            (self.app / f"f{k}.py").write_text("new\n", encoding="utf-8")
            (rb / "files" / f"f{k}.py").write_text("old\n", encoding="utf-8")
        (rb / "files" / "version.txt").write_text("1.0\n", encoding="utf-8")
        updater._write_json(rb / "rollback.json", {"from": "1.0", "to": "1.1", "backup": ["version.txt"] + [f"f{k}.py" for k in range(30)],
                                                   "added": []})
        p = mock.patch.dict(os.environ, {"FUTSAL_RESTART": "1"})
        p.start()
        self.addCleanup(p.stop)

    def pending(self, state="installed"):
        updater._write_json(self.app / updater.PENDING, {"from": "1.0", "to": "1.1", "state": state, "rollback": "v1.0"})

    def test_deferred_requirements_installed_before_import(self):
        self.pending()
        updater._write_json(self.app / updater.REQ_PENDING, {"to": "1.1"})
        runs = []

        def fake_run(cmd, **kw):
            runs.append((cmd, kw.get("env", {}).get("PYTHONIOENCODING")))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(updater.subprocess, "run", side_effect=fake_run), mock.patch.object(updater, "_import_check", return_value=(True, "")):
            self.assertEqual(updater.check(self.app, log=lambda m: None), "ok")
        self.assertEqual(runs[0][0][1:4], ["-m", "pip", "install"])
        self.assertEqual(runs[0][1], "utf-8")
        self.assertFalse((self.app / updater.REQ_PENDING).exists())
        self.assertEqual((self.app / updater.REQ_HASH).read_text(encoding="utf-8"), updater.sha256(self.app / "requirements.txt"))

    def test_deferred_requirements_failure_rolls_back(self):
        self.pending()
        updater._write_json(self.app / updater.REQ_PENDING, {"to": "1.1"})
        bad = subprocess.CompletedProcess([], 1, "", "ERROR: Could not install packages due to an OSError: [WinError 5] 액세스가 거부되었습니다")
        with mock.patch.object(updater.subprocess, "run", return_value=bad):
            self.assertEqual(updater.check(self.app, log=lambda m: None), "rolled_back")
        self.assertEqual((self.app / "version.txt").read_text(encoding="utf-8"), "1.0\n")
        res = json.loads((self.app / updater.RESULT).read_text(encoding="utf-8"))
        self.assertIn("액세스가 거부", res["reason"])
        self.assertFalse(res["broken"])

    def test_two_launchers_roll_back_once(self):
        """아이콘을 거의 동시에 두 번: 예전에는 같은 임시 파일 이름·이미 지운 표시 때문에 한쪽이 '되돌리지 못했어요' 알림."""
        self.pending("applying")
        out, logs = [], []
        slow = updater.rollback

        def rollback(*a, **k):
            time.sleep(0.3)
            return slow(*a, **k)
        with mock.patch.object(updater, "rollback", side_effect=rollback), mock.patch.object(updater, "_alert") as alert:
            ts = [threading.Thread(target=lambda: out.append(updater.check(self.app, log=logs.append))) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(30)
        self.assertEqual(sorted(out), ["none", "rolled_back"], logs)
        alert.assert_not_called()
        self.assertEqual((self.app / "f3.py").read_text(encoding="utf-8"), "old\n")
        self.assertFalse((self.app / updater.LAUNCH_LOCK).exists())

    def test_stale_lock_is_taken(self):
        f = self.app / updater.LAUNCH_LOCK
        f.write_text("123", encoding="utf-8")
        old = time.time() - 120
        os.utime(f, (old, old))
        with updater._launch_lock(self.app) as got:
            self.assertTrue(got)

    def test_install_with_leftover_staging(self):
        """지난번 정리를 다 못 한 .update_staging(잠긴 파일)이 남아 있어도 설치가 WinError 183 으로 멈추지 않음."""
        (self.app / updater.STAGING).mkdir()
        with mock.patch.object(updater, "_rmtree"):
            (self.app / updater.STAGING).mkdir(parents=True, exist_ok=True)  # 고치기 전에는 여기서 FileExistsError
        self.assertIn("exist_ok=True", Path(updater.__file__).read_text(encoding="utf-8").split("staging = app_dir / STAGING")[1][:200])

    def test_update_app_defers_pip_when_files_in_use(self):
        """켜진 앱이 불러 둔 .pyd 를 pip 가 못 바꿈(WinError 5): 되돌리지 않고 다음 실행 때 실행기가 먼저 설치하게 표시."""
        logs = []
        with mock.patch.object(core, "APP_DIR", self.app), mock.patch.object(core, "CONFIG", {"update_manifest_url": "http://x/m.json"}), \
                mock.patch.object(core, "VERSION", "1.0"), \
                mock.patch.object(updater, "fetch_manifest", return_value=({"version": "1.1"}, b"")), \
                mock.patch.object(updater, "skipped", return_value=""), \
                mock.patch.object(updater, "download_and_install", return_value={"req_changed": True}), \
                mock.patch.object(core, "_pip_self_upgrade"), mock.patch.object(core, "_pip_locked", return_value=True), \
                mock.patch.object(core, "run", return_value=subprocess.CompletedProcess([], 1, "", "[WinError 5] 액세스가 거부되었습니다")), \
                mock.patch.object(updater, "rollback") as rb:
            self.assertTrue(core.update_app(logs.append))
        rb.assert_not_called()
        self.assertTrue((self.app / updater.REQ_PENDING).exists())

    def test_pip_advice(self):
        self.assertIn("Python", core._pip_advice("ERROR: Package 'yt-dlp' requires a different Python: 3.10.11 not in '>=3.11'"))
        self.assertIn("저장 공간", core._pip_advice("OSError: [Errno 28] No space left on device"))
        self.assertIn("인터넷", core._pip_advice("ConnectionResetError(10054, ...)"))
        self.assertEqual(core._tail("a\nERROR: x\nCheck the permissions.\n", 300, 2), "ERROR: x / Check the permissions.")

    def test_engine_stuck_on_old_python(self):
        info = json.dumps({"info": {"version": "2026.11.1", "requires_python": ">=3.11"}})
        with mock.patch.object(core, "_fetch_text", return_value=info), mock.patch.object(core.sys, "version_info", (3, 10, 11)), \
                mock.patch.object(core.time, "strftime", return_value="2026-10"):
            msg = core._engine_needs_newer_python("2026.8.19")
        self.assertIn("3.11", msg)
        self.assertIn("시작하기 (Windows).bat", msg)
        with mock.patch.object(core, "_fetch_text", return_value=info), mock.patch.object(core.sys, "version_info", (3, 13, 1)):
            self.assertIsNone(core._engine_needs_newer_python("2026.11.1"))

    def test_tls_strict_flag_off(self):
        ctx = updater._ssl_context()
        self.assertFalse(ctx.verify_flags & getattr(ssl, "VERIFY_X509_STRICT", 0))
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)


# ---------- 12. 설치 확인 (setup_check · bat) ----------

class Install(unittest.TestCase):
    def test_python_range_and_arch(self):
        self.assertIsNone(setup_check._ok_python((3, 13), "win-amd64"))
        self.assertIsNotNone(setup_check._ok_python((3, 15), "win-amd64"), "휠이 아직 없는 3.15")
        self.assertIsNotNone(setup_check._ok_python((3, 9), "win-amd64"))
        with mock.patch.object(setup_check.sys, "platform", "win32"):
            self.assertIn("64비트", setup_check._ok_python((3, 13), "win-arm64"))
            self.assertIn("64비트", setup_check._ok_python((3, 13), "win32"))

    def test_broken_or_old_venv_rebuilt(self):
        tmp = Path(tempfile.mkdtemp(prefix="venv 시험 "))
        self.addCleanup(shutil.rmtree, tmp, True)
        exe = tmp / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        exe.parent.mkdir(parents=True)
        with mock.patch.object(setup_check, "APP_DIR", tmp):
            exe.write_text("#!/bin/sh\nexit 103\n", encoding="utf-8")  # 'No Python at …' (바탕 Python 을 지움)
            exe.chmod(0o755)
            self.assertEqual(setup_check.check_venv(), 1)
            for ver, base, want in (((3, 12), (3, 13), 0), ((3, 10), (3, 13), 1), ((3, 10), (3, 10), 0), ((3, 15), (3, 13), 1)):
                with mock.patch.object(setup_check, "_venv_info", return_value=(ver, "win-amd64")), \
                        mock.patch.object(setup_check.sys, "version_info", base + (0,)):
                    self.assertEqual(setup_check.check_venv(), want, (ver, base))

    def test_long_path(self):
        with mock.patch.object(setup_check, "APP_DIR", Path("C:/" + "가" * 140)), mock.patch.object(setup_check, "_long_paths_on", return_value=False):
            self.assertEqual(setup_check.check_longpath(), 1)
        with mock.patch.object(setup_check, "APP_DIR", Path("C:/Users/홍길동/Downloads/futsal-studio-main/futsal-studio-main")):
            self.assertEqual(setup_check.check_longpath(), 0)

    def test_too_new_python_message(self):
        """3.15(휠 없음): '너무 새로 나와서' + 3.14 를 함께 설치 (예전: '쓸 수 없어요'만 · 2026-10 3.15.0rc3)."""
        why = setup_check._ok_python((3, 15), "win-amd64")
        self.assertIn("너무 새로", why)
        self.assertIn("3.14", why)
        self.assertIn("너무 오래", setup_check._ok_python((3, 9), "win-amd64"))
        with mock.patch.object(setup_check.sys, "version_info", (3, 15, 0, "final", 0)), mock.patch.object(setup_check, "say") as say:
            self.assertEqual(setup_check.check_python(), 1)
        self.assertIn("3.15", say.call_args[0][0])

    def test_place_temp_or_zip(self):
        """압축 파일 안에서 바로 실행(탐색기가 %TEMP%\\Temp1_x.zip 에 풂)·압축 프로그램이 잠깐 푼 곳 → 멈추고 '압축 풀기' 안내."""
        tmp = Path(tempfile.mkdtemp(prefix="임시 "))
        self.addCleanup(shutil.rmtree, tmp, True)
        cases = ((tmp / "Temp1_futsal-studio-main.zip" / "futsal-studio-main", 1),  # %TEMP% 아래
                 (tmp / "Rar$EXa1234.5678" / "futsal", 1), (tmp / "7zO8A1B" / "futsal", 1))
        with mock.patch.dict(os.environ, {"TEMP": str(tmp), "TMP": str(tmp)}):
            for d, want in cases:
                with mock.patch.object(setup_check, "APP_DIR", d), mock.patch.object(setup_check, "say"):
                    self.assertEqual(setup_check.check_place(), want, d)
        with mock.patch.dict(os.environ, {"TEMP": str(tmp / "없음"), "TMP": ""}):
            for d, want in ((Path("/home/홍길동/Downloads/Temp1_futsal.zip/futsal"), 1), (Path("/home/홍길동/temp1_backup/futsal"), 0),
                            (Path("/home/홍길동/Downloads/futsal-studio-main/futsal-studio-main"), 0), (Path("/풋살스튜디오"), 0)):
                with mock.patch.object(setup_check, "APP_DIR", d), mock.patch.object(setup_check, "say"):
                    self.assertEqual(setup_check.check_place(), want, d)

    def test_place_and_pip_as_subprocess(self):
        """bat 과 같은 부름: 임시 폴더에 복사한 setup_check.py → place 1 · 보통 폴더 → 0 · pip <기록> → 한국어 이유 한 줄 (늘 1)."""
        tmp = Path(tempfile.mkdtemp(prefix="설치 시험 "))
        self.addCleanup(shutil.rmtree, tmp, True)
        app = tmp / "Temp2_futsal-studio-main.zip" / "futsal-studio-main"
        app.mkdir(parents=True)
        shutil.copy(REPO / "setup_check.py", app)
        env = dict(os.environ, TEMP=str(tmp), TMP=str(tmp), PYTHONIOENCODING="utf-8")

        def run(*args, cwd=app, env=env):
            return subprocess.run([sys.executable, "-I", "setup_check.py", *args], cwd=cwd, env=env, capture_output=True,
                                  text=True, encoding="utf-8", timeout=60)
        r = run("place")
        self.assertEqual(r.returncode, 1)
        self.assertIn("압축 풀기", r.stdout)
        ok = tmp / "풋살스튜디오"
        ok.mkdir()
        shutil.copy(REPO / "setup_check.py", ok)
        self.assertEqual(run("place", cwd=ok, env=dict(env, TEMP=str(tmp / "t"), TMP=str(tmp / "t"))).returncode, 0)
        log = tmp / "pip.log"
        log.write_text("ERROR: Could not find a version that satisfies the requirement onnxruntime (from versions: none)\n"
                       "ERROR: No matching distribution found for onnxruntime\n", encoding="utf-8")
        r = run("pip", str(log), cwd=ok)
        self.assertEqual(r.returncode, 1)
        self.assertIn("3.14", r.stdout.splitlines()[-1])
        self.assertIn("[안내]", r.stdout)
        self.assertEqual(run("pip", str(tmp / "없는 기록.log"), cwd=ok).returncode, 1)

    def test_pip_reason(self):
        net = ("WARNING: Retrying (Retry(total=4)) after connection broken by 'NewConnectionError(... getaddrinfo failed)'\n"
               "ERROR: Could not find a version that satisfies the requirement faster-whisper\nERROR: No matching distribution found for faster-whisper\n")
        cases = ((net, "인터넷이 끊겼"),  # 인터넷이 끊겨도 'No matching distribution' 이 나옴 → 버전 탓으로 잘못 안내하지 않음
                 ("ERROR: No matching distribution found for ctranslate2==4.8.2", "Python 3.14"),
                 ("ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device", "저장 공간"),
                 ("ERROR: Could not install packages due to an OSError: [WinError 5] Access is denied: 'C:\\a\\.venv\\x.pyd'", "사용 중"),
                 ("SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed'))", "보안"),
                 ("ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'", "압축"),
                 ("알 수 없는 오류", "인터넷 연결을 확인"))
        for text, want in cases:
            why, _ = setup_check.pip_reason(text, (3, 15))
            self.assertIn(want, why, text)
        why, err = setup_check.pip_reason(cases[1][0], (3, 15))
        self.assertIn("3.15", why)
        self.assertIn(setup_check.PY_DIRECT, why)
        self.assertTrue(err.startswith("ERROR:"))

    def test_bat_is_safe_cmd(self):
        """bat: CRLF · 괄호 블록 안에 %~dp0(폴더 이름의 ')' 로 블록이 깨짐)·괄호 든 echo 없음 · 'py -3' 대신 버전 고르기."""
        raw = (REPO / "시작하기 (Windows).bat").read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "줄 끝은 CRLF")
        depth = 0
        for ln in raw.decode("utf-8").splitlines():
            s = ln.strip()
            if depth > 0:
                self.assertNotIn("%~dp0", s, ln)
                if s.lower().startswith("echo"):
                    self.assertNotRegex(s[4:], r"[()]", ln)
            depth += s.count("(") - s.count(")") if not s.lower().startswith("echo") else 0
            self.assertGreaterEqual(depth, 0, ln)
        self.assertEqual(depth, 0)
        text = raw.decode("utf-8")
        self.assertNotIn("set PY=py -3)", text)
        self.assertIn("setup_check.py venv", text)
        self.assertIn("setup_check.py vcredist", text)
        lines = [ln.strip() for ln in text.splitlines()]
        first_py = next(i for i, ln in enumerate(lines) if "setup_check.py" in ln and not ln.startswith("if not exist"))
        for f in ("app.py", "requirements.txt", "setup_check.py"):  # 압축 안에서 바로 실행: Python 을 찾기 전에 먼저 확인
            self.assertLess(lines.index('if not exist "%s" goto :not_extracted' % f), first_py)
        self.assertLess(lines.index('set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"'), lines.index("call :pick_py"))  # 고르는 동안 몰래 설치 안 함
        self.assertIn("pymanager install -y 3.14", text)
        self.assertIn("%PY% setup_check.py place || goto :stop", text)
        self.assertLess(lines.index("%PY% setup_check.py place || goto :stop"), lines.index("%PY% -m venv .venv || goto :fail"))
        self.assertIn('> "%PIPLOG%" 2>&1 || goto :pip_fail', text)
        self.assertIn('setup_check.py pip "%PIPLOG%"', text)
        self.assertIn(setup_check.PY_DIRECT, text)  # 맞는 Python 이 없으면 3.14 독립 설치 파일을 바로 받음
        for label in ("not_extracted", "pip_fail", "stop", "pymanager_install", "pick_mgr", "pick_path", "no_python"):
            self.assertIn(":" + label, lines)


# ---------- 13. 바로가기 · claude 찾기 · NAS 경로 ----------

class Shell(unittest.TestCase):
    def test_shortcut_written_once_then_kept(self):
        """같은 실행 경로면 다시 만들지 않음 (지운 아이콘이 되살아나지 않고 숨은 PowerShell 을 켤 때마다 띄우지 않음)."""
        tmp = Path(tempfile.mkdtemp(prefix="바로가기 "))
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(winlink.sys, "platform", "win32"), mock.patch.object(winlink, "_com_all", return_value=True) as com, \
                mock.patch.object(winlink, "set_process_app_id", return_value=True) as aid, mock.patch.object(winlink, "_powershell") as ps:
            self.assertTrue(winlink.prepare(tmp / "앱", "C:/v/pythonw.exe", tmp))
            self.assertTrue(winlink.prepare(tmp / "앱", "C:/v/pythonw.exe", tmp))
            self.assertEqual(com.call_count, 1)
            self.assertEqual(aid.call_count, 2, "아이디는 켤 때마다 (바로가기와 짝)")
            winlink.prepare(tmp / "옮긴 앱", "C:/v/pythonw.exe", tmp)
            self.assertEqual(com.call_count, 2, "앱 폴더를 옮기면 다시")
            ps.assert_not_called()
        with mock.patch.object(winlink.sys, "platform", "win32"), mock.patch.object(winlink, "_com_all", return_value=False), \
                mock.patch.object(winlink, "set_process_app_id") as aid, mock.patch.object(winlink, "_powershell", return_value=True):
            self.assertFalse(winlink.prepare(tmp / "새 앱", "C:/v/pythonw.exe", tmp), "PowerShell 바로가기엔 아이디가 없으니 프로세스에도 안 줌")
            aid.assert_not_called()
        self.assertEqual(ctypes_size(winlink.PropVariant), 24 if sys.maxsize > 2 ** 32 else 16)

    def test_claude_which_skips_extensionless_npm_shim(self):
        def which(n):
            return {"claude": r"C:\Users\a\AppData\Roaming\npm\claude", "claude.cmd": r"C:\Users\a\AppData\Roaming\npm\claude.CMD"}.get(n)
        with mock.patch.object(claude_cli, "WIN", True), mock.patch.object(claude_cli.shutil, "which", side_effect=which), \
                mock.patch.dict(os.environ, {"PATHEXT": ".COM;.EXE;.BAT;.CMD", "FUTSAL_CLAUDE": ""}):
            os.environ.pop(claude_cli.ENV_EXE, None)
            with mock.patch.object(claude_cli.os, "pathsep", ";"):
                self.assertTrue(claude_cli.find_exe().endswith("claude.CMD"))

    def test_premiere_pathurl_unc_and_drive(self):
        self.assertEqual(editor._pathurl(PureWindowsPath(r"\\NAS\share\풋살\videos\x.mp4")),
                         "file://NAS/share/%ED%92%8B%EC%82%B4/videos/x.mp4")
        self.assertEqual(editor._pathurl(PureWindowsPath(r"C:\Users\홍길동\videos\x.mp4")),
                         "file://localhost/C%3A/Users/%ED%99%8D%EA%B8%B8%EB%8F%99/videos/x.mp4")


real_import = __import__


def ctypes_size(t):
    import ctypes
    return ctypes.sizeof(t)


if __name__ == "__main__":
    unittest.main()
