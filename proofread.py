"""받아쓰기 다듬기 (MSG · 사용자가 '클로드로 자막 오타 고치기'를 켰을 때만, D-142).

받아쓰기(faster-whisper)가 잘못 알아들은 글자('공으로 칠까요' → '공을 놓칠까요', '부터원이니까요' → '붙어 버리니까요')를
사용자 본인 클로드 계정(Claude Code CLI, claude_cli.py)으로 한 번 고친다. 보내는 것은 대사 글과 영상 제목·용어 사전 용어뿐 (그림·소리 없음).
- 말한 내용·말투는 그대로, 잘못 들은 낱말·맞춤법·띄어쓰기·문장 부호만 · 너무 많이 바뀐 줄은 받지 않음 (지어낸 말이 끼지 않게)
- 고친 글은 원래 단어 시각에 다시 맞춰(align_words) 노래방 자막·컷이 그대로 맞게 함
- 결과는 받아쓰기 파일(transcript.json)에 쓰고 asr.json 에 '다듬음' 표시 (같은 받아쓰기는 다시 보내지 않음)
표준 라이브러리 + claude_cli 만 씀.
"""
import difflib
import hashlib
import json
import re

TIMEOUT = 240
MAX_CHARS = 9000          # 한 번에 보내는 대사 글자 수 한도 (넘으면 나눠 보냄)
MIN_SIM = 0.6             # 고친 줄이 원래 줄과 이만큼은 같아야 받음 (글자 기준)
MAX_GROW = 0.35           # 글자 수가 이만큼 넘게 늘거나 줄면 받지 않음


# 클로드에게 알려 줄 풋살 용어 (받아쓰기 힌트와 달리 길이 한도가 넉넉해 더 넣음 · 사용자 용어 사전 용어가 앞에 붙음)
FUTSAL_TERMS = ("퍼스트 터치", "디딤발", "골대", "월패스", "2대1 패스", "인사이드", "아웃사이드", "인스텝", "토킥", "힐킥", "칩슛", "발바닥 터치",
                "트래핑", "드리블", "페인팅", "터닝", "슈팅", "패스 앤 무브", "피벗", "픽소", "아라", "고레이로", "킥인", "코너킥", "스위칭", "오버래핑")


class ProofError(RuntimeError):
    """화면에 그대로 보여 줄 안내."""


def plain(t):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(t or ""))


def text_sig(segs):
    return hashlib.sha1("\n".join(str(s.get("text") or "") for s in segs).encode("utf-8")).hexdigest()[:16]


def build_prompt(lines, title="", terms=()):
    """받아쓰기 줄 [글] → 클로드에게 보낼 글 (번호는 1부터)."""
    tl = ", ".join(dict.fromkeys([t for t in terms if t] + list(FUTSAL_TERMS)))[:600]
    body = "\n".join(f"{i}: {t}" for i, t in enumerate(lines, 1))
    return ("아래는 풋살 레슨 영상" + (f" 「{title}」" if title else "") + "의 자동 받아쓰기예요. 소리를 잘못 알아들어 생긴 글자만 고쳐 주세요.\n"
            "- 말한 내용·말투·어순은 그대로 두고, 잘못 들은 낱말·맞춤법·띄어쓰기·문장 부호만 고쳐요. 문장이 끝났는데 마침표·물음표·느낌표가 없으면 넣어요.\n"
            "- 한국어로 뜻이 통하지 않는 낱말(소리만 비슷한 엉뚱한 말, 예: '패스를 바다요' → '패스를 받아요')은 앞뒤 문맥으로 원래 말을 찾아 고쳐요.\n"
            "- 그 밖에는 원래 말이 확실할 때만 고쳐요. 말을 보태거나 다듬지 않아요.\n"
            "- 숫자는 말한 그대로(다섯 번·3개) 둬요. '어…'·'음…' 같은 군말도 그대로 둬요.\n"
            + (f"- 이 영상에 나오는 풋살 용어: {tl}\n" if tl else "")
            + 'JSON 한 줄만 답해요: {"fix":[{"i":줄 번호,"t":"고친 줄 전체"}]} — 고칠 것이 없는 줄은 넣지 않아요.\n\n' + body)


