# Decision Log – Sprint 0

## Estrategia de generación de datos
Para la validación inicial del sistema se utilizará Mockaroo como fuente de datos simulados, debido a su facilidad de uso, rapidez para generar datasets estructurados y bajo costo de implementación. El uso de scripts en Python para la generación de datos se evaluará en fases posteriores del proyecto como parte del diseño de los procesos ETL.

## Motor de base de datos
Durante el Sprint 0 se evaluarán dos gestores de base de datos relacionales:
- PostgreSQL (Tinuola)
- MySQL (Viviana)

La selección final se realizará en función de la facilidad de conexión con Power BI, estabilidad del driver y compatibilidad con los datos simulados.

## Versión de Python
Se seleccionó Python 3.12 por ser una versión estable y ampliamente soportada por las principales librerías de análisis de datos y conectores de bases de datos.

## Objetivo del Sprint 0
Validar el entorno técnico, la estructura del repositorio y la conectividad entre la base de datos seleccionada y Power BI mediante datos simulados.

## Evidencia técnica
La evidencia del Sprint 0 incluirá capturas de la inserción de datos simulados en la base de datos y su correcta visualización en Power BI.

## Verificación de entorno local — Windows 

Fecha inicial: 2 de agosto de 2026  
Validación final: 4 de agosto de 2026

Se clonó el repositorio y se verificó el entorno de desarrollo local en Windows 11,
siguiendo la configuración incorporada originalmente en
`feature/sprint-4-api-foundation`.

- Se verificaron los requisitos del sistema, incluyendo arquitectura AMD64,
  memoria disponible y virtualización por hardware.
- Docker Desktop fue instalado y configurado mediante WSL2.
- La instalación de Docker se validó correctamente con `docker run hello-world`.
- Se creó el archivo `.env` local a partir de `.env.example`.
- Se confirmó que el puerto `5433` estaba disponible para PostgreSQL.
- Se reconstruyó la imagen y se levantaron los servicios mediante
  `docker compose up -d --build`.
- `docker compose ps` confirmó que PostgreSQL se encontraba en estado
  `healthy` y que la API estaba activa.
- La documentación interactiva de FastAPI fue verificada en
  `http://localhost:8000/docs`.
- El endpoint `GET /health` respondió correctamente con el estado `ok`.
- Se confirmó mediante `git check-ignore -v .env` que el archivo `.env`
  está excluido del control de versiones.
- Se verificó que el repositorio permaneciera limpio después del commit.

No fue necesario modificar la configuración compartida del proyecto ni se
incluyeron credenciales o archivos locales en Git.
## SCRUM-54 — Definición del dataset simulado

Se estandarizó la muestra técnica utilizada para la validación funcional de FetalAlert.

### Decisiones aprobadas

- Se mantienen 30 gestantes y 30 embarazos simulados con seguimiento longitudinal.
- Se utilizan 30 dispositivos FetalAlert, uno asignado a cada embarazo durante la simulación.
- Se utilizan 3 clínicas, con 10 gestantes por clínica.
- Se utilizan 5 médicos distribuidos entre las 3 clínicas; cada médico pertenece exclusivamente a una única clínica (nunca a dos o tres), y la cantidad de embarazos por médico puede variar según cuántos médicos tenga asignados cada clínica, sin exigir una distribución uniforme.
- Se mantienen 2 usuarios administradores para las pruebas de seguridad.
- El dataset contiene 1,180 registros biométricos y no deberá superar 1,200 registros en esta versión.
- Se generan 560 registros de frecuencia cardíaca materna y SpO₂.
- Se generan 620 registros consolidados de movimientos fetales.
- Una sesión de HR/SpO₂ contiene cinco lecturas biométricas procesadas representativas.
- Las muestras internas de alta frecuencia capturadas por el MAX30102 no se persisten individualmente.
- Los movimientos fetales se registran únicamente desde la semana gestacional 20.
- Las sesiones de movimientos pueden durar aproximadamente entre 60 y 120 minutos y almacenan un conteo consolidado por sesión.
- No se establece una frecuencia obligatoria de tres sesiones diarias.
- Los campos biométricos que no aplican al evento se representan mediante NULL y no mediante cero.
- La distribución técnica de factores de riesgo es de 14 embarazos sin factor, 9 con un factor y 7 con dos factores.
- La distribución técnica del semáforo es 70 % OK, 25 % WARNING y 5 % ERROR, equivalente a 826, 295 y 59 registros respectivamente.
- Los factores de riesgo no determinan directamente las alertas biométricas.
- Algunos registros simulan sincronización diferida para validar el enfoque offline-first.
- El generador utiliza una semilla fija para garantizar reproducibilidad.
- El artefacto oficial para generar la muestra es `scripts/generate_mock_data.py`.
- Los archivos JSON y CSV generados no se versionan en Git y pueden reconstruirse ejecutando el generador.
- La carga definitiva en PostgreSQL queda pendiente de integrar y validar las migraciones del modelo relacional en el flujo correspondiente.

