"""채널 전략 '가능성(%)' 계산 (D-025) — 파일·네트워크 없이 숫자만 받아 숫자만 돌려준다.

모델 (투명한 주 단위 Monte Carlo, 비교 채널 공개 숫자로 보정):
- 영상 한 편 전체 조회수 V = exp(a + b·ln max(구독자,100) + e·ln 주당 개수 + u + σ·z)
  · a·b·τ: 비교 채널의 '보통 조회수 ~ 구독자' 회귀 (형식마다 · 분류 평균 잔차를 k=5 로 줄여 사전 평균)
  · σ: 채널 안 영상끼리 흔들림 (채널마다 ln 조회수 표준편차의 중앙값)
  · u: 우리 채널 반응 위치 — 분류 사전 분포 + 우리 최근 영상(최근 2년 · 근거 상한 8편)의 정규-정규 사후 분포
  · e: 많이 올릴 때 한 편 조회가 줄어드는 정도 (0 이하로 자름)
- 조회수는 롱폼 약 3주·쇼츠 약 1주에 걸쳐 지수적으로 쌓이고, 쌓인 조회의 cL(롱폼)·cS(쇼츠) 비율만큼 구독이 는다
  (비교 채널의 평생 조회·구독자로 Theil–Sen 직선 · 우리 채널 전환 배수 m_c 는 시뮬레이션마다 한 번 뽑음)
- 매개변수 불확실성: 채널을 다시 뽑는 bootstrap B 번 × 시뮬레이션 M 번. 공통 난수(같은 seed 의 같은 배열)를
  모든 계획 변형에 똑같이 써서, 민감도 차이는 운이 아니라 입력에서 나온다.
- 표시 규칙(BR-015): 5%p 단위, 5% 미만·95% 이상, 범위(bootstrap 별 확률의 10~90 백분위), 믿을 만함(범위 폭 + 근거), '어림'.
- 믿을 만함은 범위가 좁아도 근거가 적으면(우리 영상 근거 3편분 미만 · 비교 자료 없음 · 새 채널 · 거꾸로 시험 실패 ·
  쇼츠 전환이 데이터로 정해지지 않아 결과가 크게 움직임) '낮음' — 까닭을 함께 돌려줌. 우리 영상이 4편 이하면 한 편씩 빼고 다시 계산해 범위를 넓힘.
numpy 는 함수 안에서 불러온다 (앱 시작·업데이트 import 확인을 가볍게).
"""
import hashlib
import json
import math

# 2026-10-07 보정값 — 비교 데이터가 없거나 깨졌을 때만 씀 (DECISION_LOG D-025)
# lc0 = 비교 채널의 ln 주당 개수 평균 (탄력성 e 의 기준점: 회귀 절편은 '평균 주기로 올리는 채널' 기준이라)
DEFAULTS = {"L": {"a": 1.63, "b": 0.73, "tau": 1.30, "sigma": 0.83, "e": -0.17, "lc0": math.log(0.52)},
            "S": {"a": 4.87, "b": 0.44, "tau": 1.29, "sigma": 0.97, "e": 0.0, "lc0": math.log(1.8)},
            "cL": 0.0085, "cS": 0.00009}
FMTS = ("L", "S")
B, M, W = 100, 60, 52           # bootstrap 횟수 · bootstrap 마다 시뮬레이션 수 · 주(12개월)
SEED = 20261007                 # 같은 입력 → 같은 출력
ACCRUE = {"L": 3.0, "S": 1.0}   # 조회수가 쌓이는 시간 상수(주): 롱폼 약 3주, 쇼츠 약 1주
CONV_SD = 0.5                   # 우리 채널 구독 전환 배수의 흔들림 (ln 표준편차) 최소값 · 비교 채널끼리의 차이가 더 크면 그것을 씀
CONV_SD_MAX = 1.0
CL_RANGE = (0.002, 0.03)        # 롱폼 1회당 구독 (0.2%~3%)
CS_RANGE = (0.00002, 0.001)     # 쇼츠 1회당 구독
E_RANGE = (-0.3, 0.0)           # 업로드 빈도 탄력성: 잔차가 거의 0 이라 0 을 넘지 않게
E_SHRINK = 20                   # 탄력성 줄이기 n/(n+20)
GROUP_SHRINK = 5                # 분류 평균 잔차 줄이기 k
W_EFF_MAX = 8.0                 # 우리 옛 영상 근거 상한 (방향이 바뀌므로 옛 영상이 너무 많이 말하지 않게)
MIN_VIDS = 5                    # 회귀에 넣는 채널: 그 형식 영상이 이만큼 있어야
MIN_REG = 3                     # 회귀에 필요한 최소 채널 수 (그보다 적으면 DEFAULTS 와 섞음)
MAX_RATE = 14.0                 # 주당 개수 상한
SUBS_FLOOR = 100                # 구독자가 0 인 새 채널도 ln 이 되게
B_RANGE = (0.0, 1.0)            # 규모-조회 기울기: 조회가 구독자보다 빨리 늘지는 않게 (채널이 적은 bootstrap 표본에서 폭주 방지)
LN_V_MAX = math.log(5e7)        # 영상 한 편 조회수 상한 (계산 넘침 방지 · 실제로는 닿지 않음)
S_MAX = 1e9
SUB_GOALS = (1000, 10000, 20000, 50000, 100000)
VIEW_GOALS = {"S": 10000, "L": 5000}
H6, H12 = 25, 51                # 6개월 = 26주째 끝, 12개월 = 52주째 끝 (주 번호 0부터)
VIEW_WEEKS = 26                 # 조회수 목표: 첫 6개월에 올린 영상의 평균
KPI_DAYS = (30, 60, 90)
Z80 = 1.2816                    # 80% 범위 (정규분포)
INFLATE_GRID = (1.0, 1.25, 1.5, 1.75, 2.0)
BACKTEST_MIN = 5                # 기록 거꾸로 시험: 이만큼 모여야 퍼짐 배수를 고침
OWN_MIN_W = 3.0                 # 우리 영상 근거 무게가 이보다 적으면 '믿을 만함 낮음' (한두 편에 크게 흔들림)
LOO_MAX = 4                     # 우리 영상이 이만큼 이하면 한 편씩 빼고 다시 계산해 범위를 넓힘
CONV_SENS = 0.15                # 쇼츠 전환을 범위 끝(아래·위)으로 바꿨을 때 확률이 이만큼 넘게 움직이면 '데이터로 정하지 못함'
PACE_DIFF = 0.25                # 지금 속도가 계획과 이만큼(주당 개수 합) 넘게 다르면 '지금 속도대로면'을 따로 보여 줌


