"""자막 나누기·합치기 테스트 (D-380~D-389) — 저장소 폴더에서 python3 -m unittest tests.test_capseg

소유자 말: "자막 같은 경우도, 따로따로 들어갈 부분들이 합쳐져서 분리가 안됨" (9:16 쇼츠 · 코치와 선수가 빠르게 주고받는 짧은 말).
captions(말 경계 utterance_breaks · segment · from_segments · split_text · merged_utterances) · editor(oneline_captions · resplit_captions ·
merged_captions) · node 가 있으면 editor.html 나누기·합치기·글 고치기(capSplitPlan · capSplitDo · capMergeDo · capRetext)."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import captions  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402

NODE = shutil.which("node")
TALL, LAND = {"width": 1080, "height": 1920}, {"width": 1920, "height": 1080}

# 소유자 화면의 쇼츠와 같은 빠른 두 사람 말: (말한 사람, 글, 뒤에 쉰 초) — 예전 판은 '순두시지 마, 감독님.' · '혼난다. 시작!' 처럼 붙였음
OWNER = [("A", "감독님, 요 세 마디만 하면 돼.", 0.30), ("A", "다른 말 하지 마,", 0.20), ("A", "순두시지 마,", 0.15), ("B", "감독님.", 0.35),
         ("A", "그리고 뒤에 말하면", 0.15), ("A", "혼난다.", 0.40), ("B", "시작!", 0.60)]
OWNER_WANT = ["감독님, 요 세 마디만 하면 돼.", "다른 말 하지 마,", "순두시지 마,", "감독님.", "그리고 뒤에 말하면 혼난다.", "시작!"]
# 반말·짧은 대답 (문장부호 없이 받아쓴 것)
BANMAL = [("B", "공 받기 전에 봐야 돼", 0.30), ("B", "고개 들어", 0.20), ("A", "이렇게 하면 돼", 0.25), ("B", "그렇지 잘했어", 0.20),
          ("A", "진짜 어렵네", 0.30), ("B", "한 번 더 해", 0.15), ("A", "알겠어", 0.50)]
# 롱폼 설명 (한 사람 · 길게 이어지는 말) — 너무 잘게 나누지 않는지
LECTURE = [("B", "공을 받기 전에 고개를 들어서 주변을 먼저 보세요.", 0.45), ("B", "많은 분들이 공만 보고 있다가", 0.25),
           ("B", "공이 오면 그제서야 어디로 갈지 생각하시거든요.", 0.50), ("B", "그러면 이미 늦어요.", 0.30), ("B", "수비가 바로 붙어버리니까요.", 0.60)]


def syl(w):
    return max(1, len(re.sub(r"[^가-힣A-Za-z0-9]", "", w)))


def build(utts, mode="utt", punct=True, rate=7.0, t0=0.3):
    """빠른 말(1초에 rate 음절) → 받아쓰기 구간 · mode: utt(말마다 구간) · one(전부 한 구간) · punct=False: 쉼표·마침표·?! 지움."""
    t, words, spans = t0, [], []
    for _, text, pause in utts:
        toks, a = text.split(), len(words)
        for k, w in enumerate(toks):
            d = syl(w) / rate
            words.append({"w": w if punct else re.sub(r"[,.?!…]", "", w), "s": round(t, 2), "e": round(t + d, 2), "p": 0.9})
            t += d + (0.04 if k + 1 < len(toks) else 0)
        spans.append((a, len(words)))
        t += pause
    groups = [[i] for i in range(len(spans))] if mode == "utt" else [list(range(len(spans)))]
    segs = []
    for g in groups:
        ws = words[spans[g[0]][0]:spans[g[-1]][1]]
        segs.append({"start": ws[0]["s"], "end": ws[-1]["e"], "text": " ".join(w["w"] for w in ws), "words": [dict(w) for w in ws]})
    return segs


def texts(caps):
    return [c["text"] for c in caps]


def strip(t):
    return re.sub(r"[,.?!]", "", t)


class SegmentTest(unittest.TestCase):
    """새 자막 나누기 (D-380 · D-381 · D-383): 따로 한 말은 따로 · 한 말은 길이 한도 안에서."""

    def test_owner_script_shorts_and_long(self):
        for fmt in ("shorts", "long"):
            for mode in ("utt", "one"):
                with self.subTest(fmt=fmt, mode=mode):
                    self.assertEqual(texts(captions.from_segments(build(OWNER, mode), fmt, captions.DEFAULT_TERMS)), OWNER_WANT)

    def test_owner_script_without_punctuation(self):
        for fmt in ("shorts", "long"):
            caps = texts(captions.from_segments(build(OWNER, "one", False), fmt, captions.DEFAULT_TERMS))
            self.assertEqual(caps, [strip(t) for t in OWNER_WANT], fmt)

    def test_short_replies_stay_alone_and_readable(self):
        caps = captions.from_segments(build(OWNER), "shorts", captions.DEFAULT_TERMS)
        last = caps[-1]
        self.assertEqual(last["text"], "시작!")
        self.assertGreaterEqual(last["end"] - last["start"], captions.MIN_DUR - 1e-6)  # 뒤가 비어 있으면 MIN_DUR 까지 보여 줌
        tl = editor.timeline_captions({"captions": [{"id": f"c{k}", **{x: c[x] for x in ("start", "end", "text")}} for k, c in enumerate(caps)],
                                       "format": "shorts", "v": 2, "items": [
                                           {"id": "v", "track": "V1", "media": "main", "start": 0.0, "in": 0.0, "out": 9.0},
                                           {"id": "a", "track": "A1", "media": "main", "start": 0.0, "in": 0.0, "out": 9.0}]})
        short = [c for c in tl if c["text"] == "감독님."][0]
        self.assertGreaterEqual(short["end"] - short["start"], 0.7 - 1e-6)  # 타임라인에서는 CAP_MIN(0.7초) 보이게 다음 자막을 조금 늦춤

    def test_banmal_endings(self):
        caps = texts(captions.from_segments(build(BANMAL, "one", False), "long", captions.DEFAULT_TERMS))
        for a, b in (("이렇게 하면 돼", "그렇지 잘했어"), ("한 번 더 해", "알겠어"), ("진짜 어렵네", "한 번 더 해")):
            self.assertFalse(any(a in c and b in c for c in caps), caps)

    def test_lecture_not_over_split(self):
        for fmt, most in (("long", 6), ("shorts", 9)):
            for punct in (True, False):
                caps = captions.from_segments(build(LECTURE, "one", punct), fmt, captions.DEFAULT_TERMS)
                self.assertLessEqual(len(caps), most, (fmt, punct, texts(caps)))
                self.assertTrue(all(len(c["text"].split()) > 1 for c in caps), texts(caps))  # 낱말 하나짜리 자막 없음

    def test_rules_and_guards(self):
        def brk(pairs, **kw):
            t, ws = 0.0, []
            for w, gap in pairs:
                ws.append({"w": w, "s": t, "e": t + 0.3})
                t += 0.3 + gap
            return captions.utterance_breaks(ws, **kw)
        self.assertEqual(brk([("시작!", 0.12), ("하자", 0)]), {0})            # 문장부호 + 0.12초
        self.assertEqual(brk([("혼난다", 0.15), ("시작", 0)]), {0})           # 반말 말끝(-ㄴ다)
        self.assertEqual(brk([("하지", 0.0), ("마", 0.15), ("감독님", 0)]), {1})  # '하지 / 마'는 붙임 · '마' 뒤는 나눔
        self.assertEqual(brk([("해", 0.3), ("주세요", 0)]), set())            # 붙여 읽는 풀이말
        self.assertEqual(brk([("할", 0.3), ("수", 0.0), ("있어요", 0)]), set())  # 기대는 말 앞
        self.assertEqual(brk([("두", 0.3), ("개", 0)]), set())                # 꾸미는 말 뒤
        self.assertEqual(brk([("선수가", 0.2), ("공을", 0)]), set())          # '~가'는 조사일 때가 많아
        self.assertEqual(brk([("선수가", 0.35), ("공을", 0)]), set())         # 쉬어도 뒤가 새 말이 아니면 안 나눔 (검토: '수비수가' | '따라오면 패스')
        self.assertEqual(brk([("알겠니", 0.35), ("네", 0)]), {0})             # 뒤가 대답이면 나눔
        self.assertEqual(brk([("자,", 0.4), ("첫", 0.0), ("번째", 0)]), set())  # '자,' 는 뒤 말의 머리 · 쉼표만으로는 안 나눔
        self.assertEqual(brk([("감독님,", 0.25), ("이거", 0)]), set())         # 말 머리의 부르는 말은 뒤 말과 함께 (검토: '감독님,' 혼자 떨어짐)
        self.assertEqual(brk([("감독님,", 0.6), ("이거", 0)]), {0})            # 오래 쉬면 나눔
        self.assertEqual(brk([("하지", 0.0), ("마", 0.0), ("감독님", 0.3), ("그리고", 0)]), {2})  # 앞말 뒤에 붙은 부르는 말 뒤
        self.assertEqual(brk([("요", 0.2), ("세", 0)]), set())                # 혼자 쓴 '요'는 '이'
        self.assertEqual(brk([("공을", 0.6), ("차요", 0)]), {0})              # 0.6초 넘게 쉼
        self.assertEqual(brk([("퍼스트", 0.7), ("터치", 0)], terms=["퍼스트 터치"]), set())  # 여러 단어 용어 안은 안 나눔
        self.assertEqual(brk([("시작!", 0), ("하자", 0)], est=True), {0})     # 어림한 시각: 문장부호만
        self.assertEqual(brk([("혼난다", 0.3), ("시작", 0)], est=True), set())

    def test_latched_fast_replies(self):
        """검토(I-381): 쉼 없이 바로 받는 두 사람 말 — 받아쓰기가 마침표 뒤를 0.04~0.10초로 잡아도 . ? ! 뒤는 나눔 (예전 6/16 · 검토 시점 5/16 합쳐짐)."""
        fx = json.loads((ROOT / "tests" / "fixtures" / "capseg_latched.json").read_text(encoding="utf-8"))
        bounds = [(a["end"] + b["start"]) / 2 for a, b in zip(fx["truth"], fx["truth"][1:])]
        for fmt in ("shorts", "long"):
            caps = captions.from_segments(fx["segments"], fmt, captions.DEFAULT_TERMS)
            merged = sum(1 for t in bounds if any(any(w["e"] <= t + 0.12 and nx["s"] >= t - 0.12 for w, nx in zip(c["words"], c["words"][1:])) for c in caps))
            self.assertLessEqual(merged, 1, (fmt, texts(caps)))
            for want in ("이것도 들어갔어요.", "아, 아깝다.", "볼 때 맞았어요.", "다시 할게요.", "괜찮아요.", "좋아요.", "와, 이거죠."):
                self.assertIn(want, texts(caps), fmt)

    def test_owner_script_zero_gap(self):
        """검토: 소유자 대본을 쉼 0~0.08초로 받아써도 마침표·느낌표 뒤는 나눔 ('그리고 뒤에 말하면 혼난다.' | '시작!')."""
        for gap in (0.0, 0.08):
            t, ws = 0.0, []
            for k, w in enumerate("감독님, 요 세 마디만 하면 돼. 다른 말 하지 마, 순두시지 마, 감독님. 그리고 뒤에 말하면 혼난다. 시작!".split()):
                ws.append({"w": w, "s": round(t, 3), "e": round(t + 0.25, 3)})
                t += 0.25 + (gap if w[-1] in ",.!" else 0.02)
            segs = [{"start": ws[0]["s"], "end": ws[-1]["e"], "text": " ".join(w["w"] for w in ws), "words": ws}]
            self.assertEqual(texts(captions.from_segments(segs, "shorts", captions.DEFAULT_TERMS)),
                             ["감독님, 요 세 마디만 하면 돼.", "다른 말 하지 마,", "순두시지 마, 감독님.", "그리고 뒤에 말하면 혼난다.", "시작!"], gap)

    def test_no_forced_breaks_inside_speech(self):
        """검토: 꾸미는 말·이음말 뒤 · 주어(~가) · 이음꼴 ~아/~어 · 이름씨 '플레이어' · 이음꼴+요 · 말 머리 부르는 말은 쉬어도 혼자 떨어지지 않음."""
        def segs(spec):
            t, ws = 0.0, []
            for tok in spec.split():
                g = 0.02
                if "|" in tok:
                    tok, g = tok.split("|")
                    g = float(g)
                d = max(0.12, len(tok) * 0.07)
                ws.append({"w": tok, "s": round(t, 3), "e": round(t + d, 3)})
                t += d + g
            return ws
        for spec, bad in (("공을 받아|0.2 바로 슈팅", "공을 받아"), ("수비수가|0.35 따라오면 패스", "수비수가"), ("플레이어|0.2 위치를 봐", "플레이어"),
                          ("윙어|0.2 쪽으로 벌려", "윙어"), ("패스하고 그|1.27 자리에 서 있으면 왜 안 될까요?", "패스하고 그"),
                          ("그리고|0.8 이건 그냥 참고로만 들으세요.", "그리고"), ("여기서요|0.15 이렇게 딱 잡고요|0.15 돌아서요|0.15 슈팅", "여기서요"),
                          ("감독님,|0.25 이거 맞아요?", "감독님,"), ("민수야,|0.2 패스 줘", "민수야,"), ("감독님,|0.3 요 세 마디만 하면 돼.", "감독님,"),
                          ("Mr.|0.0 Kim 패스", "Mr."), ("음…|0.3 이건 좀 어렵네", "음…")):
            with self.subTest(spec=spec):
                self.assertNotIn(bad, texts(captions.segment(segs(spec), "shorts")))
                self.assertFalse(captions.utterance_breaks(segs(spec)), spec)
        for spec, want in (("고개 들어|0.2 이렇게 하면 돼", ["고개 들어", "이렇게 하면 돼"]), ("잡아|0.2 그렇지", ["잡아", "그렇지"]),
                           ("알겠니|0.3 네", ["알겠니", "네"]), ("순두시지 마,|0.3 감독님.", ["순두시지 마,", "감독님."]),
                           ("혼난다.|0.0 시작!", ["혼난다.", "시작!"]), ("알겠지?|0.0 네!", ["알겠지?", "네!"])):
            with self.subTest(spec=spec):
                self.assertEqual(texts(captions.segment(segs(spec), "shorts")), want)
        self.assertFalse(captions.merged_utterances("Mr. Kim 패스"))  # 단어 시각 없을 때도 줄임말은 문장 끝이 아님
        self.assertFalse(captions.merged_utterances("음… 이건 좀 어렵네"))

    def test_split_text_and_merged(self):
        parts = captions.split_text("혼난다. 시작!", 30.0, 31.2, "shorts")
        self.assertEqual(texts(parts), ["혼난다.", "시작!"])
        self.assertEqual((parts[0]["start"], parts[-1]["end"]), (30.0, 31.2))
        self.assertTrue(captions.merged_utterances("혼난다. 시작!"))
        self.assertFalse(captions.merged_utterances("공을 받고 돌아요"))
        self.assertFalse(captions.merged_utterances("대박."))
        ws = [{"s": 34.0, "e": 34.5}, {"s": 34.5, "e": 34.8}, {"s": 35.1, "e": 35.6}]
        self.assertTrue(captions.merged_utterances("순두시지 마, 감독님.", ws))
        self.assertFalse(captions.merged_utterances("순두시지 마, 감독님.", [{"s": 34.0, "e": 34.5}, {"s": 34.5, "e": 34.8}, {"s": 34.8, "e": 35.6}]))
        self.assertFalse(captions.needs_split("혼난다. 시작!", "shorts"))  # 길이 규칙은 그대로 (나누는 까닭은 merged_utterances)


def old_caps(segs, fmt):
    """예전 판(v2.21.1)이 만든 것과 같은 꼴: 말 경계 없이 chunk 만 (소유자 화면 '순두시지 마, 감독님.' · '혼난다. 시작!')."""
    ws = [w for s in segs for w in s["words"]]
    out = [editor._make_cap(c, fmt, []) for c in captions.chunk(ws, fmt)]
    for k, c in enumerate(out):
        c["id"] = f"o{k}"
    return out


class EditorResplitTest(unittest.TestCase):
    """editor.oneline_captions (D-387) · resplit_captions '자막 다시 나누기' (D-380) · merged_captions (열 때 안내)."""

    def test_old_captions_resplit(self):
        old = old_caps(build(OWNER), "shorts")
        self.assertIn("혼난다. 시작!", texts(old))
        self.assertGreaterEqual(editor.merged_captions(old), 2)
        src = {}
        new, n = editor.resplit_captions(old, TALL, None, terms=[], src_of=src)
        self.assertEqual(texts(new), OWNER_WANT)
        self.assertGreater(n, 0)
        self.assertEqual(editor.merged_captions(new), 0)
        self.assertTrue(all(c.get("wt") for c in new))  # 단어 시각 그대로
        ids = [c["id"] for c in new]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(set(src.values()) <= {c["id"] for c in old})
        again, n2 = editor.resplit_captions(new, TALL, None, terms=[])
        self.assertEqual((texts(again), n2), (texts(new), 0))  # 두 번 눌러도 같음
        self.assertEqual([(c["start"], c["end"]) for c in again], [(c["start"], c["end"]) for c in new])

    def test_resplit_keeps_others(self):
        old = old_caps(build(OWNER), "shorts")
        edited = [dict(c) for c in old]
        hand = next(c for c in edited if c["text"] == "혼난다. 시작!")
        hand["tm"] = 1  # 손으로 시간을 맞춘 자막은 그대로
        other = next(c for c in edited if c["text"].startswith("순두시지"))
        other["trk"] = "C2"  # 다른 트랙 자막은 그 트랙 안에서만
        new, _ = editor.resplit_captions(edited, TALL, [c["id"] for c in edited if c["text"] != "다른 말 하지 마,"], terms=[])
        self.assertIn("혼난다. 시작!", texts(new))
        self.assertIn("다른 말 하지 마,", texts(new))
        c2 = [c for c in new if c.get("trk") == "C2"]
        self.assertEqual(texts(c2), ["순두시지 마,", "감독님."])
        land, _ = editor.resplit_captions(old_caps(build(OWNER), "long"), LAND, None, terms=[])
        self.assertEqual(texts(land), OWNER_WANT)
        for c in land:  # 가로 영상 자막은 쇼츠 편집본에서 나눌 곳(sh)도 새 규칙 (쇼츠 한 줄 13글자 안이면 안 나눔)
            self.assertEqual(c.get("sh"), None, c)
        nowords = [{"id": "x", "start": 1.0, "end": 2.2, "text": "혼난다. 시작!"}]  # 단어 시각 없음 (손으로 쓴·SRT): 글 속 문장부호에서만
        self.assertEqual(texts(editor.resplit_captions(nowords, TALL, None, terms=[])[0]), ["혼난다.", "시작!"])

    def test_oneline_splits_short_merged(self):
        caps = [{"id": "e", "start": 30.0, "end": 31.2, "text": "혼난다. 시작!", "trk": "C2"},
                {"id": "k", "start": 32.0, "end": 33.0, "text": "공을 받고 돌아요"}]
        src = {}
        out, n = editor.oneline_captions(caps, TALL, terms=[], src_of=src)
        self.assertEqual((texts(out), n), (["혼난다.", "시작!", "공을 받고 돌아요"], 1))
        self.assertEqual([c.get("trk") for c in out[:2]], ["C2", "C2"])
        self.assertEqual(out[0]["id"], "e")
        self.assertEqual(src, {out[1]["id"]: "e"})
        again, n2 = editor.oneline_captions(out, TALL, terms=[])
        self.assertEqual((texts(again), n2), (texts(out), 0))

    def test_hand_captions_kept(self):
        """검토 (D-387): 손으로 맞추거나 쓴 자막(tm · SRT · Shift+T)과 손으로 나누거나 합친 자막(ms)은 '한 줄씩 나누기'·'다시 나누기'가
        따로 한 말 규칙으로 다시 나누지 않음 (길면 길이 규칙은 그대로) · 줄임말 'Mr.' 은 문장 끝이 아님."""
        caps = [{"id": "s", "start": 1.0, "end": 2.2, "text": "네. 알겠습니다.", "tm": 1},           # SRT·손으로 쓴 자막
                {"id": "m", "start": 3.0, "end": 4.0, "text": "Mr. Kim 패스"},                    # 줄임말
                {"id": "j", "start": 5.0, "end": 6.2, "text": "혼난다. 시작!", "ms": 1},          # Ctrl+J 로 합친 자막
                {"id": "l", "start": 7.0, "end": 12.0, "tm": 1, "text": "오늘은 왼발 인사이드로 공을 받아서 수비수를 등지고 돌아서는 동작을 연습해 볼게요."}]
        out, n = editor.oneline_captions(caps, TALL, terms=[])
        self.assertEqual(texts(out)[:3], ["네. 알겠습니다.", "Mr. Kim 패스", "혼난다. 시작!"])
        self.assertEqual(n, 1)  # 긴 tm 자막만 (길이 규칙)
        self.assertTrue(all(c.get("tm") for c in out[3:]))
        self.assertEqual(editor.merged_captions(caps), 0)  # 열 때 안내도 안 셈
        land, _ = editor.oneline_captions([dict(caps[2], sh=[], shn=2), dict(caps[2], id="j2", start=7.0, end=8.2)], LAND, terms=[])
        self.assertEqual([c.get("sh") or None for c in land], [None, None])  # 합친 자막은 쇼츠 한 줄에 들면 쇼츠에서도 한 자막
        # '자막 다시 나누기': ms 자막은 그대로 · 그 앞뒤와 잇지 않음
        old = old_caps(build(OWNER), "shorts")
        hand = next(c for c in old if c["text"] == "혼난다. 시작!")
        hand["ms"] = 1
        new, _ = editor.resplit_captions(old, TALL, None, terms=[])
        self.assertIn("혼난다. 시작!", texts(new))
        self.assertIn("그리고 뒤에 말하면", texts(new))  # ms 자막 앞에서 사슬이 끊겨 '혼난다.' 와 다시 묶지 않음

    def test_resplit_overlapping_speakers(self):
        """검토 (D-387): 같은 트랙에 시간이 겹친 두 자막(두 사람이 겹쳐 말함)은 낱말을 섞지 않음."""
        wt = lambda ws: [{"w": w, "s": a, "e": b} for w, a, b in ws]  # noqa: E731
        a = editor._make_cap({"start": 10.0, "end": 11.4, "text": "왼쪽으로 돌아서 슈팅해",
                              "words": wt([("왼쪽으로", 10.0, 10.5), ("돌아서", 10.5, 10.9), ("슈팅해", 10.95, 11.4)])}, "shorts", [])
        b = editor._make_cap({"start": 10.55, "end": 10.85, "text": "오른쪽!", "words": wt([("오른쪽!", 10.55, 10.85)])}, "shorts", [])
        a["id"], b["id"] = "a", "b"
        new, _ = editor.resplit_captions([a, b], TALL, None, terms=[])
        self.assertEqual(sorted(texts(new)), sorted(["왼쪽으로 돌아서 슈팅해", "오른쪽!"]), texts(new))

    def test_shorts_cuts_follow_new_rules(self):
        segs = build(OWNER, "one")
        ws = [w for s in segs for w in s["words"]]
        part = [w for w in ws if 3.5 <= w["s"] < 5.2]  # '순두시지 마, 감독님.'
        sh = editor._shorts_cuts(" ".join(w["w"] for w in part), part[0]["s"], part[-1]["e"], [], part)
        self.assertEqual(sh, [2])


class ResplitRouteTest(unittest.TestCase):
    """/api/edit/resplit '자막 다시 나누기' (실제 HTTP 서버로 · 받아쓰기 없는 영상이어도 자막 단어 시각만으로)."""

    def setUp(self):
        import app
        self.tmp = Path(tempfile.mkdtemp(prefix="자막 다시 나누기 "))
        self.work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": self.work, "VIDEOS": self.work / "videos", "ANALYSIS": self.work / "analysis", "OUT": self.work / "out"}
        for d in list(dirs.values()) + [self.work / "projects", self.work / "edit_media"]:
            d.mkdir(parents=True, exist_ok=True)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        self.patches += [mock.patch.object(editor, "PROJECTS", self.work / "projects"), mock.patch.object(editor, "ASSETS", self.work / "edit_media"),
                         mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log")]
        for p in self.patches:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_resplit_route(self):
        old = old_caps(build(OWNER), "shorts")
        code, j = self.post("/api/edit/resplit", {"name": "두 사람.mp4", "captions": old, "info": TALL, "ids": [c["id"] for c in old]})
        self.assertEqual(code, 200, j)
        self.assertEqual([c["text"] for c in j["captions"]], OWNER_WANT)
        self.assertGreater(j["changed"], 0)
        self.assertTrue(set(j["from"].values()) <= {c["id"] for c in old})
        code, j = self.post("/api/edit/resplit", {"name": "두 사람.mp4", "captions": old, "info": TALL, "ids": [old[0]["id"]]})  # 고른 것만
        self.assertEqual(code, 200)
        self.assertEqual([c["text"] for c in j["captions"]][1:], [c["text"] for c in old][1:])
        for bad in ({"name": "두 사람.mp4", "captions": "x", "info": TALL}, {"name": "두 사람.mp4", "captions": [], "ids": "x", "info": TALL},
                    {"name": "../밖.mp4", "captions": [], "info": TALL}):
            code, _ = self.post("/api/edit/resplit", bad)
            self.assertEqual(code, 400, bad)


@unittest.skipUnless(NODE, "node 가 없어요")
class EditorJsTest(unittest.TestCase):
    """editor.html 나누기·합치기 (D-384 · D-386 · D-388) — 가장 가까운 낱말 사이 · 같은 글 두 번 없음 · 단어 시각 나눠 가짐 · sh 나눠 가짐 · 글 고쳐도 단어 시각."""

    def run_js(self, body):
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        i = src.index("function capWords(")
        blk = src[i:src.index("/* 자막 → 타임라인", i)]
        self.assertNotIn("document", blk)
        prog = ("const FPS = 30, r6c = v => Math.round(v * 1e6) / 1e6, clamp = (v, a, b) => Math.min(b, Math.max(a, v));\n"
                "let nidN = 0; const nid = () => 'n' + (++nidN), CAPNEW = new Map(), otLockNote = '잠김';\n"
                "let OFF = new Set(), INEDIT = () => true; const capOffSet = () => OFF, capInEdit = () => INEDIT;\n"
                "const otCap = c => c && typeof c.trk === 'string' ? c.trk : 'C1', otLockOf = () => false;\n"
                "let P = { captions: [], sequences: [], info: { width: 1920, height: 1080 } };\n" + blk + "\n" + body)
        r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    W = [("감독님,", 12.00, 12.45), ("요", 12.75, 12.85), ("세", 12.90, 13.00), ("마디만", 13.00, 13.35), ("하면", 13.35, 13.60), ("돼.", 13.60, 13.85),
         ("다른", 14.35, 14.55), ("말", 14.55, 14.65), ("하지", 14.65, 14.95), ("마,", 14.95, 15.30)]

    def setup_js(self):
        return ("const W = " + json.dumps(self.W, ensure_ascii=False) + ";\n"
                "const mk = () => ({ id: 'cW', start: 12.0, end: 15.6, text: W.map(w => w[0]).join(' '), wt: wtOf(W.map(w => ({ s: w[1], e: w[2] }))), sh: [6], shn: 10, trk: 'C2', tm: 1 });\n")

    def test_split_nearest_word(self):
        out = self.run_js(self.setup_js() + """