### Revisión técnica final (cierre de SCRUM-54)

- En la definición inicial de SCRUM-54, el dataset se contrastó contra el diseño relacional conceptual aprobado de la tesis, ya que los modelos SQLAlchemy del esquema operacional aún no estaban implementados en el repositorio. Posteriormente, una vez disponibles esos modelos, se realizó una segunda revisión y alineación técnica; sus resultados se documentan en la sección «Alineación técnica con SQLAlchemy real» más abajo.
- Se completaron en el generador los registros maestros/relacionales que ya estaban previstos en el diseño pero aún no se generaban: `TelefonoPaciente` (40 registros: 30 celulares principales, 10 correos alternos secundarios), `TelefonoMedico` (10 registros: 5 celulares principales, 5 contactos de domicilio secundarios), `Usuario` (37 cuentas: 30 PACIENTE, 5 MEDICO, 2 ADMIN), `UsuarioPaciente` (30 relaciones) y `UsuarioMedico` (5 relaciones).
- Las tres clínicas simuladas se ubican en zonas coherentes con el alcance rural de FetalAlert: una en Chiriquí (Renacimiento, Plaza Caisán), una en Veraguas (Santa Fe, Calovébora) y una en Darién (Chepigana, Camogantí). Nombres y direcciones son completamente sintéticos.
- La información geográfica pertenece exclusivamente a `Clinica`. No se modela residencia de la paciente (sin provincia, distrito, corregimiento ni dirección residencial en `Paciente`).
- El enum `tipo_contacto` está alineado con el enum real `TipoContacto` del modelo SQLAlchemy: `CELULAR`, `TELEFONO_DOMICILIO` y `CORREO_ALTERNO`.
- No se generaron filas de `AuditoriaLog`; se producirán mediante acciones reales durante pruebas funcionales.
- Las cantidades biométricas aprobadas (732 sesiones, 1,180 lecturas, 560 HR/SpO₂, 620 movimientos, 826/295/59 semáforo) no se modificaron.
- Los valores físicos de enums, tipos de datos, longitudes y claves primarias/foráneas fueron posteriormente validados contra los modelos SQLAlchemy reales del esquema operacional; los ajustes resultantes de esa validación quedaron incorporados al dataset (ver sección «Alineación técnica con SQLAlchemy real»).

### Alineación técnica con SQLAlchemy real (SCRUM-51 / rama feature/sprint-4-sqlalchemy-models)

Con los 22 modelos SQLAlchemy reales ya disponibles (aunque todavía no fusionados a esta rama), se realizaron los siguientes ajustes de alineación técnica:

