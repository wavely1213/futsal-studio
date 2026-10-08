"""풋살사관학교 스튜디오 — 안전한 업데이트·실행기.

표준 라이브러리만 씀: 새 버전이 망가져도 이 파일은 돌아가야 해요.
  python updater.py --launch     방금 업데이트했으면 새 버전이 열리는지 확인 (안 되면 이전 버전으로 되돌림) → 이 프로세스에서 앱 실행
  python updater.py --selftest   (업데이트 설치 전에) 이 실행기가 이 PC의 Python 에서 끝까지 돌아가는지 임시 폴더에서 확인

업데이트 순서 (core.update_app 이 부름):
  받기(임시 폴더) → zip 검사 → .update_staging 에 풀기 → 버전·파일 지문(sha256) 확인 → 새 파일 문법·새 실행기 확인
  → 바뀔 파일을 .rollback/v<이전 버전>/ 에 복사 → 하나씩 교체(Windows 잠금 대비 재시도) → 없어진 파일 지우기
  → .update_pending 표시 → (core) 구성요소 목록이 바뀌었을 때만 pip
"""
import contextlib
import hashlib
import http.client
import json
import os
import re
import runpy
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

APP_DIR = Path(__file__).resolve().parent
STAGING, ROLLBACK, PENDING, RESULT, REQ_HASH = ".update_staging", ".rollback", ".update_pending", ".update_result", ".req_hash"
SKIP = ".update_skip"  # 이 PC에서 열리지 않아 되돌린 버전 (더 새 버전이 나올 때까지 다시 권하지 않음)
REQ_PENDING = ".req_pending"  # 켜져 있는 앱이 쓰는 파일(.pyd·.dll) 때문에 못 한 구성요소 설치 → 다음 실행 때 앱보다 먼저
LAUNCH_LOCK = ".launch_lock"  # 아이콘을 거의 동시에 여러 번 눌러도 확인·되돌리기·구성요소 설치는 한 번에 하나
LOCK_FRESH = 30   # 이 시간(초) 안에 고친 잠금 = 다른 실행기가 아직 쓰는 중 (쓰는 쪽이 5초마다 고침)
PIP_TIMEOUT = 1800
KEEP = {"config.json"}  # 사용자 설정: 덮어쓰지도 지우지도 않음 (없을 때만 넣음)
NEVER = {".venv", "venv", ".git", STAGING, ROLLBACK, "__pycache__"}  # 앱 폴더 안이라도 손대지 않는 폴더
INTERNAL = {PENDING, RESULT, REQ_HASH, SKIP, REQ_PENDING, LAUNCH_LOCK}
IMPORT_CHECK = "import app, core, editor, thumb, style, qa"
GRACE = 60       # 업데이트 직후 첫 실행은 (백신 검사·창 준비로) 오래 걸릴 수 있음 → 그사이 아이콘을 또 눌러도 실패로 세지 않음
MAX_FAILED = 2   # 새 버전이 (GRACE 초가 지나도) 이만큼 안 켜졌으면 다음 실행 때 되돌림
UA = {"User-Agent": "futsal-studio-updater"}
WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if WIN else 0  # CREATE_NO_WINDOW: 검은 창 안 띄움
NET_ERRORS = (OSError, ValueError, http.client.HTTPException)  # 연결 끊김(IncompleteRead 등)·잘못된 주소 포함
_SELFTEST = False  # 시험 중에는 알림 창을 띄우지 않음
_NOTICES = []      # 앱이 켜진 뒤 화면에 한 번 띄울 업데이트 결과 (core.check_update 가 가져감)


class UpdateError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 업데이트 실패 (앱 폴더는 바뀌지 않았거나 되돌려 둠)."""


# ---------- 작은 도구 ----------

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _retry(fn, *a, tries=12):
    """Windows: 백신·탐색기·켜져 있는 프로그램이 파일을 잠깐 잡고 있으면 실패 → 조금 기다렸다 다시."""
    delay = 0.05
    for k in range(tries):
        try:
            return fn(*a)
        except FileNotFoundError:
            raise
        except OSError:
            if k == tries - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


# ---------- 여러 모듈이 같이 쓰는 Windows 대비 도구 (표준 라이브러리만 · core·thumb·editor·style 등이 부름) ----------

REPLACE_SECS = 8.0   # 보통 저장: 백신·OneDrive·탐색기·검색 색인이 막 만든 파일을 잠깐 잡는 동안 기다리는 시간
SETTLE_SECS = 60.0   # 막 만든 큰 영상(내보내기·묶기): Defender·V3·알약이 오래 검사함 (refs.SETTLE_SECS 와 같은 이유)


def replace_retry(src, dst, secs=None):
    """os.replace + Windows 잠금(PermissionError: WinError 5·32)이면 점점 길게 기다리며 다시 (secs 초까지 · 기본 REPLACE_SECS).
    읽기 전용 표시가 붙은 대상은 풀고 다시. 다른 오류(없는 파일·다른 드라이브·디스크 가득)는 바로 올려 보냄."""
    end, delay = time.monotonic() + (REPLACE_SECS if secs is None else secs), 0.05
    while True:
        try:
            return os.replace(src, dst)
        except PermissionError:
            _unlock(dst)
            if time.monotonic() + delay > end:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


def write_atomic(path, data, encoding="utf-8", fsync=False, secs=None, mode=None):
    """임시 파일(프로세스·스레드마다 다른 이름)에 다 쓴 뒤 바꿔 끼움 → 쓰다 꺼지거나 디스크가 차도 예전 파일은 그대로.
    data: 글(str) 또는 바이트. mode: 임시 파일을 처음부터 이 권한으로 만들고 바꿔 끼우기 전에 한 번 더 (비밀 파일은 0o600 — 잠깐이라도
    다른 사용자가 읽을 수 있는 틈이 없게 · Windows 는 읽기 전용 표시만 바뀌므로 무시). 실패하면 임시 파일을 지우고 오류를 그대로 올려 보냄."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}_{threading.get_ident()}.tmp")
    try:
        wb = isinstance(data, bytes)
        # mode: open() 의 opener 로 처음부터 그 권한 — 파일 손잡이는 open() 이 끝까지 맡음 (중간에 실패해도 새지 않음)
        opener = None if mode is None else (lambda p, flags: os.open(p, flags, mode))
        if wb:
            f = open(tmp, "wb", opener=opener)
        else:
            f = open(tmp, "w", encoding=encoding, opener=opener)  # Path.write_text 와 같게 (Windows 는 줄바꿈 \r\n)
        with f:
            f.write(data)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        replace_retry(tmp, path, secs)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def awake_state(on, prev=None):
    """Windows: 이 스레드가 PC 를 깨어 있게 쥠(on → 들어올 때 상태) · 놓음(그 상태 prev 로). 다른 운영체제는 아무 일도 안 함 (None).
    SetThreadExecutionState 를 부르는 곳은 이것 하나 — 앱은 core.keep_awake(core._keep_awake 가 이것), 실행기는 _awake (D-034).
    Windows 는 이 상태를 스레드마다 셈 (어느 스레드든 쥐고 있으면 깨어 있음)."""
    import ctypes
    try:
        f = ctypes.windll.kernel32.SetThreadExecutionState
    except AttributeError:
        return None
    f.restype, f.argtypes = ctypes.c_uint, [ctypes.c_uint]
    if on:
        return f(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) or None
    f(prev if prev and prev & ES_CONTINUOUS else ES_CONTINUOUS)
    return None


