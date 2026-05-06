# /fix-mcp — Diagnóstico ordenado de MCP servers

Cuando un MCP server falla, seguir ESTE ORDEN antes de tocar nada.

## Paso 1 — Variables de entorno (causa más común)
Codex Desktop en Windows NO hereda el PATH del usuario por defecto.
```bash
# Verificar qué env vars tiene el proceso
echo $PATH
echo $PYTHONPATH
# Si no aparecen las rutas del venv → el problema es herencia de env vars
```

## Paso 2 — Integridad del venv
```bash
# ¿Existe y es local (no sincronizado por Syncthing)?
ls venv/Scripts/python.exe
# ¿Los paths internos apuntan a esta máquina?
cat venv/pyvenv.cfg
# Si home= apunta a otra máquina → recrear
py -3.12 -m venv venv --clear
venv/Scripts/pip install -r requirements.txt
```

## Paso 3 — Paths con espacios
```bash
# Siempre usar comillas en paths con espacios
# Correcto: "/c/Users/tuusuario/Mis Proyectos/..."
# Incorrecto: /c/Users/tuusuario/Mis Proyectos/...
```

## Paso 4 — Test directo del comando MCP
```bash
# Ejecutar el comando MCP directamente en terminal, no desde Codex Desktop
venv/Scripts/python.exe -m <nombre_servidor_mcp>
```

## Paso 5 — Solo después de 1-4: DLL/pywin32
Si los pasos 1-4 están OK y sigue fallando, investigar dependencias del sistema.

## Reglas
- NUNCA saltarse el orden
- NUNCA aplicar más de un fix a la vez
- SIEMPRE verificar que el fix funcionó antes de pasar al siguiente paso
