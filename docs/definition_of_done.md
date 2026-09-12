# Definition of Done (DoD)

Un ticket de FetalAlert se considera **terminado** solo cuando se cumplen todos
los puntos de esta lista. Aplica a cualquier ticket del proyecto: código,
migraciones, datos simulados o documentación.

## Lista de verificación

- [ ] **Criterios de aceptación cumplidos.** Todo lo que pide el ticket está
      implementado, y nada fuera de su alcance se coló en el cambio.
- [ ] **Pruebas locales aprobadas.** La suite corre en verde en la computadora
      de quien desarrolla, antes de abrir el Pull Request, desde `backend/`:
      `python -m pytest -q --ignore=tests/test_migration_postgresql.py --ignore=tests/test_load_mock_data_postgresql.py --ignore=tests/test_ingestion_api_postgresql.py --ignore=tests/test_ingestion_idempotency_postgresql.py --ignore=tests/test_edge_postgresql.py --ignore=tests/test_edge_sincronizacion_postgresql.py --ignore=tests/test_etl_postgresql.py`.
- [ ] **CI aprobado.** El workflow `CI` (`.github/workflows/ci.yml`) termina en
      verde para el Pull Request. Un job en rojo bloquea el cierre del ticket.
- [ ] **Pruebas de PostgreSQL ejecutadas y no omitidas.** Los siete archivos que
      necesitan un servidor real deben ejecutarse de verdad y no aparecer como
      *skipped*:
      `tests/test_migration_postgresql.py`, con `SCRUM52_TEST_DATABASE_URL`
      apuntando a la base dedicada `scrum52_validacion_tmp`, cubriendo el ciclo
      completo `upgrade -> downgrade -> upgrade -> alembic check` sobre esa
      base: confirma que la migración puede revertirse y volver a aplicarse, y
      que al final no quedan divergencias pendientes entre los modelos y el
      esquema; `tests/test_load_mock_data_postgresql.py`, con
      `SCRUM61_TEST_DATABASE_URL` apuntando a una base ya desplegada en el
      `head` de Alembic; `tests/test_ingestion_api_postgresql.py`, con
      `SCRUM62_TEST_DATABASE_URL` apuntando también a una base en `head`, que
      ejercita el endpoint de ingesta dentro de transacciones que siempre se
      revierten; y `tests/test_ingestion_idempotency_postgresql.py`, con
      `SCRUM63_TEST_DATABASE_URL`, que valida la idempotencia de reenvíos e
      incluye pruebas **concurrentes** con conexiones independientes. Estas
      últimas confirman sus filas de referencia y luego borran exactamente lo
      que crearon, así que la base debe quedar sin filas residuales al terminar.
      `tests/test_edge_postgresql.py`, con `SCRUM64_TEST_DATABASE_URL`
      apuntando también a una base en `head`, que ejercita el ciclo completo del
      nodo edge simulado —SQLite temporal, cliente HTTP, endpoint y
      PostgreSQL— con el mismo aislamiento por transacción revertida.
      Y `tests/test_edge_sincronizacion_postgresql.py`, con
      `SCRUM65_TEST_DATABASE_URL`, que ejercita la sincronización resiliente
      —desconexión y recuperación, confirmación perdida y *replay*, agotamiento
      exacto, error permanente, reinicio entre intentos, varios eventos y la
      trazabilidad de una misma clave desde SQLite hasta
      `operacional.idempotencia_solicitud`— con el reloj y la espera inyectados,
      de modo que no depende de ningún *sleep* real.
      Y `tests/test_etl_postgresql.py`, con `SCRUM69_TEST_DATABASE_URL`, que
      usa esa conexión solo para crear y eliminar sus propias bases temporales
      (`scrum69_tmp_`): en ellas migra, carga el dataset canónico y ejecuta el
      ETL analítico con commits reales —carga inicial y conciliación, segunda
      ejecución idéntica, incremento por el endpoint con *replay*, llegada
      tardía, identificador menor que el máximo, lectura modificada, rollback,
      candado y zona horaria—, y no deja ninguna base al terminar.
      El workflow `CI` lo verifica sobre el reporte JUnit de cada ejecución y
      falla el job si al menos una prueba de PostgreSQL queda omitida.
- [ ] **Pull Request vinculado al ticket de Jira y aprobado.** El PR referencia
      su ticket (por ejemplo, `SCRUM-60`) y cuenta con la aprobación de la otra
      autora.
