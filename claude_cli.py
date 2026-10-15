"""클로드 계정으로 쓰기 — 사용자 PC 에 설치된 Claude Code CLI(Anthropic 공식 프로그램)를 headless(-p)로 불러 판단을 받음.

- 로그인은 사용자가 공식 프로그램 창에서 브라우저로 직접 함 (앱은 claude.ai 로그인을 받지 않음 · 사용자의 Pro/Max 구독 사용량을 씀).
- 실행은 빈 임시 폴더에서: 세션 저장·설정 파일·MCP 끔, 도구는 Read 만(그림이 있을 때, 그 폴더 안만) 또는 없음,
  권한 질문은 모두 거절(dontAsk), 프롬프트는 stdin 으로 넣고 바로 닫음, 창 없이, 벽시계 제한·멈추기(✕) 가능.
- 자식 환경에서 ANTHROPIC_API_KEY·ANTHROPIC_AUTH_TOKEN 을 지움 (그게 있으면 계정 로그인보다 먼저 쓰여 요금이 나감).
  선택: 'claude setup-token' 으로 받은 로그인 코드는 ~/.futsal-studio/claude_token 에 두고 CLAUDE_CODE_OAUTH_TOKEN 으로만 넘김.
- 프롬프트·대답·토큰은 기록(studio.log)에 남기지 않음. 호출부는 결과 종류만 기록.
- 클로드 아껴 쓰기(기본 켬 · D-390~D-395): 일마다 모델(TASK_MODEL · 대부분 Sonnet, 채널 전략 '더 깊게'만 Opus) · 짧은 일 전용 지시(--system-prompt ·
  Claude Code 의 긴 기본 지시를 대신함) · 그림은 줄여서 메시지에 바로(--input-format stream-json · Read 도구 차례 없이) · 같은 물음은 저장해 둔 대답
  (~/.futsal-studio/claude_cache · 400개/30MB) · 쓴 양은 숫자만 ~/.futsal-studio/claude_usage.json. 끄면 예전 그대로(모두 Opus·기본 지시·Read).
  옵션은 모두 'claude --help' 에 있을 때만 넣음 (예전 판은 예전 명령 그대로).
문서: code.claude.com/docs (headless · cli-reference · authentication · setup).
"""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ENV_EXE = "FUTSAL_CLAUDE"       # 시험·고급용: 실행 파일 경로를 직접 지정
MODEL = "opus"                  # CLI 별칭 — 최신 Opus 로 풀림 (2026-10-07 기준 claude-opus-5-5)
TIMEOUT = 240                   # 한 번 판단 벽시계 제한(초)
STATUS_TTL = 60                 # 로그인 상태 확인 캐시(초)
INSTALL_PS = "irm https://claude.ai/install.ps1 | iex"   # 공식 Windows 설치 명령 (code.claude.com/docs setup)
HOME = Path.home() / ".futsal-studio"
TOKEN_RE = re.compile(r"sk-ant-oat[0-9A-Za-z_\-]{10,290}")
WIN = sys.platform == "win32"
NO_WINDOW, NEW_GROUP, NEW_CONSOLE = 0x08000000, 0x00000200, 0x00000010

_LOCK = threading.Lock()
_FEAT = {}
_STATUS = {"t": 0.0, "v": None}

# ---------- 클로드 아껴 쓰기 (D-390~D-395) ----------
# 일 이름 → 모델 별칭. 아껴 쓰기를 끄면 모두 MODEL(Opus). 채널 전략 '클로드로 더 깊게'는 사용자가 직접 누르는 깊은 판단이라 Opus 그대로.
TASK_MODEL = {"proofread": "sonnet", "writer": "sonnet", "frames": "sonnet", "copy": "sonnet", "judge": "sonnet", "plan": "sonnet",
              "strategy": "opus"}
# Claude Code 의 긴 기본 지시(도구 사용법·코딩 규칙 등) 대신 쓰는 짧은 지시 (--system-prompt). 한 줄 · 큰따옴표·&|<>^%!() 없음 —
# npm 의 claude.cmd 는 cmd.exe 를 거쳐 인자가 넘어가므로 (시험 test_system_prompts_cmd_safe).
SYS_BASE = ("당신은 풋살 레슨 유튜브 채널 풋살사관학교의 영상 제작 앱이 부른 한국어 도우미예요. 대화 없이 이 요청 하나에만 바로 답해요. "
            "요청이 정한 대답 형식 그대로만 쓰고 인사나 덧붙이는 설명은 쓰지 않아요.")
