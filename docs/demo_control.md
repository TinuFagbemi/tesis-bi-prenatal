# Demo Control: preparación y controles de la sustentación (SCRUM-104)

Demo Control es una consola interna para quien prepara y presenta la
demostración técnica. Reúne en un solo punto de entrada los pasos que antes
había que recordar: comprobar el entorno, cortar y restablecer la comunicación
con el servidor central, previsualizar la próxima carga del ETL y ejecutarla.

**No es un componente de FetalAlert.** No es una interfaz para pacientes,
médicos ni administradores, no es un módulo de pruebas y no añade ninguna
funcionalidad al sistema: **orquesta mecanismos que ya existen** y no
reimplementa ninguno. Ninguna regla de clasificación, de sincronización o de
carga vive en `scripts/demo_control.py`.

Todos los datos son simulados y completamente ficticios.

```powershell
.\backend\.venv\Scripts\python.exe scripts\demo_control.py
```

Se usa el entorno virtual del backend: el Python del sistema no tiene las
dependencias del proyecto.

```text
==========================================================
                FETALALERT — DEMO CONTROL
==========================================================
PREPARACIÓN
1. Verificar estado
2. Restablecer baseline

DEMOSTRACIÓN
3. Simular desconexión
4. Restablecer conexión
5. Previsualizar próxima carga ETL
6. Ejecutar ETL

7. Salir
```

Abrir Demo Control no ejecuta nada: ninguna opción corre hasta que se elige.

---

## 1. Reparto con Adminer

| Herramienta | Para qué sirve |
|---|---|
| Demo Control | **Controla el proceso**: comprueba, corta y restablece la API, previsualiza y ejecuta el ETL. |
| Adminer ([guía](demo_adminer.md)) | **Muestra el dato**: operacional (A), analítica (B), publicación (C) y auditoría (D). |

Demo Control no muestra la auditoría ni repite las consultas A–D. Tampoco
calcula los valores de referencia (`id_ultima_sesion_previa`, `id_ultimo_log_previo`,
`ultima_secuencia_publicada_previa` y `seudonimo_embarazo_demo`): se toman con la consulta de preparación de Adminer (`demo_adminer.md`, §4.2), **después** de
que la opción 1 diga `LISTO PARA DEMO` y antes de que la gestante inicie
sesión. Cambian con cada captura, inicio de sesión o acceso, así que no se
reutilizan los de un ensayo anterior.

## 2. Qué se entiende por «baseline»

Son cuatro cosas distintas, y conviene no mezclarlas:

| Capa | Qué es | ¿La cambia Demo Control? |
|---|---|---|
| Dataset canónico de la tesis | Las filas que produce `generate_mock_data.py` y carga `load_mock_data.py`. De ahí salen las cifras de la tesis. | Nunca. |
| Estado acumulado de PostgreSQL de la demostración | El dataset, más el episodio de demostración, más **todas** las capturas de cada ensayo con su idempotencia, su auditoría, sus hechos y sus seudónimos. Solo crece. | Solo la opción 6, que ejecuta el ETL real. Nada se borra. |
| Cola SQLite activa de la cuenta | `data/gestante/movimientos/cuenta-<id_usuario>.sqlite3`. | Nunca: solo se lee. |
| Listo para un nuevo ensayo | Lo que comprueba la opción 1. | — |

**Un reset no deja vacía la base.** Los conteos de la base de la demostración
incluyen las capturas de los ensayos y no son resultados cuantitativos de la
tesis.

## 3. Opción 1 — Verificar estado

Solo lectura. No corrige, no crea y no arranca nada.

- **PostgreSQL:** cada conexión se abre con `default_transaction_read_only=on`
  y una transacción `REPEATABLE READ READ ONLY` que siempre se revierte. El
  motor rechaza cualquier `INSERT`, `UPDATE`, `DELETE`, DDL o `nextval`.
