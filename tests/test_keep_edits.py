"""고친 것이 사라지지 않게 (C2 · D-410~D-414) — 저장소 폴더에서 python3 -m unittest tests.test_keep_edits

· 편집점 다시 찾기: 손으로 고치거나 지운 자막은 그대로, 받아쓴 그대로인 자막만 새 받아쓰기로 (editor.keep_edited_captions · D-413)
· 편집점 다시 찾기: 새 롱폼 가편집을 열 편집본으로 + 이번에 더한 편집본 표시(fresh) + 화면 알림용 again (D-414)
· 편집실 저장이 계속 안 됨: 쉬운 까닭 · studio.log 에 한 번 · '편집본 파일로 내려받기' (/api/edit/rescue · D-412)
· 뒤로 가기: JSON·HTML 응답 Cache-Control: no-store · 썸네일 저장 판 번호(rev) 검사 (D-411)
· 용어 사전 자동 저장 (ui.html · D-410) — 화면 코드를 node 로 돌려 봄 (node 가 없으면 건너뜀)
받아쓰기 모델·인터넷은 쓰지 않음. 시험 영상은 ffmpeg lavfi 로 만듦."""
import json
import shutil
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import app  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import thumb  # noqa: E402
from test_captions import W, WorkDir  # noqa: E402
from test_windows_compat import Server  # noqa: E402

# 다시 찾기 전 받아쓰기 (단어 시각 있음)
OLD_SEGS = [{"start": 0.5, "end": 2.3, "text": "오늘은 퍼스트 터치를 배워볼게요.",
             "words": [W("오늘은", 0.5, 0.8), W("퍼스트", 0.85, 1.15), W("터치를", 1.2, 1.5), W("배워볼게요.", 1.55, 2.3)]},
            {"start": 3.2, "end": 4.6, "text": "발 안쪽으로 받아요.", "words": [W("발", 3.2, 3.35), W("안쪽으로", 3.4, 3.9), W("받아요.", 3.95, 4.6)]},
            {"start": 5.4, "end": 6.6, "text": "피버를 해 봐요.", "words": [W("피버를", 5.4, 5.9), W("해", 6.0, 6.1), W("봐요.", 6.15, 6.6)]},
            {"start": 7.4, "end": 8.6, "text": "어 그러니까 음.", "words": [W("어", 7.4, 7.6), W("그러니까", 7.7, 8.2), W("음.", 8.3, 8.6)]}]
# 다시 찾은 받아쓰기 (용어 사전으로 '피버'→'피벗' · 낱말 시각은 조금씩 다름)
NEW_SEGS = [{"start": 0.5, "end": 2.3, "text": "오늘은 퍼스트 터치를 배워 볼게요.",
             "words": [W("오늘은", 0.5, 0.8), W("퍼스트", 0.86, 1.15), W("터치를", 1.2, 1.5), W("배워", 1.55, 1.8), W("볼게요.", 1.85, 2.3)]},
            {"start": 3.2, "end": 4.6, "text": "발 안쪽으로 받아요.", "words": [W("발", 3.21, 3.35), W("안쪽으로", 3.4, 3.9), W("받아요.", 3.95, 4.6)]},
            {"start": 5.4, "end": 6.6, "text": "피벗을 해 봐요.", "words": [W("피벗을", 5.4, 5.9), W("해", 6.0, 6.1), W("봐요.", 6.15, 6.6)]},
            {"start": 7.4, "end": 8.6, "text": "어 그러니까 음.", "words": [W("어", 7.41, 7.6), W("그러니까", 7.7, 8.2), W("음.", 8.3, 8.6)]},
            {"start": 9.4, "end": 10.4, "text": "새로 들린 말이에요.", "words": [W("새로", 9.4, 9.7), W("들린", 9.75, 10.0), W("말이에요.", 10.05, 10.4)]}]


def caps_of(segs):
    return [editor._make_cap(c, "long", ()) for c in editor.captions.from_segments(segs, "long", ())]


def base_of(segs):
    out = []
    for s in segs:
        out += [(w["s"], w["e"], w["w"], True) for w in s.get("words") or ()] or [(s["start"], s["end"], s["text"], False)]
    return out


def find(caps, word):
    return next(c for c in caps if word in c["text"])


