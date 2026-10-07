"""썸네일 편집기 thumb.html 만들기: python3 thumb_src/build.py  (head.html + parts/*.js 를 이어 붙임 · thumb.html 은 직접 고치지 않음)"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
head = (HERE / "head.html").read_text(encoding="utf-8")
parts = [(HERE / "parts" / n).read_text(encoding="utf-8") for n in sorted(os.listdir(HERE / "parts")) if n.endswith(".js")]
out = head + "".join(parts) + "</script>\n</body>\n</html>\n"
(ROOT / "thumb.html").write_text(out, encoding="utf-8", newline="\n")
js = out[out.rindex("<script>") + 8:out.rindex("</script>")]
try:
    r = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0,'utf8'));console.log('syntax ok')"], input=js, text=True, encoding="utf-8", capture_output=True)
    print(r.stdout.strip() or r.stderr.strip())
    sys.exit(0 if r.returncode == 0 else 1)
except FileNotFoundError:
    print("node 없음 · 문법 확인 건너뜀")
