"""MSG 재미 글자 쓰기 (사용자가 '클로드로 재미 자막 쓰기'를 켰을 때만, D-031).

정해 둔 문구('(머쓱)'·'나이스!!')만으로는 같은 글이 영상마다 되풀이되고, 낱말이 안 잡히는 곳(시범·딴소리·긴 설명)에는 글자가 없음 →
사용자 본인 클로드 계정(Claude Code CLI, claude_cli.py)으로 재미 순간마다 짧은 속마음·효과·상황 글자를 한 번 받아 씀.
보내는 것은 대사 글(순간 앞뒤 몇 줄)과 영상 제목뿐 (그림·소리 없음).
- 감독님을 깎아내리지 않음: 외모·몸·나이·실력 놀림·욕 낱말이 든 글은 받지 않음 (BANNED) · 같은 때 말을 되풀이하는 글도 받지 않음
- 글 길이·꼴을 확인 (속마음은 괄호 · 효과 글자는 느낌표) · 맞지 않으면 그 순간은 정해 둔 문구로
- 결과는 분석 폴더 msg_ai_lines.json 에 남김 (같은 받아쓰기·같은 순간이면 다시 안 보냄)
표준 라이브러리 + claude_cli 만 씀.
"""
import hashlib
import json
import os
import re

VER = 4
TIMEOUT = 240
MAX_LEN = {"inner": 11, "fx": 7, "situ": 11}   # 글자 수 한도 (띄어쓰기·괄호·문장 부호 빼고)
TALK_GAP = 12.0     # 재미 순간 없이 말만 이만큼(초) 이어지면 그 가운데 말에도 글자 한 줄을 물어봄
# 순간 종류 → 쓸 수 있는 글자 종류 (앞의 것을 먼저)
KINDS = {"punchline": ("inner",), "fail": ("inner", "fx"), "success": ("fx", "inner"), "surprise": ("fx",), "question": ("inner",),
         "aside": ("inner",), "play": ("inner", "fx"), "talk": ("inner",)}
KO = {"inner": "속마음", "fx": "효과 글자", "situ": "상황"}
BANNED = re.compile(r"명장면|진심 ?어린|참 쉽|역시 프로|얼굴|외모|못생|뚱뚱|살[이찐]|배[가 ]?나|키[가 ]?작|대머리|머리숱|늙|나이|노안|아재|꼰대|멍청|바보|한심|창피|망신|못하네|허접|"
                    r"실력[이 ]?[없부]|굴욕|흑역사|ㅋ{2,}|ㅎ{2,}|ㅠ|씨[발바]|시[발바]|ㅅㅂ|병신|존나|개[같못]|[\U0001F300-\U0001FAFF☀-➿]")


class WriteError(RuntimeError):
    """화면에 그대로 보여 줄 안내."""


def _plain(t):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(t or ""))


SKIP_TALK = re.compile(r"오늘은 여기까지|오늘 영상은 여기|감사합니다|구독|다음 영상|다시 할게요|잠깐만요|아니다")


