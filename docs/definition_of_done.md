# Definition of Done (DoD)

Un ticket de FetalAlert se considera **terminado** solo cuando se cumplen todos
los puntos de esta lista. Aplica a cualquier ticket del proyecto: código,
migraciones, datos simulados o documentación.

## Lista de verificación

- [ ] **Criterios de aceptación cumplidos.** Todo lo que pide el ticket está
      implementado, y nada fuera de su alcance se coló en el cambio.
- [ ] **Pruebas locales aprobadas.** La suite corre en verde en la computadora
      de quien desarrolla, antes de abrir el Pull Request:
      `python -m pytest -q --ignore=tests/test_migration_postgresql.py`
      desde `backend/`.
- [ ] **CI aprobado.** El workflow `CI` (`.github/workflows/ci.yml`) termina en
      verde para el Pull Request. Un job en rojo bloquea el cierre del ticket.
- [ ] **Pruebas de PostgreSQL ejecutadas y no omitidas.**
      `tests/test_migration_postgresql.py` debe ejecutarse de verdad y no
      aparecer como *skipped*, con `SCRUM52_TEST_DATABASE_URL` apuntando a la
      base dedicada `scrum52_validacion_tmp`. El workflow `CI` lo verifica
      sobre el reporte JUnit de esa ejecución y falla el job si al menos una
      prueba de PostgreSQL queda omitida.
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

## Nota sobre los datos

FetalAlert trabaja **exclusivamente con datos simulados y sintéticos,
completamente ficticios**. No deben incluirse datos clínicos reales de pacientes
—ni siquiera parcialmente o de forma anonimizada— en el código, las pruebas, los
datasets de ejemplo, la documentación ni las automatizaciones del repositorio.
