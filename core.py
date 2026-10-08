"""풋살사관학교 편집도우미 — 핵심 기능 (목록·다운로드·분석·러프컷·업데이트)."""
import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import updater

APP_DIR = Path(__file__).resolve().parent
CONFIG_DEFAULT = {"channel_url": "https://www.youtube.com/channel/UCRYziLOw2T6BF6fXtpUby-g"}
# config.json 은 설정 화면이 없어 메모장으로 고침 → BOM·ANSI·'D:\풋살작업'(역슬래시 하나)도 읽고, 못 읽으면 기본 설정 + 안내 (앱은 켜짐)
_cfg, _cfg_note = updater.read_config(APP_DIR)
CONFIG = dict(CONFIG_DEFAULT, **_cfg)
CONFIG_NOTES = [_cfg_note] if _cfg_note else []  # 앱이 켜진 뒤 작업 기록·화면 알림으로 한 번 (app._after_start)
VERSION = (APP_DIR / "version.txt").read_text(encoding="utf-8").strip()
DEFAULT_MANIFEST = "https://raw.githubusercontent.com/wavely1213/futsal-studio/main/manifest.json"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}


WORKER_WORKSPACE = "FUTSAL_WORKER_WORKSPACE"  # worker.call 이 따로 프로세스(자식)에 넘기는 부모 앱의 작업 폴더


def _open_workspace():
    """작업 폴더와 videos·analysis·out 을 만듦. 설정한 폴더를 쓸 수 없으면(빠진 외장 드라이브·잘못된 경로) 기본 작업 폴더로
    열고 안내 (예전에는 import 오류로 앱이 아예 안 켜지고, 남길 기록도 없었음).
    따로 프로세스(worker.py 자식)는 config.json 을 다시 읽지 않고 부모 앱이 연 폴더 그대로 — 앱이 켜질 때 외장 드라이브가 빠져
    기본 폴더로 열었는데 그 뒤 꽂으면, 자식이 설정 폴더에 누끼를 써서 앱이 못 찾았음 (D-069 검토 고침)."""
    forced = os.environ.get(WORKER_WORKSPACE)
    if forced:
        return Path(forced)
    want = updater.configured_workspace(APP_DIR)
    try:
        for d in (want / "videos", want / "analysis", want / "out"):
            d.mkdir(parents=True, exist_ok=True)
        return want
    except (OSError, ValueError) as e:
        if want == updater.default_workspace():
            raise
        CONFIG_NOTES.append(f"설정한 작업 폴더({want})를 쓸 수 없어서 기본 작업 폴더로 열었어요. "
                            f"외장 드라이브라면 연결한 뒤 프로그램을 다시 켜 주세요 · {e}")
    ws = updater.default_workspace()
    for d in (ws / "videos", ws / "analysis", ws / "out"):
        d.mkdir(parents=True, exist_ok=True)
    return ws


WORK = _open_workspace()
VIDEOS, ANALYSIS, OUT = WORK / "videos", WORK / "analysis", WORK / "out"


# 화면에 보여줄 진행 상태 (app.py 가 /api/state 로 내보냄)
PROGRESS = {}


def set_progress(**kw):
    PROGRESS.clear()
    PROGRESS.update(kw)


_FF = None


def ffmpeg():
    """앱과 함께 설치된 ffmpeg(imageio-ffmpeg)를 먼저 씀 — PATH 의 오래된 ffmpeg 는 xfade·자막 등이 없을 수 있음.
    FUTSAL_FFMPEG 환경 변수로 직접 지정 가능."""
    global _FF
    if _FF is None:
        import os
        exe = os.environ.get("FUTSAL_FFMPEG")
        if not exe:
            try:
                import imageio_ffmpeg
                exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("ffmpeg를 찾지 못했어요")
        _FF = exe
    return _FF


NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # Windows: 검은 창 안 띄움
_JOB = {}  # Windows Job Object 손잡이 (한 번만 만듦)
_JOB_LOCK = threading.Lock()


def _kill_on_close_job():
    """Windows: 이 앱이 꺼지면(창 닫기·업데이트 재시작·오류·작업 관리자) 같이 꺼지는 자식 프로세스 묶음 (Job Object ·
    KILL_ON_JOB_CLOSE). Windows 는 부모가 꺼져도 자식(ffmpeg·pip·claude)을 끄지 않아, 숨은 ffmpeg 가 몇 분씩 파일을 잡고
    쓰거나 pip 두 개가 같은 venv 를 고치게 됨. 만들 수 없으면 None (예전처럼)."""
    if sys.platform != "win32":
        return None
    with _JOB_LOCK:
        if "h" not in _JOB:
            _JOB["h"] = _make_job()
        return _JOB["h"]


def _make_job():
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class Ext(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", ctypes.c_ulonglong * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        h = k32.CreateJobObjectW(None, None)
        if not h:
            return None
        info = Ext()
        info.Basic.LimitFlags = 0x2000 | 0x800  # KILL_ON_JOB_CLOSE | BREAKAWAY_OK (자식이 따로 떨어지겠다고 하면 막지는 않음)
        if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9 = ExtendedLimitInformation
            k32.CloseHandle(h)
            return None
        return (k32, h)
    except Exception:  # noqa: BLE001 — 못 만들면 예전처럼 (자식이 남을 수 있음)
        return None


def track(proc):
    """자식 프로세스를 '앱이 꺼지면 같이 꺼짐' 묶음에 넣음 (Windows · 실패해도 그대로 진행)."""
    job = _kill_on_close_job()
    if job and getattr(proc, "_handle", None):
        try:
            job[0].AssignProcessToJobObject(job[1], int(proc._handle))
        except Exception:  # noqa: BLE001
            pass
    return proc


def popen(cmd, **kw):
    """외부 프로그램 실행 (검은 창 안 띄움 · 앱이 꺼지면 같이 꺼짐). subprocess.Popen 과 같은 인자."""
    if "creationflags" not in kw:
        kw.update(NO_WINDOW)
    return track(subprocess.Popen(cmd, **kw))


def run(cmd, timeout=None, stdin=None):
    """외부 프로그램을 끝까지 실행하고 출력(utf-8 글자)을 받음 = subprocess.run(capture_output=True) + popen.
    파이썬 자식(pip 등)은 출력을 UTF-8 로 쓰게 (Windows 파이프 기본 cp949 → 한국어 오류·한글 경로가 깨짐).
    stdin: 기본은 물려받음 (subprocess.run 과 같음) · 입력을 기다리면 안 되는 것은 subprocess.DEVNULL (tunnel._version)."""
    env = updater.py_env() if cmd and str(cmd[0]) == sys.executable else None
    with popen(cmd, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env) as p:
        try:
            out, err = p.communicate(timeout=timeout)
        except BaseException:
            p.kill()
            p.wait()
            raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def clean_text(s):
    """반쪽 이모지(짝 없는 UTF-16 대리 문자)를 '�'로. 화면 JS 의 .slice() 가 이모지를 반으로 자르면 서버에 그렇게 오고,
    그 글자는 UTF-8 로 쓸 수 없어 studio.log·스타일 파일·화면 응답(/api/state)이 오류로 멈춤. 짝이 맞는 것은 이어 붙임."""
    if not isinstance(s, str):
        return s
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def clean_json(v):
    """요청 JSON 전체(키·값·목록 안)의 글자를 clean_text 로."""
    if isinstance(v, str):
        return clean_text(v)
    if isinstance(v, list):
        return [clean_json(x) for x in v]
    if isinstance(v, dict):
        return {clean_text(k): clean_json(x) for k, x in v.items()}
    return v


def run_bytes(cmd):
    """명령을 실행하고 표준 출력 바이트 (실패하면 None)."""
    r = subprocess.run(cmd, capture_output=True, **NO_WINDOW)
    return r.stdout if r.returncode == 0 else None


# ---------- 목록·다운로드 ----------

def _listing_target(url, kind):
    """채널·영상·재생목록 주소(또는 @핸들) → yt-dlp 에 넘길 주소 (공유 꼬리 ?si=·탭 이름 정리). 비우면 우리 채널.
    list_videos 와 channel_listing(채널 전략)이 같이 씀."""
    from urllib.parse import parse_qs, urlsplit
    url = (url or "").strip()
    if not url:
        return f"{CONFIG['channel_url'].rstrip('/')}/{kind}"
    if not re.match(r"https?://", url, re.I):
        if re.match(r"(?:(?:www|m|music)\.)?(?:youtube\.com|youtu\.be)/", url, re.I):
            url = "https://" + url
        else:
            url = "https://www.youtube.com/" + (url if url.startswith("@") else "@" + url)
    p = urlsplit(url)
    host, q = p.netloc.lower(), parse_qs(p.query)
    if host.endswith("youtu.be"):
        vid = p.path.strip("/").split("/")[0] or None
    elif p.path.rstrip("/") == "/watch":
        vid = (q.get("v") or [None])[0]
    else:
        m = re.match(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})", p.path)
        vid = m.group(1) if m else None
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"  # list=/si=/t= 제거
    if p.path.rstrip("/") == "/playlist":
        return url
    path = re.sub(r"/(videos|shorts|streams|featured|playlists|about|community)$", "", p.path.rstrip("/"))
    return f"https://www.youtube.com{path}/{kind}"  # ?si= 같은 공유 꼬리 제거


def _extract_flat(target, opts, log):
    """yt-dlp 목록 정보(받지 않음) · YouTube 가 막으면 엔진을 최신으로 바꾼 뒤 한 번만 다시 (BR-009).
    그래도 막히면 RuntimeError(BLOCKED_MSG)."""
    def extract():
        opts.update(_js_opts())
        with _yt(log).YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False)

    with _engine(log):
        try:
            return extract()
        except Exception as e:
            if not _blocked(e):
                raise
            if not self_update_allowed():
                log(REMOTE_NO_UPDATE_MSG)
                _old_python_hint(log)
                raise RuntimeError(BLOCKED_MSG) from e
            log("YouTube가 막아서 다운로드 엔진을 최신으로 바꾼 뒤 한 번 더 불러올게요")
            update_engine(log)
            try:
                return extract()
            except Exception as e2:
                if _blocked(e2):
                    _old_python_hint(log)
                    raise RuntimeError(BLOCKED_MSG) from e2
                raise


