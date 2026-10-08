"""설치 확인 (시작하기 (Windows).bat 이 부름) — 표준 라이브러리만 · 오래된 Python 에서도 돌아가는 문법만.

  python setup_check.py python      이 Python 으로 구성요소 폴더(.venv)를 만들어도 되는지 (3.10~3.14 · 64비트 x64)
  python setup_check.py venv         지금 .venv 를 그대로 써도 되는지 (깨졌거나·범위 밖·3.10 인데 더 새 Python 이 있으면 새로)
  python setup_check.py longpath     앱 폴더 경로가 너무 길어 설치가 실패할지 (Windows 260자 제한이 켜져 있을 때)
  python setup_check.py vcredist     받아쓰기·누끼에 필요한 Microsoft Visual C++ 구성요소(msvcp140)가 있는지
  python setup_check.py place        앱 폴더가 임시 폴더(압축 파일 안에서 바로 실행·압축 프로그램이 잠깐 푼 곳)가 아닌지
  python setup_check.py pip <기록>    pip 설치가 실패한 이유를 한국어 한 줄로 (bat 이 남긴 설치 기록 파일을 읽음 · 결과는 늘 1)

되면 0, 아니면 1 (이유는 화면에 한국어로). bat 은 결과로 다음 할 일을 정함.
"""
import os
import re
import subprocess
import sys
import sysconfig
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PY_MIN, PY_MAX = (3, 10), (3, 14)   # 휠(onnxruntime·ctranslate2)이 있고 앱이 시험된 범위 · 3.15 는 아직 휠이 없음
PY_PREFER = (3, 11)                 # 3.10 은 2026-10 지원 종료 → 받기 엔진(yt-dlp)이 곧 뺌: 더 새 Python 이 있으면 새로 만듦
PLATFORM = "win-amd64"              # ARM64·32비트 Python 은 ctranslate2·onnxruntime 휠이 없어 설치가 실패함
VENV_TAIL = len(r"\.venv\Lib\site-packages\onnxruntime\tools\ort_format_model\ort_flatbuffers_py\fbs"
                r"\RuntimeOptimizationRecordContainerEntry.py")  # 구성요소 중 가장 긴 파일 경로 (앱 폴더 뒤)
MAX_PATH = 259
PY_DIRECT = "https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.exe"  # 설치 관리자가 아닌 독립 설치 파일 (bat 도 같은 주소)
VC_MIN = (14, 40)                   # ctranslate2·onnxruntime 은 MSVC 14.4x 로 빌드 → 그보다 예전 msvcp140 이면 멈출 수 있음


def say(msg):
    try:
        print(msg)
    except Exception:  # noqa: BLE001 — 출력이 안 돼도 결과(종료 코드)는 그대로
        pass


def _ok_python(ver, plat):
    if tuple(ver[:2]) > PY_MAX:  # 막 나온 Python: 받아쓰기·누끼 구성요소(onnxruntime·ctranslate2)가 아직 그 버전용으로 안 나옴
        return ("Python %d.%d 은(는) 너무 새로 나와서 아직 받아쓰기 구성요소가 없어요. "
                "Python 3.14 를 함께 설치해 주세요 (%d.%d 은 지우지 않아도 돼요)" % (tuple(ver[:2]) * 2))
    if tuple(ver[:2]) < PY_MIN:
        return "Python %d.%d 은(는) 너무 오래됐어요 (3.10~3.14 필요 · 권장 3.13)" % tuple(ver[:2])
    if sys.platform == "win32" and plat != PLATFORM:
        return "이 Python 은 %s 용이에요. 64비트(x64) Python 이 필요해요 ('Windows installer (64-bit)')" % plat
    return None


def check_python():
    why = _ok_python(sys.version_info, sysconfig.get_platform())
    if why:
        say(why)
        return 1
    return 0


def _venv_info(exe):
    """.venv 의 Python → ((major, minor), platform) · 실행이 안 되면(바탕 Python 을 지웠거나 옮김) None."""
    try:
        r = subprocess.run([str(exe), "-c", "import sys,sysconfig;print(sys.version_info[0],sys.version_info[1],sysconfig.get_platform())"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = (r.stdout or "").split()
    if r.returncode or len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1])), parts[2]
    except ValueError:
        return None


