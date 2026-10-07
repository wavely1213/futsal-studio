"""QR 만들기(qr.py) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_qr

segno(개발 전용, requirements.txt 에 없음)가 있으면 버전 1~10 · 마스크 8가지 칸을 하나하나 비교한다.
segno 는 끝맺음(0000) 뒤 비트가 이미 바이트 경계여도 0 바이트 하나를 더 넣는다(ISO 18004 7.4.10 은 '경계가 아닐 때만').
둘 다 읽히는 QR 이지만 칸이 달라지므로, 비교할 때만 segno 의 그 부분을 규격대로 바꿔 끼운다.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qr  # noqa: E402

try:
    import segno
    import segno.encoder as _segno_enc
except ImportError:  # 개발 전용 도구가 없으면 비교만 건너뜀
    segno = None

LONGEST_PAIR = "https://mulgyeol.kr/futsal#pair=7K3QM9XD2P&u=" + "a" * 63  # 빠른 터널 이름이 가장 길 때


def _spec_padding(buff, version, length):
    if length % 8:
        buff.extend([0] * (8 - length % 8))


class KnownVectorTests(unittest.TestCase):
    def test_reed_solomon_published_example(self):
        """널리 알려진 'HELLO WORLD' 1-M 예시의 정정 코드워드 10개."""
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        self.assertEqual(qr.rs_remainder(data, 10), [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])

    def test_format_bits_table_for_m(self):
        want = ["101010000010010", "101000100100101", "101111001111100", "101101101001011",
                "100010111111001", "100000011001110", "100111110010111", "100101010100000"]
        self.assertEqual([format(qr.format_bits(k), "015b") for k in range(8)], want)

    def test_version_info_bits(self):
        want = {7: "000111110010010100", 8: "001000010110111100", 9: "001001101010011001", 10: "001010010011010011"}
        self.assertEqual({v: format(qr.version_bits(v), "018b") for v in want}, want)


class EncodeTests(unittest.TestCase):
    def test_longest_pairing_url_fits_and_is_square(self):
        rows = qr.encode(LONGEST_PAIR)
        self.assertLessEqual(len(rows), 17 + 4 * 10)
        self.assertTrue(all(len(r) == len(rows) and set(r) <= {"0", "1"} for r in rows))

    def test_finder_patterns_in_three_corners(self):
        rows = qr.encode("https://mulgyeol.kr/futsal")
        n = len(rows)
        finder = ["1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111"]
        for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
            self.assertEqual([rows[oy + i][ox:ox + 7] for i in range(7)], finder)

    def test_too_long_raises(self):
        with self.assertRaises(qr.TooLong):
            qr.encode("x" * 300)

    def test_chosen_mask_has_lowest_penalty(self):
        m, v, k = qr.matrix(LONGEST_PAIR)
        scores = [qr._penalty(qr.matrix(LONGEST_PAIR, mask=j, version=v)[0]) for j in range(8)]
        self.assertEqual(scores[k], min(scores))

    def test_svg_draws_dark_modules_only(self):
        s = qr.svg(["10", "01"], scale=1, quiet=0)
        self.assertIn('M0,0h1v1h-1z', s)
        self.assertIn('M1,1h1v1h-1z', s)
        self.assertNotIn('M1,0', s)


@unittest.skipUnless(segno, "segno 가 없어 칸 비교는 건너뜀 (개발 전용: pip install segno)")
class SegnoCompareTests(unittest.TestCase):
    TEXTS = ["A", "https://mulgyeol.kr/futsal", "z" * 30, "w" * 50, "https://mulgyeol.kr/futsal#pair=7K3QM9XD2P&u=abandoned-unexpected-cooperative-boring",
             LONGEST_PAIR, "한글 주소 시험 " * 5, "x" * 108, "x" * 150, "q" * 170, "y" * 210]

    def test_every_version_and_mask_matches_segno(self):
        seen = set()
        with mock.patch.object(_segno_enc, "write_padding_bits", _spec_padding):
            for t in self.TEXTS:
                data = t.encode("utf-8")
                for mask in range(8):
                    m, v, _ = qr.matrix(t, mask=mask)
                    s = segno.make(data, error="m", version=v, mode="byte", mask=mask, boost_error=False, micro=False)
                    self.assertEqual([[bool(c) for c in row] for row in s.matrix], m, f"{len(data)}바이트 · 버전 {v} · 마스크 {mask}")
                    seen.add(v)
        self.assertEqual(seen, set(range(1, 11)))  # 버전 1~10 모두 (정렬 패턴 여러 개·버전 정보 7 이상까지)

    def test_version_matches_segno_minimum(self):
        for t in self.TEXTS:
            s = segno.make(t.encode("utf-8"), error="m", mode="byte", boost_error=False, micro=False)
            self.assertEqual(qr.matrix(t)[1], s.version)


if __name__ == "__main__":
    unittest.main()