def channel_listing(url, kind, limit, log=None, lang=None, sleep_requests=0.75):
    """채널 탭(videos·shorts) 목록 원본 정보 (채널 전략 · 최신순 · 받지 않음). limit = 앞에서부터 몇 개까지.
    lang="ko" 면 원래 한국어 제목 (그 대신 조회수·구독자 수가 비어 옴). 요청 사이 sleep_requests 초 쉼 (YouTube 에 몰아서 묻지 않게)."""
    log = log or print
    opts = {"extract_flat": True, "quiet": True, "no_warnings": True, "noplaylist": True,
            "playlistend": int(limit), "sleep_interval_requests": sleep_requests}
    if lang:
        opts["extractor_args"] = {"youtube": {"lang": [lang]}}
    return _extract_flat(_listing_target(url, kind), opts, log)


def list_videos(kind="videos", cookies_browser=None, url=None, log=None):
    """채널 영상 목록 (조회수 순). url을 주면 다른 유튜버 채널, 영상 주소 하나면 그 영상만."""
    log = log or print
    opts = {"extract_flat": True, "quiet": True, "no_warnings": True}
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    opts["noplaylist"] = True  # 영상 주소에 붙은 재생목록(list=)은 펼치지 않음
    info = _extract_flat(_listing_target(url, kind), opts, log)
    ents = info.get("entries") if info.get("entries") is not None else [info]
    flat = []
    for e in ents:  # 탭이 여러 개로 묶여 오면 안쪽 영상까지 펼침
        if e and e.get("_type") == "playlist" and e.get("entries"):
            flat += [x for x in e["entries"] if x]
        elif e:
            flat.append(e)
    import source
    top = source.meta_of(info, channel_only=True)  # 채널 목록은 채널 정보가 바깥에만 있음 → 영상마다 붙여 출처(우리 채널·다른 채널)를 가림
    rows = [dict({"id": e["id"], "title": e.get("title") or "", "views": e.get("view_count") or 0,
                  "duration": e.get("duration") or 0, "kind": kind}, **dict(top, **source.meta_of(e, channel_only=True)))
            for e in flat if e.get("id") and e.get("_type") != "playlist"]
    return sorted(rows, key=lambda r: r["views"], reverse=True)


# 같은 해상도·fps 면 H.264(avc1) 화면을 먼저 (yt-dlp 기본은 AV1 > VP9 > H.264 라 '399+140' AV1 을 고름).
# 편집실은 같은 원본을 미리보기·파형·장면 고르기·내보내기·검수에서 여러 번 다시 푸는데, AV1 은 풀기가 1.3~1.4배 무겁고
# 하드웨어 풀기가 없는 구형 노트북·Premiere 에서 끊기거나 안 열림. H.264 가 없거나 해상도가 낮으면 지금 규칙(해상도·fps 먼저) 그대로.
# 'res'·'fps' 를 앞에 둬서 1080p AV1 대신 720p H.264 로 떨어지지 않게 · HDR 은 그 뒤라 같은 해상도면 SDR H.264 (편집·내보내기용)
FORMAT_SORT = ["res", "fps", "vcodec:h264"]


def format_opts(max_height=1080):
    """yt-dlp 형식 고르기: mp4 화면 + m4a 소리 (없으면 합쳐진 것) · 같은 해상도면 H.264 먼저."""
    return {"format": f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}]/b",
            "format_sort": list(FORMAT_SORT)}


def download(ids, log, cookies_browser=None, max_height=1080, dest=None, archive=None, label="보관함에 담는 중", remember=None,
             why=None, blocked_msg=None):
    """영상 받기 → 받지 못한 영상 id 목록. 기본은 편집용 보관함(VIDEOS · archive.txt · 출처는 sources.json).
    학습용 영상(refs)은 dest(받는 폴더)·archive(False 면 쓰지 않음)·label(진행 표시)·remember(받은 영상 정보 [dict] 를 받는 함수)를 바꿔 씀.
    why(dict 를 주면): 받지 못한 영상 id → 쉬운 안내 {kind, msg, actions} (trouble.explain) · blocked_msg: 이 화면용 막힘 안내."""
    import source
    cur = {"i": 0, "n": len(ids), "vid": None, "streams": {}, "infos": {}}

    def hook(d):
        info = d.get("info_dict") or {}
        if info.get("id"):  # 채널 정보(출처)는 다 받은 뒤 sources.json 에 기록
            cur["infos"][info["id"]] = {k: info.get(k) for k in source.INFO_KEYS}
        fmts = info.get("requested_formats") or [info]
        sizes = [f.get("filesize") or f.get("filesize_approx") or 0 for f in fmts]
        fid = info.get("format_id")
        idx = next((k for k, f in enumerate(fmts) if f.get("format_id") == fid), 0)
        if d["status"] == "downloading":
            got = d.get("downloaded_bytes") or 0
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or sizes[idx] or 0
            cur["streams"][idx] = (got, tot)
            done = sum(g for g, _ in cur["streams"].values())
            whole = sum(max(t, sizes[k] if k < len(sizes) else 0) for k, (_, t) in cur["streams"].items())
            whole += sum(sz for k, sz in enumerate(sizes) if k not in cur["streams"])
            pct = min(99, int(done * 100 / whole)) if whole else None
            set_progress(label=label, item=cur["vid"], step=f"{cur['i']}/{cur['n']}", pct=pct,
                         detail=f"{done / 1e6:.1f}MB / {whole / 1e6:.1f}MB" + (f" · {d['_speed_str'].strip()}" if d.get("_speed_str") else ""))
        elif d["status"] == "finished" and idx == len(fmts) - 1:
            set_progress(label=label, item=cur["vid"], step=f"{cur['i']}/{cur['n']}", pct=99, detail="영상과 소리를 합치는 중")

    opts = {
        **format_opts(max_height),
        "merge_output_format": "mp4",
        # 폴더는 paths 로 따로: trim_file_name 은 outtmpl 전체(폴더 포함)를 자르므로 긴 작업 폴더면 제목이 사라지거나
        # 받은 파일이 다른 폴더로 나감 (yt-dlp _prepare_filename)
        "paths": {"home": str(dest or VIDEOS)},
        "outtmpl": "%(upload_date)s_%(id)s_%(title).60B.%(ext)s",
        "trim_file_name": 120,
        "ffmpeg_location": ffmpeg(),
        "quiet": True, "no_warnings": True, "noprogress": True,
        "progress_hooks": [hook],
    }
    if archive is not False:
        opts["download_archive"] = str(archive or VIDEOS / "archive.txt")
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    failed, retried = [], False  # 막히면 엔진 최신화 + 다시 받기는 한 번만
    # YouTube 해석 도구: 켤 때 받기 시작한 설치가 있으면 (진행률을 보여 주며) 끝날 때까지 기다림 · 엔진 잠금 밖에서
    ensure_deno(log, install=_since("deno_install", "t_fail") > ENGINE_RETRY)
    with _engine(log):
        opts.update(_js_opts())
        ydl = _yt(log).YoutubeDL(opts)
        try:
            for k, vid in enumerate(ids, 1):
                log(f"{label} · {vid}")
                for attempt in (1, 2):
                    cur.update(i=k, vid=vid, streams={})
                    set_progress(label=label, item=vid, step=f"{k}/{len(ids)}", pct=None, detail="연결하는 중")
                    try:
                        ydl.download([vid if vid.startswith("http") else f"https://www.youtube.com/watch?v={vid}"])
                        log("  담기 완료")
                        _remember_source(cur, log, remember)
                    except Exception as e:  # 한 개 실패해도 나머지 계속
                        if attempt == 1 and not retried and _blocked(e) and self_update_allowed():
                            retried = True
                            log("  YouTube가 막았어요 · 다운로드 엔진을 최신으로 바꾼 뒤 한 번 더 받아 볼게요")
                            ydl.close()
                            set_progress(label="다운로드 엔진 준비 중", item=vid, step=f"{k}/{len(ids)}", pct=None, detail="최신으로 바꾸는 중이에요")
                            update_engine(log)
                            opts.update(_js_opts())
                            ydl = _yt(log).YoutubeDL(opts)
                            continue
                        _failed_one(vid, e, log, cookies_browser, blocked_msg, why)
                        failed.append(vid)
                    break
        finally:
            ydl.close()
    return failed


def _failed_one(vid, e, log, browser, blocked_msg, why):
    """영상 하나를 받지 못함: 화면 기록에는 쉬운 한 줄, studio.log 에만 원문 (영어 원문이 화면에 보이지 않게)."""
    import studiolog
    import trouble
    if not self_update_allowed():  # 휴대폰에서 시킨 받기(학습용 포함): 엔진을 바꾸지 않았으니 PC 에서 할 일 (PC 화면용 안내 대신)
        blocked_msg = REMOTE_BLOCKED_MSG
    info = trouble.explain(e, browser=browser, blocked=blocked_msg)  # 막힘: 이 화면용 안내 (없으면 '브라우저를 골라 다시 받아 보세요')
    if info["kind"] == "blocked":
        _old_python_hint(log)
    if why is not None:
        why[vid] = info
    log(f"  담지 못했어요 · {info['msg']}")
    studiolog.write(f"    원문 · {vid} · {' '.join(str(e).split())[:400]}")


def _remember_source(cur, log, remember=None):
    """방금 받은 영상의 채널 정보 → sources.json (학습용 영상은 remember) · 실패해도 받은 영상은 그대로."""
    import source
    infos, cur["infos"] = list(cur["infos"].values()), {}
    try:
        (remember or source.record_download)(infos)
    except Exception as e:  # noqa: BLE001
        log(f"  출처(채널)는 기록하지 못했어요 · {e}")