class KeepEditedCaptionsTest(unittest.TestCase):
    """editor.keep_edited_captions (D-413) — 재현(백로그): 자막 2줄 글 고침·1줄 색·1줄 삭제 뒤 다시 찾으면 고친 글·색이 사라지고 지운 줄이 되살아났음."""

    def setUp(self):
        self.old = caps_of(OLD_SEGS)
        self.assertEqual([c["text"] for c in self.old], ["오늘은 퍼스트 터치를 배워볼게요.", "발 안쪽으로 받아요.", "피버를 해 봐요.", "어 그러니까 음."])
        self.before, self.new = base_of(OLD_SEGS), caps_of(NEW_SEGS)

    def test_untouched_captions_follow_new_transcript(self):
        out, kept, skipped = editor.keep_edited_captions(self.old, self.new, self.before)
        self.assertEqual((kept, skipped), (0, 0))
        self.assertEqual([c["text"] for c in out], [c["text"] for c in self.new], "고치지 않았으면 모두 새 받아쓰기 ('피벗을' · 새로 들린 말)")
        self.assertTrue(all(editor._cap_untouched(c, self.before, set()) for c in self.old), "받아쓰기로 만든 자막은 모두 '그대로'로 봄")

    def test_edited_text_color_and_deleted_lines_stay(self):
        old = [dict(c) for c in self.old]
        find(old, "오늘은")["text"] = "오늘은 퍼스트 터치를 배워 볼게요!"   # 글 고침 (낱말 수가 바뀜)
        find(old, "안쪽으로")["text"] = "발 안쪽으로 받아요!"            # 글 고침 (낱말 수 그대로)
        find(old, "피버를")["fill"] = "#FFD400"                           # 색만 바꿈
        old = [c for c in old if "그러니까" not in c["text"]]               # 지움 (목록에서)
        out, kept, skipped = editor.keep_edited_captions(old, self.new, self.before)
        texts = [c["text"] for c in out]
        self.assertEqual(texts, ["오늘은 퍼스트 터치를 배워 볼게요!", "발 안쪽으로 받아요!", "피버를 해 봐요.", "새로 들린 말이에요."])
        self.assertEqual(find(out, "피버를")["fill"], "#FFD400", "색을 바꾼 자막은 그 모양·글 그대로")
        self.assertEqual((kept, skipped), (3, 4), "고친 3줄 남김 · 새 자막 중 고친 자리 3줄 + 지운 자리 1줄은 안 넣음")
        self.assertNotIn("어 그러니까 음.", texts, "지운 자막이 되살아나지 않음")

    def test_hidden_in_one_sequence_and_hand_timed_stay(self):
        old = [dict(c) for c in self.old]
        off, timed = find(old, "피버를"), find(old, "안쪽으로")
        timed.update(start=3.0, tm=1)  # 시간을 손으로 맞춤
        seqs = [{"id": "s1", "capOff": [off["id"]]}, {"id": "s2", "capPos": {find(old, "그러니까")["id"]: {"y": 0.3}}}]
        out, kept, _ = editor.keep_edited_captions(old, self.new, self.before, seqs)
        self.assertEqual(kept, 3)
        self.assertIn(off["id"], [c["id"] for c in out], "편집본에서 지운 자막(capOff)은 같은 id 로 남아 계속 안 보임")
        self.assertEqual(find(out, "안쪽으로")["start"], 3.0)
        self.assertEqual(find(out, "오늘은")["text"], "오늘은 퍼스트 터치를 배워 볼게요.", "그 밖은 새 받아쓰기")

    def test_same_lines_keep_their_id(self):
        again = caps_of(OLD_SEGS)  # 같은 받아쓰기를 다시 (새 id)
        self.assertNotEqual([c["id"] for c in again], [c["id"] for c in self.old])
        out, kept, skipped = editor.keep_edited_captions(self.old, again, self.before)
        self.assertEqual((kept, skipped), (0, 0))
        self.assertEqual([c["id"] for c in out], [c["id"] for c in self.old], "시간·글이 같은 줄은 예전 id 그대로 (편집본마다 둔 자리가 이어짐)")

    def test_user_added_caption_in_silence_stays(self):
        old = self.old + [{"id": "mine", "start": 11.0, "end": 12.0, "text": "구독 부탁해요"}]
        out, kept, _ = editor.keep_edited_captions(old, self.new, self.before)
        self.assertEqual(kept, 1)
        self.assertIn("구독 부탁해요", [c["text"] for c in out])

    def test_old_transcript_without_word_times(self):
        segs = [{"start": 0.5, "end": 2.0, "text": "오늘은 퍼스트 터치를 배워볼게요"}, {"start": 2.4, "end": 4.6, "text": "공이 오면 발 안쪽으로 받아요"}]
        old = caps_of(segs)
        self.assertTrue(all("wt" not in c for c in old))
        before = base_of(segs)
        self.assertTrue(all(editor._cap_untouched(c, before, set()) for c in old))
        old[1] = dict(old[1], text="공이 오면 발 안쪽으로 받아요!")
        out, kept, _ = editor.keep_edited_captions(old, caps_of(NEW_SEGS), before)
        self.assertEqual(kept, 1)
        self.assertIn("공이 오면 발 안쪽으로 받아요!", [c["text"] for c in out])
        self.assertIn("피벗을 해 봐요.", [c["text"] for c in out])

    def test_real_transcripts_are_seen_as_untouched(self):
        """시험용 받아쓰기(낱말 시각이 0초짜리로 겹친 것 포함)로 만든 자막이 모두 '받아쓴 그대로'로 보임 (아니면 다시 찾아도 안 바뀜)."""
        segs = [{"start": 35.22, "end": 36.28, "text": "이렇게 패스와 동시에 몸이 나가야 해요.",
                 "words": [W("이렇게", 35.22, 35.22), W("패스와", 35.22, 35.22), W("동시에", 35.22, 35.22), W("몸이", 35.22, 35.64),
                           W("나가야", 35.64, 36.02), W("해요.", 36.02, 36.28)]},
                {"start": 36.28, "end": 37.0, "text": "하나, 둘.", "words": [W("하나,", 36.28, 36.4), W("둘.", 36.4, 37.0)]}]
        for fmt in ("long", "shorts"):
            caps = [editor._make_cap(c, fmt, ()) for c in editor.captions.from_segments(segs, fmt, ())]
            self.assertTrue(all(editor._cap_untouched(c, base_of(segs), set()) for c in caps), fmt)


    def test_caption_moved_to_other_track_is_not_doubled(self):
        """리뷰: 받아쓴 그대로인 자막을 C2 줄로 옮기면(trk) 남기는데, 새 C1 자막도 같은 말로 들어가 두 번 보였음."""
        old = [dict(c) for c in self.old]
        find(old, "안쪽으로")["trk"] = "C2"  # 옮김 (C1 에는 그 말 자막이 없음)
        out, kept, skipped = editor.keep_edited_captions(old, self.new, self.before)
        self.assertEqual([c.get("trk") for c in out if "안쪽으로" in c["text"]], ["C2"], "옮긴 자막 하나만 (새 C1 자막은 안 넣음)")
        self.assertEqual((kept, skipped), (1, 1))
        self.assertIn("피벗을 해 봐요.", [c["text"] for c in out], "그 밖은 새 받아쓰기")
        # 복사(C1 원래 자막은 그대로 둔 채 C2 에 하나 더): C1 은 새 받아쓰기로, C2 사본은 그대로
        old = [dict(c) for c in self.old] + [dict(find(self.old, "안쪽으로"), id="copy", trk="C2")]
        out, kept, _ = editor.keep_edited_captions(old, self.new, self.before)
        self.assertEqual(sorted(editor.cap_trk(c) for c in out if "안쪽으로" in c["text"]), ["C1", "C2"])

    def test_split_boundaries_move_keeps_every_word(self):
        """리뷰: 새 받아쓰기가 자막을 다른 곳에서 나누면, 고친 자막과 겹친 새 자막을 통째로 빼서 옆 말('라')이 자막 없이 남았음."""
        ws = [W(t, k * 0.5, k * 0.5 + 0.5) for k, t in enumerate("가 나 다 라 마 바".split())]

        def mk(xs):
            return editor._make_cap({"start": xs[0]["s"], "end": xs[-1]["e"], "text": " ".join(w["w"] for w in xs), "words": xs}, "long", ())
        before = [(w["s"], w["e"], w["w"], True) for w in ws]
        a, b = mk(ws[:3]), mk(ws[3:])
        self.assertTrue(editor._cap_untouched(a, before, set()) and editor._cap_untouched(b, before, set()))
        new = [mk(ws[:4]), mk(ws[4:])]  # [가 나 다 라] [마 바]
        out, kept, skipped = editor.keep_edited_captions([dict(a, text="가 나 따"), b], new, before)
        self.assertEqual([(c["text"], c["start"], c["end"]) for c in out], [("가 나 따", 0.0, 1.5), ("라", 1.5, 2.0), ("마 바", 2.0, 3.0)],
                         "고친 자막 자리 낱말만 빼고 남은 '라'는 새 자막으로")
        self.assertEqual((kept, skipped), (1, 1))
        self.assertEqual(editor._cap_words(out[1])[0]["w"], "라", "다시 만든 자막도 단어 시각(wt)이 맞음")
        # 지운 자막 자리와 걸친 새 자막도 지운 말만 빠짐 (B 를 지움 → [가 나 다 라] 는 [가 나 다] 로 · [마 바] 는 안 넣음)
        out, kept, skipped = editor.keep_edited_captions([a], new, before)
        self.assertEqual([c["text"] for c in out], ["가 나 다"])
        self.assertEqual(out[0]["id"], a["id"], "줄인 결과가 예전과 시간·글이 같으면 예전 id")
        self.assertEqual((kept, skipped), (0, 2))

    def test_deleted_lines_vs_new_words_in_long_pause(self):
        """지운 말 자리는 이어진 낱말을 한 구간으로 (짧은 틈에 걸친 새 낱말이 조각 자막으로 안 살아남) · 긴 쉼(1초 넘게)에 새로 들린 말은 넣음."""
        ws = [W("가", 0, 0.4), W("나", 0.5, 0.9), W("다", 5.0, 5.4), W("라", 5.5, 5.9)]

        def mk(xs):
            return editor._make_cap({"start": xs[0]["s"], "end": xs[-1]["e"], "text": " ".join(w["w"] for w in xs), "words": xs}, "long", ())
        new = [mk([W("가", 0, 0.4)]), mk([W("음", 0.42, 0.48)]), mk([W("새말", 2.5, 3.0)]), mk([W("다", 5.0, 5.4), W("라", 5.5, 5.9)])]
        out, kept, skipped = editor.keep_edited_captions([], new, [(w["s"], w["e"], w["w"], True) for w in ws])  # 자막을 모두 지움
        self.assertEqual(([c["text"] for c in out], kept, skipped), (["새말"], 0, 3))

    def test_long_lesson_merges_fast(self):
        """리뷰: 자막 수 × 낱말 수로 느려져 2시간 영상이면 '가편집 만드는 중' 99% 에서 수십 초 멈췄음 (이 PC 에서 1,400줄 4.5초)."""
        segs, t = [], 0.0
        for i in range(1400):
            ws = [W(f"말{i}_{j}", t + j * 0.45, t + j * 0.45 + 0.4) for j in range(3)]
            segs.append({"start": ws[0]["s"], "end": ws[-1]["e"], "text": " ".join(w["w"] for w in ws), "words": ws})
            t += 2.85
        old, new, before = caps_of(segs), caps_of(segs), base_of(segs)
        old[5]["fill"] = "#FFD400"
        t0 = editor.time.perf_counter()
        out, kept, skipped = editor.keep_edited_captions(old, new, before)
        self.assertLess(editor.time.perf_counter() - t0, 2.0)
        self.assertEqual((len(out), kept, skipped), (len(old), 1, 1))


