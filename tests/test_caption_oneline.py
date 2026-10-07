"""자막 한 줄씩 나누기 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_caption_oneline

captions.chunk·split_text·from_segments(한 줄 규칙 · 단어 시각 없을 때 글자 수대로 · 용어 안 나눔 · 자투리 합치기)
· editor.oneline_captions(고친 글 그대로 · 다른 값 유지 · 두 번 눌러도 같음) · 내보내기 ASS 한 줄 · 노래방 \\kf
· 쇼츠 편집본 나누기와 글자 크기 맞추기가 편집실 미리보기(editor.html 의 JS, node 로 실행)와 같은지 · /api/edit/oneline·open."""
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
# 소유자 화면에서 3줄로 뭉쳐 보이던 자막 (예전 받아쓰기 · 단어 시각 없음)
OWNER = ["아까 처음에 제가 몸풀 때 말씀드렸던 게 집중을 해야 되고 상대를 그려야 되고",
         "어쨌든 이제 여러분들 제가 오늘 우리 선생님들 만나 뵈면서 다시 한 번 강조할 때는 퍼스트 터치"]


def chars(t):
    return len(re.sub(r"\s+", "", t))


def W(w, s, e):
    return {"w": w, "s": s, "e": e}


def spoken(text, t0=0.0, cps=6.5, gap=0.06):
    """글을 초당 cps 글자 빠르기로 말한 단어 시각."""
    out, t = [], t0
    for w in text.split():
        d = max(0.12, chars(w) / cps)
        out.append(W(w, round(t, 2), round(t + d, 2)))
        t += d + gap
    return out


