# Entorno de demostración restaurable

Permite ejecutar la demostración tantas veces como haga falta y volver siempre al
mismo punto de partida: el dataset canónico (1180 lecturas, 1180 hechos) más N
episodios de demostración, sin lecturas, auditoría ni idempotencia residuales.
Todos los datos son simulados y completamente ficticios.

## Piezas

| Script | Qué hace |
|---|---|
| `scripts/provisionar_demo.py provisionar --gestantes N` | N episodios de demostración (1–8), cada uno con su cuenta, embarazo y dispositivo. El primero es el de siempre (`DEMO-SCRUM72-01`, también en `provision.json`); los demás (`DEMO-GESTANTE-02`…) se crean sobre pacientes cuyos embarazos están todos cerrados. Es idempotente. |
| `scripts/dispositivo_gestante.py capturar --dispositivo CODIGO` | Captura sin red con el aprovisionamiento de ese dispositivo (`data/gestante/dispositivos/CODIGO.json`). |
| `scripts/escenario_demo.py ejecutar ESCENARIO.json` | Captura, inicia sesión con la cuenta de cada gestante, sincroniza y verifica la llegada única. `verificar REGISTRO.json`, después del ETL, comprueba también los hechos. |
| `scripts/entorno_demo.py respaldar \| verificar \| restaurar --carpeta X` | Respaldo de la base y de `data/gestante`, comparación con el respaldo y restauración. |

Un escenario es un JSON con las gestantes por su dispositivo y cuántas sesiones
captura cada una. Cada sesión lleva una lectura; con `"lecturas": N` (opcional,
1–10; SCRUM-109), las sesiones de signos maternos llevan N lecturas conjuntas de
FC y SpO₂ y las de movimientos siguen con un solo conteo (lo mismo que
`dispositivo_gestante.py capturar --tipo SIGNOS_MATERNOS --lecturas N`):

```json
{"nombre": "C-varias-gestantes", "concurrente": true,
 "gestantes": [
   {"dispositivo": "DEMO-SCRUM72-01", "sesiones": 2, "tipo": "SIGNOS_MATERNOS"},
   {"dispositivo": "DEMO-GESTANTE-03", "sesiones": 3, "tipo": ["MOVIMIENTOS_FETALES", "SIGNOS_MATERNOS"]}]}
```

## Qué garantiza la restauración

- **Comprueba antes de cambiar.** El dump se restaura en `<base>__restaurando` y
  se compara con el manifiesto. Solo si coincide se intercambia con la activa.
- **No borra.** La base anterior queda como `<base>__previa_<marca>`, sin conexiones
  permitidas. Los archivos locales anteriores quedan en `data/gestante__previa_<marca>`.
- **Comprueba después.** Tablas fila por fila, secuencias, políticas y estado de
  RLS, vistas, funciones, permisos, roles y membresías, más las SQLite por
  contenido. Cualquier diferencia se informa con código de salida 3.
- **Se niega** si hay sesiones abiertas en la base (API, Adminer, Power BI), si el
  portal está en marcha, si el respaldo está alterado (`SHA256SUMS`) o si el
  respaldo es de otra base y no se declaró con `--respaldo-de`.
- No guarda contraseñas: los roles se respaldan con `--no-role-passwords`.

La huella se calcula con `psql` dentro del contenedor, como superusuario y en solo
lectura. El superusuario no está sujeto a RLS, así que ve todas las filas de las
tablas con FORCE ROW LEVEL SECURITY. Las filas se ordenan con `COLLATE "C"`.

## Secuencia del día D