class ForecastError(RuntimeError):
    """화면에 그대로 보여 줄 한국어 안내."""


def _np():
    try:
        import numpy as np
        return np
    except ImportError as e:
        raise ForecastError("가능성 계산에 필요한 부품(numpy)이 없어요. '시작하기 (Windows).bat'을 다시 실행해 주세요") from e


# ---------- 작은 통계 도구 ----------

def _valid_views(xs):
    out = []
    for v in xs or []:
        if isinstance(v, bool):
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v > 0 and math.isfinite(v):
            out.append(v)
    return out


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def _ols(x, y):
    """최소제곱 직선 → (a, b). 점이 모두 같은 x 면 None."""
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx <= 1e-12:
        return None
    b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sxx
    return my - b * mx, b


def _sd(xs, ddof=1):
    n = len(xs)
    if n - ddof <= 0:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((v - m) ** 2 for v in xs) / (n - ddof))


def theil_sen(x, y):
    """Theil–Sen 직선 → (절편, 기울기): 점 두 개씩의 기울기 중앙값 · 튀는 채널 하나에 덜 흔들림."""
    sl = [(y[j] - y[i]) / (x[j] - x[i]) for i in range(len(x)) for j in range(i + 1, len(x)) if abs(x[j] - x[i]) > 1e-12]
    if not sl:
        return (_median(y) or 0.0), 0.0
    m = _median(sl)
    return _median([yi - m * xi for xi, yi in zip(x, y)]), m


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ---------- 보정 ----------

def _rows(peers, f):
    """회귀에 쓸 채널: (ln 구독자, ln 보통 조회수, 채널 안 ln 표준편차, 분류, 주당 개수|None)."""
    out = []
    for p in peers:
        vs = _valid_views(p.get(f))
        subs = p.get("subs")
        if len(vs) < MIN_VIDS or isinstance(subs, bool) or not isinstance(subs, (int, float)) or subs <= 0:
            continue
        lv = [math.log(v) for v in vs]
        cad = (p.get("cad") or {}).get(f)
        out.append({"x": math.log(subs), "y": math.log(_median(vs)), "sd": _sd(lv) or 0.0, "g": p.get("group") or "",
                    "cad": float(cad) if isinstance(cad, (int, float)) and cad > 0 else None, "key": p.get("key")})
    return out


def _fit(rows, f, group):
    """형식 하나의 반응 회귀 (rows 는 _rows 결과) → a, b, tau, sigma, adj(우리 분류 사전 평균) · 채널이 적으면 DEFAULTS 와 섞음."""
    d = DEFAULTS[f]
    n = len(rows)
    sig = _median([r["sd"] for r in rows if r["sd"] > 0])
    line = _ols([r["x"] for r in rows], [r["y"] for r in rows]) if n >= MIN_REG else None
    if line is None:
        # 1~2곳: 회귀하지 않고 기본 직선을 그 채널들 쪽으로 조금 옮김 (줄이기 k=5)
        res = [r["y"] - (d["a"] + d["b"] * r["x"]) for r in rows]
        shift = sum(res) / (n + GROUP_SHRINK) if n else 0.0
        return {"a": d["a"] + shift, "b": d["b"], "tau": d["tau"], "sigma": sig or d["sigma"], "adj": 0.0, "n": n, "defaults": True}
    a, b = line
    if not (B_RANGE[0] <= b <= B_RANGE[1]):  # 평균점은 지나게 두고 기울기만 자름
        mx = sum(r["x"] for r in rows) / n
        my = sum(r["y"] for r in rows) / n
        b = _clamp(b, *B_RANGE)
        a = my - b * mx
    res = [r["y"] - (a + b * r["x"]) for r in rows]
    tau = _sd(res, ddof=2) if n > 2 else None
    gres = [e for r, e in zip(rows, res) if r["g"] == group]
    adj = sum(gres) / (len(gres) + GROUP_SHRINK) if gres else 0.0
    return {"a": a, "b": b, "tau": max(0.2, tau or d["tau"]), "sigma": sig or d["sigma"], "adj": adj, "n": n, "defaults": False}


def _elasticity(rows, fit, f):
    """잔차 ~ ln 주당 개수 기울기를 n/(n+20)으로 줄이고 [-0.3, 0] 으로 자름 · RSS 주기가 있는 채널이 5곳 미만이면 기본값.
    → (e, 채널 수, 줄이기 전 기울기, 기준점 lc0 = 그 채널들의 ln 주당 개수 평균)."""
    pts = [(math.log(max(r["cad"], 0.05)), r["y"] - (fit["a"] + fit["b"] * r["x"])) for r in rows if r["cad"]]
    if len(pts) < 5:
        return DEFAULTS[f]["e"], len(pts), None, DEFAULTS[f]["lc0"]
    lc0 = sum(p[0] for p in pts) / len(pts)
    line = _ols([p[0] for p in pts], [p[1] for p in pts])
    if line is None:
        return DEFAULTS[f]["e"], len(pts), None, lc0
    raw = line[1]
    return _clamp(raw * len(pts) / (len(pts) + E_SHRINK), *E_RANGE), len(pts), raw, lc0


def _conv_rows(peers):
    out = []
    for p in peers:
        life = p.get("life") or {}
        subs = p.get("subs")
        lL, lS = life.get("L"), life.get("S")
        if not life.get("complete") or isinstance(subs, bool) or not isinstance(subs, (int, float)) or subs <= 0:
            continue
        if not isinstance(lL, (int, float)) or lL <= 0 or not isinstance(lS, (int, float)) or lS < 0:
            continue
        out.append((lS / lL, subs / lL))
    return out


def _conv(rows):
    if len(rows) < 3:
        return DEFAULTS["cL"], DEFAULTS["cS"], True
    c, m = theil_sen([r[0] for r in rows], [r[1] for r in rows])
    return _clamp(c, *CL_RANGE), _clamp(m, *CS_RANGE), False


def _conv_sd(rows, cL, cS):
    """채널마다 실제 구독/조회 ÷ 직선 예측의 ln 표준편차 → 우리 전환 배수의 흔들림 (0.5~1.0 · 3곳 미만이면 0.5)."""
    res = [math.log(y / (cL + cS * x)) for x, y in rows if y > 0 and cL + cS * x > 0]
    sd = _sd(res) if len(res) >= 3 else None
    return _clamp(sd, CONV_SD, CONV_SD_MAX) if sd else CONV_SD