- **Cola SQLite:** se abre con `mode=ro` y solo si el archivo existe. Si hay un
  `-journal`, `-wal` o `-shm` a su lado, no se abre. **Es la forma de
  inspeccionar la cola sin tocarla.** `edge_node.py … estado` y `… traza` no
  son de solo lectura garantizada: pasan por la inicialización del
  almacenamiento, que crearía o migraría el archivo si hiciera falta.
- **Docker:** solo `docker compose ps`, `docker compose config --hash` y
  `docker inspect`. Cualquier otro subcomando está fuera de la lista blanca del
  script y se rechaza antes de lanzarlo.
- **HTTP:** solo `GET`.

**La verificación no es menos estricta que la ejecución.** Empieza por la
misma validación del destino que exigen las opciones 2 a 6 (sección 7), sin
relajar nada. Si esa validación fallara —otra base, otro PostgreSQL atendiendo
el puerto, otro proyecto de Compose, el contenedor `api` desfasado—, la línea
«Destino» es `FAIL` y el estado no puede ser `LISTO PARA DEMO`.

Lo que comprueba:

| Línea | Qué mira | Si no se cumple |
|---|---|---|
| Destino | La validación completa de la sección 7, incluido que el contenedor `api` no se creó con otra configuración. | `FAIL` |
| PostgreSQL (db) | El contenedor `db` es de este proyecto y está sano. | `FAIL` |
| Credenciales | Las tres URLs nombran la misma base, en el servidor esperado. | `FAIL` |
| Migraciones | La base está en el *head* de Alembic. | `FAIL` |
| Episodio demo | Un dispositivo `DEMO-SCRUM72-01`, una asignación, un embarazo y una cuenta PACIENTE. Si hay cero o más de uno, no se elige. | `FAIL` |
| Cuenta, embarazo, dispositivo | Cuenta activa, embarazo `ACTIVO`, dispositivo `ASIGNADO` con asignación vigente. | `FAIL` |
| Semana gestacional | La semana de hoy está en el catálogo. | `FAIL` fuera; `WARN` en las dos últimas |
| Vigencia del episodio | Último día en que la API admitirá capturas del episodio. | informativo |
| Médico responsable | Las reglas del ETL (`medico_responsable`, `verificar_afiliacion`) aceptarían una captura de hoy. | `FAIL` |
| Seudónimo del embarazo | Existe en `privado.seudonimo_embarazo`. | `FAIL` |
| ETL al día | Ninguna lectura sin incorporar al modelo analítico. | `WARN` |
| Aprovisionamiento local | `provision.json` coincide con la base. | `WARN` |
| Cola local | Sin `PENDIENTE`, `FALLIDO` ni intentos abiertos. | `WARN`; `FAIL` si no se puede leer |
| Portal → API | `GESTANTE_API_BASE_URL` es el puerto publicado del servicio `api`. Si no lo es, no se consulta `/health` en esa URL. | `FAIL`: bloquearía las opciones 3 y 4 |
| API central | El contenedor está en marcha y responde a `/health`. | `WARN` |
| Portal de la gestante | Responde en `GESTANTE_PORT`. Es necesario para el recorrido; Demo Control no lo arranca. | `WARN` |
| Adminer | Disponible, apagado o sin respuesta. Es opcional. | informativo; `WARN` sin respuesta |

Estado general: `LISTO PARA DEMO` (sin `WARN` ni `FAIL`), `REQUIERE
PREPARACIÓN` (algún `WARN`) o `NO DISPONIBLE` (algún `FAIL`).

**Adminer apagado no es «disponible».** Es opcional, y Demo Control no lo
arranca: lo informa con uno de tres estados —disponible, apagado, en marcha
sin respuesta— y nunca los confunde.

**Vigencia del episodio.** El embarazo de demostración se aprovisiona en la
semana 28 del día en que se crea, y el catálogo gestacional llega a la semana
40. La verificación informa siempre la fecha del último día admitido y
advierte en las dos últimas semanas. Volver a ejecutar
`provisionar_demo.py provisionar` **no** renueva el episodio: reconoce el que
ya existe. Pasada esa fecha hace falta decidir un episodio nuevo, que este
ticket no crea.

