import { readdir, readFile } from 'node:fs/promises';
import { extname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const sourceRoot = fileURLToPath(new URL('../src/', import.meta.url));
const defaultLimits = {
  '.ts': 600,
  '.tsx': 600,
  '.css': 500,
};
const ratchets = new Map([
  ['App.tsx', 3600],
  ['index.css', 1300],
  ['components/ModelCatalog/ModelCatalog.tsx', 750],
]);

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  }));
  return nested.flat();
}

const failures = [];
for (const path of await filesUnder(sourceRoot)) {
  const extension = extname(path);
  if (!(extension in defaultLimits)) continue;
  const modulePath = relative(sourceRoot, path).replaceAll('\\', '/');
  const limit = ratchets.get(modulePath) ?? defaultLimits[extension];
  const lines = (await readFile(path, 'utf8')).split(/\r?\n/).length;
  if (lines > limit) failures.push(`${modulePath}: ${lines}/${limit}`);
}

if (failures.length) {
  process.stderr.write(`Módulos por encima del límite:\n${failures.map((item) => `- ${item}`).join('\n')}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write('Límites de módulo respetados.\n');
}
