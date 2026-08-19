// @generated from schemas/protocol/openwhisper.schema.json; do not edit by hand.
export const SCHEMA_SHA256 = "bd57bc6d79cc96ca0ece63b31701477d8653c2706dc7a44319e954353f0841a7";
export const CURRENT_PROTOCOL_VERSION = 2;
export const PREVIOUS_PROTOCOL_VERSION = 1;
export const MAX_FRAME_BYTES = 8 * 1024 * 1024;

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export type ErrorCode =
  | "usage"
  | "configuration"
  | "daemon_unavailable"
  | "unsupported_capability"
  | "permission_denied"
  | "model_unavailable"
  | "provider_unavailable"
  | "transcription_failed"
  | "cleanup_failed"
  | "insertion_failed"
  | "io"
  | "network"
  | "cancelled"
  | "conflict"
  | "protocol"
  | "internal";

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
