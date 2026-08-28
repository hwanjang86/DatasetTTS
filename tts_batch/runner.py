# -*- coding: utf-8 -*-
"""Drive a whole script through the API: parallel, resumable, auditable."""

import csv
import io
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from .audio import analyze, load_wav, pcm_to_wav, trim_silence
from .normalize import build_prompt, normalize
from .parser import parse_file, voice_name
from .synth import SynthError


class Cancelled(Exception):
    """Raised inside a worker when the run has been asked to stop."""


class Progress:
    """Single-line progress with a running failure count."""

    def __init__(self, total, on_tick=None, quiet=False):
        self.total = total
        self.done = 0
        self.failed = 0
        self.skipped = 0
        self.flagged = 0
        self.start = time.time()
        self.lock = threading.Lock()
        # The web UI subscribes here; the CLI leaves it None and prints instead.
        self.on_tick = on_tick
        self.quiet = quiet

    def snapshot(self):
        elapsed = time.time() - self.start
        rate = self.done / elapsed if elapsed else 0
        return {"done": self.done, "total": self.total, "failed": self.failed,
                "skipped": self.skipped, "flagged": self.flagged,
                "rate": round(rate, 2), "elapsed": round(elapsed, 1),
                "eta": round((self.total - self.done) / rate, 1) if rate else None}

    def tick(self, failed=False, skipped=False, flagged=False):
        with self.lock:
            self.done += 1
            self.failed += bool(failed)
            self.skipped += bool(skipped)
            self.flagged += bool(flagged)
            if self.on_tick:
                self.on_tick(self.snapshot())
            if not self.quiet and (self.done % 10 == 0 or self.done == self.total):
                self._draw()

    def _draw(self):
        elapsed = time.time() - self.start
        rate = self.done / elapsed if elapsed else 0
        left = (self.total - self.done) / rate if rate else 0
        line = ("  %d/%d  skip %d  fail %d  flag %d  |  %.1f/s  eta %dm%02ds"
                % (self.done, self.total, self.skipped, self.failed,
                   self.flagged, rate, int(left // 60), int(left % 60)))
        print(line, flush=True)


def _wav_is_valid(path, min_bytes=2048):
    if not os.path.exists(path) or os.path.getsize(path) < min_bytes:
        return False
    try:
        samples, _ = load_wav(path)
        return len(samples) > 0
    except Exception:
        return False


def run(script_path, out_dir, synthesizer, sample_rate=24000,
        drop_tags=("neutral",), concurrency=5, force=False, limit=None,
        only=None, trim_ms=None, on_tick=None, quiet=False, should_stop=None):
    """Generate every clip in `script_path` into `out_dir`.

    Existing valid clips are skipped unless `force`, so an interrupted run can
    be resumed without paying for the same audio twice.
    """
    entries = parse_file(script_path)
    voice = voice_name(script_path)
    if only:
        wanted = set(only)
        entries = [e for e in entries if e.wav in wanted]
    if limit:
        entries = entries[:limit]

    wav_dir = os.path.join(out_dir, "wavs")
    os.makedirs(wav_dir, exist_ok=True)

    todo = []
    skipped = []
    for e in entries:
        path = os.path.join(wav_dir, e.wav)
        if not force and _wav_is_valid(path):
            skipped.append(e)
        else:
            todo.append(e)

    if not quiet:
        print("lines: %d   to generate: %d   already done: %d"
              % (len(entries), len(todo), len(skipped)))
    if not todo:
        return {"generated": 0, "skipped": len(skipped), "failed": 0,
                "flagged": 0}

    progress = Progress(len(todo), on_tick=on_tick, quiet=quiet)
    results = []
    failures = []
    lock = threading.Lock()

    def work(e):
        if should_stop is not None and should_stop():
            raise Cancelled(e.wav)
        norm = normalize(e.text)
        prompt = build_prompt(e.emotion, norm, drop_tags)
        path = os.path.join(wav_dir, e.wav)
        pcm = synthesizer.synth(prompt, e.wav)
        if trim_ms is not None:
            samples = np.frombuffer(pcm[: (len(pcm) // 2) * 2], dtype="<i2")
            pcm = trim_silence(samples, sample_rate, trim_ms).tobytes()
        pcm_to_wav(path, pcm, sample_rate)
        samples, sr = load_wav(path)
        qc = analyze(samples, sr, text=norm)
        return {
            "wav": e.wav,
            "emotion": e.emotion,
            "raw_text": e.text,
            "text": norm,
            "prompt": prompt,
            "model_id": synthesizer.model_id,
            "seed": synthesizer.request_params(prompt, e.wav)["seed"],
            "duration": round(qc["duration"], 3),
            "peak": qc["peak"],
            "rms": round(qc["rms"], 1),
            "lead_silence": round(qc["lead_silence"], 3),
            "tail_silence": round(qc["tail_silence"], 3),
            "flags": qc["flags"],
        }

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(work, e): e for e in todo}
        for fut in as_completed(futures):
            e = futures[fut]
            try:
                rec = fut.result()
            except Cancelled:
                progress.tick(skipped=True)
                continue
            except (SynthError, Exception) as exc:  # noqa: BLE001
                with lock:
                    failures.append({"wav": e.wav, "text": e.text,
                                     "error": "%s: %s" % (type(exc).__name__, exc)})
                progress.tick(failed=True)
                continue
            with lock:
                results.append(rec)
            progress.tick(flagged=bool(rec["flags"]))

    _write_outputs(out_dir, voice, results, failures, skipped, script_path)
    return {"generated": len(results), "skipped": len(skipped),
            "failed": len(failures),
            "flagged": sum(1 for r in results if r["flags"])}


def _write_outputs(out_dir, voice, results, failures, skipped, script_path):
    results.sort(key=lambda r: r["wav"])

    # append-mode manifest so resumed runs accumulate rather than overwrite
    with io.open(os.path.join(out_dir, "manifest.jsonl"), "a",
                 encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # LJSpeech-style: id | transcript. The transcript is the normalized text,
    # which is exactly what the voice says.
    meta = os.path.join(out_dir, "metadata.csv")
    existing = {}
    if os.path.exists(meta):
        with io.open(meta, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter="|"):
                if row:
                    existing[row[0]] = row
    for r in results:
        existing[os.path.splitext(r["wav"])[0]] = [
            os.path.splitext(r["wav"])[0], r["text"], r["emotion"]]
    with io.open(meta, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="|", lineterminator="\n")
        for key in sorted(existing):
            w.writerow(existing[key])

    # Every clip touched this run gets its stale row dropped first. Appending
    # blindly would leave a retried clip listed as broken after it was fixed,
    # so the next `retry --include-flagged` would regenerate it again.
    touched = {r["wav"] for r in results} | {f["wav"] for f in failures}

    _merge_csv(os.path.join(out_dir, "failures.csv"),
               ["wav", "text", "error"], touched,
               [[f["wav"], f["text"], f["error"]] for f in failures])

    _merge_csv(os.path.join(out_dir, "qc_flags.csv"),
               ["wav", "flags", "duration", "tail_silence", "text"], touched,
               [[r["wav"], ";".join(r["flags"]), r["duration"],
                 r["tail_silence"], r["text"]]
                for r in results if r["flags"]])


def _merge_csv(path, header, drop_keys, rows):
    """Rewrite a keyed-by-wav report: drop stale rows, add current ones."""
    kept = []
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as fh:
            reader = csv.reader(fh)
            next(reader, None)
            kept = [r for r in reader if r and r[0] not in drop_keys]
    if not kept and not rows:
        if os.path.exists(path):
            os.remove(path)
        return
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in sorted(kept + rows, key=lambda x: x[0]):
            w.writerow(r)
