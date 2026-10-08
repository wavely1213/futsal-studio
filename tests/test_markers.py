"""편집실 마커 → 챕터 (D-074 · editor.html cutMarkers·chapterCheck 를 node 로 그대로 실행).

- 잘라내고 당기기: 예전에는 잘라 낸 끝보다 뒤의 마커만 당겨서 3초 '챕터 A'·9초 '챕터 B'·20초 '챕터 C' 에서 2~10초를 잘라내면
  [3, 9, 12] — 잘린 구간 안 A·B 가 그대로 남아 B 가 원래 17초 장면을 가리켰다. 이제 구간 뒤로 이어지는 장(B)만 잘린 자리(2초)로,
  내용이 다 잘린 장(A)은 지움 → [2 'B', 12 'C'].
- 챕터 규칙(upload.py 와 같은 규칙): 3분 이상 · 3개 이상 · 각 10초 이상 · 첫 챕터 00:00 · 편집 메모 이름 · 쇼츠 없음.
  같은 마커 목록을 upload.chapters 에 넣어 화면 판단과 키트 결과가 맞는지도 확인.
실행: python3 -m unittest tests.test_markers
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import upload  # noqa: E402

NODE = shutil.which("node")


def _js():
    src = (ROOT / "editor.html").read_text(encoding="utf-8")
    out = []
    for name in ("const FPS =", "const EPS =", "const fr =", "const mmss ="):
        i = src.index(name)
        out.append(src[i:src.index("\n", i)])
    i = src.index("/* ---------- 마커 · 챕터 (D-074) ---------- */")
    j = src.index("function markCut(", i)
    out.append(src[i:j])
    i = src.index("const CH_MIN_VIDEO", j)
    out.append(src[i:src.index("function renderMarkers(", i)])
    return "\n".join(out)


def mk(t, name=""):
    return {"t": t, "name": name}


@unittest.skipUnless(NODE, "node 가 없어요")
class MarkerTests(unittest.TestCase):
    def run_js(self, calls):
        prog = _js() + "\nconst inp = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
            "process.stdout.write(JSON.stringify(inp.map(c => c.op === 'cut' ? cutMarkers(c.ms, c.a, c.b) : chapterCheck(c.ms, c.dur, c.fmt))));"
        r = subprocess.run([NODE, "-e", prog], input=json.dumps(calls), capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def ripple(self, ms, a, b):
        """잘라내고 당기기 (editor.html extractRange: cutMarkers → shiftAfter 의 마커 당기기)."""
        res, = self.run_js([{"op": "cut", "ms": ms, "a": a, "b": b}])
        out = [dict(m, t=round(m["t"] - (b - a), 4) if m["t"] >= b - 1e-4 else m["t"]) for m in res["markers"]]
        return sorted(out, key=lambda m: m["t"]), res

    def test_backlog_repro(self):
        """백로그 재현값: 3 'A' · 9 'B' · 20 'C' 에서 2~10초 잘라내기 → 예전 [3, 9, 12] · 이제 [2 'B', 12 'C']."""
        out, res = self.ripple([mk(3, "챕터 A"), mk(9, "챕터 B"), mk(20, "챕터 C")], 2, 10)
        self.assertEqual([(m["t"], m["name"]) for m in out], [(2, "챕터 B"), (12, "챕터 C")])
        self.assertEqual((res["moved"], res["removed"]), (1, 1))

    def test_marker_at_cut_end_continues(self):
        """잘린 끝(b)에 마커가 있으면 그 장이 이어짐 → 구간 안 마커는 모두 지움 · b 의 마커가 a 로."""
        out, res = self.ripple([mk(3, "A"), mk(10, "B")], 2, 10)
        self.assertEqual([(m["t"], m["name"]) for m in out], [(2, "B")])
        self.assertEqual((res["moved"], res["removed"]), (0, 1))

    def test_marker_at_cut_start(self):
        """잘린 시작(a)의 마커: 구간 안에 다른 마커가 없으면 그대로 (뒤 장면이 이어 붙음) · 있으면 더 늦은 장이 이김."""
        out, res = self.ripple([mk(2, "A"), mk(30, "C")], 2, 10)
        self.assertEqual([(m["t"], m["name"]) for m in out], [(2, "A"), (22, "C")])
        self.assertEqual((res["moved"], res["removed"]), (0, 0))
        out, _ = self.ripple([mk(2, "A"), mk(5, "B")], 2, 10)
        self.assertEqual([(m["t"], m["name"]) for m in out], [(2, "B")])

    def test_outside_markers_untouched(self):
        out, res = self.ripple([mk(1.5, "앞"), mk(10.5, "뒤")], 2, 10)
        self.assertEqual([(m["t"], m["name"]) for m in out], [(1.5, "앞"), (2.5, "뒤")])
        self.assertEqual((res["moved"], res["removed"]), (0, 0))

    def test_chapter_rules(self):
        long = 600.0
        res, = self.run_js([{"op": "chk", "ms": [mk(0, "인사"), mk(65, "퍼스트 터치"), mk(70, "너무 가까움"), mk(200, "BGM 바꾸기"),
                                                 mk(300), mk(595, "끝 직전"), mk(700, "끝 뒤")], "dur": long, "fmt": "long"}])
        rows = {r["m"]["t"]: r for r in res["rows"]}
        self.assertEqual(res["chapters"], 2)
        self.assertEqual(rows[0]["kind"], "chapter")
        self.assertIn("10초 이상 떨어져야", rows[70]["prob"])
        self.assertEqual((rows[200]["kind"], rows[200]["prob"]), ("memo", "편집 메모처럼 보여 챕터에서 빠져요"))
        self.assertEqual(rows[300]["kind"], "plain")
        self.assertIn("끝나기 10초 안", rows[595]["prob"])
        self.assertIn("끝보다 뒤", rows[700]["prob"])
        self.assertTrue(any("3개 이상이어야" in n["text"] and not n["ok"] for n in res["notes"]))

    def test_first_chapter_and_ok(self):
        res, = self.run_js([{"op": "chk", "ms": [mk(4, "인사"), mk(60, "패스"), mk(120, "슈팅")], "dur": 300, "fmt": "long"}])
        texts = [n["text"] for n in res["notes"]]
        self.assertTrue(any("00:00 으로 당겨져요" in t for t in texts), texts)
        self.assertTrue(any("챕터 3개" in t and "규칙에 맞아요" in t for t in texts), texts)
        res, = self.run_js([{"op": "chk", "ms": [mk(30, "패스"), mk(60, "슈팅"), mk(120, "수비")], "dur": 300, "fmt": "long"}])
        self.assertTrue(any("'시작' 챕터가 붙어요" in n["text"] for n in res["notes"]))

    def test_shorts_and_short_videos_have_no_chapters(self):
        for fmt, dur, word in (("shorts", 600, "쇼츠에는 챕터가 안 들어가요"), ("long", 179, "3분이 안 되는")):
            res, = self.run_js([{"op": "chk", "ms": [mk(0, "a"), mk(60, "b"), mk(120, "c")], "dur": dur, "fmt": fmt}])
            self.assertEqual(res["chapters"], 0)
            self.assertIn(word, res["notes"][0]["text"])

    def test_memo_rule_matches_upload(self):
        """편집 메모 이름 판단이 올리기 키트(upload._MEMO)와 같음 (한글 이름이 '글자 없음'으로 잘못 걸리지 않게 · 유니코드)."""
        names = ["퍼스트 터치", "BGM", "자막 수정", "다시 확인", "여기", "이 부분 체크", "??", "!!", "방향 바꾸기", "공 빼기", "각 자르기",
                 "TODO 컷", "효과음 넣기", "패스 앤 무브", "1. 인사", "확인", "체크 필요", "Shooting"]
        res, = self.run_js([{"op": "chk", "ms": [mk(i * 20, n) for i, n in enumerate(names)], "dur": 1000, "fmt": "long"}])
        js = {r["name"]: r["kind"] == "memo" for r in res["rows"]}
        for n in names:
            self.assertEqual(js[n], bool(upload._MEMO.search(n)), n)

    def test_screen_matches_kit(self):
        """화면이 '챕터'로 보인 마커 = 올리기 키트가 이름 붙인 챕터로 쓰는 마커 (00:00 으로 당긴 첫 챕터 포함)."""
        ms = [mk(3, "인사"), mk(65, "퍼스트 터치"), mk(70, "너무 가까움"), mk(200, "BGM 바꾸기"), mk(260, "슈팅"), mk(400, "마무리")]
        res, = self.run_js([{"op": "chk", "ms": ms, "dur": 480, "fmt": "long"}])
        screen = sorted(r["name"] for r in res["rows"] if r["kind"] == "chapter" and not r["prob"])
        kit = upload.chapters([{"start": 0, "end": 480, "text": "x"}], ms, dur=480)
        self.assertEqual(sorted(c["title"] for c in kit if c["named"]), screen)
        self.assertEqual(kit[0]["time"], "00:00")


if __name__ == "__main__":
    unittest.main()
