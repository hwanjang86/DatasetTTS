# -*- coding: utf-8 -*-
"""WAV writing and automated quality checks.

The engine occasionally appends a stray sound after the sentence ends -- a
click, a breath, a fragment of a syllable. At 4,211 clips nobody is going to
catch those by ear, so `analyze` looks for the signature: a short burst of
energy separated from the main utterance by a clear gap.
"""

import wave

import numpy as np

FRAME_MS = 20
SILENCE_FLOOR = 250          # absolute RMS below which a frame is silence
SILENCE_REL = 0.02           # ...or this fraction of the clip's peak
# Tuned against two clips confirmed by ear. Their signature: the utterance
# decays to silence, a gap of 120-140ms passes, then energy climbs again -- to
# the clip's peak in one case -- and the file ends while it is still loud.
# A clip that finished properly always decays into trailing silence, so
# "still sounding at the last sample" is what separates the two.
ARTIFACT_GAP_MS = 100        # a gap at least this long isolates a trailing blip
# 200, not 500: the ticks in this corpus are bimodal -- 55 of the 56 flagged
# clips ended in a 20ms blip, while 2718.wav ended in a 480ms one that turned
# out to be its final word. A 500ms ceiling cut real speech out of that clip.
# The confirmed artifacts run 20-140ms, so 200 keeps every true positive.
ARTIFACT_MAX_MS = 200        # ...and a blip is short; longer is real speech
ARTIFACT_TAIL_MS = 60        # ...and it runs to the end instead of decaying
CLIP_LEVEL = 32700


def pcm_to_wav(path, pcm_bytes, sample_rate=24000):
    """Wrap raw signed 16-bit little-endian mono PCM in a WAV container."""
    n = len(pcm_bytes) // 2
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes[: n * 2])
    return n / float(sample_rate)


def load_wav(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2"), sr


def _voiced_runs(samples, sample_rate):
    """Contiguous (start_frame, end_frame) spans of non-silent audio."""
    flen = max(1, int(sample_rate * FRAME_MS / 1000))
    usable = (len(samples) // flen) * flen
    if usable == 0:
        return [], flen, np.zeros(0)
    frames = samples[:usable].reshape(-1, flen).astype(np.float64)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    peak = float(np.abs(samples).max()) if len(samples) else 0.0
    thr = max(SILENCE_FLOOR, peak * SILENCE_REL)
    voiced = rms > thr

    runs = []
    start = None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(voiced)))
    return runs, flen, rms


def trim_silence(samples, sample_rate, pad_ms=80):
    """Cut leading/trailing silence down to a fixed pad.

    Trailing silence in this corpus ranges from 0.01s to 0.84s. A TTS training
    set wants that constant, so the model does not learn to emit a random
    amount of nothing. Returns the samples unchanged if the clip is all silence.
    """
    runs, flen, _ = _voiced_runs(samples, sample_rate)
    if not runs:
        return samples
    pad = int(sample_rate * pad_ms / 1000)
    start = max(0, runs[0][0] * flen - pad)
    end = min(len(samples), runs[-1][1] * flen + pad)
    return samples[start:end]


def strip_trailing_artifact(samples, sample_rate, pad_ms=50):
    """Cut a trailing tick off, at the end of the real utterance.

    Returns None when the clip does not carry the artifact signature, so this
    is safe to run over a whole directory.

    A fixed cut length cannot work here: across the 56 affected clips the tick
    itself is a steady ~20ms, but the silence between the utterance and the
    tick ranges from 100ms to 420ms. Trimming everything by the largest total
    (610ms) would eat real speech from 55 of the 56. The cut point has to come
    from each clip's own detected utterance end.
    """
    runs, flen, _ = _voiced_runs(samples, sample_rate)
    if len(runs) < 2:
        return None
    frame_s = flen / float(sample_rate)
    duration = len(samples) / float(sample_rate)
    tail = duration - runs[-1][1] * frame_s
    gap = (runs[-1][0] - runs[-2][1]) * frame_s
    blip = (runs[-1][1] - runs[-1][0]) * frame_s
    if not (gap * 1000 >= ARTIFACT_GAP_MS
            and blip * 1000 <= ARTIFACT_MAX_MS
            and tail * 1000 <= ARTIFACT_TAIL_MS):
        return None
    end = min(len(samples),
              runs[-2][1] * flen + int(sample_rate * pad_ms / 1000))
    return samples[:end]


def analyze(samples, sample_rate, text=None):
    """Measure one clip and flag anything that looks wrong."""
    n = len(samples)
    duration = n / float(sample_rate)
    flags = []

    if n == 0:
        return {"duration": 0.0, "peak": 0, "rms": 0.0,
                "lead_silence": 0.0, "tail_silence": 0.0,
                "flags": ["empty"]}

    peak = int(np.abs(samples).max())
    rms = float(np.sqrt((samples.astype(np.float64) ** 2).mean()))
    runs, flen, _ = _voiced_runs(samples, sample_rate)
    frame_s = flen / float(sample_rate)

    if not runs:
        flags.append("silent")
        lead = tail = duration
    else:
        lead = runs[0][0] * frame_s
        tail = duration - runs[-1][1] * frame_s

        # trailing artifact: a short, isolated burst after a real gap that is
        # still sounding when the clip ends
        if len(runs) >= 2:
            gap = (runs[-1][0] - runs[-2][1]) * frame_s
            blip = (runs[-1][1] - runs[-1][0]) * frame_s
            if (gap * 1000 >= ARTIFACT_GAP_MS
                    and blip * 1000 <= ARTIFACT_MAX_MS
                    and tail * 1000 <= ARTIFACT_TAIL_MS):
                flags.append("trailing_artifact")

    if peak >= CLIP_LEVEL:
        flags.append("clipped")
    if duration < 0.35:
        flags.append("too_short")

    # A clip far off the corpus norm of roughly 8 characters per second is
    # usually a truncation or a runaway generation.
    if text:
        expected = len(text) / 8.0
        if expected > 0.5 and (duration < expected * 0.45 or
                               duration > expected * 2.4):
            flags.append("duration_mismatch")

    return {"duration": duration, "peak": peak, "rms": rms,
            "lead_silence": lead, "tail_silence": tail, "flags": flags}