SYS_TASK = {"proofread": "할 일은 자동 받아쓰기에서 잘못 들은 글자 고치기예요.",
            "writer": "할 일은 레슨 영상의 짧은 재미 자막 쓰기예요.",
            "frames": "할 일은 썸네일 배경으로 쓸 장면에 점수 매기기예요.",
            "copy": "할 일은 썸네일 문구 만들기예요.",
            "judge": "할 일은 썸네일 그림 평가하기예요.",
            "plan": "할 일은 잘 되는 유튜브 영상의 기획 분석이에요.",
            "strategy": "할 일은 유튜브 채널을 키울 전략 세우기예요."}
SYS_IMG = ("그림 파일은 이 메시지에 바로 붙어 있고 각 그림 바로 앞의 [그림 이름]이 그 파일 이름이에요. "
           "글에서 Read 도구로 열어 보라거나 같은 폴더에 있다고 한 그림은 붙어 있는 그 그림이니 도구 없이 바로 보고 답해요.")
MAX_TURNS = 2                   # 도구가 없을 때 (--max-turns 가 있는 판만)
IMG_SIDE, IMG_PIXELS, IMG_Q = 1568, 1_150_000, 80   # 그림 긴 변·화소 수 한도(클로드가 더 크면 어차피 줄여 봄) · 줄일 때 JPEG 품질
IMG_BYTES = 3_500_000           # 이보다 큰 그림 파일은 크기가 작아도 다시 저장 (한 그림 5MB 한도 아래로)
PREFS_FILE = "claude_prefs.json"   # 아껴 쓰기 켬/끔 (HOME 안 · 숫자·참거짓만)
USAGE_FILE = "claude_usage.json"   # 날마다 쓴 양 (숫자만 · 프롬프트·대답 없음)
USAGE_DAYS = 31
CACHE_DIR = "claude_cache"         # 저장해 둔 대답 (열쇠는 내용 해시 · 프롬프트는 남기지 않음)
CACHE_MAX_N, CACHE_MAX_BYTES = 400, 30 << 20
CACHE_VER = 1
_ULOCK = threading.Lock()          # 쓴 양·저장해 둔 대답 파일
_JOB = {"on": False}               # 지금 작업(app.start_job)의 클로드 사용 합계
_INLINE_BAD = set()                # 그림 바로 보내기가 결과 없이 끝난 실행 파일 (이번 실행 동안은 Read 로)


class ClaudeError(Exception):
    """사용자에게 그대로 보여 줄 한국어 안내 + 종류(kind: missing·login·limit·timeout·network·cancel·update·format·error)."""

    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind = kind


MSG = {
    "missing": "이 PC에 클로드 프로그램이 없어요. 위 '클로드 계정으로 쓰기'에서 [설치하기]를 눌러 주세요",
    "login": "클로드 로그인이 필요해요. 위 '클로드 계정으로 쓰기'에서 [로그인]을 눌러 주세요",
    "limit": "클로드 사용 한도에 닿았어요. 한도가 풀린 뒤 다시 눌러 주세요 (내 PC 분석 결과는 그대로 있어요)",
    "timeout": "클로드 대답이 너무 늦어요. 잠시 뒤 다시 해 주세요",
    "network": "인터넷 연결을 확인해 주세요 (내 PC 분석 결과는 그대로 있어요)",
    "cancel": "클로드 판단을 멈췄어요",
    "update": "클로드 프로그램이 오래된 판이에요. 위 '클로드 계정으로 쓰기'에서 [설치하기]를 다시 눌러 새 판으로 바꾼 뒤 눌러 주세요",
    "error": "클로드가 대답하지 못했어요. 잠시 뒤 다시 눌러 주세요 (내 PC 분석 결과는 그대로 있어요)",
}


# ---------- 실행 파일 찾기 ----------