@contextlib.contextmanager
def _awake():
    """실행기: 업데이트 마무리(이전 앱 기다리기·미룬 구성요소 설치·새 버전 열리는지 확인) 동안 PC 가 잠들지 않게.
    이전 앱(작업·휴대폰으로 보기 '켜 둔 동안 항상')이 쥐던 것은 그 프로세스가 끝나면 풀리므로 여기서 이어 쥠 (D-034)."""
    prev = awake_state(True)
    try:
        yield
    finally:
        awake_state(False, prev)


def py_env(**extra):
    """파이썬 자식 프로세스(pip·확인 실행)의 환경: 출력을 UTF-8 로 (Windows 3.10~3.14 는 파이프에 cp949 로 써서
    한국어 오류·한글 경로가 '���' 로 깨짐 · PEP 686 이전)."""
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", **extra)


def _ssl_context():
    """Python 3.13+ 의 엄격한 인증서 검사(VERIFY_X509_STRICT)만 끔: 백신 'HTTPS 검사'·회사 프록시의 인증서가
    규격을 조금 벗어나도 3.12 처럼 받게 (인증서 사슬·주소 확인은 그대로)."""
    import ssl
    ctx = ssl.create_default_context()
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict:
        ctx.verify_flags &= ~strict
    return ctx


def urlopen(req, timeout):
    """업데이트·Deno·모델 받기에 쓰는 urlopen (위 인증서 설정으로)."""
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


# ---------- config.json (사람이 메모장으로 고치는 파일) ----------

