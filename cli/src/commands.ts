import { existsSync } from "node:fs";

import { localError } from "./errors";
import type { JsonValue } from "./protocol.generated";

export interface ParsedCommand {
  route: "ui" | "rpc" | "local";
  method?: string;
  params: JsonValue;
  local?: "help" | "version" | "completion" | "service_start";
  forceNoStart?: boolean;
}

function requireArg(args: string[], index: number, name: string): string {
  const value = args[index];
  if (!value || value.startsWith("--")) throw localError("usage", `Missing ${name}.`);
  return value;
}

function option(args: string[], name: string): string | undefined {
  const index = args.indexOf(name);
  if (index < 0) return undefined;
  const value = args[index + 1];
  if (!value || value.startsWith("--")) throw localError("usage", `Missing value for ${name}.`);
  return value;
}

function validateTail(args: string[], start: number, valueFlags: string[] = [], booleanFlags: string[] = []): void {
  const seen = new Set<string>();
  for (let index = start; index < args.length; index += 1) {
    const token = args[index]!;
    if (!token.startsWith("--")) throw localError("usage", `Unexpected argument: ${token}`);
    if (seen.has(token)) throw localError("usage", `Duplicate option: ${token}`);
    seen.add(token);
    if (valueFlags.includes(token)) {
      const value = args[++index];
      if (!value || value.startsWith("--")) throw localError("usage", `Missing value for ${token}.`);
    } else if (!booleanFlags.includes(token)) {
      throw localError("usage", `Unknown option: ${token}`);
    }
  }
}

function enumValue(value: string | undefined, name: string, allowed: string[], fallback?: string): string | undefined {
  const resolved = value ?? fallback;
  if (resolved !== undefined && !allowed.includes(resolved)) throw localError("usage", `${name} must be ${allowed.join(", ")}.`);
  return resolved;
}

function limitValue(value: string | undefined): number {
  if (value === undefined) return 50;
  if (!/^\d+$/.test(value)) throw localError("usage", "--limit must be an integer from 1 to 1000.");
  const limit = Number(value);
  if (limit < 1 || limit > 1000) throw localError("usage", "--limit must be an integer from 1 to 1000.");
  return limit;
}

