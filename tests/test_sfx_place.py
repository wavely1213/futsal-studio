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
        # (순간 시각은 군말 '그리고' 뒤 '마지막으로' · D-379)
        self.assertTrue(any(m["kind"] == "section" and m["a"] == 1.0 and m["t"] == segs[0]["words"][1]["s"] for m in moms), moms)
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
        # 참고 영상에서 효과음을 못 찾았으면(자리 기록 없음·효과음 양 0) 자막 성격을 따름 (D-379 — 예능 자막 스타일이 깔끔한 소리로 바뀌던 것)
        self.assertEqual(msg.learned_sound({"fun": {"items": []}}, {}, "variety"), ("variety", None))
        self.assertEqual(msg.learned_sound({"fun": {"items": []}}, {"sfx": 0.0}, "speech"), ("clean", None))
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

    def test_msg_list_shows_the_placed_sound(self):
        """MSG 목록의 소리만 사건: 실제로 놓인 소리 이름 · 그 맞출 점 (D-379 — 예전: 예약한 '휙'·예약 시각이 남아 팡·뽁이 나는 곳을 '휙'으로)."""
        n = 0
        for r in self.res.values():
            for q in r["sequences"]:
                items = {it["id"]: it for it in q["items"]}
                for e in q["msg"]["events"]:
                    snd = [items[i] for i in (e.get("refs") or {}).get("items") or [] if i in items and items[i]["track"] in ("A2", "A5")]
                    if e["kind"] != "sfx" or not snd:
                        continue
                    nm = re.match(r"자동 효과음 '([^']+)'", snd[0]["why"])[1]
                    sh = sfxlib.shape(nm, editor.ASSETS / sfxlib.sfx_file_name(nm))
                    self.assertEqual(e["text"], nm, e)
                    self.assertAlmostEqual(e["t"], max(0.0, float(snd[0]["start"]) + sh["hit"] - float(snd[0]["in"])), delta=0.011)
                    n += 1
        self.assertGreater(n, 0)

    def test_mild_is_calmer(self):
        n = {k: len(self.sfx_rows(r["sequences"][0])) for k, r in self.res.items()}
        self.assertLessEqual(n["담백"], n["보통"])
        self.assertLessEqual(n["보통"], n["듬뿍"])