- Las PK/FK del dataset pasaron de códigos de texto (`PAC-001`, `CLI-001`, ...) a enteros determinísticos que comienzan en 100 dentro de cada entidad, alineados con las PK `Integer` reales. Los códigos legibles se conservan solo donde el modelo real tiene una columna de negocio propia (`cedula`, `ruc`, `codigo_dispositivo`).
- `Clinica` genera `direccion_fisica` (antes `calle`); las tres ubicaciones rurales aprobadas no cambiaron.
- `TelefonoMedico` usa `TELEFONO_DOMICILIO` en vez de `FIJO` (valor eliminado del enum real `TipoContacto`).
- `Embarazo.estado_embarazo` usa exclusivamente `ACTIVO` / `FINALIZADO` / `SUSPENDIDO`, distribuidos determinísticamente 20/8/2 sobre los 30 embarazos, con `fecha_cierre` coherente (NULL solo en ACTIVO; en los demás, posterior o igual a la última captura biométrica real del embarazo, sin sesiones ni lecturas después del cierre).
- `Dispositivo.estado` refleja el estado del embarazo asociado: 20 `ASIGNADO` (embarazos ACTIVO) y 10 `DISPONIBLE` (embarazos FINALIZADO/SUSPENDIDO, asignación histórica cerrada con `fecha_fin = fecha_cierre`).
- `SesionMonitoreo.origen_dato` usa `DISPOSITIVO` (antes `API`, valor inexistente en el enum real).
- `SesionMonitoreo.tipo_sesion` (campo obligatorio real, antes no generado) se persiste en todas las sesiones: 112 `SIGNOS_MATERNOS`, 620 `MOVIMIENTOS_FETALES`.
- `SesionMonitoreo.fecha_inicio/fecha_fin`, `LecturaBiometrica.fecha_hora_captura/fecha_hora_sincronizacion` y `Dispositivo.fecha_registro` ahora son datetimes UTC offset-aware, alineados con `DateTime(timezone=True)`. Las fechas propias de `Embarazo` (Date en el modelo real) se mantienen sin componente de hora.
- La entidad `paciente_factor_riesgo` se renombró a `embarazo_factor_riesgo` (nombre físico real de la tabla); los campos y la distribución 14/9/7 no cambiaron.
- Cada uno de los 5 médicos pertenece exclusivamente a una única clínica (nunca a dos o tres); una misma clínica puede tener varios médicos asociados. La cantidad de embarazos por médico puede variar según la cantidad de médicos asignados a cada clínica, sin exigir un reparto uniforme; cada clínica conserva exactamente 10 embarazos.
- La vigencia de `SeguimientoClinico` es coherente con el estado del embarazo: mientras el embarazo está `ACTIVO`, el seguimiento permanece activo con `fecha_fin = NULL`; cuando el embarazo queda `FINALIZADO` o `SUSPENDIDO`, el seguimiento se marca inactivo con `fecha_fin = Embarazo.fecha_cierre`. El médico de cada seguimiento pertenece siempre a la clínica correspondiente al embarazo.
- Los estados globales de embarazo (20 `ACTIVO`, 8 `FINALIZADO`, 2 `SUSPENDIDO`) se distribuyen entre las tres clínicas, de modo que cada una conserve una combinación de embarazos activos y cerrados, evitando una correlación artificial entre clínica/provincia y estado del embarazo.
- Ninguna de las cantidades biométricas aprobadas cambió: 732 sesiones, 1,180 lecturas, 560 HR/SpO₂, 620 movimientos, 826/295/59 semáforo, 37 usuarios, 40 TelefonoPaciente, 10 TelefonoMedico, 30 UsuarioPaciente, 5 UsuarioMedico.

Durante la revisión técnica se identificó inicialmente una incompatibilidad entre la cardinalidad `SesionMonitoreo` → `LecturaBiometrica` del modelo SQLAlchemy, definida como 1:1 (`id_sesion` único), y la regla funcional aprobada de 5 lecturas procesadas por sesión HR/SpO₂. El modelo fue posteriormente ajustado a una relación 1:N: `LecturaBiometrica.id_sesion` ya no está restringido como único, y `SesionMonitoreo.lecturas` se maneja como una colección. La muestra de SCRUM-54 conserva las 5 lecturas procesadas representativas por sesión HR/SpO₂ definidas para esta granularidad técnica; ese valor no representa un límite máximo de cardinalidad del modelo.

## SCRUM-61 — Carga idempotente del dataset simulado en PostgreSQL

Se implementó el proceso que lleva el dataset simulado aprobado a las tablas
reales del esquema operacional. El artefacto oficial es
`scripts/load_mock_data.py`, con la lógica reutilizable en `backend/app/loader/`.

### Decisiones aprobadas

- **JSON como fuente canónica.** Se carga `data/generated/dataset_fetalalert.json`;
  los CSV siguen siendo una exportación paralela y no participan en la carga.
