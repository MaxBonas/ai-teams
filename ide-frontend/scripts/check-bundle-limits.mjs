import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';

const assetsRoot = fileURLToPath(new URL('../dist/assets/', import.meta.url));
const budgets = {
  '.js': { raw: 400 * 1024, gzip: 120 * 1024 },
  '.css': { raw: 120 * 1024, gzip: 25 * 1024 },
};
const totals = Object.fromEntries(Object.keys(budgets).map((extension) => [
  extension,
  { raw: 0, gzip: 0 },
]));

for (const name of await readdir(assetsRoot)) {
  const extension = Object.keys(budgets).find((candidate) => name.endsWith(candidate));
  if (!extension) continue;
  const content = await readFile(join(assetsRoot, name));
  totals[extension].raw += content.byteLength;
  totals[extension].gzip += gzipSync(content).byteLength;
}

const failures = [];
for (const [extension, budget] of Object.entries(budgets)) {
  const total = totals[extension];
  if (total.raw > budget.raw) failures.push(`${extension} raw ${total.raw}/${budget.raw}`);
  if (total.gzip > budget.gzip) failures.push(`${extension} gzip ${total.gzip}/${budget.gzip}`);
}

if (failures.length) {
  process.stderr.write(`Bundle por encima del presupuesto:\n${failures.map((item) => `- ${item}`).join('\n')}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(
    `Bundle dentro del presupuesto: JS ${totals['.js'].raw} B raw/${totals['.js'].gzip} B gzip; `
    + `CSS ${totals['.css'].raw} B raw/${totals['.css'].gzip} B gzip.\n`,
  );
}
