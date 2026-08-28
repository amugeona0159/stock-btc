"""조사. 에러 메시지가 사용자 화면에 그대로 나가므로 여기가 틀리면 매번 눈에 걸린다."""
import pytest

from marketlens.core.text import has_final, with_josa


@pytest.mark.parametrize("word,expected", [
    ("바이낸스", "바이낸스는"),      # 받침 없음
    ("업비트", "업비트는"),
    ("한국투자증권", "한국투자증권은"),  # 받침 있음
    ("삼성전자", "삼성전자는"),
    ("야후 파이낸스 (전 세계)", "야후 파이낸스 (전 세계)는"),  # 괄호는 판단에서 뺀다
    ("BTCUSDT", "BTCUSDT는"),        # T 는 '티'
    ("AAPL", "AAPL은"),              # L 은 '엘'
    ("005930", "005930은"),          # 0 은 '영'
])
def test_josa_picks_the_right_particle(word, expected):
    assert with_josa(word, "은는") == expected


def test_bracket_note_does_not_change_the_particle():
    """괄호 안 보충 설명이 조사를 바꾸면 안 된다. '야후' 로 판단해야 한다."""
    assert has_final("야후 (전 세계)") is False


def test_unknown_ending_falls_back_to_the_softer_form():
    assert with_josa("★", "은는") == "★는"


def test_other_pairs():
    assert with_josa("차트", "이가") == "차트가"
    assert with_josa("사건", "이가") == "사건이"
    assert with_josa("봉", "을를") == "봉을"
    assert with_josa("사례", "을를") == "사례를"
