import assert from 'node:assert/strict';
import test from 'node:test';

import { status } from '../src/status.mjs';

test('reports status', () => {
  assert.equal(status, 'ok');
});
