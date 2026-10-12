# Adminer para la inspección visual de la demostración (SCRUM-103)

Adminer es una herramienta **auxiliar, opcional y local** que permite mostrar
durante la sustentación, sin una terminal llena de SQL, cómo avanza una captura
simulada por PostgreSQL: operacional, analítico, publicación y auditoría.

**No es un componente de FetalAlert.** Ningún servicio depende de él y el
sistema funciona igual con Adminer apagado. No cambia la arquitectura, la lógica
de negocio, el RLS, los roles ni los permisos. El perfil `demo` de Docker
Compose solo decide si este contenedor se arranca: no es un perfil de usuario,
ni un modo de FetalAlert, ni concede nada.

Todos los datos son simulados y completamente ficticios.

---

## 1. Encender, comprobar y apagar

Desde la raíz del repositorio:

```powershell
# 0 y 1. Comprobar db y, solo si es conforme, encender Adminer. El bloque se
# pega entero: cualquier fallo lo detiene antes de arrancar nada.
& {
  $db = @(docker compose ps -a --format '{{.ID}}|{{.Project}}|{{.State}}|{{.Health}}' db)
  if ($LASTEXITCODE -ne 0 -or $db.Count -ne 1) { throw 'No hay exactamente un contenedor del servicio db.' }
  $id, $proyecto, $estado, $salud = $db[0].Split('|')
  if ($estado -ne 'running' -or $salud -ne 'healthy') { throw "db no está en ejecución y sana ($estado/$salud)." }
  $redes = docker inspect $id --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'
  if ($LASTEXITCODE -ne 0 -or (-split $redes) -notcontains "${proyecto}_default") { throw "db no está en la red ${proyecto}_default." }
  "db conforme: proyecto $proyecto, red ${proyecto}_default."
  # --no-deps: no arranca, recrea ni detiene db ni api.
  docker compose --profile demo up -d --no-deps adminer
}

# 2. Comprobar el estado de los tres servicios, incluidos los detenidos.
docker compose --profile demo ps -a

# 3. Apagar solo Adminer.
docker compose --profile demo stop adminer
```

- Con `--no-deps`, Compose no comprueba ni arranca `db`. Si `db` no estuviera
  sana, Adminer abriría pero no podría conectarse; por eso la comprobación va
  antes, en el mismo bloque. Resuelve el contenedor por el servicio `db` del
  proyecto de esta carpeta, sin depender de su nombre, y solo lee el estado y
  las redes del contenedor. Poner en marcha `db` es preparación del entorno, no
  parte de este procedimiento.
- Dirección: **<http://127.0.0.1:8090>**. Solo se publica en la interfaz local
  de la computadora.
- Adminer pertenece al perfil `demo`: un arranque ordinario sin perfiles
  (`docker compose up -d`) no lo inicia. Docker Compose sí lo inicia si se
  nombra el servicio explícitamente (`docker compose up -d adminer`), aunque no
  se active el perfil. Este procedimiento usa siempre `--profile demo`, para que
  la intención quede escrita en el comando.
- Adminer no tiene política de reinicio: si Docker Desktop se reinicia, hay que
  volver a encenderlo.
- Imagen: `adminer:6.1.1-standalone`, sin plugins ni tema.

## 2. Iniciar sesión

| Campo | Valor |
|---|---|
| Sistema | PostgreSQL |
| Servidor | Escribir `db` manualmente |
| Usuario | según la pestaña, ver la tabla siguiente |
| Contraseña | se escribe a mano |
| Base de datos | la base de la demostración |

**El servidor es `db`, no `127.0.0.1` ni el puerto publicado** (5433 por
omisión, o el `POSTGRES_PORT` del `.env` local). Adminer corre dentro de Docker
y llega a PostgreSQL por la red interna, así que no le afecta qué puerto use la
computadora.

Compose no inyecta credenciales de PostgreSQL en el servicio Adminer. El
operador las introduce en el formulario y Adminer las utiliza para conectarse.
**La contraseña nunca va en una URL, en este documento, en una captura de
pantalla ni en el repositorio.** Solo se escribe en el formulario.

