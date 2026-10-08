"""NG 테이크·슬레이트 말·말더듬 자동 정리(takes.find_junk · editor.recommend) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_takes

회귀 기준 fixtures/takes_before.json 은 바꾸기 전 editor.recommend(git 998a5ee:editor.py)를 그대로 돌려 저장한 결과
(코치 설명 받아쓰기 · 편집실 테스트 영상 받아쓰기 × 말 사이 공백 스타일 4가지).
받아쓰기 모델은 흉내(가짜 faster_whisper)만 쓰고, 조용한 곳은 진짜 ffmpeg 로 찾음. 인터넷은 쓰지 않음."""
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy  # noqa: F401 — sys.modules 를 잠시 바꾸는 시험 전에 먼저 불러 둠 (되돌릴 때 지워지면 다시 못 불러옴)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402
import takes  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
NG, SLATE, STUTTER = "다시 찍은 앞 테이크", "슬레이트 말", "말더듬"


def S(a, b, t):
    return {"start": a, "end": b, "text": t}


# (a) 앞 테이크 → 슬레이트 → 다시 찍은 테이크
RETAKE = [S(1.0, 4.0, "자 퍼스트 터치는요 발 안쪽으로…"), S(4.6, 5.6, "아 다시 할게요"), S(7.0, 10.2, "퍼스트 터치는 발 안쪽으로 받아요")]


