"""누끼(배경 지우기)를 따로 프로세스에서 (worker.py 틀).

- 고품질(BiRefNet)은 한 번에 6.5GB 넘게 씀 (1920 장면 기준 측정 · 입력은 모델이 1024x1024 로 고정) → 남은 메모리가
  HQ_MIN_MB 보다 적으면 빠른 누끼(u2net_human_seg, 약 0.6GB)로 하고 '메모리가 부족해 빠른 누끼로 했어요' 안내
- 고품질 프로세스가 죽거나(메모리 부족 등) 실패하면 빠른 누끼로 한 번 더
- 프로세스가 끝나면 모델·계산 메모리를 전부 OS 에 돌려줌 (앱 서버엔 누끼 세션이 남지 않음)
- 자식 안에서는 onnxruntime 의 CPU 메모리 풀(arena)을 끔: 마스크는 똑같고 빠른 누끼가 942MB → 462MB
마스크 계산은 thumb.remove_bg 그대로 (같은 모델·같은 전처리 → 앱 안에서 돌린 것과 같은 결과).
"""
import os

import worker

HQ_MIN_MB = int(os.environ.get("FUTSAL_HQ_MIN_MB") or 7500)  # 고품질 최대 6.5GB + 여유
LOW_MEM_NOTE = "메모리가 부족해 빠른 누끼로 했어요"
FAIL_NOTE = "고품질 누끼가 끝까지 못 해서 빠른 누끼로 했어요"


def remove_bg(src_path, kind="hq", cancel=None, procs=None, log=None):
    """→ (PNG 경로, 실제로 쓴 kind, 안내 문장 또는 None). 실패하면 worker.WorkerError · 멈추면 worker.Cancelled."""
    from pathlib import Path
    note = None
    if kind == "hq":
        free = worker.avail_mb()
        if free is not None and free < HQ_MIN_MB:
            kind, note = "fast", LOW_MEM_NOTE
            if log:
                log(f"  남은 메모리 {free}MB → 빠른 누끼로")
    try:
        out = worker.call("cutout_worker:_child_remove_bg", str(src_path), kind, cancel=cancel, procs=procs)
    except worker.WorkerError as e:
        if kind != "hq":
            raise
        if log:
            log(f"  고품질 누끼 실패 → 빠른 누끼로 · {e}")
        kind = "fast"
        note = LOW_MEM_NOTE if e.kind == "died" or "memory" in str(e).lower() or "alloc" in str(e).lower() else FAIL_NOTE
        out = worker.call("cutout_worker:_child_remove_bg", str(src_path), kind, cancel=cancel, procs=procs)
    return Path(out), kind, note


def _arena_off():
    """이 (자식) 프로세스 안에서 만드는 onnxruntime 세션은 CPU 메모리 풀을 끔 (결과는 같고 메모리는 덜 잡음)."""
    import onnxruntime as ort
    base = ort.InferenceSession
    if getattr(base, "_futsal_arena_off", False):
        return

    class Sess(base):
        _futsal_arena_off = True

        def __init__(self, path, sess_options=None, *a, **kw):
            if sess_options is None:
                sess_options = ort.SessionOptions()
                sess_options.enable_cpu_mem_arena = False
                sess_options.enable_mem_pattern = False
            super().__init__(path, sess_options, *a, **kw)

    ort.InferenceSession = Sess


def _child_remove_bg(src, kind):
    """(자식 프로세스에서) thumb.remove_bg 를 그대로 부름 → 결과 PNG 경로 글자."""
    from pathlib import Path
    _arena_off()
    import thumb
    return str(thumb.remove_bg(Path(src), kind))