def find_exe():
    """Claude Code 실행 파일 경로 (없으면 None): FUTSAL_CLAUDE → PATH 의 claude → 공식 설치 위치 → npm 전역 설치."""
    env = os.environ.get(ENV_EXE)
    if env:
        return env if Path(env).is_file() else None
    w = shutil.which("claude")
    if w and (not WIN or Path(w).suffix.upper() in _pathext()):
        return w  # Python 3.12.0 의 which 는 npm 폴더의 확장자 없는 'claude'(sh 스크립트, 실행 불가)를 먼저 돌려줌 → 건너뜀
    if WIN:
        for ext in (".exe", ".cmd"):
            w = shutil.which("claude" + ext)
            if w:
                return w
    home = Path.home()
    cands = [home / ".local" / "bin" / ("claude.exe" if WIN else "claude")]
    if WIN and os.environ.get("APPDATA"):
        cands.append(Path(os.environ["APPDATA"]) / "npm" / "claude.cmd")
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def _pathext():
    return {e.strip().upper() for e in (os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if e.strip()}


def _is_cmd(exe):
    return str(exe).lower().endswith((".cmd", ".bat"))


def _base(exe):
    """npm 의 claude.cmd 는 cmd.exe 를 거쳐 실행."""
    return [os.environ.get("COMSPEC") or "cmd.exe", "/c", str(exe)] if _is_cmd(exe) else [str(exe)]


def _track(p):
    """앱이 꺼지면(창 닫기·재시작) 클로드도 같이 꺼지게 (core.track · Windows Job Object). core 가 없으면 그대로."""
    try:
        import core
        core.track(p)
    except Exception:  # noqa: BLE001
        pass


def _flags(hidden=True):
    if WIN:
        return {"creationflags": (NO_WINDOW | NEW_GROUP) if hidden else NEW_CONSOLE}
    return {"start_new_session": True}


def token_file():
    return HOME / "claude_token"


def read_token():
    try:
        t = token_file().read_text(encoding="utf-8").strip()
        return t if TOKEN_RE.fullmatch(t) else None
    except OSError:
        return None


def has_token():
    return read_token() is not None


def save_token(tok):
    """'claude setup-token' 으로 받은 로그인 코드 저장 (형식만 확인 · 사용자 폴더 안 · 앱 폴더·config.json 밖)."""
    t = str(tok or "").strip()
    if not TOKEN_RE.fullmatch(t):
        raise ClaudeError("format", "로그인 코드 형식이 달라요. 'sk-ant-oat'로 시작하는 코드를 그대로 붙여 넣어 주세요")
    HOME.mkdir(parents=True, exist_ok=True)
    f = token_file()
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(t, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, f)
    reset_status()


def clear_token():
    try:
        token_file().unlink()
    except FileNotFoundError:
        pass
    reset_status()


def child_env():
    """자식 프로세스 환경: API 키 변수를 지우고(계정 로그인보다 먼저 쓰여 요금이 나감), 저장한 로그인 코드가 있으면 그것만 넘김."""
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)
    tok = read_token()
    if tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    else:
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return env


def _quick(args, timeout=10):
    """짧은 확인 실행 → (종료 코드, 표준 출력). 실행 파일이 없으면 None."""
    exe = find_exe()
    if not exe:
        return None
    try:
        r = subprocess.run(_base(exe) + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout, env=child_env(), cwd=tempfile.gettempdir(), **_flags())
        return r.returncode, (r.stdout or "") + "\n" + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return None


HELP_TIMEOUTS = (10, 30)   # 'claude --help' 확인 — 처음 켤 때 백신 검사·npm 의 cmd.exe 로 느릴 수 있어 한 번 더 길게


def features(exe=None):
    """'claude --help' 을 읽어 지원하는 옵션을 기억 (예전 판에 없는 옵션은 넣지 않음).
    확인이 실패하면(시간 초과·이상한 출력) 기억하지 않고 {} — build_cmd 는 그때 안전 옵션(도구·설정·MCP 제한)을 모두 넣음 (fail closed)."""
    exe = exe or find_exe()
    if not exe:
        return {}
    with _LOCK:
        if exe in _FEAT:
            return _FEAT[exe]
    out = ""
    for to in HELP_TIMEOUTS:
        r = _quick(["--help"], timeout=to)
        out = r[1] if r else ""
        if "--output-format" in out or "--print" in out:
            break
        out = ""
    if not out:
        return {}  # 모름: 기억하지 않음 (다음에 다시 확인)
    f = {"permission_prompts": "--permission-prompts" in out, "restricted": "--restricted" in out,
         "no_session": "--no-session-persistence" in out, "strict_mcp": "--strict-mcp-config" in out,
         "setting_sources": "--setting-sources" in out, "tools": "--tools" in out, "auth": bool(re.search(r"^\s+auth\b", out, re.M)),
         # 아껴 쓰기 (D-391·D-392): 짧은 지시 · 차례 수 한도 · 그림을 메시지에 바로 (입력·출력 모두 stream-json 이고 --verbose 가 있어야 함)
         "system_prompt": bool(re.search(r"--system-prompt(?![-\w])", out)), "max_turns": "--max-turns" in out,
         "stream_in": bool(re.search(r"--input-format\b", out)) and "stream-json" in out and "--verbose" in out}
    with _LOCK:
        _FEAT[exe] = f
    return f


# ---------- 상태 · 설치 · 로그인 ----------

def reset_status():
    with _LOCK:
        _STATUS.update(t=0.0, v=None)


