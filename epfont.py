"""은평 스타일 글꼴 (KFA 글꼴 고정 · BR-310~BR-313 · D-500~D-505) — 이 PC 에서 KFA 글꼴을 찾아 은평 스타일 쇼츠의 모든 글자에 씀.

KFA 글꼴(대한축구협회 글꼴 · 지역 협회에 엠블럼 작업용으로 나눠 줌)은 사용 허락이 공개돼 있지 않아 앱·저장소에 싣지 않는다 (D-500).
찾는 차례:
① 스타일 화면 '글꼴 파일 고르기'로 고른 파일 (확인 뒤 작업 폴더 styles/설정/은평 그림/ 에 복사 · style.card_font_path) — 이름은 따지지 않음
② Windows 에 깔린 글꼴 중 파일 이름에 'KFA' 가 든 것 (속 이름은 안 따짐 · 진짜 KFA 글꼴의 속 이름은 아직 모름) → 없으면 파일 속 글꼴 이름
   (이름 표의 글꼴 이름·전체 이름·PostScript 이름·묶음 이름 · 모든 언어)에 'KFA' 가 든 것 (C:\\Windows\\Fonts · %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts · 여럿이면 가장 굵은 것)
찾은 글꼴은 진짜 글꼴 이름(ASS 글꼴 이름 · Windows 이름 표 1번 · 영어가 먼저 · 쉼표·제어 글자가 든 이름은 다음 이름·전체 이름(CFF 글꼴은 PostScript 이름까지) · 다 못 쓰면 그 글꼴은 안 씀)·굵기·
줄 높이 비율(libass 가 쓰는 win 위+아래 / em)·글자 폭 표(글자 크기 1 기준)를 읽어 둠 → editor.ep_font (내보내기 ASS · 미리보기 @font-face · 글자 맞춤이 모두 같은 표).
TTC 묶음은 고른 글꼴 하나만 떼어 미리보기에 줌 (single_face · 브라우저는 묶음의 첫 글꼴만 그림 · 내보내기 libass 는 이름·굵기로 고름).
찾기는 결과를 기억해 두고(고른 파일·글꼴 폴더가 바뀌면 다시 · 파일마다 읽은 이름 표도 기억해 새로 깐 파일만 읽음) 앱을 켤 때 뒤에서 미리 함 (warm) —
화면 요청(find(wait=False))은 스스로 찾지 않고 뒤에서 찾게 한 뒤 UI_WAIT 초만 기다림."""
import hashlib
import io
import os
import struct
import threading
import time
from pathlib import Path

FONT_EXTS = (".ttf", ".otf", ".ttc")
FONT_MAX = 40 << 20        # 고른 글꼴 파일 크기 한도 (바이트 · 한글 글꼴은 보통 2~20MB)
KEY = "kfa"                # 깔린 글꼴 중 이 글자가 이름에 든 것 (대소문자 상관없이)
RECHECK = 5.0              # 기억해 둔 결과를 다시 확인하는 간격 (초 · 고른 파일·글꼴 폴더 바뀜만 봄 = 가벼움)
UI_WAIT = 0.5              # 화면 요청이 뒤의 찾기를 기다리는 가장 긴 시간 (초 · 넘으면 기억해 둔 값 + '찾는 중' · 화면은 잠시 뒤 다시 물음)
SCAN_MAX = 4000            # 한 폴더에서 살펴볼 글꼴 파일 수 한도 (글꼴을 아주 많이 깐 PC 에서도 끝나게)
EM_SIZE = 1000             # 글자 폭을 잴 글자 크기 (화소 · 폭 표는 1 기준으로 나눔 · 넷째 자리까지)
# 글자 폭 표에 넣을 글자: 띄어쓰기·영문·숫자·부호 + 화면 글에 자주 나오는 것 (한글은 아래 HANGUL 평균 하나 · 한글 글꼴은 보통 모든 한글이 같은 폭)
EM_CHARS = "".join(chr(c) for c in range(0x20, 0x7F)) + "·▶…“”‘’→"
HANGUL = "가나다라마바사아자차카타파하것는을를이에서한국축구풋살패스슈팅드리블레슨전체강좌채널"
_HANGUL_RANGES = ((0xAC00, 0xD7A3), (0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xD7B0, 0xD7FF))
_SFNT = (b"\x00\x01\x00\x00", b"OTTO", b"true")