export function parseCommand(args: string[]): ParsedCommand {
  const [group, action] = args;
  if (!group || group === "help" || group === "--help" || group === "-h") return { route: "local", local: "help", params: null };
  if (group === "ui") { validateTail(args, 1); return { route: "ui", params: null }; }
  if (group === "version" || group === "--version" || group === "-V") return { route: "local", local: "version", params: null };
  if (group === "completion") return { route: "local", local: "completion", params: { shell: action ?? "bash" } };
  if (group === "doctor") { validateTail(args, 1); return rpc("system.doctor"); }
  if (group === "setup") { validateTail(args, 1); return rpc("system.setup"); }
  if (group === "logs") { validateTail(args, 1, [], ["--follow"]); return rpc("system.logs", { follow: args.includes("--follow") }); }
  if (group === "update") { validateTail(args, 1); return rpc("system.update"); }

  if (group === "record") {
    if (!["start", "stop", "toggle", "cancel", "status"].includes(action ?? "")) throw localError("usage", "Record action must be start, stop, toggle, cancel, or status.");
    validateTail(args, 2, ["--mode"], ["--wait", "--insert-live"]);
    if (args.includes("--wait") && action !== "stop") throw localError("usage", "--wait is supported only by `record stop`.");
    if (args.includes("--mode") && !["start", "toggle"].includes(action!)) throw localError("usage", "--mode is supported only by record start/toggle.");
    if (args.includes("--insert-live") && !["start", "toggle"].includes(action!)) throw localError("usage", "--insert-live is supported only by record start/toggle.");
    const mode = enumValue(option(args, "--mode"), "--mode", ["raw", "clean", "code"]);
    const params: Record<string, JsonValue> = {};
    if (mode) params.mode = mode;
    if (args.includes("--insert-live")) params.insert_live = true;
    if (action === "stop") params.wait = args.includes("--wait");
    return rpc(`record.${action}`, params);
  }
  if (group === "transcribe") {
    const path = requireArg(args, 1, "path or -");
    validateTail(args, 2, ["--mode", "--language"], ["--copy", "--insert"]);
    if (path !== "-" && !existsSync(path)) throw localError("io", `Input does not exist: ${path}`);
    return rpc("transcribe.file", {
      path, mode: enumValue(option(args, "--mode"), "--mode", ["raw", "clean", "code"], "raw")!,
      language: enumValue(option(args, "--language"), "--language", ["auto", "ar", "en"], "auto")!,
      copy: args.includes("--copy"), insert: args.includes("--insert"), source: path === "-" ? "stdin" : "file",
    });
  }
  if (group === "history") {
    if (action === "list") { validateTail(args, 2, ["--limit"]); return rpc("history.list", { limit: limitValue(option(args, "--limit")) }); }
    if (action === "search") { const query = requireArg(args, 2, "search query"); validateTail(args, 3, ["--limit"]); return rpc("history.search", { query, limit: limitValue(option(args, "--limit")) }); }
    if (["show", "copy", "delete"].includes(action ?? "")) { const id = requireArg(args, 2, "history id"); validateTail(args, 3); return rpc(`history.${action}`, { id }); }
    if (action === "clear") { validateTail(args, 2, [], ["--yes"]); return rpc("history.clear", { confirmed: args.includes("--yes") }); }
    if (action === "export") { const path = requireArg(args, 2, "export path"); validateTail(args, 3); return rpc("history.export", { path }); }
    throw localError("usage", "History action must be list, search, show, copy, delete, clear, or export.");
  }
  if (group === "modes") {
    if (action === "list") { validateTail(args, 2); return rpc("modes.list"); }
    if (action === "show" || action === "select") { const name = requireArg(args, 2, "mode name"); validateTail(args, 3); enumValue(name, "mode", ["raw", "clean", "code"]); return rpc(`modes.${action}`, { name }); }
    throw localError("usage", "Modes action must be list, show, or select.");
  }
  if (group === "vocab") return collectionCommand("vocab", action, args, "term");
  if (group === "snippets") {
    if (action === "list") { validateTail(args, 2); return rpc("snippets.list"); }
    if (action === "add") { const name = requireArg(args, 2, "snippet name"); const body = requireArg(args, 3, "snippet body"); validateTail(args, 4); return rpc("snippets.add", { name, body }); }
    if (["remove", "run"].includes(action ?? "")) { const name = requireArg(args, 2, "snippet name"); validateTail(args, 3); return rpc(`snippets.${action}`, { name }); }
    if (["import", "export"].includes(action ?? "")) { const path = requireArg(args, 2, "path"); validateTail(args, 3); return rpc(`snippets.${action}`, { path }); }
    throw localError("usage", "Snippets action must be list, add, remove, run, import, or export.");
  }
  if (group === "models") return namedResourceCommand("models", action, args);
  if (group === "providers") return namedResourceCommand("providers", action, args);
  if (group === "config") {
    if (action === "list") { validateTail(args, 2); return rpc("config.list"); }
    if (action === "get") { const key = requireArg(args, 2, "config key"); validateTail(args, 3); return rpc("config.get", { key }); }
    if (action === "set") { const key = requireArg(args, 2, "config key"); const value = requireArg(args, 3, "config value"); validateTail(args, 4); return rpc("config.set", { key, value: parseValue(value) }); }
    throw localError("usage", "Config action must be list, get, or set.");
  }
  if (group === "service") {
    if (action === "start") return { route: "local", local: "service_start", params: null };
    if (action === "status") { validateTail(args, 2); return { ...rpc("system.status"), forceNoStart: true }; }
    if (action === "stop") { validateTail(args, 2); return { ...rpc("system.shutdown"), forceNoStart: true }; }
    if (["install", "restart", "uninstall"].includes(action ?? "")) return rpc(`service.${action}`, { purge: args.includes("--purge") });
    throw localError("usage", "Service action must be install, start, stop, restart, status, or uninstall.");
  }
  throw localError("usage", `Unknown command: ${group}`);
}

function rpc(method: string, params: JsonValue = null): ParsedCommand {
  return { route: "rpc", method, params };
}

function collectionCommand(group: string, action: string | undefined, args: string[], key: string): ParsedCommand {
  if (action === "list") { validateTail(args, 2); return rpc(`${group}.list`); }
  if (["add", "remove"].includes(action ?? "")) { const value = requireArg(args, 2, key); validateTail(args, 3); return rpc(`${group}.${action}`, { [key]: value }); }
  if (["import", "export"].includes(action ?? "")) { const path = requireArg(args, 2, "path"); validateTail(args, 3); return rpc(`${group}.${action}`, { path }); }
  throw localError("usage", `${group} action must be list, add, remove, import, or export.`);
}

function namedResourceCommand(group: string, action: string | undefined, args: string[]): ParsedCommand {
  if (action === "list") { validateTail(args, 2); return rpc(`${group}.list`); }
  if (group === "models" && action === "import") {
    const name = requireArg(args, 2, "model name");
    enumValue(name, "model", ["fast", "balanced", "accurate"]);
    const path = requireArg(args, 3, "model path");
    validateTail(args, 4);
    if (!existsSync(path)) throw localError("io", `Input does not exist: ${path}`);
    return rpc("models.import", { name, path });
  }
  if (group === "models" && action === "install") {
    const name = requireArg(args, 2, "model name");
    enumValue(name, "model", ["fast", "balanced", "accurate"]);
    validateTail(args, 3, [], ["--yes"]);
    return rpc("models.install", { name, yes: args.includes("--yes") });
  }
  if (["install", "remove", "verify", "select", "import", "configure", "test", "unset"].includes(action ?? "")) {
    const name = requireArg(args, 2, `${group} name`);
    if (group === "models") enumValue(name, "model", ["fast", "balanced", "accurate"]);
    validateTail(args, 3);
    return rpc(`${group}.${action}`, { name });
  }
  throw localError("usage", `Unsupported ${group} action.`);
}

function parseValue(value: string): JsonValue {
  if (value === "true") return true;
  if (value === "false") return false;
  if (value === "null") return null;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  return value;
}