- [ ] **Comentarios de revisión resueltos.** Cada observación de la revisión fue
      atendida o respondida explícitamente; no quedan hilos abiertos.
- [ ] **Sin secretos ni datos reales.** El cambio no introduce contraseñas,
      tokens, claves ni credenciales reales, ni datos clínicos reales, en
      código, pruebas, documentación, archivos de configuración versionados ni
      automatizaciones. Las credenciales de CI son ficticias y efímeras.
- [ ] **Documentación actualizada cuando aplica.** Si el cambio modifica el
      comportamiento, la configuración o la forma de ejecutar el proyecto, el
      README y los documentos de `docs/` lo reflejan.
- [ ] **Integrado en `main`.** El trabajo quedó incorporado a la rama de
      integración final del ticket.

## Estado verificado localmente de SCRUM-69

Esta sección registra **lo que ya se comprobó en la computadora de desarrollo**,
como evidencia local previa al Pull Request. **No sustituye la ejecución remota
de CI ni la revisión de la otra autora.** La rama se desarrolló apilada sobre la
de SCRUM-65, todavía en revisión.

### Comprobado

- **Conteos de pruebas, reproduciendo los comandos exactos del CI** contra un
  PostgreSQL 16 desechable, tanto con las versiones del entorno local como con
  las que instalaría el CI a la fecha (Alembic 1.20.0, SQLAlchemy 2.0.52):

  | Bloque | Resultado |
  | --- | --- |
  | Offline (sin servidor PostgreSQL) | 1,215 passed |
  | Migraciones — SCRUM-52 | 79 passed |
  | Cargador — SCRUM-61 | 25 passed |
  | Endpoint — SCRUM-62 | 46 passed |
  | Idempotencia — SCRUM-63 | 64 passed |
  | Nodo edge — SCRUM-64 | 20 passed |
  | Sincronización — SCRUM-65 | 16 passed |
  | Esquema analítico y ETL — SCRUM-69 | 50 passed |
  | **Total** | **1,515 passed, 0 failed, 0 skipped** |

  El guardián JUnit del workflow, ejecutado tal cual sobre los siete reportes,
  confirma 300 pruebas de PostgreSQL ejecutadas y ninguna omitida. Los siete
  bloques de PostgreSQL dan los mismos conteos con el entorno local y con el
  del CI, y el guardián termina en 0 en ambos.
- **Una sola cabeza de Alembic:** `60facdbacf51`, que se apoya en
  `87d8ed46686b`. El ciclo `upgrade → downgrade → upgrade` y `alembic check`
  terminan sin diferencias, y el esquema operacional se despliega con los mismos
  nombres de restricciones de siempre.
- **Línea base del ETL sobre el dataset canónico:** 1,180 hechos; 732 sesiones
  distintas en las lecturas de origen y en el hecho, y 732 sesiones con al menos
  una lectura; 560 lecturas de signos maternos y 620 de movimiento; semáforo
  826 OK, 295 WARNING y 59 ERROR, idéntico al registrado en `operacional`;
  `estado_hr` 472/76/12, `estado_spo2` 478/64/18 y `estado_mov` 436/155/29;
  cero huérfanos, cero combinaciones inválidas de NULL y 30 clasificaciones de
  embarazo pendientes, reportadas sin ser error. La segunda ejecución no inserta
  ni actualiza nada.

## Estado verificado localmente de SCRUM-65

Esta sección registra **lo que ya se comprobó en la computadora de desarrollo**,
como evidencia local previa al Pull Request. **No sustituye la auditoría final,
la ejecución remota de CI ni la revisión de la otra autora.**

### Comprobado

- **Conteos de pruebas, reproduciendo los comandos exactos del CI:**

  | Bloque | Resultado |
  | --- | --- |
  | Offline (sin servidor PostgreSQL) | 925 passed, 0 failed, 0 skipped |
  | Migraciones — SCRUM-52 | 79 passed |
  | Cargador — SCRUM-61 | 25 passed |
  | Endpoint — SCRUM-62 | 46 passed |
  | Idempotencia — SCRUM-63 | 64 passed |
  | Nodo edge — SCRUM-64 | 20 passed |
  | Sincronización — SCRUM-65 | 16 passed |
  | **Total** | **1175 passed, 0 failed, 0 skipped** |

  El bloque offline pasa de 760 a 925 con 165 pruebas nuevas: 64 de la política
  de reintentos, 22 de la migración local v1 → v2, 56 de la sincronización
  —lease, censo, reconciliación, resultados tardíos, pausa por transporte y
  códigos de salida—, 15 de la traza, 6 de los límites de la familia HTTP
  reintentable, más 1 del CLI y 1 de almacenamiento añadidas al corregir dos
  pruebas heredadas.
  Las 16 de SCRUM-65 con PostgreSQL son una suite **separada**, excluida del
  bloque offline y ejecutada en su propio paso del workflow. Ningún conteo
  anterior disminuyó.

