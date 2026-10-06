# DOCUMENTATION_POLICY.md — 문서화 정책

> **목적**: 어떤 문서를, 언제, 어떻게 쓰고 갱신하는지 정의한다.
> 원칙: **문서는 코드 변경과 같은 작업 안에서 갱신한다.** "나중에 정리"는 없다.

---

## 1. 문서 체계

| 문서 | 성격 | 갱신 주기 |
|---|---|---|
| `AGENTS.md` | AI 지침 진입점 | 규칙 변경 시 |
| `CLAUDE.md` | `AGENTS.md`를 가리키는 포인터 — **내용 추가 금지** | 바꾸지 않음 |
| `docs/PROJECT_CONTEXT.md` | 프로젝트 맥락 | 방향·스택 변경 시 |
| `docs/ARCHITECTURE.md` | 구조 정의 | 구조 변경 시 |
| `docs/DEVELOPMENT_RULES.md` 외 규칙 문서 | 프로세스 규칙 | 규칙 변경 시 |
| `docs/DECISION_LOG.md` | 결정 기록 (append) | 결정할 때마다 |
| `docs/KNOWN_ISSUES.md` | 이슈 기록 (수시) | 발견·해결 시 |
| `docs/DOMAIN_KNOWLEDGE.md` | 도메인 지식 | 규칙·용어 추가 시 |
| `README.md` | 사용자(비개발자)용 설치·사용 안내 + 끝의 "(관리자용) 새 버전 배포 방법" | 사용법·화면·배포 절차 변경 시 |
| 모듈 머리말 docstring | 그 파일이 하는 일 (`CODING_STANDARDS.md` 4번) | 파일 역할이 바뀔 때 |

- 별도 CHANGELOG는 없다. 버전별 변경은 `./release.sh X.Y.Z "변경 내용"`의 한 줄이 남긴다. 이 줄은 배포 커밋 메시지와 `manifest.json`의 `notes`에 들어간다. `notes`는 `/api/update/check`가 돌려주지만 지금 화면에는 보이지 않는다.
- `docs/`, `AGENTS.md`, `CLAUDE.md`, `README.md`도 추적 파일이므로 **배포 때 사용자 PC로 함께 간다.** 저장소도 공개다. 문서에 시크릿·개인정보·사용자 PC 정보를 쓰지 않는다(`SECURITY_GUIDELINES.md` 1번).

## 2. 갱신 트리거 — 이 변경을 했다면 이 문서를 고친다

| 코드 변경 | 갱신할 문서 |
|---|---|
| 실행·빌드 방법 변경 | `AGENTS.md` 5번, `PROJECT_CONTEXT.md` 5절, `README.md` |
| 레이어·모듈 추가/이동 (새 `.py`·새 화면 HTML) | `ARCHITECTURE.md` |
| 기술 선택·트레이드오프 결정 | `DECISION_LOG.md` |
| 임시방편·미해결 문제 발생 | `KNOWN_ISSUES.md` |
| 새 도메인 용어·비즈니스 규칙 (YouTube 규격·글자 수, 쇼츠·챕터 규칙, 썸네일 템플릿·세이프존) | `DOMAIN_KNOWLEDGE.md` |
| 환경변수 추가 (`FUTSAL_*` 등) | `PROJECT_CONTEXT.md` 5절 (변수명만, 값 금지) |
| 사용자에게 보이는 기능·화면·단축키 변경 | `README.md` (사용자 안내) |
| 업데이트·배포 흐름 변경 (`updater.py`·`release.sh`·`manifest.json` 형식) | 배포 절차는 `DEVELOPMENT_RULES.md` 8번, 사용자 PC 쪽 흐름은 `ARCHITECTURE.md` 6절 '업데이트 흐름'(상태는 `DOMAIN_KNOWLEDGE.md` 5절), 사람용 안내는 `README.md` (관리자용). 다른 문서는 이 두 절을 링크만 한다 |
| 의존성 추가·제거 (`requirements.txt`, 처음 쓸 때 받는 모델·실행 파일, CDN) | `DEPENDENCY_POLICY.md` 6번, `PROJECT_CONTEXT.md` 3·6절 |
| 새 외부 연결·다운로드 주소, 새 API 경로·파일 입출력 | `PROJECT_CONTEXT.md` 6절, `SECURITY_GUIDELINES.md` (규칙이 바뀌면) |
| 테스트 묶음 추가·이동 (저장소 밖 e2e 포함) | `TESTING_GUIDELINES.md` 1번 (실행 방법), `AGENTS.md` 5번 (묶음 이름·`$SCRATCH` 경로) |
| `thumb.html` 원본·빌드 방식 변경 | `AGENTS.md` 5번, `ARCHITECTURE.md` 7절 |

AI는 작업 완료 보고 전에 이 표를 확인하고 해당 문서를 갱신한다.

## 3. 작성 규칙

- 언어: 한국어. 기술 용어는 원어 병기 허용.
  - `docs/`·`AGENTS.md`는 짧은 평서문(~한다)으로 쓴다.
  - `README.md`의 사용자 안내 부분은 화면 문구와 같은 쉬운 해요체로 쓴다. 개발 용어는 "(관리자용)" 절에만 쓴다.
- 간결하게 — 문서는 길수록 안 읽힌다. 접속사 남발·중복 설명 제거.
- 코드로 알 수 있는 것(파라미터 목록 등)은 문서에 중복 기술하지 않는다. 문서는 "왜"와 "어디"를 담는다. 코드 위치는 `모듈.함수`(예: `core.update_engine`)로 가리킨다.
- 날짜는 `YYYY-MM-DD` 형식.
- 문서 간 중복 금지 — 한 곳에 쓰고 나머지는 링크한다. (중복은 반드시 어긋난다)
- 저장소 밖에 있는 것(e2e 묶음, `thumb.html` 원본, `build_thumb.sh`, `backlog.json`)은 문서에 **"저장소 밖"이라고 분명히 적는다.** 실제 경로는 `AGENTS.md` 5번에만 두고, 다른 문서는 `$SCRATCH/…`로 적는다.

## 4. 코드 내 문서화

- 공개 API·복잡한 로직: 문서화 주석 — Python은 한국어 docstring, HTML 안 JS는 `//` 주석.
- 주석 규칙 상세는 `CODING_STANDARDS.md` 4번.

## 5. 문서 신뢰도 규칙 (AI용)

- 문서와 코드가 다르면 **코드가 진실**이다. 발견 시 문서를 고치고 한 줄 보고한다.
- 문서에 없는 내용을 추측으로 채워넣지 않는다. 모르는 칸은 `(작성 필요)` 또는 `<!-- TODO: 무엇이 필요한지 -->`로 두고 소유자에게 알린다.