### Dos identidades, y no son equivalentes

| Pestañas | Identidad | Qué la limita |
|---|---|---|
| A, B, D | Sesión administrativa local (`POSTGRES_USER` del `.env` local) | **Solo un parámetro de sesión** |
| C | `fetalalert_powerbi` | **Sus privilegios**: `SELECT` sobre `publicacion` y nada más |

**La sesión administrativa no es una credencial de solo lectura.** En el entorno
local es superusuario. Lo único que la protege es que el contenedor de Adminer
abre cada sesión con `default_transaction_read_only=on` (variable `PGOPTIONS`):
eso evita escrituras **accidentales** —por ejemplo, editar un valor en la tabla
de resultados—, pero la propia sesión podría desactivarlo con `SET` o
`BEGIN READ WRITE`. No limita privilegios. Se usa exclusivamente para ejecutar
las consultas preparadas de este documento.

**`fetalalert_powerbi` es la cuenta técnica de servicio de Power BI.** La
pestaña C muestra la superficie publicada que **esa cuenta** puede leer. No
muestra lo que ve un médico concreto: el aislamiento entre médicos lo aplica el
RLS del modelo de Power BI a partir de la identidad del visor (UPN) y se
demuestra después, en Power BI. Esta cuenta puede leer las once vistas
publicadas, incluida `publicacion.v_paciente_medico`, que contiene nombre,
cédula, teléfono y correo (sintéticos) de las pacientes con seguimiento vigente.
Su contraseña es un secreto de despliegue. Ver
[Protección analítica](proteccion_analitica.md), §13.

## 3. Comprobar cada sesión antes de la defensa

En cada pestaña, una vez iniciada la sesión:

```sql
SELECT current_user, session_user, current_database(),
       current_setting('server_version')                AS version,
       current_setting('default_transaction_read_only') AS default_ro,
       current_setting('transaction_read_only')         AS tx_ro,
       r.rolsuper, r.rolbypassrls
FROM pg_roles r
WHERE r.rolname = current_user;
```

Lo esperado: la base de la demostración, `default_ro = on` y `tx_ro = on` en las
cuatro pestañas. En A, B y D se verá `rolsuper = true`: es el límite declarado
en la sección 2, no un error. En C, `rolsuper` y `rolbypassrls` deben ser
`false`.

## 4. Preparar las consultas

Las consultas siguen **una captura concreta**. Para eso se toman, justo antes de
entrar a la defensa, unos valores de referencia que separan lo que ya existía de
lo que la demostración va a producir. Ninguna consulta usa «hoy» ni «las últimas
N filas».

### 4.1 Precondiciones

Ningún mecanismo del sistema las impone: se comprueban antes de empezar. Si
alguna no se cumple, las consultas pueden mostrar filas ajenas a la captura de
la demostración.

La opción 1 de [Demo Control](demo_control.md) comprueba en solo lectura las
precondiciones 1 a 5 y las resume en un estado general. No toma los valores de
referencia: esos siguen saliendo de la consulta de la sección 4.2, que se
ejecuta **después** de que la verificación diga `LISTO PARA DEMO`.

1. **Entorno aprovisionado.** La base de la demostración está en la última
   migración, con los roles de conexión creados y el dataset cargado. `E`, el
   embarazo de la demostración, lo define el aprovisionamiento de la demo: es el
   `id_embarazo` que imprime `python scripts/provisionar_demo.py verificar`. No
   se supone un valor fijo.
2. **El seudónimo de `E` existe.** Si la consulta de preparación devuelve `seud`
   vacío, **detenerse**: el ETL todavía no procesó ese embarazo y la pestaña C
   no tendría con qué filtrar.
3. **ETL al día para `E`.** `lecturas_e_sin_etl` debe ser `0`. Si no lo es,
   ejecutar el ETL y repetir la preparación.