class RulesTest(unittest.TestCase):
    """한 줄 규칙: 글자 수·길이 한도, 낱말 경계, 말끝에서 끊기, 용어, 자투리."""

    def check_parts(self, parts, fmt, text):
        mc, md = captions.LIMITS[fmt]
        self.assertEqual(" ".join(p["text"] for p in parts).split(), text.split())  # 낱말 그대로, 순서대로
        for p in parts:
            self.assertNotIn("\n", p["text"])
            if len(p["text"].split()) > 1:
                self.assertLessEqual(chars(p["text"]), mc, p)
        for a, b in zip(parts, parts[1:]):
            self.assertLessEqual(a["end"], b["start"] + 1e-6)
            self.assertLess(a["start"], b["start"])

    def test_limits_are_one_short_line(self):
        self.assertEqual(captions.LIMITS["shorts"], (13, 2.2))
        self.assertEqual(captions.LIMITS["long"], (17, 3.0))
        self.assertFalse(hasattr(captions, "LINE"))  # 두 줄 규칙은 없어짐

    def test_word_timed_sentences_one_line(self):
        for text in OWNER:
            ws = spoken(text, 3.0)
            for fmt in ("shorts", "long"):
                with self.subTest(fmt=fmt, text=text[:10]):
                    parts = captions.chunk(ws, fmt, terms=captions.DEFAULT_TERMS)
                    self.check_parts(parts, fmt, text)
                    md = captions.LIMITS[fmt][1]
                    for p in parts:
                        self.assertLessEqual(p["end"] - p["start"], md + 1e-9, p)
                    self.assertGreaterEqual(len(parts), 3 if fmt == "shorts" else 2)

    def test_prefers_natural_endings(self):
        text = OWNER[0]
        parts = captions.split_text(text, 0.0, chars(text) / 6.5, "long", captions.DEFAULT_TERMS)
        # 말끝 '~되고' 뒤에서 끊음 (조사 '~을' 뒤보다)
        self.assertEqual([p["text"] for p in parts], ["아까 처음에 제가 몸풀 때 말씀드렸던 게", "집중을 해야 되고 상대를 그려야 되고"])
        ws = spoken("공을 받을 때는 몸을 열어서 앞을 보면서 받아야 다음 동작이 빨라지는데 이게 생각보다 어려워요")
        cuts = [p["words"][-1]["w"] for p in captions.chunk(ws, "shorts")[:-1]]
        self.assertTrue(all(re.search(r"(요|고|서|데|다|는|면|을|를|이|가|게|에)$", c) for c in cuts), cuts)

    def test_no_words_split_at_spaces_proportional_time(self):
        text = OWNER[1]
        start, end = 12.0, 12.0 + chars(text) / 6.0
        for fmt in ("shorts", "long"):
            with self.subTest(fmt):
                parts = captions.split_text(text, start, end, fmt, captions.DEFAULT_TERMS)
                self.check_parts(parts, fmt, text)
                self.assertNotIn("words", parts[0])  # 시각을 지어내 단어 시각으로 남기지 않음
                self.assertEqual((parts[0]["start"], parts[-1]["end"]), (start, end))  # 원래 자막 시작·끝 그대로
                for a, b in zip(parts, parts[1:]):
                    self.assertAlmostEqual(a["end"], b["start"], places=6)  # 빈틈 없이 이어짐
                tot = chars(text)
                for p in parts[:-1]:  # 길이는 글자 수에 비례 (깜빡임 막기로 늘린 것 빼고)
                    want = (end - start) * chars(p["text"]) / tot
                    self.assertLess(abs((p["end"] - p["start"]) - want), 0.06, p)

    def test_no_words_uses_silences(self):
        # 앞 말 뒤에 1초 쉼 → 글자 수만으로는 '공을 받고 돌아서' 중간에 끊길 시간이지만, 조용한 곳에서 끊음
        text = "오늘은 공을 받고 바로 패스해요 이번에는 돌아서 슈팅까지 해 볼게요"
        sil = [{"start": 3.0, "end": 4.0}]
        parts = captions.split_text(text, 0.0, 7.0, "long", silences=sil)
        self.assertIn("오늘은 공을 받고 바로 패스해요", [p["text"] for p in parts])
        later = next(p for p in parts if p["text"].startswith("이번에는"))
        self.assertGreaterEqual(later["start"], 4.0 - 1e-6)  # 쉼이 끝난 뒤에 나옴
        ws = captions.even_words(text.split(), 0.0, 7.0, sil)
        self.assertTrue(all(not (3.0 < (w["s"] + w["e"]) / 2 < 4.0) for w in ws))  # 조용한 곳엔 글자가 없음
        self.assertEqual(captions._speech(0.0, 2.0, [{"start": 0.1, "end": 1.9}]), [(0.0, 2.0)])  # 말소리가 너무 적으면 무시

    def test_terms_never_split(self):
        pre = "그래서 오늘 정말 중요한 것 다시 한번".split()
        for k in range(len(pre) + 1):
            text = " ".join(pre[:k] + ["퍼스트", "터치를"] + pre[k:] + ["꼭", "기억해", "주세요"])
            for fmt in ("shorts", "long"):
                for parts in (captions.split_text(text, 0.0, chars(text) / 6.0, fmt, captions.DEFAULT_TERMS),
                              captions.chunk(spoken(text), fmt, terms=captions.DEFAULT_TERMS)):
                    with self.subTest(k=k, fmt=fmt):
                        self.assertTrue(any("퍼스트 터치" in p["text"] for p in parts), [p["text"] for p in parts])

    def test_tiny_tail_and_orphan_merged(self):
        ws = [W("빠르게", 0.0, 0.4), W("돌아서", 0.45, 0.9), W("패스해", 0.95, 1.5), W("요.", 1.52, 1.72)]
        self.assertEqual([c["text"] for c in captions.chunk(ws, "shorts")], ["빠르게 돌아서 패스해 요."])
        text = "발 안쪽으로 부드럽게 공을 받아서 앞으로 치고 나가요 네"
        for fmt in ("shorts", "long"):
            parts = captions.split_text(text, 0.0, chars(text) / 6.5, fmt)
            self.assertNotEqual(parts[-1]["text"], "네", fmt)  # 낱말 하나짜리 짧은 끝은 앞에 붙음
            for p in parts:
                self.assertGreaterEqual(p["end"] - p["start"], captions.MIN_DUR - 1e-6, p)

    def test_needs_split(self):
        self.assertTrue(captions.needs_split("공을 받고\n돌아요"))
        self.assertTrue(captions.needs_split(OWNER[0], "long"))
        self.assertFalse(captions.needs_split("공을 받고 돌아요", "long"))
        self.assertFalse(captions.needs_split("가나다라마바사아자차카타파하가나다라마", "long"))  # 낱말 하나는 못 나눔
        self.assertTrue(captions.needs_split("오늘은 퍼스트 터치를 배워볼게요", "shorts"))  # 14글자 > 13
        self.assertFalse(captions.needs_split("오늘은 퍼스트 터치를 배워볼게요", "long"))

    def test_old_transcript_long_segment_split(self):
        segs = [{"start": 1.0, "end": 9.0, "text": OWNER[0]}, {"start": 9.5, "end": 16.0, "text": OWNER[1]}]
        caps = captions.from_segments(segs, "long", captions.DEFAULT_TERMS)
        self.assertGreater(len(caps), 2)
        self.assertEqual(" ".join(c["text"] for c in caps).split(), " ".join(OWNER).split())
        self.assertTrue(all(chars(c["text"]) <= 17 and "\n" not in c["text"] for c in caps))
        self.assertTrue(all(c["end"] <= 9.0 + 1e-6 for c in caps if c["start"] < 9.0))  # 구간 밖으로 안 넘침