1. Demo Control, opción 3 (detener la API). Cerrar Adminer, Power BI Desktop y el portal.
2. Poner en uso la base limpia:

   ```powershell
   python scripts/entorno_demo.py restaurar --carpeta C:\FetalAlert_respaldos\<baseline> `
       --respaldo-de fetalalert_demo_baseline
   ```

3. Demo Control, opción 4 (iniciar la API) y opción 1 (verificar). Arrancar el portal.
4. Ejecutar la demostración (escenarios A, B o C, o el portal), y después el ETL (opciones 5 y 6).
5. Para repetirla, volver al paso 1.

El nombre de la base activa no cambia, así que la API, Demo Control y Power BI
no necesitan otra configuración. Con la opción `--respaldo-de` se aplica a la
base activa el respaldo de la base limpia construida aparte.

## Escenarios de la sustentación

Están en `scripts/escenarios_demo/`. Se ejecutan con
`python scripts/escenario_demo.py ejecutar scripts/escenarios_demo/A.json`, y
después del ETL se comprueban con `verificar <registro>`.

| Escenario | Gestantes | Sesiones | Embarazos |
|---|---|---|---|
| A | `DEMO-SCRUM72-01` | 1 | 130 |
| B | `DEMO-GESTANTE-02` | 4, alternando signos maternos y movimientos fetales | 131 |
| C | `DEMO-SCRUM72-01`, `-03` y `-04`, a la vez | 2, 3 y 5 | 130, 132 y 133 |

En Power BI, con «Ver como rol Medico», `medico01@example.com` ve los embarazos
130 a 132 y `medico03@example.com` ve el 133. En `v_resumen_administrativo` las
lecturas de la demo **no aparecen**, y es lo esperado. Esa vista suprime toda
celda (provincia × mes × semáforo) con menos de 5 embarazos distintos, y ninguna
provincia reúne 5 episodios de demostración.

## Equipo de respaldo (contingencia)

Un segundo equipo puede ejecutar una demostración equivalente sin depender del
principal. Tiene **su propia** instancia de PostgreSQL, su propia API y su propia
conexión de Power BI, y no hay sincronización entre los dos equipos. Lo único
que se comparte son el código y el dataset simulado.

### Requisitos

- Windows con Docker Desktop, Git, Python 3.12 y Power BI Desktop.
- El repositorio en la misma rama o el mismo commit que el equipo principal.
- Su **propio** `.env`, creado desde `.env.example`, con secretos propios
  (`POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, claves de `fetalalert_api`,
  `fetalalert_etl` y `fetalalert_powerbi`). Nunca se copia el `.env` de otro
  equipo.
- Un `POSTGRES_PORT` libre. Un PostgreSQL nativo suele ocupar el 5432 o el 5433.
- **El mismo dataset**: copiar `data/generated/` del equipo principal, que es
  simulado y no contiene contraseñas en claro, y comprobar su huella:
  `dataset_fetalalert.json` debe dar
  `9085651a4b6c0fe850c69ae802d82636e79a0b67bfd6a0026f769c7f99b08823` (SHA-256).
  Regenerarlo con el generador da los mismos datos clínicos, pero otros
  `password_hash`.

### Preparación

Se construye desde cero en el propio equipo; no se trasladan respaldos entre
equipos.

1. `docker compose up -d db` y esperar a que el contenedor esté `healthy`.
2. Crear como superusuario los roles con LOGIN (`fetalalert_api`, `fetalalert_etl`
   y `fetalalert_powerbi`), sin SUPERUSER ni BYPASSRLS y con las claves del
   `.env` local.
3. `python scripts/bootstrap_roles.py aplicar`, después
   `cd backend; alembic upgrade head; cd ..` y por último
   `python scripts/load_mock_data.py`. Debe informar 1180 lecturas.
4. `python scripts/provisionar_demo.py provisionar --gestantes 4`.
5. `docker compose up -d api` y `python scripts/etl_analitico.py ejecutar`.
6. Demo Control, opción 1: `LISTO PARA DEMO`.
7. Con la API detenida: `python scripts/entorno_demo.py respaldar --carpeta <respaldo>`.
   Ese respaldo es la base limpia de **ese** equipo.
8. Power BI Desktop: conectar a `127.0.0.1:<POSTGRES_PORT>`, a la base del `.env`,
   con `fetalalert_powerbi` y sin cifrado (el servidor tiene `ssl=off`).
   Importar las vistas de `publicacion` (ver `docs/proteccion_analitica.md` §8).

### Validación antes de darlo por listo

Un recorrido completo:

1. Escenarios A, B y C.
2. Previsualización y ETL.
3. `verificar` de los tres registros.
4. Actualizar Power BI.
5. `entorno_demo.py restaurar --carpeta <respaldo>` (IDÉNTICO).
6. Repetir un escenario.

Hasta completar ese recorrido, el equipo de respaldo no se considera preparado.

## Validación

- `backend/tests/test_entorno_demo.py`, `test_escenario_demo.py` y la sección H de
  `test_gestante_dispositivo.py`, sin servidor.
- `backend/tests/test_entorno_demo_postgresql.py`, contra PostgreSQL real y su
  contenedor (`ENTORNO_DEMO_TEST_DATABASE_URL`, `ENTORNO_DEMO_TEST_CONTENEDOR`,
  `ENTORNO_DEMO_TEST_ADMIN`). Tiene su propio step en CI: varias gestantes, cero
  filas canónicas cambiadas, ciclo respaldar → alterar → restaurar sin residuos
  y RLS vigente tras restaurar.