- **Cero pruebas de PostgreSQL omitidas.** El guardián JUnit del workflow,
  ejecutado sobre los **seis** reportes de la reproducción local, terminó con
  código 0: 250 pruebas ejecutadas de verdad contra PostgreSQL 16, ninguna
  `skipped`.

- **Determinismo temporal, sin dormir.** El reloj y la espera se inyectan en
  todas las pruebas de la política: el sleeper anota lo que recibe y adelanta un
  reloj falso, de modo que un lease de 70 s con techo de 1 s se recorre entero
  sin gastar un segundo real. Las suites sensibles a tiempo y concurrencia
  —política, sincronización, traza, migración y emisor— se repitieron **tres
  veces**: 217 passed en las tres, sin variación.

- **La fórmula de espera, con su desfase acechante.** Se comprobó
  `delay(k) = min(base × 2^(k-1), techo)` para k de 1 a 6, la aplicación del
  techo, la saturación con un exponente absurdo y —explícitamente— que el
  intento 4 espera 8 s y no 4 s, que es el off-by-one que produce la lectura
  alternativa de `k`.

- **Configuración inválida rechazada**, incluidos `NaN`, `+inf` y `-inf` en los
  tres campos numéricos, `max_attempts < 1`, `batch_limit <= 0`,
  `base_delay_seconds <= 0`, un techo por debajo de la base, `http_timeout <= 0`
  y booleanos colados como enteros.

- **Duraciones no programables rechazadas, y en el sitio correcto.** Las tres
  esperas configurables viven en un intervalo cerrado —`1 µs`, la resolución de
  `timedelta`, hasta `86400 s`— porque un `float` finito y positivo no basta:
  `5e-324` se convertía en cero al programarse y `1e308` hacía estallar
  `timedelta` con un `OverflowError` a mitad de una pasada. Se comprobaron los
  dos bordes inclusivos, toda la franja por debajo del microsegundo, un *ulp* por
  encima del máximo, y el `http_timeout` cuyo **lease derivado** —`4 × timeout +
  30`— se sale de la cota aunque el campo quepa. El rechazo ocurre al construir
  la política: hay una prueba que fotografía `captura_local`, `outbox` e
  `intento_sincronizacion` y verifica que seis configuraciones inválidas no
  cambian una sola fila ni envían una sola petición HTTP. No queda ningún
  `except OverflowError` en el paquete.

- **Los límites de la familia HTTP reintentable, ejercidos.** `599` se reintenta;
  `600`, `699` y `999` no, porque no pertenecen a ninguna familia documentada y
  la condición `500 <= codigo` sin cota superior se los tragaba; y `408` y `429`
  siguen siendo permanentes por decisión registrada —este endpoint no implementa
  ni timeouts de petición ni limitación de tasa, y `Retry-After` no se
  implementa—. Cada caso comprueba el desenlace completo: clasificación, estado,
  `reintentable`, `motivo_revision` y presencia o ausencia de
  `proximo_intento_en`.

- **La fórmula del backoff, comprobada contra aritmética exacta.** No hay tope
  fijo del exponente: la saturación se deriva de la base y del techo, de modo que
  `delay(k) = min(base × 2^(k-1), techo)` se cumple para cualquier configuración
  válida. Se comparó contra una referencia calculada con fracciones en seis
  configuraciones que recorren el dominio admisible de punta a punta —el caso por
  omisión, un techo que no es potencia de dos, base igual a techo, el intervalo
  entero y sus dos extremos degenerados— sin una sola divergencia. Un tope
  constante parecía equivalente y no lo era: con base `2**-100` y techo `1.0`, el
  intento 101 devolvía `9.09e-13` en lugar de `1.0`, un factor de `2**40`. Ese
  par ya no es configurable —la base queda por debajo del mínimo programable—,
  así que las pruebas se reescribieron sobre el rango más ancho que sí se admite,
  base `1 µs` contra techo `24 h`, donde la saturación cae entre `demora(37)` y
  `demora(38)`. Lo que fijan no es un número de duplicaciones, sino que ese punto
  lo decide la base y no una constante. Un ordinal enorme —hasta `2**62`— satura
  en el techo sin `OverflowError`.

