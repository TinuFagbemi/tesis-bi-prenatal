# Validación de R1 — SCRUM-110 (autorregistro de pacientes)

Fecha: 2026-11-05 (hora de Panamá, UTC−5). Worktree de SCRUM-110, rama
`viviana/scrum-110-autorregistro`. Sin staging, commits, push ni PR.

> **Versión publicada.** Para el repositorio se generalizaron las referencias
> a herramientas y se omitieron las rutas de archivos locales. Los hechos,
> las cifras y las conclusiones no cambian. La copia original se conserva
> solo en el equipo donde se hizo la validación.

Esta carpeta no contiene secretos. Se revisaron sus 51 archivos buscando cada
variante (en claro y codificadas) de las dos credenciales usadas: ninguna aparece.
Las credenciales se leen de los `.env` locales y viajan solo por variables de
entorno. Toda la salida pasa por un enmascarado antes de escribirse.

## Evidencia incluida en el repositorio y evidencia solo local

**Se propone versionar solo este informe** (`docs/validacion_scrum110_r1/INFORME.md`).
R1 no tiene capturas.

**Todo lo demás que este informe cita se conserva solo en el equipo de la
validación** y no está en el repositorio: `registros/` (salidas, XML de JUnit,
instantáneas de roles), `manifiesto_*.txt` y `herramientas/`. Las referencias a
esos archivos sirven para quien tenga esa copia local; para los demás, este
informe resume lo que contienen. La huella agregada de cada manifiesto permite
comprobar el árbol probado aunque el manifiesto no se haya publicado.

Se revisó antes de proponerlo: no contiene credenciales, ni datos personales
reales, ni rutas con el nombre de usuario del equipo. Todos los datos citados son
ficticios. Explica que una contraseña local de desarrollo se mostró en una
sesión y que su rotación está pendiente (§5 y §6), pero no contiene la contraseña.

## 1. Estado del código probado