def adir(name):
    """영상별 분석 폴더. Windows 는 폴더 이름 끝의 공백·점을 지워버리므로 미리 제거.
    편집용 보관함에 파일이 있으면 analysis/<이름> (library_dir) · 아니고 학습용 영상(refs) 기록이 있으면 그 채널 폴더의
    _analysis/<이름> (보관함에서 지운 같은 이름 영상의 옛 분석 폴더가 남아 있어도 학습용이 이김) · 둘 다 아니면 analysis/<이름>."""
    if (VIDEOS / Path(name).name).exists():
        return library_dir(name)
    return _ref("adir_of", name) or ANALYSIS / _stem_key(name)


def _stem_key(name):
    return Path(name).stem.rstrip(" .") or "video"


def _alt_key(name):
    """확장자만(또는 Windows 에서 대소문자만) 다른 영상끼리 분석 폴더가 겹칠 때 뒤에 온 쪽의 폴더 이름: 이름 + 전체 이름 지문
    (editor._cache_dir 의 가져온 파일과 같은 방식 · Windows 폴더 이름은 대소문자를 안 가림)."""
    n = Path(name).name
    base = _stem_key(n)[:60].rstrip(" .") or "video"
    return f"{base}_{hashlib.sha1(n.lower().encode('utf-8', 'surrogatepass')).hexdigest()[:8]}"


_STEMS = {"key": None, "t": 0.0, "map": {}}
_STEMS_LOCK = threading.Lock()


def _stem_twins(name):
    """보관함에서 이 영상과 분석 폴더 이름(대소문자 무시)이 같은 다른 영상 이름들 (IMG_1234.MOV ↔ IMG_1234.mp4 ·
    OBS 녹화 .mkv ↔ 바꾼 .mp4). 목록은 폴더가 바뀌었을 때만(+ FAT 외장 드라이브는 폴더 시각이 안 바뀌어 2초마다) 다시 읽음."""
    try:
        key = (str(VIDEOS), VIDEOS.stat().st_mtime_ns)
    except OSError:
        return []
    with _STEMS_LOCK:
        if _STEMS["key"] != key or time.monotonic() - _STEMS["t"] > 2:
            m = {}
            try:
                for v in VIDEOS.iterdir():
                    if is_video_file(v.name):
                        m.setdefault(_stem_key(v.name).casefold(), []).append(v.name)
            except OSError:
                pass
            _STEMS.update(key=key, t=time.monotonic(), map=m)
        same = _STEMS["map"].get(_stem_key(name).casefold(), [])
    n = Path(name).name
    return [x for x in same if x != n]


def _folder_owner(d):
    """분석 폴더가 어느 영상 것인지: 타임라인 첫 줄('# 타임라인: <이름>') → 편집실 프로젝트의 source. 모르면 None."""
    try:
        with open(d / "transcript_timeline.md", encoding="utf-8") as f:
            first = f.readline()
        if first.startswith("# 타임라인: "):
            return first[len("# 타임라인: "):].rstrip("\r\n")
    except (OSError, ValueError):
        pass
    try:
        src = json.loads((WORK / "projects" / f"{d.name}.json").read_text(encoding="utf-8")).get("source")
        return src if isinstance(src, str) and src else None
    except (OSError, ValueError, AttributeError):
        return None


def library_dir(name):
    """편집용 보관함 영상의 분석 폴더 (편집 프로젝트·썸네일 문서·캐시 이름도 이 폴더 이름을 씀).
    보통은 analysis/<이름>. 이름(대소문자 무시)이 같은 다른 영상이 있으면 그 폴더의 주인(먼저 분석·편집한 쪽)만
    analysis/<이름> 을 쓰고 다른 쪽은 analysis/<이름>_<지문> (한 번 생긴 지문 폴더는 계속 그 영상 것)."""
    n = Path(name).name
    plain, alt = ANALYSIS / _stem_key(n), ANALYSIS / _alt_key(n)
    if alt.is_dir():
        return alt
    twins = _stem_twins(n)
    if not twins:
        return plain
    owner = _folder_owner(plain)
    if owner is None:  # 아직 아무도 분석·편집하지 않았으면 이름 순서로 (늘 같은 답)
        owner = min([n] + twins, key=lambda x: (x.lower(), x))
    return plain if owner == n else alt


def video_file(name):
    """영상 파일 경로: 편집용 보관함(videos) → 없으면 학습용 영상(refs) → 둘 다 없으면 보관함 경로 (있는지는 부르는 쪽이 확인)."""
    p = VIDEOS / Path(name).name
    if p.is_file():
        return p
    return _ref("path_of", name) or p


def kept_sig(name):
    """학습용 영상을 '배운 뒤 영상 파일 지우기'로 지웠으면 그때 파일 지문 (남은 분석 기록을 그대로 쓸 때) · 아니면 None."""
    return None if (VIDEOS / Path(name).name).is_file() else _ref("kept_sig", name)


def _ref(fn, name):
    try:
        import refs
        return getattr(refs, fn)(Path(name).name)
    except Exception:  # noqa: BLE001 — 학습용 기록을 못 읽어도 편집용 보관함은 그대로
        return None


# yt-dlp 가 받는 동안 만드는 중간 파일: 화면만(.f137.mp4)·소리만(.f140.m4a)·합치는 중(.temp.mp4).
# 받다 끊기면(절전·창 닫기·네트워크) 그대로 남아 소리 없는 영상이 보관함에 하나 더 보였음 → 받은 영상 이름 꼴일 때만 뺌
_PARTIAL = re.compile(r"^(?:\d{8}|NA)_[A-Za-z0-9_-]{11}_.*\.(?:f\d+(?:-[0-9A-Za-z]+)*|temp)\.[0-9A-Za-z]+$")


def is_partial(name):
    """받는 중이거나 받다 남은 yt-dlp 중간 파일 이름인지."""
    return bool(_PARTIAL.match(str(name)))


def is_video_file(name):
    """보관함·학습용 영상으로 보여 줄 파일 이름 (영상 확장자 · 받다 남은 중간 파일 아님)."""
    return Path(name).suffix.lower() in VIDEO_EXTS and not is_partial(Path(name).name)


def local_videos():
    out = []
    for v in sorted(VIDEOS.iterdir()):
        if is_video_file(v.name):
            try:  # 받기를 마친 yt-dlp 가 중간 파일을 지우는 순간이면 목록과 stat 사이에 사라짐 (Windows: 지우는 중)
                size = v.stat().st_size
            except OSError:
                continue
            done = (adir(v.name) / "transcript_timeline.md").exists()
            out.append({"name": v.name, "size_mb": round(size / 1e6, 1), "analyzed": done})
    return out


def add_local(paths, log):
    for p in paths:
        src = Path(p)
        if src.suffix.lower() in VIDEO_EXTS and src.exists():
            shutil.copy2(src, VIDEOS / src.name)
            import source
            source.mark_footage(src.name, "local")
            log(f"추가 · {src.name}")


# ---------- 분석 ----------

SRT_ENCODING = "utf-8-sig"  # 자막(.srt)은 BOM 을 붙임: 프리미어·옛 메모장·자막 프로그램이 BOM 없는 UTF-8 을 ANSI 로 읽어 한글이 깨짐


def _ts(sec):
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def _short(sec):
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def _silences(wav, noise_db=-35, min_dur=0.8):
    err = run([ffmpeg(), "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"]).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return [{"start": round(a, 2), "end": round(b, 2)} for a, b in zip(starts, ends)]


def _peaks(wav, top=10):
    err = run([ffmpeg(), "-i", str(wav), "-af",
               "asetnsamples=n=16000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
               "-f", "null", "-"]).stderr
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", err)]
    levels = [float(x) if "inf" not in x else -120.0 for x in re.findall(r"RMS_level=(-?[\d.inf]+)", err)]
    if not levels:
        return []
    median = sorted(levels)[len(levels) // 2]
    picked = []
    for lvl, t in sorted(zip(levels, times), reverse=True):
        if lvl < median + 6:
            break
        if all(abs(t - p["time"]) > 5 for p in picked):
            picked.append({"time": round(t, 1), "rms_db": round(lvl, 1)})
        if len(picked) >= top:
            break
    return sorted(picked, key=lambda p: p["time"])


# ---------- 단어 단위 받아쓰기: 모델 한 번만 불러 쓰기 · 용어 사전 · 받아쓰는 동안 PC 잠들지 않게 ----------
# 사전 형식·고치기·자막 나누기는 captions.py (표준 라이브러리만 쓰는 도우미라 core 가 불러 씀)
_WHISPER, _WHISPER_LOCK, _SESSION = {}, threading.Lock(), threading.local()
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = updater.ES_CONTINUOUS, updater.ES_SYSTEM_REQUIRED


def dict_path():
    """용어 사전 파일 (스튜디오 보관함의 '용어 사전'에서 고침)."""
    return WORK / "dict.json"


VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
_DLL_RE = re.compile(r"DLL load failed|Could not find module|one of its dependencies")


def dll_missing(e):
    """받아쓰기(ctranslate2)·누끼/얼굴(onnxruntime)의 DLL 을 못 불러온 오류인지 = 보통 Windows 에 Microsoft Visual C++
    구성요소(msvcp140·msvcp140_1)가 없음 (Python 에는 들어 있지 않고 두 휠도 넣어 주지 않음 · pip 설치는 성공함)."""
    return isinstance(e, (ImportError, OSError)) and bool(_DLL_RE.search(str(e)))


def vc_runtime_msg(what):
    return (f"이 PC에 Microsoft Visual C++ 구성요소가 없어서 {what}을(를) 할 수 없어요. 앱 폴더의 '시작하기 (Windows).bat'을 "
            f"한 번 실행하면 설치돼요 (또는 {VC_REDIST_URL} 를 받아 설치한 뒤 앱을 다시 켜 주세요)")


# 말소리 고르기(VAD) 문턱: 기본 0.5 는 공 차는 소리·응원 소리가 섞인 말을 통째로 버림 (MSG 400초 글자 오류: 0.5 10.3% ·
# 0.35 2.6% · 0.25 3.0% · 0.2 3.0%, CPU 같음 · 빠진 줄 '어? 이것도 들어갔어요. 대박!' 등이 돌아옴). 셋 중 가장 보수적인 0.35.
# 쉬는 시간·말 앞뒤 여유는 기본값 그대로.
VAD_PARAMS = {"threshold": 0.35}
# 말소리 없는 소음에서 whisper 가 지어내는 '자막 만든 사람' 표시 (유튜브 자막 학습 흔적 · 실제 풋살 영상에서 말할 일이 없음).
# '다음 영상에서 만나요'·'시청해 주셔서 감사합니다' 처럼 실제로 말할 수 있는 끝인사는 일부러 안 거름.
_CREDIT = re.compile(r"(자막|번역)\s*(by|제공|제작|협찬)|subtitles?\s+by", re.I)


def _whisper(model):
    """받아쓰기 모델 — (모델, 스레드 수)마다 한 번만 불러 씀 (여러 영상을 이어서 받아써도). 편집점 찾기가 끝나면 내려놓음."""
    try:
        from faster_whisper import WhisperModel
    except (ImportError, OSError) as e:
        if dll_missing(e):
            raise RuntimeError(vc_runtime_msg("받아쓰기")) from e
        raise
    key = (model, min(8, os.cpu_count() or 4))
    with _WHISPER_LOCK:
        if key not in _WHISPER:
            _WHISPER[key] = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=key[1])
        return _WHISPER[key]