## 4. Opción 2 — Restablecer baseline

Restablece **únicamente un artefacto local**: reconstruye
`data/gestante/provision.json` cuando falta, no se puede leer o no coincide con
la base.

Lo que **no** hace, nunca:

- No escribe en PostgreSQL: ni `INSERT`, ni `UPDATE`, ni `DELETE`.
- No toca el dataset canónico.
- No crea episodios, dispositivos ni cuentas. No ejecuta
  `provisionar_demo.py provisionar`.
- No retira, archiva ni modifica capturas de la cola local.

Cómo lo hace:

1. Valida el destino (sección 7). Si algo no cuadra, se detiene sin cambios.
2. Lee el episodio y los catálogos en una transacción de solo lectura, con
   `localizar_episodio_demo`, que exige un episodio inequívoco. Si no lo hay,
   se detiene: crear el episodio es una decisión de quien opera.
3. Si el archivo ya coincide, informa «sin cambios» y no pregunta nada.
4. Si no coincide, muestra las diferencias —identificadores, nunca datos
   personales— y pide `S`. Cualquier otra respuesta cancela.
5. Guarda el archivo anterior como `provision.json.<fecha UTC>.bak`, comprueba
   que la copia es idéntica y escribe el nuevo de forma atómica (temporal en la
   misma carpeta y `os.replace`): el archivo activo es siempre el anterior o el
   nuevo, nunca uno a medias.
6. Vuelve a comparar el resultado con la base. Si algo falla, informa
   `OPERACIÓN INCOMPLETA` y dónde quedó el respaldo.
7. Ejecuta la opción 1.

Un segundo reset sobre un entorno ya preparado no cambia nada.

### Si la cola local tiene capturas sin entregar

Demo Control **no las retira**. La verificación y el reset informan
`REQUIERE PREPARACIÓN`, y se resuelve a mano:

| Lo que hay en la cola | Qué hacer |
|---|---|
| `PENDIENTE` o `FALLIDO` reintentable | Con la API en marcha (opción 4), iniciar sesión como la gestante en el portal y esperar a que el envío automático los deje en `ENVIADO`. |
| Intento abierto | Lo mismo: el propio portal lo reconcilia al vencer su plazo. |
| `FALLIDO` en revisión | Revisarlo a mano: `python scripts\edge_node.py --base <cola> traza <Idempotency-Key>`. Ese comando no es de solo lectura garantizada; para comprobar después el estado de la cola, la opción 1. |
| Archivo ilegible o con `-journal`/`-wal`/`-shm` | Abrir el portal para que SQLite recupere el archivo; si sigue ilegible, revisarlo a mano. |

Después, repetir la opción 1. Las capturas entregadas pasan por la API con su
clave de idempotencia, así que no se duplican.

El archivado automático de una cola con capturas sin entregar **no está
implementado**: quedó diferido a una autorización específica y el menú no
ofrece ninguna opción que lo aparente.

## 5. Opciones 3 y 4 — Desconexión y reconexión

Las dos actúan **solo sobre el servicio `api`** de Docker Compose. El script no
puede detener `db` ni `adminer`: `stop api` y `start api` son los únicos
comandos de control que su lista blanca admite.

**Opción 3 — Simular desconexión.** Valida el destino, comprueba que
`GESTANTE_API_BASE_URL` apunta al puerto publicado del servicio `api`, ejecuta
`docker compose stop api` y comprueba **el efecto**, no solo el comando:

- `db` sigue en marcha y sano;
- Adminer queda en el mismo estado que tenía;
- el contenedor `api` está detenido;
- `GET /health` en la URL del portal ya no responde.

Si el portal apunta a otra API, la opción se niega. Si tras detener el
contenedor algo sigue respondiendo en esa URL —por ejemplo, una instancia de
`uvicorn` en la computadora—, informa `SIMULACIÓN NO EFECTIVA` y no da la
desconexión por buena.

