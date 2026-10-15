"""편집실 받아 두기 기다림(HOLD · D-360~D-362) 검토 고침 — editor.html 의 실제 함수를 node 로 그대로 실행. 실행: python3 -m unittest tests.test_play_hold

- 빠르게 훑기(L 두 번 이상 · 4·8배)는 예전처럼 쫓아감: 0.3초·1.5초 기준이 원본 시간이라 8배에서는 37ms 만 어긋나도 '멀어짐(far)' 기다림으로
  모든 재생기를 멈췄다 출발해 그림·소리가 멎고 실제로 6~7배밖에 안 갔음 · 못 받음(starve) 기다림도 빠르게 훑을 때는 안 함 (D-361 검토 고침).
- 2배까지(보통 재생 · L 한 번)는 그대로 기다림 (밖에서 소리 끊김을 고친 D-360·D-361).
- J·L 로 방향·속도를 바꾸면(play) 남아 있던 '불러오는 중…' 도 지움 (예전엔 거꾸로 재생 내내 모니터 위에 남았음 · D-360 검토 고침).
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

STUBS = r"""
let T = 0, playing = false, shuttle = 1, clock = { t0: 0, at: 0 }, FRN = 1, frameDirty = false;
const POOL = new Map(), NOPLAY = new Set(), BADGE = { hidden: true }, BTN = {};
const $ = id => id === "holdBadge" ? BADGE : BTN;
const clamp = (x, a, b) => Math.min(b, Math.max(a, x)), fr = x => x, total = () => 100;
const sp = it => it.speed || 1, iLen = it => (it.out - it.in) / sp(it), iEnd = it => it.start + iLen(it), mt = (it, t) => it.in + (t - it.start) * sp(it);
const mDur = md => md.dur;
const shareNet = () => {}, ensureAudio = () => {}, updateFxLive = () => {};
function fakeEl(over) {
  return Object.assign({ tagName: "VIDEO", paused: true, currentTime: 0, playbackRate: 1, seeking: false, readyState: 4, error: null, duration: 100,
    buffered: { length: 1, start: () => 0, end: () => 100 }, play() { this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; } }, over || {});
}
const IT = { id: "v", start: 0, in: 0, out: 100, speed: 1, rev: false };
function ent(over) { const e = { el: fakeEl(over), md: { id: "main", dur: 100 }, mid: "main", key: "v" }; e.el._e = e; POOL.set(e.key, e); return e; }
"""


def _js():
    src = (ROOT / "editor.html").read_text(encoding="utf-8")
    i = src.index("\nconst elOff =") + 1
    j = src.index("\n// 기본 화면 배치", i)
    out = [src[i:j]]
    for f in ("play", "pause"):
        k = src.index("\nfunction " + f + "(") + 1
        out.append(src[k:src.index("\n}", k) + 2])
    return STUBS + "\n".join(out)


@unittest.skipUnless(NODE, "node 가 없어요")
class PlayHoldTest(unittest.TestCase):
    def run_js(self, body):
        prog = _js() + "\n(async () => {\n" + body + "\n})().catch(e => { console.error(e.stack); process.exit(3); });"
        r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_fast_shuttle_chases_instead_of_hold(self):
        """검토 재현: 8배로 훑는 중 재생기가 0.3초 넘게 뒤지고 1.5초 안에 한 번 맞췄던 것 → 예전(5252d77)은 '멀어짐' 기다림으로 모두 멈춤 ·
        이제 4·8배는 예전(11bf533)처럼 바로 그 자리(+0.06초·속도)로 맞추고 시계는 계속 · 1·2배는 그대로 기다림."""
        res = self.run_js(r"""
          const out = {};
          for (const sh of [1, 2, 4, 8]) {
            POOL.clear(); HOLD = null; playing = true; shuttle = sh; T = 11; clock = { t0: 0, at: performance.now() - 1000 };
            const e = ent({ paused: false, currentTime: 10, playbackRate: sh });
            e.hardAt = performance.now() - 200;  // 방금(0.2초 전) 한 번 맞췄음
            syncEl(e, IT, 11);
            out[sh] = { hold: HOLD && HOLD.why, ct: +e.el.currentTime.toFixed(3), paused: e.el.paused };
          }
          process.stdout.write(JSON.stringify(out));""")
        self.assertEqual(res["1"]["hold"], "far")
        self.assertEqual(res["2"]["hold"], "far")
        self.assertEqual(res["1"]["paused"], True)  # 기다림 = 모든 재생기 그 자리에 멈춤
        for sh in ("4", "8"):
            self.assertIsNone(res[sh]["hold"], sh)
            self.assertEqual(res[sh]["paused"], False, sh)
            self.assertAlmostEqual(res[sh]["ct"], 11 + 0.06 * int(sh), places=3)

    def test_fast_shuttle_no_starve_hold_or_need_growth(self):
        """8배로 훑는 중 재생기가 0.15초 넘게 못 받아도(찾는 중) 기다림을 시작하지 않음 → 다음 재생에 받아 둘 길이(holdNeed)도 그대로 0.5초 ·
        1배에서는 같은 상황이 '못 받음' 기다림."""
        res = self.run_js(r"""
          const out = {};
          for (const sh of [1, 8]) {
            POOL.clear(); HOLD = null; holdNeed = 0.5; holdLast = performance.now() - 1000; playing = true; shuttle = sh; T = 20;
            clock = { t0: 20, at: performance.now() - 1000 };
            const e = ent({ paused: false, currentTime: 20, seeking: true, readyState: 1 });
            NEEDF = [e];
            const t0 = performance.now();
            for (let k = 0; k <= 20; k++) holdCheck(t0 + k * 16);  // 0.32초 동안 화면마다
            out[sh] = { hold: HOLD && HOLD.why, need: holdNeed };
          }
          process.stdout.write(JSON.stringify(out));""")
        self.assertEqual(res["1"]["hold"], "starve")
        self.assertEqual(res["8"], {"hold": None, "need": 0.5})

    def test_play_clears_badge(self):
        """검토 재현: 받아 두는 중 '불러오는 중…' 이 보이는 채로 J(거꾸로) → 예전엔 표시가 거꾸로 재생 내내 남음 · 이제 play() 가 지움.
        이미 받아 둔 곳에서 L 로 다시 앞으로 가도(새 기다림 없음) 지워진 채."""
        res = self.run_js(r"""
          POOL.clear(); playing = true; shuttle = 1; T = 88; HOLD = { since: performance.now() - 800, why: "start", need: 0.5, lead: 80, frn: 0 };
          BADGE.hidden = false;
          play(-1);
          const afterJ = { badge: BADGE.hidden, hold: HOLD, shuttle };
          BADGE.hidden = false;  // (L 로 돌아올 때도 남아 있었다고 치고)
          NEEDT = T; NEEDF = [];  // 필요한 재생기가 모두 받아 둠 → 새 기다림 없이 바로 출발
          play(1);
          process.stdout.write(JSON.stringify({ afterJ, afterL: { badge: BADGE.hidden, hold: HOLD, shuttle } }));""")
        self.assertEqual(res["afterJ"], {"badge": True, "hold": None, "shuttle": -1})
        self.assertEqual(res["afterL"], {"badge": True, "hold": None, "shuttle": 1})


if __name__ == "__main__":
    unittest.main()
