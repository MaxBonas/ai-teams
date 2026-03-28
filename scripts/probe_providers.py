"""
Probe rápido de todos los providers configurados.
Uso: venv/Scripts/python.exe scripts/probe_providers.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Añadir raíz al path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Cargar .env (forzado, sobreescribe para garantizar lectura correcta)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key and val:
            os.environ[key] = val

from aiteam.adapters.subscription import SubscriptionAdapter  # noqa: E402

PROMPT = "Responde en una frase: ¿cuál es la capital de Francia?"

# (nombre, provider, modelo)
CASES = [
    ("anthropic / claude-sonnet-4-6",    "anthropic", "claude-sonnet-4-6"),
    ("anthropic / claude-haiku-4-5",     "anthropic", "claude-haiku-4-5-20251001"),
    ("google    / gemini-2.0-flash",     "google",    "gemini-2.0-flash"),
    ("google    / gemini-1.5-pro",       "google",    "gemini-1.5-pro"),
    ("openai    / gpt-4.1",              "openai",    "gpt-4.1"),
    ("openai    / gpt-4.1-mini",         "openai",    "gpt-4.1-mini"),
    ("groq      / llama-4-scout",        "groq",      "meta-llama/llama-4-scout-17b-16e-instruct"),
    ("groq      / llama-3.3-70b",        "groq",      "llama-3.3-70b-versatile"),
]

KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "google":    "GOOGLE_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "groq":      "GROQ_API_KEY",
}

W = 38
print(f"\n{'Provider / Modelo':<{W}} {'Status':<8} {'ms':>6}  Respuesta / Error")
print("─" * 110)

ok_count = total = 0
for label, provider, model in CASES:
    key_env = KEY_MAP.get(provider, "")
    key_val = os.getenv(key_env, "")
    if not key_val:
        print(f"{label:<{W}} {'NO KEY':<8} {'—':>6}  ({key_env} no configurada)")
        continue

    total += 1
    adapter = SubscriptionAdapter(f"probe_{provider}", provider, model)
    start = time.time()
    try:
        resp = adapter.invoke(PROMPT)
        ms = int((time.time() - start) * 1000)
        if resp.success:
            preview = resp.content.strip().replace("\n", " ")[:70]
            print(f"{label:<{W}} {'OK':<8} {ms:>6}  {preview}")
            ok_count += 1
        else:
            err = str(resp.error or "")[:70]
            print(f"{label:<{W}} {'FAIL':<8} {ms:>6}  {err}")
    except Exception as exc:
        ms = int((time.time() - start) * 1000)
        print(f"{label:<{W}} {'ERROR':<8} {ms:>6}  {str(exc)[:70]}")

print(f"\n{'─'*110}")
print(f"Resultado: {ok_count}/{total} providers OK\n")
