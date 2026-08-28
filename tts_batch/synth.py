# -*- coding: utf-8 -*-
"""ElevenLabs client wrapper: one clip, with retries and a stable seed."""

import hashlib
import random
import time


RETRYABLE_STATUS = (408, 425, 429, 500, 502, 503, 504)


class SynthError(RuntimeError):
    """Raised when a clip could not be generated after every retry."""


def stable_seed(voice, wav, offset=0):
    """A per-clip seed derived from its identity, so reruns reproduce audio.

    `offset` deliberately breaks that reproducibility. A clip flagged for a
    trailing artifact would come back byte-identical on the same seed, so the
    retry path bumps the offset to draw a genuinely different take. The seed
    used is recorded in manifest.jsonl either way.
    """
    h = hashlib.sha256(
        ("%s/%s/%d" % (voice, wav, offset)).encode("utf-8")).hexdigest()
    return int(h[:8], 16) % (2 ** 31 - 1)


def _status_of(exc):
    for attr in ("status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        v = body.get("status_code")
        if isinstance(v, int):
            return v
    return None


def _is_retryable(exc):
    status = _status_of(exc)
    if status is not None:
        return status in RETRYABLE_STATUS
    # Network-level failures carry no status; those are worth another try.
    return isinstance(exc, (IOError, OSError))


class Synthesizer:
    def __init__(self, client, voice_id, voice_name, model_id="eleven_v3",
                 output_format="pcm_24000", normalization="off",
                 voice_settings=None, max_retries=5, base_delay=1.0,
                 seed_offset=0):
        self.client = client
        self.voice_id = voice_id
        self.voice_name = voice_name
        self.model_id = model_id
        self.output_format = output_format
        self.normalization = normalization
        self.voice_settings = voice_settings
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.seed_offset = seed_offset

    def request_params(self, prompt, wav):
        p = {
            "voice_id": self.voice_id,
            "text": prompt,
            "model_id": self.model_id,
            "output_format": self.output_format,
            "apply_text_normalization": self.normalization,
            "seed": stable_seed(self.voice_name, wav, self.seed_offset),
        }
        if self.voice_settings:
            p["voice_settings"] = self.voice_settings
        return p

    def synth(self, prompt, wav):
        """Return raw PCM bytes for one clip. Retries transient failures."""
        params = self.request_params(prompt, wav)
        last = None
        for attempt in range(self.max_retries):
            try:
                chunks = self.client.text_to_speech.convert(**params)
                data = b"".join(chunks)
                if not data:
                    raise SynthError("empty response")
                return data
            except Exception as exc:  # noqa: BLE001 - re-raised below
                last = exc
                if attempt == self.max_retries - 1 or not _is_retryable(exc):
                    break
                # exponential backoff with jitter, so parallel workers that hit
                # the same rate limit do not retry in lockstep
                delay = self.base_delay * (2 ** attempt)
                time.sleep(delay * (0.5 + random.random()))
        raise SynthError("%s failed after %d attempt(s): %s: %s"
                         % (wav, self.max_retries, type(last).__name__, last))
