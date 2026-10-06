"""풋살사관학교 스튜디오 — 데스크톱 앱 (화면은 전용 창, 내부 통신은 127.0.0.1 전용)."""
import base64
import json
import mimetypes
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

import bundle
import core
import editor
import hooks
import qa
import style
import thumb
import upload

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
        editor.CANCEL.clear()  # 예전 작업에서 누른 멈추기(✕)가 다음 작업에 남지 않게
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
    kw["env"] = dict(os.environ, FUTSAL_RESTART="1")  # 새 프로세스는 '이미 실행 중' 확인을 건너뜀
    args = ["--browser"] if "--browser" in sys.argv else []
    subprocess.Popen([_gui_python(), str(core.APP_DIR / "updater.py"), "--launch", *args], **kw)  # 새 버전이 열리는지 확인 후 실행
    _quit()


def _quit():
    """앱 끝내기: 저장 중인 프로젝트는 끝까지 쓰고, 남은 ffmpeg 는 끔 (Windows 는 자식 프로세스가 같이 안 꺼짐)."""
    editor.wait_saves(3)
    editor.cancel_export()
    os._exit(0)


def _gui_python():
    """Windows 에서는 콘솔 창이 안 뜨는 pythonw.exe 사용."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        return str(exe.parent / "pythonw.exe")
    return str(exe)


# ---------- 안전한 업데이트 · 다운로드 엔진 자동 관리 ----------

def _after_start():
    """앱이 잘 켜진 뒤(포트 확보): 업데이트 표시 정리·결과 알림, 다운로드 엔진은 뒤에서 확인 (3일마다 최신으로)."""
    try:
        import updater
        for msg in updater.finish(core.APP_DIR):
            log(msg)
    except Exception as e:
        log(f"업데이트 마무리 중 문제가 생겼어요 · {e}")
    if sys.platform in ("win32", "darwin"):
        threading.Thread(target=core.engine_autoupdate, args=(log,), daemon=True).start()


def _redirect_to_updater():
    """app.py 를 바로 켰는데(예전에 고정한 작업 표시줄 아이콘 등) 업데이트가 중간에 끊겨 있으면
    실행기(updater.py --launch)를 거쳐 다시 켬 → 섞인 파일을 이전 버전으로 되돌린 뒤 열림. 그렇게 했으면 True."""
    if os.environ.pop("FUTSAL_VIA_UPDATER", None):
        return False
    try:
        import updater
        p = updater._read_json(core.APP_DIR / updater.PENDING)
        if not isinstance(p, dict) or p.get("state") == "installed":
            return False
        updater.spawn(core.APP_DIR, sys.argv[1:])
        return True
    except Exception:
        return False


APP_NAME = "풋살사관학교 스튜디오"


def ensure_shortcut():
    """바탕화면·시작 메뉴(Windows) 또는 응용 프로그램(Mac)에 아이콘을 만든다. 이미 있으면 건너뜀."""
    try:
        if sys.platform == "win32":
            def q(v):  # PowerShell '…' 안의 작은따옴표
                return str(v).replace("'", "''")
            ps = f"""
