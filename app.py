"""풋살사관학교 편집도우미 — 로컬 웹 클라이언트 (127.0.0.1 전용)."""
import json
import os
import subprocess
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import core

PORT = int(os.environ.get("FUTSAL_PORT", "8765"))
LOG, JOB = [], {"name": None, "result": None}
LOCK = threading.Lock()


def log(msg):
    with LOCK:
        LOG.append(msg)
    print(msg, flush=True)


def start_job(name, fn):
    with LOCK:
        if JOB["name"]:
            return False
        JOB.update(name=name, result=None)

    def runner():
        try:
            JOB["result"] = fn()
        except Exception as e:
            log(f"문제가 생겼어요 · {e}")
            traceback.print_exc()
        finally:
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
    log("다시 시작하는 중…")
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "--no-browser"])


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

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            return self._send(200, (core.APP_DIR / "ui.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/state":
            since = int(q.get("since", ["0"])[0])
            with LOCK:
                lines = LOG[since:]
                total = len(LOG)
            return self._send(200, {"version": core.VERSION, "workspace": str(core.WORK), "job": JOB["name"],
                                    "result": JOB["result"] if not JOB["name"] else None,
                                    "log": lines, "log_total": total, "local": core.local_videos()})
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
            "/api/analyze": ("편집점 찾기", lambda: [str(core.analyze(n, log, b.get("model", "large-v3-turbo"))) for n in b["names"]]),
            "/api/render": ("러프컷 만들기", lambda: str(core.render(b["name"], b["spec"], log))),
            "/api/update": ("업데이트", lambda: self._update(b)),
        }
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
    def _update(b):
        if b.get("engine", True):
            core.update_engine(log)
        changed = core.update_app(log) if b.get("app", True) else False
        return {"restart": changed}


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    log(f"풋살사관학교 스튜디오 v{core.VERSION} · {url}")
    log(f"작업 폴더 · {core.WORK}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
