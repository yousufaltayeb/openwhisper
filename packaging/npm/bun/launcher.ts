#!/usr/bin/env bun
import { dirname, join } from "node:path";

const libc = process.platform === "linux" ? (Bun.file("/etc/alpine-release").size > 0 ? "musl" : "gnu") : "";
const suffix = process.platform === "linux" ? `-${libc}` : "";
const packageName = `@yousufaltayeb/openwhisper-${process.platform}-${process.arch}${suffix}`;
try {
  const manifestPath = Bun.resolveSync(`${packageName}/package.json`, import.meta.dir);
  const manifest = await Bun.file(manifestPath).json();
  const binary = join(dirname(manifestPath), manifest.openwhisperBinary ?? (process.platform === "win32" ? "bin/openwhisper.exe" : "bin/openwhisper"));
  const child = Bun.spawn([binary, ...process.argv.slice(2)], { stdin: "inherit", stdout: "inherit", stderr: "inherit" });
  process.exit(await child.exited);
} catch (error) {
  console.error(`OpenWhisper native payload ${packageName} is missing or invalid.`);
  console.error(String(error));
  process.exit(4);
}
