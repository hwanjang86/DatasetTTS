# -*- coding: utf-8 -*-
"""Local control panel and review workstation for tts_batch.

Built on Starlette rather than FastAPI on purpose: starlette 1.6.0 is already
in this venv because the ElevenLabs MCP server depends on it, and installing
FastAPI would pull an older starlette in and break that server.

Binds to 127.0.0.1. The API key lives in this process's environment and the
audio is half a gigabyte on local disk -- none of this is meant to leave the
machine.
"""

import csv
import io
import json
import os
import sys

import anyio
import numpy as np
from starlette.applications import Starlette
from starlette.responses import (FileResponse, JSONResponse, PlainTextResponse,
                                 StreamingResponse)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_batch import parser as scriptparser          # noqa: E402
from tts_batch.parser import iter_clips               # noqa: E402
from tts_batch import retrim as retrimmod             # noqa: E402
from tts_batch.audio import analyze, load_wav         # noqa: E402
from tts_batch.cli import DEFAULTS                    # noqa: E402
from tts_batch.normalize import (PROTECTED_TOKENS, build_prompt,  # noqa: E402
                                 expand_numbers, fix_josa)
from tts_batch.preview import billed_chars            # noqa: E402
from tts_batch.preview import run as preview_run      # noqa: E402
from webapp.jobs import JobManager                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(ROOT, "output")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

jobs = JobManager()


# --- helpers ---------------------------------------------------------------

def _scripts():
    return sorted(f for f in os.listdir(ROOT)
                  if f.endswith(".txt") and not f.startswith("_"))


def _out_dir(script):
    return os.path.join(OUT_ROOT, scriptparser.voice_name(script))


def _script_path(script):
    """Resolve a script name safely: basename only, and it must exist in ROOT."""
    name = os.path.basename(script or "")
    if not name.endswith(".txt") or name not in _scripts():
        raise ValueError("unknown script: %r" % script)
    return os.path.join(ROOT, name)


def _safe_rel(rel):
    """Validate a clip path relative to wavs/, e.g. 0001.wav or 행복/0001.wav.

    One optional style folder, no separators beyond that, and nothing that can
    climb out of the directory.
    """
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel.endswith(".wav"):
        raise ValueError("not a wav: %r" % rel)
    parts = rel.split("/")
    if len(parts) > 2:
        raise ValueError("clip path is too deep: %r" % rel)
    for part in parts:
        if not part or part in (".", ".."):
            raise ValueError("bad clip path: %r" % rel)
        if os.path.basename(part) != part:
            raise ValueError("bad clip path: %r" % rel)
    return "/".join(parts)


def _client():
    from elevenlabs.client import ElevenLabs
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    return ElevenLabs(api_key=key)


def _resolve_voice(client, voice):
    res = client.voices.search(search=voice, page_size=100)
    for v in res.voices:
        if v.name == voice:
            return v.voice_id
    raise RuntimeError("no voice named %r in this account" % voice)


def _manifest(out_dir):
    """Latest take per clip."""
    path = os.path.join(out_dir, "manifest.jsonl")
    takes = {}
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    takes[r.get("path") or r["wav"]] = r
    return takes


async def _body(request):
    try:
        return await request.json()
    except Exception:                                   # noqa: BLE001
        return {}


# --- inventory -------------------------------------------------------------

async def index(request):
    return FileResponse(os.path.join(STATIC, "index.html"))


async def api_scripts(request):
    out = []
    for name in _scripts():
        path = os.path.join(ROOT, name)
        voice = scriptparser.voice_name(name)
        d = _out_dir(name)
        wav_dir = os.path.join(d, "wavs")
        try:
            lines, err = len(scriptparser.parse_file(path)), None
        except Exception as exc:                        # noqa: BLE001
            lines, err = 0, str(exc).splitlines()[0]
        done = len([f for f in os.listdir(wav_dir)
                    if f.endswith(".wav")]) if os.path.isdir(wav_dir) else 0
        out.append({"script": name, "voice": voice, "lines": lines,
                    "generated": done, "error": err,
                    "hasOutput": os.path.isdir(d)})
    return JSONResponse({"scripts": out, "defaults": DEFAULTS})


async def api_subscription(request):
    try:
        s = _client().user.subscription.get()
        return JSONResponse({
            "tier": s.tier, "used": s.character_count,
            "limit": s.character_limit,
            "remaining": s.character_limit - s.character_count})
    except Exception as exc:                            # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=502)


# --- text pipeline ---------------------------------------------------------

