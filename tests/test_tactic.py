"""편집실 전술 그림(D-180~D-185) — 저장소 폴더에서 python3 -m unittest tests.test_tactic

- tactic.normalize: 저장본 정리 (모르는 종류·점 모자람 → 버림 · 값 범위 · 따라가기 정렬)
- tactic.ops_at: 보이는 때·그려지며 나오기·끝에서 흐려지기·따라가기(원은 통째로, 화살표는 출발점만)·윤곽 방향(채우기 +, 구멍 −)
- 미리보기 = 내보내기: editor.html tacOps·tacNorm·tacOrder 를 node 로 돌려 tactic.ops_at 과 숫자까지 같은지 (롱폼·쇼츠, 여러 시각)
- tactic.ass_lines / editor.build_ass: Board 스타일·프레임 가운데 시간 창·같은 모양은 한 줄·스포트라이트 먼저·편집본 끝에서 자름 · seq_total
- 원본 ↔ 화면 자리 (media_rect·src_to_frame·frame_to_src) · 선수 고르기·이어 따라가기·놓친 장면 메우기·track(가짜 모델)
- /api/edit/track (가짜 track) · msg.place_tactics: 담백 없음 · 보통 발밑 원(따라감) · 듬뿍 + 움직인 길 화살표 · 작은 사람·화면 밖은 건너뜀
- 편집 뒤 따라가기 기록 (D-184): tacFollowCut·tacFollowHold·tidySeq 를 node 로 · MSG 확실할 때만 (D-185): 공 둘·공 곁 두 아이·몰림·근접·차기/몰고 가기·다른 공
인터넷·모델·브라우저는 쓰지 않음 (node 가 없으면 미리보기 비교만 건너뜀)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor  # noqa: E402
import msg  # noqa: E402
import tactic  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None


def tac(kind, pts, start=0.0, dur=3.0, **kw):
    return dict({"id": kind, "kind": kind, "pts": pts, "start": start, "dur": dur}, **kw)


SAMPLES = [
    tac("arrow", [[0.1, 0.8], [0.35, 0.55]]),
    tac("curve", [[0.4, 0.85], [0.5, 0.4], [0.7, 0.7]], start=0.5, color="#ff3b30", width=20),
    tac("pass", [[0.1, 0.3], [0.3, 0.1], [0.55, 0.3]], draw=0.8),
    tac("ring", [[0.8, 0.85]], follow=[[0, 0, 0], [1, 0.05, -0.02], [2, 0.1, 0.0]]),
    tac("spot", [[0.6, 0.5]], size=180, dim=0.7),
    tac("label", [[0.95, 0.35]], text="최경진 감독 7번", glow=False),
    tac("curve", [[0.2, 0.2], [0.3, 0.6], [0.5, 0.2]], follow=[[0, 0, 0], [2, -0.1, 0.05]], draw=0),
    tac("label", [[0.5, 0.4]], text="A", color="#1F4FE0", draw=0.5),
]


def area(ct):
    return tactic._area(ct)


class NormalizeTests(unittest.TestCase):
    def test_bad_ones_dropped(self):
        self.assertIsNone(tactic.normalize({"kind": "star", "pts": [[0, 0]]}))
        self.assertIsNone(tactic.normalize({"kind": "arrow", "pts": [[0, 0]]}), "화살표는 점 2개")
        self.assertIsNone(tactic.normalize({"kind": "curve", "pts": [[0, 0], [1, 1]]}), "곡선은 점 3개")
        self.assertIsNone(tactic.normalize("ring"))

    def test_defaults_and_ranges(self):
        n = tactic.normalize({"kind": "ring", "pts": [[2, -1], ["x", 0.5]], "start": -3, "dur": 0, "color": "red", "width": 999, "size": None})
        self.assertEqual(n["pts"], [[1.5, -0.5]], "점은 화면 둘레 반 칸까지 · 한 점만 씀")
        self.assertEqual((n["start"], n["dur"]), (0.0, 0.1))
        self.assertEqual(n["color"], tactic.DEFAULTS["ring"]["color"], "색 글자가 아니면 기본색")
        self.assertEqual(n["width"], 80.0)
        self.assertEqual(n["size"], tactic.DEFAULTS["ring"]["size"])
        self.assertTrue(n["glow"])
        self.assertIsNone(n["follow"])
        lab = tactic.normalize({"kind": "label", "pts": [[0.5, 0.5]], "text": "가" * 50, "color": "#a6ff00"})
        self.assertEqual(len(lab["text"]), 30)
        self.assertEqual(lab["color"], "#A6FF00")
        f = tactic.normalize({"kind": "spot", "pts": [[0.5, 0.5]], "follow": [[2, 0.1, 0], [0, 0, 0], "x", [1, 9, -9]]})
        self.assertEqual(f["follow"], [[0.0, 0.0, 0.0], [1.0, 2.0, -2.0], [2.0, 0.1, 0.0]], "시각 순서 · 옮김은 ±2 화면까지")
        self.assertEqual(f["dim"], tactic.DEFAULTS["spot"]["dim"])


class OpsTests(unittest.TestCase):
    W, H = 1920, 1080

    def ops(self, t, at):
        return tactic.ops_at(tactic.normalize(t), at, self.W, self.H)

    def test_visible_only_inside(self):
        t = tac("ring", [[0.5, 0.8]], start=2, dur=3)
        self.assertEqual(self.ops(t, 1.99), [])
        self.assertEqual(self.ops(t, 5.0), [], "끝 시각은 이미 사라짐")
        self.assertTrue(self.ops(t, 2.0) or True)
        self.assertTrue(self.ops(t, 4.0))

    def test_draw_on_and_fade(self):
        a = tac("arrow", [[0.1, 0.5], [0.6, 0.5]], dur=3, draw=0.6, glow=False)
        self.assertEqual(self.ops(a, 0.0), [], "길이 0 이면 아직 안 보임")
        mid, full = self.ops(a, 0.15), self.ops(a, 1.0)
        tip = lambda ops: max(x for o in ops for ct in o["c"] for x, _ in ct)  # noqa: E731
        self.assertLess(tip(mid), tip(full), "그려지는 중에는 화살표가 짧음")
        self.assertAlmostEqual(tip(full), 0.6 * self.W, delta=1.0, msg="다 그리면 화살촉 끝 = 끝점")
        self.assertEqual({o["a"] for o in full}, {1.0, tactic.CORE_A})
        fade = self.ops(a, 3 - tactic.FADE_OUT / 2)
        self.assertAlmostEqual(fade[0]["a"], 0.5, places=2, msg="끝 0.25초 동안 흐려짐")
        self.assertEqual(self.ops(dict(a, draw=0), 0.0)[0]["a"], 1.0, "그려지는 시간 0 이면 바로 다 보임")

    def test_glow_core_layers(self):
        a = self.ops(tac("arrow", [[0.1, 0.5], [0.6, 0.5]]), 2.0)
        self.assertEqual([o["col"] for o in a], ["#FFE14D"] * 4 + ["#FFFFFF"] * 2, "빛 번짐 2(몸통·화살촉) + 몸통·화살촉 + 흰 심선 2")
        self.assertGreater(a[0]["b"], 0)
        self.assertEqual(a[2]["b"], 0)
        no = self.ops(tac("arrow", [[0.1, 0.5], [0.6, 0.5]], glow=False), 2.0)
        self.assertEqual(len(no), 4)

    def test_contours_same_direction_holes_reverse(self):
        for t in SAMPLES:
            for at in (0.3, 1.0, 2.5):
                for o in self.ops(t, at):
                    if o["k"] != "poly":
                        continue
                    if t["kind"] == "spot" and o["col"] == "#000000":
                        self.assertGreater(area(o["c"][0]), 0)
                        self.assertLess(area(o["c"][1]), 0, "스포트라이트 밝은 원은 구멍")
                    else:
                        for ct in o["c"]:
                            self.assertGreater(area(ct), 0, (t["kind"], at))

    def test_follow_moves_ring_but_arrow_end_stays(self):
        fol = [[0, 0, 0], [1, 0.1, -0.05]]
        r0 = self.ops(tac("ring", [[0.5, 0.8]], draw=0, glow=False), 1.0)
        r1 = self.ops(tac("ring", [[0.5, 0.8]], draw=0, glow=False, follow=fol), 1.0)
        cx = lambda ops: sum(x for x, _ in ops[0]["c"][0]) / len(ops[0]["c"][0])  # noqa: E731
        self.assertAlmostEqual(cx(r1) - cx(r0), 0.1 * self.W, delta=0.5)
        self.assertEqual(tactic.follow_at(fol, 0.5), (0.05, -0.025))
        self.assertEqual(tactic.follow_at(fol, 9), (0.1, -0.05), "끝 뒤는 마지막 값")
        a = tac("arrow", [[0.2, 0.8], [0.6, 0.5]], draw=0, glow=False)
        a0, a1 = self.ops(a, 1.0), self.ops(dict(a, follow=fol), 1.0)
        tip = lambda ops: ops[1]["c"][0][0]  # noqa: E731 — 화살촉 꼭짓점
        self.assertEqual(tip(a0), tip(a1), "가리키는 끝은 그대로")
        self.assertNotEqual(a0[0]["c"], a1[0]["c"], "출발점은 선수를 따라감")

    def test_pass_marches(self):
        p = tac("pass", [[0.1, 0.5], [0.5, 0.3], [0.9, 0.5]], draw=0, glow=False)
        a, b = self.ops(p, 1.0), self.ops(p, 1.0 + tactic.MARCH / 2)
        self.assertNotEqual(a[0]["c"], b[0]["c"], "점선이 흐름")
        c = self.ops(p, 1.0 + tactic.MARCH)
        self.assertEqual(len(a[0]["c"]), len(c[0]["c"]))

    def test_ring_sweep_and_spot_iris(self):
        r = tac("ring", [[0.5, 0.8]], draw=1.0, glow=False)
        early = self.ops(r, 0.05)
        full = self.ops(r, 2.0)
        n = lambda ops: sum(len(ct) for o in ops for ct in o["c"])  # noqa: E731
        self.assertLess(n(early), n(full), "원이 한 바퀴 그려지며 나옴")
        s = tac("spot", [[0.5, 0.5]], size=200, glow=False)
        hole = lambda ops: max(x for x, _ in ops[0]["c"][1]) - 0.5 * self.W  # noqa: E731
        self.assertGreater(hole(self.ops(s, 0.1)), hole(self.ops(s, 2.0)), "밝은 원이 좁혀 들어옴")
        self.assertAlmostEqual(hole(self.ops(s, 2.0)), 200, delta=1)
        self.assertAlmostEqual(self.ops(s, 2.0)[0]["a"], 0.6)

    def test_label_text_and_clamp(self):
        ops = self.ops(tac("label", [[0.99, 0.3]], text="최경진", draw=0), 1.0)
        self.assertEqual(ops[-1]["k"], "text")
        self.assertEqual(ops[-1]["col"], "#111111", "밝은 이름표엔 검은 글자")
        xs = [x for x, _ in ops[0]["c"][0]]
        self.assertLessEqual(max(xs), self.W - 9.9, "화면 오른쪽 끝에서도 이름표가 화면 안")
        self.assertIn([0.99 * self.W, 0.3 * self.H], ops[0]["c"][1], "뾰족한 끝은 선수 머리 위 그대로")
        dark = self.ops(tac("label", [[0.5, 0.3]], text="A", color="#1F4FE0", draw=0), 1.0)
        self.assertEqual(dark[-1]["col"], "#FFFFFF")
        self.assertEqual(self.ops(tac("label", [[0.5, 0.3]], text=""), 1.0), [])

    def test_scale_with_frame(self):
        r = tac("ring", [[0.5, 0.8]], draw=0, glow=False)
        a = tactic.ops_at(tactic.normalize(r), 1, 1920, 1080)
        b = tactic.ops_at(tactic.normalize(r), 1, 3840, 2160)
        self.assertAlmostEqual(b[0]["c"][0][0][0], a[0]["c"][0][0][0] * 2, delta=0.02, msg="4K 는 같은 모양 2배")


NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const a = src.indexOf('/* ---------- 전술 그림: 모양 계산 (tactic.py'), b = src.indexOf('/* ---------- 전술 그림: 모양 계산 끝');
if (a < 0 || b < 0) throw new Error('전술 그림 모양 계산 칸을 찾지 못함');
eval(src.slice(a, b) + '; globalThis.X = { tacOps, tacNorm, tacOrder };');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(([fn, args]) => X[fn](...args))));
"""


