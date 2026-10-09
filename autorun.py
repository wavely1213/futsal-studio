"""끝까지 자동으로 (E7 · D-200~D-203): 보관함 영상 하나 → 편집점 찾기 → 가편집·쇼츠 포인트 → MSG 롱폼 → 내보내기
→ 쇼츠 N개(포인트 하나 = 쇼츠 하나 · 저마다 MSG + 내보내기) → 결과물마다 AI 추천 썸네일 → 올리기 키트 → '확인할 것' 목록.

- 한 작업(app.start_job) 안에서 차례로 한다. 단계마다 분석 폴더의 auto_run.json 에 남겨, 멈추거나(✕) 프로그램이 꺼져도
  [이어서 하기]로 끝난 단계·결과물은 건너뛴다 (꺼짐은 studiolog 실행 표시 → app.CRASH 카드 · D-072 와 같은 길).
- 단계가 실패해도 만든 결과물은 그대로 두고 다음 단계로 간다 (편집점 찾기·가편집만 멈춤). MSG 를 못 넣은 결과물은 가편집 그대로 내보낸다.
- 유튜브에는 올리지 않는다 (키트까지 · 올리기는 사람이 7단계에서 누름).
- 썸네일 그림은 썸네일 편집기의 AI 추천(thumb.html renderDoc)이 그린다 — 열려 있는 스튜디오·편집실 화면이 보이지 않는 틀(iframe)로
  썸네일 편집기를 열어 그리고 /api/auto/thumb 로 돌려준다 (request_thumb · claim · deliver). 화면이 없으면 그 단계만 '확인할 것'.
"""
import copy
import json
import re
import threading
import time
import uuid

import core
import editor
import studiolog
import updater

JOB = "끝까지 자동으로"
PATH = "/api/auto"           # 실행 표시(꺼짐 카드 [이어서 하기])에 남기는 주소
STATE_FILE = "auto_run.json"  # 분석 폴더 안 (이름 바꾸기 때 분석 폴더와 함께 옮겨짐)
VER = 1
SHORTS_MAX = 6
SHORTS_DEFAULT = 3
LEVELS = ("담백", "보통", "듬뿍")
PRESETS = ("youtube", "small")
STEPS = (("analyze", "편집점 찾기"), ("rough", "가편집·쇼츠 포인트"), ("msg_long", "MSG 롱폼"), ("export_long", "롱폼 내보내기"),
         ("msg_shorts", "쇼츠 MSG"), ("export_shorts", "쇼츠 내보내기"), ("thumbs", "썸네일"), ("kits", "올리기 키트"),
         ("check", "확인할 것 정리"))
LABEL = dict(STEPS)
FATAL = ("analyze", "rough")   # 이 단계가 안 되면 뒤 단계가 쓸 재료가 없음
LOW_P = 0.4                    # 받아쓰기 낱말 확신이 이보다 낮으면 '자막 확인'
CAPTION_MAX = 8                # '자막 확인' 줄 수 상한 (헛것 + 확신 낮은 낱말)
THUMB_WEAK = 70                # AI 추천 점수가 이보다 낮으면 '썸네일 장면이 약해요'
CHAPTERS_MIN = 3               # 유튜브 챕터는 3개 이상 (BR-007)
LOUD_TITLES = ("소리가 작아요", "소리가 커요", "소리 크기", "소리 깨짐 가능")
NO_PAGE = ("썸네일을 그릴 화면이 없어요 · 스튜디오 첫 화면이나 편집실을 열어 둔 채로 다시 해 주세요 "
           "(그동안 썸네일 만들기의 'AI 추천 썸네일'로 직접 만들 수 있어요)")
UPLOAD_NOTE = "유튜브에는 아직 올리지 않았어요 · 7 올리기에서 키트를 확인하고 직접 올려 주세요"
_LOCK = threading.RLock()
LIVE = {}   # 지금 도는 자동 작업 {name, i, n, label} (/api/state · 화면의 단계 표시)


class AutoError(RuntimeError):
    """화면에 그대로 보여 줄 안내 (쉬운 한 줄)."""


class Stopped(Exception):
    """멈추기(✕)를 눌렀음."""


# ---------- 기록 ----------

def state_path(name):
    return core.adir(name) / STATE_FILE


