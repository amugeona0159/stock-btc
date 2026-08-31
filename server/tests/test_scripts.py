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


# --- 죽은 실행을 다음 실행이 알려 준다 -----------------------------------

def _runcheck():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("runcheck", SCRIPTS / "runcheck.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["runcheck"] = module
    spec.loader.exec_module(module)
    return module


def test_a_finished_run_says_nothing():
    """정상 종료에 경고를 붙이면 그 경고는 곧 무시된다."""
    check = _runcheck()
    log = "===== 2026-08-30 07:30 START" + chr(10) + "일 함" + chr(10)         + "===== 2026-08-30 07:38 END code=0"
    assert check.last_run(log) == ("2026-08-30 07:30", "0")


def test_a_killed_run_is_visible_next_time():
    """**끝 표시가 없으면 그 실행은 죽은 것이다.** 스케줄러 진단 기록은 관리자
    권한이 있어야 켜지므로, 로그 자체가 답하게 만든다."""
    check = _runcheck()
    log = ("===== 8/29 06:00 START" + chr(10) + "===== 8/29 06:20 END code=0" + chr(10)
           + "===== 8/30 06:00 START" + chr(10) + "세 줄 쓰다 말았다")
    started, code = check.last_run(log)
    assert started == "8/30 06:00" and code is None


def test_a_log_without_markers_says_nothing():
    """이 기능이 들어오기 전의 로그다. **모르는 것을 죽었다고 적으면 더 나쁘다.**"""
    assert _runcheck().last_run("아침 추천 · 2026-08-29" + chr(10) + "얼렸다") is None


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_the_check_runs_before_this_run_stamps_its_start(path: Path):
    """뒤에 두면 **자기 START 를 보고** 매번 '지난 실행이 죽었다'고 적는다."""
    body = path.read_text(encoding="utf-8")
    assert "runcheck.py" in body, f"{path.name}: 지난 실행을 안 본다"
    lines = [i for i, l in enumerate(body.splitlines()) if "runcheck.py" in l]
    stamps = [i for i, l in enumerate(body.splitlines()) if "%TIME% START" in l]
    assert lines and stamps and lines[0] < stamps[0],         f"{path.name}: runcheck 가 START 뒤에 있다"


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_every_script_a_batch_file_calls_writes_utf8(path: Path):
    """**로그는 UTF-8 이다.** 리다이렉트되면 파이썬 stdout 은 기본이 시스템
    코드페이지(cp949)라 `—` 하나에 `UnicodeEncodeError` 로 죽는다. `.cmd` 는
    `@echo off` 로 도니 그 사실이 다음 줄에 조용히 묻힌다 — 실제로 그렇게 묻혔다.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if "python.exe" not in line or "scripts" + chr(92) not in line:
            continue
        name = line.split("scripts" + chr(92))[1].split()[0]
        called = SCRIPTS / name
        if not called.is_file():
            continue
        body = called.read_text(encoding="utf-8")
        assert 'sys.stdout.reconfigure(encoding="utf-8"' in body,             f"{name}: stdout 을 UTF-8 로 안 맞춘다 — 한글 한 자에 죽는다"


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_batch_files_are_ascii_only(path: Path):
    """**`.cmd` 에는 한글을 쓰지 않는다.**

    cmd.exe 는 배치 파일을 OEM 코드페이지(949)로 읽는데 이 저장소는 UTF-8 로 쓴다.
    그래서 한글 `REM` 줄은 통째로 깨져 읽히고, 깨진 조각이 어쩌다 명령처럼 보이면
    cmd 가 그걸 실행하려 든다 — 실제로 두 번 났다:

        '07:30' is not recognized as an internal or external command
        '???글을' is not recognized as an internal or external command

    `chcp 65001` 로 덮어 보려 했지만 그래도 샜다. 파서를 이기려 하는 대신 파서가
    헷갈릴 거리를 없앤다. **이유는 CLAUDE.md 에 적는다** — 거기가 실제로 읽히는
    자리이기도 하다.

    로그 인코딩도 같은 문제다. `echo` 는 OEM 으로 쓰고 파이썬은 UTF-8 로 쓰므로,
    표시 줄에 한글을 넣으면 한 파일에 두 인코딩이 섞인다.
    """
    body = path.read_text(encoding="utf-8")
    bad = [(i, line) for i, line in enumerate(body.splitlines(), 1)
           if not line.isascii()]
    assert not bad, (f"{path.name}: ASCII 가 아닌 줄 {len(bad)}개 — "
                     f"첫 줄 {bad[0][0]}: {bad[0][1][:60]}")


# --- 콘솔이 없어야 끝까지 돈다 --------------------------------------------

LAUNCHER = SCRIPTS / "runjob.py"


def test_the_launcher_exists():
    """작업 스케줄러는 이것을 부른다. 없어지면 `.cmd` 가 다시 콘솔에 붙는다."""
    assert LAUNCHER.is_file(), "scripts/runjob.py 가 없다"


def test_the_launcher_opens_no_console():
    """**콘솔 창이 있으면 그 창이 닫히면서 작업이 죽는다.**

    스케줄러가 띄운 콘솔은 시작 1~6초 뒤 사라지고, 거기 붙어 있던 프로세스는
    `CTRL_CLOSE` 를 받아 `0xC000013A` 로 끝난다. 콘솔 컨트롤 핸들러를 달아
    직접 찍어 본 것이라 추측이 아니다. `pythonw` 는 자기 콘솔이 없지만 자식
    `cmd.exe` 는 플래그가 없으면 창을 하나 만든다 — 그러면 원점이다.
    """
    body = LAUNCHER.read_text(encoding="utf-8")
    assert "CREATE_NO_WINDOW = 0x08000000" in body, "콘솔 없이 띄우는 플래그가 없다"
    assert "creationflags=CREATE_NO_WINDOW" in body, "플래그를 안 넘긴다"


def test_the_launcher_never_prints():
    """**`pythonw` 에는 stdout 이 없다.** `sys.stdout` 이 `None` 이라 한 줄만
    찍어도 `AttributeError` 로 죽고, 그 사실조차 아무 데도 안 남는다."""
    body = LAUNCHER.read_text(encoding="utf-8")
    bad = [i for i, line in enumerate(body.splitlines(), 1)
           if line.lstrip().startswith("print(")]
    assert not bad, f"runjob.py {bad} 번 줄에 print — pythonw 에서는 죽는다"


def test_the_launcher_leaves_a_note_when_it_cannot_start():
    """부를 것을 못 찾으면 그것도 어딘가에 적혀야 한다. 스케줄러가 남기는 것은
    결과 코드 하나뿐이라, 안 적으면 아침에 결과가 없는 이유를 볼 데가 없다."""
    body = LAUNCHER.read_text(encoding="utf-8")
    assert "logs" in body and "runjob.log" in body, "실패를 로그로 안 남긴다"