def loo_coverage(rows):
    """반응 회귀를 채널 하나씩 빼고 다시 맞춰, 뺀 채널이 80% 범위에 드는지 → (적중, 수)."""
    hit = n = 0
    for i in range(len(rows)):
        rest = rows[:i] + rows[i + 1:]
        line = _ols([r["x"] for r in rest], [r["y"] for r in rest]) if len(rest) >= MIN_REG else None
        if line is None:
            continue
        res = [r["y"] - (line[0] + line[1] * r["x"]) for r in rest]
        tau = _sd(res, ddof=2)
        if not tau:
            continue
        n += 1
        hit += abs(rows[i]["y"] - (line[0] + line[1] * rows[i]["x"])) <= Z80 * tau
    return hit, n


def calibrate(peers, group, boot=B, seed=SEED):
    """비교 채널 → 보정값 + bootstrap 표본. peers: [{key, group, subs, L:[조회수], S:[조회수], cad:{L,S}, life:{L,S,complete}}]."""
    np = _np()
    rng = np.random.default_rng(seed)
    out = {"groups": {}, "boot": {}, "n_peers": len(peers)}
    for f in FMTS:
        rows = _rows(peers, f)
        fit = _fit(rows, f, group)
        e, en, eraw, lc0 = _elasticity(rows, fit, f) if not fit["defaults"] else (DEFAULTS[f]["e"], 0, None, DEFAULTS[f]["lc0"])
        fit.update(e=e, e_n=en, e_raw=eraw, lc0=lc0, loo=loo_coverage(rows) if len(rows) > MIN_REG else (0, 0))
        out[f] = fit
        bs = []
        for _ in range(boot):
            if len(rows) >= MIN_REG:
                idx = rng.integers(0, len(rows), len(rows))
                bf = _fit([rows[i] for i in idx], f, group)
                if bf["defaults"]:
                    bf = fit
            else:
                bf = fit
            bs.append((bf["a"], bf["b"], bf["tau"], bf["sigma"], bf["adj"]))
        out["boot"][f] = bs
    crow = _conv_rows(peers)
    cL, cS, cdef = _conv(crow)
    out.update(cL=cL, cS=cS, conv_n=len(crow), conv_defaults=cdef, conv_sd=_conv_sd(crow, cL, cS) if not cdef else CONV_SD)
    cb = []
    for _ in range(boot):
        if len(crow) >= 3:
            idx = rng.integers(0, len(crow), len(crow))
            bL, bS, _d = _conv([crow[i] for i in idx])
        else:
            bL, bS = cL, cS
        cb.append((bL, bS))
    out["boot"]["c"] = cb
    return out


# ---------- 우리 채널 ----------

def own_used(own, f):
    """근거로 쓰는 우리 영상 [(번호, 무게, 다 쌓인 조회수 어림)] — 최근 1년 무게 1 · 1~2년 0.5 · 2년 넘은 영상·날짜 모르는 영상은 뺌.
    막 올린 영상(조회가 30% 도 안 쌓임)도 뺌."""
    out = []
    for i, v in enumerate(own.get("videos") or []):
        if v.get("k") != f:
            continue
        views = _valid_views([v.get("v")])
        age = v.get("age")
        if not views or age is None or age > 730:
            continue
        frac = 1 - math.exp(-(max(age, 0) / 7.0) / ACCRUE[f])
        if frac < 0.3:
            continue
        out.append((i, 1.0 if age <= 365 else 0.5, views[0] / frac))
    return out


def _own_resid(own, f, a, b):
    """우리 영상 잔차 (가중 평균, 무게 합) — own_used 의 영상만. 쌓이는 중인 영상은 다 쌓인 값으로 어림."""
    s0 = max(float(own.get("subs") or 0), SUBS_FLOOR)
    ws = sw = 0.0
    for _i, wt, full in own_used(own, f):
        r = math.log(full) - (a + b * math.log(s0))
        ws += wt * r
        sw += wt
    return (ws / sw if sw else 0.0), sw


def own_posterior(own, f, a, b, tau, sigma, adj):
    """우리 반응 위치 u 의 사후 분포 (평균, 분산, 쓴 근거 무게)."""
    rbar, w = _own_resid(own, f, a, b)
    we = min(w, W_EFF_MAX)
    prec = we / sigma ** 2 + 1 / tau ** 2
    mean = (we / sigma ** 2 * rbar + adj / tau ** 2) / prec
    return mean, 1 / prec, we


def own_conv_mu(own, cL, cS):
    """우리 평생 구독/조회 비율로 전환 배수 ln 평균 (끝까지 받은 목록이 있을 때만).
    무게 n/(n+3) 의 n 은 최근 2년 롱폼 근거 무게 — 다시 시작하는 채널의 평생 비율은 옛 시절 숫자라 너무 많이 말하지 않게."""
    life = own.get("life") or {}
    s0 = own.get("subs") or 0
    if not life.get("complete") or s0 <= 0:
        return 0.0
    exp_subs = cL * float(life.get("L") or 0) + cS * float(life.get("S") or 0)
    if exp_subs <= 0:
        return 0.0
    n = _own_resid(own, "L", 0.0, 0.0)[1]
    return _clamp(math.log(s0 / exp_subs) * n / (n + 3), -1.5, 1.5)


# ---------- 시뮬레이션 ----------

def _rates(plan):
    return {f: _clamp(float(plan.get(f) or 0), 0.0, MAX_RATE) for f in FMTS}


def _n_videos(rate, weeks=W):
    return int(math.floor(weeks * rate + 1e-9))


def draws(boot, sims, max_rates, seed=SEED):
    """공통 난수: 영상 순번마다 z · 우리 반응 위치 · 전환 배수 (모든 계획 변형이 같은 배열을 씀)."""
    np = _np()
    rng = np.random.default_rng(seed + 1)
    d = {f: rng.standard_normal((max(1, _n_videos(max_rates[f]) + 1), boot, sims)) for f in FMTS}
    d.update(uL=rng.standard_normal((boot, sims)), uS=rng.standard_normal((boot, sims)), uc=rng.standard_normal((boot, sims)))
    return d


def _params(cal, own, inflate):
    """bootstrap 마다 쓰는 값 → (B,1) 배열 묶음."""
    np = _np()
    P = {}
    for f in FMTS:
        bs = cal["boot"][f]
        a = np.array([x[0] for x in bs])[:, None]
        b = np.array([x[1] for x in bs])[:, None]
        tau = np.array([x[2] for x in bs]) * inflate
        sig = np.array([x[3] for x in bs])[:, None]
        mean, sd = [], []
        for (aa, bb, tt, ss, adj), ti in zip(bs, tau):
            m, v, _w = own_posterior(own, f, aa, bb, ti, ss, adj)
            mean.append(m)
            sd.append(math.sqrt(v))
        P[f] = {"a": a, "b": b, "sig": sig, "um": np.array(mean)[:, None], "us": np.array(sd)[:, None], "e": cal[f]["e"],
                "lc0": cal[f].get("lc0", DEFAULTS[f]["lc0"])}
    cb = cal["boot"]["c"]
    P["cL"] = np.array([x[0] for x in cb])[:, None]
    P["cS"] = np.array([x[1] for x in cb])[:, None]
    P["muc"] = np.array([own_conv_mu(own, x[0], x[1]) for x in cb])[:, None]
    return P


