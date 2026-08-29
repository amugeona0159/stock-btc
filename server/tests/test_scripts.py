"""작업 스케줄러가 부르는 배치 파일.

**`.cmd` 는 CRLF 여야 한다.** LF 로 저장하면 `cmd.exe` 가 줄을 제대로 못 끊어
`REM` 주석의 한글이 명령으로 해석된다 — 실제로 그랬다:

    '07:30' is not recognized as an internal or external command

작업 스케줄러는 그걸 `Last Result: 1` 한 줄로만 알려 주고 **로그 파일조차 안 만든다.**
아침에 추천이 안 나오는데 이유를 알 길이 없는 종류의 고장이라 여기서 막는다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
BATCH = sorted(SCRIPTS.glob("*.cmd"))


def test_there_are_batch_files():
    """이 파일 전체가 무의미해지지 않게. 배치가 사라졌으면 알아야 한다."""
    assert BATCH, "scripts/*.cmd 가 하나도 없다"


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_batch_files_use_windows_line_endings(path: Path):
    raw = path.read_bytes()
    lone = raw.replace(b"\r\n", b"").count(b"\n")
    assert lone == 0, f"{path.name}: LF 만인 줄이 {lone}개 — cmd.exe 가 못 읽는다"


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_batch_files_run_from_anywhere(path: Path):
    """작업 스케줄러는 작업 디렉터리를 안 정해 준다. 스크립트가 스스로 옮겨야 한다."""
    body = path.read_text(encoding="utf-8")
    assert 'cd /d "%~dp0.."' in body, f"{path.name}: 저장소 뿌리로 안 옮긴다"


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_batch_files_write_a_log(path: Path):
    """터졌을 때 볼 게 있어야 한다. 스케줄러는 `Last Result` 숫자 하나만 남긴다."""
    body = path.read_text(encoding="utf-8")
    assert "logs\\" in body, f"{path.name}: 로그를 안 남긴다"
    assert "2>&1" in body, f"{path.name}: 오류를 로그로 안 보낸다"


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_batch_files_keep_their_own_learning_folder(path: Path):
    """이 PC 는 `learning-local/` 에 쓴다. 저장소의 `learning/` 은 Actions 것이라
    같이 쓰면 매일 아침 pull 이 충돌한다."""
    body = path.read_text(encoding="utf-8")
    assert "MARKET_LENS_LEARNING=learning-local" in body, path.name


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_batch_files_run_python_unbuffered(path: Path):
    """**버퍼를 켜 두면 죽은 실행이 로그를 한 줄도 안 남긴다.**

    실제로 그랬다 — 06:00 학습이 3분을 돌다 재부팅에 끊겼는데, 로그에는 그 3분이
    통째로 없었다. 스케줄러는 결과 코드 하나만 알려 주고 그 기록조차 기본으로
    꺼져 있어서, 아침에 결과가 없는데 왜 없는지 볼 데가 아무 데도 없었다.
    """
    body = path.read_text(encoding="utf-8")
    for line in body.splitlines():
        if "python.exe" not in line:
            continue
        assert '" -u ' in line, f"{path.name}: -u 가 없다 — 끊기면 로그가 빈다: {line}"


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_batch_files_stamp_the_run(path: Path):
    """언제 시작해 어떤 코드로 끝났는지. **끝 표시가 없으면 그게 곧 죽었다는 뜻**이라,
    로그만 보고도 중간에 끊긴 실행을 알아볼 수 있다."""
    body = path.read_text(encoding="utf-8")
    assert "%DATE% %TIME% START" in body, f"{path.name}: 시작을 안 남긴다"
    assert "%ERRORLEVEL%" in body, f"{path.name}: 끝난 코드를 안 남긴다"
    # cmd.exe 의 echo 는 OEM 코드페이지로 쓴다. 파이썬이 UTF-8 로 쓰는 같은 파일에
    # 한글을 넣으면 두 인코딩이 섞여 깨진다 — 실제로 깨졌다.
    for line in body.splitlines():
        if line.startswith("echo ") and ">>" in line:
            assert line.isascii(), f"{path.name}: 표시 줄에 한글이 있다 — 로그가 깨진다"
