"""쉬는 동안 메모리 돌려주기: 작업이 끝나고 IDLE_SEC(5분) 동안 새 작업이 없으면 불러 둔 모델(얼굴·표정 · 글자 읽기 ·
소리 듣기 · 앱 안 누끼 · 받아쓰기)을 내려놓고 메모리를 OS 에 돌려줌. 다음에 쓸 때 다시 불러옴 (얼굴·글자·소리 0.1~0.3초).

모델은 작업(app.start_job) 안에서 쓰므로, 작업이 없을 때만(app 의 LOCK 을 잡고 확인) 내려놓음 → 쓰는 도중에 사라지지 않음.
작업이 멈춰도 잠깐 더 사는 모델 스레드(plan 의 글자 읽기 'model-ocr')가 있으면 그것도 끝날 때까지 기다림.
(합칠 때: 작업 밖에서 face·avmodels·thumb 세션을 쓰는 곳이 생기면 start_job 안으로 넣거나 스레드 이름을 'model-' 로)
app.py 에서: idle.start(LOCK, lambda: bool(JOB["name"])) + JOB_HOOKS 에 idle.touch.
"""
import ctypes
import gc
import sys
import threading
import time

IDLE_SEC = 300
MODEL_THREAD = "model-"  # 이 이름으로 시작하는 스레드(plan 의 글자 읽기 등)가 살아 있으면 안 내려놓음 (멈춘 작업이 남긴 것 포함)
CHECK_SEC = 30
_ST = {"last": time.time(), "dirty": False, "thread": None}


def touch(*_a, **_kw):
    """작업이 끝날 때마다 (JOB_HOOKS) → 여기서부터 IDLE_SEC 를 셈."""
    _ST["last"] = time.time()
    _ST["dirty"] = True


def _clear(mod_name, attr="_SESS", lock="_LOCK"):
    m = sys.modules.get(mod_name)
    d = getattr(m, attr, None) if m else None
    if not d:
        return 0
    lk = getattr(m, lock, None)
    if lk is not None and not lk.acquire(timeout=1):  # 아직 불러오는 중(멈춘 작업이 남긴 것 등) → 이번엔 건너뜀
        return 0
    try:
        n = len(d)
        d.clear()
    finally:
        if lk is not None:
            lk.release()
    return n


def trim():
    """해제한 메모리를 OS 에 돌려줌: Linux glibc 는 malloc_trim (안 하면 수백 MB 가 프로세스에 남음).
    Windows·macOS 는 큰 덩어리를 해제할 때 바로 돌려주므로 따로 할 일이 없음."""
    gc.collect()
    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass


def models_in_use():
    """작업 밖에서도 모델을 쓰는 스레드가 아직 살아 있는지 (멈춘 작업이 남긴 글자 읽기 스레드 등)."""
    return any(t.is_alive() and t.name.startswith(MODEL_THREAD) for t in threading.enumerate())


def release():
    """불러 둔 모델을 모두 내려놓음 → 내려놓은 것 수. (작업이 없을 때만 부를 것)"""
    n = _clear("face") + _clear("avmodels") + _clear("thumb")
    n += _clear("core", "_WHISPER", "_WHISPER_LOCK")
    trim()
    _ST["dirty"] = False
    return n


def start(lock, busy, idle_sec=None, log=None):
    """뒤에서 CHECK_SEC 마다 확인: 작업이 없고 마지막 작업이 끝난 지 idle_sec 가 지났으면 release()."""
    if _ST["thread"] is not None:
        return

    def loop():
        while True:
            time.sleep(CHECK_SEC)
            try:
                if not _ST["dirty"] or time.time() - _ST["last"] < (idle_sec or IDLE_SEC):
                    continue
                with lock:  # 이 사이엔 새 작업이 시작되지 않음
                    if busy() or models_in_use():
                        continue
                    n = release()
                if n and log:
                    log(f"쉬는 동안 모델 {n}개를 내려놓았어요 (메모리 반환)")
            except Exception:  # noqa: BLE001 — 곁가지가 앱을 깨뜨리지 않게
                pass

    t = threading.Thread(target=loop, daemon=True, name="idle-release")
    _ST["thread"] = t
    t.start()
