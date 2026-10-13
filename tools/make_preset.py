"""미리 정해 둔 로그인(D-258 · D-291) 만들기 — 개발 PC 에서만 한 번 돌리는 도구 (앱이 부르지 않음).

기본은 아이디만 방식(mode idOnly · 소유자 결정 D-290): 비밀번호가 없다 — 검증값은 아이디(rlogin.PRESET_ID)에서 나온 비밀
(rlogin.id_secret · 맞춘 아이디 그대로)로 만든다. 환경 변수도 입력도 필요 없다.

    python3 tools/make_preset.py

비밀번호를 쓰는 미리 정한 로그인(예전 D-258 방식)이 다시 필요하면 --password 를 붙이고, 비밀번호는 환경 변수 FUTSAL_PRESET_PW 로만 받는다
(명령 줄·파일·기록에 남기지 않게 · 규칙 D-253 을 거침):

    FUTSAL_PRESET_PW='…' python3 tools/make_preset.py --password

출력: rlogin.py 의 `PRESET = …` 한 줄 자리에 붙여 넣을 한 줄 (방식·SRP 검증값·salt·횟수만 · 비밀번호는 들어 있지 않음).
넣는 곳은 한 곳: rlogin.py 의 `PRESET = ` 줄 (PRESET_ID·PRESET_CHANNEL 은 그대로).
PC 에 넣는 조건은 remote.Service._apply_preset (D-258): 로그인을 정한 적 없음 · 채널 · 소유자 PC 표시 · 같은 아이디를 쓰는 PC 없음 · 한 번만.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rlogin  # noqa: E402


def make(password=None, iters=None):
    """PRESET dict (아이디 PRESET_ID). password 가 없으면 아이디만 방식(검증값은 아이디에서 나온 비밀로) ·
    있으면 그 비밀번호의 검증값 (규칙에 안 맞으면 ValueError · 문장은 PC 화면과 같음)."""
    base = {"id": rlogin.PRESET_ID, "idShow": rlogin.PRESET_ID, "canon": rlogin.CANON}
    if password is None:
        v = rlogin.make_id_verifier(rlogin.PRESET_ID, iters)
        return dict(base, mode="idOnly", grp=v["grp"], salt=v["salt"], iters=v["iters"], verifier=v["verifier"])
    probs = rlogin.pw_problems(password, rlogin.PRESET_ID)
    if probs:
        raise ValueError(rlogin.PW_MSG[probs[0]])
    v = rlogin.make_verifier(password, iters)
    return dict(base, grp=v["grp"], salt=v["salt"], iters=v["iters"], verifier=v["verifier"])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--password" in argv:
        pw = os.environ.get("FUTSAL_PRESET_PW")
        if not pw:
            print("FUTSAL_PRESET_PW 환경 변수에 비밀번호를 넣고 다시 돌려 주세요", file=sys.stderr)
            return 2
        try:
            p = make(pw)
        except ValueError as e:
            print(f"이 비밀번호는 쓸 수 없어요 · {e}", file=sys.stderr)
            return 1
    elif os.environ.get("FUTSAL_PRESET_PW"):   # 비밀번호를 넣었는데 아이디만 PRESET 이 나오면 헷갈림 → 멈춤
        print("FUTSAL_PRESET_PW 가 있어요 · 비밀번호 PRESET 이면 --password 를 붙이고, 아이디만이면 환경 변수를 지우고 다시 돌려 주세요", file=sys.stderr)
        return 2
    else:
        p = make()
        assert rlogin.preset_matches_id(p)
    assert rlogin.preset_ok(p)
    print("PRESET = " + repr(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