4. **Ninguna captura anterior pendiente para `E`.** La cola local de la cuenta de
   la gestante no tiene eventos `PENDIENTE`. Se comprueba con la opción 1 de
   [Demo Control](demo_control.md), línea «Cola local», que abre el archivo
   en solo lectura y no lo crea si no existe. Una captura vieja que se
   sincronizara durante la demo aparecería junto a la nueva.

   `python scripts/edge_node.py --base <cola> estado` y `… traza` **no son
   comandos de solo lectura garantizada**: abren el almacenamiento pasando
   por su inicialización, que crea la carpeta y crearía o migraría el archivo
   si faltara o fuera de una versión anterior. No se usan para una inspección
   que deba dejar la cola intacta.
5. **Ninguna captura de `E` posterior al momento de la demo.**
   `ultima_captura_e` debe ser anterior a la hora de la defensa, para que la
   sesión nueva reciba la siguiente `secuencia_sesion`.
6. **Una única captura nueva y ninguna actividad concurrente.** Desde la
   preparación hasta el final, solo se hace la captura de la demostración: ningún
   otro dispositivo, portal, nodo edge, ETL, aprovisionamiento ni inicio de
   sesión ajeno. Cualquier acción así aparecería en la pestaña D y podría
   aparecer en A, B o C. La verificación y la previsualización de Demo
   Control no cuentan como actividad: solo leen, y ninguna escribe una fila
   ni un evento de auditoría.
7. **Después de un ensayo, la preparación se repite.** Los valores de referencia
   cambian con cada captura.

### 4.2 Consulta de preparación

En la pestaña 1 (sesión administrativa), sustituyendo `{E}`:

```sql
SELECT p.e                                                         AS embarazo,
       COALESCE((SELECT max(id_sesion) FROM operacional.sesion_monitoreo), 0) AS s0,
       COALESCE((SELECT max(id_log) FROM operacional.auditoria_log), 0)     AS l0,
       se.seudonimo                                                AS seud,
       (SELECT COALESCE(max(v.secuencia_sesion), 0)
          FROM publicacion.v_lectura v
         WHERE v.seudonimo_embarazo = se.seudonimo)                AS q0,
       (SELECT max(l.fecha_hora_captura)
          FROM operacional.lectura_biometrica l
          JOIN operacional.sesion_monitoreo s USING (id_sesion)
         WHERE s.id_embarazo = p.e)                                AS ultima_captura_e,
       (SELECT count(*)
          FROM operacional.lectura_biometrica l
          JOIN operacional.sesion_monitoreo s USING (id_sesion)
          LEFT JOIN analitico.fact_lectura_biometrica f
                 ON f.id_lectura = l.id_lectura
         WHERE s.id_embarazo = p.e
           AND f.id_lectura IS NULL)                               AS lecturas_e_sin_etl
FROM (VALUES ({E})) AS p(e)
LEFT JOIN privado.seudonimo_embarazo se ON se.id_embarazo = p.e;
```

Devuelve siempre una fila. Anotar `s0`, `l0`, `seud` y `q0`, y comprobar las
precondiciones 2, 3 y 5.

### 4.3 Las cuatro pestañas

Abrir cuatro pestañas en la página «SQL command» de Adminer:

```
http://127.0.0.1:8090/?pgsql=db&username=<usuario>&db=<base>&sql=
```

| Pestaña | Consulta | Usuario |
|---|---|---|
| 1 | A · Operacional | sesión administrativa |
| 2 | B · Analítica | sesión administrativa |
| 3 | C · Publicación | `fetalalert_powerbi` |
| 4 | D · Auditoría | sesión administrativa |

Adminer añade `&ns=public` a esa dirección al abrirla. También puede precargarse
la consulta en la propia URL, codificada en `sql=`: Adminer la coloca en el
editor **sin ejecutarla**, y recargar esa URL tampoco la ejecuta.

