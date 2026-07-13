# Haiku Archive — Design

**Goal:** Keep a local, append-only record of every haiku the bridge composes, and surface
the trends in it — so a stale, repetitive, or trope-violating prompt becomes *visible*
instead of being read one line at a time on a 64×32 display and forgotten.

## Motivation

Two motivations were raised: the archive might be interesting in itself, and it might help
dial in the haiku prompt. They are not two features — they are one log with two views, and
the log is the prerequisite for either.

**Deliberately NOT stored: the digest.** Full prompt tuning would want the model's *input*
(a dull haiku has two possible causes — a weak system prompt or a thin digest — and the
output alone can't distinguish them). But the digest (`state.py:129-138`) contains
`asked: "<the user's prompt>"`, the tool activity, and `reply: "<Claude's reply>"` — a
running record of real work. That is not something to accumulate on disk for a nice-to-have.

The archive therefore stores **outputs only**. This is a conscious trade: it cannot tell you
*why* a haiku was bad, but it reliably tells you *that* the haikus are drifting — repeated
phrasing, recycled imagery, banned tropes creeping back — which is the signal worth acting
on. Diagnosis stays a manual investigation.

## Record format

One JSON object per line, appended to `~/.local/share/familiar/haikus.jsonl`
(`$XDG_DATA_HOME/familiar/haikus.jsonl` when set):

```json
{"ts":"2026-07-13T11:42:03-05:00","lines":["...","...","..."],"model":"claude-haiku-4-5-20251001","prompt":"a3f1c802"}
```

| Field | Meaning |
| --- | --- |
| `ts` | ISO-8601 local time with UTC offset |
| `lines` | the haiku, 1–3 strings, exactly as `to_haiku()` parsed them |
| `model` | the model id that produced it |
| `prompt` | `sha256(_SYSTEM).hexdigest()[:8]` — the system-prompt version |

**Why the prompt hash matters:** without it, rewording the system prompt silently mixes two
eras into one trend line and you compare across a regime change without knowing. With it,
every statistic can be grouped by prompt version, so "did my edit help?" is answerable.

**Not stored, and not by accident:** the digest, the user's prompt, Claude's reply, the
project name, file paths, token counts. Anything that would turn the file into a work log.

**Size:** ~150 bytes per record; a haiku is composed at most every 90s (`haiku_periodic`).
That is a few megabytes a year. **No rotation, no size cap** — the code isn't worth it.

## Module: `linux-bridge/src/familiar/archive.py`

New, self-contained, no new dependencies.

```python
DEFAULT_PATH: Path                      # $XDG_DATA_HOME/familiar/haikus.jsonl
def prompt_id(system: str) -> str       # sha256(system)[:8]
def append(lines, *, model, prompt, path=DEFAULT_PATH, now=None) -> bool
def load(path=DEFAULT_PATH, limit=None) -> list[dict]
def stats(records) -> dict
def main(argv) -> int                   # the `familiar haikus` subcommand
```

### `append` — best-effort, never raises

**Wired in `_make_compose` (`daemon.py:336-343`), not in `Bridge`.** `_make_compose` already
closes over `cfg` — which is where the model id and the `haiku_archive` flag live — and every
successful compose passes through it exactly once. Archiving there makes the write a
decorator over `compose`:

```python
def _make_compose(cfg, append=None):
    if not cfg.api_key:
        return None
    write = append or archive.append

    async def compose(digest):
        lines = await haiku.compose(digest, api_key=cfg.api_key, model=cfg.model)
        if lines and cfg.haiku_archive:
            write(lines, model=cfg.model, prompt=archive.prompt_id(haiku.SYSTEM))
        return lines

    return compose
```

`Bridge` therefore gains **no new parameter, no new state, and no knowledge that an archive
exists** — which also means every existing `Bridge` test (which injects a fake `compose`)
keeps passing untouched.

`append` creates the parent directory if needed, opens in append mode, writes one line,
closes. No file handle is held between writes — haikus are rare, and a long-lived handle is a
liability in a daemon that must survive anything.

`haiku.py`'s `_SYSTEM` is renamed to **`SYSTEM`** (a public module constant) so `prompt_id`
can hash it without reaching into a private.

**Every failure is swallowed and returns `False`** (unwritable path, full disk, permission
error, a `ts` that won't serialise). This matches the existing contract in `haiku.py`
("Best-effort and never raises"): **the buddy must never break because a log file failed.**
An archive is a nicety; the pet is the product.

### `load` — tolerant of a corrupt line

A truncated final line (power loss mid-write) or any unparseable line is **skipped, not
fatal**. An append-only log that a crash can render permanently unreadable is a bad log.

## Statistics — what "notice trends" actually means

The obvious implementation is a word-frequency count. That is the wrong metric, and worth
being explicit about why.

### 1. Document frequency, not raw count

A word used twice inside one haiku is just a haiku. A word appearing in **40% of all
haikus** is a rut. So the metric is *the share of haikus containing a word at least once*:

```
freq(w) = |{h : w ∈ h}| / |H|
```

Reported as the top N words by document frequency, after removing a small built-in stopword
list (`the, a, an, of, in, on, and, to, is, it, its, with, at, by, for, from, as, into`).
This is what surfaces the model leaning on a crutch.

### 2. Repeated lines

An identical line (case- and whitespace-normalised) emitted verbatim across two or more
*different* haikus is the strongest staleness signal available, and costs one `Counter`.
Report each repeated line with its count.

### 3. Trope compliance — the highest-value metric

The system prompt (`haiku.py:17-18`) explicitly bans specific imagery:

> "Avoid the tired tropes: no keyboards, keys, clicking, typing fingers, glowing screens, or
> blinking cursors."

So we can measure **whether the model is obeying its own instructions**. The banned-word list
is derived from that sentence: `keyboard, keys, key, click, clicking, typing, type, finger,
fingers, screen, screens, glowing, glow, cursor, cursors, blinking, blink`.

If `cursor` appears in 15% of haikus, the prompt is not working — and you would *never*
notice that reading them one at a time on a tiny display. This is the metric most likely to
change what you do.

### Grouping

All three statistics are computed **per prompt version** as well as overall, so a prompt edit
shows up as a boundary rather than as noise.

## CLI: `familiar haikus`

`cli.py` uses a hand-rolled dispatch (`cli.py:20-28`); add one branch and one `_HELP` line.

```
familiar haikus [--limit N]        print the most recent haikus (default 20)
familiar haikus --stats [--limit N]  print the trend report
```

`--stats` output (illustrative shape, not fixed text):

```
142 haikus, 2026-06-30 .. 2026-07-13, 2 prompt versions

RECURRING IMAGERY (share of haikus containing the word)
  silence   18%   (26)
  quiet     14%   (20)
  ...

REPEATED LINES
  3x  "the silent cursor waits"
  2x  "a function blooms"

TROPE VIOLATIONS (banned by the system prompt)
  cursor    9%   (13)
  screen    4%   (6)
  -- none for: keyboard, keys, click, typing, finger, glow, blink

BY PROMPT VERSION
  a3f1c802  98 haikus   top: silence 21%   tropes: 11%
  7bd94e10  44 haikus   top: hush 9%       tropes: 2%
```

An empty or missing archive prints a one-line "no haikus archived yet" and exits 0 — not an
error.

## Configuration

`Config` (`config.py:13`) gains `haiku_archive: bool = True`. The archive runs whenever haiku
mode is on; setting `haiku_archive = false` in `config.toml` disables the writer entirely.
It writes to the user's disk, so the user gets a switch.

## Error handling

- Archive write fails → swallowed, returns `False`, haiku still displayed. Never propagates.
- Archive dir can't be created → same.
- Corrupt/truncated line on read → skipped.
- No archive file → `load()` returns `[]`; the CLI prints a friendly note.
- `stats([])` returns a well-formed empty report, not a `ZeroDivisionError`.

## Testing

All pure and fast; no network, no device, no filesystem beyond `tmp_path`.

1. `prompt_id` is stable and changes when the system prompt changes.
2. `append` → `load` roundtrip preserves lines, model, prompt, ts.
3. `append` to an unwritable path returns `False` and **does not raise**.
4. `load` skips a corrupt/truncated line and still returns the good records.
5. Document frequency: a word appearing 3× in ONE haiku out of 10 scores 10%, not 30%.
   (This is the metric's whole point — a test that doesn't distinguish these is worthless.)
6. Repeated lines are detected across haikus and ignored within one.
7. Trope detection flags a banned word and is case-insensitive.
8. `stats([])` returns an empty report without raising.
9. Grouping by prompt version splits records correctly.
10. The `compose` returned by `_make_compose` appends exactly one record on a successful
    compose, and **none** when the underlying `haiku.compose` returns `None`.
11. With `haiku_archive = false`, `_make_compose`'s compose writes nothing.
12. The existing `Bridge` tests still pass untouched — proof that the archive is genuinely
    decoupled from the daemon's hot path.

## Out of scope

- **Storing the digest / any tuning data.** Explicitly rejected above.
- A rendered HTML gallery. Defer until the archive proves interesting; the JSONL is the
  substrate, and a gallery is a view that can be added later without re-collecting anything.
- Any upload, sync, or sharing. The file is local, and nothing in this feature moves it.
- Deduplication of consecutive identical haikus — repetition is *the signal we are trying to
  measure*, so suppressing it at write time would defeat the feature.
- Rotation / size caps.
