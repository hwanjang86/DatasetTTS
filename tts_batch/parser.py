# -*- coding: utf-8 -*-
"""Read a voice script: one line per clip.

    (행복)[happy] 오늘은 정말 즐거운 날이에요! | 0001.wav
     |     |
     |     +-- audio tag, sent inline to the model
     +-------- style, decides which folder the clip is filed under

Both tags are optional, so the older `[neutral] 텍스트 | 0001.wav` form still
parses. The style never reaches the API -- it is a filing label, so it costs
no characters.
"""

import io
import os
import re
from collections import namedtuple

LINE_RE = re.compile(
    r"^(?:\((?P<style>[^)]*)\))?\s*"
    r"(?:\[(?P<emotion>[^\]]*)\])?\s*"
    r"(?P<text>.+?)\s*\|\s*(?P<wav>\S+\.wav)\s*$"
)

# Characters a folder name may not contain, plus the names Windows reserves.
BAD_STYLE_CHARS = set('/\\:*?"<>|')
RESERVED = {"con", "prn", "aux", "nul"} | {
    "%s%d" % (p, i) for p in ("com", "lpt") for i in range(1, 10)}

Entry = namedtuple("Entry", "lineno style emotion text wav")


class ScriptError(ValueError):
    pass


def check_style(style):
    """Return an error message if `style` is unusable as a folder name."""
    if style is None:
        return None
    if not style:
        return "empty style tag"
    if len(style) > 40:
        return "style tag too long: %r" % style[:20]
    if any(c in BAD_STYLE_CHARS for c in style):
        return "style tag has a path character: %r" % style
    if any(ord(c) < 32 for c in style):
        return "style tag has a control character"
    if style in (".", "..") or style.strip(". ") == "":
        return "style tag is not a usable folder name: %r" % style
    if style.lower() in RESERVED:
        return "style tag is a reserved name on Windows: %r" % style
    return None


def parse_file(path):
    """Parse a script file. Raises ScriptError listing every bad line.

    The voice name is the file's basename, e.g. Closers_Android.txt is spoken
    by the ElevenLabs voice named Closers_Android.
    """
    entries = []
    bad = []
    seen = {}
    with io.open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            m = LINE_RE.match(line)
            if not m:
                bad.append((lineno, line))
                continue

            style = m.group("style")
            if style is not None:
                style = style.strip()
            problem = check_style(style)
            if problem:
                bad.append((lineno, problem))
                continue

            wav = m.group("wav")
            if os.path.basename(wav) != wav:
                bad.append((lineno, "wav name may not contain a path: %s" % wav))
                continue
            if wav in seen:
                bad.append((lineno, "duplicate wav %s (first at line %d)"
                            % (wav, seen[wav])))
                continue
            seen[wav] = lineno

            emotion = m.group("emotion")
            emotion = emotion.strip() if emotion else "neutral"
            entries.append(Entry(lineno, style, emotion, m.group("text"), wav))

    if bad:
        detail = "\n".join("  line %d: %s" % (n, s) for n, s in bad[:20])
        more = "\n  ... and %d more" % (len(bad) - 20) if len(bad) > 20 else ""
        raise ScriptError("%s: %d unparsable line(s)\n%s%s"
                          % (path, len(bad), detail, more))
    return entries


def voice_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def rel_path(entry):
    """Where the clip lives under wavs/ -- 행복/0001.wav, or just 0001.wav."""
    return "%s/%s" % (entry.style, entry.wav) if entry.style else entry.wav


def clip_path(wav_dir, entry):
    if entry.style:
        return os.path.join(wav_dir, entry.style, entry.wav)
    return os.path.join(wav_dir, entry.wav)


def iter_clips(wav_dir):
    """Yield every clip under wavs/ as a path relative to it, styles included."""
    if not os.path.isdir(wav_dir):
        return
    for root, dirs, files in os.walk(wav_dir):
        dirs.sort()
        for name in sorted(files):
            if name.endswith(".wav"):
                rel = os.path.relpath(os.path.join(root, name), wav_dir)
                yield rel.replace(os.sep, "/")
