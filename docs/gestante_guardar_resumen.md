# Gestante: «Guardar mi resumen» y diagnóstico de desconexiones

Estado al 2026-10-17. Rama `feature/gestante-guardar-resumen-demo`, creada
desde `origin/main` en `029fe50` (merge del PR #20, interfaz gestante
aprobada) y actualizada con un merge de `origin/main` en `ccc209f` (PR #21,
SCRUM-103). Publicada en el PR #22 el 2026-10-11 (`0aab3ac`); el 2026-10-17
se añade la corrección de las descargas que dejan de ser vigentes, pedida en
la revisión (sección «Descarga vigente»). Sin merge.

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
4. Se abre la vista previa del documento. Arriba, **«Contenido del PDF»**:
   - **Resumen**: últimos valores y hasta 10 lecturas recientes (opción por
     omisión cada vez que se abre).
   - **Historial completo**: todas las lecturas del período elegido.
   La vista previa cambia al elegir.
5. **«Descargar PDF»** descarga el archivo directamente, sin diálogo de
   impresión: `FetalAlert-resumen-AAAA-MM-DD.pdf` o
   `FetalAlert-historial-AAAA-MM-DD.pdf` (fecha de Panamá; sin nombres ni
   identificadores). La página dice «Descarga iniciada: «…». Búscalo en la
   carpeta de descargas.»: el navegador decide dónde lo guarda y la página no
   puede confirmarlo.
6. Cada página del PDF lleva, pequeño, en la **esquina superior derecha**,
   el momento exacto de **esa** descarga, con segundos: «Descargado el
   10 oct 2026, 18:05:32 (hora de Panamá)». Se toma al pulsar «Descargar
   PDF»: dos descargas del mismo resumen llevan marcas distintas y el resto
   del documento es idéntico. En la primera página va en claro sobre la
   banda; en las siguientes, a la derecha de la cabecera.

### Decisiones (cambio sobre el planteamiento inicial del ticket)

El ticket pedía no presentarlo como descarga en un clic y evitar dependencias
nuevas. A petición de la usuaria (2026-10-10) se cambió a **descarga directa**
de un PDF **con la estética de la interfaz**, eligiendo entre resumen e
historial completo. Consecuencias aceptadas:

- **Dependencia nueva:** pdfmake 0.2.23 (MIT) con fuentes Roboto 3.014
  (SIL OFL 1.1), copiado sin modificar en `frontend/gestante/vendor/`
  (≈ 2,2 MB; detalle y sumas SHA-256 en `vendor/LICENCIAS.md`). Genera un PDF
  con **texto real** (seleccionable), no una imagen de la pantalla.
- **Ruta nueva en el adaptador** (`app/gestante/rutas.py`): `/resumen_pdf.js`,
  `/vendor/pdfmake.min.js` y `/vendor/vfs_fonts.js`, como rutas explícitas
  (no un directorio montado). Sin CDN: funciona sin internet.
- **Carga diferida:** el motor (≈ 2,2 MB) se pide al portal solo la primera
  vez que se pulsa «Descargar PDF», no en cada visita. Si falla, se dice
  («No se pudo preparar el PDF. Inténtalo de nuevo.») y se puede reintentar.
- **Papel Carta** fijo (el habitual en Panamá): una descarga no pregunta el
  tamaño. Con la vista abierta, Ctrl+P sigue imprimiendo solo el documento
  (`@media print`), en el papel que se elija.
- Sin motor de PDF en backend: el PDF se genera en el navegador.

### Cómo está hecho

- `app.js` arma un **modelo** del documento (`modeloDelResumen`): solo textos
  ya formateados, con las reglas de siempre (fechas en Panamá, cero frente a
  ausencia, semáforo de una sola lectura). Con ese modelo se pinta la vista
  previa (`createElement` + `textContent`, nunca `innerHTML`) y se genera el
  PDF: lo que se ve es lo que se descarga.
