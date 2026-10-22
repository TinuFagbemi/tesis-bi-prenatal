# Escenario de demostración de SCRUM-109

Un grupo de pacientes del escenario, con historias fijas y reproducibles, para
mostrar FetalAlert de forma coherente en la sustentación (20 a 23 de noviembre de
2026). Todos los datos son **simulados y completamente ficticios**; las
frecuencias de registro son un diseño de datos de prueba, no una recomendación
clínica. No hay hardware: el «dispositivo» es el dispositivo simulado de
siempre (registros de `dispositivo` y archivos de aprovisionamiento) y las
capturas en vivo las produce el simulador existente.

## Tres conjuntos de datos que no se mezclan

| | Qué es | Dónde vive | Cifras |
|---|---|---|---|
| **Dataset canónico** | La muestra aprobada de la tesis. | `data/generated/` → `load_mock_data.py` | 30 pacientes, 732 sesiones, 1.180 lecturas. **No cambia.** |
| **Precarga del escenario** | La historia del grupo de pacientes del escenario hasta el corte. | `scripts/escenario_scrum109_definicion.py` → `scripts/cargar_escenario_scrum109.py` | Propias (226 sesiones/lecturas con corte 2026-10-22); se informan en el manifiesto. |
| **Medición en vivo** | Lo que se captura durante la demostración. | `dispositivo_gestante.py`, portal, `escenario_demo.py` → API | Las que se hagan; id ≥ 11.000.000. |

La precarga **no inventa trazabilidad de ingesta**: sus lecturas tienen
`fecha_hora_sincronizacion` en NULL —igual que las que llegan por la API, porque
el paquete viaja con ese campo vacío—, no crea filas en
`idempotencia_solicitud` ni en `auditoria_log`, y sus sesiones son
`COMPLETADA`/`DISPOSITIVO`, como las canónicas. La fecha de **captura** es la
de la historia; la de **carga** no se guarda en ninguna columna (el esquema no
la tiene) y queda en el historial del manifiesto.

## El grupo de pacientes del escenario

Todas en la clínica 100 (Chiriquí) y seguidas por `medico01@example.com`
(id_medico 100, PRINCIPAL), el médico autorizado de la demo. Correos
`escenario109.<nombre>@example.com`; cédulas `4-109-000N`; la contraseña es la
contraseña simulada del dataset (`PASSWORD_SIMULADA` del generador; no se
repite aquí).

| id_paciente | Perfil | Embarazo (id) | Dispositivo | Qué permite demostrar |
|---|---|---|---|---|
| 10901 | **Protagonista** (Elena) | 10901 FINALIZADO, 2025-01-13 → cierre 2025-10-13 | `DEMO-SCRUM109-01` (DISPONIBLE, asignación cerrada) | Historial de un embarazo anterior (≈9 meses de seguimiento, WARNING aislados en julio de 2025). |
| | | 10902 ACTIVO, 2026-04-27 (semana 30–31 en la sustentación) | **`DEMO-SCRUM72-01`** (el principal, una sola asignación) | Embarazo actual, Demo Control, captura en vivo, descarga del resumen; WARNING histórico en julio de 2026 seguido de OK. |
| 10902 | Apoyo (Marta) | 10903 ACTIVO, 2026-03-02 | `DEMO-SCRUM109-03` | Solo lecturas OK. |
| 10903 | Apoyo (Rosa) | 10904 ACTIVO, 2026-04-13 | `DEMO-SCRUM109-04` | **WARNING recientes**: desde el 2026-10-05 todas sus lecturas son WARNING. |
| 10904 | Apoyo (Inés) | 10905 ACTIVO, 2026-03-23 | `DEMO-SCRUM109-05` | **WARNING históricos seguidos de OK** (2026-08-17 a 2026-09-06). |
| 10905 | Apoyo (Clara) | 10906 ACTIVO, 2026-06-01 | `DEMO-SCRUM109-06` | Embarazo temprano: movimientos solo desde la semana 20 (2026-10-14). |
| 10906 | Paula, **sin cuenta** | — | — | Perfil preparado para SCRUM-110 (habilitar una cuenta). Sin embarazo ni mediciones. |

