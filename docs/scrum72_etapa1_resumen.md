# SCRUM-72 — Etapa 1 (datos y comportamiento): resumen para la etapa 2

Estado al 2026-09-25. SCRUM-72 **no está terminado**: falta el rediseño visual
(etapa 2) y las decisiones pendientes de abajo.

**Alcance respecto de `main` (con SCRUM-98 integrado, `f91991a`).** Código
nuevo: `backend/app/gestante/`, `frontend/gestante/`,
`scripts/gestante_web.py`, `scripts/provisionar_demo.py` y sus pruebas
`backend/tests/test_gestante_*.py` y `test_provisionar_demo_postgresql.py`.
Archivos existentes que cambian: `.github/workflows/ci.yml` (paso de Node;
step y entrada del guardián para el aprovisionamiento; su `--ignore` en la
capa sin base), `.gitignore` (`data/gestante/`), `README.md` y
`backend/tests/test_cuentas.py` (el guardián pasa de 14 a 15 reportes, número
exacto, y exige el nuevo). Sin cambios en el código de la API, las
migraciones, el cargador, el ETL, `bootstrap_roles`, Compose ni dependencias:
JWT/RBAC, RLS, contratos clínicos y publicación analítica quedan intactos.

## Qué quedó hecho

- **«Tus últimos registros»** en Inicio: una tarjeta **neutral** por
  variable con el último valor no nulo de esa variable en el embarazo en
  curso, cada una con su fecha y la semana de su lectura. Sin semáforo: la API
  solo clasifica lecturas completas. Regla única en `app.gestante.clinico`
  (`serie`, `ultimo_registro`); el adaptador la expone en
  `datos.ultimos_registros` de `GET /adaptador/embarazos/{id}/monitoreo`.
  `ultima_lectura` se conserva y alimenta «Tu lectura más reciente»: un solo
  semáforo, de una sola lectura, con fecha y alcance escritos.
- **Series** para gráficas en `datos.series.{frecuencia_cardiaca|saturacion_oxigeno|movimientos_fetales}`:
  `{unidad, puntos:[{valor, unidad, fecha_hora_captura, semana_gestacion_lectura, id_lectura, id_sesion}]}`,
  ascendentes, solo lecturas existentes. `valor` llega como texto para
  NUMERIC (`"83.00"`) y entero para movimientos; `0` es un valor. Si una
  gráfica necesita el semáforo, debe tomarlo de `sesiones[].lecturas[]` y
  rotularlo como clasificación de la lectura, no de la variable.
- **Semana actual** (`datos.semana_actual`, `datos.hoy` en
  `/adaptador/embarazos`): día de calendario de Panamá, aritmética
  `app.services.ingesta.semana_gestacional`, sin topes numéricos y solo si
  hoy no pasa de la fecha probable de parto del episodio; si no, `null` e
  Inicio muestra «Semana en el último registro: N (fecha)».
- **Limitación del escenario**: el dataset simulado tiene episodios `ACTIVO`
  con fechas ya pasadas (embarazo 129: FPP 1/7/2026). Se documenta, no se
  avisa a la paciente y no se modifican fechas ni estados.
- **Fechas**: `Intl` con `es` + `America/Panama`; fechas de calendario
  formateadas como día UTC (no se corren).
- **Conexión y sesión central**: `GET /adaptador/estado-conexion` y
  `POST /adaptador/reautenticar` (misma cuenta, sin cerrar la sesión local).
  403 de `/yo` ya no se trata como 401. Ver README, «Estado de conexión».
- **Refresco**: el periódico ya no relee lo clínico (auditado por SCRUM-98).
- **CI**: `test_provisionar_demo_postgresql.py` corre en su propio step sobre
  `scrum72_provision_tmp` (copia `TEMPLATE` de la base de CI tras cargar el
  dataset), como el migrador no superusuario, y su reporte entra en el
  guardián de omisiones (ahora 15 reportes).

## Causa demostrada del «desconectado» tras ~15 minutos

1. Este equipo suspende a los **900 s sin uso con corriente** (600 s con
   batería; `powercfg`), y el registro de Windows muestra suspensiones S3
   varias veces al día.
2. Durante la suspensión el navegador corta las peticiones en curso. El
   `app.js` anterior pintaba «Sin conexión» ante **cualquier** fallo del
   `fetch` al portal local y no volvía a comprobar al reanudar; seguía así
   hasta el siguiente tic (20 s; hasta 1 min en segundo plano). Reproducido
   con CDP (red del navegador cortada, API en marcha):
   `scratchpad/navegador/corte_red_codigo_antiguo.log`.
3. Si la suspensión supera los 30 min del JWT, el código anterior mostraba
   «Conectado» con la nota «hace falta iniciar sesión de nuevo **cuando haya
   conexión**», sin acción para reautenticarse. Observado en una suspensión
   real de 45 min durante esta sesión (12:48–13:33).

