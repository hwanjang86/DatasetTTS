# -*- coding: utf-8 -*-
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_batch.audio import analyze, trim_silence  # noqa: E402

SR = 24000


def tone(seconds, amp=6000, freq=180):
    t = np.arange(int(SR * seconds)) / float(SR)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def silence(seconds):
    return np.zeros(int(SR * seconds), dtype=np.int16)


def test_clean_utterance_has_no_flags():
    clip = np.concatenate([silence(0.05), tone(2.0), silence(0.1)])
    assert analyze(clip, SR)["flags"] == []


def test_trailing_artifact_is_detected():
    # the shape measured in the two clips confirmed by ear: speech, a ~130ms
    # gap, then a short burst that is still sounding at the last sample
    clip = np.concatenate([tone(2.0), silence(0.13), tone(0.12)])
    assert "trailing_artifact" in analyze(clip, SR)["flags"]


def test_loud_trailing_artifact_is_detected():
    # L3_calm_C climbed to the clip's peak in its final frames
    clip = np.concatenate([tone(2.0, amp=5000), silence(0.12),
                           tone(0.14, amp=20000)])
    assert "trailing_artifact" in analyze(clip, SR)["flags"]


def test_short_gap_is_not_an_artifact():
    # a natural pause mid-sentence must not be flagged
    clip = np.concatenate([tone(1.0), silence(0.08), tone(1.0), silence(0.2)])
    assert "trailing_artifact" not in analyze(clip, SR)["flags"]


def test_long_second_phrase_is_not_an_artifact():
    # two real phrases separated by a breath: the tail is too long to be a blip
    clip = np.concatenate([tone(1.5), silence(0.3), tone(1.5), silence(0.2)])
    assert "trailing_artifact" not in analyze(clip, SR)["flags"]


def test_blip_that_decays_into_silence_is_not_an_artifact():
    # a real final word ends with trailing silence; only a clip that is still
    # sounding when it cuts off is the artifact we are hunting
    clip = np.concatenate([tone(2.0), silence(0.2), tone(0.12), silence(0.25)])
    assert "trailing_artifact" not in analyze(clip, SR)["flags"]


def test_silence_is_flagged():
    assert "silent" in analyze(silence(1.0), SR)["flags"]


def test_empty_is_flagged():
    assert analyze(np.zeros(0, dtype=np.int16), SR)["flags"] == ["empty"]


def test_clipping_is_flagged():
    clip = np.concatenate([tone(1.0, amp=32760), tone(0.5, amp=32760)])
    assert "clipped" in analyze(clip, SR)["flags"]


def test_duration_mismatch_on_truncation():
    # 40 characters should run about 5s at the corpus rate; 0.6s means truncated
    clip = tone(0.6)
    flags = analyze(clip, SR, text="가" * 40)["flags"]
    assert "duration_mismatch" in flags


def test_duration_ok_for_matching_text():
    clip = tone(2.5)
    flags = analyze(clip, SR, text="가" * 20)["flags"]
    assert "duration_mismatch" not in flags


def test_trim_normalizes_edge_silence():
    clip = np.concatenate([silence(0.5), tone(1.0), silence(0.9)])
    out = trim_silence(clip, SR, pad_ms=80)
    r = analyze(out, SR)
    assert 0.05 < r["lead_silence"] < 0.12
    assert 0.05 < r["tail_silence"] < 0.12


def test_trim_keeps_the_speech_intact():
    clip = np.concatenate([silence(0.3), tone(1.0), silence(0.3)])
    out = trim_silence(clip, SR, pad_ms=80)
    # 1.0s of speech plus two 80ms pads
    assert abs(len(out) / float(SR) - 1.16) < 0.05


def test_trim_leaves_all_silence_alone():
    clip = silence(1.0)
    assert len(trim_silence(clip, SR)) == len(clip)


def test_measurements_are_sane():
    clip = np.concatenate([silence(0.2), tone(1.0), silence(0.4)])
    r = analyze(clip, SR)
    assert abs(r["duration"] - 1.6) < 0.01
    assert 0.15 < r["lead_silence"] < 0.25
    assert 0.35 < r["tail_silence"] < 0.45
