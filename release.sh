#!/bin/bash
# 새 버전 배포: ./release.sh 1.0.2 "변경 내용 한 줄"
set -euo pipefail
cd "$(dirname "$0")"
VER="$1"; NOTES="${2:-}"
echo "$VER" > version.txt
python3 - "$VER" "$NOTES" <<'PY'
import json, sys
ver, notes = sys.argv[1], sys.argv[2]
json.dump({"version": ver, "notes": notes,
           "zip": "https://github.com/wavely1213/futsal-studio/archive/refs/heads/main.zip"},
          open("manifest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
git add -A
git commit -m "release v$VER${NOTES:+: $NOTES}"
git tag "v$VER"
git push origin main
git push origin "v$VER" || echo "(태그는 올리지 못했지만 업데이트에는 영향 없음)"
echo "배포 완료: v$VER (사용자 클라이언트에서 '업데이트' 버튼으로 받을 수 있습니다)"
