// Production build: bundles src/main.jsx -> dist/assets/app.js (+ app.css) and copies index.html.
import * as esbuild from "esbuild";
import { copyFileSync, cpSync, existsSync, mkdirSync, rmSync } from "node:fs";

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
import { readFileSync, writeFileSync } from "node:fs";
const v = Date.now().toString(36);
writeFileSync("dist/index.html", readFileSync("index.html", "utf8").replace(/\/assets\/app\.(js|css)/g, (m) => `${m}?v=${v}`));
if (existsSync("public")) cpSync("public", "dist", { recursive: true });
