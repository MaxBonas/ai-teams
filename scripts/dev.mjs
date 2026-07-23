#!/usr/bin/env node
/**
 * AI Team dev launcher
 * Finds two free ports, starts backend (uvicorn) + frontend (vite), wires them together.
 */

import { createServer } from 'net';
import { spawn, spawnSync } from 'child_process';
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
  const venvPy = IS_WIN
    ? join(ROOT, 'venv', 'Scripts', 'python.exe')
    : join(ROOT, 'venv', 'bin', 'python');
  if (existsSync(venvPy)) return venvPy;
  throw new Error('Falta el Python local; ejecuta el bootstrap del checkout.');
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

function processRegistry(python, args, quiet = false) {
  const result = spawnSync(
    python,
    [join(ROOT, 'scripts', 'ide_processes.py'), ...args],
    { cwd: ROOT, encoding: 'utf8' },
  );
  if (!quiet && result.stdout) process.stdout.write(result.stdout);
  if (result.status !== 0) {
    const detail = (result.stdout || result.stderr || '').trim();
    throw new Error(`process registry failed (${result.status}): ${detail}`);
  }
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
  processRegistry(python, ['assert-clear'], true);

  // ── Backend ────────────────────────────────────────────────────────────────
  const backend = spawnProc(
    python,
    ['-m', 'uvicorn', 'api.main:app',
      '--host', '0.0.0.0',
      '--port', String(backendPort),
      '--reload'],
    { cwd: ROOT },
  );
  try {
    processRegistry(
      python,
      ['register-one', '--role', 'backend', '--pid', String(backend.pid), '--port', String(backendPort)],
      true,
    );
  } catch (error) {
    backend.kill('SIGTERM');
    throw error;
  }

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
  try {
    processRegistry(
      python,
      [
        'register-one',
        '--role', 'frontend',
        '--pid', String(frontend.pid),
        '--port', String(frontendPort),
      ],
      true,
    );
  } catch (error) {
    try {
      processRegistry(python, ['stop'], true);
    } catch {
      backend.kill('SIGTERM');
      frontend.kill('SIGTERM');
    }
    throw error;
  }

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
    const url = `http://localhost:${frontendPort}`;
    const openCmd = IS_WIN ? 'powershell.exe' : (process.platform === 'darwin' ? 'open' : 'xdg-open');
    const openArgs = IS_WIN ? ['-NoProfile', '-Command', `Start-Process -FilePath '${url}'`] : [url];
    spawn(openCmd, openArgs, { detached: true, stdio: 'ignore' }).unref();
  } else {
    console.error('\n  ✗ One or more services failed to start. Check logs above.');
  }

  // ── Cleanup on Ctrl+C ─────────────────────────────────────────────────────
  function shutdown() {
    console.log('\n[dev] Shutting down...');
    try {
      processRegistry(python, ['stop'], true);
    } catch (error) {
      console.error(`[dev] ${error.message}`);
    }
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