# Windows: 이 스레드가 PC 를 깨어 있게 쥠(on) · 들어올 때 상태로 되돌림 — 실행기(업데이트 마무리)와 같은 하나뿐인 호출 (D-034)
_keep_awake = updater.awake_state


@contextlib.contextmanager
def keep_awake():
    """Windows: 이 스레드가 일하는 동안 PC 가 절전으로 들어가지 않게 (app.start_job 이 모든 작업에 씀 · 겹쳐 써도 됨).
    내보내기·묶기·받기는 CPU·디스크를 써도 Windows 가 '사용 중'으로 보지 않아 유휴 절전 시간이 지나면 잠듦.
    절전 막기를 쥐는 곳은 모두 이것(또는 같은 스레드 안의 _analysis_session)으로만 — SetThreadExecutionState 를 직접 부르지 않는다
    (부르는 곳은 updater.awake_state 하나 · 실행기는 업데이트 마무리 동안 updater._awake 로 같은 것을 씀).
    Windows 는 이 상태를 스레드마다 따로 세므로(어느 스레드든 쥐고 있으면 깨어 있음) 작업 스레드·편집점 찾기·휴대폰으로 보기
    (remote 의 자기 스레드 · '켜 둔 동안 항상')가 겹쳐도 한쪽이 놓을 때 다른 쪽 것이 풀리지 않는다 (D-034).
    같은 스레드 안에서 겹치면 들어올 때의 상태로 되돌린다 (바깥이 쥔 것은 그대로)."""
    prev = _keep_awake(True)
    try:
        yield
    finally:
        _keep_awake(False, prev)


@contextlib.contextmanager
def _analysis_session():
    """편집점 찾기 한 묶음 (겹쳐 불러도 바깥 한 번만): 절전 막기 → 끝나면(실패해도) 되돌리고 모델을 내려놓음 (메모리 반환)."""
    depth = getattr(_SESSION, "depth", 0)
    prev = _keep_awake(True) if depth == 0 else None
    _SESSION.depth = depth + 1
    try:
        yield
    finally:
        _SESSION.depth = depth
        if depth == 0:
            _keep_awake(False, prev)
            with _WHISPER_LOCK:
                _WHISPER.clear()


# 앞 구간 글에 기대지 않고 받아쓸 모델 (condition_on_previous_text=False · E12 D-103) — 정답 대사 A/B 로 잰 모델만.
# '정확하게'(large-v3-turbo)는 글자 오류율 10.65% → 6.10% · '빠르게'(small)는 오히려 나빠져서(6.2% → 8.3%, 문장이 빠지고 그림자 '감사합니다') 예전 그대로
NOCOND_MODELS = ("large-v3-turbo",)


def _model_name(m):
    """불러 둔 받아쓰기 모델의 이름 (_whisper 로 불러 둔 것만 · 모르면 None)."""
    with _WHISPER_LOCK:
        return next((k[0] for k, v in _WHISPER.items() if v is m), None)


def _whisper_opts(m, vocab):
    """단어 시각 + 용어 사전 힌트 (첫머리 initial_prompt, 설치된 faster-whisper 가 받으면 매 구간 hotwords) ·
    '정확하게' 모델은 앞 구간 글에 기대지 않기 (condition_on_previous_text=False — 같은 문장을 한 번 더 쓰거나 하지 않은 끝말을 지어내지 않게 ·
    NOCOND_MODELS 만 · E12 D-103)."""
    import inspect
    import captions
    tok = getattr(m, "hf_tokenizer", None)

    def count(s):  # 토큰 수 (토크나이저가 없으면 한글 1글자 ≈ 2토큰으로 넉넉히)
        try:
            return len(tok.encode(" " + s, add_special_tokens=False).ids)
        except Exception:
            return 2 * len(s)
    opts = {"word_timestamps": True}
    p = captions.prompt(vocab["terms"], 180, count)  # 첫머리 힌트 한도 ≈223토큰 · hotwords(60)와 합쳐도 받아쓸 자리가 남게
    if p:
        opts["initial_prompt"] = p
    try:
        params = inspect.signature(m.transcribe).parameters
    except (TypeError, ValueError):
        params = {}
    h = captions.hotwords(vocab["terms"], 60, count) if "hotwords" in params else ""
    if h:
        opts["hotwords"] = h
    if "condition_on_previous_text" in params and _model_name(m) in NOCOND_MODELS:  # 앞 문장에 기대지 않음 (같은 말 되풀이·지어내기 막기)
        opts["condition_on_previous_text"] = False
    return opts


def _seg_of(s, fixmap, off=0.0):
    """받아쓴 구간 하나 → {start, end, text(, words)} · 사전 고치기 · 글은 단어를 이은 것과 똑같게. (구간, 고친 곳 수)
    off: 소리 일부만 다시 들었을 때 그 시작 시각 (구간·단어 시각에 더함)."""
    import captions
    seg = {"start": round(s.start + off, 2), "end": round(s.end + off, 2), "text": s.text.strip()}
    ws = [{"w": w.word.strip(), "s": round(float(w.start) + off, 2), "e": round(float(w.end) + off, 2), "p": round(float(w.probability), 2)}
          for w in getattr(s, "words", None) or () if w.word.strip()]
    if not ws:
        t = captions.apply_dict(seg["text"], fixmap)
        return dict(seg, text=t), int(t != seg["text"])
    ws, n = captions.fix_words(ws, fixmap)
    return dict(seg, text=" ".join(w["w"] for w in ws), words=ws), n


# ---------- 받아쓰기 모델 고르기: PC 사양에 맞는 기본값 · 예상 시간·메모리 (D-070) ----------
# 같은 60초 레슨 실측 (4코어·16GB 개발 PC, CPU int8): '빠르게'(small) 16.3초·최대 765MB · '정확하게'(large-v3-turbo) 47.7초·2,048MB
# → (받아쓰기 초 / 영상 초, 최대 메모리 MB). 60분 영상이면 약 16분 vs 48분.
WHISPER_COST = {"small": (0.27, 765), "large-v3-turbo": (0.80, 2048)}
WHISPER_LABEL = {"small": "빠르게", "large-v3-turbo": "정확하게"}
WHISPER_CORES_MEASURED = 4  # 위 값을 잰 PC 의 받아쓰기 스레드 수 (_whisper 는 min(8, 코어) 스레드)
LOW_MEM_GB = 8.5  # 이하면 '빠르게' 기본 (8GB 노트북은 Windows 가 7.7~7.9GB 로 보여 줌 · 정확하게 2GB + 앱·브라우저면 스왑)
LOW_CORES = 4     # 이하면 '빠르게' 기본 (정확하게는 영상 길이의 약 0.8배가 걸림)
_SPEC = {}


def _mem_total_gb():
    """이 PC 의 전체 메모리(GB) · 모르면 None (표준 라이브러리만: Windows GlobalMemoryStatusEx · 그 밖 sysconf)."""
    if sys.platform == "win32":
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        try:
            st = MemStatus()
            st.dwLength = ctypes.sizeof(MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return round(st.ullTotalPhys / 1024 ** 3, 1)
        except (AttributeError, OSError):
            pass
        return None
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1)
    except (AttributeError, ValueError, OSError):
        return None


def pc_spec():
    """{"memGB": 전체 메모리 GB 또는 None, "cores": 논리 코어 수} — 한 번 재서 기억."""
    if not _SPEC:
        _SPEC.update(memGB=_mem_total_gb(), cores=os.cpu_count() or 4)
    return dict(_SPEC)


def default_model(spec=None):
    """이 PC 에 맞는 받아쓰기 기본값: 메모리 8GB 이하이거나 4코어 이하면 '빠르게'(small), 아니면 '정확하게'."""
    spec = spec or pc_spec()
    low = (spec.get("memGB") is not None and spec["memGB"] <= LOW_MEM_GB) or (spec.get("cores") or 4) <= LOW_CORES
    return "small" if low else "large-v3-turbo"


def model_of(v):
    """화면·휴대폰이 보낸 모델 이름 → 아는 모델만 (없거나 모르는 값이면 이 PC 기본값)."""
    return v if isinstance(v, str) and v in WHISPER_COST else default_model()  # 목록·객체가 오면 `in` 이 TypeError