- **El censo, en una sola instantánea.** Se toma con **una** sentencia de solo
  lectura, sin transacción de escritura y sin dejar nada abierto. Con dos
  consultas existía una intercalación reproducible que hacía terminar al
  sincronizador dejando un reintento programado atrás: la primera veía un evento
  cuyo único intento estaba abierto, otro proceso lo cerraba y programaba el
  reintento, la segunda ya no encontraba intentos abiertos, y el censo combinado
  informaba de cero elegibles, cero programados y cero abiertos. Hay pruebas con
  dos conexiones SQLite y un proxy instrumentado que afirman que el censo nunca
  describe una cola sin trabajo mientras el evento real está programado.

- **La terminalidad de «requiere revisión», con control negativo.** Retirada la
  guarda `NOT (estado = 'FALLIDO' AND reintentable = 0)` del `UPDATE`, la prueba
  de que un fallo tardío no revierte un agotamiento falla; restaurada, pasa.

- **La correlación del lease, con control negativo.** Se corrigió un defecto en
  el que la subconsulta comparaba `i.id_outbox` contra un `id_outbox` **sin
  calificar**, que SQLite resolvía en el ámbito interno. Reintroducido el
  prefijo vacío, fallan la prueba de forma y la de comportamiento; restaurado,
  las cinco pruebas de resolución de herencia pasan.

- **Migración v1 → v2 sin pérdida, sobre datos reales.** Se ejecutó `init` sobre
  una **copia** de la base de demostración de SCRUM-64: pasó de `user_version` 1
  a 2, conservó el evento `ENVIADO` con su clave, sus identificadores remotos y
  su `ultimo_http`, y registró sus 2 intentos como heredados. La traza declara
  «confirmado antes de SCRUM-65 (sin historial de intentos)» y `confirmado_en:
  no medido`, sin inventar el instante. Se verificó además que el DDL almacenado
  de una base migrada es **idéntico** al de una creada de cero, tabla e índices
  parciales incluidos.

- **Demostración manual del agotamiento**, con la API apagada y una ruta fuera
  del repositorio:

  ~~~text
  init                            : almacenamiento local creado
  capturar (API apagada)          : PENDIENTE, sin contactar al servidor
  traza antes de intentar         : 0 intentos, sin política adoptada
  sincronizar --max-intentos 3    : 3 rondas, 2 esperas (1 s + 1 s), 1 agotado
                                    código de salida 2
  traza tras el agotamiento       : 3 de 3, motivo AGOTAMIENTO,
                                    ultimo_http vacío junto a su mensaje propio
  segunda ejecución               : 0 rondas, intentos siguen en 3
                                    no existe un cuarto intento
  ~~~

  Un paquete inválido produjo `tipo_sesion: enum; lecturas.0: value_error` —
  campos y tipos de error, **ningún valor del paquete**.

- **La evidencia manual de SCRUM-64 se conservó intacta.**
  `data/edge/demo_scrum64.sqlite3` mantiene su SHA-256
  `13c83c9687fea2d2619901e30ecd168d61e742d976d4ca249eac70c7c94e11e1` y su fecha
  de modificación original antes y después de toda la validación. La suite
  offline queda completamente verde **con esa base en su sitio**, que es lo que
  antes no ocurría: la prueba heredada
  `test_el_repositorio_no_contiene_bases_sqlite` exigía que no existiera ningún
  archivo SQLite bajo la raíz y por tanto castigaba ejecutar la demostración
  documentada. Ahora comprueba las dos cosas que de verdad importan —que el
  comando no cree bases dentro del repositorio, comparando el conjunto antes y
  después, y que Git no siga ninguna— y tolera un artefacto local correctamente
  ignorado.

- **Sin artefactos nuevos en el repositorio.** Tras la validación no quedan
  bases SQLite creadas por las pruebas ni reportes JUnit, y el árbol de trabajo
  queda **limpio**: no hay archivos de la implementación sin seguimiento, porque
  todos están confirmados. El único `*.sqlite3` bajo la raíz es la evidencia
  manual, y `git ls-files` no sigue ninguna base.