- `resumen_pdf.js` (nuevo, sin DOM) convierte el modelo en la definición de
  pdfmake con la estética de la interfaz: banda oscura con el degradado del
  encabezado y el logotipo en la primera página, franja morada y rosa,
  iconos del sprite en círculos de color, tarjetas con franja superior,
  bloque del semáforo con los colores del aviso (y siempre su nombre),
  tabla con cabecera lila repetida en cada página, filas alternas y filas que
  no se parten; cabecera discreta y «Página n de m» en todas las páginas.
- El documento sale de la **instantánea** del monitoreo que ya cargó «Mi
  historial»: no hace peticiones de datos nuevas ni guarda nada en el
  navegador. Una descarga que deja de corresponder a lo que hay en pantalla
  no se entrega (sección siguiente).

### Descarga vigente

Cada pulsación de «Descargar PDF» pertenece al resumen que estaba en pantalla
en ese momento: su contenido, su embarazo, su nombre de archivo y su marca de
descarga. Mientras se prepara (carga del motor y generación), **cualquiera de
estas acciones la deja sin efecto, en el acto**:

- cambiar entre «Resumen» e «Historial completo»;
- salir de la vista previa («Volver a «Mi historial»» o el menú), aunque
  luego se vuelva a abrir el mismo resumen o el de otro embarazo;
- **confirmar** el cierre de sesión, sin esperar la respuesta del servidor
  (también si esa petición falla). Pulsar «Cancelar» en el diálogo no la
  afecta: la descarga sigue.

Al invalidarse, el botón queda libre para pedir el contenido nuevo, y nada de
la descarga anterior (ni su error ni su final) cambia la pantalla ni los
controles de otra posterior. Cómo:

- Un turno `resumen` en `turnos` (`app.js`), el mismo mecanismo que ya usan
  las peticiones. Avanza al abrir, cambiar o cerrar el resumen
  (`invalidarDescarga`), y al confirmar el cierre de sesión, porque
  `manejarLogout()` ya avanza todos los turnos antes de llamar al servidor.
  La descarga anota el turno, la época de la sesión y el resumen abierto al
  pulsar; el resumen es un objeto nuevo en cada apertura que nada modifica
  después, así que no hace falta copiarlo.
- El PDF se genera **en memoria**: `createPdf(...).getStream()` arma el
  documento y sus trozos se reúnen en un `Blob`. Después, **sin nada
  asíncrono en medio**, se comprueba otra vez que la solicitud siga vigente
  y solo entonces se entrega al navegador con un enlace `download` y una
  dirección `blob:` temporal, que se libera a los 40 s (la misma espera que
  usa FileSaver dentro de `download()` de pdfmake).
- Se dejó de usar `download()` / `getBlob()` porque en pdfmake 0.2.23 generan
  dentro de una promesa propia y, si la generación falla, el error se pierde
  sin avisar (comprobado con el pdfmake incluido): la página decía
  «Descarga iniciada» sin descargar nada. Con `getStream()` el error llega y
  se muestra «No se pudo preparar el PDF. Inténtalo de nuevo.».

Qué se garantiza y qué no:

- **Se cancela la entrega** de un resultado pendiente: si la solicitud ya no
  es vigente, el PDF generado se descarta y no llega al navegador.
- **No se detiene** el trabajo interno de pdfmake: una generación ya
  empezada puede terminar en memoria (y se descarta).
- **No se retira** un archivo ya entregado al navegador. La comprobación
  final está inmediatamente antes de entregarlo; lo que ocurra después (la
  descarga en sí) depende del navegador.
- La ventana no se limita a la primera descarga: en Chrome, del clic al
  inicio de la descarga se midieron unos 780 ms con el motor sin cargar y
  unos 200 ms con el motor ya cargado (generación), en el equipo de prueba.

### Qué contiene