def load(name):
    """저장된 자동 작업 기록 (없거나 깨졌으면 None)."""
    try:
        d = json.loads(state_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("v") == VER and isinstance(d.get("steps"), dict) else None


def save(st):
    st["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    p = state_path(st["name"])
    p.parent.mkdir(parents=True, exist_ok=True)
    updater.write_atomic(p, json.dumps(st, ensure_ascii=False, indent=1))


def resumable(st):
    """이어서 할 수 있는 기록: 끝나지 않았거나, 다 했어도 실패한 단계가 있음 (화면이 없어 못 그린 썸네일 등 · 빠진 것만 다시)."""
    if not st:
        return False
    return st.get("status") in ("running", "stopped", "failed") or (
        st.get("status") == "done" and any((x or {}).get("state") == "fail" for x in (st.get("steps") or {}).values()))


def view(name):
    """화면용: 마지막 자동 작업 기록 + 이어서 할 수 있는지."""
    st = load(name)
    if not st:
        return None
    out = public(st)
    out["resumable"] = resumable(st) and not (LIVE.get("name") == name)
    return out


def public(st):
    """화면에 보낼 모양 (단계는 순서대로 · 이름 붙임)."""
    steps = [dict(key=k, label=lb, **{x: v for x, v in (st["steps"].get(k) or {"state": "todo"}).items() if x in ("state", "note", "at")})
             for k, lb in STEPS]
    return {"name": st["name"], "id": st["id"], "status": st.get("status"), "opts": st.get("opts"), "steps": steps,
            "outputs": [dict(o, thumbLabel=thumb_label(o)) for o in st.get("outputs") or []], "check": st.get("check") or [], "started": st.get("started"),
            "finished": st.get("finished"), "points": len(st.get("points") or []), "redo": resumable(st)}


def thumb_label(o):
    """결과물 썸네일 파일 이름에 넣는 말 ('<편집본 이름>_썸네일' → '<영상>_<편집본 이름>_썸네일_N.jpg' · 그 편집본 키트가 고름).
    결과 카드 [썸네일]이 썸네일 편집기에 넘겨, 사람이 다시 만든 썸네일도 그 결과물 것으로 저장됨 (E7 검토)."""
    import upload
    sq = {"name": o.get("name"), "format": "shorts" if o.get("kind") == "shorts" else "long"}
    return upload._seq_label(sq) + upload.OWN_THUMB.rstrip("_")


def default_style():
    """MSG 스타일 기본값: 첫 가편집에 쓰는 배운 스타일 → 섞은 스타일(초안) → 기본 스타일 첫째 (편집실 MSG 칩 순서와 같음)."""
    import msg
    import style
    try:
        n = style.rough_default()
    except Exception:  # noqa: BLE001 — 스타일 설정을 못 읽으면 다음 것
        n = None
    if n:
        return {"kind": "style", "name": n}
    try:
        if msg._mix_path(msg.DRAFT_NAME).is_file():
            return {"kind": "mix", "name": msg.DRAFT_NAME}
    except (OSError, ValueError):
        pass
    return {"kind": "preset", "name": next(iter(msg.PRESETS))}


PREFS_FILE = "auto_prefs.json"   # 작업 폴더 안: PC 화면에서 마지막으로 고른 받아쓰기·MSG 설정 (휴대폰에서 시작할 때 씀 · E7 검토)
PREF_KEYS = ("proofread", "writer", "model")


def load_prefs():
    try:
        d = json.loads((core.WORK / PREFS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: d[k] for k in PREF_KEYS if k in d} if isinstance(d, dict) else {}


def save_prefs(b):
    """PC 화면(스튜디오 시작 · 편집실 MSG 체크)이 고른 설정을 남김 — 보낸 것만 바꿈. → 남긴 설정."""
    b = b if isinstance(b, dict) else {}
    d = load_prefs()
    for k in ("proofread", "writer"):
        if isinstance(b.get(k), bool):
            d[k] = b[k]
    if isinstance(b.get("model"), str):
        d["model"] = core.model_of(b["model"])
    try:
        updater.write_atomic(core.WORK / PREFS_FILE, json.dumps(d, ensure_ascii=False))
    except OSError:  # 못 남겨도 이번 작업은 그대로
        pass
    return d


def options(b, remember=False):
    """화면·휴대폰이 보낸 값 → 정해 둔 값만 (모르는 값은 기본값). 화면에서 정한 받아쓰기·MSG 설정(클로드 오타·재미 자막)을 그대로 따름 —
    휴대폰처럼 그 설정을 보내지 않으면 PC 화면에서 마지막으로 고른 것 (remember: PC 화면이 보낸 것을 남김)."""
    b = b if isinstance(b, dict) else {}
    if remember:
        save_prefs(b)
    pf = load_prefs()
    b = dict(b, **{k: pf[k] for k in PREF_KEYS if k in pf and k not in b})
    lv = b.get("intensity") if b.get("intensity") in LEVELS else "보통"
    try:
        n = int(b.get("shorts", SHORTS_DEFAULT))
    except (TypeError, ValueError):
        n = SHORTS_DEFAULT
    if isinstance(b.get("shorts"), bool):
        n = SHORTS_DEFAULT
    sty = b.get("style")
    if not (isinstance(sty, dict) and sty.get("kind") in ("preset", "style", "mix") and isinstance(sty.get("name"), str) and sty["name"]):
        sty = default_style()
    return {"intensity": lv, "shorts": max(0, min(SHORTS_MAX, n)), "style": {"kind": sty["kind"], "name": sty["name"][:80]},
            "model": core.model_of(b.get("model")), "proofread": b.get("proofread") is True, "writer": b.get("writer") is True,
            "preset": b.get("preset") if b.get("preset") in PRESETS else "youtube"}


def new_state(name, opts):
    return {"v": VER, "name": name, "id": uuid.uuid4().hex[:12], "opts": opts, "status": "running", "steps": {}, "outputs": [],
            "points": [], "check": [], "started": time.strftime("%Y-%m-%d %H:%M:%S"), "finished": None}


# ---------- 실행 ----------

def _cancelled():
    return editor.CANCEL.is_set()


def _say(e):
    import trouble
    return trouble.explain(e)["msg"] if not isinstance(e, AutoError) else str(e)


def run(name, opts, log=print, analyze=None, resume=False):
    """끝까지 자동으로 (작업 함수). resume: 저장된 기록이 끝나지 않았으면 이어서 (설정도 그때 것) · 아니면 처음부터.
    analyze(b): 편집점 찾기 + 1차 가편집 (app.Handler._analyze). → public(기록)."""
    editor.video_path(name)
    with _LOCK:
        old = load(name) if resume else None
        st = old if resumable(old) else new_state(name, opts)
        if st is old:
            log(f"{JOB} · 지난번에 하던 것을 이어서 해요 (끝난 단계는 건너뛰어요)")
        else:
            log(f"{JOB} · {name} · MSG {opts['intensity']} · 쇼츠 {opts['shorts']}개까지 · 유튜브에는 올리지 않아요")
        st["status"] = "running"
        save(st)
        LIVE.clear()
        LIVE.update(name=name, i=0, n=len(STEPS), label="")
    studiolog.target(path=PATH, names=[name])  # 갑자기 꺼지면 다음에 켤 때 이 영상으로 [이어서 하기]
    ctx = _Ctx(st, log, analyze)
    ran = False
    try:
        for i, (key, label) in enumerate(STEPS):
            # 이어서 할 때: 끝난 단계는 건너뛰고 실패한 단계부터 다시 — 그 뒤 단계는 모두 다시 봄 (단계 안에서 만든 결과물은 건너뜀 ·
            # 앞에서 새로 만든 MSG 편집본·썸네일이 뒤의 내보내기·키트에 담기게 · E7 검토)
            if not ran and key != "check" and (st["steps"].get(key) or {}).get("state") in ("done", "skip"):
                continue
            ran = True
            if _cancelled():
                raise Stopped()
            LIVE.update(i=i + 1, label=label)
            core.set_progress(label=label, item=name, pct=None, detail=f"{i + 1}/{len(STEPS)} 단계")
            st["steps"][key] = {"state": "run", "at": time.strftime("%H:%M:%S")}
            save(st)
            try:
                state, note = STEP_FN[key](ctx)
            except Stopped:
                raise
            except Exception as e:  # noqa: BLE001 — 단계 하나의 실패는 적어 두고 다음 단계로 (앞 두 단계만 멈춤)
                if _cancelled():
                    raise Stopped() from None
                why = _say(e)
                if not isinstance(e, (AutoError, RuntimeError)):
                    studiolog.trace(e, f"{JOB} · {label} 오류 위치")
                st["steps"][key] = {"state": "fail", "note": why, "at": time.strftime("%H:%M:%S")}
                log(f"  {label} · 하지 못했어요 · {why}")
                if key in FATAL:
                    st["status"] = "failed"
                    st["check"] = checklist(st)
                    save(st)
                    raise AutoError(f"'{label}' 단계에서 멈췄어요 · {why}") from None
                save(st)
                continue
            st["steps"][key] = {"state": state, "note": note or "", "at": time.strftime("%H:%M:%S")}
            log(f"  {label} · " + ({"done": "끝", "skip": "건너뜀", "fail": "일부만"}.get(state, state)) + (f" · {note}" if note else ""))
            save(st)
        st["status"] = "done"
        st["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save(st)
        n_out = sum(1 for o in st["outputs"] if o.get("export"))
        log(f"{JOB} · 다 했어요 · 완성본 {n_out}개 · 확인할 것 {sum(1 for c in st['check'] if c['lv'] != 'info')}개")
    except Stopped:
        for k, s in st["steps"].items():  # 하던 단계는 다음에 처음부터 (만든 결과물은 단계 안에서 건너뜀)
            if s.get("state") == "run":
                st["steps"][k] = {"state": "todo", "note": "멈춤"}
        st["status"] = "stopped"
        st["check"] = checklist(st)
        save(st)
        log(f"{JOB} · 멈췄어요 · 만든 결과물은 그대로 두었어요 ([이어서 하기]로 나머지를 해요)")
    finally:
        with _LOCK:
            LIVE.clear()
    return public(st)


class _Ctx:
    def __init__(self, st, log, analyze):
        self.st, self.log, self.analyze = st, log, analyze
        self.name, self.opts = st["name"], st["opts"]


def _out(st, key, **init):
    """결과물 기록 하나 (없으면 만듦) — key: 'long' · 'short1'…"""
    for o in st["outputs"]:
        if o.get("key") == key:
            return o
    o = dict(key=key, seq=None, name=None, msg=False, export=None, srt=None, qa=[], thumb=None, kit=None, **init)
    st["outputs"].append(o)
    st["outputs"].sort(key=lambda x: (x["key"] != "long", int(re.sub(r"\D", "", x["key"]) or 0)))
    return o


def _set_seq(o, sid, name, msg):
    """결과물의 편집본을 정함 — 편집본이 바뀌면(가편집 → 다시 해서 MSG) 예전 편집본으로 만든 완성본·썸네일·키트 기록은 버림 (다시 만들게)."""
    if o.get("seq") and o["seq"] != sid:
        for k in ("export", "srt", "qaScore", "thumb", "thumbInfo", "kit"):
            o.pop(k, None)
        o["qa"] = []
    o.update(seq=sid, name=name, msg=msg)
    if msg:
        o.pop("msgFail", None)


def _seq_of(proj, sid):
    return next((q for q in proj.get("sequences") or [] if isinstance(q, dict) and q.get("id") == sid), None)


def _tagged(proj, run_id, key):
    """이번 자동 작업이 이미 프로젝트에 더한 편집본 (꺼진 뒤 이어서 할 때 같은 MSG 를 또 더하지 않게)."""
    return next((q for q in proj.get("sequences") or [] if isinstance(q, dict) and q.get("autoRun") == run_id and q.get("autoKey") == key), None)


def add_to_project(name, seqs, media=(), captions=None, captions_old=None, client="auto"):
    """새 편집본(+ MSG 미디어)을 프로젝트에 더함 (편집실 msgAdd 와 같은 규칙: 이름이 겹치면 (2) · 자막은 사용자가 고치지 않았을 때만 새 받아쓰기로).
    편집실이 그 사이 저장했으면 한 번 다시 읽어 더함. → 실제로 붙은 이름들."""
    for attempt in (1, 2):
        proj = editor.load_project(name)
        have = {m.get("id") for m in proj.get("media") or []}
        proj["media"] = list(proj.get("media") or []) + [m for m in media if m.get("id") not in have]
        if captions:
            txt = lambda cs: "\n".join(str(c.get("text") or "") for c in cs or [])  # noqa: E731
            cur = proj.get("captions") or []
            if not cur or (captions_old is not None and txt(cur) == txt(captions_old)):
                proj["captions"] = captions
        names = {q.get("name") for q in proj.get("sequences") or []}
        added = []
        for q in seqs:
            q = copy.deepcopy(q)
            q.pop("msgCaps", None)
            q["name"] = editor._uniq_name(q.get("name") or "자동 편집본", names)
            names.add(q["name"])
            added.append(q)
        proj["sequences"] = list(proj.get("sequences") or []) + added
        try:
            editor.save_project(name, proj, proj.get("rev"), client=client)
            return added
        except editor.Conflict:
            if attempt == 2:
                raise AutoError("편집실에서 저장하는 중이라 편집본을 더하지 못했어요 · 잠시 뒤 [이어서 하기]를 눌러 주세요") from None
    return []


# ---------- 단계 ----------

def _step_analyze(c):
    d = core.adir(c.name)
    if (d / "transcript.json").exists():  # 받아쓰기가 있으면 (편집실 프로젝트는 다음 단계가 없으면 만듦)
        return "skip", "이미 편집점을 찾았어요 (그대로 써요)"
    if c.analyze is None:
        raise AutoError("편집점 찾기를 할 수 없어요 · 보관함에서 '편집점 찾기'를 먼저 해 주세요")
    res = c.analyze({"names": [c.name], "model": c.opts["model"]})
    studiolog.target(path=PATH, names=[c.name])  # 편집점 찾기가 바꾼 실행 표시를 이 작업으로 되돌림
    if isinstance(res, dict) and res.get("failed"):
        why = (res.get("why") or {}).get(c.name)
        raise AutoError((why.get("msg") if isinstance(why, dict) else why) or "편집점을 찾지 못했어요")
    return "done", ""


def _shorts_keep():
    """쇼츠 포인트를 고를 때 말 사이 쉼 (첫 가편집 스타일의 쇼츠 값 · auto_sequences 와 같음)."""
    st = editor.default_style_params() or {}
    by = st.get("byFormat")
    if isinstance(by, dict) and by:
        st = by.get("shorts") or next(iter(by.values()))
    return st.get("keepPause")


ORDINAL = re.compile(r"(?:첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열|한|\d+)\s*번\s*째")
ORD_TAIL = {"포인트", "동작", "방법", "단계", "팁", "꿀팁", "기술", "가지", "연습", "훈련", "설명", "시범"}


def generic_hook(h):
    """'퍼스트 터치 첫 번째 포인트' · '패스 두 번째 동작' 처럼 순서 말뿐인 제목 (유튜브 제목·쇼츠 위 큰 제목으로 뜻이 안 드러남)."""
    h = re.sub(r"\s+", " ", str(h or "")).strip()
    if not h:
        return True
    if not ORDINAL.search(h):
        return False
    rest = [w for w in ORDINAL.sub(" ", h).replace("!", " ").replace("?", " ").split() if w not in ORD_TAIL and re.sub(r"[^0-9A-Za-z가-힣]", "", w)]
    return len("".join(rest)) <= 6


def _cut_off(alt, title):
    """예전 규칙 제목이 첫 문장을 글자 수로 자른 조각인지 ('공이 오기 전에 고개를 들어서 주변을' ← '… 주변을 먼저 보세요.')."""
    a, t = (re.sub(r"[^0-9A-Za-z가-힣]", "", str(x or "")) for x in (alt, title))
    return bool(a) and a in t and len(a) < len(t)


def short_hook(r):
    """쇼츠 하나의 위 큰 제목 · 키트 제목 → (제목, 사람이 확인할지). 추천 제목이 순서 말뿐이면 예전 규칙('주제어 꿀팁')으로 —
    예전 규칙이 첫 문장을 자른 조각이면 추천 제목의 주제어 + '꿀팁' (E7 검토)."""
    h = editor._hook(r)
    if not generic_hook(h):
        return h, False
    title = str(r.get("title") or "")
    try:
        alt = editor._old_hook(dict(r, keywords=list(r.get("keywords") or []), title=title))
    except Exception:  # noqa: BLE001 — 예전 규칙이 안 되면 아래
        alt = ""
    if alt and not generic_hook(alt) and not _cut_off(alt, title):
        return alt, True
    rest = " ".join(w for w in ORDINAL.sub(" ", str(h or "")).split() if w not in ORD_TAIL).strip()
    if rest:
        return f"{rest} 꿀팁", True
    return (alt if alt and not generic_hook(alt) else (h or "오늘의 포인트")), True


def _step_rough(c):
    proj = editor.load_project(c.name)  # 없으면 첫 가편집(첫 가편집 스타일)까지 만듦
    rough = next((q for q in proj.get("sequences") or [] if q.get("format") == "long" and q.get("auto") in ("rough", "style")), None)
    c.st["rough"] = {"long": rough["id"] if rough else None}
    n = int(c.opts["shorts"])
    pts = []
    if n:
        rec = editor.recommend(c.name, keep_pause=_shorts_keep(), n=n)
        for r in sorted(rec["shorts"], key=lambda r: r["start"]):
            hook, check = short_hook(r)
            pts.append({"start": round(float(r["start"]), 2), "end": round(float(r["end"]), 2), "hook": hook, "hookCheck": check,
                        "title": r.get("title") or "", "keywords": r.get("keywords") or [], "cuts": r["cuts"]})
        seen = {}
        for pt in pts:  # 쇼츠끼리 같은 제목이면 사람이 고르게
            seen.setdefault(pt["hook"], []).append(pt)
        for same in seen.values():
            if len(same) > 1:
                for pt in same:
                    pt["hookCheck"] = True
    c.st["points"] = pts
    if not n:
        return "done", "쇼츠는 만들지 않아요"
    if len(pts) < n:
        return "done", f"쇼츠로 쓸 포인트가 {len(pts)}개라 쇼츠도 {len(pts)}개만 만들어요"
    return "done", f"쇼츠 포인트 {len(pts)}개"


def _msg_args(c):
    o = c.opts
    return dict(log=c.log, proofread=o["proofread"], writer=o["writer"])


def _step_msg_long(c):
    import msg
    o = _out(c.st, "long", kind="long")
    proj = editor.load_project(c.name)
    have = _tagged(proj, c.st["id"], "long")
    if have:
        _set_seq(o, have["id"], have.get("name"), bool(have.get("msg")))
        return "done", "이미 만들었어요"
    try:
        res = msg.build_variants(c.name, [c.opts["style"]], c.opts["intensity"], ("long",), **_msg_args(c))
    except Exception as e:  # noqa: BLE001 — MSG 를 못 넣으면 가편집 그대로 (만든 것은 남김)
        if _cancelled():
            raise Stopped() from None
        rid = (c.st.get("rough") or {}).get("long")
        if not rid or not _seq_of(proj, rid):
            raise
        sq = _seq_of(proj, rid)
        _set_seq(o, rid, sq.get("name"), False)
        o["msgFail"] = _say(e)
        return "fail", f"MSG 를 넣지 못해 가편집 '{sq.get('name')}' 그대로 내보내요 · {_say(e)}"
    seq = res["sequences"][0]
    seq.update(name=f"자동 롱폼 · {seq['msg']['style'].get('label') if isinstance(seq.get('msg'), dict) and isinstance(seq['msg'].get('style'), dict) else c.opts['style']['name']} · {c.opts['intensity']}",
               autoRun=c.st["id"], autoKey="long")
    got = add_to_project(c.name, [seq], res.get("media") or [], res.get("captions"), res.get("captionsOld"))
    _set_seq(o, got[0]["id"], got[0]["name"], True)
    return "done", (seq.get("msg") or {}).get("summary", {}).get("text", "")


def _short_name(i, pt, tail=""):
    """쇼츠 편집본 이름 (파일 이름이 되므로 줄바꿈·겹친 빈칸 없이 · 위 큰 제목은 두 줄일 수 있음)."""
    return f"자동 쇼츠 {i + 1} · {_name_cut(pt.get('hook'))}{tail}"


NAME_MAX = 24


def _name_cut(text, n=NAME_MAX):
    """파일 이름이 될 제목을 n 자 안으로: 문장부호 뒤 → 이음말(…면·…고·…데·…서) 뒤 → 낱말 경계 순으로 자르고 '…' (질문 한가운데서 끊지 않게 · E7 검토)."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(t) <= n:
        return t
    head = t[:n]
    m = list(re.finditer(r"[,.?!~…]", head))
    if m and m[-1].start() >= n // 3:
        return head[:m[-1].start()].strip() + "…"
    ws = head.split(" ")[:-1] if not t[n:n + 1].isspace() else head.split(" ")
    for k in range(len(ws), 1, -1):
        if re.search(r"(?:면|고|데|서|며|니까|는데|지만)$", ws[k - 1]) and len(" ".join(ws[:k])) >= n // 3:
            return " ".join(ws[:k]) + "…"
    return (" ".join(ws) or head).strip() + "…"


def _rough_short(c, i, pt):
    """MSG 를 못 넣은 쇼츠 포인트 → 그 구간 그대로의 가편집 쇼츠 (첫 가편집 스타일)."""
    info = editor.media_info(c.name)
    r = {k: pt[k] for k in ("start", "end", "cuts", "hook", "title", "keywords")}
    seqs = editor.auto_sequences(c.name, info, editor.default_style_params(), ("shorts",), rec={"shorts": [r], "tidy": []})
    if not seqs:
        return None
    q = seqs[0]
    q.update(name=_short_name(i, pt, " (가편집)"), auto="user", autoRun=c.st["id"], autoKey=f"short{i + 1}")
    return q


def _step_msg_shorts(c):
    import msg
    pts = c.st.get("points") or []
    if not pts:
        return "skip", "쇼츠 포인트가 없어요"
    proj = editor.load_project(c.name)
    todo = []
    for i, pt in enumerate(pts):
        o = _out(c.st, f"short{i + 1}", kind="shorts", point=i, hook=pt["hook"], src=[pt["start"], pt["end"]])
        o["hookCheck"] = bool(pt.get("hookCheck"))
        have = _tagged(proj, c.st["id"], f"short{i + 1}")
        if have:
            _set_seq(o, have["id"], have.get("name"), bool(have.get("msg")))
        else:
            todo.append(i)
    if not todo:
        return "done", "이미 만들었어요"
    err, got = None, {}
    try:
        res = msg.build_variants(c.name, [c.opts["style"]], c.opts["intensity"], ("shorts",), windows=[pts[i] for i in todo], **_msg_args(c))
        for q in res["sequences"]:
            i = todo[int(q.get("msgWin") or 0)]
            q.update(name=_short_name(i, pts[i]), autoRun=c.st["id"], autoKey=f"short{i + 1}")
            got[i] = q
        added = add_to_project(c.name, list(got.values()), res.get("media") or [], res.get("captions"), res.get("captionsOld"))
        got = {i: q for i, q in zip(got, added)}
    except Exception as e:  # noqa: BLE001 — MSG 를 못 넣은 쇼츠는 가편집 그대로
        if _cancelled():
            raise Stopped() from None
        err, got = _say(e), {}
    rough = []
    for i in todo:
        o = _out(c.st, f"short{i + 1}")
        if i in got:
            _set_seq(o, got[i]["id"], got[i]["name"], True)
            continue
        q = _rough_short(c, i, pts[i])
        if q:
            rough.append((i, q))
    if rough:
        added = add_to_project(c.name, [q for _, q in rough])
        for (i, _), q in zip(rough, added):
            o = _out(c.st, f"short{i + 1}")
            _set_seq(o, q["id"], q["name"], False)
            o["msgFail"] = err or "재미 순간이 모인 구간이 너무 짧아요"
    if rough:
        return "fail", f"쇼츠 {len(rough)}개는 MSG 를 넣지 못해 가편집 그대로예요" + (f" · {err}" if err else "")
    return "done", f"쇼츠 {len(todo)}개"


def _exported(name, o):
    """이 결과물을 이미 내보냈는지 (파일이 아직 있으면)."""
    return bool(o.get("export")) and (core.OUT / o["export"]).is_file()


def _export_one(c, o):
    import qa
    if _exported(c.name, o):
        return
    outs = editor.export_saved(c.name, o["seq"], c.opts.get("preset") or "youtube", c.log)
    mp4 = next((x for x in outs if str(x).lower().endswith(".mp4")), None)
    srt = next((x for x in outs if str(x).lower().endswith(".srt")), None)
    o.update(export=mp4, srt=srt, qa=[])
    if mp4:
        meta = editor.EXPORT_META.get(mp4) or {}
        try:  # 검수 (소리 크기·검은 화면·멈춘 화면 · 편집실 '영상 검수'와 같은 것)
            r = qa.check_video(core.OUT / mp4, meta.get("format"), meta.get("master"), editor.run_killable)
            o["qa"] = [{"lv": x["lv"], "title": x["title"], "msg": x["msg"], "t": x.get("t")} for x in r.get("items") or [] if x["lv"] != "ok"]
            o["qaScore"] = r.get("score")
        except Exception as e:  # noqa: BLE001 — 검수를 못 해도 완성본은 그대로
            if _cancelled():
                raise Stopped() from None
            o["qa"] = [{"lv": "warn", "title": "검수 못 함", "msg": f"자동 검수를 하지 못했어요 · {_say(e)}", "t": None}]


def _step_export_long(c):
    o = _out(c.st, "long", kind="long")
    if not o.get("seq"):
        return "skip", "내보낼 롱폼이 없어요"
    if _cancelled():
        raise Stopped()
    _export_one(c, o)
    return "done", o.get("export") or ""


def _step_export_shorts(c):
    outs = [o for o in c.st["outputs"] if o["kind"] == "shorts" and o.get("seq")]
    if not outs:
        return "skip", "내보낼 쇼츠가 없어요"
    bad = []
    for k, o in enumerate(outs):
        if _cancelled():
            raise Stopped()
        core.set_progress(label="쇼츠 내보내기", item=c.name, pct=None, detail=f"쇼츠 {k + 1}/{len(outs)}")
        try:
            _export_one(c, o)
        except Exception as e:  # noqa: BLE001 — 쇼츠 하나가 안 돼도 다음 쇼츠
            if _cancelled():
                raise Stopped() from None
            o["exportFail"] = _say(e)
            bad.append(o["name"])
        save(c.st)
    if bad:
        return "fail", f"{len(bad)}개를 내보내지 못했어요 · " + ", ".join(bad)
    return "done", f"쇼츠 {len(outs)}개"


def _step_thumbs(c):
    import thumb
    outs = [o for o in c.st["outputs"] if o.get("seq")]
    if not outs:
        return "skip", "썸네일을 만들 결과물이 없어요"
    todo = [o for o in outs if not (o.get("thumb") and (core.OUT / o["thumb"]).is_file())]
    if not todo:
        return "done", "이미 만들었어요"
    if thumb.cached_analysis(c.name) is None:  # 장면·선수·누끼·문구 (썸네일 편집기 'AI 추천'과 같은 분석 · 캐시)
        thumb.analyze(c.name, c.log)
    proj = editor.load_project(c.name)
    bad = []
    for k, o in enumerate(todo):
        if _cancelled():
            raise Stopped()
        sq = _seq_of(proj, o["seq"]) or {"name": o.get("name"), "format": "shorts" if o["kind"] == "shorts" else "long"}
        core.set_progress(label="썸네일", item=c.name, pct=None, detail=f"AI 추천 썸네일 그리는 중 ({k + 1}/{len(todo)}) · 스튜디오 화면이 그려요")
        try:
            used = [x["thumbInfo"]["copy"] for x in outs if x is not o and (x.get("thumbInfo") or {}).get("copy")]
            r = request_thumb(c.name, "short" if o["kind"] == "shorts" else "long", thumb_label(dict(o, name=sq.get("name") or o.get("name"))),
                              o.get("src"), used)
        except AutoError as e:
            o["thumbFail"] = str(e)
            bad.append(str(e))
            if str(e) == NO_PAGE:  # 그릴 화면이 없음 → 나머지도 못 그림
                for x in todo[k + 1:]:
                    x["thumbFail"] = str(e)
                break
            continue
        o.update(thumb=r["file"], thumbInfo={x: r.get(x) for x in ("score", "weak", "gates", "within", "tpl", "copy", "t")})
        o.pop("thumbFail", None)
        save(c.st)
    if bad:
        return "fail", bad[0] if len(set(bad)) == 1 else f"썸네일 {len(bad)}개를 만들지 못했어요"
    return "done", f"썸네일 {len(todo)}개"


def _step_kits(c):
    import upload
    outs = [o for o in c.st["outputs"] if o.get("seq")]
    if not outs:
        return "skip", "키트를 만들 결과물이 없어요"
    bad = []
    for o in outs:
        if _cancelled():
            raise Stopped()
        try:
            kit = upload.build_kit(c.name, o["seq"])
        except Exception as e:  # noqa: BLE001 — 하나가 안 돼도 다음 것
            o["kitFail"] = _say(e)
            bad.append(o.get("name") or o["key"])
            continue
        th = kit.get("thumbnail") or {}
        o["kit"] = {"txt": (kit.get("files") or {}).get("txt"), "title": kit.get("title"), "chapters": len(kit.get("chapters") or []),
                    "format": kit.get("format"), "duration": kit.get("duration"), "foreign": [f.get("channel") for f in kit.get("foreign") or [] if isinstance(f, dict)],
                    "thumb": th.get("file"), "thumbOk": bool(th.get("ok")), "notes": list(kit.get("notes") or [])[:6]}
        o.pop("kitFail", None)
    if bad:
        return "fail", f"키트 {len(bad)}개를 만들지 못했어요 · " + ", ".join(bad)
    return "done", f"키트 {len(outs)}개 (유튜브에는 올리지 않았어요)"


def _step_check(c):
    c.st["check"] = checklist(c.st)
    n = sum(1 for x in c.st["check"] if x["lv"] != "info")
    return "done", f"확인할 것 {n}개" if n else "따로 확인할 것이 없어요"


STEP_FN = {"analyze": _step_analyze, "rough": _step_rough, "msg_long": _step_msg_long, "export_long": _step_export_long,
           "msg_shorts": _step_msg_shorts, "export_shorts": _step_export_shorts, "thumbs": _step_thumbs, "kits": _step_kits,
           "check": _step_check}


# ---------- 확인할 것 ----------

def _mmss(t):
    t = max(0, int(float(t or 0)))
    return f"{t // 60:02d}:{t % 60:02d}"


def _cut_point(items, a, b):
    """원본에서 지운 구간 [a, b] 가 이 편집본 안(앞뒤로 남긴 컷 사이)에 있으면 그 이음매의 타임라인 시각 · 아니면 None."""
    v1 = sorted((it for it in items if it.get("track") == "V1" and it.get("media", "main") == "main"), key=lambda it: float(it["start"]))
    before = any(float(it["out"]) <= a + 0.3 for it in v1)
    after = next((it for it in v1 if float(it["in"]) >= b - 0.3), None)
    return float(after["start"]) if before and after else None


def caption_checks(name, outputs=None, limit=CAPTION_MAX):
    """사람이 들어 봐야 할 자막: 헛것으로 보고 뺀 말·작은 말소리(확인 필요) + 받아쓰기가 자신 없어 한 낱말.
    outputs(결과물 기록)를 주면 결과물에 실제로 들어간(또는 그 사이에서 지운) 것만, '결과물 이름 + 그 편집본 시각'으로 (E7 검토 ·
    모든 결과물에서 잘려 나간 말은 빼서 찾느라 헛수고하지 않게). 결과물이 없으면 원본 시각."""
    import captions
    out = []
    try:
        rec = editor.recommend(name)
        segs = editor._segments_of(name)
        raw = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 받아쓰기를 못 읽으면 자막 확인은 건너뜀
        return out
    words = [w for s in raw if isinstance(s, dict) for w in s.get("words") or () if isinstance(w, dict) and str(w.get("w") or "").strip()]

    def said(a, b):
        return " ".join(str(w["w"]).strip() for w in words if a - 0.05 <= (float(w["s"]) + float(w["e"])) / 2 <= b + 0.05)[:30]
    flagged = []  # (a, b, 낱말, 까닭, 지웠는지)
    for j in rec.get("junk_list") or []:
        if j.get("why") in (captions.HALLU_WHY, captions.HALLU_MAYBE):
            gone = j["why"] == captions.HALLU_WHY
            flagged.append((float(j["a"]), float(j["b"]), said(j["a"], j["b"]) or "…", "지어낸 말 같아서 뺐어요 (진짜 말이었는지 들어 봐 주세요)" if gone
                            else "작은 말소리라 남겨 뒀어요", gone))
    low = sorted(((float(w.get("p")), float(w["s"]), float(w["e"]), str(w["w"]).strip()) for s in segs for w in s.get("words") or ()
                  if isinstance(w.get("p"), (int, float)) and float(w["p"]) < LOW_P
                  and len(re.sub(r"[^0-9A-Za-z가-힣]", "", str(w.get("w") or ""))) >= 2), key=lambda x: x[0])  # '어?'·'세' 같은 한 글자는 늘 낮게 나와 뺌
    for p, a, b, w in low:
        if any(fa - 0.1 <= a <= fb + 0.1 for fa, fb, *_ in flagged):
            continue
        flagged.append((a, b, w, f"받아쓰기가 자신 없어 했어요 ({int(p * 100)}%)", False))
    seqs = []
    if outputs is not None:
        try:
            proj = editor.load_project(name)
        except Exception:  # noqa: BLE001 — 프로젝트를 못 읽으면 결과물 시각 없이 (원본 시각)
            proj = None
        for o in outputs:
            sq = _seq_of(proj or {}, o.get("seq")) if o.get("seq") else None
            if sq and sq.get("items"):
                seqs.append((o, sq["items"]))
    rows = []
    for a, b, w, why, gone in flagged:
        if not seqs:
            if outputs is None or not any(o.get("seq") for o in outputs):
                rows.append((0, a, {"kind": "caption", "lv": "warn", "text": f"자막 확인 · 원본 {_mmss(a)} '{w}' · {why}", "t": round(a, 2), "src": round(a, 2)}))
            continue
        hits = []
        for o, items in seqs:
            t = _cut_point(items, a, b) if gone else editor._src_to_tl(items, (a + b) / 2)
            if t is not None:
                hits.append((o, t))
        if not hits:
            continue  # 모든 결과물에서 잘려 나감 → 볼 것 없음
        o, t = hits[0]
        more = f" (그 밖에 결과물 {len(hits) - 1}개에도)" if len(hits) > 1 else ""
        rows.append((0 if o.get("kind") == "long" else 1, t, {"kind": "caption", "lv": "warn", "out": o["key"], "seq": o["seq"], "t": round(t, 2),
                                                               "src": round(a, 2), "text": f"자막 확인 · {o.get('name') or o['key']} {_mmss(t)} '{w}' · {why}{more}"}))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in rows[:limit]]


def checklist(st):
    """사람이 확인할 것 [{kind, lv(bad|warn|info), text, out(결과물 key), t(원본 시각)}] — 실패한 단계 · 자막 · 다른 채널 · 챕터 · 소리 · 썸네일."""
    import upload
    items = []

    def add(kind, lv, text, out=None, t=None):
        items.append({"kind": kind, "lv": lv, "text": text, "out": out, "t": t})
    for k, lb in STEPS:
        s = st["steps"].get(k) or {}
        if s.get("state") == "fail":
            add("fail", "bad", f"{lb} · {s.get('note') or '하지 못했어요'}")
        elif s.get("state") in (None, "todo", "run") and st.get("status") in ("stopped", "failed"):
            add("fail", "warn", f"{lb} · 아직 안 했어요 ([이어서 하기]로 마저 해요)")
    if (st["steps"].get("rough") or {}).get("state") in ("done", "skip"):
        items += caption_checks(st["name"], [o for o in st.get("outputs") or [] if o.get("seq")])
    weak = []

    def low_score(ti):
        return ti.get("score") is not None and float(ti["score"]) < THUMB_WEAK

    def tried(step):  # 그 단계를 해 봤을 때만 '없어요'를 적음 (멈춰서 아직 안 한 단계는 위의 '아직 안 했어요' 한 줄)
        return (st["steps"].get(step) or {}).get("state") in ("done", "fail")
    for o in st.get("outputs") or []:
        nm, key = o.get("name") or o["key"], o["key"]
        if o.get("seq") and not o.get("msg"):
            add("msg", "warn", f"{nm} · MSG 를 넣지 못해 가편집 그대로예요" + (f" ({o['msgFail']})" if o.get("msgFail") else ""), key)
        if o.get("seq") and not o.get("export") and tried("export_long" if o["kind"] == "long" else "export_shorts"):
            add("export", "bad", f"{nm} · 아직 내보내지 못했어요" + (f" · {o['exportFail']}" if o.get("exportFail") else ""), key)
        for q in o.get("qa") or []:
            loud = q["title"] in LOUD_TITLES
            if loud or q["lv"] == "bad":
                add("loud" if loud else "qa", q["lv"], f"{nm} · {q['title']} · {q['msg']}", key, q.get("t"))
        ti = o.get("thumbInfo") or {}
        if o.get("seq") and not o.get("thumb") and tried("thumbs"):
            add("thumb", "warn", f"{nm} · 썸네일이 없어요" + (f" · {o['thumbFail']}" if o.get("thumbFail") else ""), key)
        elif o.get("thumb") and ti.get("weak") and low_score(ti):  # 장면이 약하다는 표시는 점수도 낮을 때만 (점수가 높으면 쓸 만함 · E7 검토)
            weak.append(o)
        elif o.get("thumb") and (low_score(ti) or ti.get("gates")):
            add("thumb", "warn", f"{nm} · 썸네일 AI 점수가 낮아요 ({round(float(ti.get('score') or 0))}점"
                + (f" · {', '.join(ti['gates'][:2])}" if ti.get("gates") else "") + ") · 결과 카드의 [썸네일]에서 다시 골라 보세요", key)
        if o.get("thumb") and o["kind"] == "shorts" and ti.get("within") is False:
            add("thumb", "warn", f"{nm} · 이 쇼츠 구간에는 썸네일로 쓸 장면이 없어 다른 장면으로 만들었어요", key)
        if o.get("seq") and o.get("hookCheck"):
            add("title", "warn", f"{nm} · 위 큰 제목·키트 제목: '{o.get('hook') or ''}' · 추천 제목이 순서 말뿐이거나 다른 쇼츠와 같아 바꿨어요. "
                "내용이 드러나는지 편집실(제목)과 7 올리기(키트 제목)에서 확인해 주세요", key)
        kit = o.get("kit") or {}
        if o.get("seq") and not kit and tried("kits"):
            add("kit", "bad", f"{nm} · 올리기 키트를 만들지 못했어요" + (f" · {o['kitFail']}" if o.get("kitFail") else ""), key)
        if kit.get("foreign"):
            add("source", "warn", f"{nm} · 다른 채널 영상이 들어 있어요 ({', '.join(kit['foreign'][:3])}) · 설명에 출처를 넣었어요. 짧게, 해설 위주인지 확인해 주세요", key)
        if kit and o["kind"] == "long" and kit.get("chapters", 0) < CHAPTERS_MIN and float(kit.get("duration") or 0) >= upload.CH_MIN_VIDEO:
            add("chapters", "warn", f"{nm} · 챕터가 {'없어요' if not kit.get('chapters') else str(kit['chapters']) + '개뿐이에요'} · 유튜브 챕터는 3개 이상이어야 보여요 (키트에서 챕터를 확인해 주세요)", key)
    if weak:  # 장면이 약한 것은 영상 탓이라 결과물마다가 아니라 한 줄로
        names = [o.get("name") or o["key"] for o in weak]
        sc = ", ".join(f"{round(float(o['thumbInfo']['score']))}점" for o in weak[:4])
        add("thumb", "warn", ("이 영상은 " if len(weak) > 1 else f"{names[0]} · ") + f"썸네일로 쓸 만한 장면이 약해요 (AI 점수 {sc} · {THUMB_WEAK}점 아래 · "
            "장면 평가도 낮았어요)" + (f" · 결과물 {len(weak)}개" if len(weak) > 1 else "")
            + " · 결과 카드의 [썸네일]에서 장면을 직접 골라 저장하면 그 결과물 키트에 들어가요", weak[0]["key"])
    order = {"bad": 0, "warn": 1, "info": 2}
    items.sort(key=lambda x: order.get(x["lv"], 3))
    add("upload", "info", UPLOAD_NOTE)
    return items


# ---------- 썸네일 그리기 (스튜디오 화면 ↔ 작업) ----------

PAGE_WAIT = 600   # 화면이 맡을 때까지 기다리는 시간(초) — 창을 5분 넘게 내려 두면 브라우저가 타이머를 1분에 한 번으로 늦춤 (E7 검토)
PAGE_NOTE = 8     # 이만큼 기다려도 아무 화면도 안 맡으면 진행 표시에 '화면을 열어 주세요'를 띄움
DRAW_WAIT = 300   # 맡은 뒤 다 그릴 때까지 (처음 한 번 글꼴·장면 그림을 받음)
DATA_MAX = 2_900_000   # dataURL 글자 수 상한 (thumb.AB_MAX 와 같음 · JPG 2MB 안쪽)
_RQ = {"want": None, "claim": None, "got": {}}
_RQC = threading.Condition()


def pending():
    """/api/state 에 실을 것: 화면이 아직 맡지 않은 썸네일 그리기 요청 {id} (없으면 None)."""
    with _RQC:
        w = _RQ["want"]
        return {"id": w["id"]} if w and not _RQ["claim"] else None


def claim(rid):
    """화면 하나가 이 요청을 맡음 → {id, name, fmt, a, b} (이미 다른 화면이 맡았거나 없는 요청이면 None)."""
    with _RQC:
        w = _RQ["want"]
        if not w or w["id"] != rid or _RQ["claim"]:
            return None
        _RQ["claim"] = time.time()
        within = w.get("within") or [None, None]
        return {"id": w["id"], "name": w["name"], "fmt": w["fmt"], "a": within[0], "b": within[1], "avoid": w.get("avoid") or []}


def deliver(rid, b):
    """썸네일 편집기(보이지 않는 틀)가 그린 그림 → 완성본 폴더에 '<영상>_<편집본 이름>_썸네일_N.jpg' (이름은 서버가 정함). → 파일 이름.
    그림을 못 그렸으면 b.error (쉬운 한 줄)."""
    import thumb
    with _RQC:
        w = _RQ["want"]
        if not w or w["id"] != rid or not _RQ["claim"]:
            raise LookupError("이미 끝난 썸네일 요청이에요")
    if b.get("error"):
        res = {"error": str(b["error"])[:200]}
    else:
        data = b.get("data")
        if not isinstance(data, str) or not data.startswith(("data:image/jpeg;base64,", "data:image/png;base64,")) or len(data) > DATA_MAX:
            raise ValueError("그림 형식이 다르거나 너무 커요")
        f = thumb.export_image(w["name"], data, "jpg", w["label"])

        def num(v):
            return round(float(v), 1) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        res = {"file": f.name, "score": num(b.get("score")), "weak": bool(b.get("weak")), "within": b.get("within") is not False,
               "gates": [str(g)[:40] for g in (b.get("gates") or [])[:5] if isinstance(g, str)] if isinstance(b.get("gates"), list) else [],
               "tpl": str(b.get("tpl") or "")[:40], "copy": str(b.get("copy") or "")[:60], "t": num(b.get("t"))}
    with _RQC:
        _RQ["got"][rid] = res
        _RQC.notify_all()
    return res.get("file")


def request_thumb(name, fmt, label, within=None, avoid=()):
    """썸네일 하나를 화면에 그려 달라고 하고 기다림 → {file, score, weak, gates, within, tpl, copy, t}. 못 그리면 AutoError · 멈추기면 Stopped.
    avoid: 앞 결과물 썸네일의 문구('첫 줄/둘째 줄') — 화면은 AI 추천 6개 중 그 문구가 아닌 가장 높은 것을 고름 (쇼츠마다 같은 문구가 되지 않게)."""
    rid = uuid.uuid4().hex[:12]
    with _RQC:
        _RQ.update(want={"id": rid, "name": name, "fmt": fmt, "label": label, "within": list(within) if within else None,
                         "avoid": [str(a)[:60] for a in avoid if a][:10]}, claim=None)
        _RQ["got"].pop(rid, None)
    t0, noted = time.time(), False
    try:
        while True:
            with _RQC:
                got = _RQ["got"].pop(rid, None)
                cl = _RQ["claim"]
                if got is None:
                    _RQC.wait(0.5)
                    got = _RQ["got"].pop(rid, None)
                    cl = _RQ["claim"]
            if cl is not None and noted:  # 창을 다시 열어 화면이 맡음 → '창을 열어 주세요'를 거둠
                noted = False
                core.set_progress(label="썸네일", item=name, pct=None, detail="AI 추천 썸네일 그리는 중 · 스튜디오 화면이 그려요")
            if got is not None:
                if got.get("error"):
                    raise AutoError(f"썸네일을 그리지 못했어요 · {got['error']}")
                return got
            if _cancelled():
                raise Stopped()
            now = time.time()
            if cl is None and now - t0 > PAGE_WAIT:
                raise AutoError(NO_PAGE)
            if cl is None and now - t0 > PAGE_NOTE and not noted:
                noted = True
                core.set_progress(label="썸네일", item=name, pct=None,
                                  detail="썸네일을 그릴 화면을 기다려요 · 스튜디오나 편집실 창을 열어 보이게 해 주세요")
            if cl is not None and now - cl > DRAW_WAIT:
                raise AutoError("썸네일을 그리는 데 너무 오래 걸려 건너뛰었어요")
    finally:
        with _RQC:
            if _RQ["want"] and _RQ["want"]["id"] == rid:
                _RQ.update(want=None, claim=None)
