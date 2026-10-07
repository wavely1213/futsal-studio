"""QR 코드 만들기 (표준 라이브러리만) — '휴대폰으로 보기'의 연결 QR (D-027).

encode(text) → 줄 문자열 목록 ('1' = 검은 칸). 바이트 모드 · 오류 정정 M · 버전 1~10 · 마스크 8가지 중 벌점이 가장 낮은 것.
규칙은 ISO/IEC 18004 그대로다 (시험은 tests/test_qr.py 가 segno 와 칸 하나하나 비교).
"""

# 버전마다 (오류 정정 M) 블록 구성: (블록당 정정 코드워드 수, [(블록 수, 블록당 데이터 코드워드 수), …])
_BLOCKS_M = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
    6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_ECL_M = 0  # 형식 정보의 오류 정정 단계 비트 (L=1, M=0, Q=3, H=2)
MAX_VERSION = 10


class TooLong(ValueError):
    pass


# ---------- GF(256) · 리드-솔로몬 ----------

def _gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    out = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            out[j] = _gf_mul(out[j], root)
            if j + 1 < degree:
                out[j] ^= out[j + 1]
        root = _gf_mul(root, 0x02)
    return out


def rs_remainder(data, degree):
    """데이터 코드워드 → 정정 코드워드 degree 개."""
    div = _rs_divisor(degree)
    out = [0] * degree
    for b in data:
        factor = b ^ out.pop(0)
        out.append(0)
        for i, c in enumerate(div):
            out[i] ^= _gf_mul(c, factor)
    return out


# ---------- 형식·버전 정보 ----------

def format_bits(mask, ecl=_ECL_M):
    data = ecl << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return (data << 10 | rem) ^ 0x5412


def version_bits(version):
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return version << 12 | rem


# ---------- 데이터 ----------

def _data_codewords(version):
    return sum(n * k for n, k in _BLOCKS_M[version][1])


def _pick_version(nbytes):
    for v in range(1, MAX_VERSION + 1):
        bits = 4 + (8 if v < 10 else 16) + 8 * nbytes
        if bits <= _data_codewords(v) * 8:
            return v
    raise TooLong("QR 에 넣기에 글자가 너무 길어요")


def _codewords(data, version):
    cap = _data_codewords(version)
    bits = []

    def put(val, n):
        bits.extend((val >> i) & 1 for i in range(n - 1, -1, -1))

    put(0b0100, 4)
    put(len(data), 8 if version < 10 else 16)
    for b in data:
        put(b, 8)
    put(0, min(4, cap * 8 - len(bits)))
    put(0, (-len(bits)) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < cap:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    ec_len, groups = _BLOCKS_M[version]
    blocks, k = [], 0
    for n, size in groups:
        for _ in range(n):
            blocks.append(words[k:k + size])
            k += size
    eccs = [rs_remainder(b, ec_len) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ec_len):
        out += [e[i] for e in eccs]
    return out


# ---------- 그리기 ----------

class _Grid:
    def __init__(self, version):
        self.v = version
        self.n = 17 + 4 * version
        self.m = [[False] * self.n for _ in range(self.n)]
        self.fn = [[False] * self.n for _ in range(self.n)]

    def set_fn(self, x, y, dark):
        self.m[y][x] = bool(dark)
        self.fn[y][x] = True

    def functions(self):
        n = self.n
        for i in range(n):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.v]
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format(0)
        if self.v >= 7:
            bits = version_bits(self.v)
            for i in range(18):
                b = (bits >> i) & 1
                a, c = n - 11 + i % 3, i // 3
                self.set_fn(a, c, b)
                self.set_fn(c, a, b)

    def format(self, mask):
        bits, n = format_bits(mask), self.n

        def bit(i):
            return (bits >> i) & 1
        for i in range(6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fn(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, n - 15 + i, bit(i))
        self.set_fn(8, n - 8, True)  # 늘 검은 칸

    def place(self, words):
        n, i, total = self.n, 0, len(words) * 8
        for right in range(n - 1, 0, -2):
            if right <= 6:
                right -= 1
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    up = (right + 1) & 2 == 0
                    y = n - 1 - vert if up else vert
                    if not self.fn[y][x] and i < total:
                        self.m[y][x] = bool((words[i >> 3] >> (7 - (i & 7))) & 1)
                        i += 1

    def apply_mask(self, mask):
        f = _MASKS[mask]
        for y in range(self.n):
            for x in range(self.n):
                if not self.fn[y][x] and f(x, y):
                    self.m[y][x] = not self.m[y][x]


_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


# ---------- 벌점 (마스크 고르기) ----------

def _penalty(m):
    n, score = len(m), 0

    def finder_count(h):
        k = h[1]
        core = k > 0 and h[2] == h[4] == h[5] == k and h[3] == k * 3
        return (1 if core and h[0] >= k * 4 and h[6] >= k else 0) + (1 if core and h[6] >= k * 4 and h[0] >= k else 0)

    def add_hist(run, h):
        if h[0] == 0:
            run += n  # 앞쪽 빈 테두리
        h.insert(0, run)
        h.pop()

    for line in [row for row in m] + [[m[y][x] for y in range(n)] for x in range(n)]:
        color, run, h = False, 0, [0] * 7
        for c in line:
            if c == color:
                run += 1
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            else:
                add_hist(run, h)
                if not color:
                    score += finder_count(h) * 40
                color, run = c, 1
        if color:
            add_hist(run, h)
            run = 0
        add_hist(run + n, h)  # 뒤쪽 빈 테두리
        score += finder_count(h) * 40
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark, total = sum(c for row in m for c in row), n * n
    score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
    return score


def matrix(text, mask=None, version=None):
    """→ (칸 목록 [[bool]], 버전, 마스크). mask·version 을 주면 그대로 (시험용 비교)."""
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    v = version or _pick_version(len(data))
    if _pick_version(len(data)) > v:
        raise TooLong("QR 에 넣기에 글자가 너무 길어요")
    g = _Grid(v)
    g.functions()
    g.place(_codewords(data, v))
    if mask is None:
        best = None
        for k in range(8):
            g.apply_mask(k)
            g.format(k)
            p = _penalty(g.m)
            if best is None or p < best[0]:
                best = (p, k)
            g.apply_mask(k)  # 되돌림 (XOR 두 번)
        mask = best[1]
    g.apply_mask(mask)
    g.format(mask)
    return g.m, v, mask


def encode(text):
    """주소 → 줄 문자열 목록 ('1' 검은 칸 · '0' 흰 칸, 테두리 여백은 그리는 쪽이 4칸 둠)."""
    m, _, _ = matrix(text)
    return ["".join("1" if c else "0" for c in row) for row in m]


def svg(rows, scale=6, quiet=4):
    """줄 문자열 → SVG 글 (PC 화면은 같은 일을 JS 로 함 · 시험·문서용)."""
    n = len(rows)
    size = (n + quiet * 2) * scale
    path = "".join(f"M{(x + quiet) * scale},{(y + quiet) * scale}h{scale}v{scale}h-{scale}z"
                   for y, row in enumerate(rows) for x, c in enumerate(row) if c == "1")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
            f'<rect width="100%" height="100%" fill="#fff"/><path d="{path}" fill="#000"/></svg>')