| Bloque | Alcance |
|---|---|
| Marca FetalAlert, título y el embarazo («Embarazo desde 6 ene 2025 — Finalizado») | El embarazo elegido en «Mi historial». Nunca el de Inicio si son distintos. Título: «Resumen de mis registros» o «Historial de mis registros». |
| Datos consultados el / Resumen (o Historial) generado el | Consulta = cuándo llegó al navegador el monitoreo usado. Generación = cuándo se armó el documento. Son fechas distintas. |
| Embarazo: estado, inicio, FPP y fecha de cierre (solo si existen) | Del episodio. |
| «Semana actual (al <hoy>)» | Solo para el embarazo en curso y solo si el adaptador publica `semana_actual`. En cualquier otro caso (finalizado, ambiguo, FPP vencida): «Semana en el último registro», con su fecha. |
| Últimos valores registrados (FC, SpO₂, movimientos) | De **todo el embarazo**, uno por variable, cada uno con su fecha, hora y semana. Pueden venir de lecturas distintas, y así se dice. Sin semáforo propio. |
| Clasificación de la lectura más reciente | El `codigo_semaforo` de la fuente autorizada para **una** lectura, con su fecha y lo que midió. Texto explícito: no se aplica a cada medición ni a los últimos valores. Un código desconocido es «Sin clasificación disponible», sin color. |
| Lecturas recientes / Todas las lecturas del período | Resumen: hasta **10**; historial completo: todas. Del período elegido, de la más reciente a la más antigua, cada una con su clasificación (color + nombre). Se indica el período con sus fechas, cuántas se incluyen y de cuántas. |
| Marca de descarga (esquina superior derecha, todas las páginas) | El instante en que se pulsó «Descargar PDF», en hora de Panamá y con segundos. Distinto de «generado el», que es cuándo se armó la vista previa. |
| Pie | Horas de Panamá; «—» = valor no registrado, el cero sí es un valor; datos simulados; no sustituye la valoración del personal de salud. |

### Límites

- **Cobertura de los datos.** El servidor central no limita ni pagina
  `/embarazos/{id}/sesiones` ni `/sesiones/{id}/lecturas`
  (`services/consulta_clinica.py`), y el adaptador responde error en vez de
  un historial parcial si falla cualquier sesión. Por eso el documento puede
  hablar de «las N lecturas más recientes de las M del período» o de todas.
- **«Últimos 30 días con registros»** se cuenta hacia atrás desde la lectura
  más reciente del embarazo (la misma idea que las gráficas, que lo cuentan
  por variable desde el último registro de cada serie). El documento escribe
  las fechas del período.
- **Sin conexión.** Si el monitoreo ya estaba cargado, se puede generar y
  descargar; si la última comprobación no confirmó la conexión, el documento
  lo dice: «Los datos son los de la última consulta y podrían no incluir
  registros posteriores». No se puede tras recargar sin API (no hay datos en
  memoria ni almacenamiento persistente, por diseño).
- **Sin lecturas** no se genera un documento: «Este embarazo todavía no tiene
  lecturas registradas, así que no hay un resumen que guardar.»
- **No se muestran** identificadores (de lectura, sesión, embarazo o
  dispositivo), códigos internos (`OK`, `WARNING`…), claves de idempotencia
  ni tokens; tampoco en el nombre del archivo ni en los metadatos del PDF.
- Sin gráficas en el documento: el texto debe poder seleccionarse y leerse
  sin color.

### Pruebas ejecutadas

Node (`frontend/gestante/pruebas/`):

