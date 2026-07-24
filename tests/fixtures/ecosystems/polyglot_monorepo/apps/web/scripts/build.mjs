import { mkdir, writeFile } from 'node:fs/promises';

await mkdir('dist', { recursive: true });
await writeFile('dist/fixture.txt', 'polyglot-built\n', 'utf8');
