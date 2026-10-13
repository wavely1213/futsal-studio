"""편집실 B3 검토 고침 (D-285~D-288) — editor.html 의 실제 함수를 node 로 그대로 실행. 실행: python3 -m unittest tests.test_edit_follow

- '여기는 줄이지 않기'(noDuck) 구간이 잔물결 편집에서 그 자리 내용과 함께 옮겨짐: 예전에는 시작이 자른 끝 뒤인 구간만 당겨져서
  자른 구간 안에 있던 구간은 엉뚱한 내용 위에 남고, 걸친 구간은 제자리라 말소리 위에서 음악이 그대로 컸다 (잘라내고 당기기·잔물결 삭제·
  빈 곳 지우기·대사 잘라내기·끼워 넣기·잔물결 다듬기·속도 바꾸기·MSG 빼기 모두 군더더기 정리와 같은 tlRemap 규칙).
- 자막 목록 ✂(대사 잘라내기)도 자막이 따라가는 클립(capItems) 기준: L 컷·B롤 덮어쓰기에서 들리는 대사를 자르고, 흐린 대사는 안 자름.
- 연결된 대사 클립이 자리가 막혀 옮겨질 때 효과음 트랙(대사 역할 + voiceFx false)으로는 안 감 (역할 3가지가 같아야).
- 넣을 트랙: 말이 든 보관함 영상의 소리는 대사 트랙(A1 이 비면 A1), 앱 효과음은 효과음 트랙(빈 음악 트랙은 효과음 트랙으로 바꿈).
- '여기는 줄이지 않기' 구간 고르기: I·O 둘 다 → 고른 클립(음악 클립 빼고) → 재생 위치의 말 구간.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

LINES = ("const FPS =", "const fr =", "const linkId =", "const byId =", "const mmss =", "const clamp =", "const nid =", "const niceName =", "const EPS =", "const MED =", "const sp =",
         "const iEnd =", "const iLen =", "const mt =", "const tlOf =", "const TRK =", "const aTracks =", "const itemsOn =", "const isV =", "const mdOf =",
         "const unlocked =", "const mDur =", "const linkedOf =", "const r6 =", "const r3 =", "const headTopic =", "const roleOf =", "const ROLE_NAME =",
         "const lbl =", "const isSfxMd =", "const isTalkMd =", "const sfxOnly =", "const MCAPS =", "const TAC_REFOLLOW =", "const seqLen =",
         "const DUCK_PAD =", "let SPEECH =", "const inIv =", "const targetOf =", "const BGM_FADE =", "const MSG_KIND =")
FUNCS = ("mergeIv", "tlRemap", "swapIn", "topicAfter", "capItems", "minusIv", "noDuckSpans", "ndRange", "speechSpans", "tacRefollow", "tacFollowCut",
         "tacFollowHold", "tacRippleCut", "tacRippleHold", "neighbor", "prevEnd", "nextStart", "edgeOp", "setSpeed", "msgRemove", "autoDest",
         "ensureTrack", "setRole", "videoEnd", "placeMedia", "dropOn", "otherCapNote")
STUBS = r"""
let S, P = { captions: [], speech: [] }, selI = new Set(), linkOn = true, markIn = null, markOut = null, selX = null, ver = 0, primary = null, T = 0, ROW = null;
const TOASTS = [], $ = () => null;
const commit = () => {}, changed = () => { ver++; }, seek = () => {}, toast = m => TOASTS.push(m), renderTimeline = () => {}, renderPanels = () => {};
const renderRec = () => {}, renderMediaBin = () => {}, refitMusic = () => {}, musicFit = () => false, revealRow = () => {}, addGfx = () => {};
const total = () => Math.max(0, ...S.items.map(iEnd)), audioAt = () => [];
const addMedia = async (src, f) => MED[f], rowAtY = () => ROW, xToT = e => e.t, tacVideoAt = () => null; // 전술 그림 없음
"""


def _js():
    src = (ROOT / "editor.html").read_text(encoding="utf-8")
    out = []
    for n in LINES:  # 여러 줄에 걸친 선언(MSG_KIND 등)은 괄호가 맞을 때까지
        i = src.index("\n" + n) + 1
        j = src.index("\n", i)
        while any(src[i:j].count(a) != src[i:j].count(b) for a, b in ("{}", "()", "[]")):
            j = src.index("\n", j + 1)
        out.append(src[i:j])
    for f in FUNCS:
        i = src.index("function " + f + "(")
        if src[i - 6:i] == "async ":
            i -= 6
        first = src[i:src.index("\n", i)]
        out.append(first if first.count("{") and first.count("{") == first.count("}") else src[i:src.index("\n}", i) + 2])
    i = src.index("/* ---------- 편집 기본 동작 ---------- */")
    out.append(src[i:src.index("function deleteGfx(", i)])
    return STUBS + "\n".join(out)


def V(i, start, a, b, link=None, media="main", track="V1", **kw):
    return dict({"id": i, "track": track, "media": media, "start": start, "in": a, "out": b, "speed": 1, "rev": False, "link": link,
                 "reframe": 0.5, "fit": "auto", "fx": {}, "color": {}}, **kw)


def A(i, start, a, b, link=None, media="main", track="A1", **kw):
    return dict({"id": i, "track": track, "media": media, "start": start, "in": a, "out": b, "speed": 1, "rev": False, "link": link,
                 "fx": {}, "gain": 0, "fadeIn": 0, "fadeOut": 0, "mute": False}, **kw)


def tracks(extra=()):
    tr = [{"id": "V1", "k": "v", "lock": False, "hide": False, "target": True, "h": 1},
          {"id": "V2", "k": "v", "lock": False, "hide": False, "target": False, "h": 1},
          {"id": "A1", "k": "a", "lock": False, "mute": False, "solo": False, "target": True, "h": 1, "vol": 0, "role": "dialog"},
          {"id": "A2", "k": "a", "lock": False, "mute": False, "solo": False, "target": False, "h": 1, "vol": 0, "role": "music"},
          {"id": "A3", "k": "a", "lock": False, "mute": False, "solo": False, "target": False, "h": 1, "vol": 0, "role": "music"}]
    for t in extra:
        x = next((y for y in tr if y["id"] == t["id"]), None)
        if x:
            x.update(t)
        else:
            tr.append(dict({"k": "a", "lock": False, "mute": False, "solo": False, "target": False, "h": 1, "vol": 0}, **t))
    return tr


def seq(items, **kw):
    q = {"id": "s", "format": "long", "items": items, "tracks": tracks(), "trans": [], "titles": [], "shapes": [], "tactics": [], "markers": [],
         "duck": {"on": True, "amount": -14}}
    q.update(kw)
    return q


def long_pair(n=40.0):
    return [V("v", 0, 0, n, "L"), A("a", 0, 0, n, "L")]


CAPS = [  # 원본 시각 자막 (tests.test_caption_follow_audio 와 같은 꼴)
    {"id": "c1", "start": 25.0, "end": 27.0, "text": "공을 먼저 보세요"},
    {"id": "c2", "start": 34.1, "end": 36.0, "text": "디딤발은 공 옆으로"},
    {"id": "c4", "start": 97.5, "end": 99.5, "text": "그제서야 어디로 갈지"},
]


def lcut():
    return [V("v1", 0, 24.0, 34.0, "LA"), A("a1", 0, 24.0, 37.3, "LA"), V("v2", 10.0, 97.0, 103.6, "LB"), A("a2", 13.3, 100.3, 103.6, "LB")]


def broll():
    return [V("v1", 0, 24.0, 30.0, "L1"), A("a1", 0, 24.0, 48.0, "L1"), V("vb", 6.0, 20.0, 26.0, media="brl"), V("v3", 12.0, 36.0, 48.0, "L1")]


def rnd(x):
    return [[round(a, 3), round(b, 3)] for a, b in x]


@unittest.skipUnless(NODE, "node 가 없어요")
class EditFollowTest(unittest.TestCase):
    def run_js(self, body, data=None):
        prog = _js() + "\nconst D = " + json.dumps(data) + ";\n(async () => {\n" + body + "\n})().catch(e => { console.error(e.stack); process.exit(3); });"
        r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    # ---------- '여기는 줄이지 않기' 가 잔물결 편집에서 내용과 함께 (D-286) ----------
    def nd_after(self, op, nd, items=None, **kw):
        q = seq(items or long_pair(), noDuck=nd, **kw)
        return self.run_js("S = D; MED.main = { id: 'main', kind: 'video', dur: 1000, audio: true };\n" + op +
                           "\nprocess.stdout.write(JSON.stringify({ nd: S.noDuck, total: total(), toasts: TOASTS }));", q)

    def test_extract_range_cut_inside_across(self):
        """검토 재현: [12,15]·[18,26]·[30,34] 에서 10~20초 잘라내고 당기기 → 예전 [[12,15],[18,26],[20,24]] (안 [12,15] 는 22~25초 내용 위에,
        걸친 [18,26] 은 28~36초 내용 위에) · 이제 군더더기 정리(remapSeq)와 같게 [[10,16],[20,24]]."""
        r = self.nd_after("extractRange(10, 20);", [[12, 15], [18, 26], [30, 34]])
        self.assertEqual(r["total"], 30)
        self.assertEqual(rnd(r["nd"]), [[10, 16], [20, 24]])
        # 출력 렌즈 재현: [40,50] 에서 44~46 → [40,48] (말 '부드럽게 받아야 해요.' 가 범위 밖에 남음) · 43~47 이 [44,46] 을 다 덮으면 없어짐 · 38~42 → [38,46]
        q = long_pair(80.0)
        self.assertEqual(rnd(self.nd_after("extractRange(44, 46);", [[40, 50]], q)["nd"]), [[40, 48]])
        self.assertEqual(self.nd_after("extractRange(43, 47);", [[44, 46]], q)["nd"], [])
        self.assertEqual(rnd(self.nd_after("extractRange(38, 42);", [[40, 50]], q)["nd"]), [[38, 46]])
        self.assertEqual(rnd(self.nd_after("extractRange(48, 52);", [[40, 50]], q)["nd"]), [[40, 48]])

    def test_delete_sel_ripple_and_gap(self):
        items = [V("v1", 0, 0, 10, "L1"), A("a1", 0, 0, 10, "L1"), V("v2", 10, 10, 20, "L2"), A("a2", 10, 10, 20, "L2"),
                 V("v3", 20, 20, 30, "L3"), A("a3", 20, 20, 30, "L3")]
        r = self.nd_after("selI = new Set(['v2', 'a2']); deleteSel(true);", [[8, 12], [14, 16], [25, 27]], items)
        self.assertEqual(r["total"], 20)
        self.assertEqual(rnd(r["nd"]), [[8, 10], [15, 17]])  # 걸친 구간은 줄고 · 지운 클립 안 구간은 없어지고 · 뒤는 당겨짐
        gap = [V("v1", 0, 0, 10), V("v2", 14, 14, 30)]
        r = self.nd_after("deleteGap({ k: 'gap', track: 'V1', s: 10, e: 14 });", [[8, 12], [16, 20]], gap)
        self.assertEqual(r["total"], 26)
        self.assertEqual(rnd(r["nd"]), [[8, 10], [12, 16]])

    def test_insert_gap_extends_straddling(self):
        """검토 재현: 10초에 4초 끼워 넣기 → 예전 [5,15] 그대로(지킨 원본 11~15초가 15~19초로 밀려 다시 줄어듦) · 이제 [5,19]."""
        self.assertEqual(rnd(self.nd_after("insertGap(10, 4, mediaTracks());", [[5, 15]])["nd"]), [[5, 19]])
        # 끼운 자리에서 끝나는 구간은 그대로 · 끼운 자리에서 시작하는 구간은 밀림
        self.assertEqual(rnd(self.nd_after("insertGap(10, 4, mediaTracks());", [[2, 10], [10, 12], [20, 22]])["nd"]), [[2, 10], [14, 16], [24, 26]])

    def test_ripple_trim_and_speed(self):
        two = [V("x", 0, 0, 10), V("y", 10, 10, 20)]
        r = self.nd_after("edgeOp('ripple', ['x'], 'r', -2);", [[8.5, 9.5], [15, 18]], two)  # 끝을 2초 줄임: 사라진 8~10초 안 구간은 없어짐
        self.assertEqual((r["total"], rnd(r["nd"])), (18, [[13, 16]]))
        r = self.nd_after("edgeOp('ripple', ['y'], 'l', 2);", [[11, 14], [15, 18]], two)  # 앞을 2초 줄임: 잘린 머리 10~12초
        self.assertEqual((r["total"], rnd(r["nd"])), (18, [[10, 12], [13, 16]]))
        r = self.nd_after("edgeOp('ripple', ['x'], 'r', 3);", [[9, 12], [15, 18]], [V("x", 0, 0, 10), V("y", 10, 30, 40)])  # 꼬리 늘림 = 끼움
        self.assertEqual((r["total"], rnd(r["nd"])), (23, [[9, 15], [18, 21]]))
        r = self.nd_after("setSpeed([S.items[0]], 2, true);", [[12, 14], [6, 11]], two)  # 0~10초를 2배속: 뒤는 5초 당겨짐
        self.assertEqual((r["total"], rnd(r["nd"])), (15, [[5, 6], [7, 9]]))

    def test_extract_source_moves_no_duck(self):
        r = self.nd_after("P = { captions: [{ id: 'c', start: 10, end: 12, text: '말' }] }; extractSource(10, 12);", [[11, 14], [20, 22]])
        self.assertEqual(rnd(r["nd"]), [[10, 12], [18, 20]])

    def test_msg_remove_inserted_scene(self):
        """검토 재현: 5~10초에 끼운 5초 다시 보기를 'MSG 빼기' → 마커는 22 → 17 로 당겨지는데 예전 noDuck 은 [20,24] 그대로 · 이제 [15,19]."""
        items = [V("v1", 0, 0, 5, "L1"), A("a1", 0, 0, 5, "L1"), V("rep", 5, 0, 5, noCaps=True), V("v2", 10, 5, 23, "L2"), A("a2", 10, 5, 23, "L2")]
        q = seq(items, noDuck=[[20, 24], [6, 8], [3, 7]], markers=[{"id": "m", "t": 22}],
                msg={"events": [{"id": "e1", "kind": "replay", "t": 5, "ins": {"start": 5, "len": 5}, "refs": {"items": ["rep"]}}], "summary": None})
        r = self.run_js("S = D; msgRemove('e1'); process.stdout.write(JSON.stringify({ nd: S.noDuck, mk: S.markers.map(m => m.t), total: total() }));", q)
        self.assertEqual((r["total"], r["mk"]), (23, [17]))
        self.assertEqual(rnd(r["nd"]), [[3, 5], [15, 19]])

    # ---------- 자막 목록 ✂ = 자막이 따라가는 클립 기준 (D-285) ----------
    def cut_src(self, items, a, b, caps=CAPS):
        q = seq(items)
        return self.run_js("S = D.q; P = { captions: D.caps }; const t0 = total(); extractSource(D.a, D.b);\n"
                           "process.stdout.write(JSON.stringify({ t0, t1: total(), toasts: TOASTS, items: S.items.map(i => [i.id, i.track, +i.start.toFixed(3), +i.in.toFixed(3), +i.out.toFixed(3)]) }));",
                           {"q": q, "caps": caps, "a": a, "b": b})

    def test_cut_heard_line_in_lcut(self):
        """L 컷: 들리는 '디딤발은 공 옆으로'(원본 34.1~36초 · 앞말 소리 A1 이 10.1~12초에) → 예전 '이미 잘려나간 대사예요' · 이제 그 1.9초를 V1·A1 에서 잘라냄."""
        r = self.cut_src(lcut(), 34.1, 36.0)
        self.assertEqual(r["toasts"][-1][:9], "대사 구간 1.9")
        self.assertAlmostEqual(r["t0"] - r["t1"], 1.9, 3)
        it = {x[0]: x for x in r["items"]}
        self.assertEqual(it["a2"][2], 11.4)  # 뒤 소리도 같이 당겨짐 (13.3 → 11.4) · 싱크 유지
        self.assertEqual(it["v2"][2], 10.0)  # 다음 영상은 잘린 자리(10.1~12) 전에 시작 → 걸친 만큼 잘림 · 시작은 그대로

    def test_cut_greyed_line_does_nothing(self):
        """L 컷에서 영상만 보이고 소리는 없는 '그제서야 어디로 갈지'(흐림) → 예전에는 들리는 앞말 소리 1.6초를 잘랐음 · 이제 '이미 잘려나간 대사예요'."""
        r = self.cut_src(lcut(), 97.5, 99.5)
        self.assertEqual(r["toasts"], ["이미 잘려나간 대사예요"])
        self.assertEqual(r["t0"], r["t1"])

    def test_cut_line_under_broll(self):
        r = self.cut_src(broll(), 34.1, 36.0)  # B롤(6~12초) 아래 A1 에서 들리는 대사 → 예전 '이미 잘려나간 대사예요'
        self.assertAlmostEqual(r["t0"] - r["t1"], 1.9, 3)
        it = {x[0]: x for x in r["items"]}
        self.assertEqual((it["vb"][2], round(it["vb"][4] - it["vb"][3], 3)), (6.0, 4.1))  # B롤도 같은 1.9초가 빠짐 (6 → 4.1초 길이)
        self.assertEqual(it["v3"][2], 10.1)  # V1·A1 같이 당겨짐

    # ---------- 연결된 대사 클립은 효과음 트랙으로 안 옮겨짐 (D-285) ----------
    def test_follow_to_keeps_role(self):
        tr = tracks([{"id": "A2", "role": "dialog"}, {"id": "A3", "role": "dialog", "voiceFx": False}])
        items = [V("v1", 0, 0, 10, "L1"), A("a1", 0, 0, 10, "L1"), V("v2", 10, 10, 15, "L2"), A("a2", 10, 10, 15, "L2"),
                 V("v3", 15, 15, 60, "L3"), A("a3", 15, 15, 60, "L3"), A("vo", 5, 100, 135, track="A2"),
                 V("b", 40, 0, 5, "LB", media="brl", track="V2"), A("ba", 40, 0, 5, "LB", media="brl", track="A2")]
        body = "S = D; extractRange(10, 15); const ba = S.items.find(i => i.id === 'ba'); process.stdout.write(JSON.stringify([ba.track, ba.start]));"
        self.assertEqual(self.run_js(body, seq(items, tracks=tr)), ["A2", 40])  # 예전: A3(효과음)으로 옮겨져 자막·목소리 보정·줄이기 기준을 잃음
        tr2 = tracks([{"id": "A2", "role": "dialog"}, {"id": "A3", "role": "dialog"}])
        self.assertEqual(self.run_js(body, seq(items, tracks=tr2)), ["A3", 35])  # 같은 대사 트랙이면 예전처럼 옮겨 싱크 유지

    # ---------- 넣을 트랙: 말이 든 영상 → 대사 트랙 · 효과음 → 효과음 트랙 (D-287) ----------
    def test_auto_dest_roles(self):
        body = """
