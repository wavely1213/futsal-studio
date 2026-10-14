"""편집실 여러 개 고르기 · 자막 지우기는 이 편집본에서만 (D-340~D-344) — 저장소 폴더에서 python3 -m unittest tests.test_multi_select

- 편집본의 '지운 자막'(capOff · D-344)은 내보내기 자막(editor.timeline_captions → ASS·SRT·XML·올리기 키트)에서 빠지고 다른 편집본에는 그대로 (보고서 S43:
  예전에는 자막을 지우면 자막 글(모든 편집본 공용)에서 지워져 모든 편집본에서 사라졌다)
- editor.html capsTL(미리보기)이 같은 규칙인지 node 로 견줌 (test_caption_follow_audio 와 같은 방법) · 틀린 capOff(목록이 아님·글자 아닌 id)는 무시
- editor.html: 여러 개 고르기(selOvs · groupDrag · nudgeMulti · deleteMulti · copyOvs/pasteOvs · moveTrackMulti) 로 키·메뉴가 이어짐 (글로 확인)
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import editor  # noqa: E402
from tests.test_caption_follow_audio import CAPS, MULTI, NODE, OT3, broll, lcut, proj  # noqa: E402


def tl(q):
    return [(round(c["start"], 4), round(c["end"], 4), c["text"], c.get("trk", "C1")) for c in editor.timeline_captions(q)]


class CapOffTest(unittest.TestCase):
    def test_capoff_drops_only_that_caption(self):
        base = proj(lcut())
        full = tl(base)
        off = dict(base, capOff=["c2"])
        got = tl(off)
        self.assertTrue(any(t == "디딤발은 공 옆으로" for _, _, t, _ in full))
        self.assertFalse(any(t == "디딤발은 공 옆으로" for _, _, t, _ in got), "이 편집본에서 지운 자막은 내보내기에서 빠짐")
        self.assertEqual([x for x in full if x[2] != "디딤발은 공 옆으로"], got, "다른 자막은 그대로 (시간·트랙도)")
        self.assertEqual(tl(base), full, "다른 편집본(capOff 없음)은 그대로")

    def test_capoff_multi_tracks_and_shorts(self):
        q = dict(proj(lcut(), caps=MULTI), otracks=OT3, capOff=["m2", "c4"])
        got = tl(q)
        self.assertNotIn("보조 자막", [t for _, _, t, _ in got])
        self.assertNotIn("그제서야 어디로 갈지", [t for _, _, t, _ in got])
        self.assertIn("셋째 줄", [t for _, _, t, _ in got])
        s = dict(proj(lcut(), fmt="shorts"), capOff=["c1"])
        self.assertFalse(any("공을" in t for _, _, t, _ in tl(s)), "쇼츠형으로 나눈 조각도 모두 빠짐")

    def test_bad_capoff_ignored(self):
        base = proj(broll())
        full = tl(base)
        for bad in ("c1", {"c1": 1}, [1, None], None, 5):
            self.assertEqual(tl(dict(base, capOff=bad)), full, repr(bad))

    def test_ass_follows_capoff(self):
        q = dict(proj(lcut()), captionsOn=True, captionStyle={"weight": "Bold", "size": 64, "fill": "#FFFFFF", "stroke": "#000000", "strokeW": 6, "bgOn": False,
                                                               "bg": "#000000", "bgOpacity": 0.6, "y": 0.9, "effect": "none", "highlight": "#FFD400"},
                 titles=[], shapes=[], tactics=[], layout={"mode": "fill"})
        a1 = editor.build_ass(q, 1920, 1080)
        a2 = editor.build_ass(dict(q, capOff=["c2"]), 1920, 1080)
        self.assertIn("디딤발은", a1)
        self.assertNotIn("디딤발은", a2)
        self.assertIn("공을 먼저 보세요", a2)


@unittest.skipUnless(NODE, "node 가 없어요")
class CapOffParityTest(unittest.TestCase):
    """미리보기 editor.html capsTL 도 capOff 를 같은 규칙으로 뺌 (node 로 그대로 실행 · test_caption_follow_audio.PreviewParityTest 와 같은 틀)."""

    def run_js(self, cases):
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        out = []
        for name in ("const FPS =", "const EPS =", "const sp =", "const iEnd =", "const iLen =", "const mt =", "const tlOf =", "const aTracks ="):
            i = src.index(name)
            out.append(src[i:src.index("\n", i)])
        for a in ("function mergeIv(", "function minusIv("):
            i = src.index(a)
            out.append(src[i:src.index("\n}\n", i) + 2])
        i = src.index("function capWords(")
        out.append(src[i:src.index("// 한 줄 자막이 화면 밖으로", i)])
        prog = "let S, P, ver = 0;\nconst total = () => Math.max(0, ...S.items.map(iEnd));\n" + "\n".join(out) + """
