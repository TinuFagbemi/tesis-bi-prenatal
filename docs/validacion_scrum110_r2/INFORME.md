# Validación de R2 — SCRUM-110 («Crear cuenta» en el portal)

Fecha: 2026-11-06, hora de Panamá. Worktree
`C:\GitHub\TESIS\tesis-bi-prenatal-scrum110`, rama
`viviana/scrum-110-autorregistro`, HEAD `f5790d8`. Sin staging, commits, push
ni PR. La evidencia de R1 (`docs/validacion_scrum110_r1/`) no se tocó.

Esta carpeta no contiene secretos. Se revisaron sus archivos buscando cada
variante de las dos credenciales de la base: no aparece ninguna. El
`JWT_SECRET_KEY` de la API efímera se generó en memoria y nunca se escribió.

## Evidencia incluida en el repositorio y evidencia solo local

**Se propone versionar:**

| Archivo | Qué muestra |
|---|---|
| `INFORME.md` | Este informe. |
| `capturas/e02_formulario_vacio.png` | Formulario en escritorio (1280 px), vacío, casillas desmarcadas. |
| `capturas/m02_formulario_vacio.png` | Formulario en móvil (390 px, DPR 3), una columna. |
| `capturas/e03_politica_de_privacidad.png` | Política de privacidad abierta en el diálogo modal. |
| `capturas/e05_cuenta_creada.png` | «Tu cuenta está lista», tras el 201 real. |
| `capturas/e06_portal_recien_creado.png` | Portal vacío después del inicio de sesión de siempre. |
| `capturas/e08_respuesta_perdida.png` | «No sabemos si tu cuenta se creó» (la cuenta sí existía). |

Las seis son de la segunda pasada, sobre el código final. Se revisaron antes de
proponerlas: no muestran credenciales ni datos personales reales; el único
teléfono visible es el de soporte que ya figura en el pie del portal. Los PNG no
llevan bloques de metadatos de texto.

**Se conserva solo en el equipo de la validación**, sin publicar:

- Las otras 11 capturas.
- `registros/`: salidas, XML de JUnit, JSON del conductor y comprobaciones en la base.
- `manifiesto_r2_*.txt`.
- `herramientas/`: lanzador, proxy y conductor de Chrome.
- `correccion_reautenticar.patch`.

Las referencias a esos archivos sirven para quien tenga esa copia local; para
los demás, este informe resume lo que contienen.

## 1. Código probado

| Manifiesto | Huella | Qué es |
|---|---|---|
| `manifiesto_r2_01_recorrido_chrome.txt` | `95b68899…20ff0` | Árbol con el que corrió la segunda pasada en Chrome (42 archivos). |
| `manifiesto_r2_02_codigo_final.txt` | `4dd05e46…956782` | Estado final. Respecto del anterior solo cambian `docs/autorregistro_pacientes.md` y `docs/decision_log.md`. |

Para verificarlo: `python herramientas\manifiesto.py verificar manifiesto_r2_02_codigo_final.txt`.

## 2. Qué se implementó

**Interfaz.**

- `frontend/gestante/registro.js` (nuevo).
- `index.html`: «Crear cuenta» en el inicio de sesión, el formulario, el panel de desenlace, el diálogo de documentos y la carga de `registro.js` con `defer` en `<head>`.
- `styles.css`: un bloque propio.
- Se corrigen dos textos que dejaban de ser ciertos: el del inicio de sesión y el de «Acerca de».

**Adaptador.**

- `central.py`: `documentos_registro()` y `autorregistrar()`, con `EstadoRegistro`.
- `rutas.py`: `GET /registro.js`, `GET /adaptador/registro/documentos` y `POST /adaptador/registro`.
- `aplicacion.py`: la ruta del registro entra en `RUTAS_CON_CREDENCIALES`, así que su 422 no devuelve lo recibido.

**`app.js` no cambia.**

El detalle de cada pieza y de cada desenlace está en `docs/autorregistro_pacientes.md` §4.

## 3. Pruebas: simuladas y reales, por separado