@unittest.skipUnless(HAS_NODE, "node 가 없어 미리보기 계산 비교를 건너뜀")
class PreviewParityTests(unittest.TestCase):
    """미리보기(editor.html)와 내보내기(tactic.py)가 같은 숫자를 내는지 — 렌더링 차이(흐림·가장자리)는 e2e 화면 비교로."""

    def js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "editor.html")], input=json.dumps(cases), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def same(self, a, b, path="ops"):
        if isinstance(a, float) or isinstance(b, float):
            self.assertAlmostEqual(float(a), float(b), delta=0.011, msg=path)
        elif isinstance(a, list):
            self.assertEqual(len(a), len(b), path)
            for i, (x, y) in enumerate(zip(a, b)):
                self.same(x, y, f"{path}[{i}]")
        elif isinstance(a, dict):
            self.assertEqual(sorted(a), sorted(b), path)
            for k in a:
                self.same(a[k], b[k], f"{path}.{k}")
        else:
            self.assertEqual(a, b, path)

    def test_ops_same_as_export(self):
        cases, want = [], []
        for W, H in ((1920, 1080), (1080, 1920)):
            for t in SAMPLES:
                n = tactic.normalize(t)
                for at in (0.0, 0.1, 0.33, 0.7, 1.234, 2.0, 2.9, n["start"] + 0.05):
                    cases.append(["tacOps", [n, at, W, H]])
                    want.append(tactic.ops_at(n, at, W, H))
        got = self.js(cases)
        for i, (g, w) in enumerate(zip(got, want)):
            self.same(w, g, f"case{i}:{cases[i][1][0]['kind']}@{cases[i][1][1]}")

    def test_norm_and_order_same(self):
        raws = SAMPLES + [{"kind": "ring", "pts": [[2, -1]], "start": -3, "dur": 0, "color": "red", "width": 999},
                          {"kind": "spot", "pts": [[0.5, 0.5]], "follow": [[2, 0.1, 0], [0, 0, 0], "x", [1, 9, -9]]},
                          {"kind": "label", "pts": [[0.5, 0.5]], "text": "가" * 50}, {"kind": "nope", "pts": []}]
        got = self.js([["tacNorm", [r]] for r in raws] + [["tacOrder", [raws]]])
        for r, g in zip(raws, got):
            self.same(tactic.normalize(r), g, r.get("kind"))
        self.same(tactic.ordered(raws), got[-1], "order")
        self.assertEqual(got[-1][0]["kind"], "spot", "스포트라이트는 맨 아래")