- **21 tablas pobladas de 22.** `auditoria_log` no se carga: sus registros se
  producirán mediante acciones reales durante las pruebas funcionales.
- **`usuarios_administradores` no se inserta por separado.** Es un subconjunto
  informativo de `usuarios`; cargarlo aparte duplicaría a los dos administradores.
- **Carga en orden de llaves foráneas**, declarado explícitamente y verificado
  por una prueba que deriva las dependencias de `Base.metadata`.
- **Transacción única.** La carga completa se aplica o no queda nada. La función
  de carga no hace `commit` ni `rollback`: la transacción pertenece a quien la
  llama (`engine.begin()` en el comando; una transacción revertida en las pruebas).
- **Idempotencia sin sobrescritura:** se insertan los registros ausentes, se
  conservan los ya presentes e idénticos, y una llave primaria existente con
  contenido distinto se trata como conflicto que aborta la carga y revierte todo.
- **Sin `ON CONFLICT`.** Un `ON CONFLICT DO NOTHING` ocultaría en silencio una
  violación `UNIQUE` distinta de la llave primaria. Se comparan los registros
  existentes y se insertan solo los ausentes, de modo que PostgreSQL sigue siendo
  la autoridad final sobre `UNIQUE`, `CHECK` y llaves foráneas.
- **Sin operaciones destructivas:** no se usa `TRUNCATE`, `DROP`, `DELETE` ni
  recreación del esquema, y no se desactiva ninguna restricción.
- **El cargador no crea el esquema.** Verifica que exista `alembic_version` y que
  la revisión desplegada sea exactamente el `head`, obtenido dinámicamente de
  Alembic. La base se prepara con `alembic upgrade head`.
- **Guardias antes de conectar:** el cargador solo se ejecuta con `APP_ENV` en
  `development`, `test` o `ci`, y valida con `make_url` que el destino sea
  PostgreSQL antes de construir el engine, para que una URL equivocada no alcance
  a crear nada. La URL nunca se recibe por argumento, no se imprime y se depura
  de credenciales en cualquier mensaje de error.
- **Ajuste de secuencias tras insertar IDs explícitos.** Solo para llaves
  primarias simples autoincrementales, detectadas con `pg_get_serial_sequence`
  (17 de las 21 tablas cargadas). Se usa `ALTER SEQUENCE ... RESTART WITH`, que es
  transaccional, en lugar de `setval`, que no lo es: así un rollback tampoco deja
  la secuencia movida. El máximo se calcula sobre toda la tabla, una secuencia ya
  adelantada nunca se reduce, y una tabla sin filas se omite.
- **Verificación poscarga acotada a las llaves primarias del dataset**, no con un
  `count(*)` global, para que el resultado siga siendo correcto en una base de
  desarrollo que ya contenga otros registros.
- **El dataset generado no se versiona.** `data/generated/` permanece ignorado y
  se reconstruye con el generador y su semilla fija.

### Validación

Las pruebas contra PostgreSQL 16 se ejecutan en CI sobre la misma base efímera de
SCRUM-52, después del paso que la deja desplegada en `head`. No migran, no crean
y no borran nada, y la base queda como estaba, pero por dos mecanismos distintos:
la mayoría revierte su propia transacción, mientras que la prueba que consume
`nextval` sobre una secuencia real —una operación que no es transaccional por
naturaleza— toma además una instantánea previa y la restaura explícitamente con
`setval` en un `try/finally`, fallando si la restauración no funciona. El reporte
JUnit de ambos archivos se revisa al final del job y una sola prueba omitida lo
pone en rojo.

## SCRUM-62 — Contratos Pydantic y endpoint de recepción de lecturas biométricas

Se implementó la primera entrada vertical de la aplicación: solicitud HTTP →
validación Pydantic → validaciones mínimas de negocio → persistencia SQLAlchemy
→ respuesta tipada. El artefacto es `POST /api/v1/sesiones-monitoreo`, con los
schemas en `backend/app/schemas/`, la lógica en `backend/app/services/` y el
router en `backend/app/api/v1/`.

### Decisiones aprobadas