class OnelineCaptionsTest(unittest.TestCase):
    """editor.oneline_captions — 편집실 '자막 한 줄씩 나누기'."""
    LAND, TALL = {"width": 1920, "height": 1080}, {"width": 1080, "height": 1920}

    def test_user_edited_text_is_split_and_fields_kept(self):
        orig = spoken("공을 받을 때는 몸을 열어서 앞을 보면서 받아요", 2.0)
        cap = {"id": "c1", "start": 2.0, "end": orig[-1]["e"], "text": "공을 받을 때는\n몸을 활짝 열고 앞을 보면서\n받아요",  # 사용자가 고친 글
               "wt": editor._wt(orig), "y": 0.7, "note": "내 표시"}
        out, n = editor.oneline_captions([cap], self.LAND, terms=())
        self.assertEqual(n, 1)
        self.assertEqual(" ".join(c["text"] for c in out), "공을 받을 때는 몸을 활짝 열고 앞을 보면서 받아요")  # 고친 글 그대로
        self.assertEqual(out[0]["id"], "c1")
        self.assertEqual(len({c["id"] for c in out}), len(out))
        for c in out:
            self.assertEqual((c["y"], c["note"]), (0.7, "내 표시"))  # 다른 값은 그대로
            self.assertNotIn("\n", c["text"])
            self.assertNotIn("wt", c)  # 고친 글은 낱말 수가 달라 단어 시각을 안 씀
        self.assertEqual((out[0]["start"], out[-1]["end"]), (cap["start"], round(cap["end"], 3)))

    def test_word_times_used_when_text_matches(self):
        text = OWNER[0]
        ws = spoken(text, 5.0)
        cap = {"id": "a", "start": 5.0, "end": ws[-1]["e"], "text": text, "wt": editor._wt(ws)}
        out, n = editor.oneline_captions([cap], self.TALL, terms=captions.DEFAULT_TERMS)
        self.assertEqual(n, 1)
        k = 0
        for c in out:
            got = editor._cap_words(c)
            self.assertIsNotNone(got, c)  # 나눈 자막마다 단어 시각이 이어짐 (노래방 \kf)
            for w in got:
                self.assertEqual((w["w"], w["s"], w["e"]), (ws[k]["w"], ws[k]["s"], ws[k]["e"]))
                k += 1
            if c is not out[0]:
                self.assertEqual(c["start"], got[0]["s"])  # 실제로 말한 때에 시작
        self.assertEqual(k, len(ws))

    def test_idempotent_and_short_captions_untouched(self):
        caps = [{"id": "s", "start": 0.0, "end": 1.0, "text": "짧은 자막"},
                {"id": "l", "start": 1.0, "end": 9.0, "text": OWNER[0]}]
        once, n1 = editor.oneline_captions(caps, self.TALL, terms=())
        self.assertEqual(once[0], caps[0])
        twice, n2 = editor.oneline_captions(json.loads(json.dumps(once)), self.TALL, terms=())
        self.assertEqual((n1, n2), (1, 0))
        self.assertEqual(twice, once)
        self.assertEqual(editor.long_captions(caps, self.TALL), 1)
        self.assertEqual(editor.long_captions(once, self.TALL), 0)

    def test_landscape_gets_shorts_split_points(self):
        out, _ = editor.oneline_captions([{"id": "x", "start": 0.0, "end": 8.0, "text": OWNER[1]}], self.LAND, terms=captions.DEFAULT_TERMS)
        self.assertTrue(all(chars(c["text"]) <= 17 for c in out))
        with_sh = [c for c in out if c.get("sh")]
        self.assertTrue(with_sh)
        for c in with_sh:
            self.assertEqual(c["shn"], len(c["text"].split()))
            parts = editor._shorts_parts(c)
            self.assertGreater(len(parts), 1)
            self.assertTrue(all(chars(p["text"]) <= 13 for p, _ in parts))
            self.assertEqual(" ".join(p["text"] for p, _ in parts), c["text"])
        edited = dict(with_sh[0], text=with_sh[0]["text"] + " 추가")  # 낱말 수가 바뀌면 나누지 않음
        self.assertEqual(len(editor._shorts_parts(edited)), 1)


