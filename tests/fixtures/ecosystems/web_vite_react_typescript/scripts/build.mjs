import { mkdir, readFile, writeFile } from "node:fs/promises";

const [component, stylesheet, config] = await Promise.all([
  readFile("src/App.tsx", "utf8"),
  readFile("src/app.css", "utf8"),
  readFile("vite.config.ts", "utf8"),
]);

if (!component.includes("AppProps") || !stylesheet.includes("color-scheme") || !config.includes("outDir")) {
  throw new Error("web fixture inputs are incomplete");
}

await mkdir("dist", { recursive: true });
await writeFile("dist/index.html", "<main>fixture built</main>\n", "utf8");