**Opción 4 — Restablecer conexión.** Exige lo mismo que la opción 3 antes de
actuar: el destino validado y que `GESTANTE_API_BASE_URL` apunte al servicio
`api` de ese proyecto. Si el portal apunta a otra API —`:8010`, por ejemplo—,
se niega sin iniciar nada, aunque esa otra instancia esté respondiendo.

Después ejecuta `docker compose start api`, espera hasta 60 segundos a que
`GET /health` responda `{"status": "ok"}` y vuelve a leer Docker. Solo dice
`CONEXIÓN RESTABLECIDA` si se cumplen las dos cosas: `/health` responde **y**
el contenedor `api` de este proyecto está en marcha. Si algo responde pero el
contenedor no arrancó, informa que la reconexión no quedó comprobada.
`/health` confirma que la API responde; **no es una comprobación integral de
PostgreSQL**.

**El token de la gestante.** Caduca a los `JWT_EXPIRATION_MINUTES` (30 por
omisión). Si caduca mientras la API está detenida, la captura sale después de
un nuevo inicio de sesión en el portal. Demo Control lo advierte y no hace nada
más: no amplía el token ni omite la autenticación.

## 6. Opciones 5 y 6 — ETL

### Opción 5 — Previsualizar próxima carga ETL

Ejecuta `python scripts\etl_analitico.py previsualizar` y presenta su
resultado. Esa orden recorre **la misma extracción y la misma transformación**
que `ejecutar` —las mismas funciones y las mismas reglas SIM-1.0— dentro de una
transacción `REPEATABLE READ READ ONLY`, sin tomar el candado del ETL, y se
detiene antes de cargar (`modelo_analitico_etl.md`, «Previsualización»).

Muestra, por cada lectura candidata:

- **Contexto:** embarazo, fecha clínica (America/Panama), semana y trimestre,
  clínica y médico PRINCIPAL.
- **Transformación:** el estado de cada métrica (`OK`, `WARNING`, `ERROR` o «no
  aplica»), el semáforo derivado, el registrado en origen y si coinciden.
- **Destino previsto:** una fila candidata en `fact_lectura_biometrica`.

Nunca muestra un valor biométrico ni un dato personal: los valores se ven en la
consulta A de Adminer.

No escoge ni embellece. Lista todas las lecturas nuevas; las que no pertenecen
al embarazo de la demostración aparecen marcadas `NO ESPERADA`, porque el ETL
real también las cargaría.

**Una candidata no es un hecho confirmado.** La ejecución real todavía puede
revertirse en la carga, en los seudónimos o en la conciliación, y el origen
puede cambiar entre la previsualización y la ejecución. La salida lo dice
siempre, junto con el estado del candado del ETL en ese instante.

Si la salida de la orden no tiene el formato acordado, Demo Control informa un
formato inesperado y **no la muestra**.

### Opción 6 — Ejecutar ETL

1. Valida el destino (sección 7). Ante cualquier discrepancia, no ejecuta.
2. Muestra el destino validado —base, proyecto de Compose, puerto y rol— y
   avisa de que es una operación con escrituras.
3. Pide escribir `EJECUTAR`. Cualquier otra respuesta cancela.
4. Lanza `python scripts\etl_analitico.py ejecutar`: el ETL de siempre, en su
   proceso, con su única transacción, su candado y su conciliación.

No hay pasos sueltos de extracción, transformación o carga: el ETL no los
expone, y Demo Control no los simula con una barra de progreso.

| Código | Significado |
|---|---|
| 0 | La transacción se confirmó entera. |
| 1 | Configuración, precondición o base de datos. No se conservó nada. |
| 2 | El origen contiene algo que las reglas no permiten cargar. No se conservó nada. En la previsualización: la ejecución real lo rechazaría. |
| 3 | La conciliación encontró diferencias, u otra inconsistencia del ETL. Se revirtió todo. |
| 4 | Otra ejecución tiene el candado. La previsualización nunca lo pide. |

## 7. Validación del destino