def status(refresh=False):
    """{state: missing·login·ready·unknown, method, hasToken, detail}. 60초 동안 기억 (refresh 면 다시)."""
    with _LOCK:
        if not refresh and _STATUS["v"] is not None and time.time() - _STATUS["t"] < STATUS_TTL:
            return dict(_STATUS["v"])
    exe = find_exe()
    if not exe:
        v = {"state": "missing", "hasToken": has_token()}
    else:
        r = _quick(["auth", "status", "--json"])
        v = {"state": "unknown", "hasToken": has_token()}
        if r is not None:
            m = re.search(r"\{.*\}", r[1], re.S)
            try:
                d = json.loads(m[0]) if m else None
            except ValueError:
                d = None
            if isinstance(d, dict) and "loggedIn" in d:
                v["state"] = "ready" if d.get("loggedIn") else "login"
                v["method"] = str(d.get("authMethod") or "")[:40]
            elif r[0] != 0 and re.search(r"not logged in|log ?in", r[1], re.I):
                v["state"] = "login"
    with _LOCK:
        _STATUS.update(t=time.time(), v=dict(v))
    return v


def install():
    """공식 설치 프로그램을 보이는 PowerShell 창에서 실행 (Windows). 고정 문자열이라 -EncodedCommand 로 넘김 (사용자 입력 없음)."""
    if not WIN:
        raise ClaudeError("error", "설치 창은 Windows 에서만 띄울 수 있어요")
    enc = base64.b64encode(INSTALL_PS.encode("utf-16-le")).decode()
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-EncodedCommand", enc],
                     cwd=str(Path.home()), **_flags(hidden=False))
    reset_status()


def login():
    """보이는 창에서 'claude auth login --claudeai' (예전 판이면 'claude') — 사용자가 브라우저에서 직접 로그인. 창을 기다리지 않음."""
    exe = find_exe()
    if not exe:
        raise ClaudeError("missing", MSG["missing"])
    if not WIN:
        raise ClaudeError("error", "로그인 창은 Windows 에서만 띄울 수 있어요. 터미널에서 'claude' 를 실행해 로그인해 주세요")
    args = ["auth", "login", "--claudeai"] if features(exe).get("auth") else []
    env = child_env()
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    subprocess.Popen(_base(exe) + args, cwd=str(Path.home()), env=env, **_flags(hidden=False))
    reset_status()


# ---------- headless 실행 ----------

def _args_ok(exe):
    """띄어쓰기가 든 인자(짧은 지시)를 넘겨도 되는지 — claude.cmd 는 cmd.exe /c 를 거치는데, 실행 파일 경로가 따옴표로 묶이면(경로에 띄어쓰기)
    따옴표가 셋 이상일 때 cmd 가 처음·마지막 따옴표를 떼어 명령이 깨짐 → 그때는 짧은 지시를 넣지 않음 (기본 지시 그대로)."""
    return not _is_cmd(exe) or " " not in str(exe)


def build_cmd(exe, with_images, feat=None, model=MODEL, system=None, inline=False):
    """claude -p 명령 (프롬프트는 stdin). 그림이 있으면 Read 도구만 (작업 폴더 안), 없으면 도구 없음.
    아껴 쓰기(D-391·D-392): model · system(짧은 지시, --system-prompt 가 있는 판만) · inline(그림을 stdin 메시지에 바로 — stream-json 입출력, 도구 없음)."""
    f = feat if feat is not None else features(exe)
    read = with_images and not inline   # Read 도구가 있어야 하는지 (그림을 폴더에서 읽을 때만)
    # 도구·설정·MCP·세션 제한은 판을 모르면(확인 실패) 늘 넣음 — CLI 가 모르는 옵션이라고 하면 '업데이트해 주세요' 로 안내 (제한 없이 돌리지 않음)
    cmd = _base(exe) + ["-p", "--output-format", "stream-json" if inline else "json", "--model", model]
    if inline:
        cmd += ["--input-format", "stream-json", "--verbose"]  # stream-json 출력은 -p 에서 --verbose 가 있어야 함
    if f.get("no_session", True):
        cmd.append("--no-session-persistence")
    if f.get("strict_mcp", True):
        cmd.append("--strict-mcp-config")
    if f.get("setting_sources", True) and not _is_cmd(exe):  # cmd.exe 를 거치면 빈 인자("")가 깨질 수 있어 뺌 (도구는 Read 로 묶음)
        cmd += ["--setting-sources", ""]
    cmd += ["--permission-mode", "dontAsk"]
    if f.get("permission_prompts"):
        cmd += ["--permission-prompts", "none"]
    if f.get("restricted"):
        cmd.append("--restricted")
    if f.get("tools", True):
        if read:
            cmd += ["--tools", "Read"]
        elif not _is_cmd(exe):
            cmd += ["--tools", ""]
        else:
            cmd += ["--tools", "Read"]  # 빈 인자를 못 넘김 → Read 만 (빈 임시 폴더 안)
    if system and f.get("system_prompt") and _args_ok(exe):
        cmd += ["--system-prompt", system]
    if system and not read and f.get("max_turns"):  # system: 아껴 쓰기일 때만 줌 · 도구가 없으니 한 번이면 끝 (여유 1)
        cmd += ["--max-turns", str(MAX_TURNS)]
    return cmd