def _decode_text(raw):
    """메모장 저장 방식: UTF-8(BOM 있든 없든) → 아니면 ANSI(cp949)."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _has_ctrl(d):
    """글자 값 안에 줄바꿈·탭 같은 제어 문자 → 'D:\\new' 처럼 역슬래시를 하나만 써서 잘못 읽힌 것."""
    return isinstance(d, dict) and any(isinstance(v, str) and re.search(r"[\x00-\x1f]", v) for v in d.values())


def loads_tolerant(raw):
    """사람이 고친 JSON 읽기: BOM·ANSI(cp949)·이스케이프 안 한 Windows 경로("D:\\풋살작업", 끝 역슬래시 포함)도.
    읽을 수 없으면 ValueError."""
    text = _decode_text(raw)
    single = re.sub(r'\\\\|\\"|\\', lambda m: m.group(0) if len(m.group(0)) == 2 else "\\\\", text)  # 하나뿐인 \ → \\
    trail = re.sub(r'(?<!\\)\\"(?=\s*[,}\]\r\n])', r'\\\\"', single)  # "D:\풋살\" 처럼 끝이 역슬래시
    err = None
    for cand in (text, single, trail):
        try:
            d = json.loads(cand)
        except ValueError as e:
            err = err or e
            continue
        if not _has_ctrl(d):
            return d
    raise err or ValueError("설정 파일을 읽지 못했어요")


def read_config(app_dir=APP_DIR):
    """config.json → (설정 dict, 안내 문구 또는 None). 파일이 깨졌으면 빈 설정(기본값으로 켬) + 안내."""
    p = Path(app_dir) / "config.json"
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        return {}, f"설정 파일(config.json)을 읽지 못해 기본 설정으로 열었어요 · {e}"
    try:
        d = loads_tolerant(raw)
    except ValueError as e:
        return {}, (f"설정 파일(config.json)의 내용이 올바르지 않아 기본 설정으로 열었어요. "
                    f"메모장으로 고쳤다면 따옴표·쉼표를 확인해 주세요 · {e}")
    if not isinstance(d, dict):
        return {}, "설정 파일(config.json)의 내용이 올바르지 않아 기본 설정으로 열었어요"
    return d, None


def default_workspace():
    return Path.home() / "풋살사관학교_작업"


def _unlock(p):
    """읽기 전용 표시가 붙은 파일은 Windows 에서 바꾸거나 지울 수 없음."""
    try:
        if os.path.exists(p) and not os.access(p, os.W_OK):
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _replace(src, dst):
    def go():
        try:
            os.replace(src, dst)
        except PermissionError:
            _unlock(dst)
            raise
    _retry(go)


def _remove(p):
    def go():
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        except PermissionError:
            _unlock(p)
            raise
    _retry(go)


def _rmtree(p):
    p = Path(p)
    if not p.exists():
        return

    def onerr(fn, path, _exc):
        _unlock(path)
        try:
            fn(path)
        except OSError:
            pass
    for _ in range(3):
        shutil.rmtree(p, onerror=onerr)
        if not p.exists():
            return
        time.sleep(0.3)


def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(p, obj):
    p = Path(p)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")  # 아이콘을 거의 동시에 두 번 눌러도 서로 덮지 않게
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    _replace(tmp, p)


def _read_version(app_dir):
    try:
        return (Path(app_dir) / "version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_manifest(app_dir=APP_DIR):
    m = _read_json(Path(app_dir) / "manifest.json")
    return m if isinstance(m, dict) else {}


def _rel(name):
    """목록·압축 속 경로를 안전한 상대 경로로 ('C:\\', '..', 절대 경로, 끝이 공백·점인 이름은 거절)."""
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\0" in name:
        return None
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(x in (".", "..") or x != x.rstrip(" .") for x in p.parts):
        return None
    return p.as_posix()


def workspace(app_dir=APP_DIR):
    """core.WORK 와 같은 규칙 (core 를 불러오지 않고 계산 — 새 버전이 망가졌을 때도 기록을 남기려고).
    설정한 폴더를 쓸 수 없으면(빠진 외장 드라이브 등) core 처럼 기본 작업 폴더."""
    want = configured_workspace(app_dir)
    if want == default_workspace() or _usable_dir(want):
        return want
    return default_workspace()


def configured_workspace(app_dir=APP_DIR):
    cfg, _ = read_config(app_dir)
    ws = cfg.get("workspace")
    return Path(ws).expanduser() if isinstance(ws, str) and ws.strip() else default_workspace()


def _usable_dir(p):
    """폴더가 있거나 만들 수 있는지 (만들어 봄)."""
    try:
        Path(p).mkdir(parents=True, exist_ok=True)
        return Path(p).is_dir()
    except (OSError, ValueError):
        return False


def _protected(rel, app_dir):
    parts = PurePosixPath(rel).parts
    if rel in KEEP or rel in INTERNAL or parts[0] in NEVER:
        return True
    try:  # 작업 폴더를 앱 폴더 안에 둔 경우 그 안은 사용자 데이터
        ws = configured_workspace(app_dir).resolve()
        target = (Path(app_dir) / rel).resolve()
        return ws == target or ws in target.parents
    except OSError:
        return False


def studio_log(app_dir, msg):
    """작업 폴더의 studio.log 에 남김 (앱이 안 켜져도 볼 수 있게). 못 쓰는 글자(반쪽 이모지 등)는 대신 표시."""
    try:
        ws = workspace(app_dir)
        ws.mkdir(parents=True, exist_ok=True)
        with open(ws / "studio.log", "a", encoding="utf-8", errors="replace") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")  # studiolog.stamp 와 같은 꼴 (연도 포함)
    except (OSError, ValueError):
        pass


# ---------- 받기 ----------

def fetch_manifest(url, timeout=15):
    """업데이트 안내(manifest.json) → (내용, 받은 그대로의 바이트)."""
    try:
        req = urllib.request.Request(url, headers={**UA, "Cache-Control": "no-cache"})
        with urlopen(req, timeout) as r:
            raw = r.read()
    except NET_ERRORS as e:
        raise UpdateError(f"업데이트 정보를 받지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요 · {_why(e)}")
    try:
        m = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        raise UpdateError("업데이트 정보를 읽지 못했어요. 잠시 뒤 다시 시도해 주세요.")
    if not isinstance(m, dict) or not re.fullmatch(r"\d+(\.\d+)*", str(m.get("version") or "")):
        raise UpdateError("업데이트 정보가 올바르지 않아요. 잠시 뒤 다시 시도해 주세요.")
    return m, raw


RESUME_SUFFIX = ".resume"  # 받다 만 파일 옆 기록: 주소·전체 길이·ETag/Last-Modified (같은 파일을 이어받는지 확인)
_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


def _resume_meta(dest):
    return Path(str(dest) + RESUME_SUFFIX)


def _read_resume(dest, url):
    """이어받을 수 있으면 (이미 받은 바이트 수, 기록) · 아니면 (0, None)."""
    try:
        meta = json.loads(_resume_meta(dest).read_text(encoding="utf-8"))
        have = Path(dest).stat().st_size
    except (OSError, ValueError):
        return 0, None
    if not isinstance(meta, dict) or meta.get("url") != url or not have or not isinstance(meta.get("total"), int) or have >= meta["total"]:
        return 0, None
    return have, meta


def _drop_resume(dest):
    try:
        _resume_meta(dest).unlink()
    except OSError:
        pass


def _same_file(meta, headers):
    """이어받는 206 응답이 처음 받던 그 파일인지: ETag(있으면) 또는 Last-Modified 가 기록과 같아야 함
    (If-Range 를 무시하고 바뀐 파일의 뒷부분을 주는 CDN 이 있음 · 크기가 같으면 길이 확인으로는 못 거름)."""
    if meta.get("etag"):
        return headers.get("ETag") == meta["etag"]
    return bool(meta.get("modified")) and headers.get("Last-Modified") == meta["modified"]


def download(url, dest, progress=None, timeout=30, resume=False):
    """url → dest. 서버가 알려 준 길이(Content-Length)만큼 다 받지 못하면 IncompleteRead (반쪽 파일을 완성본으로 쓰지 않게 —
    http.client 는 길이를 정해 읽을 때 연결이 먼저 끊기면 오류 없이 빈 조각을 돌려줌).
    resume: 받다 끊겨 dest 에 남은 파일과 옆 기록(<dest>.resume)이 같은 주소면 그 자리부터 이어받음 (Range · If-Range 로
    서버 파일이 그대로일 때만) — 서버가 206 으로 그 자리부터 주면 이어 붙이고, 200 이면(바뀜·이어받기 안 됨) 처음부터.
    끊기면 dest 와 기록을 남겨 다음에 이어받음 · 다 받으면 기록을 지움. progress(받은 바이트, 전체 바이트)."""
    have, meta = _read_resume(dest, url) if resume else (0, None)
    if have and not (meta.get("etag") or meta.get("modified")):  # 서버 파일이 그대로인지 확인할 표(ETag·Last-Modified)가 없음 → 처음부터
        have, meta = 0, None
    headers = dict(UA)
    if have:
        headers["Range"] = f"bytes={have}-"
        headers["If-Range"] = meta.get("etag") or meta.get("modified")
    req = urllib.request.Request(url, headers=headers)
    try:
        r = urlopen(req, timeout)
    except urllib.error.HTTPError as e:
        if not (have and e.code == 416):  # 이어받을 자리가 서버 파일보다 뒤 → 처음부터
            raise
        e.close()
        _drop_resume(dest)
        return download(url, dest, progress, timeout, resume)
    with r:
        m = _RANGE.match(r.headers.get("Content-Range") or "")
        if have and r.status == 206 and m and int(m[1]) == have and int(m[3]) == meta["total"] and _same_file(meta, r.headers):
            got, total, mode = have, meta["total"], "ab"
        else:
            if have and r.status == 206:  # 엉뚱한 구간·바뀐 파일(If-Range 를 모르는 서버) → 처음부터 다시 (이어 붙이면 두 판이 섞인 깨진 파일)
                _drop_resume(dest)
                return download(url, dest, progress, timeout, resume)
            got, total, mode = 0, int(r.headers.get("Content-Length") or 0), "wb"
            if resume and total:
                try:
                    write_atomic(_resume_meta(dest), json.dumps({"url": url, "total": total, "etag": r.headers.get("ETag"),
                                                                "modified": r.headers.get("Last-Modified")}))
                except OSError:  # 기록을 못 쓰면 이어받기만 못 함
                    pass
        with open(dest, mode) as f:
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
    if total and got != total:
        raise http.client.IncompleteRead(b"", total - got)
    if resume:
        _drop_resume(dest)
    return dest


def download_and_install(manifest, raw=None, app_dir=APP_DIR, log=print, progress=None):
    url = str(manifest.get("zip") or "")
    if not re.match(r"https?://", url):
        raise UpdateError("업데이트 파일 주소가 없어요. 관리자에게 알려 주세요.")
    with tempfile.TemporaryDirectory(prefix="futsal-update-", ignore_cleanup_errors=True) as td:
        zpath = Path(td) / "app.zip"
        log("  새 버전 받는 중")
        try:
            download(url, zpath, progress)
        except NET_ERRORS as e:
            raise UpdateError(f"새 버전을 받지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요 · {_why(e)}")
        return install(manifest, zpath, app_dir, log, raw)


def _why(e):
    """오류를 짧게 (urlopen 오류는 안쪽 이유만, 받다 끊긴 것은 알아볼 수 있게)."""
    if isinstance(e, http.client.IncompleteRead):
        return "받는 도중 연결이 끊겼어요"
    why = str(getattr(e, "reason", None) or e or type(e).__name__)
    if "CERTIFICATE_VERIFY_FAILED" in why:  # 백신의 HTTPS 검사·회사 프록시가 연결을 가로챔
        why += " (백신 프로그램의 'HTTPS 검사'·'웹 보호'를 잠시 끄고 다시 해 보세요)"
    return why


# ---------- 설치 ----------

def _zip_name(info):
    n = info.filename
    if not info.flag_bits & 0x800:  # UTF-8 표시가 없는 압축: 한글 이름이 깨져 보일 수 있음
        try:
            n = n.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return n


def _extract(zpath, staging):
    """zip 을 staging 에 풀기. 맨 위 폴더 하나(futsal-studio-main/)는 벗김. {상대 경로: 풀린 파일}"""
    out = {}
    with zipfile.ZipFile(zpath) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        names = [_zip_name(i) for i in infos]
        tops = {n.split("/", 1)[0] for n in names}
        root = tops.pop() + "/" if len(tops) == 1 and all("/" in n for n in names) else ""
        for info, n in zip(infos, names):
            rel = _rel(n[len(root):])
            if rel is None or (info.external_attr >> 16) & 0o170000 == 0o120000:  # 이상한 경로·링크는 건너뜀
                continue
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dst, "wb") as f:
                shutil.copyfileobj(src, f, 1 << 20)
            out[rel] = dst
    return out


def _safe_ver(v):
    return re.sub(r"[^0-9A-Za-z._-]", "_", v or "") or "unknown"


def _check_files(files):
    if not isinstance(files, dict) or not files:
        raise UpdateError("업데이트 정보의 파일 목록이 올바르지 않아요. 관리자에게 알려 주세요.")
    out = {}
    for k, v in files.items():
        rel = _rel(k)
        if rel is None or not isinstance(v, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", v):
            raise UpdateError(f"업데이트 정보의 파일 목록이 올바르지 않아요 ({k}). 관리자에게 알려 주세요.")
        out[rel] = v.lower()
    return out


def _verify_staged(staging, wanted, log):
    """설치 전에 새 버전이 이 PC의 Python 에서 돌아가는지: 모든 .py 문법 + 새 실행기(updater.py) 시험 실행.
    실행기는 바탕화면 아이콘·다시 시작이 모두 거치는 문이라, 망가지면 앱이 아예 안 켜지고 되돌릴 수도 없음."""
    def bad(detail):
        return UpdateError(f"새 버전 파일에 문제가 있어서 업데이트를 멈췄어요 (지금 버전은 그대로예요). "
                           f"관리자에게 알려 주세요 · {detail}")
    if "updater.py" not in wanted:
        raise bad("updater.py 가 없어요")
    log("  새 버전이 이 PC에서 열리는지 확인 중")
    for rel in wanted:
        if rel.endswith(".py"):
            try:
                compile((staging / rel).read_bytes(), rel, "exec", dont_inherit=True)
            except (SyntaxError, ValueError) as e:
                raise bad(f"{rel} {getattr(e, 'lineno', '') or ''}번째 줄 · {type(e).__name__}: {getattr(e, 'msg', e)}")
    try:
        r = subprocess.run([_console_python(), str(staging / "updater.py"), "--selftest"], cwd=str(staging),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                           env=py_env(), creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise bad(f"새 실행기 시험 실패 · {e}")
    if r.returncode != 0:
        lines = [x for x in (r.stderr or r.stdout or "").strip().splitlines() if x.strip()]
        raise bad(f"새 실행기 시험 실패 · {lines[-1] if lines else f'종료 코드 {r.returncode}'}")


def install(manifest, zpath, app_dir=APP_DIR, log=print, raw=None):
    """받은 zip 을 검사한 뒤 앱 폴더에 적용. 문제가 있으면 앱 폴더는 그대로 두고 UpdateError.
    manifest 에 "files" 가 없으면 (예전 형식) 지문 검사·지우기만 건너뛰고 나머지는 똑같이 안전하게."""
    app_dir = Path(app_dir)
    new_ver = str(manifest.get("version") or "").strip()
    files = _check_files(manifest["files"]) if "files" in manifest else None
    p = _read_json(app_dir / PENDING)
    if isinstance(p, dict) and p.get("state") != "installed":  # 지난번에 끊긴 업데이트부터 되돌림
        rollback(app_dir, log)
        if (app_dir / PENDING).exists():
            raise UpdateError("지난번 업데이트를 정리하지 못했어요. 프로그램을 껐다 켠 뒤 다시 시도해 주세요.")
        p = None
    old_ver = _read_version(app_dir)  # 되돌린 뒤에 읽어야 진짜 이전 버전
    if isinstance(p, dict) and p.get("to") == new_ver == old_ver:
        log("  이미 받아 둔 새 버전이에요 · 다시 시작하면 바뀌어요")  # 다시 시작 전에 또 누른 경우 (되돌리기 기록 보존)
        return {"from": p.get("from"), "to": new_ver, "changed": [], "removed": [], "req_changed": False}
    try:
        with zipfile.ZipFile(zpath) as z:
            bad = z.testzip()
    except (zipfile.BadZipFile, OSError, EOFError) as e:
        raise UpdateError(f"받은 업데이트 파일이 깨졌어요. 다시 시도해 주세요 · {e}")
    if bad:
        raise UpdateError(f"받은 업데이트 파일이 깨졌어요 ({bad}). 다시 시도해 주세요.")

    staging = app_dir / STAGING
    _rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)  # 잠깐 잡힌 파일 때문에 다 못 지웠어도 (남은 파일은 목록에 없으면 쓰지 않음)
    try:
        log("  받은 파일 확인 중")
        staged = _extract(zpath, staging)
        sv = _read_version(staging)
        if sv != new_ver:
            raise UpdateError(f"받은 프로그램 버전(v{sv or '?'})이 안내된 버전(v{new_ver})과 달라요. "
                              "방금 새 버전이 올라오는 중일 수 있어요. 몇 분 뒤 다시 시도해 주세요.")
        if files is not None:
            for rel, h in files.items():
                if rel not in staged or sha256(staged[rel]) != h:
                    raise UpdateError(f"받은 파일이 배포 목록과 달라요 ({rel}). 새 버전이 올라오는 중일 수 있어요. "
                                      "몇 분 뒤 다시 시도해 주세요. 계속되면 관리자에게 알려 주세요.")
            wanted = sorted(files)
        else:
            wanted = sorted(r for r in staged if r != "manifest.json" and not r.startswith("tests/"))
        _verify_staged(staging, wanted, log)
        # 다음 업데이트 때 '없어진 파일'을 알 수 있게, 확인에 쓴 안내를 그대로 함께 넣음
        (staging / "manifest.json").write_bytes(raw if raw else json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        wanted.append("manifest.json")

        plan = []
        for rel in dict.fromkeys(wanted):
            dst = app_dir / rel
            if _protected(rel, app_dir):
                if rel in KEEP and not dst.exists():  # 설정 파일이 아예 없을 때만 넣음
                    plan.append(rel)
                continue
            if dst.is_file() and sha256(dst) == sha256(staging / rel):
                continue
            plan.append(rel)
        removed = []
        old_files = read_manifest(app_dir).get("files")
        if files is not None and isinstance(old_files, dict):
            keep = {r.lower() for r in files} | {"manifest.json"}  # Windows 는 대소문자만 바뀐 이름도 같은 파일
            for k in old_files:
                rel = _rel(k)
                if rel and rel.lower() not in keep and not _protected(rel, app_dir) and (app_dir / rel).is_file():
                    removed.append(rel)

        req = app_dir / "requirements.txt"
        req_base = _req_recorded(app_dir) or (sha256(req) if req.is_file() else "")

        # 되돌리기용 복사본 + 기록 (바꾸는 도중 꺼져도 다음 실행 때 updater 가 되돌림)
        rb = app_dir / ROLLBACK / f"v{_safe_ver(old_ver)}"
        _rmtree(rb)
        (rb / "files").mkdir(parents=True, exist_ok=True)
        backup, added = [], []
        for rel in plan + removed:
            src = app_dir / rel
            if src.is_file():
                (rb / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, rb / "files" / rel)
                backup.append(rel)
            else:
                added.append(rel)
        _write_json(rb / "rollback.json", {"from": old_ver, "to": new_ver, "backup": backup, "added": added,
                                           "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        pending = {"from": old_ver, "to": new_ver, "state": "applying", "rollback": rb.name}
        _write_json(app_dir / PENDING, pending)

        log(f"  파일 바꾸는 중 · {len(plan)}개" + (f", 지울 파일 {len(removed)}개" if removed else ""))
        try:
            for rel in plan:
                dst = app_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                _replace(staging / rel, dst)
            for rel in removed:
                _remove(app_dir / rel)
                _prune_empty_dirs(app_dir, rel)
        except Exception as e:
            log(f"  파일을 바꾸다 문제가 생겼어요 · {e}")
            if rollback(app_dir, log):
                raise UpdateError(f"파일을 바꾸지 못해 업데이트를 취소했어요 (이전 버전 그대로예요). "
                                  f"다른 프로그램이 앱 파일을 열고 있으면 닫고 다시 시도해 주세요 · {e}")
            raise UpdateError(f"파일을 바꾸지 못했어요. 프로그램을 껐다 켜면 이전 버전으로 되돌려요 · {e}")
        _write_json(app_dir / PENDING, dict(pending, state="installed"))
        _prune_rollbacks(app_dir, keep=rb.name)
    finally:
        _rmtree(staging)

    req_now = sha256(req) if req.is_file() else ""
    if req_now == req_base and not (app_dir / REQ_HASH).exists():
        (app_dir / REQ_HASH).write_text(req_now, encoding="utf-8")
    return {"from": old_ver, "to": new_ver, "changed": plan, "removed": removed, "req_changed": req_now != req_base}


def _prune_empty_dirs(app_dir, rel):
    d = (Path(app_dir) / rel).parent
    while d != Path(app_dir) and Path(app_dir) in d.parents:
        try:
            d.rmdir()  # 비어 있을 때만 지워짐
        except OSError:
            return
        d = d.parent


def _prune_rollbacks(app_dir, keep, n=3):
    """되돌리기 복사본은 최근 몇 개만 (앱 파일은 작아서 공간 문제는 거의 없음)."""
    root = Path(app_dir) / ROLLBACK
    dirs = sorted((d for d in root.iterdir() if d.is_dir() and d.name != keep), key=lambda d: d.stat().st_mtime, reverse=True)
    for d in dirs[n - 1:]:
        _rmtree(d)


def _req_recorded(app_dir):
    try:
        return (Path(app_dir) / REQ_HASH).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def mark_requirements(app_dir=APP_DIR, ok=True):
    """pip 설치가 끝난 구성요소 목록의 지문을 기억 (실패면 다음 업데이트 때 다시 설치하게)."""
    req = Path(app_dir) / "requirements.txt"
    (Path(app_dir) / REQ_HASH).write_text(sha256(req) if ok and req.is_file() else "failed", encoding="utf-8")


# ---------- 되돌리기 · 실행 ----------

def rollback(app_dir=APP_DIR, log=print):
    """.rollback 의 복사본으로 업데이트 전 상태로 되돌림. 성공하면 True (업데이트 표시도 지움)."""
    app_dir = Path(app_dir)
    p = _read_json(app_dir / PENDING) or {}
    name = p.get("rollback") or ""
    rb = app_dir / ROLLBACK / name
    journal = _read_json(rb / "rollback.json") if name and _rel(name) and "/" not in name else None
    if not isinstance(journal, dict):
        log("  되돌릴 복사본이 없어요")
        _remove(app_dir / PENDING)  # 되돌릴 것이 없으면 매번 다시 시도하지 않음
        return False
    _write_json(app_dir / PENDING, dict(p, state="rolling_back"))  # 되돌리다 꺼져도 다음에 이어서
    ok = True
    for rel in journal.get("backup", []):
        rel = _rel(rel)
        if not rel or _protected(rel, app_dir):
            continue
        try:
            dst = app_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f"{dst.name}.{os.getpid()}.rollback-tmp")  # 실행기 둘이 겹쳐도 서로의 임시 파일을 옮기지 않게
            shutil.copyfile(rb / "files" / rel, tmp)  # 복사본은 남겨 둠 (되돌리다 끊겨도 다시 할 수 있게)
            _replace(tmp, dst)
        except OSError as e:
            ok = False
            log(f"  되돌리지 못한 파일 · {rel} · {e}")
    for rel in journal.get("added", []):
        rel = _rel(rel)
        if not rel or _protected(rel, app_dir):  # 설정 파일은 새로 넣었어도 남겨 둠
            continue
        try:
            _remove(app_dir / rel)
            _prune_empty_dirs(app_dir, rel)
        except OSError as e:
            ok = False
            log(f"  지우지 못한 새 파일 · {rel} · {e}")
    if ok:
        _remove(app_dir / PENDING)
        log(f"  이전 버전(v{journal.get('from') or '?'})으로 되돌렸어요")
    return ok


def _console_python():
    """확인용: 결과를 읽어야 해서 python.exe (pythonw.exe 옆에 있음)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def _gui_python():
    """앱 실행용: Windows 에서는 콘솔 창이 안 뜨는 pythonw.exe."""
    exe = Path(sys.executable)
    if WIN and exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        return str(exe.parent / "pythonw.exe")
    return str(exe)