const res = [];
for (const src of [14.35, 14.10, 13.85, 12.70, 14.40, 14.50, 15.55, 12.05]) {
  const c = mk(), p = capSplitPlan(c, src); P.captions = [c]; P.sequences = [{ capPos: { cW: { y: 0.3 } }, capOff: [] }];
  const b = capSplitDo(c, p);
  res.push({ src, a: c.text, b: b.text, c: p.c, wa: (capWords(c) || []).length, wb: (capWords(b) || []).length, sha: c.sh || null, shb: b.sh || null, shnb: b.shn ?? null,
             trk: [c.trk, b.trk], tm: [c.tm, b.tm], pos: P.sequences[0].capPos[b.id] || null });
}
const n = { id: 'n', start: 20, end: 23.6, text: W.map(w => w[0]).join(' ') }, pn = capSplitPlan(n, 22.35);
const one = capSplitPlan({ id: 'o', start: 1, end: 2, text: '대박.' }, 1.5);
const e = { id: 'e', start: 30, end: 31.2, text: '혼난다. 시작!' }, pe = capSplitPlan(e, 31.15);
process.stdout.write(JSON.stringify({ res, n: [pn.a, pn.b, pn.c], one: one.why, e: [pe.a, pe.b] }));""")
        want = {14.35: ("돼.", 14.35), 14.10: ("돼.", 14.10), 13.85: ("돼.", 13.85), 12.70: ("감독님,", 12.70), 14.40: ("돼.", 14.35),
                14.50: ("다른", 14.55), 15.55: ("하지", 14.95), 12.05: ("감독님,", 12.75)}
        for r in out["res"]:
            last, cut = want[r["src"]]
            self.assertEqual(r["a"].split()[-1], last, r)
            self.assertAlmostEqual(r["c"], cut, places=5)
            self.assertNotEqual(r["a"], r["b"])
            self.assertEqual(len(r["a"].split()) + len(r["b"].split()), 10)
            self.assertEqual((r["wa"], r["wb"]), (len(r["a"].split()), len(r["b"].split())))  # 단어 시각을 나눠 가짐
            self.assertEqual((r["trk"], r["tm"], r["pos"]), (["C2", "C2"], [1, 1], {"y": 0.3}))
        r = next(x for x in out["res"] if x["src"] == 14.35)
        self.assertEqual((r["sha"], r["shb"]), (None, None))  # sh=[6] 은 바로 나눈 자리 → 둘 다 한 덩어리
        r = next(x for x in out["res"] if x["src"] == 12.70)
        self.assertEqual((r["sha"], r["shb"], r["shnb"]), (None, [5], 9))  # 쇼츠 조각 경계는 낱말 번호를 옮겨 그대로
        self.assertEqual(out["n"][:2], ["감독님, 요 세 마디만 하면 돼.", "다른 말 하지 마,"])
        self.assertAlmostEqual(out["n"][2], 22.35, places=5)  # 단어 시각 없음: 시각은 재생 위치
        self.assertEqual(out["one"], "낱말이 하나라 나눌 수 없어요")
        self.assertEqual(out["e"], ["혼난다.", "시작!"])

    def test_merge_and_retext(self):
        out = self.run_js(self.setup_js() + """