**Frecuencias.** Signos maternos (FC y SpO₂) una vez por semana desde la semana
4, a las 09:30 de Panamá; movimientos fetales una vez por semana desde la
semana 20, a las 16:00. Antes de la semana 20 **no existe** ninguna sesión de
movimiento: la ausencia significa «no registrado» y nunca se escribe un cero.
Una lectura por sesión, como el simulador.

**Semáforos.** Los decide `app.etl.reglas.clasificar_lectura` (SIM-1.0), la
misma regla que el ETL usa para conciliar; el escenario solo elige valores. No
hay ningún ERROR ni ninguna categoría nueva.

**Identificadores.** Bloques reservados: 10901–10999 para pacientes, cuentas,
teléfonos, dispositivos, embarazos, seguimientos y asignaciones;
10.900.001–10.999.999 para sesiones y lecturas (fijos para toda la historia,
también para lo posterior al corte). Tras la carga, las secuencias de esas
tablas quedan detrás del bloque (11000 y 11.000.000): la API y el
aprovisionamiento nunca toman un identificador reservado.

## Preparar un entorno aislado

El entorno de SCRUM-109 se probó en un worktree propio
(`C:\GitHub\TESIS\tesis-bi-prenatal-scrum109`). Compose toma el nombre del
proyecto del directorio, así que contenedores (`tesis-bi-prenatal-scrum109-*`) y
volumen (`tesis-bi-prenatal-scrum109_fetalalert_pgdata`) no son los del entorno
habitual. Demo Control y `entorno_demo.py` se fijan a ese mismo directorio.

1. `.env` propio del worktree (no versionado), con secretos propios y puertos
   que no choquen con el entorno habitual: `POSTGRES_PORT=5443`,
   `GESTANTE_API_BASE_URL=http://127.0.0.1:8019`, `GESTANTE_PORT=8119`, y las
   tres URL (`DATABASE_URL` a `db:5432`, `ALEMBIC_DATABASE_URL` y
   `ETL_DATABASE_URL` a `127.0.0.1:5443`) sobre la misma base.
2. `docker-compose.override.yml` local (no versionado) para publicar la API en
   otro puerto:

   ```yaml
   services:
     api:
       ports: !override
         - "8019:8000"
   ```

3. Base: `docker compose up -d db`; crear como superusuario los roles con
   LOGIN `fetalalert_api`, `fetalalert_etl` y `fetalalert_powerbi`;
   `python scripts/bootstrap_roles.py aplicar`; `cd backend; alembic upgrade
   head; cd ..`; `python scripts/generate_mock_data.py`;
   `python scripts/load_mock_data.py` (debe informar 1180 lecturas).

En Windows, `generate_mock_data.py` escribe todos sus archivos y después puede
fallar al **imprimir** el resumen (`UnicodeEncodeError` por «SpO₂» en una
consola cp1252). Es previo a este ticket; se evita con
`$env:PYTHONIOENCODING = "utf-8"`.

## Construir la base de demostración del escenario

**Antes de ejecutar `provisionar_demo.py`.** El aprovisionamiento antiguo
asignaría `DEMO-SCRUM72-01` a una paciente canónica; si ya ocurrió, el
cargador se niega («El dispositivo principal de la demo ya existe…») y hay que
partir de una base canónica limpia.

```powershell
$env:PYTHONIOENCODING = "utf-8"
python scripts/cargar_escenario_scrum109.py cargar --ensayo   # todo, y revierte
python scripts/cargar_escenario_scrum109.py cargar            # corte = ahora
python scripts/etl_analitico.py ejecutar                      # o Demo Control 5 y 6
python scripts/cargar_escenario_scrum109.py verificar         # solo lectura
```