### Con respuestas simuladas (sin backend)

| Prueba | Resultado | Registro |
|---|---|---|
| `registro.comportamiento.test.js`: `registro.js` sobre un DOM mínimo con `fetch` falso | 21/21 | `50_…log` (dentro de la suite) |
| Mutaciones de control: 6 cambios deliberados en `registro.js` (doble envío, reintento tras 504, contraseñas sin vaciar, «no se creó» ante una respuesta perdida, detalle del servidor en pantalla, casilla marcada al leer) | Las 6 detectadas; sin mutar, 21/21 | descrito aquí; copias borradas |
| `test_gestante_registro.py`: cliente con `httpx.MockTransport` y rutas con un doble | 49/49 | `50_…log` |
| `test_gestante_frontend.py` (ampliado) | incluido | `50_…log` |
| **Suite offline completa, código final** | **2992 aprobadas, 0 fallos, 1042 omitidas** (todas de PostgreSQL, ninguna sin servidor), salida 0. Antes de R2 eran 2936: +56 = 49 del adaptador + 7 estáticas; las 21 de Node corren dentro de una sola prueba de pytest. | `50_offline_codigo_final.log/.xml` |

### Contra el backend real

Chrome headless → adaptador real (`gestante_web.py`, 8111) → proxy de prueba (8112) → API real (uvicorn, 8110) → PostgreSQL en la base desechable `scrum110_r2_20261105_2356_4e4e5f` (OID 59520), migrada al head y con el dataset cargado.

- **API efímera.** Corre como `fetalalert_api`, con `REGISTRO_MAXIMO_POR_ORIGEN=4`, para que el 429 llegue en el quinto envío.
- **Proxy.** Reenvía todo tal cual, salvo en dos casos que se indican: [proxy] reenvía un registro y corta la conexión sin devolver la respuesta; [caído] está detenido.
- **Dos pasadas.** La primera (registros `10`–`25`) se hizo antes de corregir el texto de ayuda de la fecha. La segunda, sobre el código final (`30`–`37`), usó cédulas y correos con sufijo `-F`. Las capturas son de la segunda.

| Paso | Qué se comprobó | Resultado (las dos pasadas) |
|---|---|---|
| e01/m01 | El inicio de sesión ofrece «Crear cuenta». En móvil no hay desbordamiento horizontal. | ✔ |
| e02/m02 | Formulario vacío y casillas desmarcadas; el foco va al título. En móvil, una columna. | ✔ |
| e03/m03 | El diálogo es modal (`:modal`), el foco va al texto (8 apartados) y Escape o «Cerrar» lo cierran devolviendo el foco al botón. Leer no marca la casilla. | ✔ |
| e04/m04 | Al enviar vacío, nada sale; se marcan los 8 obligatorios y las 2 casillas, y el foco va al primero. | ✔ |
| e05/m05 | Registro real → 201 → «Tu cuenta está lista». | ✔ |
| e06/m06 | Inicio de sesión de siempre con el correo precargado → portal vacío (0 embarazos). `localStorage` y `sessionStorage` vacíos; la única cookie es `fa_sesion`, HttpOnly y SameSite=Strict. | ✔ |
| e07 | Misma cédula con otro correo → 409 con el mensaje propio. Contraseñas vaciadas, cédula conservada. | ✔ |
| e08 | [proxy] La API confirmó con 201 y la respuesta se perdió. La pantalla dice «No sabemos si tu cuenta se creó» y no se reenvió nada. | ✔ |
| e09 | Esa cuenta «perdida» existía: el inicio de sesión de siempre entró. | ✔ |
| m07 | Quinto envío → 429. «Espera 15 minutos», el botón queda bloqueado y no hay reintento. | ✔ |
| e10 | [caído] Sin documentos: casillas deshabilitadas y «Volver a cargar los documentos». | ✔ |
| 503 | [caído] `POST /adaptador/registro` → 503 `{"enviado": false}`, y en la base no hay nada. | ✔ (`23`, `35`) |
| Consola | Errores de JavaScript en las dos pestañas | 0 |