const c = mk(); delete c.sh; delete c.shn; P.captions = [c];
const b = capSplitDo(c, capSplitPlan(c, 14.35)), r = capMergeDo(c);
const merged = { text: c.text, start: c.start, end: c.end, words: (capWords(c) || []).length, n: P.captions.length, sh: c.sh || null };
const x = { id: 'x', start: 1.0, end: 1.6, text: '혼난다.' }, y = { id: 'y', start: 1.6, end: 2.1, text: '시작!' }; P.captions = [x, y];
capMergeDo(x); const short = { text: x.text, sh: x.sh || null };
const l1 = { id: 'l1', start: 0, end: 2, text: '가나다라 마바사아 자차카' }, l2 = { id: 'l2', start: 2, end: 4, text: '타파하 가나다라 마바' }; P.captions = [l1, l2];
capMergeDo(l1); const long = { sh: l1.sh || null, shn: l1.shn ?? null };
const t = { id: 't', start: 12.0, end: 15.6, trk: 'C1', text: W.map(w => w[0]).join(' '), wt: wtOf(W.map(w => ({ s: w[1], e: w[2] }))), sh: [6], shn: 10 };
const base = capRetextBase(t); t.text = t.text.replace('요 ', '이 ').replace('마,', '마, 알겠지?'); capRetext(t, base);
const rt = capWords(t).map(w => [w.w, +w.s.toFixed(2), +w.e.toFixed(2)]);
const base2 = capRetextBase(t); t.text = '감독님, 다른 말 하지 마, 알겠지?'; capRetext(t, base2);
process.stdout.write(JSON.stringify({ merged, short, long, rt, sh: t.sh || null, shn: t.shn ?? null, rt2: capWords(t).map(w => w.w) }));""")
        self.assertEqual(out["merged"], {"text": " ".join(w[0] for w in self.W), "start": 12.0, "end": 15.6, "words": 10, "n": 1, "sh": [6]})  # 쇼츠 한 줄(13글자)에 안 들어 이음매는 쇼츠 조각 경계
        self.assertEqual(out["short"], {"text": "혼난다. 시작!", "sh": None})  # 합쳐도 쇼츠 한 줄에 들면 쇼츠에서도 한 자막
        self.assertEqual(out["long"], {"sh": [3], "shn": 6})              # 안 들면 이음매는 쇼츠에서 경계
        rt = out["rt"]
        self.assertEqual([w[0] for w in rt], ["감독님,", "이", "세", "마디만", "하면", "돼.", "다른", "말", "하지", "마,", "알겠지?"])
        for w in rt:
            o = next((x for x in self.W if x[0] == w[0]), None)
            if o:
                self.assertEqual((w[1], w[2]), (o[1], o[2]))  # 그대로인 낱말은 시각 그대로
        self.assertEqual(rt[1][1:], [12.75, 12.85])  # 바꾼 낱말은 그 자리 시간
        self.assertTrue(15.3 - 1e-6 <= rt[-1][1] and rt[-1][2] <= 15.6 + 1e-6)  # 더한 낱말은 끝 빈 곳
        self.assertEqual(out["rt2"], ["감독님,", "다른", "말", "하지", "마,", "알겠지?"])
        self.assertEqual((out["sh"], out["shn"]), ([1], 6))  # 쇼츠 조각 경계('다른' 앞)도 같은 낱말에

    def test_merge_rules_in_edit(self):
        """검토 (D-386): 다음 자막은 이 편집본에 걸친 것만 · 1.2초 넘게 떨어지면 force 일 때만 · 이 편집본에서 지운 자막(지금 자막도)은 안 합침 · 나누고 합친 자막은 ms."""
        out = self.run_js("""
