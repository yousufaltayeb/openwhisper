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
export const CURRENT_PROTOCOL_VERSION = 2;
export const PREVIOUS_PROTOCOL_VERSION = 1;
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