def _cad_term(pf, r):
    """업로드 빈도 효과: e·(ln 주당 개수 − 비교 채널 평균 ln 주당 개수) · 평균보다 많이 올리면 한 편 조회가 조금 줄어듦."""
    return pf["e"] * (math.log(max(r, 0.1)) - pf["lc0"])


def simulate(cal, own, plan, dr, inflate=1.0, g0=0.0, u_shift=0.0, conv_mult=1.0, weeks=W, cS_fix=None, P=None):
    """주 단위 시뮬레이션 → {traj (W,B,M), avg{L,S} (B,M)|None, med0{L,S} (B,M)|None}.
    cS_fix: 쇼츠 전환을 그 값으로 고정 (데이터로 정해지지 않을 때 범위 끝에서 얼마나 바뀌는지 보기) · P: 미리 만든 _params."""
    np = _np()
    rates = _rates(plan)
    P = dict(P or _params(cal, own, inflate))
    if cS_fix is not None:
        P["cS"] = np.full_like(P["cS"], float(cS_fix))
    nb, ns = dr["uL"].shape
    s0 = float(own.get("subs") or 0)
    S = np.full((nb, ns), s0)
    mc = np.exp(P["muc"] + cal.get("conv_sd", CONV_SD) * inflate * dr["uc"]) * conv_mult
    u = {f: P[f]["um"] + P[f]["us"] * dr["u" + f] + u_shift for f in FMTS}
    R = {f: np.zeros((nb, ns)) for f in FMTS}
    vsum = {f: np.zeros((nb, ns)) for f in FMTS}
    vcnt = {f: 0 for f in FMTS}
    keep = {f: math.exp(-1.0 / ACCRUE[f]) for f in FMTS}
    traj = np.empty((weeks, nb, ns))
    med0 = {}
    for f in FMTS:
        r = rates[f]
        med0[f] = np.exp(P[f]["a"] + P[f]["b"] * math.log(max(s0, SUBS_FLOOR)) + _cad_term(P[f], r) + u[f]) if r > 0 else None
    for t in range(weeks):
        for f in FMTS:
            r = rates[f]
            n0, n1 = _n_videos(r, t), _n_videos(r, t + 1)
            if n1 <= n0:
                continue
            base = P[f]["a"] + P[f]["b"] * np.log(np.maximum(S, SUBS_FLOOR)) + _cad_term(P[f], r) + u[f]
            z = dr[f]
            for j in range(n0, n1):
                V = np.exp(np.minimum(base + P[f]["sig"] * z[min(j, z.shape[0] - 1)], LN_V_MAX))
                R[f] += V
                if t < VIEW_WEEKS:
                    vsum[f] += V
                    vcnt[f] += 1
        gain = np.zeros((nb, ns))
        for f, c in (("L", P["cL"]), ("S", P["cS"])):
            real = R[f] * (1 - keep[f])
            R[f] -= real
            gain += c * real
        S = np.minimum(S + mc * gain + g0, S_MAX)
        traj[t] = S
    avg = {f: (vsum[f] / vcnt[f] if vcnt[f] else None) for f in FMTS}
    return {"traj": traj, "avg": avg, "med0": med0}


# ---------- 표시 규칙 (BR-015) ----------

def round5(p):
    return int(5 * round(100 * p / 5))


def _ptext(p):
    return "5% 미만" if p < 0.05 else "95% 이상" if p > 0.95 else f"약 {round5(p)}%"


def show(p, lo, hi, why=None):
    """확률(0~1) → 화면 값: 5%p 단위 · 5% 미만/95% 이상 · 범위 · 믿을 만함(confidence) · 아주 불확실.
    믿을 만함은 범위 폭으로 정하되, why(까닭 목록)가 있으면 언제나 '낮음' (근거가 적은 것은 폭이 좁아도 믿기 어려움)."""
    P, L, H = round5(p), round5(lo), round5(hi)
    L, H = min(L, P), max(H, P)
    L, H = max(0, L), min(100, H)
    d = {"p": P, "lo": L, "hi": H, "text": _ptext(p), "range": f"{L}~{H}%", "why": list(why or [])}
    _set_conf(d)
    return d


def _set_conf(d):
    width = d["hi"] - d["lo"]
    d["confidence"] = "낮음" if d.get("why") else "높음" if width <= 25 else "보통" if width <= 45 else "낮음"
    d["wide"] = width > 60
    d["range"] = f"{d['lo']}~{d['hi']}%"
    return d


def sig2(x):
    """유효숫자 2자리 (예: 9,431 → 9,400)."""
    if not x or x <= 0:
        return 0
    k = int(math.floor(math.log10(x))) - 1
    return int(round(x / 10 ** k) * 10 ** k) if k > 0 else int(round(x))


def _subs_label(n):
    return f"{n // 10000}만" if n >= 10000 and n % 10000 == 0 else f"{n // 1000}천" if n % 1000 == 0 and n < 10000 else f"{n:,}"


# ---------- 결과 묶기 ----------

def _milestones(sim, own, plan, goals):
    np = _np()
    s0 = float(own.get("subs") or 0)
    out = []
    targets = [(g, False) for g in SUB_GOALS]
    for k, h in (("m6", H6), ("m12", H12)):
        g = (goals or {}).get(k)
        if isinstance(g, (int, float)) and g > 0 and int(g) not in SUB_GOALS:
            targets.append((int(g), k))
    for g, user in targets:
        for hz, wk in (("6", H6), ("12", H12)):
            if user and ((user == "m6") != (hz == "6")):
                continue
            hit = sim["traj"][wk] >= g
            pb = hit.mean(axis=1)
            d = show(float(pb.mean()), float(np.percentile(pb, 10)), float(np.percentile(pb, 90)))
            d.update(id=f"subs{g}_{hz}", kind="subs", target=g, label=f"구독자 {_subs_label(g)}" + (" (우리 목표)" if user else ""),
                     horizon=int(hz), achieved=s0 >= g, user=bool(user))
            out.append(d)
    rates = _rates(plan)
    for f, g in (("S", VIEW_GOALS["S"]), ("L", VIEW_GOALS["L"])):
        name = "쇼츠" if f == "S" else "롱폼"
        lab = f"{name} 평균 {_subs_label(g)} 회"
        avg = sim["avg"][f]
        if avg is None or rates[f] <= 0:
            out.append({"id": f"avg{f}_6", "kind": "views", "fmt": f, "target": g, "label": lab, "horizon": 6, "p": 0, "lo": 0, "hi": 0,
                        "text": "계획에 없어요", "range": "", "confidence": "", "wide": False, "achieved": False, "none": True})
            continue
        pb = (avg >= g).mean(axis=1)
        d = show(float(pb.mean()), float(np.percentile(pb, 10)), float(np.percentile(pb, 90)))
        d.update(id=f"avg{f}_6", kind="views", fmt=f, target=g, label=lab, horizon=6, achieved=False)
        out.append(d)
    return out


