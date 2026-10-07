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
import captions
import claude_cli
import core
import editor
import hooks
import plan
import qa
import source
import style
import thumb
import upload


def _utf8_console():
    """Windows 콘솔·기록 출력은 기본이 cp949 라 쪼개진 한글 자모(예: 'ᄃ')·이모지가 든 파일 이름을 쓰면 오류가 남
    → UTF-8 로 바꾸고, 그래도 못 쓰는 글자는 대신 표시 (출력 때문에 작업이 멈추지 않게)."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pythonw(콘솔 없음)는 None · 바꿀 수 없는 출력은 그대로
            pass


_utf8_console()

PORT = int(os.environ.get("FUTSAL_PORT", "8765"))
LOG, JOB = [], {"name": None, "result": None, "error": None}
LOCK = threading.Lock()


LOGFILE = core.WORK / "studio.log"
STYLE_JOBS = ("스타일 배우기", "클로드로 더 깊게 보기")   # 스타일 파일(plan)을 끝에 다시 쓰는 작업


def log(msg):
    with LOCK:
        LOG.append(msg)
    try:
        print(msg, flush=True)
    except Exception:  # 화면 출력이 안 돼도 작업·파일 기록은 계속
        pass
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
        # ---- 영상 기획 분석: 클로드 계정 상태 (읽기만 · 60초 기억) · Claude 에게 물어볼 내용 ----
        if u.path == "/api/claude/status":
            return self._send(200, claude_cli.status(refresh=(q.get("refresh") or ["0"])[0] == "1"))
        if u.path == "/api/style/plan_prompt":
            try:
                d = json.loads(style.style_file((q.get("name") or [""])[0]).read_text(encoding="utf-8"))
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            except (OSError, ValueError):
                return self._send(500, {"ok": False, "error": "스타일 파일을 읽지 못했어요"})
            if not isinstance(d.get("plan"), dict):
                return self._send(400, {"ok": False, "error": "이 스타일은 아직 기획 분석이 없어요. 먼저 '다시 배우기'를 눌러 주세요"})
            return self._send(200, {"ok": True, "prompt": plan.claude_prompt(d)})
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
            local = source.annotate(core.local_videos())  # 영상마다 출처(풋살사관학교·다른 채널·내 촬영본) + 고르기 칩 개수
            return self._send(200, {"version": core.VERSION, "workspace": str(core.WORK), "job": JOB["name"],
                                    "result": JOB["result"] if not JOB["name"] else None,
                                    "error": JOB["error"] if not JOB["name"] else None,
                                    "log": lines, "log_total": total, "progress": dict(core.PROGRESS), "local": local, "sources": source.summary(local)})
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
        # ---- 용어 사전: 받아쓰기에 알려 줄 풋살 용어·이름 + 자주 틀리게 받아쓰는 말 (작업 폴더 dict.json) ----
        if u.path == "/api/dict":
            return self._send(200, dict(captions.load_dict(core.dict_path()), defaults=captions.default_dict()))
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
            "/api/list": ("채널 불러오기", lambda: source.annotate_listing(hooks.remember_listing(  # 우리 채널이면 제목 패턴용으로 저장 (올리기 키트)
                core.list_videos(b.get("kind", "videos"), ck, b.get("url"), log), b.get("kind", "videos"), b.get("url")), b.get("url"))),
            "/api/style/learn": ("스타일 배우기", lambda: style.learn(b.get("name") or "내 스타일", b["names"], log)),
            "/api/download": ("보관함에 담기", lambda: source.remember_hints(b.get("sources")) or core.download(b["ids"], log, ck)),
            "/api/analyze": ("편집점 찾기", lambda: self._analyze(b)),
            "/api/render": ("러프컷 만들기", lambda: str(core.render(b["name"], b["spec"], log))),
            "/api/update": ("업데이트", lambda: self._update(b)),
        }
        if path == "/api/style/delete":
            f = (style.STYLES / f"{b['name']}.json").resolve()
            if style.STYLES.resolve() in f.parents and f.exists():
                f.unlink()
            return self._send(200, {"ok": True})
        # ---- 컷 리듬 맞추기 (#7): 스타일 카드의 '말 빠르기 맞추기' 켜기/끄기 (스타일 파일에 저장) ----
        if path == "/api/style/tempo":
            try:
                style.set_tempo(str(b.get("name") or ""), bool(b.get("on")))
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            except (OSError, ValueError):
                return self._send(500, {"ok": False, "error": "설정을 저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(200, {"ok": True})
        # ---- 스타일 일치 점수: 배운 스타일로 만든 자동 가편집이 그 스타일과 얼마나 닮았는지 (내보내지 않고 바로) ----
        # 원본 화면을 처음 보는 영상이면 작업으로 한 번 살펴본 뒤 매김 (결과는 작업 결과로) · 안내는 한국어만 화면에
        if path == "/api/style/score":
            sname, vname = str(b.get("style") or ""), str(b.get("name") or "")
            fail = "점수를 매기지 못했어요. 왼쪽 아래 '작업 기록 보기'에서 내용을 확인해 주세요"

            def do_score():
                try:
                    return dict(style.score_video(sname, vname, log, analyze=True), ok=True)
                except style.StyleError as e:
                    return {"ok": False, "error": str(e)}
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    log(f"점수를 매기지 못했어요 · {e}")
                    return {"ok": False, "error": fail}
            try:
                return self._send(200, dict(style.score_video(sname, vname), ok=True))
            except style.NeedsAnalysis:
                ok = start_job("스타일 일치 점수", do_score)
                return self._send(200 if ok else 409, {"ok": ok, "job": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            except style.StyleError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                log(f"점수를 매기지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": fail})
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
                rev = editor.save_project(b["name"], b["project"], b.get("rev"), bool(b.get("force")), b.get("client"), b.get("seq"))
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
        # ---- 영상 기획 분석: 클로드 계정으로 쓰기 (사용자 PC 의 Claude Code CLI) · Claude 대답 붙여 넣기 ----
        if path in ("/api/claude/install", "/api/claude/login", "/api/claude/token"):
            try:
                if path == "/api/claude/install":
                    claude_cli.install()
                elif path == "/api/claude/login":
                    claude_cli.login()
                elif b.get("clear"):
                    claude_cli.clear_token()
                else:
                    claude_cli.save_token(b.get("token"))
            except claude_cli.ClaudeError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError:
                return self._send(500, {"ok": False, "error": "창을 띄우지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            log({"/api/claude/install": "클로드 설치 창을 띄웠어요", "/api/claude/login": "클로드 로그인 창을 띄웠어요",
                 "/api/claude/token": "클로드 로그인 코드를 " + ("지웠어요" if b.get("clear") else "저장했어요")}[path])
            return self._send(200, {"ok": True})
        if path in ("/api/style/plan_ai", "/api/style/plan_paste"):
            sname = str(b.get("name") or "")
            try:
                style.style_file(sname)
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            if path == "/api/style/plan_paste":
                try:
                    ai = plan.parse_ai(b.get("text"), by="paste")
                    # 스타일 파일을 쓰는 작업(배우기·클로드 판단)이 도는 중이면 그 작업이 끝에 덮어써서 붙여 넣은 대답이 사라짐 → 거절.
                    # 확인과 저장을 작업 잠금 안에서 해서, 저장하는 동안 새 작업이 끼어들지 않게 (저장은 금방 끝남)
                    with LOCK:
                        if JOB["name"] in STYLE_JOBS:
                            return self._send(409, {"ok": False, "busy": True,
                                                    "error": f"지금 '{JOB['name']}' 중이에요. 끝난 뒤에 [저장]을 다시 눌러 주세요 (붙여 넣은 글은 그대로 있어요)"})
                        plan.save_ai(sname, ai)
                except (ValueError, style.StyleError) as e:
                    return self._send(400, {"ok": False, "error": str(e)})
                except OSError:
                    return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
                log(f"Claude 대답을 붙여 넣었어요 · {sname}")
                return self._send(200, {"ok": True, "ai": ai})

            def do_ai():
                try:
                    return plan.run_ai(sname, log, editor.CANCEL)
                except (style.StyleError, OSError, ValueError) as e:
                    return {"ok": False, "error": str(e) or "클로드 판단을 저장하지 못했어요"}
            ok = start_job("클로드로 더 깊게 보기", do_ai)
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
        # ---- 영상 출처 (풋살사관학교 · 다른 채널 · 내 촬영본): 보관함에서 직접 고르기 · 모르는 영상 뒤에서 찾기 ----
        if path == "/api/source":
            try:
                n = editor.safe_name(b.get("name"))
                editor.video_path(n)
                return self._send(200, {"ok": True, "source": source.set_manual(n, b.get("kind"), b.get("channel"), b.get("channelKey"))})
            except FileNotFoundError:
                return self._send(404, {"ok": False, "error": "보관함에서 영상을 찾지 못했어요. 목록을 새로 고친 뒤 다시 골라 주세요"})
            except (ValueError, TypeError) as e:
                return self._send(400, {"ok": False, "error": str(e) or "잘못된 파일 이름이에요"})
            except OSError as e:
                log(f"영상 출처를 저장하지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
        if path == "/api/source/check":  # 작업(start_job)이 아님: 다른 작업을 막지 않고 뒤에서 천천히
            if b.get("stop"):
                source.stop_backfill()
            else:
                source.start_backfill(log)
            return self._send(200, {"ok": True, "running": source.backfill_running()})
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
        # ---- 용어 사전 저장 (다음 '편집점 찾기'부터 받아쓰기에 씀) ----
        if path == "/api/dict":
            try:
                d = captions.save_dict(core.dict_path(), {"terms": b.get("terms", []), "fix": b.get("fix", {})})
            except ValueError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError as e:
                log(f"용어 사전을 저장하지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": f"용어 사전을 저장하지 못했어요 · {e}"})
            return self._send(200, dict(d, ok=True))
        if path == "/api/restart":
            self._send(200, {"ok": True})
            threading.Timer(0.5, restart).start()
            return
        if path in jobs:
            if path in ("/api/list", "/api/download"):
                source.stop_backfill()  # 뒤에서 하던 출처 찾기는 멈춤 (YouTube 에 한꺼번에 묻지 않게 · 다음에 보관함을 열면 이어서)
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