def candidates(moms, lines, junk=()):
    """클로드에게 물어볼 순간 [{id, key, kind, t, said, ctx}] — 재미 순간(KINDS) + 말만 길게 이어지는 곳의 가운데 말(kind 'talk' ·
    정리할 곳(NG·군말)·마무리 인사·다시 찍는 말은 뺌)."""
    out = []
    for m in sorted(moms, key=lambda m: m["t"]):
        if m["kind"] not in KINDS or m["kind"] == "talk":
            continue
        out.append({"kind": m["kind"], "t": float(m["t"]), "m": m})
    fun_t = sorted(float(m["t"]) for m in moms if m["kind"] in KINDS)
    prev = 0.0
    for ln in lines:  # 말만 이어지는 곳: 앞뒤 재미 순간과 TALK_GAP/2 넘게 떨어진 말
        a, b = float(ln["start"]), float(ln["end"])
        mid = (a + b) / 2
        if a - prev < TALK_GAP / 2 or len(_plain(ln["text"])) < 8 or SKIP_TALK.search(str(ln["text"])) \
                or any(float(j[0]) - 0.1 <= mid <= float(j[1]) + 0.1 for j in junk or ()):
            continue
        if all(abs(mid - t) >= TALK_GAP / 2 for t in fun_t):
            m = {"kind": "talk", "a": round(a, 2), "b": round(b, 2), "t": round(min(b - 0.3, a + 1.2), 2), "score": 0.5, "text": ln["text"],
                 "why": "말만 이어지는 설명"}
            out.append({"kind": "talk", "t": m["t"], "m": m})
            prev = a
            fun_t.append(mid)
    out.sort(key=lambda x: x["t"])
    res = []
    def cut(ln):  # 편집에서 빠지는 말(NG·군말)은 앞뒤 말에도 안 넣음 (시청자가 못 보는 말에 기댄 글이 생기지 않게)
        mid = (float(ln["start"]) + float(ln["end"])) / 2
        return any(float(j[0]) - 0.1 <= mid <= float(j[1]) + 0.1 for j in junk or ())
    for i, x in enumerate(out, 1):
        t = x["t"]
        # 앞뒤 말은 그 순간까지만 (뒤 4초 말까지 보내면 7초 뒤에 나올 말에 맞춘 글이 먼저 뜸 · 판정 round5 최종 '(발밑으로 쏙)')
        near = [ln for ln in lines if float(ln["end"]) >= t - 6.0 and float(ln["start"]) <= t + (3.0 if x["kind"] == "play" else 1.0)
                and not cut(ln)]   # (시범은 바로 뒤 반응 말까지)
        said = next((ln["text"] for ln in lines if float(ln["start"]) - 0.3 <= t <= float(ln["end"]) + 0.3), "")
        res.append({"id": i, "kind": x["kind"], "t": round(t, 2), "said": said, "ctx": " / ".join(str(ln["text"]) for ln in near)[:220], "m": x["m"]})
    return res


def build_prompt(cands, title=""):
    rows = "\n".join(f"{c['id']}. [{c['kind']}] {c['t']:.1f}초 · 지금 말: \"{c['said']}\" · 앞뒤 말: {c['ctx']} · 쓸 수 있는 글자: "
                     + "/".join(KO[k] for k in KINDS[c["kind"]]) for c in cands)
    return ("풋살 레슨 유튜브 영상" + (f" 「{title}」" if title else "") + "을 예능처럼 재미있게 편집하고 있어요. 아래 순간마다 화면에 띄울 짧은 글자를 써 주세요.\n"
            "- 속마음: 감독님(코치)의 속마음을 괄호로, 띄어쓰기 빼고 11글자 안. 예: (이 맛에 레슨하지) (아직 끝 아님)\n"
            "- 효과 글자: 순간 반응 감탄 1~2낱말 + 느낌표, 7글자 안. 예: 깔끔! 이거지! 아깝다!\n"
            "- 상황: 지금 무엇을 하는지 짧은 이름표, 11글자 안. 예: 핵심 설명 중\n"
            "- 감독님을 존중해요: 외모·몸·나이·실력을 놀리거나 깎아내리지 않고, 욕·이모지·'ㅋㅋ'를 쓰지 않아요. 실수에는 놀림 대신 응원·공감.\n"
            "- 지금 말한 낱말을 그대로 되풀이하지 않아요 (말 자막이 이미 보여 줌) — 새 재미나 시청자 마음을 보태요.\n"
            "- 비꼬거나 반어로 읽힐 수 있는 글은 쓰지 않아요 (예: '진심 어린 조언' · '참 쉽죠' · '역시 프로'). 딴소리·여담에는 가벼운 공감만.\n"
            "- 앞뒤 말에 없는 일(편집에서 잘린 실수 등)은 지어내지 않아요.\n"
            "- 글은 그 순간의 '지금 말'에 맞춰요 (아직 안 나온 말을 미리 쓰지 않아요). 여담·잡담을 팁·핵심처럼 부풀리지 않아요.\n"
            "- 순간마다 다른 글을 써요. 어울리는 글이 없으면 그 번호는 빼요.\n"
            'JSON 한 줄만 답해요: {"lines":[{"id":번호,"kind":"inner|fx|situ","t":"글"}]}\n\n' + rows)


