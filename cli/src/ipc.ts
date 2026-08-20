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
  decodeServerMessage,
} from "./protocol.generated";

const CLIENT_VERSION = "1.0.0-alpha.1";
const HANDSHAKE_TIMEOUT_MS = 5_000;
const REQUEST_TIMEOUT_MS = 310_000;
const MODEL_INSTALL_TIMEOUT_MS = 60 * 60 * 1_000;

export function requestTimeoutFor(method: string): number {
  return method === "models.install" ? MODEL_INSTALL_TIMEOUT_MS : REQUEST_TIMEOUT_MS;
}

export function resolveEndpoint(environment: NodeJS.ProcessEnv = process.env): string {
  return runtimePaths(environment).socket;
}

export function runtimePaths(environment: NodeJS.ProcessEnv = process.env, userHome = homedir()) {
  const override = environment.OPENWHISPER_V1_HOME;
  if (override) return {
    config: join(override, "config"), data: join(override, "data"), cache: join(override, "cache"),
    runtime: join(override, "run"), socket: join(override, "run", "openwhisperd.sock"),
  };
  if (process.platform === "win32") return { config: "", data: "", cache: "", runtime: "", socket: String.raw`\\.\pipe\openwhisperd-v1` };
  const runtime = environment.XDG_RUNTIME_DIR;
  const config = join(environment.XDG_CONFIG_HOME ?? join(userHome, ".config"), "openwhisper", "v1");
  const data = join(environment.XDG_DATA_HOME ?? join(userHome, ".local", "share"), "openwhisper", "v1");
  const cache = join(environment.XDG_CACHE_HOME ?? join(userHome, ".cache"), "openwhisper", "v1");
  const runtimePath = runtime ? join(runtime, "openwhisper-v1") : join(data, "openwhisper-v1");
  return { config, data, cache, runtime: runtimePath, socket: join(runtimePath, "openwhisperd.sock") };
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
    await withTimeout(new Promise<void>((resolve, reject) => {
      socket.once("connect", resolve);
      socket.once("error", reject);
    }), HANDSHAKE_TIMEOUT_MS, "IPC connection timed out").catch((cause: unknown) => {
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
    await withTimeout(acknowledged, HANDSHAKE_TIMEOUT_MS, "IPC handshake timed out");
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
    try {
      const timeout = requestTimeoutFor(method);
      return await withTimeout(result, timeout, `Request ${method} timed out`);
    } finally {
      this.pending.delete(id);
    }
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
        this.route(decodeServerMessage(JSON.parse(payload.toString("utf8"))));
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

async function withTimeout<T>(promise: Promise<T>, milliseconds: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_, reject) => { timer = setTimeout(() => reject(localError("daemon_unavailable", message)), milliseconds); }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}
