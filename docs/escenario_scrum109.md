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
| **Precarga del escenario** | La historia del grupo de pacientes del escenario hasta el corte. | `scripts/escenario_scrum109_definicion.py` → `scripts/cargar_escenario_scrum109.py` | Propias (281 programadas hasta el 2026-11-30; 226 sesiones/lecturas con el corte 2026-10-23T21:12:57-05:00); se informan en el manifiesto. |
| **Medición en vivo** | Lo que se captura durante la demostración. | `dispositivo_gestante.py`, portal, `escenario_demo.py` → API | Las que se hagan; id ≥ 11.000.000. |

La precarga **no inventa trazabilidad de ingesta**: sus lecturas tienen
`fecha_hora_sincronizacion` en NULL —igual que las que llegan por la API, porque
el paquete viaja con ese campo vacío—, no crea filas en
`idempotencia_solicitud` ni en `auditoria_log`, y sus sesiones son
`COMPLETADA`/`DISPOSITIVO`, como las canónicas. La fecha de **captura** es la
de la historia; la de **carga** no se guarda en ninguna columna (el esquema no
la tiene) y queda en el historial del manifiesto.

## El grupo de pacientes del escenario

**Clínica de Darién y médica propia de la demo (SCRUM-109.4).** Los embarazos
y los dispositivos del escenario pertenecen a la clínica de Darién del dataset
canónico, que el cargador resuelve en la base: la definición solo declara la
provincia (`PROVINCIA_DE_LA_DEMO`) y el cargador exige **una** clínica en ella.
Los seis seguimientos PRINCIPAL son, desde el inicio de cada embarazo, de una
**médica sintética propia del escenario** (`MEDICO_DE_LA_DEMO`), de modo que
quien inicia sesión como ella ve **solo** a las cinco pacientes preparadas y
los médicos canónicos conservan únicamente las suyas. Su única afiliación es a
esa clínica, desde el 2025-01-01 y sin fin; la definición comprueba, día a día
y con las reglas del ETL (`medico_responsable` y `verificar_afiliacion`), que
cubre cada día con seguimiento u observación y que hay exactamente un
PRINCIPAL. El cargador ya no depende de que haya un único médico afiliado a la
clínica: otro médico afiliado a Darién no impide la carga.

| | Valor |
|---|---|
| Clínica | id_clinica 102, «Clínica Rural Simulada 03», Darién, distrito Chepigana, corregimiento Camogantí |
| Médica de la demo | id_medico 10950 (también su cuenta y su teléfono, en el bloque reservado), **Itzel Marina Barría Cortés**, Ginecología y Obstetricia, cel. 6731-5820, cuenta MEDICO y UPN `itzel.barria@example.com`, afiliada a la 102 desde el 2025-01-01 |
| Médico canónico de Darién | id_medico 104, Daniel Vega, `medico05@example.com`: conserva sus 8 pacientes y embarazos canónicos en curso y ya no sigue ninguno del escenario |

La contraseña de la médica es la contraseña simulada del dataset, como la de
las pacientes.

**Identidades.** Cada paciente tiene la identidad completa que guarda la base
—dos nombres, dos apellidos, cédula, correo y celular, y en dos casos un
contacto secundario— para que en el portal y en el BI se vean como un
expediente real. Por definición de esta demo, las seis son **originarias de
Darién**: su cédula lleva el prefijo provincial 5 (provincia-tomo-asiento),
como las de Darién del generador. No es la clínica la que fija la cédula
—residir o atenderse en una provincia no la cambia—, sino el origen elegido.
Todo es inventado: los correos usan el dominio reservado `example.com`
(parecen personales pero no pueden ser de nadie); **las cédulas y los
teléfonos se inventaron a mano y no se ha verificado si coinciden con los de
personas reales**. Ninguna cédula coincide con las de la base (las de Darién
del dataset usan el tomo 900). La contraseña es la contraseña simulada del
dataset (`PASSWORD_SIMULADA` del generador; no se repite aquí).