def whisper_estimate(secs, spec=None):
    """영상 secs 초를 받아쓸 때 모델마다 {secs: 예상 초, perHour: 60분 영상이면, memMB: 최대 메모리, tight: 이 PC 메모리에 빠듯함}
    + 기본값·사양.
    코어가 잰 PC 보다 적으면 그만큼 느리게 · 많아도 빨라진다고 보지 않음 (하이퍼스레딩은 거의 안 빨라짐 · 넉넉하게 안내)."""
    spec = spec or pc_spec()
    slow = max(1.0, WHISPER_CORES_MEASURED / max(1, min(8, spec.get("cores") or 4)))
    mem = spec.get("memGB")
    models = {m: {"secs": round(max(0.0, float(secs or 0)) * rate * slow), "perHour": round(3600 * rate * slow), "memMB": mb,
                  "label": WHISPER_LABEL[m], "tight": bool(mem) and mb / 1024 > mem * 0.25}
              for m, (rate, mb) in WHISPER_COST.items()}
    why = "mem" if mem is not None and mem <= LOW_MEM_GB else "cores" if (spec.get("cores") or 4) <= LOW_CORES else None  # 기본값이 '빠르게'인 까닭
    return {"default": default_model(spec), "why": why, "memGB": mem, "cores": spec.get("cores"), "dur": round(float(secs or 0), 1),
            "models": models}


class FileProblem(RuntimeError):
    """그 영상 파일 하나의 문제 (깨짐·소리 없음) — 여러 개를 찾을 때는 건너뛰고 나머지를 계속 (analyze_many)."""


PER_FILE = ("broken", "missing", "locked", "ffmpeg", "copying")  # 그 파일만의 문제로 보는 오류 종류 (trouble.explain)


class Analyzed(list):
    """analyze_many 결과: 끝난 분석 폴더 목록 + failed {이름: 쉬운 안내} (그 파일만의 문제로 건너뛴 영상)."""
    failed = None


def analyze_many(names, log, model="large-v3-turbo"):
    """여러 영상 편집점 찾기 → 끝난 분석 폴더 목록(Analyzed · .failed).
    여러 개일 때 그 파일만의 문제(깨짐·소리 없음·파일 없음·잠김)는 건너뛰고 나머지를 계속 · .failed 에 {이름: 쉬운 안내}
    (모두 실패하면 작업 실패). 메모리·인터넷처럼 다음 영상도 같을 문제는 거기서 멈추고, 오류에 retry(남은 영상 이름)·
    note(몇 개 끝났는지)를 붙임 → 화면의 [다시 하기]는 남은 영상만. 하나만 찾을 때는 예전처럼 그 오류 그대로."""
    import trouble
    for n in names:  # 하나라도 아직 복사 중이면 아무것도 시작하지 않음
        _not_copying(n)
    out, failed = Analyzed(), {}
    out.failed = failed
    with _analysis_session():
        for k, n in enumerate(names, 1):
            try:
                out.append(str(_analyze_kept(n, log, model, f"{k}/{len(names)}")))
            except Exception as e:
                info = trouble.explain(e)
                if len(names) == 1 or info["kind"] == "cancelled" or not (isinstance(e, FileProblem) or info["kind"] in PER_FILE):
                    if len(names) > 1 and info["kind"] != "cancelled":
                        left = list(names[k - 1:])
                        e.retry = {"names": left}
                        e.note = f"{len(names)}개 중 {len(out)}개는 끝났어요 · [다시 하기]는 남은 {len(left)}개만 해요"
                    raise
                failed[n] = info
                log(f"  이 영상은 건너뛰고 다음 영상을 찾을게요 · {info['msg']}")
    if names and len(failed) == len(names):  # 모두 그 파일만의 문제 → 작업 실패 (다시 해도 같음)
        first = failed[names[0]]
        raise trouble.Trouble(first["kind"], f"{len(names)}개 모두 편집점을 찾지 못했어요 · {first['msg']}", ["folder", "log"])
    return out


def analyze(name, log, model="large-v3-turbo", step="1/1", label="편집점 찾는 중"):
    _not_copying(name)
    with _analysis_session():
        return _analyze_kept(name, log, model, step, label)


def _analyze_kept(name, log, model, step, label="편집점 찾는 중"):
    """편집점 찾기 + 찾기 시작할 때의 파일 크기·수정 시각 기록 (나중에 파일이 바뀌면(덜 복사된 채 찾았음) 보관함이 알려 줌).
    지난번 기록과 파일이 다르면(복사가 덜 된 채 찾았음) 그때 만든 파형·썸네일·미리보기 파일을 지움 → 편집실이 새 파일로 다시 만듦."""
    import intake
    sig = intake.sig(VIDEOS / name)
    try:
        if intake.changed(adir(name), (VIDEOS / name).stat()):
            n = intake.clear_media_cache(adir(name))
            if n:
                log("  파일이 바뀌어서 예전 파형·썸네일·미리보기 파일을 지우고 새로 만들게요")
    except OSError:
        pass
    out = _analyze(name, log, model, step, label)
    intake.remember(out, sig)
    return out


def _not_copying(name):
    """아직 다른 프로그램이 쓰는 중(복사 중)인 영상이면 편집점 찾기를 멈춤 (앞부분만 받아쓰고 '준비됨'이 붙지 않게)."""
    import intake
    import trouble
    if intake.busy(VIDEOS / name):
        raise trouble.Trouble("copying", intake.copying_msg(name), ["retry"])