Las opciones 2 a 6 no actúan hasta identificar el destino sin dudas, y fallan
cerradas: ante cualquier discrepancia informan el motivo y no hacen nada.

1. `DATABASE_URL`, `ETL_DATABASE_URL` y `ALEMBIC_DATABASE_URL` nombran la misma
   base; las del ETL y de mantenimiento apuntan a la interfaz local y al puerto
   `POSTGRES_PORT`; la de la API, al servicio `db:5432` con el rol
   `fetalalert_api`.
2. El contenedor `db` lo creó Compose **desde este directorio** (etiquetas
   `working_dir` y `config_files`), está sano y publica ese puerto solo en
   `127.0.0.1`.
3. Las conexiones llegan **a ese contenedor**: el servidor responde con la IP
   del `db` en la red del proyecto (`inet_server_addr()`), no con la de otro
   PostgreSQL que escuche en el mismo puerto de la computadora.
4. La credencial del ETL es `fetalalert_etl`, sin superusuario ni `BYPASSRLS`,
   y la base está en el *head* de Alembic.
5. El contenedor `api` no está desfasado: su `config-hash` coincide con el que
   Compose calcula hoy, y su `DATABASE_URL` con la del `.env`. Esa comparación
   se hace en memoria; el entorno del contenedor no se imprime ni se guarda.
   (El reset no exige este punto: no usa la API.)
6. El episodio de demostración se identifica sin ambigüedad.

Que las tres URLs lleven el mismo nombre de base no basta: se comprueba la
instancia a la que llegan.

Si el contenedor `api` está desfasado, se recrea a mano con
`docker compose up -d api`. Demo Control no lo hace.

### Si se interrumpe con Ctrl+C

En el menú no había nada ejecutándose, y Demo Control lo dice. Dentro de una
opción **no se puede saber en qué punto cayó la interrupción**: un
`docker compose stop`, un `start` o un ETL pueden haber terminado, no haber
empezado o seguir su curso. Demo Control no afirma que nada quedó a medias:
indica qué opción se interrumpió y pide comprobar el estado de la API y de los
datos antes de repetirla —la opción 1 y, tras un ETL,
`python scripts\etl_analitico.py conciliar`.

## 8. Lo que nunca se imprime

Una URL de conexión, una contraseña, un token, el contenido del `.env`, el
entorno de un contenedor, un valor biométrico, un correo, un nombre o una
cédula. Toda la salida pasa por el mismo saneador que usan el cargador y el
ETL, y un error de base de datos se describe por su clase y su SQLSTATE.

## 9. Pruebas

| Archivo | Qué demuestra |
|---|---|
| `backend/tests/test_demo_control.py` | Sin servidor: menú, entradas inválidas, cancelaciones, lista blanca de Docker, verificación sin efectos sobre archivos, estados de la cola (ausente, limpia, pendiente, fallida, corrupta, con archivos auxiliares), reset y sus fallos, validación del destino ante cada discrepancia, desconexión y reconexión con Docker simulado, interpretación de la salida del ETL y ausencia de secretos. |
| `backend/tests/test_etl_postgresql.py` | La previsualización no escribe (contenido de cada tabla y valor de cada secuencia, antes y después), no toma el candado, y predice lo que el ETL real carga después. Las opciones 5 y 6 con el CLI real. |
| `backend/tests/test_provisionar_demo_postgresql.py` | La verificación y el reset contra PostgreSQL real no modifican ni una fila, y el dataset canónico sigue idéntico fila por fila. |
| `backend/tests/test_etl_cli.py` | La orden `previsualizar`: códigos de salida y salida sin credenciales. |

## 10. Trabajo futuro

Demo Control es hoy una herramienta de preparación, orquestación y verificación
de la demostración. Podría evolucionar hacia pruebas de humo, de integración o
de regresión —aserciones automáticas, escenarios declarativos, ejecución desde
CI—, y el archivado seguro de una cola con capturas sin entregar quedó
diseñado pero sin implementar. Nada de eso es un resultado de este ticket.
