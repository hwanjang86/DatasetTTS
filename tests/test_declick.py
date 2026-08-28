# -*- coding: utf-8 -*-
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_batch.audio import analyze, strip_trailing_artifact  # noqa: E402

SR = 24000


def tone(seconds, amp=6000, freq=180):
    t = np.arange(int(SR * seconds)) / float(SR)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def silence(seconds):
    return np.zeros(int(SR * seconds), dtype=np.int16)


def test_tick_is_removed_and_clip_measures_clean():
    clip = np.concatenate([tone(2.0), silence(0.13), tone(0.02, amp=20000)])
    assert "trailing_artifact" in analyze(clip, SR)["flags"]

    out = strip_trailing_artifact(clip, SR, pad_ms=50)
    assert out is not None
    assert analyze(out, SR)["flags"] == []


def test_cut_lands_just_after_the_utterance():
    clip = np.concatenate([tone(2.0), silence(0.13), tone(0.02, amp=20000)])
    out = strip_trailing_artifact(clip, SR, pad_ms=50)
    # 2.0s of speech plus the 50ms pad, not the full 2.15s
    assert abs(len(out) / float(SR) - 2.05) < 0.03


def test_clean_clip_is_left_alone():
    clip = np.concatenate([tone(2.0), silence(0.05)])
    assert strip_trailing_artifact(clip, SR) is None


def test_real_second_phrase_is_never_cut():
    # a genuine phrase after a breath is long; cutting it would lose speech
    clip = np.concatenate([tone(1.5), silence(0.3), tone(1.5), silence(0.05)])
    assert strip_trailing_artifact(clip, SR) is None


def test_short_gap_is_not_treated_as_an_artifact():
    clip = np.concatenate([tone(1.0), silence(0.06), tone(0.02, amp=20000)])
    assert strip_trailing_artifact(clip, SR) is None


def test_varying_gaps_cut_by_different_amounts():
    # the point of per-clip cutting: the gap varies fivefold in the real corpus
    short = np.concatenate([tone(2.0), silence(0.12), tone(0.02, amp=20000)])
    long_ = np.concatenate([tone(2.0), silence(0.42), tone(0.02, amp=20000)])
    a = len(short) - len(strip_trailing_artifact(short, SR, 50))
    b = len(long_) - len(strip_trailing_artifact(long_, SR, 50))
    assert b > a
    # both keep the same amount of real speech
    assert abs(len(strip_trailing_artifact(short, SR, 50))
               - len(strip_trailing_artifact(long_, SR, 50))) < SR * 0.03


def test_is_idempotent():
    clip = np.concatenate([tone(2.0), silence(0.13), tone(0.02, amp=20000)])
    once = strip_trailing_artifact(clip, SR, 50)
    assert strip_trailing_artifact(once, SR, 50) is None


def test_long_final_word_is_not_mistaken_for_a_tick():
    # 2718.wav: a 480ms final word after a 100ms breath, ending with only 30ms
    # of trailing silence. A 500ms ceiling classified it as a tick and cut it.
    clip = np.concatenate([tone(2.48), silence(0.10), tone(0.48), silence(0.03)])
    assert "trailing_artifact" not in analyze(clip, SR)["flags"]
    assert strip_trailing_artifact(clip, SR) is None


def test_twenty_millisecond_tick_is_still_caught():
    # the shape of the other 55
    clip = np.concatenate([tone(2.0), silence(0.15), tone(0.02, amp=20000)])
    assert "trailing_artifact" in analyze(clip, SR)["flags"]
    assert strip_trailing_artifact(clip, SR) is not None


def test_confirmed_setup_artifacts_still_caught():
    # L1_combat_C had a 100ms blip, L3_calm_C a 140ms one
    for blip in (0.10, 0.14):
        clip = np.concatenate([tone(2.0), silence(0.13), tone(blip, amp=20000)])
        assert "trailing_artifact" in analyze(clip, SR)["flags"], blip
