# SCRUM-72 — Etapa 2 (interfaz y gráficas): resumen

Estado al 2026-09-25. Continúa `docs/scrum72_etapa1_resumen.md`. SCRUM-72
**no está cerrado**: ver «Pendientes» al final.

## Qué cambió

- **Diseño** (`frontend/gestante/index.html`, `styles.css`): encabezado
  compacto con el logotipo tipográfico, el eslogan, las secciones y el área de
  cuenta; Inicio en bloques («Embarazo actual», «Tus últimos registros»,
  «Tu lectura más reciente», «Tus registros en el tiempo», «Sesión de
  movimientos»); iconos SVG propios en el mismo HTML. Misma paleta y mismas
  reglas de identidad. Foco visible con `outline` (no lo tapan las sombras
  de los botones) y `prefers-reduced-motion` sin animaciones.
- **Gráficas** (`frontend/gestante/graficas.js`, servido por la nueva ruta
  explícita `/graficas.js` del portal): SVG propio sin dependencias. Consumen
  `datos.series` del adaptador (etapa 1). Eje temporal proporcional, una marca
  por registro, líneas rectas entre observaciones (FC, SpO₂), una barra por
  registro (movimientos), sin agregación, interpolación ni bandas clínicas.
  Período «Todo el embarazo» o «Últimos 30 días con registros», contado desde
  el último registro. Detalle con ratón, toque y teclado (flechas, Inicio,
  Fin; un solo punto en el orden de tabulación) y tabla alternativa.
  Ajuste visual posterior: línea más gruesa con relleno suave debajo, puntos
  más grandes con borde blanco, barras anchas con esquinas redondeadas (solo
  se estrechan las de registros muy cercanos, para no taparse) y la unidad
  en vertical en el eje. El valor se escribe sobre cada marca solo si caben
  todos; si no, se consulta tocando la marca o en la tabla. El resumen sobre
  cada gráfica va en tres líneas: período, fechas y número de registros.
- **Historial**: resumen con el número de lecturas del episodio, las tres
  gráficas (cada una con su número de registros) y la tabla completa en un
  desplegable.
- **Fechas**: «29 may 2026, 03:27», en español y hora de Panamá; las de
  calendario no se mueven de día.
- **Indicador**: textos breves («Conectada al servidor», «Vuelve a iniciar
  sesión», «Acceso no autorizado», «Sin conexión con el servidor», «Error del
  servidor», «No se pudo comprobar la conexión»), misma lógica de la etapa 1.
- **Registro**: el botón dice «Guardando…» y queda deshabilitado hasta la
  respuesta; una segunda pulsación no crea otro registro.
- **Manual**: nueve pasos de una acción, cada uno con un recorte de la
  interfaz que marca dónde tocar, y el significado de los colores. Sin
  sensores, sin ESP32, sin contador manual.
- **Acerca de**: propósito, función de la interfaz frente a Power BI,
  guardado y envío, acceso autorizado (incluye al personal de salud
  autorizado), datos simulados y alcance. La simulación se explica solo aquí.

Sin cambios en la API central, migraciones, permisos, reglas clínicas,
generador, dataset ni fechas. El refresco periódico sigue sin releer datos
clínicos.

## Escenarios de demostración y procedencia de los datos

Revisión del 2026-09-25, sin cambios de código: la implementación ya
separaba los escenarios; esta sección lo deja escrito.

### Dataset canónico de la tesis frente a la base de demostración

| | Contenido | Uso |
|---|---|---|
| **Dataset canónico de la tesis** | `data/generated`, generado por `scripts/generate_mock_data.py`: **30 embarazos, 732 sesiones y 1180 lecturas**. | Única fuente para las estadísticas del dataset, el ETL, Power BI y los resultados cuantitativos de la muestra. |
| **Base / escenario de demostración de SCRUM-72** | El dataset canónico cargado, **más** el embarazo técnico que agrega `scripts/provisionar_demo.py` a `paciente01` (embarazo 130, dispositivo `DEMO-SCRUM72-01`, su asignación y su seguimiento) y las sesiones de movimientos registradas desde el portal para las pruebas. | Solo la demostración de la interfaz de la gestante y del flujo sin conexión. |

