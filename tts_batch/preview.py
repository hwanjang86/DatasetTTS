# -*- coding: utf-8 -*-
"""Dry-run the text pipeline over a whole script. Makes no API calls.

Writes three review artifacts next to the output directory:
  preview.txt       every line, original -> normalized
  changes.txt       only the lines the pipeline altered
  josa_changes.txt  every distinct particle correction, with counts

The particle report is the safety check: it is the one pass that rewrites words
rather than just spelling out digits, so each distinct change is listed for a
human to confirm before any credits are spent.
"""

import io
import os
from collections import Counter

from .normalize import build_prompt, expand_numbers, fix_josa, normalize
from .parser import parse_file, voice_name


# Syllables that only ever appear inside a spoken number. A correction whose
# stem is built solely from these is mechanical and safe; anything else is a
# real word being rewritten and has to be read by a human.
NUMERAL_SYLLABLES = set(
    "영일이삼사오육칠팔구십백천만억"          # Sino-Korean
    "한두세네다섯여섯일곱여덟아홉열스무물서른마흔쉰예순일흔여든아흔"  # native
)


def _is_numeral_stem(token):
    stem = token[:-1]
    return bool(stem) and all(ch in NUMERAL_SYLLABLES for ch in stem)


def _token_diff(before, after):
    """Pair up tokens that differ between two versions of a line.

    Only ever called on the pair (after digit expansion, after particle fix),
    which have identical token counts -- diffing against the raw line would
    silently skip every line whose token count changed when digits expanded.
    """
    b, a = before.split(), after.split()
    assert len(b) == len(a), "token count must be stable across the josa pass"
    return [(x, y) for x, y in zip(b, a) if x != y]


def run(script_path, out_dir, drop_tags=("neutral",)):
    entries = parse_file(script_path)
    voice = voice_name(script_path)
    os.makedirs(out_dir, exist_ok=True)

    changed = []
    josa_pairs = Counter()
    josa_examples = {}
    total_chars = 0
    body_chars = 0
    emotions = Counter()

    prev_path = os.path.join(out_dir, "preview.txt")
    with io.open(prev_path, "w", encoding="utf-8") as fh:
        fh.write("# voice: %s\n# script: %s\n# lines: %d\n\n"
                 % (voice, script_path, len(entries)))
        for e in entries:
            expanded = expand_numbers(e.text)
            norm = fix_josa(expanded)
            prompt = build_prompt(e.emotion, norm, drop_tags)
            total_chars += len(prompt)
            body_chars += len(norm)
            emotions[e.emotion] += 1

            fh.write("%s  [%s]\n" % (e.wav, e.emotion))
            fh.write("  raw : %s\n" % e.text)
            fh.write("  say : %s\n" % norm)
            fh.write("  send: %s\n\n" % prompt)

            if norm != e.text:
                changed.append((e.wav, e.emotion, e.text, norm))
            # Audit the particle pass in isolation: digit expansion is
            # mechanical, but rewriting a word can corrupt a real noun.
            for x, y in _token_diff(expanded, norm):
                josa_pairs[(x, y)] += 1
                josa_examples.setdefault((x, y), (e.wav, e.text, norm))

    with io.open(os.path.join(out_dir, "changes.txt"), "w", encoding="utf-8") as fh:
        fh.write("# %d of %d lines changed\n\n" % (len(changed), len(entries)))
        for wav, emo, raw, norm in changed:
            fh.write("%s  [%s]\n  - %s\n  + %s\n\n" % (wav, emo, raw, norm))

    word_pairs = Counter({k: v for k, v in josa_pairs.items()
                          if not _is_numeral_stem(k[0])})
    num_pairs = Counter({k: v for k, v in josa_pairs.items()
                         if _is_numeral_stem(k[0])})

    with io.open(os.path.join(out_dir, "josa_changes.txt"), "w", encoding="utf-8") as fh:
        fh.write("# WORD REWRITES -- REVIEW EVERY ONE.\n")
        fh.write("# A real noun mis-parsed as noun+particle gets corrupted here.\n\n")
        for (x, y), n in word_pairs.most_common():
            wav, raw, norm = josa_examples[(x, y)]
            fh.write("%-14s -> %-14s  x%-4d  (%s)\n" % (x, y, n, wav))
            fh.write("    - %s\n    + %s\n\n" % (raw, norm))
        fh.write("\n\n# NUMBER-ADJACENT (mechanical, %d occurrences)\n\n"
                 % sum(num_pairs.values()))
        for (x, y), n in num_pairs.most_common():
            fh.write("%-16s -> %-16s x%d\n" % (x, y, n))

    return {
        "voice": voice,
        "entries": len(entries),
        "changed": len(changed),
        "josa_kinds": len(josa_pairs),
        "josa_total": sum(josa_pairs.values()),
        "word_pairs": word_pairs,
        "num_pairs": num_pairs,
        "body_chars": body_chars,
        "billed_chars": total_chars,
        "emotions": emotions,
        "josa_pairs": josa_pairs,
        "out_dir": out_dir,
    }


def billed_chars(script_path, drop_tags=("neutral",), limit=None):
    """Characters this run will actually be charged for.

    Kept separate from `run`, which always covers the whole script so the
    review artifacts stay complete: a --limit pilot must quote the cost of the
    clips it is about to make, not of the file it read.
    """
    entries = parse_file(script_path)
    if limit:
        entries = entries[:limit]
    return sum(len(build_prompt(e.emotion, normalize(e.text), drop_tags))
               for e in entries)