- `app.comportamiento.test.js`, sobre el `app.js` real con DOM mínimo y un
  pdfmake falso que guarda la definición. Desde la corrección de «Descarga
  vigente», el falso genera de forma asíncrona y controlable
  (`pdf.diferir`/`terminar()`, `pdf.fallar`), y una descarga solo cuenta si
  se pulsó el enlace `download` con la dirección `blob:` de un PDF generado:
  se comprueba la **entrega al navegador**, no la llamada a `createPdf()`:
  - habilitación solo con lecturas cargadas; deshabilitado al cambiar de
    embarazo y con error del servidor;
  - correspondencia con el embarazo de Historial y no con el de Inicio;
  - semana actual solo en el embarazo en curso; finalizado y FPP vencida
    usan la del último registro;
  - variables con fechas distintas y semáforo de una sola lectura, con su
    alcance; código desconocido;
  - cero frente a «Sin registros» y «No disponible»; sin lecturas;
  - límite de 10, orden y cobertura; período de 30 días;
  - fecha de consulta frente a generación; aviso sin conexión;
  - respuesta tardía de otra selección; cierre de sesión y cambio de cuenta;
  - foco al título y vuelta al botón;
  - **descarga**: nombre del archivo, mismos textos y filas que la vista
    previa, sin identificadores; «Historial completo»; el motor se pide al
    portal la primera vez y en orden; cerrar sesión durante la carga no
    descarga nada; error de carga y reintento; marcado como texto; cada
    descarga lleva su propio momento exacto (dos descargas con el reloj
    adelantado 2 min 17 s dan «10:00:00» y «10:02:17») en todas las páginas.
  - **descarga vigente** (12 pruebas, con la carga del motor y la generación
    retenidas por promesas que la prueba suelta, sin esperas): entrega desde
    memoria y dirección liberada a los 40 s, no antes; cambio a «Historial
    completo», salir y reabrir el mismo resumen, y abrir el de otro
    embarazo durante la carga: ninguna entrega de la anterior, y la nueva
    lleva su contenido, nombre y marca; confirmar el cierre con la petición
    pendiente y con fallo de red: nada se entrega; cancelar el diálogo: la
    descarga sigue; el error de una descarga anterior no aparece en el
    resumen nuevo; el final de la anterior no habilita el botón de la que
    está en curso y ambas comparten una sola carga del motor; con el motor ya
    cargado, cambiar, salir o confirmar el cierre **después de generado el
    PDF y antes de entregarlo** impide la entrega; un fallo de generación se
    dice y se puede reintentar; otra cuenta que entra no recibe la descarga
    pendiente de la anterior (en el escenario probado).
- `resumen_pdf.test.js` (nuevo): papel Carta y metadatos; todos los textos
  del modelo llegan tal cual; tabla con cabecera repetida, filas sin partir y
  6 columnas; semáforo siempre con nombre y sin color si es desconocido;
  aviso solo si existe; banda solo en la primera página y numeración en
  todas; el título de la tabla no queda solo al pie; ningún dato entra en un
  SVG; la marca de descarga va arriba a la derecha, pequeña y clara sobre
  la banda.

pytest: `test_gestante_rutas.py` comprueba que se sirven `/resumen_pdf.js` y
los dos archivos de `/vendor/`, y que cualquier otra ruta (por ejemplo
`/vendor/LICENCIAS.md`) da 404.

Resultados sobre el código final (commit `6265886`, que integra
`origin/main`; el commit posterior solo cambia documentación y muestras), en
un checkout limpio sin `.env` y sin `data/generated`, como el CI:

| Suite | Base `029fe50` | Rama (`6265886`) |
|---|---|---|
| `node --test frontend/gestante/pruebas/*.test.js` | 73 pass, 1 skip | 104 pass, 1 skip |
| `cd backend; python -m pytest -q` con los mismos `--ignore=tests/*_postgresql.py` del primer paso del CI | 2449 passed | 2469 passed, 0 skipped |

- La única omisión de Node es preexistente: la prueba del dataset se omite si
  `data/generated` no está generado (con él generado: 105 pass, 0 skip).
- La base y la rama no son comparables uno a uno: la rama incluye ya las
  pruebas que trajo `origin/main` (SCRUM-103).
- En el worktree de trabajo, que tiene un `.env` local, pytest omite 53
  pruebas de `test_config_secretos.py` que exigen que no exista: por eso la
  medida válida es la del checkout limpio.
