// Production build: bundles src/main.jsx -> dist/assets/app.js (+ app.css) and copies index.html.
import * as esbuild from "esbuild";
import { copyFileSync, mkdirSync, rmSync } from "node:fs";

rmSync("dist", { recursive: true, force: true });
mkdirSync("dist/assets", { recursive: true });
await esbuild.build({
  entryPoints: { app: "src/main.jsx" },
  bundle: true,
  minify: true,
  sourcemap: false,
  format: "esm",
  target: ["es2020"],
  outdir: "dist/assets",
  jsx: "automatic",
  loader: { ".js": "jsx" },
  define: { "process.env.NODE_ENV": '"production"' },
  legalComments: "none",
  logLevel: "info",
});
copyFileSync("index.html", "dist/index.html");
