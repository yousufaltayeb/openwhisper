import type { ErrorCode, RpcError } from "./protocol.generated";

const EXIT_CODES: Record<ErrorCode, number> = {
  usage: 2,
  configuration: 2,
  daemon_unavailable: 3,
  unsupported_capability: 4,
  permission_denied: 4,
  model_unavailable: 5,
  provider_unavailable: 5,
  transcription_failed: 6,
  cleanup_failed: 6,
  insertion_failed: 7,
  io: 8,
  network: 8,
  cancelled: 130,
  conflict: 2,
  protocol: 2,
  internal: 6,
};

export class OpenWhisperError extends Error {
  readonly code: ErrorCode;
  readonly detail?: string;
  readonly action?: string;
  readonly retryable: boolean;

  constructor(error: RpcError) {
    super(error.message);
    this.name = "OpenWhisperError";
    this.code = error.code;
    this.retryable = error.retryable;
    if (error.detail !== undefined) this.detail = error.detail;
    if (error.action !== undefined) this.action = error.action;
  }

  get exitCode(): number {
    return EXIT_CODES[this.code];
  }
}

export function localError(code: ErrorCode, message: string, action?: string): OpenWhisperError {
  return new OpenWhisperError(action === undefined
    ? { code, message, retryable: false }
    : { code, message, action, retryable: false });
}