| id_paciente | Perfil | Cédula | Embarazo (id) | Dispositivo | Qué permite demostrar |
|---|---|---|---|---|---|
| 10901 | **Protagonista**: Elena Sofía Araúz Quiel, `elena.arauz94@example.com`, cel. 6674-2918, domicilio 299-6408 | 5-708-1834 | 10901 FINALIZADO, 2025-01-13 → cierre 2025-10-13 | `DEMO-SCRUM109-01` (DISPONIBLE, asignación cerrada) | Historial de un embarazo anterior (≈9 meses de seguimiento, WARNING aislados en julio de 2025). |
| | | | 10902 ACTIVO, 2026-04-27 (semana 30–31 en la sustentación) | **`DEMO-SCRUM72-01`** (el principal, una sola asignación) | Embarazo actual, Demo Control, captura en vivo, descarga del resumen; WARNING histórico en julio de 2026 seguido de OK. |
| 10902 | Apoyo: Marta Isabel Samudio Caballero, `marta.samudio@example.com` | 5-704-2290 | 10903 ACTIVO, 2026-03-02 | `DEMO-SCRUM109-03` | Solo lecturas OK. |
| 10903 | Apoyo: Rosa María Jaramillo Serrano, `rosamjaramillo@example.com` (alterno `rosa.jaramillo.s@example.com`) | 5-711-1065 | 10904 ACTIVO, 2026-04-13 | `DEMO-SCRUM109-04` | **WARNING recientes**: desde el 2026-10-05 todas sus lecturas son WARNING. |
| 10904 | Apoyo: Inés Carolina Pittí Morales, `ines.pitti88@example.com` | 5-702-1918 | 10905 ACTIVO, 2026-03-23 | `DEMO-SCRUM109-05` | **WARNING históricos seguidos de OK** (2026-08-17 a 2026-09-06). |
| 10905 | Apoyo: Clara Lucía Miranda Guerra, `claramiranda01@example.com` | 5-716-1597 | 10906 ACTIVO, 2026-06-01 | `DEMO-SCRUM109-06` | Embarazo temprano: movimientos solo desde la semana 20 (2026-10-14). |
| 10906 | Paula Andrea Quintero Saldaña, `paula.quintero@example.com`, **sin cuenta** | 5-713-2213 | — | — | Perfil preparado para SCRUM-110 (habilitar una cuenta). Sin embarazo ni mediciones. |

**Los dos embarazos de Elena: fechas e intervalo.**

- *Qué es `embarazo.fecha_inicio`.* El modelo no lo declara con un nombre
  clínico, pero el sistema lo usa como el **día 0 del cómputo gestacional**
  (equivalente a la fecha de la última menstruación), no como la fecha de
  concepción: la FPP es `fecha_inicio + 280 días` (generador y escenario) y la
  semana gestacional es `días transcurridos // 7 + 1`
  (`app.services.ingesta.semana_gestacional`, la misma aritmética del
  generador). La concepción no se registra; por convención clínica se estima
  unas 2 semanas después de ese día 0.
- *Fechas (definitivas, sin cambios).* Anterior 10901: 2025-01-13 → cierre
  2025-10-13 (273 días; semana 40 por la aritmética del sistema, 39+0
  cumplidas; FPP 2025-10-20), FINALIZADO, G1 P0, seguimiento cerrado ese día.
  Actual 10902: 2026-04-27, ACTIVO, G2 P1, FPP 2027-02-01; semanas 30–31 del
  20 al 23 de noviembre de 2026. Elena (nacida el 1994-05-14) tenía 30 y 31
  años al inicio de cada uno.
- *Intervalo.* Del cierre anterior al inicio registrado del siguiente: **196
  días ≈ 6,4 meses**. Hasta la concepción estimada (≈ 2026-05-11): ≈ 210 días ≈
  6,9 meses. No hay solapamiento: las lecturas del anterior terminan antes de
  su cierre, las del actual empiezan después de su inicio, cada uno tiene su
  dispositivo y su asignación, y los seguimientos no se cruzan.
