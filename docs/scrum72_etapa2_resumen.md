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
  movimiento reducido y diálogo de cierre.
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
3. **ESP32.** Si la tesis presenta una interfaz embebida en un ESP32 sin
   cuenta central, conviene tratarla como antecedente del prototipo o como
   trabajo futuro: el MVP actual no integra hardware y usa datos simulados.
4. **Acceso.** Evitar «solo tú puedes acceder»: la gestante accede solo a su
   información, y el servidor también la pone a disposición del personal de
   salud autorizado para su seguimiento, con control por roles y aislamiento
   por filas.
5. **Alcance.** Prototipo académico con datos simulados; sin validación
   clínica, diagnóstico ni monitoreo continuo; el manual aplica criterios de
   accesibilidad (una acción por paso, texto breve, iconos con texto), pero no
   se evaluó con gestantes.

## Pendientes

- Revisar la narrativa de la tesis contra el documento vigente (arriba).
- Ejecutar CI sobre el nuevo commit (nuevas pruebas de Node y la ruta
  `/graficas.js`); esta etapa no autoriza push.
- `MOV_SIMULADO = 12` sigue como escenario técnico documentado.
- El guardado sin API tarda ~2,8 s en Windows; se muestra «Guardando…».
- Las capturas y recorridos viven en el scratchpad de la sesión, fuera del
  repositorio.
