# Gestante: «Guardar mi resumen» y diagnóstico de desconexiones

Estado al 2026-10-10. Rama `feature/gestante-guardar-resumen-demo`, creada
desde `origin/main` en `029fe50` (merge del PR #20, interfaz gestante
aprobada). Sin push, PR ni merge.

Dos entregas independientes:

- **A.** «Guardar mi resumen» (implementado y probado).
- **B.** Diagnóstico de desconexiones de la cola de capturas (solo
  verificación; no cambia código de captura ni de sincronización).

Todos los datos son simulados y ficticios.

---

## A. «Guardar mi resumen»

### Cómo se usa

1. Entrar al portal y abrir **«Mi historial»**.
2. Elegir el embarazo (y, si se quiere, el «Período de las gráficas»).
3. Pulsar **«Guardar mi resumen»**. El botón solo se habilita cuando las
   lecturas de ese embarazo terminaron de cargarse.
4. Se abre la vista del resumen con la indicación «En las opciones de
   impresión, selecciona “Guardar como PDF”.» y dos botones: «Volver a
   «Mi historial»» e «Imprimir o guardar como PDF».
5. «Imprimir o guardar como PDF» abre el diálogo de impresión del navegador.
   El nombre propuesto para el archivo es el título de la página,
   «FetalAlert — Mi resumen». La interfaz no anuncia «archivo guardado»: la
   persona puede haber cancelado y la página no puede saberlo.

### Cómo está hecho

- Vista `vista-resumen` dentro de la propia página (`index.html`), sin
  ventana emergente: no hay bloqueo de pop-ups ni ventana que cerrar.
- El documento se construye en `app.js` con `createElement` y `textContent`
  (nunca `innerHTML`), a partir de una **instantánea** del monitoreo que ya
  cargó «Mi historial»: no hace peticiones nuevas, no usa servicios externos,
  no añade dependencias y no guarda nada en el navegador.
- `styles.css`: estilos de pantalla y un bloque `@media print` que, solo con
  la vista del resumen abierta (`body.con-resumen`), oculta cabecera, menú,
  pie, avisos y controles. Sin tamaño de papel fijo (`@page` solo fija
  márgenes), así que A4 y Carta usan el que elija la persona. Cabecera de la
  tabla repetida en cada página y filas sin cortes.

### Qué contiene

| Bloque | Alcance |
|---|---|
| Marca FetalAlert, «Resumen de mis registros» y el embarazo («Embarazo desde 6 ene 2025 — Finalizado») | El embarazo elegido en «Mi historial». Nunca el de Inicio si son distintos. |
| Datos consultados el / Resumen generado el | Consulta = cuándo llegó al navegador el monitoreo usado. Generación = cuándo se pulsó el botón. Son fechas distintas. |
| Embarazo: estado, inicio, FPP y fecha de cierre (solo si existen) | Del episodio. |
| «Semana actual (al <hoy>)» | Solo para el embarazo en curso y solo si el adaptador publica `semana_actual`. En cualquier otro caso (finalizado, ambiguo, FPP vencida): «Semana en el último registro», con su fecha. |
| Últimos valores registrados (FC, SpO₂, movimientos) | De **todo el embarazo**, uno por variable, cada uno con su fecha, hora y semana. Pueden venir de lecturas distintas, y así se dice. Sin semáforo propio. |
| Clasificación de la lectura más reciente | El `codigo_semaforo` de la fuente autorizada para **una** lectura, con su fecha y lo que midió. Texto explícito: no se aplica a cada medición ni a los últimos valores. Un código desconocido es «Sin clasificación disponible». |
| Lecturas recientes | Hasta **10** del período elegido, de la más reciente a la más antigua, cada una con su clasificación (color + nombre). Se indica el período con sus fechas, cuántas se incluyen y de cuántas. |
| Pie | Horas de Panamá; «—» = valor no registrado, el cero sí es un valor; datos simulados; no sustituye la valoración del personal de salud. |

### Límites

- **Cobertura de los datos.** El servidor central no limita ni pagina
  `/embarazos/{id}/sesiones` ni `/sesiones/{id}/lecturas`
  (`services/consulta_clinica.py`), y el adaptador responde error en vez de
  un historial parcial si falla cualquier sesión. Por eso el resumen puede
  hablar de «las N lecturas más recientes de las M del período». El límite de
  10 es solo del documento; «Mi historial» sigue mostrando todas.