def _import_check(app_dir, python=None):
    try:
        r = subprocess.run([python or _console_python(), "-c", IMPORT_CHECK], cwd=str(app_dir), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=180,
                           env=py_env(), creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    lines = [x for x in (r.stderr or "").strip().splitlines() if x.strip()]
    return r.returncode == 0, (lines[-1] if lines else f"종료 코드 {r.returncode}")


def _app_ports(app_dir=APP_DIR):
    """앱이 듣고 있을 포트: FUTSAL_PORT 또는 8765 + (8765 를 못 써서 다른 포트로 켰으면) 작업 폴더 .port 의 번호."""
    try:
        base = int(os.environ.get("FUTSAL_PORT") or 8765)
    except ValueError:
        base = 8765
    ports = [base]
    if not os.environ.get("FUTSAL_PORT"):
        try:
            p = int((workspace(app_dir) / ".port").read_text(encoding="utf-8").strip())
            if base < p < base + 40:
                ports.append(p)
        except (OSError, ValueError):
            pass
    return ports


def _app_running(app_dir=APP_DIR):
    """이미 켜져 있는 이 앱이 있으면 (그 창이 앞으로 나옴) 확인을 건너뜀. 그 포트를 다른 프로그램이 쓰고 있으면 아님
    (GET /api/ping 으로 확인 · 예전 버전은 404 {"error": "not found"})."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for port in _app_ports(app_dir):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                pass
        except OSError:
            continue
        try:
            with opener.open(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
                if json.loads(r.read() or b"{}").get("app") == "futsal-studio":
                    return True
        except urllib.error.HTTPError as e:
            try:
                if e.code == 404 and json.loads(e.read() or b"{}") == {"error": "not found"}:
                    return True
            except ValueError:
                pass
        except (OSError, ValueError, http.client.HTTPException):
            pass
    return False


@contextlib.contextmanager
def _launch_lock(app_dir, wait=PIP_TIMEOUT + 60):
    """실행기끼리 한 번에 하나 (잠금 파일 · 쥔 쪽은 5초마다 고쳐 '아직 씀'을 알림).
    LOCK_FRESH 초 넘게 안 고친 잠금은 꺼진 실행기가 남긴 것 → 지우고 가져감. 잠금 파일을 못 만들면 잠금 없이."""
    f = Path(app_dir) / LAUNCH_LOCK
    end, got = time.monotonic() + wait, False
    while True:
        try:
            fd = os.open(str(f), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            got = True
            break
        except FileExistsError:
            try:
                age = time.time() - f.stat().st_mtime
            except OSError:  # 그 사이 풀림 → 다시 (계속 못 읽으면 기다린 끝에 잠금 없이)
                if time.monotonic() > end:
                    break
                time.sleep(0.05)
                continue
            if age > LOCK_FRESH or time.monotonic() > end:
                try:
                    f.unlink()
                except OSError:
                    break  # 지울 수도 없음 → 잠금 없이
                continue
            time.sleep(0.2)
        except OSError:
            break
    stop = threading.Event()

    def beat():
        while not stop.wait(5):
            try:
                os.utime(f)
            except OSError:
                pass
    if got:
        threading.Thread(target=beat, daemon=True).start()
    try:
        yield got
    finally:
        stop.set()
        if got:
            try:
                f.unlink()
            except OSError:
                pass


def check(app_dir=APP_DIR, python=None, log=None):
    """실행 전 확인. 'none'(업데이트 없었음) · 'ok'(새 버전 확인) · 'starting'(방금 켠 새 버전이 아직 뜨는 중)
    · 'rolled_back'(이전 버전으로 되돌림) · 'running'(이미 켜져 있음) · 'failed'.
    아이콘을 거의 동시에 여러 번 눌러도 확인·되돌리기는 한 번에 하나 (뒤에 온 실행기는 앞 실행기가 끝난 상태를 다시 읽음)."""
    app_dir = Path(app_dir)
    if not isinstance(_read_json(app_dir / PENDING), dict):
        return "none"
    if not os.environ.get("FUTSAL_RESTART") and _app_running(app_dir):
        return "running"  # 켜져 있는 앱이 업데이트하는 중일 수도 있음 → 손대지 않음 (그 창이 앞으로 나옴)
    with _launch_lock(app_dir), _awake():
        return _check_locked(app_dir, python, log)


def _check_locked(app_dir, python, log):
    p = _read_json(app_dir / PENDING)  # 잠금을 기다리는 사이 앞 실행기가 되돌렸거나 마쳤을 수 있음
    if not isinstance(p, dict):
        return "none"
    say = log or (lambda m: studio_log(app_dir, m))
    if p.get("state") != "installed":
        return _undo(app_dir, p, "업데이트가 끝나기 전에 프로그램이 꺼졌어요", say, broken=False)
    if (app_dir / REQ_PENDING).exists():  # 앱이 켜진 채로는 못 바꾼 구성요소 → 아무것도 불러오지 않은 지금 설치
        # 업데이트 다시 시작(app.restart)이면 이전 앱 프로세스가 끝나(.pyd 를 놓아)야 바꿀 수 있음 → 끝날 때까지 기다림.
        # 이전 앱은 실행기를 띄우기 전에 휴대폰으로 보기(터널·리스너)를 이미 껐음 (D-034)
        _wait_gone(os.environ.get("FUTSAL_OLD_PID"), OLD_APP_WAIT)
        why = _deferred_pip(app_dir, python, say)
        if why:
            return _undo(app_dir, p, why, say, broken=False)
    now = time.time()
    last = p.get("last_launch")
    if isinstance(last, (int, float)) and 0 <= now - last < GRACE:
        return "starting"  # 앞서 켠 새 버전이 아직 뜨는 중일 수 있음 → 세지도 되돌리지도 않고 그냥 실행 (겹친 실행은 app.py 가 정리)
    p["launches"] = int(p.get("launches") or 0) + 1
    p["last_launch"] = now
    _write_json(app_dir / PENDING, p)  # 확인하는 동안 또 누른 실행도 '방금 켬'으로 보이게 먼저 적어 둠
    if p["launches"] > MAX_FAILED:  # 앞선 실행들이 GRACE 초가 지나도 창을 못 열었음
        return _undo(app_dir, p, "새 버전이 여러 번 제대로 켜지지 않았어요", say)
    ok, err = _import_check(app_dir, python)
    if ok:
        return "ok"
    return _undo(app_dir, p, f"새 버전이 열리지 않아요 ({err})", say)


OLD_APP_WAIT = 20  # 업데이트 다시 시작: 이전 앱 프로세스가 끝나길 기다리는 시간(초) · 넘으면 그냥 설치 (실패하면 예전처럼 되돌림)


def _pid_alive(pid):
    """그 번호의 프로세스가 아직 살아 있는지 (모르면 False). Windows 의 os.kill 은 프로세스를 끝내므로 쓰지 않는다."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if WIN:
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not h:
                return False
            try:
                return k32.WaitForSingleObject(h, 0) == 0x00000102  # WAIT_TIMEOUT = 아직 도는 중
            finally:
                k32.CloseHandle(h)
        except (OSError, AttributeError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # PermissionError: 있음 (다른 사용자)
        return True
    return True


def _wait_gone(pid, secs):
    """pid 프로세스가 끝날 때까지 secs 초까지 기다림 (없거나 이미 끝났으면 바로)."""
    end = time.monotonic() + secs
    while _pid_alive(pid) and time.monotonic() < end:
        time.sleep(0.1)


def _deferred_pip(app_dir, python, say):
    """.req_pending 이 있으면 구성요소(requirements.txt) 설치 → 실패 이유 (성공·할 일 없으면 None).
    앱 프로세스는 numpy·onnxruntime 같은 .pyd/.dll 을 불러 둔 채라 Windows 에서는 그 파일을 바꾸지 못함 (core.update_app)."""
    say("업데이트 마무리 · 새 구성요소를 설치하는 중이에요 (몇 분 걸릴 수 있어요 · 끝나면 창이 열려요)")
    try:
        r = subprocess.run([python or _console_python(), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                            "-r", str(Path(app_dir) / "requirements.txt")], cwd=str(app_dir), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=PIP_TIMEOUT, env=py_env(), creationflags=NO_WINDOW)
        ok = r.returncode == 0
        lines = [x.strip() for x in (r.stderr or "").splitlines() if x.strip()]
        err = " / ".join(lines[-2:])[-300:] if lines else f"종료 코드 {r.returncode}"
    except (OSError, subprocess.TimeoutExpired) as e:
        ok, err = False, str(e)
    try:
        _remove(Path(app_dir) / REQ_PENDING)
        mark_requirements(app_dir, ok=ok)
    except OSError:
        pass
    if ok:
        say("  새 구성요소 설치 완료")
        return None
    return f"새 구성요소를 설치하지 못했어요 ({err})"


def _undo(app_dir, p, reason, say, broken=True):
    """업데이트를 되돌리고 다음에 켜질 앱이 알릴 결과를 남김.
    broken: 새 버전 자체가 이 PC에서 안 열림 → 더 새 버전이 나올 때까지 다시 권하지 않음."""
    old, new = p.get("from") or "?", p.get("to") or "?"
    say(f"업데이트 확인 · v{old} → v{new} · {reason} → 이전 버전으로 되돌려요")
    if rollback(app_dir, say):
        _write_json(app_dir / RESULT, {"rolled_back": True, "from": old, "to": new, "reason": reason, "broken": broken})
        if broken:
            _write_json(app_dir / SKIP, {"version": new, "from": old, "reason": reason, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        return "rolled_back"
    say("이전 버전으로 되돌리지 못했어요. 이 studio.log 파일을 관리자에게 보내 주세요.")
    _alert("업데이트를 되돌리지 못했어요.\n작업 폴더의 studio.log 파일을 관리자에게 보내 주세요.")
    return "failed"


def finish(app_dir=APP_DIR):
    """앱이 잘 켜진 뒤(app.py): 업데이트 표시를 지우고, 화면 기록에 남길 말을 돌려줌 (화면 알림도 한 번: take_notice)."""
    app_dir = Path(app_dir)
    msgs = []
    res = _read_json(app_dir / RESULT)
    if isinstance(res, dict):
        old, new = res.get("from"), res.get("to")
        msgs.append(f"v{new} 업데이트를 취소하고 이전 버전(v{old})으로 되돌렸어요 · "
                    f"{res.get('reason', '')} · 보관함과 작업 파일은 그대로예요")
        if res.get("broken", True):
            text = f"새 버전(v{new})이 이 PC에서 열리지 않아 이전 버전(v{old})으로 되돌렸어요. 보관함과 작업 파일은 그대로예요. 관리자에게 알려 주세요."
        else:
            text = f"업데이트가 중간에 끊겨서 이전 버전(v{old})으로 되돌렸어요. 보관함과 작업 파일은 그대로예요. 다시 업데이트하면 돼요."
        _NOTICES.append({"text": text, "warn": True})
        _remove(app_dir / RESULT)
    p = _read_json(app_dir / PENDING)
    if isinstance(p, dict) and p.get("state") == "installed" and _read_version(app_dir) == p.get("to"):
        _remove(app_dir / PENDING)
        _remove(app_dir / SKIP)  # 새 버전이 잘 켜졌으니 예전에 건너뛴 기록은 필요 없음
        msgs.append(f"업데이트 완료 · v{p.get('from')} → v{p.get('to')}")
        _NOTICES.append({"text": msgs[-1], "warn": False})
    return msgs


def take_notice():
    """앱이 켜진 뒤 화면에 한 번 띄울 업데이트 결과 {text, warn} (없으면 None)."""
    if not _NOTICES:
        return None
    out = {"text": " ".join(n["text"] for n in _NOTICES), "warn": any(n["warn"] for n in _NOTICES)}
    _NOTICES.clear()
    return out


def skipped(app_dir=APP_DIR):
    """이 PC에서 열리지 않아 되돌린 버전 ('' = 없음). 더 새 버전이 나오면 그건 다시 권함."""
    s = _read_json(Path(app_dir) / SKIP)
    return str(s.get("version") or "") if isinstance(s, dict) else ""


def _alert(msg):
    if WIN and not _SELFTEST:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "풋살사관학교 스튜디오", 0x10)
        except Exception:
            pass


def spawn(app_dir=APP_DIR, args=(), env=None):
    """실행기(updater.py --launch)를 따로 떨어진 새 프로세스로 (이 프로세스는 곧 끝남)."""
    kw = {"cwd": str(app_dir), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "env": env}
    if WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen([_gui_python(), str(Path(app_dir) / "updater.py"), "--launch", *args], **kw)


def run_app(app_dir=APP_DIR, args=()):
    """앱(app.py)을 바로 이 프로세스에서 실행. 아이콘이 띄운 프로세스가 곧 앱 창이어야 Windows 가 창을 그 아이콘과
    묶어 둠 (켜져 있는 창을 '작업 표시줄에 고정'해도 이 실행기를 거치는 아이콘이 고정됨)."""
    app_dir = Path(app_dir)
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    try:
        os.chdir(app_dir)  # 따로 켜던 때처럼 앱 폴더에서
    except OSError:
        pass
    os.environ["FUTSAL_VIA_UPDATER"] = "1"  # app.py: 실행기를 거쳐 켜졌음 (다시 돌려보내지 않음)
    os.environ.pop("FUTSAL_OLD_PID", None)  # 기다릴 이전 앱은 확인(check)에서만 · 이 앱이 띄울 프로세스에는 넘기지 않음 (번호 재사용)
    sys.argv = [str(app_dir / "app.py"), *args]
    try:
        runpy.run_path(str(app_dir / "app.py"), run_name="__main__")
    except Exception as e:
        _start_failed(app_dir, args, e)
        return 1
    return 0


def _error_trace(app_dir):
    """방금 난 오류의 traceback 을 작업 폴더의 studio-error.log 에 (pythonw 는 화면에 안 보이므로 · 실패해도 그만)."""
    try:
        import traceback
        ws = workspace(app_dir)
        text = traceback.format_exc()
        try:  # 비밀(터널 주소·주제 등)은 '…' — 규칙은 remote.redact 한 곳 (켜다 멈춘 까닭이 그 모듈이면 그대로)
            import remote
            text = remote.redact(text)
        except Exception:  # noqa: BLE001
            pass
        with open(ws / "studio-error.log", "a", encoding="utf-8", errors="replace") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "프로그램을 켜다 멈춤\n" + text)
    except (OSError, ValueError):
        pass


def _start_failed(app_dir, args, e):
    """app.py 가 창을 열기 전에 오류로 멈춤: 방금 업데이트한 버전이면 바로 되돌리고 이전 버전을 새 프로세스로 켬."""
    err = f"{type(e).__name__}: {e}"
    _error_trace(app_dir)

    def say(m):
        studio_log(app_dir, m)
    p = _read_json(app_dir / PENDING)
    if isinstance(p, dict) and p.get("state") == "installed" and p.get("to") == _read_version(app_dir):
        if _undo(app_dir, p, f"새 버전이 켜지다 멈췄어요 ({err})", say) == "rolled_back":
            spawn(app_dir, args, env=dict(os.environ, FUTSAL_RESTART="1"))  # 새 버전 코드가 섞이지 않게 새 프로세스로
        return
    say(f"프로그램을 켜지 못했어요 · {err}")
    _alert("프로그램을 켜지 못했어요.\n작업 폴더의 studio.log 파일을 관리자에게 보내 주세요.")


def main(argv=None, app_dir=APP_DIR):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return _selftest()
    if "--launch" not in argv:
        print("사용법: python updater.py --launch")
        return 2
    args = [a for a in argv if a != "--launch"]
    try:
        check(app_dir)
    except Exception as e:  # 확인이 실패해도 앱은 켬
        studio_log(app_dir, f"업데이트 확인 중 문제가 생겼어요 · {e}")
    return run_app(app_dir, args)


def _selftest():
    """설치 전에 core 가 새 버전의 이 파일로 부름: 임시 폴더에 '안 열리는 새 버전'을 꾸며 --launch 와 똑같이
    확인 → 되돌리기 → 앱 실행 → 마무리까지 해 봄. 끝까지 되면 0."""
    global _SELFTEST
    _SELFTEST = True
    os.environ.pop("FUTSAL_RESTART", None)
    with socket.socket() as s:  # 아무도 안 쓰는 포트 → '이미 켜져 있음' 확인도 그대로 지나감
        s.bind(("127.0.0.1", 0))
        os.environ["FUTSAL_PORT"] = str(s.getsockname()[1])
    problems = []
    with tempfile.TemporaryDirectory(prefix="futsal-selftest-", ignore_cleanup_errors=True) as td:
        d = Path(td) / "앱 폴더"
        rb = d / ROLLBACK / "v1.0"
        (rb / "files").mkdir(parents=True)
        (d / "config.json").write_text(json.dumps({"workspace": str(d / "작업")}), encoding="utf-8")
        (d / "version.txt").write_text("1.1\n", encoding="utf-8")
        (d / "app.py").write_text("raise ImportError('시험용 고장')\n", encoding="utf-8")
        (rb / "files" / "version.txt").write_text("1.0\n", encoding="utf-8")
        (rb / "files" / "app.py").write_text("from pathlib import Path\nif __name__ == '__main__':\n"
                                             "    Path(__file__).with_name('ran.txt').write_text('ok')\n", encoding="utf-8")
        _write_json(rb / "rollback.json", {"from": "1.0", "to": "1.1", "backup": ["app.py", "version.txt"], "added": []})
        _write_json(d / PENDING, {"from": "1.0", "to": "1.1", "state": "installed", "rollback": rb.name})
        here = os.getcwd()
        try:
            code = main(["--launch"], app_dir=d)
        finally:
            os.chdir(here)  # Windows: 지금 들어가 있는 폴더는 지울 수 없음
        if code != 0:
            problems.append(f"실행 종료 코드 {code}")
        if not (d / "ran.txt").is_file():
            problems.append("되돌린 앱이 켜지지 않음")
        if (d / PENDING).exists() or _read_version(d) != "1.0":
            problems.append("이전 버전으로 되돌리지 못함")
        if skipped(d) != "1.1":
            problems.append("건너뛸 버전을 적지 못함")
        try:
            log = (d / "작업" / "studio.log").read_text(encoding="utf-8")
        except OSError:
            log = ""
        if "시험용 고장" not in log:
            problems.append("studio.log 에 이유를 남기지 못함")
        if not any("되돌렸어요" in m for m in finish(d)) or not (take_notice() or {}).get("warn"):
            problems.append("되돌림 안내를 만들지 못함")
    if problems:
        print("실행기 시험 실패 · " + ", ".join(problems), file=sys.stderr)
        return 1
    print("selftest ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