def is_hangul(ch):
    o = ord(ch)
    return any(a <= o <= b for a, b in _HANGUL_RANGES)


def font_dirs():
    """Windows 글꼴 폴더 (모든 사용자 · 이 사용자만 설치) — 없는 폴더는 건너뜀 · Windows 가 아니면 없음 (개발 PC · 시험은 FONT_DIRS)."""
    if os.name != "nt":
        return []
    out = [Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts"]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        out.append(Path(la) / "Microsoft" / "Windows" / "Fonts")
    return out


FONT_DIRS = None   # 시험은 폴더 목록을 바꿔 끼움 (None = font_dirs())


# ---------- 글꼴 파일 읽기 (sfnt · 표준 라이브러리만) ----------

def _u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def _i16(b, o):
    return struct.unpack_from(">h", b, o)[0]


def _u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def _read(f, off, n):
    f.seek(off)
    b = f.read(n)
    if len(b) < n:
        raise ValueError("잘린 글꼴 파일")
    return b


def _names(raw):
    """이름 표 → {이름 번호: [(플랫폼, 언어, 글)]} (글꼴 이름 1 · 하위 이름 2 · 전체 이름 4 · PostScript 이름 6 · 묶음 이름 16 만)."""
    out = {}
    count, base = _u16(raw, 2), _u16(raw, 4)
    for i in range(count):
        o = 6 + 12 * i
        if o + 12 > len(raw):
            break
        plat, enc, lang, nid, ln, off = struct.unpack_from(">6H", raw, o)
        if nid not in (1, 2, 4, 6, 16):
            continue
        s = raw[base + off: base + off + ln]
        if plat in (0, 3):
            txt = s.decode("utf-16-be", "replace")
        elif plat == 1 and enc == 0:
            txt = s.decode("latin-1")
        else:
            continue
        txt = txt.replace("\x00", "").strip()
        if txt:
            out.setdefault(nid, []).append((plat, lang, txt))
    return out


def _pick_name(names, nid):
    """libass 가 맞춰 보는 이름 (Windows 플랫폼 · 영어가 먼저 · 없으면 아무 언어 · 그래도 없으면 다른 플랫폼)."""
    rows = names.get(nid) or []
    for want in (lambda p, l: p == 3 and l == 0x409, lambda p, l: p == 3, lambda p, l: p == 0, lambda p, l: True):
        for p, lang, txt in rows:
            if want(p, lang):
                return txt
    return ""


def _face(f, off, index):
    head = _read(f, off, 12)
    if head[:4] not in _SFNT:
        raise ValueError("글꼴 파일이 아니에요")
    n = _u16(head, 4)
    if not 0 < n < 200:
        raise ValueError("글꼴 표가 이상해요")
    recs = _read(f, off + 12, 16 * n)
    tabs = {recs[16 * i: 16 * i + 4]: (_u32(recs, 16 * i + 8), _u32(recs, 16 * i + 12)) for i in range(n)}
    for need in (b"head", b"name", b"cmap", b"hhea", b"hmtx"):
        if need not in tabs:
            raise ValueError("글꼴에 꼭 있어야 할 표가 없어요")
    h = _read(f, tabs[b"head"][0], 54)
    if _u32(h, 12) != 0x5F0F3CF5:
        raise ValueError("글꼴 머리 표가 이상해요")
    upem = _u16(h, 18)
    if not 16 <= upem <= 16384:
        raise ValueError("글꼴 크기 단위가 이상해요")
    hh = _read(f, tabs[b"hhea"][0], 36)
    hhea = _i16(hh, 4) - _i16(hh, 6)
    win, weight = 0, 400
    if b"OS/2" in tabs and tabs[b"OS/2"][1] >= 78:
        os2 = _read(f, tabs[b"OS/2"][0], 78)
        weight = _u16(os2, 4) or 400
        win = _u16(os2, 74) + _u16(os2, 76)
    toff, tlen = tabs[b"name"]
    if tlen > 1 << 20:
        raise ValueError("글꼴 이름 표가 너무 커요")
    names = _names(_read(f, toff, tlen))
    # libass 는 줄 높이를 OS/2 win 위+아래로 맞춤 (없으면 hhea) → ASS 글자 크기 = 화면 글자 크기 × 이 비율 (editor.FONT_K 와 같은 뜻)
    k = (win or hhea) / upem
    if not 0.5 <= k <= 3.0:
        raise ValueError("글꼴 줄 높이가 이상해요")
    # libass 가 글꼴을 맞춰 볼 이름 (Windows 플랫폼 글꼴 이름 1·전체 이름 4 · 영어가 먼저 · CFF 글꼴만 PostScript 이름도) → _info 가 ASS 에 쓸 수 있는 첫 이름
    wn = lambda nid: [t for _p, _l, t in sorted((r for r in names.get(nid) or [] if r[0] == 3), key=lambda r: r[1] != 0x409)]  # noqa: E731
    ass = wn(1) + wn(4) + (wn(6) if head[:4] == b"OTTO" else [])
    return {"index": index, "family": _pick_name(names, 1), "full": _pick_name(names, 4), "ass": ass, "weight": int(weight), "k": round(k, 4),
            "names": sorted({t for rows in names.values() for _, _, t in rows})}


def faces(src):
    """글꼴 파일(경로 또는 바이트) → 글꼴 목록 [{index, family, full, weight, k, names}] (TTC 는 여럿) · 글꼴이 아니면 ValueError."""
    f = io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else open(src, "rb")
    try:
        tag = _read(f, 0, 12)
        if tag[:4] == b"ttcf":
            n = _u32(tag, 8)
            if not 0 < n <= 64:
                raise ValueError("글꼴 묶음 파일이 이상해요")
            offs = struct.unpack(f">{n}I", _read(f, 12, 4 * n))
        elif tag[:4] in _SFNT:
            offs = (0,)
        else:
            raise ValueError("글꼴 파일이 아니에요")
        return [_face(f, o, i) for i, o in enumerate(offs)]
    except (struct.error, OSError, OverflowError) as e:
        raise ValueError("글꼴 파일을 읽지 못했어요") from e
    finally:
        f.close()


def ext_of(raw):
    """글꼴 바이트 → 확장자 (.ttc 묶음 · .otf 'OTTO' · 그 밖 .ttf)."""
    return ".ttc" if raw[:4] == b"ttcf" else ".otf" if raw[:4] == b"OTTO" else ".ttf"


def has_kfa(face):
    return any(KEY in t.casefold() for t in face["names"])


def metrics(path, index=0):
    """글꼴(경로 또는 바이트)의 글자 폭 표 (글자 크기 1 기준 · 넷째 자리) {"em": {글자: 폭}, "hangul": 한글 평균 폭} — 글꼴에 없는 글자는 표에서 뺌
    (화면은 다른 글꼴로 그리므로 예전 어림 그대로) · 한글이 없거나 PIL 이 못 읽으면 ValueError."""
    try:
        from PIL import ImageFont
        fo = ImageFont.truetype(io.BytesIO(path) if isinstance(path, (bytes, bytearray)) else str(path), EM_SIZE, index=index)
    except Exception as e:  # noqa: BLE001 — PIL 은 깨진 글꼴마다 다른 예외를 냄
        raise ValueError("글꼴 파일을 읽지 못했어요") from e
    nd = "\U0010FFFD"   # 어떤 글꼴에도 없는 글자 → 빈 글자 모양(.notdef)
    nd_w, nd_m = fo.getlength(nd), bytes(fo.getmask(nd))

    def missing(ch):
        return ch != " " and fo.getlength(ch) == nd_w and bytes(fo.getmask(ch)) == nd_m
    hs = [fo.getlength(ch) for ch in HANGUL if not missing(ch)]
    if len(hs) < len(HANGUL) // 2:
        raise ValueError("한글이 없는 글꼴이에요 · 한글 글꼴을 골라 주세요")
    em = {ch: round(fo.getlength(ch) / EM_SIZE, 4) for ch in EM_CHARS if not missing(ch)}
    return {"em": em, "hangul": round(sum(hs) / len(hs) / EM_SIZE, 4)}


def ass_name_ok(name):
    """ASS 'Style:' 줄에 넣을 수 있는 글꼴 이름인지 — 쉼표(칸 나눔)·제어 글자가 없고 비어 있지 않음."""
    return bool(name and name.strip()) and "," not in name and not any(ord(c) < 32 or ord(c) == 127 for c in name)


def _info(path, face, src, shown):
    p = Path(path)
    st = p.stat()
    sig = hashlib.sha1(f"{p}|{face['index']}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")).hexdigest()[:10]
    fam = next((x for x in face["ass"] if ass_name_ok(x)), "")
    if not fam and not face["ass"]:   # Windows 이름이 없으면 FreeType 이 주는 이름 (libass 도 그렇게 함 · 이름이 있는데 다 못 쓰면 이 글꼴은 안 씀)
        from PIL import ImageFont
        fam = ImageFont.truetype(str(p), 20, index=face["index"]).getname()[0] or ""
    if not ass_name_ok(fam):
        raise ValueError("글꼴 이름이 없거나 쓸 수 없는 글자가 들어 있어요")
    return dict(metrics(p, face["index"]), path=str(p), index=face["index"], family=fam, weight=face["weight"], k=face["k"],
                src=src, file=shown, sig=sig, ext=p.suffix.lower())


