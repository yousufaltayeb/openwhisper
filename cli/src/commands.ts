import { existsSync } from "node:fs";

import { localError } from "./errors";
import type { JsonValue } from "./protocol.generated";

export interface ParsedCommand {
  route: "ui" | "rpc" | "local";
  method?: string;
  params: JsonValue;
  local?: "help" | "version" | "completion" | "service_start";
}

function requireArg(args: string[], index: number, name: string): string {
  const value = args[index];
  if (!value || value.startsWith("--")) throw localError("usage", `Missing ${name}.`);
  return value;
}

function option(args: string[], name: string): string | undefined {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}

export function parseCommand(args: string[]): ParsedCommand {
  const [group, action] = args;
  if (!group || group === "help" || group === "--help" || group === "-h") return { route: "local", local: "help", params: null };
  if (group === "ui") return { route: "ui", params: null };
  if (group === "version" || group === "--version" || group === "-V") return { route: "local", local: "version", params: null };
  if (group === "completion") return { route: "local", local: "completion", params: { shell: action ?? "bash" } };
  if (group === "doctor") return rpc("system.doctor");
  if (group === "setup") return rpc("system.setup");
  if (group === "logs") return rpc("system.logs", { follow: args.includes("--follow") });
  if (group === "update") return rpc("system.update");

  if (group === "record") {
    if (!["start", "stop", "toggle", "cancel", "status"].includes(action ?? "")) throw localError("usage", "Record action must be start, stop, toggle, cancel, or status.");
    const mode = option(args, "--mode");
    return rpc(`record.${action}`, mode ? { wait: args.includes("--wait"), mode } : { wait: args.includes("--wait") });
  }
  if (group === "transcribe") {
    const path = requireArg(args, 1, "path or -");
    if (path !== "-" && !existsSync(path)) throw localError("io", `Input does not exist: ${path}`);
    return rpc("transcribe.file", { path, mode: option(args, "--mode") ?? "raw", insert: args.includes("--insert") });
  }
  if (group === "history") {
    if (action === "list") return rpc("history.list", { limit: Number(option(args, "--limit") ?? 50) });
    if (action === "search") return rpc("history.search", { query: requireArg(args, 2, "search query"), limit: Number(option(args, "--limit") ?? 50) });
    if (["show", "copy", "delete"].includes(action ?? "")) return rpc(`history.${action}`, { id: requireArg(args, 2, "history id") });
    if (action === "clear") return rpc("history.clear", { confirmed: args.includes("--yes") });
    if (action === "export") return rpc("history.export", { path: requireArg(args, 2, "export path") });
    throw localError("usage", "History action must be list, search, show, copy, delete, clear, or export.");
  }
  if (group === "modes") {
    if (action === "list") return rpc("modes.list");
    if (action === "show") return rpc("modes.show", { name: requireArg(args, 2, "mode name") });
    if (action === "select") return rpc("modes.select", { name: requireArg(args, 2, "mode name") });
    throw localError("usage", "Modes action must be list, show, or select.");
  }
  if (group === "vocab") return collectionCommand("vocab", action, args, "term");
  if (group === "snippets") {
    if (action === "list") return rpc("snippets.list");
    if (action === "add") return rpc("snippets.add", { name: requireArg(args, 2, "snippet name"), body: requireArg(args, 3, "snippet body") });
    if (["remove", "run"].includes(action ?? "")) return rpc(`snippets.${action}`, { name: requireArg(args, 2, "snippet name") });
    if (["import", "export"].includes(action ?? "")) return rpc(`snippets.${action}`, { path: requireArg(args, 2, "path") });
    throw localError("usage", "Snippets action must be list, add, remove, run, import, or export.");
  }
  if (group === "models") return namedResourceCommand("models", action, args);
  if (group === "providers") return namedResourceCommand("providers", action, args);
  if (group === "config") {
    if (action === "list") return rpc("config.list");
    if (action === "get") return rpc("config.get", { key: requireArg(args, 2, "config key") });
    if (action === "set") return rpc("config.set", { key: requireArg(args, 2, "config key"), value: parseValue(requireArg(args, 3, "config value")) });
    throw localError("usage", "Config action must be list, get, or set.");
  }
  if (group === "service") {
    if (action === "start") return { route: "local", local: "service_start", params: null };
    if (action === "status") return rpc("system.status");
    if (action === "stop") return rpc("system.shutdown");
    if (["install", "restart", "uninstall"].includes(action ?? "")) return rpc(`service.${action}`, { purge: args.includes("--purge") });
    throw localError("usage", "Service action must be install, start, stop, restart, status, or uninstall.");
  }
  throw localError("usage", `Unknown command: ${group}`);
}

function rpc(method: string, params: JsonValue = null): ParsedCommand {
  return { route: "rpc", method, params };
}

function collectionCommand(group: string, action: string | undefined, args: string[], key: string): ParsedCommand {
  if (action === "list") return rpc(`${group}.list`);
  if (["add", "remove"].includes(action ?? "")) return rpc(`${group}.${action}`, { [key]: requireArg(args, 2, key) });
  if (["import", "export"].includes(action ?? "")) return rpc(`${group}.${action}`, { path: requireArg(args, 2, "path") });
  throw localError("usage", `${group} action must be list, add, remove, import, or export.`);
}

function namedResourceCommand(group: string, action: string | undefined, args: string[]): ParsedCommand {
  if (action === "list") return rpc(`${group}.list`);
  if (["install", "remove", "verify", "select", "import", "configure", "test", "unset"].includes(action ?? "")) {
    return rpc(`${group}.${action}`, { name: requireArg(args, 2, `${group} name`) });
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