class FindJunkTest(unittest.TestCase):
    """takes.find_junk — 받아쓴 구간만 보는 순수 함수."""

    def test_retake_with_slate(self):
        # 앞 테이크 시작 ~ 다시 찍은 테이크 시작 (사이의 슬레이트 말은 그 안에 들어가서 따로 안 나옴)
        self.assertEqual(takes.find_junk(RETAKE, []), [(1.0, 7.0, NG)])

    def test_retake_without_slate(self):
        segs = [S(1.0, 3.0, "퍼스트 터치는요"), S(5.5, 9.0, "퍼스트 터치는 발 안쪽으로 받아서 바로 돌아요")]
        self.assertEqual(takes.find_junk(segs, []), [(1.0, 5.5, NG)])
        trail = [S(1.0, 4.0, "공이 오면 발 안쪽으로 받아서 어 그러니까"), S(5.5, 9.0, "공이 오면 발 안쪽으로 받아요")]  # 끝의 머뭇거림은 빼고 봄
        self.assertEqual(takes.find_junk(trail, []), [(1.0, 5.5, NG)])

    def test_three_takes_keep_last(self):
        segs = [S(1.0, 3.0, "오늘은 퍼스트 터치를"), S(6.0, 8.0, "오늘은 퍼스트 터치를 어"),
                S(11.0, 14.0, "오늘은 퍼스트 터치를 배워볼게요"), S(15.0, 18.0, "공이 오면 발 안쪽으로 받아요")]
        self.assertEqual(takes.find_junk(segs, []), [(1.0, 6.0, NG), (6.0, 11.0, NG)])

    def test_aborted_take_of_several_lines(self):
        # 두 줄 말하다 틀려서 처음부터 — 사이의 말이 다시 찍은 쪽에 다시 나오면 같은 테이크로 봄
        segs = [S(1.0, 4.0, "오늘은 퍼스트 터치를 배워볼게요"), S(4.5, 8.0, "공이 오면 발 바깥쪽으로 어 아니"),
                S(10.0, 13.0, "오늘은 퍼스트 터치를 배워볼게요"), S(13.5, 17.0, "공이 오면 발 안쪽으로 받아요")]
        self.assertEqual(takes.find_junk(segs, []), [(1.0, 10.0, NG)])

    def test_nested_retake_inside_bad_take(self):
        # 검수: 틀린 테이크 안에서 한 줄을 또 다시 말해도(작은 짝이 안에 들어 있어도) 바깥 NG 를 버리지 않음
        segs = [S(1.0, 4.0, "오늘은 퍼스트 터치를 배워볼게요"), S(4.5, 8.0, "공이 오면 발 안쪽으로"),
                S(9.0, 12.0, "공이 오면 발 안쪽으로 어 그러니까"), S(12.5, 13.5, "아 다시 할게요"),
                S(15.0, 18.0, "오늘은 퍼스트 터치를 배워볼게요"), S(18.5, 22.0, "공이 오면 발 안쪽으로 받아요")]
        self.assertEqual(takes.find_junk(segs, []), [(1.0, 15.0, NG)])
        no_slate = segs[:3] + [S(13.0, 16.0, segs[4]["text"]), S(16.5, 20.0, segs[5]["text"])]
        self.assertEqual(takes.find_junk(no_slate, []), [(1.0, 13.0, NG)])

    def test_several_line_take_shot_three_times(self):
        # 두 줄짜리를 세 번 → 앞의 두 번 다 지우고 마지막 두 줄만 남김 (둘째 줄끼리 짝이 새 테이크 첫 줄을 지우지 않음)
        intro = "오늘은 퍼스트 터치를 배워볼게요"
        segs = [S(1, 4, intro), S(4.5, 8, "공이 오면 발 바깥쪽으로 어"), S(8.5, 9.5, "아 다시 할게요"),
                S(11, 14, intro), S(14.5, 18, "공이 오면 발 안쪽으로 밀어 아"), S(18.5, 19.5, "죄송합니다 다시 갈게요"),
                S(21, 24, intro), S(24.5, 28, "공이 오면 발 안쪽으로 받아요"), S(28.5, 31, "이게 진짜 중요해요")]
        self.assertEqual(takes.find_junk(segs, []), [(1, 11, NG), (11, 21, NG)])

    def test_tip_between_same_lines_is_kept(self):
        # 검수: 같은 강조 말로 감싼 팁 · 나란한 설명은 슬레이트 말이 없으면 NG 가 아님 (가운데 팁이 지워지면 안 됨)
        cases = [
            [S(0.5, 3.0, "퍼스트 터치 꿀팁 알려드릴게요"), S(3.5, 5.5, "이게 진짜 중요해요"), S(6.0, 9.0, "공을 보지 말고 앞을 보세요"),
             S(9.5, 11.5, "이게 진짜 중요해요"), S(12.0, 15.0, "그래야 패스가 빨라져요")],
            [S(0.5, 3.0, "퍼스트 터치 꿀팁"), S(3.5, 5.0, "이게 핵심이에요"), S(5.5, 7.5, "시선은 항상 앞으로"), S(8.0, 10.0, "몸은 반쯤 열고"),
             S(10.8, 13.0, "이게 핵심이에요 여러분")],
            [S(1.0, 4.0, "공을 받을 때는 몸을 열고"), S(5.0, 8.0, "공을 받을 때는 시선을 앞으로")],
            [S(1.0, 4.0, "공이 오면 발 안쪽으로 받아요"), S(5.0, 8.0, "공이 오면 발 바깥쪽으로 밀어요")],
        ]
        for segs in cases:
            with self.subTest(segs[1]["text"]):
                self.assertEqual(takes.find_junk(segs, []), [])
        # 고쳐 말하기('아 아니다' · '그게 아니라')만 사이에 있으면 슬레이트 말이 없어도 NG
        for fix in ("아 아니다", "그게 아니라"):
            segs = [S(1.0, 3.0, "오늘은 퍼스트 터치를 배워볼"), S(3.5, 4.5, fix), S(6.0, 9.0, "오늘은 퍼스트 터치를 배워볼게요")]
            self.assertEqual(takes.find_junk(segs, []), [(1.0, 6.0, NG)], fix)
        segs[1] = S(3.5, 4.5, "아니요")  # 대답은 내용
        self.assertEqual(takes.find_junk(segs, []), [])

    def test_retake_window_3s_to_60s(self):
        early = [S(1.0, 2.0, "패스를 받을 때는요"), S(3.5, 6.0, "패스를 받을 때는 고개를 먼저 들어요")]  # 2.5초 차이
        self.assertEqual([j for j in takes.find_junk(early, []) if j[2] == NG], [])
        mid = S(30.0, 31.0, "다시 갈게요")
        ok = [S(1.0, 3.0, "패스를 받을 때는요"), mid, S(60.5, 64.0, "패스를 받을 때는 고개를 먼저 들어요")]  # 59.5초
        self.assertIn((1.0, 60.5, NG), takes.find_junk(ok, []))
        far = [S(1.0, 3.0, "패스를 받을 때는요"), mid, S(61.5, 65.0, "패스를 받을 때는 고개를 먼저 들어요")]  # 60.5초
        self.assertEqual([j for j in takes.find_junk(far, []) if j[2] == NG], [])

    def test_short_or_different_lines_are_not_retakes(self):
        cases = [
            [S(1.0, 2.0, "좋아요 좋아"), S(5.0, 6.0, "좋아요 좋아")],  # 5글자: 너무 짧음
            [S(1.0, 3.0, "발 안쪽으로 받아요"), S(5.0, 7.0, "발 바깥쪽으로 받아요")],  # 비교하는 말(대조)
            [S(1.0, 4.0, "오른발로 받으면 왼쪽으로 돌기가 쉬워요"), S(5.0, 8.0, "왼발로 받으면 오른쪽으로 돌기가 쉬워요")],
            [S(1.0, 4.0, "공이 오면 발 안쪽으로 받아요"), S(9.0, 12.0, "다리가 굳어 있으면 공이 튀어 나가요"),
             S(13.0, 16.0, "무릎을 살짝 굽히고 힘을 빼세요"), S(17.0, 20.0, "공이 오면 발 안쪽으로 받아요")],  # 사이에 다른 설명
        ]
        for segs in cases:
            with self.subTest(segs[0]["text"]):
                self.assertEqual(takes.find_junk(segs, []), [])

    def test_long_quiet_between_needs_slate(self):
        # 말 없이 20초 (시범 장면일 수 있음) → 같은 말이어도 그대로, 슬레이트 말이 있으면 NG
        segs = [S(1.0, 4.0, "공이 오면 발 안쪽으로 받아요"), S(24.0, 27.0, "공이 오면 발 안쪽으로 받아요 이렇게")]
        self.assertEqual(takes.find_junk(segs, []), [])
        segs.insert(1, S(5.0, 6.0, "아 잠깐만요"))
        self.assertEqual(takes.find_junk(segs, []), [(1.0, 24.0, NG)])

    def test_contrast_lines_are_not_retakes(self):
        # 리뷰 지적: 안 되는 예 → (시범) → 되는 예 · 오른발/왼발 · 한 번/두 번 은 슬레이트 말이 없으면 둘 다 남김
        pairs = [("이렇게 하면 안 돼요", "이렇게 하면 돼요"),
                 ("이번엔 오른발로 받아서 안쪽으로 치고 나가요", "이번엔 왼발로 받아서 안쪽으로 치고 나가요"),
                 ("자 이번에는 한 번 터치하고 슈팅", "자 이번에는 두 번 터치하고 슈팅"),
                 ("이렇게 하면 돼요", "이렇게 하면 절대 안 돼요")]
        for a, b in pairs:
            for gap in (5, 12):
                with self.subTest(a=a, gap=gap):
                    segs = [S(0, 3, a), S(3 + gap, 6 + gap, b), S(7 + gap, 10 + gap, "그래서 이게 중요해요")]
                    self.assertEqual(takes.find_junk(segs, []), [])
                    segs.insert(1, S(3.5, 4.5, "아 다시 할게요"))  # 슬레이트 말이 있으면 코치가 다시 찍은 것
                    self.assertEqual(takes.find_junk(segs, []), [(0, 3 + gap, NG)])  # 슬레이트 말은 NG 구간 안에 듦

    def test_unfinished_first_take_still_retake(self):
        # 슬레이트 말이 없어도 끊긴 앞 테이크(뒤 말의 앞부분 · 끝 머뭇거림 · 가운데 추임새만 다름)는 NG
        for a in ("공이 오면 발 안쪽으로", "공이 오면 발 안쪽으로 받아서 음 아니", "공이 오면 발 어 안쪽으로 받아요"):
            with self.subTest(a=a):
                segs = [S(0, 3, a), S(8, 11, "공이 오면 발 안쪽으로 받아요")]
                self.assertEqual(takes.find_junk(segs, []), [(0, 8, NG)])

    def test_emphasis_inside_one_segment_kept(self):
        # (b) 한 구간 안의 '빠르게, 빠르게!' 는 강조라서 그대로
        segs = [S(1.0, 4.0, "공을 받을 때는"), S(4.5, 5.7, "빠르게, 빠르게!"), S(6.3, 9.0, "몸을 돌려서 패스하세요")]
        self.assertEqual(takes.find_junk(segs, []), [])

    def test_stutter_across_segments_keeps_last(self):
        # E6: 줄마다 나뉜 외친 강조('빠르게,' → '빠르게!')는 한 구간 안의 강조와 똑같이 그대로 (예전에는 앞의 것을 말더듬으로 지움)
        split = [S(1.0, 4.0, "공을 받을 때는"), S(4.5, 4.9, "빠르게,"), S(5.3, 5.8, "빠르게!"), S(6.4, 9.0, "몸을 돌려서 패스하세요")]
        self.assertEqual(takes.find_junk(split, []), [])
        mumble = [S(1.0, 4.0, "공을 받을 때는"), S(4.5, 4.9, "그래서"), S(5.3, 5.8, "그래서"), S(6.4, 9.0, "몸을 돌려서 패스하세요")]
        self.assertEqual(takes.find_junk(mumble, []), [(4.5, 5.3, STUTTER)])  # 외치지 않은 같은 말 반복은 그대로 말더듬
        false_start = [S(1.0, 1.4, "퍼스트"), S(1.9, 2.3, "퍼스트"), S(2.8, 6.0, "퍼스트 터치는 발 안쪽으로 받아요")]
        self.assertEqual(takes.find_junk(false_start, []), [(1.0, 1.9, STUTTER), (1.9, 2.8, STUTTER)])
        slow = [S(4.5, 4.9, "빠르게"), S(6.2, 8.0, "빠르게 돌아서 패스")]  # 1.3초 뒤 → 말더듬 아님
        self.assertEqual(takes.find_junk(slow, []), [])
        # 검수: 한 마디씩 늘려 가는 순서 설명·숫자 세기는 말더듬 아님
        steps = [S(10, 11.2, "받고"), S(11.6, 12.9, "받고 돌고"), S(13.3, 15.5, "받고 돌고 슈팅"), S(16, 19, "이 순서로 연습하세요")]
        self.assertEqual(takes.find_junk(steps, []), [])
        self.assertEqual(takes.find_junk([S(1, 2.2, "하나 둘"), S(2.6, 3.9, "하나 둘 셋")], []), [])

    def test_drill_counts_and_cheers_split_by_line_are_kept(self):
        # E6 (탐색 7회차): 받아쓰기가 구령·환호를 줄마다 나눠도 말더듬이 아님 — 시범 박자 구령과 성공 리액션이 가편집에서 빠지면 안 됨
        cases = {
            "숫자 구령": [S(113.64, 114.42, "하나, 둘, 셋."), S(114.96, 118.2, "하나, 둘, 셋.")],  # MSGRAW01 실측 받아쓰기 그대로
            "외친 구령": [S(10, 11.5, "하나, 둘, 셋!"), S(12, 13.5, "하나, 둘, 셋!")],
            "환호": [S(123.88, 124.52, "나이스!"), S(124.8, 125.4, "나이스!")],
            "칭찬": [S(10, 10.6, "좋아요."), S(10.9, 11.5, "좋아요.")],
            "재촉": [S(10, 10.6, "빠르게!"), S(10.9, 11.5, "빠르게!")],
            "골": [S(10, 10.4, "골!"), S(10.8, 11.2, "골!")],
            "리듬 말 세 번": [S(10, 11.5, "왼발, 오른발,"), S(12, 13.5, "왼발, 오른발,"), S(14, 15.5, "왼발, 오른발,")],
            "영어 구령": [S(10, 10.8, "원, 투"), S(11.2, 12.0, "원, 투")],
            "숫자로 받아쓴 구령": [S(10, 10.8, "1, 2, 3"), S(11.2, 12.0, "1, 2, 3")],
            "추임새 붙은 구령": [S(10, 10.8, "자 하나 둘"), S(11.2, 12.0, "하나 둘")],
        }
        for k, segs in cases.items():
            with self.subTest(k):
                self.assertEqual(takes.find_junk(segs, []), [])
        # 구령이 들어가도 설명 문장이 붙으면 구령이 아님 (끊긴 첫마디는 그대로 말더듬)
        self.assertFalse(takes.is_chant("하나 둘 셋 리듬으로 움직여 보세요"))
        self.assertFalse(takes.is_chant("이게 중요해요"))
        self.assertFalse(takes.is_chant("음"))
        self.assertTrue(takes.is_chant("좋아요!"))
        self.assertTrue(takes.is_chant("하나둘셋"))
        # 구령 낱말이어도 짧게 끊긴 첫마디('패스' → '패스할 때는 …')는 말더듬 (같은 말 반복만 구령으로 봄)
        self.assertEqual(takes.find_junk([S(0, 0.4, "패스"), S(0.8, 4, "패스할 때는 발 안쪽으로 밀어 주세요")], []), [(0, 0.8, STUTTER)])
        self.assertEqual(takes.find_junk([S(0, 0.4, "퍼스트"), S(0.8, 4, "퍼스트 터치는 발 안쪽으로 받아요")], []), [(0, 0.8, STUTTER)])

    def test_same_explanation_around_demo_is_not_ng(self):
        # E6 (탐색 7회차): '같은 설명 → 시범 → 같은 설명 → 시범' 드릴 — 사이에 시범(말 없는 3초 넘는 장면·큰 소리)이 있고
        # 슬레이트 말이 없으면 첫 설명·첫 시범을 지우지 않음
        line = "패스하고 바로 앞으로 뛰어나가세요."
        segs = [S(10, 12.5, line), S(20, 22.5, line)]
        self.assertEqual(takes.find_junk(segs, []), [])  # 조용한 곳 정보가 없으면 7.5초 빈 곳은 시범으로 봄
        self.assertEqual(takes.find_junk(segs, [{"start": 12.6, "end": 19.9}]), [(10, 20, NG)])  # 다 조용하면 다시 찍으려 쉰 것
        self.assertEqual(takes.find_junk(segs, [{"start": 12.6, "end": 19.9}], [{"time": 15.0, "rms_db": -8.0}]), [])  # 공 차는 소리
        self.assertEqual(takes.find_junk(segs, [{"start": 12.6, "end": 19.9}], [15.0]), [])  # 시각만 넘겨도 됨
        self.assertEqual(takes.find_junk(segs, [{"start": 12.6, "end": 15.0}, {"start": 16.5, "end": 19.9}]), [(10, 20, NG)])  # 말 없는 소리 1.5초
        # 소리만 있고 봉우리가 없으면 6초 넘게 이어져야 시범 (체육관·운동장은 늘 시끄러워서 4~5초 숨 고르기는 다시 찍기)
        self.assertEqual(takes.find_junk(segs, [{"start": 12.6, "end": 14.0}, {"start": 18.5, "end": 19.9}]), [(10, 20, NG)])
        self.assertEqual(takes.find_junk(segs, [{"start": 12.6, "end": 13.0}, {"start": 19.5, "end": 19.9}]), [])  # 6.5초 시범 소리
        show = [S(10, 12, "한 번 더 보여 드릴게요."), S(20, 22, "한 번 더 보여 드릴게요.")]
        self.assertEqual(takes.find_junk(show, []), [])
        # 슬레이트 말이 있으면 시범이 있어도 NG · 끊긴 앞 테이크도 그대로 NG
        self.assertEqual(takes.find_junk([S(10, 12.5, line), S(13, 14, "아 다시 할게요"), S(20, 22.5, line)], []), [(10, 20, NG)])
        self.assertEqual(takes.find_junk([S(10, 11.5, "패스하고 바로 앞으로"), S(20, 22.5, line)], []), [(10, 20, NG)])
        self.assertEqual(takes.find_junk([S(10, 12.5, "패스하고 어 바로 앞으로 뛰어나가세요."), S(20, 22.5, line)], []), [(10, 20, NG)])
        # 사이가 3초 이하면 시범으로 안 봄 (같은 말을 바로 다시 한 것)
        self.assertEqual(takes.find_junk([S(10, 12.5, line), S(15.5, 18, line)], []), [(10, 15.5, NG)])

    def test_demo_protection_with_coach_fillers_and_noisy_retakes(self):
        # E6 검토: 코치는 거의 모든 말을 '자,'로 시작하고 '…, 네.'로 끝냄 — 이런 말버릇이 있어도 다 말한 설명이라 시범 사이 첫 설명은 그대로
        line = "패스하고 바로 앞으로 뛰어나가세요."
        peak = [15.0]
        for a, b in ((f"자, {line}", f"자, {line}"), (f"자, {line}", line), (f"{line[:-1]}, 네.", f"{line[:-1]}, 네."),
                     ("자 이렇게 발 안쪽으로 받아 주세요.", "자 이렇게 발 안쪽으로 받아 주세요.")):
            with self.subTest(a=a, b=b):
                self.assertEqual(takes.find_junk([S(10, 12.5, a), S(20, 22.5, b)], [], peak), [])  # 공 차는 소리
                self.assertEqual(takes.find_junk([S(10, 12.5, a), S(20, 22.5, b)], []), [])  # 조용한 곳 정보 없이 7.5초
        # 머뭇거림이 든 앞 테이크는 그대로 NG ('자,'로 시작해도)
        self.assertEqual(takes.find_junk([S(10, 12.5, "자, 패스하고 음 바로 앞으로 뛰어나가세요."), S(20, 22.5, line)], [], peak), [(10, 20, NG)])
        self.assertEqual(takes.find_junk([S(10, 12.5, "자, 패스하고 바로 앞으로 뛰어나가세요 어"), S(20, 22.5, line)], [], peak), [(10, 20, NG)])
        # 시끄러운 곳에서 4초 숨 고르고 슬레이트 말 없이 다시 한 말은 NG (봉우리 없음 · 조용한 곳 없음)
        self.assertEqual(takes.find_junk([S(10, 12.5, line), S(16.5, 19, line)], []), [(10, 16.5, NG)])
        # 짧은 빈 곳의 큰 소리(카메라에 공이 맞음)는 시범이 아님 → NG
        self.assertEqual(takes.find_junk([S(10, 12.5, line), S(13.4, 15.9, line)], [], [12.6]), [(10, 13.4, NG)])
        # 봉우리 창(1초)이 말에 걸치면 시범 소리로 안 봄 (말소리 자체가 큰 것)
        self.assertEqual(takes.find_junk([S(10, 12.5, line), S(16.8, 19.3, line)], [], [12.0]), [(10, 16.8, NG)])

    def test_chant_words_do_not_shield_real_stutters(self):
        # E6 검토: 방향·동작 말('앞으로' · '패스')은 문장 첫마디로도 흔함 — 혼자 두 번이면 말더듬 · 외친 줄('패스!')·두 낱말 리듬만 구령
        self.assertEqual(takes.find_junk([S(0, 0.4, "앞으로"), S(0.6, 1.0, "앞으로"), S(1.1, 4, "나가면서 받으세요")], []), [(0, 0.6, STUTTER)])
        self.assertEqual(takes.find_junk([S(0, 0.4, "패스"), S(0.6, 1.0, "패스"), S(1.1, 4, "패스할 때는 디딤발을 공 옆에 두세요")], []),
                         [(0, 0.6, STUTTER), (0.6, 1.1, STUTTER)])  # '패스 패스할 때는' 이 남지 않게 둘 다
        self.assertEqual(takes.find_junk([S(0, 0.4, "계속"), S(0.6, 1.0, "계속")], []), [(0, 0.6, STUTTER)])
        self.assertEqual(takes.find_junk([S(0, 0.4, "패스!"), S(0.6, 1.0, "패스!")], []), [])  # 드릴 중 외친 구령
        self.assertEqual(takes.find_junk([S(0, 0.8, "안쪽, 바깥쪽"), S(1.0, 1.8, "안쪽, 바깥쪽")], []), [])
        for w in ("앞으로", "패스", "계속", "빨리", "더", "옆", "위"):
            self.assertFalse(takes.is_chant(w), w)
        self.assertTrue(takes.is_chant("빠르게!"))
        self.assertFalse(takes.is_chant("와"))  # '와'는 구령 낱말에서 뺌 ('우와'는 그대로)
        self.assertTrue(takes.is_chant("우와!"))

    def test_shouted_lines_are_not_blanket_exempt(self):
        # E6 검토: Whisper 가 붙인 '!' 하나로 반복이 다 살아나면 안 됨 — 똑같이 외친 짧은 말·긴 문장 반복은 그대로 지움
        self.assertEqual(takes.find_junk([S(0, 0.4, "그러니까!"), S(0.6, 1.0, "그러니까!"), S(1.2, 4, "공을 끝까지 보세요")], []),
                         [(0, 0.6, STUTTER)])
        self.assertFalse(takes.repeat_ok("오늘은 슈팅 챌린지예요!", "오늘은 슈팅 챌린지예요!"))
        self.assertTrue(takes.find_junk([S(0, 1.8, "오늘은 슈팅 챌린지예요!"), S(2.0, 3.8, "오늘은 슈팅 챌린지예요!")], []))
        self.assertFalse(takes.repeat_ok("오늘은 정말 중요한 슈팅 챌린지를 할 거예요,", "오늘은 정말 중요한 슈팅 챌린지를 할 거예요!"))
        self.assertTrue(takes.repeat_ok("빠르게,", "빠르게!"))  # 짧은 말을 외치며 다시 함
        self.assertTrue(takes.repeat_ok("나이스!", "나이스!"))

    def test_slate_from_preceding_silence(self):
        # (c) 1초 이상 조용한 곳의 시작부터 슬레이트 말 끝까지 (1초보다 짧은 조용함은 안 봄)
        segs = [S(0.5, 4.2, "오늘은 퍼스트 터치를 배워볼게요"), S(6.2, 7.0, "아 잠깐만요"), S(9.0, 12.5, "공이 오면 발 안쪽으로 받아요")]
        sil = [{"start": 4.3, "end": 5.5}, {"start": 5.7, "end": 6.1}, {"start": 7.1, "end": 8.9}]
        self.assertEqual(takes.find_junk(segs, sil), [(4.3, 7.0, SLATE)])
        self.assertEqual(takes.find_junk(segs, []), [(6.2, 7.0, SLATE)])  # 조용한 곳 정보가 없으면 그 구간 시작부터
        self.assertEqual(takes.find_junk(segs, [{"start": 5.2, "end": 6.1}]), [(6.2, 7.0, SLATE)])  # 0.9초
        # 조용함이 앞 말 끝보다 먼저 시작하면 앞 말은 건드리지 않음
        self.assertEqual(takes.find_junk(segs, [{"start": 3.9, "end": 6.1}]), [(4.2, 7.0, SLATE)])

    def test_slate_words(self):
        yes = ["아 다시 할게요", "다시할께요", "다시 갈게요", "처음부터 다시 할게요", "자 처음부터", "처음부터 할게요", "NG", "ng 다시",
               "엔지", "잠깐만요", "아 잠깐만", "잠시만요", "아 틀렸다", "아 죄송합니다 다시 갈게요", "말이 꼬였네", "다시 하자", "다시 찍을게요",
               "자 처음부터 다시 찍을게요", "아 죄송해요 처음부터 다시 갈게요", "아 틀렸어요 다시 할게요", "NG 다시 갈게요"]
        no = ["처음부터 끝까지 집중하세요", "이 자세는 틀렸어요", "잠깐만 보세요", "엔지니어처럼 정확하게", "이번엔 반대로 다시 할게요",
              "다시 하자 이번엔 왼발로 강하게 차볼게요", "SONG", "롱패스", "공을 다시 받아요",
              # 검수: 연습 안내('천천히 다시 할게요' …)는 촬영용 말이 아님
              "왼발로 다시 할게요", "천천히 다시 할게요", "반대로 다시 갈게요", "한 번 더 다시 갈게요", "빠르게 다시 갈게요",
              "오른쪽도 다시 할게요", "두 명이서 다시 할게요", "이제 천천히 다시 할게요"]
        for t in yes:
            self.assertTrue(takes.is_slate(t), t)
        for t in no:
            self.assertFalse(takes.is_slate(t), t)

    def test_polite_and_spaced_slates(self):
        # 검수: 존댓말·띄어 쓴 슬레이트 말도 알아봄 · 연습 안내는 그대로
        yes = ["다시 하겠습니다", "아 다시 가겠습니다", "다시 해 볼게요", "다시 가 볼게요", "다시 한 번 할게요", "다시 한번 갈게요",
               "아 다시", "다시요", "처음부터 다시 하겠습니다", "아 이거 아닌데 다시 할게요", "아 죄송합니다 다시 하겠습니다", "다시 해 보겠습니다"]
        no = ["천천히 다시 하겠습니다", "왼발로 다시 하겠습니다", "다시 봐요", "다시!", "공을 다시 받아요", "한 번 더 다시 갈게요"]
        for t in yes:
            self.assertTrue(takes.is_slate(t), t)
        for t in no:
            self.assertFalse(takes.is_slate(t), t)
        for mid in ["아 다시 하겠습니다", "다시 해 볼게요", "아 이거 아닌데 다시 할게요", "다시요"]:
            segs = [S(0, 3, "자 퍼스트 터치는요 발 안쪽으로"), S(4, 5.2, mid), S(6.5, 10, "퍼스트 터치는 발 안쪽으로 받아요")]
            self.assertEqual(takes.find_junk(segs, []), [(0, 6.5, NG)], mid)

    def test_emphasis_lead_word_not_stutter(self):
        # 검수: 일부러 한 첫마디·감탄('포인트!' → '포인트는 …')은 끊긴 말더듬이 아님
        for a, b in [("포인트!", "포인트는 시선을 먼저 보는 거예요"), ("왼발.", "왼발로 공을 멈추고 오른발로 차요"),
                     ("좋아요!", "좋아요 이제 다음 동작 보여 드릴게요")]:
            for d in (0.8, 0.4):
                self.assertEqual(takes.find_junk([S(0, d, a), S(d + 0.4, 4, b)], []), [], (a, d))
        self.assertEqual(takes.find_junk([S(0, 0.8, "포인트"), S(1.2, 4, "포인트는 시선을 먼저 보는 거예요")], []), [])  # 또박또박 한 낱말
        self.assertEqual(takes.find_junk([S(0, 0.4, "퍼스트"), S(0.8, 4, "퍼스트 터치는 발 안쪽으로 받아요")], []), [(0, 0.8, STUTTER)])

    def test_pure_sorted_and_safe(self):
        segs = [dict(s) for s in RETAKE]
        before = json.dumps(segs, ensure_ascii=False)
        shuffled = [segs[2], S(3.0, 3.0, "  "), segs[0], segs[1]]
        self.assertEqual(takes.find_junk(shuffled, None), [(1.0, 7.0, NG)])
        self.assertEqual(json.dumps(segs, ensure_ascii=False), before)  # 넘긴 목록은 그대로
        self.assertEqual(takes.find_junk([], []), [])
        self.assertEqual(takes.find_junk([S(0.0, 1.0, "…"), S(1.2, 1.4, "!")], []), [])