En cada una se pega la consulta de la sección 5 **con los valores ya
sustituidos** en lugar de `{E}`, `{S0}`, `{L0}`, `{SEUD}` y `{Q0}`. Si queda un
marcador sin sustituir, la consulta falla en lugar de devolver filas
equivocadas. Se ejecuta una vez para comprobar el estado inicial y se deja
abierta. Estado inicial esperado: A, B y C **vacías**. D puede tener ya filas:
`L0` se toma antes de que la gestante inicie sesión, así que el inicio de sesión
y lo que registre el portal al abrirse aparecen después de `L0`.

Durante la defensa solo se pulsa **Execute** (o Ctrl+Enter): no se escribe SQL.
No se recarga la página con F5, que volvería a enviar el formulario.

Las dos identidades se abren a la vez en **ventanas separadas** (por ejemplo,
una normal y una privada), para que no compartan la sesión. Da igual qué
identidad vaya en cada ventana: se comprueba por el usuario de la sesión, con la
consulta de la sección 3.

## 5. Las cuatro consultas

### A · Operacional: ¿llegó la captura al servidor central?

```sql
SELECT s.id_sesion, l.id_lectura, s.id_embarazo, s.tipo_sesion, i.clave,
       l.fecha_hora_captura AT TIME ZONE 'America/Panama' AS captura,
       l.hr_valor, l.spo2_valor, l.mov_valor,
       sem.codigo_nivel AS semaforo
FROM operacional.sesion_monitoreo s
JOIN operacional.lectura_biometrica l USING (id_sesion)
JOIN operacional.semaforo sem ON sem.id_semaforo = l.id_semaforo
LEFT JOIN operacional.idempotencia_solicitud i ON i.id_sesion = s.id_sesion
WHERE s.id_embarazo = {E}
  AND s.id_sesion > {S0}
ORDER BY l.id_lectura;
```

- **Antes de sincronizar:** ninguna fila. La captura existe solo en el
  dispositivo.
- **Después:** la sesión nueva con sus lecturas. La columna `clave` es el
  `Idempotency-Key` que imprimió `scripts/dispositivo_gestante.py capturar`, y
  permite comprobar que es **la misma** captura.

### B · Analítica: ¿la incorporó el ETL?

```sql
SELECT l.id_lectura, s.id_sesion,
       f.id_lectura IS NOT NULL AS incorporada_por_etl,
       f.estado_hr, f.estado_spo2, f.estado_mov
FROM operacional.sesion_monitoreo s
JOIN operacional.lectura_biometrica l USING (id_sesion)
LEFT JOIN analitico.fact_lectura_biometrica f ON f.id_lectura = l.id_lectura
WHERE s.id_embarazo = {E}
  AND s.id_sesion > {S0}
ORDER BY l.id_lectura;
```

- **Sincronizada, antes del ETL:** las lecturas aparecen con
  `incorporada_por_etl = false`.
- **Después del ETL:** `true`, con los estados que calcula el ETL.

La diferencia entre sincronizar y procesar es visible en una sola tabla: la
tabla de hechos conserva el mismo `id_lectura` que la tabla operacional.

### C · Publicación: ¿qué puede leer la cuenta técnica de Power BI?

```sql
SELECT secuencia_sesion, fecha_captura, semana_gestacion, codigo_semaforo,
       hr_valor, spo2_valor, mov_valor, estado_hr, estado_spo2, estado_mov
FROM publicacion.v_lectura
WHERE seudonimo_embarazo = '{SEUD}'
  AND secuencia_sesion > {Q0}
ORDER BY secuencia_sesion;
```

- **Antes del ETL:** ninguna fila.
- **Después:** la sesión nueva, tal como queda publicada para el consumo
  analítico: sin identificadores operacionales y con la fecha al día, sin hora.

Se consulta como `fetalalert_powerbi` y muestra la superficie de esa cuenta, no
lo que ve un médico: ver la sección 2. La vista no se modifica para la
demostración.

### D · Auditoría: eventos posteriores a `L0`