def _trajectory(sim):
    np = _np()
    t = sim["traj"].reshape(sim["traj"].shape[0], -1)
    q = np.percentile(t, [10, 25, 50, 75, 90], axis=1)
    return {"weeks": list(range(1, t.shape[0] + 1)), **{k: [round(float(x)) for x in row] for k, row in zip(("p10", "p25", "p50", "p75", "p90"), q)}}


def _kpi(sim):
    np = _np()
    out = {}
    for d in KPI_DAYS:
        wk = max(0, round(d / 7) - 1)
        x = sim["traj"][wk].ravel()
        out[f"day{d}"] = {"subs": {"p25": sig2(float(np.percentile(x, 25))), "p50": sig2(float(np.percentile(x, 50))),
                                   "p75": sig2(float(np.percentile(x, 75)))}, "week": wk + 1}
    med = {}
    for f in FMTS:
        m = sim["med0"][f]
        med[f] = None if m is None else {"p25": sig2(float(np.percentile(m, 25))), "p50": sig2(float(np.percentile(m, 50))),
                                         "p75": sig2(float(np.percentile(m, 75)))}
    out["videoMedian"] = med
    return out


def _subs_range(sim, wk):
    np = _np()
    x = sim["traj"][wk].ravel()
    p10, p50, p90 = (sig2(float(np.percentile(x, q))) for q in (10, 50, 90))
    return {"p10": p10, "p50": p50, "p90": p90, "text": f"약 {p10:,}~{p90:,}명 (가운데 {p50:,}명)"}


def _rep_goal(own, goals):
    """대표 목표: 12개월 안에 다음으로 넘을 구독자 목표 (이미 넘은 것은 빼고)."""
    s0 = float(own.get("subs") or 0)
    nxt = [g for g in SUB_GOALS if g > s0]
    if nxt:
        return nxt[0]
    g = (goals or {}).get("m12")
    return int(g) if isinstance(g, (int, float)) and g > s0 else None


def _p_goal(sim, g, wk):
    return float((sim["traj"][wk] >= g).mean())


def variants(plan):
    """민감도 변형: (id, 문장 앞부분, 계획, u 이동, 전환 배수)."""
    rL, rS = _rates(plan)["L"], _rates(plan)["S"]

    def n(x):
        return f"{x:g}"
    out = [("S+2", f"쇼츠를 주 {n(rS)}→{n(rS + 2)}개로 늘리면", {"L": rL, "S": rS + 2}, 0.0, 1.0),
           ("L+1", f"롱폼을 주 {n(rL)}→{n(rL + 1)}개로 늘리면", {"L": rL + 1, "S": rS}, 0.0, 1.0)]
    if rL > 0:
        out.append(("L/2", f"롱폼을 주 {n(rL)}→{n(rL / 2)}개로 줄이면", {"L": rL / 2, "S": rS}, 0.0, 1.0))
    if rS > 0:
        out.append(("S/2", f"쇼츠를 주 {n(rS)}→{n(rS / 2)}개로 줄이면", {"L": rL, "S": rS / 2}, 0.0, 1.0))
    out.append(("u+", "제목·썸네일·기획을 다듬어 영상마다 조회수가 1.6배 나오면", {"L": rL, "S": rS}, 0.5, 1.0))
    out.append(("c+", "구독 권유·재생목록으로 구독 전환이 1.5배가 되면", {"L": rL, "S": rS}, 0.0, 1.5))
    return out


def change_text(lead, lab, p, q):
    """'계획을 바꾸면' 한 줄: 두 끝을 각각 반올림한 값으로 차이를 셈 → '약 70% → 약 70% (−5%p)' 같은 앞뒤 안 맞는 글이 없게.
    차이가 3%p 안쪽이거나 반올림한 두 끝이 같으면 '거의 차이 없어요'. → (글, 반올림한 차이 %p)."""
    a, b = _ptext(p), _ptext(q)
    if abs(q - p) < 0.03 or a == b:
        return f"{lead}: {lab} {a} → 거의 차이 없어요", 0
    d = round5(q) - round5(p)
    return f"{lead}: {lab} {a} → {b} ({'+' if d > 0 else '−'}{abs(d)}%포인트 안팎)", d


def _sensitivity(cal, own, plan, dr, base, goal, inflate, g0, weeks, P0=None, conv=None):
    """conv: 쇼츠 전환이 데이터로 정해지지 않았을 때 {"hi": 전환을 위 끝으로 둔 기본 시뮬레이션} → 쇼츠 줄에 '전환에 따라 달라요'."""
    if not goal:
        return []
    p12, p6 = _p_goal(base, goal, H12), _p_goal(base, goal, H6)
    out = []
    lab = f"{_subs_label(goal)}(12개월)"
    for vid, lead, pl, us, cm in variants(plan):
        sim = simulate(cal, own, pl, dr, inflate=inflate, g0=g0, u_shift=us, conv_mult=cm, weeks=weeks, P=P0)
        q12, q6 = _p_goal(sim, goal, H12), _p_goal(sim, goal, H6)
        text, d12 = change_text(lead, lab, p12, q12)
        row = {"id": vid, "text": text, "plan": pl, "d12": d12, "d6": round5(q6) - round5(p6), "raw12": round(q12 - p12, 4), "small": d12 == 0}
        if conv and vid in ("S+2", "S/2"):
            hi = simulate(cal, own, pl, dr, inflate=inflate, g0=g0, weeks=weeks, P=P0, cS_fix=CS_RANGE[1])
            dhi = _p_goal(hi, goal, H12) - _p_goal(conv["hi"], goal, H12)
            if abs(dhi - (q12 - p12)) >= 0.05:
                row["conv"] = f"쇼츠 구독 전환이 높은 쪽이라면 {'+' if dhi >= 0 else '−'}{abs(round5(dhi))}%포인트 안팎 (전환을 데이터로 정하지 못했어요)"
        out.append(row)
    out.sort(key=lambda x: -abs(x["raw12"]))
    return out