Solución: estados separados y comprobados, «No se pudo comprobar la conexión»
cuando ni el portal contestó, comprobación inmediata en `visibilitychange`,
`resume`, `online` y `pageshow`, y reautenticación explícita.

## Pendientes concretos

1. **Fuente del valor simulado de movimientos.** Decisión de la usuaria para
   esta entrega: se **conserva** `MOV_SIMULADO = 12` como escenario técnico
   documentado —no procede del dataset ni es una medición—, sin otro guion,
   sin tocar el dataset y sin migraciones. Queda como referencia, para una
   entrega posterior, la propuesta mínima:
   - *Procedencia*: un guion de simulación versionado y separado del dataset
     (p. ej. `data/simulacion/movimientos_demo.json`, con semilla y versión
     declaradas), documentado como **entrada del sensor simulado**, no como
     medición ni como dato canónico.
   - *Vinculación*: `provisionar_demo.py` lo asocia al dispositivo que
     aprovisiona (el mismo `id_dispositivo` 130 que ya distingue estos
     registros), nunca a un embarazo del dataset.
   - *Creación*: cada «Registrar sesión» consume el siguiente valor del guion
     y guarda en la captura local el índice usado (el paquete de ingesta no
     cambia; `idempotencia_solicitud`, `auditoria_log` y el dispositivo ya
     trazan su origen en el servidor, como se comprobó con la sesión 832).
   Si se prefiriera marcar la procedencia en la propia fila (p. ej. un nuevo
   valor de `origen_dato`), eso **sí** cambia un contrato central y un
   `CHECK`: queda como decisión de SCRUM-98, no de esta etapa.
2. **CI en GitHub Actions**: ver el resultado de la ejecución del checkpoint
   publicado (step «Pruebas del aprovisionamiento de la demo contra
   PostgreSQL (SCRUM-72)»).
3. **Registrar sin API tarda ~2,8 s**: el adaptador contrasta el embarazo con
   el servidor antes de guardar y, en Windows, una conexión rechazada tarda
   en fallar. Funciona; valorar usar el último estado conocido.
4. **Etapa 2 (visual)**: dibujar `series`; revisar a 360 px la píldora
   larga «Servidor disponible · inicia sesión de nuevo» y el aviso de
   reautenticación (el desborde solo se midió en estado conectado).
5. La base de revisión ya tiene ~3 100 filas `ACCESO_CLINICO_PERMITIDO`
   generadas por el refresco anterior; no se borraron.
6. Cola local de la cuenta 107 en revisión: 1 enviado y 2 pendientes (uno de
   ayer y otro creado hoy a las 09:59 hora local, antes de esta sesión). No
   se enviaron. La revisión visual ya no supone un número: compara la frase
   mostrada con los conteos reales de `/adaptador/movimientos/estado`.

## Omisiones del gate local

Las 53 pruebas omitidas en la capa sin PostgreSQL son todas de
`tests/test_config_secretos.py`, y todas por la misma razón: el `.env` local
define `JWT_SECRET_KEY`, así que no pueden comprobar su ausencia. No son
omisiones esperadas en CI (allí no hay `.env`). Ejecutadas en un checkout de
HEAD sin `.env` (`git archive`, sin variables JWT/DB en el entorno):
53 pasadas, 0 omitidas.

## Cómo arrancar la revisión (sin secretos)

Requisitos: el contenedor `tesis-bi-prenatal-db-1` en marcha (base
`scrum72_demo_gestante` en `127.0.0.1:5433`) y el `.env` del worktree con
`DATABASE_URL` (rol `fetalalert_api`), `JWT_SECRET_KEY` y
`GESTANTE_API_BASE_URL=http://127.0.0.1:8010`. No usar `docker compose` desde
este worktree: crearía otro proyecto y otra base.

```powershell
cd backend; ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
# en otra terminal, desde la raíz del worktree:
.venv\Scripts\python.exe scripts\gestante_web.py --comprobar
.venv\Scripts\python.exe scripts\gestante_web.py        # http://127.0.0.1:8100
```

La API de `docker compose` en el puerto 8000 es la del repositorio principal y
no forma parte de esta revisión.

## Pruebas

- `node --test frontend/gestante/pruebas/app.comportamiento.test.js`
  (DOM mínimo, reloj y temporizadores controlados).
- `pytest tests/test_gestante_*.py` desde `backend/`
  (`test_gestante_ultimos_registros.py` cubre la regla por variable, las
  series, la semana actual, `estado-conexion` y `reautenticar`).
- Recorridos en Chrome headless (fuera del repositorio, en el scratchpad de
  la sesión): `revision_visual.js` (solo lectura contra 8100) y
  `sesion_vencida.js` (entorno aislado 8011/8101 sobre
  `scrum72_prueba_offline`, JWT de 1 minuto).