const talk = { id: "o", kind: "video", src: "videos", file: "다른 영상.mp4", audio: true, dur: 4 }, brl = { id: "b", kind: "video", src: "assets", file: "b.mp4", audio: true, dur: 4 };
const sfx = { id: "s", kind: "audio", src: "assets", file: "효과음_휙.wav", audio: true, dur: 0.5 }, song = { id: "m", kind: "audio", src: "assets", file: "bgm.mp3", audio: true, dur: 60 };
Object.assign(MED, { o: talk, b: brl, s: sfx, m: song }); MCAPS.o = [{ id: "x", start: 0, end: 2, text: "말" }];
S = D; const r = {};
r.talkEnd = autoDest(talk, 30, 4, false); r.talkMid = autoDest(talk, 5, 4, false); r.brl = autoDest(brl, 5, 4, false);
r.talkSrc = autoDest(talk, 30, 4, true);
const keep = MCAPS.o; delete MCAPS.o; r.noCaps = autoDest(talk, 5, 4, false); MCAPS.o = keep; // 자막 없는 영상은 예전처럼
r.sfx = autoDest(sfx, 5, 0.5, false); r.song = autoDest(song, 0, 60, false);
S.items.push({ id: "bg", track: "A2", media: "m", start: 0, in: 0, out: 30, speed: 1 });
r.sfxBgm = autoDest(sfx, 5, 0.5, false);
S.items.push({ id: "bg3", track: "A3", media: "m", start: 0, in: 0, out: 30, speed: 1 });
r.sfxFull = autoDest(sfx, 5, 0.5, false);
S.tracks.push({ id: "A4", k: "a", role: "dialog" }); r.talkMid2 = autoDest(talk, 5, 4, false);
process.stdout.write(JSON.stringify(r));"""
        r = self.run_js(body, seq(long_pair(30.0)))
        self.assertEqual(r["talkEnd"], ["V2", "A1"])  # 예전 V2·A2(음악): 그 영상 자막이 안 뜨고 음악처럼 줄어듦
        self.assertEqual(r["talkSrc"], ["V2", "A1"])  # 소스 모니터 덮어쓰기도
        self.assertEqual(r["talkMid"], ["V2", "A4"])  # A1 이 차 있으면 새 대사 트랙
        self.assertEqual(r["talkMid2"], ["V2", "A4"])  # 빈 대사 트랙이 있으면 그 트랙
        self.assertEqual(r["brl"], ["V2", "A2"])  # 자막 없는 영상 소리(B롤 현장음)는 예전처럼
        self.assertEqual(r["noCaps"], ["V2", "A2"])
        self.assertEqual(r["sfx"], [None, "A2"])  # 빈 음악 트랙 (넣을 때 효과음 트랙으로 바꿈 · 예전: 화면 밖 새 A4)
        self.assertEqual(r["song"], [None, "A2"])
        self.assertEqual(r["sfxBgm"], [None, "A3"])  # 배경음악이 든 A2 는 안 바꿈
        self.assertEqual(r["sfxFull"], [None, "A4"])

    def test_place_and_drop_roles(self):
        body = """