- **«Últimos 30 días con registros»** se cuenta hacia atrás desde la lectura
  más reciente del embarazo (la misma idea que las gráficas, que lo cuentan
  por variable desde el último registro de cada serie). El documento escribe
  las fechas del período, así que no hay ambigüedad sobre qué cubre.
- **Sin conexión.** Si el monitoreo ya estaba cargado, se puede generar; si
  la última comprobación no confirmó la conexión, el resumen lo dice: «Los
  datos son los de la última consulta y podrían no incluir registros
  posteriores». No se puede generar tras recargar sin API (no hay datos en
  memoria ni almacenamiento persistente, por diseño).
- **Sin lecturas** no se genera un documento: se muestra «Este embarazo
  todavía no tiene lecturas registradas, así que no hay un resumen que
  guardar.»
- **No se muestran** identificadores (de lectura, sesión, embarazo o
  dispositivo), códigos internos (`OK`, `WARNING`…), claves de idempotencia
  ni tokens.
- Sin gráficas en el documento: el texto debe poder seleccionarse y leerse
  sin color.

### Pruebas ejecutadas

Pruebas de comportamiento (Node, `frontend/gestante/pruebas/`), 16 nuevas en
`app.comportamiento.test.js` sobre el `app.js` real con DOM mínimo:

- habilitación solo con lecturas cargadas; deshabilitado al cambiar de
  embarazo y con error del servidor;
- correspondencia con el embarazo de Historial y no con el de Inicio;
- semana actual solo en el embarazo en curso; finalizado y FPP vencida usan
  la del último registro;
- variables con fechas distintas y semáforo de una sola lectura, con su
  alcance; código de semáforo desconocido;
- cero frente a «Sin registros» y «No disponible»;
- sin lecturas: mensaje y ningún documento;
- límite de 10, orden y cobertura; período «Últimos 30 días con registros»;
- fecha de consulta frente a fecha de generación; aviso sin conexión;
- respuesta tardía de otra selección; cierre de sesión con una carga de la
  cuenta A pendiente y entrada de la cuenta B;
- foco al título, «Volver» devuelve el foco al botón, imprimir solo llama a
  `window.print()` y no se anuncia «guardado»;
- escape de contenido (`<img onerror>` llega como texto) y ausencia de
  identificadores internos.

Resultados al cerrar la rama:

| Suite | Base `029fe50` | Rama |
|---|---|---|
| `node --test frontend/gestante/pruebas/*.test.js` | 73 pass, 1 skip | 89 pass, 1 skip; con `data/generated` generado, 90 pass, 0 skip |
| `pytest -k gestante` (sin PostgreSQL) | — | 370 passed, 1 skipped |
| `pytest` sin PostgreSQL (los mismos `--ignore` del primer paso del CI) | 2449 passed | 2449 passed |

El *skip* de Node es preexistente («data/generated no está generado»). Las
suites `*_postgresql.py` no se ejecutaron: el cambio no toca backend.

`test_gestante_frontend.py` prohíbe cifras de umbral clínico en `app.js`; la
constante del día se escribe como `86400000` por eso.

### Revisión visual (Chrome headless)

Con Chrome headless y `playwright-core` instalado **fuera** del
repositorio, contra un servidor local con datos ficticios (32 lecturas en el
embarazo finalizado) y contra el portal real del entorno aislado de la parte B:

- Inicio idéntico **píxel a píxel** a la versión base en 1366 px y 390 px;
  Historial igual salvo el botón nuevo.
- Botón alcanzable con Tab y activable con Enter; el foco pasa al título del
  resumen; al volver, al botón.
- Sin desplazamiento horizontal a 1366 y 390 px; en pantallas estrechas la
  tabla se apila como la del historial.
- Al imprimir solo se ve el documento.
- PDF generado por Chrome headless: **2 páginas en A4 y en Carta**, con y sin
  fondos; texto extraíble/seleccionable; fuentes incrustadas y sin recursos
  externos (se abre sin conexión). Sin filas cortadas ni páginas vacías.

### Comprobaciones manuales pendientes