class MinusTest(unittest.TestCase):
    def test_subtract(self):
        cuts = [{"in": 0.85, "out": 4.45}, {"in": 6.85, "out": 10.45}]
        out = editor._minus(cuts, [(4.3, 6.85, NG)], pre=0.15)
        self.assertEqual(out, [{"in": 0.85, "out": 4.3}, {"in": 6.85, "out": 10.45}])
        self.assertIs(out[1], cuts[1])  # 안 겹친 컷은 그대로
        # 검수: 슬레이트 말은 그 말 끝까지 다 뺌 (뒤 말 앞 여유만큼 남기지 않음)
        self.assertEqual(editor._minus(cuts, [(4.3, 7.0, SLATE)], pre=0.15), [{"in": 0.85, "out": 4.3}, {"in": 7.0, "out": 10.45}])
        self.assertEqual(editor._minus(cuts, [(2.0, 3.0, NG)]), [{"in": 0.85, "out": 2.0}, {"in": 3.0, "out": 4.45}, cuts[1]])
        self.assertEqual(editor._minus(cuts, [(1.0, 10.3, NG)]), [])  # 남는 0.15초 조각들은 버림
        self.assertEqual(editor._minus(cuts, []), cuts)


class RecommendTest(unittest.TestCase):
    """editor.recommend · auto_sequences — 한글·띄어쓰기 작업 폴더에서."""
    name = "촬영 1 [풋살 꿀팁].mp4"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="NG 정리 테스트 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rec(self, segs, silences=None, **kw):
        d = core.adir(self.name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        if silences is not None:
            (d / "analysis.json").write_text(json.dumps({"silences": silences, "loud_peaks": []}), encoding="utf-8")
        else:
            (d / "analysis.json").unlink(missing_ok=True)
        return editor.recommend(self.name, **kw)

    def test_a_tidy_keeps_only_the_good_take(self):
        r = self.rec(RETAKE, [{"start": 5.7, "end": 6.9}])
        self.assertEqual(r["tidy"], [{"in": 6.85, "out": 10.45}])  # 셋째 줄만 (앞 여유 0.15초 · 뒤 0.25초)
        self.assertEqual(r["junk_list"], [{"a": 1.0, "b": 7.0, "why": NG}])
        self.assertEqual((r["junk"], r["segments"]), (2, 3))
        json.dumps(r)  # 화면으로 그대로 보낼 수 있어야 함
        seq = editor.auto_sequences(self.name, {"duration": 12.0}, kinds=("long",))[0]
        v1 = [(i["in"], i["out"]) for i in seq["items"] if i["track"] == "V1"]
        self.assertEqual(v1, [(6.85, 10.45)])

    def test_a_shorts_skip_the_bad_take(self):
        r = self.rec(RETAKE, [], min_len=3.0, max_len=20.0)
        self.assertTrue(r["shorts"])
        for s in r["shorts"]:
            self.assertEqual(s["cuts"], [{"in": 6.85, "out": 10.4}])
            self.assertEqual(s["title"], RETAKE[2]["text"][:28])  # 지운 테이크로 제목을 만들지 않음
            self.assertEqual(s["length"], round(10.4 - 6.85, 1))

    def test_b_emphasis_kept(self):
        segs = [S(1.0, 4.0, "공을 받을 때는"), S(4.5, 5.7, "빠르게, 빠르게!"), S(6.3, 9.0, "몸을 돌려서 패스하세요")]
        r = self.rec(segs, [])
        self.assertEqual(r["junk_list"], [])
        self.assertEqual(r["tidy"], [{"in": 0.85, "out": 9.25}])

    def test_c_slate_cut_from_silence_start(self):
        segs = [S(0.5, 4.2, "오늘은 퍼스트 터치를 배워볼게요"), S(6.2, 7.0, "아 잠깐만요"), S(9.0, 12.5, "공이 오면 발 안쪽으로 받아요")]
        r = self.rec(segs, [{"start": 4.3, "end": 6.1}, {"start": 7.1, "end": 8.9}], keep_pause=3.0)  # 쉬는 곳을 길게 남기는 스타일
        self.assertEqual(r["junk_list"], [{"a": 4.3, "b": 7.0, "why": SLATE}])
        self.assertEqual(r["tidy"], [{"in": 0.35, "out": 4.3}, {"in": 8.85, "out": 12.75}])  # 앞 말 뒤 여유도 조용한 곳 시작에서 끊음
        r = self.rec(segs)  # analysis.json 이 없어도 됨 → 슬레이트 구간 시작부터
        self.assertEqual(r["junk_list"], [{"a": 6.2, "b": 7.0, "why": SLATE}])
        self.assertEqual(r["tidy"], [{"in": 0.35, "out": 4.45}, {"in": 8.85, "out": 12.75}])

    def test_slate_tail_not_heard_before_next_line(self):
        # 검수: 슬레이트 말 바로 뒤에 다음 말이 붙어 있어도 슬레이트 말 끝('…요')은 남지 않음
        segs = [S(0, 4, "오늘은 퍼스트 터치를 배워볼게요"), S(6, 7, "아 잠깐만요"), S(7, 11, "공을 받을 때는 몸을 열어 주세요")]
        r = self.rec(segs, [{"start": 4.1, "end": 6.0}])
        self.assertEqual(r["junk_list"], [{"a": 4.1, "b": 7, "why": SLATE}])
        self.assertEqual(r["tidy"], [{"in": 0, "out": 4.1}, {"in": 7.0, "out": 11.25}])

    def test_polite_slate_tidy(self):
        # 검수: 존댓말 슬레이트('다시 하겠습니다')도 (a)처럼 앞 테이크·슬레이트를 뺌
        segs = [RETAKE[0], S(4.6, 5.6, "아 다시 하겠습니다"), RETAKE[2]]
        r = self.rec(segs, [{"start": 5.7, "end": 6.9}])
        self.assertEqual(r["tidy"], [{"in": 6.85, "out": 10.45}])

    def test_contrast_explanation_kept_in_tidy(self):
        # 리뷰 지적 재현: '안 돼요' 설명이 가편집에서 사라지면 안 됨 (바꾸기 전과 같은 가편집)
        segs = [S(1, 4, "오늘은 퍼스트 터치 꿀팁 알려드릴게요"), S(5, 8, "자 이렇게 받으면 안 돼요"),
                S(16, 19, "이렇게 받으면 돼요"), S(20, 24, "공이 발 앞에 딱 멈추죠")]
        r = self.rec(segs, [])
        self.assertEqual(r["junk_list"], [])
        self.assertEqual(r["tidy"], [{"in": 0.85, "out": 8.25}, {"in": 15.85, "out": 24.25}])

    def test_drill_lesson_tidy_keeps_counts_cheers_and_first_demo(self):
        # E6 재현 (MSGRAW01 113.64~114.96 '하나, 둘, 셋.' 이 말더듬으로 잘림 · 드릴 레슨 첫 설명·첫 시범이 'NG 앞 테이크'로 사라짐)
        line = "패스하고 바로 앞으로 뛰어나가세요."
        segs = [S(1.0, 4.0, "오늘은 패스 앤 무브 드릴을 해 볼게요"), S(10, 12.5, line),
                S(13.64, 14.42, "하나, 둘, 셋."), S(14.96, 18.2, "하나, 둘, 셋."), S(23.88, 24.52, "나이스!"), S(24.8, 25.4, "나이스!"),
                S(26.0, 26.4, "골!"), S(30, 32.5, line), S(33.0, 34.5, "왼발, 오른발,"), S(35.0, 36.5, "왼발, 오른발,"),
                S(37.0, 38.5, "왼발, 오른발,"), S(40.0, 43.0, "좋아요 오늘은 여기까지 할게요")]
        quiet = [{"start": 4.1, "end": 9.8}, {"start": 18.3, "end": 23.7}, {"start": 26.5, "end": 29.8}]
        r = self.rec(segs, quiet)
        self.assertEqual(r["junk_list"], [])
        kept = lambda a, b: sum(max(0.0, min(b, c["out"]) - max(a, c["in"])) for c in r["tidy"]) / (b - a)  # noqa: E731
        for s in segs:
            self.assertGreater(kept(s["start"], s["end"]), 0.99, s["text"])
        # 시범 소리 봉우리는 analysis.json loud_peaks 그대로 씀 (사이가 조용해도 공 차는 소리가 있으면 같은 설명이 둘 다 남음)
        demo = [S(10, 12.5, line), S(20, 22.5, line)]
        d = core.adir(self.name)
        (d / "transcript.json").write_text(json.dumps(demo, ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps({"silences": [{"start": 12.6, "end": 19.9}], "loud_peaks": [{"time": 16.0, "rms_db": -6.0}]}),
                                         encoding="utf-8")
        self.assertEqual(editor.recommend(self.name)["junk_list"], [])
        self.assertEqual(self.rec(demo, [{"start": 12.6, "end": 19.9}])["junk_list"], [{"a": 10, "b": 20, "why": NG}])  # 소리 없이 쉬었으면 NG

    def test_shorts_cuts_never_touch_ng_parts(self):
        segs = [S(1.0, 4.5, "오늘은 퍼스트 터치 꿀팁을 알려드릴게요"), S(5.0, 8.0, "공이 오면 발 안쪽으로 부드럽게"),
                S(8.6, 9.4, "아 틀렸다"), S(11.0, 14.5, "공이 오면 발 안쪽으로 부드럽게 받아요"), S(15.0, 18.0, "이게 진짜 중요해요"),
                S(18.4, 18.8, "무릎"), S(19.2, 22.5, "무릎을 살짝 굽히고 힘을 빼세요"), S(23.0, 26.0, "그래야 공이 안 튀어요"),
                S(27.0, 30.0, "여러분도 꼭 연습해 보세요")]
        r = self.rec(segs, [{"start": 9.5, "end": 10.9}], min_len=10.0, max_len=30.0, n=3)
        self.assertEqual(r["junk_list"], [{"a": 5.0, "b": 11.0, "why": NG}, {"a": 18.4, "b": 19.2, "why": STUTTER}])
        self.assertTrue(r["shorts"])
        for cuts in [r["tidy"]] + [s["cuts"] for s in r["shorts"]]:
            for c in cuts:
                self.assertLess(c["in"], c["out"])
                for j in r["junk_list"]:
                    self.assertFalse(c["in"] < j["b"] - 0.15 and c["out"] > j["a"], (c, j))
        for s in r["shorts"]:
            self.assertEqual(s["length"], round(sum(c["out"] - c["in"] for c in s["cuts"]), 1))

    # ---------- 검수에서 나온 경우 (가편집에 그대로 드러나는 것) ----------

    def test_nested_retake_intro_not_said_twice(self):
        segs = [S(1.0, 4.0, "오늘은 퍼스트 터치를 배워볼게요"), S(4.5, 8.0, "공이 오면 발 안쪽으로"),
                S(9.0, 12.0, "공이 오면 발 안쪽으로 어 그러니까"), S(12.5, 13.5, "아 다시 할게요"),
                S(15.0, 18.0, "오늘은 퍼스트 터치를 배워볼게요"), S(18.5, 22.0, "공이 오면 발 안쪽으로 받아요")]
        r = self.rec(segs, [])
        self.assertEqual(r["junk_list"], [{"a": 1.0, "b": 15.0, "why": NG}])
        self.assertEqual(r["tidy"], [{"in": 14.85, "out": 22.25}])  # 다시 찍은 테이크 두 줄만

    def test_tip_and_drill_lines_kept_in_tidy(self):
        bookend = [S(0.5, 3.0, "퍼스트 터치 꿀팁 알려드릴게요"), S(3.5, 5.5, "이게 진짜 중요해요"), S(6.0, 9.0, "공을 보지 말고 앞을 보세요"),
                   S(9.5, 11.5, "이게 진짜 중요해요"), S(12.0, 15.0, "그래야 패스가 빨라져요")]
        drill = [S(0.5, 4.0, "공이 오면 발 안쪽으로 받아요"), S(4.5, 6.0, "천천히 다시 할게요"), S(6.5, 10.0, "발목에 힘을 빼고 부드럽게")]
        for segs, tidy in ((bookend, [{"in": 0.35, "out": 15.25}]), (drill, [{"in": 0.35, "out": 10.25}])):
            with self.subTest(segs[1]["text"]):
                r = self.rec(segs, [])
                self.assertEqual((r["junk_list"], r["tidy"]), ([], tidy))  # 줄 하나도 안 빠짐

    def test_shorts_not_shorter_than_min_len_after_ng(self):
        take = [("공이 오면 발 안쪽으로 받아요", 4.0), ("무릎을 살짝 굽히고 힘을 빼세요", 8.0), ("그래야 공이 안 튀어요", 12.0)]
        segs = ([S(0.5, 3.5, "오늘의 꿀팁 퍼스트 터치예요")] + [S(t, t + 3.5, x) for x, t in take] + [S(17.0, 18.0, "아 다시 할게요")]
                + [S(t + 16, t + 19.5, x) for x, t in take])
        r = self.rec(segs, [])
        self.assertEqual(r["junk_list"], [{"a": 4.0, "b": 20.0, "why": NG}])
        self.assertEqual(r["shorts"], [])  # NG 를 빼면 15초 남짓 → 20초 쇼츠 후보가 아님
        more = segs + [S(32.0 + 4 * k, 35.5 + 4 * k, x) for k, x in enumerate(
            ["발목은 단단하게 고정하세요", "시선은 항상 앞을 보세요", "다음 동작을 미리 생각하세요", "여러분도 꼭 연습해 보세요"])]
        r = self.rec(more, [])
        self.assertTrue(r["shorts"])
        for s in r["shorts"]:
            self.assertGreaterEqual(s["end"] - s["start"] - editor._covered([(4.0, 20.0)], s["start"], s["end"]), 20.0, s)

    def test_covered(self):
        ivs = [(4.0, 20.0, NG), (12.5, 13.5, SLATE), (18.0, 22.0, STUTTER), (30.0, 31.0, SLATE)]
        self.assertAlmostEqual(editor._covered(ivs, 0.0, 40.0), 19.0)  # 겹친 곳은 한 번만
        self.assertAlmostEqual(editor._covered(ivs, 10.0, 30.5), 12.5)
        self.assertEqual(editor._covered([], 0.0, 10.0), 0.0)
        self.assertEqual(editor._covered(ivs, 23.0, 29.0), 0.0)

    def test_d_regression_same_as_before(self):
        # (d) 다시 찍기·슬레이트가 없는 받아쓰기는 바꾸기 전과 똑같고 junk_list·offscript 만 더해짐 (코치 설명 끝의 구독 부탁은 쇼츠에서만 빠짐 · E12)
        fx = json.loads((FIX / "takes_before.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fx["cases"]), 8)
        for c in fx["cases"]:
            with self.subTest(c["input"], **c["kwargs"]):
                src = fx["inputs"][c["input"]]
                d = core.adir(self.name)
                d.mkdir(parents=True, exist_ok=True)
                (d / "transcript.json").write_text(json.dumps(src["transcript"], ensure_ascii=False), encoding="utf-8")
                (d / "analysis.json").write_text(json.dumps(src["analysis"], ensure_ascii=False), encoding="utf-8")
                now = editor.recommend(self.name, **c["kwargs"])
                junk_list = now.pop("junk_list")
                off = now.pop("offscript")  # E12: 영상 밖 말 목록이 더해짐
                self.assertEqual(now.pop("demos"), [])  # E2: 말 없는 틈의 시범 구간 (낱말 시각이 없는 받아쓰기는 없음)
                for sh in now["shorts"]:  # E2: 쇼츠 위 큰 제목 (editor.short_hooks) · 점수는 한 글자 추임새 줄 감점이 빠져 조금 다름(순서·구간은 그대로)
                    self.assertTrue(sh.pop("hook"))
                    sh.pop("score")
                c = dict(c, before=dict(c["before"], shorts=[{k: v for k, v in sh.items() if k != "score"} for sh in c["before"]["shorts"]]))
                if c["input"].startswith("코치"):
                    self.assertEqual(junk_list, [])
                    # E12 (BR-060): 끝의 '구독과 좋아요 부탁드립니다'는 영상 밖 안내 말 — 롱폼(정리 컷)은 그대로, 쇼츠에서만 빠짐
                    self.assertEqual([(o["a"], o["b"], o["kind"], o["cut"]) for o in off], [(71.7, 74.8, "cta", "shorts")])
                    before = c["before"]
                    self.assertEqual((now["tidy"], now["junk"], now["segments"]), (before["tidy"], before["junk"], before["segments"]))
                    gone = [s for s in before["shorts"] if any(x["in"] < 74.5 and x["out"] > 72.0 for x in s["cuts"])]
                    kept = [s for s in before["shorts"] if s not in gone]
                    self.assertEqual([s for s in now["shorts"] if s in kept], kept)  # 안내 말이 없던 쇼츠는 그대로
                    for s in now["shorts"]:
                        self.assertFalse(any(x["in"] < 74.5 and x["out"] > 72.0 for x in s["cuts"]), s)
                else:  # 편집실 테스트 영상: '패스주고 리턴받고' 두 번 — 원래도 빠지던 앞쪽만 목록에 나옴 (컷은 그대로)
                    # E2 (BR-092): 끝인사 '감사합니다'(50.5~)는 쇼츠에서 뺌 → 그걸로 끝나던 쇼츠는 짧아져 빠지거나 그 앞에서 끝남 · 나머지는 예전 그대로
                    bye = [sh for sh in c["before"]["shorts"] if sh["end"] > 50.0]
                    self.assertEqual(json.dumps(dict(now, shorts=[sh for sh in now["shorts"] if sh in c["before"]["shorts"]]), sort_keys=True, ensure_ascii=False),
                                     json.dumps(dict(c["before"], shorts=[sh for sh in c["before"]["shorts"] if sh not in bye]), sort_keys=True, ensure_ascii=False))
                    self.assertFalse([sh for sh in now["shorts"] if any(x["out"] > 50.6 for x in sh["cuts"])])
                    self.assertEqual(junk_list, [{"a": 28.5, "b": 34.0, "why": NG}])
                    self.assertEqual(off, [])

    def test_analyze_then_recommend_uses_real_silences(self):
        """받아쓰기(흉내) → 진짜 ffmpeg 무음 찾기 → analysis.json → 슬레이트 말이 조용한 곳 시작부터 빠짐."""
        video = core.VIDEOS / self.name
        speak = "lt(t,4)+between(t,6.2,7)+gte(t,9)"  # 말하는 곳만 소리 (4~6.2초, 7~9초 조용함)
        r = core.run([core.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=10:d=12.5",
                      "-f", "lavfi", "-i", f"aevalsrc='0.5*sin(2*PI*440*t)*({speak})':s=16000:d=12.5",
                      "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)])
        self.assertEqual(r.returncode, 0, r.stderr)
        heard = [S(0.3, 3.8, "오늘은 퍼스트 터치를 배워볼게요"), S(6.2, 7.0, "아 잠깐만요"), S(9.0, 12.4, "공이 오면 발 안쪽으로 받아요")]

        class Model:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, *a, **k):
                return iter(types.SimpleNamespace(**s) for s in heard), types.SimpleNamespace(duration=12.5)

        with mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=Model)}):
            core.analyze(self.name, lambda m: None)
        core.set_progress()
        sil = json.loads((core.adir(self.name) / "analysis.json").read_text(encoding="utf-8"))["silences"]
        self.assertTrue(any(abs(x["start"] - 4.0) < 0.1 and abs(x["end"] - 6.2) < 0.1 for x in sil), sil)
        r = editor.recommend(self.name)
        first = next(x for x in sil if abs(x["start"] - 4.0) < 0.1)
        self.assertEqual(r["junk_list"], [{"a": first["start"], "b": 7.0, "why": SLATE}])
        self.assertEqual(r["tidy"], [{"in": 0.15, "out": first["start"]}, {"in": 8.85, "out": 12.65}])


if __name__ == "__main__":
    unittest.main()
