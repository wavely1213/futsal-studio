"""Windows 바로가기·작업 표시줄: 바탕화면·시작 메뉴 아이콘을 만들고, 켜진 창과 고정한 아이콘이 한 묶음이 되게 (AppUserModelID).

venv 의 pythonw.exe 는 진짜 Python 을 자식 프로세스로 띄우는 실행기라, 창은 그 자식(…\\Python3xx\\pythonw.exe)의 것이 됨.
아이디를 따로 주지 않으면 Windows 는 창을 그 pythonw 로 묶어서 고정한 아이콘과 켜진 창이 작업 표시줄에 둘로 보이고,
켜진 창을 '작업 표시줄에 고정'하면 아무것도 안 켜지는 pythonw 가 고정됨. → 프로세스와 바로가기(.lnk)에 같은 아이디를 줌.
.lnk 는 COM(IShellLinkW·IPropertyStore)으로 직접 쓰고, 안 되면 예전처럼 PowerShell(WScript.Shell)로 (아이디 없이).
이미 같은 내용으로 만들었으면(ENGINE_HOME/shortcut.json) 다시 만들지 않음 → 사용자가 지운 아이콘이 되살아나지 않고,
켤 때마다 숨은 PowerShell 을 띄우지 않음. 표준 라이브러리(ctypes)만 씀. Windows 실기 미검증 (KNOWN_ISSUES I-015).
"""
import base64
import ctypes
import json
import subprocess
import sys
import threading
import uuid
from pathlib import Path

APP_NAME = "풋살사관학교 스튜디오"
APP_USER_MODEL_ID = "FutsalAcademy.Studio"
MARK_VER = 2  # 바로가기 내용 규칙이 바뀌면 올림 → 한 번 다시 만듦


def _guid(s):
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]
    return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)


CLSID_SHELL_LINK = "00021401-0000-0000-C000-000000000046"
IID_SHELL_LINK_W = "000214F9-0000-0000-C000-000000000046"
IID_PERSIST_FILE = "0000010b-0000-0000-C000-000000000046"
IID_PROPERTY_STORE = "886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"
PKEY_APP_USER_MODEL = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"  # System.AppUserModel.* (ID 는 pid 5)
VT_LPWSTR = 31


class PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", ctypes.c_ubyte * 16), ("pid", ctypes.c_uint32)]


class PropVariant(ctypes.Structure):
    """PROPVARIANT (64비트: 24바이트) 중 글자(VT_LPWSTR) 하나만 쓰는 꼴."""
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]


def _method(obj, index, *argtypes):
    """COM 개체의 index 번째 함수 (반환값 HRESULT 가 실패면 OSError)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    fn = proto(vtbl[index])
    return lambda *a: fn(obj, *a)


def _query(obj, iid):
    out = ctypes.c_void_p()
    _method(obj, 0, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(_guid(iid)), ctypes.byref(out))
    return out


def _release(obj):
    if obj:
        try:
            vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(obj)
        except OSError:
            pass


def write_shortcut(path, target, args, workdir, icon, app_id=APP_USER_MODEL_ID):
    """.lnk 한 개를 COM 으로 씀 (대상·인자·시작 폴더·아이콘 + AppUserModelID). 실패하면 OSError. COM 을 켠 스레드에서 부름."""
    ole32 = ctypes.OleDLL("ole32")
    link = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(_guid(CLSID_SHELL_LINK)), None, 1, ctypes.byref(_guid(IID_SHELL_LINK_W)), ctypes.byref(link))
    store = persist = None
    try:
        w = ctypes.c_wchar_p
        _method(link, 20, w)(str(target))                  # SetPath
        _method(link, 11, w)(str(args))                    # SetArguments
        _method(link, 9, w)(str(workdir))                  # SetWorkingDirectory
        _method(link, 17, w, ctypes.c_int)(str(icon), 0)   # SetIconLocation
        if app_id:
            store = _query(link, IID_PROPERTY_STORE)
            key = PropertyKey(pid=5)
            ctypes.memmove(key.fmtid, ctypes.byref(_guid(PKEY_APP_USER_MODEL)), 16)
            val = PropVariant(vt=VT_LPWSTR, pwszVal=app_id)
            _method(store, 6, ctypes.c_void_p, ctypes.c_void_p)(ctypes.byref(key), ctypes.byref(val))  # SetValue
            _method(store, 7)()                                                                   # Commit
        persist = _query(link, IID_PERSIST_FILE)
        _method(persist, 6, w, ctypes.c_int)(str(path), 1)  # Save
    finally:
        _release(store)
        _release(persist)
        _release(link)


def _folder(csidl):
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value) if buf.value else None


def shortcut_paths():
    """만들 바로가기: 바탕화면·시작 메뉴 + (예전에 고정해 둔 것이 있으면) 작업 표시줄 고정 아이콘."""
    out = [d / f"{APP_NAME}.lnk" for d in (_folder(0x10), _folder(0x02)) if d]  # CSIDL_DESKTOPDIRECTORY · CSIDL_PROGRAMS
    appdata = _folder(0x1A)  # CSIDL_APPDATA
    if appdata:
        pin = appdata / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar" / f"{APP_NAME}.lnk"
        if pin.exists():
            out.append(pin)  # 예전에 고정한 작업 표시줄 아이콘도 실행기를 거치게
    return out


def set_process_app_id(app_id=APP_USER_MODEL_ID):
    """이 프로세스(=앱 창)의 작업 표시줄 아이디. 창을 만들기 전에 불러야 함."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
        return True
    except (AttributeError, OSError):
        return False