def check_venv():
    exe = APP_DIR / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not exe.exists():
        return 0
    info = _venv_info(exe)
    if info is None:
        say("구성요소 폴더(.venv)를 만든 Python 이 이 PC에서 지워졌거나 옮겨졌어요. 새로 만들어요.")
        return 1
    why = _ok_python(info[0], info[1])
    if why:
        say("구성요소 폴더(.venv)를 새로 만들어요 · " + why)
        return 1
    if info[0] < PY_PREFER <= sys.version_info[:2]:
        say("구성요소 폴더(.venv)를 더 새 Python(%d.%d)으로 새로 만들어요 (Python %d.%d 은 곧 지원이 끝나요)"
            % (sys.version_info[0], sys.version_info[1], info[0][0], info[0][1]))
        return 1
    return 0


def _long_paths_on():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            return winreg.QueryValueEx(k, "LongPathsEnabled")[0] == 1
    except (ImportError, OSError):
        return False


def check_longpath():
    n = len(str(APP_DIR)) + VENV_TAIL
    if n <= MAX_PATH or (sys.platform == "win32" and _long_paths_on()):
        return 0
    say("앱 폴더 경로가 너무 길어서 구성요소 설치가 실패해요 (%d자 · 윈도우 한도 260자):" % n)
    say("  " + str(APP_DIR))
    say("  폴더를 'C:\\풋살스튜디오' 처럼 짧은 곳으로 옮긴 뒤 이 파일을 다시 실행해 주세요.")
    return 1


def _file_version(path):
    """Windows 파일 버전 (a, b, c, d) · 모르면 None."""
    import ctypes
    from ctypes import wintypes
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return None
    p, n = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n)) or not p.value:
        return None

    class Fixed(ctypes.Structure):
        _fields_ = [("sig", wintypes.DWORD), ("struc", wintypes.DWORD), ("ms", wintypes.DWORD), ("ls", wintypes.DWORD)]
    f = ctypes.cast(p, ctypes.POINTER(Fixed)).contents
    return (f.ms >> 16, f.ms & 0xFFFF, f.ls >> 16, f.ls & 0xFFFF)


def check_vcredist():
    if sys.platform != "win32":
        return 0
    sysdir = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32"
    for dll in ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140_1.dll"):
        p = sysdir / dll
        if not p.is_file():
            say("Microsoft Visual C++ 구성요소(%s)가 없어요." % dll)
            return 1
        try:
            v = _file_version(p)
        except Exception:  # noqa: BLE001 — 버전을 못 읽으면 있는 것으로
            v = None
        if v and v[:2] < VC_MIN:
            say("Microsoft Visual C++ 구성요소(%s)가 오래됐어요 (%d.%d)." % (dll, v[0], v[1]))
            return 1
    return 0


_TEMP_NAME = re.compile(r"^(temp\d+_.+\.zip|rar\$ex[\w.]*|7zo[0-9a-f]+(\.tmp)?|bnz\.[0-9a-f]+)$", re.I)
# 탐색기(Temp1_x.zip)·WinRAR(Rar$EX…)·7-Zip(7zO…)·반디집(BNZ.…)이 잠깐 푸는 폴더 · 알집 등 나머지는 TEMP 아래인지로 잡음


def _temp_dirs():
    out = []
    for var in ("TEMP", "TMP"):
        v = os.environ.get(var)
        if v:
            # realpath: 사용자 이름이 길거나 한글이면 TEMP 가 짧은 이름(C:\Users\HONGGI~1\…)이라 앱 폴더(긴 이름)와 그냥은 안 맞음
            for x in (os.path.abspath(v), os.path.realpath(v)):
                x = os.path.normcase(x).rstrip("\\/")
                if x not in out:
                    out.append(x)
    return out


def _in_temp(path):
    p = os.path.normcase(os.path.realpath(str(path)))
    for t in _temp_dirs():
        if p == t or p.startswith(t + os.sep) or p.startswith(t + "/"):
            return True
    return any(_TEMP_NAME.match(x) for x in Path(p).parts[1:])


def check_place():
    if not _in_temp(APP_DIR):
        return 0
    say("[안내] 앱 파일이 임시 폴더에 있어요. 압축 파일 안에서 바로 실행하면 이렇게 돼요 (나중에 저절로 지워져 앱이 사라져요):")
    say("  " + str(APP_DIR))
    say("  받은 압축 파일을 오른쪽 클릭 → '압축 풀기'(모두 압축 풀기)로 'C:\\풋살스튜디오' 같은 곳에 푼 뒤,")
    say("  그 폴더의 '시작하기 (Windows).bat' 을 다시 실행해 주세요.")
    return 1