$w = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('Programs')) ''))
$pin = Join-Path $env:APPDATA 'Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\TaskBar'
if (Test-Path -LiteralPath (Join-Path $pin '{APP_NAME}.lnk')) {{ $dirs += $pin }}  # 예전에 고정한 작업 표시줄 아이콘도 실행기를 거치게
foreach ($dir in $dirs) {{
  $p = Join-Path $dir '{APP_NAME}.lnk'
  $s = $w.CreateShortcut($p)
  $s.TargetPath = '{q(_gui_python())}'
  $s.Arguments = '"{q(core.APP_DIR / "updater.py")}" --launch'
  $s.WorkingDirectory = '{q(core.APP_DIR)}'
  $s.IconLocation = '{q(core.APP_DIR / "icon.ico")}'
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
            end = min(int(b) if a and b else size - 1, start + (8 << 20) - 1)  # 8MB씩: 편집실이 영상 여러 개를 열어도 연결이 막히지 않게
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

    def _host_ok(self):
        """이 컴퓨터의 앱 창(127.0.0.1)에서 온 요청만 (다른 사이트가 주소를 바꿔 몰래 읽는 것 막기)."""
        return (self.headers.get("Host") or "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        # 영상 이름을 받는 주소: 파일 이름만 허용 (C:\..., \\서버\... 같은 경로는 거절)
        if u.path in ("/frame", "/api/thumb/open", "/api/edit/open", "/api/edit/autoseq", "/media", "/thumbs.jpg", "/api/timeline", "/api/edit/backups"):
            try:
                n = q["name"][0]
                editor.safe_name(n)
                if u.path != "/api/edit/backups" and u.path != "/thumbs.jpg":
                    editor.video_path(n)
            except (KeyError, ValueError, FileNotFoundError):
                return self._send(404, {"error": "not found"})
        if u.path == "/":
            return self._send(200, (core.APP_DIR / "ui.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/thumb":
            return self._send(200, (core.APP_DIR / "thumb.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/frame":
            p = thumb.grab(q["name"][0], float(q["t"][0]))
            return self._file(p, "image/jpeg") if p.exists() else self._send(404, {"error": "not found"})
        if u.path.startswith("/asset/"):
            p = (thumb.ASSETS / Path(u.path).name).resolve()
            ctype = "image/png" if p.suffix == ".png" else "image/jpeg"
            return self._file(p, ctype) if p.exists() else self._send(404, {"error": "not found"})
        if u.path == "/api/style/list":
            return self._send(200, {"styles": style.list_styles()})
        if u.path == "/api/thumb/open":
            n = q["name"][0]
            rec = editor.recommend(n)
            return self._send(200, {"docs": thumb.load_docs(n), "hooks": [editor._hook(r) for r in rec["shorts"]],
                                    "keywords": sorted({k for r in rec["shorts"] for k in r["keywords"]}),
                                    "title": n, "info": editor.media_info(n),
                                    "cached": thumb.cached_candidates(n) is not None})
        if u.path == "/editor":
            return self._send(200, (core.APP_DIR / "editor.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/media":
            return self._file(editor.video_path(q["name"][0]), "video/mp4")
        if u.path.startswith("/fonts/"):
            p = (editor.FONTS / Path(u.path).name).resolve()
            return self._file(p, "font/otf") if p.exists() else self._send(404, {"error": "not found"})
        if u.path == "/thumbs.jpg":
            p = core.adir(q["name"][0]) / "thumbs2.jpg"
            return self._file(p, "image/jpeg") if p.exists() else self._send(404, {"error": "not found"})
        if u.path == "/api/edit/file":  # 편집실 미디어 (보관함 영상·가져온 음악/이미지, proxy=1 이면 미리보기 파일)
            try:
                p = editor.media_path(q["f"][0], q.get("src", ["videos"])[0]).resolve()
            except (KeyError, ValueError):
                return self._send(404, {"error": "not found"})
            if not p.exists() or p.parent not in (core.VIDEOS.resolve(), editor.ASSETS.resolve()):
                return self._send(404, {"error": "not found"})
            if q.get("proxy") and editor.proxy_path(p.name, q.get("src", ["videos"])[0]).exists():
                return self._file(editor.proxy_path(p.name, q.get("src", ["videos"])[0]), "video/mp4")
            return self._file(p, mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        if u.path == "/api/edit/media":
            try:
                return self._send(200, editor.media_bundle(q["f"][0], q.get("src", ["videos"])[0]))
            except (FileNotFoundError, ValueError) as e:
                return self._send(404, {"error": f"파일을 찾을 수 없어요 · {e}"})
            except Exception as e:
                return self._send(500, {"error": f"미디어를 열지 못했어요 · {e}"})
        if u.path in ("/api/edit/thumbs.jpg", "/api/edit/poster.jpg", "/api/edit/revaudio"):
            try:
                f, src = q["f"][0], q.get("src", ["videos"])[0]
                if not editor.media_path(f, src).exists():
                    return self._send(404, {"error": "not found"})
                if u.path == "/api/edit/revaudio":  # 거꾸로 재생 클립 소리 (미리보기)
                    return self._file(editor.rev_audio(src, f, q["a"][0], q["b"][0], q.get("sp", ["1"])[0]), "audio/wav")
                if u.path == "/api/edit/poster.jpg":  # 미디어 탭 목록용 한 장면 (빠름)
                    p = editor.poster(f, src)
                else:
                    p = editor.thumbs_file(f, src)
                    if not p.exists() and Path(f).suffix.lower() not in editor.AUDIO_EXTS:
                        editor.thumbs(f, src)  # 처음 보는 미디어는 그 자리에서 만듦 (키프레임만 읽어 빠름)
            except (KeyError, ValueError, OSError, RuntimeError):
                return self._send(404, {"error": "not found"})
            return self._file(p, "image/jpeg") if p.exists() else self._send(404, {"error": "not found"})
        if u.path == "/api/edit/library":
            return self._send(200, editor.library())
        if u.path == "/api/edit/backups":
            return self._send(200, {"backups": editor.backups(q["name"][0])})
        if u.path == "/api/edit/autoseq":
            n = q["name"][0]
            return self._send(200, {"sequences": editor.auto_sequences(n, editor.media_info(n))})
        if u.path == "/api/edit/open":
            n = q["name"][0]
            try:
                proj = editor.load_project(n)
                rec = proj.pop("_recovered", None)
                return self._send(200, {"project": proj, "waveform": editor.waveform(n), "recovered": rec,
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
        # ---- 올리기 키트 (제목 후보·설명·챕터·태그) ----
        if u.path == "/api/upload/kit":  # 고를 수 있는 편집본 + 저장해 둔 키트
            try:
                n = q["name"][0]
                editor.video_path(n)
                src = upload.sources(n)
            except (KeyError, ValueError, FileNotFoundError):
                return self._send(404, {"error": "영상을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 골라 주세요"})
            seq = parse_qs(u.query, keep_blank_values=True).get("seq", [src["default"]])[0]
            try:
                kit = upload.load_kit(n, seq)
            except LookupError:  # 그 사이 편집실에서 지운 편집본
                seq, kit = "", upload.load_kit(n, "")
            return self._send(200, dict(src, seq=seq, kit=kit))
        if u.path == "/api/upload/thumb":  # 완성본 폴더의 썸네일 미리보기
            try:
                p = core.OUT / editor.safe_name(q["file"][0])
            except (KeyError, ValueError):
                return self._send(404, {"error": "not found"})
            if p.suffix.lower() not in upload.IMG_EXTS or not p.is_file():
                return self._send(404, {"error": "not found"})
            return self._file(p, "image/png" if p.suffix.lower() == ".png" else "image/jpeg")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # 다른 사이트가 이 로컬 서버를 조작하지 못하게: 같은 출처 요청만 허용
        origin = self.headers.get("Origin")
        if not self._host_ok() or (origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")):
            return self._send(403, {"error": "forbidden"})
        if urlparse(self.path).path == "/api/edit/upload":  # 큰 파일은 JSON 대신 그대로 받음
            try:
                n = parse_qs(urlparse(self.path).query)["name"][0]
                return self._send(200, editor.save_upload(n, self.rfile, int(self.headers.get("Content-Length") or 0)))
            except Exception as e:
                return self._send(400, {"error": str(e)})
        b, path = self._body(), urlparse(self.path).path
        try:  # 영상 이름은 파일 이름만
            for n in ([b["name"]] if isinstance(b.get("name"), str) and path.startswith(("/api/edit/", "/api/thumb/", "/api/render")) else []) + \
                     (list(b.get("names") or []) if path in ("/api/analyze", "/api/style/learn") else []):
                editor.safe_name(n)
        except ValueError:
            return self._send(400, {"error": "잘못된 파일 이름이에요"})
        ck = b.get("cookies") or None
        jobs = {
            "/api/list": ("채널 불러오기", lambda: hooks.remember_listing(  # 우리 채널이면 제목 패턴용으로 저장 (올리기 키트)
                core.list_videos(b.get("kind", "videos"), ck, b.get("url"), log), b.get("kind", "videos"), b.get("url"))),
            "/api/style/learn": ("스타일 배우기", lambda: style.learn(b.get("name") or "내 스타일", b["names"], log)),
            "/api/download": ("보관함에 담기", lambda: core.download(b["ids"], log, ck)),
            "/api/analyze": ("편집점 찾기", lambda: self._analyze(b)),
            "/api/render": ("러프컷 만들기", lambda: str(core.render(b["name"], b["spec"], log))),
            "/api/update": ("업데이트", lambda: self._update(b)),
        }
        if path == "/api/style/delete":
            f = (style.STYLES / f"{b['name']}.json").resolve()
            if style.STYLES.resolve() in f.parents and f.exists():
                f.unlink()
            return self._send(200, {"ok": True})
        if path == "/api/thumb/frames":
            c = thumb.cached_candidates(b["name"])  # 이미 골라 둔 장면이 있으면 다른 작업 중이어도 바로 돌려줌
            if c is not None:
                return self._send(200, {"ok": True, "frames": c})
            ok = start_job("장면 고르기", lambda: thumb.frame_candidates(b["name"]))
            return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
        if path == "/api/thumb/cut":
            def do_cut():
                src = b["src"]
                if src.startswith("/frame"):
                    qq = parse_qs(urlparse(src).query)
                    sp = thumb.grab(qq["name"][0], float(qq["t"][0]))
                else:
                    sp = (thumb.ASSETS / Path(urlparse(src).path).name).resolve()
                out = thumb.remove_bg(sp, b.get("kind", "hq"))
                log("  누끼 완료")
                return {"cut": thumb.asset_url(out), "src": src}
            ok = start_job("누끼 따기", do_cut)
            return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
        if path == "/api/thumb/upload":
            ext = "png" if b["data"].startswith("data:image/png") else "jpg"
            return self._send(200, {"url": thumb.asset_url(thumb.save_upload(b["data"], ext))})
        if path == "/api/thumb/save":
            thumb.save_docs(b["name"], b["docs"])
            return self._send(200, {"ok": True})
        if path == "/api/thumb/export":
            out = thumb.export_image(b["name"], b["data"], b.get("fmt", "jpg"), b.get("label", "썸네일"))
            log(f"썸네일 저장 · {out.name}")
            return self._send(200, {"ok": True, "file": out.name})
        if path == "/api/edit/save":
            try:  # rev: 편집실이 받은 판 번호 → 그 사이 다른 창이 저장했으면 덮어쓰지 않고 알려 줌
                rev = editor.save_project(b["name"], b["project"], b.get("rev"), bool(b.get("force")))
            except editor.Conflict as e:
                return self._send(409, {"ok": False, "conflict": True, "rev": e.rev, "error": str(e)})
            except OSError as e:
                return self._send(500, {"ok": False, "error": f"저장하지 못했어요 · {e}"})
            return self._send(200, {"ok": True, "rev": rev})
        if path == "/api/edit/export":
            ok = start_job("내보내기", lambda: editor.export(b["name"], b["project"], b.get("opts", {}), log))
            return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
        if path in ("/api/edit/restore", "/api/edit/freeze"):
            try:
                if path == "/api/edit/restore":
                    return self._send(200, {"project": editor.restore_backup(b["name"], b["file"])})
                return self._send(200, editor.freeze_frame(b.get("src", "videos"), b["file"], float(b["t"])))
            except Exception as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/edit/qa":  # 내보낸 영상 자동 검수
            f = (core.OUT / Path(b["file"]).name).resolve()
            if core.OUT.resolve() not in f.parents or not f.exists():
                return self._send(404, {"ok": False, "error": "검수할 파일을 찾지 못했어요"})
            meta = editor.EXPORT_META.get(f.name) or {}  # 내보낼 때 기록한 설정이 먼저 (편집실을 새로 열면 화면 쪽 기록이 없음)
            ok = start_job("영상 검수", lambda: qa.check_video(f, meta.get("format") or b.get("format"), meta.get("master") or b.get("master"),
                                                             editor.run_killable))
            return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
        if path == "/api/edit/proxy":
            ok = start_job("미리보기 파일 만들기", lambda: editor.make_proxy(b["file"], b.get("src", "videos"), log))
            return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
        if path == "/api/edit/cancel":
            editor.cancel_export()
            return self._send(200, {"ok": True})
        if path == "/api/edit/autoseq":  # 배운 스타일로 자동 가편집 (새 편집본으로 추가)
            n, sname = b["name"], b.get("style")
            st = next((x for x in style.list_styles() if x["name"] == sname), None)
            if sname and not st:
                return self._send(404, {"error": "스타일을 찾지 못했어요"})
            kinds = tuple(b.get("kinds") or ("long", "shorts"))
            seqs = editor.auto_sequences(n, editor.media_info(n), st["params"] if st else None, kinds)
            for q in seqs:
                q["name"] = f"{sname} 스타일 가편집" + ("" if q["format"] == "long" else " · " + q["name"]) if sname else q["name"]
            return self._send(200, {"sequences": seqs, "params": st["params"] if st else None})
        if path == "/api/focus":  # 이미 켜진 앱을 다시 실행하면 그 창을 앞으로
            try:
                import webview
                for w in webview.windows:
                    w.restore()
                    w.show()
                    w.on_top = True
                    w.on_top = False
            except Exception:
                return self._send(200, {"ok": False})
            return self._send(200, {"ok": bool(_WINDOW)})
        if path == "/api/open":
            try:
                open_folder({"videos": core.VIDEOS, "analysis": core.ANALYSIS, "out": core.OUT}[b["which"]])
            except Exception as e:
                log(f"폴더를 열지 못했어요 · {e}")
                return self._send(200, {"ok": False})
            return self._send(200, {"ok": True})
        # ---- 촬영본 묶음: 여러 파일을 찍은 순서대로 한 영상으로 (원본은 그대로) ----
        if path == "/api/bundle":
            try:
                names = list(dict.fromkeys(str(n) for n in (b.get("names") or [])))
                for n in names:
                    editor.video_path(n)  # 보관함 안의 파일 이름만
            except (ValueError, FileNotFoundError, TypeError):
                return self._send(400, {"ok": False, "error": "보관함에서 찾지 못한 영상이 있어요. 목록을 새로 고친 뒤 다시 골라 주세요"})
            if len(names) < 2:
                return self._send(400, {"ok": False, "error": "묶으려면 영상을 2개 이상 골라 주세요"})

            def run_bundle():
                r = bundle.make_bundle(names, b.get("title"), log)
                # 이어서 편집점 찾기까지 같은 작업 안에서 (그사이 편집실·썸네일에 다녀와도 끊기지 않게)
                try:
                    self._analyze({"names": [r["name"]], "model": b.get("model") or "large-v3-turbo"})
                    r["analyzed"] = True
                except Exception as e:
                    traceback.print_exc()
                    log(f"묶은 영상은 만들었지만 편집점 찾기는 하지 못했어요 · {e} · 보관함에서 골라 '편집점 찾기'를 다시 눌러 주세요")
                    r["analyze_error"] = str(e)
                return r
            ok = start_job("한 영상으로 묶기", run_bundle)
            return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
        # ---- 올리기 키트 (제목 후보·설명·챕터·태그) ----
        if path == "/api/upload/kit":  # 만들기 · edits 가 있으면 화면에서 고친 내용 저장
            try:
                n = editor.safe_name(b.get("name"))
                editor.video_path(n)
                e = b.get("edits")
                if isinstance(e, dict):
                    kit = upload.save_edits(n, b.get("seq") or None, e.get("title"), e.get("description"), e.get("tags"))
                else:
                    kit = upload.build_kit(n, b.get("seq") or None)
                    log(f"올리기 키트 · {kit['files']['txt']}")
                return self._send(200, {"ok": True, "kit": kit})
            except (ValueError, FileNotFoundError, LookupError) as e:
                return self._send(400, {"ok": False, "error": str(e) or "영상을 찾지 못했어요"})
            except Exception as e:
                traceback.print_exc()
                log(f"올리기 키트를 만들지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": f"올리기 키트를 만들지 못했어요 · {e}"})
        if path == "/api/upload/open":  # 유튜브 스튜디오 · 키트 파일 위치 · 설명 틀
            try:
                if b.get("what") == "studio":
                    upload.open_studio()
                elif b.get("what") == "template":
                    upload.open_template()
                elif b.get("what") == "file":
                    upload.reveal(core.OUT / editor.safe_name(b.get("file")))
                else:
                    return self._send(400, {"ok": False, "error": "무엇을 열지 모르겠어요"})
            except ValueError:
                return self._send(400, {"ok": False, "error": "잘못된 파일 이름이에요"})
            except Exception as e:
                log(f"열지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": f"열지 못했어요 · {e}"})
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
            had = editor._ppath(n).exists()
            # 이미 편집하던 영상이면 만든 편집본은 그대로 두고, 자막만 새로 + 새 가편집은 옆에 추가 (이전 상태는 백업)
            proj, added, caps_changed = editor.reanalyze_project(n)
            editor.thumbs(n)
            editor.waveform(n)
            if had:
                log(f"  편집본은 그대로 두고 새 가편집 {added}개를 추가했어요 · 자막은 새로 받아쓴 내용으로 바꿨어요"
                    + (" (예전 자막은 편집실 '기록' 탭의 '편집점 다시 찾기 전' 백업에 있어요)" if caps_changed else ""))
            else:
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


_WINDOW = []


def _focus_running():
    """이미 켜진 이 앱이 있으면 그 창을 앞으로 (새 창·브라우저를 또 열지 않음 → 같은 편집본을 두 곳에서 고치지 않게)."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/focus", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read() or b"{}").get("ok") is True
    except Exception:
        return False


def _on_closing(win):
    """창을 닫을 때: 편집실에 저장 안 된 것이 있으면 먼저 저장하고 닫음."""
    state = {"go": False}

    def handler():
        if state["go"]:
            return True
        state["go"] = True

        def flush():
            done = threading.Event()
            try:
                win.evaluate_js("Promise.resolve(window.flushBeforeClose ? window.flushBeforeClose() : true)", callback=lambda r: done.set())
                done.wait(6)
            except Exception:
                pass
            editor.wait_saves(3)
            try:
                win.destroy()
            except Exception:
                _quit()

        threading.Thread(target=flush, daemon=True).start()
        return False  # 저장이 끝난 뒤 다시 닫음

    return handler


def main():
    url = f"http://127.0.0.1:{PORT}/"
    if _redirect_to_updater():  # 끊긴 업데이트는 실행기가 먼저 되돌림
        return
    if not os.environ.pop("FUTSAL_RESTART", None) and _focus_running():
        return
    srv = _bind()
    if srv is None:  # 이미 실행 중 → 그 창을 앞으로 (안 되면 그 화면만 띄워줌)
        if not _focus_running():
            webbrowser.open(url)
        return
    log(f"{APP_NAME} v{core.VERSION} 시작")
    log(f"작업 폴더 · {core.WORK}")
    _after_start()
    threading.Thread(target=editor.sweep_temp, daemon=True).start()  # 멈췄거나 갑자기 꺼져 남은 임시 폴더 정리
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if sys.platform in ("win32", "darwin"):
        threading.Thread(target=ensure_shortcut, daemon=True).start()
    if "--browser" not in sys.argv:
        try:
            import webview  # pywebview: 전용 앱 창
            win = webview.create_window(APP_NAME, url, width=1320, height=860, min_size=(960, 640))
            _WINDOW.append(win)
            try:
                win.events.closing += _on_closing(win)
            except Exception:
                pass
            webview.start()
            _quit()  # 창을 닫으면 종료
        except Exception as e:
            log(f"앱 창을 열지 못해 브라우저로 엽니다 · {e}")
    webbrowser.open(url)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