- Las suites `*_postgresql.py` no se ejecutaron localmente (el único cambio de
  backend son rutas de archivos estáticos); las ejecuta el CI del PR.

`test_gestante_frontend.py` prohíbe cifras de umbral clínico en `app.js`; la
constante del día se escribe como `86400000` por eso.

### Revisión visual (Chrome headless)

Con Chrome headless y `playwright-core` instalado **fuera** del repositorio,
contra un servidor local que sirve los archivos del frontend y responde
`/adaptador/*` con datos ficticios (32 lecturas en el embarazo finalizado).
La versión anterior (impresión del navegador) se probó además contra el
portal real de la parte B; la descarga directa, solo contra el servidor
local:

- Inicio idéntico **píxel a píxel** a la versión base en 1366 px y 390 px;
  Historial igual salvo el botón nuevo.
- Botón alcanzable con Tab y activable con Enter; el foco pasa al título del
  documento; al volver, al botón.
- Sin desplazamiento horizontal a 1366 y 390 px; en pantallas estrechas la
  tabla de la vista previa se apila como la del historial.
- **Descarga real** con pdfmake en Chrome (evento de descarga del navegador):
  Carta; resumen en **2 páginas** y historial completo (32 lecturas) en
  **3 páginas**; fuentes Roboto incrustadas; texto extraíble, incluido
  «SpO₂»; cabecera de la tabla repetida; ninguna fila partida ni cabecera
  sola al pie; el cero como «0»; cada color con su nombre.

Muestras versionadas en `docs/evidencias/gestante-guardar-resumen/` (datos
ficticios del servidor local de prueba, generadas desde `6265886`):
`muestra-resumen.pdf`, `pdf-resumen-p1.png`,
`pdf-historial-completo-p2.png` y `vista-previa-escritorio.png`.

### Corrección «Descarga vigente»: resultados (2026-10-17)

**Pruebas automáticas** (Node 22.23.1; pytest con el `.venv` del proyecto),
en un worktree limpio sin `.env` ni `data/generated`, como el CI:

| Suite | Resultado |
|---|---|
| `node --test frontend/gestante/pruebas/*.test.js` | 117: 116 pass, 0 fail, 1 skip (la del dataset, como antes) |
| `cd backend; python -m pytest -q` con los `--ignore` de la DoD | 2469 passed, 0 failed, 545 skipped |

- Las 545 omitidas son suites de PostgreSQL de SCRUM-98/99 (`test_rls`,
  `test_publicacion*`, `test_http_como_api`, `test_bootstrap`) que piden
  variables `SCRUM98_*`/`SCRUM99_*` y no están en la lista de la DoD; las
  ejecuta el CI. No las toca este cambio, que es solo de frontend.
- **Las regresiones detectan el defecto:** las mismas pruebas contra el
  `app.js` de `0aab3ac` (copia aislada; su `download()` simulado con el
  comportamiento real: genera asíncrono, entrega con un enlace y pierde los
  errores) dan 105 pass y **11 fail**: entregas indebidas al cambiar de
  embarazo, al confirmar el cierre (pendiente y con fallo de red) y con el
  motor ya cargado; el error antiguo en la vista nueva; el final antiguo que
  libera el botón de otra descarga; «Descarga iniciada» con la generación
  fallida; el botón bloqueado al cambiar de contenido o reabrir (donde la
  entrega indebida ya se había reproducido en el diagnóstico); y el botón de
  otra cuenta bloqueado por la descarga anterior (sin que pasara ningún
  dato). La undécima (liberar la dirección a los 40 s) solo refleja que esa
  versión no gestionaba la dirección por sí misma.