class ReanalyzeTest(WorkDir):
    """editor.reanalyze_project · app._analyze — 새 롱폼 가편집을 열 편집본으로 · fresh · 고친 자막 지킴 · 화면 알림 again (D-413 · D-414)."""

    def test_reanalyze_keeps_edits_and_opens_new_rough(self):
        name = self.video("다시 찾기.mp4", "320x180", 11.0)
        self.transcript(name, OLD_SEGS)
        proj = editor.load_project(name)
        first = proj["active"]
        find(proj["captions"], "안쪽으로")["text"] = "발 안쪽으로 받아요!"
        proj["captions"] = [c for c in proj["captions"] if "그러니까" not in c["text"]]
        editor.save_project(name, proj)
        before = editor.caption_baseline(name)
        self.assertEqual(len(before), 13)
        self.transcript(name, NEW_SEGS)
        out, added, changed = editor.reanalyze_project(name, before)
        self.assertTrue(added and changed)
        texts = [c["text"] for c in out["captions"]]
        self.assertIn("발 안쪽으로 받아요!", texts)
        self.assertIn("피벗을 해 봐요.", texts, "고치지 않은 줄은 고친 사전대로")
        self.assertNotIn("어 그러니까 음.", texts)
        fresh = [q for q in out["sequences"] if q["id"] in out["fresh"]["ids"]]
        self.assertEqual(len(fresh), added)
        self.assertTrue(all("(새로 찾음)" in q["name"] for q in fresh))
        act = next(q for q in out["sequences"] if q["id"] == out["active"])
        self.assertNotEqual(out["active"], first, "편집실은 새 가편집으로 엶 (예전: 예전 편집본 그대로)")
        self.assertEqual((act["format"], act["id"] in out["fresh"]["ids"]), ("long", True))
        self.assertEqual(out["fresh"]["kept"], 1)
        self.assertEqual(editor.load_project(name)["active"], out["active"], "저장됨")

    def test_without_baseline_replaces_all_like_before(self):
        name = self.video("기준 없음.mp4", "320x180", 11.0)
        self.transcript(name, OLD_SEGS)
        proj = editor.load_project(name)
        find(proj["captions"], "안쪽으로")["text"] = "발 안쪽으로 받아요!"
        editor.save_project(name, proj)
        self.transcript(name, NEW_SEGS)
        out, _, _ = editor.reanalyze_project(name)
        self.assertNotIn("발 안쪽으로 받아요!", [c["text"] for c in out["captions"]], "받아쓰기를 못 읽었으면 예전처럼 모두 새로")
        self.assertIsNone(editor.caption_baseline("없는 영상.mp4"))

    def test_app_analyze_reports_again(self):
        name = self.video("알림.mp4", "320x180", 11.0)
        self.transcript(name, OLD_SEGS)
        proj = editor.load_project(name)
        find(proj["captions"], "피버를")["fill"] = "#FFD400"
        editor.save_project(name, proj)
        fresh_name = self.video("처음.mp4", "320x180", 11.0)

        def many(names, log, model):
            for n in names:
                self.transcript(n, NEW_SEGS)
            return core.Analyzed([str(core.adir(n)) for n in names])
        lines = []
        with mock.patch.object(core, "analyze_many", side_effect=many), mock.patch.object(editor, "thumbs"), \
                mock.patch.object(editor, "waveform"), mock.patch.object(app, "log", side_effect=lines.append):
            res = app.Handler._analyze({"names": [name, fresh_name]})
        self.assertEqual(set(res["again"]), {name}, "처음 찾은 영상은 again 에 없음")
        self.assertEqual(res["again"][name]["kept"], 1)
        self.assertGreater(res["again"][name]["added"], 0)
        self.assertEqual(len(res["done"]), 2)
        self.assertTrue(any("손으로 고친 자막 1줄은 그대로" in ln for ln in lines), lines)
        self.assertEqual(find(editor.load_project(name)["captions"], "피버를")["fill"], "#FFD400")
        with mock.patch.object(core, "analyze_many", side_effect=many), mock.patch.object(editor, "thumbs"), \
                mock.patch.object(editor, "waveform"), mock.patch.object(app, "log"):
            shutil.rmtree(core.adir("새 영상.mp4"), ignore_errors=True)
            self.video("새 영상.mp4", "320x180", 11.0)
            res = app.Handler._analyze({"names": ["새 영상.mp4"]})
        self.assertIsInstance(res, list, "처음 찾을 때는 예전 그대로 (분석 폴더 목록)")