Los registros del escenario de demostración **no forman parte de la muestra
canónica** y no deben usarse para estadísticas del dataset, ETL, Power BI ni
resultados cuantitativos. Ni el generador ni el dataset se modificaron para
montar la demostración.

Verificado el 2026-09-25:

- `data/generated`: 30 / 732 / 1180; ningún registro del embarazo 130.
- `scrum72_demo_gestante`: 31 / 733 / 1181 en total; sin el embarazo 130,
  30 / 732 / 1180. Las lecturas de los embarazos 100 y 129 son idénticas, fila
  a fila, a las del CSV.
- `scrum72_prueba_offline` (la base de las pruebas que escriben): el canónico
  sigue en 30 / 732 / 1180; todo lo nuevo está en el embarazo 130.
- Base desechable cargada solo con `data/generated` y aprovisionada por la
  suite `test_provisionar_demo_postgresql.py`: pasa de 30 / 732 / 1180 a
  31 / 732 / 1180. El aprovisionamiento solo agrega el embarazo técnico, sin
  sesiones ni lecturas.
- Ninguna sesión del dataset tiene fila en `idempotencia_solicitud`; todas
  las del embarazo 130 la tienen y usan el dispositivo 130, que no existe en
  el dataset. Así se distingue en la base qué vino del portal.

**Advertencia sobre el ETL.** En las dos bases de revisión, el esquema
`analitico` se construyó cuando el embarazo 130 ya existía:
`dim_embarazo` tiene 31 filas (incluye el 130) y `fact_lectura_biometrica`
tiene 1180, ninguna del 130. Si se vuelve a ejecutar el ETL sobre esas bases,
incorporaría también las sesiones sincronizadas del 130. Por eso los
resultados analíticos y Power BI de la tesis deben salir de una base cargada
únicamente con `data/generated` y sin ejecutar `provisionar_demo.py`, nunca
de `scrum72_demo_gestante` ni de `scrum72_prueba_offline`. El ETL no se
modificó.

### `paciente30@example.com`: escenario principal con datos canónicos

Embarazo 129 del dataset (`ACTIVO`, inicio 24 sept 2025, FPP 1 jul 2026). No
tiene embarazo técnico ni está aprovisionada para capturar: el portal rechaza
(404) una captura simulada en su embarazo. Todo lo que muestra Inicio sale de
sus lecturas autorizadas, a través de la API central y del adaptador; el
frontend no tiene constantes de valores clínicos:

- FC 83 y SpO₂ 96: lectura 549 (29 may 2026, 03:27, semana 36).
- Movimientos 7: lectura 1259 (21 jun 2026, 09:56, semana 39).
- «Semana en el último registro 39 (21 jun 2026)»: la FPP ya pasó en el
  calendario real, así que no se presenta una semana actual.
- Semáforo Amarillo: el de la lectura más reciente (1259).
- Gráficas: 30, 30 y 20 registros («Todo el embarazo») y 10, 10 y 5
  («Últimos 30 días con registros»). Cada punto es una lectura de la base, con
  su fecha y su valor: no se inventan, interpolan, acumulan ni sustituyen
  puntos. Historial muestra solo el embarazo 129.

### `paciente01@example.com`: continuidad longitudinal entre dos embarazos

| | Embarazo anterior | Embarazo actual |
|---|---|---|
| Id | 100 | 130 |
| Origen | Dataset canónico | Fixture técnico de `provisionar_demo.py` |
| Estado y fechas | `FINALIZADO`, inicio 6 ene 2025, FPP 13 oct 2025 | `ACTIVO`, inicio 19 mar 2026 (semana 28 el día del aprovisionamiento) |
| Datos | 41 lecturas históricas en 25 sesiones (FC 20, SpO₂ 20, movimientos 21), las del dataset | Solo las sesiones de movimientos registradas desde el portal |
| Dónde se ve | Historial, al elegirlo en el selector | Inicio; también en Historial |

El embarazo 130 existe solo para tener una gestación válida en el reloj real
(los episodios del dataset quedaron fuera del rango de semanas que admite la
ingesta) y poder ejecutar la captura sin conexión. No forma parte de la
muestra canónica.