const sfx = { id: "s", kind: "audio", src: "assets", file: "효과음_휙.wav", audio: true, dur: 0.5 }, song = { id: "m", kind: "audio", src: "assets", file: "bgm.mp3", audio: true, dur: 60 };
const talk = { id: "o", kind: "video", src: "videos", file: "다른 영상.mp4", audio: true, dur: 4 };
Object.assign(MED, { main: { id: "main", kind: "video", dur: 1000, audio: true }, "효과음_휙.wav": sfx, "bgm.mp3": song, "다른 영상.mp4": talk, s: sfx, m: song, o: talk });
MCAPS.o = [{ id: "x", start: 0, end: 2, text: "말" }];
S = D; const r = [], role = id => roleOf(S.tracks.find(t => t.id === id)), last = () => S.items[S.items.length - 1];
// 효과음을 빈 '음악 2' 줄에 끌어 놓음 → 그 트랙이 효과음 트랙
ROW = "A2"; await dropOn("media", "assets|효과음_휙.wav", { t: 2, clientY: 0 }); r.push([last().track, role("A2"), TOASTS.pop()]);
// 배경음악이 든 A3 줄에 효과음 → 효과음 트랙(A2)으로 · 왜 옮겼는지 안내
S.items.push({ id: "bg", track: "A3", media: "m", start: 0, in: 0, out: 30, speed: 1 });
ROW = "A3"; await dropOn("media", "assets|효과음_휙.wav", { t: 9, clientY: 0 }); r.push([last().track, role("A3"), TOASTS.pop()]);
// 대사 줄(A1)에 놓아도 효과음 트랙
ROW = "A1"; await dropOn("media", "assets|효과음_휙.wav", { t: 12, clientY: 0 }); r.push([last().track]);
// 말이 든 영상을 V2 줄에 → 소리는 A1(그 자리 비어 있음)
ROW = "V2"; await dropOn("media", "videos|다른 영상.mp4", { t: 30, clientY: 0 }); r.push(S.items.slice(-2).map(i => i.track));
// 줄 밖(＋ 단추)에 말이 든 영상, A1 이 차 있는 곳 → 새 대사 트랙
ROW = null; T = 5; await dropOn("media", "videos|다른 영상.mp4", null); r.push([...S.items.slice(-2).map(i => i.track), role(S.items[S.items.length - 1].track)]);
process.stdout.write(JSON.stringify(r));"""
        r = self.run_js(body, seq(long_pair(30.0)))
        self.assertEqual(r[0][:2], ["A2", "sfx"])  # 예전: A2(음악)에 놓여 말할 때 -14dB
        self.assertIn("'음악 2' 트랙을 효과음 트랙으로 바꿨어요", r[0][2])
        self.assertEqual(r[1][:2], ["A2", "music"])
        self.assertIn("A3 트랙은 배경음악이 든 트랙이라 효과음 트랙에 넣었어요", r[1][2])
        self.assertEqual(r[2], ["A2"])
        self.assertEqual(r[3], ["V2", "A1"])
        self.assertEqual(r[4], ["V2", "A4", "dialog"])

    # ---------- '여기는 줄이지 않기' 구간 고르기 (D-288) ----------
    def test_nd_range(self):
        body = """
