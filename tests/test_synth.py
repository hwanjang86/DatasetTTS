# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_batch.synth import stable_seed  # noqa: E402


def test_seed_is_stable_across_runs():
    a = stable_seed("Closers_Android", "0001.wav")
    b = stable_seed("Closers_Android", "0001.wav")
    assert a == b


def test_seed_differs_per_clip():
    assert (stable_seed("Closers_Android", "0001.wav")
            != stable_seed("Closers_Android", "0002.wav"))


def test_seed_differs_per_voice():
    assert (stable_seed("Closers_Android", "0001.wav")
            != stable_seed("Other_Voice", "0001.wav"))


def test_retake_changes_the_seed():
    # otherwise `retry --include-flagged` would regenerate the identical
    # artifact it was asked to replace
    base = stable_seed("Closers_Android", "0001.wav", 0)
    assert stable_seed("Closers_Android", "0001.wav", 1) != base
    assert stable_seed("Closers_Android", "0001.wav", 2) != base


def test_seed_is_in_api_range():
    for i in range(500):
        s = stable_seed("V", "%04d.wav" % i)
        assert 0 <= s < 2 ** 31 - 1
