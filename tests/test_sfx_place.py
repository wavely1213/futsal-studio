"""효과음이 제자리에 (D-370~D-378) — 저장소 폴더에서 python3 -m unittest tests.test_sfx_place
실은 효과음이 제 길이로 들림(예전 판: 앞 5ms 뒤 무음 · 편집실 캐시도 다시 만듦) · 소리 모양(맞출 점) · 뜻 표 · 말 지키기 · 소리만 남기기 ·
농담 뒤 웃음 박자 · 시작 신호(사장님 쇼츠 '혼난다. 시작!') · 배운 스타일 효과음 자리 · 시험 원본으로 만든 편집본을 렌더 없이 객관 검사
(맞출 점이 기준에서 ±1프레임 · 작은 소리가 아니면 크게 들리는 부분이 낱말과 안 겹침 · 자동 효과음마다 넣은 이유 · 10초 순간 수).
가게 영상으로 하는 큰 검사(3개 원본 × 6 스타일 × 3 양)는 개발 폴더의 check_sfx.py (렌더 없음 · 실제 섞기 들림 검사 --probe)."""
import json
import re
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import editor  # noqa: E402
import make_msg_fixture as F  # noqa: E402
import msg  # noqa: E402
import plan  # noqa: E402
import sfxlib  # noqa: E402

FRAME = 1.0 / 30
SHORT_OK = {"틱": 0.008, "틱2": 0.008, "지지직": 0.008, "뽁": 0.03}   # 원래 아주 짧은 소리 (Kenney 원본도 10~35ms)


def read_wav(p):
    import numpy as np
    with wave.open(str(p), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels()).astype(np.float64) / 32767.0