async def api_preview(request):
    body = await _body(request)
    script = _script_path(body.get("script"))
    out_dir = _out_dir(os.path.basename(script))
    r = preview_run(script, out_dir, DEFAULTS["drop_tags"])
    return JSONResponse({
        "voice": r["voice"], "entries": r["entries"], "changed": r["changed"],
        "billedChars": r["billed_chars"], "bodyChars": r["body_chars"],
        "emotions": dict(r["emotions"]),
        "wordRewrites": [{"from": k[0], "to": k[1], "count": v}
                         for k, v in r["word_pairs"].most_common()],
        "numericFixes": sum(r["num_pairs"].values()),
        "protected": sorted(PROTECTED_TOKENS),
    })


async def api_lines(request):
    """Paged raw -> spoken -> sent, for eyeballing the normalizer."""
    body = await _body(request)
    script = _script_path(body.get("script"))
    q = (body.get("q") or "").strip()
    only_changed = bool(body.get("changed"))
    offset = int(body.get("offset", 0))
    limit = min(int(body.get("limit", 100)), 500)

    rows = []
    for e in scriptparser.parse_file(script):
        norm = fix_josa(expand_numbers(e.text))
        if only_changed and norm == e.text:
            continue
        if q and q not in e.text and q not in norm and q not in e.wav:
            continue
        rows.append({"wav": e.wav, "emotion": e.emotion, "raw": e.text,
                     "say": norm,
                     "send": build_prompt(e.emotion, norm, DEFAULTS["drop_tags"]),
                     "changed": norm != e.text})
    return JSONResponse({"total": len(rows), "rows": rows[offset:offset + limit]})


async def api_protect(request):
    """Add a noun to PROTECTED_TOKENS so the particle pass stops rewriting it."""
    body = await _body(request)
    token = (body.get("token") or "").strip()
    if not token or len(token) > 12 or any(c.isspace() for c in token):
        return JSONResponse({"error": "invalid token"}, status_code=400)
    path = os.path.join(ROOT, "tts_batch", "normalize.py")
    src = io.open(path, encoding="utf-8").read()
    quoted = '"' + token + '"'
    if quoted in src:
        return JSONResponse({"ok": True, "already": True})
    anchor = "PROTECTED_TOKENS = frozenset(["
    if anchor not in src:
        return JSONResponse({"error": "could not locate PROTECTED_TOKENS"},
                            status_code=500)
    src = src.replace(anchor, anchor + "\n    " + quoted + ",", 1)
    io.open(path, "w", encoding="utf-8", newline="\n").write(src)
    return JSONResponse({"ok": True, "added": token, "restartRequired": True})


# --- clips -----------------------------------------------------------------

async def api_clips(request):
    body = await _body(request)
    script = os.path.basename(body.get("script") or "")
    out_dir = _out_dir(script)
    wav_dir = os.path.join(out_dir, "wavs")
    if not os.path.isdir(wav_dir):
        return JSONResponse({"total": 0, "rows": [], "flagCounts": {},
                             "restorable": 0})

    takes = _manifest(out_dir)
    flt = body.get("filter") or "all"
    q = (body.get("q") or "").strip()
    offset = int(body.get("offset", 0))
    limit = min(int(body.get("limit", 60)), 300)

    style_filter = body.get("style") or ""
    flag_counts = {}
    style_counts = {}
    restorable_total = 0
    rows = []
    for rel in iter_clips(wav_dir):
        r = takes.get(rel, {})
        style = r.get("style") or (rel.split("/")[0] if "/" in rel else "")
        flags = r.get("flags") or []
        for f in flags:
            flag_counts[f] = flag_counts.get(f, 0) + 1
        style_counts[style or "(없음)"] = style_counts.get(style or "(없음)", 0) + 1
        restorable = retrimmod.has_original(out_dir, rel)
        restorable_total += bool(restorable)
        if flt == "flagged" and not flags:
            continue
        if flt == "declicked" and not restorable:
            continue
        if style_filter and style != style_filter:
            continue
        if q and q not in rel and q not in (r.get("text") or ""):
            continue
        rows.append({"wav": rel, "name": os.path.basename(rel), "style": style,
                     "text": r.get("text", ""),
                     "emotion": r.get("emotion", ""),
                     "duration": r.get("duration"),
                     "leadSilence": r.get("lead_silence"),
                     "tailSilence": r.get("tail_silence"),
                     "seed": r.get("seed"), "flags": flags,
                     "restorable": restorable})
    return JSONResponse({"total": len(rows), "rows": rows[offset:offset + limit],
                         "flagCounts": flag_counts, "styleCounts": style_counts,
                         "restorable": restorable_total})


