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
intervals. The bundled fixture proves the metric pipeline only and is not a
product benchmark. No competitive claim may use it.
