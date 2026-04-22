# Crowe Logic Foundry: Demo Tour

Step-by-step sequence for recording a full-feature demo of the Foundry
CLI with [asciinema](https://asciinema.org). Follow the steps in
order. The whole recording runs 3 to 5 minutes.

## Prereqs

```bash
brew install asciinema
```

Optionally install `agg` to convert the cast to GIF later:

```bash
brew install agg
```

Make sure `crowe-logic` runs cleanly and that your `.env` has at
least one working provider. Anthropic (for Supreme) and Ollama Pro
(for Eclipse/Crescent) are the minimum for the dual-mode scenes.

## Start the recording

```bash
cd ~/Projects/crowe-logic-foundry
asciinema rec demo/foundry-tour.cast \
  --title "Crowe Logic Foundry: Multi-Model CLI" \
  --idle-time-limit 2
```

Then run the commands below. The `--idle-time-limit 2` compresses
long pauses while you read model output, so the final recording
stays under 5 minutes even if the models stream slowly.

Stop recording with `Ctrl-D` or `exit`.

## Sequence

### 1. Launch and overview (20 seconds)

```bash
crowe-logic
```

Let the welcome banner render, then type:

```
/help
```

This shows every slash command including the new `/dual synth ...`
and `/replay` / `/fork` rows. Narration beat: "Thirty commands,
seven model tiers, full agent framework."

### 2. Single-model Supreme turn (40 seconds)

```
/model supreme
```

Then:

```
Explain RICO claims in two paragraphs.
```

Watch the HUD populate with `COST`, `CREDITS`, `TURNS` rows as
Supreme streams. Let it finish. Narration: "Real-time cost and
credits. Every turn you know exactly what you're burning."

### 3. Second turn to demonstrate cache hit (25 seconds)

```
Now summarize that in one sentence.
```

Watch for `cached` flag in the HUD on the second turn. Anthropic
prompt caching kicks in. Narration: "Second turn hits the cache,
ten percent of the original input cost."

### 4. Alias diagnostic (15 seconds)

```
/model resolve eclipse
```

Shows label, provider, backend, chain index, all aliases.
Narration: "Every alias transparent. No hidden config layering."

### 5. Dual mode (60 seconds)

```
/dual on
```

Watch the confirmation (Supreme + Eclipse pairing). Then:

```
What's the best strategy for a contested landlord dispute with RICO elements?
```

Both panes stream concurrently. Narration: "Two flagship models,
same prompt, watch them diverge." Let both finish. Point at the
two different reasoning paths.

### 6. Dual mode plus synthesis (90 seconds)

```
/dual synth on
```

Then:

```
/dual synth mode merge
```

Then a follow-up prompt that benefits from synthesis:

```
How do I structure a demand letter for this?
```

Both panes stream. When both finish, the synthesis panel appears
below, streams its own output. Narration: "Third model reads both
responses and produces the merged final answer. Merge mode, judge
mode, or diff mode."

### 7. Replay on a different model (35 seconds)

```
/dual off
```

```
/replay 2 crescent
```

Replays the "summarize in one sentence" turn on Crescent instead
of Supreme. Shows how fast A/B model comparison is. Narration:
"Any past turn, any other model, one command."

### 8. Fork to try a different approach (25 seconds)

```
/fork 3
```

Shows the fork notice (X subsequent turns truncated). The prompt
re-runs. Narration: "Fork discards later turns so the replay
becomes the new tail. Try again, differently, without the stale
branch."

### 9. Final HUD check (10 seconds)

Let the HUD render its running totals one last time. Point at the
session total cost and credit count. Narration: "Full visibility
from the first turn to the last."

### 10. Exit (5 seconds)

```
/exit
```

Stop the recording with `Ctrl-D`.

## After recording

Upload to asciinema.org for a shareable URL:

```bash
asciinema upload demo/foundry-tour.cast
```

Or convert to GIF for embedding in the landing page:

```bash
agg demo/foundry-tour.cast demo/foundry-tour.gif --theme monokai
```

## Landing page embed

Once uploaded, replace the static terminal demo in
`landing/index.html` (hero section) with an asciinema embed:

```html
<script async id="asciicast-XXXXXX"
        src="https://asciinema.org/a/XXXXXX.js"
        data-speed="1.5"
        data-theme="monokai"></script>
```

Swap `XXXXXX` for the asciinema cast id the upload command prints.

## Tips

- **Pause before each command** for half a second. It reads much
  cleaner on playback than rapid-fire typing.
- **Don't narrate while the model streams.** Wait until it finishes,
  point at the output, then move on. Asciinema doesn't record audio;
  voiceover goes on top in post if you want one.
- **If a model fails mid-record**, just keep going. The fallback
  chain is itself a feature; showing it handle a real 503 from
  Eclipse and drop to Crescent is a bonus. If you need a clean
  re-record, stop with `Ctrl-D` and start over.
- **The full demo touches Anthropic, Ollama Cloud, and synthesis**.
  Total cost per recording: roughly 15 cents on Supreme plus the
  fixed Ollama Pro subscription.
