import { inspect } from "node:util";

import type { JsonValue } from "./protocol.generated";

export type OutputMode = "human" | "plain" | "json" | "jsonl";

export function renderOutput(value: JsonValue, mode: OutputMode): string {
  if (mode === "json") return `${JSON.stringify(value, null, 2)}\n`;
  if (mode === "jsonl") {
    if (Array.isArray(value)) return `${value.map((item) => JSON.stringify(item)).join("\n")}\n`;
    return `${JSON.stringify(value)}\n`;
  }
  if (typeof value === "string") return `${value}\n`;
  if (value === null) return "\n";
  if (Array.isArray(value)) {
    return `${value.map((item) => plainItem(item)).join("\n")}\n`;
  }
  if (typeof value === "object") {
    return `${Object.entries(value)
      .map(([key, item]) => `${key}: ${plainItem(item)}`)
      .join("\n")}\n`;
  }
  return `${String(value)}\n`;
}

function plainItem(value: JsonValue): string {
  if (value === null) return "—";
  if (typeof value === "object") return inspect(value, { colors: false, depth: 4, compact: true, breakLength: 100 });
  return String(value);
}
