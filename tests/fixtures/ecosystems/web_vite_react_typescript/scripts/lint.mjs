import { readFile } from "node:fs/promises";

const source = await readFile("src/App.tsx", "utf8");
if (source.includes(": any") || source.includes("var ")) {
  throw new Error("unsafe TypeScript pattern");
}