def _per_k(c):  # 1,000회당 명
    x = c * 1000
    return "0.1명 미만" if x < 0.1 else f"{x:.2f}".rstrip("0").rstrip(".") + "명" if x < 1 else f"{x:.1f}".rstrip("0").rstrip(".") + "명"


def own_evidence(own):
    """우리 영상 근거 {L: {n, w}, S: {n, w}} (날짜를 아는 최근 2년 영상 · 1년 넘으면 무게 0.5)."""
    out = {}
    for f in FMTS:
        u = own_used(own, f)
        out[f] = {"n": len(u), "w": round(sum(w for _i, w, _v in u), 2)}
    return out


def assumptions(cal, own, plan, peers_n, inflate, pace=None, conv_hit=False):
    L, S = cal["L"], cal["S"]
    ev = own_evidence(own)
    spread = math.exp(min(L["sigma"], S["sigma"])), math.exp(max(L["sigma"], S["sigma"]))
    e_drop = round(100 * (1 - 2 ** L["e"]))
    r = _rates(plan)
    out = [
        f"영상 조회수는 같은 규모 비교 채널 {peers_n}곳의 보통 조회수에서 출발해, 우리 영상으로 조정해요. "
        f"날짜를 아는 최근 2년 영상 중 롱폼 {ev['L']['n']}편 · 쇼츠 {ev['S']['n']}편만 썼어요(1년 넘은 영상은 반만 셈 · 많아도 8편분까지). "
        "편 수가 적으면 한두 편에 크게 흔들려요.",
        f"같은 채널 안에서도 한 편 한 편은 보통 {spread[0]:.1f}~{spread[1]:.1f}배씩 위아래로 흔들려요.",
        f"구독은 롱폼 1,000회당 약 {_per_k(cal['cL'])}, 쇼츠 1,000회당 약 {_per_k(cal['cS'])}으로 봐요"
        + (f"(비교 채널 {cal['conv_n']}곳의 전체 조회수·구독자로 맞춤" if not cal["conv_defaults"] else "(맞출 채널이 부족해 2026-10-07 기본값")
        + " · 쇼츠는 다시 보기도 조회로 세어 숫자가 커요). "
        + f"우리 채널은 비교 채널끼리 차이만큼(약 {math.exp(cal.get('conv_sd', CONV_SD)):.1f}배 안팎) 위아래로 다를 수 있다고 봤어요."
        + (f" 쇼츠 전환은 데이터로 정해지지 않아서(맞춘 값이 정해 둔 범위 끝에 닿음) 1,000회당 {_per_k(CS_RANGE[0])}~{_per_k(CS_RANGE[1])} 사이 어디든 될 수 있어요."
           if conv_hit else ""),
        f"많이 올려도 영상 질은 같다고 보되, 롱폼을 비교 채널 평균(주 {math.exp(L.get('lc0', DEFAULTS['L']['lc0'])):.1f}개)의 2배로 올리면 한 편 조회가 약 {max(0, e_drop)}% 줄어드는 것으로 넣었어요."
        if e_drop > 0 else "많이 올려도 영상 질은 같다고 봤어요 (한 편 조회가 줄어드는 정도는 데이터로 확인되지 않아 넣지 않았어요).",
        "조회수는 롱폼 약 3주, 쇼츠 약 1주 안에 대부분 나와요.",
        "예전 영상에서 오는 구독과 구독 취소는 점검 기록이 쌓이기 전까지 넣지 않았어요." if not own.get("g0") else
        f"올리지 않아도 주마다 약 {own.get('g0'):.0f}명씩 느는 것으로 넣었어요 (점검 기록에서 계산).",
        f"구독자가 늘면 새 영상 조회도 조금씩 늘어나요 (구독자 2배 → 롱폼 약 {2 ** L['b']:.1f}배 · 쇼츠 약 {2 ** S['b']:.1f}배).",
        f"모든 %는 계획대로 매주 올린다고 본 값이에요 (롱폼 주 {r['L']:g}개 · 쇼츠 주 {r['S']:g}개)."
        + (f" 지금 속도(롱폼 주 {pace['L']:g}개 · 쇼츠 주 {pace['S']:g}개)대로면 달라요 — '지금 속도대로면' 줄을 보세요." if pace else "")
        + " 점검하면 실제 올린 수를 함께 보여 줘요.",
        "큰 협업·이슈·알고리즘 변화 같은 뜻밖의 일은 넣지 않았어요.",
    ]
    if inflate > 1:
        out.append(f"지난 기록으로 거꾸로 시험해 보니 범위가 좁게 나와서, 흔들림을 {inflate:g}배로 넓혔어요 (관측 보정).")
    return out


def video_reliability(peers, cal):
    """영상 단위 신뢰도: 비교 채널 RSS 영상 중 목표(쇼츠 1만·롱폼 5천)를 넘은 비율 vs 모델 확률 (채널 보통 조회수 기준)."""
    out = {}
    for f in FMTS:
        g, sig = VIEW_GOALS[f], cal[f]["sigma"]
        pred = obs = n = 0
        for p in peers:
            med = _median(_valid_views(p.get(f)))
            if not med:
                continue
            for v in p.get("rss" + f) or []:
                vv = _valid_views([v])
                if not vv:
                    continue
                pred += 1 - _phi((math.log(g) - math.log(med)) / sig)
                obs += vv[0] >= g
                n += 1
        out[f] = {"n": n, "pred": round(100 * pred / n) if n else None, "obs": round(100 * obs / n) if n else None}
    return out


