import { mkdir, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";

interface SplitItem {
  id: string;
  audio_path: string;
  reference: string;
  subset: "arabic" | "mixed" | "english";
  language?: "auto" | "ar" | "en";
  technical_terms?: string[];
  entities?: string[];
}

interface WorkerResponse {
  type: string;
  text?: string;
  language?: string;
  message?: string;
}

const args = parseArgs(process.argv.slice(2));
const splitPath = resolve(required(args, "split"));
const hardwarePath = resolve(required(args, "hardware-manifest"));
const workerPath = resolve(args.worker ?? "target/debug/openwhisper-worker-native");
const modelPath = resolve(args["native-model"] ?? join(process.env.HOME ?? "", ".local/share/openwhisper/v1/models/large-v3-turbo-q5_0.bin"));
const python = resolve(args.python ?? ".venv/bin/python");
const pythonModel = required(args, "python-model");
const corpusRevision = required(args, "corpus-revision");
const outputRoot = resolve(args.output ?? "benchmarks/local-results");
const splitText = await Bun.file(splitPath).text();
const split = splitText.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line) as SplitItem);
validateSplit(split, splitPath);
if (!(await Bun.file(hardwarePath).exists())) throw new Error(`hardware manifest does not exist: ${hardwarePath}`);
for (const path of [workerPath, modelPath, python]) if (!(await Bun.file(path).exists())) throw new Error(`runtime input does not exist: ${path}`);

const runId = `${new Date().toISOString().replaceAll(":", "-").replace(".", "-")}-${crypto.randomUUID().slice(0, 8)}`;
const output = join(outputRoot, runId);
await mkdir(join(output, "native"), { recursive: true, mode: 0o700 });
await mkdir(join(output, "python"), { recursive: true, mode: 0o700 });
await writeFile(join(output, "split.jsonl"), splitText, { mode: 0o600 });
await writeFile(join(output, "hardware.json"), await Bun.file(hardwarePath).text(), { mode: 0o600 });

const workerHash = await sha256File(workerPath);
const modelHash = await sha256File(modelPath);
const splitHash = new Bun.CryptoHasher("sha256").update(splitText).digest("hex");
const archiveRevision = await commandText(["git", "rev-parse", "archive/pre-cli-rewrite-2026-08-19"]);
const pythonSettings = {
  model: pythonModel,
  device: args["python-device"] ?? "cpu",
  compute_type: args["python-compute-type"] ?? "int8",
  implementation: "src.openwhisper.providers.local.FasterWhisperProvider",
};
const manifest = {
  schema: 1,
  status: "local_non_gating",
  created_at: new Date().toISOString(),
  corpus: "Perle",
  corpus_revision: corpusRevision,
  split_sha256: splitHash,
  sample_count: split.length,
  sample_ids: split.map((item) => item.id),
  native: { worker_path: workerPath, worker_sha256: workerHash, model_path: modelPath, model_sha256: modelHash, worker_abi: "openwhisper-worker-1" },
  python: { executable: python, archive_revision: archiveRevision.trim(), settings: pythonSettings },
  hardware_manifest: "hardware.json",
  readiness_effect: "none",
  publication: "manual_only",
};
await writeJson(join(output, "run-manifest.json"), manifest);

const nativePredictions = await runNative(split, workerPath, modelPath);
await writeJsonl(join(output, "native/predictions.jsonl"), nativePredictions);

const pythonResult = Bun.spawn([
  python,
  resolve("benchmarks/faster_whisper_runner.py"),
  "--split", splitPath,
  "--output", join(output, "python/predictions.jsonl"),
  "--model", pythonSettings.model,
  "--device", pythonSettings.device,
  "--compute-type", pythonSettings.compute_type,
], { stdout: "pipe", stderr: "inherit", env: { ...process.env, PYTHONPATH: resolve("src") } });
const pythonSummary = await new Response(pythonResult.stdout).text();
if (await pythonResult.exited !== 0) throw new Error("archived faster-whisper comparison failed");
await writeFile(join(output, "python/runtime-summary.json"), pythonSummary, { mode: 0o600 });

for (const runtime of ["native", "python"] as const) {
  const score = Bun.spawn(["bun", resolve("benchmarks/score.ts"), join(output, runtime, "predictions.jsonl")], { stdout: "pipe", stderr: "inherit" });
  const scored = await new Response(score.stdout).text();
  if (await score.exited !== 0) throw new Error(`${runtime} scoring failed`);
  await writeFile(join(output, runtime, "score.json"), scored, { mode: 0o600 });
}

