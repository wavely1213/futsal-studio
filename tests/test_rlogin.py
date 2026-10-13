"""아이디·비밀번호로 연결의 순수 계산 (rlogin.py · D-250~D-258) · python3 -m unittest tests.test_rlogin

글자 맞추기(canon v1) · 아이디·비밀번호 규칙 · 추천 · PC 찾기 열쇠 · SRP-6a (RFC 5054 부록 B 시험값 · 3072비트 묶음이 RFC 3526
15번(π 로 다시 만듦)인지 · pysrp 교차 확인은 개발 때만: FUTSAL_PYSRP_PATH 에 pysrp 가 있으면) · 막기(Guard) · 주고받는 중(Pending) ·
알림 낱말 · 블록 확인 · 미리 정해 둔 로그인(가짜 비밀번호로만).
"""
import ast
import importlib
import io
import os
import random
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rlogin  # noqa: E402

FAKE_PW = "가짜비밀번호시험용7391"   # 시험에서만 쓰는 가짜 (진짜 미리 정한 비밀번호는 저장소에 없음)


def H(s):
    return int("".join(s.split()), 16)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, s):
        self.t += s


def gcm_pair():
    import remote
    return remote.gcm_seal, remote.gcm_open


class CanonTests(unittest.TestCase):
    def test_hangul_equals_english_keys(self):
        self.assertEqual(rlogin.canon_id("김코치77"), "rlazhcl77")
        self.assertEqual(rlogin.canon_id("RLAZHCL77"), "rlazhcl77")
        self.assertEqual(rlogin.canon_pw("김코치77"), rlogin.canon_pw("rlazhcl77"))
        self.assertEqual(rlogin.canon_pw("노을축구공바다4827"), rlogin.canon_pw("SHDMFCNRRNRHDQKEK4827"))

    def test_case_and_tense_fold(self):
        self.assertEqual(rlogin.keys_of("까치"), rlogin.keys_of("가치"))
        self.assertEqual(rlogin.keys_of("ㅒ"), rlogin.keys_of("ㅐ"))
        self.assertEqual(rlogin.keys_of("ㅆ"), rlogin.keys_of("ㅅ"))
        self.assertEqual(rlogin.keys_of("AbC"), "abc")

    def test_compat_conjoining_syllable_equal(self):
        want = rlogin.keys_of("닭")
        self.assertEqual(want, "ekfr")
        self.assertEqual(rlogin.keys_of("달ㄱ"), want)
        self.assertEqual(rlogin.keys_of("닭"), want)   # 첫가끝 자모
        self.assertEqual(rlogin.keys_of("ㄷㅏㄹㄱ"), want)               # 호환 자모 따로

    def test_fullwidth_and_ideographic_space(self):
        self.assertEqual(rlogin.canon_pw("Ｋｉｍ　Ｃｏａｃｈ"), "kim coach")
        self.assertEqual(rlogin.canon_id("Ｋｉｍ　Ｃｏａｃｈ"), "kimcoach")

    def test_won_sign(self):
        self.assertEqual(rlogin.keys_of("₩"), "\\")
        self.assertEqual(rlogin.keys_of("￦"), "\\")   # 전각 원 기호도 NFKC 로

    def test_id_strips_space_dot_dash_underscore(self):
        self.assertEqual(rlogin.canon_id(" kim.coach-7_7 "), "kimcoach77")

    def test_archaic_jamo_and_emoji_rejected(self):
        for s in ("ㆍㆍㆍ아이디만", "축구⚽코치12", "風林火山코치"):
            self.assertEqual(rlogin.id_problem(s), "chars", s)
        self.assertIn("chars", rlogin.pw_problems("축구⚽코치1234abcd"))
        self.assertIn("chars", rlogin.pw_problems("ᅀᅀabcdef1234"))   # 옛 자모 (반치음)

    def test_pw_trims_only_outer_ascii_ws(self):
        self.assertEqual(rlogin.canon_pw("  ab cd 12  \t\n"), "ab cd 12")


class IdRuleTests(unittest.TestCase):
    def test_min_len_6_canonical(self):
        self.assertIsNone(rlogin.id_problem("가나다"))       # rkskek (6)
        self.assertEqual(rlogin.id_problem("철수"), "short")  # cjftn (5)
        self.assertEqual(rlogin.id_problem("abc12"), "short")
        self.assertEqual(rlogin.id_problem("a" * 33), "long")

    def test_all_digits_rejected(self):
        self.assertEqual(rlogin.id_problem("01012345678"), "digits")

    def test_reserved_including_korean_forms(self):
        for s in ("futsal", "FUTSAL", "풋살코치", "vnttkfzhcl", "풋살사관학교", "관리자", "테스트", "아이디", "Wavely"):
            self.assertEqual(rlogin.id_problem(s), "reserved", s)
        self.assertIsNone(rlogin.id_problem("wavely1213"))
        self.assertIsNone(rlogin.id_problem("김코치77"))
        for code, msg in rlogin.ID_MSG.items():
            self.assertTrue(msg.endswith("요"), code)