def _kill(p):
    try:
        if WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW, timeout=10)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        p.kill()
    except OSError:
        pass


def explain(code, out, err):
    """종료 코드·JSON·오류 글 → (종류, 결과 dict 또는 None)."""
    d = None
    for ln in reversed((out or "").strip().splitlines()):  # stream-json(그림 바로 보내기)은 여러 줄 — 'result' 줄을 먼저
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                x = json.loads(ln)
            except ValueError:
                continue
            if d is None or (isinstance(x, dict) and x.get("type") == "result"):
                d = x
            if isinstance(x, dict) and x.get("type") == "result":
                break
    if d is None:
        m = re.search(r"\{.*\}", out or "", re.S)
        try:
            d = json.loads(m[0]) if m else None
        except ValueError:
            d = None
    blob = " ".join(str(x) for x in ((d or {}).get("result"), (d or {}).get("api_error_status"), (d or {}).get("subtype"), err, out if d is None else ""))
    status_code = (d or {}).get("api_error_status")
    if isinstance(d, dict) and not d.get("is_error") and d.get("type") == "result" and d.get("subtype") == "success" and code == 0:
        return "ok", d
    if d is None and re.search(r"unknown option|unrecognized option|unknown argument|error: option .* argument missing", blob, re.I):
        return "update", d  # 안전 옵션을 모르는 예전 판 → 제한 없이 돌리지 않고 업데이트 안내
    if status_code == 401 or re.search(r"not logged in|please run /login|/login|invalid api key|oauth token (has )?expired|authentication|401", blob, re.I):
        return "login", d
    if status_code == 429 or re.search(r"usage limit|limit reached|rate limit|429|5-hour limit|weekly limit", blob, re.I):
        return "limit", d
    if re.search(r"ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|network|getaddrinfo|unable to connect|fetch failed", blob, re.I):
        return "network", d
    return "error", d


def _model_of(d, default=MODEL):
    mu = (d or {}).get("modelUsage") or {}
    names = [k for k in mu if "haiku" not in k] or list(mu)
    return names[0] if names else default


# ---------- 아껴 쓰기: 설정 · 그림 줄이기 · 저장해 둔 대답 · 쓴 양 (D-390~D-395) ----------

