# Frozen Arabic-English benchmark harness

`score.ts` scores raw predictions only. Its v1 normalization performs NFC,
edge trimming, and whitespace collapse; it does not rewrite Arabic, punctuation,
case, entities, or technical terms. Keep cleanup output in a separate input.

```bash
bun benchmarks/score.ts benchmarks/fixtures/predictions.jsonl > result.json
```

Every publishable result must include a completed hardware manifest, immutable
corpus split identifiers, model and application hashes, raw prediction JSONL,
competitor version/settings/date, and speaker-disjoint bootstrap confidence
intervals. Scoring emits deterministic 1,000-resample 95% intervals for WER,
CER, and mixed error rate. The bundled fixture proves the metric pipeline only
and is not a product benchmark. No competitive claim may use it.

## Local native/Python comparison

`compare.ts` is a deliberately non-gating local runner. It requires an exact
600-row Perle JSONL split with unique IDs and runs the persistent native worker
and the archived `FasterWhisperProvider` over those same rows. Audio must
already satisfy each runtime's input contract. Nothing is uploaded or
published, and results never alter model readiness.

```bash
npm run benchmark:compare -- \
  --split /path/to/perle-600.jsonl \
  --corpus-revision <immutable-corpus-revision> \
  --hardware-manifest benchmarks/manifests/my-machine.json \
  --native-model ~/.local/share/openwhisper/v1/models/large-v3-turbo-q5_0.bin \
  --python .venv/bin/python \
  --python-model /path/to/pinned/faster-whisper-large-v3-turbo
```

Rows contain `id`, `audio_path`, `reference`, `subset`, optional `language`,
`technical_terms`, and `entities`. The runner records the corpus revision and
IDs, split/model/worker hashes, archived Python revision and fully resolved
settings, hardware manifest, per-item raw predictions/latency/RSS, runtime
summaries, scores, and confidence intervals under
`benchmarks/local-results/`. That directory is gitignored; publication is a
separate manual decision.
