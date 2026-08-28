# -*- coding: utf-8 -*-
"""Enforce the edge-silence invariant on files already on disk.

The trim applied during generation did not stick on 39 of 4,211 clips and the
cause was not reproducible -- a fresh call to `trim_silence` on the same text
trimmed correctly. Rather than trust the generate-time pass, this re-checks
every stored clip and repairs the ones out of spec. It costs nothing, needs no
API call, and is idempotent, so it is safe to run after every batch.
"""

import io
import json
import os
import shutil

from .audio import (analyze, load_wav, pcm_to_wav,
                    strip_trailing_artifact, trim_silence)


def check(out_dir, pad_ms=50, tolerance_ms=40):
    """Return (path, lead, tail) for every clip outside the pad tolerance."""
    wav_dir = os.path.join(out_dir, "wavs")
    limit = (pad_ms + tolerance_ms) / 1000.0
    bad = []
    for name in sorted(os.listdir(wav_dir)):
        if not name.endswith(".wav"):
            continue
        path = os.path.join(wav_dir, name)
        samples, sr = load_wav(path)
        r = analyze(samples, sr)
        if r["lead_silence"] > limit or r["tail_silence"] > limit:
            bad.append((name, r["lead_silence"], r["tail_silence"]))
    return bad


def repair(out_dir, pad_ms=50, tolerance_ms=40):
    """Re-trim every out-of-spec clip in place. Returns the list repaired."""
    wav_dir = os.path.join(out_dir, "wavs")
    fixed = []
    for name, lead, tail in check(out_dir, pad_ms, tolerance_ms):
        path = os.path.join(wav_dir, name)
        samples, sr = load_wav(path)
        before = len(samples) / float(sr)
        trimmed = trim_silence(samples, sr, pad_ms)
        if len(trimmed) >= len(samples):
            continue  # nothing to cut; leave it alone
        pcm_to_wav(path, trimmed.tobytes(), sr)
        after = len(trimmed) / float(sr)
        fixed.append((name, before, after, lead, tail))
    if fixed:
        _refresh_manifest(out_dir, {f[0] for f in fixed})
    return fixed


def _refresh_manifest(out_dir, names):
    """Rewrite the measurements of repaired clips so the manifest stays true."""
    path = os.path.join(out_dir, "manifest.jsonl")
    if not os.path.exists(path):
        return
    wav_dir = os.path.join(out_dir, "wavs")
    lines = []
    for raw in io.open(path, encoding="utf-8"):
        r = json.loads(raw)
        if r["wav"] in names:
            samples, sr = load_wav(os.path.join(wav_dir, r["wav"]))
            q = analyze(samples, sr, text=r.get("text"))
            r.update(duration=round(q["duration"], 3), peak=q["peak"],
                     rms=round(q["rms"], 1),
                     lead_silence=round(q["lead_silence"], 3),
                     tail_silence=round(q["tail_silence"], 3),
                     flags=q["flags"], retrimmed=True)
        lines.append(json.dumps(r, ensure_ascii=False))
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def declick(out_dir, pad_ms=50, dry_run=False):
    """Cut the trailing tick off every clip that carries one.

    Cheaper and less disruptive than regenerating: the take stays what it was,
    only the stray sound at the end goes. Each clip is cut at its own detected
    utterance end, because the silence before the tick varies fivefold across
    the corpus while the tick itself does not.

    Returns [(name, before, after, removed_ms)] for the clips it changed.
    """
    wav_dir = os.path.join(out_dir, "wavs")
    changed = []
    for name in sorted(os.listdir(wav_dir)):
        if not name.endswith(".wav"):
            continue
        path = os.path.join(wav_dir, name)
        samples, sr = load_wav(path)
        stripped = strip_trailing_artifact(samples, sr, pad_ms)
        if stripped is None or len(stripped) >= len(samples):
            continue
        before = len(samples) / float(sr)
        after = len(stripped) / float(sr)
        if not dry_run:
            # Keep the original. The threshold that decides tick-vs-word got
            # this wrong once already (a 480ms final word read as a tick), and
            # without a copy there is nothing to restore from.
            backup = os.path.join(out_dir, "originals")
            os.makedirs(backup, exist_ok=True)
            dest = os.path.join(backup, name)
            if not os.path.exists(dest):
                shutil.copy2(path, dest)
            pcm_to_wav(path, stripped.tobytes(), sr)
        changed.append((name, before, after, (before - after) * 1000))
    if changed and not dry_run:
        _refresh_manifest(out_dir, {c[0] for c in changed})
        _refresh_flags(out_dir, {c[0] for c in changed})
    return changed


def _refresh_flags(out_dir, names):
    """Drop clips from qc_flags.csv once they measure clean again."""
    import csv

    path = os.path.join(out_dir, "qc_flags.csv")
    if not os.path.exists(path):
        return
    wav_dir = os.path.join(out_dir, "wavs")
    with io.open(path, encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        rows = [r for r in reader if r]
    kept = []
    for r in rows:
        if r[0] in names:
            samples, sr = load_wav(os.path.join(wav_dir, r[0]))
            q = analyze(samples, sr)
            if not q["flags"]:
                continue
            r = [r[0], ";".join(q["flags"]), round(q["duration"], 3),
                 round(q["tail_silence"], 3), r[-1]]
        kept.append(r)
    if not kept:
        os.remove(path)
        return
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in kept:
            w.writerow(r)


def restore(out_dir, names=None):
    """Put back the pre-declick originals. Returns the clips restored."""
    backup = os.path.join(out_dir, "originals")
    wav_dir = os.path.join(out_dir, "wavs")
    if not os.path.isdir(backup):
        return []
    done = []
    for name in sorted(os.listdir(backup)):
        if not name.endswith(".wav"):
            continue
        if names is not None and name not in names:
            continue
        shutil.copy2(os.path.join(backup, name), os.path.join(wav_dir, name))
        os.remove(os.path.join(backup, name))
        done.append(name)
    if done:
        _refresh_manifest(out_dir, set(done))
    return done


def has_original(out_dir, name):
    return os.path.exists(os.path.join(out_dir, "originals", name))
