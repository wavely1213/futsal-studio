"""올리기 키트: 유튜브에 올릴 제목 후보·설명·챕터·태그를 영상 내용으로 만들고 썸네일을 확인 → 완성본 폴더에 <이름>_올리기.txt/.json.
키트 맞춤(E11): 다른 키트·이미 올린 우리 제목과 겹침 검사 · 채널 전략 할 일·시리즈 · 출연자·게스트(작업 폴더 guests.json) ·
만들어 둔 편집본(stock)과 다음 올릴 날(strategy.kit_slot) — 꼬리표·올릴 날은 열 때마다 계산하고 파일에는 쓰지 않음 (_LIVE_KEYS).

유튜브 규칙 (넘으면 저장이 안 되거나 무시됨)
- 제목 100자 · 설명 5000자 · 태그 합계 500자 · 해시태그 15개 · 제목·설명에 < > 못 씀
- 챕터: 첫 줄 00:00 · 3개 이상 · 각 10초 이상 (3분 안 되는 영상·쇼츠는 넣지 않음)
- 썸네일: 2MB 이하 · 1280×720 (쇼츠 1080×1920)
"""
import copy
import difflib
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter
from pathlib import Path

import core
import editor
import hooks

TITLE_MAX, DESC_MAX, TAGS_MAX, HASHTAG_MAX = 100, 5000, 500, 15
CH_MIN_VIDEO, CH_MIN_LEN, CH_MIN_COUNT = 180, 10, 3
SPLIT_LO, SPLIT_HI, SPLIT_TAIL = 90, 150, 30  # 90~150초마다 나누고, 끝에 30초도 안 남으면 안 나눔
CH_TITLE_MAX = 20
SHORTS_MAX = 180  # 쇼츠는 3분까지
THUMB_MAX = 2 * 1024 * 1024
THUMB_SIZES = {(1280, 720), (1080, 1920)}
IMG_EXTS = (".jpg", ".jpeg", ".png")
STUDIO_URL = "https://studio.youtube.com"
COACH = "최경진 감독"
BASE_TAGS = ["풋살", "풋살사관학교", "최경진 감독", "최경진", "풋살 레슨", "풋살 기술", "풋살 강좌", "축구", "futsal"]
SUFFIX = "_올리기"
_LOCK = threading.RLock()  # 만들기·고친 내용 저장이 겹쳐도 파일이 섞이지 않게

DEFAULT_TEMPLATE = """## 올리기 키트의 설명 틀이에요. 고쳐서 저장하면 다음에 만드는 키트부터 그대로 들어가요.
## '##'로 시작하는 줄은 설명에 들어가지 않아요 (안내용이에요).
## {훅} 영상 내용으로 만든 첫 두 줄 · {챕터} 목차 (3분 넘는 긴 영상만) · {해시태그} 해시태그 · {제목} 고른 제목 · {주제} 주제어
## {레슨 문의} 영상에서 말한 레슨·수강 안내 (인스타 DM·주말반 등 · 말하지 않았으면 빠져요)
{훅}

▶ 출연
최경진 감독 (풋살사관학교)

{레슨 문의}

{챕터}

{해시태그}
"""

# 문장 앞 군말: 띄어쓰기만 있어도 빼는 말 · '네'(넷)·'예'·'그'(그 공)는 바로 뒤에 쉼표·마침표가 있을 때만 ('네 번째' '그 공을'은 그대로)
_LEAD = ["자 이제", "그래서", "그리고", "그러면", "그럼", "근데", "이제", "여러분", "좋습니다", "좋아요", "오케이", "자", "어", "음", "아"]
_LEAD_RE = re.compile(r"(?:(?:" + "|".join(map(re.escape, _LEAD)) + r")[\s,.~!…]+|(?:네|예|그)\s*[,.~!…]+\s*|그\s+(?=(?:어|음|아)[\s,.~!…]))")
_SECTION = re.compile(r"^\s*(?:\d{1,2}\s*[.)]|\d{1,2}\s*(?:번|단계)|[①-⑳]|(?:첫|두|세|네|다섯)\s?(?:번째|째)|(?:step|part|chapter)\s?\d|#\d)", re.I)
# 편집 메모로 붙인 마커 이름 (챕터로 안 씀 · '방향 바꾸기' '공 빼기' '각 자르기' 같은 기술 이름은 걸리지 않게 좁게)
_MEMO = re.compile(r"TODO|FIXME|BGM|효과음|싱크|메모|삭제|지우기|자막\s?(?:수정|확인)|(?:수정|확인|체크)\s?(?:필요|요망)|(?:확인|체크)$|"
                   r"다시\s?(?:확인|찍|녹음)|^(?:여기|이\s?부분|요기)(?:\s|$)|\?\?|^\W*$", re.I)
_HASHTAG = re.compile(r"(?<![0-9A-Za-z가-힣_&#])#[0-9A-Za-z가-힣_]+")


# ---------- 공통 ----------