El PDF anterior lo genera Chrome headless (`page.pdf`), que usa el mismo motor
de impresión pero **no** es el diálogo «Guardar como PDF». Falta hacerlo a mano:

1. Abrir el portal de demostración, entrar con una cuenta de demo, ir a
   «Mi historial», elegir el embarazo finalizado.
2. «Guardar mi resumen» → «Imprimir o guardar como PDF».
3. En el diálogo: Destino «Guardar como PDF»; tamaño A4; márgenes
   «Predeterminados»; escala 100 %. Guardar.
4. Repetir con tamaño Carta.
5. Desconectar la red y abrir ambos PDF: comprobar fechas, unidades, 1–2
   páginas, texto seleccionable y que no falte ninguna fila de la tabla.
6. Probar Cancelar en el diálogo: la página no debe mostrar ningún mensaje de
   archivo guardado.
7. Si se quiere declarar compatibilidad, repetir en Edge y Firefox y en un
   móvil real (no probado).

No se declara conformidad completa de accesibilidad: solo se comprobó
teclado, foco, etiquetas de texto junto al color y HTML semántico.

Además, con el portal real de la parte B (datos del dataset canónico,
embarazo finalizado de `paciente01`), el resumen se generó desde la interfaz
y salió en 2 páginas en A4 y en Carta.

---

## B. Diagnóstico de desconexiones

Solo verificación. **No se cambió** código de captura, cola, envío ni
sincronización. Termina en PostgreSQL: no se ejecutó el ETL ni se tocó
Power BI, y nada de esto afirma que el personal médico haya visto estas
capturas en su dashboard.

### Flujo probado

El del portal actual (README, «Captura simulada, operación sin conexión y
envío automático»):

```
scripts/dispositivo_gestante.py capturar      productor (dispositivo simulado; sin red ni JWT)
  → app.edge.capturar
  → <GESTANTE_MOVIMIENTOS_DIR>/cuenta-107.sqlite3   cola POR CUENTA, outbox PENDIENTE
  → app.gestante.envio_automatico               hilo del proceso del portal
  → app.edge.ejecutar_pasada → POST /api/v1/sesiones-monitoreo (JWT de la paciente + Idempotency-Key)
  → PostgreSQL operacional
```

No se probó el **nodo edge general** (`scripts/edge_node.py`, una cola
compartida de clínica con `EDGE_API_TOKEN`), que ya tiene sus suites
`test_edge_*`. Los resultados de abajo son solo de la cola por cuenta.

**Dónde vive cada cosa:**

- La cola SQLite está en la máquina que ejecuta el portal (`gestante_web.py`),
  en `GESTANTE_MOVIMIENTOS_DIR`. En la demo es el equipo de quien presenta,
  **no** el teléfono de la paciente.
- El JWT central solo existe en la memoria del proceso del portal. No se
  guarda en disco.
- En el navegador no hay nada clínico persistente: `localStorage`,
  `sessionStorage`, IndexedDB, Cache Storage y service workers vacíos (0); la
  única cookie es `fa_sesion` (`HttpOnly`, `SameSite=Strict`), un
  identificador opaco de la sesión local. Lo consultado vive en memoria de la
  pestaña.

### Entorno aislado

Nada del equipo se usó ni se detuvo: Docker no estaba en ejecución y no se
arrancó (su contenedor `tesis-bi-prenatal-db-1` y la base
`scrum72_demo_gestante` no se tocaron); no se usó ningún `.env` (el worktree
no tiene) ni los puertos 5433/8010/8100.

| Pieza | Valor |
|---|---|
| PostgreSQL | 16.2 (binarios del paquete `pgserver`, en un venv del scratchpad), clúster nuevo con `initdb`, `127.0.0.1:55433`, base `diag_desconexion_tmp` |
| Roles | migrador sin SUPERUSER ni BYPASSRLS, `fetalalert_api/etl/powerbi`, todos con claves ficticias; `bootstrap_roles.py aplicar`, `alembic upgrade head` |
| Datos | `generate_mock_data.py` + `load_mock_data.py` (1180 lecturas, `APP_ENV=test`); `provisionar_demo.py provisionar` → cuenta 107, embarazo 130, dispositivo 130 |
| API | `uvicorn app.main:app` en `127.0.0.1:8031`, `JWT_EXPIRATION_MINUTES=1` (para observar el vencimiento) |
| Portal | `scripts/gestante_web.py` en `127.0.0.1:8131`, `GESTANTE_ENVIO_AUTOMATICO_SEGUNDOS=5` (por omisión, 30) |
| Cola y sesión local | SQLite en el scratchpad (`GESTANTE_MOVIMIENTOS_DIR`, `GESTANTE_SQLITE_PATH`, `GESTANTE_PROVISION_PATH`) |