def _analyze(name, log, model, step, label="편집점 찾는 중"):
    video = VIDEOS / name
    outdir = adir(name)
    outdir.mkdir(parents=True, exist_ok=True)
    wav = outdir / "audio.wav"
    log(f"{label} · {name}")
    set_progress(label=label, item=name, step=step, pct=None, detail="소리 추출 중")
    r = run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    if r.returncode or not wav.exists():  # 어느 영상인지 이름을 넣음 (여러 개를 찾을 때 카드에서 알 수 있게)
        raise FileProblem(f"'{name}'에서 소리를 꺼내지 못했어요 (파일이 깨졌거나 소리가 없는 영상일 수 있어요) · {r.stderr.strip()[-200:]}")

    log("  대사를 받아쓰는 중이에요 (처음 한 번은 준비에 몇 분 걸려요)")
    set_progress(label=label, item=name, step=step, pct=None, detail="받아쓰기 준비 중")
    import captions
    m = _whisper(model)
    # PyAV 버전 차이로 인한 오류를 피하려고, ffmpeg로 뽑은 wav를 직접 읽어 넘긴다
    import wave
    import numpy as np
    with wave.open(str(wav), "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    vocab = captions.load_dict(dict_path())
    if vocab["terms"]:  # 힌트 길이 한도 때문에 뒤쪽 용어가 빠질 수 있어서 실제로 알려 준 수를 적음
        opts = _whisper_opts(m, vocab)
        sent = opts["initial_prompt"].count(", ") + 1 if opts.get("initial_prompt") else 0
        n = len(vocab["terms"])
        log(f"  용어 사전의 말 {n}개를 받아쓰기에 알려 줘요" if sent >= n else
            f"  용어 사전의 말 {n}개 중 앞의 {sent}개를 받아쓰기에 알려 줘요 (힌트 길이 한도 · 중요한 말을 앞에 두세요)")

    def on_seg(t, total, k):
        if total:
            set_progress(label=label, item=name, step=step, pct=min(99, int(t * 100 / total)),
                         detail=f"대사 받아쓰는 중 · {_short(t)} / {_short(total)}")
        if k % 20 == 0:
            log(f"  {_short(t)}까지 받아씀")
    stats = {}
    segments, fixed, echoed, unheard = transcribe_audio(m, audio, vocab, on_seg, stats=stats)
    ghost = stats.get("ghost", 0)
    set_progress(label=label, item=name, step=step, pct=99, detail="컷 후보·하이라이트 찾는 중")
    sil, peaks = _silences(wav), _peaks(wav)

    # 결과는 임시 파일 → 바꿔 끼우기 (다시 찾다 꺼져도 예전 결과가 반쪽 파일이 되지 않게). '찾음' 표시인 타임라인은 맨 마지막
    updater.write_atomic(outdir / "analysis.json", json.dumps({"silences": sil, "loud_peaks": peaks}, ensure_ascii=False, indent=1))
    write_transcript(name, segments, sil, peaks, asr={"v": ASR_VER, "model": model})
    try:  # 소리 파일 정리는 곁가지: 탐색기·백신이 잡고 있어도 받아쓴 결과는 그대로 (다음 편집점 찾기가 덮어씀)
        wav.unlink()
    except OSError:
        pass
    log(f"  완료 · 대사 {len(segments)}줄 · 컷 후보 {len(sil)}곳 · 하이라이트 {len(peaks)}곳"
        + (f" · 용어 사전으로 {fixed}곳을 고쳤어요" if fixed else "")
        + (f" · 말소리가 불분명해 용어 목록만 잘못 받아쓴 {echoed}곳은 뺐어요" if echoed else "")
        + (f" · 말소리가 없는 곳에서 잘못 들은 {unheard}곳은 뺐어요" if unheard else "")
        + (f" · 소음에서 잘못 받아쓴 자막 표시 {ghost}곳은 뺐어요" if ghost else ""))
    return outdir


def write_transcript(name, segments, sil=None, peaks=None, asr=None):
    """받아쓰기 결과 파일들 (transcript.json · subtitles.srt · transcript_timeline.md · 받아쓰기 방식 asr.json) — 임시 파일에 쓰고 바꿔 끼움.
    sil·peaks 가 없으면 analysis.json 의 것을 씀 (MSG 가 받아쓰기만 다시 고칠 때)."""
    outdir = adir(name)
    if sil is None or peaks is None:
        try:
            extra = json.loads((outdir / "analysis.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            extra = {}
        sil, peaks = extra.get("silences") or [], extra.get("loud_peaks") or []
    srt = "".join(f"{i}\n{_ts(s['start'])} --> {_ts(s['end'])}\n{s['text']}\n\n" for i, s in enumerate(segments, 1))
    events = [(s["start"], f"[{_short(s['start'])}] {s['text']}") for s in segments]
    events += [(x["start"], f"[{_short(x['start'])}] ── 무음 {x['end'] - x['start']:.1f}초 (컷 후보)") for x in sil]
    events += [(p["time"], f"[{_short(p['time'])}] ▲ 음량 피크 {p.get('rms_db', p.get('db', ''))}dB (하이라이트 후보)") for p in peaks]
    md = f"# 타임라인: {name}\n\n" + "".join(line + "\n" for _, line in sorted(events))
    files = [("transcript.json", json.dumps(segments, ensure_ascii=False, indent=1), "utf-8"), ("subtitles.srt", srt, SRT_ENCODING)]
    if asr is not None:
        files.append(("asr.json", json.dumps(asr, ensure_ascii=False), "utf-8"))
    files.append(("transcript_timeline.md", md, "utf-8"))  # '찾음' 표시인 타임라인은 맨 마지막
    for fn, text, enc in files:
        updater.write_atomic(outdir / fn, text, encoding=enc)


def asr_info(name):
    """받아쓰기 방식 기록 (asr.json) · 없으면(v2.0.0 까지의 받아쓰기) {}."""
    try:
        d = json.loads((adir(name) / "asr.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------- 받아쓰기 (전체를 듣고, 말소리가 없는 곳의 글만 버림) ----------
# v2.0.0 까지는 faster-whisper 의 말 찾기(VAD)로 말소리만 이어 붙여 받아썼는데, 짧은 감탄('어? 이것도 들어갔어요. 대박!')·
# 작은 말이 통째로 빠지고 앞 문장에 끌려 같은 말을 되풀이했다('네 번째 빗나갔어요. 네번째 빗나갔어요.').
# 지금은 소리 전체를 앞 문장에 기대지 않고 듣고(condition_on_previous_text=False), 느슨한 말 찾기로 말소리가 있는 곳을 따로 찾아
# 그 밖에서 나온 글(무음·음악에서 지어낸 '시청해 주셔서 감사합니다')만 버린다.
ASR_VER = 2
SPEECH_VAD = {"threshold": 0.2, "min_speech_duration_ms": 80, "min_silence_duration_ms": 500, "speech_pad_ms": 300}
SPEECH_PAD = 0.35   # 말소리 구간 앞뒤 여유(초) — 단어 시각이 조금 밀려도 버리지 않게


def speech_regions(audio, sr=16000):
    """말소리가 있을 만한 곳 [(a, b)] (느슨한 문턱 0.2 · 짧은 감탄도 잡음) · 말 찾기 모델을 못 쓰면 None."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:
        return None
    out = []
    for x in get_speech_timestamps(audio, VadOptions(**SPEECH_VAD), sampling_rate=sr):
        a, b = x["start"] / sr - SPEECH_PAD, x["end"] / sr + SPEECH_PAD
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([max(0.0, a), b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


def heard(seg, regions):
    """받아쓴 구간이 말소리가 있는 곳에서 나왔는지 (단어 가운데 시각의 절반 넘게 말소리 구간 안) · 단어 시각이 없으면 구간 가운데."""
    ws = seg.get("words") or [{"s": seg["start"], "e": seg["end"]}]
    inside = sum(1 for w in ws if any(a <= (float(w["s"]) + float(w["e"])) / 2 <= b for a, b in regions))
    return 2 * inside > len(ws)


def transcribe_audio(m, audio, vocab, on_seg=None, sr=16000, stats=None):
    """소리(16kHz float) → (구간 목록, 사전으로 고친 수, 용어 목록만 따라 쓴 구간 수, 말소리 없는 곳에서 나와 버린 구간 수).
    stats: 주면 stats['ghost'] 에 '자막 by …' 처럼 소음에서 지어낸 줄을 버린 수를 넣음.
    faster-whisper 가 단어 시각을 맞추다 가끔 멈추면(IndexError · 말 없는 소리에서) 그 뒤는 예전 방식(말 찾기)으로 마저 들음."""
    import captions
    opts = _whisper_opts(m, vocab)
    total = len(audio) / sr
    regions = speech_regions(audio, sr)
    segments, fixed, echoed, unheard = [], 0, 0, 0
    off, vad = 0.0, regions is None  # 말 찾기 모델이 없으면 예전 방식 그대로
    while off < max(0.3, total - 0.3):
        part = audio[int(off * sr):]
        last = off
        try:
            segs, _ = m.transcribe(part, language="ko", vad_filter=vad, **({"vad_parameters": VAD_PARAMS} if vad else {}), **opts)
            for s in segs:
                seg, n = _seg_of(s, vocab["fix"], off)
                last = max(last, seg["end"])
                if captions.echo(seg.get("words"), vocab["terms"]):  # 말소리가 불분명한 곳에서 용어 목록만 따라 쓴 구간은 버림
                    echoed += 1
                    continue
                if _CREDIT.search(seg["text"]):  # 소음에서 지어낸 '한글자막 by …' 같은 줄
                    if stats is not None:
                        stats["ghost"] = stats.get("ghost", 0) + 1
                    continue
                if regions is not None and not heard(seg, regions):
                    unheard += 1
                    continue
                if segments and seg["start"] < segments[-1]["end"] - 0.5 and seg["text"] == segments[-1]["text"]:
                    continue  # 다시 들은 곳의 겹친 구간
                segments.append(seg)
                fixed += n
                if on_seg:
                    on_seg(seg["end"], total, len(segments))
            break
        except (IndexError, ValueError):
            if vad:
                break
            off, vad = max(off, last), True
    return segments, fixed, echoed, unheard


def timeline_events(name):
    """화면 표시용: 대사·컷 후보·하이라이트를 시간순 목록으로."""
    d = adir(name)
    if not (d / "transcript.json").exists():
        return []
    segs = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
    extra = json.loads((d / "analysis.json").read_text(encoding="utf-8"))
    ev = [{"t": s["start"], "end": s["end"], "type": "speech", "text": s["text"]} for s in segs]
    ev += [{"t": x["start"], "end": x["end"], "type": "silence", "text": f"{x['end'] - x['start']:.1f}초 동안 조용함"} for x in extra["silences"]]
    ev += [{"t": p["time"], "end": p["time"] + 1, "type": "peak", "text": "소리가 갑자기 커짐 (환호·슈팅·웃음 가능성)"} for p in extra["loud_peaks"]]
    return sorted(ev, key=lambda e: e["t"])


def timeline(name):
    p = adir(name) / "transcript_timeline.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------- 러프컷 ----------

def _tc(sec, fps=30):
    frames = int(round(sec * fps))
    h, rem = divmod(frames, 3600 * fps)
    m, rem = divmod(rem, 60 * fps)
    s, f = divmod(rem, fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def render(name, spec, log):
    video = VIDEOS / name
    cuts, fmt = spec["cuts"], spec.get("format", "long")
    stem = f"{video.stem.rstrip(' .')}_{fmt}"
    vf = "scale=-2:1920,crop=1080:1920" if fmt == "shorts" else "null"
    tmp = Path(tempfile.mkdtemp(dir=OUT))
    parts = []
    for i, c in enumerate(cuts):
        set_progress(label="러프컷 만드는 중", item=name, step=f"{i + 1}/{len(cuts)}", pct=int(i * 100 / len(cuts)), detail=f"컷 {i + 1} 자르는 중")
        log(f"  컷 {i + 1}/{len(cuts)} · {c['start']}초~{c['end']}초 {c.get('note', '')}")
        part = tmp / f"p{i:03d}.mp4"
        r = run([ffmpeg(), "-y", "-loglevel", "error", "-ss", str(c["start"]), "-to", str(c["end"]), "-i", str(video),
                 "-vf", vf, "-af", "loudnorm=I=-14:TP=-1.5", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-ar", "48000", str(part)])
        if r.returncode:
            raise RuntimeError(r.stderr[-500:])
        parts.append(part)
    (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    out = OUT / f"{stem}.mp4"
    run([ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-c", "copy", str(out)])
    shutil.rmtree(tmp)

    rec, lines = 0.0, [f"TITLE: {stem}", "FCM: NON-DROP FRAME", ""]
    for i, c in enumerate(cuts, 1):
        dur = c["end"] - c["start"]
        lines.append(f"{i:03d}  AX       AA/V  C        {_tc(c['start'])} {_tc(c['end'])} {_tc(rec)} {_tc(rec + dur)}")
        lines.append(f"* FROM CLIP NAME: {video.name}")
        if c.get("note"):
            lines.append(f"* COMMENT: {c['note']}")
        rec += dur
    (OUT / f"{stem}.edl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"  완성 · {out.name} ({rec:.1f}초) · 편집 프로그램용 {stem}.edl")
    return out


# ---------- 다운로드 엔진 (yt-dlp · YouTube 해석 도구 Deno) ----------
# YouTube 는 자주 바뀜 → 켤 때 3일마다 엔진을 최신으로, 막히면 그 자리에서 한 번 최신으로 바꾸고 다시 시도.
# yt-dlp 공식 안내: YouTube 를 제대로 받으려면 yt-dlp-ejs(= yt-dlp[default]) + 자바스크립트 실행기(Deno 권장, 2.3 이상)가 필요.

ENGINE_HOME = Path.home() / ".futsal-studio"  # 사용자별 (앱 폴더를 지워도 남음)
ENGINE_EVERY = 3 * 86400
ENGINE_RETRY = 86400  # 켤 때 저절로 하다 실패했으면 하루 뒤에 다시 (인터넷이 막힌 곳에서 켤 때마다 붙잡지 않게)
ENGINE_PKG = "yt-dlp[default]"
DENO_MIN = (2, 3, 0)
DENO_ZIP = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
DENO_ZIP_ALT = "https://dl.deno.land/release/{ver}/deno-x86_64-pc-windows-msvc.zip"
DENO_AUTO = sys.platform == "win32"  # 자동 설치는 Windows 만
BLOCKED_MSG = ("다운로드 엔진을 최신으로 바꿔 다시 해 봤지만 YouTube가 계속 막고 있어요. YouTube에 로그인해 둔 브라우저(파이어폭스·엣지·웨일·"
               "크롬)를 소재 찾기의 '다운로드가 계속 실패하나요?' → '로그인 정보로 받기'에서 고른 뒤 다시 해 보세요.")
_ENGINE_LOCK = threading.RLock()  # pip 로 엔진을 바꾸는 동안만 목록·다운로드가 기다림 (같은 작업 안의 재시도는 통과)
_SELF_UPDATE = threading.local()
REMOTE_NO_UPDATE_MSG = ("  휴대폰에서 시킨 작업이라 다운로드 엔진은 바꾸지 않았어요 · "
                        "PC에서 '업데이트 확인' → 다운로드 엔진을 최신으로 바꾼 뒤 다시 받아 주세요")
REMOTE_BLOCKED_MSG = ("YouTube가 막았어요 · 휴대폰에서 시킨 받기라 다운로드 엔진은 바꾸지 않았어요. PC에서 '업데이트 확인'으로 "
                      "엔진을 최신으로 바꾸거나 '로그인 정보로 받기'에서 YouTube에 로그인해 둔 브라우저를 골라 다시 받아 주세요")


@contextlib.contextmanager
def no_self_update():
    """휴대폰에서 시킨 작업(remote, D-027): 이 스레드에서는 다운로드 엔진(pip)·Deno 를 설치·업데이트하지 않는다 (PC 에서만)."""
    prev = getattr(_SELF_UPDATE, "off", False)
    _SELF_UPDATE.off = True
    try:
        yield
    finally:
        _SELF_UPDATE.off = prev


def self_update_allowed():
    return not getattr(_SELF_UPDATE, "off", False)
_DENO_LOCK = threading.Lock()     # Deno 설치는 한 번에 하나 (엔진 잠금과 따로: 받는 동안 목록 불러오기는 기다리지 않음)
_DENO_PROG = {}                   # 받는 중인 Deno 진행률 (기다리는 다운로드 작업이 화면에 보여 줌)
_DENO = None


def _blocked_text(msg):
    return "not a bot" in msg or "Sign in" in msg or re.search(r"(?<![\w-])403(?![\w-])", msg) is not None


def _blocked(e):
    """YouTube 가 막았다는 다운로드 오류인지 (엔진을 최신으로 바꾸면 풀리는 경우가 많음)."""
    return any(c.__name__ == "DownloadError" for c in type(e).__mro__) and _blocked_text(str(e))


@contextlib.contextmanager
def _engine(log):
    if not _ENGINE_LOCK.acquire(blocking=False):
        set_progress(label="다운로드 엔진 준비 중", pct=None, detail="최신으로 바꾸는 중이에요 · 잠시만 기다려 주세요")
        _ENGINE_LOCK.acquire()
        set_progress()  # 기다림 끝 → 원래 작업 이름이 다시 보이게
    try:
        ensure_deno(log, install=False)
        yield
    finally:
        _ENGINE_LOCK.release()


def _js_opts():
    return {"js_runtimes": {"deno": {"path": _DENO}}} if _DENO else {}


def _yt(log=print):
    try:
        import yt_dlp
        return yt_dlp
    except Exception as e:  # 엔진 업데이트가 중간에 끊겨 망가진 경우 → 다시 설치
        log(f"다운로드 엔진이 없거나 망가져서 다시 설치해요 · {e}")
    update_engine(log)
    try:
        import yt_dlp
        return yt_dlp
    except Exception as e:
        raise RuntimeError("다운로드 엔진을 설치하지 못했어요. 인터넷 연결을 확인한 뒤 '시작하기 (Windows).bat'을 "
                           f"다시 실행해 주세요 · {e}")


def _forget_ytdlp():
    """pip 로 바꾼 뒤 새 yt-dlp 를 처음부터 다시 읽게 (예전 것과 섞이지 않게)."""
    for m in [m for m in sys.modules if m.split(".")[0] in ("yt_dlp", "yt_dlp_ejs")]:
        sys.modules.pop(m, None)
    importlib.invalidate_caches()


def _ytdlp_version():
    try:
        from importlib import metadata
        return metadata.version("yt-dlp")
    except Exception:
        return None


def _tail(text, n=200, k=1):
    """오류 출력의 마지막 k 줄 (n 글자까지) — pip 는 마지막 줄이 'Check the permissions.' 처럼 원인이 아닌 경우가 있음."""
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    return " / ".join(lines[-k:])[-n:] if lines else ""


def _stamp(name, **kw):
    """ENGINE_HOME/<name>.json 기록 고치기 (값이 None 이면 그 항목을 지움)."""
    p = ENGINE_HOME / f"{name}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    d.update(kw)
    try:
        ENGINE_HOME.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({k: v for k, v in d.items() if v is not None}), encoding="utf-8")
    except OSError:
        pass


def _since(name, key):
    """기록된 시각 뒤 지난 초 (기록이 없거나 이상하면 아주 큼)."""
    try:
        age = time.time() - float(json.loads((ENGINE_HOME / f"{name}.json").read_text(encoding="utf-8"))[key])
    except (OSError, ValueError, KeyError, TypeError):
        return float("inf")
    return age if age >= 0 else float("inf")


def engine_age():
    """마지막으로 엔진을 최신으로 바꾼 뒤 지난 초."""
    return _since("engine_upgrade", "t")


def update_engine(log, deno=True):
    """다운로드 엔진(yt-dlp + YouTube 해석 부품)을 최신으로 바꾸고 새 버전을 다시 읽음. 성공하면 True.
    잠금은 pip 동안만 (Deno 는 그 밖에서 확인·설치). 휴대폰에서 시킨 작업 안에서는 하지 않고 False."""
    if not self_update_allowed():
        log(REMOTE_NO_UPDATE_MSG)
        return False
    with _ENGINE_LOCK:
        before = _ytdlp_version()
        log("다운로드 엔진을 최신으로 바꾸는 중")
        r = run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-U", ENGINE_PKG])
        _forget_ytdlp()
        ok = r.returncode == 0
        if ok:
            after = _ytdlp_version()
            _stamp("engine_upgrade", t=time.time(), version=after, t_fail=None)
            old_py = _engine_needs_newer_python(after)
            ENGINE_NOTE["old_python"] = old_py
            if old_py:  # pip 는 이 Python 에 맞는 예전 버전에서 멈추고도 '이미 최신'처럼 끝남 → YouTube 가 바뀌면 계속 막힘
                log(f"  완료 · {after} · {old_py}")
                updater._NOTICES.append({"text": old_py, "warn": True})
            else:
                log(f"  완료 · {after or '최신'}" + (" (이미 최신이에요)" if after and after == before else ""))
        else:
            _stamp("engine_upgrade", t_fail=time.time())
            log(f"  최신으로 바꾸지 못했어요 · 인터넷 연결을 확인해 주세요 · {_tail(r.stderr)}")
    if deno:
        ensure_deno(log)
    return ok


PY_RECOMMENDED = "3.13"
ENGINE_NOTE = {}  # 엔진을 최신으로 못 바꾸는 이유 (old_python: Python 이 오래됨) — 막혔을 때 '크롬 로그인'보다 먼저 알림


def _old_python_hint(log):
    if ENGINE_NOTE.get("old_python"):
        log(f"  {ENGINE_NOTE['old_python']}")


def python_too_old_msg(need=None):
    """이 PC 의 Python 이 너무 오래돼 최신 구성요소를 받을 수 없을 때 안내 (무엇을 하면 되는지까지)."""
    have = f"{sys.version_info[0]}.{sys.version_info[1]}"
    return (f"이 PC의 Python({have})이 오래돼서 최신 다운로드 엔진을 받을 수 없어요"
            + (f" (Python {need} 이상 필요)" if need else "")
            + f". python.org 에서 Python {PY_RECOMMENDED}을 설치한 뒤 앱 폴더의 '시작하기 (Windows).bat'을 한 번 실행해 주세요"
            " (작업 파일은 그대로예요)")


def _engine_needs_newer_python(installed):
    """PyPI 의 최신 yt-dlp 가 이 Python 을 더 이상 받지 않아 설치된 것이 그보다 예전이면 안내 문구 · 아니면 None.
    (확인 못 하면 None — 인터넷이 안 되면 그냥 넘어감). yt-dlp 는 Python 지원이 끝날 무렵(3.N 은 2016+N 년 10월) 그 버전을
    빼므로, 끝나기 석 달 전부터만 물어봄 (새 Python 이면 PyPI 에 따로 묻지 않음)."""
    if time.strftime("%Y-%m") < f"{2016 + sys.version_info[1]}-07":
        return None
    try:
        info = json.loads(_fetch_text("https://pypi.org/pypi/yt-dlp/json"))["info"]
        latest, req = str(info.get("version") or ""), str(info.get("requires_python") or "")
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r">=\s*(\d+)\.(\d+)", req)
    if not m or not installed or installed == latest or sys.version_info[:2] >= (int(m[1]), int(m[2])):
        return None
    return python_too_old_msg(f"{m[1]}.{m[2]}")


def engine_autoupdate(log):
    """앱을 켤 때 뒤에서: 3일이 지났으면 엔진을 최신으로, Deno 가 없으면 설치. 실패했으면 하루 동안은 다시 하지 않음.
    화면 진행률은 건드리지 않음 (그사이 시작한 작업의 진행률을 덮지 않게)."""
    try:
        if engine_age() > ENGINE_EVERY and _since("engine_upgrade", "t_fail") > ENGINE_RETRY:
            update_engine(log, deno=False)
        ensure_deno(log, install=_since("deno_install", "t_fail") > ENGINE_RETRY, show=False)
    except Exception as e:
        log(f"다운로드 엔진 확인을 건너뛰었어요 · {e}")


def _deno_ok(path):
    if not path or not os.path.isfile(path):
        return False
    try:
        r = subprocess.run([str(path), "--version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.search(r"deno (\d+)\.(\d+)\.(\d+)", r.stdout or "")
    return bool(m) and tuple(int(x) for x in m.groups()) >= DENO_MIN


def _use_deno(path):
    """찾은 Deno 를 yt-dlp 가 쓰게: 경로를 기억하고 이 프로그램의 PATH 맨 앞에 넣음."""
    global _DENO
    _DENO = str(path)
    d = os.path.dirname(_DENO)
    if d not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    return _DENO


def _fetch_text(url):
    req = urllib.request.Request(url, headers=updater.UA)
    with updater.urlopen(req, 20) as r:
        return r.read().decode("utf-8", "replace")


def _deno_urls():
    yield DENO_ZIP
    try:  # GitHub 이 안 되면 Deno 공식 배포 서버
        yield DENO_ZIP_ALT.format(ver=_fetch_text("https://dl.deno.land/release-latest.txt").strip())
    except updater.NET_ERRORS:
        return


def _install_deno(dest, progress=None):
    """공식 Windows 용 deno.exe 를 받아 확인(sha256·실행)한 뒤 dest 에 넣음."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    err = None
    for url in _deno_urls():
        try:
            with tempfile.TemporaryDirectory(dir=ENGINE_HOME, ignore_cleanup_errors=True) as td:
                zp = Path(td) / "deno.zip"
                updater.download(url, zp, progress, timeout=60)
                want = re.search(r"\b[0-9a-fA-F]{64}\b", _fetch_text(url + ".sha256sum"))
                if not want or updater.sha256(zp) != want.group(0).lower():
                    raise RuntimeError("받은 파일 확인(sha256)에 실패했어요")
                new = Path(td) / dest.name
                with zipfile.ZipFile(zp) as z:
                    new.write_bytes(z.read("deno.exe"))
                if sys.platform != "win32":
                    new.chmod(0o755)
                if not _deno_ok(new):
                    raise RuntimeError("받은 Deno가 실행되지 않아요")
                updater._replace(new, dest)
                return
        except Exception as e:
            err = e
    raise RuntimeError(updater._why(err) if isinstance(err, updater.NET_ERRORS) else str(err))


def _deno_home():
    return ENGINE_HOME / "bin" / ("deno.exe" if sys.platform == "win32" else "deno")


def _find_deno():
    if _DENO and os.path.isfile(_DENO):
        return _DENO
    for cand in (_deno_home(), shutil.which("deno")):
        if _deno_ok(cand):
            return _use_deno(cand)
    return None


def _deno_progress(show):
    def cb(got, total):
        pct = min(99, int(got * 100 / total)) if total else None
        _DENO_PROG.update(pct=pct, detail=f"{got / 1e6:.0f}MB" + (f" / {total / 1e6:.0f}MB" if total else "") + " · 처음 한 번만 받아요")
        if show:
            set_progress(label="YouTube 해석 도구 받는 중", pct=pct, detail=_DENO_PROG["detail"])
    return cb


def ensure_deno(log, install=True, show=True):
    """YouTube 해석에 쓰는 자바스크립트 실행기(Deno)를 찾고, 없으면 (Windows) 이 사용자 폴더에 설치.
    실패해도 멈추지 않고 None (영상 일부가 안 받아질 수 있음). 다른 쪽이 설치하는 중이면 끝날 때까지 기다렸다 그 결과를 씀.
    show: 받는 진행률을 화면에 (작업 안에서 부를 때만). 휴대폰에서 시킨 작업 안에서는 찾기만 (설치 안 함)."""
    install = install and self_update_allowed()
    found = _find_deno()
    if found or not (install and DENO_AUTO):
        return found
    while not _DENO_LOCK.acquire(timeout=0.5):
        if show:
            set_progress(label="YouTube 해석 도구 받는 중", pct=_DENO_PROG.get("pct"),
                         detail=_DENO_PROG.get("detail") or "처음 한 번만 받아요 · 잠시만 기다려 주세요")
    try:
        found = _find_deno()  # 기다리는 동안 다른 쪽이 설치했으면 그대로 씀
        if found:
            return found
        log("YouTube 해석 도구(Deno)를 설치하는 중이에요 · 처음 한 번만, 1~2분 걸려요")
        _DENO_PROG.clear()
        try:
            _install_deno(_deno_home(), _deno_progress(show))
        except Exception as e:
            _stamp("deno_install", t_fail=time.time())
            log(f"  YouTube 해석 도구(Deno)를 설치하지 못했어요 · 일부 영상이 안 받아질 수 있어요. "
                f"인터넷 연결을 확인한 뒤 왼쪽 아래 '업데이트 확인'을 눌러 주세요 ({e})")
            return None
        _stamp("deno_install", t_fail=None)
        log("  YouTube 해석 도구 설치 완료")
        return _use_deno(_deno_home())
    finally:
        _DENO_PROG.clear()
        _DENO_LOCK.release()


# ---------- 업데이트 (실제 설치·되돌리기는 updater.py) ----------

def _newer(a, b):
    return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))


def _skip_note(ver):
    return f"새 버전(v{ver})이 이 PC에서 열리지 않아 이전 버전(v{VERSION})을 쓰고 있어요. 관리자에게 알려 주세요."


def check_update():
    out = {"current": VERSION, "available": False}
    n = updater.take_notice()  # 방금 업데이트·되돌림 결과 → 화면에 한 번 알림
    if n:
        out.update(notice=n["text"], notice_warn=n["warn"])
    url = CONFIG.get("update_manifest_url") or DEFAULT_MANIFEST
    if not url:
        return dict(out, note="업데이트 주소가 설정되지 않았어요")
    try:
        m, _ = updater.fetch_manifest(url)
    except updater.UpdateError as e:
        return dict(out, note=str(e))
    out.update(latest=m["version"], available=_newer(m["version"], VERSION), notes=m.get("notes", ""), zip=m.get("zip"))
    if out["available"] and updater.skipped(APP_DIR) == m["version"]:  # 되돌렸던 그 버전은 더 새 버전이 나올 때까지 권하지 않음
        out.update(available=False, note=_skip_note(m["version"]))
    return out


def update_app(log):
    """새 버전을 확인하며 설치 (받기 → 검사 → 되돌리기 복사본 → 교체). 다시 시작해야 하면 True."""
    url = CONFIG.get("update_manifest_url") or DEFAULT_MANIFEST
    if not url:
        log("프로그램 업데이트 건너뜀 · 업데이트 주소가 설정되지 않았어요")
        return False
    m, raw = updater.fetch_manifest(url)
    if not _newer(m["version"], VERSION):
        log(f"이미 최신 버전이에요 (v{VERSION})")
        return False
    if updater.skipped(APP_DIR) == m["version"]:
        log(f"프로그램 업데이트 건너뜀 · {_skip_note(m['version'])}")
        return False
    log(f"프로그램 업데이트 · v{VERSION} → v{m['version']}")

    def prog(got, total):
        set_progress(label="업데이트 받는 중", pct=min(99, int(got * 100 / total)) if total else None,
                     detail=f"{got / 1e6:.1f}MB" + (f" / {total / 1e6:.1f}MB" if total else ""))

    res = updater.download_and_install(m, raw, APP_DIR, log, prog)
    if res["req_changed"]:  # 구성요소 목록이 바뀐 버전만 pip (안 바뀌면 건너뜀)
        set_progress(label="업데이트 설치 중", pct=None, detail="새 구성요소를 설치하는 중이에요 · 몇 분 걸릴 수 있어요")
        log("  새 구성요소를 설치하는 중이에요 (몇 분 걸릴 수 있어요)")
        _pip_self_upgrade()
        r = run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(APP_DIR / "requirements.txt")])
        if r.returncode and _pip_locked(r.stderr):
            # 켜져 있는 앱이 불러 둔 .pyd/.dll(numpy·onnxruntime 등)은 Windows 에서 바꿀 수 없음 → 다시 켤 때 실행기가
            # 아무것도 불러오기 전에 마저 설치 (updater._deferred_pip · 실패하면 그때 이전 버전으로 되돌림)
            updater._write_json(APP_DIR / updater.REQ_PENDING, {"to": m["version"], "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            log("  켜져 있는 프로그램이 쓰는 구성요소가 있어서, 다시 켤 때 마저 설치해요 (창이 1~2분 늦게 열릴 수 있어요)")
        elif r.returncode:
            updater.mark_requirements(APP_DIR, ok=False)
            back = updater.rollback(APP_DIR, log)
            raise RuntimeError("새 버전에 필요한 구성요소를 설치하지 못해 업데이트를 취소했어요"
                               + (" (이전 버전 그대로예요)" if back else " (프로그램을 껐다 켜면 이전 버전으로 돌아가요)")
                               + f". {_pip_advice(r.stderr)} · {_tail(r.stderr, 300, 2)}")
        else:
            updater.mark_requirements(APP_DIR)
    log("  완료 · 프로그램을 다시 시작해요")
    return True


PIP_MIN = (23, 3)  # 이보다 예전 pip 는 Windows 에서 켜져 있는 .pyd 를 못 지우면 설치 전체를 실패로 끝냄 (23.3 부터 경고만)


def _pip_self_upgrade():
    """venv 의 pip 가 PIP_MIN 보다 예전이면 먼저 올림 (Python 3.10 은 pip 23.0 이 들어 있음 · Windows 만). 실패해도 계속."""
    if sys.platform != "win32":
        return
    try:
        from importlib import metadata
        ver = tuple(int(x) for x in re.findall(r"\d+", metadata.version("pip"))[:2])
    except Exception:  # noqa: BLE001
        return
    if ver >= PIP_MIN:
        return
    run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", f"pip>={PIP_MIN[0]}.{PIP_MIN[1]}"])


_PIP_LOCK_RE = re.compile(r"WinError (5|32)\b|Access is denied|액세스가 거부|다른 프로세스가 파일을 사용 중|being used by another process"
                          r"|Check the permissions", re.I)


def _pip_locked(stderr):
    """pip 실패가 '파일을 다른 프로그램(=켜져 있는 이 앱)이 쓰는 중' 때문인지 (Windows)."""
    return sys.platform == "win32" and bool(_PIP_LOCK_RE.search(stderr or ""))


def _pip_advice(stderr):
    """pip 실패 이유에 맞는 다음 할 일 (예전: 무엇이든 '인터넷 연결 확인')."""
    t = stderr or ""
    if re.search(r"Requires-Python|requires a different Python|No matching distribution found", t):
        return python_too_old_msg()
    if re.search(r"No space left|WinError 112|디스크 공간이 부족", t):
        return "저장 공간이 부족해요. 공간을 비운 뒤 다시 시도해 주세요"
    return "인터넷 연결을 확인하고 다시 시도해 주세요"