- **PostgreSQL no cambió.** El ticket no añadió columnas, migraciones de Alembic
  ni modelos: la correlación se apoya en `operacional.idempotencia_solicitud`
  tal como la dejó SCRUM-63.

- **Documentación actualizada:** README (comandos `sincronizar` y `traza`, la
  diferencia con `enviar`, la política y su fórmula, las cuatro fechas, la
  evolución del almacenamiento local y los códigos de salida), decision log
  (decisiones de SCRUM-65) y esta lista.

### Verificaciones externas requeridas para cerrar SCRUM-65

El estado de estas verificaciones cambia fuera del contenido versionado y debe
comprobarse directamente en GitHub y Jira antes de cerrar el ticket.

Ya hecho:

- el trabajo está confirmado en commits y el árbol local está limpio;
- la rama `feature/scrum-65-sincronizacion-reintentos-trazabilidad` está
  **publicada** en GitHub, con sus commits en el remoto.

Todavía **pendiente**:

- **el Pull Request no existe** —y por tanto GitHub Actions no se ha ejecutado
  para SCRUM-65: el workflow se dispara con `pull_request` hacia `main`, no con
  un push a una rama de trabajo—;
- la otra autora no lo ha revisado ni aprobado;
- no está integrado en `main`;
- no hay CI posterior al merge.

La rama parte del `main` que integró SCRUM-64, así que el Pull Request tendrá
`main` como base y
`feature/scrum-65-sincronizacion-reintentos-trazabilidad` como head, y el CI se
disparará solo al abrirlo.

## Estado verificado localmente de SCRUM-64

Esta sección registra **lo que ya se comprobó en la computadora de desarrollo**,
como evidencia local previa al Pull Request. **No sustituye la auditoría final,
la ejecución remota de CI ni la revisión de la otra autora.**

### Comprobado

- **Conteos de pruebas, reproduciendo los comandos exactos del CI:**

  | Bloque | Resultado |
  | --- | --- |
  | Offline (sin servidor PostgreSQL) | 760 passed, 0 skipped |
  | Migraciones — SCRUM-52 | 79 passed |
  | Cargador — SCRUM-61 | 25 passed |
  | Endpoint — SCRUM-62 | 46 passed |
  | Idempotencia — SCRUM-63 | 64 passed |
  | Nodo edge — SCRUM-64 | 20 passed |
  | **Total recolectado** | **994** (`pytest --collect-only -q`) |

  El bloque offline pasa de 594 a 760 con las 166 pruebas locales del nodo edge
  (55 de almacenamiento y esquema, 37 de captura, 54 de emisor y clasificación,
  20 del comando). Las 20 de SCRUM-64 con PostgreSQL son una suite **separada**,
  excluida del bloque offline y ejecutada en su propio paso del workflow.
  Ninguna suite anterior cambió de conteo.

- **Cero pruebas de PostgreSQL omitidas.** El guardián JUnit del workflow,
  ejecutado sobre los cinco reportes de la reproducción local, terminó con
  código 0. Se comprobó con un control negativo: inyectar un `<skipped>` en
  `pytest-scrum64.xml` lo hace fallar.

- **La terminalidad de `ENVIADO`, con control negativo.** Retirada la guarda
  `estado <> 'ENVIADO'` del `UPDATE`, las dos pruebas de secuencia concurrente
  fallan; restaurada, pasan. La garantía está ejercida, no solo escrita.

- **Detección de un esquema local debilitado.** Se verificó que
  `verificar_esquema` rechaza un `UNIQUE` retirado, un `NOT NULL` retirado, una
  llave foránea retirada, un `CHECK` borrado, un cuarto estado colado en el
  `CHECK` de estados y una columna de más.

- **`fecha_hora_sincronizacion` congelada, no prohibida.** Se comprobó que un
  valor válido no nulo se acepta, se persiste sin alterarlo, sobrevive al
  reinicio, viaja idéntico en los tres intentos de un mismo evento y llega así a
  PostgreSQL, incluso recorriendo la ventana de respuesta perdida —donde sigue
  produciendo un replay y no un `409`—. Un valor inválido lo sigue rechazando el
  contrato Pydantic, no una regla local.