class PwRuleTests(unittest.TestCase):
    def test_each_rule_exact_message(self):
        table = [
            ("abc12", None, None, "short", "비밀번호를 조금 더 길게 해 주세요 (영문·숫자 섞어 8자 이상 · 한글은 5글자 이상)"),
            ("abcdefghjk", None, None, "letters", "비밀번호를 조금 더 길게 해 주세요 (영문·숫자 섞어 8자 이상 · 한글은 5글자 이상)"),
            ("a" * 65, None, None, "long", "비밀번호가 너무 길어요 · 64글자 안으로 정해 주세요"),
            ("38291047", None, None, "digits", "숫자만으로는 안 돼요"),
            ("rlazhcl77xyz9", "rlazhcl77", None, "id", "비밀번호에 아이디가 들어 있어요"),
            ("qwer1234", None, None, "common", "너무 흔한 비밀번호예요"),
            ("zzzz8391k", None, None, "run", "같은 글자 반복이나 1234·qwer 같은 줄은 쉽게 맞혀져요"),
            ("x01012345678", None, None, "personal", "전화번호·생일은 남이 알기 쉬워요"),
            ("가방⚽소풍8391", None, None, "chars", "쓸 수 없는 글자가 있어요 (한글·영문·숫자·기호만)"),
            ("노을축구공바다4827", None, "노을축구공바다4828", "mismatch", "두 비밀번호가 달라요"),
        ]
        for pw, cid, pw2, code, msg in table:
            got = rlogin.pw_problems(pw, cid, pw2)
            self.assertIn(code, got, pw)
            self.assertEqual(rlogin.PW_MSG[code], msg)
        self.assertEqual(rlogin.pw_problems("노을축구공바다4827", "rlazhcl77", "SHDMFCNRRNRHDQKEK4827"), [])

    def test_letters_only_needs_12(self):
        self.assertIn("letters", rlogin.pw_problems("goalkeeperx"))   # 11
        self.assertEqual(rlogin.pw_problems("goalkeeperxy"), [])       # 12
        self.assertEqual(rlogin.pw_problems("노을축구공바다"), [])       # 한글만 (shdmfcnrrnrhdqkek 17)

    def test_five_hangul_syllables_need_no_digits(self):
        """화면 문장 '한글 5글자 이상이면 숫자 없어도 돼요' 그대로 (받침 없는 다섯 글자는 10~11글쇠 · 검토 고침)."""
        for pw in ("소나기바다", "나무가지바", "하루바다꽃", "바다나라소", "나는코치다", "코치최고야", "모래사자고", "가자우리팀", "도레미파솔"):
            self.assertLess(len(rlogin.canon_pw(pw)), 12, pw)
            self.assertEqual(rlogin.pw_problems(pw, "rlazhcl77"), [], pw)
            self.assertEqual(rlogin.hangul_syllables(pw), 5)
        self.assertEqual(rlogin.pw_problems("소나기비"), ["letters"])        # 네 글자는 여전히 짧음 (문장도 '한글은 5글자 이상')
        self.assertEqual(rlogin.pw_problems("thskrlqkek"), ["letters"])     # 같은 글쇠를 영문으로 정할 때는 12글쇠 규칙 (로그인은 같은 꼴)
        self.assertIn("한글은 5글자 이상", rlogin.PW_MSG["letters"])

    def test_common_with_trailing_digits(self):
        for pw in ("qwer1234!", "password12", "Password!!", "사랑해요1234", "1q2w3e4r!"):
            self.assertIn("common", rlogin.pw_problems(pw), pw)
        self.assertNotIn("common", rlogin.pw_problems("qwer1234zz9"))

    def test_contains_id(self):
        self.assertIn("id", rlogin.pw_problems("김코치77최고야", "rlazhcl77"))
        self.assertIn("id", rlogin.pw_problems("RLA.ZHCL77abc", "rlazhcl77"))
        self.assertNotIn("id", rlogin.pw_problems("노을축구공바다4827", "rlazhcl77"))

    def test_keyboard_run_and_repeat(self):
        for pw in ("aaaa83k2x", "1234567x", "qwertyu1", "abcdefg9", "9876543z", "lkjhgfd2"):
            self.assertIn("run", rlogin.pw_problems(pw), pw)
        self.assertNotIn("run", rlogin.pw_problems("abcde9x3k7m2"))   # 줄을 빼도 7글자 남음
        self.assertNotIn("run", rlogin.pw_problems("aaa8b3k2x"))

    def test_phone_and_date(self):
        for pw in ("010-1234-5678", "x01012345678z", "19900101ab", "abc20240315"):
            self.assertIn("personal", rlogin.pw_problems(pw), pw)
        self.assertNotIn("personal", rlogin.pw_problems("노을축구공바다4827"))


class SuggestTests(unittest.TestCase):
    def test_100_suggestions_pass_rules(self):
        self.assertEqual(len(rlogin.NOUNS), 256)
        self.assertEqual(len(set(rlogin.NOUNS)), 256)
        self.assertTrue(all(2 <= len(w) <= 3 for w in rlogin.NOUNS))
        for _ in range(100):
            pw = rlogin.suggest("rlazhcl77")
            self.assertEqual(rlogin.pw_problems(pw, "rlazhcl77", pw), [], pw)
            self.assertRegex(pw, r"^[가-힣]{6,9}\d{4}$")

    def test_suggestion_uses_secrets(self):
        with mock.patch.object(rlogin.secrets, "SystemRandom", wraps=rlogin.secrets.SystemRandom) as sr:
            rlogin.suggest()
        self.assertTrue(sr.called)
        seen = {rlogin.suggest() for _ in range(50)}
        self.assertGreater(len(seen), 45)