`cargar` hace todo en **una transacción** (con
`app.loader.postgres.cargar_dataset`, el motor idempotente del cargador
canónico, sin su validación de metadatos 732/1.180): inserta lo que falta,
conserva lo idéntico y aborta ante cualquier conflicto. Después escribe, bajo
la carpeta de `GESTANTE_PROVISION_PATH`:

- `provision.json` de la protagonista, con el formato de siempre, a partir de
  `localizar_episodio_demo` (la misma lectura estricta de Demo Control);
- `dispositivos/DEMO-SCRUM72-01.json` y `DEMO-SCRUM109-03…06.json`, para
  `dispositivo_gestante.py --dispositivo` y `escenario_demo.py`;
- `escenario_scrum109/manifiesto.json`: versión, corte efectivo, base, revisión
  de Alembic, referencias técnicas (ids, códigos, bloques), cantidades
  esperadas/presentes/insertadas/existentes, observaciones por embarazo y
  semáforo, secuencias e historial de cargas. Sin correos, nombres, cédulas,
  claves ni hashes.

`python scripts/provisionar_demo.py provisionar` (sin `--gestantes`) también
funciona después: reconoce el episodio por `DEMO-SCRUM72-01` y no crea filas.
No uses `--gestantes N` en esta base: crearía `DEMO-GESTANTE-NN` sobre
pacientes canónicas que el escenario no necesita.

### Conflictos que detiene, sin sobrescribir

- una fila con la misma llave y otro contenido (`ConflictoDeDatos`);
- una fila ajena dentro de un bloque reservado;
- un correo, cédula o código de dispositivo del escenario usado por otra fila;
- asignaciones o seguimientos ajenos sobre sus dispositivos o embarazos, u otro
  embarazo de una de sus pacientes;
- una cuenta cuyo hash ya no verifica la contraseña simulada;
- el médico de la demo sin una única afiliación que cubra todo el escenario
  (se comprueba con `reglas.verificar_afiliacion`, la del ETL).

## Elegir el corte

| `--corte` | Significado |
|---|---|
| (omitido) | El instante de la ejecución, en America/Panama. |
| `2026-11-19` | Hasta el final de ese día en Panamá (23:59:59,999999). |
| `2026-11-19T08:00` | Ese instante en Panamá. |
| `2026-11-19T13:00+00:00` | Ese instante, convertido a Panamá. |

Una observación entra si su `fecha_hora_captura` es anterior o igual al corte.
Reglas:

- **Nunca futuro.** Un corte posterior al momento real de la ejecución se
  rechaza; no se cambia la fecha del sistema ni se desplaza la historia.
- **No antes de la estructura.** El corte mínimo es 2026-06-01 (último inicio
  de embarazo del escenario).
- **Repetir** el mismo corte no inserta nada.
- **Avanzar** el corte inserta solo lo pendiente (p. ej., cargar el 19 de
  noviembre sobre una base preparada antes).
- **Retroceder** se rechaza si ya hay observaciones posteriores al nuevo corte:
  no se borra nada en silencio. Para volver atrás se **restaura** un respaldo
  tomado con ese corte (sección siguiente).

Conviene avanzar el corte **antes** de las capturas en vivo del día, no
después, para que la precarga no intercale lecturas entre las de la
demostración.

## Respaldar y restaurar

Con la API y el portal detenidos (Demo Control, opción 3), y tras ETL y
verificación:

```powershell
python scripts/entorno_demo.py respaldar --carpeta C:\FetalAlert_respaldos\SCRUM-109_base_inicial_<fecha>
python scripts/entorno_demo.py verificar --carpeta C:\FetalAlert_respaldos\SCRUM-109_base_inicial_<fecha>
# ... ensayo ...
python scripts/entorno_demo.py restaurar --carpeta C:\FetalAlert_respaldos\SCRUM-109_base_inicial_<fecha>
```

