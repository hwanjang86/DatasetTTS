# -*- coding: utf-8 -*-
"""Command line entry point.

    python -m tts_batch preview  Closers_Android.txt
    python -m tts_batch estimate Closers_Android.txt
    python -m tts_batch generate Closers_Android.txt --limit 50
    python -m tts_batch generate Closers_Android.txt
    python -m tts_batch retry    Closers_Android.txt
"""

import argparse
import csv
import io
import os
import sys

DEFAULTS = {
    "model_id": "eleven_v3",
    "output_format": "pcm_24000",
    "sample_rate": 24000,
    "normalization": "off",   # we normalize ourselves; see normalize.py
    "drop_tags": ["neutral"],
    "concurrency": 5,         # Creator tier allows 5 concurrent requests
    "max_retries": 5,
    "trim_ms": 50,            # pad edge silence to a constant 50ms
}


def _out_dir(script_path, root):
    from .parser import voice_name
    return os.path.join(root, voice_name(script_path))


def _client():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("ELEVENLABS_API_KEY is not set in the environment.")
    from elevenlabs.client import ElevenLabs
    return ElevenLabs(api_key=key)


def _resolve_voice(client, name):
    """Map a script filename to the ElevenLabs voice of the same name."""
    res = client.voices.search(search=name, page_size=100)
    for v in res.voices:
        if v.name == name:
            return v.voice_id
    names = ", ".join(v.name for v in res.voices[:10]) or "(none)"
    sys.exit("No voice named %r in this account. Closest matches: %s"
             % (name, names))


def cmd_preview(args):
    from .preview import run
    r = run(args.script, _out_dir(args.script, args.out), DEFAULTS["drop_tags"])
    print("lines %d | changed %d | particle fixes %d"
          % (r["entries"], r["changed"], r["josa_total"]))
    print("billed characters if generated now: %d" % r["billed_chars"])
    print("\nWORD REWRITES -- read these before generating:")
    for (x, y), n in r["word_pairs"].most_common():
        print("   %-14s -> %-14s x%d" % (x, y, n))
    print("\nartifacts: %s" % r["out_dir"])


def cmd_estimate(args):
    from .preview import run
    client = _client()
    sub = client.user.subscription.get()
    r = run(args.script, _out_dir(args.script, args.out), DEFAULTS["drop_tags"])
    remaining = sub.character_limit - sub.character_count
    need = r["billed_chars"]
    print("tier            : %s" % sub.tier)
    print("used / limit    : %d / %d" % (sub.character_count, sub.character_limit))
    print("remaining       : %d" % remaining)
    print("this run needs  : %d" % need)
    print("after the run   : %d" % (remaining - need))
    if need > remaining:
        print("\nNOT ENOUGH CREDIT: short by %d characters." % (need - remaining))
        return 1
    return 0


