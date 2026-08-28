# -*- coding: utf-8 -*-
"""Turn a raw script line into the exact text the TTS engine will speak.

Two passes:
  1. expand digits to hangul, choosing native or Sino-Korean by the counter
  2. fix particle agreement -- both the errors already in the source script and
     the ones created by pass 1 (e.g. "3928를" -> "삼천구백이십팔을")

Pass 2 runs after pass 1 on purpose: the correct particle depends on how the
number is *spoken*, which is only known once the digits are expanded.
"""

import re

from .korean import (
    JOSA_PAIRS,
    has_batchim,
    pick_josa,
    pick_ro,
    read_number,
)

# counter -> (numeral system, separator between numeral and counter)
# 월/일 attach directly: 7월 -> 칠월, 10일 -> 십일.
COUNTERS = {
    "퍼센트": ("sino", " "),
    "킬로미터": ("sino", " "),
    "미터": ("sino", " "),
    "시간": ("native", " "),   # duration: 1시간 -> 한 시간
    "시": ("sino", " "),       # clock hour: 23시 -> 이십삼 시
    "분": ("sino", " "),
    "초": ("sino", " "),
    "월": ("sino", ""),
    "일": ("sino", ""),
    "개": ("native", " "),
    "명": ("native", " "),
    "곳": ("native", " "),
    "발": ("native", " "),
    "배": ("native", " "),
}

# Longest counter first so 킬로미터 wins over 미터 and 시간 over 시.
_COUNTER_ALT = "|".join(
    sorted((re.escape(c) for c in COUNTERS), key=len, reverse=True)
)
# The counter group is optional *as a whole* so that a bare number keeps the
# space after it: "562 증가" must not collapse into "오백육십이증가".
NUMBER_RE = re.compile(r"(\d+)(?:\s*(%s))?" % _COUNTER_ALT)

# 일 is two different counters wearing one hat. As a calendar day it attaches
# (7월 10일 -> "칠월 십일"); as a span of days it must not, because "5일" run
# together comes out as 오일, the word for oil. A preceding N월 marks the date.
DATE_PREFIX_RE = re.compile(r"\d+\s*월\s*$")

TRAILING_PUNCT = ".,?!\"'"

# Nouns whose last syllable is shaped like a particle. Without this guard the
# corrector reads "가을 수확제" as 가+을 and emits "가를 수확제".
#
# This list cannot be complete for Korean in general -- it is the escape hatch
# for what the preview report turns up. Run `preview_run.py` on every new script
# and read the WORD REWRITES section before generating: any real noun that shows
# up there belongs here.
PROTECTED_TOKENS = frozenset([
    "가을", "마을",                                    # -을
    "아이", "사이", "나이", "오이",                      # -이 after a vowel
    "증가", "평가", "국가", "참가", "물가", "시가",        # -가 after a consonant
    "단가", "원가", "정가", "저가", "고가",
])


def expand_numbers(text):
    """Replace every digit run with its spoken hangul form."""

    def repl(m):
        n = int(m.group(1))
        counter = m.group(2)
        if counter is None:
            return read_number(n, "sino")
        system, sep = COUNTERS[counter]
        if counter == "일" and not DATE_PREFIX_RE.search(m.string[:m.start()]):
            sep = " "
        return read_number(n, system) + sep + counter

    return NUMBER_RE.sub(repl, text)


def _fix_token(tok):
    """Correct a trailing particle on one token. Returns the token unchanged
    when it already agrees, or when the preceding character is not hangul."""
    core = tok.rstrip(TRAILING_PUNCT)
    tail = tok[len(core):]
    if len(core) < 2 or core in PROTECTED_TOKENS:
        return tok

    # 으로 / 로
    for form in ("으로", "로"):
        if core.endswith(form):
            stem = core[: -len(form)]
            if not stem:
                break
            correct = pick_ro(stem[-1])
            if correct and correct != form:
                return stem + correct + tail
            return tok

    last, prev = core[-1], core[-2]
    if has_batchim(prev) is None:
        return tok
    for cons, vow in JOSA_PAIRS:
        if last in (cons, vow):
            correct = pick_josa(prev, cons, vow)
            if correct and correct != last:
                return core[:-1] + correct + tail
            return tok
    return tok


def fix_josa(text):
    """Apply particle agreement across the whole line, token by token."""
    return " ".join(_fix_token(t) for t in text.split())


def normalize(text):
    """Full pipeline: what the voice will actually say."""
    return fix_josa(expand_numbers(text))


def build_prompt(emotion, normalized_text, drop_tags=("neutral",)):
    """The exact string submitted to the API.

    The [neutral] tag is dropped: measurement showed it changes nothing on v3
    while costing 35,150 billed characters across this script.
    """
    if emotion in drop_tags:
        return normalized_text
    return "[%s] %s" % (emotion, normalized_text)
