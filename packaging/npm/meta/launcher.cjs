#!/usr/bin/env node
const { spawnSync } = require("node:child_process");
const { existsSync, readFileSync } = require("node:fs");
const { join } = require("node:path");

function libc() {
  if (process.platform !== "linux") return "";
  try { return process.report.getReport().header.glibcVersionRuntime ? "gnu" : "musl"; }
  catch { return existsSync("/etc/alpine-release") ? "musl" : "gnu"; }
}

const suffix = process.platform === "linux" ? `-${libc()}` : "";
const packageName = `@yousufaltayeb/openwhisper-${process.platform}-${process.arch}${suffix}`;
let root;
try { root = require.resolve(`${packageName}/package.json`); }
catch {
  console.error(`OpenWhisper has no installed native payload for ${process.platform}/${process.arch}${suffix}.`);
  console.error(`Install ${packageName} at exactly the same version, or use a native installer.`);
  process.exit(4);
}
const manifest = JSON.parse(readFileSync(root, "utf8"));
const binary = join(root, "..", manifest.openwhisperBinary || (process.platform === "win32" ? "bin/openwhisper.exe" : "bin/openwhisper"));
const result = spawnSync(binary, process.argv.slice(2), { stdio: "inherit" });
if (result.error) { console.error(result.error.message); process.exit(8); }
process.exit(result.status === null ? 130 : result.status);
