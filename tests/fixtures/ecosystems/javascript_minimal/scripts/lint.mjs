import { readFile } from 'node:fs/promises';

const source = await readFile('src/index.mjs', 'utf8');
if (!source.includes('export function add')) {
  throw new Error('expected export missing');
}
