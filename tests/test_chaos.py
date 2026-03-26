import pytest
import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from aiteam.observability import EventLogger
from aiteam.autotools import AutoToolIntegrator
from aiteam.persistence import AtomicFileWriter

def test_ledger_corruption_recovery(tmp_path: Path):
    """Prueba que el EventLogger y los parsers pueden sobrevivir a corrupciones crasas en JSONL."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True)
    events_path = runtime_dir / "events.jsonl"
    
    # 1. Evento valido
    events_path.write_text('{"event_type": "boot", "payload": {"foo": "bar"}}\n', encoding="utf-8")
    
    # 2. Evento corrupto a la mitad
    with open(events_path, "a", encoding="utf-8") as f:
        f.write('{"event_type": "corrupted", "payload": { \n')
        
    # 3. Evento no-json
    with open(events_path, "a", encoding="utf-8") as f:
        f.write('esto no es json\n')
        
    # 4. Evento valido post-corrupcion
    with open(events_path, "a", encoding="utf-8") as f:
        f.write('{"event_type": "shutdown", "payload": {}}\n')

    logger = EventLogger(runtime_dir)
    records = logger._records()
    
    # Debe ignorar la corrupcion y parsear los validos individualmente
    assert len(records) == 2
    assert records[0]["event_type"] == "boot"
    assert records[1]["event_type"] == "shutdown"

def test_tool_acquisition_network_chaos(tmp_path: Path):
    """Inyecta fallos de socket/timeout en adquisiciones para validar backoff."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    integrator = AutoToolIntegrator(
        runtime_dir=runtime_dir,
        project_root=tmp_path
    )
    
    with patch("subprocess.run") as mock_run:
        # 1 y 2 fallan con OSError, 3ro exitoso
        mock_run.side_effect = [
            OSError("Network Socket Chaos 1"),
            subprocess.TimeoutExpired(cmd="fake", timeout=1),
            MagicMock(returncode=0, stdout="success", stderr="")
        ]
        
        with patch("time.sleep") as mock_sleep:
            ok, msg = integrator._run_command(["fake-npm", "install", "foo"], timeout=1)
            
            # Debe sobrevivir y lograr "acquire_ok" tras reintentos
            assert ok is True
            assert msg == "acquire_ok"
            assert mock_run.call_count == 3
            assert mock_sleep.call_count == 2
            
def test_tool_acquisition_permanent_chaos(tmp_path: Path):
    """Inyecta fallo permanente para asegurar que no se reintente sin fin."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    integrator = AutoToolIntegrator(
        runtime_dir=runtime_dir,
        project_root=tmp_path
    )
    
    with patch("subprocess.run") as mock_run:
        # Fallo constante en todos los reintentos (defecto: 3)
        mock_run.side_effect = OSError("Chaos Constant")
        
        with patch("time.sleep") as mock_sleep:
            ok, msg = integrator._run_command(["fake-npm", "install", "foo"], timeout=1)
            assert ok is False
            assert "acquire_failed" in msg
            assert "Chaos Constant" in msg
            assert mock_run.call_count == 3
            assert mock_sleep.call_count == 2