def parse(text, n):
    """클로드 대답 → {줄 번호(0부터): 고친 글} (모양이 틀리면 ProofError)."""
    m = re.search(r"\{.*\}", str(text or ""), re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not isinstance(d.get("fix"), list):
        raise ProofError("클로드 대답 모양이 달라 자막 오타 고치기를 건너뛰었어요")
    out = {}
    for x in d["fix"]:
        if not isinstance(x, dict):
            continue
        try:
            i = int(x.get("i")) - 1
        except (TypeError, ValueError):
            continue
        t = re.sub(r"\s+", " ", str(x.get("t") or "")).strip()
        if 0 <= i < n and t:
            out[i] = t
    return out


def acceptable(old, new):
    """고친 줄을 받아도 되는지: 글자가 충분히 같고 길이가 크게 안 바뀜 (지어낸 말·요약 막기)."""
    a, b = plain(old), plain(new)
    if not a or not b or a == b and old.strip() == new.strip():
        return False
    if abs(len(b) - len(a)) > MAX_GROW * len(a) + 4:
        return False
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= MIN_SIM


def align_words(words, new_text):
    """원래 단어 [{w, s, e, p}] + 고친 글 → 고친 낱말에 원래 시각을 나눠 준 단어 목록.
    같은 낱말은 시각 그대로 · 바뀐 묶음은 그 묶음의 처음~끝 시각을 글자 수대로 나눔 · 새로 끼운 낱말은 바로 앞 낱말과 시각을 나눠 씀."""
    toks = new_text.split()
    if not words:
        return []
    if not toks:
        return []
    old = [plain(w["w"]) for w in words]
    new = [plain(t) for t in toks]
    blocks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal" and blocks and blocks[-1][0] != "equal":  # 이어진 바꾸기·끼우기·빼기는 한 묶음
            blocks[-1] = ["replace", blocks[-1][1], i2, blocks[-1][3], j2]
        else:
            blocks.append(["equal" if tag == "equal" else "replace", i1, i2, j1, j2])
    for k, b in enumerate(blocks):  # 끼우기만 한 묶음은 이웃 같은 낱말 하나와 시각을 나눠 씀
        if b[0] != "replace" or b[1] < b[2]:
            continue
        if k > 0 and blocks[k - 1][2] > blocks[k - 1][1]:
            blocks[k - 1][2] -= 1
            blocks[k - 1][4] -= 1
            b[1] -= 1
            b[3] -= 1
        elif k + 1 < len(blocks) and blocks[k + 1][2] > blocks[k + 1][1]:
            blocks[k + 1][1] += 1
            blocks[k + 1][3] += 1
            b[2] += 1
            b[4] += 1
    out = []
    for tag, i1, i2, j1, j2 in blocks:
        if tag == "equal":
            for a, b in zip(range(i1, i2), range(j1, j2)):
                out.append(dict(words[a], w=toks[b]))
        elif j2 > j1 and i2 > i1:
            s, e = float(words[i1]["s"]), float(words[i2 - 1]["e"])
            p = min(float(w.get("p", 1.0)) for w in words[i1:i2])
            weights = [len(new[b]) + 1 for b in range(j1, j2)]
            tot, t = sum(weights), s
            for b, wt in zip(range(j1, j2), weights):
                d = (e - s) * wt / tot
                out.append({"w": toks[b], "s": round(t, 2), "e": round(t + d, 2), "p": p})
                t += d
        # delete: 원래 낱말 버림
    return out


def apply(segs, fixes):
    """받아쓰기 구간 + {번호: 고친 글} → (새 구간 목록, 고친 줄 수)."""
    out, n = [], 0
    for i, s in enumerate(segs):
        t = fixes.get(i)
        if t is None or not acceptable(s.get("text") or "", t):
            out.append(s)
            continue
        ws = s.get("words") or []
        if ws:
            nw = align_words(ws, t)
            if not nw:
                out.append(s)
                continue
            out.append(dict(s, text=" ".join(w["w"] for w in nw), words=nw))
        else:
            out.append(dict(s, text=t))
        n += 1
    return out, n


def _batches(lines):
    """대사 줄을 MAX_CHARS 안으로 나눔 → [(시작 번호, 줄들)]."""
    out, cur, size, k0 = [], [], 0, 0
    for k, t in enumerate(lines):
        if cur and size + len(t) > MAX_CHARS:
            out.append((k0, cur))
            cur, size, k0 = [], 0, k
        cur.append(t)
        size += len(t) + 4
    if cur:
        out.append((k0, cur))
    return out


def proofread(segs, title="", terms=(), cancel=None, run=None):
    """받아쓰기 구간 → (고친 구간 목록, 고친 줄 수, 쓴 모델). run: 시험용 (claude_cli.run 흉내)."""
    if run is None:
        import claude_cli
        run = claude_cli.run
    idx = [k for k, s in enumerate(segs) if str(s.get("text") or "").strip()]
    lines = [re.sub(r"\s+", " ", str(segs[k]["text"])).strip() for k in idx]
    fixes, model = {}, None
    for k0, part in _batches(lines):
        res = run(build_prompt(part, title, terms), timeout=TIMEOUT, cancel=cancel)
        model = res.get("model") or model
        for i, t in parse(res.get("text"), len(part)).items():
            fixes[idx[k0 + i]] = t
    new, n = apply(segs, fixes)
    return new, n, model
