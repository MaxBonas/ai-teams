import { mkdir, writeFile } from 'node:fs/promises';

await mkdir('dist', { recursive: true });
await writeFile('dist/fixture.txt', 'fixture-built\n', 'utf8');