class DeriveTests(unittest.TestCase):
    def test_derive_login_fixed_vector(self):
        """node(proto.deriveLogin) 와 같은 값 (tests.test_remote_proto 도 같은 수를 씀)."""
        d = rlogin.derive_login("rlazhcl77")
        self.assertEqual(d["topic"], "fsl1d315a8a97aaa3d816a1584b")
        self.assertEqual(d["bkey"].hex(), "b90a54d8ab036421d70d92c2662c87d35fb2c8fbd154b15d51d4fd520d451629")
        self.assertEqual(d["idc"], "nBjO1vuzIzg432UZX9coMQ")

    def test_topic_matches_redact_regex(self):
        import remote
        d = rlogin.derive_login("rlazhcl77")
        out = remote.redact(f"주제 {d['topic']} · 끊기 fsc{'a' * 24} · fsl1.abc.def · fsk1.{rlogin.kill_token()}")
        self.assertNotIn(d["topic"], out)
        self.assertNotIn("fsc" + "a" * 24, out)
        self.assertNotIn("fsl1.abc", out)
        self.assertNotIn("fsk1.", out.replace("fsk1.…", ""))

    def test_lookup_seal_open_and_aad_binds_topic(self):
        seal, opn = gcm_pair()
        d = rlogin.derive_login("rlazhcl77")
        inner = {"v": 1, "pc": "0123456789abcdef", "seq": 3, "ts": 1_800_000_000, "state": "on", "reason": None, "url": "https://a-b.trycloudflare.com"}
        msg = rlogin.seal_lookup(d["bkey"], d["topic"], inner, seal)
        self.assertTrue(msg.startswith("fsl1."))
        self.assertEqual(rlogin.open_lookup(d["bkey"], d["topic"], msg, opn), inner)
        self.assertIsNone(rlogin.open_lookup(d["bkey"], "fsl" + "0" * 24, msg, opn))   # 다른 주제로 옮긴 글
        other = rlogin.derive_login("kimcoach99")
        self.assertIsNone(rlogin.open_lookup(other["bkey"], d["topic"], msg, opn))
        bad = rlogin.seal_lookup(d["bkey"], d["topic"], dict(inner, pc="XYZ"), seal)
        self.assertIsNone(rlogin.open_lookup(d["bkey"], d["topic"], bad, opn))
        self.assertNotIn("내 PC", msg)