**Chrome real** (Chrome 155 headless, perfil propio desechable, conducido
por CDP; clics de ratón con `Input.dispatchMouseEvent`; descargas
observadas con los eventos del navegador y leídas después con pypdf).
pdfmake es **el real** del portal en todos los casos. El portal es un
servidor local de prueba que sirve los mismos archivos que `rutas.py` y
simula `/adaptador/*` con datos ficticios; **no** es el adaptador real ni la
base de la demo. Lo que controla los tiempos está **instrumentado**: [red]
el servidor retiene la carga de `pdfmake.min.js` o la petición de cierre de
sesión hasta soltarlas; [gen] en la página se retiene el evento final del
stream de pdfmake, con el PDF ya generado en memoria.

| Escenario | Instrumentación | Resultado con la corrección |
|---|---|---|
| Descarga normal, motor sin cargar y ya cargado; «Historial completo»; embarazo finalizado | ninguna | 4 PDF correctos (`%PDF-`, 2 páginas, texto extraíble): título, embarazo, 10 / 15 / 3 lecturas y marca de cada uno |
| Cambio a «Historial completo» durante la carga | [red] | 0 entregas de la anterior; botón libre; la nueva: historial, 15 lecturas |
| Salir y abrir el resumen del embarazo finalizado | [red] | 0 entregas de la anterior; la nueva: «6 ene 2025 — Finalizado» |
| Salir y reabrir el mismo resumen | [red] | 0 entregas de la anterior |
| Confirmar el cierre con la petición pendiente | [red] | 0 entregas antes ni después del login; nueva sesión: 1 descarga |
| Confirmar el cierre y la petición se corta | [red] | 0 entregas; vuelve al login |
| Cancelar el diálogo de cierre | [red] | la descarga sigue: 1 PDF correcto |
| Motor cargado; cambio de contenido / otro embarazo / cierre confirmado con el PDF ya generado y aún no entregado | [gen] (+[red] en el cierre) | ese PDF no se entrega; la nueva descarga sí, con su contenido |
| La carga del motor falla (503) | [red] | «No se pudo preparar el PDF…», botón libre, 0 entregas; el reintento descarga |

La misma batería contra `0aab3ac` mostró en Chrome «Descarga iniciada» en
los casos de cambio de contenido, de embarazo, de reabrir y de cierre
confirmado, nada más cargar el motor y antes de pedir nada nuevo. Con el
corte de la petición de cierre, la descarga ya existía antes de volver al
login. Cortar la conexión de un `<script>` no basta para simular un fallo
del motor: Chrome reintenta la petición; por eso se usa un 503. El aspecto
de la vista previa y del PDF no cambia (este cambio no toca `styles.css`,
`index.html` ni `resumen_pdf.js`).

Scripts para repetirlo en
`docs/evidencias/gestante-guardar-resumen/validacion-descarga/`
(desde esa carpeta, con salida **fuera** del repositorio):

```
node servidor.js ../../../../frontend/gestante 8765
node conductor.js http://127.0.0.1:8765 <dir de salida fuera del repo>
<venv con pypdf>/Scripts/python -I -X utf8 verificar_pdf.py <dir de salida>
```

