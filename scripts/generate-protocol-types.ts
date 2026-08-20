import { dirname, join } from "node:path";

const root = dirname(import.meta.dir);
const schemaPath = join(root, "schemas", "protocol", "openwhisper.schema.json");
const tsPath = join(root, "cli", "src", "protocol.generated.ts");
const rustPath = join(root, "crates", "openwhisper-protocol", "src", "lib.rs");
const schemaText = await Bun.file(schemaPath).text();
const schema = JSON.parse(schemaText) as {
  $defs: {
    errorCode: { enum: string[] };
  };
};
const hash = new Bun.CryptoHasher("sha256").update(schemaText).digest("hex");
const errorCodes = schema.$defs.errorCode.enum.map((value) => `  | ${JSON.stringify(value)}`).join("\n");

const types = `// @generated from schemas/protocol/openwhisper.schema.json; do not edit by hand.
export const SCHEMA_SHA256 = ${JSON.stringify(hash)};
export const CURRENT_PROTOCOL_VERSION = 3;
export const PREVIOUS_PROTOCOL_VERSION = 2;
export const MAX_FRAME_BYTES = 8 * 1024 * 1024;

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export type ErrorCode =
${errorCodes};

export interface Capability {
  available: boolean;
  backend: string;
  detail: string;
  fallback?: string;
}

export interface Capabilities {
  audio: Capability;
  toggle_hotkey: Capability;
  push_to_talk: Capability;
  insertion: Capability;
  overlay: Capability;
  notifications: Capability;
  secrets: Capability;
  service_manager: Capability;
  accelerator: Capability;
}

export interface RpcError {
  code: ErrorCode;
  message: string;
  detail?: string;
  action?: string;
  retryable: boolean;
}

export type TranscriptMode = "raw" | "clean" | "code";
export type Language = "auto" | "ar" | "en";
export type TranscriptionSource = "microphone" | "file" | "stdin";
export interface ReadinessBlocker { capability: string; code: string; detail: string; action: string }
export type CaptureState =
  | { phase: "idle" }
  | { phase: "capturing"; session_id: string; generation: number; started_at: string; mode: TranscriptMode }
  | { phase: "transcribing" | "processing"; session_id: string; generation: number; mode: TranscriptMode }
  | { phase: "delivering"; session_id: string; generation: number }
  | { phase: "failed"; session_id?: string | null; generation: number; message: string };
export interface SystemStatus {
  daemon: string; version: string; protocol: number; capture: CaptureState; capture_available: boolean;
  blockers: ReadinessBlocker[]; mode: TranscriptMode; language: Language; local_only: boolean;
  audio_backend?: string; model?: string; model_installing?: boolean;
}
export interface TranscriptionResult {
  session_id: string; generation: number; raw_text: string; final_text: string; language: Language;
  mode: TranscriptMode; duration_ms: number; source: TranscriptionSource; history_id: string | null;
  inserted: false; copied: boolean; insertion_method: "clipboard"; warnings: string[];
}
export type ModelTrust = "builtin_pinned";
export type BenchmarkStatus = "not_run";
export interface ModelInfo {
  name: string; model_id: string; installed: boolean; selected: boolean; installing: boolean;
  trust: ModelTrust; benchmark_status: BenchmarkStatus; source: string; license: string;
  size_bytes: number; sha256: string; worker_abi: string; path?: string;
}
export interface ModelDownloadProgress { name: string; downloaded_bytes: number; total_bytes: number }

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(\`Invalid \${label}\`);
  return value as Record<string, unknown>;
}
function oneOf(value: unknown, allowed: readonly string[], label: string): string {
  if (typeof value !== "string" || !allowed.includes(value)) throw new Error(\`Invalid \${label}\`);
  return value;
}
export function decodeSystemStatus(value: unknown): SystemStatus {
  const item = record(value, "system status");
  const capture = record(item.capture, "capture state");
  oneOf(capture.phase, ["idle", "capturing", "transcribing", "processing", "delivering", "failed"], "capture phase");
  oneOf(item.mode, ["raw", "clean", "code"], "mode");
  oneOf(item.language, ["auto", "ar", "en"], "language");
  if (typeof item.capture_available !== "boolean" || !Array.isArray(item.blockers)) throw new Error("Invalid readiness status");
  return item as unknown as SystemStatus;
}
export function decodeTranscriptionResult(value: unknown): TranscriptionResult {
  const item = record(value, "transcription result");
  for (const key of ["session_id", "raw_text", "final_text", "insertion_method"]) if (typeof item[key] !== "string") throw new Error(\`Invalid \${key}\`);
  if (typeof item.generation !== "number" || typeof item.duration_ms !== "number" || typeof item.copied !== "boolean" || item.inserted !== false || !Array.isArray(item.warnings)) throw new Error("Invalid transcription result fields");
  oneOf(item.language, ["auto", "ar", "en"], "language"); oneOf(item.mode, ["raw", "clean", "code"], "mode"); oneOf(item.source, ["microphone", "file", "stdin"], "source");
  return item as unknown as TranscriptionResult;
}

export function decodeServerMessage(value: unknown): ServerMessage {
  const item = record(value, "server message");
  oneOf(item.type, ["handshake_ack", "response", "error", "event", "snapshot"], "server message type");
  return item as unknown as ServerMessage;
}

export type ClientMessage =
  | { type: "handshake"; protocol_version: number; client: string; client_version: string }
  | { type: "request"; id: string; method: string; params?: JsonValue }
  | { type: "subscribe"; after_sequence?: number };

export type ServerMessage =
  | { type: "handshake_ack"; protocol_version: number; server_version: string; capabilities: Capabilities }
  | { type: "response"; id: string; result: JsonValue }
  | { type: "error"; id?: string; error: RpcError }
  | { type: "event"; sequence: number; event: string; data: JsonValue }
  | { type: "snapshot"; sequence: number; state: JsonValue };
`;

await Bun.write(tsPath, types);
const rust = await Bun.file(rustPath).text();
const updatedRust = rust.replace(
  /pub const SCHEMA_SHA256: &str = "(?:[a-f0-9]{64}|pending)";/,
  `pub const SCHEMA_SHA256: &str = "${hash}";`,
);
if (updatedRust === rust && !rust.includes(hash)) throw new Error("Rust schema hash marker was not found");
await Bun.write(rustPath, updatedRust);
console.log(`Protocol bindings synchronized to ${hash}`);
