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
  `23514` (CHECK) → 422, porque es un dato que el cliente envió mal; `23503`
  (FK, por carrera) → 409; `23505` (UNIQUE/PK) → **500** *(corregido en la
  revisión del PR #10; ver más abajo)*; `23502` (NOT NULL) → **500**, porque
  Pydantic ya garantiza los campos obligatorios y un `NULL` que llegue hasta la
  base delata un defecto del servidor, no del payload; `DataError` → **500**
  salvo que el valor pueda atribuirse al payload de forma controlada, y hoy no
  existe ese caso porque el contrato ya acota `mov_valor` al rango `SMALLINT`;
  cualquier otro código → 500. `clasificar_error_de_base` es pura y se prueba
  con errores fabricados.
- **Nada se sobrescribe nunca.** Ningún camino actualiza ni reemplaza una fila
  existente: o se crea el paquete completo, o no queda nada.
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
secuencia hasta el `id_sesion` de esa sesión y envía un segundo paquete
deliberadamente distinto en tipo, fechas y forma biométrica. El resultado es
`500` —es una secuencia desincronizada, no algo que el cliente pudiera haber
hecho—, la fila previa queda idéntica campo por campo, no aparece una segunda
sesión, no queda ninguna lectura del paquete fallido, la respuesta no es un 2xx
ni devuelve identificadores, y no filtra SQL, credenciales, nombre de
restricción ni `SQLSTATE`.

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

### Correcciones de la revisión del PR #10

La revisión encontró cinco invariantes que el endpoint no estaba sosteniendo.
Ninguna necesitó migración: el esquema ya las soportaba, lo que faltaba era
comprobarlas.

- **El dispositivo debe estar asignado a ese embarazo durante la sesión.**
  Comprobar por separado que el embarazo existe y que el dispositivo existe no
  demuestra que ese dispositivo se le hubiera entregado a esa gestante:
  cualquier combinación de dos filas válidas pasaba. `AsignacionDispositivo` es
  justamente la tabla que los une, y los une **durante un período**, así que la
  regla es temporal: la asignación tiene que empezar antes o el mismo día que la
  sesión, y si tiene `fecha_fin`, terminar después o el mismo día que ella. Una
  asignación sin `fecha_fin` sigue vigente y cubre cualquier sesión posterior a
  su inicio.

  Deliberadamente **no** se mira `activo`. Ese campo describe el préstamo
  *ahora*, y juzgar con él una sesión pasada invalidaría retroactivamente todas
  las lecturas tomadas durante un préstamo que después se cerró. Hay una prueba
  dedicada a eso: una asignación con `activo=False` que sí cubría las fechas se
  acepta.

  Una consulta por paquete, no por lectura. Si el paquete es imposible, el
  código es `422`: las dos referencias existen, lo que no se sostiene es la
  combinación.

- **`id_tiempo_gest` se valida contra el embarazo y la fecha de captura.**
  Antes solo se comprobaba que la fila del catálogo existiera, lo que
  demostraba qué semana *declaraba* el cliente, no en qué semana estaba el
  embarazo cuando se capturó la lectura. Ahora la semana se calcula desde
  `Embarazo.fecha_inicio` con la misma aritmética que usa el generador
  —semanas completas transcurridas, contando la primera como semana 1— y se
  exige que coincida con la del catálogo.

  Esto arregla dos cosas a la vez. La primera es la veracidad de la propia
  dimensión: una lectura archivada en la semana 31 pero capturada en la 12
  corrompería cualquier agrupación gestacional que la ETL construya después. La
  segunda es la regla de la semana 20, que hasta ahora era **trivial de
  esquivar**: bastaba con apuntar `id_tiempo_gest` a cualquier semana ≥ 20 y un
  movimiento capturado en la semana 12 entraba sin problema. Aplicada sobre la
  semana calculada, esa puerta se cierra, y hay una prueba que lo comprueba
  intentando exactamente ese engaño.

  También se rechaza de forma controlada una captura anterior al inicio del
  embarazo o que caiga fuera del rango 1-42 que admite el catálogo.

  Sin consultas nuevas: la fecha de inicio del embarazo se lee en la misma
  consulta que prueba que existe, y las semanas del catálogo ya venían en lote.

- **`23505` sobre las llaves primarias generadas es un error interno, no un
  conflicto del cliente.** Un duplicado normalmente significa «mandaste algo que
  ya está», y `409` sería la respuesta honesta. Aquí no puede significar eso: el
  cliente no envía `id_sesion` ni `id_lectura`, las dos llaves salen de
  secuencias de PostgreSQL, y ninguna de las dos tablas tiene una clave de
  negocio con la que pudiera chocar. Una llave duplicada solo puede describir
  una secuencia que quedó por detrás de las filas guardadas, que es un fallo de
  este lado. Devolver `409` culparía al cliente de un problema del servidor y,
  además, se leería como «detecté tu reenvío», que es precisamente lo que
  SCRUM-62 **no** hace. Pasa a `500`. Cuando SCRUM-63 introduzca un
  identificador que envíe el cliente, un `409` real será posible y esta decisión
  volverá a mirarse.

  `23503` (llave foránea por carrera) se queda en `409`: es lo único que hoy
  puede provocar el cliente y merecer ese código.

- **Cada lectura tiene que haberse capturado dentro de su sesión.** Se validaba
  que la sincronización no precediera a la captura y que la sesión no terminara
  antes de empezar, pero nada ataba la lectura a la sesión en el tiempo: una
  sesión de media hora podía traer una lectura capturada semanas antes o
  después. Ahora `fecha_hora_captura` debe caer entre `fecha_inicio` y
  `fecha_fin`, extremos incluidos.

  `fecha_hora_sincronizacion` queda fuera de la regla a propósito: sincronizar
  mucho después de que la sesión terminó es el comportamiento normal de un
  sistema pensado para conectividad intermitente, no una anomalía.

- **`estado_sesion` y `fecha_fin` tienen que decir lo mismo.** No se inventa una
  máquina de estados: se aplica literalmente la semántica que ya documenta
  `SesionMonitoreo`, donde `fecha_fin` permanece NULL mientras la sesión sigue
  `PENDIENTE` o quedó `INTERRUMPIDA`. Por tanto esos dos estados no admiten
  `fecha_fin`, y `COMPLETADA` y `PROCESADA` la exigen: una sesión que declara
  haber terminado tiene que decir cuándo. Omitir el estado se valida como
  `PENDIENTE`, porque es lo que la base va a guardar, así que omitirlo y mandar
  `fecha_fin` es tan incoherente como declararlo.

### Lo que SCRUM-62 sigue sin hacer con un reenvío

Conviene dejarlo por escrito sin ambigüedad, porque la redacción anterior de
este documento se prestaba a leerse al revés: **este endpoint no reconoce
reenvíos en absoluto**. El mismo JSON enviado dos veces crea dos sesiones, con
dos `id_sesion` distintos, y las dos respuestas son `201`. No hay detección, ni
deduplicación, ni reutilización de respuestas, y un `409` nunca significa «era
un reenvío». Hay una prueba de integración dedicada a dejar constancia de ese
comportamiento, para que nadie lo suponga distinto.

La idempotencia HTTP real sigue siendo alcance de SCRUM-63, y necesitará una
migración que añada la clave que hoy no existe.

### Datos de prueba corregidos

La fixture de PostgreSQL describía una combinación imposible: un embarazo
iniciado el 2025-08-01 con capturas el 2026-03-01 declaradas como semana 41,
cuando esa fecha cae en la **semana 31**, y sin ninguna `AsignacionDispositivo`
que prestara el dispositivo a esa gestante. Las pruebas pasaban porque el
endpoint no comprobaba ninguna de las dos cosas.

Ahora la fixture crea la asignación y deriva las semanas de las fechas: dos
ventanas, una en la semana 31 (2026-03-01, por encima del umbral de movimiento)
y otra en la semana 12 (2025-10-17, por debajo), ambas calculadas a mano desde
la fecha de inicio del embarazo y no copiadas de la implementación que
verifican.

## SCRUM-63 — Idempotencia y manejo de duplicados en la API

SCRUM-62 dejó dicho, y probado, que el endpoint no reconocía reenvíos: el mismo
JSON enviado dos veces creaba dos sesiones. Esta entrada registra lo que se
implementó para cambiarlo.

La garantía que ofrece el endpoint se enuncia así, y conviene no exagerarla:
**para una misma clave y este mismo recurso, la API no crea dos veces el paquete
confirmado y reproduce su resultado con los mismos identificadores**. No se
afirma «exactly once»: un cliente que no reutilice su clave obtiene, con toda
razón, una operación nueva.

### Decisiones aprobadas

- **Unidad idempotente: el paquete completo.** Una sesión con todas sus
  lecturas, que es exactamente la unidad que ya era atómica. Ni la sesión sola
  ni una lectura suelta: cualquier otra granularidad obligaría a inventar
  identidad para filas que no la tienen y a partir la transacción única.
- **Fuente: la cabecera `Idempotency-Key`, obligatoria.** Va en la cabecera y no
  en el cuerpo por tres razones: es **metadato de transporte** —identifica el
  envío, no describe la sesión ni las lecturas—, y mantenerlo fuera del cuerpo
  deja el contrato de dominio sin campos que no le pertenecen; el cuerpo actual
  declara `extra="forbid"`, así que aceptarla ahí obligaría a añadirla al
  schema; y **la clave no forma parte de la huella del payload**, que describe
  solo el contenido que se persiste.
- **Ámbito: `(recurso, clave)`.** El recurso es la constante
  `POST /api/v1/sesiones-monitoreo`. Se descartó acotar por dispositivo: ese
  identificador viaja **en el cuerpo**, que es justo lo que se está
  deduplicando, así que el ámbito de la clave habría dependido de un campo del
  contenido, y la misma clave con otro dispositivo habría creado un registro
  nuevo en vez de colisionar. El ámbito se decide antes de mirar el cuerpo.
- **Validación de la cabecera: `^[A-Za-z0-9_-]{8,128}$`,** anclado y aplicado
  con `fullmatch`. El máximo no es un número suelto: es el ancho de la columna,
  importado en vez de repetido, así que una clave que pasa la validación siempre
  cabe donde va a guardarse. El alfabeto cubre un UUID canónico y un token
  base64url **sin padding**: el carácter `=` no está permitido.
- **Clave ausente o mal formada → `400`,** no `422`. En este proyecto `422`
  significa, de forma consistente, «el cuerpo no cumple el contrato o rompe una
  regla del dominio»; una cabecera que falta es un defecto de encuadre, anterior
  al cuerpo. Para conseguirlo la cabecera se lee de `Request` en una dependencia
  —un `Header(...)` obligatorio produciría `422`— y el parámetro se declara a
  mano en el `openapi_extra` del endpoint, de modo que el esquema publique
  `required: true` y no contradiga al comportamiento.
- **Precedencia comprobada, no supuesta.** FastAPI resuelve y ejecuta las
  subdependencias **antes** de validar el cuerpo, así que una solicitud sin
  clave y con un cuerpo inválido se responde `400`. Se midió con la versión
  instalada y hay una prueba de regresión que lo fija.
- **Canonicalización sobre el modelo Pydantic ya validado, no sobre los bytes.**
  La pregunta no es «¿mandó los mismos bytes?» sino «¿esto se guardaría igual?».
  Hashear el cuerpo tal como llegó habría convertido en `409` un reenvío que
  solo reordenó sus propiedades JSON o escribió `97` donde antes iba `97.00`.
  Las reglas, todas con prueba propia:
  - **defaults efectivos**: omitir `estado_sesion` u `origen_dato` equivale a
    enviar el valor que aplica el modelo, porque la fila resultante es la misma;
  - **decimales** cuantizados a la escala real de `NUMERIC(5, 2)` y escritos
    como texto, porque un número JSON no puede llevar la escala;
  - **datetimes** normalizados a UTC, ya que `TIMESTAMPTZ` guarda el instante y
    no el offset con que se escribió — la misma regla que ya aplica
    `normalizar_valor` en el cargador de SCRUM-61;
  - **enums** por su `value`; **`null`** se conserva como `null` y nunca se
    convierte en cero ni en cadena vacía; **enteros** siguen siendo enteros;
  - **el orden de las lecturas se conserva y jamás se ordena**, porque
    `ids_lectura` vuelve en ese orden y dos órdenes son dos respuestas;
  - `fecha_hora_sincronizacion` **entra** en la huella como cualquier otro
    campo. Se persiste, así que dos paquetes que difieren en ella se guardan
    distinto. La consecuencia es una regla para el cliente, no una excepción
    aquí: la clave identifica un paquete inmutable, y un reenvío repite el
    paquete que preparó en vez de volver a sellarlo.
- **Huella SHA-256** del texto canónico, en hexadecimal, con `hashlib` de la
  biblioteca estándar.
- **Tabla `operacional.idempotencia_solicitud`**, en una migración propia
  (`87d8ed46686b`, sobre `150788f88be7`). Va en una tabla independiente y no
  como columnas sobre las existentes: la clave y su huella son **metadatos de
  transporte e idempotencia**, no atributos clínicos, y mezclarlos con las
  entidades del dominio los volvería parte de su modelo. Así el modelo
  operacional heredado queda intacto: ninguna de sus tablas cambia.
- **`UNIQUE (recurso, clave)` es la garantía, y la única.** Ni un diccionario en
  memoria, ni un lock de Python, ni una consulta previa. Hay una consulta previa
  —la vía rápida— pero es una optimización que evita que un reenvío secuencial
  escriba nada; no es lo que decide.
- **Se almacenan `id_sesion` e `ids_lectura`** para reconstruir la respuesta.
  `ids_lectura` es un `INTEGER[]` con el orden del paquete: reconstruirlo con
  `ORDER BY id_lectura` habría dependido de que el orden de inserción coincida
  con el de la secuencia, cierto hoy pero propiedad del código y no del esquema.
  `lecturas_creadas` **no** es columna: es la longitud de ese array, y una
  segunda copia solo podría contradecir a la primera.
- **Reclamación con `INSERT ... ON CONFLICT (recurso, clave) DO NOTHING
  RETURNING`,** y no capturando `IntegrityError`. Tres razones: no levanta
  `23505`, así que el mapa de SQLSTATE de SCRUM-62 sigue significando lo mismo
  —una llave duplicada en esas tablas sigue siendo una secuencia
  desincronizada, y sigue siendo `500`—; deja la transacción utilizable, de modo
  que el perdedor puede leer al ganador sin revertir antes; y la exclusión mutua
  la aplica PostgreSQL, no este proceso.
- **La reclamación va antes de `verificar_referencias`.** Es lo que convierte
  «se tomó la clave y luego el trabajo falló» en una situación que ocurre de
  verdad y puede probarse, y lo que hace que un duplicado se detenga antes de
  hacer trabajo que va a descartar.
- **Recuperación del ganador en la misma transacción, sin rollback previo.** Un
  retorno vacío del `ON CONFLICT` indica que otra reclamación ganó la carrera, y
  de ahí salen dos caminos:
  - **flujo normal** — la reclamación ganadora está confirmada, y el `SELECT`
    posterior la encuentra porque bajo READ COMMITTED cada sentencia toma un
    snapshot nuevo. Se recupera su resultado y se reproduce;
  - **anomalía defensiva** — si esa fila no aparece, o aparece con el resultado
    incompleto, no hay respuesta que reproducir y se responde `500` saneado.
    No debería ocurrir mientras el código sea dueño de su transacción y el
    `RESTRICT` proteja la reclamación, pero se comprueba en vez de suponerse.

  **No hay reintento en ninguna parte**: un competidor que aborta no deja fila
  viva, PostgreSQL reevalúa dentro de la misma sentencia y el `INSERT` tiene
  éxito en vez de no hacer nada. Hay una prueba concurrente que lo verifica.
- **La huella se compara de forma estricta**, y la integridad del resultado se
  comprueba **antes** que la huella. Un `409` afirma «tu clave ya nombra un
  paquete *distinto*, y la respuesta de aquel paquete se mantiene»; una fila sin
  resultado no sostiene esa afirmación, así que comparar huellas primero habría
  culpado al cliente de un estado que es del servidor.
- **Semántica de respuestas:** primera vez `201` con `Idempotency-Replayed:
  false`; reenvío equivalente `201` con el mismo cuerpo y `Idempotency-Replayed:
  true`; misma clave con otro contenido `409`; reclamación existente pero
  incompleta `500` saneado, **nunca** un replay inventado ni un `409`. La
  cabecera de respuesta se documenta en OpenAPI bajo el `201`, con sus dos
  valores.
- **El `commit` y todos los `rollback` siguen siendo del router.** El flujo
  completo —vía rápida, reclamación, referencias, sesión, lecturas y
  finalización— vive en `procesar_ingesta_idempotente`, que no confirma ni
  revierte y deja pasar todas las excepciones. Exactamente un `commit` cuando se
  crea, exactamente un `rollback` en cualquier otro camino, incluidos el replay
  y la colisión.
- **Una sola transacción.** Reclamación, verificación de referencias, sesión,
  lecturas y enlace del resultado se confirman juntos, y un fallo posterior a la
  reclamación **la retira también**. Ésa es la razón de que la clave no quede
  envenenada, y por eso se descartó el patrón clásico de dos fases —reclamar y
  confirmar por separado—, que envenena la clave si el proceso muere entre
  ambos commits.
- **El `UPDATE` de finalización comprueba su `rowcount`.** Si no toca
  exactamente una fila se levanta una anomalía y se revierte: confirmar una
  sesión cuya reclamación no dice nada dejaría una inconsistencia silenciosa que
  el siguiente reenvío leería como error interno.
- **`ON DELETE RESTRICT` desde la reclamación hacia la sesión.** Con `CASCADE`,
  borrar una sesión habría liberado en silencio la clave que la identificaba, y
  el siguiente reenvío habría creado una segunda sesión. PostgreSQL lo impide.
- **PostgreSQL es la autoridad de concurrencia.** Se descartaron Redis, locks
  distribuidos, locks de Python, colas externas, outbox, reintentos automáticos
  con backoff y SQLite: ninguno hacía falta —la restricción `UNIQUE` ya resuelve
  la exclusión— y todos habrían añadido infraestructura que este entorno
  controlado no necesita. No se añadió ninguna dependencia nueva.
- **Nada se sobrescribe.** No existe ningún camino que actualice una sesión ya
  creada a partir de un reenvío.
- **Trazabilidad con logs seguros.** Se registran tres eventos —replay, colisión
  y anomalía— con el recurso y un `clave_hash`: los 12 primeros caracteres del
  SHA-256 de la clave. El campo se llama `clave_hash` y no `clave` justamente
  para que nadie confunda lo registrado con la clave. Qué es y qué no es, sin
  adornos:
  - es un **identificador abreviado de correlación**, para seguir una misma
    clave entre varias líneas de log;
  - **evita registrar la clave en claro**, que es texto arbitrario del cliente;
  - **puede colisionar**: 12 caracteres hexadecimales no son una identidad
    única, y dos claves distintas podrían compartir prefijo;
  - **no es un secreto ni una garantía de anonimización.** Una clave predecible
    —un contador, una fecha, un identificador de dispositivo— podría
    confirmarse por tanteo comparando su hash con el registrado. Por eso el
    cliente debe generar **claves de alta entropía**, y un UUID versión 4 es la
    opción recomendada.

  **Nunca** se escriben la clave en claro, la huella del paquete, ningún valor
  clínico, SQL, parámetros, la URL de conexión, el texto del driver ni trazas.
  `AuditoriaLog` **no** se tocó: la auditoría persistente corresponde al ticket
  de autenticación.
- **Lo que deliberadamente no existe**, para que nadie lo suponga leyendo lo
  anterior: no hay **contador de replays** ni ninguna columna que lleve la
  cuenta de cuántas veces se reprodujo una respuesta; y no hay **expiración ni
  TTL** de las claves —el esquema no la implementa, y no se definió política de
  retención, purga ni limpieza—. Si alguna de las dos hiciera falta, sería una
  decisión propia con su migración.
- **Compatibilidad con SCRUM-64 y SCRUM-65: solo como contrato.** Lo que aquí
  queda fijado es qué deberá enviar un futuro nodo edge —una clave por paquete,
  guardada junto al paquete en su cola de pendientes, reenviada sin volver a
  sellarla—. **El nodo edge no existe**, ni su cola, ni la sincronización
  diferida: nada de eso se implementó en este ticket.

### Validación

Tres archivos nuevos, con responsabilidades separadas, además de los ajustes a
los heredados.

`test_idempotencia.py` valida la canonicalización y la huella con funciones
puras: sin FastAPI, sin base de datos y sin dobles. La pregunta que responde
cada caso es la misma —¿estos dos paquetes se guardarían igual?— y las dos
direcciones importan: dos paquetes que se guardan igual con huellas distintas
convierten un reenvío legítimo en un `409`, y dos que se guardan distinto con la
misma huella harían que el segundo recibiera el resultado del primero,
descartando en silencio lo que el cliente envió.

`test_idempotencia_api.py` aísla el router con dobles y comprueba la decisión:
qué código responde cada situación, cuándo se confirma y cuándo se revierte. El
doble de la `Session` anota el **orden** de las sentencias, porque tres
propiedades del diseño son afirmaciones sobre el orden y no sobre cantidades: la
reclamación ocurre antes de verificar referencias y de escribir nada, la
recuperación del ganador ocurre sin un rollback previo, y un reenvío reconocido
no escribe una sola fila.

`test_ingestion_idempotency_postgresql.py` ejecuta el ciclo real contra
**PostgreSQL 16**, con `SCRUM63_TEST_DATABASE_URL` y sin recurso alternativo.
Cubre el ciclo de migración dirigido (`head → 150788f88be7 → head`, con
`alembic check` sin divergencias), la estructura física de la tabla leída del
catálogo, la creación, el replay, la colisión, dos claves distintas con el mismo
cuerpo, los fallos posteriores a la reclamación, la anomalía de una reclamación
incompleta sembrada a propósito y la integridad real —`RESTRICT`, `rowcount` y
la relación 1:N—.

**Concurrencia real.** Catorce de esas pruebas ejercen dos solicitudes
simultáneas, cada una con su propio engine, su propia conexión PostgreSQL, su
propio `TestClient` y una `Session` nueva; se verifica que los
`pg_backend_pid()` son distintos. Que dos peticiones salgan a la vez no
demuestra que se encuentren donde importa, así que la ganadora se detiene en un
punto conocido —posterior a su reclamación— y se comprueba con
`pg_blocking_pids` que **la perdedora quedó bloqueada por la transacción de la
ganadora**. No se usan sleeps fijos para asumir simultaneidad: se sondean
condiciones observables en los catálogos de PostgreSQL con intervalos breves
—20 ms— y un deadline que hace fallar la prueba en vez de continuar a ciegas.

Los tres escenarios: **idénticas** —dos `201`, una `false` y una `true`, cuerpos
e identificadores idénticos, una sesión y una reclamación—; **colisión** —un
`201` y un `409`, solo las lecturas del ganador, sin mezcla y sin revelar nada
del paquete anterior—; y **la ganadora que revierte**, donde la primera reclama
la clave y luego falla de verdad con un `404`, y al revertir, la segunda
adquiere la clave y completa. Ese último es el que sostiene la decisión de no
reintentar en ninguna parte. Se ejecutaron **12 veces consecutivas** sin un solo
fallo, dejando la base **sin filas residuales** cada vez.

El aislamiento difiere según el caso: las pruebas secuenciales reutilizan las
fixtures de SCRUM-62 —transacción exterior que siempre se revierte—; las
concurrentes no pueden, porque dos transacciones distintas no ven las filas sin
confirmar de una tercera, así que confirman sus referencias y luego borran
exactamente lo que crearon, en orden inverso de llaves foráneas. Sin `TRUNCATE`,
sin `DROP` y sin `create_all`.

En la reproducción local de los comandos del CI, las cuatro suites de PostgreSQL
se ejecutaron con **cero pruebas omitidas**, verificado sobre sus reportes JUnit.

### Integración continua

El workflow gana un quinto paso, `Pruebas de idempotencia contra PostgreSQL
(SCRUM-63)`, con su propia `SCRUM63_TEST_DATABASE_URL` construida de la misma
fuente única que las otras tres, y su reporte `pytest-scrum63.xml` incorporado
al guardián que pone el job en rojo si alguna prueba de PostgreSQL queda
omitida. El archivo se añade además a la lista de ignorados del bloque offline.

Va el último y en un paso propio a propósito: ejecuta DDL real y sus pruebas
concurrentes toman un candado exclusivo mientras dos peticiones esperan. En
serie es seguro; compartir la base con otra suite corriendo a la vez no lo
sería, y por eso no hay matriz ni paralelización.

## SCRUM-64 — Nodo edge simulado con SQLite y patrón outbox

SCRUM-63 dejó la API capaz de reconocer un reenvío. Esta entrada registra al
cliente que lo necesita: el nodo edge simulado, que acepta una sesión de
monitoreo **con la API apagada**, la conserva a través de un reinicio y la
entrega después sin que se registre dos veces.

La propiedad que se implementa conviene enunciarla con precisión, porque es
fácil prometer de más. **No** es «entrega exactamente una vez»: ningún cliente
puede garantizar eso sobre una red donde una respuesta se puede perder después
de que el servidor confirmó. Lo que sí se garantiza es:

> el paquete sobrevive localmente hasta que se confirma, un reintento repite
> exactamente la misma clave y los mismos bytes, y por eso el efecto de negocio
> en PostgreSQL ocurre una sola vez.

La otra mitad —algo que ejecute esa pasada por su cuenta— es trabajo posterior y
no se implementó aquí.

### Decisiones aprobadas

- **Unidad de captura: el paquete completo**, una `SesionMonitoreoEntrada` con
  todas sus lecturas. Es la misma unidad que el endpoint trata como una
  operación idempotente y la misma que ya era atómica en PostgreSQL. Partirla
  por lectura permitiría que una sesión quedara a medias en el servidor, y
  ningún reintento la recompondría.

- **SQLite, y no un archivo JSON ni un diccionario en memoria.** La captura y su
  registro de envío tienen que escribirse **juntos o ninguno**, y sobrevivir a
  que el proceso se cierre. Eso es una transacción y una restricción, que es
  justo lo que un archivo plano no ofrece. Tampoco se añadió una cola externa
  —Redis, RabbitMQ, Celery—: sería infraestructura nueva para un prototipo de un
  solo escritor, y la garantía que hace falta ya la da el propio archivo.

- **Dos tablas: `captura_local` y `outbox`.** El paquete es inmutable desde que
  se acepta; el estado de entrega cambia en cada intento. Separarlos hace que
  «el payload y la clave no cambian entre intentos» sea visible en el esquema en
  lugar de ser una promesa del código. `outbox.id_captura` es `UNIQUE`, así que
  un paquete no puede encolarse dos veces.

- **Integridad en el esquema, no en Python.** Llave primaria por fila, llave
  foránea de la outbox hacia su captura, `UNIQUE` sobre la referencia y sobre la
  clave, `NOT NULL` donde hace falta y cinco `CHECK`. `PRAGMA foreign_keys = ON`
  se activa en **cada** conexión —es por conexión y viene apagado—, y fuera de
  toda transacción, porque SQLite ignora el pragma dentro de una y no avisa.

- **La restricción de evidencia remota, escrita con `CASE`.** Redactada como
  bicondicional sobre el `AND` de las tres columnas admitía evidencia a medias:
  con el estado distinto de `ENVIADO` bastaba que una fuera `NULL` para que el
  lado derecho resultara falso, los dos lados coincidieran y la fila entrara sin
  ruido. La forma `CASE` dice lo que se quería decir: la evidencia está completa
  exactamente cuando el estado es `ENVIADO`, y ausente por completo en cualquier
  otro. Hay pruebas para las cinco combinaciones parciales en los dos estados no
  enviados.

- **Identidad estable: un UUID4 canónico, generado una sola vez.** Sus 36
  caracteres cumplen el `^[A-Za-z0-9_-]{8,128}$` del servidor, no contienen
  nombre, dato clínico ni credencial, y se persisten en la misma transacción que
  el paquete. Esa cadena es la `Idempotency-Key` de todos los intentos: no
  cambia tras un timeout, un reinicio, un `5xx` ni un replay. Una clave nueva con
  el mismo cuerpo **no** es un reenvío —para la API es una operación nueva y
  crearía otra sesión—, y evitar exactamente eso es la razón de que la identidad
  se cree una vez y se guarde antes del primer intento.

- **Serialización durable: `model_dump_json()` del contrato aprobado.** Se validó
  con `SesionMonitoreoEntrada` —el mismo modelo que valida la API— y se guarda su
  propio volcado, que es también lo que se envía como cuerpo, byte por byte. Se
  comprobó experimentalmente lo que hacía falta comprobar: la ida y vuelta por
  SQLite **conserva la huella del servidor**, y el texto recuperado vuelve a
  validar contra el contrato, incluido su `extra="forbid"`. Sin esa propiedad, un
  reenvío legítimo llegaría como contenido distinto bajo la misma clave y sería
  un `409`. No se escribió un segundo esquema local: dos definiciones del mismo
  contrato acabarían separándose.

- **`fecha_hora_sincronizacion` se congela, no se prohíbe.** Ese campo forma
  parte de la huella del servidor, y por eso el riesgo es concreto: un instante
  estampado en el primer envío y refrescado en un reintento convertiría un
  reenvío legítimo en `409`, justo después de una pérdida de respuesta, que es
  cuando más falta hace que funcione. Lo que elimina ese riesgo es que el valor
  **no cambie entre intentos**, y guardar el paquete validado una sola vez ya lo
  garantiza. De ahí dos comportamientos, ninguno de los cuales necesita una
  regla propia:

  1. **El nodo nunca lo estampa.** Una captura sin conexión no se ha
     sincronizado, así que el campo queda en `null` —es lo que produce el
     simulador y lo que muestran los ejemplos—. El instante de la entrega
     confirmada se registra en `outbox.enviado_en`, que es donde ese hecho
     ocurre.
  2. **Un archivo de entrada puede traerlo ya informado**, y si el contrato lo
     acepta, la captura lo acepta: se guarda sin alterarlo y se reenvía idéntico
     en cada intento.

  Se evaluó rechazar todo paquete con ese campo informado y **se descartó**: el
  edge habría aplicado un contrato más estricto que el de la API a la que
  alimenta, negando paquetes que el endpoint sí acepta, y ese segundo juego de
  reglas habría quedado libre de separarse del primero. Lo que se rechaza lo
  rechaza `SesionMonitoreoEntrada` —una sincronización anterior a su captura, o
  sin offset—, y este módulo no lo reescribe.

- **Tres estados y ni uno más: `PENDIENTE`, `ENVIADO`, `FALLIDO`.** Declarados
  una sola vez en `app.edge.estados`, y el `CHECK` que limita la columna se
  genera de ese enum, así que no pueden separarse. No se importa ni se deriva de
  `EstadoSesion`: que ambos tengan un `PENDIENTE` es una coincidencia de nombre
  entre un estado clínico y uno de entrega.

- **`FALLIDO` lleva un indicador `reintentable`**, obligatorio en ese estado y
  prohibido en los otros. Es lo que separa «otra pasada puede intentarlo» —fallo
  de transporte, `5xx`— de «hace falta que alguien lo mire» —`409`, `404`,
  `422`, una respuesta que no cumple el contrato—. Sin esa distinción, o se
  reintenta para siempre algo que siempre será rechazado, o se abandona algo que
  solo necesitaba otra oportunidad.

- **`ENVIADO` es terminal, y lo garantiza el `UPDATE`, no una convención.** La
  guarda viaja en el `WHERE`:

  ```sql
  UPDATE outbox SET ... WHERE id_outbox = ? AND estado <> 'ENVIADO'
  ```

  Esto cierra una carrera concreta y fácil de pasar por alto: el emisor A recibe
  su `201` y marca `ENVIADO`; el emisor B, que llevaba rato esperando en un
  socket que acabó en timeout, escribe `FALLIDO` sobre la misma fila. El paquete
  *sí* se entregó, PostgreSQL lo tiene una vez, y el estado local diría que no.
  Ningún `busy_timeout` lo evita: no hay contención de bloqueo, hay dos
  escritores y uno trabaja con información vieja. Con la guarda, `rowcount = 0`
  significa «otro ya lo entregó», que no es un error y se reporta como tal. Por
  la misma razón `intentos` se incrementa en SQL (`intentos = intentos + 1`) y no
  leyendo y reescribiendo desde Python, que es como se pierden los contadores.

- **Sin `EN_PROCESO`, sin lease y sin lock.** Serían maquinaria para un problema
  que dos palabras en un `WHERE` ya resuelven, y añadirían un cuarto estado que
  el ticket no pide. `busy_timeout` se conserva, pero solo para lo suyo: que un
  segundo emisor invocado por error falle con un mensaje legible en vez de
  quedarse esperando.

- **Versionado con `PRAGMA user_version`, sin un segundo framework de
  migraciones.** Un entero basta para un esquema con una versión, y Alembic
  gobierna PostgreSQL, que es otra responsabilidad. Se comprobó que el pragma
  **participa de la transacción**: crear las dos tablas y estampar la versión
  ocurre en un solo `BEGIN IMMEDIATE`…`COMMIT`, así que no puede quedar un
  archivo que se declare versión 1 con medio esquema dentro.

- **La inicialización rechaza en lugar de reparar.** Solo hay un camino que
  escribe —archivo sin estrenar y sin tablas—; los demás se niegan con un
  mensaje legible: tablas propias sin versión (esquema anterior al versionado o
  parcial), tablas ajenas (el archivo es de otra base), versión desconocida, o
  versión correcta con un esquema que no coincide. No existe ningún `DROP`,
  `TRUNCATE` ni `DELETE`, y hay una prueba que lo verifica sobre el árbol
  sintáctico del módulo.

- **La verificación del esquema comprueba garantías, no nombres.** Columnas con
  su tipo y su `NOT NULL`, llaves primarias, los `UNIQUE` que SQLite realmente
  está aplicando —leídos de sus propios índices, no del texto—, las llaves
  foráneas y su destino, y por último la definición almacenada, que es el único
  lugar donde un `CHECK` puede inspeccionarse porque ningún `PRAGMA` los expone.
  Las expectativas están escritas a mano y no derivadas del DDL: derivadas
  coincidirían por construcción y no probarían nada. Se verificó que detecta un
  `UNIQUE` retirado, un `NOT NULL` retirado, una llave foránea retirada, un
  `CHECK` borrado, un cuarto estado colado en el `CHECK` y una columna de más.

- **Un `201` no basta por sí solo.** Se exigen cinco condiciones juntas: el
  código es exactamente `201`; `Idempotency-Replayed` está presente y vale
  `false` o `true`; el cuerpo valida como `SesionMonitoreoCreada`;
  `lecturas_creadas` coincide con la cantidad de `ids_lectura`; y
  `lecturas_creadas` coincide con **las lecturas del paquete que se envió**. La
  quinta es la que impide aceptar como confirmación una respuesta impecable que
  describe otro paquete. Se deriva del payload guardado en el momento de usarla,
  no de una columna con el conteo: es la misma razón por la que
  `lecturas_creadas` tampoco es columna en `idempotencia_solicitud`, porque una
  segunda copia de un número solo puede acabar discrepando de la primera.

- **`201` con `Idempotency-Replayed: true` es una entrega, y un `409` nunca lo
  es.** El replay significa que el paquete está en PostgreSQL exactamente una vez
  y que esos son sus identificadores.

- **Un `409` no se diagnostica más allá.** El endpoint lo usa para dos
  situaciones distintas —colisión de idempotencia y una referencia que
  desapareció en una carrera— y no publica un código de error que las separe: lo
  único que difiere es prosa en español. Analizarla haría que el cliente se
  rompiera el día que alguien mejore una frase. Las dos se tratan igual y de
  forma conservadora: no se confirma, no se reintenta sola, se conservan clave y
  payload, y queda para revisión.

- **Un `5xx` es reintentable pero no se rotula «temporal».** El endpoint también
  responde `500` ante una reclamación idempotente incompleta, que no tiene nada
  de transitorio. Queda elegible para otra pasada explícita, y nada más.

- **Un fallo de transporte deja el resultado remoto en «desconocido».** Es la
  ventana que da sentido al ticket: la petición pudo confirmarse y perderse solo
  la respuesta. Se conservan la misma clave y el mismo cuerpo, y la siguiente
  pasada obtiene un replay con los mismos identificadores.

- **Una sola pasada, finita y explícita.** `enviar` intenta una cantidad acotada
  de eventos elegibles, en orden FIFO determinista (`ORDER BY id_outbox`), y
  termina. No hay bucle, ni temporizador, ni sondeo de conectividad, ni
  *backoff*. La pasada **sí** se detiene ante un fallo de transporte —la API no
  está accesible y los demás eventos chocarían con la misma pared, con un timeout
  cada uno—, pero no ante un `409` o un `5xx`, que son propiedades de un paquete
  y no deben ocultar la cola que viene detrás.

- **Ningún bloqueo de SQLite se mantiene durante la red.** Los elegibles se leen
  en una sentencia que termina antes de la primera petición, y cada resultado se
  escribe en su propia transacción corta. Hay una prueba en la que otra conexión
  escribe en la base mientras el emisor está «en la red».

- **Cliente HTTP síncrono, con `httpx`**, que ya era dependencia directa del
  backend. No se añadió ninguna dependencia: `MockTransport` para guionar
  respuestas y fallos, y `TestClient` —que *es* un `httpx.Client`— para conducir
  la aplicación real. El cliente se **inyecta**, así que no existe una sola línea
  de código de producción escrita para las pruebas. Se descartó `ASGITransport`
  por ser asíncrono, y con él la opción de un cliente `async`, que no aportaría
  nada a un comando finito sobre una base SQLite síncrona.

- **El edge nunca abre una conexión a PostgreSQL.** No importa `sqlalchemy` ni
  `psycopg` en ninguno de sus módulos ni en su CLI, y hay una prueba que lo
  comprueba sobre el árbol de importaciones. Todo lo que llega al servidor pasa
  por la API.

- **Errores saneados, con dos cuidados propios de este ticket.** El texto de una
  `ValidationError` de Pydantic incluye `input_value`, y para este contrato ese
  valor es el dato clínico; por eso el mensaje se reconstruye con solo la
  ubicación del campo y el tipo de error. Y un `detail` que llega como **lista**
  —el `422` automático de FastAPI— no se guarda en absoluto, porque sus entradas
  repiten el valor rechazado; se registra su forma, no su contenido. Un `detail`
  de texto sí se conserva, truncado: son mensajes que este proyecto escribió.

- **La base local es configurable y no se versiona.** `EDGE_SQLITE_PATH`,
  `EDGE_API_BASE_URL` y `EDGE_HTTP_TIMEOUT`, en una clase de configuración
  propia con prefijo `EDGE_`, separada de la del backend para que el edge no
  pueda heredar ni filtrar `database_url`. `.gitignore` gana una sola regla
  estrecha, `data/edge/`, que cubre la base y sus auxiliares `-wal`/`-shm` sin
  ampliar patrones globales; sigue el precedente de `data/generated/`.

- **Sin WAL.** No aporta nada a un prototipo de un solo escritor y añadiría dos
  archivos auxiliares más que gestionar. Se dejó el journal por omisión.

### Lo que se reserva para la sincronización automática

No se implementó, y no debe darse por implementado: servicio permanente o
demonio, detección o sondeo de conectividad, *scheduler*, reintentos automáticos
programados, *backoff* exponencial, *jitter*, `next_attempt_at`, política de
límite de intentos, reconciliación periódica, trazabilidad de extremo a extremo,
métricas operativas y orquestación de varios nodos.

La columna `intentos` se **cuenta** pero no decide nada: convertirla en política
es exactamente el trabajo siguiente. La estructura queda preparada para ello sin
anticiparlo.

### Validación

Cinco archivos de pruebas nuevos: cuatro que no necesitan servidor
—almacenamiento y esquema, captura, emisor y clasificación, y el comando— y uno
que ejerce el ciclo real contra PostgreSQL 16.

Las pruebas de persistencia usan **archivos SQLite reales** bajo `tmp_path`,
nunca `:memory:`: lo que hay que demostrar es que un evento sobrevive a que el
proceso se cierre, y una base en memoria desaparece con la conexión, así que
probaría lo contrario de lo que se afirma. Cada prueba tiene su propio archivo.

La suite integrada declara su propia `SCRUM64_TEST_DATABASE_URL` y su propio
`engine_de_pruebas`, y reutiliza sin cambiarlas las fixtures de SCRUM-62
—`conexion_revertida`, `sesion_de_pruebas`, `referencias` y los constructores de
paquetes—. Como esas fixtures piden el engine **por nombre**, hay una prueba que
deja explícita esa atadura: si alguien retirara el engine de este módulo, las
fixtures caerían en silencio sobre el de SCRUM-62 y la suite seguiría en verde.

La ventana de confirmación perdida se reproduce con una subclase de
`TestClient` que deja pasar la petición —el endpoint procesa y PostgreSQL
confirma— y después lanza `ReadTimeout` en lugar de devolver la respuesta. La
prueba comprueba las dos mitades: que la respuesta interna **fue un `201` real**
y que, pese a ello, el evento local no quedó marcado como enviado. La pasada
siguiente obtiene el replay, con los mismos identificadores y una sola sesión en
PostgreSQL.

La guarda de `ENVIADO` se validó además con un **control negativo**: retirada del
`UPDATE`, las dos pruebas de secuencia concurrente fallan; restaurada, pasan.

### Integración continua

El workflow gana un sexto paso, `Pruebas del nodo edge contra PostgreSQL
(SCRUM-64)`, con su propia `SCRUM64_TEST_DATABASE_URL` construida de la misma
fuente única que las anteriores, y su reporte `pytest-scrum64.xml` incorporado al
guardián que pone el job en rojo si alguna prueba de PostgreSQL queda omitida. El
archivo se añade además a la lista de ignorados del bloque offline, donde sí
corren las cuatro suites locales del edge.

Va después de SCRUM-63 por la misma razón por la que aquel iba el último:
aquella capa ejecuta DDL real y toma candados exclusivos, y compartir la base con
otra suite corriendo a la vez no sería seguro. SQLite no necesita ningún servicio
adicional en el runner: cada prueba crea su archivo dentro del `tmp_path` que le
da pytest.


## SCRUM-65 — Sincronización diferida, reintentos y trazabilidad extremo a extremo

SCRUM-64 dejó un nodo que conserva un paquete y sabe entregarlo cuando alguien se
lo pide. Lo que faltaba es la política: **qué hacer cuando la API no responde**,
cuántas veces insistir, cuánto esperar entre intentos, cuándo dejar de insistir, y
cómo saber después qué ocurrió con cada evento. Eso es este ticket.

La garantía que se puede afirmar no cambió, y conviene repetirla porque es fácil
prometer de más:

```
entrega al menos una vez desde el edge
+ efecto de negocio una sola vez en PostgreSQL
+ evidencia correlacionada de cada etapa
```

No se promete «exactamente una llamada HTTP». Una petición puede llegar al
servidor aunque el edge nunca vea la respuesta.

### Decisiones aprobadas

**La política de reintentos vive en un solo módulo.** `app/edge/politica.py`
decide si queda otro intento y cuánto se espera; el emisor, el sincronizador, la
reconciliación, el CLI y las pruebas preguntan ahí. Dos fórmulas equivalentes en
dos módulos es exactamente como una acaba divergiendo de la otra.

**`max_attempts` incluye el primer intento**, y se dice explícitamente en el
README, en la ayuda del CLI y en el docstring del módulo, porque es la clase de
detalle que cada lector supone al revés.

**La fórmula es `delay(k) = min(base × 2^(k-1), techo)`, con `k` = ordinal del
intento que acaba de fallar.** La definición alternativa —«los intentos ya
consumidos»— produce un desfase de uno que sólo se nota con eventos migrados: uno
con tres intentos heredados hace el número 4, y le corresponde `delay(4)`, no
`delay(3)`. Hay cuatro pruebas dedicadas exclusivamente a ese desfase.

**Sin *jitter*.** Un solo nodo no tiene manada que dispersar, y añadirlo
complicaría las pruebas sin aportar nada demostrable.

**`base_delay_seconds = 0` se rechaza.** Con 0 todas las demoras valen 0 y no hay
espera incremental, que es un criterio de aceptación del ticket; una configuración
capaz de contradecir lo que el ticket promete no debería poder construirse. No se
admite «sólo para pruebas» porque el sleeper se inyecta: ninguna prueba necesita
ese valor para no dormir.

**La finitud se valida antes que el signo.** `NaN < 0` es falso y `NaN >= 0`
también, así que una comprobación de signo escrita primero aceptaría un `NaN` en
silencio. `math.isfinite` va delante de todo, y hay una prueba por cada campo y
por cada uno de los tres valores no finitos.

**Un `float` finito y positivo no basta: la duración tiene que ser
*programable*.** Rechazar `<= 0` y no finito dejaba pasar dos bordes que sólo se
ven cuando el número entra en un `timedelta`, que es exactamente donde acaban las
tres duraciones configurables:

- `timedelta(seconds=5e-324).total_seconds()` vale `0.0`. Una espera positiva se
  convertía en ninguna espera. Y no es sólo el subnormal: `timedelta` **redondea**
  al microsegundo más cercano en vez de truncar, así que toda la franja por debajo
  de 1 µs programa algo distinto de lo configurado —cero de medio microsegundo
  hacia abajo, y 1 µs entre medio microsegundo y uno—.
- `timedelta(seconds=1e308)` lanza `OverflowError`. Ocurría **a mitad de una
  pasada**, con el evento ya reclamado y el intento ya contado.

De ahí sale un intervalo cerrado, y las dos cotas se eligen por motivos
distintos. El mínimo, `timedelta.resolution` = **1 µs**, no es una decisión: es
lo que la biblioteca sabe representar. El máximo, **86 400 s (24 h)**, sí lo es.
No se usó el máximo de `float` ni `timedelta.max` porque serían cotas falsas —una
espera de mil años es representable y no la programa nadie—; `sincronizar` es un
comando finito que una persona lanza y espera, y una sola espera de más de un día
sobrevive a cualquier sesión manual plausible y a la marca de agua de la propia
ejecución. Con la base mínima, 24 horas siguen dejando sitio a 36 duplicaciones,
muchas más de las que consume cualquier `max_attempts` sensato.

**El lease es derivado, y también tiene que caber.** `duracion_del_lease` es
`4 × http_timeout + 30`, y desborda mucho antes que el campo del que sale:
`4 * 1e308` ya es `inf`. Se valida como una duración más, con lo que aparece un
límite implícito —`http_timeout ≤ 21592.5 s`— que el mensaje de error explica en
lugar de dejar al lector adivinar por qué un valor admisible falla.

**Todo se comprueba en `__post_init__`, y esa es la decisión de fondo.** La
alternativa era capturar `OverflowError` en cada punto que suma una duración a un
instante —el emisor, el sincronizador, `outbox.reclamar_intento`—, y eso son tres
sitios que pueden divergir y un fallo que llega cuando el evento ya está
reclamado. Validando al construir, el rechazo ocurre antes de que exista una
petición HTTP, antes de incrementar `intentos`, antes de insertar una fila en
`intento_sincronizacion` y antes de mover ningún estado: la política que existe
es utilizable, y no hay un solo `except OverflowError` en el paquete. Hay una
prueba que lo afirma comparando la base entera —`captura_local`, `outbox` e
`intento_sincronizacion`— antes y después de intentar sincronizar con seis
configuraciones inválidas distintas.

**Consecuencia sobre la regresión del tope fijo.** El caso que la demostraba
—base `2**-100`, techo `1.0`— ya no es configurable, porque esa base está
veinticinco órdenes de magnitud por debajo del mínimo. El peor caso que queda
dentro del contrato es el intervalo entero, base 1 µs contra techo 24 h, donde
caben 36.33 duplicaciones; es decir que **hoy** un `EXPONENTE_MAXIMO = 60` no se
notaría. Se conserva igualmente el cálculo derivado con `math.frexp`, por dos
razones: sigue siendo lo que hace `demora(k)` exacta y libre de desbordamiento
para *cualquier* ordinal, y no depende de que esas dos constantes se queden donde
están. Las pruebas se reescribieron para fijar eso —que la saturación cae donde
la base dice— y no el número 60.

**El límite se persiste por evento, en `max_intentos_aplicado`.** Se fija al
reclamar el primer intento y no vuelve a cambiar. Así, editar `EDGE_MAX_ATTEMPTS`
alcanza a los eventos que aún no han empezado y no reescribe retroactivamente el
contrato de los que están en curso. Un evento migrado cuyos intentos heredados ya
igualan el límite que adoptaría se cierra explícitamente como
`AGOTAMIENTO_HEREDADO` **sin crear otro intento**, en lugar de quedar bloqueado
para siempre.

**El agotamiento no inventa un estado.** `FALLIDO` con `reintentable = 0` ya
significaba «requiere revisión» en SCRUM-64, y la selección ya lo excluía. Lo
único que se añade es `motivo_revision`, con vocabulario cerrado, para que la
traza distinga un agotado de un rechazado. Se persiste en vez de deducirse porque
deducirlo dependería del `EDGE_MAX_ATTEMPTS` vigente al mirarlo, y un evento no
debería cambiar de diagnóstico porque alguien editara un `.env`.

**El intento se cuenta al empezar, no al guardar el resultado.** Un proceso que
muere a mitad de la petición no dejaba rastro en SCRUM-64 y podía repetir eso
indefinidamente sin gastar presupuesto. El coste es que un intento de desenlace
desconocido también consume límite, que es la lectura honesta: bien pudo llegar al
servidor. Como consecuencia, `marcar_enviado` y `marcar_fallido` dejaron de
incrementar el contador.

**El ordinal sale de `UPDATE ... RETURNING`.** La reclamación es a la vez el
compare-and-set y la fuente del número. La alternativa —actualizar y después
consultar el contador— sería correcta sólo por el `BEGIN IMMEDIATE`, es decir, por
un argumento sobre semántica de bloqueos en lugar de por una propiedad de una sola
sentencia. Exige SQLite 3.35, y `conectar` lo comprueba y rechaza con una frase en
vez de dejar aparecer un error de sintaxis a mitad de una sincronización.

**«Elegible» se declara una vez.** `predicado_elegible()` renderiza la cláusula y
la usan la selección, la reclamación y el censo. Los paréntesis alrededor de los
dos estados elegibles no son cosméticos: `AND` liga más fuerte que `OR`, y sin
ellos el límite de intentos y la fecha se aplicarían sólo a la rama `FALLIDO`, de
modo que un `PENDIENTE` podría reintentarse sin límite alguno.

### El lease local, y lo que no demuestra

Un intento **en vuelo** y uno **abandonado** son indistinguibles sin una ventana
temporal, y las dos salidas son malas: o se reintenta sobre intentos vivos
—multiplicando peticiones y quemando el límite con fallos fantasma— o no se
recupera nunca de una caída del proceso.

La solución es un lease local mínimo: `reconciliable_en`, calculado **al reclamar
el intento** con el timeout vigente entonces y persistido. Cambiar la
configuración después no puede hacer que un intento activo parezca abandonado
antes de tiempo ni retrasar arbitrariamente su recuperación.

```
duracion_lease = 4 × http_timeout + 30 s
```

Es una **heurística conservadora de recuperación**, no un máximo real de la
petición ni una propiedad garantizada del cliente HTTP: httpx no impone una fecha
límite total, y sus timeouts de lectura y escritura acotan la inactividad entre
fragmentos. Una respuesta puede llegar después de que el lease venza, y el diseño
lo trata como caso normal.

**La seguridad no descansa en el lease.** Descansa en la misma `Idempotency-Key`,
en las guardas por ordinal, en las guardas de terminalidad y en el tratamiento de
resultados tardíos. El lease sólo reduce la frecuencia con la que hay que lidiar
con uno.

No es un lock distribuido —vive en el mismo archivo SQLite y vence solo— y no es
un estado: `EstadoEntrega` no cambió. Se expresa como «existe un intento abierto y
sin reconciliar», un hecho que el historial ya registra, y el índice parcial
`ux_intento_abierto` lo convierte en una garantía de la base: **como máximo un
intento abierto por evento**.

### Resultados tardíos

| Estado del evento | Resultado tardío | Efecto |
| --- | --- | --- |
| agotado o rechazado | `ENTREGADO` | **prevalece**: pasa a `ENVIADO` y se limpian las columnas de revisión |
| agotado o rechazado | fallo | se guarda en el historial y **no toca la outbox** |
| `ENVIADO` | cualquiera | se guarda en el historial; la guarda impide degradar la entrega |

Una confirmación válida es verdad: PostgreSQL tiene la sesión, y dejar la fila
diciendo lo contrario sería mentir sobre un hecho comprobado. Un fallo tardío, en
cambio, no puede reabrir un evento que la reconciliación ya cerró; eso produciría
un intento N+1 por encima del límite. La regla está además en SQL —`NOT (estado =
'FALLIDO' AND reintentable = 0)` en `marcar_fallido`— para que la propiedad no
dependa de que la rama de Python sea correcta. Se validó con un control negativo:
retirada la guarda, la prueba falla; restaurada, pasa.

**Un resultado real nunca se descarta en memoria.** Si el proceso recibió una
respuesta, el historial la conserva aunque el intento ya estuviera reconciliado:
tirar esa evidencia sería lo contrario de lo que este ticket produce. Lo que el
carácter tardío gobierna es si la **outbox** puede tocarse, no si el hecho se
registra.

**Varias respuestas `ENTREGADO` para un mismo evento son legítimas.** Reconciliado
el intento 1 puede iniciarse el 2, y ambos pueden devolver un `201` válido —uno
inicial y otro *replay*—. Por eso no hay unicidad sobre `resultado = 'ENTREGADO'`:
la habría, y habría reventado con un error de integridad justo en el camino más
importante del ticket. Lo que sí es único es `confirmo_transicion = 1`, el intento
que **aplicó** la transición local, y de él se deriva la fecha de confirmación.

### Los cuatro timestamps

| Fecha | Fuente única |
| --- | --- |
| captura | `captura_local.capturado_en`, leído justo **antes** de abrir la transacción |
| intento | `intento_sincronizacion.iniciado_en`, una fila por intento |
| confirmación | `finalizado_en` del intento con `confirmo_transicion = 1` |
| sincronización | `outbox.enviado_en`, instante registrado para la transición local |

La confirmación se **deriva** en lugar de copiarse a la outbox. Dos copias del
mismo instante en dos tablas no se pueden mantener iguales con ningún `CHECK` de
SQLite, y la solución que sí lo intentaba obligaba además a condicionar
`evidencia_remota_coherente` para que un `ENVIADO` heredado —sin intentos— no
hiciera abortar la migración entera. Derivarla eliminó las dos cosas: la
restricción de SCRUM-64 quedó **idéntica**, carácter por carácter.

`enviado_en` no es «el instante posterior al `COMMIT`»: se obtiene del reloj dentro
de la transacción que ejecuta la transición, necesariamente antes de que el commit
termine. Se documenta así y no de otra forma.

PostgreSQL conserva su propia evidencia temporal en
`idempotencia_solicitud.fecha_hora`. Son relojes distintos y las pruebas los
correlacionan sin fingir que coinciden.

### La correlación es la clave, y no se creó otra

Se reutiliza la `Idempotency-Key` como identidad idempotente **y** como
identificador de correlación técnica. Nace en el edge antes del primer envío, está
persistida en SQLite, viaja en cada petición, está persistida en PostgreSQL por
SCRUM-63, identifica la misma operación lógica durante todos los reintentos y no
contiene información clínica ni personal.

```
SQLite.clave_idempotencia → HTTP Idempotency-Key
  → operacional.idempotencia_solicitud (recurso, clave) → id_sesion + ids_lectura
```

No se añadió `X-Correlation-ID`, ni otro UUID, ni una columna en PostgreSQL, ni
una migración del servidor. **PostgreSQL no cambió en absoluto en este ticket.**
En logs y en la traza se muestra como `correlation_id`, pero el valor es
exactamente la clave ya persistida.

### El bucle, y por qué pregunta a la cola

Decidir que se ha terminado porque «la ronda no seleccionó nada» falla justo
después de un reinicio: con todos los reintentables programados para el futuro, la
ronda no selecciona nada y una ejecución que debía esperar se iría dejando la cola
intacta. Así que cada iteración toma un **censo** —elegibles ahora, programados,
con intento abierto, enviados, en revisión, bloqueados— y decide desde ahí.

`ENVIADO` y «requiere revisión» son ambos terminales y se cuentan **por separado**:
agruparlos habría permitido que una ejecución terminara con eventos agotados
informando éxito.

**La guarda de progreso sólo castiga la inercia.** Una modificación durable la
reinicia; una espera **positiva** también, porque dormir es avance temporal normal
y no estancamiento. Sólo una iteración que no modificó nada, no reclamó nada y no
esperó incrementa el contador. Contar las esperas habría matado una ejecución con
techo de un segundo y lease de setenta a los cuatro segundos, y hay una prueba
dedicada a ese caso exacto.

**Un fallo de transporte pausa la ejecución entera, no la ronda.** SCRUM-64 rompía
la pasada para no estrellar N timeouts seguidos contra una API caída; envuelto en
un bucle, el censo veía los otros eventos elegibles y abría otra ronda de
inmediato, de modo que cincuenta eventos significaban cincuenta fallos de conexión
consecutivos. Ahora la ronda devuelve hasta cuándo pausar y el sincronizador
retiene toda la ejecución. La pausa vive **en memoria**: es lo que este proceso
aprendió sobre la API hace un instante, no un hecho durable sobre ningún evento. Y
no bloquea la reconciliación, que es SQLite puro y debe seguir corriendo para que
un lease pueda vencer.

**Una marca de agua sobre `id_outbox`** acota cada ejecución a lo que existía al
empezar, de modo que un proceso que siga capturando no la prolongue
indefinidamente.

**`batch_limit` es el tamaño de una ronda**, no el máximo de intentos ni el total
de la ejecución. Lo que acota la ejecución es la marca de agua.

### Clasificación de respuestas

La clasificación siguió siendo la de `cliente.py`: no se escribió una segunda
tabla de decisiones. Dos ajustes:

- **`500 <= codigo < 600`.** Faltaba la cota superior, así que un código no
  estándar `600` o `999` se clasificaba como reintentable. El contrato promete
  reintentos para la familia `5xx`, y nada en un código fuera del estándar promete
  que volver a intentarlo sirva de algo: ahora cae en «respuesta inesperada».
- **`408` y `429` siguen siendo permanentes**, por decisión consciente y probada.
  Este endpoint no implementa ni timeouts de petición ni *rate limiting*, así que
  tratarlos como recuperables añadiría un camino que ninguna prueba podría ejercer
  contra el servidor real y abriría la cuestión de honrar `Retry-After`, que no se
  implementa.

El resultado **desconocido** no estrenó un miembro del enum. Sigue siendo
`REINTENTABLE` con `codigo_http = None`, y esa combinación recibió un nombre
—`es_resultado_desconocido`— para que la ronda, la pausa y la traza dejaran de
re-derivarla cada una por su cuenta.

### Coherencia de `ultimo_http` y `ultimo_error`

Se escriben **siempre juntos**, describiendo el mismo intento. La reconciliación
guarda `ultimo_http = NULL` en sus dos ramas: dejar un `503` del intento anterior
junto a «proceso interrumpido» sería un par que nunca ocurrió.

`AGOTAMIENTO_HEREDADO` es la excepción deliberada: **no toca ninguno de los dos**.
Son evidencia real del último intento de v1, y el motivo del cierre no es un
resultado sino un límite que se adopta; eso ya lo dice `motivo_revision`.

### Evolución del esquema SQLite: v1 → v2

SQLite no sabe añadir un `CHECK` a una tabla existente, así que las cinco
invariantes nuevas obligan a reconstruir `outbox`. La reconstrucción renombra la
tabla **vieja** y crea la nueva **directamente con su nombre definitivo**.

El orden no es estilístico. `ALTER TABLE outbox_v2 RENAME TO outbox` hace que
SQLite almacene `CREATE TABLE "outbox" (...)`, **con comillas**, y
`verificar_esquema` compara contra el texto que el módulo escribe: una migración
construida así habría producido una base que la ejecución siguiente rechaza, un
fallo que sólo aparece con datos reales. Se comprobó empíricamente antes de
elegir, y hay una prueba que compara el DDL de una base nueva con el de una
migrada.

El único `DROP` del módulo retira la copia renombrada, dentro de la transacción y
después de haber insertado sus filas en la nueva tabla. La prueba de SCRUM-64 que
prohibía toda sentencia destructiva no se relajó: se **estrechó** para exigir que
ésa sea la única, que apunte a la tabla temporal, que viva sólo dentro de
`_migrar_v1_a_v2` y que el `INSERT` la preceda.

Valores iniciales: `proximo_intento_en` a `NULL`, `motivo_revision` a
`RECHAZO_PERMANENTE` para lo que ya estaba en revisión, `max_intentos_aplicado` a
`NULL` y `intentos_heredados = intentos`.

**Los intentos heredados no se reinventan ni se reinician.** v1 contaba intentos
pero no guardaba su detalle, y ninguna fecha ni ningún resultado pueden
reconstruirse para algo que nunca se registró. Se conserva el total, cuenta para el
límite, y la invariante de coherencia se reformuló para cubrir los dos orígenes con
una sola regla:

```
count(intento_sincronizacion) == outbox.intentos - outbox.intentos_heredados
```

Para un evento nativo el término heredado es 0 y la regla se reduce a la original.
El primer intento v2 de un evento migrado continúa la numeración —el número 4 si
traía tres— y el hueco 1-3 es la señal honesta de que no hay detalle.

### Lo que se decidió NO hacer

Sin demonio ni servicio de Windows, sin scheduler permanente, sin detección previa
de conectividad —que un *health check* responda no garantiza que el request
posterior funcione—, sin cola externa, sin Redis, RabbitMQ, Kafka, Celery ni MQTT,
sin OpenTelemetry, sin locks distribuidos, sin estado `EN_PROCESO`, sin WAL, sin
cambios en PostgreSQL, sin migraciones de Alembic, sin autenticación, sin purga
automática de la outbox y sin refactorizaciones ajenas al ticket.

### Validación

Cuatro archivos de pruebas nuevos sin servidor —política, migración local del
esquema, sincronización y traza— y uno que ejerce el ciclo resiliente completo
contra PostgreSQL 16.

Las pruebas de persistencia usan **archivos SQLite reales** bajo `tmp_path`, nunca
`:memory:`, por la misma razón que en SCRUM-64: lo que hay que demostrar es que un
evento sobrevive a que el proceso se cierre.

**Ninguna prueba duerme.** El reloj y el sleeper se inyectan; el sleeper anota lo
que recibe y adelanta un reloj falso, de modo que una espera de setenta segundos
cuesta lo mismo que una de uno y la secuencia exacta de demoras queda observable.
La suite integrada usa `SCRUM65_TEST_DATABASE_URL`, sin recurso alguno a
`DATABASE_URL` ni a la variable de otra suite, y reutiliza sin cambiarlas las
fixtures de SCRUM-62 con la misma prueba de atadura al engine.

### Integración continua

El workflow gana un séptimo paso, `Pruebas de sincronización contra PostgreSQL
(SCRUM-65)`, con su propia variable construida de la misma fuente única y su
reporte `pytest-scrum65.xml` incorporado al guardián que pone el job en rojo si
alguna prueba de PostgreSQL queda omitida. Va después de SCRUM-64 por la misma
razón de siempre: en serie es seguro compartir la base efímera, en paralelo no lo
sería. Las cuatro suites nuevas sin servidor entran en el bloque offline.

## SCRUM-69 — Esquema analítico y proceso ETL reproducible

Hasta aquí el sistema guardaba lecturas; ninguna estaba lista para analizarse.
Este ticket materializa el modelo dimensional aprobado —el Star Schema v6 del
Capítulo III— y el ETL que lo alimenta desde `operacional`. Las fuentes de
autoridad se separaron así: el **modelo objetivo** lo fijan el diagrama v6, el
Capítulo III y su Tabla de Umbrales; **qué datos existen** lo fijan los modelos,
las migraciones y el dataset; **cómo se implementa sin romper nada** lo fijan
Alembic, las pruebas y el CI. Una columna que no existe en `operacional` no se
elimina del modelo si el Capítulo III dice que el ETL debe derivarla.

### Decisiones aprobadas

**Un esquema propio, `analitico`, y un registro declarativo propio.** Las nueve
tablas viven en `BaseAnalitica` (`app/db/base_analitica.py`), no en `Base`: el
contrato de 23 tablas operacionales sigue describiendo exactamente lo mismo, y
las pruebas analíticas son un contrato aparte. Ambos registros comparten la
convención de nombres. Los nombres físicos son snake_case; los del diagrama
(`Fact_LecturaBiometrica`, `Dim_Paciente`...) son los nombres lógicos y se
documentan.

**Grano: una fila del hecho por lectura operacional**, con la misma
`id_lectura` como clave. Las dimensiones reutilizan el identificador
operacional: sin claves sustitutas, sin secuencias, sin SCD tipo 2, sin staging
permanente. Las dimensiones son tipo 1.

**`id_sesion`, refinamiento aditivo.** El diagrama no lo tiene; se añadió como
dimensión degenerada, `INTEGER NOT NULL`, indexada y sin llave foránea, porque
contar filas del hecho no es contar sesiones (1,180 lecturas, 732 sesiones) y el
análisis de adherencia necesita `COUNT(DISTINCT id_sesion)`. No cambia el grano.
Debe reflejarse después en el diagrama.

**`id_tiempo_gestacional` apunta a `id_tiempo_gest`.** No se «unificaron» los
nombres: una llave foránea no necesita llamarse como la clave que referencia, y
el diagrama ya los define distintos.

**Correcciones físicas, nunca cambios de negocio.** Donde un tipo del diagrama
podía truncar o redondear un valor que `operacional` acepta, se usó el más
amplio: `NUMERIC(5,2)` para HR y SpO₂ (el diagrama decía `INT` y
`DECIMAL(4,1)`), `TIMESTAMPTZ` para `fecha_hora` (el diagrama decía `TIMESTAMP`),
`BIGINT` para `id_lectura` como dibujado, y en los textos las longitudes
operacionales —nombre completo 243 = 4 × 60 + 3 separadores, teléfonos 120,
correo 120, clínica 150, provincia y distrito 60, factores 50 y 150, mensaje
255, versión 30, descripciones `TEXT`—. `especialidad` es `VARCHAR(100)`: el 504
del diagrama se trató como error tipográfico. Donde el tipo dibujado ya era un
superconjunto seguro, se conservó (`VARCHAR(7)` para `codigo_nivel`, cuyo valor
más largo es «WARNING»).

**Sin llaves foráneas hacia `operacional`.** Dentro de `analitico` sí existen las
llaves dimensionales; ninguna sale del esquema. La trazabilidad se conserva con
las claves operacionales copiadas y la demuestra la conciliación. Así el
almacén no queda atado al ciclo transaccional.

**Dos llaves foráneas con nombre explícito.** La convención produciría 71 y 67
caracteres y PostgreSQL recorta en 63 sin avisar; se nombraron
`fk_fact_lectura_id_tiempo_gestacional` y
`fk_bridge_embarazo_factor_id_factor_riesgo`, y una prueba compara los nombres
desplegados con los declarados.

**Estados por métrica y semáforo global.** El ETL calcula `estado_hr`,
`estado_spo2` y `estado_mov` —el Capítulo III dice que el ETL «calcula el estado
clínico de cada variable»— con códigos `OK`, `WARNING` y `ERROR`, y `NULL`
cuando la métrica no aplica. El semáforo global es el estado más severo de las
métricas aplicables (OK < WARNING < ERROR), se resuelve contra `Dim_Semaforo` y
se **compara** con el `id_semaforo` que la lectura ya tiene en `operacional`: si
difieren, la ejecución falla y se revierte. Los conteos 826/295/59 se miden
sobre el semáforo analítico.

**Umbrales SIM-1.0, en un solo lugar.** `app/etl/reglas.py` es el único sitio
del repositorio donde viven:

- HR: `< 55` o `> 110` es ERROR, `55 ≤ HR < 60` y `100 ≤ HR ≤ 110` son WARNING,
  `60 ≤ HR < 100` es OK. La Tabla 4 pone el 100 en dos rangos: gana la mayor
  severidad.
- **`55 ≤ HR < 60` es WARNING, y la regla es exhaustiva.** Una primera versión
  trató ese intervalo como un hueco de la tabla y lo convirtió en un error de
  dominio que revertía la ejecución. La revisión cerró la regla: una bradicardia
  que todavía no alcanza el umbral de alerta es **precaución de tamizaje**, no
  un diagnóstico individual ni un dato imposible. Los intervalos de HR cubren
  ahora la recta continua, ningún valor detiene la carga por falta de regla y el
  pendiente «regla clínica para HR 55–59» queda cerrado.
- SpO₂: `< 92` ERROR, `92 ≤ SpO₂ < 95` WARNING, `≥ 95` OK. La frontera de la
  tabla es 95 y el valor es continuo: 94.5 es WARNING, no hay hueco.
- Movimientos: `≥ 10` OK, `5 ≤ mov < 10` WARNING, `< 5` ERROR, e inválido antes
  de la semana 20. La tabla habla de un «umbral por trimestre» sin números; 10
  es la constante con la que se construyó el dataset simulado, no una afirmación
  clínica universal. La función recibe el trimestre para que una versión futura
  pueda variarlo.
- La versión de las reglas debe coincidir con `version_referencia` del catálogo
  del semáforo, y la prioridad del catálogo con el orden de severidad.

El generador no se convirtió en clasificador. Una prueba comprueba que su umbral
coincide y que los valores que fabrica para cada nivel caen en ese nivel.

**Día clínico: America/Panama.** El médico de cada lectura es el del seguimiento
PRINCIPAL cuyo periodo cubre la fecha de captura convertida a la zona de Panamá,
porque las fechas de seguimiento son fechas clínicas de ese contexto. Uno se
usa; ninguno es un error de integridad; dos son una ambigüedad. No se elige «el
primero» ni el menor identificador, y no se consulta `activo`: un seguimiento
cerrado sigue cubriendo las lecturas tomadas mientras estuvo abierto. APOYO y
REEMPLAZO no se usan: ninguna fuente dice cómo sustituyen al PRINCIPAL.

**Clínicas y teléfonos derivados sin elegir.** La clínica del hecho es siempre
la del embarazo de esa lectura. `Dim_Medico.id_clinica` y
`Dim_Paciente.id_clinica` se derivan si la clínica es única (por
`medico_clinica` y por los embarazos de la paciente) y fallan si hay cero o
varias. El teléfono es el contacto `CELULAR` marcado como principal: uno se usa,
ninguno es NULL, dos fallan. Esas dos columnas de clínica son contexto, no una
segunda ruta de filtrado.

**`duracion_est_semanas` se deriva** de `fecha_probable_parto − fecha_inicio`,
en semanas enteras (280 días, 40 semanas, en todo el dataset). Un residuo no se
redondea: no hay regla aprobada, así que detiene la ejecución.

**`clasificacion_embarazo`, pendiente y visible.** Campo previsto por el modelo
dimensional v6. La versión actual de las fuentes de negocio no define una regla
de clasificación. SCRUM-69 conserva el atributo como nullable, sin CHECK de
valores, y no inventa semántica clínica. No es un NULL silencioso: cada
ejecución informa `clasificacion_embarazo_pendiente` (30 en la base canónica).
No se usa en criterios de aceptación, semáforos, filtros, clasificación clínica
ni seguridad por fila. Rellenarlo después es cambiar una función, no reconstruir
el almacén.

**Bridge con los atributos del diagrama.** `id_embarazo`, `id_factor_riesgo`,
`fecha_diagnostico`, `activo`, `observaciones` y una clave primaria compuesta
que garantiza la idempotencia. `fecha_fin` no se añadió. Una relación que
desaparece del origen no se borra: la conciliación la señala y la ejecución
falla, hasta que exista una política de borrado aprobada.

**Incrementalidad por anti-join.** Una lectura es nueva si su `id_lectura` no
está en el hecho. Se descartaron `MAX(id)` —pierde una transacción que toma un
identificador menor y confirma después—, la marca de agua por fecha clínica
—pierde una llegada tardía—, `fecha_hora_sincronizacion` —el edge la envía
NULL— e `idempotencia_solicitud.fecha_hora` —es la hora de la reclamación, no la
del commit, y no existe para lo que cargó SCRUM-61. El anti-join no ve una
lectura ya cargada que cambió; eso lo ve la conciliación completa, que falla sin
actualizar el hecho: los hechos son inmutables.

**Una transacción `REPEATABLE READ` cuya primera sentencia es el candado.**
`pg_try_advisory_xact_lock` se toma dentro de la misma transacción del ETL y
antes que cualquier otra consulta. No espera: si otra ejecución lo tiene, esta
termina de inmediato con código 4 sin haber leído ni escrito nada. Lo libera
PostgreSQL al confirmar o al revertir, así que **no hay ningún
`pg_advisory_unlock`** y ningún camino puede dejarlo tomado. Extracción, cargas
y conciliación ven la misma instantánea, y cualquier error revierte todo,
dimensiones y bridge incluidos.

Queda una ventana mínima, y es la razón por la que el hecho se inserta con un
`INSERT` normal: bajo `REPEATABLE READ` la instantánea se fija en esa primera
sentencia, así que una ejecución que arranque justo mientras otra confirma
podría fijarla un instante antes de ver ese commit y tomar el candado un
instante después de que se libere. Su anti-join seleccionaría filas ya
insertadas y la inserción chocaría con sus llaves primarias: la ejecución falla,
se revierte y basta con repetirla. Preferimos ese fallo ruidoso a un
`ON CONFLICT DO NOTHING` que lo convertiría en filas saltadas en silencio.

**Los hechos se insertan sin `ON CONFLICT`.** El anti-join selecciona las
lecturas que el hecho no tiene y el candado garantiza un único escritor; una
clave que aun así exista es una anomalía, no un caso previsto. La violación de
integridad se traduce a un error con su SQLSTATE —sin la sentencia ni sus
valores— y revierte la ejecución. Además se comprueba que las filas insertadas
sean exactamente las candidatas detectadas.

**El médico del hecho debe estar afiliado ese día a la clínica del embarazo.**
Al seguimiento PRINCIPAL vigente se le añade la comprobación de
`medico_clinica`: exactamente una afiliación cuyo periodo
(`fecha_inicio`–`fecha_final`, bordes inclusivos) cubra el día clínico de
Panamá, y que sea la clínica del embarazo. `activo` no filtra: una afiliación
cerrada sigue cubriendo los días en que estuvo abierta. Cero afiliaciones,
afiliación a otra clínica o dos afiliaciones aplicables detienen la ejecución;
como `medico_clinica` tiene clave (médico, clínica), dos aplicables son siempre
dos clínicas el mismo día, y ninguna fuente dice cuál gana. La conciliación
repite la comprobación en SQL.

**La clínica de `Dim_Medico` y `Dim_Paciente` admite NULL.** Es un atributo de
contexto: NULL significa que todavía no hay relación —una paciente sin embarazo
registrado, un médico sin afiliación—, nunca una clínica inventada, y no debe
usarse como ruta de filtrado. Una entidad maestra sin relación ya no detiene la
carga completa; dos clínicas distintas siguen siendo una ambigüedad que la
detiene. El médico al que apunta un hecho siempre tiene afiliación, y es la
comprobación por fecha la que lo garantiza.

**`tzdata` se declara en `requirements.txt`.** El día clínico usa
`ZoneInfo("America/Panama")`, y en Windows la base de zonas horarias llega solo
con ese paquete. Hasta ahora aparecía como dependencia transitiva de `psycopg`;
depender de eso es depender de un detalle ajeno. Se declara sin tope superior
porque su versión es el año de la base IANA: fijar un máximo dejaría de recibir
cambios de zonas.

**Sin `etl_log`.** Ninguna fuente vigente exige guardar el historial de
ejecuciones; la planificación antigua que lo mencionaba no prevalece sobre el
modelo aprobado. El progreso es el conjunto de claves del hecho, y la evidencia
reproducible es la salida estructurada del comando.

**Limpieza = validar y fallar.** El texto vigente del Capítulo III y de la
Figura 5 describe la limpieza como la eliminación de lecturas duplicadas o fuera
de rango. SCRUM-69 adopta deliberadamente una política más conservadora: el
origen ya impide duplicados y valores fuera de rango, y lo que las reglas no
admiten hace fallar la ejecución y provoca rollback, sin descartar la lectura en
silencio. Es una decisión de implementación consciente —una lectura que
desaparece sin dejar rastro es peor que una ejecución que se detiene y se
reejecuta corregida—, no una descripción de lo que hoy dice la tesis: el texto y
el diagrama quedan pendientes de actualización.

**Conciliación independiente.** `app/etl/conciliacion.py` no importa las reglas
ni la transformación: repite en SQL las derivaciones —nombre, teléfono, clínica,
médico responsable con `AT TIME ZONE 'America/Panama'`, duración— y compara. Los
umbrales no se duplican ahí; los estados por métrica se verifican con las
pruebas de frontera y, en SQL, con dos señales independientes: el global debe ser
el más severo y debe coincidir con el de origen. Dos verificaciones son
informativas: la semana recalculada con el día de Panamá (la API validó la
semana con el offset recibido, que `TIMESTAMPTZ` no conserva) y las sesiones sin
lecturas, que no generan hechos porque el grano es la lectura.

**Un comando batch, sin scheduler.** `scripts/etl_analitico.py` con dos órdenes,
`ejecutar` y `conciliar` (solo lectura). La cadencia nocturna del Capítulo III
es la de un proceso que invocaría este mismo comando. Códigos de salida: 0
éxito, 1 configuración o base de datos, 2 origen rechazado por las reglas, 3
conciliación fallida, 4 candado ocupado. La salida solo lleva identificadores,
conteos y códigos; un error de base de datos se describe sin sentencia ni
parámetros.

**Cambios mínimos en lo heredado.** `alembic/env.py` compara la base contra un
único `MetaData` que contiene una copia de las tablas de los dos registros, con
la convención de nombres compartida. No se usó la lista de metadatas que Alembic
también acepta, y el motivo quedó demostrado: Alembic solo aplica la convención
de nombres a sus operaciones (`op.create_table`) cuando `target_metadata` es un
`MetaData` que la lleva, y con una lista los CHECK de los enums de la primera
revisión se creaban con su nombre corto en una base nueva. La prueba de
migración de SCRUM-52 lo detectó; la suite de SCRUM-69 añade una prueba que lo
fija. Al copiar, cada llave foránea sin esquema recibe el de su propia tabla
(ninguna cruza de esquema), y los nombres, destinos y CHECK ligados a tipos de
la copia son idénticos a los de los registros. `test_migrations.py`
reconoce tres revisiones y aparta las sentencias del esquema analítico antes de
contar las operacionales —con una prueba que demuestra que solo apartó esas—.
La suite de SCRUM-63 sigue probando su propia revisión: la nombra, baja
explícitamente a la inicial en lugar de usar «-1» y comprueba que el head
desciende de ella. El cargador no se tocó.

### Validación

Cuatro archivos de pruebas nuevos sin servidor —reglas, transformación, contrato
del esquema y de su revisión, y comando— y uno contra PostgreSQL 16 que crea sus
propias bases temporales (`scrum69_tmp_`), las migra, carga el dataset canónico
y ejecuta el ETL con commits reales: primera carga, segunda ejecución idéntica,
incremento por el endpoint con *replay*, llegada tardía, identificador menor que
el máximo, lectura modificada, dimensión tipo 1, relación desaparecida, lectura
inválida con recuperación, semáforo discrepante, médico ambiguo, candado,
zona horaria y salida del comando sin datos personales. Solo elimina las bases
que creó en esa ejecución, comprobadas por nombre.

### Integración continua

El workflow gana un octavo paso, `Pruebas del esquema analítico y el ETL contra
PostgreSQL (SCRUM-69)`, con su propia variable `SCRUM69_TEST_DATABASE_URL`
—construida de la misma fuente única— y su reporte `pytest-scrum69.xml` en el
guardián de omisiones. Esa URL es solo la conexión de mantenimiento: la suite no
escribe sobre la base del servicio.

### Pendiente fuera del código

- Regla de negocio de `clasificacion_embarazo`.
- Umbrales de movimiento por trimestre, si el proyecto los define.
- Actualizar el diagrama v6 y el Capítulo III: `id_sesion`, las correcciones
  físicas de tipos y longitudes, la clave del bridge, TIMESTAMPTZ, SIM-1.0 y
  sus fronteras, America/Panama, y el comando como mecanismo del batch. El
  diccionario del Capítulo III describe `hr_valor` como frecuencia cardíaca
  fetal; el modelo la define materna.