class ExportTest(unittest.TestCase):
    """내보내기: 자막마다 한 줄 · 노래방 · 쇼츠 글자 크기 맞추기."""

    def seq(self, caps, fmt, style=None):
        st = dict(style or (editor.SHORTS_STYLE if fmt == "shorts" else editor.LONG_STYLE))
        v = {"id": "v", "track": "V1", "media": "main", "start": 0.0, "in": 0.0, "out": 60.0, "speed": 1.0}
        return {"format": fmt, "captionStyle": st, "titles": [], "shapes": [], "items": [v], "captions": caps, "captionsOn": True}

    def dialogues(self, proj, W, H):
        return [ln.split(",", 9) for ln in editor.build_ass(proj, W, H).splitlines() if ln.startswith("Dialogue: 1,") and ",Cap," in ln]

    def old_caps(self):
        return [{"id": "a", "start": 1.0, "end": 9.0, "text": OWNER[0]}, {"id": "b", "start": 9.5, "end": 17.0, "text": OWNER[1]}]

    def test_ass_one_line_per_caption(self):
        before = self.dialogues(self.seq(self.old_caps(), "shorts"), 1080, 1920)
        self.assertEqual(len(before), 2)  # 예전: 긴 자막 두 개 (화면에서 여러 줄로 감김)
        for info, fmt, W, H in ((OnelineCaptionsTest.LAND, "long", 1920, 1080), (OnelineCaptionsTest.LAND, "shorts", 1080, 1920),
                                (OnelineCaptionsTest.TALL, "shorts", 1080, 1920)):
            with self.subTest(info=info, fmt=fmt):
                caps, _ = editor.oneline_captions(self.old_caps(), info, terms=captions.DEFAULT_TERMS)
                proj = self.seq(caps, fmt)
                tl = editor.timeline_captions(proj)
                d = self.dialogues(proj, W, H)
                self.assertEqual(len(d), len(tl))
                self.assertGreater(len(d), 4)
                lim = captions.LIMITS[fmt][0]
                for f, c in zip(d, tl):
                    self.assertNotIn(r"\N", f[9])
                    self.assertLessEqual(chars(c["text"]), lim, c)
                    self.assertIsNone(re.search(r"\\fs\d", f[9]))  # 기본 크기면 줄일 필요 없음
                srt = editor._srt(tl)
                self.assertEqual(srt.count("-->"), len(tl))

    def test_karaoke_after_split_follows_words(self):
        ws = spoken(OWNER[0], 2.0)
        caps, _ = editor.oneline_captions([{"id": "a", "start": 2.0, "end": ws[-1]["e"], "text": OWNER[0], "wt": editor._wt(ws)}],
                                          OnelineCaptionsTest.LAND, terms=())
        for fmt, W, H in (("long", 1920, 1080), ("shorts", 1080, 1920)):
            st = dict(editor.SHORTS_STYLE if fmt == "shorts" else editor.LONG_STYLE, effect="karaoke")
            proj = self.seq(caps, fmt, st)
            tl = editor.timeline_captions(proj)
            d = self.dialogues(proj, W, H)
            self.assertEqual(len(d), len(tl))
            for f, c in zip(d, tl):
                kf = [int(x) for x in re.findall(r"\\kf(\d+)", f[9])]
                self.assertEqual(len(kf), len(c["text"].split()))
                self.assertLessEqual(abs(sum(kf) - (c["end"] - c["start"]) * 100), 2, f[9])
                self.assertIsNotNone(c.get("words"))
                acc = 0
                for k, w in enumerate(c["words"][1:], 1):
                    acc += kf[k - 1]
                    self.assertLessEqual(abs(acc - (w["s"] - c["start"]) * 100), 1.01, f[9])

    def test_too_wide_line_shrinks_in_shorts(self):
        st = dict(editor.SHORTS_STYLE)
        self.assertEqual(editor.cap_fit("가" * 13, st, 1080), 1.0)  # 기본 크기(72)에 13글자는 그대로
        big = dict(st, size=96)
        k = editor.cap_fit("어쨌든 이제 여러분들 제가", big, 1080)
        self.assertTrue(0.7 <= k < 1.0, k)
        need = sum(editor._em(ch) for ch in "어쨌든 이제 여러분들 제가") * 96 * k + 2 * big["strokeW"]
        self.assertLessEqual(need, 0.9 * 1080)  # 줄인 뒤엔 1080 화면 90% 안
        line = self.dialogues(self.seq([{"id": "a", "start": 0, "end": 2, "text": "어쨌든 이제 여러분들 제가"}], "shorts", big), 1080, 1920)[0][9]
        self.assertIn(rf"\fs{round(96 * k * editor.FONT_K)}", line)
        self.assertEqual(editor.cap_fit(OWNER[0], st, 1080), 1.0)  # 너무 긴 예전 자막은 줄이지 않고 줄바꿈 (예전과 같게)
        self.assertEqual(editor.cap_fit("가나다", dict(st, size=600), 1080), 1.0)  # 0.7배보다 더 줄여야 하면 그대로
        self.assertLess(editor.cap_fit("가나다라마바사아", dict(st, size=72, x=0.8), 1080), 1.0)  # 오른쪽에 치우치면 남은 폭 기준