def cmd_generate(args):
    from .preview import billed_chars, run as preview_run
    from .runner import run
    from .synth import Synthesizer
    from .parser import voice_name

    out = _out_dir(args.script, args.out)
    client = _client()
    voice = voice_name(args.script)
    voice_id = _resolve_voice(client, voice)

    pv = preview_run(args.script, out, DEFAULTS["drop_tags"])
    sub = client.user.subscription.get()
    remaining = sub.character_limit - sub.character_count
    # quote the cost of the clips about to be made, not of the whole file
    need = billed_chars(args.script, DEFAULTS["drop_tags"], args.limit)

    print("voice      : %s (%s)" % (voice, voice_id))
    print("model      : %s" % DEFAULTS["model_id"])
    print("lines      : %d%s" % (pv["entries"],
                                 "  (limited to %d)" % args.limit if args.limit else ""))
    print("characters : %d billed%s"
          % (need, "  (whole script: %d)" % pv["billed_chars"]
             if args.limit else ""))
    print("credit     : %d remaining" % remaining)
    print("edge silence: %s" % ("padded to %dms" % args.trim if args.trim
                                else "left as generated"))
    print()

    if not args.yes:
        reply = input("Generate now? This spends credits. [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("aborted.")
            return 1

    synth = Synthesizer(
        client, voice_id, voice,
        model_id=DEFAULTS["model_id"],
        output_format=DEFAULTS["output_format"],
        normalization=DEFAULTS["normalization"],
        max_retries=DEFAULTS["max_retries"],
    )
    summary = run(args.script, out, synth,
                  sample_rate=DEFAULTS["sample_rate"],
                  drop_tags=DEFAULTS["drop_tags"],
                  concurrency=args.concurrency,
                  force=args.force, limit=args.limit,
                  trim_ms=args.trim or None)
    print("\ngenerated %(generated)d | skipped %(skipped)d | "
          "failed %(failed)d | flagged %(flagged)d" % summary)
    if summary["flagged"]:
        print("review %s" % os.path.join(out, "qc_flags.csv"))
    if summary["failed"]:
        print("retry with: python -m tts_batch retry %s" % args.script)
    return 0


def cmd_retry(args):
    """Regenerate only the clips in failures.csv and qc_flags.csv."""
    from .runner import run
    from .synth import Synthesizer
    from .parser import voice_name

    out = _out_dir(args.script, args.out)
    targets = set()
    for name, col in (("failures.csv", "wav"), ("qc_flags.csv", "wav")):
        path = os.path.join(out, name)
        if not os.path.exists(path):
            continue
        if name == "qc_flags.csv" and not args.include_flagged:
            continue
        with io.open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                targets.add(row[col])
    if not targets:
        print("nothing to retry.")
        return 0

    print("retrying %d clip(s)" % len(targets))
    client = _client()
    voice = voice_name(args.script)
    synth = Synthesizer(client, _resolve_voice(client, voice), voice,
                        model_id=DEFAULTS["model_id"],
                        output_format=DEFAULTS["output_format"],
                        normalization=DEFAULTS["normalization"],
                        seed_offset=args.take)
    summary = run(args.script, out, synth,
                  sample_rate=DEFAULTS["sample_rate"],
                  drop_tags=DEFAULTS["drop_tags"],
                  concurrency=args.concurrency,
                  force=True, only=targets,
                  trim_ms=DEFAULTS["trim_ms"] or None)
    print("\nregenerated %(generated)d | failed %(failed)d | "
          "flagged %(flagged)d" % summary)
    return 0


def cmd_retrim(args):
    from .retrim import check, repair
    out = _out_dir(args.script, args.out)
    if args.check:
        bad = check(out, args.pad)
        print("out of spec: %d clip(s)" % len(bad))
        for name, lead, tail in bad[:25]:
            print("   %-12s lead %.3f  tail %.3f" % (name, lead, tail))
        return 0
    fixed = repair(out, args.pad)
    print("repaired %d clip(s)" % len(fixed))
    for name, before, after, lead, tail in fixed[:25]:
        print("   %-12s %.2fs -> %.2fs   (tail was %.3f)"
              % (name, before, after, tail))
    return 0


def cmd_declick(args):
    from .retrim import declick
    out = _out_dir(args.script, args.out)
    changed = declick(out, args.pad, dry_run=args.check)
    verb = "would cut" if args.check else "cut"
    print("%s %d clip(s)" % (verb, len(changed)))
    for name, before, after, removed in changed[:60]:
        print("   %-12s %.2fs -> %.2fs   (-%.0fms)" % (name, before, after, removed))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="tts_batch")
    p.add_argument("--out", default="output", help="output root (default: output)")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn, helptext in (
        ("preview", cmd_preview, "normalize the script and report; no API calls"),
        ("estimate", cmd_estimate, "compare the run's cost against remaining credit"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("script")
        sp.set_defaults(func=fn)

    sp = sub.add_parser("generate", help="synthesize the script")
    sp.add_argument("script")
    sp.add_argument("--limit", type=int, help="only the first N lines (pilot run)")
    sp.add_argument("--concurrency", type=int, default=DEFAULTS["concurrency"])
    sp.add_argument("--force", action="store_true", help="regenerate existing clips")
    sp.add_argument("-y", "--yes", action="store_true", help="skip the cost prompt")
    sp.add_argument("--trim", type=int, metavar="MS",
                    default=DEFAULTS["trim_ms"],
                    help="pad leading/trailing silence to MS milliseconds "
                         "(default %(default)s; 0 keeps the audio as generated)")
    sp.set_defaults(func=cmd_generate)

    sp = sub.add_parser("retrim", help="re-check stored clips for the edge-silence "
                                       "invariant and repair any that drifted")
    sp.add_argument("script")
    sp.add_argument("--pad", type=int, default=DEFAULTS["trim_ms"])
    sp.add_argument("--check", action="store_true", help="report only, change nothing")
    sp.set_defaults(func=cmd_retrim)

    sp = sub.add_parser("declick", help="cut the trailing tick off flagged clips "
                                        "instead of regenerating them")
    sp.add_argument("script")
    sp.add_argument("--pad", type=int, default=DEFAULTS["trim_ms"])
    sp.add_argument("--check", action="store_true", help="report only, change nothing")
    sp.set_defaults(func=cmd_declick)

    sp = sub.add_parser("retry", help="regenerate failed (and optionally flagged) clips")
    sp.add_argument("script")
    sp.add_argument("--include-flagged", action="store_true",
                    help="also redo clips with QC flags such as trailing_artifact")
    sp.add_argument("--take", type=int, default=1,
                    help="seed offset (default 1). The same take reproduces the "
                         "same audio, so raise it for another attempt at a clip "
                         "that keeps coming back flawed.")
    sp.add_argument("--concurrency", type=int, default=DEFAULTS["concurrency"])
    sp.set_defaults(func=cmd_retry)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