```sql
SELECT id_log, fecha_hora AT TIME ZONE 'America/Panama' AS fecha_hora,
       id_usuario, accion, nombre_entidad_afectada, id_entidad_afectada,
       ip_origen
FROM operacional.auditoria_log
WHERE id_log > {L0}
ORDER BY id_log;
```

Muestra, en orden, todos los eventos registrados **después de `L0`**, es decir,
los que produjo la demostración si se cumplió la precondición 6. Como `L0` se
toma antes de que la gestante inicie sesión, incluye su inicio de sesión y los
accesos que registre el portal, además del registro de la sesión sincronizada.

**Correlación con A.** El registro de la sesión aparece como
`SESION_MONITOREO_REGISTRADA`, con `nombre_entidad_afectada = 'sesion_monitoreo'`
e `id_entidad_afectada` igual al `id_sesion` que muestra la pestaña A. Ese
registro se escribe en la misma transacción que la sesión: o se confirman los
dos o ninguno.

FetalAlert registra **las acciones relevantes definidas** para trazabilidad y
control de acceso, no todo lo que ocurre: por ejemplo, un reenvío idempotente o
un token rechazado no escriben fila. Ver el README, sección «Auditoría».

## 6. Con la API detenida

```powershell
docker compose stop api
docker compose --profile demo ps -a # db y adminer en marcha; api detenida
docker compose start api
```

Las opciones 3 y 4 de [Demo Control](demo_control.md) ejecutan esos mismos dos
comandos y además comprueban su efecto: que `db` sigue sano, que Adminer queda
como estaba y que la API del portal dejó de responder, o volvió a hacerlo.

`docker compose stop api` detiene solo el contenedor de la API (puerto 8000).
Adminer depende únicamente de `db`, así que las cuatro pestañas siguen
funcionando. Para que ese comando corte de verdad la comunicación de la
gestante, el portal debe enviar a esa misma API:
`GESTANTE_API_BASE_URL=http://127.0.0.1:8000`. Si apuntara a otra —por ejemplo, una instancia de `uvicorn` en el puerto
8010—, ese comando no la detendría.

El token de la gestante caduca a los `JWT_EXPIRATION_MINUTES` (30 por
omisión). Si vence mientras la API está detenida, la captura sale después de
volver a iniciar sesión en el portal.

## 7. Qué no hacer

- Escribir credenciales en URLs, documentos o capturas.
- Navegar por el menú de tablas y vistas o abrir tablas completas en el
  proyector: la sesión administrativa alcanza tablas con datos personales
  sintéticos, y C alcanza `v_paciente_medico`.
- Editar valores en la tabla de resultados, o desactivar el modo de solo
  lectura.
- Usar Adminer para corregir o preparar datos. Eso pertenece al
  aprovisionamiento de la demostración.

Al terminar: «Logout» en cada identidad y apagar Adminer (sección 1).

## 8. Si Adminer no abre

1. `docker compose --profile demo ps`: si `adminer` no está en marcha,
   comprobar `db` y encenderlo (sección 1, pasos 0 y 1).
2. Abrir `http://127.0.0.1:8090`, no `https://`.
3. Puerto ocupado: `Get-NetTCPConnection -LocalPort 8090 -State Listen`.
4. Error al iniciar sesión por el servidor: debe ser `db`.
5. `db` no está sano: `docker compose ps db`.
6. Registro de Adminer: `docker compose --profile demo logs adminer --tail 50`.
   Muestra las direcciones pedidas, que incluyen el usuario y la consulta, nunca
   la contraseña.
7. Sesión caducada: volver a iniciar sesión.

## 9. Estado de verificación

**Comprobado sin arrancar contenedores:** que Compose resuelve el proyecto con y
sin el perfil `demo`, y el contrato de `backend/tests/test_compose_adminer.py`.

**Comprobado mediante HTTP** (un cliente programático, no un navegador), con la
sesión administrativa local y bases sin el escenario de la demostración:

- arranque con `--no-deps` sin arrancar, recrear ni detener `db` ni `api`;
- acceso solo desde `127.0.0.1:8090`, no desde la red local;
- inicio y cierre de sesión;
- `default_transaction_read_only = on` y `transaction_read_only = on` sin
  ningún `SET` ni `BEGIN READ ONLY` añadido: los fija `PGOPTIONS`;
- `INSERT`, `UPDATE` y `DELETE` con `WHERE false` sobre
  `operacional.auditoria_log`, ejecutados por separado: los tres rechazados con
  `cannot execute … in a read-only transaction`, sin cambios en el número de
  filas, el máximo `id_log` ni la secuencia;
- precarga de una consulta por URL sin ejecutarla, también al recargar, y
  nueva ejecución al volver a enviar el formulario;
- varias pestañas del **mismo** usuario en la misma sesión;
- una conexión nueva a PostgreSQL en cada petición;
- disponibilidad con la API **ya detenida**;
- la contraseña no aparece en el registro de Adminer.

**Entorno local preparado el 7/10/2026** (sección 10) y comprobado sin
navegador:

- la API de Compose arranca y se conecta como `fetalalert_api`, sin
  superusuario ni `BYPASSRLS`, a la base de la demostración. Su contenedor
  recibe `DATABASE_URL`, la configuración JWT y `APP_NAME`/`APP_ENV`, sin
  credenciales de mantenimiento, ETL ni Power BI;
- `fetalalert_powerbi` inicia sesión, alcanza `publicacion` y ningún otro
  esquema, y no puede escribir;
- las precondiciones 1 a 5 se cumplen para el embarazo de la demostración
  (`E = 130` en esta preparación);
- el portal de la gestante arranca en `127.0.0.1:8100`.

### Ensayo controlado del 9/10/2026

Hecho en la computadora de desarrollo, sobre `fetalalert_demo_sustentacion`
(sección 10), con una sola captura `SIGNOS_MATERNOS` del embarazo 130. Valida el
procedimiento de SCRUM-103; **no** decide la base definitiva de la sustentación.

Referencias tomadas antes del inicio de sesión de la gestante: `S0 = 831`,
`L0 = 0`, `Q0 = 0` y el seudónimo del embarazo 130.

La columna «Navegador» recoge lo que la operadora verificó y reportó a mano en
Adminer y en el portal. La columna «Terminal / HTTP» recoge las comprobaciones
hechas desde la línea de comandos, que no sustituyen a las del navegador.

| Paso | Navegador (verificación manual) | Terminal / HTTP |
|---|---|---|
| Identidades | `fetalalert_dev` y `fetalalert_powerbi`, abiertas a la vez en ventanas separadas. Cada una se identificó por el usuario de la sesión con la consulta de la sección 3: `on`/`on` en las dos; `rolsuper`/`rolbypassrls` valen `t`/`t` y `f`/`f` respectivamente. | — |
| Estado inicial | A, B, C y D sin registros. | Embarazo 130 sin sesiones, auditoría vacía, ETL al día y sin cola local. |
| Inicio de sesión de la gestante | Inicio: embarazo en curso, semana 28, sin registros de FC, SpO₂ ni movimientos. D: `LOGIN_EXITOSO` y dos `ACCESO_CLINICO_PERMITIDO` del usuario 107. | — |
| API detenida y captura | A sigue sin registros; Adminer sigue respondiendo. | `api` detenida y `/health` sin respuesta. Captura `PENDIENTE`, 0 intentos; FC 80, SpO₂ 97. |
| Token caducado | — | Al volver la API, la comprobación de credencial del portal recibió `401`: el token de 30 minutos había vencido. La cola siguió `PENDIENTE`, con 0 intentos, hasta un nuevo inicio de sesión. |
| Reconexión | A: sesión 832, lectura 1280, `clave` igual al `Idempotency-Key`, FC 80, SpO₂ 97 y semáforo OK. Portal: Inicio e Historial muestran la medición del 9/10 a las 18:19. | Traza `PENDIENTE` → `ENVIADO` en el primer intento, `ENTREGADO`, `POST /api/v1/sesiones-monitoreo` → `201`. |
| Antes del ETL | B: `incorporada_por_etl = f`. C: sin registros. | — |
| ETL | — | `SUCCESS`, conciliación OK (27 verificaciones, 0 fallidas), `hechos_nuevos=1`. La lectura 1280 queda en la tabla de hechos con estados OK/OK, semana 28. |
| Después del ETL | B: `incorporada_por_etl = t`, `estado_hr` OK, `estado_spo2` OK, `estado_mov` NULL. C: una fila, secuencia 1, 2026-10-09, semana 28, FC 80, SpO₂ 97, semáforo OK. | — |
| Auditoría | D: `SESION_MONITOREO_REGISTRADA`, entidad `sesion_monitoreo`, `id_entidad_afectada = 832`, igual al `id_sesion` de A. | — |

