# Motor de autorización multi-tenant auditable

Construye una librería Python pequeña para decidir acceso a recursos de varios
tenants sin depender de frameworks externos.

Contrato público:

- paquete `tenant_auth` con `Policy`, `Decision` y `Authorizer` exportados;
- una política declara tenant, subject, action y resource pattern;
- `Authorizer.decide(...)` aplica deny-by-default y nunca permite que una
  política de otro tenant autorice el acceso;
- soporta comodín `*` por segmento en acciones y recursos, sin que cruce `/`;
- un deny coincidente prevalece sobre allows coincidentes;
- cada decisión incluye `allowed`, `reason` y los identificadores de políticas
  evaluadas, de forma determinista;
- API inmutable desde el punto de vista del llamador: decidir no modifica las
  políticas recibidas;
- incluye persistencia JSON round-trip con validación estricta y rechazo de
  campos desconocidos;
- incluye tests públicos y documentación de amenazas, invariantes y límites.

Prioriza aislamiento entre tenants, evidencia auditable y comportamiento seguro
ante entradas incompletas. No uses red ni una base de datos.