def _spec(app_dir, gui_python):
    return {"v": MARK_VER, "target": str(gui_python), "args": f'"{Path(app_dir) / "updater.py"}" --launch',
            "dir": str(app_dir), "icon": str(Path(app_dir) / "icon.ico")}


def _mark_file(engine_home):
    return Path(engine_home) / "shortcut.json"


def _read_mark(engine_home):
    try:
        d = json.loads(_mark_file(engine_home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_mark(engine_home, spec, app_id):
    try:
        f = _mark_file(engine_home)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(dict(spec, appid=bool(app_id)), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _com_all(spec):
    """모든 바로가기를 COM 으로 (아이디 포함). 하나라도 실패하면 False."""
    ole32 = ctypes.OleDLL("ole32")
    try:
        ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    except OSError:
        pass
    try:
        for p in shortcut_paths():
            write_shortcut(p, spec["target"], spec["args"], spec["dir"], spec["icon"])
        return True
    except Exception:  # noqa: BLE001 — PowerShell 로 다시
        return False
    finally:
        try:
            ctypes.windll.ole32.CoUninitialize()
        except OSError:
            pass


def _powershell(spec, log):
    """예전 방식(아이디 없이): WScript.Shell 로 바로가기. 끝나면 True."""
    def q(v):  # PowerShell '…' 안의 작은따옴표
        return str(v).replace("'", "''")
    ps = f"""
$w = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('Programs')) ''))
$pin = Join-Path $env:APPDATA 'Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\TaskBar'
if (Test-Path -LiteralPath (Join-Path $pin '{APP_NAME}.lnk')) {{ $dirs += $pin }}
foreach ($dir in $dirs) {{
  $p = Join-Path $dir '{APP_NAME}.lnk'
  $s = $w.CreateShortcut($p)
  $s.TargetPath = '{q(spec["target"])}'
  $s.Arguments = '{q(spec["args"])}'
  $s.WorkingDirectory = '{q(spec["dir"])}'
  $s.IconLocation = '{q(spec["icon"])}'
  $s.Save()
}}"""
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                           capture_output=True, creationflags=0x08000000, timeout=60)  # CREATE_NO_WINDOW
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        log(f"바로가기를 만들지 못했어요 · {e}")
        return False


def prepare(app_dir, gui_python, engine_home, log=print):
    """앱 창을 만들기 전에 (Windows): 바로가기가 지금 실행 경로와 같으면 그대로, 다르거나 처음이면 다시 만듦.
    아이디가 든 바로가기면 이 프로세스에도 같은 아이디를 줌 (둘이 다르면 오히려 작업 표시줄에 둘로 보이므로 짝이 맞을 때만)."""
    if sys.platform != "win32":
        return False
    spec = _spec(app_dir, gui_python)
    mark = _read_mark(engine_home)
    if {k: mark.get(k) for k in spec} == spec:
        return set_process_app_id() if mark.get("appid") else False
    res = {}
    t = threading.Thread(target=lambda: res.update(ok=_com_all(spec)), daemon=True)  # COM 은 따로 켠 스레드에서
    t.start()
    t.join(15)
    if res.get("ok"):
        _write_mark(engine_home, spec, True)
        return set_process_app_id()

    def fallback():
        if _powershell(spec, log):
            _write_mark(engine_home, spec, False)
    threading.Thread(target=fallback, daemon=True).start()
    return False