### Procedimiento reproducible

1. Clúster desechable: `initdb -D <dir> -U <admin> -A scram-sha-256`, y
   `pg_ctl -D <dir> -o "-p 55433 -c listen_addresses=127.0.0.1" start`.
2. Roles y base como el paso «Crear el migrador de CI» de `ci.yml`; después
   `python scripts/bootstrap_roles.py aplicar` y, en `backend/`,
   `alembic upgrade head`, con `ALEMBIC_DATABASE_URL` del migrador.
3. `python scripts/generate_mock_data.py`, `python scripts/load_mock_data.py`
   (`APP_ENV=test`) y `python scripts/provisionar_demo.py provisionar`.
4. Arrancar API y portal con las variables de la tabla. Un guion
   (`escenarios.py`, guardado fuera del repositorio) los arranca y detiene,
   inicia sesión en el portal como lo haría el navegador, ejecuta
   `python scripts/dispositivo_gestante.py capturar`, lee la cola en solo
   lectura (`?mode=ro`) y consulta PostgreSQL con el administrador del
   clúster desechable.
5. Al terminar: detener API y portal, y `pg_ctl -D <dir> stop`. Para repetir
   desde cero, borrar el directorio del clúster y el de la cola.

### Resultados (2026-10-10, ejecución real)

| # | Escenario | Resultado | Evidencia |
|---|---|---|---|
| 1 | Captura con la API detenida | Pasa | `capturar` sale 0: «Captura guardada en el dispositivo como PENDIENTE. No se ha usado la red.» Un evento nuevo en `cuenta-107.sqlite3`. |
| 2 | Persistida y pendiente | Pasa | Tras ~3 ciclos sin API: `PENDIENTE`, `intentos=0`, ningún registro de intento. El portal informa `api_central: no_disponible`. |
| 3–4 | Reinicio del portal | Pasa | Tras detenerlo y arrancarlo: misma clave, mismo `capturado_en`, mismo paquete (embarazo 130, dispositivo 130, 12 movimientos, fecha original). |
| — | Condición del envío automático | Pasa | Con la API de vuelta pero **sin token** (el reinicio lo borró), no se envía: `PENDIENTE`, 0 intentos; el portal informa `reautenticacion_requerida` y la sesión local sigue abierta. |
| 5 | Recuperación con autenticación válida | Pasa | Tras `POST /adaptador/reautenticar`, en ≤ 5 s: `ENVIADO`, 1 intento. En PostgreSQL: 1 solicitud de idempotencia, 1 sesión (embarazo 130), 1 lectura con `fecha_hora_captura` igual a la de la captura y `mov_valor=12`. |
| 6 | Reintento del mismo evento | Pasa | Más ciclos: sigue `ENVIADO`, intentos sin cambio. Reenvío manual de los **mismos bytes con la misma `Idempotency-Key`**: `201` con `Idempotency-Replayed: true` y el mismo `id_sesion`. PostgreSQL sigue con 1 solicitud y 1 sesión. |
| 7 | Credencial vencida | Pasa | Segunda captura con la API caída; se esperó a que venciera el JWT (1 min) y se arrancó la API: el portal pasa a `reautenticacion_requerida`, la captura sigue `PENDIENTE` con 0 intentos y 0 filas en PostgreSQL. Tras reautenticar: `ENVIADO`, 1 sesión, fecha original conservada. |

Al final: 2 sesiones nuevas del embarazo 130 en PostgreSQL (antes 0), una por
captura; las dos filas de la outbox en `ENVIADO` con 1 intento cada una.