S = D; P = { captions: [{ id: "c", start: 4, end: 8, text: "말" }, { id: "d", start: 20, end: 22, text: "말" }], speech: [] };
const sel = ids => new Set(ids), r = [];
r.push(ndRange(2, 6, sel([]), 0));                    // I·O 둘 다
r.push(ndRange(null, null, sel(["bgm"]), 5));          // 배경음악 클립만 고름 → 재생 위치의 말 구간 (예전: 편집본 전체 [0,28])
r.push(ndRange(20, null, sel(["a"]), 0));              // I 하나만 남음 + 클립 고름 → 고른 클립 (예전: 20~끝)
r.push(ndRange(null, null, sel([]), 14));              // 말 없는 곳
P.captions = []; r.push(ndRange(null, null, sel([]), 5)); // 받아쓴 말 없음
process.stdout.write(JSON.stringify(r));"""
        q = seq(long_pair(28.0) + [A("bgm", 0, 0, 28, media="m", track="A2")])
        r = self.run_js(body, q)
        self.assertEqual(r[0], {"a": 2, "b": 6, "how": "I·O 구간"})
        self.assertEqual(r[1]["how"], "재생 위치의 말 구간")
        self.assertEqual([round(r[1]["a"], 3), round(r[1]["b"], 3)], [3.8, 8.55])  # 말 4~8초 ± 0.2 + 말 뒤 쉼 0.35
        self.assertEqual(r[2], {"a": 0, "b": 28, "how": "고른 클립"})
        self.assertIn("err", r[3])
        self.assertIn("err", r[4])


if __name__ == "__main__":
    unittest.main()