- **Forma del paquete HTTP: una sesión con su colección de lecturas.** El cuerpo
  trae la `SesionMonitoreo` y, anidada, la lista de sus `LecturaBiometrica`. La
  relación es **1:N** y la lista **no puede venir vacía**: una sesión sin
  lecturas no describe ningún evento de monitoreo.
- **Ruta versionada desde el primer endpoint:** `/api/v1/...`. El consumidor
  previsto es el nodo edge de SCRUM-64/65, que sincroniza de forma diferida y
  puede estar corriendo una versión antigua cuando el servidor ya cambió; el
  prefijo es lo que permite evolucionar el contrato sin romperlo. `GET /health`
  se mantiene sin prefijo: es operativo, no parte de la API de negocio.
- **Schemas Pydantic separados de los modelos ORM.** Un modelo SQLAlchemy nunca
  se acepta como cuerpo HTTP ni se devuelve como respuesta. Los enums sí se
  importan de `app.models.enums`, para que exista un solo vocabulario.
- **`extra="forbid"`.** Un campo que el contrato no declara es un error, igual
  que en el cargador de SCRUM-61. Sin esto, un `id_seison` mal escrito se leería
  como «el cliente omitió `id_sesion`».
- **Enteros estrictos, decimales laxos.** `StrictInt` en los identificadores y en
  `mov_valor`, de modo que `true` no se lea como 1 ni `"119"` como 119. Para los
  valores biométricos se mantiene la regla laxa: una cadena numérica se acepta y
  un booleano no. Es exactamente la línea que ya traza `normalizar_valor` en el
  cargador, y trazarla distinta habría hecho que el mismo dato entrara por una
  puerta y fuera rechazado por la otra.
- **Marcas de tiempo con offset obligatorio** (`AwareDatetime`), coherente con
  las columnas `TIMESTAMPTZ` y con lo que ya exige el cargador. El offset se
  conserva; no se normaliza a UTC durante la validación.
- **`None` → `NULL`, nunca cero ni cadena vacía.** Omitir una métrica equivale a
  enviarla en `null`.
- **Reparto explícito de validaciones entre capas.** Pydantic se ocupa de la
  *forma* del mensaje: tipos, obligatorios y opcionales, enums, zona horaria,
  forma biométrica de cada lectura y coherencia entre `tipo_sesion` y la forma
  de todas sus lecturas. PostgreSQL conserva la autoridad final sobre los
  *rangos de valor* (`hr_valor > 0`, `spo2_valor` entre 0 y 100,
  `mov_valor >= 0`) y sobre la integridad referencial. Restarle esos rangos a la
  base habría significado abrir una segunda copia del criterio clínico dentro de
  la API.
- **Tres reglas se duplican a propósito** —forma biométrica, sincronización no
  anterior a la captura, y fin de sesión no anterior a su inicio—, porque
  convertir un error opaco de la base en un 422 que nombra el campo vale más que
  la línea ahorrada. Los rangos numéricos **no** se duplican.
- **Coherencia `tipo_sesion` ↔ forma de las lecturas.** `SIGNOS_MATERNOS` solo
  admite lecturas de HR/SpO₂ y `MOVIMIENTOS_FETALES` solo lecturas de
  movimiento. Ningún CHECK de SQL puede expresarlo, porque la regla cruza
  `sesion_monitoreo` y `lectura_biometrica`: el validador de Pydantic es el
  único lugar donde vive.
- **Regla de la semana 20 sin duplicar el umbral.** Los movimientos fetales solo
  se registran desde la semana gestacional 20. El docstring de
  `LecturaBiometrica` ya asignaba explícitamente esa regla a la capa de
  servicio, y aquí se aplica **reutilizando de solo lectura** la constante
  `SEMANA_MINIMA_DE_MOVIMIENTO` que ya declara `app.loader.dataset`. No se
  ejecuta el cargador ni se modifica nada de SCRUM-61; una prueba verifica que
  lo único que el endpoint toma de ese módulo es esa constante.
- **El endpoint no clasifica el semáforo ni deriva la semana gestacional.**
  Recibe `id_semaforo` e `id_tiempo_gest` ya decididos y solo comprueba que
  existan. El generador tampoco clasifica —parte de un estado ya elegido y
  fabrica valores compatibles con él—, así que no hay ninguna tabla de umbrales
  en el repositorio que copiar, y SCRUM-62 no inaugura una.