El respaldo incluye la base y `data/gestante` completa (aprovisionamiento,
archivos por dispositivo, manifiesto y colas). La restauración conserva el
estado del ensayo como `<base>__previa_<marca>` y
`data/gestante__previa_<marca>`, y vuelve al escenario inicial con su
precarga intacta. Después: arrancar API y portal, Demo Control opción 1 y, si se
valida Power BI, **actualizar el modelo** para no mostrar datos del ensayo
anterior.

Nota: `data/gestante__previa_<marca>/` no está en `.gitignore` y contiene
SQLite; `test_edge_cli.py::test_ninguna_base_sqlite_esta_versionada` falla
mientras exista dentro del repositorio. Muévelo fuera o bórralo tras revisarlo.

## Publicación médica y administrativa

- **Paciente** (portal y `/api/v1/clinico`): la protagonista ve sus dos
  embarazos, el actual y el anterior, cada uno con su historial.
- **Médico autorizado** (`medico01@example.com`): ve las cinco pacientes con
  cuenta y sus identidades (`v_paciente_medico`) y sus cinco embarazos **en
  curso**. **No** ve el embarazo anterior de la protagonista: la regla vigente
  (API y `v_entitlement_medico`) exige un seguimiento activo y vigente hoy, y el
  de un embarazo finalizado está cerrado. El escenario respeta esa regla.
- **Otro médico**: `medico02` (misma clínica, sin seguimiento) y `medico03`
  (otra clínica) no ven ninguna paciente del escenario (404 en la API).
- **Administración** (`v_resumen_administrativo`): sin identidades. Publica
  Chiriquí × {2026-06, 07, 08, 09} × OK con 5 embarazos cada celda; los WARNING
  del escenario (grupos de 1) quedan suprimidos por el mínimo de 5 y siguen
  disponibles para el médico. El embarazo anterior de la protagonista suma 1
  embarazo a celdas de Chiriquí de 2025 de esta base, sin cambiar qué celdas se
  publican; el dataset canónico de la tesis no se toca.

## Power BI: comprobaciones pendientes (manuales)

El repositorio no contiene el PBIX. El modelo revisado para nombrar tablas y
medidas es el **PBIP del 27/09/2026**
(`FetalAlert_PowerBI.SemanticModel`), que importa `publicacion v_embarazo`,
`publicacion v_lectura`, `publicacion v_entitlement_medico` y
`publicacion v_resumen_administrativo`, con el rol `Medico`
(`[upn_medico] = LOWER(USERPRINCIPALNAME())`). El archivo vigente puede ser
posterior: comprueba los nombres antes de aplicar nada. Ver el informe de
entrega para la lista y las expresiones DAX propuestas.

## Dependencias

Esta rama parte del PR #24 (`feature/entorno-demo-restaurable`, que ya contiene
el #23 de Demo Control) e integra el PR #22 (`feature/gestante-guardar-resumen-demo`,
descarga del resumen) con un merge sin conflictos. Mientras esos PR sigan
abiertos, el PR de SCRUM-109 mostrará también sus commits; debe fusionarse
**después** de ellos.

## Pruebas

- `backend/tests/test_escenario_scrum109.py`, sin servidor: reglas del dominio,
  semana 20, forma de las lecturas, coberturas, reglas de médico y afiliación del
  ETL, semáforos, celdas administrativas, columnas exactas del modelo y corte.
- `backend/tests/test_escenario_scrum109_postgresql.py`, con
  `SCRUM109_TEST_DATABASE_URL` hacia una base desechable `scrum109_*` migrada y
  con el dataset canónico: primera carga y reejecución, avance y retroceso del
  corte, ausencia de futuro, canónico intacto fila por fila, secuencias,
  integridad, semáforos, cuentas, rollback ante fallo, conflictos y `--ensayo`
  del CLI. Tiene su propio paso en CI.