def backtest(peers, cal, sims=400, seed=SEED):
    """기록 거꾸로 시험: 기록이 4주 넘게 떨어져 2개 이상 있는 비교 채널마다, 앞 기록에서 시뮬레이션해 뒤 기록 구독자가
    P10~P90 띠에 드는지 → {n, hit, inflate}. 적중이 70% 아래면 적중 80% 가 되는 가장 작은 퍼짐 배수(1~2)."""
    np = _np()
    cases = []
    for p in peers:
        h = sorted([x for x in p.get("history") or [] if isinstance(x.get("subs"), (int, float)) and isinstance(x.get("at"), (int, float))], key=lambda x: x["at"])
        if len(h) < 2 or h[-1]["at"] - h[0]["at"] < 28 * 86400:
            continue
        cases.append((p, h[0], h[-1]))
    if len(cases) < BACKTEST_MIN:
        return {"n": len(cases), "hit": None, "inflate": 1.0, "enough": False}
    rng = np.random.default_rng(seed + 7)

    def cover(infl):
        hit = 0
        for p, h0, h1 in cases:
            weeks = int((h1["at"] - h0["at"]) // (7 * 86400))
            s0 = float(h0["subs"])
            S = np.full(sims, s0)
            R = {f: np.zeros(sims) for f in FMTS}
            mc = np.exp(cal.get("conv_sd", CONV_SD) * infl * rng.standard_normal(sims))
            for t in range(weeks):
                for f in FMTS:
                    r = _clamp(float((p.get("cad") or {}).get(f) or 0), 0, MAX_RATE)
                    med = _median(_valid_views(p.get(f)))
                    n0, n1 = _n_videos(r, t), _n_videos(r, t + 1)
                    if not med:
                        continue
                    for _ in range(n0, n1):
                        R[f] += med * np.exp(cal[f]["sigma"] * rng.standard_normal(sims))
                gain = 0
                for f, c in (("L", cal["cL"]), ("S", cal["cS"])):
                    real = R[f] * (1 - math.exp(-1 / ACCRUE[f]))
                    R[f] -= real
                    gain = gain + c * real
                S = S + mc * gain
            lo, hi = np.percentile(S, 10), np.percentile(S, 90)
            hit += lo <= h1["subs"] <= hi
        return hit / len(cases)
    base = cover(1.0)
    infl, after = 1.0, base
    if base < 0.7:
        for x in INFLATE_GRID[1:]:
            infl = x
            after = cover(x)
            if after >= 0.8:
                break
    return {"n": len(cases), "hit": round(100 * base), "inflate": infl, "enough": True, "hitAfter": round(100 * after)}


def inputs_hash(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]


def _goal_show(sim, g, wk, why=None):
    np = _np()
    pb = (sim["traj"][wk] >= g).mean(axis=1)
    return show(float(pb.mean()), float(np.percentile(pb, 10)), float(np.percentile(pb, 90)), why)


def _widen(m, vals, note):
    """우리 영상 한 편씩 빼고 다시 계산한 확률(vals)까지 범위를 넓힘 (근거가 적을 때 한 편에 얼마나 흔들리는지 보이게)."""
    if not vals:
        return
    lo, hi = round5(min(vals)), round5(max(vals))
    m["lo"], m["hi"] = min(m["lo"], lo), max(m["hi"], hi)
    m["loo"] = (m["loo"] + " · " if m.get("loo") else "") + f"{note} {lo}~{hi}%"  # 롱폼·쇼츠 둘 다 적으면 함께
    _set_conf(m)


def run(peers, own, plan, goals=None, group="풋살 특화", boot=B, sims=M, weeks=W, seed=SEED, pace=None, plans=None):
    """가능성 전체 계산 → 화면에 그대로 쓰는 dict.
    own: {subs, videos:[{k:'L'|'S', v, age(일)|None}], life:{L,S,complete,nL}, g0}
    plan: {L: 롱폼 주당 개수, S: 쇼츠 주당 개수} · goals: {m6, m12} (선택)
    pace: 지금 실제로 올리는 속도 {L, S} (계획과 다르면 '지금 속도대로면' 확률을 따로) · plans: {이름: {L, S}} 방향 비교용 (같은 운으로)."""
    _np()
    peers = [p for p in peers or [] if isinstance(p, dict)]
    cal = calibrate(peers, group, boot=boot, seed=seed)
    bt = backtest(peers, cal, seed=seed)
    inflate = bt["inflate"]
    g0 = max(0.0, float(own.get("g0") or 0))
    rates = _rates(plan)
    pace_r = _rates(pace) if isinstance(pace, dict) else None
    if pace_r and abs(pace_r["L"] - rates["L"]) + abs(pace_r["S"] - rates["S"]) < PACE_DIFF:
        pace_r = None
    plan_rs = {k: _rates(v) for k, v in (plans or {}).items() if isinstance(v, dict)}
    mx = {f: max([rates[f] + (2 if f == "S" else 1)] + [r[f] for r in plan_rs.values()] + ([pace_r[f]] if pace_r else [])) for f in FMTS}
    dr = draws(boot, sims, mx, seed=seed)
    P0 = _params(cal, own, inflate)
    base = simulate(cal, own, rates, dr, inflate=inflate, g0=g0, weeks=weeks, P=P0)
    goal = _rep_goal(own, goals)
    s0 = float(own.get("subs") or 0)
    ev = own_evidence(own)
    ms = _milestones(base, own, rates, goals)
    conv_r = _conv_range(cal)
    conv_hit = not cal["conv_defaults"] and (conv_r["cS"][0] <= CS_RANGE[0] * 1.05 or conv_r["cS"][1] >= CS_RANGE[1] * 0.95)
    # ---- 믿을 만함: 범위 폭만이 아니라 근거 (BR-015) ----
    glob = []
    if not peers:
        glob.append("비교 자료가 없어 기본값으로 계산했어요")
    elif any(rates[f] > 0 and cal[f]["n"] < MIN_REG for f in FMTS):
        glob.append("비교할 채널이 적어요")
    if s0 < SUBS_FLOOR:
        glob.append("구독자가 아주 적은 새 채널이라 비교가 어려워요")
    if bt.get("enough") and (bt.get("hitAfter") if bt.get("hitAfter") is not None else bt.get("hit") or 0) < 60:
        glob.append("지난 기록으로 거꾸로 시험해 보니 잘 맞지 않았어요")
    lab = {"L": "롱폼", "S": "쇼츠"}
    own_why = {f: (f"우리 {lab[f]} 근거가 {ev[f]['n']}편뿐이에요" if ev[f]["n"] else f"우리 {lab[f]} 영상 근거가 아직 없어요")
               for f in FMTS if ev[f]["w"] < OWN_MIN_W}
    conv_sims = None
    if conv_hit and rates["S"] > 0:
        conv_sims = {"lo": simulate(cal, own, rates, dr, inflate=inflate, g0=g0, weeks=weeks, P=P0, cS_fix=CS_RANGE[0]),
                     "hi": simulate(cal, own, rates, dr, inflate=inflate, g0=g0, weeks=weeks, P=P0, cS_fix=CS_RANGE[1])}
    for m in ms:
        if m.get("none") or m.get("achieved"):
            continue
        why = list(glob)
        if m["kind"] == "subs":
            if cal["conv_defaults"] and peers:
                why.append("구독 전환을 맞출 채널이 부족해 기본값을 썼어요")
            why += [own_why[f] for f in FMTS if f in own_why and rates[f] > 0]
            if conv_sims:
                wk = H6 if m["horizon"] == 6 else H12
                plo, phi = _p_goal(conv_sims["lo"], m["target"], wk), _p_goal(conv_sims["hi"], m["target"], wk)
                if phi - plo >= CONV_SENS:
                    m["convSensitive"] = True
                    m["conv"] = f"쇼츠 구독 전환에 따라 {round5(plo)}~{round5(phi)}%"
                    why.append("쇼츠 구독 전환을 데이터로 정하지 못했어요")
        else:
            if m["fmt"] in own_why:
                why.append(own_why[m["fmt"]])
        m["why"] = why
        _set_conf(m)
    # ---- 우리 영상 한 편씩 빼 보기 (근거가 LOO_MAX 편 이하일 때) ----
    for f in FMTS:
        used = own_used(own, f)
        if not (1 <= len(used) <= LOO_MAX) or rates[f] <= 0:
            continue
        p12, p6, pv = [], [], []
        for idx, _w, _v in used:
            own2 = dict(own, videos=[v for j, v in enumerate(own.get("videos") or []) if j != idx])
            sim2 = simulate(cal, own2, rates, dr, inflate=inflate, g0=g0, weeks=weeks)
            if goal:
                p12.append(_p_goal(sim2, goal, H12))
                p6.append(_p_goal(sim2, goal, H6))
            if sim2["avg"][f] is not None:
                pv.append(float((sim2["avg"][f] >= VIEW_GOALS[f]).mean()))
        note = f"우리 {lab[f]} 한 편을 빼고 계산하면"
        for m in ms:
            if m.get("achieved") or m.get("none"):
                continue
            if m["kind"] == "subs" and goal and m["target"] == goal and not m.get("user"):
                _widen(m, p12 if m["horizon"] == 12 else p6, note)
            elif m["kind"] == "views" and m["fmt"] == f:
                _widen(m, pv, note)
    out = {
        "milestones": ms,
        "trajectory": _trajectory(base),
        "kpi": _kpi(base),
        "subs6": _subs_range(base, min(H6, weeks - 1)),
        "subs12": _subs_range(base, min(H12, weeks - 1)),
        "goal": goal,
        "sensitivity": _sensitivity(cal, own, rates, dr, base, goal, inflate, g0, weeks, P0, conv_sims),
        "assumptions": assumptions(cal, own, rates, cal["L"]["n"] or cal["S"]["n"] or len(peers), inflate, pace_r, conv_hit),
        "plan": rates,
        "own": ev,
        "trustWhy": glob,
        "calibration": {
            "n_peers": len(peers), "nL": cal["L"]["n"], "nS": cal["S"]["n"],
            "loo": {f: {"hit": cal[f]["loo"][0], "n": cal[f]["loo"][1],
                        "pct": round(100 * cal[f]["loo"][0] / cal[f]["loo"][1]) if cal[f]["loo"][1] else None} for f in FMTS},
            "fit": {f: {k: round(cal[f][k], 3) for k in ("a", "b", "tau", "sigma", "e", "adj", "lc0")} for f in FMTS},
            "e_raw": {f: (round(cal[f]["e_raw"], 2) if cal[f]["e_raw"] is not None else None) for f in FMTS},
            "cL": round(cal["cL"], 5), "cS": round(cal["cS"], 6), "conv_n": cal["conv_n"], "conv_sd": round(cal.get("conv_sd", CONV_SD), 2),
            "conv_range": conv_r, "convHit": conv_hit, "cS_bounds": list(CS_RANGE), "cL_bounds": list(CL_RANGE),
            "defaults_used": bool(cal["L"]["defaults"] or cal["S"]["defaults"] or cal["conv_defaults"]),
            "conv_defaults": cal["conv_defaults"], "inflate": inflate, "backtest": bt,
            "video": video_reliability(peers, cal),
            "boot": boot, "sims": sims,
        },
        "s0": own.get("subs") or 0,
    }
    if goal and pace_r:
        sp = simulate(cal, own, pace_r, dr, inflate=inflate, g0=g0, weeks=weeks, P=P0)
        m12 = _goal_show(sp, goal, H12, glob)
        lead = f"지금 속도(롱폼 주 {pace_r['L']:g}개 · 쇼츠 주 {pace_r['S']:g}개)대로면"
        text, d = change_text(lead, f"{_subs_label(goal)}(12개월)", _p_goal(base, goal, H12), _p_goal(sp, goal, H12))
        out["pace"] = {"rates": pace_r, "goal": goal, "m12": m12, "m6": _goal_show(sp, goal, H6, glob), "text": text, "d12": d,
                       "subs12": _subs_range(sp, min(H12, weeks - 1))}
    if goal and plan_rs:
        out["plans"] = {}
        for k, r in plan_rs.items():
            sp = simulate(cal, own, r, dr, inflate=inflate, g0=g0, weeks=weeks, P=P0)
            why = list(glob) + [own_why[f] for f in FMTS if f in own_why and r[f] > 0]
            d = {"rates": r, "goal": goal, "m12": _goal_show(sp, goal, H12, why), "m6": _goal_show(sp, goal, H6, why),
                 "subs12": _subs_range(sp, min(H12, weeks - 1))}
            if conv_hit and r["S"] > 0:
                lo = _p_goal(simulate(cal, own, r, dr, inflate=inflate, g0=g0, weeks=weeks, P=P0, cS_fix=CS_RANGE[0]), goal, H12)
                hi = _p_goal(simulate(cal, own, r, dr, inflate=inflate, g0=g0, weeks=weeks, P=P0, cS_fix=CS_RANGE[1]), goal, H12)
                if hi - lo >= CONV_SENS:
                    d["conv"] = f"쇼츠 구독 전환에 따라 {round5(lo)}~{round5(hi)}% — 주로 가정에서 나온 숫자예요"
            out["plans"][k] = d
    return out


def _conv_range(cal):
    np = _np()
    cb = cal["boot"]["c"]
    cl = [x[0] for x in cb]
    cs = [x[1] for x in cb]
    return {"cL": [round(float(np.percentile(cl, 10)), 5), round(float(np.percentile(cl, 90)), 5)],
            "cS": [round(float(np.percentile(cs, 10)), 6), round(float(np.percentile(cs, 90)), 6)]}


def brier(pairs):
    """(예측 확률 0~1, 실제 0/1) 목록 → Brier 점수 (낮을수록 좋음)."""
    pairs = [(float(p), float(o)) for p, o in pairs]
    return round(sum((p - o) ** 2 for p, o in pairs) / len(pairs), 3) if pairs else None
