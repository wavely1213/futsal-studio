"""풋살사관학교 스튜디오 — 데스크톱 앱 (화면은 전용 창, 내부 통신은 127.0.0.1 전용)."""
import base64
import json
import os
import time
import subprocess
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import core
import editor

PORT = int(os.environ.get("FUTSAL_PORT", "8765"))
LOG, JOB = [], {"name": None, "result": None, "error": None}
LOCK = threading.Lock()


LOGFILE = core.WORK / "studio.log"


def log(msg):
    with LOCK:
        LOG.append(msg)
    print(msg, flush=True)
    try:  # 콘솔 없이 실행되므로 파일에도 남김
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def start_job(name, fn):
    with LOCK:
        if JOB["name"]:
            return False
        JOB.update(name=name, result=None, error=None)

    def runner():
        core.set_progress()
        try:
            JOB["result"] = fn()
        except Exception as e:
            JOB["error"] = str(e)
            log(f"문제가 생겼어요 · {e}")
            traceback.print_exc()
        finally:
            core.set_progress()
            JOB["name"] = None

    threading.Thread(target=runner, daemon=True).start()
    return True


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def restart():
    """새 프로세스로 앱을 다시 띄우고 지금 프로세스는 종료 (업데이트 후)."""
    log("다시 시작하는 중…")
    kw = {"cwd": str(core.APP_DIR)}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([_gui_python(), str(Path(__file__).resolve())], **kw)
    os._exit(0)


def _gui_python():
    """Windows 에서는 콘솔 창이 안 뜨는 pythonw.exe 사용."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        return str(exe.parent / "pythonw.exe")
    return str(exe)


APP_NAME = "풋살사관학교 스튜디오"


def ensure_shortcut():
    """바탕화면·시작 메뉴(Windows) 또는 응용 프로그램(Mac)에 아이콘을 만든다. 이미 있으면 건너뜀."""
    try:
        if sys.platform == "win32":
            ps = f"""
