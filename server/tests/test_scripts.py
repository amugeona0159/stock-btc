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
