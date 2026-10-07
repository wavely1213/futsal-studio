"""설치 확인 (시작하기 (Windows).bat 이 부름) — 표준 라이브러리만 · 오래된 Python 에서도 돌아가는 문법만.

  python setup_check.py python      이 Python 으로 구성요소 폴더(.venv)를 만들어도 되는지 (3.10~3.14 · 64비트 x64)
  python setup_check.py venv         지금 .venv 를 그대로 써도 되는지 (깨졌거나·범위 밖·3.10 인데 더 새 Python 이 있으면 새로)
  python setup_check.py longpath     앱 폴더 경로가 너무 길어 설치가 실패할지 (Windows 260자 제한이 켜져 있을 때)
  python setup_check.py vcredist     받아쓰기·누끼에 필요한 Microsoft Visual C++ 구성요소(msvcp140)가 있는지

되면 0, 아니면 1 (이유는 화면에 한국어로). bat 은 결과로 다음 할 일을 정함.
"""
import os
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
VC_MIN = (14, 40)                   # ctranslate2·onnxruntime 은 MSVC 14.4x 로 빌드 → 그보다 예전 msvcp140 이면 멈출 수 있음


def say(msg):
    try:
        print(msg)
    except Exception:  # noqa: BLE001 — 출력이 안 돼도 결과(종료 코드)는 그대로
        pass


def _ok_python(ver, plat):
    if not (PY_MIN <= tuple(ver[:2]) <= PY_MAX):
        return "Python %d.%d 은(는) 쓸 수 없어요 (3.10~3.14 필요 · 권장 3.13)" % tuple(ver[:2])
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


def main(argv):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    what = argv[1] if len(argv) > 1 else ""
    fn = {"python": check_python, "venv": check_venv, "longpath": check_longpath, "vcredist": check_vcredist}.get(what)
    if fn is None:
        say(__doc__)
        return 2
    return fn()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