- **Estrategia de identificadores: los genera PostgreSQL.** `id_sesion` e
  `id_lectura` no forman parte de la entrada; salen de las secuencias `SERIAL` y
  se devuelven en la respuesta. Aceptarlos del cliente habría chocado con esas
  secuencias y obligado a reimplementar el ajuste que SCRUM-61 necesitó para
  cargar identificadores explícitos.
- **Una sola transacción por paquete, con un único `commit`.** El servicio hace
  `add` y `flush` y **no** hace `commit` ni `rollback`: la transacción pertenece
  a quien llama, igual que en el cargador. El dueño explícito es el router. El
  segundo `flush` es el que fuerza la evaluación de todos los CHECK y las llaves
  foráneas de las lecturas **antes** del commit, y es lo que hace atómico el
  paquete: si falla la tercera lectura de cinco, la sesión escrita un momento
  antes nunca se confirma.
- **`get_db()` solo abre y cierra.** No decide sobre la transacción, lo que deja
  la decisión visible en el router y permite que las pruebas sustituyan la
  dependencia por una sesión unida a una transacción que ellas revierten.
- **Semántica de respuestas:** `201` creado, `404` referencia inexistente, `409`
  conflicto de integridad, `422` contrato o regla del dominio incumplidos, `500`
  error interno. La respuesta de éxito devuelve solo `id_sesion`,
  `lecturas_creadas` e `ids_lectura`.
- **Clasificación de errores por `SQLSTATE`, en una función centralizada.**
  `23514` (CHECK) → 422, porque es un dato que el cliente envió mal; `23505`
  (UNIQUE/PK) y `23503` (FK, por carrera) → 409; `23502` (NOT NULL) → **500**,
  porque Pydantic ya garantiza los campos obligatorios y un `NULL` que llegue
  hasta la base delata un defecto del servidor, no del payload; `DataError` →
  **500** salvo que el valor pueda atribuirse al payload de forma controlada, y
  hoy no existe ese caso porque el contrato ya acota `mov_valor` al rango
  `SMALLINT`; cualquier otro código → 500. `clasificar_error_de_base` es pura y
  se prueba con errores fabricados.
- **Conflictos rechazados sin sobrescribir.** Un `409` nunca actualiza ni
  reemplaza nada, y un reenvío **no** se trata como éxito.
- **Idempotencia HTTP diferida a SCRUM-63.** No se implementan
  `Idempotency-Key`, tabla o caché de idempotencia, deduplicación ni
  reutilización de respuestas. Queda constancia de un hallazgo relevante para ese
  ticket: **ni `sesion_monitoreo` ni `lectura_biometrica` tienen hoy un UNIQUE
  fuera de su llave primaria**, así que no existe ningún identificador estable
  con el que reconocer un reenvío. SCRUM-63 necesitará una migración —un
  identificador externo único, o una tabla de idempotencia—. SCRUM-62 no cierra
  esa puerta: la respuesta ya devuelve los identificadores que una respuesta
  idempotente tendría que reutilizar, y `extra="forbid"` no impide añadir
  después un campo opcional nuevo.
- **Ningún mensaje del driver sale del proceso**, ni en la respuesta ni en el
  log. No se usa `str(excepción)` ni `repr(excepción)` en ninguna parte: el
  cuerpo lleva mensajes fijos escritos en el proyecto, y el log solo registra
  campos elegidos uno por uno —clase de la excepción, `SQLSTATE`, y los nombres
  de restricción, tabla y columna que reporta PostgreSQL—. El texto del driver
  cita la sentencia y sus parámetros, y esos parámetros son el paquete. Tampoco
  se adjunta `exc_info`: la traza de un error de base arrastra la sentencia en
  sus marcos.
- **Sin autenticación y sin despliegue productivo.** El endpoint no tiene JWT ni
  RBAC —corresponden a un ticket posterior— y **no es apto para producción**. Se
  ejecuta solo en el entorno controlado, nunca expuesto a una red pública.
- **No se creó ninguna migración.** El esquema desplegado soporta el endpoint tal
  como está.
