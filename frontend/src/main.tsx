import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@fontsource-variable/readex-pro/wght.css";

import { App } from "./App";
import "./styles/fonts.css";
import "./styles/global.css";
import "./styles/tokens.css";

if (import.meta.env.MODE === "e2e") {
  void import("@wdio/tauri-plugin");
}

const root = document.getElementById("root");

if (!root) {
  throw new Error("OpenWhisper could not find its application root.");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
