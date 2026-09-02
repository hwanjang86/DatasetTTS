# -*- coding: utf-8 -*-
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_batch.normalize import build_prompt  # noqa: E402
from tts_batch.parser import (ScriptError, check_style, clip_path,  # noqa: E402
                              iter_clips, parse_file, rel_path)


def write(tmp_path, body, name="Test_Voice.txt"):
    p = tmp_path / name
    io.open(str(p), "w", encoding="utf-8").write(body)
    return str(p)


# --- parsing ---------------------------------------------------------------

def test_style_and_audio_tag_are_separate(tmp_path):
    path = write(tmp_path, "(행복)[happy] 오늘은 정말 즐거운 날이에요! | 0001.wav\n")
    e = parse_file(path)[0]
    assert e.style == "행복"
    assert e.emotion == "happy"
    assert e.text == "오늘은 정말 즐거운 날이에요!"
    assert e.wav == "0001.wav"


def test_style_never_reaches_the_api(tmp_path):
    """The paren tag files the clip; it is not spoken and is not billed."""
    path = write(tmp_path, "(행복)[happy] 안녕하세요. | 0001.wav\n")
    e = parse_file(path)[0]
    prompt = build_prompt(e.emotion, e.text)
    assert "행복" not in prompt
    assert "(" not in prompt
    assert prompt == "[happy] 안녕하세요."


def test_neutral_audio_tag_still_dropped_with_a_style(tmp_path):
    path = write(tmp_path, "(슬픔)[neutral] 안녕하세요. | 0001.wav\n")
    e = parse_file(path)[0]
    assert build_prompt(e.emotion, e.text) == "안녕하세요."


def test_old_format_still_parses(tmp_path):
    path = write(tmp_path, "[neutral] 보스의 위치를 확인했습니다. | 0001.wav\n")
    e = parse_file(path)[0]
    assert e.style is None
    assert e.emotion == "neutral"


def test_style_without_audio_tag(tmp_path):
    path = write(tmp_path, "(웃음) 하하하 정말 웃기네요. | 0001.wav\n")
    e = parse_file(path)[0]
    assert e.style == "웃음"
    assert e.emotion == "neutral"        # default, and therefore dropped
    assert build_prompt(e.emotion, e.text) == "하하하 정말 웃기네요."


def test_no_tags_at_all(tmp_path):
    path = write(tmp_path, "그냥 문장입니다. | 0001.wav\n")
    e = parse_file(path)[0]
    assert e.style is None and e.emotion == "neutral"


def test_parens_inside_the_text_are_left_alone(tmp_path):
    path = write(tmp_path, "[neutral] 결과(성공)를 확인했습니다. | 0001.wav\n")
    e = parse_file(path)[0]
    assert e.style is None
    assert e.text == "결과(성공)를 확인했습니다."


def test_mixed_styles_in_one_script(tmp_path):
    path = write(tmp_path, "\n".join([
        "(행복)[happy] 좋은 아침이에요. | 0001.wav",
        "(슬픔)[sad] 다시 만날 수 있을까요. | 0002.wav",
        "[neutral] 확인했습니다. | 0003.wav",
    ]) + "\n")
    entries = parse_file(path)
    assert [e.style for e in entries] == ["행복", "슬픔", None]


# --- folder placement ------------------------------------------------------

def test_rel_path_puts_styled_clips_in_a_folder(tmp_path):
    path = write(tmp_path, "(행복)[happy] 안녕. | 0001.wav\n[neutral] 안녕. | 0002.wav\n")
    a, b = parse_file(path)
    assert rel_path(a) == "행복/0001.wav"
    assert rel_path(b) == "0002.wav"
    assert clip_path("W", a) == os.path.join("W", "행복", "0001.wav")
    assert clip_path("W", b) == os.path.join("W", "0002.wav")


def test_iter_clips_walks_style_folders(tmp_path):
    wavs = tmp_path / "wavs"
    (wavs / "행복").mkdir(parents=True)
    (wavs / "슬픔").mkdir()
    for p in [wavs / "0003.wav", wavs / "행복" / "0001.wav", wavs / "슬픔" / "0002.wav"]:
        io.open(str(p), "wb").write(b"\0" * 10)
    io.open(str(wavs / "notes.txt"), "wb").write(b"x")
    assert sorted(iter_clips(str(wavs))) == ["0003.wav", "슬픔/0002.wav", "행복/0001.wav"]


# --- style names must be safe folder names ---------------------------------

@pytest.mark.parametrize("style", [
    "../escape", "..\\escape", "a/b", "a\\b", "a:b", "a*b", "a?b",
    'a"b', "a<b", "a>b", "a|b", "", "  ", ".", "..", "con", "PRN", "com1",
    "x" * 41,
])
def test_unsafe_style_is_rejected(style):
    assert check_style(style) is not None


@pytest.mark.parametrize("style", ["행복", "슬픔", "웃음", "분노", "놀람",
                                   "happy", "very happy", "감정-01"])
def test_safe_style_is_accepted(style):
    assert check_style(style) is None


def test_traversal_in_a_script_is_a_parse_error(tmp_path):
    path = write(tmp_path, "(../../etc)[happy] 안녕. | 0001.wav\n")
    with pytest.raises(ScriptError) as exc:
        parse_file(path)
    assert "path character" in str(exc.value)


def test_wav_name_may_not_carry_a_path(tmp_path):
    path = write(tmp_path, "[neutral] 안녕. | ../0001.wav\n")
    with pytest.raises(ScriptError):
        parse_file(path)


def test_duplicate_wav_across_styles_is_rejected(tmp_path):
    # the wav name is the clip's identity everywhere, so it stays globally unique
    path = write(tmp_path, "(행복)[happy] 가. | 0001.wav\n(슬픔)[sad] 나. | 0001.wav\n")
    with pytest.raises(ScriptError) as exc:
        parse_file(path)
    assert "duplicate" in str(exc.value)
