import { readFile } from "node:fs/promises";

const [source, config] = await Promise.all([
  readFile("src/App.tsx", "utf8"),
  readFile("tsconfig.json", "utf8"),
]);

if (!source.includes("type AppProps") || !config.includes('"strict": true')) {
  throw new Error("strict TypeScript contract missing");
}