const inp = JSON.parse(require('fs').readFileSync(0, 'utf8')), res = [];
for (const c of inp) {
  S = { items: c.items, tracks: c.tracks, format: c.format, otracks: c.otracks, capOff: c.capOff }; P = { captions: c.captions }; ver++;
  res.push(capsTL().map(x => [x.s, x.e, x.text, x.trk]));
}
process.stdout.write(JSON.stringify(res));"""
        r = subprocess.run([NODE, "-e", prog], input=json.dumps(cases), capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_parity(self):
        cases = [dict(proj(lcut()), capOff=["c2"]), dict(proj(lcut(), fmt="shorts"), capOff=["c1", "c5"]),
                 dict(proj(lcut(), caps=MULTI), otracks=OT3, capOff=["m2"]), dict(proj(broll()), capOff=[]), proj(lcut())]
        js = self.run_js(cases)
        for c, j in zip(cases, js):
            self.assertEqual(tl(c), [(round(a, 4), round(b, 4), t, k) for a, b, t, k in j])
        self.assertNotIn("디딤발은 공 옆으로", [x[2] for x in js[0]])
        self.assertIn("디딤발은 공 옆으로", [x[2] for x in js[4]])


class EditorHtmlWiringTest(unittest.TestCase):
    """키·메뉴·패널이 여러 개 고르기 쪽으로 이어졌는지 (글로 확인 — 실제 동작은 ppalign_e2e/multi_test.py)."""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "editor.html").read_text(encoding="utf-8")

    def fn(self, name):
        i = self.src.index(f"function {name}(")
        return self.src[i:self.src.index("\n}\n", i)]

    def test_routes(self):
        for name, call in (("nudge", "nudgeMulti"), ("deleteSel", "deleteMulti"), ("moveTrackSel", "moveTrackMulti"), ("splitSel", "splitMulti"),
                           ("dupSel", "dupMulti"), ("selMenu", "multiMenu"), ("pvNudge", "pvNudgeMulti"), ("renderFx", "renderMultiPanel"), ("copySel", "copyOvs"),
                           ("pasteAt", "pasteOvs")):
            self.assertIn(call, self.fn(name), name)
        self.assertRegex(self.src, r'mod && k === "a"\) \{ e\.preventDefault\(\); selectAll\(\)')
        self.assertIn("multiSel()", self.fn("selSetEdge"), "[ ] 는 하나만")

    def test_caption_delete_is_per_sequence(self):
        body = self.fn("deleteGfx")
        self.assertIn("capDelete(", body)
        self.assertNotRegex(body, r"P\.captions = P\.captions\.filter", "예전처럼 자막 글에서 바로 지우지 않음")
        self.assertIn("capShownIn(q, c)", self.fn("capDelete"))
        self.assertTrue(re.search(r"S\.capOff", self.fn("capsTL")))

    def test_alt_drag_copies_overlays(self):
        self.assertIn("groupDrag(ev, { k: \"caption\", id: hit })", self.fn("capDown"))
        self.assertIn("groupDrag(ev, { k, id })", self.fn("gfxDown"))
        self.assertIn("const dup = ev.altKey", self.fn("groupDrag"))


if __name__ == "__main__":
    unittest.main()