En la base (`21`, `36`, `37`):

- **Seis cuentas** (E01, E02 y M01, más sus `-F`), cada una con:
  - perfil y un celular principal;
  - cuenta PACIENTE activa con hash `$argon2id$` y su vínculo;
  - 2 aceptaciones con versión `2026-11-v1` y las huellas `4ddd33c6…`/`11cfa66f…`;
  - 0 embarazos;
  - 1 fila de auditoría con la propia cuenta como actor.
- **Nada** de los intentos 409, 429 y 503.
- **El proxy registró** 201, 409, 201 PERDIDA, 201 y 429.

**Limpieza.** Se verificó el OID justo antes de eliminar la base y se eliminó solo esa (`40`–`42`). Las seis bases previas siguen. Roles y membresías quedaron idénticos (`44`). Al final: contenedor detenido, Docker Desktop apagado, puertos 5433/8110–8112 libres y ningún Chrome headless (`46`). Los perfiles de Chrome y el SQLite del adaptador estaban en el scratchpad y se borraron.

## 4. PR #22

`git merge-file` de tres vías (`51_…txt`): base `f5790d8`, igual a la del PR en estos archivos; R2; y `6bd303e`. Sale **0 conflictos** en `index.html`, `styles.css` y `rutas.py`, los únicos archivos que tocan las dos ramas. R2 no toca `app.js` ni `test_gestante_rutas.py`.

En un árbol combinado del scratchpad (PR #22 + R1/R2 + los tres fusionados), ya borrado (`52_…log`):

- Node: 137/138. La que falta se omite porque `git archive` no incluye `data/generated`.
- pytest de interfaz, adaptador y registro: 353 aprobadas.

No se fusionó ni se modificó el PR.

## 5. Corrección previa a R3: el 422 de `/adaptador/reautenticar` (aparte de R2)

**Confirmado.** `RUTAS_CON_CREDENCIALES` del adaptador no incluía `/adaptador/reautenticar`, cuyo cuerpo lleva la contraseña. El cuerpo se valida antes de mirar la sesión, así que no hace falta una sesión abierta. Con el código anterior (`60a_…log`), la prueba de regresión falló en sus dos casos: sin correo (el error adjunta el cuerpo entero) y con una contraseña de más de 512 caracteres (el error adjunta la contraseña). En los dos, el 422 devolvía la contraseña al navegador que la envió.

**Corrección mínima.** `/adaptador/reautenticar` se añade a `RUTAS_CON_CREDENCIALES` en `backend/app/gestante/aplicacion.py`, cada ruta en su propia línea. Se añade `test_el_422_de_reautenticar_tampoco_devuelve_la_contrasena` (2 casos) en `backend/tests/test_gestante_seguridad.py`. Con la corrección, las suites del adaptador dan 114 aprobadas (`60b_…log`) y la suite offline completa da 2994 aprobadas, 0 fallos y 1042 omitidas, todas de PostgreSQL (`61_…log`). Son las 2992 de R2 más las 2 regresiones nuevas.

**Identificada por separado.** `correccion_reautenticar.patch` contiene **solo** esta corrección y su prueba. Se aplica limpio sobre `f5790d8`; se comprobó en una copia temporal. Sirve para versionarla en su propio commit, antes o después de R1/R2, sin mezclarla con el cambio de R2 en la misma constante. No se amplió el alcance: no se tocó ninguna otra ruta ni el manejador del 422.

## 6. Pendiente

- **Pruebas de integración solo de CI, sin cambios desde R1:**
  - `test_migration_postgresql.py`: exige `scrum52_validacion_tmp`.
  - `test_bootstrap_postgresql.py`: no se ejecuta en el servidor compartido.
  - Las demás suites con guardián propio.
  No se han ejecutado y no se declaran aprobadas.
- **R3:** validación completa y retorno al baseline común con el mecanismo del PR #24. No se ejecutó.
- **Rotación de `fetalalert_dev`:** sigue pendiente y no autorizada.
