r"""작업 스케줄러가 부르는 자리 — **콘솔 없이** 배치를 돌린다.

한동안 "스케줄러가 `recommend.py` 를 1초 안에 죽인다"로 남아 있던 것의 정체다.
결과 코드는 `0xC000013A`(강제 종료)뿐이었고, 같은 `.cmd` 를 셸에서 `cmd /c` 로
돌리면 멀쩡히 끝나서 몇 주를 못 잡았다. 프로세스에 콘솔 컨트롤 핸들러를 달아
무엇이 오는지 찍어 보고서야 답이 나왔다:

    15:53:11 START pid=40536
    15:53:16 tick 4
    15:53:17 !! console ctrl event: CTRL_CLOSE

**`CTRL_C` 가 아니라 `CTRL_CLOSE` 였다.** 스케줄러가 작업을 멈춘 게 아니라
작업이 붙어 있던 **콘솔 창이 닫힌** 것이다. 그래서 로그도 이벤트도 안 남는다 —
누가 죽인 게 아니라 창이 사라진 것이라서.

재 본 것(작업 하나마다 25초짜리 프로브):

| 작업이 부르는 것 | 결과 |
|---|---|
| `cmd.exe /c foo.cmd` | 1~6초 뒤 `CTRL_CLOSE`, `0xC000013A` |
| `python.exe`(콘솔 있음) | 같음 |
| `pythonw.exe`(콘솔 없음) | 25초 **전부 돈다** |
| `pythonw.exe` → `cmd.exe`(`CREATE_NO_WINDOW`) | 25초 **전부 돈다** |

닫힐 창이 없으면 닫히지도 않는다. 그래서 창을 이기려 하지 않고 **없앤다** —
`.cmd` 의 인코딩을 `chcp` 로 이기려다 실패했을 때와 같은 답이다.

작업의 동작을 이것으로 바꾼다(순서는 그대로 `.cmd` 안에 있다):

    C:\...\.venv\Scripts\pythonw.exe  scripts\runjob.py  scripts\daily.cmd  6

**여기서 `print` 를 쓰지 말 것.** `pythonw` 에는 stdout 이 없어서 `sys.stdout` 이
`None` 이고, 한 줄만 찍어도 `AttributeError` 로 죽는다 — 그것도 아무 데도 안 남는다.
할 말은 로그 파일에 적는다.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

# 콘솔 창을 아예 안 만든다. `pythonw` 자신은 콘솔이 없지만, 자식으로 띄우는
# `cmd.exe` 는 이 플래그가 없으면 자기 창을 하나 만든다 — 그러면 원점이다.
CREATE_NO_WINDOW = 0x08000000

ROOT = Path(__file__).resolve().parents[1]
FAILED = ROOT / "logs" / "runjob.log"


def complain(message: str) -> None:
    """말할 데가 여기뿐이다. stdout 이 없으므로 파일에 적는다."""
    try:
        FAILED.parent.mkdir(parents=True, exist_ok=True)
        with FAILED.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        complain("부를 배치 파일을 안 넘겼다")
        return 2

    batch = Path(argv[1])
    if not batch.is_absolute():
        batch = ROOT / batch
    if not batch.is_file():
        complain(f"배치 파일이 없다: {batch}")
        return 2

    return subprocess.run(["cmd.exe", "/c", str(batch), *argv[2:]],
                          cwd=ROOT, creationflags=CREATE_NO_WINDOW).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