Solo escuchan en `127.0.0.1` y no usan la base, el `.env` ni los servicios de
la demo. Resultados, PDF descargados y capturas de esta ejecución (datos
ficticios) quedaron fuera del repositorio, en
`C:\GitHub\TESIS\evidencias\pr22-correccion-descarga-2026-10-17\`.

### Verificación manual (Viviana, 2026-10-11)

Confirmado por Viviana en el portal de demostración, **sobre `0aab3ac`**,
antes de la corrección «Descarga vigente»:

- La descarga funciona en **Google Chrome**.
- El PDF descargado **abre sin internet**.

No se declara ninguna otra comprobación manual, y esta no cubre la
corrección: la nueva forma de entregar el archivo (enlace `blob:`) no tiene
todavía verificación manual.

### Pendientes

- **Verificación manual de la corrección** en el portal de demostración con
  Chrome: descargar «Resumen» e «Historial completo» y abrir ambos PDF sin
  internet; pulsar «Descargar PDF» y, antes de que termine, cambiar a
  «Historial completo», o volver a «Mi historial», o confirmar el cierre de
  sesión, y comprobar en la carpeta de descargas que no aparece el archivo
  de la descarga anterior. Sin red lenta la ventana es breve, así que el
  arnés automático sigue siendo la prueba determinista.
- El diálogo de cierre deja pulsar «Cancelar» mientras la petición de cierre
  está pendiente (comportamiento previo, de SCRUM-72): se registra como
  pendiente aparte. La descarga ya queda invalidada en ese caso.
- Otros navegadores (Edge, Firefox) y móvil real: no probados.
- Impresión en papel del PDF descargado: no comprobada.

No se declara conformidad completa de accesibilidad: solo se comprobó
teclado, foco, etiquetas de texto junto al color y HTML semántico. El PDF no
lleva etiquetas de accesibilidad (PDF/UA): pdfmake no las genera.

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
| PostgreSQL | 16.2 (binarios del paquete `pgserver`, en un entorno virtual temporal), clúster nuevo con `initdb`, `127.0.0.1:55433`, base `diag_desconexion_tmp` |
| Roles | migrador sin SUPERUSER ni BYPASSRLS, `fetalalert_api/etl/powerbi`, todos con claves ficticias; `bootstrap_roles.py aplicar`, `alembic upgrade head` |
| Datos | `generate_mock_data.py` + `load_mock_data.py` (1180 lecturas, `APP_ENV=test`); `provisionar_demo.py provisionar` → cuenta 107, embarazo 130, dispositivo 130 |
| API | `uvicorn app.main:app` en `127.0.0.1:8031`, `JWT_EXPIRATION_MINUTES=1` (para observar el vencimiento) |
| Portal | `scripts/gestante_web.py` en `127.0.0.1:8131`, `GESTANTE_ENVIO_AUTOMATICO_SEGUNDOS=5` (por omisión, 30) |
| Cola y sesión local | SQLite en una carpeta temporal (`GESTANTE_MOVIMIENTOS_DIR`, `GESTANTE_SQLITE_PATH`, `GESTANTE_PROVISION_PATH`) |

### Procedimiento reproducible

1. Clúster desechable: `initdb -D <dir> -U <admin> -A scram-sha-256`, y
   `pg_ctl -D <dir> -o "-p 55433 -c listen_addresses=127.0.0.1" start`.
2. Roles y base como el paso «Crear el migrador de CI» de `ci.yml`; después
   `python scripts/bootstrap_roles.py aplicar` y, en `backend/`,
   `alembic upgrade head`, con `ALEMBIC_DATABASE_URL` del migrador.
3. `python scripts/generate_mock_data.py`, `python scripts/load_mock_data.py`
   (`APP_ENV=test`) y `python scripts/provisionar_demo.py provisionar`.
4. Arrancar API y portal con las variables de la tabla. Un guion
   (`escenarios.py`, en la copia local de evidencias) los arranca y detiene,
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

### Evidencia conservada

- **En el repositorio** (accesible para todo el equipo): esta nota y las
  muestras de `docs/evidencias/gestante-guardar-resumen/`.
- **Copia local**, solo en la computadora de Viviana y **no accesible para
  otras colaboradoras**: `C:\GitHub\TESIS\evidencias\gestante-guardar-resumen-2026-10-10\`,
  con su propio `README.md`. Contiene los resultados de los 7 escenarios
  (`diagnostico/evidencia.json` y `evidencia.log`), los guiones
  (`escenarios.py`, `crear_roles.py`, `entorno.ejemplo.sh` con marcadores en
  lugar de claves, `portal_real.js`, `secuencia_recarga.js`,
  `revision_resumen.js`), las capturas, los PDF de muestra y la reproducción
  del defecto preexistente (`defecto-preexistente/repro_nota_sin_conexion.test.js`).
- No se conservan el clúster PostgreSQL ni las colas SQLite, ni ningún
  `.env`. Las claves del entorno aislado eran ficticias y desechables.
