# DatasetTTS

Bulk TTS dataset generation from voice scripts, via ElevenLabs.

One script file per voice: the filename is the ElevenLabs voice name
(`Closers_Android.txt` → the voice named `Closers_Android`).

## Script format

```
[emotion] 텍스트 | NNNN.wav
```

## Commands

Run everything through the venv that has the ElevenLabs SDK:

```
%USERPROFILE%\.claude\mcp-servers\elevenlabs\venv\Scripts\python.exe -m tts_batch <cmd>
```

| Command | API calls | What it does |
|---|---|---|
| `preview <script>` | none | Normalizes the whole script, writes review files |
| `estimate <script>` | 1 (free) | Compares the run's cost against remaining credit |
| `generate <script>` | one per line | Synthesizes; prompts before spending |
| `retry <script>` | one per clip | Regenerates failures, and flagged clips with `--include-flagged` |
| `retrim <script>` | none | Re-checks stored clips for the edge-silence invariant and repairs drift |
| `declick <script>` | none | Cuts the trailing tick off flagged clips instead of regenerating them |

Useful flags: `--limit N` (pilot run), `--trim MS` (edge silence, default 50),
`--force` (regenerate existing), `-y` (skip the cost prompt),
`retry --take N` (draw a different take of a clip that keeps coming back flawed).

## Web app

```
%USERPROFILE%\.claude\mcp-servers\elevenlabsenv\Scripts\python.exe -m webapp
```

Opens on http://127.0.0.1:8765 — loopback only. There is no authentication, the
process holds your API key, and it can spend credit, so do not bind it to a
public interface.

Five tabs, covering the same six operations as the CLI:

- **Scripts** — every `.txt` in the project, its voice, and how far generation got
- **Preview** — normalization summary, the word-rewrite audit with a one-click
  "add to `PROTECTED_TOKENS`" button, and a searchable per-line diff
- **Generate** — cost quoted before anything runs, live progress, stop button
- **Review** — play any clip, draw its waveform, filter to flagged or declicked
  ones, compare a declicked clip against its original side by side, and revert
- **Tools** — `retrim` and `declick`, each with a check-only mode first

The server is a thin wrapper: it calls the same `tts_batch` functions the CLI
does rather than reimplementing anything, so both paths always agree and the
existing tests cover both. One job runs at a time; a second request gets a 409.

It is built on Starlette, not FastAPI, because starlette 1.6.0 is already in
this venv for the ElevenLabs MCP server and installing FastAPI would downgrade
it and break that server.

## Text pipeline

The engine reads bare digits one at a time — `43636` comes out "사삼육삼육" — so
every number is expanded to hangul before it is sent, and the expanded text is
what lands in `metadata.csv`. That keeps transcript and audio identical, which
a training set requires.

Two passes, in this order:

1. **Digits → hangul.** The counter picks the numeral system: `8개` → "여덟 개"
   (native), `95퍼센트` → "구십오 퍼센트" (Sino). Native numerals stop at 99, so
   `295발` → "이백구십오 발". `일` is context-sensitive: `7월 10일` → "칠월 십일"
   (date, attached) but `5일 남았습니다` → "오 일 남았습니다" (span, separated —
   run together it becomes 오일, the word for oil).
2. **Particle agreement.** Fixes both the errors already in the source
   (`정찰병가` → `정찰병이`) and the ones pass 1 creates: `3928를` becomes
   "삼천구백이십팔**을**", because the correct particle depends on how the number
   is spoken.

`PROTECTED_TOKENS` in `normalize.py` guards nouns whose last syllable looks like
a particle — without it, `가을 수확제` becomes `가를 수확제`.

**Before generating any new script, run `preview` and read the WORD REWRITES
section.** Pass 2 rewrites words, and a real noun mis-parsed as noun+particle is
silently corrupted. Anything in that list that is not a genuine grammatical fix
belongs in `PROTECTED_TOKENS`.

## Output

```
output/<Voice>/
  wavs/NNNN.wav     24 kHz mono 16-bit PCM
  metadata.csv      id|transcript|emotion  (LJSpeech-style)
  manifest.jsonl    full request parameters and QC measurements per clip
  qc_flags.csv      clips needing a listen
  failures.csv      clips that never succeeded
  preview.txt       raw → spoken → sent, for every line
  changes.txt       only the lines the pipeline altered
  josa_changes.txt  every distinct particle correction
```

## Reliability

Runs are **resumable** — an existing valid WAV is skipped, so an interrupted run
costs nothing to continue. Each clip gets a **stable seed** derived from voice
and filename, so a rerun reproduces the same audio. `retry` bumps a seed offset
so a flawed clip comes back genuinely different rather than byte-identical.
Transient failures (429, 5xx, network) retry with exponential backoff and
jitter; a clip that never succeeds is logged and the run continues.

A clip flagged `trailing_artifact` can be fixed two ways: `retry --include-flagged`
regenerates it at a new seed and costs credits, or `declick` cuts the tick off
and costs nothing. `declick` cuts each clip at its own detected utterance end,
never a fixed length: across the 56 affected clips the tick itself held steady
near 20ms while the silence before it ranged from 100ms to 420ms, so trimming
everything by the largest total (610ms) would have eaten real speech from 55 of
the 56 -- up to 490ms in the worst case.

`ARTIFACT_MAX_MS` is what separates a tick from a word, and it is the setting
most likely to need retuning on a new voice. At 500ms it cut the final word out
of one clip whose last run was 480ms of real speech. The lengths turned out to
be sharply bimodal -- 55 clips at 20ms, one at 480ms, nothing between -- so it
now sits at 200ms. **Listen to what `declick` changed before trusting it on a
new voice**; a clip that ends on a short, clipped final syllable is exactly what
this check can get wrong.

Trimming is verified after the fact, not trusted: on the 4,211-clip run, 39
clips (0.9%) kept their untrimmed tails even though a fresh call to
`trim_silence` on the same text trimmed correctly, and the cause did not
reproduce. `retrim` re-checks every stored clip and repairs the ones out of
spec. It is idempotent and costs nothing, so run it after every batch.

`failures.csv` and `qc_flags.csv` are rewritten, not appended: a clip that
comes back clean drops out of the report, and the file is deleted once it is
empty, so `retry` never re-does work that already succeeded.

QC flags each clip for `trailing_artifact`, `clipped`, `silent`, `too_short`,
and `duration_mismatch`. The artifact detector was tuned against two clips
confirmed by ear: the utterance decays to silence, a gap of 120–140ms passes,
then energy climbs again — to the clip's peak in one case — and the file ends
while still loud. A clip that finished properly always decays into trailing
silence, so "still sounding at the last sample" is the discriminator. On the 32
sample clips generated during setup it flags exactly those two and nothing else.

## Configuration

Defaults live in `DEFAULTS` in `cli.py`: model `eleven_v3`, `pcm_24000`,
`apply_text_normalization="off"` (we normalize ourselves), concurrency 5
(Creator tier limit), `[neutral]` tags dropped.

Dropping `[neutral]` was measured, not assumed: tagged and untagged pairs
differed by ±0.08s with no audible difference, and the tag would have cost
35,150 billed characters across this script.

## Tests

```
python -m pytest tests/ -q
```
