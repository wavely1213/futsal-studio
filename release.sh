#!/bin/bash
# 새 버전 배포: ./release.sh 1.0.2 "변경 내용 한 줄"
#  0) 업데이트·실행기 테스트 (망가진 updater.py 가 나가면 사용자 PC 에서 앱이 아예 안 켜져요)
#  1) version.txt 를 올리고, 파일마다 지문(sha256)을 적은 manifest.json 과 함께 커밋 → 이 커밋이 배포본
#     (예전 버전 PC 는 zip 속 manifest.json 을 그대로 가져가므로, 여기에도 파일 목록이 있어야 다음 업데이트 때 없어진 파일을 지워요)
#  2) 그 커밋에 고정된 zip 주소를 manifest.json 에 더해 두 번째 커밋 → 푸시
#  사용자 PC 는 zip 을 받아 지문을 하나하나 맞춰 본 뒤에만 설치해요 (배포 뒤 main 에 커밋이 더 쌓여도 안전).
#  GitHub 릴리스·태그 권한은 필요 없어요 (zip 은 github.com/…/archive/<커밋>.zip).
#  커밋 메시지 끝에 붙일 줄이 있으면 RELEASE_TRAILER 환경 변수로 넘겨요.
set -euo pipefail
cd "$(dirname "$0")"
VER="${1:-}"; NOTES="${2:-}"
[[ "$VER" =~ ^[0-9]+(\.[0-9]+)+$ ]] || { echo "사용법: ./release.sh 1.8.0 \"변경 내용 한 줄\""; exit 1; }
REPO="https://github.com/wavely1213/futsal-studio"
if [ -f tests/test_update.py ]; then
  echo "업데이트 테스트 중…"
  python3 -m unittest -q tests.test_update || { echo "업데이트 테스트가 실패해서 배포를 멈췄어요 (아무것도 커밋하지 않았어요)"; exit 1; }
fi
# manifest.json 쓰기: write_manifest <index|커밋> <버전> <설명> [zip 주소]
#  파일 목록 = 그 트리의 파일 지문 (manifest.json 자신과 tests/, 링크·하위 저장소는 뺌)
write_manifest() {
python3 - "$@" <<'PY'
import hashlib, json, subprocess, sys
src, ver, notes = sys.argv[1:4]
zip_url = sys.argv[4] if len(sys.argv) > 4 else None
git = lambda *a: subprocess.run(["git", *a], capture_output=True, check=True).stdout
recs = []
if src == "index":  # 커밋하기 전: git add 해 둔 내용 ("모드 sha 단계\t경로")
    for r in filter(None, git("ls-files", "-s", "-z").split(b"\0")):
        meta, path = r.split(b"\t", 1)
        mode, sha, _stage = meta.split()
        recs.append((mode, sha, path))
else:  # 커밋 ("모드 종류 sha\t경로")
    for r in filter(None, git("ls-tree", "-r", "-z", "--full-tree", src).split(b"\0")):
        meta, path = r.split(b"\t", 1)
        mode, kind, sha = meta.split()
        if kind == b"blob":
            recs.append((mode, sha, path))
files = {}
for mode, sha, path in recs:
    path = path.decode("utf-8")
    if mode in (b"120000", b"160000") or path == "manifest.json" or path.startswith(("tests/", "thumb_src/")):
        continue
    files[path] = hashlib.sha256(git("cat-file", "blob", sha.decode())).hexdigest()
if "version.txt" not in files or "updater.py" not in files:
    sys.exit("version.txt·updater.py 가 없어요")
m = {"version": ver, "notes": notes}
if zip_url:
    m.update(zip=zip_url, commit=src)
    old = json.load(open("manifest.json", encoding="utf-8")).get("files")
    if old != files:  # 1)에서 적은 목록과 실제 커밋이 같아야 함
        sys.exit("배포 커밋의 파일 지문이 1단계와 달라요")
m["files"] = files
with open("manifest.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(m, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(f"배포 파일 {len(files)}개 지문 기록" + (f" · {zip_url}" if zip_url else ""))
PY
}
echo "$VER" > version.txt
git add -A
write_manifest index "$VER" "$NOTES"
git add manifest.json
git commit -m "release v$VER${NOTES:+: $NOTES}" ${RELEASE_TRAILER:+-m "$RELEASE_TRAILER"}
CODE=$(git rev-parse HEAD)
write_manifest "$CODE" "$VER" "$NOTES" "$REPO/archive/$CODE.zip"
git add manifest.json
git commit -m "manifest v$VER" ${RELEASE_TRAILER:+-m "$RELEASE_TRAILER"}
git tag "v$VER" "$CODE"
git push origin main
git push origin "v$VER" || echo "(태그는 올리지 못했지만 업데이트에는 영향 없음)"
echo "배포 완료: v$VER (사용자 클라이언트에서 '업데이트' 버튼으로 받을 수 있습니다)"