process.stdout.write(`${output}\n`);

async function runNative(items: SplitItem[], worker: string, model: string) {
  const process = Bun.spawn([worker], { stdin: "pipe", stdout: "pipe", stderr: "inherit" });
  const lines = lineReader(process.stdout);
  const ready = JSON.parse(await lines.next()) as WorkerResponse;
  if (ready.type !== "ready") throw new Error("native worker did not return its ready frame");
  const predictions = [];
  let peakRssKb = 0;
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index]!;
    const audioPath = resolve(dirname(splitPath), item.audio_path);
    const started = performance.now();
    process.stdin.write(`${JSON.stringify({
      id: crypto.randomUUID(), generation: index + 1, command: "transcribe",
      model_path: model, audio_path: audioPath, language: item.language ?? "auto",
    })}\n`);
    await process.stdin.flush();
    const response = JSON.parse(await lines.next()) as WorkerResponse;
    const latencyMs = performance.now() - started;
    peakRssKb = Math.max(peakRssKb, await rssKb(process.pid));
    if (response.type !== "transcript" || typeof response.text !== "string") {
      throw new Error(`native worker failed sample ${item.id}: ${response.message ?? response.type}`);
    }
    predictions.push(prediction(item, response.text, response.language, latencyMs, peakRssKb));
  }
  process.stdin.write(`${JSON.stringify({ id: crypto.randomUUID(), generation: items.length + 1, command: "shutdown" })}\n`);
  await process.stdin.flush();
  await process.exited;
  await writeJson(join(output, "native/runtime-summary.json"), { peak_rss_kb: peakRssKb, samples: items.length });
  return predictions;
}

function prediction(item: SplitItem, text: string, language: string | undefined, latencyMs: number, rssKbValue: number) {
  return {
    id: item.id, subset: item.subset, reference: item.reference, prediction: text,
    technical_terms: item.technical_terms ?? [], entities: item.entities ?? [],
    detected_language: language ?? null, latency_ms: latencyMs, rss_kb: rssKbValue,
  };
}

function validateSplit(items: SplitItem[], path: string) {
  if (items.length !== 600) throw new Error(`Perle split must contain exactly 600 items; ${path} has ${items.length}`);
  const ids = new Set<string>();
  for (const item of items) {
    if (!item.id || ids.has(item.id)) throw new Error(`Perle split contains a missing or duplicate id: ${item.id}`);
    if (!item.reference.trim() || !["arabic", "mixed", "english"].includes(item.subset)) throw new Error(`invalid split item: ${item.id}`);
    const audio = resolve(dirname(path), item.audio_path);
    if (!isAbsolute(audio)) throw new Error(`audio path did not resolve absolutely: ${item.id}`);
    ids.add(item.id);
  }
}

function parseArgs(values: string[]) {
  const parsed: Record<string, string> = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    const value = values[index + 1];
    if (!key?.startsWith("--") || !value || value.startsWith("--")) throw new Error(`invalid comparison argument near ${key ?? "end"}`);
    parsed[key.slice(2)] = value;
  }
  return parsed;
}

function required(values: Record<string, string>, key: string) {
  const value = values[key];
  if (!value) throw new Error(`missing --${key}`);
  return value;
}

async function sha256File(path: string) {
  const hasher = new Bun.CryptoHasher("sha256");
  for await (const chunk of Bun.file(path).stream()) hasher.update(chunk);
  return hasher.digest("hex");
}

async function commandText(command: string[]) {
  const process = Bun.spawn(command, { stdout: "pipe", stderr: "inherit" });
  const value = await new Response(process.stdout).text();
  if (await process.exited !== 0) throw new Error(`command failed: ${command.join(" ")}`);
  return value;
}

async function rssKb(pid: number) {
  const value = await commandText(["ps", "-o", "rss=", "-p", String(pid)]);
  return Number(value.trim()) || 0;
}

function lineReader(stream: ReadableStream<Uint8Array>) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  return {
    async next(): Promise<string> {
      while (true) {
        const newline = buffer.indexOf("\n");
        if (newline >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          return line;
        }
        const chunk = await reader.read();
        if (chunk.done) throw new Error("worker stdout closed before a complete response");
        buffer += decoder.decode(chunk.value, { stream: true });
      }
    },
  };
}

async function writeJson(path: string, value: unknown) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
}

async function writeJsonl(path: string, values: unknown[]) {
  await writeFile(path, `${values.map((value) => JSON.stringify(value)).join("\n")}\n`, { mode: 0o600 });
}