- *Posible frente a recomendado.* Es un intervalo **biológicamente posible**
  y **más corto que el recomendado**: la OMS recomienda esperar al menos 24
  meses tras un nacido vivo antes de un nuevo embarazo
  ([WHO/RHR/07.1, 2005](https://www.who.int/publications/i/item/WHO-RHR-07.1)),
  y ACOG/SMFM aconsejan evitar intervalos menores de 6 meses y asesorar sobre
  los menores de 18 ([Obstetric Care Consensus n.º 8, 2019](https://www.acog.org/clinical/clinical-guidance/obstetric-care-consensus/articles/2019/01/interpregnancy-care)).
  Son recomendaciones de asesoría, no reglas del sistema: no se validan ni se
  imponen, y las fechas no se cambiaron. El modelo solo guarda el estado
  FINALIZADO y la fórmula G/P (el P1 del actual cuenta el parto del anterior);
  no registra el desenlace del parto, y el escenario no lo inventa.

**Paula y la clínica.** El modelo solo relaciona paciente y clínica a través
del embarazo (`embarazo.id_clinica`). Paula no tiene cuenta ni embarazo, así
que en la base no queda ligada a la clínica 102; crearle un embarazo para
ligarla falsearía el perfil que SCRUM-110 necesita. Su relación con Darién es
la de origen (la cédula).

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
también para lo posterior al corte); los contactos secundarios usan el id de su
paciente + 10 (10911, 10913). Tras la carga, las secuencias de esas
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

### Reconstruir la base del escenario desde cero

El cargador nunca sobrescribe una fila con otro contenido: un cambio de la
definición (identidades, clínica) se aplica reconstruyendo la base, no
editándola. Con la API y el portal detenidos, y tras un respaldo verificado del
estado actual:

```powershell
$env:PYTHONIOENCODING = "utf-8"
$m = "20261024t0207z"   # marca UTC; la base actual se aparta, no se borra
docker exec tesis-bi-prenatal-scrum109-db-1 sh -c "psql -U `$POSTGRES_USER -d postgres -v ON_ERROR_STOP=1 -c 'ALTER DATABASE fetalalert_scrum109 RENAME TO fetalalert_scrum109__previa_$m' -c 'ALTER DATABASE fetalalert_scrum109__previa_$m ALLOW_CONNECTIONS false' -c 'CREATE DATABASE fetalalert_scrum109'"
Move-Item data\gestante C:\FetalAlert_respaldos\SCRUM-109_data_gestante_previa_$m; New-Item -ItemType Directory data\gestante
python scripts/bootstrap_roles.py aplicar
cd backend; python -m alembic upgrade head; cd ..
python scripts/load_mock_data.py                              # 732 sesiones, 1180 lecturas
python scripts/cargar_escenario_scrum109.py cargar --ensayo
python scripts/cargar_escenario_scrum109.py cargar            # corte = ahora
python scripts/etl_analitico.py ejecutar
python scripts/cargar_escenario_scrum109.py verificar
```

`load_mock_data.py` usa el artefacto existente de `data/generated/`; no hace
falta regenerarlo. Si se regenera, solo cambia `password_hash` (sal aleatoria
de Argon2id) y la clave simulada sigue verificando.

**Base inicial de referencia (SCRUM-109.4).** Reconstruida así el 2026-10-24
(con un primer `cargar --corte 2026-10-10` para comparar con la versión
anterior al mismo corte), con corte efectivo **2026-10-24T16:49:54-05:00
(America/Panama)**: 1.180 lecturas canónicas + 226 del escenario, ETL
`SUCCESS`, sin capturas en vivo ni filas de auditoría. Respaldo:
`C:\FetalAlert_respaldos\SCRUM-109_base_inicial_medico_demo_20261024` (el
estado previo, 109.3, está en `SCRUM-109_antes_medico_demo_20261024`).

### Conflictos que detiene, sin sobrescribir

- una fila con la misma llave y otro contenido (`ConflictoDeDatos`);
- una fila ajena dentro de un bloque reservado;
- un correo, cédula o código de dispositivo del escenario usado por otra fila;
- asignaciones o seguimientos ajenos sobre sus dispositivos o embarazos, u otro
  embarazo de una de sus pacientes;
- una cuenta cuyo hash ya no verifica la contraseña simulada;
- cero o varias clínicas en la provincia de la demo: no se elige una;
- seguimientos de otros embarazos sobre la médica de la demo, o afiliaciones
  suyas a otra clínica;
- en la definición, un día sin PRINCIPAL o con dos superpuestos, o una
  afiliación que no cubra cada día con seguimiento u observación.

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
- **Médica de la demo** (`itzel.barria@example.com`): ve **solo** las cinco
  pacientes con cuenta —Elena, Marta, Rosa, Inés y Clara— con sus identidades
  (`v_paciente_medico`) y sus cinco embarazos **en curso** (10902–10906).
  **No** ve el embarazo anterior de la protagonista: la regla vigente (API y
  `v_entitlement_medico`) exige un seguimiento activo y vigente hoy, y el de un
  embarazo finalizado está cerrado. El escenario respeta esa regla (ver «Por
  qué el médico no ve el embarazo anterior»). Paula no aparece: no tiene cuenta,
  embarazo ni seguimiento.
- **Médicos canónicos**: `medico05` (Darién) conserva sus 8 embarazos
  canónicos en curso y ya no ve ninguna paciente del escenario; `medico01` a
  `medico04` (Chiriquí y Veraguas) tampoco (404 en la API, sin entitlement en
  las vistas). Administración recibe 403 en la superficie clínica.
- La separación sale de las filas de `seguimiento_clinico` y de los permisos
  efectivos que ya derivan de ellas (API, RLS y vistas de publicación); no hay
  filtros visuales ni excepciones de RLS.
- **Administración** (`v_resumen_administrativo`): sin identidades, con el
  mínimo vigente de 5 embarazos distintos por provincia, mes y semáforo. En
  Darién, las celdas OK de 2026-07, 2026-08 y 2026-09 se publican **solo con
  los 5 embarazos del escenario** (el canónico no tiene lecturas en esos
  meses), y la de 2026-06 con 8 (5 + 3 canónicos). Los WARNING del escenario
  (grupos de 1) y la celda OK de 2026-10 (4 embarazos, porque Rosa solo tiene
  WARNING desde el día 5) quedan suprimidos y siguen disponibles para el
  médico. El embarazo anterior de la protagonista aporta 1 embarazo a celdas
  de Darién de 2025, todas suprimidas. El dataset canónico de la tesis no se
  toca.

| Superficie (corte 2026-10-24T16:49:54-05:00 de la base inicial, sin capturas) | Valor esperado |
|---|---|
| `itzel.barria@example.com` (UPN) | 5 pacientes, 5 embarazos, 170 lecturas, todas del escenario (no entran las 56 del embarazo finalizado) |
| `medico05` (UPN) | 8 pacientes, 8 embarazos, 334 lecturas; ninguna del escenario |
| `medico01` / `02` / `03` / `04` | 3 / 3 / 3 / 3 pacientes y embarazos; 127 / 115 / 116 / 113 lecturas; ninguna del escenario |
| `v_lectura` (sin RLS) | 1.406 lecturas (1.180 canónicas + 226 del escenario), 36 embarazos |
| `v_resumen_administrativo` | 34 celdas publicadas, 1.021 lecturas: Chiriquí 10/286, Darién 14/453, Veraguas 10/282 |

Respecto de SCRUM-109.3 (mismo corte de observaciones) solo cambia quién ve
las pacientes del escenario: `medico05` pasa de 13/13/504 a 8/8/334 y la
médica de la demo recibe las 5/5/170. `v_lectura` y las celdas
administrativas no cambian: el administrador sigue viendo el conjunto
ampliado —canónico más demo— sin identidades y con el mínimo de 5 embarazos
por celda. Este entorno ampliado **no es** la muestra de la tesis (30
gestantes, 732 sesiones y 1.180 lecturas), que sigue intacta en sus filas.

## Power BI: comprobaciones pendientes (manuales)

El repositorio no contiene el PBIX. El modelo revisado para nombrar tablas y
medidas es el **PBIP del 27/09/2026**
(`FetalAlert_PowerBI.SemanticModel`), que importa `publicacion v_embarazo`,
`publicacion v_lectura`, `publicacion v_entitlement_medico` y
`publicacion v_resumen_administrativo`, con el rol `Medico`
(`[upn_medico] = LOWER(USERPRINCIPALNAME())`). El archivo vigente puede ser
posterior: comprueba los nombres antes de aplicar nada. Ver el informe de
entrega para la lista y las expresiones DAX propuestas.

Lo comprobado por SQL y HTTP (cifras de la tabla anterior, permisos por rol)
**no** valida el PBIX. En Power BI Desktop queda pendiente:

1. **Conexión**: servidor `127.0.0.1:5443`, base `fetalalert_scrum109`, rol
   `fetalalert_powerbi` (clave en el `.env` del worktree, no versionada).
   Confirmar que el modelo apunta a este origen y no al entorno habitual.
2. **Origen de `v_embarazo`**: que la tabla importe `publicacion.v_embarazo`
   (y no una versión anterior o una consulta propia).
3. **Relaciones** entre `v_embarazo`, `v_lectura`, `v_entitlement_medico`,
   `v_embarazo_medico`, `v_entitlement_paciente_medico` y `v_paciente_medico`.
4. **RLS** con «Ver como» `itzel.barria@example.com`: 5 pacientes, 5
   embarazos y 170 lecturas, nombres y cédulas solo de esas cinco; con
   `medico05@example.com`: 8/8/334 y ninguna paciente del escenario; los
   permisos de embarazos y de pacientes (incluidos nombres y cédulas) deben
   salir de las vistas de entitlement, no de un filtro de página.
5. **Filtros guardados que hay que limpiar**: la paciente «Katherine» en el
   resumen médico y el seudónimo antiguo en la página de evolución; si quedan,
   ocultan o vacían los datos del escenario.

No basta con «actualizar» hasta verificar en Desktop el modelo vigente.

## Por qué el médico no ve el embarazo anterior (limitación aceptada)

**Causa.** El acceso del médico no depende del estado del embarazo
(`ACTIVO`/`FINALIZADO`), sino de que tenga **hoy** un `seguimiento_clinico`
propio vigente: `activo` y `fecha_asignacion <= hoy <= fecha_fin` (o
`fecha_fin` nula), con «hoy» en America/Panama. La misma condición está escrita
en dos sitios independientes, ambos de la migración
`backend/alembic/versions/3b4a352bc39a_enable_row_level_security_and_clinical_.py`:

| Superficie | Dónde | Efecto sobre el embarazo 10901 |
|---|---|---|
| API (`/api/v1/clinico/*`) y RLS de `operacional` | `seguridad.embarazo_en_seguimiento_vigente()`, usada por `app/services/consulta_clinica.py` y por las políticas | `GET /clinico/embarazos` no lo lista y `/clinico/embarazos/10901/sesiones` responde 404 (comprobado por HTTP). |
| Power BI | `publicacion.v_entitlement_medico` (condición `VIGENCIA`); de ella derivan `v_embarazo_medico` y `v_entitlement_paciente_medico` (`9c1d7f2ab4e8_…`) | Sin fila de entitlement ni fila en `v_embarazo_medico`. Sus 56 lecturas sí están en `v_lectura`, que no filtra en la base: las oculta el RLS del PBIX (R1 → `v_embarazo_medico` → R4). |

El seguimiento del embarazo 10901 se cerró con el parto
(`fecha_fin = 2025-10-13`, `activo = false`), igual que en los embarazos
finalizados del dataset canónico. Es una decisión de diseño de SCRUM-98/99:
`docs/proteccion_analitica.md` §14.2 explica que ver a una paciente no debe
arrastrar «un episodio que el médico no sigue».

**Por qué no se corrige en SCRUM-109.** Con los datos solo se lograría dejando
abierto un seguimiento de un embarazo cerrado hace un año, que es inventar un
seguimiento activo para eludir la regla. La alternativa es cambiar el predicado
de la API/RLS o las vistas de publicación, lo que amplía permisos y queda
fuera de este ticket. Si se quiere que el médico consulte antecedentes
obstétricos, es una decisión de autorización para otro ticket.

**Recorrido de la demostración.** El historial del embarazo anterior se muestra
en el **portal de la paciente** (Mi historial y su PDF); en el **BI médico**,
con la médica de la demo, se muestra el embarazo actual de la protagonista y
los de las cuatro pacientes de apoyo.

## Dependencias

Esta rama parte del PR #24 (`feature/entorno-demo-restaurable`, `0866598`, que
contiene los cuatro primeros commits del #23 de Demo Control), integra el PR
#22 (`feature/gestante-guardar-resumen-demo`, `6bd303e`, descarga del resumen)
y el último commit del #23 (`3b8bece`, nombres descriptivos de Adminer), ambos
con merges locales sin conflictos. Mientras esos PR sigan
abiertos, el PR de SCRUM-109 mostrará también sus commits; debe fusionarse
**después** de ellos.

## Pruebas

- `backend/tests/test_escenario_scrum109.py`, sin servidor: identidades
  (cédula de Darién, correos reservados, sin duplicados), reglas del dominio,
  semana 20, forma de las lecturas, coberturas, reglas de médico y afiliación del
  ETL, médica de la demo y sus seguimientos, afiliación tardía, PRINCIPAL
  superpuesto, coherencia de los dos embarazos de Elena (intervalo de 196 días,
  FPP, semana de cierre, edades), semáforos, celdas administrativas, columnas
  exactas del modelo y corte.
- `backend/tests/test_escenario_scrum109_postgresql.py`, con
  `SCRUM109_TEST_DATABASE_URL` hacia una base desechable `scrum109_*` migrada y
  con el dataset canónico: primera carga y reejecución, avance y retroceso del
  corte, ausencia de futuro, canónico intacto fila por fila, secuencias,
  integridad, semáforos, cuentas, rollback ante fallo, conflictos, `--ensayo`
  del CLI, resolución de la clínica de Darién (otro médico afiliado no impide
  la carga), seguimientos y afiliaciones ajenas sobre la médica de la demo, y
  control negativo: la base rechaza dos seguimientos PRINCIPAL superpuestos.
  Tiene su propio paso en CI.
- `scripts/reproducir_ci_local.py`: reproduce en local los pasos del job de
  CI contra un PostgreSQL desechable; se detiene en el primer fallo y borra los
  reportes JUnit previos para que el guardián solo lea los de la ejecución en
  curso.