- **Demostración manual completa**, contra la base temporal
  `scrum64_validacion_tmp` cargada con el dataset simulado:

  ~~~text
  Captura con la API apagada     : PENDIENTE, sin contactar al servidor
  Pasada con la API apagada      : FALLIDO reintentable, clave conservada
  Reapertura del archivo         : mismo evento, misma clave, payload intacto
  Pasada con la API encendida    : 201, estado ENVIADO
  PostgreSQL para esa clave      : 1 fila de idempotencia, 1 sesión, 2 lecturas
  Reenvío de la misma clave      : 201 / Idempotency-Replayed=true, id_sesion 832
  Segundo reenvío                : 201 / replay, mismos ids_lectura [1280, 1281]
  Clave nueva con el mismo cuerpo: 201 con id_sesion 833  (no es un reenvío)
  Nueva pasada del emisor        : 0 seleccionados (lo confirmado no se reenvía)
  ~~~

  Los identificadores `832` y `[1280, 1281]` son evidencia de esa ejecución, no
  valores contractuales. La base temporal se eliminó y los archivos de la
  demostración se borraron; `data/edge/` quedó además cubierto por `.gitignore`,
  verificado con `git check-ignore`.

- **Base sin filas residuales** tras la secuencia completa de los seis bloques.

- **Documentación actualizada:** README (sección del nodo edge simulado y estado
  actual del proyecto), decision log (decisiones de SCRUM-64) y esta lista.

### Verificaciones externas requeridas para cerrar SCRUM-64

El estado de estas verificaciones cambia fuera del contenido versionado y debe
comprobarse directamente en GitHub y Jira antes de cerrar el ticket:

- los commits de SCRUM-64 fueron publicados en su rama;
- GitHub Actions terminó en verde para el Pull Request;
- la otra autora revisó y aprobó el Pull Request;
- el Pull Request fue integrado en `main`;
- el CI posterior al merge terminó en verde.

## Estado verificado localmente de SCRUM-63

Registro del ticket anterior, ya integrado en `main`. Se conserva como
antecedente; la lista de verificación vigente es la de arriba.

### Comprobado

- **Migración.** Cadena de dos revisiones, `150788f88be7 -> 87d8ed46686b`. Ciclo
  real contra PostgreSQL 16 sobre la base dedicada `scrum52_validacion_tmp`:
  `upgrade`, `downgrade` de la revisión nueva, `upgrade` otra vez, y
  `alembic check` **sin divergencias**. La base queda desplegada en el `head`.
- **Conteos de pruebas, reproduciendo los comandos exactos del CI:**

  | Bloque | Resultado |
  | --- | --- |
  | Offline (sin servidor PostgreSQL) | 594 passed, 0 skipped |
  | Migraciones — SCRUM-52 | 79 passed |
  | Cargador — SCRUM-61 | 25 passed |
  | Endpoint — SCRUM-62 | 46 passed |
  | Idempotencia — SCRUM-63 | 64 passed |
  | **Total recolectado** | **808** (`pytest --collect-only -q`) |

- **Cero pruebas de PostgreSQL omitidas.** El guardián JUnit del workflow,
  ejecutado sobre los cuatro reportes de la reproducción local, terminó con
  código 0. Se comprobó además con un control negativo: inyectar un `<skipped>`
  en el reporte nuevo lo hace fallar con código 1.
- **Concurrencia real ejecutada, no omitida.** Las 14 pruebas concurrentes
  figuran como ejecutadas en `pytest-scrum63.xml`, sin `skipped` ni `failure`.
  Se repitieron 12 veces consecutivas sin un solo fallo.
- **Base sin filas residuales** tras la secuencia completa de los cinco bloques.
- **Documentación actualizada:** README (contrato de `Idempotency-Key`),
  decision log (decisiones de SCRUM-63) y esta lista.

### Verificaciones externas requeridas para cerrar SCRUM-63

El estado de estas verificaciones cambia fuera del contenido versionado y debe
comprobarse directamente en GitHub y Jira antes de cerrar el ticket:

- los commits de SCRUM-63 fueron publicados en su rama;
- GitHub Actions terminó en verde para el Pull Request;
- la otra autora revisó y aprobó el Pull Request;
- el Pull Request fue integrado en `main`;
- el CI posterior al merge terminó en verde.

## Nota sobre los datos

FetalAlert trabaja **exclusivamente con datos simulados y sintéticos,
completamente ficticios**. No deben incluirse datos clínicos reales de pacientes
—ni siquiera parcialmente o de forma anonimizada— en el código, las pruebas, los
datasets de ejemplo, la documentación ni las automatizaciones del repositorio.