Observaciones:

- El cambio `PENDIENTE` → `ENVIADO` se acredita por la traza de la cola y por
  las comprobaciones anteriores y posteriores; no se observó en el instante
  exacto.
- El token de la gestante caducó entre su inicio de sesión y la reconexión. En
  la defensa conviene que la gestante inicie sesión poco antes de detener la
  API, o prever un segundo inicio de sesión.
- El ETL y la auditoría de esta base son de demostración y no representan las
  cifras canónicas de la tesis.

### Fuera de este ensayo

- **Power BI oficial.** No se conectó ni se probó. El informe de la
  sustentación usa otro origen, en la laptop donde se presentará.
- **Aislamiento por médico.** No se probó: la pestaña C muestra la superficie
  de la cuenta técnica `fetalalert_powerbi`, no lo que ve un médico.
- **El ensayo en la laptop de la sustentación**, con la base que se decida usar
  allí.
- En navegador, la recarga con F5 y la lectura en el proyector no se
  comprobaron por separado.

## 10. Entorno local de la sustentación

Preparado con los comandos existentes del repositorio, en este orden, sobre el
PostgreSQL de Docker Compose, sin recrearlo:

1. base nueva `fetalalert_demo_sustentacion`;
2. roles de conexión `fetalalert_api`, `fetalalert_etl` y `fetalalert_powerbi`,
   con los atributos del CI y contraseñas locales;
3. `python scripts/bootstrap_roles.py aplicar` y `alembic upgrade head`, con la
   credencial de mantenimiento;
4. `python scripts/generate_mock_data.py` y `python scripts/load_mock_data.py`;
5. `python scripts/provisionar_demo.py provisionar`;
6. `python scripts/etl_analitico.py ejecutar`, para que el embarazo de la
   demostración tenga seudónimo antes de preparar la pestaña C.

Variables del `.env` local, que no se versiona:

| Variable | Identidad | Servidor |
|---|---|---|
| `DATABASE_URL` | `fetalalert_api` | `db:5432` (la usa el contenedor de la API) |
| `ALEMBIC_DATABASE_URL` | credencial de mantenimiento | `127.0.0.1:<POSTGRES_PORT>` (desde Windows) |
| `ETL_DATABASE_URL` | `fetalalert_etl` | `127.0.0.1:<POSTGRES_PORT>` (desde Windows) |
| `JWT_SECRET_KEY` | — | la usa la API |
| `GESTANTE_API_BASE_URL` | — | `http://127.0.0.1:8000`, la API de Compose |

Docker Compose pasa al contenedor de la API `DATABASE_URL`, la configuración JWT
y `APP_NAME`/`APP_ENV`, y ninguna credencial de mantenimiento, ETL ni Power BI.
La contraseña de `fetalalert_powerbi` se guarda únicamente en el `.env` local.
Compose no la inyecta en ningún servicio: el operador la introduce en el
formulario de Adminer, que la utiliza para conectarse.

Cada ensayo deja su captura en esta base. Antes de repetirlo hay que rehacer la
preparación de la sección 4.

