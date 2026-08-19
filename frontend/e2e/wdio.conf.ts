import path from "node:path";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));
const appBinary = path.resolve(directory, "../../src-tauri/target/debug/openwhisper");
process.env.OPENWHISPER_ENGINE = path.resolve(directory, "fixtures/fake-engine.py");

export const config: WebdriverIO.Config = {
  runner: "local",
  specs: ["./specs/**/*.e2e.ts"],
  maxInstances: 1,
  logLevel: "warn",
  waitforTimeout: 12_000,
  connectionRetryTimeout: 90_000,
  connectionRetryCount: 1,
  services: [
    [
      "tauri",
      {
        appBinaryPath: appBinary,
        driverProvider: "embedded",
        embeddedPort: 4445,
        captureBackendLogs: true,
        captureFrontendLogs: true,
        startTimeout: 60_000,
      },
    ],
  ],
  capabilities: [
    {
      browserName: "tauri",
      "tauri:options": {
        application: appBinary,
        args: ["--show"],
      },
    },
  ],
  framework: "mocha",
  reporters: ["spec"],
  mochaOpts: {
    ui: "bdd",
    timeout: 60_000,
  },
};
