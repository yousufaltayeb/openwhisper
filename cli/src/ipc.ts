import { connect, type Socket } from "node:net";
import { homedir } from "node:os";
import { join } from "node:path";

import { OpenWhisperError, localError } from "./errors";
import {
  CURRENT_PROTOCOL_VERSION,
  MAX_FRAME_BYTES,
  type ClientMessage,
  type JsonValue,
  type ServerMessage,
} from "./protocol.generated";

const CLIENT_VERSION = "1.0.0-alpha.1";

export function resolveEndpoint(environment: NodeJS.ProcessEnv = process.env): string {
  const override = environment.OPENWHISPER_V1_HOME;
  if (override) return join(override, "run", "openwhisperd.sock");
  if (process.platform === "win32") return String.raw`\\.\pipe\openwhisperd-v1`;
  const runtime = environment.XDG_RUNTIME_DIR;
  if (runtime) return join(runtime, "openwhisper-v1", "openwhisperd.sock");
  const data = environment.XDG_DATA_HOME ?? join(homedir(), ".local", "share");
  return join(data, "openwhisper", "v1", "openwhisper-v1", "openwhisperd.sock");
}

export type EventHandler = (message: Extract<ServerMessage, { type: "event" | "snapshot" }>) => void;

export class IpcClient {
  private socket: Socket | undefined;
  private buffer = Buffer.alloc(0);
  private pending = new Map<string, { resolve(value: JsonValue): void; reject(reason: unknown): void }>();
  private handshakeResolve: ((message: Extract<ServerMessage, { type: "handshake_ack" }>) => void) | undefined;
  private handshakeReject: ((reason: unknown) => void) | undefined;
  private listeners = new Set<EventHandler>();
  private disconnectListeners = new Set<() => void>();

  async connect(endpoint = resolveEndpoint()): Promise<void> {
    if (this.socket) return;
    const socket = connect(endpoint);
    this.socket = socket;
    socket.on("data", (chunk: Buffer) => this.receive(chunk));
    socket.on("error", (error) => this.rejectAll(error));
    socket.on("close", () => {
      const wasConnected = this.socket !== undefined;
      this.socket = undefined;
      this.rejectAll(localError("daemon_unavailable", "OpenWhisper daemon disconnected."));
      if (wasConnected) for (const listener of this.disconnectListeners) listener();
    });
    await new Promise<void>((resolve, reject) => {
      socket.once("connect", resolve);
      socket.once("error", reject);
    }).catch((cause: unknown) => {
      this.socket = undefined;
      throw localError(
        "daemon_unavailable",
        "OpenWhisper daemon is unavailable.",
        cause instanceof Error ? cause.message : "Run `openwhisper service start`.",
      );
    });

    const acknowledged = new Promise<Extract<ServerMessage, { type: "handshake_ack" }>>(
      (resolve, reject) => {
        this.handshakeResolve = resolve;
        this.handshakeReject = reject;
      },
    );
    this.send({
      type: "handshake",
      protocol_version: CURRENT_PROTOCOL_VERSION,
      client: "openwhisper",
      client_version: CLIENT_VERSION,
    });
    await acknowledged;
  }

  close(): void {
    this.socket?.destroy();
    this.socket = undefined;
  }

  async request(method: string, params: JsonValue = null): Promise<JsonValue> {
    if (!this.socket) throw localError("daemon_unavailable", "OpenWhisper daemon is not connected.");
    const id = crypto.randomUUID();
    const result = new Promise<JsonValue>((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.send({ type: "request", id, method, params });
    return await result;
  }

  subscribe(afterSequence = 0, listener: EventHandler): () => void {
    this.listeners.add(listener);
    this.send({ type: "subscribe", after_sequence: afterSequence });
    return () => this.listeners.delete(listener);
  }

  onDisconnect(listener: () => void): () => void {
    this.disconnectListeners.add(listener);
    return () => this.disconnectListeners.delete(listener);
  }

  private send(message: ClientMessage): void {
    const payload = Buffer.from(JSON.stringify(message), "utf8");
    if (payload.byteLength > MAX_FRAME_BYTES) {
      throw localError("protocol", `IPC frame exceeds ${MAX_FRAME_BYTES} bytes.`);
    }
    const header = Buffer.allocUnsafe(4);
    header.writeUInt32BE(payload.byteLength);
    this.socket?.write(Buffer.concat([header, payload]));
  }

  private receive(chunk: Buffer): void {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    while (this.buffer.byteLength >= 4) {
      const length = this.buffer.readUInt32BE(0);
      if (length > MAX_FRAME_BYTES) {
        this.rejectAll(localError("protocol", `Daemon sent an oversized ${length}-byte frame.`));
        this.close();
        return;
      }
      if (this.buffer.byteLength < length + 4) return;
      const payload = this.buffer.subarray(4, 4 + length);
      this.buffer = this.buffer.subarray(4 + length);
      try {
        this.route(JSON.parse(payload.toString("utf8")) as ServerMessage);
      } catch (cause) {
        this.rejectAll(localError("protocol", "Daemon sent malformed JSON.", String(cause)));
        this.close();
      }
    }
  }

  private route(message: ServerMessage): void {
    if (message.type === "handshake_ack") {
      this.handshakeResolve?.(message);
      this.handshakeResolve = undefined;
      this.handshakeReject = undefined;
      return;
    }
    if (message.type === "error") {
      const error = new OpenWhisperError(message.error);
      if (message.id) {
        this.pending.get(message.id)?.reject(error);
        this.pending.delete(message.id);
      } else {
        this.handshakeReject?.(error);
      }
      return;
    }
    if (message.type === "response") {
      this.pending.get(message.id)?.resolve(message.result);
      this.pending.delete(message.id);
      return;
    }
    for (const listener of this.listeners) listener(message);
  }

  private rejectAll(reason: unknown): void {
    this.handshakeReject?.(reason);
    this.handshakeReject = undefined;
    for (const pending of this.pending.values()) pending.reject(reason);
    this.pending.clear();
  }
}
