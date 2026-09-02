# -*- coding: utf-8 -*-
"""Repairs applied to clips already on disk. No API calls, no cost.

Every function walks wavs/ recursively, so a clip filed under a style folder
(wavs/행복/0001.wav) is treated the same as one at the top level. Paths are
handled relative to wavs/ throughout, which is also how manifest.jsonl and
metadata.csv identify a clip.
"""

import csv
import io
import json
import os
import shutil

from .audio import (analyze, load_wav, pcm_to_wav, strip_trailing_artifact,
                    trim_silence)
from .parser import iter_clips


def _wav_path(out_dir, rel):
    return os.path.join(out_dir, "wavs", rel.replace("/", os.sep))


def _original_path(out_dir, rel):
    return os.path.join(out_dir, "originals", rel.replace("/", os.sep))


def check(out_dir, pad_ms=50, tolerance_ms=40):
    """Return (rel_path, lead, tail) for every clip outside the pad tolerance."""
    limit = (pad_ms + tolerance_ms) / 1000.0
    bad = []
    for rel in iter_clips(os.path.join(out_dir, "wavs")):
        samples, sr = load_wav(_wav_path(out_dir, rel))
        r = analyze(samples, sr)
        if r["lead_silence"] > limit or r["tail_silence"] > limit:
            bad.append((rel, r["lead_silence"], r["tail_silence"]))
    return bad


def repair(out_dir, pad_ms=50, tolerance_ms=40):
    """Re-trim every out-of-spec clip in place.

    The trim applied during generation did not stick on 39 of 4,211 clips once
    and the cause was not reproducible -- a fresh call to `trim_silence` on the
    same text trimmed correctly. So the invariant is enforced after the fact
    rather than trusted. Idempotent; safe to run after every batch.
    """
    fixed = []
    for rel, lead, tail in check(out_dir, pad_ms, tolerance_ms):
        path = _wav_path(out_dir, rel)
        samples, sr = load_wav(path)
        before = len(samples) / float(sr)
        trimmed = trim_silence(samples, sr, pad_ms)
        if len(trimmed) >= len(samples):
            continue  # nothing to cut; leave it alone
        pcm_to_wav(path, trimmed.tobytes(), sr)
        fixed.append((rel, before, len(trimmed) / float(sr), lead, tail))
    if fixed:
        _refresh_manifest(out_dir, {f[0] for f in fixed})
    return fixed


def declick(out_dir, pad_ms=50, dry_run=False):
    """Cut the trailing tick off every clip that carries one.

    Cheaper and less disruptive than regenerating: the take stays what it was,
    only the stray sound at the end goes. Each clip is cut at its own detected
    utterance end, because the silence before the tick varies fivefold across
    the corpus while the tick itself does not.

    Returns [(rel_path, before, after, removed_ms)] for the clips it changed.
    """
    changed = []
    for rel in iter_clips(os.path.join(out_dir, "wavs")):
        path = _wav_path(out_dir, rel)
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
            dest = _original_path(out_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if not os.path.exists(dest):
                shutil.copy2(path, dest)
            pcm_to_wav(path, stripped.tobytes(), sr)
        changed.append((rel, before, after, (before - after) * 1000))
    if changed and not dry_run:
        names = {c[0] for c in changed}
        _refresh_manifest(out_dir, names)
        _refresh_flags(out_dir, names)
    return changed


def restore(out_dir, names=None):
    """Put back the pre-declick originals. Returns the clips restored."""
    backup = os.path.join(out_dir, "originals")
    if not os.path.isdir(backup):
        return []
    done = []
    for rel in list(iter_clips(backup)):
        if names is not None and rel not in names:
            continue
        src = _original_path(out_dir, rel)
        shutil.copy2(src, _wav_path(out_dir, rel))
        os.remove(src)
        done.append(rel)
    if done:
        _prune_empty(backup)
        _refresh_manifest(out_dir, set(done))
    return done


def has_original(out_dir, rel):
    return os.path.exists(_original_path(out_dir, rel))


def _prune_empty(root):
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath != root and not dirnames and not filenames:
            os.rmdir(dirpath)


def _manifest_key(record):
    """Clips are identified by their path under wavs/, styles included."""
    return record.get("path") or record.get("wav")


def _refresh_manifest(out_dir, rels):
    """Rewrite the measurements of repaired clips so the manifest stays true."""
    path = os.path.join(out_dir, "manifest.jsonl")
    if not os.path.exists(path):
        return
    lines = []
    for raw in io.open(path, encoding="utf-8"):
        raw = raw.strip()
        if not raw:
            continue
        r = json.loads(raw)
        if _manifest_key(r) in rels:
            samples, sr = load_wav(_wav_path(out_dir, _manifest_key(r)))
            q = analyze(samples, sr, text=r.get("text"))
            r.update(duration=round(q["duration"], 3), peak=q["peak"],
                     rms=round(q["rms"], 1),
                     lead_silence=round(q["lead_silence"], 3),
                     tail_silence=round(q["tail_silence"], 3),
                     flags=q["flags"])
        lines.append(json.dumps(r, ensure_ascii=False))
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def _refresh_flags(out_dir, rels):
    """Drop clips from qc_flags.csv once they measure clean again."""
    path = os.path.join(out_dir, "qc_flags.csv")
    if not os.path.exists(path):
        return
    by_name = {os.path.basename(r): r for r in rels}
    with io.open(path, encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        rows = [r for r in reader if r]
    kept = []
    for row in rows:
        rel = by_name.get(row[0])
        if rel is not None:
            samples, sr = load_wav(_wav_path(out_dir, rel))
            q = analyze(samples, sr)
            if not q["flags"]:
                continue
            row = [row[0], ";".join(q["flags"]), round(q["duration"], 3),
                   round(q["tail_silence"], 3), row[-1]]
        kept.append(row)
    if not kept:
        os.remove(path)
        return
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for row in kept:
            w.writerow(row)
