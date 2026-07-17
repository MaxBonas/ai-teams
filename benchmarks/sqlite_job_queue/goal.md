# Benchmark: cola durable de trabajos SQLite

Construye en Python 3.12, usando solo la librería estándar, una cola de trabajos
durable y segura ante varios workers.

## API requerida

En `jobqueue.py` debe existir:

```python
class SQLiteJobQueue:
    def __init__(self, db_path: str | Path): ...
    def enqueue(self, payload: dict, *, max_attempts: int = 3) -> str: ...
    def claim(self, worker_id: str, *, lease_seconds: int = 30, now: float | None = None) -> dict | None: ...
    def ack(self, job_id: str, worker_id: str) -> bool: ...
    def fail(self, job_id: str, worker_id: str, error: str, *, retry_delay: int = 0, now: float | None = None) -> bool: ...
    def stats(self) -> dict[str, int]: ...
```

Contrato:

- `enqueue` persiste JSON y devuelve un ID único.
- `claim` reclama atómicamente el trabajo disponible más antiguo.
- Dos workers concurrentes nunca reciben el mismo trabajo.
- Un lease expirado permite reclamar el trabajo de nuevo.
- Cada claim incrementa `attempts`.
- `ack` y `fail` solo pueden actuar si `worker_id` posee el lease activo.
- `fail` devuelve el trabajo a `pending` mientras queden intentos; al alcanzar
  `max_attempts` pasa a `dead`.
- `retry_delay` impide reclamar antes de `now + retry_delay`.
- `stats` devuelve exactamente las claves `pending`, `running`, `done`, `dead`.
- Payloads no serializables y `max_attempts < 1` deben lanzar `ValueError`.

Los diccionarios devueltos por `claim` deben incluir al menos `id`, `payload`,
`attempts`, `max_attempts`, `worker_id` y `lease_until`.

## CLI

En `queue_cli.py`:

- `python queue_cli.py --db <ruta> enqueue '<json>'` imprime solo el ID.
- `python queue_cli.py --db <ruta> stats` imprime un objeto JSON con los cuatro estados.
- JSON inválido termina con exit code 2 y mensaje por stderr.

## Entregables

1. `jobqueue.py`.
2. `queue_cli.py`.
3. Tests pytest propios con concurrencia y reintentos.
4. `README.md` con decisiones de atomicidad y ejemplos.

No uses dependencias externas en la implementación.
