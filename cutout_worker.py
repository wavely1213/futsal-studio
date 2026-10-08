"""누끼(배경 지우기)를 따로 프로세스에서 (worker.py 틀).

- 고품질(BiRefNet)은 한 번에 6.5GB 넘게 씀 (1920 장면 기준 측정 · 입력은 모델이 1024x1024 로 고정) → 남은 메모리가
  HQ_MIN_MB 보다 적으면 빠른 누끼(u2net_human_seg, 약 0.6GB)로 하고 '메모리가 부족해 빠른 누끼로 했어요' 안내
- 고품질 프로세스가 죽거나(메모리 부족 등) 실패하면 빠른 누끼로 한 번 더
- 프로세스가 끝나면 모델·계산 메모리를 전부 OS 에 돌려줌 (앱 서버엔 누끼 세션이 남지 않음)
- 자식 안에서는 onnxruntime 의 CPU 메모리 풀(arena)을 끔: 마스크는 똑같고 빠른 누끼가 942MB → 462MB
마스크 계산은 thumb.remove_bg 그대로 (같은 모델·같은 전처리 → 앱 안에서 돌린 것과 같은 결과).
- AI 추천 썸네일 분석의 주인공 자동 누끼(cut_auto)도 여기로: 장면 여러 장을 자식 하나에서 thumb.cut_auto 그대로
  (다듬기·품질 검사·캐시 파일 같음) → 앱 프로세스는 누끼 모델(BiRefNet·u2net)을 아예 불러오지 않음 (D-069)
"""
import os

import core
import studiolog
import worker

HQ_MIN_MB = int(os.environ.get("FUTSAL_HQ_MIN_MB") or 7500)  # 고품질 최대 6.5GB + 여유
LOW_MEM_NOTE = "메모리가 부족해 빠른 누끼로 했어요"
FAIL_NOTE = "고품질 누끼가 끝까지 못 해서 빠른 누끼로 했어요"
FAIL_MSG = "누끼를 따지 못했어요"
AUTO_FAIL_MSG = "자동 누끼를 따지 못했어요"  # 썸네일 분석의 자동 누끼 (분석은 누끼 없이 계속 · 화면 분석 줄에 이 한 줄)
MEM_MSG = "메모리가 부족해요. 다른 프로그램을 닫고 다시 해 주세요"


def remove_bg(src_path, kind="hq", cancel=None, procs=None, log=None):
    """→ (PNG 경로, 실제로 쓴 kind, 안내 문장 또는 None). 실패하면 worker.WorkerError · 멈추면 worker.Cancelled."""
    from pathlib import Path
    kind, note = _pick_kind(kind, log)
    try:
        try:
            out = worker.call("cutout_worker:_child_remove_bg", str(src_path), kind, cancel=cancel, procs=procs)
        except worker.WorkerError as e:
            if kind != "hq":
                raise
            if log:
                log(f"  고품질 누끼 실패 → 빠른 누끼로 · {e}")
            studiolog.trace(e, "고품질 누끼 오류 위치")
            kind = "fast"
            note = LOW_MEM_NOTE if worker.out_of_memory(e) else FAIL_NOTE  # 그 밖(DLL·백신 차단 등)은 메모리 탓이라 하지 않음
            out = worker.call("cutout_worker:_child_remove_bg", str(src_path), kind, cancel=cancel, procs=procs)
    except worker.WorkerError as e:
        raise worker.WorkerError(fail_msg(e), e.code, e.kind) from e
    return Path(out), kind, note


def _pick_kind(kind, log=None):
    """고품질인데 남은 메모리가 HQ_MIN_MB 보다 적으면 빠른 누끼로 → (쓸 kind, 안내 또는 None)."""
    if kind == "hq":
        free = worker.avail_mb()
        if free is not None and free < HQ_MIN_MB:
            if log:
                log(f"  남은 메모리 {free}MB → 빠른 누끼로")
            return "fast", LOW_MEM_NOTE
    return kind, None


def cut_auto(name, jobs, kind="fast", cancel=None, procs=None, log=None):
    """썸네일 분석의 주인공 자동 누끼 여러 장을 따로 프로세스 하나에서 (모델은 한 번만 불러옴 · 끝나면 메모리 반환 · 죽어도 앱은 그대로).
    jobs: [(장면 시각, 주인공 상자(0~1) 또는 None, 얼굴 목록, 박힌 글자 상자)] → [(시각, PNG 경로, 품질)].
    자식은 thumb.cut_auto 를 그대로 부름 (같은 다듬기·품질 검사 · 같은 캐시 파일 — 앞에서 딴 장면은 실패해도 파일로 남음).
    분석은 처음부터 빠른 누끼(약 0.5GB)라 메모리 확인으로 바뀔 것이 없지만, 고품질을 넘기면 remove_bg 와 같은 확인을 함.
    실패하면 worker.WorkerError('자동 누끼를 따지 못했어요 · …' — 실패 카드가 쓰는 꼴) · 멈추면 worker.Cancelled."""
    from pathlib import Path
    kind, _ = _pick_kind(kind, log)
    try:
        out = worker.call("cutout_worker:_child_cut_auto", str(name), [list(j) for j in jobs], kind, cancel=cancel, procs=procs)
    except worker.WorkerError as e:
        raise worker.WorkerError(fail_msg(e, AUTO_FAIL_MSG), e.code, e.kind) from e
    return [(t, Path(p), q) for t, p, q in out]


def fail_msg(e, head=FAIL_MSG):
    """누끼 프로세스 실패 → 실패 카드(trouble.explain)가 쓸 글: 앞은 쉬운 한국어 한 줄(head), ' · ' 뒤는 원문 (종류 고르기·studio.log 용)."""
    raw = str(e)
    if e.kind == "died":  # 틀(worker)이 붙인 '작업 프로세스가…' 안내는 빼고 원문만 (카드에 두 번 '못 했어요'가 안 나오게)
        raw = raw.split(" · ", 1)[1] if " · " in raw else f"exit code {e.code}"
    if core._DLL_RE.search(raw) and "Visual C++" not in raw:  # 자식이 onnxruntime 을 못 불러옴 = 앱 안에서와 같은 안내 (thumb.remove_bg)
        return f"{head} · {core.vc_runtime_msg('누끼 따기')} · {raw}"
    if worker.out_of_memory(e):  # 종료 코드로만 안 경우에도 실패 카드가 '메모리' 종류로 (trouble 의 memory 규칙)
        return f"{head} · {MEM_MSG} · MemoryError · {raw}"
    return f"{head} · {raw}"


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


def _child_cut_auto(name, jobs, kind="fast"):
    """(자식 프로세스에서) 장면마다 thumb.cut_auto 를 그대로 부름 (진행 표시는 부모 화면으로) → [[시각, PNG 경로 글자, 품질]].
    한 장이 실패하면 거기서 멈춤 (예전 앱 안 분석과 같음 · 앞에서 딴 것은 파일로 남음)."""
    _arena_off()
    import thumb
    out = []
    for i, (t, box, faces, tboxes) in enumerate(jobs):
        core.set_progress(label="썸네일 분석", item=name, pct=int(i * 100 / max(1, len(jobs))), detail=f"주인공 누끼 따는 중 {i + 1}/{len(jobs)}")
        p, q = thumb.cut_auto(name, t, box, kind, faces=faces, tboxes=tboxes)
        out.append([t, str(p), q])
    return out