def _best(fs, want_kfa=False):
    """한 파일의 글꼴 중 쓸 것 — KFA 이름이 든 것 먼저 · 가장 굵은 것 (영상 글자는 굵게 · 여럿이면 소유자에게 묻기)."""
    order = lambda c: sorted(c, key=lambda x: (-x["weight"], x["index"]))  # noqa: E731
    k = order([x for x in fs if has_kfa(x)])
    return k if want_kfa else k + order([x for x in fs if not has_kfa(x)])


# ---------- 찾기 ----------

def _picked():
    """스타일 화면에서 고른 글꼴 (경로, 보이는 이름) · 없으면 (None, '')."""
    try:
        import style
        return style.card_font_path(), style.card_font()["name"]
    except Exception:  # noqa: BLE001 — 설정을 못 읽으면 고른 것 없음
        return None, ""


def _dirs():
    return list(FONT_DIRS) if FONT_DIRS is not None else font_dirs()


def _scan(d):
    try:
        with os.scandir(d) as it:
            out = []
            for e in it:
                if len(out) >= SCAN_MAX:
                    break
                if e.name.lower().endswith(FONT_EXTS) and e.is_file():
                    out.append(Path(e.path))
            return sorted(out, key=lambda p: p.name.lower())
    except OSError:
        return []


