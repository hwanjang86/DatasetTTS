# -*- coding: utf-8 -*-
"""Read a voice script: one line per clip, `[emotion] text | NNNN.wav`."""

import io
import os
import re
from collections import namedtuple

LINE_RE = re.compile(
    r"^\[(?P<emotion>[^\]]+)\]\s*(?P<text>.+?)\s*\|\s*(?P<wav>\S+\.wav)\s*$"
)

Entry = namedtuple("Entry", "lineno emotion text wav")


class ScriptError(ValueError):
    pass


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
            wav = m.group("wav")
            if wav in seen:
                bad.append((lineno, "duplicate wav %s (first at line %d)"
                            % (wav, seen[wav])))
                continue
            seen[wav] = lineno
            entries.append(Entry(lineno, m.group("emotion"),
                                 m.group("text"), wav))
    if bad:
        detail = "\n".join("  line %d: %s" % (n, s) for n, s in bad[:20])
        more = "\n  ... and %d more" % (len(bad) - 20) if len(bad) > 20 else ""
        raise ScriptError("%s: %d unparsable line(s)\n%s%s"
                          % (path, len(bad), detail, more))
    return entries


def voice_name(path):
    return os.path.splitext(os.path.basename(path))[0]
