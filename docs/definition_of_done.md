# Definition of Done (DoD)

Un ticket de FetalAlert se considera **terminado** solo cuando se cumplen todos
los puntos de esta lista. Aplica a cualquier ticket del proyecto: código,
migraciones, datos simulados o documentación.

## Lista de verificación

- [ ] **Criterios de aceptación cumplidos.** Todo lo que pide el ticket está
      implementado, y nada fuera de su alcance se coló en el cambio.
- [ ] **Pruebas locales aprobadas.** La suite corre en verde en la computadora
      de quien desarrolla, antes de abrir el Pull Request, desde `backend/`:
      `python -m pytest -q --ignore=tests/test_migration_postgresql.py --ignore=tests/test_load_mock_data_postgresql.py --ignore=tests/test_ingestion_api_postgresql.py --ignore=tests/test_ingestion_idempotency_postgresql.py`.
- [ ] **CI aprobado.** El workflow `CI` (`.github/workflows/ci.yml`) termina en
      verde para el Pull Request. Un job en rojo bloquea el cierre del ticket.
- [ ] **Pruebas de PostgreSQL ejecutadas y no omitidas.** Los cuatro archivos que
      necesitan un servidor real deben ejecutarse de verdad y no aparecer como
      *skipped*:
      `tests/test_migration_postgresql.py`, con `SCRUM52_TEST_DATABASE_URL`
      apuntando a la base dedicada `scrum52_validacion_tmp`, cubriendo el ciclo
      completo `upgrade -> downgrade -> upgrade -> alembic check` sobre esa
      base: confirma que la migración puede revertirse y volver a aplicarse, y
      que al final no quedan divergencias pendientes entre los modelos y el
      esquema; `tests/test_load_mock_data_postgresql.py`, con
      `SCRUM61_TEST_DATABASE_URL` apuntando a una base ya desplegada en el
      `head` de Alembic; `tests/test_ingestion_api_postgresql.py`, con
      `SCRUM62_TEST_DATABASE_URL` apuntando también a una base en `head`, que
      ejercita el endpoint de ingesta dentro de transacciones que siempre se
      revierten; y `tests/test_ingestion_idempotency_postgresql.py`, con
      `SCRUM63_TEST_DATABASE_URL`, que valida la idempotencia de reenvíos e
      incluye pruebas **concurrentes** con conexiones independientes. Estas
      últimas confirman sus filas de referencia y luego borran exactamente lo
      que crearon, así que la base debe quedar sin filas residuales al terminar.
      El workflow `CI` lo verifica sobre el reporte JUnit de cada ejecución y
      falla el job si al menos una prueba de PostgreSQL queda omitida.
- [ ] **Pull Request vinculado al ticket de Jira y aprobado.** El PR referencia
      su ticket (por ejemplo, `SCRUM-60`) y cuenta con la aprobación de la otra
      autora.
- [ ] **Comentarios de revisión resueltos.** Cada observación de la revisión fue
      atendida o respondida explícitamente; no quedan hilos abiertos.
- [ ] **Sin secretos ni datos reales.** El cambio no introduce contraseñas,
      tokens, claves ni credenciales reales, ni datos clínicos reales, en
      código, pruebas, documentación, archivos de configuración versionados ni
      automatizaciones. Las credenciales de CI son ficticias y efímeras.
- [ ] **Documentación actualizada cuando aplica.** Si el cambio modifica el
      comportamiento, la configuración o la forma de ejecutar el proyecto, el
      README y los documentos de `docs/` lo reflejan.
- [ ] **Integrado en `main`.** El trabajo quedó incorporado a la rama de
      integración final del ticket.

## Estado verificado localmente de SCRUM-63

Esta sección registra **lo que ya se comprobó en la computadora de desarrollo**,
como evidencia local previa al Pull Request. **No sustituye la auditoría final,
la ejecución remota de CI ni la revisión de la otra autora**, ni tampoco a la
lista de arriba: las verificaciones que dependen de GitHub y de la revisión se
enumeran al final y se comprueban allí, no en este documento.

### Comprobado

- **Migración.** Cadena de dos revisiones, `150788f88be7 -> 87d8ed46686b`. Ciclo
  real contra PostgreSQL 16 sobre la base dedicada `scrum52_validacion_tmp`:
  `upgrade`, `downgrade` de la revisión nueva, `upgrade` otra vez, y
  `alembic check` **sin divergencias**. La base queda desplegada en el `head`.
- **Conteos de pruebas, reproduciendo los comandos exactos del CI:**

  | Bloque | Resultado |
  | --- | --- |
  | Offline (sin servidor PostgreSQL) | 594 passed, 0 skipped |
  | Migraciones — SCRUM-52 | 79 passed |
  | Cargador — SCRUM-61 | 25 passed |
  | Endpoint — SCRUM-62 | 46 passed |
  | Idempotencia — SCRUM-63 | 64 passed |
  | **Total recolectado** | **808** (`pytest --collect-only -q`) |

- **Cero pruebas de PostgreSQL omitidas.** El guardián JUnit del workflow,
  ejecutado sobre los cuatro reportes de la reproducción local, terminó con
  código 0. Se comprobó además con un control negativo: inyectar un `<skipped>`
  en el reporte nuevo lo hace fallar con código 1.
- **Concurrencia real ejecutada, no omitida.** Las 14 pruebas concurrentes
  figuran como ejecutadas en `pytest-scrum63.xml`, sin `skipped` ni `failure`.
  Se repitieron 12 veces consecutivas sin un solo fallo.
- **Base sin filas residuales** tras la secuencia completa de los cinco bloques.
- **Documentación actualizada:** README (contrato de `Idempotency-Key`),
  decision log (decisiones de SCRUM-63) y esta lista.

### Verificaciones externas requeridas para cerrar SCRUM-63

El estado de estas verificaciones cambia fuera del contenido versionado y debe
comprobarse directamente en GitHub y Jira antes de cerrar el ticket:

- los commits de SCRUM-63 fueron publicados en su rama;
- GitHub Actions terminó en verde para el Pull Request;
- la otra autora revisó y aprobó el Pull Request;
- el Pull Request fue integrado en `main`;
- el CI posterior al merge terminó en verde.

## Nota sobre los datos

FetalAlert trabaja **exclusivamente con datos simulados y sintéticos,
completamente ficticios**. No deben incluirse datos clínicos reales de pacientes
—ni siquiera parcialmente o de forma anonimizada— en el código, las pruebas, los
datasets de ejemplo, la documentación ni las automatizaciones del repositorio.