_FACES = {}   # 글꼴 파일 경로 → ((바뀐 시각, 크기), faces 결과 · 글꼴이 아니면 None) — 글꼴을 하나 새로 깔아도 그 파일만 읽게 (_LOCK 안에서만)


def _faces_cached(p):
    try:
        st = p.stat()
    except OSError:
        return None
    sig, hit = (st.st_mtime_ns, st.st_size), _FACES.get(str(p))
    if hit and hit[0] == sig:
        return hit[1]
    try:
        fs = faces(p)
    except (ValueError, OSError):
        fs = None
    _FACES[str(p)] = (sig, fs)
    return fs


def _installed():
    """깔린 KFA 글꼴 → (경로, 글꼴) · 없으면 None — 파일 이름에 KFA 가 든 것부터 보고 (빠름 · 속 이름은 안 따짐 · KFA 이름이 든 것 먼저),
    없으면 모든 글꼴 파일의 이름 표를 읽어 이름에 KFA 가 든 것 (파일마다 읽은 것은 기억해 둠 · _FACES)."""
    files = [p for d in _dirs() for p in _scan(d)]
    keep = {str(p) for p in files}
    for k in [k for k in _FACES if k not in keep]:   # 지운 글꼴은 잊음
        del _FACES[k]
    quick = [p for p in files if KEY in p.name.casefold()]
    for group, by_name in ((quick, False), ([p for p in files if p not in quick], True)):
        found = []
        for p in group:
            fs = _faces_cached(p)
            if fs:
                found += [(p, x) for x in _best(fs, want_kfa=by_name)]
        for p, x in sorted(found, key=lambda t: (not has_kfa(t[1]), -t[1]["weight"], t[0].name.lower(), t[1]["index"])):
            try:
                metrics(p, x["index"])
                return p, x
            except ValueError:
                continue
    return None