class SaveTrapTest(Server):
    """편집실 저장이 계속 안 될 때 (D-412) — 재현(백로그): projects/<영상>.json.tmp 자리를 폴더로 막으면 '저장 실패'만 보이고 화면·studio.log 에 까닭이 없었음."""

    def setUp(self):
        super().setUp()
        self.video("갇힘.mp4")
        self.proj = {"v": 2, "rev": 1, "source": "갇힘.mp4", "info": {"duration": 5, "width": 320, "height": 180},
                     "media": [], "sequences": [{"id": "s1", "name": "롱폼 가편집", "items": []}], "captions": []}
        editor._ppath("갇힘.mp4").write_text(json.dumps(self.proj, ensure_ascii=False), encoding="utf-8")
        app._SAVE_FAIL.clear()

    def test_reason_logged_once_and_rescue(self):
        tmp = editor._ppath("갇힘.mp4").with_name(editor._ppath("갇힘.mp4").name + ".tmp")
        tmp.mkdir()
        lines = []
        with mock.patch.object(app, "log", side_effect=lines.append):
            for _ in range(3):  # 편집실은 3초마다 다시 저장
                code, j = self.call("/api/edit/save", {"name": "갇힘.mp4", "project": self.proj, "rev": 1})
                self.assertEqual((code, j["ok"]), (500, False))
        self.assertEqual(j["why"], app.SAVE_WHY["other"])
        self.assertIn(j["why"], j["error"])
        self.assertEqual(sum("편집본을 저장하지 못했어요" in ln for ln in lines), 1, "같은 까닭은 한 번만 기록")
        self.assertNotIn("Errno", "".join(lines), "영어 원문은 화면 기록에 안 보임")
        home = self.tmp / "집"
        with mock.patch.object(Path, "home", return_value=home), mock.patch.object(app, "reveal") as rv, mock.patch.object(app, "log"):
            code, j = self.call("/api/edit/rescue", {"name": "갇힘.mp4", "project": dict(self.proj, captions=[{"text": "남길 자막"}])})
        self.assertEqual((code, j["ok"]), (200, True), j)
        self.assertEqual(j["where"], ["편집실 '기록' 탭 자동 백업", "다운로드 폴더"])
        down = list((home / "Downloads").iterdir())
        self.assertEqual(len(down), 1)
        self.assertEqual(json.loads(down[0].read_text(encoding="utf-8"))["captions"][0]["text"], "남길 자막")
        rv.assert_called_once_with(down[0])
        bk = editor.backups("갇힘.mp4")
        self.assertEqual(len(bk), 1)
        self.assertIn("저장하지 못한 창 내용", bk[0]["time"], "기록 탭에서 되돌릴 수 있게")
        self.assertEqual(self.call("/api/edit/rescue", {"name": "갇힘.mp4", "project": {"x": 1}})[0], 400)
        tmp.rmdir()
        with mock.patch.object(app, "log", side_effect=lines.append):
            self.assertEqual(self.call("/api/edit/save", {"name": "갇힘.mp4", "project": self.proj, "rev": 1})[0], 200)
        self.assertIn("편집본을 다시 저장했어요 · 갇힘.mp4", lines)

    def test_disk_full_and_locked_reasons(self):
        for err, kind in ((OSError(28, "No space left on device"), "disk"), (PermissionError(13, "Permission denied"), "locked")):
            with mock.patch.object(editor, "save_project", side_effect=err), mock.patch.object(app, "log"):
                code, j = self.call("/api/edit/save", {"name": "갇힘.mp4", "project": self.proj, "rev": 1})
            self.assertEqual((code, j["why"]), (500, app.SAVE_WHY[kind]))

    def test_rescue_falls_back_to_out_folder(self):
        home = self.tmp / "집"
        with mock.patch.object(Path, "home", return_value=home), mock.patch.object(editor, "PROJECTS", self.tmp / "없는" / "projects"), \
                mock.patch.object(editor.updater, "write_atomic", side_effect=[OSError(13, "Permission denied"), OSError(13, "Permission denied"), None]):
            files = editor.rescue_copy("갇힘.mp4", self.proj)
        self.assertEqual([f.parent for f in files], [core.OUT])


