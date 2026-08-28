# -*- coding: utf-8 -*-
"""Korean number reading and particle (josa) agreement.

The TTS engine reads bare digits one-by-one ("43636" -> "사삼육삼육"), so every
number is expanded to hangul here before it is sent. The expanded text is also
what gets written to metadata.csv, keeping transcript and audio identical.
"""

SINO_DIGITS = ["영", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]

# Native numerals in their attributive form, i.e. the shape used in front of a
# counter: 하나 -> 한, 둘 -> 두, 셋 -> 세, 넷 -> 네, 스물 -> 스무.
NATIVE_ONES = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
NATIVE_TENS = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]

# Above this, native numerals stop being idiomatic and Sino-Korean takes over
# (e.g. 295발 -> "이백구십오 발", not "이백아흔다섯 발").
NATIVE_MAX = 99

HANGUL_BASE = 0xAC00
HANGUL_LAST = 0xD7A3
JONG_RIEUL = 8  # index of ㄹ in the jongseong table


def jongseong(ch):
    """Trailing-consonant index of a hangul syllable; None if not hangul."""
    c = ord(ch)
    if not (HANGUL_BASE <= c <= HANGUL_LAST):
        return None
    return (c - HANGUL_BASE) % 28


def has_batchim(ch):
    """True if the syllable ends in a consonant. None if not hangul."""
    j = jongseong(ch)
    return None if j is None else j != 0


def _sino_under_10000(n):
    """Read 1..9999 in Sino-Korean. Leading 1 is dropped before 십/백/천."""
    out = []
    for value, name in ((1000, "천"), (100, "백"), (10, "십")):
        q, n = divmod(n, value)
        if q:
            out.append(("" if q == 1 else SINO_DIGITS[q]) + name)
    if n:
        out.append(SINO_DIGITS[n])
    return "".join(out)


def sino(n):
    """Read a non-negative integer in Sino-Korean.

    A space separates 만-groups, matching standard orthography:
    43636 -> "사만 삼천육백삼십육".
    """
    if n < 0:
        raise ValueError("negative numbers are not supported: %r" % n)
    if n == 0:
        return "영"
    if n < 10000:
        return _sino_under_10000(n)

    parts = []
    eok, n = divmod(n, 100000000)
    if eok:
        parts.append(("" if eok == 1 else _sino_under_10000(eok)) + "억")
    man, n = divmod(n, 10000)
    if man:
        parts.append(("" if man == 1 else _sino_under_10000(man)) + "만")
    if n:
        parts.append(_sino_under_10000(n))
    return " ".join(parts)


def native(n):
    """Read 1..99 in native Korean, attributive form (used before a counter)."""
    if not 1 <= n <= NATIVE_MAX:
        raise ValueError("native numerals cover 1..%d, got %r" % (NATIVE_MAX, n))
    tens, ones = divmod(n, 10)
    if tens == 0:
        return NATIVE_ONES[ones]
    if ones == 0:
        # 20 alone is 스무, not 스물, when it stands before a counter.
        return "스무" if tens == 2 else NATIVE_TENS[tens]
    return NATIVE_TENS[tens] + NATIVE_ONES[ones]


def read_number(n, system="sino"):
    """Read n in the requested system, falling back to Sino above 99."""
    if system == "native" and 1 <= n <= NATIVE_MAX:
        return native(n)
    return sino(n)


# --- particles -------------------------------------------------------------
#
# 과/와 is deliberately excluded: this corpus contains no genuine 과/와 particle,
# and the noun 효과 would otherwise be mangled into 효와.

JOSA_PAIRS = [
    ("을", "를"),
    ("은", "는"),
    ("이", "가"),
]


def pick_josa(prev_char, after_consonant, after_vowel):
    """Choose the particle form that agrees with the preceding syllable."""
    b = has_batchim(prev_char)
    if b is None:
        return None
    return after_consonant if b else after_vowel


def pick_ro(prev_char):
    """Choose 로 vs 으로. A ㄹ-final syllable takes 로, like a vowel does."""
    j = jongseong(prev_char)
    if j is None:
        return None
    return "로" if j in (0, JONG_RIEUL) else "으로"