Comprobado en la interfaz: Inicio del 130 muestra FC y SpO₂ «Sin registros»
(no toma nada del 100) y ninguna fecha de 2025. El Historial del 100 muestra
sus 41 lecturas, idénticas al CSV, sin ninguna fila de 2026 ni la captura
simulada. Ir y volver entre 100 y 130 no mezcla filas, e Inicio queda igual.

### `MOV_SIMULADO = 12`: una nueva captura simulada

`MOV_SIMULADO = 12` (`backend/app/gestante/simulacion.py`) es el valor fijo
de **una nueva captura simulada** durante la demostración. **No es una lectura
histórica del dataset ni una medición**: se conserva a propósito, porque
reemplazarlo por una lectura del dataset haría pasar un dato antiguo por una
medición nueva.

- Solo se usa en `construir_paquete_simulado`, cuando la gestante pulsa
  «Registrar sesión de movimientos» en Inicio.
- Solo puede ir al embarazo para el que está aprovisionado el dispositivo
  (`Provision.sirve_a`: cuenta 107, embarazo 130). Una captura contra el 100 o
  contra el 129 de otra cuenta se rechaza (404) sin dejar nada en la cola.
- Recorre el mismo camino que cualquier captura: SQLite local de la cuenta,
  outbox, envío manual, `Idempotency-Key` y sesión en la base con
  `id_dispositivo` 130.
- Las lecturas de movimientos del embarazo 100 siguen siendo las del dataset
  (21 valores idénticos al CSV).
- La interfaz no lo atribuye al dataset: «Acerca de» dice que las sesiones
  registradas desde Inicio «usan valores simulados fijos; no son una
  medición».

### Prueba sin conexión (paciente01, entorno aislado)

API 8011 con JWT de 1 minuto, portal 8101 y base `scrum72_prueba_offline`;
la API 8010, el portal 8100 y `scrum72_demo_gestante` no se tocan.
30/30 comprobaciones:

1. Inicio usa el embarazo actual 130; el 100 queda entre los anteriores.
2. Con la API detenida, el indicador dice «Sin conexión con el servidor».
3. y 4. «Registrar» muestra «Guardando…» con el botón deshabilitado (~2,7 s)
   y termina en «Registro guardado en este dispositivo, pendiente de envío.».
5. SQLite de la cuenta: una captura `PENDIENTE` del embarazo 130 y del
   dispositivo 130, movimientos 12, FC y SpO₂ nulos. La base no cambia.
6. Recargar la página y reiniciar el portal sin API conserva la sesión local
   y el pendiente. Al volver la API se pide «Vuelve a iniciar sesión»; tras
   reautenticar, el pendiente sigue ahí.
7. La segunda pulsación durante el guardado no duplica: una sola captura.
8. y 9. Con la API recuperada, «Enviar ahora» responde «Registro enviado.».
10. La outbox queda `ENVIADO`, con su id de sesión remota, y aparece «Último
    envío confirmado». En la base hay exactamente +1 sesión, +1 lectura y
    +1 clave de idempotencia, todas en el 130. Sin pendientes, «Enviar ahora»
    se oculta. Una ronda forzada no entrega nada, y reenviar el mismo paquete
    con la misma `Idempotency-Key` responde 201 `Idempotency-Replayed: true`
    con la misma sesión, sin filas nuevas.
11. Los embarazos 100 y 129 conservan la misma huella MD5 de sus lecturas,
    el canónico sigue en 30 / 732 / 1180 y el Historial del 100 sigue con
    41 lecturas.

El envío es manual («Enviar ahora»), no automático.

## Verificación

- Node: `app.comportamiento.test.js` y `graficas.test.js` (proporcionalidad,
  sin puntos inventados, cero frente a nulo, período desde el último
  registro, casos vacío y único). El envoltorio de pytest ejecuta ahora todos
  los `*.test.js`, igual que el paso de Node del CI.
