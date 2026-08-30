"""지난 실행이 끝을 봤나 — **관리자 권한 없이** 알아내는 방법.

작업 스케줄러가 작업을 죽이면 그 이유는 `TaskScheduler/Operational` 채널에 남는데,
그 채널은 기본으로 꺼져 있고 **켜려면 관리자 권한이 필요하다.** 그게 없으면 남는
것은 `Last Result` 숫자 하나뿐이고, 그마저도 다음 실행이 덮어쓴다.

그래서 로그 자체가 답하게 만든다. `.cmd` 가 시작할 때 `START`, 끝날 때 `END code=N`
을 적으므로 — **마지막 START 뒤에 END 가 없으면 그 실행은 끝을 못 본 것이다.**
이 스크립트가 다음 실행의 맨 앞에서 그걸 읽어 로그에 적는다. 아침에 결과가 없을 때
"언제 시작해서 못 끝냈다" 까지는 확실히 알 수 있게 된다.

작업의 마지막 종료 코드도 같이 적는다(`schtasks /query`). 이것도 관리자가 필요 없다.

    python -u scripts/runcheck.py logs/daily.log market-lens-daily
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# **로그는 UTF-8 이다.** 리다이렉트되면 stdout 은 기본이 시스템 코드페이지(cp949)라
# `—` 하나에 `UnicodeEncodeError` 로 죽는다. 실제로 그렇게 죽었고, `.cmd` 는
# `@echo off` 로 도는 중이라 그 사실이 다음 줄에 조용히 묻혔다.
# `recommend.py`·`backfill.py` 도 같은 이유로 첫머리에서 이걸 한다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# `.cmd` 가 적는 표시. 영문인 이유는 cmd.exe 의 echo 가 OEM 코드페이지로 쓰기
# 때문이다 — 한글을 넣으면 파이썬이 UTF-8 로 쓰는 같은 파일에서 깨진다.
START = re.compile(r"^=+ (.+?) START\s*$")
END = re.compile(r"^=+ (.+?) END code=(-?\d+)\s*$")


def last_run(text: str) -> tuple[str, str | None] | None:
    """(마지막 실행이 시작한 시각, 끝난 코드). 아직 안 끝났으면 코드가 None.

    표시가 하나도 없으면 `None` — 이 기능이 들어오기 전의 로그다. 그때는 아무 말도
    안 한다. **모르는 것을 죽었다고 적으면 그게 더 나쁘다.**
    """
    started: str | None = None
    code: str | None = None
    for line in text.splitlines():
        if (m := START.match(line)):
            started, code = m.group(1).strip(), None
        elif (m := END.match(line)):
            code = m.group(2)
    return None if started is None else (started, code)


def task_result(name: str) -> str | None:
    """작업의 마지막 종료 코드. 관리자 권한이 필요 없다 — 이벤트 로그가 아니라
    작업 자체의 상태를 읽는 것이다."""
    try:
        out = subprocess.run(["schtasks", "/query", "/tn", name, "/v", "/fo", "LIST"],
                             capture_output=True, text=True, timeout=20,
                             encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        # 로케일마다 라벨이 다르다. 값이 숫자인 줄만 본다.
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        if "result" in label.lower() or "결과" in label:
            value = value.strip()
            if value.lstrip("-").isdigit():
                return value
    return None


def main() -> None:
    if len(sys.argv) < 2:
        return
    path = Path(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    found = last_run(text)
    if found is None:
        return
    started, code = found
    if code is not None:
        return                                    # 지난 실행은 제대로 끝났다

    # **끝 표시가 없다 = 그 실행은 죽었다.** 재부팅·스케줄러의 강제 종료·전원이
    # 대부분이고, 어느 쪽이든 그날치 결과가 없다는 뜻이다.
    result = task_result(name) if name else None
    tail = f" (작업 마지막 결과 {result})" if result else ""
    print(f"!! 지난 실행({started})이 끝을 못 봤다{tail} — 그날치 결과가 없다")


if __name__ == "__main__":
    main()