class SrpVectorTests(unittest.TestCase):
    """RFC 5054 부록 B (1024비트 · SHA-1 · I=alice · P=password123)."""
    s = bytes.fromhex("BEB25379D1A8581EB5A727673A2441EE")
    a = H("60975527 035CF2AD 1989806F 0407210B C81EDC04 E2762A56 AFD529DD DA2D4393")
    b = H("E487CB59 D31AC550 471E81F0 0F6928E0 1DDA08E9 74A004F4 9E61F5D1 05284D20")

    def test_rfc5054_appendix_b_sha1_1024(self):
        G = "5054-1024-sha1"
        self.assertEqual(rlogin.srp_k(G), H("7556AA04 5AEF2CDD 07ABAF0F 665C3E81 8913186F"))
        x = rlogin.srp_x_rfc("alice", "password123", self.s)
        self.assertEqual(x, H("94B7555A ABE9127C C58CCF49 93DB6CF8 4D16C124"))
        v = rlogin.srp_verifier(x, G)
        self.assertEqual(v, H("""7E273DE8 696FFC4F 4E337D05 B4B375BE B0DDE156 9E8FA00A 9886D812 9BADA1F1 822223CA 1A605B53 0E379BA4 729FDC59
            F105B478 7E5186F5 C671085A 1447B52A 48CF1970 B4FB6F84 00BBF4CE BFBB1681 52E08AB5 EA53D15C 1AFF87B2 B9DA6E04 E058AD51 CC72BFC9
            033B564E 26480D78 E955A5E2 9E7AB245 DB2BE315 E2099AFB"""))
        _, A = rlogin.client_start(G, self.a)
        self.assertEqual(A, H("""61D5E490 F6F1B795 47B0704C 436F523D D0E560F0 C64115BB 72557EC4 4352E890 3211C046 92272D8B 2D1A5358 A2CF1B6E
            0BFCF99F 921530EC 8E393561 79EAE45E 42BA92AE ACED8251 71E1E8B9 AF6D9C03 E1327F44 BE087EF0 6530E69F 66615261 EEF54073 CA11CF58
            58F0EDFD FE15EFEA B349EF5D 76988A36 72FAC47B 0769447B"""))
        _, B, u = rlogin.server_start(v, A, G, b=self.b)
        self.assertEqual(B, H("""BD0C6151 2C692C0C B6D041FA 01BB152D 4916A1E7 7AF46AE1 05393011 BAF38964 DC46A067 0DD125B9 5A981652 236F99D9
            B681CBF8 7837EC99 6C6DA044 53728610 D0C6DDB5 8B318885 D7D82C7F 8DEB75CE 7BD4FBAA 37089E6F 9C6059F3 88838E7A 00030B33 1EB76840
            910440B1 B27AAEAE EB4012B7 D7665238 A8E3FB00 4B117B58"""))
        self.assertEqual(u, H("CE38B959 3487DA98 554ED47D 70A7AE5F 462EF019"))
        S = H("""B0DC82BA BCF30674 AE450C02 87745E79 90A3381F 63B387AA F271A10D 233861E3 59B48220 F7C4693C 9AE12B0A 6F67809F 0876E2D0
            13800D6C 41BB59B6 D5979B5C 00A172B4 A2A5903A 0BDCAF8A 709585EB 2AFAFA8F 3499B200 210DCC1F 10EB3394 3CD67FC8 8A2F39A4 BE5BEC4E
            C0A3212D C346D7E4 74B29EDE 8A469FFE CA686E5A""")
        self.assertEqual(rlogin.server_S(A, self.b, v, u, G), S)
        self.assertEqual(rlogin.client_S(B, self.a, x, u, G), S)

    def test_group_3072_is_rfc3526_group15_g5(self):
        """N = 2^3072 - 2^3008 - 1 + 2^64 * (floor(2^2942 π) + 1690314) (RFC 3526 15번 · RFC 5054 부록 A 3072) · g = 5 · SHA-256."""
        one = 1 << (2942 + 40)

        def atan_inv(n):
            total = term = one // n
            k, sign, n2 = 1, -1, n * n
            while term:
                term //= n2
                k += 2
                total += sign * (term // k)
                sign = -sign
            return total
        pi_bits = (16 * atan_inv(5) - 4 * atan_inv(239)) >> 40
        N = 2 ** 3072 - 2 ** 3008 - 1 + 2 ** 64 * (pi_bits + 1690314)
        Ng, g, hf = rlogin.group("5054-3072")
        self.assertEqual(Ng, N)
        self.assertEqual((g, hf), (5, rlogin.hashlib.sha256))
        self.assertEqual(rlogin.nlen(N), 384)
        self.assertEqual(pow(3, N - 1, N), 1)   # 소수 확인 한 번 (페르마)

    def test_matches_pysrp_if_installed(self):
        """개발 때만: pysrp(1.0.x · requirements.txt 에 넣지 않음)와 k·x·v·A·B·u·S 가 같은지 (FUTSAL_PYSRP_PATH 또는 설치된 srp)."""
        extra = os.environ.get("FUTSAL_PYSRP_PATH")
        if extra and extra not in sys.path:
            sys.path.insert(0, extra)
        try:
            srp = importlib.import_module("srp._pysrp")
        except ImportError:
            self.skipTest("pysrp 없음 (개발 도구 · FUTSAL_PYSRP_PATH)")
        srp.rfc5054_enable(True)
        try:
            N, g, _ = rlogin.group("5054-3072")
            n_hex, g_hex = format(N, "X"), "5"
            salt = bytes(range(1, 17))   # pysrp 는 salt 를 수로 바꿔 다뤄 앞의 0 바이트를 잃음
            a, b = (1 << 255) | 12345, (1 << 255) | 67890
            x = rlogin.srp_x_rfc("kim", "노을축구공바다4827", salt, "5054-3072")
            v = rlogin.srp_verifier(x, "5054-3072")
            _, A = rlogin.client_start("5054-3072", a)
            ver = srp.Verifier("kim", salt, srp.long_to_bytes(v), srp.long_to_bytes(A), srp.SHA256, srp.NG_CUSTOM, n_hex, g_hex,
                               bytes_b=b.to_bytes(32, "big"))
            self.assertEqual(ver.k, rlogin.srp_k("5054-3072"))
            _, B, u = rlogin.server_start(v, A, "5054-3072", b=b)
            self.assertEqual(ver.B, B)
            usr = srp.User("kim", "노을축구공바다4827", srp.SHA256, srp.NG_CUSTOM, n_hex, g_hex, bytes_a=a.to_bytes(32, "big"))
            usr.process_challenge(salt, srp.long_to_bytes(B))
            self.assertEqual((usr.x, usr.u), (x, u))
            ver.verify_session(usr.M)
            self.assertEqual(ver.S, rlogin.server_S(A, b, v, u, "5054-3072"))
            self.assertEqual(usr.S, rlogin.client_S(B, a, x, u, "5054-3072"))
        finally:
            srp.rfc5054_enable(False)


def handshake(pw_set, pw_try, remember=True, name="Windows · Chrome", install="", host="abc-def.trycloudflare.com",
              pc="0123456789abcdef", idc="nBjO1vuzIzg432UZX9coMQ", login="L" * 22, iters=100_000):
    """PC(검증값) ↔ 클라이언트 한 번 → (서버 M1, 클라이언트 M1, K 둘, T)."""
    salt = bytes(range(16))
    v = rlogin.srp_verifier(rlogin.srp_x(rlogin.canon_pw(pw_set), salt, iters))
    a, A = rlogin.client_start()
    b, B, u = rlogin.server_start(v, A)
    T = rlogin.transcript(host, pc, idc, login, rlogin.GROUP, iters, salt, A, B)
    Ks = rlogin.session_key(rlogin.server_S(A, b, v, u))
    Kc = rlogin.session_key(rlogin.client_S(B, a, rlogin.srp_x(rlogin.canon_pw(pw_try), salt, iters), u))
    return rlogin.m1(Ks, T, remember, name, install), rlogin.m1(Kc, T, remember, name, install), Ks, Kc, T


class SrpProtocolTests(unittest.TestCase):
    def test_round_trip_50_random_passwords(self):
        rnd = random.Random(7)
        for i in range(50):
            pw = rlogin.suggest(rnd=rnd) if i % 2 else "".join(rnd.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(14))
            ms, mc, Ks, Kc, _ = handshake(pw, pw.upper() if i % 3 == 0 else pw, iters=1000)
            self.assertEqual(Ks, Kc, pw)
            self.assertEqual(ms, mc)

    def test_wrong_password_m1_mismatch(self):
        ms, mc, Ks, Kc, _ = handshake("노을축구공바다4827", "노을축구공바다4828", iters=1000)
        self.assertNotEqual(ms, mc)
        self.assertNotEqual(Ks, Kc)

    def test_rejects_A_zero_N_2N_and_wrong_length(self):
        N = rlogin.group()[0]
        for A in (0, N, 2 * N, -1, N + 5):
            self.assertFalse(rlogin.a_ok(A), A)
            with self.assertRaises(ValueError):
                rlogin.server_start(12345, A)
        self.assertTrue(rlogin.a_ok(2))

    def test_u_zero_rerolls_b(self):
        vals = iter([0, 0, 7])
        real = rlogin.srp_u
        with mock.patch.object(rlogin, "srp_u", side_effect=lambda A, B, grp=rlogin.GROUP: next(vals) if True else real(A, B, grp)):
            b, B, u = rlogin.server_start(12345, 2)
        self.assertEqual(u, 7)

    def test_T_binds_host_pc_idc_login_grp_iters_salt_A_B(self):
        base = dict(host="a.trycloudflare.com", pc_id="0123456789abcdef", idc="I" * 22, login="L" * 22, grp=rlogin.GROUP,
                    iters=600000, salt=b"s" * 16, A=12345, B=67890)
        T = rlogin.transcript(**base)
        for k, alt in (("host", "b.trycloudflare.com"), ("pc_id", "fedcba9876543210"), ("idc", "J" * 22), ("login", "M" * 22),
                       ("iters", 600001), ("salt", b"t" * 16), ("A", 12346), ("B", 67891)):
            self.assertNotEqual(rlogin.transcript(**dict(base, **{k: alt})), T, k)
        self.assertEqual(rlogin.transcript(**dict(base, host="A.TRYCLOUDFLARE.COM")), T)   # host 는 작은 글자로

    def test_M1_binds_remember_name_install(self):
        K, T = b"k" * 32, b"t" * 32
        m = rlogin.m1(K, T, True, "Windows · Chrome", "i" * 22)
        self.assertNotEqual(rlogin.m1(K, T, False, "Windows · Chrome", "i" * 22), m)
        self.assertNotEqual(rlogin.m1(K, T, True, "Windows · Edge", "i" * 22), m)
        self.assertNotEqual(rlogin.m1(K, T, True, "Windows · Chrome", "j" * 22), m)
        self.assertNotEqual(rlogin.m1(K, T, True, "Windows · Chrome", ""), m)

    def test_M2_binds_sealed_hash(self):
        K, T, M1 = b"k" * 32, b"t" * 32, b"m" * 32
        self.assertNotEqual(rlogin.m2(K, T, M1, "fsl2.aaa"), rlogin.m2(K, T, M1, "fsl2.aab"))
        self.assertNotEqual(rlogin.m2(K, T, b"n" * 32, "fsl2.aaa"), rlogin.m2(K, T, M1, "fsl2.aaa"))

    def test_seal_key_hkdf_equal_both_sides(self):
        _, _, Ks, Kc, T = handshake("노을축구공바다4827", "노을축구공바다4827", iters=1000)
        self.assertEqual(rlogin.seal_key(Ks, T), rlogin.seal_key(Kc, T))
        self.assertEqual(len(rlogin.seal_key(Ks, T)), 32)
        # RFC 5869 와 같은 계산인지 (표준 hmac 으로 다시)
        import hashlib
        import hmac
        prk = hmac.new(T, Ks, hashlib.sha256).digest()
        okm = hmac.new(prk, b"fsl-seal/v1\x01", hashlib.sha256).digest()
        self.assertEqual(rlogin.seal_key(Ks, T), okm)

    def test_fake_params_deterministic_same_shape(self):
        fake = b"f" * 32
        s1, v1 = rlogin.fake_params(fake, "I" * 22)
        s2, v2 = rlogin.fake_params(fake, "I" * 22)
        self.assertEqual((s1, v1), (s2, v2))
        self.assertEqual(len(s1), 16)
        self.assertNotEqual(rlogin.fake_params(fake, "J" * 22), (s1, v1))
        self.assertNotEqual(rlogin.fake_params(b"g" * 32, "I" * 22), (s1, v1))
        self.assertEqual(len(rlogin.pad(v1, rlogin.group()[0])), 384)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.saved = 0
        self.login = {}
        self.g = rlogin.Guard(self.login, self._persist, self.clock, scale=1.0)

    def _persist(self):
        self.saved += 1

    def fails(self, n, net="1.2.3.0/24", real=True):
        out = None
        for _ in range(n):
            with self.g.lock:
                if self.g.check(net):
                    return "locked", out
                out = self.g.fail(net, real)
        return "ok", out

    def test_net_ladder_5_then_3_3_3(self):
        net = "1.2.3.0/24"
        for allow, dur in rlogin.NET_LADDER:
            st, out = self.fails(allow, net, real=False)
            self.assertEqual(st, "ok")
            v = self.g.check(net)
            self.assertEqual((v["code"], v["scope"]), ("locked", "net"))
            self.assertEqual(v["until"], int(self.clock() + dur))
            self.assertEqual(out["until"], int(self.clock() + dur))
            self.assertEqual(out["left"], 0)
            self.clock.tick(dur)
            self.assertIsNone(self.g.check(net))
        self.assertEqual(self.login["guard"]["nets"][net]["lv"], 3)   # 24시간 칸에 머묾

    def test_net_no_reset_right_after_24h_lock(self):
        net = "1.2.3.0/24"
        for allow, dur in rlogin.NET_LADDER:
            self.fails(allow, net, real=False)
            self.clock.tick(dur)
        st = self.g._net(net, self.clock())
        self.assertEqual((st["lv"], st["n"]), (3, 0))   # 24시간 막힘이 끝난 직후: 처음 칸으로 돌아가지 않음 → 3번만
        self.fails(3, net, real=False)
        self.assertEqual(self.g.check(net)["scope"], "net")

    def test_net_reset_after_24h_quiet_or_success(self):
        net = "1.2.3.0/24"
        self.fails(5, net, real=False)
        self.clock.tick(900 + 86400)
        self.assertEqual(self.g.left(net), 5)
        self.fails(4, net, real=False)
        self.g.success(net)
        self.assertEqual(self.g.left(net), 5)

    def test_global_10_per_hour(self):
        for i in range(10):
            self.fails(1, f"10.0.{i}.0/24")
        v = self.g.check("99.0.0.0/24")
        self.assertEqual((v["code"], v["scope"]), ("locked", "all"))
        self.clock.tick(3600)
        self.assertIsNone(self.g.check("99.0.0.0/24"))

    def test_global_30_per_day_until_count_drops(self):
        for i in range(30):
            self.fails(1, f"10.0.{i}.0/24")
            self.clock.tick(400)   # 시간당 10번 아래로 (9번)
            if self.g.check("99.0.0.0/24"):
                self.clock.tick(3600)
        v = self.g.check("99.0.0.0/24")
        self.assertEqual(v["scope"], "all")
        first = min(self.login["guard"]["recent"])
        self.assertEqual(v["until"], int(first + 86400 + 0.999))
        self.clock.t = first + 86400
        self.assertIsNone(self.g.check("99.0.0.0/24"))

    def test_total_50_suspends_and_never_decays(self):
        n = 0
        while n < 50:
            net = f"10.{n}.0.0/24"
            with self.g.lock:
                if not self.g.check(net):
                    self.g.fail(net, True)
                    n += 1
                else:
                    self.clock.tick(3600 * 25)
        self.assertEqual(self.g.check("99.0.0.0/24"), {"code": "suspended", "why": "fails"})
        self.clock.tick(86400 * 30)
        self.assertEqual(self.g.check("99.0.0.0/24")["code"], "suspended")
        self.assertEqual(self.login["guard"]["total"], 50)

    def test_wrong_id_counts_net_only(self):
        self.fails(4, "1.2.3.0/24", real=False)
        self.assertEqual(self.login["guard"]["total"], 0)
        self.assertEqual(self.login["guard"]["recent"], [])
        self.assertEqual(self.login["guard"]["nets"]["1.2.3.0/24"]["n"], 4)

    def test_left_value(self):
        _, out = self.fails(1)
        self.assertEqual(out["left"], 4)
        self.login["guard"]["total"] = 48
        self.assertEqual(self.g.left("5.5.5.0/24"), 2)
        self.login["guard"]["total"] = 1
        for i in range(8):
            self.fails(1, f"20.0.{i}.0/24")
        self.assertEqual(self.g.left("5.5.5.0/24"), 1)   # 시간당 10 - 9

    def test_notify_thresholds_5_20_40_spacing_1h_and_suspend_always(self):
        notes = []

        def go(k):
            nonlocal notes
            for _ in range(k):
                net = f"10.{len(notes)}.{self.login['guard']['total']}.0/24"
                with self.g.lock:
                    while self.g.check(net):
                        self.clock.tick(3600)
                    notes += self.g.fail(net, True)["notes"]
        go(5)
        self.assertEqual(notes, [("login_fail", 5)])
        self.clock.tick(3600 * 30)
        go(15)
        self.assertEqual(notes[-1], ("login_fail", 20))
        go(1)   # 21: 이미 보냄
        self.assertEqual(len(notes), 2)
        while self.login["guard"]["total"] < 39:
            go(1)
        self.login["guard"]["noticeAt"] = self.clock()   # 방금 보낸 것처럼 → 40 은 한 시간 뒤 다음 실패에
        go(1)
        self.assertEqual(len(notes), 2)
        self.clock.tick(3601)
        go(1)
        self.assertEqual(notes[-1], ("login_fail", 41))
        self.login["guard"]["noticeAt"] = self.clock()
        while not self.login.get("suspended"):
            go(1)
        self.assertEqual(notes[-1], ("login_stop", 50))   # 잠금은 간격과 상관없이

    def test_unlock_clears_all(self):
        self.fails(5)
        self.login["suspended"] = {"why": "kick", "at": 1}
        self.g.unlock()
        self.assertIsNone(self.g.check("1.2.3.0/24"))
        self.assertEqual(self.login["guard"], rlogin.fresh_guard())
        self.assertIsNone(self.login["suspended"])

    def test_persist_reload(self):
        before = self.saved
        self.fails(5)
        self.assertEqual(self.saved - before, 5)   # 셀 때마다 저장
        import json
        again = json.loads(json.dumps(self.login))
        g2 = rlogin.Guard(again, None, self.clock, scale=1.0)
        self.assertEqual(g2.check("1.2.3.0/24")["scope"], "net")

    def test_parallel_40_threads_never_overshoot(self):
        def run(nets, real):
            counted = []
            barrier = threading.Barrier(len(nets))

            def one(net):
                barrier.wait()
                with self.g.lock:   # remote.Service.r_login_finish 와 같은 모양: 확인 → 세기 → 저장을 한 잠금 안에서
                    if self.g.check(net):
                        return
                    self.g.fail(net, real)
                    counted.append(net)
            ts = [threading.Thread(target=one, args=(n,)) for n in nets]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            return counted
        self.assertEqual(len(run(["7.7.7.0/24"] * 40, False)), 5)
        self.clock.tick(86400 * 3)
        self.assertLessEqual(len(run([f"30.0.{i}.0/24" for i in range(40)], True)), 10)
        self.assertEqual(self.login["guard"]["total"], 10)

    def test_time_scale_knob_dev_only(self):
        with mock.patch.dict(os.environ, {"FUTSAL_LOGIN_TIME_SCALE": "0.01"}, clear=False):
            os.environ.pop("FUTSAL_REMOTE_DEV", None)
            self.assertEqual(rlogin.time_scale(), 1.0)
            os.environ["FUTSAL_REMOTE_DEV"] = "1"
            self.assertEqual(rlogin.time_scale(), 0.01)
            g = rlogin.Guard({}, None, self.clock)
            for _ in range(5):
                g.fail("1.1.1.0/24", False)
            self.assertEqual(g.check("1.1.1.0/24")["until"], int(self.clock() + 9 + 0.999))
            os.environ["FUTSAL_LOGIN_TIME_SCALE"] = "5"
            self.assertEqual(rlogin.time_scale(), 1.0)   # 늘리는 것은 안 됨
        os.environ.pop("FUTSAL_REMOTE_DEV", None)


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.p = rlogin.Pending(self.clock)

    def test_two_per_net_oldest_replaced(self):
        a = self.p.put("n1", {"x": 1})
        self.clock.tick(1)
        b = self.p.put("n1", {"x": 2})
        self.clock.tick(1)
        c = self.p.put("n1", {"x": 3})
        self.assertIsNone(self.p.take(a))
        self.assertEqual(self.p.take(b)["x"], 2)
        self.assertEqual(self.p.take(c)["x"], 3)
        self.assertRegex(a, r"^[A-Za-z0-9_-]{22}$")

    def test_same_time_oldest_is_first_put(self):
        for _ in range(20):   # 같은 시각이면 먼저 넣은 것이 밀려남 (확인 번호의 글자 차례와 상관없이)
            p = rlogin.Pending(self.clock)
            a, b = p.put("n1", {"x": 1}), p.put("n1", {"x": 2})
            c = p.put("n1", {"x": 3})
            self.assertIsNone(p.take(a))
            self.assertEqual((p.take(b)["x"], p.take(c)["x"]), (2, 3))

    def test_256_total_busy_no_eviction(self):
        ids = [self.p.put(f"n{i}", {"i": i}) for i in range(256)]
        with self.assertRaises(rlogin.Busy):
            self.p.put("new", {})
        self.assertEqual(self.p.take(ids[0])["i"], 0)   # 남의 것을 밀어내지 않음
        self.p.put("n5", {"i": "again"})                  # 같은 대역은 제 것과 바꿈 (2개 안)

    def test_single_use(self):
        a = self.p.put("n", {})
        self.assertIsNotNone(self.p.take(a))
        self.assertIsNone(self.p.take(a))
        self.assertIsNone(self.p.take("../../etc"))

    def test_ttl_60(self):
        a = self.p.put("n", {})
        self.clock.tick(61)
        self.assertIsNone(self.p.take(a))
        for i in range(256):
            self.p.put(f"m{i}", {})
        self.clock.tick(61)
        self.p.put("later", {})   # 지난 것은 지워져 자리가 남


class UaVocabTests(unittest.TestCase):
    def test_closed_vocab(self):
        cases = {
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36": "Windows · Chrome",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36 Edg/129.0": "Windows · Edge",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Whale/3.27 Safari/537.36": "Windows · 웨일",
            "Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0 Mobile Safari/537.36":
                "Android · 삼성 인터넷",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 KAKAOTALK 10.8.0": "iPhone · 카카오톡",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 NAVER(inapp; search; 2000; 12.6.3)":
                "iPhone · 네이버",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15": "Mac · Safari",
            "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36": "ChromeOS · Chrome",
            "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0": "Linux · Firefox",
            "<script>alert(1)</script>\n가짜 이름": "기타 · 기타",
            "": "기타 · 기타",
        }
        for ua, want in cases.items():
            self.assertEqual(rlogin.ua_vocab(ua), want, ua)


class CountryTests(unittest.TestCase):
    def test_country_vocab(self):
        self.assertEqual(rlogin.country("KR"), "한국")
        self.assertEqual(rlogin.country("us"), "해외(US)")
        for x in (None, "", "XX", "T1", "KOR", "<b>"):
            self.assertIsNone(rlogin.country(x), x)


def good_block():
    import base64
    b = lambda n: base64.urlsafe_b64encode(bytes([7]) * n).rstrip(b"=").decode()  # noqa: E731
    d = rlogin.derive_login("rlazhcl77")
    v = rlogin.make_verifier("노을축구공바다4827", iters=100_000, salt=bytes(16))
    return {"v": 1, "canon": 1, "enabled": True, "id": "rlazhcl77", "idShow": "김코치77", "topic": d["topic"],
            "bkey": rlogin._b64u(d["bkey"]), "idc": d["idc"], "grp": v["grp"], "salt": v["salt"], "iters": v["iters"],
            "verifier": v["verifier"], "fake": b(32), "control": "fsc" + "1" * 24, "gen": 1, "setAt": 1_800_000_000, "seq": 0,
            "guard": rlogin.fresh_guard(), "suspended": None, "events": [], "collision": None}


class ValidLoginTests(unittest.TestCase):
    def test_bad_shapes_rejected(self):
        self.assertTrue(rlogin.valid_login(good_block()))
        N = rlogin.group()[0]
        bad = {"v": 2, "canon": 2, "enabled": "yes", "id": "Kim!", "idShow": "", "topic": "fsl123", "bkey": "AA", "idc": "x",
               "grp": "5054-1024-sha1", "salt": "AA", "iters": 10, "verifier": rlogin._b64u(rlogin.pad(N, N)), "fake": "AA",
               "control": "fsc", "gen": 0, "setAt": "x", "seq": -1, "guard": [], "suspended": {"why": "other"},
               "events": [{"ref": 1}], "collision": "x"}
        for k, v in bad.items():
            self.assertFalse(rlogin.valid_login(dict(good_block(), **{k: v})), k)
        self.assertFalse(rlogin.valid_login(None))
        self.assertFalse(rlogin.valid_login([]))


class KillTokenTests(unittest.TestCase):
    def test_hash_and_format(self):
        t = rlogin.kill_token()
        self.assertRegex(t, r"^[A-Za-z0-9_-]{24}$")
        self.assertTrue(rlogin.KILL_RE.fullmatch(t))
        self.assertEqual(rlogin.kill_hash(t), __import__("hashlib").sha256(t.encode()).hexdigest())
        self.assertNotEqual(rlogin.kill_token(), t)


class PresetToolTests(unittest.TestCase):
    """tools/make_preset.py (가짜 비밀번호로만) · 저장소의 PRESET 은 None(끔) 이거나 검증값만 든 모양 (D-258 소유자 결정으로 넣음)."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        self.mp = importlib.import_module("make_preset")

    def test_shipped_preset_is_off_or_verifier_only(self):
        # D-258 → D-291: 소유자가 넣기로 정함 — 넣었다면 SRP 검증값 열쇠만 (비밀번호·평문 칸 없음) · 아이디는 PRESET_ID ·
        # 지금은 아이디만 방식 (D-290): 검증값은 아이디에서 나온 비밀로 만든 것이어야 함 (진짜 비밀번호의 검증값이 아님)
        P = rlogin.PRESET
        if P is not None:
            self.assertTrue(rlogin.preset_ok(P))
            self.assertEqual(set(P), {"id", "idShow", "canon", "mode", "grp", "salt", "iters", "verifier"})
            self.assertEqual(P["id"], rlogin.canon_id(rlogin.PRESET_ID))
            self.assertEqual(P["mode"], "idOnly")
            self.assertTrue(rlogin.preset_matches_id(P))
            self.assertGreaterEqual(P["iters"], rlogin.ID_ITERS)
        self.assertEqual(rlogin.PRESET_ID, "wavely1213")
        self.assertIsNone(rlogin.id_problem(rlogin.PRESET_ID))

    def test_make_preset_default_is_id_only(self):
        p = self.mp.make()
        self.assertTrue(rlogin.preset_ok(p) and rlogin.preset_matches_id(p))
        self.assertEqual((p["mode"], p["iters"]), ("idOnly", rlogin.ID_ITERS))
        x = rlogin.srp_x(rlogin.canon_pw("WAVELY1213"), rlogin._unb64u(p["salt"]), p["iters"])   # 페이지: 친 아이디 → 맞춘 꼴
        self.assertEqual(rlogin.pad(rlogin.srp_verifier(x), rlogin.group()[0]), rlogin._unb64u(p["verifier"]))
        self.assertFalse(rlogin.preset_matches_id(dict(p, verifier=self.mp.make()["verifier"])))   # 다른 salt 의 검증값
        self.assertFalse(rlogin.preset_matches_id(self.mp.make(FAKE_PW, iters=100_000)))           # 비밀번호 PRESET

    def test_make_preset_has_no_password_and_logs_in(self):
        p = self.mp.make(FAKE_PW, iters=100_000)
        self.assertTrue(rlogin.preset_ok(p))
        text = repr(p)
        for form in (FAKE_PW, rlogin.canon_pw(FAKE_PW)):
            self.assertNotIn(form, text)
        salt = rlogin._unb64u(p["salt"])
        x = rlogin.srp_x(rlogin.canon_pw(FAKE_PW), salt, p["iters"])
        self.assertEqual(rlogin.pad(rlogin.srp_verifier(x), rlogin.group()[0]), rlogin._unb64u(p["verifier"]))

    def test_make_preset_refuses_weak(self):
        with self.assertRaises(ValueError):
            self.mp.make("qwer1234")

    def test_main_reads_env_only(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FUTSAL_PRESET_PW", None)
            self.assertEqual(self.mp.main(["--password"]), 2)   # 비밀번호 PRESET 은 환경 변수로만
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(self.mp.main([]), 0)              # 기본: 아이디만 (입력 없음)
            p = ast.literal_eval(out.getvalue().strip()[len("PRESET = "):])
            self.assertTrue(rlogin.preset_matches_id(p))
            os.environ["FUTSAL_PRESET_PW"] = FAKE_PW
            self.assertEqual(self.mp.main([]), 2)                  # 넣은 비밀번호를 말없이 버리지 않음

    def test_preset_ok_rejects_other_id(self):
        p = self.mp.make(FAKE_PW, iters=100_000)
        self.assertFalse(rlogin.preset_ok(dict(p, id="someone99")))
        self.assertFalse(rlogin.preset_ok(dict(p, verifier="AA")))
        self.assertFalse(rlogin.preset_ok(None))


if __name__ == "__main__":
    unittest.main()


class IdOnlyTests(unittest.TestCase):
    """아이디만 (D-290 · 소유자 결정): 소유자 아이디에서만 · SRP 비밀 = 맞춘 아이디 · 로그인 블록 모양."""

    def test_only_owner_id(self):
        for s in ("wavely1213", "WAVELY1213", "ㅈㅁㅍ디ㅛ1213", "wavely.1213"):
            self.assertTrue(rlogin.id_only_ok(s), s)
        for s in ("wavely12134", "kimcoach99", "김코치77", "", None):
            self.assertFalse(rlogin.id_only_ok(s), s)

    def test_secret_is_canonical_id_and_survives_canon_pw(self):
        self.assertEqual(rlogin.id_secret("ㅈㅁㅍ디ㅛ1213"), "wavely1213")
        self.assertEqual(rlogin.canon_pw(rlogin.id_secret("WAVELY1213")), "wavely1213")   # 예전 페이지 비밀번호 칸에 아이디를 쳐도 같음

    def test_id_verifier_matches_page_math(self):
        v = rlogin.make_id_verifier("wavely1213")
        self.assertEqual(v["iters"], rlogin.ID_ITERS)
        x = rlogin.srp_x("wavely1213", rlogin._unb64u(v["salt"]), v["iters"])
        self.assertEqual(rlogin.pad(rlogin.srp_verifier(x), rlogin.group()[0]), rlogin._unb64u(v["verifier"]))

    def test_valid_login_mode(self):
        block = {"v": 1, "canon": rlogin.CANON, "enabled": True, "id": "wavely1213", "idShow": "wavely1213", "gen": 1, "setAt": 1, "seq": 0,
                 "fake": rlogin._b64u(b"f" * 32), "control": "fsc" + "0" * 24, "events": [], "collision": None, "suspended": None}
        d = rlogin.derive_login("wavely1213")
        block.update(topic=d["topic"], bkey=rlogin._b64u(d["bkey"]), idc=d["idc"], **rlogin.make_id_verifier("wavely1213"))
        self.assertTrue(rlogin.valid_login(block))                       # 예전 블록 (mode 없음 = password)
        self.assertTrue(rlogin.valid_login(dict(block, mode="idOnly")))
        self.assertTrue(rlogin.valid_login(dict(block, mode="password")))
        self.assertFalse(rlogin.valid_login(dict(block, mode="open")))
        d2 = rlogin.derive_login("kimcoach99")
        other = dict(block, id="kimcoach99", idShow="kimcoach99", topic=d2["topic"], bkey=rlogin._b64u(d2["bkey"]), idc=d2["idc"])
        self.assertTrue(rlogin.valid_login(other))
        self.assertFalse(rlogin.valid_login(dict(other, mode="idOnly")))  # 소유자 아이디가 아니면 아이디만이 될 수 없음
