"""한국어 조사.

에러 메시지가 사용자 화면에 그대로 나간다. "야후 파이낸스 (전 세계) 은 3m 봉을..." 처럼
조사가 틀리면 매번 눈에 걸린다. 괄호로 "은(는)" 을 쓰는 것도 마찬가지다 —
읽는 사람이 매번 괄호를 넘겨 읽어야 한다.
"""
from __future__ import annotations

import re

# 앞 글자에 받침이 있을 때 / 없을 때
PAIRS = {
    "은는": ("은", "는"),
    "이가": ("이", "가"),
    "을를": ("을", "를"),
    "과와": ("과", "와"),
    "으로로": ("으로", "로"),
    "이에요예요": ("이에요", "예요"),
}

# 숫자·영문자로 끝나면 **읽는 소리**로 판단한다. 철자가 아니라 발음이 조사를 정한다 —
# T 는 '티'라 받침이 없고, L 은 '엘'이라 있다.
# 0영 1일 3삼 6육 7칠 8팔 = 받침, 2이 4사 5오 9구 = 없음
_DIGIT_HAS_FINAL = {"0": True, "1": True, "3": True, "6": True, "7": True, "8": True,
                    "2": False, "4": False, "5": False, "9": False}
# 받침이 있는 것: L엘 M엠 N엔 R알 S에스 X엑스 F에프. 나머지는 없다.
_LETTER_HAS_FINAL = {
    "a": False, "b": False, "c": False, "d": False, "e": False, "f": True,
    "g": False, "h": False, "i": False, "j": False, "k": False, "l": True,
    "m": True, "n": True, "o": False, "p": False, "q": False, "r": True,
    "s": True, "t": False, "u": False, "v": False, "w": False, "x": True,
    "y": False, "z": False,
}

# 뒤에 붙은 괄호·공백·문장부호는 조사 판단에서 빼야 한다.
_TRAILING = re.compile(r"[\s)\]}>\"'·.,!?~-]+$")


def has_final(word: str) -> bool | None:
    """마지막 글자에 받침이 있는가. 판단할 수 없으면 None."""
    cleaned = _TRAILING.sub("", word.strip())
    # 괄호 안의 보충 설명은 발음의 기준이 아니다 — "야후 (전 세계)" 는 '후' 로 판단한다.
    cleaned = re.sub(r"[(\[{<][^)\]}>]*$", "", cleaned).strip() or cleaned
    if not cleaned:
        return None

    last = cleaned[-1]
    if "가" <= last <= "힣":
        return (ord(last) - 0xAC00) % 28 != 0
    if last.isdigit():
        return _DIGIT_HAS_FINAL.get(last)
    if last.isalpha() and last.isascii():
        return _LETTER_HAS_FINAL.get(last.lower())
    return None


def josa(word: str, pair: str) -> str:
    """조사만. 판단이 안 되면 받침 없는 쪽을 쓴다 — 그쪽이 덜 어색하다."""
    with_final, without_final = PAIRS[pair]
    result = has_final(word)
    return without_final if result is None else (with_final if result else without_final)


def with_josa(word: str, pair: str) -> str:
    """단어 + 조사. 메시지를 쓸 때는 이걸 쓴다."""
    return f"{word}{josa(word, pair)}"