Base: `HEAD = f5790d81dc8a71f0d771fa90fe3f43c0b0512440` (= `origin/main`, PR #23).
Todo R1 está en el árbol de trabajo, sin commit. Cada manifiesto lista el SHA-256 de
cada archivo modificado o nuevo respecto de HEAD (los nuevos incluidos) y una huella
agregada. Excluye esta carpeta.

| Manifiesto | Huella agregada | Qué es |
|---|---|---|
| `manifiesto_01_codigo_recibido.txt` | `2477181a…3177471` | R1 tal como quedó en la sesión anterior (31 archivos). |
| `manifiesto_01b_codigo_pruebas_postgresql.txt` | `afba06b2…57f3dd` | + corrección de `env.py` y su prueba (33). **Con este árbol corrieron las pruebas de PostgreSQL.** |
| `manifiesto_02_codigo_final.txt` | `61dcedac…52f5c3` | + entrada en `decision_log.md`. Estado final (33). |

Entre 01b y 02 solo cambia `docs/decision_log.md`. El 01b se reconstruyó a partir
del 02 con la línea de `decision_log.md` del 01, y su huella coincide con la que
grabaron en su cabecera las corridas 04b–27.

Para verificar que el árbol actual es el probado:

```
cd docs\validacion_scrum110_r1
python herramientas\manifiesto.py verificar manifiesto_02_codigo_final.txt
```

Cada registro en `registros/` empieza con `# huella_arbol:`, la huella del árbol
en el momento de esa ejecución.

## 2. Resultados

Python: `tesis-bi-prenatal-scrum109\backend\.venv` (el mismo de la sesión
anterior). Orden con `-p no:randomly`.

| # | Qué | Árbol | Resultado | Registro |
|---|---|---|---|---|
| 01 | Suite offline completa, código recibido | 2477181a | **2935 aprobadas, 0 fallos, 1042 omitidas**, salida 0 | `01_offline_codigo_recibido.log/.xml` |
| 04a | Regresión nueva de `env.py`, **sin** la corrección | e744e07e | **Falla**: la clave aparece en la salida (esperado) | `04a_…log` |
| 04b | `test_config_secretos.py` + `test_migrations.py`, con la corrección | afba06b2 | 134 aprobadas, salida 0 | `04b_…log` |
| 08–16 | Migración y escalón en la base temporal | afba06b2 | Todo con salida 0 (§3) | `08_…` a `16_…` |
| 17–19 | Dataset generado y cargado | afba06b2 | 30 pacientes, 37 usuarios, 30 embarazos, 732 sesiones, 1180 lecturas | `17_…` a `19d_…` |
| 20 | `test_registro_postgresql.py` como `fetalalert_api` | afba06b2 | **59 aprobadas, 0 omitidas**, salida 0 | `20_pg_registro.log/.xml` |
| 21 | `test_rls_postgresql.py` como `fetalalert_api` | afba06b2 | **97 aprobadas, 0 omitidas**, salida 0 | `21_pg_rls.log/.xml` |
| 22 | Residuos tras las pruebas | afba06b2 | Conteos iguales al dataset; 0 triggers y 0 funciones `scrum110*` | `22_…log` |
| 23 | Catálogo de `fetalalert_powerbi` | afba06b2 | Solo USAGE en `publicacion` y `public`; sin SELECT ni INSERT en `auditoria_log` | `23_…log` |
| 30 | Suite offline completa, código final | 61dcedac | **2936 aprobadas, 0 fallos, 1042 omitidas** (todas de PostgreSQL), salida 0 | `30_offline_codigo_final.log/.xml` |

**Las 1042 omitidas de la suite offline** son, todas, pruebas de 17 suites de
PostgreSQL sin URL definida (agrupadas por archivo con el XML de JUnit). Ninguna
prueba sin servidor se omitió. La sesión anterior reportó 1004. La diferencia
(38) es coherente con las pruebas que se añadieron a `test_registro_postgresql.py`
después de esa corrida, que hoy tiene 59, pero no hay un desglose anterior que lo
demuestre.

**Qué cubre la suite de registro (20), con evidencia:**

- Registro real con todas sus filas.
- Inicio de sesión inmediato y lista de embarazos vacía.
- Duplicados (409, sin crear nada).
- Una carrera de cuatro solicitudes (exactamente un registro).
- Rollback después de la función y rollback en el COMMIT (con un trigger diferido temporal).
- La API sin privilegios directos y sin poder vincular cuentas.
- La función pertenece al dueño NOLOGIN, solo la ejecuta la API (PUBLIC no) y no tiene parámetros de rol, estado ni perfil.
- Privilegios mínimos del dueño (20 casos).
- La política nueva es solo de INSERT y solo para el dueño.
- Las rutas administrativas siguen siendo solo de ADMIN.

**Dataset (17–19).** La salida del primer `generar` se cortó en la consola
(cp1252), aunque su código de salida fue 0. Para demostrar que la carga usó una
generación completa:

- se regeneró dos veces con la misma semilla;
- 20 de los 23 archivos son idénticos byte a byte;
- los 23 son idénticos si se enmascaran los hashes Argon2id, que llevan sal aleatoria (`17f_…txt`);
- los 37 usuarios de la base coinciden con `usuarios.csv` sin el hash, y todos tienen hash `$argon2id$` (`19c`/`19d`).

## 3. Cobertura de migración

**Comprobado en la base autorizada**, `scrum110_r1_20261105_2316_e7e1cd`
(OID 58755), creada vacía para esta ejecución:

- `alembic upgrade head` desde cero hasta `c5d2a8f1e073`.
- `alembic check`: «No new upgrade operations detected», antes y después del escalón.
- Escalón `head → 3b4a352bc39a (SCRUM-98) → head`. En cada punto se sondeó lo mismo que `test_el_autorregistro_desaparece_entero_y_vuelve`:

| Punto | revisión | tabla | función | INSERT dueño en paciente | INSERT dueño en usuario | pol_autorregistro | funciones en `seguridad` | políticas |
|---|---|---|---|---|---|---|---|---|
| head | c5d2a8f1e073 | sí | sí | sí | sí | 1 | 10 | 22 |
| SCRUM-98 | 3b4a352bc39a | no | no | no | no | 0 | 9 | 21 |
| head otra vez | c5d2a8f1e073 | sí | sí | sí | sí | 1 | 10 | 22 |

Los recuentos 10/22 son los mismos que ahora esperan `test_rls_postgresql`
(10 helpers, comprobado en 21) y `test_bootstrap_postgresql` (no ejecutada).

**Pendiente para CI (no ejecutado aquí, no se declara aprobado):**

- `test_migration_postgresql.py`. Su guardián exige la base `scrum52_validacion_tmp` y no se relajó. Cubre además el ciclo completo de la cadena, las vistas de SCRUM-99 y las columnas de SCRUM-99 en el escalón, que el sondeo manual no repite.
- `test_bootstrap_postgresql.py`. Crea y borra roles del servidor; no se ejecuta en el servidor compartido. Cubre el preflight de roles y los recuentos 10 helpers / 22 políticas tras migrar con membresías limpias.
- Las suites con guardián `scrum98_*` (`contexto`, `http_como_api`), las que crean sus propias bases (`cuentas`, `autenticacion`, `etl`, `provisionar_demo`) y las de publicación (`publicacion`, `publicacion_medica`), que necesitan las credenciales de `fetalalert_etl` y `fetalalert_powerbi`, y no se modificaron roles para obtenerlas.

## 4. Entorno, base temporal y limpieza

- **Antes:** Docker Desktop apagado y nada escuchando en 5432, 5433 ni 5443 (`00_estado_previo_servicios.json`). Los cinco contenedores estaban detenidos (`00_contenedores_antes.txt`).
- **Se inició** Docker Desktop y solo `tesis-bi-prenatal-db-1` (127.0.0.1:5433). No se tocaron los contenedores de SCRUM-109 ni la API.
- **Bases previas:** `fetalalert_dev`, `postgres`, `scrum72_demo_gestante`, `scrum72_prueba_offline`, `scrum73_powerbi` y `scrum99_powerbi`. No había ninguna `scrum110_*`, así que no hubo nada que reutilizar ni que borrar de ejecuciones anteriores. Ninguna de esas bases se modificó.
- **Base temporal:** se comprobó que el nombre no existía, se creó (OID 58755), se verificó el OID justo antes de borrarla y se eliminó solo esa (`24`–`26`). Las seis bases previas siguen ahí.
- **Roles y membresías:** idénticos antes y después en los 7 roles no del sistema (`03`, `27`, `28_diff_roles.txt`). No se modificó ningún rol.
- **Después:** contenedor detenido y Docker Desktop apagado; nada escuchando en esos puertos (`29_…`).
- **Fuera de la base:** `data/generated/` se regeneró (está en `.gitignore`). En una carpeta temporal local quedó una copia del dataset, sin credenciales de base.

## 5. Corrección de la fuga de credenciales

**Causa.** `backend/alembic/env.py` pasaba `ALEMBIC_DATABASE_URL` a
`config.set_main_option`, que usa la interpolación de configparser. Una
contraseña con caracteres reservados codificada en la URL lleva `%`. configparser
la rechazaba con un `ValueError` cuyo mensaje incluía la URL entera, contraseña
incluida. En la sesión anterior el lanzador codificaba la clave con `safe=""`,
lo que produjo `%` y la fuga.

**Corrección.**

- **Código:** `env.py` duplica `%` antes de `set_main_option`; configparser lo deshace al leer.
- **Regresión:** `test_alembic_acepta_una_url_con_porcentaje_sin_mostrarla`, en `test_config_secretos.py`. Renderiza las migraciones con una URL ficticia que lleva `%`, exige que la clave no aparezca en la salida y que la URL guardada en el Config sea la original. Falla sin la corrección (04a) y pasa con ella (04b).
- **Documentación:** entrada en `decision_log.md`.
- **Lanzador** (`herramientas/lanzar.py`), con dos barreras más: no construye URLs con `%` y enmascara toda la salida. Con él se ejecutaron, sin incidentes, los comandos de Alembic 08–16.

## 6. Rotación de la credencial de `fetalalert_dev` (no ejecutada)

**Pendiente y fuera del alcance de este PR.** No está autorizada: no se
ha rotado ninguna contraseña ni se han modificado credenciales, permisos o
configuraciones. Lo que sigue es solo el plan propuesto.

**Qué es.** El superusuario de arranque del clúster `tesis-bi-prenatal-db-1`
(volumen `fetalalert_pgdata`, solo en 127.0.0.1:5433). Es dueño de sus seis
bases. Su contraseña solo aparece en el `.env` del checkout principal, en
`POSTGRES_PASSWORD` (línea 17) y en `DATABASE_URL` (línea 31,
`fetalalert_dev@db:5432`). No hay `pgpass.conf` ni `.pgpass`.

**Afectados:**

- `tesis-bi-prenatal/.env`: las dos líneas.
- El contenedor `tesis-bi-prenatal-api-1`, que recibe esa `DATABASE_URL` al crearse.
- Los lanzadores locales (`herramientas/lanzar.py` y el `validar_r1.py` de la sesión anterior), que leen la clave del `.env` y se actualizan solos.
- Cualquier cliente externo guardado con esa cuenta: Adminer (se escribe a mano), pgAdmin, DBeaver u orígenes de Power BI Desktop contra 5433. No se pueden comprobar desde el repositorio; hay que revisarlos a mano.
- Un registro local de la sesión anterior, que contiene la clave en claro. Tras la rotación deja de servir; se puede borrar si se quiere.

**No afectados:**

- CI, que usa `fetalalert_ci` efímero.
- `fetalalert_api`, `fetalalert_etl` y `fetalalert_powerbi`, que tienen claves propias.
- El clúster de SCRUM-109 (5443), cuyo `.env` no contiene esta clave.
- `tesis-bi-prenatal-scrum72-final/.env`, que usa `fetalalert_api`.

**Procedimiento propuesto:**

1. Generar una clave nueva fuera de cualquier herramienta que guarde historial, por ejemplo `python -c "import secrets; print(secrets.token_urlsafe(32))"` (solo caracteres que no necesitan `%`).
2. Arrancar solo `tesis-bi-prenatal-db-1` y confirmar que ningún proceso lo usa.
3. `docker exec -it tesis-bi-prenatal-db-1 psql -U fetalalert_dev -d postgres` y, dentro, `\password fetalalert_dev`. La clave se pide sin eco y no queda en el historial de la shell.
4. Editar a mano en `tesis-bi-prenatal\.env` `POSTGRES_PASSWORD` y la clave de `DATABASE_URL`. `POSTGRES_PASSWORD` solo se aplica al inicializar un volumen vacío: **no** recrear el volumen (`down -v`).
5. Si se va a usar la API de ese checkout: `docker compose up -d api`, que la recrea con la variable nueva (`restart` no relee el `.env`).
6. Comprobar que la clave nueva conecta y la vieja no; por ejemplo, con `lanzar.py ... servidor "SELECT current_user"`.
7. Actualizar los clientes externos guardados y, opcionalmente, borrar el registro local citado.
8. Detener el contenedor y Docker Desktop si estaban apagados.

## 7. Estado de R1

**Implementado y validado con evidencia:**

- La API sin sesión, la función SECURITY DEFINER, la transacción única con rollback, los duplicados y la carrera, los privilegios mínimos, la auditoría y la migración reversible.
- La suite offline en el código recibido, más las regresiones de RLS y de registro en PostgreSQL.

- La suite offline sobre el estado final (corrida 30): 2936 aprobadas; la nueva es la regresión de `env.py`.

**Faltan para cerrar R1:**

1. Las suites que solo pueden correr en CI (§3). Se ejecutarán cuando haya PR, que no está autorizado.
2. La rotación de `fetalalert_dev` (§6), que no se hizo.
3. Tu revisión de R1, incluida la corrección de `env.py`, que se añadió en esta fase.