def clean(kind, text, said=""):
    """한 줄을 규칙에 맞게 다듬음 → 쓸 수 있는 글 또는 None."""
    t = re.sub(r"\s+", " ", str(text or "")).strip().strip("\"'“”‘’")
    if not t or BANNED.search(t) or "\n" in t:
        return None
    core_ = re.sub(r"[()（）]", "", t).strip()
    n = len(_plain(core_))
    if n < 1 or n > MAX_LEN[kind]:
        return None
    if kind == "inner":
        t = f"({core_})"
    elif kind == "fx":
        t = core_.rstrip(".~") + ("" if core_.endswith(("!", "?")) else "!")
    else:
        t = core_.rstrip(".!")
    p, s = _plain(core_), _plain(said)
    if len(p) >= 2 and s and p in s:  # 지금 말을 그대로 되풀이
        return None
    return t


def parse(text, cands):
    """클로드 대답 → {moment_key: {kind, text}} (모양이 틀리면 WriteError)."""
    m = re.search(r"\{.*\}", str(text or ""), re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not isinstance(d.get("lines"), list):
        raise WriteError("클로드 대답 모양이 달라 재미 자막은 정해 둔 문구로 했어요")
    by = {c["id"]: c for c in cands}
    out, seen = {}, set()
    for x in d["lines"]:
        if not isinstance(x, dict):
            continue
        try:
            c = by.get(int(x.get("id")))
        except (TypeError, ValueError):
            continue
        kind = str(x.get("kind") or "")
        if c is None or kind not in KINDS[c["kind"]]:
            continue
        t = clean(kind, x.get("t"), c["said"])
        if not t or _plain(t) in seen:
            continue
        seen.add(_plain(t))
        out[key_of(c["m"])] = {"kind": kind, "text": t}
    return out


def key_of(m):
    return f"{m['kind']}:{float(m['t']):.2f}"


def _sig(cands, title):
    body = json.dumps([[c["kind"], c["t"], c["said"]] for c in cands], ensure_ascii=False) + title
    return hashlib.sha1(f"{VER}|{body}".encode("utf-8")).hexdigest()[:16]


def write(cache_file, moms, lines, title="", cancel=None, run=None, log=print, junk=()):
    """재미 순간 → ({moment_key: {kind, text}}, 더한 'talk' 순간 목록). 캐시 파일에 같은 입력의 결과가 있으면 그것.
    클로드를 못 쓰면 ClaudeError(claude_cli) · 대답이 이상하면 WriteError. run: 시험용 (claude_cli.run 흉내)."""
    cands = candidates(moms, lines, junk)
    talk = [c["m"] for c in cands if c["kind"] == "talk"]
    if not cands:
        return {}, []
    sig = _sig(cands, title)
    try:
        with open(cache_file, encoding="utf-8") as f:
            old = json.loads(f.read())
        if old.get("sig") == sig and isinstance(old.get("lines"), dict):
            return old["lines"], talk
    except (OSError, ValueError, TypeError):
        pass
    if run is None:
        import claude_cli
        run = claude_cli.run
    res = run(build_prompt(cands, title), timeout=TIMEOUT, cancel=cancel)
    out = parse(res.get("text"), cands)
    tmp = f"{cache_file}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"v": VER, "sig": sig, "model": res.get("model"), "lines": out}, ensure_ascii=False))
        os.replace(tmp, cache_file)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    log(f"  클로드가 재미 자막 {len(out)}줄을 썼어요 (순간 {len(cands)}곳 중)")
    return out, talk