- **No se añadió ninguna dependencia.** FastAPI, Pydantic, SQLAlchemy, psycopg,
  pytest y httpx ya instalados fueron suficientes.
- **`http.HTTPStatus` en vez de `fastapi.status`.** La constante de 422 está
  deprecada en la versión de Starlette instalada; la de la biblioteca estándar
  no emite advertencia y no cambia con la versión del framework.

### Validación

Las pruebas se reparten en tres archivos con responsabilidades distintas.
`test_ingestion_schemas.py` valida el contrato sin FastAPI ni base de datos, e
incluye pruebas que confirman que un `spo2_valor` de 150 **sí** atraviesa el
contrato, para que la decisión de dejar los rangos a PostgreSQL no se pierda por
descuido en un cambio futuro. `test_ingestion_api.py` aísla la capa HTTP con
dobles: comprueba la traducción de cada `SQLSTATE`, que el `commit` ocurre una
sola vez y que hay `rollback` ante cualquier fallo, y que ni la respuesta ni el
log filtran la contraseña, la URL, el SQL o la traza de un error fabricado que
los lleva a propósito. También verifica, sobre el árbol sintáctico, que los
módulos que atienden la petición no invocan el cargador ni crean esquema.

`test_ingestion_api_postgresql.py` ejecuta el ciclo real contra PostgreSQL 16 con
`SCRUM62_TEST_DATABASE_URL`, sin recurso alternativo a `DATABASE_URL`. No ejecuta
ninguna sentencia DDL: cada prueba abre una transacción exterior que revierte
siempre, y la `Session` que atiende la petición se une a ella con
`join_transaction_mode="create_savepoint"`. Ese modo es explícito y necesario:
con el valor por omisión la Session caería en `rollback_only`, y entonces el
`rollback()` del endpoint arrastraría también la transacción exterior, borrando
las filas de referencia y haciendo imposible distinguir «revirtió el paquete» de
«revirtió todo». Con el SAVEPOINT, el `commit` del endpoint es real y observable
pero no sobrevive. Las filas de referencia son ficticias, con valores UNIQUE
prefijados por el ticket, y las del catálogo se reutilizan si ya existen, de modo
que la suite corre igual sobre la base vacía de CI que sobre una de desarrollo.
En CI comparte la misma base efímera de SCRUM-52 y SCRUM-61, y su reporte JUnit
se suma a la verificación que pone el job en rojo si alguna prueba queda omitida.

**Colisión real de llave primaria.** Como el cliente no envía identificadores y
no hay UNIQUE fuera de la PK, un choque solo puede provocarse adelantando la
secuencia de `id_sesion` para que el siguiente `nextval` devuelva un valor ya
ocupado. La prueba crea primero una sesión legítima por el endpoint, adelanta la
secuencia hasta el `id_sesion` de esa sesión y reenvía un segundo paquete
deliberadamente distinto en tipo, estado, fechas y forma biométrica. El
resultado es `409`, la fila previa queda idéntica campo por campo, no aparece
una segunda sesión, no queda ninguna lectura del paquete rechazado, la respuesta
no es un 2xx ni devuelve identificadores —un reenvío no es un éxito
idempotente— y no filtra SQL, credenciales, nombre de restricción ni `SQLSTATE`.

Tres salvaguardas rodean la maniobra. La secuencia se descubre desde el catálogo
con `pg_get_serial_sequence`, en vez de componer su nombre a mano. Su estado
`(last_value, is_called)` se fotografía antes y se restaura después con `setval`
en un `try/finally` que falla explícitamente si la restauración no funciona; esa
restauración corre con `lock_timeout`, porque `ALTER SEQUENCE` toma un bloqueo
exclusivo y así un problema de orden se manifiesta como error y nunca como un
bloqueo indefinido. Y una prueba aparte mide que el rollback de la transacción
exterior deshace el `ALTER SEQUENCE` por sí solo, antes de que la red de
seguridad actúe, de modo que un fallo del aislamiento se vería en lugar de
quedar tapado. Verificado también desde fuera de las pruebas: la secuencia
`operacional.sesion_monitoreo_id_sesion_seq` queda en el mismo estado antes y
después de la suite completa.
