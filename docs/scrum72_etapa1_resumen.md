# SCRUM-72 — Etapa 1 (datos y comportamiento): resumen para la etapa 2

Estado al 2026-09-25. SCRUM-72 **no está terminado**: falta el rediseño visual
(etapa 2) y las decisiones pendientes de abajo. Todo se hizo sin tocar
contratos centrales de SCRUM-98 (JWT/RBAC, RLS, esquemas clínicos, publicación
analítica): los cambios viven en `backend/app/gestante/`, `frontend/gestante/`,
sus pruebas y el workflow de CI.

## Qué quedó hecho

- **«Tus últimos registros»** en Inicio: una tarjeta por variable con el
  último valor no nulo de esa variable en el embarazo en curso, cada una con
  su fecha, la semana de su lectura y la clasificación de su lectura. Regla
  única en `app.gestante.clinico` (`serie`, `ultimo_registro`); el adaptador
  la expone en `datos.ultimos_registros` de
  `GET /adaptador/embarazos/{id}/monitoreo`. `ultima_lectura` se conserva y
  alimenta «Tu lectura más reciente» (un solo semáforo, de una sola lectura).
- **Series** para gráficas en `datos.series.{frecuencia_cardiaca|saturacion_oxigeno|movimientos_fetales}`:
  `{unidad, puntos:[{valor, unidad, fecha_hora_captura, semana_gestacion_lectura, codigo_semaforo_lectura, id_lectura, id_sesion}]}`,
  ascendentes, solo lecturas existentes. `valor` llega como texto para
  NUMERIC (`"83.00"`) y entero para movimientos; `0` es un valor.
- **Semana actual** (`datos.semana_actual`, `datos.hoy` en
  `/adaptador/embarazos`): día de calendario de Panamá, aritmética
  `app.services.ingesta.semana_gestacional`, sin topes.
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

1. **Fuente del valor simulado de movimientos (decisión de la usuaria).**
   Hoy cada registro local lleva `MOV_SIMULADO = 12`, una constante que **no
   procede del dataset**. No se sustituyó: no se deben copiar lecturas entre
   embarazos ni reproducir sesiones canónicas como nuevas sin especificación.
   Propuesta mínima, sin tocar el dataset ni contratos centrales:
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
2. **CI en GitHub Actions**: el step nuevo solo se validó localmente (mismo
   mecanismo `TEMPLATE`, 13/13, pero con la identidad de administración y no
   con el migrador no superusuario). Confirmar en la primera ejecución real.
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
   se enviaron.

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