def audible_sec(x, hop=240):
    """들리는 길이(초): 5ms 창 크기가 최대보다 30dB 안쪽인 첫 창 ~ 마지막 창."""
    import numpy as np
    m = np.abs(x).max(axis=1)
    n = len(m) // hop
    env = 20 * np.log10(np.sqrt((m[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-12)
    loud = np.nonzero(env >= env.max() - 30)[0]
    return float((loud[-1] - loud[0] + 1) * hop / sfxlib.SR)


class ShippedSounds(unittest.TestCase):
    """D-370: sfx/*.ogg 26개가 앞 5ms 만 소리이고 나머지는 0 이라 '딸깍'으로만 들렸음 (짠·딩동·레벨업·뻥 …)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="효과음 길이 "))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_sound_is_audible_and_full_length(self):
        import numpy as np
        for name, (src, _) in sfxlib.CATALOG.items():
            fn = sfxlib.ensure_sfx(name, self.tmp)
            x = read_wav(self.tmp / fn)
            aud = audible_sec(x)
            self.assertGreaterEqual(aud, SHORT_OK.get(name, 0.06), f"{name} 들리는 길이 {aud:.3f}초")
            if not src.startswith("@"):
                self.assertAlmostEqual(20 * np.log10(np.abs(x).max()), sfxlib.PEAK_DB, delta=0.5, msg=name)
                self.assertFalse(sfxlib.cut_short(self.tmp / fn), name)

    def test_known_lengths(self):
        """원본(Kenney zip · sha256 은 LICENSE.txt)에서 잰 들리는 길이의 80% 넘게 (tests/make_sfx_assets.py --check 와 같은 값)."""
        want = {"딩동": 0.275, "레벨업": 0.945, "짠": 0.24, "뻥": 0.29, "맑은 짧은 음악2": 1.26, "쿵": 0.84, "경쾌 짧은 음악": 0.33}
        for name, sec in want.items():
            x = read_wav(self.tmp / sfxlib.ensure_sfx(name, self.tmp))
            self.assertGreaterEqual(audible_sec(x), 0.8 * sec, name)

    def test_old_truncated_cache_is_rebuilt(self):
        """예전 판이 만든 잘린 캐시(edit_media/효과음_딩동.wav: 앞 5ms 뒤 0)는 크기가 맞아도 다시 만듦 — 이름이 같아 예전 편집본도 다음 내보내기부터 제 소리."""
        fn = sfxlib.sfx_file_name("딩동")
        x = [[0.7, 0.7] if k < 240 else [0.0, 0.0] for k in range(int(0.3 * sfxlib.SR))]
        (self.tmp / fn).write_bytes(sfxlib._wav_bytes(x))
        self.assertTrue(sfxlib.cut_short(self.tmp / fn))
        sfxlib.ensure_sfx("딩동", self.tmp)
        self.assertGreater(audible_sec(read_wav(self.tmp / fn)), 0.2)
        self.assertFalse(sfxlib.cut_short(self.tmp / fn))

    def test_shape_hit_points(self):
        """맞출 점(D-371): 부딪히는 소리는 첫소리 · 점점 커지는 소리(휙·라이저·두둥)는 가장 큰 곳 · 작은 소리 표시."""
        sh = {n: sfxlib.shape(n, self.tmp / sfxlib.ensure_sfx(n, self.tmp)) for n in ("휙", "라이저", "두둥", "딩동", "짠", "뽁", "호루라기")}
        self.assertGreater(sh["휙"]["hit"], 0.15)
        self.assertGreater(sh["라이저"]["hit"], 0.9)
        self.assertGreater(sh["두둥"]["hit"], 0.5)
        for n in ("딩동", "짠", "뽁", "호루라기"):
            self.assertLess(sh[n]["hit"], 0.03, n)
        self.assertTrue(sh["뽁"]["soft"])
        self.assertFalse(sh["딩동"]["soft"])


def _seg(a, b, t):
    toks = t.split()
    d = (b - a) / len(toks)
    return {"start": a, "end": b, "text": t, "words": [{"w": w, "s": round(a + k * d, 2), "e": round(a + (k + 1) * d - 0.04, 2)} for k, w in enumerate(toks)]}


class MomentsTest(unittest.TestCase):
    SIG = {"name": "x.mp4", "duration": 30.0, "tags": {}, "tagStep": 0.48, "onsets": [], "hits": [], "demo": [], "junk": [], "motion": [], "motionStep": 0.5,
           "cuts": []}

    def test_owner_rapid_lines_start_cue(self):
        """사장님 쇼츠('감독님, 요 세 마디만 하면 돼.' … '혼난다. 시작!'): 예전에는 순간이 하나도 없었음 → 시작 신호는 '시작!' 끝에 (D-373)."""
        segs = [_seg(0.3, 2.0, "감독님, 요 세 마디만 하면 돼."), _seg(2.2, 3.0, "다른 말 하지 마,"), _seg(3.1, 4.4, "순두시지 마, 감독님."),
                _seg(4.6, 5.6, "그리고 뒤에 말하면"), _seg(5.7, 7.0, "혼난다. 시작!")]
        cue = [m for m in msg.moments(self.SIG, segs) if m["kind"] == "cue"]
        self.assertEqual(len(cue), 1)
        self.assertAlmostEqual(cue[0]["t"], 6.96, delta=0.01)   # '시작!' 낱말 끝
        self.assertFalse([m for m in msg.moments(self.SIG, [_seg(1.0, 3.0, "시작하는 방법은 간단해요.")]) if m["kind"] == "cue"])

    def test_section_after_lead_word_and_split_count(self):
        """'그리고 마지막으로 …'도 다음 포인트 · 쉼에서 쪼개진 '하나,' / '둘, 셋 리듬으로 …'도 숫자 세기 (시각은 '하나')."""
        segs = [_seg(1.0, 2.5, "그리고 마지막으로 고개를 드세요."), _seg(4.0, 4.3, "하나,"), _seg(5.0, 7.0, "둘, 셋 리듬으로 움직여요.")]
        moms = msg.moments(self.SIG, segs)
        self.assertTrue(any(m["kind"] == "section" and m["t"] == 1.0 for m in moms), moms)
        cnt = [m for m in moms if m["kind"] == "count"]
        self.assertEqual(len(cnt), 1, moms)
        self.assertEqual(cnt[0]["t"], 4.0)

    def test_weak_praise_needs_a_kick(self):
        """설명 중 '좋아요'는 성공이 아님 — 바로 앞(3초 안)에 공 소리가 있을 때만 (D-376) · '나이스'는 그대로."""
        segs = [_seg(5.0, 7.0, "좋아요. 이렇게 공이 발에 붙어야 해요.")]
        self.assertFalse([m for m in msg.moments(self.SIG, segs) if m["kind"] == "success"])
        sig = dict(self.SIG, onsets=[3.4])
        self.assertTrue([m for m in msg.moments(sig, segs) if m["kind"] == "success"])
        self.assertTrue([m for m in msg.moments(self.SIG, [_seg(5.0, 6.0, "나이스! 들어갔어요.")]) if m["kind"] == "success"])

    def test_laugh_is_not_a_kick(self):
        """웃음 터지는 소리(순간 큰 소리)는 공 소리 후보에서 뺌 · 시범 안에서 말과 겹친 공 소리(hits)는 더함 (D-376)."""
        sig = dict(self.SIG, onsets=[3.0, 10.8, 11.1], hits=[3.0, 15.2, 21.0], demo=[[14.0, 17.0]], tags={"laugh": [0.0] * 22 + [0.3, 0.3, 0.0]})
        self.assertEqual(msg._kicks(sig), [3.0, 15.2])

    def test_laugh_beat_is_kept_after_punchline(self):
        """웃음소리가 붙은 농담 말 끝에서 조각이 바로 끝나면 웃음 박자까지 늘림 (다음 말 앞까지 · D-375)."""
        moms = [{"kind": "punchline", "a": 8.0, "b": 10.0, "t": 9.9, "laugh": 10.4}]
        words = [(8.0, 9.9, "넘어지거든요.", 0), (11.5, 12.0, "자,", 1)]
        out = msg.keep_laugh_beat([{"in": 0.0, "out": 10.2}, {"in": 13.0, "out": 20.0}], moms, words)
        self.assertAlmostEqual(out[0]["out"], 11.0, delta=0.01)   # 말 끝 + 0.9 · 웃음 시작 + 0.6
        out = msg.keep_laugh_beat([{"in": 0.0, "out": 10.2}, {"in": 13.0, "out": 20.0}], moms, [(8.0, 9.9, "x", 0), (10.5, 11.0, "y", 1)])
        self.assertEqual(out[0]["out"], 10.38)   # 다음 낱말 0.12초 앞까지만
        out = msg.keep_laugh_beat([{"in": 0.0, "out": 10.2}], moms, words, last=False)
        self.assertEqual(out[0]["out"], 10.2)   # 쇼츠 마지막 조각은 그대로


class LearnedStyleTest(unittest.TestCase):
    """D-377: 배운 스타일이 효과음 양만이 아니라 성격·자리도 배움."""

    def test_reference_anchor_counting(self):
        ev = {"sfx": [1.0, 5.1, 9.0, 20.0], "laugh": [[8.8, 10.0]]}
        caps = [{"a": 1.2, "kind": "emph"}, {"a": 30.0, "kind": "speech"}]
        sev = {"cuts": [5.0], "zooms": [{"t": 40.0}]}
        at = plan.sfx_anchors(ev, caps, sev)
        self.assertEqual((at["n"], at["caption"], at["cut"], at["laugh"], at["other"]), (4, 1, 1, 1, 1))
        self.assertEqual(at["capKinds"], {"emph": 1})

    def test_palette_from_reference(self):
        pal, at = msg.learned_sound({"fun": {"sfxAt": {"n": 10, "caption": 5, "cut": 1, "zoom": 1, "laugh": 1, "other": 2}}}, {"sfx": 4.0}, "speech")
        self.assertEqual(pal, "variety")
        self.assertEqual(at["caption"], 0.5)
        self.assertEqual(msg.learned_sound({"fun": {"sfxAt": {"n": 10, "cut": 8, "other": 2}}}, {"sfx": 4.0}, "variety")[0], "clean")
        # 예전 판 분석(자리 기록 없음): 참고 영상에 효과음이 없으면 깔끔한 소리 (예전: 늘 예능 소리 · 무게 0.3)
        self.assertEqual(msg.learned_sound({"fun": {"items": []}}, {}, "variety"), ("clean", None))
        self.assertEqual(msg.learned_sound({}, {"sfx": 2.0}, "mixed"), ("variety", None))
        st = msg.learned_aspects({"params": {}, "plan": {"fun": {"items": [], "sfxAt": {"n": 6, "zoom": 1, "cut": 5}}}})
        self.assertEqual(st["sound"]["palette"], "clean")
        self.assertEqual(st["sound"]["sfxAt"]["cut"], 0.83)


def _talk_tl(q, words):
    """편집본에서 들리는 낱말 (타임라인 [(시작, 끝, 낱말)])."""
    out = []
    for it in q["items"]:
        if it["track"] != "A1" or it.get("media") != "main" or it.get("mute"):
            continue
        a, i0, i1, sp = float(it["start"]), float(it["in"]), float(it["out"]), float(it.get("speed") or 1.0)
        for s, e, w, _ in words:
            if s < i1 - 0.02 and e > i0 + 0.02:
                out.append((a + (max(s, i0) - i0) / sp, a + (min(e, i1) - i0) / sp, w))
    return sorted(out)


class _Placed:
    """편집본 하나의 자동 효과음 (맞출 점 · 크게 들리는 부분 · 이유)."""

    def sfx_rows(self, q):
        names = {msg._mid(sfxlib.sfx_file_name(n)): n for n in sfxlib.CATALOG}
        rows = []
        for it in q["items"]:
            if it["track"] not in ("A2", "A5"):
                continue
            nm = names[it["media"]]
            sh = sfxlib.shape(nm, editor.ASSETS / sfxlib.sfx_file_name(nm))
            i0 = float(it.get("in") or 0.0)
            st = float(it["start"])
            lo, hi = msg._loud_part(st, sh, i0)
            rows.append({"name": nm, "start": st, "hit": st + max(0.0, sh["hit"] - i0), "lo": lo, "hi": hi, "why": it.get("why") or "",
                         "soft": sh["soft"] or "작게" in (it.get("why") or "")})
        return sorted(rows, key=lambda r: r["hit"])

    def check_seq(self, q, words):
        """모든 편집본에 늘 맞아야 하는 규칙 (D-371·D-372·D-374·D-378)."""
        rows = self.sfx_rows(q)
        talk = _talk_tl(q, words)
        titles = {t["id"]: t for t in q["titles"]}
        for r in rows:
            self.assertTrue(r["why"].startswith("자동 효과음 '"), r)
            if not r["soft"]:   # 작은 소리가 아니면 크게 들리는 부분이 낱말과 안 겹침
                over = [w for w in talk if w[0] < r["hi"] - 0.02 and w[1] > r["lo"] + 0.02]
                self.assertFalse(over, (q["name"], r, over))
            self.assertNotIn(r["name"], ("띠로리", "띠용"), r)   # 실수 놀림·속마음 띠용 없음 (D-373)
        # 글자에 붙은 소리: 맞출 점이 그 글자 첫 장면에서 ±1프레임 (사건 기록의 글자)
        for e in q["msg"]["events"]:
            ids = (e.get("refs") or {}).get("items") or []
            snd = [r for r in rows if any(it["id"] in ids and it["track"] in ("A2", "A5") and abs(float(it["start"]) - r["start"]) < 1e-6 for it in q["items"])]
            ts = [titles[x]["start"] for x in (e.get("refs") or {}).get("titles") or [] if x in titles]
            if e["kind"] in ("title", "situ", "emphasis", "fx", "inner", "count") and ts:
                for r in snd:
                    self.assertTrue(any(abs(r["hit"] - t) <= FRAME + 1e-6 for t in ts), (q["name"], e["kind"], r, ts))
        # 10초 안 순간 수 (숫자 세기 틱 묶음은 하나)
        moments = []
        for r in rows:
            if "숫자 세기" in r["why"] and moments and "숫자 세기" in moments[-1]["why"] and r["hit"] - moments[-1]["hit"] < 3.0:
                continue
            moments.append(r)
        hits = [m["hit"] for m in moments]
        for t in hits:
            self.assertLessEqual(sum(1 for x in hits if t <= x < t + 10.0), msg.SFX_PER_10S, (q["name"], hits))
        return rows


class FixtureEditsTest(unittest.TestCase, _Placed):
    """시험 원본(말·시범·성공 '나이스! 들어갔어요.'·스스로 낮추는 농담 + 웃음·마무리)으로 만든 편집본."""

    @classmethod
    def setUpClass(cls):
        cls.w = F.MsgWork()
        cls.words = msg._words(editor._segments_of(cls.w.name))
        styles = [{"kind": "preset", "name": n} for n in ("예능 MSG형", "담백 레슨형")]
        cls.res = {k: msg.build_variants(cls.w.name, styles, k, ("long",), lambda m: None) for k in msg.INTENSITY}

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_rules_hold_in_every_edit(self):
        for k, r in self.res.items():
            for q in r["sequences"]:
                self.check_seq(q, self.words)

    def test_meaning_table(self):
        """보통: 제목 짠 · 다음 포인트 · 성공 · 웃음이 붙은 스스로 낮추는 농담에도 바둠츠(예능 소리) · 끝 (D-373·D-375)."""
        for q in self.res["보통"]["sequences"]:
            rows = self.sfx_rows(q)
            whys = " | ".join(r["why"] for r in rows)
            self.assertIn("작은 제목", whys)
            self.assertIn("다음 순서로 넘어감", whys)
            self.assertIn("성공 반응", whys)
            self.assertTrue(any("엔드" in r["why"] for r in rows), whys)
            if q["msg"]["style"]["sfx"] == "variety":
                joke = [r for r in rows if r["name"] == "바둠츠"]
                self.assertEqual(len(joke), 1, whys)
                end = max(e for s, e, w in _talk_tl(q, self.words) if "넘어지거든요" in w)
                self.assertGreaterEqual(joke[0]["hit"], end)   # 펀치라인이 들린 뒤 (예전: 말 끝 0.1초 앞)
                self.assertLessEqual(joke[0]["hit"], end + 0.6)
            else:
                self.assertFalse([r for r in rows if r["name"] in ("바둠츠", "띠용")])   # 깔끔한 소리 스타일은 농담 효과음 없음

    def test_success_does_not_cover_reaction_word(self):
        """성공 소리는 반응 말('나이스!') 위가 아니라 그 앞(보이는 결과)이나 말 틈에 · 작게 놓는 것만 말 위 (D-372)."""
        for k in ("보통", "듬뿍"):
            for q in self.res[k]["sequences"]:
                talk = _talk_tl(q, self.words)
                for r in self.sfx_rows(q):
                    if "성공" in r["why"] and not r["soft"]:
                        self.assertFalse([w for w in talk if "나이스" in w[2] and w[0] < r["hi"] - 0.02 and w[1] > r["lo"] + 0.02], r)

    def test_no_quota_filler(self):
        """1분 개수를 채우려고 아무 확대에 휙을 더하지 않음 (D-374) — 모든 자동 효과음이 뜻 있는 순간(이유)에 붙음."""
        for k, r in self.res.items():
            for q in r["sequences"]:
                for row in self.sfx_rows(q):
                    if "확대" in row["why"]:   # 확대 휙은 웃음·반응 순간의 쾅 확대에만 (말하는 중 강조 확대·오래 멈춘 곳 확대에는 없음)
                        self.assertRegex(row["why"], r"웃음|웃는 얼굴|반응", row)
        self.assertFalse(hasattr(msg, "_top_up_sfx"))

    def test_every_sfx_is_heard_in_the_real_mix(self):
        """실제 섞기(내보내기와 같은 editor._mix_audio)에서 효과음 트랙을 켠 것·끈 것을 견줘 효과음마다 제 자리 창(첫소리부터 0.4초 · 짧은 소리는
        제 길이)이 커짐 — 작은 소리 아닌 것 3dB · 작게 놓은 것 1dB 넘게 (D-378 · 예전 판은 잘린 파일이 +0.05dB 로 '안 들림'이었는데 시험은 최대
        크기만 봐서 통과했음)."""
        import numpy as np
        res = self.res["보통"]
        q = res["sequences"][0]
        info = editor.media_info(self.w.name)
        proj = dict(q, captions=res.get("captions") or [], info=info, source=self.w.name, media=list(res.get("media") or []))
        proj, _, _, _, _, _, _, media, _ = editor._export_prep(self.w.name, proj, {"preset": "youtube"}, lambda *a: None)
        total = editor.seq_total(proj)

        def mix(pj):
            tmp = Path(tempfile.mkdtemp(prefix="효과음 섞기 "))
            try:
                p, _ = editor._mix_audio(pj, media, 0.0, total, tmp, editor.valid_trans(pj), lambda x: None)
                return np.array(np.memmap(p, np.float32, "r")).reshape(-1, 2).mean(axis=1)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        def db(x):
            return 10 * np.log10(float(np.mean(x.astype(np.float64) ** 2)) + 1e-12)
        on = mix(proj)
        off_p = json.loads(json.dumps(proj))
        for tr in off_p["tracks"]:
            if tr["id"] in ("A2", "A5"):
                tr["mute"] = True
        off = mix(off_p)
        rows = self.sfx_rows(proj)
        self.assertGreaterEqual(len(rows), 4)
        for r in rows:
            sh = sfxlib.shape(r["name"], editor.ASSETS / sfxlib.sfx_file_name(r["name"]))
            t0 = r["start"] + sh["attack"]
            n = int(min(0.4, max(0.05, sh["audible"] + 0.02)) * editor.SR)
            a = int(t0 * editor.SR)
            rise = db(on[a:a + n]) - db(off[a:a + n])
            # (제목·훅·엔드 소리가 말 틈을 못 찾아 첫 낱말 위에 놓이면 일부러 말보다 6dB 작게 — 첫 말을 가리지 않게 · 0.5dB)
            over_word = re.search(r"작은 제목|쇼츠 첫 장면|엔드", r["why"]) and r["why"].endswith("에 맞춤 · 말 위라 작게")
            self.assertGreaterEqual(rise, 0.5 if over_word else 1.0 if r["soft"] else 3.0, (r, round(rise, 2)))

    def test_mild_is_calmer(self):
        n = {k: len(self.sfx_rows(r["sequences"][0])) for k, r in self.res.items()}
        self.assertLessEqual(n["담백"], n["보통"])
        self.assertLessEqual(n["보통"], n["듬뿍"])


OWNER = {"NAME": "20261015_OWNER_세 마디 [시험].mp4", "DUR": 27.0,
         "LINES": [(0.3, 2.0, "감독님, 요 세 마디만 하면 돼."), (2.2, 3.0, "다른 말 하지 마,"), (3.1, 4.4, "순두시지 마, 감독님."), (4.6, 5.6, "그리고 뒤에 말하면"),
                   (5.7, 7.0, "혼난다. 시작!"), (12.0, 13.4, "나이스! 들어갔어요."), (13.8, 15.4, "감독님 진짜 세 마디만 했어요."),
                   (16.0, 17.6, "자, 이번엔 감독님 차례."), (18.0, 19.0, "하나, 둘, 셋!"), (24.4, 26.2, "오늘은 여기까지예요.")],
         "PLAYS": [(7.4, 11.6, [8.4]), (19.4, 24.0, [20.6])],
         "LAUGHS": [(26.9, 26.92)],   # (소리 식이 비지 않게 · 웃음 점수는 TRUTH 의 laughs 로 · 없음)
         "TRUTH": {"plays": [{"a": 7.4, "b": 11.6, "kicks": [8.4]}, {"a": 19.4, "b": 24.0, "kicks": [20.6]}], "punchlines": [], "laughs": [], "emphasis": [],
                   "success": [{"a": 12.0, "b": 13.4}], "question": [], "section": [{"a": 16.0, "b": 17.6}], "closing": [{"a": 24.4, "b": 26.2}], "junk": [],
                   "duration": 27.0}}


class OwnerShortTest(unittest.TestCase, _Placed):
    """사장님이 보여 준 것 같은 짧은 주고받기 말 + '혼난다. 시작!' 신호 + 시범 (9:16 쇼츠와 롱폼)."""

    @classmethod
    def setUpClass(cls):
        cls.patch = mock.patch.multiple(F, **OWNER)
        cls.patch.start()
        cls.w = F.MsgWork()
        cls.words = msg._words(editor._segments_of(cls.w.name))
        cls.res = {fmt: msg.build_variants(cls.w.name, [{"kind": "preset", "name": n} for n in ("예능 MSG형", "담백 레슨형")], "보통", (fmt,), lambda m: None)
                   for fmt in ("shorts", "long")}

    @classmethod
    def tearDownClass(cls):
        cls.w.close()
        cls.patch.stop()

    def test_start_whistle_on_cue_and_rules(self):
        for fmt, r in self.res.items():
            self.assertEqual(len(r["sequences"]), 2, fmt)
            for q in r["sequences"]:
                rows = self.check_seq(q, self.words)
                cue = [x for x in rows if x["name"] == "호루라기"]
                self.assertEqual(len(cue), 1, (fmt, [x["why"] for x in rows]))
                end = max(e for s, e, w in _talk_tl(q, self.words) if w.startswith("시작"))
                self.assertTrue(end - FRAME <= cue[0]["hit"] <= end + 0.4, (cue, end))
                self.assertIn("시작 신호", cue[0]["why"])
                self.assertTrue(any("성공 반응" in x["why"] for x in rows))
                if fmt == "shorts":
                    self.assertTrue(any("쇼츠 첫 장면" in x["why"] for x in rows))
                    self.assertGreaterEqual(len(rows) / (editor.seq_total(q) / 60.0), msg.SFX_BAND["shorts"]["보통"][0], [x["why"] for x in rows])

    def test_reasons_are_saved_on_clips(self):
        """편집실 효과음 클립에 넣은 이유가 그대로 저장됨 (클립 위 이름·마우스 올리면 보임 · D-378)."""
        q = self.res["shorts"]["sequences"][0]
        clip = next(it for it in q["items"] if it["track"] in ("A2", "A5"))
        self.assertRegex(clip["why"], r"^자동 효과음 '.+' · .+ · .+")
        self.assertEqual(json.loads(json.dumps(clip))["why"], clip["why"])
        html = (Path(editor.__file__).parent / "editor.html").read_text(encoding="utf-8")
        self.assertIn("sfxWhyShort(it.why)", html)
        self.assertIn("넣은 이유:", html)
        self.assertTrue(re.search(r"title=\"\$\{esc\(tip\)\}\"", html))


if __name__ == "__main__":
    unittest.main()
