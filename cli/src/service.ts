import { dirname, join } from "node:path";

import { localError } from "./errors";
import { IpcClient } from "./ipc";

export function daemonExecutable(): string {
  if (process.env.OPENWHISPERD_PATH) return process.env.OPENWHISPERD_PATH;
  const suffix = process.platform === "win32" ? ".exe" : "";
  if (process.execPath.includes("openwhisper")) return join(dirname(process.execPath), `openwhisperd${suffix}`);
  return join(process.cwd(), "target", "debug", `openwhisperd${suffix}`);
}

export async function startDaemon(): Promise<void> {
  const executable = daemonExecutable();
  const file = Bun.file(executable);
  if (!(await file.exists())) {
    throw localError(
      "daemon_unavailable",
      `Daemon executable was not found at ${executable}.`,
      "Install the complete OpenWhisper platform package or run `cargo build -p openwhisperd`.",
    );
  }
  const child = Bun.spawn([executable], {
    stdin: "ignore",
    stdout: "ignore",
    stderr: "ignore",
    env: process.env,
  });
  child.unref();
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await Bun.sleep(50);
    const client = new IpcClient();
    try {
      await client.connect();
      client.close();
      return;
    } catch {
      client.close();
    }
  }
  throw localError("daemon_unavailable", "Daemon did not become ready within 1.5 seconds.");
}

export async function connectClient(autoStart: boolean): Promise<IpcClient> {
  const client = new IpcClient();
  try {
    await client.connect();
    return client;
  } catch (error) {
    client.close();
    if (!autoStart) throw error;
  }
  await startDaemon();
  const retried = new IpcClient();
  await retried.connect();
  return retried;
}
