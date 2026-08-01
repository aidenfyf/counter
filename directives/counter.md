# Directive: Claude Code counter card

A glanceable card showing the last 30 days of Claude Code usage, rendered to the
Mac desktop (Übersicht) and the iPhone home screen (Scriptable). No server, no
hosting, no account.

## Goal

One PNG, regenerated hourly, that answers "what did the last 30 days actually
look like" without opening anything.

## Layers

| Layer | File | Job |
|---|---|---|
| 1 directive | this file | what the tiles mean, how to change one |
| 3 execution | `execution/count.py` | scan transcripts -> `out/stats.json` |
| 3 execution | `execution/archive.py` | freeze completed months -> `archive/YYYY-MM.json` |
| 3 execution | `execution/render.py` | both cards + stats -> `out/card*.png` |
| 3 execution | `execution/refresh.sh` | all three, in order; what launchd calls |
| config | `config/patterns.yml` | every phrase counter |
| config | `config/rates.yml` | model pricing + subscription price map |

## Inputs

- `~/.claude/projects/**/*.jsonl` - session transcripts
- `~/.claude.json` - `oauthAccount.organizationRateLimitTier` gives the live plan

## Changing a tile

Phrase counters are data, not code. Add a row to `config/patterns.yml`:

```yaml
  - key: my_counter
    who: me            # me = my typed turns; claude = assistant output
    mode: turns        # turns = messages that matched; hits = total occurrences
    label: "what it says under the number"
    regex: '\b(pattern)\b'
```

Then reference `S.phrases.my_counter.count` (or `.one_in`) in `card/card.html`.
Run `./execution/refresh.sh` and look at the PNG.

## Rules learned the hard way

1. **A human turn is `origin.kind == "human"`.** Everything else that looks like
   a user message is machine noise: tool results, `[Image: ...]` stubs,
   `<command-name>` slash expansions, `<bash-stdout>`, context-continuation
   summaries, hook feedback. Verified: of 341 plain-string user rows without an
   `origin` field, zero were human writing.

2. **Cost and tool counts include subagent sidechains.** Agents spend real tokens
   on your behalf. Filtering `isSidechain` under-reports by roughly a third.

3. **Sum tokens across the window, then apply rates once.** Costing per message
   and adding up truncates tens of thousands of times and drifts ~2%.

4. **The window is rolling 30 days, not a calendar month.** Transcripts are pruned
   on a rolling basis, so a rolling window can never claim data the pruner already
   took, and the card is never near-empty on the 1st.

5. **`one_in` only applies to my own turns.** Claude emits tens of thousands of
   assistant messages per window, so "1 in 630" there is noise, not a stat.

6. **The comparison delta stays hidden until both windows are provably complete.**
   Against a pruned prior window it reads +2000%, which is the pruner, not growth.
   `Window.complete()` gates it; do not remove that gate to make the line appear.

7. **`render.py` must replace `/*__STATS__*/ null`, including the `null`.**
   Replacing only the comment yields `const S = {...} null;`, a syntax error, and
   a syntax error renders a silently empty card that still screenshots fine.
   `check_dom()` exists because that shipped once.

8. **Every flex child needs `flex-shrink: 0`.** Without it, content that overruns
   the page silently squeezes the cost tile and clips the "saved ..." line rather
   than overflowing where you would see it.

9. **`check_render()` verifies the chamfer, not the blur.** It asks the live page
   where its tiles are, then measures each one's top edge against the pixels just
   inside it and asserts the rim is there. Two earlier versions were worse: the
   first only printed image dimensions while claiming to check the glass; the
   second compared regional mean warmth, which is invariant to blur by
   construction (blur is a mean-preserving convolution) and passed on a
   filter-free render. Do not "fix" it by tuning a warmth threshold - no threshold
   on a regional mean can separate blurred from unblurred. `backdrop-filter` is
   close to a visual no-op in this design anyway (the backdrop is already
   blur(90px)+, so re-blurring it changes the render by ~1/255 RMS); the glass
   read is carried by the translucent fill, the chamfer, and the drop shadow.

## Two cards, not one scaled

`card/card.html` is the desktop composition (1200x718 @2x). `card/card-phone.html`
is the iPhone large-widget composition (1014x1062 @1x, i.e. 3x nominal, so 1 CSS
px = 1 device px on a 3x phone).

They are separate files on purpose. Shrinking the desktop card to widget size puts
its labels at ~3.6pt against a ~9pt legibility floor, and its 1px chamfer at a
third of a point, where it disappears. The phone card carries four stats instead
of seven, labels at 30px (10pt), and a 2px chamfer.

## Widget-extension gotchas (phone)

These cost a full evening each and none of them fail loudly.

- `stack.backgroundImage` does **not** render in a widget extension - it paints a
  blank white block over its own text. It works in the in-app preview, so it looks
  correct until it ships. `ListWidget.backgroundImage` works; use that.
- SwiftUI gradients do not dither, so any ramp subtle enough to read as glass
  bands into stripes. Bake it instead.
- Random-noise dithering is incompressible; ordered (Bayer) dithering compresses
  ~19x better, which is what makes a native-resolution background small enough to
  embed. Embedding matters: it removes any dependency on iCloud having synced.
- Scriptable prepends its own metadata header on import. If the file already
  starts with a `//` comment it eats that line's slashes, orphaning the text as
  bare code - a syntax error on line 1. Keep its header first.
- There is no baseline alignment. `bottomAlignContent()` aligns box bottoms, so
  mixed font sizes on one line drift apart as the size gap grows.

## Retention

Set `cleanupPeriodDays` to **90** in `~/.claude/settings.json` (the default is 30). 90 covers the rolling window, a prior-30
comparison, and calendar month-over-month, each with buffer. Costs ~7 GB at
steady state and plateaus - it does not grow without bound.

Raise it before you start, not after: whatever the pruner has already taken is
gone, and a month that was partially pruned gets archived with
`complete: false` so the card never compares against it.

## The iCloud mirror lives in the widget, not in launchd

A LaunchAgent can create new files in iCloud Drive but cannot overwrite one iCloud
has marked `UF_TRACKED`, which it does to everything it syncs. Measured with
launchd probes: copy-over, truncate, rename-over and unlink-then-write all fail.
So the first run into a fresh folder succeeds and every run after it fails, which
makes it look intermittent.

An Ubersicht child process is not subject to that guard - verified with a probe
widget that copied the tracked file successfully. So `claude-counter.jsx`'s
`command` owns the mirror and runs it every 2 minutes, and **no Full Disk Access
grant is required anywhere.** Do not reintroduce the mirror into `refresh.sh`.

## Known blind spot

Only local Claude Code sessions are counted, because they are the only ones that
write to `~/.claude/projects`. Anything calling the Anthropic API from elsewhere
(a server, a cron job, a hosted agent) is invisible here, which is why the card
says "local terminal only" on its face. Including those would mean having each
one report its own `usage` object somewhere this can read.

The Admin API is not a shortcut for that: its usage and Claude Code analytics
endpoints require an organization, and are unavailable on an individual account.


## Operating

```sh
./execution/refresh.sh          # regenerate everything
python3 execution/archive.py 2026-07 --force   # re-freeze one month
COUNTER_WINDOW_DAYS=7 python3 execution/count.py   # try a different window
```

launchd: `com.claude-counter`, every 10 minutes, `RunAtLoad` so a completed month
is still archived if the Mac was off at the turn of the month. `./install.sh`
writes and loads the agent for you.

## QA

The card is a rendered static graphic. Both the desktop and phone compositions
were reviewed by an art-direction pass and a real-GPU browser pass; the findings
that mattered are baked into the rules above.