- pytest de la gestante (incluye la ruta `/graficas.js`).
- Chrome headless contra 8100, solo lectura: capturas de las cinco vistas a
  1280, 390 y 360 px sin desbordes; gráficas de `paciente30` iguales fila a
  fila a las series autorizadas; `paciente01` con 41 lecturas del embarazo
  100 y la lectura del 8 sept 2025 (86 BPM, 97 %); teclado, toque, período,
  movimiento reducido y diálogo de cierre; cambio de cuenta en la misma
  pestaña con una respuesta tardía retenida de la cuenta anterior, que no
  pinta nada; tabla del embarazo 100 desplegada a 360 px sin recortes.
- Entorno aislado (API 8011 con JWT de 1 minuto, portal 8101, base
  `scrum72_prueba_offline`): vencimiento, reautenticación, caída, «Guardando…»,
  recuperación, envío y corte de red, también a 360 px.

## Tesis: apartados a revisar

**Limitación:** el documento de la tesis no se adjuntó a esta etapa; en el
equipo hay varias versiones (anteproyecto, borradores «TESIS V2», artículo)
y no se sabe cuál es la vigente, así que no se leyó ni se cita ninguna. Lo
que sigue son redacciones propuestas a partir de lo implementado, para
ubicarlas donde la tesis describa la interfaz de la gestante. No sustituyen
una revisión contra el texto real.

1. **Interfaz de la gestante.** «La gestante consulta sus registros desde un
   portal local que corre en su dispositivo. El portal no accede a la base de
   datos: consulta la API central con la sesión de la propia gestante, que
   solo le entrega su información. Muestra, por separado, el último valor
   registrado de cada medición con su fecha, la clasificación de la lectura
   más reciente y gráficas de los registros de cada embarazo. El análisis
   para el personal médico autorizado se realiza aparte, en Power BI, sobre
   una capa seudonimizada.»
2. **Funcionamiento sin conexión.** «Las sesiones de movimientos se guardan
   primero en el dispositivo y se envían al servidor cuando la gestante lo
   solicita. El reenvío no duplica registros. Sin conexión puede seguir
   consultando lo ya mostrado en la sesión y guardar registros, pero no
   enviarlos ni actualizar su información. El envío no es automático.»
3. **ESP32.** El ESP32 pertenece al antecedente tecnológico del proyecto y
   no es hardware desarrollado en esta tesis. El MVP no integra hardware:
   las capturas son simuladas y el dataset es sintético.
4. **Acceso.** Evitar «solo tú puedes acceder»: la gestante accede solo a su
   información, y el servidor también la pone a disposición del personal de
   salud autorizado para su seguimiento, con control por roles y aislamiento
   por filas.
5. **Alcance.** Prototipo académico con datos simulados; sin validación
   clínica, diagnóstico ni monitoreo continuo; el manual aplica criterios de
   accesibilidad (una acción por paso, texto breve, iconos con texto), pero no
   se evaluó con gestantes. No hubo validación clínica ni pruebas con
   gestantes reales.
6. **Escenarios de demostración.** `paciente30` es el escenario principal,
   con datos canónicos. `paciente01` es el caso longitudinal: un embarazo
   histórico del dataset y un embarazo actual de demostración, que es un
   fixture técnico fuera de la muestra canónica. El 12 de movimientos es una
   nueva captura simulada para validar el flujo sin conexión, no una lectura
   del dataset. La interfaz de la gestante es independiente de Power BI.

## Pendientes

- Revisar la narrativa de la tesis contra el documento vigente (arriba).
- Ejecutar CI sobre el nuevo commit (nuevas pruebas de Node y la ruta
  `/graficas.js`); esta etapa no autoriza push.
- `MOV_SIMULADO = 12`: resuelto. Se conserva como nueva captura simulada
  (ver «Escenarios de demostración»); no queda propuesta de reemplazo
  pendiente.
- La suite `test_provisionar_demo_postgresql.py` se ejecutó en local con el
  superusuario de desarrollo; en CI corre con el migrador sin `SUPERUSER`.
- El guardado sin API tarda ~2,8 s en Windows; se muestra «Guardando…».
- Las capturas y recorridos viven en el scratchpad de la sesión, fuera del
  repositorio.