class BackNavTest(Server):
    """뒤로 가기로 다시 나온 예전 화면 (D-411) — no-store · 썸네일 판 번호.
    재현(백로그): 썸네일에 '새 도형 A'를 넣고 저장 → 스튜디오 → 뒤로 가기 → 예전 화면에서 '새 도형 B' → 서버가 ['배경','새 도형 B'] (A 가 말없이 사라짐)."""

    def head(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        with self.opener.open(req, timeout=20) as r:
            r.read()
            return r.headers

    def test_no_store_on_json_and_pages(self):
        for p in ("/", "/editor?name=x.mp4", "/thumb?name=x.mp4", "/api/dict", "/api/state"):
            self.assertEqual(self.head(p).get("Cache-Control"), "no-store", p)

    def doc(self, *names, rev=None):
        d = {"designs": [{"id": "d1", "name": "디자인 1", "doc": {"w": 1280, "h": 720, "layers": [{"name": n} for n in names]}}]}
        if rev is not None:
            d["rev"] = rev
        return d

    def test_thumb_save_rev_check(self):
        self.video("썸.mp4")
        code, j = self.call("/api/thumb/save", {"name": "썸.mp4", "docs": self.doc("배경")})  # 예전 화면(rev 없음): 확인 없이
        self.assertEqual((code, j["rev"]), (200, 1))
        self.assertEqual(thumb.load_docs("썸.mp4")["rev"], 1, "/api/thumb/open 이 판 번호를 함께 줌")
        code, j = self.call("/api/thumb/save", {"name": "썸.mp4", "docs": self.doc("배경", "새 도형 A", rev=1), "client": "새 창"})
        self.assertEqual((code, j["rev"]), (200, 2))
        code, j = self.call("/api/thumb/save", {"name": "썸.mp4", "docs": self.doc("배경", "새 도형 B", rev=1), "client": "예전 창"})
        self.assertEqual((code, j["conflict"], j["rev"]), (409, True, 2))
        self.assertEqual([l["name"] for l in thumb.load_docs("썸.mp4")["designs"][0]["doc"]["layers"]], ["배경", "새 도형 A"], "A 가 남음")
        # 같은 창이 닫을 때 보낸 신호 저장(답을 못 받아 판이 뒤처짐)은 이어서
        code, j = self.call("/api/thumb/save", {"name": "썸.mp4", "docs": self.doc("배경", "새 도형 A", "C", rev=1), "client": "새 창"})
        self.assertEqual((code, j["rev"]), (200, 3))
        # 그래도 이 창 내용으로: 그 전 디자인은 사본
        code, j = self.call("/api/thumb/save", {"name": "썸.mp4", "docs": self.doc("배경", "새 도형 B", rev=1), "client": "예전 창", "force": True})
        self.assertEqual((code, j["rev"]), (200, 4))
        cp = thumb._doc_path("썸.mp4").with_name(thumb._doc_path("썸.mp4").stem + ".덮어쓰기전.json.bak")
        self.assertEqual([l["name"] for l in json.loads(cp.read_text(encoding="utf-8"))["designs"][0]["doc"]["layers"]], ["배경", "새 도형 A", "C"])

    def test_thumb_page_handles_conflict(self):
        src = (REPO / "thumb.html").read_text(encoding="utf-8")
        self.assertIn("if (r && r.conflict) return onConflict();", src)
        self.assertIn("DOCS.rev = r.rev", src)
        self.assertIn("client: TCID", src)


NODE_DICT = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const a = src.indexOf("/* 2-2. 용어 사전: 받아쓰기에 알려 줄 용어"), b = src.indexOf("loadDict();\n", a);
const els = {};
const el = id => els[id] || (els[id] = { id, value: "", textContent: "", innerHTML: "", disabled: false, focus() {}, set onclick(f) { this._click = f; }, get onclick() { return this._click; } });
const $ = el, esc = s => String(s);
const document = { querySelectorAll: () => [] };
const posts = [], toasts = [], beacons = [];
let fail = false;
async function api(path, body) {
  if (body === undefined) return { terms: ["피벗"], fix: {}, defaults: { terms: ["피벗"], fix: {} } };
  posts.push(JSON.parse(JSON.stringify(body)));
  if (fail) return { ok: false, error: "x" };
  return { ok: true, terms: [...new Set(body.terms)], fix: body.fix };
}
const toast = m => toasts.push(m);
const listeners = {};
const window = { addEventListener: (k, f) => (listeners[k] = f) };
const navigator = { sendBeacon: (u, blob) => { beacons.push(u); return true; } };
class Blob { constructor(p) { this.p = p; } }
const confirm = () => true;
const sleep = ms => new Promise(r => setTimeout(r, ms));
eval(src.slice(a, b) + "globalThis.T = { get dict() { return dict; }, dictFlush, addTerm, get saved() { return dictSaved; } };");
(async () => {
  await loadDict(); await sleep(5);
  const out = {};
  $("dictTerm").value = "스쿱턴"; addTerm();
  out.stateNow = $("dictState").textContent;
  out.postsNow = posts.length;
  await sleep(1300);
  out.postsAfter = posts.length; out.saved = T.saved; out.state = $("dictState").textContent;
  $("dictTerm").value = "파라렐라"; addTerm();
  await T.dictFlush();  // 편집점 찾기 직전 (job)
  out.flushPosts = posts.length; out.flushTerms = posts[posts.length - 1].terms;
  $("dictTerm").value = "마르세유"; addTerm();
  listeners.pagehide();  // 다른 화면으로 (편집실 열기)
  out.beacons = beacons;
  fail = true; $("dictTerm").value = "토킥"; addTerm(); await T.dictFlush();
  out.failState = $("dictState").textContent; out.saveBtn = $("dictSave").disabled;
  out.flushHook = typeof window.flushBeforeClose;
  console.log(JSON.stringify(out));
})();
"""


class DictAutosaveTest(unittest.TestCase):
    """ui.html 용어 사전 (D-410) — 재현(백로그): 용어를 넣고 '저장'을 안 누른 채 편집점 찾기·편집실 열기에 다녀오면 서버 용어는 그대로였음."""

    def test_autosave_flush_and_leave(self):
        if not shutil.which("node"):
            self.skipTest("node 없음")
        r = subprocess.run(["node", "-e", NODE_DICT, str(REPO / "ui.html")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["stateNow"], out["postsNow"]), ("저장 중…", 0), "넣자마자 '저장 중…' · 1초 기다렸다 한 번에")
        self.assertEqual(out["postsAfter"], 1)
        self.assertEqual(json.loads(out["saved"])["terms"], ["스쿱턴", "피벗"])
        self.assertIn("저장됨", out["state"])
        self.assertEqual((out["flushPosts"], out["flushTerms"]), (2, ["파라렐라", "스쿱턴", "피벗"]), "편집점 찾기 직전에 바로 저장")
        self.assertEqual(out["beacons"], ["/api/dict"], "다른 화면으로 갈 때 기다리던 저장을 보냄")
        self.assertIn("저장하지 못했어요", out["failState"])
        self.assertFalse(out["saveBtn"], "실패하면 [저장]으로 다시")
        self.assertEqual(out["flushHook"], "function")

    def test_jobs_that_transcribe_flush_dict_first(self):
        src = (REPO / "ui.html").read_text(encoding="utf-8")
        self.assertIn('const DICT_JOBS = ["/api/analyze", "/api/auto"];', src)
        self.assertIn("(DICT_JOBS.includes(path) ? dictFlush().catch(() => {}) : Promise.resolve()).then(() => api(path, body))", src)


NODE_FRESH = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const a = src.indexOf("function seqSig(q)"), b = src.indexOf("function hideFreshBan()", a);
let P = null;
eval(src.slice(a, b) + "globalThis.F = { seqSig, signFresh, oldFresh };");
const seq = (id, name, auto) => ({ id, name, auto, format: "long", items: [{ id: id + "i", track: "V1", start: 0, in: 0, out: 5 }], titles: [] });
const out = {};
// 1번째 다시 찾기 뒤 처음 열 때: 이번에 만든 것에 지문
P = { active: "n1", fresh: { ids: ["n1", "n2"] }, sequences: [seq("u", "롱폼 가편집", "rough"), seq("n1", "롱폼 정리본 (새로 찾음)", "rough"), seq("n2", "쇼츠 1 (새로 찾음)", "rough")] };
F.signFresh();
out.signed = P.sequences.map(q => !!q.fsig);
const s1 = P.sequences[1].fsig; F.signFresh(); out.stable = P.sequences[1].fsig === s1;
// 저녁 내내 n1 을 다듬음 (이름은 그대로 · auto 도 'rough' 그대로) · n2 는 안 건드림 → 저장 → 다시 엶
P.sequences[1].items[0].out = 3; P.sequences[1].titles.push({ text: "오늘의 기술" });
P = JSON.parse(JSON.stringify(P)); delete P.fresh;
// 2번째 다시 찾기 (새 n3 를 엶)
P.fresh = { ids: ["n3"] }; P.active = "n3"; P.sequences.push(seq("n3", "롱폼 정리본 (새로 찾음) 2", "rough"));
P.sequences.push(seq("x", "옛 버전이 만든 (새로 찾음)", "rough"));  // 지문이 없는 예전 것
F.signFresh();
out.offer = F.oldFresh().map(q => q.id);
console.log(JSON.stringify(out));
"""


NODE_THUMB_LEAVE = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const a = src.indexOf("let gone = "), b = src.indexOf("// 창을 닫거나 숨길 때", a);
const c = src.indexOf('$("back").onclick = async'), d = src.indexOf("\n};\n", c) + 4;
const els = {};
const $ = id => els[id] || (els[id] = { textContent: "", onclick: null });
let D = {}, DOCS = {}, saveTimer = null, saveGen = 0, reloads = 0, asks = [];
const toast = () => {}, renderDesigns = () => {}, saveBody = () => "{}";
const location = { href: "", reload() { reloads++; } };
let answer = true;
const confirm = m => { asks.push(m.slice(0, 16)); return answer; };
let reply = { ok: false, conflict: true, rev: 5 };
const postRaw = async () => reply;
eval(src.slice(a, b) + src.slice(c, d));
(async () => {
  const out = {};
  await $("back").onclick();  // 다른 곳이 더 최근에 저장 → [확인] 다시 불러오기
  out.reload = { asks: asks.length, reloads, href: location.href };
  await $("back").onclick();  // 다시 불러오는 중에 또 눌러도 '저장하지 못했어요'를 묻지 않음
  out.again = { asks: asks.length, href: location.href };
  console.log(JSON.stringify(out));
})();
"""


class FreshCleanupPageTest(unittest.TestCase):
    """editor.html 예전 '(새로 찾음)' 정리 (D-416) — 리뷰: auto 표시는 복제·이름 바꾸기에만 바뀌어, 손으로 다듬은 '(새로 찾음)'도 지우자고 했음."""

    def test_only_untouched_fresh_cuts_are_offered(self):
        if not shutil.which("node"):
            self.skipTest("node 없음")
        r = subprocess.run(["node", "-e", NODE_FRESH, str(REPO / "editor.html")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["signed"], [False, True, True], "이번에 만든 편집본만 지문")
        self.assertTrue(out["stable"])
        self.assertEqual(out["offer"], ["n2"], "다듬은 n1 · 지문 없는 예전 것 · 지금 연 n3 는 지우자고 안 함")
        src = (REPO / "editor.html").read_text(encoding="utf-8")
        self.assertIn("P.sequences.forEach(ensureSeq); signFresh();", src, "화면에 묶기 전에 지문")
        self.assertNotIn("내가 만든 편집본은 그대로예요", src)


class ThumbLeaveTest(unittest.TestCase):
    """thumb.html 나가기 (D-416) — 리뷰: 다른 곳 최근 내용 [확인](다시 불러오기) 바로 뒤에 '저장하지 못했어요. 그래도 나갈까요?'를 또 물었음."""

    def test_reload_choice_does_not_ask_again(self):
        if not shutil.which("node"):
            self.skipTest("node 없음")
        r = subprocess.run(["node", "-e", NODE_THUMB_LEAVE, str(REPO / "thumb.html")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["reload"], {"asks": 1, "reloads": 1, "href": ""}, "한 번만 묻고 다시 불러옴 (홈으로 가지 않음)")
        self.assertEqual(out["again"], {"asks": 1, "href": ""})


class EditorPageTest(unittest.TestCase):
    """editor.html — 저장이 계속 안 될 때 나가는 길 · 다시 찾은 뒤 알림 (화면 코드는 ed2 e2e 묶음이 실제로 돌림)."""

    def test_wiring(self):
        src = (REPO / "editor.html").read_text(encoding="utf-8")
        for s in ('id="sfModal"', ">편집본 파일로 내려받기<", ">저장 안 하고 나가기<",
                  "else if (!conflict && saveFails >= 2) showSaveFail(url, back);", 'post("/api/edit/rescue"',
                  "if (!showFreshBan()) showCapBan(j.capLong, j.capMerged);"):
            self.assertIn(s, src)


if __name__ == "__main__":
    unittest.main()