def _read_text(p):
    """메모장이 어떤 방식으로 저장했든 읽기 (UTF-8 · BOM · 옛 한글 cp949 · 유니코드 UTF-16)."""
    raw = Path(p).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _write_safe(path, text, encoding="utf-8"):
    """임시 파일에 다 쓴 뒤 바꿔 끼움. 다른 프로그램이 잡고 있어 못 바꾸면 ' (2)' 를 붙인 이름으로 저장."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    for i in range(20):  # Windows: 백신·OneDrive·미리보기가 잠깐 잡고 있으면 조금 뒤 다시
        try:
            os.replace(tmp, path)
            return path
        except PermissionError:
            time.sleep(0.05)
    for k in range(2, 50):
        alt = path.with_name(f"{path.stem} ({k}){path.suffix}")
        try:
            os.replace(tmp, alt)
            return alt
        except PermissionError:
            continue
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"{path.name} 파일을 저장하지 못했어요 · 다른 프로그램에서 열려 있으면 닫고 다시 해 주세요")


def stamp(t):
    t = int(t)
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _mtime(p):
    try:
        return p.stat().st_mtime
    except OSError:  # 그 사이 지워졌거나 잠김
        return 0.0


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _oneline(t, limit=None):
    t = hooks.NO_TITLE_CHARS.sub("", re.sub(r"\s+", " ", str(t or ""))).strip()
    return t[:limit].rstrip() if limit else t


# ---------- 자막 ----------

_SRT_T = r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
_SRT_LINE = re.compile(_SRT_T + r"\s*-->\s*" + _SRT_T)


def _srt_sec(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + float("0." + ms)


def parse_srt(text):
    """SRT 글 → [{"start", "end", "text"}] (번호·꾸밈 표시 무시, 깨진 칸은 건너뜀)."""
    out = []
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.split("\n")
        for k, ln in enumerate(lines):
            m = _SRT_LINE.search(ln)
            if not m:
                continue
            a, b = _srt_sec(*m.groups()[:4]), _srt_sec(*m.groups()[4:])
            txt = " ".join(x.strip() for x in lines[k + 1:] if x.strip())
            txt = re.sub(r"<[^>]*>|\{\\[^}]*\}", "", txt).strip()
            if txt:
                out.append({"start": round(a, 3), "end": round(max(a, b), 3), "text": txt})
            break
    return sorted(out, key=lambda s: s["start"])


def _segments(x):
    """대사 목록 · SRT 글 · SRT 파일 경로 모두 받음."""
    if isinstance(x, Path):
        x = _read_text(x)
    if isinstance(x, str):
        segs = parse_srt(x)
    else:
        segs = []
        for s in x or []:
            a = _num(s.get("start"))
            if a is None:
                continue
            b = _num(s.get("end"))
            segs.append({"start": a, "end": a if b is None else max(a, b), "text": re.sub(r"\s+", " ", str(s.get("text") or "")).strip()})
    return sorted((s for s in segs if s["text"]), key=lambda s: s["start"])


# ---------- 짧게 나눈 자막 → 문장 (#5 단어 단위 자막) ----------

_SENT_END = re.compile(r"(?:[.?!…]|(?:요|다|죠|까|니다|네|자|래)[~.?!]*)[\"'”’)]*$")
SENT_GAP, SENT_MAX = 1.2, 120  # 이 안에 이어지는 말만 · 이은 문장은 이 글자까지


def sentences(segs):
    """읽기 좋게 짧게 나눈 자막(쇼츠형·롱폼형 덩어리)을 다시 문장으로 이음 — 설명 첫 줄·챕터 제목·주제어가 반쪽 문장이 되지 않게.
    앞 자막이 문장 끝(마침표·'~요'·'~다' 등)이 아니고 사이가 1.2초 안이면 이어 붙임 (120글자까지).
    마침표 없이 여러 문장을 이어 쓴 긴 받아쓰기 구간(condition_on_previous_text=False · 30초 넘는 한 줄)은 먼저 문장 끝말에서 나눔 (E12 · captions.split_sentences)."""
    import captions
    out = []
    for s in captions.split_sentences(segs):
        p = out[-1] if out else None
        if (p and not _SENT_END.search(p["text"]) and s["start"] - p["end"] <= SENT_GAP
                and len(p["text"]) + 1 + len(s["text"]) <= SENT_MAX):
            p["text"] += " " + s["text"]
            p["end"] = max(p["end"], s["end"])
        else:
            out.append(dict(s))
    return out


# ---------- 챕터 ----------

def first_sentence(text, limit=CH_TITLE_MAX):
    """챕터 제목: 첫 문장에서 '자, 이제' 같은 군말을 빼고 limit 자 안으로 (낱말 중간에서 자르지 않음)."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.split(r"(?<=[.!?…])\s+", t)[0]
    while True:
        m = _LEAD_RE.match(t)
        if not m or m.end() >= len(t):
            break
        t = t[m.end():]
    t = hooks.NO_TITLE_CHARS.sub("", t).strip(" ,.~…!·-")
    if len(t) > limit:
        cut = t[:limit]
        sp = cut.rfind(" ")
        t = (cut[:sp] if sp >= limit // 2 else cut).rstrip(" ,.~…·-")
    return t


def _title_at(segs, t, k):
    """t 초에 시작하는 챕터 제목 (그 자리 첫 문장 · 인사말이면 '인트로')."""
    near = [s for s in segs if s["start"] >= t - 1.0][:3] or [s for s in segs if s["end"] > t][:1]
    if t == 0 and near and re.search(r"안녕하세요|반갑습니다|안녕하십니까", near[0]["text"]):
        return "인트로"
    for s in near:
        title = first_sentence(s["text"])
        if len(title) >= 2:
            return title
    return "인트로" if t == 0 else f"파트 {k + 1}"


def _auto_cuts(segs, a, b):
    """[a, b) 를 90~150초마다 가장 오래 쉰 곳(다음 말이 시작하는 곳)에서 나눈 시각들."""
    starts, prev_end = [], None
    for s in segs:
        pause = s["start"] - (prev_end if prev_end is not None else 0.0)
        prev_end = s["end"] if prev_end is None else max(prev_end, s["end"])
        if a < s["start"] < b:
            starts.append((s["start"], max(0.0, pause)))
    cuts, cur = [], a
    while b - cur > SPLIT_HI:
        win = [(p, s) for s, p in starts if cur + SPLIT_LO <= s <= min(cur + SPLIT_HI, b - SPLIT_TAIL)]
        if not win:  # 말이 없는 긴 구간(경기 장면 등) → 그 뒤 처음 말이 시작하는 곳
            win = [(p, s) for s, p in starts if s > cur + SPLIT_HI and b - s >= SPLIT_TAIL][:1]
            if not win:
                break
        mid = cur + (SPLIT_LO + SPLIT_HI) / 2
        _, s = max(win, key=lambda x: (round(x[0], 1), -abs(x[1] - mid)))
        cuts.append(s)
        cur = s
    return cuts


def _finalize(points, dur, segs):
    """유튜브 규칙에 맞게: 정수 초 · 첫 챕터 00:00 · 각 10초 이상 (이름 붙인 것 우선)."""
    items = sorted(((int(t), nm, named) for t, nm, named in points), key=lambda x: (x[0], not x[2]))
    if not items or items[0][0] >= CH_MIN_LEN:
        items.insert(0, (0, None, False))
    else:  # 10초 안쪽에서 시작하면 00:00 으로
        items[0] = (0,) + items[0][1:]
    out = []
    for t, nm, named in items:
        if dur - t < CH_MIN_LEN:  # 마지막 챕터도 10초 이상
            continue
        if out and t - out[-1][0] < CH_MIN_LEN:
            if named and not out[-1][2]:  # 바로 앞이 자동으로 나눈 곳이면 이름 붙인 쪽을 남김
                if len(out) == 1:
                    out[0] = (0, nm, True)
                elif t - out[-2][0] >= CH_MIN_LEN:
                    out[-1] = (t, nm, True)
            continue
        out.append((t, nm, named))
    res, seen = [], set()
    for k, (t, nm, named) in enumerate(out):
        title = nm or _title_at(segs, t, k)
        if title in seen:  # 같은 제목이 또 나오면 번호를 붙임
            title = f"{title} {sum(1 for x in res if x['title'].startswith(title)) + 1}"
        seen.add(title)
        res.append({"t": t, "time": stamp(t), "title": title, "named": bool(named)})
    return res


def _clean_name(v, limit=60):
    return _oneline(v, limit)


def chapters(segments_or_srt, markers=None, titles=None, dur=None, notes=None):
    """유튜브 챕터 [{"t", "time", "title", "named"}].
    이름 붙인 편집실 마커가 있으면 그 자리로 (없으면 '1. …'처럼 번호 붙은 그래픽 제목), 없으면 90~150초마다
    가장 오래 쉰 곳에서 나누고 그 자리 첫 문장(20자 이내)을 제목으로. 3분 미만이거나 규칙을 못 맞추면 []."""
    notes = [] if notes is None else notes
    segs = _segments(segments_or_srt)
    dur = _num(dur)
    if dur is None or dur <= 0:
        dur = max((s["end"] for s in segs), default=0.0)
    if dur < CH_MIN_VIDEO:
        notes.append("3분이 안 되는 영상이라 챕터는 넣지 않았어요")
        return []
    named, memo = [], []
    for m in markers or []:
        nm, t = _clean_name((m or {}).get("name")), _num((m or {}).get("t"))
        if nm and t is not None and 0 <= t < dur:
            (memo if _MEMO.search(nm) else named).append((t, nm))
    if memo:
        notes.append("편집 메모처럼 보이는 마커 이름은 챕터에서 뺐어요 · " + ", ".join(f"'{nm}'" for _, nm in memo[:5]))
    from_markers = bool(named)
    if not named:  # 번호 붙은 그래픽 제목('1. 퍼스트 터치')은 장 제목으로 봄
        for x in titles or []:
            nm, t = _clean_name((x or {}).get("text")), _num((x or {}).get("start"))
            if nm and t is not None and 0 <= t < dur and _SECTION.match(nm):
                named.append((t, nm))
    pts = [(t, nm, True) for t, nm in named]
    by_marker = "편집실 마커 이름으로 챕터를 만들었어요 · 메모로 붙인 이름이 있으면 설명에서 고쳐 주세요"
    if pts:
        out = _finalize(pts, dur, segs)
        if len(out) >= CH_MIN_COUNT:
            if from_markers:
                notes.append(by_marker)
            return out
        bounds = sorted({0.0, dur} | {float(int(t)) for t, _ in named})
        extra = [(s, None, False) for a, b in zip(bounds, bounds[1:]) for s in _auto_cuts(segs, a, b)]
        out = _finalize(pts + extra, dur, segs)
        if len(out) >= CH_MIN_COUNT:
            if from_markers:
                notes.append(by_marker)
            notes.append("이름 붙인 마커가 적어서 사이사이 쉬는 곳에서 챕터를 더 나눴어요")
            return out
    elif segs:
        out = _finalize([(0.0, None, False)] + [(s, None, False) for s in _auto_cuts(segs, 0.0, dur)], dur, segs)
        if len(out) >= CH_MIN_COUNT:
            return out
    notes.append("나눌 곳이 마땅치 않아 챕터는 넣지 않았어요 (챕터는 3개 이상, 각 10초 이상이어야 해요)"
                 if segs or pts else "받아쓰기가 없어 챕터를 만들지 못했어요")
    return []


def chapters_text(chs):
    return "\n".join(f"{c['time']} {c['title']}" for c in chs)


# ---------- 설명 · 태그 ----------

def template_path():
    return core.WORK / "upload_template.txt"


def load_template():
    """작업 폴더의 설명 틀 (없으면 기본 틀을 만들어 둠 · 사용자가 메모장으로 고침)."""
    p = template_path()
    if not p.exists():
        try:
            _write_safe(p, DEFAULT_TEMPLATE, "utf-8-sig")
        except (OSError, RuntimeError):
            return DEFAULT_TEMPLATE
    try:
        t = _read_text(p)
    except OSError:
        return DEFAULT_TEMPLATE
    return t if t.strip() else DEFAULT_TEMPLATE


def _sent_score(t):
    return sum(w for k, w in editor.KEYWORDS.items() if k in t) + min(len(t), 60) / 30


def _topic_hook(hook, topics):
    """편집실 쇼츠 훅('국가대표 꿀팁' · editor._hook 은 editor.KEYWORDS 로 주제를 고름)을 키트의 주제어로 다시 씀
    → 제목·해시태그·태그·설명이 같은 주제 ('팬텀 드리블 꿀팁'). 주제어가 풋살 용어가 아니면(자주 나온 낱말) 훅의 말이 풋살 용어일 때만 그대로."""
    if not hook:
        return hook
    tail = "꿀팁" if hook.endswith("꿀팁") else "!"
    word = hook[: -len(tail)].strip()
    terms = {re.sub(r"\s+", "", t) for t in hooks.topic_terms()}
    if topics and re.sub(r"\s+", "", topics[0]) in terms:
        return f"{topics[0]} {tail}" if tail == "꿀팁" else f"{topics[0]}!"
    return hook if re.sub(r"\s+", "", word) in terms else None


# 설명 첫 줄 인용으로 쓰지 않는 말 (D-084): 인사·마무리('다음 영상에서 만나요' · msg.CLOSING 과 같은 갈래) ·
# 순서 말('두번째 포인트는')은 떼고 남은 말로 봄
_QUOTE_SKIP = re.compile(r"안녕하세요|반갑습니다|구독|좋아요|알림|오늘은 여기까지|오늘 영상은 여기|감사합니다|고맙습니다|다음 (?:시간|영상|편|주)|"
                         r"또 (?:만나|봬|뵙)|만나요|뵐게요|안녕히|댓글|궁금한\s?(?:건|거|점)|남겨\s?주세요|공유|정리해\s?(?:볼게요|보겠습니다|드릴게요)|정리할게요")
_ORDER_LEAD = re.compile(r"^(?:자[,\s]*)?(?:(?:마지막,?\s*)?(?:첫|두|세|네|다섯|여섯|일곱|[1-9]) ?(?:번째|째)|첫째|둘째|셋째|넷째|다음은|다음으로|"
                         r"마지막으로|이번엔|이번에는|그 ?다음)\s*(?:(?:포인트|팁|방법|단계|순서|동작|핵심)(?:는|은|입니다|예요|에요)?)?[\s,.!:]*")


# 인용 앞에서 떼는 맞장구 ('그렇죠 이게 완벽한 퍼스트 터치에요' → '이게 완벽한 …')
_QUOTE_LEAD = re.compile(r"^(?:그렇죠|그쵸|맞죠|맞아요|오케이|됐어요)[\s,.!?~]+(?=\S)")
# 말을 고친 문장 ('패스는 왼 아니 오른발 앞쪽으로' · '말이 꼬였네 다시 할게요') — 인용하면 실수가 첫 줄에 남음
_SLIP = re.compile(r"\S\s+아니[,.]?\s+\S|다시 할게요|다시 하겠습니다|말이 꼬|잘못 말|정정할게요|아니 아니")
# 마침표 없이 이어 붙은 받아쓰기 ('중요해요 터치가 조금 길었네요 그렇죠 이게 …') — 문장 끝말이 가운데에 두 번 넘게 있으면 여러 문장
_MID_END = re.compile(r"(?:요|다|죠|네|까)(?=\s+\S)")


def _quote_key(t):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")


def _quotable(t):
    """설명 첫 줄로 인용해도 되는 문장인지 (인사·마무리 · 끊긴 조각 · 말을 고친 문장 · 여러 문장이 이어 붙은 받아쓰기는 X · D-084)."""
    if not 10 <= len(t) <= 70 or _QUOTE_SKIP.search(t) or _SLIP.search(t):
        return False
    if not _SENT_END.search(t) and re.search(r"\s\S$", t):  # '고개들기 디딤발걸이 이'처럼 끊긴 받아쓰기 조각
        return False
    return len(_MID_END.findall(t)) < 2


def hook_lines(segs, topics, fmt="long", flow=None, dur=None, avoid=()):
    """설명 첫 두 줄: 감독님이 한 말 중 가장 힘 있는 한 문장 + 무엇을 알려 주는지 (flow: 나오는 순서의 주제어).
    쇼츠는 3분까지라 '1분 안에'는 1분 이하일 때만.
    인사·마무리 말은 고르지 않고, '두번째 포인트는 …'은 순서 말을 뗀 뒤 봄 · 강조어·기술 이름이 든 문장을 먼저 ·
    avoid: 같은 촬영본의 다른 키트가 이미 쓴 인용 (글자만 견줌 · 한쪽이 다른 쪽을 품어도 같은 문장으로 봄 · D-084)."""
    topic = topics[0] if topics else "풋살"
    avoid = {_quote_key(a) for a in avoid or () if a}
    names = {_quote_key(x) for x in list(topics[:3]) + hooks.topic_terms() + hooks.LESSON_TERMS if len(_quote_key(x)) >= 2}
    best, best_sc = None, -1.0
    for s in segs:
        t = first_sentence(_QUOTE_LEAD.sub("", _ORDER_LEAD.sub("", first_sentence(s["text"], 120))), 70)
        k = _quote_key(t)
        if not _quotable(t) or any(k in a or a in k for a in avoid if min(len(a), len(k)) >= 6):
            continue
        sc = _sent_score(t) + sum(2 for tp in topics[:3] if tp.replace(" ", "") in t.replace(" ", ""))
        if any(w in t for w in editor.KEYWORDS if w != "?") or any(n in k for n in names):  # 강조어·기술 이름이 든 문장 먼저
            sc += 10
        if sc > best_sc:
            best, best_sc = t, sc
    line1 = f"“{best}”" if best else f"{topic}{hooks.josa(topic, '이')} 늘 제자리라면 이 영상으로 바로잡아 드릴게요."
    if fmt == "shorts":
        line2 = f"{COACH}의 {topic} 꿀팁을 {'1분 안에' if hooks.one_minute(dur) else '짧게'} 담았어요."
    else:
        names = "·".join(flow or topics[:3]) or "풋살 기본기"
        line2 = f"{COACH}이 {names}{hooks.josa(names, '을')} 차근차근 알려 드려요."
    return [line1, line2]


def hashtags(topics, fmt="long"):
    """해시태그 3개 (쇼츠는 맨 앞에 #shorts)."""
    pool = ["풋살", topics[0] if topics else None, "풋살사관학교", "풋살레슨", *topics[1:]]
    out = []
    for p in pool:
        h = "#" + re.sub(r"[^0-9A-Za-z가-힣_]", "", p or "")
        if len(h) > 1 and h not in out:
            out.append(h)
        if len(out) == 3:
            break
    return (["#shorts"] if fmt == "shorts" else []) + out


def tags_len(tags):
    """유튜브 스튜디오가 세는 방식: 띄어쓰기가 있는 태그는 따옴표 2자 + 태그 사이 쉼표."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def split_tags(text):
    return [t for t in (re.sub(r"\s+", " ", x).strip() for x in re.split(r"[,\n]", text or "")) if t]


def make_tags(topics, fmt="long", limit=TAGS_MAX):
    cand = (["shorts", "쇼츠", "풋살 쇼츠"] if fmt == "shorts" else []) + list(topics[:5]) + [f"풋살 {t}" for t in topics[:3]] + BASE_TAGS
    out = []
    for t in cand:
        t = re.sub(r"\s+", " ", re.sub(r"[<>,#\"]", "", t or "")).strip()
        if not t or len(t) > 30 or t.lower() in {x.lower() for x in out} or tags_len(out + [t]) > limit:
            continue
        out.append(t)
    return out


def _limit_hashtags(text, notes):
    found = list(_HASHTAG.finditer(text))
    if len(found) <= HASHTAG_MAX:
        return text
    for m in reversed(found[HASHTAG_MAX:]):
        text = text[:m.start()] + text[m.end():]
    notes.append(f"해시태그가 {HASHTAG_MAX}개를 넘으면 유튜브가 전부 무시해서 앞의 {HASHTAG_MAX}개만 남겼어요")
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def fit_description(text, notes=None):
    """유튜브 설명 규칙: < > 빼기 · 빈 줄 정리 · 해시태그 15개 · 5000자."""
    notes = [] if notes is None else notes
    text = text.replace("\r\n", "\n").replace("<", "〈").replace(">", "〉")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(ln.rstrip() for ln in text.split("\n"))).strip()
    text = _limit_hashtags(text, notes)
    if len(text) > DESC_MAX:
        cut = text[:DESC_MAX]
        text = cut[:cut.rfind("\n")] if cut.rfind("\n") > DESC_MAX * 0.8 else cut
        notes.append(f"설명이 {DESC_MAX}자를 넘어서 뒷부분을 잘랐어요")
    return text


# ---------- 영상에서 말한 레슨 홍보 → 설명 '▶ 레슨 문의' (E12 · BR-062) ----------
PROMO_SLOT = "{레슨 문의}"
PROMO_HEAD = "▶ 레슨 문의"
_PROMO_LEAD = re.compile(r"^(?:(?:아\s*참|아참|참|참고로|그리고|아|자|또)[\s,.~!]*)+")


def promo_lines(segs):
    """받아쓰기 → 영상에서 말한 레슨·수강 홍보 문장들 (설명 '▶ 레슨 문의' 줄 · 나중에 설명 틀 링크 칸의 기본값으로도).
    takes.notice_kinds 가 홍보로 본 문장 (연락할 곳·'레슨 문의'에 '주세요'·'주시면' 같은 부탁 꼴이 있는 줄과 바로 옆 '주말반 자리 있어요') —
    촬영 준비 말·끝인사 뒤 촬영 끝 말 안에서 한 홍보도 넣음 (영상에서는 잘려도 설명에는 남게). 앞 군말('아 참,')만 떼고 'dm'은 'DM', 끝에 마침표."""
    import captions
    import takes
    sents = takes._clean(captions.split_sentences(segs or []))
    kinds = takes.notice_kinds(sents)
    out = []
    for i in sorted(k for k, v in kinds.items() if v == "promo"):
        t = _PROMO_LEAD.sub("", re.sub(r"\s+", " ", str(sents[i]["text"])).strip())
        t = re.sub(r"(?<![A-Za-z])dm(?![A-Za-z])", "DM", t, flags=re.I).strip(" ,")
        if len(t) >= 4:
            t = t if re.search(r"[.!?…]$", t) else t + "."
            if t not in out:
                out.append(t)
    return out


def add_promo(text, lines, notes=None):
    """설명 글에 '▶ 레슨 문의' 묶음을 넣음 — 설명 틀의 {레슨 문의} 자리 (없으면 목차·해시태그 앞, 그것도 없으면 맨 끝).
    lines 가 비면 {레슨 문의} 자리만 지움 · 설명 틀에 이미 '▶ 레슨 문의' 묶음(감독님이 직접 쓴 연락처)이나 같은 문장이 있으면 넣지 않음."""
    text = str(text or "")
    have = PROMO_HEAD in text.replace(PROMO_SLOT, "") or any(ln.rstrip(".") in text for ln in lines or ())
    block = (PROMO_HEAD + "\n" + "\n".join(lines)) if lines and not have else ""
    if lines and have and notes is not None:
        notes.append("설명 틀에 이미 '▶ 레슨 문의'가 있어서 영상에서 말한 레슨 안내는 따로 넣지 않았어요")
    if PROMO_SLOT in text:
        text = re.sub(r"\n{3,}", "\n\n", text.replace(PROMO_SLOT, block))
    elif block:
        ls = text.split("\n")
        at = next((i for i, ln in enumerate(ls) if ln.startswith("▶ 목차")), None)
        if at is None:
            at = next((i for i in range(len(ls) - 1, -1, -1) if _HASHTAG.match(ls[i].strip())), None)
        if at is None:
            ls += ["", block]
        else:
            ls[at:at] = [block, ""]
        text = "\n".join(ls)
        if notes is not None:
            notes.append("영상에서 말한 레슨 안내를 설명의 '▶ 레슨 문의'에 넣었어요 (설명 틀에 {레슨 문의} 자리를 두면 그 자리에 들어가요)")
    return fit_description(text, notes)


def description(hook, chs, tags_h, title="", topics=(), template=None, notes=None):
    """설명 틀(upload_template.txt)의 {훅} {챕터} {해시태그} {제목} {주제} 를 채움."""
    notes = [] if notes is None else notes
    tpl = load_template() if template is None else template
    tpl = "\n".join(ln for ln in tpl.replace("\r\n", "\n").split("\n") if not ln.lstrip().startswith("##"))
    if chs and "{챕터}" not in tpl:
        tpl = tpl.rstrip() + "\n\n{챕터}"
        notes.append("설명 틀에 {챕터} 자리가 없어 맨 아래에 목차를 붙였어요")
    if tags_h and "{해시태그}" not in tpl:
        tpl = tpl.rstrip() + "\n\n{해시태그}"
    vals = {"{훅}": "\n".join(hook), "{챕터}": ("▶ 목차\n" + chapters_text(chs)) if chs else "", "{해시태그}": " ".join(tags_h),
            "{제목}": title, "{주제}": ", ".join(topics[:3])}
    text = re.sub("|".join(map(re.escape, vals)), lambda m: vals[m[0]], tpl)  # 한 번에 바꿈 (넣은 글 속 {…}는 그대로)
    return fit_description(text, notes)


# ---------- 썸네일 확인 ----------

def image_size(path):
    """PNG·JPG 머리 부분만 읽어 (가로, 세로)."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:2] != b"\xff\xd8":
                return None
            f.seek(2)
            while True:
                b = f.read(1)
                while b and b != b"\xff":
                    b = f.read(1)
                while b == b"\xff":
                    b = f.read(1)
                if not b:
                    return None
                mk = b[0]
                if mk in (0xD8, 0x01) or 0xD0 <= mk <= 0xD7:
                    continue
                ln = struct.unpack(">H", f.read(2))[0]
                if 0xC0 <= mk <= 0xCF and mk not in (0xC4, 0xC8, 0xCC):
                    f.read(1)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                f.seek(ln - 2, 1)
    except (OSError, struct.error):
        return None


def _thumb_images(pre, files):
    """그 영상(pre = '<분석 폴더 이름>_')의 썸네일 그림만: 그림 파일 중 A/B 묶음의 '모바일 비교' 한 장은 썸네일이 아님
    (가로라서 긴 영상 썸네일로 잡혀 유튜브에 올라갈 뻔함) — 7단계 썸네일 확인(_images_of)과 4단계 '썸네일 ✓'(progress)가 같은 규칙."""
    import thumb  # 이름(AB_SHEET)만 씀 · 지연 import: 올리기 키트(·youtube_upload)를 불러올 때 thumb 의 폴더 만들기가 돌지 않게
    sheet = pre + thumb.AB_SHEET
    return [p for p in files if p.suffix.lower() in IMG_EXTS and not p.name.startswith(sheet)]


def _images_of(name):
    """썸네일 편집기가 이 영상 이름으로 저장한 이미지들 (이름이 더 긴 다른 영상의 것은 빼고 · 최근 것부터).
    A/B 묶음('_썸네일_A'·'_B'·… · 같은 ' (2)')은 한 덩어리로 (묶음에서 가장 늦게 쓴 시각) A 부터 — 마지막에 쓴 B·C 가 아니라 첫 고른 A 가 7단계·바로 올리기 썸네일."""
    import thumb  # 이름(AB_TAGS)만 씀 · 지연 import (위와 같은 까닭)
    pre = core.adir(name).name + "_"
    try:
        longer = {core.adir(v.name).name + "_" for v in core.VIDEOS.iterdir() if core.is_video_file(v.name)}
        files = [p for p in core.OUT.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS and p.name.startswith(pre)]
    except OSError:
        return []
    longer = {o for o in longer if o != pre and o.startswith(pre)}
    files = _thumb_images(pre, [p for p in files if not any(p.name.startswith(o) for o in longer)])
    ab = re.compile(re.escape(pre) + "썸네일_([" + thumb.AB_TAGS + r"])( \(\d+\))?\.jpg$")  # thumb.export_ab 의 이름
    sets = {}
    for p in files:
        m = ab.match(p.name)
        if m:
            sets[m.group(2)] = max(sets.get(m.group(2), 0), _mtime(p))

    def order(p):
        m = ab.match(p.name)
        return (sets[m.group(2)], -thumb.AB_TAGS.index(m.group(1))) if m else (_mtime(p), 0)
    return sorted(files, key=order, reverse=True)


def thumbnail_check(name, fmt="long"):
    """가장 최근에 저장한 썸네일(A/B 묶음이면 그 묶음의 A) 확인: 2MB 이하 · 1280×720(긴 영상) / 1080×1920(쇼츠)."""
    files = _images_of(name)
    if not files:
        return {"ok": False, "file": None, "problems": ["아직 저장한 썸네일이 없어요 · 아래 '썸네일 만들기'를 눌러 편집기에서 '이미지로 저장'을 눌러 주세요"]}
    sized = [(p, image_size(p)) for p in files[:30]]
    vertical = fmt == "shorts"
    pick = next(((p, s) for p, s in sized if s and (s[1] > s[0]) == vertical), sized[0])
    p, size = pick
    try:
        nbytes = p.stat().st_size
    except OSError:
        return {"ok": False, "file": None, "problems": ["썸네일 파일을 읽지 못했어요 · 다시 확인해 주세요"]}
    probs = []
    if nbytes > THUMB_MAX:
        probs.append(f"파일이 {nbytes / 1048576:.1f}MB예요 · 유튜브 썸네일은 2MB까지만 올라가요 (썸네일 편집기에서 JPG로 다시 저장해 주세요)")
    if not size:
        probs.append("이미지 크기를 읽지 못했어요 · JPG나 PNG로 다시 저장해 주세요")
    else:
        w, h = size
        if (w, h) not in THUMB_SIZES:
            probs.append(f"크기가 {w}×{h}예요 · 유튜브 권장 크기는 1280×720(긴 영상) · 1080×1920(쇼츠)이에요")
        if vertical and w > h:
            probs.append("가로 썸네일이에요 · 쇼츠에는 세로(1080×1920) 썸네일이 어울려요")
        if not vertical and h > w:
            probs.append("세로(쇼츠용) 썸네일이에요 · 긴 영상에는 가로 1280×720 썸네일을 올려 주세요")
    return {"ok": not probs, "file": p.name, "bytes": nbytes, "size_text": f"{nbytes / 1048576:.2f}MB",
            "w": size[0] if size else None, "h": size[1] if size else None, "problems": probs, "mtime": int(_mtime(p))}


# ---------- 프로젝트 (읽기만) ----------

def read_project(name):
    """편집실 프로젝트를 읽기만 함 (고치거나 새로 만들지 않음). 없거나 깨졌으면 None."""
    p = editor._ppath(name)
    for i in range(5):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            break
        except PermissionError:  # Windows: 편집실이 막 저장하는 중
            time.sleep(0.1)
        except (OSError, ValueError):
            return None
    else:
        return None
    if not isinstance(d, dict):
        return None
    seqs = []
    for s in d.get("sequences") or []:
        try:
            seqs.append(editor.migrate_seq(copy.deepcopy(s)))
        except Exception:
            continue
    d["sequences"] = seqs
    return d


def _seq_label(sq):
    """editor.export 와 같은 파일 이름 규칙."""
    fmt = sq.get("format", "shorts")
    return re.sub(r'[\\/:*?"<>|]', "", sq.get("name") or ("쇼츠" if fmt == "shorts" else "롱폼")).strip(" .")


def latest_export(name, sq):
    """이 편집본을 가장 최근에 내보낸 영상 (구간 내보내기는 뺌)."""
    rx = re.compile(rf"{re.escape(core.adir(name).name)}_{re.escape(_seq_label(sq))}(?: \(\d+\))*\.mp4", re.I)
    try:
        hits = [p for p in core.OUT.iterdir() if rx.fullmatch(p.name)]
    except OSError:
        return None
    return max(hits, key=_mtime, default=None)


def _target(name, seq, proj=None):
    """(편집본, 가장 최근에 내보낸 영상) · 원본 영상 그대로면 (None, None)."""
    if not seq:
        return None, None
    proj = read_project(name) if proj is None else proj
    sq = next((s for s in (proj or {}).get("sequences") or [] if s.get("id") == seq), None)
    if sq is None:
        raise LookupError("편집본을 찾지 못했어요 · 편집실에서 지웠다면 다시 골라 주세요")
    return sq, latest_export(name, sq)


def _kit_paths(stem):
    return core.OUT / f"{stem}{SUFFIX}.txt", core.OUT / f"{stem}{SUFFIX}.json"


_KIT_FILE = re.compile(re.escape(SUFFIX) + r"(?: \(\d+\))?\.(json|txt)$")


def _read_kit(p):
    for i in range(5):
        try:
            k = json.loads(Path(p).read_text(encoding="utf-8"))
            return k if isinstance(k, dict) else None
        except PermissionError:  # Windows: 막 저장하는 중
            time.sleep(0.05)
        except (OSError, ValueError):
            return None
    return None


def _owner(kit):
    src = kit.get("source") if isinstance(kit.get("source"), dict) else {}
    return kit.get("name"), src.get("id") or ""


def _find_kit(name, seq):
    """이 영상·편집본으로 저장해 둔 키트 중 가장 최근 것 (파일 이름이 바뀌었어도 안에 적힌 영상·편집본으로 찾음) → (경로, 키트)."""
    pre, want = core.adir(name).name + "_", (name, seq or "")
    try:
        files = [p for p in core.OUT.iterdir() if p.name.startswith(pre) and p.suffix == ".json" and _KIT_FILE.search(p.name)]
    except OSError:
        return None, None
    for p in sorted(files, key=_mtime, reverse=True):
        kit = _read_kit(p)
        if kit is not None and _owner(kit) == want:
            return p, kit
    return None, None


def _kit_stem(name, seq, sq):
    """키트 파일 이름 앞부분: 원본은 '<영상>' · 편집본은 '<영상>_<편집본 이름>'.
    내보낼 때마다 붙는 (2)·(3)과 상관없이 늘 같은 이름 · 다른 영상·편집본이 이미 쓰는 이름이면 (2), (3)…"""
    base = core.adir(name).name + (f"_{_seq_label(sq)}" if sq else "")
    for k in range(1, 100):
        stem = base if k == 1 else f"{base} ({k})"
        js = _kit_paths(stem)[1]
        kit = _read_kit(js) if js.exists() else None
        if kit is None or _owner(kit) == (name, seq or ""):
            return stem
    return base


def sources(name):
    """키트를 만들 수 있는 것: 원본 영상 그대로 + 편집실 편집본 (내보낸 영상이 있으면 표시 · 가장 최근에 내보낸 것을 기본으로)."""
    editor.video_path(name)
    proj = read_project(name)
    out, best, newest = [{"id": "", "label": "원본 영상 그대로", "format": None, "export": None}], "", 0.0
    for s in (proj or {}).get("sequences") or []:
        exp = latest_export(name, s)
        out.append({"id": s.get("id"), "label": s.get("name") or "편집본", "format": "shorts" if s.get("format") == "shorts" else "long",
                    "export": exp.name if exp else None})
        if exp and _mtime(exp) > newest:
            best, newest = s.get("id"), _mtime(exp)
    return {"sources": out, "default": best}


# ---------- 키트 ----------

def _transcript(d):
    tj = d / "transcript.json"
    try:
        return _segments(json.loads(tj.read_text(encoding="utf-8"))) if tj.exists() else []
    except (OSError, ValueError):
        return []


def _overlay_hook(hook, texts):
    """쇼츠 편집본의 큰 제목 글자를 제목 후보(훅)로 써도 되는지 → 쓸 글 · 안 되면 None (D-082 보강).
    자동 가편집이 넣는 큰 글자는 editor._hook 이 고른 '주제어 꿀팁'·'주제어!' 아니면 첫 문장을 16자에서 자른 것 —
    잘린 조각('오늘은 슈팅 챌린지예요. 다섯')·인사('안녕하세요. 오늘은')·순서 말('자, 두번째 포인트는 …')은 기본 제목이 되지 않게 버림.
    손으로 쓴 글(대사에 그대로 없는 말)이나 대사의 한 문장을 끝까지 쓴 글은 그대로."""
    h = (hook or "").strip()
    if not h:
        return None
    terms = {hooks._compact(x) for x in hooks.topic_terms()}
    if (h.endswith("꿀팁") or (h.endswith("!") and hooks._compact(h[:-1]) in terms)) and not _QUOTE_SKIP.search(h):
        return h  # editor._hook 이 고른 '주제어 꿀팁'·'주제어!'
    if _QUOTE_SKIP.search(h) or _ORDER_LEAD.match(h):
        return None
    k = _quote_key(h)
    text = " ".join(texts or [])
    keep = [i for i, c in enumerate(text) if re.match(r"[0-9A-Za-z가-힣]", c)]  # 글자만 이은 대사 → 원래 자리
    said = "".join(text[i] for i in keep)
    at = said.find(k) if len(k) >= 4 else -1
    if at < 0:
        return h  # 대사에 없는 말 = 손으로 쓴 제목
    end = keep[at + len(k) - 1] + 1
    mid_word = end < len(text) and bool(re.match(r"[0-9A-Za-z가-힣]", text[end]))  # '갔네|요'처럼 낱말 가운데에서 잘림
    whole = not mid_word and bool(_SENT_END.search(h)) and not _MID_END.search(h) and not re.search(r"[.?!…]\s+\S", h)
    return h if whole else None  # 대사 첫머리를 문장 중간에서 자른 조각 (두 문장에 걸친 것도)


def _parent_topics(d):
    """원본 영상 전체 대사의 주제어 (쇼츠·편집본 대사에 풋살 용어가 없을 때 물려받음 · D-081)."""
    srt = d / "subtitles.srt"
    segs = _segments(srt) if srt.exists() else _transcript(d)
    return hooks.topic_keywords([s["text"] for s in sentences(segs)], n=3)


def summary(segs, chs, dur, limit=2500):
    """받아쓰기 요약 (AI 없이): 챕터(없으면 90초)마다 힘 있는 문장 3개."""
    if not segs:
        return "(받아쓰기가 없어요)"
    full = "\n".join(f"({stamp(s['start'])}) {s['text']}" for s in segs)
    if len(full) <= limit:
        return full
    bounds = [c["t"] for c in chs] or list(range(0, int(max(dur, segs[-1]["end"])) + 1, 90))
    parts, seen = [], set()
    for i, a in enumerate(bounds):
        b = bounds[i + 1] if i + 1 < len(bounds) else float("inf")
        inside = []
        for s in segs:  # 같은 말을 되풀이한 문장은 한 번만
            if a <= s["start"] < b and s["text"] not in seen:
                seen.add(s["text"])
                inside.append(s)
        if not inside:
            continue
        top = sorted(sorted(inside, key=lambda s: -_sent_score(s["text"]))[:3], key=lambda s: s["start"])
        head = f"[{stamp(a)}]" + (f" {chs[i]['title']}" if chs else "")
        parts.append(head + "\n" + "\n".join(f"- {s['text']}" for s in top))
    text = "\n".join(parts)
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n…"


def claude_prompt(kit, segs):
    """Claude 에게 붙여 넣을 질문 (무료 · 사용자가 직접 붙여 넣음)."""
    short = kit["format"] == "shorts"
    m, s = divmod(int(kit["duration"]), 60)
    lines = [f"유튜브 '풋살사관학교' 채널({COACH}의 풋살 레슨)에 영상을 올리려고 해요. 아래 내용을 보고 도와주세요.", "",
             "[영상]", f"- 형식: {'쇼츠 (세로 9:16)' if short else '긴 영상 (가로 16:9)'}", f"- 길이: {m}분 {s:02d}초",
             f"- 주제어: {', '.join(kit['topics']) or '(없음)'}", ""]
    if kit["chapters"]:
        lines += ["[목차]", chapters_text(kit["chapters"]), ""]
    lines += ["[대사 요약 (받아쓰기에서 뽑은 문장)]", summary(segs, kit["chapters"], kit["duration"]), "",
              "[지금 만든 초안]", *[f"- 제목 후보 {i}: {t}" for i, t in enumerate(kit["titles"], 1)],
              f"- 태그: {', '.join(kit['tags'])}", "",
              "[부탁해요]",
              f"1. 클릭하고 싶어지는 제목 5개 — {TITLE_MAX}자 이내, 과장·낚시 없이, < > 기호 없이",
              "2. 설명 첫 두 줄 2가지 — 검색 결과에 보이는 부분이라 핵심을 먼저",
              f"3. 해시태그 3개{' (#shorts 포함해서 4개)' if short else ''}",
              f"4. 태그 15개 — 쉼표로 구분, 다 합쳐 {TAGS_MAX}자 이내",
              "5. 썸네일에 넣을 짧은 문구 3개 — 8자 이내",
              "한국어로, 바로 복사해서 쓸 수 있게 번호만 붙여 답해 주세요."]
    return "\n".join(lines)


def counts(kit):
    return {"title": len(kit.get("title") or ""), "description": len(kit.get("description") or ""), "tags": tags_len(kit.get("tags") or []),
            "hashtags": len(_HASHTAG.findall(kit.get("description") or ""))}


def build_kit(name, seq=None, save=True):
    """올리기 키트 만들기. seq: 편집실 편집본 id (없으면 원본 영상 그대로)."""
    editor.video_path(name)
    d, notes = core.adir(name), []
    proj = read_project(name) if seq else None
    if seq and proj is None:
        raise LookupError("편집실 프로젝트를 읽지 못했어요 · 편집실을 한 번 연 뒤 다시 해 주세요")
    sq, exp = _target(name, seq, proj)
    if sq:
        fmt = "shorts" if sq.get("format") == "shorts" else "long"
        segs = _segments(exp.with_suffix(".srt")) if exp and exp.with_suffix(".srt").exists() else []
        if not segs:  # 내보낸 자막이 없으면 편집본 자막(없으면 받아쓰기)을 편집본 시간으로 옮김
            caps = [c for c in (proj.get("captions") or []) if c.get("text")] or _transcript(d)
            try:
                segs = _segments(editor.timeline_captions(dict(sq, captions=caps))) if caps else []
            except Exception:
                segs = []
            notes.append("내보낸 자막 파일(.srt)이 없어서 편집본 자막으로 만들었어요" if segs else
                         "이 편집본에는 자막이 없어요 · 편집점 찾기를 했는지 확인해 주세요")
        dur = (editor.probe(exp)["duration"] if exp else 0.0) or editor.seq_total(sq)
        markers, overlays = sq.get("markers"), sq.get("titles")
        source = {"id": sq.get("id"), "label": sq.get("name") or "편집본", "export": exp.name if exp else None}
        if not exp:
            notes.append("이 편집본은 아직 내보내지 않았어요 · 편집실에서 내보낸 영상과 함께 올려 주세요")
    else:
        info = editor.media_info(name)
        dur = info["duration"]
        fmt = "shorts" if info["height"] > info["width"] and dur <= SHORTS_MAX else "long"
        srt = d / "subtitles.srt"
        segs = _segments(srt) if srt.exists() else []
        if not segs:
            segs = _transcript(d)
            if segs:
                notes.append("자막 파일(subtitles.srt)이 없어서 받아쓰기 기록(transcript.json)으로 만들었어요")
            else:
                notes.append("아직 받아쓰기가 없어요 · 보관함에서 '편집점 찾기'를 하면 챕터·설명이 영상 내용으로 채워져요")
        markers = overlays = None
        source = {"id": "", "label": "원본 영상 그대로", "export": None}

    segs = sentences(segs)  # 자막 덩어리(반쪽 문장)가 아니라 문장 단위로
    texts = [s["text"] for s in segs]
    tinfo = {}  # 쇼츠·편집본에 용어가 없으면 원본 영상 주제 · 쇼츠는 그 구간의 요점(레슨 말)을 먼저 (D-081)
    topics = hooks.kit_topics(texts, (lambda: _parent_topics(d)) if sq else None, focus=bool(sq) and fmt == "shorts", info=tinfo)
    flow = hooks.in_order(topics[:3], texts)
    if topics and topics[0] in hooks.BASIC_PHRASES:  # '기본 자세'는 대사에 그대로 없어도 맨 앞
        flow = [topics[0]] + [t for t in flow if t != topics[0]]
    hook = None
    if sq and fmt == "shorts":  # 쇼츠 편집본의 큰 제목 글자가 곧 훅 (자동으로 자른 첫 문장 조각·인사·순서 말은 빼고)
        hook = next((_oneline(t.get("text"), 40) for t in overlays or [] if _oneline(t.get("text"))), None)
        hook = _overlay_hook(hook, texts)
    if not hook and segs:
        try:
            rec = editor.recommend(name)
            h = editor._hook(rec["shorts"][0]) if rec["shorts"] else ""
            hook = h if h.endswith(("꿀팁", "!") if fmt == "shorts" else "꿀팁") else None  # 첫 문장을 자른 것이면 제목으로 안 씀
            hook = _topic_hook(hook, topics)
        except Exception:
            hook = None
    if fmt == "shorts":
        chs = []
        notes.append("쇼츠는 챕터를 넣지 않아요")
    else:
        chs = chapters(segs, markers, overlays, dur, notes)
    titles = hooks.title_candidates(topics, hook, fmt, flow=flow, dur=dur, n=TITLE_POOL)
    tags_h = hashtags(topics, fmt)
    if tinfo.get("parent"):  # 요점 말이 앞이면 원본 주제 해시태그도 ('#팔로우스루 #인사이드패스')
        ph = "#" + re.sub(r"[^0-9A-Za-z가-힣_]", "", tinfo["parent"])
        if len(ph) > 1 and ph not in tags_h:
            tags_h.insert(min(len(tags_h), tags_h.index("#풋살") + 2 if "#풋살" in tags_h else 1), ph)
    kit = {"version": 1, "made": time.strftime("%Y-%m-%d %H:%M"), "name": name, "source": source, "format": fmt,
           "duration": round(float(dur or 0), 1), "topics": topics, "titles": titles, "title": titles[0],
           "chapters": chs, "hashtags": tags_h, "tags": make_tags(topics, fmt)}
    others, hints = other_kits(name, seq), strategy_hints()
    prev = _find_kit(name, seq)[1] or {}
    kit["guestHint"] = guest_hint(texts)
    kit["seriesFit"] = series_fit(texts, flow, dur, proj, sq, about=Path(name).stem)  # '[N가지 총정리]'·대결 시리즈를 붙여도 되는지 (게스트를 바꿀 때도)
    kit["titlePool"] = list(titles)  # 게스트를 뺐을 때 후보를 다시 채울 바탕 (D-085)
    shape_titles(kit, hints, others, guests=clean_guests(prev.get("guests")), flow=flow, notes=notes)  # 게스트·시리즈·질문형 + 겹침 순서
    kit["hashtags"] = todo_hashtags(kit["hashtags"], hints, fmt)
    quote_avoid = [_kit_quote(k) for k in others if k.get("name") == name]  # 같은 촬영본의 다른 키트가 쓴 첫 줄 인용
    kit["description"] = description(hook_lines(segs, topics, fmt, flow, dur, quote_avoid), chs, kit["hashtags"], kit["title"], topics, notes=notes)
    _apply_guests(kit, kit.get("guests") or [])  # 설명 '▶ 출연' 줄·태그·해시태그 (제목 후보는 위에서)
    kit["thumbnail"] = thumbnail_check(name, fmt)
    kit["prompt"] = claude_prompt(kit, segs)
    kit["promo"] = promo_lines(_transcript(d) or segs)  # 영상에서 말한 레슨 홍보 → 설명 '▶ 레슨 문의' (쇼츠·티저에서는 뺀 말 · E12)
    kit["description"] = add_promo(kit["description"], kit["promo"], notes)
    kit["notes"] = notes
    kit["counts"] = counts(kit)
    kit["limits"] = {"title": TITLE_MAX, "description": DESC_MAX, "tags": TAGS_MAX, "hashtags": HASHTAG_MAX}
    annotate(kit, hints, others)
    if save:
        _store(kit, name, seq, sq)
    return kit


def kit_text(kit):
    """사람이 읽는 .txt (메모장으로 열어 복사해도 되게)."""
    c, th = counts(kit), kit.get("thumbnail") or {}
    src = kit["source"]["label"] + (f" · {kit['source']['export']}" if kit["source"].get("export") else "")
    sch = kit.get("schedule") or {}
    out = ["풋살사관학교 스튜디오 · 올리기 키트", f"영상: {kit['name']} ({src})", f"만든 시각: {kit['made']}",
           *([sch["line"]] if sch.get("line") else []),
           *([f"출연: {COACH} · " + " · ".join(g['name'] for g in kit['guests'])] if kit.get("guests") else []), "",
           f"■ 제목 (지금 {c['title']}/{TITLE_MAX}자)", kit["title"], "", "■ 제목 후보",
           *[f"{i}. {t}" for i, t in enumerate(kit["titles"], 1)], "",
           f"■ 설명 (지금 {c['description']}/{DESC_MAX}자)", kit["description"], "",
           f"■ 태그 (지금 {c['tags']}/{TAGS_MAX}자 · 그대로 복사해서 붙여 넣으세요)", ", ".join(kit["tags"]), ""]
    if kit["chapters"]:
        out += ["■ 챕터 (설명에도 들어 있어요)", chapters_text(kit["chapters"]), ""]
    out += ["■ 썸네일", (f"{th['file']} · {th.get('w')}×{th.get('h')} · {th.get('size_text')} · " if th.get("file") else "")
            + ("문제없어요" if th.get("ok") else " / ".join(th.get("problems") or []))]
    if kit.get("notes"):
        out += ["", "■ 알림", *[f"- {n}" for n in kit["notes"]]]
    return "\n".join(out) + "\n"


def save_kit(kit, stem):
    txt, js = _kit_paths(stem)
    kit["counts"] = counts(kit)
    with _LOCK:
        t = _write_safe(txt, kit_text(kit), "utf-8-sig")  # BOM: 옛 메모장에서도 한글이 안 깨지게
        kit["files"] = {"txt": t.name, "json": js.name}
        keep = {k: v for k, v in kit.items() if k not in _LIVE_KEYS}  # 알림·꼬리표·올릴 날은 열 때마다 새로 봄
        j = _write_safe(js, json.dumps(keep, ensure_ascii=False, indent=1))
        kit["files"]["json"] = j.name
    return kit


def _store(kit, name, seq, sq):
    """편집본 이름으로 저장 (다시 내보내도 같은 파일). 편집본 이름을 바꿨거나 파일이 잠겨 다른 이름으로 저장됐던
    예전 키트 파일은 새 파일을 저장한 뒤 지움 (같은 키트가 두 개로 보이지 않게)."""
    with _LOCK:
        old, prev = _find_kit(name, seq)
        save_kit(kit, _kit_stem(name, seq, sq))
        if old is None:
            return kit
        now = set(kit["files"].values())
        for f in {old.name, (prev.get("files") or {}).get("txt") or ""} - now:
            p = core.OUT / f
            if f and _KIT_FILE.search(f) and p.name.startswith(core.adir(name).name + "_"):
                try:
                    p.unlink(missing_ok=True)
                except OSError:  # 메모장 등에서 잡고 있으면 그대로 둠 (최근 것을 먼저 읽음)
                    pass
    return kit


def load_kit(name, seq=None, live=False):
    """저장해 둔 키트 (썸네일 확인은 새로). 없으면 None.
    키트를 만든 뒤 편집본을 다시 내보냈으면 고친 제목·설명·태그는 그대로 두고 alerts 에 알림.
    live: 제목 꼬리표·겹침·다음 올릴 날(annotate)도 새로 붙임 — 화면이 키트를 열 때만 (다른 키트·편집본·채널 목록을 다 읽어서 무거움)."""
    sq, exp = _target(name, seq)
    _, kit = _find_kit(name, seq)
    if kit is None:
        return None
    if not isinstance(kit.get("source"), dict):  # 손으로 고쳐 깨진 파일
        kit["source"] = {"id": seq or "", "label": "원본 영상 그대로", "export": None}
    alerts, src = [], kit["source"]
    if sq:
        src["label"] = sq.get("name") or "편집본"  # 편집실에서 이름을 바꿨으면 새 이름으로
        was = src.get("export")
        if exp and exp.name != was:
            alerts.append(f"키트를 만든 뒤 편집본을 {'다시 ' if was else ''}내보냈어요 ({exp.name}) · 고친 제목·설명·태그는 그대로 두었어요. "
                          "길이나 챕터 자리가 바뀌었으면 '다시 만들기'를 눌러 주세요")
            kit["notes"] = [n for n in kit.get("notes") or [] if "아직 내보내지 않았어요" not in n]
    kit["alerts"] = alerts
    kit["thumbnail"] = thumbnail_check(name, kit.get("format", "long"))
    kit["counts"] = counts(kit)
    return annotate(kit) if live else kit


def save_edits(name, seq, title=None, desc=None, tags=None):
    """화면에서 고친 제목·설명·태그를 키트 파일에 저장 (규칙을 넘으면 알림만 · 글은 그대로)."""
    with _LOCK:
        sq, _ = _target(name, seq)
        kit = load_kit(name, seq)  # 꼬리표·올릴 날은 다시 계산하지 않음 (1초마다 불림 · 아래에서 지난번 것을 붙임)
        if kit is None:
            raise LookupError("저장된 키트가 없어요 · '올리기 키트 만들기'를 먼저 눌러 주세요")
        if title is not None:  # 쓴 그대로 저장 (< > 같은 건 화면에서 경고)
            kit["title"] = re.sub(r"\s+", " ", str(title)).strip()
        if desc is not None:
            kit["description"] = str(desc).replace("\r\n", "\n").strip()
        if tags is not None:
            kit["tags"] = split_tags(tags) if isinstance(tags, str) else [str(t).strip() for t in tags if str(t).strip()]
        kit["edited"] = time.strftime("%Y-%m-%d %H:%M")
        if not _reuse_live(kit):  # 서버를 다시 켠 뒤 처음 고친 것처럼 지난 값이 없을 때만 새로 (.txt 의 올릴 날 줄도 그대로)
            annotate(kit)
        return _store(kit, name, seq, sq)


# ---------- 제목 겹침 · 전략 맞춤 · 게스트 · 올릴 날 (D-080 ~ D-086) ----------

TITLE_POOL, TITLE_KEEP = 8, 5  # 제목 후보를 넉넉히 만든 뒤 겹치지 않는 것부터 이만큼 (3~5개 규칙 그대로)
GUEST_MAX, GUEST_NAME_MAX, GUEST_BIO_MAX = 3, 20, 40
GUEST_BOOK_MAX = 200
# 열 때마다 새로 계산하는 칸 (파일에 저장하지 않음 · 다른 키트·전략·올린 기록이 바뀌면 달라짐)
_LIVE_KEYS = ("alerts", "titleInfo", "taken", "applied", "schedule", "planSaved", "maxLen", "guestBook")


def _all_kits():
    """완성본 폴더의 키트 전부 (최근 것부터 · 같은 영상·편집본은 가장 최근 것 하나 · 400개까지)."""
    try:
        files = [p for p in core.OUT.iterdir() if p.suffix == ".json" and _KIT_FILE.search(p.name)]
    except OSError:
        return []
    out, seen = [], set()
    for p in sorted(files, key=_mtime, reverse=True)[:400]:
        k = _read_kit(p)
        if not k or not isinstance(k.get("name"), str) or _owner(k) in seen:
            continue
        seen.add(_owner(k))
        out.append(k)
    return out


def other_kits(name, seq=None):
    """이 영상·편집본 말고 다른 키트들 (제목 겹침 · 같은 촬영본 인용 겹침 검사)."""
    me = (name, seq or "")
    return [k for k in _all_kits() if _owner(k) != me]


def _kit_label(k):
    """'퍼스트 터치 레슨 · 쇼츠 2' 처럼 짧게 (날짜·번호 머리말은 뺌)."""
    src = k.get("source") if isinstance(k.get("source"), dict) else {}
    nm = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", str(k.get("name") or ""))
    nm = re.sub(r"^\d{8}_[A-Za-z0-9]+_", "", nm)
    lab = re.sub(r"\s*·.*$", "", str(src.get("label") or "원본 영상"))
    return _oneline(f"{nm} · {lab}", 40)


def _kit_quote(k):
    """키트 설명 첫 줄의 인용 (“…”) · 없으면 ''."""
    first = (k.get("description") or "").split("\n", 1)[0].strip()
    return first[1:-1] if len(first) > 2 and first[0] == "“" and first[-1] == "”" else ""


def taken_titles(others):
    """겹치면 안 되는 제목 {"keys": {열쇠: 어디}, "skel": {틀 열쇠: 어디}, "list": [...]}: 다른 키트에서 고른 제목 ·
    이미 올린 우리 영상(채널 목록 · 유튜브에 바로 올린 기록). 열쇠는 hooks.norm_title (괄호 속 '(최경진 감독)'·[시리즈]·기호는 안 봄)."""
    keys, skel, lst, near = {}, {}, [], []

    def put(t, where, topics=None, uploaded=False):
        k = hooks.norm_title(t)
        if len(k) >= 2 and k not in keys:
            keys[k] = where
            lst.append({"k": k, "from": where})
            if uploaded and len(k) >= NEAR_MIN:
                near.append((k, where))
        sk = hooks.title_skeleton(t, topics) if topics else None
        if sk and sk not in skel:
            skel[sk] = where
    for k in others:
        if k.get("title"):
            put(str(k["title"]), f"'{_kit_label(k)}' 키트", k.get("topics") or [])
    try:
        rows = hooks.own_rows()
    except Exception:  # 목록을 못 읽어도 키트는 만듦
        rows = {}
    for kind in ("videos", "shorts"):
        for r in (rows.get(kind) or [])[:300]:
            put(str(r.get("title") or ""), "이미 올린 우리 영상", uploaded=True)
    try:
        import youtube_upload  # 지연 import: youtube_upload 가 upload 를 부름
        for h in youtube_upload.history(youtube_upload.HISTORY_MAX)["items"]:
            put(str(h.get("title") or ""), "유튜브에 올린 영상", uploaded=True)
    except Exception:
        pass
    return {"keys": keys, "skel": skel, "list": lst, "near": near}


NEAR_MIN, NEAR_RATIO = 6, 0.8  # 이미 올린 제목과 '거의 같음': 열쇠 6글자 넘고 같은 주제어 · 한쪽이 다른 쪽을 품거나 80% 넘게 같음


def _near_title(key, topics, taken):
    """이미 올린 우리 제목을 한두 낱말만 빼거나 더해 다시 쓴 후보인지 → 그 제목이 어디 것인지 · 아니면 None (D-082 보강).
    우리 채널 틀(D-080)은 우리 인기 제목에서 오므로, 같은 기술의 새 영상이면 옛 제목과 거의 같아짐
    ('🔥풋살기술🔥 "영재 플랩" 속성강의' ↔ 올린 '🔥600만뷰 풋살기술🔥 "영재 플랩" 속성강의')."""
    if len(key) < NEAR_MIN:
        return None
    tps = [c for c in (hooks._compact(t) for t in topics or []) if len(c) >= 2 and c in key]
    if not tps:
        return None
    for k2, where in taken.get("near") or []:
        if k2 == key or not any(c in k2 for c in tps):
            continue
        if key in k2 or k2 in key or difflib.SequenceMatcher(None, key, k2).ratio() >= NEAR_RATIO:
            return where
    return None


def strategy_hints():
    """채널 전략에서 키트에 맞출 것 (D-083) — 안 끝난 할 일 중 제목·올리기에 쓰는 것('쇼츠 운영'의 해시태그·제목 할 일도):
    질문형 먼저 · 제목 N자 안쪽 · 해시태그 · 저장한 전략의 시리즈 이름. 전략을 못 읽으면 빈 것 (키트는 그대로 만듦)."""
    out = {"todos": [], "question": None, "maxLen": None, "hashtags": [], "series": [], "saved": False}
    try:
        import strategy  # 지연 import: strategy → hooks → … (앱 시작을 가볍게)
        st = strategy.load_state()
        todos = [t for t in st.get("todos") or [] if not t.get("done") and {"title", "upload"} & set(strategy.todo_uses(t))]
    except Exception:
        return out
    for t in todos:
        text = re.sub(r"\s+", " ", str(t.get("text") or "")).strip()
        hit = False
        if "질문형" in text:
            out["question"], hit = text, True
        m = re.search(r"(\d{1,3})\s*자\s*(?:안쪽|이내|이하|까지)", text)
        if m and "제목" in text and 5 <= int(m[1]) <= TITLE_MAX:
            out["maxLen"] = min(out["maxLen"] or TITLE_MAX, int(m[1]))
            hit = True
        if "해시태그" in text:
            for h in re.findall(r"#[0-9A-Za-z가-힣_]+", text):
                out["hashtags"].append({"tag": h, "shorts": "쇼츠" in text, "todo": text})
                hit = True
        if hit:
            out["todos"].append({"id": t.get("id"), "text": text})
    strat = st.get("strategy")
    if isinstance(strat, dict):
        out["saved"] = True
        out["series"] = [s for s in strat.get("series") or [] if isinstance(s, dict) and str(s.get("name") or "").strip()]
    return out


_SERIES_EP = re.compile(r"(?<![A-Za-z])N(?![A-Za-z가])|~|EP\s*(?:\]|$)|EP\d+\s*~", re.I)  # 회차·도전자 번호 자리 (코너·시리즈 관리에서 채울 몫)


def _series_kind(text):
    return "shorts" if re.search(r"쇼츠|shorts|1분|숏폼", text, re.I) else "long" if re.search(r"롱폼|총정리|모음|몰아|강좌", text) else None


SUM_MIN_SEC = 240  # '[N가지 총정리]'는 이만큼 넘는 롱폼(또는 여러 영상을 모은 편집본)에만 — 짧은 레슨 하나는 총정리가 아님
_MATCHUP = re.compile(r"대결|(?<![A-Za-z])vs(?![A-Za-z])|1대1|일대일|승부|맞대결|이겼|졌어|졌네", re.I)


def series_fit(texts, flow, dur, proj=None, sq=None, about=""):
    """이 영상에 붙여도 되는 시리즈 갈래 (키트에 남김 · 게스트를 바꿔 다시 맞출 때도 씀 · D-083 보강):
    sum — '[N가지 총정리]': 주제가 모두 풋살 기술 이름(레슨 낱말·'기본 자세' X)이고 저마다 두 번 넘게 나오며, 4분 넘는 롱폼이거나
    여러 영상을 모은 편집본일 때만 ('수비가 따라와요'의 '수비'처럼 스친 말로 총정리 X) · vs — 대결 시리즈: 대사나 영상 이름(about)에
    대결·1대1·이겼다 같은 말."""
    terms = tuple(hooks.topic_terms())
    known = {hooks._compact(t) for t in terms}
    cnt = Counter(term for t in texts or [] for _a, _b, term in hooks._find_terms(t, terms))
    media = {it.get("media") for it in (sq or {}).get("items") or [] if str(it.get("track") or "").startswith("V") and it.get("media")}
    flow = [t for t in flow or [] if t][:3]
    big = (_num(dur) or 0) >= SUM_MIN_SEC or len(media) >= 2
    sum_ok = big and len(flow) >= 2 and all(hooks._compact(t) in known and cnt[t] >= 2 for t in flow)
    return {"sum": bool(sum_ok), "vs": bool(_MATCHUP.search(" ".join(list(texts or []) + [about or ""])))}


def series_titles(hints, topics, fmt, dur, flow=None, guests=(), fit=None):
    """저장한 전략의 시리즈 이름을 붙인 제목 (2개까지 · D-083): 쇼츠·1분 시리즈는 쇼츠(1분 이하)에, 총정리·롱폼 시리즈는 롱폼에,
    대결·vs 시리즈는 게스트가 있고 대사가 대결일 때만('[국대 vs 국대] 이한울과 1대1 드리블') · '[N가지 총정리]'는 fit["sum"]일 때 주제어 수로 ·
    회차 번호 자리(N호·EP01~)가 있는 이름은 아직 안 씀. fit: series_fit 결과 (없으면 따지지 않음)."""
    out = []
    fit = fit if isinstance(fit, dict) else {"sum": True, "vs": True}
    flow = [t for t in (flow or topics or []) if t]
    for s in hints.get("series") or []:
        name = re.sub(r"\s+", " ", str(s.get("name") or "")).strip()
        about, desc = f"{name} {s.get('desc') or ''}", str(s.get("desc") or "")
        kind = _series_kind(name) or ("long" if "롱폼" in desc else None) or _series_kind(desc)  # 이름 먼저 ('쇼츠로 낸 기술을 모은 롱폼' = 롱폼)
        if not name or (kind and kind != fmt) or ("1분" in name and not hooks.one_minute(dur)):
            continue
        vs = bool(re.search(r"(?<![A-Za-z])vs(?![A-Za-z])|대결", about, re.I))
        if vs and not (guests and fit.get("vs")):
            continue
        m = re.search(r"N(\s*가지)", name)
        topic = topics[0] if topics else "풋살"
        if m:
            if len(flow) < 2 or not fit.get("sum"):
                continue
            name = name[:m.start()] + str(len(flow[:3])) + name[m.start() + 1:]
            body = "·".join(flow[:3])
        elif vs:  # 대결은 누구와 붙었는지가 내용 ('… 꿀팁' X)
            rival = guests[0]["name"]
            body = f"{rival}{hooks.josa(rival, '와')} 1대1" + (f" {topic}" if topic not in ("풋살", "1대1") else "")
        else:  # '[1분 풋살 기술] 퍼스트 터치' · 이름에 '기술·꿀팁·강좌'가 없으면 '… 꿀팁'
            body = topic if re.search(r"기술|꿀팁|팁|강좌|레슨|클래스", name) else f"{topic} 꿀팁"
        if _SERIES_EP.search(name):
            continue
        t = hooks.tidy_title(f"{name} {body}" if name[:1] in "[【(" else f"{body} | {name}")
        if t and len(t) <= TITLE_MAX and t not in out:
            out.append(t)
        if len(out) >= 2:
            break
    return out


# ---- 게스트 ----

def clean_guests(guests):
    """화면에서 받은 출연자·게스트 → [{name, bio}] (3명까지 · 이름 20자 · 이력 40자 · < > 빼기 · 감독님·같은 이름은 한 번)."""
    out, seen = [], set()
    for g in guests if isinstance(guests, list) else []:
        if isinstance(g, str):
            g = {"name": g}
        if not isinstance(g, dict):
            continue
        name = _oneline(g.get("name"), GUEST_NAME_MAX).strip(" ,·")
        bio = _oneline(g.get("bio"), GUEST_BIO_MAX).strip(" ,·")
        key = guest_key(name)
        if len(key) < 2 or not re.search(r"[가-힣A-Za-z]", key) or key in seen or key in COACH.replace(" ", ""):
            continue
        seen.add(key)
        out.append({"name": name, "bio": bio})
        if len(out) >= GUEST_MAX:
            break
    return out


_GUEST_TITLE = re.compile(r"\s*(?:선수|코치|감독|님|쌤|선생님)$")


def guest_key(name):
    """같은 사람인지 볼 열쇠: 띄어쓰기와 끝의 부름말을 뺀 이름 ('이한울 선수' = '이한울') — 적어 둔 게스트·몇 번째·해시태그."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    return _GUEST_TITLE.sub("", n).replace(" ", "") or n.replace(" ", "")


def _guest_path():
    return core.WORK / "guests.json"


def guest_book():
    """적어 둔 게스트 [{name, bio, n(나온 키트 수), at}] (최근 것부터) — 다음 영상에서 골라 쓰고 2탄·3탄을 셈."""
    try:
        d = json.loads(_guest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = [x for x in (d.get("items") if isinstance(d, dict) else None) or [] if isinstance(x, dict) and x.get("name")]
    return [{"name": str(x["name"]), "bio": str(x.get("bio") or ""), "n": len(x.get("uses") or []), "at": x.get("at") or 0, "uses": x.get("uses") or []}
            for x in sorted(items, key=lambda x: -(x.get("at") or 0))]


def remember_guests(guests, video, seq=None):
    """키트에 넣은 게스트를 작업 폴더 guests.json 에 남김 (이름·이력 · 어느 영상·편집본에 나왔는지) — 실패해도 키트는 그대로.
    이 키트에서 뺀 게스트는 이 영상·편집본에 나온 기록을 지움 (잘못 넣었다 뺀 게스트가 다음 영상에서 '2탄'이 되지 않게 · 이름은 남김)."""
    use = [video, seq or ""]
    keep = {guest_key(g["name"]) for g in guests or []}
    if not guests and not any(use in (x.get("uses") or []) for x in guest_book()):
        return
    try:
        with _LOCK:
            book = {}
            for x in reversed(guest_book()):  # 오래된 것부터 · 예전에 '이한울 선수'로 따로 적힌 것도 한 사람으로 합침
                k = guest_key(x["name"])
                if k in book:
                    x = dict(x, uses=[u for u in book[k]["uses"] if u not in x["uses"]] + x["uses"])
                book[k] = x
            for k, x in book.items():
                if k not in keep and use in (x.get("uses") or []):
                    x["uses"] = [u for u in x["uses"] if u != use]
            now = time.time()
            for g in guests or []:
                k = guest_key(g["name"])
                x = book.get(k) or {"name": g["name"], "bio": "", "uses": []}
                x["bio"] = g.get("bio") or x.get("bio") or ""
                if use not in x["uses"]:
                    x["uses"] = (x["uses"] + [use])[-50:]
                x["at"] = now
                x.pop("n", None)
                book[k] = x
            items = sorted(book.values(), key=lambda x: -(x.get("at") or 0))[:GUEST_BOOK_MAX]
            _write_safe(_guest_path(), json.dumps({"v": 1, "items": [{k: v for k, v in x.items() if k != "n"} for x in items]}, ensure_ascii=False, indent=1))
    except (OSError, RuntimeError):
        pass


def guest_episode(name, video, seq=None):
    """이 게스트가 몇 번째 촬영본에 나오는지 (이 영상 말고 다른 영상 키트에 나온 영상 수 + 1) —
    같은 촬영본의 롱폼·원본·쇼츠 여러 개는 한 회 ('2탄'은 다른 날 다시 찍은 영상부터 · seq 는 예전 호출과 맞추려고 받기만 함)."""
    k = guest_key(name)
    vids = set()
    for g in guest_book():
        if guest_key(g["name"]) == k:
            vids |= {u[0] for u in g.get("uses") or [] if isinstance(u, list) and len(u) == 2 and u[0] != video}
    return 1 + len(vids)


def guest_titles(guests, topics, dur=None, video="", seq=None, max_len=None):
    """게스트 이름이 든 제목 2개 (검색·추천에 가장 강한 낱말 · D-085): '{이력} {이름}의 {주제} 꿀팁' · '{주제} 꿀팁 (feat. {이름})' ·
    전에 나온 게스트면 '{이름}과 함께하는 {주제} 2탄' 먼저 · max_len(전략 할 일 'N자 안쪽')이 있으면 그 안에 드는 짧은 꼴 먼저."""
    if not guests:
        return []
    g, topic = guests[0], (topics[0] if topics else "풋살")
    name, head = g["name"], re.split(r"[·,/|]", g.get("bio") or "")[0].strip()[:20]
    tip = "1분 꿀팁" if hooks.one_minute(dur) else "꿀팁"
    names = "·".join(x["name"] for x in guests)
    out = [f"{head} {name}의 {topic} {tip}" if head else f"{name}의 {topic} {tip}", f"{topic} {tip} (feat. {names})",
           f"{name}의 {topic} {tip}", f"{topic} (feat. {names})"]
    n = guest_episode(name, video, seq)
    if n >= 2:
        out.insert(0, f"{name}{hooks.josa(name, '와')} 함께하는 {topic} {n}탄")
    out = list(dict.fromkeys(hooks.tidy_title(t) for t in out if len(t) <= TITLE_MAX))
    if max_len:
        out.sort(key=lambda t: len(t) > max_len)  # 같은 쪽 안에서는 원래 순서
    return out[:2]


_GUEST_CALL = re.compile(r"([가-힣]{2,4})\s?(선수|코치|감독|님|쌤|선생님)")
_SURNAMES = set("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용")


def guest_hint(texts):
    """대사에서 부른 사람 이름 ('이한울 선수' 2번 이상 · 감독님 빼고) — 화면이 '게스트로 넣을까요?'로 보여 줌 (자동으로 넣지는 않음)."""
    cnt = {}
    for t in texts or []:
        for m in _GUEST_CALL.finditer(t):
            nm = m[1]
            if len(nm) == 3 and nm[0] in _SURNAMES and nm != "최경진" and nm not in hooks.STOP:
                cnt[nm] = cnt.get(nm, 0) + 1
    return [n for n, c in sorted(cnt.items(), key=lambda x: -x[1]) if c >= 2][:3]


def _drop_line(text, line):
    lines = text.split("\n")
    if line in lines:
        lines.remove(line)
    return "\n".join(lines)


def _insert_cast(text, lines):
    """설명의 '▶ 출연' 묶음에 게스트 줄을 넣음: 감독님 줄 바로 뒤 → '▶ 출연' 줄 뒤 → 없으면 해시태그 줄 앞에 묶음째."""
    if not lines:
        return text
    rows = text.split("\n")
    at = next((i + 1 for i, r in enumerate(rows) if COACH in r and "(" in r), None)
    if at is None:
        at = next((i + 1 for i, r in enumerate(rows) if r.strip().startswith("▶ 출연")), None)
    if at is None:
        tail = next((i for i in range(len(rows) - 1, -1, -1) if rows[i].strip().startswith("#")), len(rows))
        block = ["▶ 출연", f"{COACH} (풋살사관학교)", *lines, ""]
        return "\n".join(rows[:tail] + block + rows[tail:]).strip()
    return "\n".join(rows[:at] + lines + rows[at:])


def _apply_guests(kit, guests):
    """게스트를 키트의 설명 '▶ 출연' 줄 · 태그(이름·이력) · 해시태그(#이름)에 반영 (D-085 · 제목 후보는 shape_titles).
    지난번에 자동으로 넣은 것(guestAuto)은 먼저 빼서, 게스트를 바꾸거나 지워도 남지 않음 · 손으로 고친 다른 글은 그대로."""
    auto = dict(kit.get("guestAuto") or {})
    desc = kit.get("description") or ""
    old_line = " ".join(kit.get("hashtags") or [])
    for ln in auto.get("lines") or []:
        desc = _drop_line(desc, ln)
    tags = [t for t in kit.get("tags") or [] if t not in (auto.get("tags") or [])]
    hts = [h for h in kit.get("hashtags") or [] if h not in (auto.get("hashtags") or [])]
    lines = [f"{g['name']} ({g['bio']})" if g.get("bio") else g["name"] for g in guests]
    low = {t.lower() for t in tags}
    gtags = []
    for g in guests:  # 이름(부름말은 뺀 것도) · 이력은 '·,/|'로 나눠 따로 ('강원FS · 현 국가대표' → '강원FS', '현 국가대표')
        bits = [g["name"], guest_key(g["name"]) if guest_key(g["name"]) != g["name"].replace(" ", "") else ""]
        for t in bits + re.split(r"[·,/|]", g.get("bio") or ""):
            t = re.sub(r"\s+", " ", re.sub(r"[<>,#\"]", "", t)).strip()
            if len(t) >= 2 and len(t) <= 30 and t.lower() not in low and t not in gtags:
                gtags.append(t)
    lead = [t for t in tags if t in ("shorts", "쇼츠", "풋살 쇼츠")]
    cand = lead + gtags + [t for t in tags if t not in lead]
    new_tags = []
    for t in cand:
        if tags_len(new_tags + [t]) <= TAGS_MAX:
            new_tags.append(t)
    gtags = [t for t in gtags if t in new_tags]
    ghts = [h for h in ("#" + re.sub(r"[^0-9A-Za-z가-힣_]", "", guest_key(g["name"])) for g in guests) if len(h) > 2 and h not in hts]
    at = min(len(hts), (2 if hts[:1] == ["#shorts"] else 1) + 1)  # '#풋살 #주제' 뒤에
    hts = hts[:at] + ghts + hts[at:]
    new_line = " ".join(hts)
    if old_line and old_line in desc:
        i = desc.rfind(old_line)
        desc = desc[:i] + new_line + desc[i + len(old_line):]
    desc = _insert_cast(desc, lines)
    kit.update(description=fit_description(desc), tags=new_tags, hashtags=hts, guests=list(guests),
               guestAuto=dict(auto, lines=lines, tags=gtags, hashtags=ghts))
    return kit


# ---- 제목 후보 모양 잡기 · 꼬리표 ----

def _marks(t, kit, hints, taken, series=()):
    """제목 하나의 꼬리표 {dup, same, long, question, guest, series} (D-082 · D-083)."""
    m = {}
    key = hooks.norm_title(t)
    near = None if key in taken["keys"] else _near_title(key, kit.get("topics") or [], taken)
    if key in taken["keys"]:
        m["dup"] = taken["keys"][key]
    elif near:
        m["near"] = near
    elif t not in series:  # 시리즈 이름은 일부러 되풀이하는 틀 (같은 틀로 보지 않음)
        sk = hooks.title_skeleton(t, kit.get("topics") or [])
        if sk and sk in taken["skel"]:
            m["same"] = taken["skel"][sk]
    lim = hints.get("maxLen")
    if lim and len(t) > lim:
        m["long"] = lim
    if hooks.is_question(t):
        m["question"] = True
    if t in ((kit.get("guestAuto") or {}).get("titles") or []):
        m["guest"] = True
    if t in series:
        m["series"] = True
    return m


def shape_titles(kit, hints, others, guests=(), flow=None, notes=None, keep_title=False):
    """제목 후보 순서 (D-082 · D-083 · D-085): 게스트 제목 → 저장한 시리즈 제목 → 나머지, 켜진 전략 할 일(질문형 먼저·N자 넘으면 뒤로)과
    다른 키트·이미 올린 우리 제목과의 겹침(같은 글자는 맨 뒤 · 주제어만 바꾼 같은 틀은 한 칸 뒤)으로 다시 세움. 5개까지 남김.
    keep_title: 손으로 고른 제목은 그대로."""
    topics, fmt, dur = kit.get("topics") or [], kit.get("format"), kit.get("duration")
    src = kit.get("source") or {}
    base = [t for t in kit.get("titles") or [] if t]
    gt = guest_titles(list(guests), topics, dur, kit.get("name"), src.get("id"), hints.get("maxLen"))
    st = series_titles(hints, topics, fmt, dur, flow, guests, kit.get("seriesFit"))
    kit["guestAuto"] = dict(kit.get("guestAuto") or {}, titles=gt)
    kit["guests"] = list(guests)
    taken = taken_titles(others)
    qt = []
    if hints.get("question"):  # 질문형 틀 4개 중 다른 키트·올린 제목과 겹치지 않는 것으로 2개까지 (기본 틀에 이미 있는 질문형도 셈)
        fresh = lambda t: not any(_marks(t, kit, hints, taken).get(x) for x in ("dup", "near", "same"))  # noqa: E731
        have = sum(1 for t in gt + st + base if hooks.is_question(t) and fresh(t))
        cand = [t for t in hooks.question_titles(topics, len(hooks.QUESTION_TPL)) if t not in gt + st + base]
        qt = ([t for t in cand if fresh(t)] or cand)[:max(0, 2 - have)]
    pool = list(dict.fromkeys(gt + st + base + qt))
    marks = [_marks(t, kit, hints, taken, st) for t in pool]

    def key(i):
        m = marks[i]
        pen = (1 if m.get("same") else 0) + (1 if m.get("long") else 0) + (1 if hints.get("question") and not m.get("question") else 0)
        return (1 if m.get("dup") or m.get("near") else 0, pen - (2 if m.get("guest") else 1 if m.get("series") else 0), i)
    order = sorted(range(len(pool)), key=key)
    if hints.get("question") and not any(marks[i].get("question") for i in order[:TITLE_KEEP]):  # 질문형이 하나는 보이게
        q = next((i for i in order[TITLE_KEEP:] if marks[i].get("question") and not marks[i].get("dup") and not marks[i].get("near")), None)
        if q is not None:
            order.remove(q)
            order.insert(1, q)
    kit["titles"] = [pool[i] for i in order][:TITLE_KEEP]
    kit["guestAuto"]["titles"] = [t for t in gt if t in kit["titles"]]
    if not (keep_title and kit.get("title")):
        kit["title"] = kit["titles"][0]
    kept = [pool[i] for i in order if (marks[i].get("dup") or marks[i].get("near")) and pool[i] in kit["titles"]]  # 겹치지 않는 후보가 모자라 남은 것
    if notes is not None and kept:
        notes.append(f"제목 후보 {len(kept)}개가 다른 키트·이미 올린 우리 영상 제목과 같아요 · '겹쳐요' 표시가 붙은 후보는 고쳐 써 주세요")
    return kit


def todo_hashtags(hashtags, hints, fmt):
    """해시태그 할 일('쇼츠마다 같은 해시태그(예: #풋살사관학교)')의 해시태그를 넣은 목록 (쇼츠 할 일이면 쇼츠만 · D-083)."""
    hts = list(hashtags or [])
    for h in hints.get("hashtags") or []:
        if (fmt == "shorts" or not h["shorts"]) and h["tag"] not in hts and len(hts) < HASHTAG_MAX:
            hts.insert(1 if hts[:1] == ["#shorts"] else 0, h["tag"])
    return hts


def _series_names(hints):
    return [re.sub(r"\s+", " ", str(s.get("name") or "")).strip() for s in hints.get("series") or [] if str(s.get("name") or "").strip()]


def _is_series(t, name):
    """제목에 저장한 시리즈 이름이 붙어 있는지 ('[N가지 총정리]'의 N 은 숫자 자리 · '[3가지 총정리] …'도 그 시리즈)."""
    rx = re.escape(name)
    if re.search(r"N\s*가지", name):
        rx = re.sub(r"N(?=(?:\\\s|\s)*가지)", r"\\d+", rx)
    return bool(len(name) >= 3 and re.match(rx, t)) or t.endswith(f"| {name}")


def _applied(kit, hints, info=None):
    """전략에서 키트에 맞춘 것 (화면 '전략 할 일' 상자 아래 한 줄씩) · info: 제목 꼬리표 (겹친 질문형은 넣은 것으로 안 셈)."""
    out = []
    if hints.get("question"):
        tags = [(info[i] if info and i < len(info) else {}).get("tags") or [] for i in range(len(kit.get("titles") or []))]
        qs = [tg for t, tg in zip(kit.get("titles") or [], tags) if hooks.is_question(t) and "겹쳐요" not in tg]
        n = sum(1 for tg in qs if "같은 틀" not in tg)
        out.append(f"할 일 '짧은 질문형 제목' → 질문형 후보 {n}개를 앞쪽에 넣었어요" if n
                   else "할 일 '짧은 질문형 제목' → 질문형 틀을 이번 주 다른 키트가 이미 써서 '같은 틀'로 표시했어요" if qs
                   else "할 일 '짧은 질문형 제목' → 질문형 후보가 다른 키트·올린 영상 제목과 겹쳐서 못 넣었어요")
    if hints.get("maxLen"):
        n = sum(1 for t in kit.get("titles") or [] if len(t) > hints["maxLen"])
        out.append(f"할 일 '제목 {hints['maxLen']}자 안쪽' → {n}개가 길어서 '길어요'를 붙이고 뒤로 보냈어요" if n else f"할 일 '제목 {hints['maxLen']}자 안쪽' → 후보가 모두 {hints['maxLen']}자 안이에요")
    for h in hints.get("hashtags") or []:
        if h["tag"] in (kit.get("hashtags") or []):
            out.append(f"해시태그 할 일 → {h['tag']}{hooks.josa(h['tag'], '을')} 넣었어요")
    used = [n for n in _series_names(hints) if any(_is_series(t, n) for t in kit.get("titles") or [])]
    if used:
        out.append(f"저장한 시리즈 {', '.join(used[:2])} 이름을 붙인 후보를 넣었어요")
    return out


def annotate(kit, hints=None, others=None):
    """열 때마다 새로 붙이는 칸 (저장 안 함): 제목 꼬리표(titleInfo) · 겹치면 안 되는 제목(taken · 화면이 고쳐 쓴 제목도 바로 검사) ·
    전략에서 맞춘 것(applied) · 다음 올릴 날(schedule · D-086) · 적어 둔 게스트(guestBook). 실패해도 키트는 그대로."""
    try:
        src = kit.get("source") or {}
        hints = strategy_hints() if hints is None else hints
        others = other_kits(kit["name"], src.get("id")) if others is None else others
        taken = taken_titles(others)
        names = _series_names(hints)
        series = {t for t in kit.get("titles") or [] if any(_is_series(t, n) for n in names)}
        info = []
        for t in kit.get("titles") or []:
            m = _marks(t, kit, hints, taken, series)
            tags, why = [], []
            if m.get("guest"):
                tags.append("게스트")
            if m.get("series"):
                tags.append("시리즈")
            if m.get("question") and hints.get("question"):
                tags.append("질문형")
            if m.get("long"):
                tags.append("길어요")
                why.append(f"전략 할 일: 제목 {m['long']}자 안쪽")
            if m.get("dup"):
                tags.append("겹쳐요")
                why.append(f"{m['dup']} 제목과 같아요")
            elif m.get("near"):
                tags.append("겹쳐요")
                why.append(f"{m['near']} 제목과 거의 같아요")
            elif m.get("same"):
                tags.append("같은 틀")
                why.append(f"{m['same']} 제목과 주제어만 달라요")
            info.append({"tags": tags, "why": " · ".join(why)})
        kit["titleInfo"] = info
        kit["taken"] = taken["list"][:600]
        kit["maxLen"] = hints.get("maxLen")
        kit["applied"] = _applied(kit, hints, info)
        kit["guestBook"] = [{"name": g["name"], "bio": g["bio"], "n": g["n"]} for g in guest_book()[:30]]
        kit["schedule"] = kit_schedule(kit["name"], src.get("id"), kit.get("format"), kit.get("title"))
        kit["planSaved"] = bool(hints.get("saved"))
        _remember_live(kit)
    except Exception as e:  # 꼬리표·올릴 날을 못 붙여도 키트는 보여 줌
        studio_log(f"올리기 키트 꼬리표를 붙이지 못했어요 · {e}")
    return kit


_LIVE_CACHE = {}  # (작업 폴더, 영상, 편집본) → 마지막으로 붙인 꼬리표·올릴 날 — 고친 글을 1초마다 저장할 때 다시 계산하지 않게
_LIVE_CACHE_MAX = 64


def _live_key(kit):
    src = kit.get("source") if isinstance(kit.get("source"), dict) else {}
    return str(core.WORK), kit.get("name"), src.get("id") or ""


def _remember_live(kit):
    if len(_LIVE_CACHE) >= _LIVE_CACHE_MAX:
        _LIVE_CACHE.pop(next(iter(_LIVE_CACHE)))
    _LIVE_CACHE[_live_key(kit)] = {"titles": list(kit.get("titles") or []),
                                   "live": {k: copy.deepcopy(kit[k]) for k in _LIVE_KEYS if k in kit and k != "alerts"}}


def _reuse_live(kit):
    """지난번에 붙인 꼬리표·올릴 날을 다시 붙임 (제목 후보가 그때와 같을 때만) → 붙였으면 True."""
    hit = _LIVE_CACHE.get(_live_key(kit))
    if not hit or hit["titles"] != list(kit.get("titles") or []):
        return False
    kit.update(copy.deepcopy(hit["live"]))
    return True


def studio_log(msg):
    try:
        import studiolog
        studiolog.write(msg)
    except Exception:
        pass


def set_guests(name, seq, guests):
    """화면의 '출연자·게스트' 칸 → 키트의 제목 후보·태그·해시태그·설명 출연 줄을 바꿔 저장 (손으로 고른 제목·고친 다른 글은 그대로)."""
    with _LOCK:
        sq, _ = _target(name, seq)
        kit = load_kit(name, seq, live=False)
        if kit is None:
            raise LookupError("저장된 키트가 없어요 · '올리기 키트 만들기'를 먼저 눌러 주세요")
        guests = clean_guests(guests)
        old_auto = (kit.get("guestAuto") or {}).get("titles") or []
        # 첫 후보도, 예전 게스트 자동 제목도 아니면 손으로 고르거나 쓴 제목 → 그대로 둠
        picked = kit.get("title") not in (kit.get("titles") or [])[:1] and kit.get("title") not in old_auto
        hints, others = strategy_hints(), other_kits(name, seq)
        kit["titles"] = [t for t in kit.get("titlePool") or kit.get("titles") or [] if t not in old_auto]  # 게스트를 빼면 후보를 다시 채움
        if not picked and kit["titles"]:
            kit["title"] = kit["titles"][0]
        _apply_guests(kit, guests)
        shape_titles(kit, hints, others, guests, keep_title=picked)
        remember_guests(guests, name, seq)
        _store(kit, name, seq, sq)
        return annotate(kit, hints, others)


# ---- 올릴 날 · 만들어 둔 편집본 (D-086) ----

STOCK_DAYS = 60  # 이보다 오래전에 내보낸 편집본은 '올릴 준비'로 세지 않음 (다른 제목으로 손으로 올렸을 수 있어서)


_STOCK_CACHE = {}  # 파일 시각 열쇠 → stock 결과 (키트를 고칠 때마다 1초에 한 번 불려도 편집본을 다시 읽지 않게)


def _stat_sig(p):
    try:
        st = p.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _stock_key(now):
    """stock 결과가 바뀔 수 있는 파일들의 시각 (보관함 영상마다 편집본 파일 · 완성본 폴더의 영상·키트 · 올린 기록 · 우리 채널 목록)."""
    try:
        vids = sorted(v.name for v in core.VIDEOS.iterdir() if core.is_video_file(v.name))
        outs = sorted((p.name, _stat_sig(p)) for p in core.OUT.iterdir() if p.suffix.lower() == ".mp4" or _KIT_FILE.search(p.name))
    except OSError:
        return None
    import strategy
    return (tuple((n, _stat_sig(editor._ppath(n))) for n in vids), tuple(outs), _stat_sig(core.WORK / "youtube" / "history.json"),
            _stat_sig(hooks.cache_path()), _stat_sig(strategy._chan_path(strategy.OWN)), int(now // 3600))


def stock(now=None):
    """만들어 둔 편집본 (D-086) → {"ready": [내보냈고 아직 안 올린 것 · 내보낸 순], "drafts": {"S": n, "L": n}(아직 안 내보낸 편집본)}.
    올렸는지는 유튜브에 바로 올린 기록(같은 영상·편집본·파일 · 공개·일부 공개·공개 예약만) 또는 우리 채널 제목 ↔ 키트 제목(같은 글자)로 봄."""
    now = now or time.time()
    key = _stock_key(now)
    hit = _STOCK_CACHE.get(str(core.WORK))
    if key is not None and hit and hit[0] == key:
        return copy.deepcopy(hit[1])
    try:
        vids = [v.name for v in core.VIDEOS.iterdir() if core.is_video_file(v.name)]
    except OSError:
        vids = []
    try:
        import strategy
        import youtube_upload
        hist = [h for h in youtube_upload.history(youtube_upload.HISTORY_MAX)["items"] if strategy.published(h)]  # 잠긴·그냥 비공개는 아직 안 올림
    except Exception:
        hist = []
    try:
        rows = hooks.own_rows()
        own = {hooks.norm_title(r.get("title")) for kind in ("videos", "shorts") for r in rows.get(kind) or []}
    except Exception:
        own = set()
    kits = {_owner(k): k for k in _all_kits()}
    ready, drafts = [], {"S": 0, "L": 0}
    for name in vids:
        for sq in (read_project(name) or {}).get("sequences") or []:
            kind = "S" if sq.get("format") == "shorts" else "L"
            exp = latest_export(name, sq)
            if not exp:
                drafts[kind] += 1
                continue
            at = _mtime(exp)
            if now - at > STOCK_DAYS * 86400:
                continue
            seq = sq.get("id") or ""
            kit = kits.get((name, seq)) or {}
            if any(h.get("name") == name and (h.get("seq") or "") == seq and (h.get("file") == exp.name or (h.get("at") or 0) >= at) for h in hist):
                continue
            if kit.get("title") and hooks.norm_title(kit["title"]) in own:
                continue
            ready.append({"name": name, "seq": seq, "label": sq.get("name") or "편집본", "kind": kind, "export": exp.name, "at": at,
                          "title": kit.get("title") or ""})
    ready.sort(key=lambda x: x["at"])
    out = {"ready": ready, "drafts": drafts}
    if key is not None:
        _STOCK_CACHE.clear()
        _STOCK_CACHE[str(core.WORK)] = (key, copy.deepcopy(out))
    return out


def upload_state(name, seq=None, title=None, now=None):
    """이 영상·편집본을 이미 올렸거나 공개 예약했는지 → {"at": 공개(예정) 시각 | None, "scheduled": 예약이 아직 안 됨, "how"} · 아니면 None.
    유튜브에 바로 올린 기록(공개·일부 공개·공개 예약 · strategy.published) → 없으면 키트 제목이 우리 채널 제목과 같은지(stock 과 같은 규칙)."""
    now = now or time.time()
    try:
        import strategy
        import youtube_upload
        for h in youtube_upload.history(youtube_upload.HISTORY_MAX)["items"]:  # 최근 것부터
            if h.get("name") == name and (h.get("seq") or "") == (seq or "") and strategy.published(h):
                ts = strategy._iso(h.get("publishAt")) if h.get("publishAt") else h.get("at")
                ts = ts if isinstance(ts, (int, float)) else None
                return {"at": ts, "scheduled": bool(h.get("publishAt")) and bool(ts) and ts > now, "how": "history"}
    except Exception:  # 기록을 못 읽으면 안 올린 것으로
        pass
    if title:
        try:
            rows = hooks.own_rows()
            if hooks.norm_title(title) in {hooks.norm_title(r.get("title")) for kind in ("videos", "shorts") for r in rows.get(kind) or []}:
                return {"at": None, "scheduled": False, "how": "title"}
        except Exception:
            pass
    return None


def kit_schedule(name, seq, fmt, title=None):
    """이 키트의 다음 올릴 날 (strategy.kit_slot · 전략을 저장했을 때만) · 못 구하면 None.
    이미 올렸거나 예약한 편집본이면 새 날 대신 {"done": True, line: '이미 예약했어요: …'/'올렸어요: …'} (D-086 보강)."""
    try:
        import strategy
        if not strategy.load_state().get("strategy"):
            return None
        done = upload_state(name, seq, title)
        if done:
            at = done.get("at")
            if done["how"] == "title":
                line = "우리 채널에 같은 제목의 영상이 있어서 올린 것으로 봤어요 · 아직 안 올렸으면 제목을 바꿔 주세요"
            elif done["scheduled"]:
                line = f"이미 공개 예약했어요: {strategy.slot_text(at)}"
            else:
                line = f"이미 올렸어요{f': {strategy.slot_text(at)}' if at else ''}"
            return {"done": True, "at": at, "line": line, "why": "", "kind": "S" if fmt == "shorts" else "L"}
        return strategy.kit_slot(name, seq or "", "S" if fmt == "shorts" else "L")
    except Exception as e:
        studio_log(f"다음 올릴 날을 구하지 못했어요 · {e}")
        return None


# ---------- 열기 ----------

def reveal(path):
    """파일 위치 열기: Windows 탐색기에서 그 파일을 골라 둔 채로 (없으면 폴더만)."""
    p = Path(os.path.abspath(path))
    if sys.platform == "win32":
        if p.exists():
            subprocess.Popen(f'explorer /select,"{p}"')  # 목록이 아닌 글자 하나로 넘겨야 띄어쓰기·한글 경로가 안 깨짐
        else:
            os.startfile(str(p.parent if p.parent.exists() else core.OUT))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)] if p.exists() else ["open", str(p.parent)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])


def open_template():
    p = template_path()
    load_template()  # 없으면 기본 틀을 만들어 둠
    if sys.platform == "win32":
        try:
            os.startfile(str(p))  # noqa
        except OSError:
            subprocess.Popen(["notepad.exe", str(p)])
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])
    return p


def open_studio():
    webbrowser.open(STUDIO_URL)


# ---------- 유튜브에 바로 올리기 (youtube_upload) 가 쓰는 파일 찾기 ----------

def files_for(name, seq=None):
    """올릴 파일 (읽기만): 편집본이면 가장 최근에 내보낸 영상과 그 옆의 .srt · 원본이면 보관함 영상과 analysis/<이름>/subtitles.srt.
    → {"video": 경로 또는 None(아직 안 내보냄), "srt": 경로 또는 None, "srtWhere": "out"|"analysis"|None, "format": 편집본 형식|None, "export": 파일 이름|None}"""
    path = editor.video_path(name)
    sq, exp = _target(name, seq)
    if sq:
        srt = exp.with_suffix(".srt") if exp else None
        ok = bool(srt and srt.is_file())
        return {"video": exp, "srt": srt if ok else None, "srtWhere": "out" if ok else None,
                "format": "shorts" if sq.get("format") == "shorts" else "long", "export": exp.name if exp else None}
    srt = core.adir(name) / "subtitles.srt"
    ok = srt.is_file()
    return {"video": path, "srt": srt if ok else None, "srtWhere": "analysis" if ok else None, "format": None, "export": None}


# ---------- 스튜디오 4단계 카드의 '다음 할 일' (D-075) ----------

def _edit_kit(kit, name, ids):
    """이 영상의 편집본(ids)으로 만든 키트인지 (손으로 고쳐 깨진 칸이 있어도 오류 없이)."""
    who, sid = _owner(kit or {})
    return who == name and isinstance(sid, str) and sid in ids


def progress(names):
    """영상마다 어디까지 했는지 {seqs, rough, exported, thumb, kit}: 편집본 수 · 가편집이 있음 · 내보낸 편집본 수 ·
    저장한 썸네일 그림 · 편집본으로 만든 올리기 키트 (읽기만 · 완성본 폴더·보관함 목록은 한 번만 읽음 · 이름이 더 긴 다른 영상의 파일은 뺌)."""
    try:
        out_files = [p for p in core.OUT.iterdir() if p.is_file()]
        lib = [v.name for v in core.VIDEOS.iterdir() if core.is_video_file(v.name)]
    except OSError:
        out_files, lib = [], []
    pres = {n: core.adir(n).name + "_" for n in set(lib) | set(names)}
    res = {}
    for n in names:
        pre = pres[n]
        longer = [o for o in set(pres.values()) if o != pre and o.startswith(pre)]
        mine = [p for p in out_files if p.name.startswith(pre) and not any(p.name.startswith(o) for o in longer)]
        seqs = (read_project(n) or {}).get("sequences") or []
        mp4 = [p.name for p in mine if p.suffix.lower() == ".mp4"]
        exported = sum(1 for sq in seqs  # latest_export 와 같은 이름 규칙 (구간 내보내기는 뺌)
                       if any(re.fullmatch(rf"{re.escape(pre)}{re.escape(_seq_label(sq))}(?: \(\d+\))*\.mp4", x, re.I) for x in mp4))
        ids = {sq.get("id") for sq in seqs if sq.get("id")}  # 편집본으로 만든 키트만 ('원본 영상 그대로' 키트는 올릴 완성본의 키트가 아님)
        kit = any(p.suffix == ".json" and _KIT_FILE.search(p.name) and _edit_kit(_read_kit(p), n, ids) for p in mine)
        res[n] = {"seqs": len(seqs), "rough": bool(seqs), "exported": exported,  # 썸네일: 저장한 그림·A/B 묶음 (모바일 비교 한 장은 빼고)
                  "thumb": bool(_thumb_images(pre, mine)), "kit": kit}
    return res