_NET = re.compile(r"NewConnectionError|Max retries exceeded|getaddrinfo failed|Temporary failure in name resolution|"
                  r"Name or service not known|ConnectTimeout|ReadTimeout|timed out|ProxyError|Could not fetch URL|"
                  r"Connection aborted|Connection reset|connection broken|RemoteDisconnected|IncompleteRead", re.I)


def pip_reason(text, ver=None):
    """pip 실패 기록 → (한국어 한 줄, 영어 원인 줄 · 없으면 '')."""
    t = text or ""
    ver = tuple((ver or sys.version_info)[:2])
    err = ""
    for ln in t.splitlines():
        if re.search(r"^\s*(ERROR|error):|Error:", ln):
            err = ln.strip()
    if re.search(r"CERTIFICATE_VERIFY_FAILED|SSLError|SSL: ", t):
        why = "인터넷 보안 연결이 막혔어요. 백신·회사 보안 프로그램이 막고 있으면 잠시 끄거나 다른 인터넷(휴대폰 핫스팟)으로 다시 실행해 주세요."
    elif _NET.search(t):
        why = "구성요소를 받는 중 인터넷이 끊겼어요. 인터넷 연결을 확인하고 이 파일을 다시 실행해 주세요."
    elif re.search(r"No space left|WinError 112|Errno 28|디스크 공간이 부족", t):
        why = "저장 공간이 부족해요 (약 2GB 필요). 공간을 비운 뒤 이 파일을 다시 실행해 주세요."
    elif re.search(r"WinError 5\b|WinError 32\b|Access is denied|액세스가 거부|being used by another process|PermissionError", t):
        why = "파일이 사용 중이라 설치하지 못했어요. 앱 창을 모두 닫고(백신 검사 중이면 끝난 뒤) 이 파일을 다시 실행해 주세요."
    elif re.search(r"No matching distribution found|Could not find a version that satisfies|Requires-Python|requires a different Python", t):
        why = ("이 Python(%d.%d)에 맞는 구성요소가 아직 없어요. Python 3.14 (64비트)를 설치한 뒤 이 파일을 다시 실행해 주세요: %s"
               % (ver[0], ver[1], PY_DIRECT))
    elif re.search(r"Failed building wheel|Failed to build|subprocess-exited-with-error|metadata-generation-failed|"
                   r"Microsoft Visual C\+\+ 14", t):
        # 이 Python 용 완성본(휠)이 없어 직접 만들려다 실패 — 막 나온 Python 에서 흔함 (인터넷 탓이 아님)
        why = ("이 Python(%d.%d)에 맞게 미리 만든 구성요소가 없어 설치하지 못했어요. Python 3.14 (64비트)를 설치한 뒤 이 파일을 다시 실행해 주세요: %s"
               % (ver[0], ver[1], PY_DIRECT))
    elif re.search(r"Could not open requirements file", t):
        why = "압축을 다 풀지 않고 실행했어요. 압축 파일을 '압축 풀기'로 푼 뒤 그 폴더에서 다시 실행해 주세요."
    else:
        why = "구성요소를 설치하지 못했어요. 인터넷 연결을 확인하고 이 파일을 다시 실행해 주세요. 계속되면 아래 기록 파일을 보내 주세요."
    return why, err


def check_pip(log):
    try:
        raw = Path(log).read_bytes() if log else b""
    except OSError:
        raw = b""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:  # 한국어 윈도우에서 PYTHONUTF8 없이 남은 기록은 cp949
        text = raw.decode("cp949", errors="replace")
    why, err = pip_reason(text)
    if err:
        say("  (" + err[:300] + ")")
    if log:
        say("  설치 기록: " + str(log))
    say("[안내] " + why)
    return 1


def main(argv):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    what = argv[1] if len(argv) > 1 else ""
    if what == "pip":
        return check_pip(argv[2] if len(argv) > 2 else "")
    fn = {"python": check_python, "venv": check_venv, "longpath": check_longpath, "vcredist": check_vcredist,
          "place": check_place}.get(what)
    if fn is None:
        say(__doc__)
        return 2
    return fn()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
