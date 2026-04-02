## Resumen

Describe brevemente el cambio.

## Tipo de cambio

- [ ] Fix
- [ ] Feature
- [ ] Refactor
- [ ] Documentacion
- [ ] Infra/entorno

## Validacion

- [ ] Ejecute `.\scripts\prepare_dev_env.bat` en esta maquina
- [ ] Verifique que el cambio no depende de `runtime/` compartido
- [ ] Verifique que el cambio compartible vive en codigo o `config/*.example.json`
- [ ] Ejecute smoke tests relevantes con `.\scripts\pytest_local.bat`

Comandos ejecutados:

```powershell
# pega aqui los comandos reales
```

## Riesgos

- [ ] Toca persistencia SQLite
- [ ] Toca `api/main.py`
- [ ] Toca flujo entre maquinas
- [ ] Toca runtime/config local

## Checklist entre maquinas

- [ ] Este cambio deberia sobrevivir a `git pull` en `MAX-GAMINGPC`
- [ ] Este cambio deberia sobrevivir a `git pull` en `ORCH-01`
- [ ] No requiere commitear `venv/`, `runtime/` ni `node_modules/`
- [ ] Si requiere config compartida, la deje en `config/*.example.json`