async def api_audio(request):
    script = os.path.basename(request.query_params.get("script") or "")
    rel = _safe_rel(request.query_params.get("wav"))
    which = request.query_params.get("which") or "current"
    sub = "originals" if which == "original" else "wavs"
    path = os.path.join(_out_dir(script), sub, rel.replace("/", os.sep))
    if not os.path.exists(path):
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(path, media_type="audio/wav")


async def api_waveform(request):
    """Downsampled peaks, plus the measurements the QC checks are based on."""
    script = os.path.basename(request.query_params.get("script") or "")
    rel = _safe_rel(request.query_params.get("wav"))
    which = request.query_params.get("which") or "current"
    sub = "originals" if which == "original" else "wavs"
    path = os.path.join(_out_dir(script), sub, rel.replace("/", os.sep))
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)

    samples, sr = load_wav(path)
    buckets = 600
    step = max(1, len(samples) // buckets)
    usable = (len(samples) // step) * step
    peaks = []
    if usable:
        peaks = (np.abs(samples[:usable].reshape(-1, step)).max(axis=1)
                 / 32768.0).round(4).tolist()
    q = analyze(samples, sr)
    return JSONResponse({"peaks": peaks, "duration": round(q["duration"], 3),
                         "leadSilence": round(q["lead_silence"], 3),
                         "tailSilence": round(q["tail_silence"], 3),
                         "flags": q["flags"]})


async def api_restore(request):
    body = await _body(request)
    script = os.path.basename(body.get("script") or "")
    wavs = body.get("wavs")
    names = set(_safe_rel(w) for w in wavs) if wavs else None
    done = retrimmod.restore(_out_dir(script), names)
    return JSONResponse({"restored": done})


# --- jobs ------------------------------------------------------------------

def _job_response(job):
    if job is None:
        return JSONResponse({"job": None})
    return JSONResponse({"job": job.snapshot()})


async def api_job(request):
    return _job_response(jobs.current)


async def api_job_stop(request):
    if jobs.current and jobs.current.state == "running":
        jobs.current.stop()
    return _job_response(jobs.current)


async def api_job_stream(request):
    """One SSE event per state change, so a late subscriber still catches up."""
    async def gen():
        seen = -1
        while True:
            if await request.is_disconnected():
                break
            job = jobs.current
            if job is None:
                yield "data: " + json.dumps({"job": None}) + "\n\n"
                await anyio.sleep(2.0)
                continue
            snap = job.snapshot()
            if snap["version"] != seen:
                seen = snap["version"]
                yield "data: " + json.dumps({"job": snap}) + "\n\n"
            if job.state != "running":
                await anyio.sleep(2.0)
            else:
                await anyio.to_thread.run_sync(job.wait, seen, 5.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _start(kind, label, fn):
    try:
        job = jobs.start(kind, label, fn)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return _job_response(job)


async def api_generate(request):
    body = await _body(request)
    script = _script_path(body.get("script"))
    limit = body.get("limit") or None
    trim = body.get("trim", DEFAULTS["trim_ms"])
    concurrency = int(body.get("concurrency") or DEFAULTS["concurrency"])
    force = bool(body.get("force"))
    voice = scriptparser.voice_name(script)
    out_dir = _out_dir(os.path.basename(script))

    def fn(job):
        from tts_batch.runner import run
        from tts_batch.synth import Synthesizer
        client = _client()
        job.say("resolving voice " + voice)
        voice_id = _resolve_voice(client, voice)
        job.say("voice " + voice_id)
        synth = Synthesizer(client, voice_id, voice,
                            model_id=DEFAULTS["model_id"],
                            output_format=DEFAULTS["output_format"],
                            normalization=DEFAULTS["normalization"],
                            max_retries=DEFAULTS["max_retries"])
        job.say("generating")
        return run(script, out_dir, synth,
                   sample_rate=DEFAULTS["sample_rate"],
                   drop_tags=DEFAULTS["drop_tags"],
                   concurrency=concurrency, force=force,
                   limit=int(limit) if limit else None,
                   trim_ms=int(trim) or None,
                   on_tick=job.tick, quiet=True,
                   should_stop=job.should_stop)

    return _start("generate", "generate " + voice, fn)


async def api_retry(request):
    body = await _body(request)
    script = _script_path(body.get("script"))
    include_flagged = bool(body.get("includeFlagged"))
    take = int(body.get("take") or 1)
    concurrency = int(body.get("concurrency") or DEFAULTS["concurrency"])
    voice = scriptparser.voice_name(script)
    out_dir = _out_dir(os.path.basename(script))

    def fn(job):
        from tts_batch.runner import run
        from tts_batch.synth import Synthesizer
        targets = set()
        for name in ("failures.csv", "qc_flags.csv"):
            if name == "qc_flags.csv" and not include_flagged:
                continue
            p = os.path.join(out_dir, name)
            if not os.path.exists(p):
                continue
            with io.open(p, encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    targets.add(row["wav"])
        if not targets:
            job.say("nothing to retry")
            return {"generated": 0, "skipped": 0, "failed": 0, "flagged": 0}
        job.say("retrying %d clip(s) at take %d" % (len(targets), take))
        client = _client()
        synth = Synthesizer(client, _resolve_voice(client, voice), voice,
                            model_id=DEFAULTS["model_id"],
                            output_format=DEFAULTS["output_format"],
                            normalization=DEFAULTS["normalization"],
                            seed_offset=take)
        return run(script, out_dir, synth,
                   sample_rate=DEFAULTS["sample_rate"],
                   drop_tags=DEFAULTS["drop_tags"],
                   concurrency=concurrency, force=True, only=targets,
                   trim_ms=DEFAULTS["trim_ms"] or None,
                   on_tick=job.tick, quiet=True,
                   should_stop=job.should_stop)

    return _start("retry", "retry " + voice, fn)


async def api_tool(request):
    """retrim / declick, in check or apply mode."""
    body = await _body(request)
    script = _script_path(body.get("script"))
    tool = body.get("tool")
    pad = int(body.get("pad") or DEFAULTS["trim_ms"])
    check = bool(body.get("check"))
    out_dir = _out_dir(os.path.basename(script))
    if tool not in ("retrim", "declick"):
        return JSONResponse({"error": "unknown tool"}, status_code=400)

    def fn(job):
        if tool == "retrim":
            if check:
                bad = retrimmod.check(out_dir, pad)
                job.say("%d clip(s) out of spec" % len(bad))
                return {"mode": "check", "items": [
                    {"wav": n, "lead": round(lead, 3), "tail": round(tail, 3)}
                    for n, lead, tail in bad]}
            fixed = retrimmod.repair(out_dir, pad)
            job.say("repaired %d clip(s)" % len(fixed))
            return {"mode": "apply", "items": [
                {"wav": n, "before": round(b, 2), "after": round(a, 2),
                 "tail": round(tail, 3)} for n, b, a, lead, tail in fixed]}
        changed = retrimmod.declick(out_dir, pad, dry_run=check)
        job.say(("would cut " if check else "cut ") + str(len(changed)) + " clip(s)")
        return {"mode": "check" if check else "apply", "items": [
            {"wav": n, "before": round(b, 2), "after": round(a, 2),
             "removedMs": round(r)} for n, b, a, r in changed]}

    return _start(tool, tool + " " + scriptparser.voice_name(script), fn)


async def api_estimate(request):
    body = await _body(request)
    script = _script_path(body.get("script"))
    limit = body.get("limit") or None
    need = billed_chars(script, DEFAULTS["drop_tags"],
                        int(limit) if limit else None)
    out = {"need": need}
    try:
        s = _client().user.subscription.get()
        remaining = s.character_limit - s.character_count
        out.update(tier=s.tier, remaining=remaining, after=remaining - need,
                   enough=need <= remaining)
    except Exception as exc:                            # noqa: BLE001
        out["error"] = str(exc)
    return JSONResponse(out)


routes = [
    Route("/", index),
    Route("/api/scripts", api_scripts),
    Route("/api/subscription", api_subscription),
    Route("/api/preview", api_preview, methods=["POST"]),
    Route("/api/lines", api_lines, methods=["POST"]),
    Route("/api/protect", api_protect, methods=["POST"]),
    Route("/api/estimate", api_estimate, methods=["POST"]),
    Route("/api/clips", api_clips, methods=["POST"]),
    Route("/api/audio", api_audio),
    Route("/api/waveform", api_waveform),
    Route("/api/restore", api_restore, methods=["POST"]),
    Route("/api/generate", api_generate, methods=["POST"]),
    Route("/api/retry", api_retry, methods=["POST"]),
    Route("/api/tool", api_tool, methods=["POST"]),
    Route("/api/job", api_job),
    Route("/api/job/stop", api_job_stop, methods=["POST"]),
    Route("/api/job/stream", api_job_stream),
    Mount("/static", app=StaticFiles(directory=STATIC), name="static"),
]

async def on_bad_input(request, exc):
    """A rejected script or wav name is the caller's mistake, not a crash."""
    return JSONResponse({"error": str(exc)}, status_code=400)


async def on_runtime_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=500)


app = Starlette(routes=routes, exception_handlers={
    ValueError: on_bad_input,
    RuntimeError: on_runtime_error,
})