def _mix_on_off(w, q, captions, media):
    """편집본 하나를 실제로 섞음 (내보내기와 같은 editor._mix_audio) → (효과음 켬, 효과음만 끔, 말만, 내보내기 준비한 편집본)."""
    import numpy as np
    info = editor.media_info(w.name)
    proj = dict(q, captions=captions or [], info=info, source=w.name, media=list(media or []))
    proj, _, _, _, _, _, _, md, _ = editor._export_prep(w.name, proj, {"preset": "youtube"}, lambda *a: None)
    total = editor.seq_total(proj)

    def mix(mute):
        pj = json.loads(json.dumps(proj))
        for tr in pj["tracks"]:
            if tr["id"] in mute:
                tr["mute"] = True
        tmp = Path(tempfile.mkdtemp(prefix="효과음 섞기 "))
        try:
            p, _ = editor._mix_audio(pj, md, 0.0, total, tmp, editor.valid_trans(pj), lambda x: None)
            return np.array(np.memmap(p, np.float32, "r")).reshape(-1, 2).mean(axis=1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return mix(()), mix(("A2", "A5")), mix(("A2", "A3", "A4", "A5")), proj


class PlaceReviewTest(unittest.TestCase):
    """D-379 (검토 고침): 꼭 넣을 소리가 소수점 찌꺼기·휙 꼬리 때문에 빠지던 것 · 컷 앞 창 · 크게 들리는 부분 · 소리 크기."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="효과음 자리 "))
        self.p = mock.patch.object(editor, "ASSETS", self.tmp)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def place(self, B, words, pal="clean", level="보통"):
        return msg.place_sfx(B, {"voicePk": -10.0}, level, words, msg.SFX_PALETTE[pal])[0]

    def test_float_noise_word_start_keeps_every_tick(self):
        """받아쓰기 시각 105.57999999999997 처럼 소수점 찌꺼기가 있어도 숫자마다 틱 — 예전: 딱 맞는 틈이 0.0005초 겹쳐 빠짐 (틱 186개 중 50개)."""
        B = msg._Build("x.mp4", {"duration": 120.0}, "long")
        B.clip(0.0, 120.0, main=True)
        words = [(105.0, 105.3, "자,", 0), (105.57999999999997, 105.9, "하나,", 1), (106.4, 106.7, "둘,", 1), (107.2, 107.5, "셋!", 1)]
        for w0, n in ((105.57999999999997, "1"), (106.4, "2"), (107.2, "3")):
            t = B.title(n, w0, 0.8, {})
            msg._sfx_at(B, t["start"], "틱", None, "count", title=t["id"], must=True, group="count:105.58")
        placed = self.place(B, words)
        self.assertEqual(len(placed), 3, [(c["name"], c["how"]) for c in placed])
        for c in placed:   # 틱은 숫자 말 첫소리 바로 앞 · 낱말과 안 겹침
            self.assertFalse([w for w in words if w[0] - 0.02 < c["hi"] - 1e-6 and c["lo"] < w[1] - 0.01 - 1e-6], c)
        self.assertEqual((msg._ms_down(105.5395), msg._ms_up(105.3201), msg._ms_down(105.53999999999997)), (105.539, 105.321, 105.54))

    def test_next_point_whoosh_at_the_cut_before_the_title(self):
        """'세 번째 포인트'가 컷 바로 뒤에서 시작: 휙 봉우리를 컷에 (글자도 컷으로) — 예전: 글자 첫 장면(첫 낱말)에 맞추면 휙 꼬리가 낱말에 걸려
        늦추거나 작은 소리만 봐서 빠짐 (S1 '포인트 ③' 20편 중 14편)."""
        B = msg._Build("x.mp4", {"duration": 60.0}, "long")
        B.clip(10.0, 15.0, main=True)   # 타임라인 0~5 (끝 2.4초는 말 없음)
        B.clip(20.0, 30.0, main=True)   # 타임라인 5~15 · 컷 5.0
        words = [(12.0, 12.6, "설명했어요.", 0), (20.12, 20.15, "세", 1), (20.15, 20.43, "번째", 1), (20.43, 21.1, "포인트.", 1), (21.3, 22.0, "고개를", 1)]
        t = B.title("포인트 ③", 5.12, 2.2, {})
        k = msg._sfx_at(B, 5.12, "휙", None, "situ", title=t["id"], must=True, mk="section")
        B.event("situ", 5.12, "포인트 ③", "", refs={"titles": [t["id"]], "sfx": [k]})
        placed = self.place(B, words, "variety")
        self.assertEqual(len(placed), 1)
        c = placed[0]
        self.assertEqual((c["name"], c["soft"]), ("휙", False), c["how"])
        self.assertAlmostEqual(c["hit"], 5.0, delta=0.01)
        self.assertLessEqual(c["hi"], 5.12 - 0.02 + 1e-6)   # 크게 들리는 부분(최대 -12dB 까지)이 '세' 앞에서 끝남
        self.assertAlmostEqual(t["start"], 5.0, delta=0.01)   # 글자도 컷으로 (끝은 그대로)
        self.assertAlmostEqual(t["start"] + t["dur"], 7.32, delta=0.01)

    def test_sound_only_window_reaches_back_across_the_cut(self):
        """다음 포인트 소리만인 창은 컷 앞 조각의 말 없는 꼬리까지 (D-379 · 예전: 컷에서 잘려 S2 '두번째 동작은' 창 0.1초 · 18편 모두 빠짐)."""
        B = msg._Build("x.mp4", {"duration": 60.0}, "long")
        B.clip(10.0, 15.0, main=True)
        B.clip(20.0, 30.0, main=True)
        self.assertEqual(msg._tl_win(B, 20.0, 5.0, 19.4, 20.1), (5.0, 5.1))
        self.assertEqual(msg._tl_win(B, 20.0, 5.0, 19.4, 20.1, back=True), (4.4, 5.1))
        words = [(12.0, 14.3, "말했어요.", 0), (20.0, 20.5, "두번째", 1), (20.5, 21.0, "동작은", 1)]
        k = msg._sfx_at(B, 5.0, "휙", None, "accent", win=msg._tl_win(B, 20.0, 5.0, 19.4, 20.1, back=True), must=True, mk="section")
        B.event("sfx", 5.0, "휙", "", refs={"sfx": [k]})
        placed = self.place(B, words, "variety")
        self.assertEqual(len(placed), 1)
        self.assertLess(placed[0]["hit"], 5.0 + 1e-6)
        self.assertLessEqual(placed[0]["hi"], 5.0 - 0.02 + 1e-6)

    def test_every_must_sound_placed_after_a_pause(self):
        """꼭 넣을 소리 앞에 0.4초 넘는 쉼이 있으면 늘 들어감 (다음 포인트 · 성공 · 글자에 붙은 것 · 컷 없음)."""
        for name, mk, pal in (("휙", "section", "variety"), ("레벨업", "success", "variety"), ("딩동", "success", "clean")):
            B = msg._Build("x.mp4", {"duration": 60.0}, "long")
            B.clip(0.0, 60.0, main=True)
            words = [(8.0, 9.5, "그렇죠.", 0), (10.0, 10.4, "꽂혔다!", 1), (10.4, 11.5, "완벽해요.", 1)]
            t = B.title("꽂혔다!", 10.0, 1.1, {})
            msg._sfx_at(B, 10.0, name, None, "fx" if mk == "success" else "situ", title=t["id"], must=True, mk=mk)
            placed = self.place(B, words, pal)
            self.assertEqual(len(placed), 1, (name, mk))
            self.assertFalse([w for w in words if w[0] < placed[0]["hi"] - 0.02 and w[1] > placed[0]["lo"] + 0.02] and not placed[0]["soft"], placed[0])

    def test_loud_part_runs_to_minus_12db(self):
        """크게 들리는 부분 = 최대보다 12dB 작아질 때까지 (예전 에너지 90%: 딩동은 그 뒤에도 -8dB · 휙은 봉우리 뒤 0.1초 동안 -10dB 안쪽 · D-379)."""
        import numpy as np
        for n in ("딩동", "휙", "짠", "레벨업"):
            sh = sfxlib.shape(n, self.tmp / sfxlib.ensure_sfx(n, self.tmp))
            x = read_wav(self.tmp / sfxlib.sfx_file_name(n)).mean(axis=1)
            hop = int(sfxlib.SR * 0.005)
            e = (x[:len(x) // hop * hop].reshape(-1, hop) ** 2).mean(axis=1) + 1e-18
            db = 10 * np.log10(e)
            after = db[int(round(msg._loud_part(0.0, sh)[1] / 0.005)):]
            self.assertTrue(len(after) == 0 or after.max() < db.max() - 11.9, (n, sh))

    def test_sustained_whistle_is_not_louder_than_impacts(self):
        """호루라기(길게 울리는 높은 소리)는 귀 크기로도 맞춤 — 최대 크기만 맞추면 딩동·짠보다 8dB 넘게 크게 들림 (D-379)."""
        sig = {"voicePk": -12.0}
        loud = {}
        for n in ("호루라기", "딩동", "짠", "휙", "레벨업", "띠로리"):
            f = self.tmp / sfxlib.ensure_sfx(n, self.tmp)
            ld = sfxlib.loud_db(f)
            lv = msg._sfx_level(sig, n, "accent", sfxlib.peak_db(f), file_loud=ld)
            loud[n] = lv + ld
            self.assertLessEqual(loud[n], -12.0 - msg.SFX_GAP - msg.SFX_CREST + 0.11, n)
        self.assertLessEqual(loud["호루라기"], max(loud["딩동"], loud["짠"]) + 0.5, loud)
        f = self.tmp / sfxlib.sfx_file_name("딩동")   # 부딪히는 소리는 그대로 (최대 크기로 맞춘 값)
        self.assertEqual(msg._sfx_level(sig, "딩동", "accent", sfxlib.peak_db(f), file_loud=sfxlib.loud_db(f)), msg._sfx_level(sig, "딩동", "accent", -3.0))


class CacheReviewTest(unittest.TestCase):
    """D-379: 예전 판이 만든 캐시는 원본 지문으로 가림 (5ms 틱은 cut_short 로 못 가렸음) · 예전 편집본 클립도 새 길이로."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="효과음 캐시 "))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_5ms_tick_cache_is_rebuilt(self):
        fn = sfxlib.sfx_file_name("틱")
        (self.tmp / fn).write_bytes(sfxlib._wav_bytes([[0.7, 0.7]] * int(0.005 * sfxlib.SR)))
        self.assertFalse(sfxlib.cut_short(self.tmp / fn))   # (짐작으로는 못 가리는 예전 파일)
        sfxlib.ensure_sfx("틱", self.tmp)
        self.assertGreater(len(read_wav(self.tmp / fn)) / sfxlib.SR, 0.02)
        book = json.loads((self.tmp / sfxlib.SRC_BOOK).read_text(encoding="utf-8"))
        self.assertEqual(book["틱"], sfxlib._src_sha(sfxlib.SHIP / sfxlib.CATALOG["틱"][0]))
        m0 = (self.tmp / fn).stat().st_mtime_ns
        sfxlib.ensure_sfx("틱", self.tmp)   # 지문이 같으면 그대로
        self.assertEqual((self.tmp / fn).stat().st_mtime_ns, m0)
        (self.tmp / sfxlib.SRC_BOOK).write_text(json.dumps({"틱": "0000"}), encoding="utf-8")   # 원본이 바뀌면 다시
        sfxlib.ensure_sfx("틱", self.tmp)
        self.assertEqual(json.loads((self.tmp / sfxlib.SRC_BOOK).read_text(encoding="utf-8"))["틱"], book["틱"])
        self.assertNotIn(Path(sfxlib.SRC_BOOK).suffix, editor.MEDIA_EXTS)   # (편집실 미디어 목록에 안 잡힘)

    def test_old_edit_clips_grow_to_the_rebuilt_length(self):
        """예전 편집본: 예전 캐시 길이(0.175초)로 끝나던 자동 효과음 클립 → 다시 만든 길이까지 (같은 트랙 다음 클립 앞까지 · 자른 클립은 그대로)."""
        fn = sfxlib.sfx_file_name("레벨업2")
        (self.tmp / fn).write_bytes(sfxlib._wav_bytes([[0.5, 0.5]] * int(0.175 * sfxlib.SR)))
        media = [{"id": "m1", "kind": "audio", "src": "assets", "file": fn, "dur": 0.175}]
        items = [{"id": "a", "track": "A2", "media": "m1", "start": 1.0, "in": 0.0, "out": 0.175, "speed": 1.0},
                 {"id": "b", "track": "A2", "media": "m1", "start": 5.0, "in": 0.0, "out": 0.1, "speed": 1.0},
                 {"id": "c", "track": "A5", "media": "m1", "start": 8.0, "in": 0.0, "out": 0.175, "speed": 1.0},
                 {"id": "d", "track": "A5", "media": "m1", "start": 8.2, "in": 0.0, "out": 0.175, "speed": 1.0}]
        other = [{"id": "e", "track": "A2", "media": "m1", "start": 3.0, "in": 0.0, "out": 0.175, "speed": 1.0}]   # 같은 프로젝트의 다른 편집본
        with mock.patch.object(editor, "ASSETS", self.tmp):
            n = editor.refresh_sfx_lengths([items, other], media)
        new = len(read_wav(self.tmp / fn)) / sfxlib.SR
        self.assertGreater(new, 0.3)
        self.assertEqual(n, 4)
        self.assertAlmostEqual(items[0]["out"], new, places=3)
        self.assertAlmostEqual(other[0]["out"], new, places=3)
        self.assertEqual(items[1]["out"], 0.1)   # 자른 클립
        self.assertAlmostEqual(items[2]["out"], 0.2, places=3)   # 같은 트랙 다음 클립 앞까지
        self.assertAlmostEqual(media[0]["dur"], new, places=3)
        with mock.patch.object(editor, "ASSETS", self.tmp):
            self.assertEqual(editor.refresh_sfx_lengths([items, other], media), 0)   # 두 번 해도 같음


    def test_opening_an_old_project_grows_its_clips(self):
        """예전 프로젝트를 열 때(migrate_project) 캐시를 다시 만들고 모든 편집본의 예전 길이 클립을 늘림."""
        fn = sfxlib.sfx_file_name("딩동")
        (self.tmp / fn).write_bytes(sfxlib._wav_bytes([[0.5, 0.5]] * int(0.1 * sfxlib.SR)))
        proj = {"source": "x.mp4", "info": {"duration": 30.0, "width": 1920, "height": 1080, "fps": 30.0}, "speech": [],
                "media": [{"id": "main", "kind": "video", "src": "videos", "file": "x.mp4", "dur": 30.0, "w": 1920, "h": 1080, "fps": 30.0, "audio": True},
                          {"id": "m1", "kind": "audio", "src": "assets", "file": fn, "dur": 0.1}],
                "sequences": [{"id": f"s{k}", "name": f"편집본 {k}", "auto": "user", "format": "long", "v": 2, "items": [
                    {"id": f"a{k}", "track": "A2", "media": "m1", "start": 2.0, "in": 0.0, "out": 0.1, "speed": 1.0}]} for k in (1, 2)]}
        with mock.patch.object(editor, "ASSETS", self.tmp), mock.patch.object(editor, "_has_audio", lambda name: True):
            editor.migrate_project("x.mp4", proj)
        new = len(read_wav(self.tmp / fn)) / sfxlib.SR
        self.assertGreater(new, 0.2)
        self.assertEqual([s["items"][0]["out"] for s in proj["sequences"]], [round(new, 4)] * 2)


class MomentsReviewTest(unittest.TestCase):
    """D-379: 군말 뒤 다음 포인트 · 시작 신호 맥락 · 한 반응 묶음은 성공 하나 · 실수 뒤 확대에 휙 없음."""
    SIG = MomentsTest.SIG

    def test_next_point_after_junk_lead(self):
        """'자, 첫번째 포인트.'의 '자,'가 정리할 군말이어도 다음 포인트 (시각은 '첫번째') — 예전: 순간째 빠짐 (S1 36편 모두)."""
        segs = [_seg(24.58, 26.0, "자, 첫번째 포인트.")]
        w0 = segs[0]["words"][1]["s"]
        sig = dict(self.SIG, junk=[[24.5, w0 - 0.02]])
        sec = [m for m in msg.moments(sig, segs) if m["kind"] == "section"]
        self.assertEqual(len(sec), 1, msg.moments(sig, segs))
        self.assertAlmostEqual(sec[0]["t"], w0, delta=0.01)
        self.assertEqual(sec[0]["a"], 24.58)

    def test_start_cue_needs_context(self):
        """'시작'·'가자'로 끝나는 보통 말에는 호루라기 없음 — 준비·경고·숫자 말이 앞에 있거나 분명한 시작 말 뒤 3초 안에 공 소리일 때만."""
        def cues(lines, onsets=()):
            segs = [_seg(2.0 + 3 * k, 4.0 + 3 * k, t) for k, t in enumerate(lines)]
            return [m for m in msg.moments(dict(self.SIG, onsets=list(onsets)), segs) if m["kind"] == "cue"]
        for line in ("오늘은 패스부터 시작.", "오늘 수업 시작", "천천히 가자.", "다음 영상에서 또 만나요. 가자!"):
            self.assertFalse(cues([line], onsets=[5.0]), line)
        self.assertEqual(len(cues(["준비, 시작!"])), 1)
        self.assertEqual(len(cues(["그리고 뒤에 말하면", "혼난다. 시작!"])), 1)
        self.assertEqual(len(cues(["자, 시작!"], onsets=[5.0])), 1)
        self.assertFalse(cues(["자, 시작!"]))

    def test_one_reaction_group_is_one_success(self):
        """'들어갔어요.' + '나이스.' (한 반응 묶음 · 같은 react0) → 성공 하나 (예전: 같은 골에 레벨업2·레벨업 두 번)."""
        segs = [_seg(5.0, 5.8, "들어갔어요."), _seg(6.2, 6.8, "나이스.")]
        win = [m for m in msg.moments(dict(self.SIG, onsets=[4.0]), segs) if m["kind"] == "success"]
        self.assertEqual(len(win), 1, win)
        self.assertEqual((win[0]["react0"], win[0]["b"]), (5.0, 6.76))   # (두 줄 끝까지)

    def test_zoom_whoosh_needs_laugh_and_no_recent_fail(self):
        self.assertEqual(msg._sfx_key({"kind": "punch", "src": "reaction"}, "보통"), (None, False))   # 얼굴만 (웃음소리 없음)
        self.assertEqual(msg._sfx_key({"kind": "punch", "src": "reaction", "laugh": 3.0}, "보통"), ("zoom", False))
        self.assertEqual(msg._sfx_key({"kind": "punch", "src": "reaction", "laugh": 3.0, "nosfx": True}, "보통"), (None, False))
        moms = [{"kind": "fail", "a": 9.5, "b": 10.5, "t": 10.0, "score": 1.0, "text": "아, 아깝다.", "why": ""},
                {"kind": "reaction", "a": 10.8, "b": 12.3, "t": 11.3, "score": 1.0, "text": "", "why": ""}]
        sig = dict(self.SIG, tags={"laugh": [0.0] * 22 + [0.5, 0.5, 0.5] + [0.0] * 40})
        st = msg.resolve({"kind": "preset", "name": "예능 MSG형"})
        picked, _ = msg.plan_events(sig, moms, st, "듬뿍", "long", 7, [{"in": 0.0, "out": 30.0}], [(9.6, 10.4, "아깝다.", 0)])
        punch = [c for c in picked if c["kind"] == "punch" and c["src"] == "reaction"]
        self.assertTrue(punch)
        self.assertFalse([c for c in punch if c.get("sfx")], punch)

    def test_hook_said_point_drops_its_sound_too(self):
        """쇼츠 첫머리가 이미 말한 포인트의 상황 자막을 빼면 그 휙도 뺌 (예전: 훅 길이로 정해진 2.6초에 같은 포인트 휙 · 쇼츠 25/126)."""
        B = msg._Build("x.mp4", {"duration": 30.0}, "shorts")
        B.clip(0.0, 20.0, main=True)
        hk = B.title("두 번째 포인트는 디딤발!", 0.0, 2.5, {})
        t = B.title("포인트 ②", msg.SHORTS_HOOK_SEC + 0.1, 2.0, {})
        k = msg._sfx_at(B, t["start"], "휙", None, "situ", title=t["id"], must=True, mk="section")
        B.event("situ", t["start"], "포인트 ②", "", refs={"titles": [t["id"]], "sfx": [k]})
        self.assertEqual(msg._drop_said_order(B, hk, None), 1)
        self.assertIsNone(B.sfx[k])
        self.assertFalse([e for e in B.events if e["kind"] in ("situ", "sfx")])


class LearnedRefSfxTest(unittest.TestCase, _Placed):
    """D-377 · D-379: 참고 영상 효과음 자리(sfxAt)가 있는 배운 스타일로 만든 편집본 — 컷에 붙던 스타일은 깔끔한 소리 · 더 넣는 소리(강조)는
    안 붙이고 뜻 소리(다음 포인트·성공·끝)는 그대로 · 글자·확대·웃음에 붙던 스타일은 예능 소리 (농담 뒤 바둠츠)."""

    @classmethod
    def setUpClass(cls):
        import style
        cls.w = F.MsgWork()
        cls.words = msg._words(editor._segments_of(cls.w.name))
        prof = json.loads((Path(__file__).parent / "fixtures" / "style_v17.json").read_text(encoding="utf-8"))
        for nm, at in (("컷 효과음 스타일", {"n": 10, "caption": 0, "cut": 9, "zoom": 0, "laugh": 0, "other": 1}),
                       ("글자 효과음 스타일", {"n": 10, "caption": 6, "cut": 1, "zoom": 2, "laugh": 1, "other": 0})):
            p = dict(prof, plan={"fun": {"items": [{"key": "punch_zoom", "perMin": 4.0}], "sfxAt": at}, "captions": {"style": "variety", "perMin": {"emph": 10.0}}})
            (style.STYLES / f"{nm}.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        cls.res = msg.build_variants(cls.w.name, [{"kind": "style", "name": n} for n in ("컷 효과음 스타일", "글자 효과음 스타일")], "보통", ("long",),
                                     lambda m: None)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_palette_and_places_follow_the_reference(self):
        by = {q["msg"]["style"]["label"]: q for q in self.res["sequences"]}
        self.assertEqual(set(by), {"컷 효과음 스타일", "글자 효과음 스타일"})
        for lab, q in by.items():
            rows = self.check_seq(q, self.words)
            whys = " | ".join(r["why"] for r in rows)
            for want in ("다음 순서로 넘어감", "성공 반응", "엔드"):   # 꼭 넣을 뜻 소리는 자리 비율과 상관없이
                self.assertIn(want, whys, (lab, whys))
            if lab == "컷 효과음 스타일":
                self.assertEqual(q["msg"]["style"]["sfx"], "clean")
                self.assertFalse([r for r in rows if "강조 글자" in r["why"] or r["name"] in ("바둠츠", "뽁")], whys)
            else:
                self.assertEqual(q["msg"]["style"]["sfx"], "variety")
                self.assertTrue([r for r in rows if r["name"] == "바둠츠"], whys)

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

    def test_whistle_is_not_louder_than_speech(self):
        """'시작!' 호루라기의 귀 크기(0.4초 K 무게)가 말 낱말 귀 크기 중앙값보다 크지 않음 — 예전: 최대 크기로만 맞춰 가장 큰 말보다 7dB 크게 (D-379)."""
        import numpy as np
        r = self.res["shorts"]
        on, off, voice, proj = _mix_on_off(self.w, r["sequences"][0], r.get("captions"), r.get("media"))
        wh = next(x for x in self.sfx_rows(proj) if x["name"] == "호루라기")
        a = int(wh["start"] * editor.SR)
        fx = sfxlib.st_loud_db(on[a:a + int(0.38 * editor.SR)] - off[a:a + int(0.38 * editor.SR)], editor.SR)
        words = [10 * np.log10(float(np.mean(sfxlib.k_weight(voice[int(s0 * editor.SR):int(e0 * editor.SR)], editor.SR) ** 2)) + 1e-12)
                 for s0, e0, _ in _talk_tl(proj, self.words) if e0 - s0 >= 0.15]
        self.assertGreaterEqual(len(words), 5)
        self.assertLessEqual(fx, float(np.median(words)), (fx, np.median(words)))

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
