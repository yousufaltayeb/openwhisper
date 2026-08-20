"""Local-only archived FasterWhisperProvider benchmark process."""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

from openwhisper.providers.contracts import TranscriptionRequest
from openwhisper.providers.local import FasterWhisperProvider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--compute-type", required=True)
    args = parser.parse_args()
    items = [json.loads(line) for line in args.split.read_text(encoding="utf-8").splitlines() if line]
    if len(items) != 600:
        raise ValueError(f"Perle split must contain exactly 600 items, got {len(items)}")
    provider = FasterWhisperProvider(model=args.model, device=args.device, compute_type=args.compute_type)
    predictions: list[dict[str, object]] = []
    peak_rss_kb = 0
    for item in items:
        audio = (args.split.parent / item["audio_path"]).resolve()
        started = time.perf_counter()
        result = provider.transcribe(TranscriptionRequest(audio, language=item.get("language")))
        latency_ms = (time.perf_counter() - started) * 1000
        peak_rss_kb = max(peak_rss_kb, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        predictions.append(
            {
                "id": item["id"],
                "subset": item["subset"],
                "reference": item["reference"],
                "prediction": result.text,
                "technical_terms": item.get("technical_terms", []),
                "entities": item.get("entities", []),
                "detected_language": result.language,
                "latency_ms": latency_ms,
                "rss_kb": peak_rss_kb,
            }
        )
    args.output.write_text("".join(f"{json.dumps(item, ensure_ascii=False)}\n" for item in predictions), encoding="utf-8")
    args.output.chmod(0o600)
    print(json.dumps({"samples": len(items), "peak_rss_kb": peak_rss_kb, "settings": {"model": args.model, "device": args.device, "compute_type": args.compute_type}}, indent=2))


if __name__ == "__main__":
    main()
