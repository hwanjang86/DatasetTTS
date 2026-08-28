# -*- coding: utf-8 -*-
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_batch.preview import billed_chars  # noqa: E402

SCRIPT = "\n".join([
    "[neutral] 보스의 위치를 확인했습니다. | 0001.wav",
    "[alert] 감염체가 접근합니다. | 0002.wav",
    "[combat] 탄약은 62발 남았습니다. | 0003.wav",
]) + "\n"


def _script(tmp_path):
    p = tmp_path / "Test_Voice.txt"
    io.open(str(p), "w", encoding="utf-8").write(SCRIPT)
    return str(p)


def test_limit_quotes_only_the_clips_being_made(tmp_path):
    path = _script(tmp_path)
    one = billed_chars(path, limit=1)
    all_three = billed_chars(path)
    assert one < all_three
    # the cost gate must not quote the whole file for a --limit pilot
    assert one == len("보스의 위치를 확인했습니다.")


def test_neutral_tag_is_not_billed(tmp_path):
    path = _script(tmp_path)
    # line 1 is neutral: its tag is dropped, so only the body counts
    assert billed_chars(path, limit=1) == len("보스의 위치를 확인했습니다.")
    # line 2 keeps [alert] plus a space
    two = billed_chars(path, limit=2) - billed_chars(path, limit=1)
    assert two == len("[alert] 감염체가 접근합니다.")


def test_numbers_are_counted_as_spoken(tmp_path):
    path = _script(tmp_path)
    third = billed_chars(path, limit=3) - billed_chars(path, limit=2)
    assert third == len("[combat] 탄약은 예순두 발 남았습니다.")