## 11. Guion del ensayo

Desde la raíz del repositorio, con el entorno de la sección 10. Los comandos de
Python usan `backend\.venv\Scripts\python.exe`. `E` e `<id_usuario>` son los que
imprime `scripts/provisionar_demo.py verificar`.

Los pasos de terminal pueden hacerse desde [Demo Control](demo_control.md)
(`scripts\demo_control.py`): la opción 1 antes del paso 3, la 3 en el paso 6, la
4 en el paso 9, y la 5 seguida de la 6 en el paso 11. Lo que se observa en
Adminer no cambia.

1. **Arrancar.** `docker compose start api`; Adminer según la sección 1; el
   portal con `python scripts\gestante_web.py` (`http://127.0.0.1:8100`).
2. **Adminer, en el navegador.** Pestañas 1, 2 y 4 con la sesión
   administrativa; pestaña 3 con `fetalalert_powerbi`. Las dos identidades van
   en ventanas separadas. En cada pestaña, la consulta de la sección 3 confirma
   el usuario de la sesión.
3. **Valores de referencia, antes de que la gestante inicie sesión.** En la
   pestaña 1, la consulta de preparación (sección 4.2). Comprobar las
   precondiciones y anotar `s0`, `l0`, `seud` y `q0`.
4. **Consultas preparadas.** Sustituir los valores en A–D y ejecutarlas: A, B y
   C vacías.
5. **La gestante inicia sesión en el portal.** D puede mostrar ahora su inicio
   de sesión y los accesos que registre el portal.
6. **Corte de comunicación.** `docker compose stop api`.
7. **Captura.** `python scripts\dispositivo_gestante.py capturar --tipo SIGNOS_MATERNOS`.
   Anotar el `Idempotency-Key` que imprime.
8. **Con la API detenida, comprobar:**
   - el evento sigue **`PENDIENTE`**: opción 1 de Demo Control, línea «Cola
     local» (`PENDIENTE=1`), que lee la cola en solo lectura. El detalle de un
     evento concreto lo da
     `python scripts\edge_node.py --base data\gestante\movimientos\cuenta-<id_usuario>.sqlite3 traza <Idempotency-Key>`,
     que no es de solo lectura garantizada (precondición 4);
   - A sigue vacía: la captura no está en el servidor central;
   - `docker compose --profile demo ps -a` muestra `db` y `adminer` en marcha y
     `api` detenida, y las pestañas siguen respondiendo.
9. **Reconexión.** `docker compose start api`. El portal no envía en un plazo
   fijo: en cada ciclo (`GESTANTE_ENVIO_AUTOMATICO_SEGUNDOS`, 30 por omisión)
   comprueba primero la credencial contra la API y solo entonces reclama lo
   elegible. Mientras la API estuvo detenida no reclamó nada, así que no gastó
   intentos. Si algún intento llegó a fallar, se respeta la espera incremental
   que dejó programada, dentro de los reintentos existentes. Repetir la
   comprobación del paso 8 hasta ver el cambio **`PENDIENTE` → `ENVIADO`** (en
   la opción 1, «Sin pendientes»). Si el token de la gestante caducó, volver a
   iniciar sesión en el portal.
10. **Llegada.** Ejecutar A: aparece la sesión, con `clave` igual al
    `Idempotency-Key` del paso 7. En el portal, el registro nuevo aparece en
    Inicio y en el Historial.
11. **Analítica.** Ejecutar B (`incorporada_por_etl = false`) y C (vacía).
    Después, `python scripts\etl_analitico.py ejecutar`, y otra vez B (`true`) y
    C (la sesión nueva).
12. **Auditoría.** Ejecutar D: la fila `SESION_MONITOREO_REGISTRADA` tiene
    `id_entidad_afectada` igual al `id_sesion` de A.
13. **Cierre.** «Logout» en las dos identidades de Adminer y en el portal;
    detener el portal (Ctrl+C); `docker compose --profile demo stop adminer`;
    `docker compose stop api`.