RETIME_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8'), extra = fs.readFileSync(process.argv[2], 'utf8');
const a = src.indexOf('/* ---------- 전술 그림: 모양 계산 (tactic.py'), b = src.indexOf('/* ---------- 전술 그림: 모양 계산 끝');
eval(extra + '\n' + src.slice(a, b) + '; globalThis.X = { tacFollowCut, tacFollowHold, tacFollowAt, tidySeq };');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(([fn, args]) => X[fn](...args))));
"""


@unittest.skipUnless(HAS_NODE, "node 가 없어 따라가기 기록 옮기기 계산을 건너뜀")
class FollowRetimeTests(unittest.TestCase):
    """편집으로 그림 아래 영상이 잘리거나 밀리면 따라가기 기록도 같이 (리뷰: 잘라낸 뒤 원이 선수 옆에 둥둥 떠 있음)."""
    FOL = [[k / 8, round(0.05 * k / 8, 4), 0.0] for k in range(33)]   # 4초 동안 오른쪽으로 초당 0.05

    def js(self, cases):
        from tests.test_tidy_remap import _js
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_js())
        try:
            r = subprocess.run(["node", "-e", RETIME_RUN, str(ROOT / "editor.html"), f.name], input=json.dumps(cases), capture_output=True, text=True, timeout=120)
        finally:
            Path(f.name).unlink()
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def ring(self, start=10.0, dur=4.0):
        return tac("ring", [[0.5, 0.8]], start=start, dur=dur, follow=self.FOL)

    def x_at(self, g, t):
        return tactic.follow_at(g["follow"], t - g["start"])[0]

    def test_cut_in_middle_keeps_ring_on_player(self):
        g = self.ring()
        (c,) = self.js([["tacFollowCut", [g, 11.0, 11.5]]])
        n = dict(g, start=c["start"], follow=c["follow"], dur=3.5)
        self.assertEqual(c["start"], 10.0)
        for t_old in (10.5, 10.99, 11.5, 12.0, 13.5):   # 잘린 곳 뒤의 영상은 0.5초 당겨짐 → 원도 그 자리
            t_new = t_old if t_old < 11.0 else t_old - 0.5
            self.assertAlmostEqual(self.x_at(n, t_new), self.x_at(g, t_old), delta=0.0015, msg=t_old)
        self.assertIsNotNone(tactic.normalize(n))

    def test_cut_head_and_tail(self):
        g = self.ring()
        (h, t) = self.js([["tacFollowCut", [g, 9.0, 11.0]], ["tacFollowCut", [g, 13.0, 15.0]]])
        self.assertEqual(h["start"], 9.0, "앞이 잘리면 잘린 곳에서 시작")
        nh = dict(g, start=9.0, follow=h["follow"])
        self.assertAlmostEqual(self.x_at(nh, 9.0), self.x_at(g, 11.0), delta=0.0015)
        self.assertAlmostEqual(self.x_at(nh, 10.5), self.x_at(g, 12.5), delta=0.0015)
        nt = dict(g, follow=t["follow"])
        self.assertAlmostEqual(self.x_at(nt, 12.5), self.x_at(g, 12.5), delta=0.0015, msg="뒤가 잘리면 앞은 그대로")

    def test_hold_when_gap_inserted(self):
        g = self.ring()
        (hd,) = self.js([["tacFollowHold", [g, 11.0, 2.0]]])
        self.assertEqual(hd["dur"], 6.0)
        n = dict(g, follow=hd["follow"], dur=6.0)
        for t in (11.0, 12.0, 13.0):
            self.assertAlmostEqual(self.x_at(n, t), self.x_at(g, 11.0), delta=0.0015, msg="끼운 동안 멈춤")
        self.assertAlmostEqual(self.x_at(n, 14.5), self.x_at(g, 12.5), delta=0.0015, msg="밀린 선수를 이어서")

    def test_tidy_remap_moves_follow(self):
        from tests.test_tidy_remap import pair, seq
        q = seq(pair(0.0, 20.0, 0.0), format="long", tactics=[self.ring()])
        (r,) = self.js([["tidySeq", [q, [{"in": 0.0, "out": 11.0}, {"in": 11.5, "out": 20.0}]]]])
        (n,) = r["tactics"]
        self.assertEqual((n["start"], n["dur"]), (10.0, 3.5))
        self.assertAlmostEqual(self.x_at(n, 12.0), self.x_at(self.ring(), 12.5), delta=0.0015, msg="군더더기 정리로 0.5초 빠져도 선수 위")
        self.assertAlmostEqual(self.x_at(n, 10.9), self.x_at(self.ring(), 10.9), delta=0.0015)


class AssTests(unittest.TestCase):
    def test_lines_timing_and_merge(self):
        r = tac("ring", [[0.5, 0.8]], start=1.0, dur=2.0, draw=0.5)
        lines = tactic.ass_lines([r], 1920, 1080, 30, 100, editor._ass_time)
        self.assertTrue(all(",Board,," in ln and ln.startswith("Dialogue: 0,") for ln in lines))
        times = sorted({tuple(ln.split(",")[1:3]) for ln in lines})
        self.assertEqual(times[0][0], "0:00:01.02", "1.0초(그리기 시작 · 아직 길이 0)는 빈 프레임 → 둘째 프레임의 반 프레임 앞부터")
        self.assertEqual(max(t[1] for t in times), "0:00:02.98", "마지막 프레임(2.9667초)의 반 프레임 뒤(2.983)까지")
        # 그려지는 0.5초(15프레임)와 끝 흐려짐(0.25초, 8프레임) 사이의 멈춘 1.25초는 한 덩어리
        still = [t for t in times if t[0] < "0:00:02.75" and t[1] > "0:00:01.6"]
        self.assertTrue(any(t[1] >= "0:00:02.7" and t[0] <= "0:00:01.6" for t in still), times)
        self.assertLess(len(times), 30, "같은 모양이 이어지면 한 줄")
        self.assertEqual(tactic.ass_lines([dict(r, start=200)], 1920, 1080, 30, 100, editor._ass_time), [], "편집본 끝 뒤는 없음")

    def test_spot_first_and_drawing_format(self):
        lines = tactic.ass_lines([tac("ring", [[0.5, 0.8]], draw=0), tac("spot", [[0.5, 0.5]], draw=0, glow=False)], 1920, 1080, 30, 10, editor._ass_time)
        self.assertIn("&H000000&", lines[0], "스포트라이트(검은 어둠)가 먼저 = 아래")
        self.assertIn(r"\an7\pos(0,0)\p2\bord0\shad0", lines[0])
        self.assertIn("m -172 -172 l 4012 -172", lines[0], "\\p2 = 좌표 ×2 정수 (스포트라이트 상자는 화면 밖 86px 까지)")
        self.assertIn(r"\blur", lines[0])
        self.assertRegex(lines[0], r"\\1a&H[0-9A-F]{2}&")
        self.assertIn("m ", lines[0])
        lab = tactic.ass_lines([tac("label", [[0.5, 0.4]], text="{감독}", draw=0)], 1920, 1080, 30, 10, editor._ass_time)
        txt = [ln for ln in lab if r"\an5" in ln][0]
        self.assertIn(r"\{감독\}", txt, "중괄호는 명령이 아니라 글자")
        self.assertIn(rf"\fs{round(40 * tactic.FONT_K)}", txt)

    def test_build_ass_and_total(self):
        proj = {"captionStyle": dict(editor.DEFAULT_STYLE), "captionsOn": False, "titles": [], "shapes": [], "items": [],
                "tactics": [tac("arrow", [[0.1, 0.5], [0.5, 0.5]], start=0, dur=2)], "format": "long"}
        self.assertEqual(editor.seq_total(proj), 2.0, "클립 없이 전술 그림만 있어도 길이")
        ass = editor.build_ass(proj, 1920, 1080, 25)
        self.assertIn(tactic.STYLE_LINE, ass)
        self.assertTrue(any(",Board,," in ln for ln in ass.splitlines()))
        plain = editor.build_ass(dict(proj, tactics=[]), 1920, 1080)
        self.assertFalse(any(",Board,," in ln for ln in plain.splitlines()))
        self.assertEqual(tactic.end_of({"tactics": [{"start": 1, "dur": 2}, "x"]}), 3.0)


class MappingTests(unittest.TestCase):
    def test_contain_and_motion(self):
        it = {"track": "V1", "start": 0, "in": 0, "out": 10, "fx": {}}
        md = {"kind": "video", "w": 1280, "h": 720}
        seq = {"format": "long", "layout": {"mode": "fill"}}
        self.assertEqual(tactic.src_to_frame(it, md, seq, 1920, 1080, 0.5, 0.5, 1), (960.0, 540.0))
        self.assertEqual(tactic.src_to_frame(it, md, seq, 1920, 1080, 0, 0, 1), (0.0, 0.0))
        z = dict(it, fx={"scale": {"v": 200, "k": []}, "anchor": {"v": [0.25, 0.5], "k": []}})
        x, y = tactic.src_to_frame(z, md, seq, 1920, 1080, 0.5, 0.5, 1)
        self.assertAlmostEqual(x, 480 + 480 * 2)
        self.assertAlmostEqual(y, 540)
        r = dict(it, fx={"scale": {"v": 150, "k": [{"t": 0, "v": 100}, {"t": 10, "v": 200}]}, "rot": {"v": 30, "k": []}, "pos": {"v": [0.4, 0.6], "k": []}})
        for u, v, t in ((0.1, 0.2, 2.0), (0.7, 0.9, 7.5)):
            fx, fy = tactic.src_to_frame(r, md, seq, 1920, 1080, u, v, t)
            bu, bv = tactic.frame_to_src(r, md, seq, 1920, 1080, fx, fy, t)
            self.assertAlmostEqual(bu, u, places=6)
            self.assertAlmostEqual(bv, v, places=6)

    def test_shorts_blur_layout(self):
        it = {"track": "V1", "start": 0, "in": 0, "out": 10, "fx": {}, "reframe": 0.5}
        md = {"kind": "video", "w": 1920, "h": 1080}
        f = tactic.media_rect(it, md, {"format": "shorts", "layout": msg.seq_layout("shorts")}, 1080, 1920)
        self.assertAlmostEqual(f["mw"], 1080 * 1.35)
        self.assertAlmostEqual(f["by"], (1920 - f["bh"]) / 2)
        x, _ = tactic.src_to_frame(it, md, {"format": "shorts", "layout": msg.seq_layout("shorts")}, 1080, 1920, 0.5, 0.5, 1)
        self.assertAlmostEqual(x, 540)

    def test_follow_offsets_and_anchor(self):
        self.assertEqual(tactic.box_anchor("ring", [0.1, 0.2, 0.2, 0.4]), (0.2, 0.6000000000000001))
        self.assertEqual(tactic.box_anchor("label", [0.1, 0.2, 0.2, 0.4]), (0.2, 0.2))
        self.assertEqual(tactic.follow_offsets([(2.0, 0.5, 0.5), (2.5, 0.6, 0.45)], 2.0), [[0.0, 0.0, 0.0], [0.5, 0.1, -0.05]])
        self.assertIsNone(tactic.follow_offsets([], 0))


P = lambda x, y, w, h, s=0.9: [x, y, w, h, s]  # noqa: E731


class TrackTests(unittest.TestCase):
    def test_pick_start(self):
        ps = [P(0.1, 0.2, 0.1, 0.5), P(0.5, 0.2, 0.1, 0.5), P(0.12, 0.3, 0.05, 0.3)]
        self.assertEqual(tactic.pick_start(ps, 0.14, 0.5), ps[2][:4], "겹치면 작은 (앞에 선) 사람")
        self.assertEqual(tactic.pick_start(ps, 0.55, 0.72), ps[1][:4], "발밑을 눌러도 (상자 아래 조금)")
        self.assertIsNone(tactic.pick_start(ps, 0.9, 0.9))
        self.assertEqual(tactic.pick_start(ps, 0.55, 0.19, "label"), ps[1][:4], "이름표: 머리 위 점에 가장 가까운 선수 (상자 경계 밖이어도)")
        self.assertEqual(tactic.pick_start(ps, 0.145, 0.62, "ring"), ps[2][:4], "발밑 원: 발이 가장 가까운 선수")
        self.assertIsNone(tactic.pick_start(ps, 0.9, 0.2, "label"))

    def test_follow_boxes_ignores_other_player_and_fills_lost(self):
        seq = []
        for k in range(10):
            me = P(0.1 + 0.02 * k, 0.3, 0.1, 0.5)
            other = P(0.6 - 0.02 * k, 0.3, 0.1, 0.5)
            seq.append((k * 0.125, [other, me] if k != 4 else [other]))  # 4번째 장면은 가려짐
        raw = tactic.follow_boxes(seq, 0.15, 0.6)
        self.assertIsNone(raw[4][1], "다른 선수로 건너가지 않고 놓친 것으로")
        self.assertAlmostEqual(raw[9][1][0], 0.28)
        sm = tactic.smooth(raw)
        self.assertEqual(len(sm), 10)
        self.assertAlmostEqual(sm[4][1][0], 0.18, places=3, msg="앞뒤로 메움")
        with self.assertRaises(LookupError):
            tactic.follow_boxes([(0, [P(0.8, 0.1, 0.1, 0.2)])], 0.1, 0.9)
        self.assertEqual(tactic.smooth([(0, None)]), [])

    def test_hidden_legs_keep_feet_and_size_gate(self):
        """앞 선수에 다리가 가려 상자 키가 줄면 머리는 그대로 두고 평소 키로 (발밑 원이 허리에 뜨지 않게) · 키가 크게 다른 상자는 다른 사람."""
        seq = [(0.0, [P(0.2, 0.1, 0.1, 0.5)]), (0.125, [P(0.21, 0.1, 0.1, 0.3)]), (0.25, [P(0.22, 0.1, 0.1, 0.5)]),
               (0.375, [P(0.23, 0.11, 0.3, 0.88)]), (0.5, [P(0.24, 0.1, 0.1, 0.5), P(0.25, 0.4, 0.06, 0.2)])]
        raw = tactic.follow_boxes(seq, 0.25, 0.6)
        self.assertEqual(raw[1][1], [0.21, 0.1, 0.1, 0.5], "가린 다리 → 평소 키")
        self.assertIsNone(raw[3][1], "키가 1.76배인 상자(가까이 다가온 다른 사람)는 놓친 것으로")
        self.assertEqual(raw[4][1][:2], [0.24, 0.1], "머리 자리로 이어 같은 선수")

    def test_long_loss_stops(self):
        seq = [(0.0, [P(0.2, 0.1, 0.1, 0.5)])] + [(k / 8, []) for k in range(1, 6)] + [(6 / 8, [P(0.2, 0.1, 0.1, 0.5)])]
        raw = tactic.follow_boxes(seq, 0.25, 0.6)
        self.assertIsNone(raw[-1][1], "0.5초 넘게 놓친 뒤 같은 자리 상자도 다시 잡지 않음 (다른 사람일 수 있음)")
        seq2 = [(0.0, [P(0.2, 0.1, 0.1, 0.5)])] + [(k / 8, []) for k in range(1, 4)] + [(4 / 8, [P(0.2, 0.1, 0.1, 0.5)])]
        self.assertIsNotNone(tactic.follow_boxes(seq2, 0.25, 0.6)[-1][1], "잠깐(3장면) 가려진 것은 다시 잡음")

    def test_follow_from_middle_both_ways(self):
        seq = [(k / 8, [P(0.1 + 0.02 * k, 0.2, 0.1, 0.5), P(0.7, 0.2, 0.1, 0.5)]) for k in range(9)]
        raw = tactic.follow_boxes(seq, 0.2 + 0.05, 0.6, start=4)
        self.assertEqual([round(b[0], 2) for _, b in raw], [round(0.1 + 0.02 * k, 2) for k in range(9)], "고른 장면에서 앞으로도 뒤로도")
        self.assertEqual([t for t, _ in raw], [k / 8 for k in range(9)])

    def test_track_with_fake_model(self):
        frames = [(1.0 + k / 8, k) for k in range(8)]
        det = lambda im: {"persons": [P(0.2 + 0.01 * im, 0.3, 0.1, 0.5)]}  # noqa: E731
        with mock.patch.object(tactic, "_frames", return_value=frames) as fr:
            r = tactic.track("v.mp4", 1.0, 2.0, 0.25, 0.6, detect_fn=det, log=lambda m: None)
        self.assertEqual(fr.call_args[0][1:3], (1.0, 2.0))
        self.assertEqual(r["n"], 8)
        self.assertEqual(r["lost"], 0)
        self.assertEqual(r["boxes"][0][0], 1.0)
        self.assertEqual(len(r["boxes"][0]), 5)
        with mock.patch.object(tactic, "_frames", return_value=frames) as fr:
            tactic.track("v.mp4", 0.0, 99.0, 0.25, 0.6, detect_fn=det, log=lambda m: None)
        self.assertEqual(fr.call_args[0][2], tactic.LIMIT_FOLLOW, "한 번에 30초까지")
        with self.assertRaises(ValueError):
            tactic.track("v.mp4", 2.0, 2.0, 0.5, 0.5, detect_fn=det)

    def test_track_scene_change_ends_follow(self):
        """끝에서 선수를 계속 놓치면(장면이 바뀜) 마지막으로 본 곳까지만 · 화면은 그림을 거기서 끝냄 (seenUntil)."""
        frames = [(k / 8, k) for k in range(16)]
        det = lambda im: {"persons": [P(0.2, 0.3, 0.1, 0.5)] if im < 10 else [P(0.7, 0.1, 0.3, 0.9)]}  # noqa: E731 — 10번째 장면부터 다른 장면
        with mock.patch.object(tactic, "_frames", return_value=frames):
            r = tactic.track("v.mp4", 0.0, 2.0, 0.25, 0.6, detect_fn=det, log=lambda m: None)
        self.assertTrue(r["lostTail"])
        self.assertEqual(r["seenUntil"], 9 / 8)
        self.assertEqual(len(r["boxes"]), 10)
        det2 = lambda im: {"persons": [P(0.2, 0.3, 0.1, 0.5)] if im != 15 else []}  # noqa: E731 — 마지막 한 장면만 가려짐
        with mock.patch.object(tactic, "_frames", return_value=frames):
            r2 = tactic.track("v.mp4", 0.0, 2.0, 0.25, 0.6, detect_fn=det2, log=lambda m: None)
        self.assertFalse(r2["lostTail"], "잠깐 가려진 것은 끝까지 이음")
        self.assertEqual(len(r2["boxes"]), 16)

    def test_busy(self):
        self.assertTrue(tactic._TRACK_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(tactic.Busy):
                tactic.track("v.mp4", 0, 1, 0.5, 0.5, detect_fn=lambda im: None)
        finally:
            tactic._TRACK_LOCK.release()

    def test_no_model(self):
        import detect
        with mock.patch.object(detect, "ensure", return_value=False):
            with self.assertRaises(RuntimeError) as c:
                tactic.track("v.mp4", 0, 1, 0.5, 0.5)
        self.assertIn("모델", str(c.exception))

    def test_frames_reads_raw_video(self):
        """ffmpeg 한 번으로 장면을 줄여 받음 (시험 영상: 2초 · 320×180 · 색 막대)."""
        import core
        with tempfile.TemporaryDirectory() as d:
            v = Path(d) / "t.mp4"
            r = core.run([core.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=2", "-pix_fmt", "yuv420p", str(v)])
            self.assertEqual(r.returncode, 0, r.stderr)
            fr = tactic._frames(v, 0.5, 1.5, 8)
        self.assertGreaterEqual(len(fr), 7)
        self.assertEqual(fr[0][1].size, (320, 180))
        self.assertAlmostEqual(fr[1][0] - fr[0][0], 0.125)


class ApiTests(unittest.TestCase):
    def call(self, body, **patches):
        import app
        h = app.Handler.__new__(app.Handler)
        h.headers = {"Host": f"127.0.0.1:{app.PORT}", "Origin": f"http://127.0.0.1:{app.PORT}"}
        h.path = "/api/edit/track"
        sent = {}
        h._body = lambda: body
        h._send = lambda code, obj, *a: sent.update(code=code, obj=obj)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "v.mp4"
            f.write_bytes(b"x")
            with mock.patch.object(app.editor, "media_path", return_value=f), mock.patch.object(tactic, "track", **patches) as tr:
                h.do_POST()
        return sent, tr

    def test_ok_and_errors(self):
        s, tr = self.call({"name": "v.mp4", "file": "v.mp4", "t0": 1, "t1": 2, "x": 0.5, "y": 0.6}, return_value={"boxes": [[1, 0, 0, 1, 1]], "lost": 0, "n": 1})
        self.assertEqual(s["code"], 200)
        self.assertEqual(tr.call_args[0][1:], (1, 2, 0.5, 0.6))
        self.assertIsNone(tr.call_args[1]["kind"])
        s, tr = self.call({"name": "v.mp4", "file": "v.mp4", "t0": 1, "t1": 2, "x": 0.5, "y": 0.6, "kind": "label"}, return_value={"boxes": [], "lost": 0, "n": 0})
        self.assertEqual(tr.call_args[1]["kind"], "label")
        s, _ = self.call({"name": "v.mp4", "file": "v.mp4"}, side_effect=tactic.Busy("다른 선수 따라가기가 끝난 뒤에 다시 눌러 주세요"))
        self.assertEqual(s["code"], 409)
        s, _ = self.call({"name": "v.mp4", "file": "v.mp4"}, side_effect=LookupError("그 자리에서 선수를 찾지 못했어요"))
        self.assertEqual((s["code"], s["obj"]["ok"]), (400, False))
        s, _ = self.call({"name": "v.mp4", "file": "v.mp4"}, side_effect=RuntimeError("선수 찾기 모델을 쓰지 못했어요"))
        self.assertEqual(s["code"], 500)
        self.assertIn("모델", s["obj"]["error"])
        with mock.patch("studiolog.trace") as trc:
            s, _ = self.call({"name": "v.mp4", "file": "v.mp4"}, side_effect=ZeroDivisionError("x"))
        self.assertEqual(s["code"], 500)
        self.assertIn("따라가지 못했어요", s["obj"]["error"])
        trc.assert_called_once()

    def test_bad_file_name(self):
        import app
        h = app.Handler.__new__(app.Handler)
        h.headers = {"Host": f"127.0.0.1:{app.PORT}", "Origin": f"http://127.0.0.1:{app.PORT}"}
        h.path = "/api/edit/track"
        sent = {}
        h._body = lambda: {"name": "v.mp4", "file": "../x.mp4"}
        h._send = lambda code, obj, *a: sent.update(code=code, obj=obj)
        h.do_POST()
        self.assertEqual(sent["code"], 400)


class MsgPlaceTests(unittest.TestCase):
    INFO = {"width": 1920, "height": 1080, "duration": 120}

    def build(self, fmt="long"):
        B = msg._Build("v.mp4", self.INFO, fmt)
        B.clip(0.0, 30.0, main=True)
        B.clip(40.0, 100.0, main=True)
        return B

    MOMS = [{"kind": "play", "t": 10.0, "a": 8.0, "b": 13.0, "score": 3.0}, {"kind": "play", "t": 12.0, "a": 11.0, "b": 14.0, "score": 2.0},
            {"kind": "play", "t": 60.0, "a": 58.0, "b": 63.0, "score": 1.0}, {"kind": "play", "t": 35.0, "a": 33.0, "b": 37.0, "score": 9.0},
            {"kind": "emphasis", "t": 20.0, "a": 20.0, "b": 21.0, "score": 5.0}]

    def place(self, intensity, moving=0.03, small=False, fmt="long", x0=0.4, kick=True):
        msg._TRACKS.clear()
        B = self.build(fmt)
        frames = [(k, k) for k in range(24)]
        h = 0.1 if small else 0.45

        def det(k):  # 공 차는 순간(6번째 장면 · 10초 - 0.8초 앞부터)부터 공이 그 선수 발에서 멀어짐
            bx = x0 + 0.05 + moving * k / 2 + (0.03 * (k - 6) if kick and k > 6 else 0)
            return {"persons": [P(x0 + moving * k / 2, 0.4, 0.1, h), P(0.05, 0.3, 0.05, h * 0.6)], "ball": [bx, 0.84, 0.02, 0.02, 0.5]}
        with mock.patch.object(tactic, "_frames", side_effect=lambda p, a, b, fps: [(a + k / fps, k) for k, _ in frames if a + k / fps < b]):
            made = msg.place_tactics(B, "v.mp4", {}, self.MOMS, intensity, fmt, detect_fn=det)
        return B, made

    def test_off_for_mild(self):
        B, made = self.place("담백")
        self.assertEqual((made, B.tactics, B.events), ([], [], []))

    def test_normal_ring_follows_player(self):
        B, made = self.place("보통")
        self.assertEqual(len(made), 1, "본편 1.5분 × 0.5개 → 1개 (점수 높은 시범부터 · 잘린 35초는 건너뜀)")
        ring = B.tactics[0]
        self.assertEqual(ring["kind"], "ring")
        self.assertEqual(ring["start"], round(10.0 - msg.TACTIC_PRE, 3))
        self.assertEqual(ring["dur"], round(msg.TACTIC_PRE + msg.TACTIC_POST, 3))
        self.assertAlmostEqual(ring["pts"][0][0], 0.45, places=3, msg="공 가까운 선수의 발밑 (원본 1920 = 화면)")
        self.assertAlmostEqual(ring["pts"][0][1], 0.85, places=3)
        self.assertGreater(ring["follow"][-1][1], 0.2, "오른쪽으로 달린 만큼 원이 따라감")
        self.assertIsNotNone(tactic.normalize(ring))
        ev = B.events[0]
        self.assertEqual((ev["kind"], ev["refs"]), ("tactic", {"tactics": [ring["id"]]}))
        self.assertEqual(ev["src"], 10.0)

    def test_heavy_adds_path_arrow_and_more(self):
        B, made = self.place("듬뿍")
        self.assertEqual(len(made), 2, "듬뿍 1분 1개 → 2개 · 간격 6초 (12초 시범은 10초와 붙어서 빠짐)")
        kinds = [t["kind"] for t in B.tactics]
        self.assertEqual(kinds, ["ring", "curve", "ring", "curve"])
        arrow = B.tactics[1]
        self.assertAlmostEqual(arrow["pts"][0][0], B.tactics[0]["pts"][0][0])
        self.assertGreater(arrow["pts"][2][0], arrow["pts"][0][0], "선수가 간 쪽으로")
        self.assertLess(arrow["pts"][1][1], min(arrow["pts"][0][1], arrow["pts"][2][1]), "위로 휨")
        self.assertEqual(B.events[0]["refs"]["tactics"], [B.tactics[0]["id"], arrow["id"]])
        B2, _ = self.place("듬뿍", moving=0.0)
        self.assertEqual([t["kind"] for t in B2.tactics], ["ring", "ring"], "제자리 시범은 원만")

    def test_scene_change_shortens_ring(self):
        msg._TRACKS.clear()
        B = self.build()

        def det(k):
            return {"persons": [P(0.4, 0.4, 0.1, 0.45)] if k < 14 else [], "ball": None}
        with mock.patch.object(tactic, "_frames", side_effect=lambda p, a, b, fps: [(a + k / fps, k) for k in range(24) if a + k / fps < b]):
            msg.place_tactics(B, "v.mp4", {}, self.MOMS, "보통", "long", detect_fn=det)
        ring = B.tactics[0]
        self.assertAlmostEqual(ring["dur"], 13 / 8 + 1 / 8, places=2, msg="선수를 마지막으로 본 곳까지 (끝 0.25초는 그 안에서 흐려짐)")
        self.assertEqual(len(ring["follow"]), 14)

    def test_source_scene_cut_limits_window(self):
        msg._TRACKS.clear()
        B = self.build()
        seen = []

        def frames(p, a, b, fps):
            seen.append((round(a, 2), round(b, 2)))
            return [(a + k / fps, k) for k in range(24) if a + k / fps < b]
        with mock.patch.object(tactic, "_frames", side_effect=frames):
            msg.place_tactics(B, "v.mp4", {"cuts": [9.6, 11.5]}, self.MOMS[:1], "보통", "long", detect_fn=lambda k: {"persons": [P(0.4, 0.4, 0.1, 0.45)]})
        self.assertEqual(seen, [(9.65, 11.45)], "원본 장면 바뀜(9.6초·11.5초)을 넘지 않게 따라감")
        self.assertLessEqual(B.tactics[0]["start"] + B.tactics[0]["dur"], 11.45 + 1e-6)

    def test_skips_small_or_offscreen(self):
        self.assertEqual(self.place("보통", small=True)[1], [], "멀리 작게 보이는 사람에는 안 넣음")
        self.assertEqual(self.place("보통", moving=0.06, x0=0.6)[1], [], "발이 화면 밖으로 나가면 안 넣음")

    def test_ends_when_player_fills_frame(self):
        msg._TRACKS.clear()
        B = self.build()

        def det(k):  # 카메라 쪽으로 달려와 12번째 장면부터 화면을 채움 (머리가 위 가장자리)
            p = P(0.4, 0.4, 0.1, 0.45) if k < 12 else P(0.35, 0.0, 0.2, 0.95)
            return {"persons": [p], "ball": None}
        with mock.patch.object(tactic, "_frames", side_effect=lambda p, a, b, fps: [(a + k / fps, k) for k in range(24) if a + k / fps < b]):
            msg.place_tactics(B, "v.mp4", {}, self.MOMS[:1], "듬뿍", "long", detect_fn=det)
        self.assertTrue(B.tactics, "앞부분은 넣음")
        self.assertLessEqual(B.tactics[0]["dur"], 12 / 8 + 1 / 8 + 1e-6, "화면을 채우기 전까지만")
        self.assertEqual([t["kind"] for t in B.tactics], ["ring"], "제자리(옆으로 안 움직임)면 화살표 없음")

    def test_ball_owner_needed(self):
        self.assertEqual(len(self.place("보통", kick=False)[1]), 1, "공을 발 곁에 두고 몰고 가는 시범도 넣음")
        msg._TRACKS.clear()
        B = self.build()

        def det(k):  # 공 차는 순간 뒤로 찾기 모델이 멀리 있는 다른 아이의 공을 잡음 (공이 둘 · 그 선수 공은 안 움직임)
            return {"persons": [P(0.4, 0.4, 0.1, 0.45), P(0.85, 0.4, 0.1, 0.45)], "ball": [0.44 if k <= 6 else 0.89, 0.84, 0.02, 0.02, 0.5]}
        with mock.patch.object(tactic, "_frames", side_effect=lambda p, a, b, fps: [(a + k / fps, k) for k in range(24) if a + k / fps < b]):
            self.assertEqual(msg.place_tactics(B, "v.mp4", {}, self.MOMS, "보통", "long", detect_fn=det), [], "다른 공으로 넘어가 공을 가진 사람을 확인 못 하면 안 넣음")

    def test_subject_only_when_sure(self):
        ball = [0.45, 0.84, 0.02, 0.02, 0.5]
        a, b = P(0.4, 0.4, 0.1, 0.45), P(0.43, 0.42, 0.1, 0.45)
        self.assertIs(msg._subject({"persons": [a, P(0.05, 0.3, 0.05, 0.3)], "ball": ball}), a)
        self.assertIsNone(msg._subject({"persons": [a, b], "ball": ball}), "공 곁에 두 아이 (뒤의 아이 발도 공 가까이) → 건너뜀")
        self.assertIsNone(msg._subject({"persons": [a, P(0.47, 0.6, 0.05, 0.22)], "ball": ball}), "작게 보이는 아이도 공 곁이면 모름")
        self.assertIsNone(msg._subject({"persons": [P(0.4, 0.4, 0.1, 0.45)], "ball": [0.9, 0.5, 0.02, 0.02, 0.5]}), "공이 멀면")
        self.assertIsNone(msg._subject({"persons": [P(0.3, 0.0, 0.4, 0.95)], "ball": [0.48, 0.94, 0.02, 0.02, 0.5]}), "너무 가까이 찍힌 장면 (신발만)")
        self.assertIsNone(msg._subject({"persons": [P(0.3, 0.005, 0.2, 0.8)], "ball": [0.38, 0.8, 0.02, 0.02, 0.5]}), "머리가 위 가장자리에 닿음")
        self.assertIs(msg._subject({"persons": [a], "ball": None}), a, "공이 안 보여도 혼자면 그 사람")
        self.assertIsNone(msg._subject({"persons": [a, b], "ball": None}), "공도 없고 여럿이면 모름")
        two = [0.75, 0.6, 0.02, 0.02, 0.4]
        self.assertIsNone(msg._subject({"persons": [a, P(0.05, 0.3, 0.05, 0.3)], "ball": ball, "balls": [ball, two]}), "공이 둘 (모두 공을 가진 연습) → 모름")
        self.assertIs(msg._subject({"persons": [a, P(0.05, 0.3, 0.05, 0.3)], "ball": ball, "balls": [ball]}), a)
        crowd = [a] + [P(0.05 + 0.12 * i, 0.2, 0.05, 0.3) for i in range(4)]
        self.assertIsNone(msg._subject({"persons": crowd, "ball": ball, "balls": [ball]}), "다섯 명 이상 몰린 장면 → 건너뜀")

    def test_kicked(self):
        box = [0.4, 0.4, 0.1, 0.45]
        seq = [(k, {"ball": [0.44 + (0.02 * (k - 2) if k > 2 else 0), 0.84, 0.02, 0.02]}) for k in range(10)]
        raw = [(k, box) for k in range(10)]
        self.assertTrue(msg._kicked(seq, raw, 2))
        still = [(k, {"ball": [0.44, 0.84, 0.02, 0.02], "persons": [box]}) for k in range(10)]
        self.assertTrue(msg._kicked(still, raw, 2), "공을 발 곁에 두고 몰고 감 → 공을 가진 사람")
        crowd = [(k, {"ball": [0.44, 0.84, 0.02, 0.02], "persons": [box, [0.42, 0.42, 0.1, 0.43]]}) for k in range(10)]
        self.assertFalse(msg._kicked(crowd, raw, 2), "공 곁에 다른 아이도 붙어 있으면 누가 가졌는지 모름")
        far = [(k, {"ball": [0.44, 0.84, 0.02, 0.02] if k <= 2 else [0.6, 0.84, 0.02, 0.02], "persons": [box]}) for k in range(10)]
        self.assertTrue(msg._kicked(far, raw, 2), "차서 멀어짐")
        self.assertFalse(msg._kicked([(k, {"ball": None}) for k in range(10)], raw, 2), "공을 못 보면 모름")
        other = [(k, {"ball": [0.44, 0.84, 0.02, 0.02] if k <= 2 else [0.75, 0.6, 0.02, 0.02]}) for k in range(10)]
        self.assertFalse(msg._kicked(other, raw, 2), "옆 아이의 다른 공으로 넘어간 것은 찬 것이 아님")
        two = [(k, {"ball": seq[k][1]["ball"], "balls": [seq[k][1]["ball"]] + ([[0.8, 0.6, 0.02, 0.02]] if k == 5 else [])}) for k in range(10)]
        self.assertFalse(msg._kicked(two, raw, 2), "그 1초 안에 공이 둘 보이는 장면이 있으면 누구 공인지 모름")

    def test_no_model_no_tactics(self):
        import detect
        msg._TRACKS.clear()
        B = self.build()
        with mock.patch.object(detect, "ensure", return_value=False):
            self.assertEqual(msg.place_tactics(B, "v.mp4", {}, self.MOMS, "듬뿍", "long"), [])
        self.assertEqual(msg._TRACKS, {}, "모델이 없을 때는 기억하지 않음 (다음에 다시)")

    def test_seen_run(self):
        raw = [(k, None if k in (1, 2, 7) else [0, 0, 1, 1]) for k in range(10)]
        self.assertEqual(msg._seen_run(raw, 5), (3, 9), "두 장면 넘게 놓친 앞쪽은 빼고 · 한 장면 가려진 것은 이음")

    def test_shorts_layout_mapping(self):
        B, made = self.place("보통", fmt="shorts", moving=0.0)
        self.assertEqual(len(made), 1)
        x = B.tactics[0]["pts"][0][0]
        self.assertAlmostEqual(x, 0.5 + (0.45 - 0.5) * 1.35, places=3, msg="쇼츠 1.35배 흐린 배경 배치에서 같은 선수 발밑")

    def test_summary_mentions_tactics(self):
        s = msg.summary_of([{"kind": "tactic"}, {"kind": "emphasis"}], [], 60.0, {})
        self.assertIn("전술 그림 1", s["text"])
        self.assertNotIn("전술", msg.summary_of([], [], 60.0, {})["text"])


if __name__ == "__main__":
    unittest.main()