**Observación (no es un defecto):** la lectura recibida tiene
`fecha_hora_sincronizacion = NULL`. Está decidido así (`docs/decision_log.md`,
`docs/modelo_analitico_etl.md`): el edge la envía nula y congelada para no
alterar la huella de idempotencia. El instante de llegada queda en
`idempotencia_solicitud.fecha_hora` y, localmente, en `outbox.enviado_en`.

**Conclusión.** La evidencia respalda, **para estos escenarios simulados y
para la cola por cuenta del portal**, la afirmación: «En los escenarios
simulados probados, las capturas se conservaron durante la interrupción y se
incorporaron posteriormente al servidor sin duplicarse.» Condición explícita:
el envío necesita que la API responda **y** un JWT vigente de esa paciente
en memoria del portal; tras un reinicio o un vencimiento, sale cuando ella
vuelve a autenticarse. No se probó el nodo edge general, ni la cadena hasta
el ETL y Power BI, ni un corte **durante** un envío en curso con procesos
reales (lo cubre la prueba
`test_un_intento_abandonado_por_un_reinicio_se_reconcilia_y_se_envia`, con
HTTP falso).

### Consulta en el portal durante la desconexión (portal real, Chrome headless)

- **La API cae con la página abierta:** se conserva lo mostrado (p. ej.
  12 movimientos) y aparece «Sin conexión con el servidor. Se muestra la
  última información consultada.» No pide credenciales.
- **Cambiar de embarazo en Historial sin API:** «Sin conexión con el
  servidor. Tu información clínica se mostrará cuando vuelva la conexión.»;
  «Guardar mi resumen» queda deshabilitado (no hay datos de ese embarazo).
- **Recargar sin API:** la sesión local (cookie) mantiene el portal abierto,
  pero no hay datos: tarjetas «—» y «No disponible». Nada clínico se conserva
  en el navegador. Ver el defecto siguiente sobre el texto de la nota.
- **Primer inicio de sesión sin API:** «No hay conexión con el servidor. El
  primer inicio de sesión en este dispositivo necesita conexión.»
- Con la API caída, cada petición al adaptador tardó unos 2 s;
  `/adaptador/embarazos` y `/adaptador/estado-conexion` llegaron a ~4 s de la
  recarga.

### Pendiente externo propuesto (preexistente; no corregido aquí)

**Tras recargar sin API, Inicio puede decir «Se muestra la última información
consultada» sin mostrar ninguna información.**

- **Reproducir:** con una sesión local vigente, detener la API y recargar el
  portal; esperar ~5 s en Inicio.
- **Esperado:** «Sin conexión con el servidor. Tu información clínica se
  mostrará cuando vuelva la conexión.» (no hay nada consultado que mostrar).
- **Real (observado en el portal real):** tarjetas en «No disponible» y nota
  «Sin conexión con el servidor. Se muestra la última información
  consultada.»
- **Hechos comprobados:** se reproduce de forma determinista con el arnés de
  pruebas de la versión base `029fe50` (y en la rama, igual): si
  `/adaptador/embarazos` responde **antes** que `/adaptador/estado-conexion`,
  queda el texto incorrecto; en el orden inverso, el correcto.
  `aplicarEstadoConexion()` escribe `TEXTO_SIN_CONEXION_CONTEXTO_CONSERVADO`
  siempre que la API no está disponible, haya o no datos en pantalla, y pisa
  el aviso que dejó `pintarSinDatosClinicos()`.
- **Hipótesis:** en el uso real el orden depende de cuál de las dos peticiones
  (lanzadas a la vez desde `refrescar()`) termina antes; con la API caída
  ambas cuestan lo mismo (~2 s), así que el resultado varía.
- **Efecto:** texto engañoso, sin pérdida de datos ni riesgo clínico directo.
- **Propuesta:** ticket aparte para que el aviso de conexión elija el texto
  según haya o no contexto clínico cargado, con una prueba de los dos órdenes.

### Evidencia conservada (fuera del repositorio)

En el scratchpad de la sesión: `diag/evidencia.json` y `diag/evidencia.log`
(cada escenario con sus datos), los registros de API y portal, la cola SQLite
y el clúster (detenido), las capturas y PDF de `visual/salida*`, y la
reproducción del defecto (`base_frontend/pruebas/repro_nota_sin_conexion.test.js`).
No se versionan: contienen bases y datos temporales. Las claves usadas son
ficticias y no corresponden a ningún entorno.