def _js_funcs():
    """editor.html 에서 미리보기 함수(capWords·capParts·emOf·capFit)만 꺼냄."""
    src = (ROOT / "editor.html").read_text(encoding="utf-8")
    out = []
    for name in ("function capWords(", "function capParts(", "const emOf =", "function capFit("):
        i = src.index(name)
        j = src.index("\n}", i) + 2 if name.startswith("function") else src.index("\n", i)
        out.append(src[i:j])
    return "\n".join(out)


@unittest.skipUnless(NODE, "node 가 없어요")
class PreviewParityTest(unittest.TestCase):
    """편집실 미리보기(editor.html JS)와 내보내기(editor.py)가 같은 자막·같은 글자 크기인지 — node 로 JS 를 그대로 실행."""

    def run_js(self, caps, fits):
        prog = _js_funcs() + "\nconst inp = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
            "process.stdout.write(JSON.stringify({parts: inp.caps.map(c => capParts(c).map(p => [p.start, p.end, p.text, p.words ? p.words.map(w => +w.s) : null])),\n" \
            "  fits: inp.fits.map(f => capFit(f[0], f[1], f[2]))}));"
        r = subprocess.run([NODE, "-e", prog], input=json.dumps({"caps": caps, "fits": fits}), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_shorts_split_and_fit_match_export(self):
        segs = [{"start": 1.0, "end": 9.0, "text": OWNER[0]}, {"start": 9.5, "end": 16.0, "text": OWNER[1]},
                {"start": 16.5, "end": 21.0, "text": "오늘은 퍼스트 터치를 정확하게 하는 방법을 알려드릴게요."}]
        words = [dict(s, words=spoken(s["text"], s["start"])) for s in segs]
        caps = []
        for sg in (segs, words):
            with mock.patch.object(core, "dict_path", return_value=Path(tempfile.gettempdir()) / "없는 사전.json"):
                caps += editor._captions_of(sg, {"width": 1920, "height": 1080})
        caps += [{"id": "e", "start": 0, "end": 3, "text": "고친 글 이라서 낱말 수가 다른 자막", "sh": [2], "shn": 3},  # 낱말 수가 달라짐
                 {"id": "f", "start": 0, "end": 3, "text": "예전 프로젝트 자막 한 줄로", "sh": [2]}]  # 단어 시각도 shn 도 없음
        self.assertTrue(sum(1 for c in caps if c.get("sh")) >= 4)
        styles = [editor.SHORTS_STYLE, dict(editor.SHORTS_STYLE, size=96), dict(editor.LONG_STYLE, size=130),
                  dict(editor.SHORTS_STYLE, size=90, align="left", x=0.1), dict(editor.SHORTS_STYLE, x=0.8, strokeW=0)]
        texts = [c["text"] for c in caps] + ["ABC abc 123 퍼스트 터치!", "줄바꿈\n있는 자막입니다 길게"]
        fits = [[t, st, W] for t in texts for st in styles for W in (1080, 1920)]
        js = self.run_js(caps, fits)
        for c, got in zip(caps, js["parts"]):
            want = [[p["start"], p["end"], p["text"], [w["s"] for w in ws] if ws else None] for p, ws in editor._shorts_parts(c)]
            self.assertEqual(len(got), len(want), c)
            for g, w in zip(got, want):
                self.assertAlmostEqual(g[0], w[0], places=6)
                self.assertAlmostEqual(g[1], w[1], places=6)
                self.assertEqual(g[2], w[2])
                self.assertEqual(g[3] is None, w[3] is None)
                if w[3]:
                    for a, b in zip(g[3], w[3]):
                        self.assertAlmostEqual(a, b, places=6)
        self.assertEqual(js["fits"], [editor.cap_fit(t, st, W) for t, st, W in fits])
        self.assertTrue(any(k < 1 for k in js["fits"]))


class RouteTest(unittest.TestCase):
    """/api/edit/oneline · /api/edit/open 의 capLong (실제 HTTP 서버로)."""

    def setUp(self):
        import app
        self.tmp = Path(tempfile.mkdtemp(prefix="한 줄 자막 "))
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

    def test_oneline_route(self):
        caps = [{"id": "a", "start": 0.0, "end": 8.0, "text": OWNER[0]}, {"id": "b", "start": 8.5, "end": 9.5, "text": "네"}]
        code, j = self.post("/api/edit/oneline", {"name": "강의.mp4", "captions": caps, "info": {"width": 1080, "height": 1920}})
        self.assertEqual((code, j["split"]), (200, 1))
        self.assertTrue(all(chars(c["text"]) <= 13 for c in j["captions"]))
        self.assertEqual(j["captions"][-1], caps[-1])
        code, j = self.post("/api/edit/oneline", {"name": "강의.mp4", "captions": "x", "info": {}})
        self.assertEqual(code, 400)
        code, j = self.post("/api/edit/oneline", {"name": "../밖.mp4", "captions": [], "info": {}})
        self.assertEqual(code, 400)

    def test_open_reports_long_captions(self):
        name = "예전 강의.mp4"
        r = core.run([core.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=10:d=20",
                      "-f", "lavfi", "-i", "sine=f=440:sample_rate=16000:d=20", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", str(core.VIDEOS / name)])
        self.assertEqual(r.returncode, 0, r.stderr)
        proj = editor.load_project(name)  # 받아쓰기 없음 → 자막 없음
        proj["captions"] = [{"id": str(k), "start": 2.0 * k, "end": 2.0 * k + 1.9, "text": OWNER[k % 2]} for k in range(5)]
        editor.save_project(name, proj)
        with urllib.request.urlopen(self.base + "/api/edit/open?name=" + urllib.request.quote(name), timeout=120) as resp:
            j = json.loads(resp.read())
        self.assertEqual(j["capLong"], 5)


if __name__ == "__main__":
    unittest.main()
