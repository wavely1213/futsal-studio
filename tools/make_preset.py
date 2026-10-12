"""미리 정해 둔 로그인(D-258) 만들기 — 개발 PC 에서만 한 번 돌리는 도구 (앱이 부르지 않음).

비밀번호는 환경 변수 FUTSAL_PRESET_PW 로만 받는다 (명령 줄·파일·기록에 남기지 않게). 아이디는 rlogin.PRESET_ID 로 고정.
출력: rlogin.py 의 `PRESET = None` 자리에 붙여 넣을 한 줄 (SRP 검증값·salt·횟수만 · 비밀번호는 들어 있지 않음).

    FUTSAL_PRESET_PW='…' python3 tools/make_preset.py

넣는 곳은 한 곳: rlogin.py 의 `PRESET = None` 줄을 출력 줄로 바꾼다 (PRESET_ID·PRESET_CHANNEL 은 그대로).
PC 에 넣는 조건은 remote.Service._apply_preset (D-258): 로그인을 정한 적 없음 · 채널 · 소유자 PC 표시 · 같은 아이디를 쓰는 PC 없음 · 한 번만.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rlogin  # noqa: E402


def make(password, iters=None):
    """비밀번호 → PRESET dict (아이디 PRESET_ID 의 검증값). 규칙에 안 맞으면 ValueError (문장은 PC 화면과 같음)."""
    probs = rlogin.pw_problems(password, rlogin.PRESET_ID)
    if probs:
        raise ValueError(rlogin.PW_MSG[probs[0]])
    v = rlogin.make_verifier(password, iters)
    return {"id": rlogin.PRESET_ID, "idShow": rlogin.PRESET_ID, "canon": rlogin.CANON, "grp": v["grp"], "salt": v["salt"],
            "iters": v["iters"], "verifier": v["verifier"]}


def main():
    pw = os.environ.get("FUTSAL_PRESET_PW")
    if not pw:
        print("FUTSAL_PRESET_PW 환경 변수에 비밀번호를 넣고 다시 돌려 주세요", file=sys.stderr)
        return 2
    try:
        p = make(pw)
    except ValueError as e:
        print(f"이 비밀번호는 쓸 수 없어요 · {e}", file=sys.stderr)
        return 1
    assert rlogin.preset_ok(p)
    print("PRESET = " + repr(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