const mk = () => [{ id: 'a', start: 37.0, end: 37.8, text: '대박.' }, { id: 'z', start: 45.0, end: 46.2, text: '다음 영상에서 만나요.' },
                  { id: 'e', start: 30.0, end: 31.2, text: '혼난다. 시작!' }, { id: 'v', start: 34.0, end: 35.6, text: '순두시지 마, 감독님.' }];
P.captions = mk(); INEDIT = c => c.start < 40;  // 이 편집본: 원본 10~40초
const outside = capMergeDo(P.captions[0]);
P.captions = mk(); INEDIT = () => true;
const far = capMergeDo(P.captions.find(c => c.id === 'e')), n1 = P.captions.length;
const forced = capMergeDo(P.captions.find(c => c.id === 'e'), true), e = P.captions.find(c => c.id === 'e');
P.captions = mk(); OFF = new Set(['v']);
const offCur = capMergeDo(P.captions.find(c => c.id === 'v')), offNext = capMergeDo(P.captions.find(c => c.id === 'e'), true);
OFF = new Set(); P.captions = [{ id: 'x', start: 1.0, end: 1.6, text: '혼난다.' }, { id: 'y', start: 1.6, end: 2.1, text: '시작!' }];
capMergeDo(P.captions[0]); const ms1 = P.captions[0].ms || 0;
const s = { id: 's', start: 0, end: 2, text: '오늘은 왼발 인사이드로 받아볼게요.' }; P.captions = [s];
const t = capSplitDo(s, capSplitPlan(s, 1.0));
process.stdout.write(JSON.stringify({ outside: outside.why, far: [far.why, far.far, n1], forced: [forced.why || '', e.text, e.end, e.ms], offCur: offCur.why, offNext: offNext.why, ms1, ms2: [s.ms, t.ms] }));""")
        self.assertIn("다음 자막이 없어요", out["outside"])  # 편집본 밖 '다음 영상에서 만나요.'(45초)와 합치지 않음
        self.assertTrue(out["far"][0] and out["far"][1] > 2.7 and out["far"][2] == 4, out["far"])  # 2.8초 떨어짐 → 그냥은 안 합침
        self.assertEqual(out["forced"][:2], ["", "혼난다. 시작! 순두시지 마, 감독님."])  # 물어본 뒤(force)에는 합침
        self.assertEqual(out["forced"][3], 1)
        self.assertIn("이 편집본에서 지운 자막", out["offCur"])  # 지금 자막이 이 편집본에서 지운 자막
        self.assertIn("다음 자막은 이 편집본에서 지운 자막", out["offNext"])
        self.assertEqual((out["ms1"], out["ms2"]), (1, [1, 1]))


if __name__ == "__main__":
    unittest.main()
