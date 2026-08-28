# -*- coding: utf-8 -*-
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_batch.audio import analyze, load_wav, pcm_to_wav  # noqa: E402
from tts_batch.retrim import check, repair  # noqa: E402

SR = 24000


def _write(out_dir, name, samples):
    wav_dir = os.path.join(out_dir, "wavs")
    os.makedirs(wav_dir, exist_ok=True)
    pcm_to_wav(os.path.join(wav_dir, name), samples.tobytes(), SR)


def tone(seconds, amp=6000):
    t = np.arange(int(SR * seconds)) / float(SR)
    return (amp * np.sin(2 * np.pi * 180 * t)).astype(np.int16)


def silence(seconds):
    return np.zeros(int(SR * seconds), dtype=np.int16)


def test_long_tail_is_detected_and_repaired(tmp_path):
    out = str(tmp_path)
    _write(out, "0001.wav", np.concatenate([silence(0.05), tone(1.0), silence(0.9)]))
    assert len(check(out, 50)) == 1

    fixed = repair(out, 50)
    assert len(fixed) == 1
    assert check(out, 50) == []

    samples, sr = load_wav(os.path.join(out, "wavs", "0001.wav"))
    assert analyze(samples, sr)["tail_silence"] <= 0.09


def test_in_spec_clip_is_left_alone(tmp_path):
    out = str(tmp_path)
    clip = np.concatenate([silence(0.05), tone(1.0), silence(0.05)])
    _write(out, "0001.wav", clip)
    before = os.path.getsize(os.path.join(out, "wavs", "0001.wav"))
    assert repair(out, 50) == []
    assert os.path.getsize(os.path.join(out, "wavs", "0001.wav")) == before


def test_repair_is_idempotent(tmp_path):
    out = str(tmp_path)
    _write(out, "0001.wav", np.concatenate([silence(0.6), tone(1.0), silence(0.8)]))
    repair(out, 50)
    size = os.path.getsize(os.path.join(out, "wavs", "0001.wav"))
    assert repair(out, 50) == []
    assert os.path.getsize(os.path.join(out, "wavs", "0001.wav")) == size


def test_all_silence_clip_is_not_destroyed(tmp_path):
    out = str(tmp_path)
    _write(out, "0001.wav", silence(1.0))
    repair(out, 50)
    samples, _ = load_wav(os.path.join(out, "wavs", "0001.wav"))
    assert len(samples) > 0