def _key():
    pk, _ = _picked()
    parts = [str(pk or "")]
    if pk:
        try:
            st = pk.stat()
            parts.append(f"{st.st_mtime_ns}|{st.st_size}")
        except OSError:
            parts.append("-")
    for d in _dirs():
        try:
            parts.append(f"{d}|{d.stat().st_mtime_ns}")
        except OSError:
            parts.append(f"{d}|-")
    return "\n".join(parts)


def _lookup(log=None):
    pk, shown = _picked()
    if pk:
        try:
            for x in _best(faces(pk)):
                try:
                    return _info(pk, x, "picked", shown or pk.name)
                except ValueError:
                    continue
        except (ValueError, OSError) as e:
            (log or _log)(f"고른 은평 스타일 글꼴을 읽지 못해 깔린 글꼴을 찾아요 · {pk.name} · {e}")
    hit = _installed()
    if hit:
        try:
            return _info(hit[0], hit[1], "installed", hit[0].name)
        except (ValueError, OSError) as e:
            (log or _log)(f"KFA 글꼴을 읽지 못했어요 · {hit[0].name} · {e}")
    return None


def _log(msg):
    try:
        import studiolog
        studiolog.write(msg)
    except Exception:  # noqa: BLE001 — 기록을 못 남겨도 찾기는 계속
        pass


_LOCK = threading.Lock()
_CACHE = {"key": None, "info": None, "at": 0.0, "busy": False}
_BG_LOCK = threading.Lock()
_BG = {"th": None}   # 뒤에서 찾는 스레드 (하나만)


def _refresh():
    """(_LOCK 안에서) 고른 파일·글꼴 폴더가 바뀌었으면 다시 찾음 → 기억해 둔 값."""
    c = _CACHE
    key = _key()
    if key != c["key"]:
        c["busy"] = True
        try:
            c["info"] = _lookup()
        except Exception as e:  # noqa: BLE001 — 글꼴 찾기가 실패해도 지금 글꼴로 만듦 (부가 기능)
            _log(f"은평 스타일 글꼴을 찾지 못했어요 · {type(e).__name__}: {e}")
            c["info"] = None
        finally:
            c["busy"] = False
        c["key"] = key
    c["at"] = time.monotonic()
    return c["info"]


def _kick():
    """뒤에서 찾기 시작 (이미 찾는 중이면 그 스레드) → 스레드."""
    with _BG_LOCK:
        th = _BG["th"]
        if th is None or not th.is_alive():
            th = threading.Thread(target=find, daemon=True, name="은평 글꼴 찾기")
            th.start()
            _BG["th"] = th
        return th


