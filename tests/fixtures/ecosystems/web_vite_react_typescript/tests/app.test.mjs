import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("web fixture keeps an accessible project landmark", async () => {
  const source = await readFile("src/App.tsx", "utf8");
  assert.match(source, /aria-label="project-status"/);
});