$w = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('Programs')) ''))) {{
  $p = Join-Path $dir '{APP_NAME}.lnk'
  $s = $w.CreateShortcut($p)
  $s.TargetPath = '{_gui_python()}'
  $s.Arguments = '"{Path(__file__).resolve()}"'
  $s.WorkingDirectory = '{core.APP_DIR}'
  $s.IconLocation = '{core.APP_DIR / "icon.ico"}'
  $s.Save()
}}"""
            enc = base64.b64encode(ps.encode("utf-16-le")).decode()
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                           capture_output=True, creationflags=0x08000000)  # CREATE_NO_WINDOW
        elif sys.platform == "darwin":
            app = Path.home() / "Applications" / f"{APP_NAME}.app"
            macos = app / "Contents" / "MacOS"
            res = app / "Contents" / "Resources"
            macos.mkdir(parents=True, exist_ok=True)
            res.mkdir(parents=True, exist_ok=True)
            launcher = macos / "launcher"
            launcher.write_text(f'#!/bin/bash\ncd "{core.APP_DIR}"\nexec "{sys.executable}" "{Path(__file__).resolve()}"\n', encoding="utf-8")
            launcher.chmod(0o755)
            (app / "Contents" / "Info.plist").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>{APP_NAME}</string>
<key>CFBundleDisplayName</key><string>{APP_NAME}</string>
<key>CFBundleIdentifier</key><string>kr.futsalacademy.studio</string>
<key>CFBundleExecutable</key><string>launcher</string>
<key>CFBundleIconFile</key><string>icon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>{core.VERSION}</string>
</dict></plist>""", encoding="utf-8")
            if not (res / "icon.icns").exists():
                iconset = res / "icon.iconset"
                iconset.mkdir(exist_ok=True)
                for sz in (16, 32, 128, 256, 512):
                    for scale, suffix in ((1, ""), (2, "@2x")):
                        subprocess.run(["sips", "-z", str(sz * scale), str(sz * scale), str(core.APP_DIR / "icon.png"),
                                        "--out", str(iconset / f"icon_{sz}x{sz}{suffix}.png")], capture_output=True)
                subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(res / "icon.icns")], capture_output=True)
                subprocess.run(["rm", "-rf", str(iconset)])
    except Exception as e:
        log(f"바로가기를 만들지 못했어요 · {e}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, path, ctype):
        """영상은 구간 요청(Range)을 지원해야 미리보기에서 앞뒤로 이동 가능."""
        size = path.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if a and b else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            return self._send(200, (core.APP_DIR / "ui.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/editor":
            return self._send(200, (core.APP_DIR / "editor.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/media":
            p = (core.VIDEOS / q["name"][0]).resolve()
            if core.VIDEOS.resolve() not in p.parents or not p.exists():
                return self._send(404, {"error": "not found"})
            return self._file(p, "video/mp4")
        if u.path.startswith("/fonts/"):
            p = (editor.FONTS / Path(u.path).name).resolve()
            return self._file(p, "font/otf") if p.exists() else self._send(404, {"error": "not found"})
        if u.path == "/thumbs.jpg":
            p = core.adir(q["name"][0]) / "thumbs2.jpg"
            return self._file(p, "image/jpeg") if p.exists() else self._send(404, {"error": "not found"})
        if u.path == "/api/edit/autoseq":
            n = q["name"][0]
            return self._send(200, {"sequences": editor.auto_sequences(n, editor.media_info(n))})
        if u.path == "/api/edit/open":
            n = q["name"][0]
            try:
                return self._send(200, {"project": editor.load_project(n), "waveform": editor.waveform(n),
                                        "thumbs": editor.thumbs(n), "events": core.timeline_events(n),
                                        "recommend": editor.recommend(n)})
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": f"편집실을 열지 못했어요 · {e}"})
        if u.path == "/api/state":
            since = int(q.get("since", ["0"])[0])
            with LOCK:
                lines = LOG[since:]
                total = len(LOG)
            return self._send(200, {"version": core.VERSION, "workspace": str(core.WORK), "job": JOB["name"],
                                    "result": JOB["result"] if not JOB["name"] else None,
                                    "error": JOB["error"] if not JOB["name"] else None,
                                    "log": lines, "log_total": total, "progress": dict(core.PROGRESS), "local": core.local_videos()})
        if u.path == "/api/timeline":
            n = q["name"][0]
            return self._send(200, {"text": core.timeline(n), "events": core.timeline_events(n)})
        if u.path == "/api/update/check":
            try:
                return self._send(200, core.check_update())
            except Exception as e:
                return self._send(200, {"current": core.VERSION, "available": False, "note": f"확인 실패: {e}"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # 다른 사이트가 이 로컬 서버를 조작하지 못하게: 같은 출처 요청만 허용
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self._send(403, {"error": "forbidden"})
        b, path = self._body(), urlparse(self.path).path
        ck = b.get("cookies") or None
        jobs = {
            "/api/list": ("채널 불러오기", lambda: core.list_videos(b.get("kind", "videos"), ck)),
            "/api/download": ("보관함에 담기", lambda: core.download(b["ids"], log, ck)),
            "/api/analyze": ("편집점 찾기", lambda: self._analyze(b)),
            "/api/render": ("러프컷 만들기", lambda: str(core.render(b["name"], b["spec"], log))),
            "/api/update": ("업데이트", lambda: self._update(b)),
        }
        if path == "/api/edit/save":
            editor.save_project(b["name"], b["project"])
            return self._send(200, {"ok": True})
        if path == "/api/edit/export":
            ok = start_job("내보내기", lambda: editor.export(b["name"], b["project"], b.get("opts", {}), log))
            return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
        if path == "/api/open":
            try:
                open_folder({"videos": core.VIDEOS, "analysis": core.ANALYSIS, "out": core.OUT}[b["which"]])
            except Exception as e:
                log(f"폴더를 열지 못했어요 · {e}")
                return self._send(200, {"ok": False})
            return self._send(200, {"ok": True})
        if path == "/api/restart":
            self._send(200, {"ok": True})
            threading.Timer(0.5, restart).start()
            return
        if path in jobs:
            name, fn = jobs[path]
            ok = start_job(name, fn)
            return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
        self._send(404, {"error": "not found"})

    @staticmethod
    def _analyze(b):
        out = core.analyze_many(b["names"], log, b.get("model", "large-v3-turbo"))
        # 편집점 찾기 직후 1차 가편집(롱폼 정리본 + 쇼츠 편집본)까지 만들어 둠
        for n in b["names"]:
            core.set_progress(label="가편집 만드는 중", item=n, pct=99, detail="컷 정리·쇼츠 구간 고르는 중")
            p = editor._ppath(n)
            if p.exists():
                p.unlink()  # 새로 받아쓴 내용으로 다시 만듦
            proj = editor.load_project(n)
            editor.thumbs(n)
            editor.waveform(n)
            names = ", ".join(q["name"] for q in proj["sequences"])
            log(f"  가편집 완료 · {names}")
        return out

    @staticmethod
    def _update(b):
        if b.get("engine", True):
            core.update_engine(log)
        changed = core.update_app(log) if b.get("app", True) else False
        return {"restart": changed}


def _bind():
    # 업데이트 재시작 직후엔 이전 프로세스가 포트를 놓을 때까지 잠깐 기다림
    for _ in range(40):
        try:
            return ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        except OSError:
            time.sleep(0.25)
    return None


def main():
    url = f"http://127.0.0.1:{PORT}/"
    srv = _bind()
    if srv is None:  # 이미 실행 중 → 그 화면만 띄워줌
        webbrowser.open(url)
        return
    log(f"{APP_NAME} v{core.VERSION} 시작")
    log(f"작업 폴더 · {core.WORK}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if sys.platform in ("win32", "darwin"):
        threading.Thread(target=ensure_shortcut, daemon=True).start()
    if "--browser" not in sys.argv:
        try:
            import webview  # pywebview: 전용 앱 창
            webview.create_window(APP_NAME, url, width=1320, height=860, min_size=(960, 640))
            webview.start()
            os._exit(0)  # 창을 닫으면 종료
        except Exception as e:
            log(f"앱 창을 열지 못해 브라우저로 엽니다 · {e}")
    webbrowser.open(url)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