def find(wait=True):
    """지금 쓸 은평 스타일 글꼴 정보 (dict · 경로·이름·굵기·줄 높이 비율 k·글자 폭 표 em·hangul·src picked/installed·file·sig) · 못 찾으면 None.
    wait=True(내보내기·가편집·글자 크기를 정해 저장하는 곳): 찾기가 끝날 때까지 기다림 (다시 찾을 때는 새로 깐 파일만 읽음).
    wait=False(화면 요청): 스스로 찾지 않고 뒤에서 찾게 한 뒤 UI_WAIT 초만 기다림 — 넘으면 기억해 둔 값(처음이면 None · checking() 이 참)."""
    c = _CACHE
    if c["key"] is not None and time.monotonic() - c["at"] < RECHECK:
        return c["info"]
    if wait:
        with _LOCK:
            return _refresh()
    _kick().join(UI_WAIT)
    return c["info"]


def forget(files=True):
    """기억해 둔 결과를 버림 (글꼴을 고르거나 '처음으로' 했을 때 · 시험) · files=False 면 파일마다 읽은 이름 표는 남김 (깔린 글꼴은 그대로라서)."""
    with _LOCK:
        _CACHE.update(key=None, info=None, at=0.0)
        if files:
            _FACES.clear()


def checking():
    """다른 스레드가 고른 파일·글꼴 폴더가 바뀐 것을 보고 다시 찾는 중인지 (스타일 화면 '찾는 중' · 편집실은 끝나면 글꼴을 다시 받음)."""
    return _CACHE["busy"]


def warm():
    """앱을 켤 때 뒤에서 미리 찾아 둠 (첫 화면 요청이 글꼴 폴더를 다 읽기를 기다리지 않게)."""
    _kick()


def single_face(path, index):
    """TTC 묶음에서 글꼴 하나만 떼어 낸 글꼴 파일 바이트 (표 자료는 그대로 복사 · 위치만 새 파일 기준 · 4바이트 맞춤) → (바이트, '.ttf'/'.otf').
    브라우저 FontFace 는 묶음의 첫 글꼴만 그리므로 편집실 미리보기가 내보내기와 같은 글꼴을 그리게 (/api/style/ep_font)."""
    with open(path, "rb") as f:
        tag = _read(f, 0, 12)
        if tag[:4] != b"ttcf":
            raise ValueError("글꼴 묶음 파일이 아니에요")
        n = _u32(tag, 8)
        if not 0 <= index < n <= 64:
            raise ValueError("글꼴 묶음 파일이 이상해요")
        off = _u32(_read(f, 12 + 4 * index, 4), 0)
        head = _read(f, off, 12)
        if head[:4] not in _SFNT:
            raise ValueError("글꼴 파일이 아니에요")
        nt = _u16(head, 4)
        if not 0 < nt < 200:
            raise ValueError("글꼴 표가 이상해요")
        recs = _read(f, off + 12, 16 * nt)
        total = os.fstat(f.fileno()).st_size
        out_recs, body, pos = [], [], 12 + 16 * nt
        for i in range(nt):
            tg, ck, toff, tlen = struct.unpack_from(">4sIII", recs, 16 * i)
            if toff + tlen > total:
                raise ValueError("잘린 글꼴 파일")
            data = _read(f, toff, tlen) + b"\0" * ((-tlen) % 4)
            out_recs.append(struct.pack(">4sIII", tg, ck, pos, tlen))
            body.append(data)
            pos += len(data)
    return head + b"".join(out_recs) + b"".join(body), ".otf" if head[:4] == b"OTTO" else ".ttf"


def public(info):
    """화면에 줄 것 (경로는 빼고 · 글꼴 파일은 /api/style/ep_font 로) — 편집실 미리보기가 같은 표로 글자를 맞춤."""
    if not info:
        return None
    return {"family": info["family"], "k": info["k"], "em": info["em"], "hangul": info["hangul"], "src": info["src"], "file": info["file"],
            "url": f"/api/style/ep_font?v={info['sig']}"}
