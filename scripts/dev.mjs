#!/usr/bin/env node
/**
 * AI Team dev launcher
 * Finds two free ports, starts backend (uvicorn) + frontend (vite), wires them together.
 */

import { createServer } from 'net';
import { spawn } from 'child_process';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const IS_WIN = process.platform === 'win32';

// ── Port finder ──────────────────────────────────────────────────────────────

function freePort(preferred) {
  return new Promise((resolve, reject) => {
    const server = createServer();
    const port = preferred ?? 0;
    server.listen(port, '127.0.0.1', () => {
      const { port: found } = server.address();
      server.close(() => resolve(found));
    });
    server.on('error', () => {
      // Preferred port busy → get any free one
      const s2 = createServer();
      s2.listen(0, '127.0.0.1', () => {
        const { port: found } = s2.address();
        s2.close(() => resolve(found));
      });
      s2.on('error', reject);
    });
  });
}

// ── Health check ─────────────────────────────────────────────────────────────

async function waitForHttp(url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok || res.status < 500) return true;
    } catch { /* not ready yet */ }
    await new Promise(r => setTimeout(r, 700));
  }
  return false;
}

// ── Process helpers ──────────────────────────────────────────────────────────

function resolvePython() {
  if (IS_WIN) {
    const wrapper = join(ROOT, 'scripts', 'python_local.bat');
    if (existsSync(wrapper)) return wrapper;
  }
  const venvPy = join(ROOT, 'venv', 'Scripts', 'python.exe');
  if (existsSync(venvPy)) return venvPy;
  return IS_WIN ? 'python' : 'python3';
}

function runSetup() {
  if (!IS_WIN) return Promise.resolve();
  const setupScript = join(ROOT, 'scripts', 'prepare_dev_env.bat');
  if (!existsSync(setupScript)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const proc = spawn('cmd.exe', ['/c', setupScript], {
      cwd: ROOT,
      stdio: 'inherit',
    });
    proc.on('error', reject);
    proc.on('exit', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`prepare_dev_env failed with code ${code}`));
    });
  });
}

function spawnProc(cmd, args, opts = {}) {
  const isCmd = cmd.endsWith('.cmd') || cmd.endsWith('.bat');
  const proc = spawn(cmd, args, {
    cwd: ROOT,
    stdio: 'inherit',
    shell: IS_WIN && isCmd,
    ...opts,
  });
  proc.on('error', (err) => {
    console.error(`\n[dev] Failed to start "${cmd}": ${err.message}`);
  });
  return proc;
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  await runSetup();
  // Find two free ports (prefer familiar ones, fall back to any)
  const [backendPort, frontendPort] = await Promise.all([
    freePort(8010),
    freePort(9490),
  ]);

  console.log('\n╔══════════════════════════════════════════╗');
  console.log('║       AI Team IDE — dev launcher         ║');
  console.log('╚══════════════════════════════════════════╝');
  console.log(`  Backend  → http://localhost:${backendPort}`);
  console.log(`  Frontend → http://localhost:${frontendPort}`);
  console.log('  Press Ctrl+C to stop all services\n');

  const python = resolvePython();

  // ── Backend ────────────────────────────────────────────────────────────────
  const backend = spawnProc(
    python,
    ['-m', 'uvicorn', 'api.main:app',
      '--host', '0.0.0.0',
      '--port', String(backendPort),
      '--reload'],
    { cwd: ROOT },
  );

  // ── Frontend ───────────────────────────────────────────────────────────────
  const frontendCwd = join(ROOT, 'ide-frontend');
  const npmCmd = IS_WIN ? 'npm.cmd' : 'npm';
  const frontend = spawnProc(
    npmCmd,
    ['run', 'dev', '--',
      '--host', '0.0.0.0',
      '--port', String(frontendPort),
      '--strictPort'],
    {
      cwd: frontendCwd,
      env: {
        ...process.env,
        VITE_API_URL: `http://127.0.0.1:${backendPort}`,
      },
    },
  );

  // ── Wait for both to be ready ─────────────────────────────────────────────
  console.log('  Waiting for services to be ready...');
  const [backendOk, frontendOk] = await Promise.all([
    waitForHttp(`http://127.0.0.1:${backendPort}/openapi.json`),
    waitForHttp(`http://127.0.0.1:${frontendPort}`),
  ]);

  if (backendOk && frontendOk) {
    console.log('\n  ✓ Backend  ready');
    console.log('  ✓ Frontend ready');
    console.log(`\n  Open: http://localhost:${frontendPort}\n`);

    // Auto-open browser (best-effort)
    const openCmd = IS_WIN ? 'start' : (process.platform === 'darwin' ? 'open' : 'xdg-open');
    spawn(openCmd, [`http://localhost:${frontendPort}`], { shell: true, detached: true, stdio: 'ignore' }).unref();
  } else {
    console.error('\n  ✗ One or more services failed to start. Check logs above.');
  }

  // ── Cleanup on Ctrl+C ─────────────────────────────────────────────────────
  function shutdown() {
    console.log('\n[dev] Shutting down...');
    backend.kill('SIGTERM');
    frontend.kill('SIGTERM');
    setTimeout(() => process.exit(0), 1000);
  }
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  // Keep alive until children die
  let exited = 0;
  [backend, frontend].forEach(p => {
    p.on('exit', (code, signal) => {
      if (signal !== 'SIGTERM') {
        console.error(`\n[dev] A service exited unexpectedly (code=${code}). Shutting down.`);
        shutdown();
      }
      if (++exited >= 2) process.exit(0);
    });
  });
}

main().catch(err => {
  console.error('[dev] Fatal:', err);
  process.exit(1);
});