def save_on():
    """'클로드 아껴 쓰기' 켬(기본) / 끔 — HOME/claude_prefs.json."""
    try:
        d = json.loads((HOME / PREFS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    v = d.get("save") if isinstance(d, dict) else None
    return v if isinstance(v, bool) else True


def _write_json(f, data):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def set_save(on):
    _write_json(HOME / PREFS_FILE, {"save": bool(on)})
    return bool(on)


def model_for(task, save=None):
    """일 이름 → 모델 별칭 (아껴 쓰기를 끄면·일 이름이 없으면 MODEL)."""
    save = save_on() if save is None else save
    return TASK_MODEL.get(task, MODEL) if save and task else MODEL


def system_prompt(task, inline=False):
    """일 전용 짧은 지시 (그림을 메시지에 바로 붙일 때는 그 뜻을 한 줄 더)."""
    s = " ".join(x for x in (SYS_BASE, SYS_TASK.get(task, "")) if x)
    return f"{s} {SYS_IMG}" if inline else s


def _app_ver():
    """앱 판의 앞 두 자리 ('2.21.1' → '2.21') — 판이 바뀌면 저장해 둔 대답을 새로."""
    try:
        v = (Path(__file__).resolve().parent / "version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ".".join(v.split(".")[:2])


def _read_images(images):
    """[(원본 경로, 넣을 이름)] → [(이름, 바이트)] (못 읽는 파일은 빼고 · 예전처럼)."""
    out = []
    for src, nm in images or []:
        try:
            out.append((Path(nm).name, Path(src).read_bytes()))
        except OSError:
            pass
    return out


def _mime(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def prep_image(nm, data):
    """그림을 클로드가 보는 크기로 (긴 변 IMG_SIDE · 화소 IMG_PIXELS 안) → (이름, 바이트, 종류).
    이미 작으면 그대로 (토큰은 화소 수로 정해져 다시 저장해도 줄지 않음 · 썸네일 평가 그림의 화질을 지킴).
    줄일 때만 JPEG 품질 IMG_Q (이름이 .png 면 PNG) — 번호를 붙인 장면 시트(16장)도 번호가 읽히는 크기 (약 1400px)."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            k = min(1.0, IMG_SIDE / max(w, h), (IMG_PIXELS / float(w * h)) ** 0.5)
            if k >= 1.0 and len(data) <= IMG_BYTES:
                return nm, data, _mime(data)
            png = nm.lower().endswith(".png")
            im2 = im.convert("RGBA" if png and im.mode in ("RGBA", "LA", "P") else "RGB")
            if k < 1.0:
                im2 = im2.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
            buf = io.BytesIO()
            if png:
                im2.save(buf, "PNG", optimize=True)
            else:
                im2.save(buf, "JPEG", quality=IMG_Q, optimize=True)
            return nm, buf.getvalue(), "image/png" if png else "image/jpeg"
    except Exception:  # noqa: BLE001 — Pillow 가 없거나 그림이 아니면 그대로 (예전처럼)
        return nm, data, _mime(data)


def stream_message(prompt, imgs):
    """stdin 한 줄 (stream-json 입력): 사용자 메시지 = 그림마다 [그림 이름] 글 + base64 그림 → 마지막에 프롬프트.
    모양은 Claude Agent SDK 와 같음: {"type":"user","message":{"role":"user","content":[…]}}."""
    content = []
    for nm, data, mt in imgs:
        content.append({"type": "text", "text": f"[그림 {nm}]"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(data).decode("ascii")}})
    content.append({"type": "text", "text": str(prompt)})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False) + "\n"


def cache_key(task, model, prompt, imgs):
    """내용 해시: 일 · 모델 · 짧은 지시 · 프롬프트 · 그림 바이트 · 앱 판(앞 두 자리)."""
    h = hashlib.sha256()
    for part in (f"v{CACHE_VER}", str(task or ""), str(model), system_prompt(task), _app_ver(), str(prompt)):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\0")
    for nm, data in imgs:
        h.update(nm.encode("utf-8", "replace"))
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()[:40]


def _cache_dir():
    return HOME / CACHE_DIR


_REUSE = threading.local()   # 저장해 둔 대답을 다시 쓰는 때: '끝까지 자동으로' 안에서만 (사람이 다시 누르면 새로 물음 · I-392)


class reuse_answers:
    """with claude_cli.reuse_answers(): 이 스레드의 클로드 부르기는 같은 물음이면 저장해 둔 대답을 다시 씀 (밖에서는 저장만 함)."""

    def __enter__(self):
        self._old = getattr(_REUSE, "on", False)
        _REUSE.on = True
        return self

    def __exit__(self, *a):
        _REUSE.on = self._old
        return False


def cache_get(key):
    f = _cache_dir() / f"{key}.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("text"), str) or not d["text"].strip():
        return None
    try:
        os.utime(f)  # 최근에 쓴 것 (LRU)
    except OSError:
        pass
    return d


def cache_put(key, text, model):
    with _ULOCK:
        try:
            _write_json(_cache_dir() / f"{key}.json", {"text": text, "model": model, "t": int(time.time())})
            _cache_prune()
        except OSError:
            pass


def _cache_prune():
    """CACHE_MAX_N 개 · CACHE_MAX_BYTES 를 넘으면 오래 안 쓴 것부터 지움 (os.utime 으로 다시 쓴 때를 남김 · LRU)."""
    try:
        fs = []
        for f in _cache_dir().glob("*.json"):
            try:
                st = f.stat()
                fs.append((st.st_mtime, st.st_size, f))
            except OSError:
                pass
    except OSError:
        return
    fs.sort(key=lambda x: x[0])
    n, size = len(fs), sum(x[1] for x in fs)
    for _, sz, f in fs[:-1]:  # 막 넣은 것(가장 최근)은 남김
        if n <= CACHE_MAX_N and size <= CACHE_MAX_BYTES:
            break
        try:
            f.unlink()
        except OSError:
            continue
        n, size = n - 1, size - sz


def usage_of(d):
    """결과 JSON → {"input","output","cacheRead","cacheWrite","total"} (modelUsage 를 모두 더함 · 없으면 usage)."""
    keys = (("input", "inputTokens", "input_tokens"), ("output", "outputTokens", "output_tokens"),
            ("cacheRead", "cacheReadInputTokens", "cache_read_input_tokens"), ("cacheWrite", "cacheCreationInputTokens", "cache_creation_input_tokens"))
    out = {k: 0 for k, _, _ in keys}

    def num(x):
        return int(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 else 0
    mu = (d or {}).get("modelUsage")
    got = False
    if isinstance(mu, dict):
        for v in mu.values():
            if isinstance(v, dict):
                for k, a, _ in keys:
                    if a in v:
                        got = True
                    out[k] += num(v.get(a))
    if not got:
        u = (d or {}).get("usage")
        if isinstance(u, dict):
            for k, _, b in keys:
                out[k] += num(u.get(b))
    out["total"] = sum(out[k] for k, _, _ in keys)
    return out


def _today():
    return time.strftime("%Y-%m-%d")


def _load_usage():
    try:
        d = json.loads((HOME / USAGE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    days = d.get("days") if isinstance(d, dict) else None
    return days if isinstance(days, dict) else {}


def usage_today():
    """오늘 쓴 양 {calls, hits, total, input, output, cacheRead, cacheWrite} (숫자만)."""
    with _ULOCK:
        t = _load_usage().get(_today())
    base = {"calls": 0, "hits": 0, "total": 0, "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    if isinstance(t, dict):
        base.update({k: int(t[k]) for k in base if isinstance(t.get(k), (int, float))})
    return base


def _record(usage=None, cached=False, save=True):
    """한 번 물음(또는 저장해 둔 대답) → 지금 작업 합계 + 오늘 합계 (claude_usage.json · 숫자만)."""
    u = usage or {}
    with _ULOCK:
        if _JOB.get("on"):
            _JOB["hits" if cached else "calls"] += 1
            _JOB["total"] += int(u.get("total") or 0)
            _JOB["save"] = _JOB["save"] or bool(save)
            _JOB["old"] = _JOB["old"] or not save
        days = _load_usage()
        t = days.setdefault(_today(), {})
        t["hits" if cached else "calls"] = int(t.get("hits" if cached else "calls") or 0) + 1
        for k in ("input", "output", "cacheRead", "cacheWrite", "total"):
            t[k] = int(t.get(k) or 0) + int(u.get(k) or 0)
        keep = dict(sorted(days.items())[-USAGE_DAYS:])
        try:
            _write_json(HOME / USAGE_FILE, {"v": 1, "days": keep})
        except OSError:
            pass


def tokens_ko(n):
    """12345 → '12천' · 99999 → '10만' · 250000 → '25만' · 850 → '850'."""
    n = int(n or 0)
    if n < 1000:
        return str(n)
    k = round(n / 1000)
    return f"{k}천" if k < 100 else f"{round(n / 10000)}만"


def job_begin():
    """작업 시작 (app.start_job) — 이 작업의 클로드 사용 합계를 새로."""
    with _ULOCK:
        _JOB.clear()
        _JOB.update(on=True, calls=0, hits=0, total=0, save=False, old=False)


def job_end():
    """작업 끝 → 작업 기록 한 줄 (클로드를 안 썼으면 None). 예: '클로드 3번 · 약 12천 토큰 · 아껴 쓰기 (저장해 둔 대답 2번 다시 씀)'."""
    with _ULOCK:
        j = dict(_JOB)
        _JOB.clear()
        _JOB["on"] = False
    calls, hits = int(j.get("calls") or 0), int(j.get("hits") or 0)
    if not calls and not hits:
        return None
    parts = [f"클로드 {calls}번" if calls else "클로드는 새로 안 부름"]
    if calls and j.get("total"):
        parts.append(f"약 {tokens_ko(j['total'])} 토큰")
    mode = "아껴 쓰기" if j.get("save") else "예전처럼 (모두 Opus)"
    if hits:
        mode += f" (저장해 둔 대답 {hits}번 다시 씀)"
    parts.append(mode)
    return " · ".join(parts)


def saver():
    """화면용: 아껴 쓰기 켬/끔 + 오늘 쓴 양."""
    return {"save": save_on(), "today": usage_today()}


# ---------- 실행 ----------

def _exec(cmd, stdin_data, files, timeout, cancel, on_tick):
    """빈 임시 폴더(+그림 파일)에서 명령 실행 → (종료 코드, 표준 출력, 표준 오류, 멈춘 까닭 또는 None)."""
    with tempfile.TemporaryDirectory(prefix="futsal-claude-", ignore_cleanup_errors=True) as tmp:
        for nm, data in files:
            try:
                (Path(tmp) / nm).write_bytes(data)
            except OSError:
                pass
        try:
            p = subprocess.Popen(cmd, cwd=tmp, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=child_env(), **_flags())
        except OSError:
            raise ClaudeError("missing", MSG["missing"]) from None
        _track(p)
        res = {}

        def feed():
            try:  # 넣고 바로 닫음 (명령줄 길이 제한·프로세스 목록 노출 없이)
                p.stdin.write(stdin_data)
            except OSError:
                pass
            finally:
                try:
                    p.stdin.close()
                except OSError:
                    pass

        def drain(stream, key, cap):
            """한 줄기씩 따로 끝까지 읽음 (표준 오류가 파이프를 채워 멈추는 일 없이) · 마지막 cap 바이트만 남김."""
            buf = bytearray()
            try:
                while True:
                    b = stream.read(1 << 16)
                    if not b:
                        break
                    buf += b
                    if len(buf) > 2 * cap:
                        del buf[:-cap]
            except (OSError, ValueError):
                pass
            res[key] = bytes(buf[-cap:]).decode("utf-8", "replace")
        tf = threading.Thread(target=feed, daemon=True)
        to = threading.Thread(target=drain, args=(p.stdout, "out", 4 << 20), daemon=True)
        te = threading.Thread(target=drain, args=(p.stderr, "err", 64 << 10), daemon=True)
        tf.start()
        to.start()
        te.start()
        t0, why = time.time(), None
        while p.poll() is None:
            if cancel is not None and cancel.is_set():
                why = "cancel"
            elif time.time() - t0 > timeout:
                why = "timeout"
            if why:
                _kill(p)
                break
            if on_tick:
                on_tick(int(time.time() - t0))
            time.sleep(0.5)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill(p)
        to.join(10)
        te.join(5)
        for s in (p.stdout, p.stderr):
            try:
                s.close()
            except OSError:
                pass
    return p.returncode, res.get("out", ""), res.get("err", ""), why


def run(prompt, images=None, timeout=TIMEOUT, cancel=None, on_tick=None, task=None, check=None):
    """프롬프트(+그림 파일 [(원본 경로, 넣을 이름)]) → {"text", "model", "usage", "cached"}. 실패하면 ClaudeError (한국어 안내).
    cancel: threading.Event (멈추기 ✕) · on_tick(초): 0.5초마다 (진행 표시용).
    task: 일 이름 (TASK_MODEL) — 주면 '클로드 아껴 쓰기'(켰을 때)를 씀: 일에 맞는 모델 · 짧은 지시 · 그림 바로 보내기 · 저장해 둔 대답.
    check(대답 글): 저장하기 전 확인 — 예외가 나면 저장하지 않음 (대답은 그대로 돌려줌 · 부른 쪽이 예전처럼 처리).
    오류·멈춤·시간 초과는 저장하지 않음. 쓴 양(usage)은 숫자만 남김."""
    exe = find_exe()
    if not exe:
        raise ClaudeError("missing", MSG["missing"])
    save = bool(task) and save_on()
    model = model_for(task, save)
    imgs = _read_images(images)
    key = cache_key(task, model, prompt, imgs) if save else None
    if key and getattr(_REUSE, "on", False):
        hit = cache_get(key)
        if hit is not None:
            _record(None, cached=True, save=True)
            return {"text": hit["text"], "model": hit.get("model") or model, "usage": {}, "cached": True}
    feat = features(exe)
    if save:
        imgs = [prep_image(nm, data) for nm, data in imgs]
    else:
        imgs = [(nm, data, _mime(data)) for nm, data in imgs]
    inline = bool(save and imgs and feat.get("stream_in") and exe not in _INLINE_BAD)
    while True:
        system = system_prompt(task, inline) if save else None
        cmd = build_cmd(exe, bool(imgs), feat, model=model, system=system, inline=inline)
        data = stream_message(prompt, imgs) if inline else str(prompt)
        code, out, err, why = _exec(cmd, data.encode("utf-8"), [] if inline else [(nm, b) for nm, b, _ in imgs], timeout, cancel, on_tick)
        if why:
            raise ClaudeError(why, MSG[why])
        kind, d = explain(code, out, err)
        if inline and d is None and kind in ("error", "update"):
            _INLINE_BAD.add(exe)  # 그림 바로 보내기를 이 판이 못 받음 (결과 없이 끝남) → 이번 실행 동안은 예전처럼 폴더의 그림을 Read 로
            inline = False
            continue
        break
    if d is not None:
        _record(usage_of(d), save=save)
    if kind != "ok":
        if kind == "login":
            reset_status()
        raise ClaudeError(kind, MSG[kind])
    text = str(d.get("result") or "")
    if not text.strip():
        raise ClaudeError("error", MSG["error"])
    name = _model_of(d, model)
    if key:
        try:
            if check is not None:
                check(text)
            cache_put(key, text, name)
        except Exception:  # noqa: BLE001 — 모양이 틀린 대답은 저장하지 않음 (다시 누르면 새로 물음)
            pass
    return {"text": text, "model": name, "usage": usage_of(d), "cached": False}
