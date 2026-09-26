# FetalAlert

**Diseño de un sistema de inteligencia de negocios seguro para el seguimiento prenatal en zonas rurales de Panamá**

Proyecto de tesis de licenciatura enfocado en el diseño e implementación de un sistema de inteligencia de negocios (BI) que permita el seguimiento prenatal en zonas rurales de Panamá, con especial atención a la seguridad, privacidad y disponibilidad en contextos de conectividad intermitente.

> **Aviso importante:** Este proyecto utiliza exclusivamente **datos simulados y sintéticos, completamente ficticios**. No se recopila, almacena ni procesa información real de pacientes en ninguna etapa del desarrollo o las pruebas. No se anonimizan datos de pacientes reales: los mecanismos de anonimización se implementan y validan sobre información ficticia, para demostrar su funcionamiento.

## Problema que aborda

En zonas rurales de Panamá, el acceso limitado a personal médico especializado y la conectividad a internet intermitente dificultan el seguimiento continuo de embarazos de riesgo. Esto puede retrasar la detección de señales de alerta y limitar la capacidad del personal clínico para tomar decisiones oportunas basadas en datos históricos.

## Antecedente: prototipo original de FetalAlert

Un trabajo previo desarrolló un prototipo wearable compuesto por un ESP32, un sensor MAX30102 y un sensor MPU6050, con una interfaz web local alojada en el ESP32 y accesible mediante una red Wi-Fi en modo punto de acceso, sin requerir conexión a internet.

Ese prototipo es **antecedente y motivación** de este proyecto de tesis. **No forma parte de los componentes desarrollados, implementados o validados en este repositorio**: aquí no se reimplementa el firmware del wearable, no se validan físicamente los sensores, y no se sirve ninguna interfaz local en tiempo real desde un ESP32 real.

## Alcance de esta tesis

Esta tesis diseña e implementa el **sistema de inteligencia de negocios** que rodea al monitoreo prenatal, usando **datos simulados** para representar el flujo de información que en un escenario real provendría de un dispositivo wearable:

- No se recopilan ni utilizan datos biométricos reales de gestantes.
- No se trabaja con información real de pacientes en ningún momento.
- No se reimplementa el firmware completo del wearable ni se validan físicamente el ESP32 ni los sensores.
- Toda prueba, dataset de ejemplo y demostración usa **datos simulados y sintéticos, completamente ficticios**.
- El sistema se desarrolla y prueba en un **entorno controlado** que simula condiciones de conectividad rural (conectividad intermitente simulada); no se afirma que el sistema haya sido desplegado ni probado en comunidades rurales reales.

El propósito no es validar el dispositivo biométrico original, sino implementar y evaluar el **flujo de datos, la seguridad, la persistencia, el procesamiento ETL y la analítica** del sistema propuesto.

## Objetivo general

Diseñar un sistema de inteligencia de negocios seguro que permita gestionar y analizar la información prenatal proveniente de comunidades rurales de Panamá, facilitando el seguimiento dual entre embarazadas y personal médico.

## Arquitectura propuesta

El sistema se organiza en cinco capas:

1. **Generación y entrada de datos simulados** — scripts en Python que generan lecturas simuladas (frecuencia cardíaca, SpO₂, movimiento fetal, timestamps, sesiones de monitoreo) y soportan importación alternativa vía archivos JSON y CSV.
2. **Simulación del nodo edge / almacenamiento temporal** — un componente en Python con SQLite que representa el comportamiento de un nodo edge: almacena lecturas localmente y mantiene una cola de registros pendientes de sincronización.
3. **Backend central** — una API REST desarrollada con **FastAPI**, responsable de autenticación, autorización, validación, y recepción de datos sincronizados de forma asíncrona (no en tiempo real).
4. **Persistencia** — **PostgreSQL**, con un modelo operacional normalizado y un modelo dimensional (Star Schema) para analítica.
5. **ETL y analítica** — un proceso ETL *batch* reproducible en **Python/SQLAlchemy** que transforma el esquema `operacional` en el esquema analítico `analitico` de **PostgreSQL**, previsto para una ejecución periódica (nocturna). **Microsoft Power BI** no consume `analitico`: desde SCRUM-98 consulta únicamente las cuatro vistas seudonimizadas del esquema `publicacion` —`v_embarazo`, `v_lectura`, `v_entitlement_medico` y `v_resumen_administrativo`— con la credencial técnica de solo lectura `fetalalert_powerbi`, que no recibe `USAGE` sobre `operacional`, `analitico`, `privado` ni `seguridad`. No se ejecuta necesariamente tras cada sincronización, y no hay *scheduler* ni demonio implementado: el mecanismo de ejecución es el comando `scripts/etl_analitico.py`. La actualización es asíncrona — nunca en tiempo real — y Power BI está destinado exclusivamente a personal médico o autorizado.

## Seguimiento dual: gestante y personal médico

El objetivo general contempla un seguimiento **dual** entre la gestante y el personal médico, con niveles de acceso distintos:

- **Personal médico/autorizado:** accede a la analítica y los dashboards en Power BI, alimentados por el ETL *batch* en su ejecución periódica, no tras cada sincronización.
- **Gestante:** accede solo a su propia información, separada de Power BI, mediante el portal local de SCRUM-72 ([Interfaz web de la gestante](#interfaz-web-de-la-gestante-scrum-72)): un proceso en su dispositivo que consulta la API central con su propia sesión y guarda localmente las sesiones de movimientos hasta enviarlas.

## Flujo simulado de conectividad intermitente

Para representar el comportamiento de zonas rurales con conectividad inestable, el sistema simula:

- Generación de lecturas simuladas.
- Almacenamiento temporal local (nodo edge simulado).
- Cola de registros pendientes de sincronización.
- Sincronización **asíncrona** con el servidor central cuando hay conectividad disponible (no en tiempo real).
- Reintentos automáticos ante fallos de conexión.
- **Idempotencia** para evitar registros duplicados.
- Registro del estado de las sesiones y del proceso de sincronización.

## Tecnologías previstas

- **Lenguaje principal:** Python 3.12
- **Generación de datos simulados:** Python 3.12
- **Formatos de intercambio/importación:** JSON, CSV
- **Simulación de nodo edge y almacenamiento temporal:** Python + SQLite
- **Backend / API REST:** FastAPI
- **Validación de datos:** Pydantic
- **Base de datos operacional y dimensional:** PostgreSQL
- **Acceso a datos:** SQLAlchemy
- **Migraciones:** Alembic
- **Autenticación:** JWT
- **Autorización:** RBAC (control de acceso basado en roles)
- **Hash de contraseñas:** Argon2id
- **Cifrado de datos en tránsito:** HTTPS/TLS
- **Protección de datos:** aislamiento por fila (RLS), seudonimización de la capa publicada (sobre datos ficticios), auditoría (`AuditoriaLog`) y controles alineados con la Ley 81 de 2019 de Panamá
- **ETL:** Python, SQLAlchemy, PostgreSQL
- **Analítica y dashboards:** Microsoft Power BI Desktop
- **Pruebas automatizadas:** pytest
- **Documentación/pruebas manuales de API:** Swagger/OpenAPI (integrado en FastAPI)
- **Entorno reproducible:** Docker, Docker Compose
- **Control de versiones:** Git y GitHub

## Modelos de datos

- **Modelo operacional:** modelo relacional normalizado en PostgreSQL para el funcionamiento del sistema (pacientes, embarazos, sesiones de monitoreo, lecturas, usuarios, roles, auditoría, etc.), con datos exclusivamente ficticios y sintéticos.
- **Modelo dimensional (Star Schema vigente)**, implementado en el esquema `analitico` (ver [Esquema analítico y ETL](#esquema-analítico-y-etl)):
  - `Fact_LecturaBiometrica` — tabla de hechos central.
  - `Dim_Paciente`
  - `Dim_Medico`
  - `Dim_Clinica`
  - `Dim_TiempoGestacional`
  - `Dim_Embarazo`
  - `Dim_Semaforo`
  - `Dim_FactorRiesgo`
  - `Bridge_EmbarazoFactorRiesgo`

## Seguridad, auditoría y anonimización

**Ya implementado (SCRUM-70):**

- **Autenticación mediante JWT**, con algoritmo fijado por el servidor y expiración efectiva.
- **Autorización basada en roles (RBAC)** para los perfiles ADMIN, MEDICO y PACIENTE.
- **Hash de contraseñas con Argon2id.** Un hash no es un cifrado: protege credenciales de forma irreversible y no protege ningún dato clínico, ni en tránsito ni en reposo.
- **Auditoría en `auditoria_log`** de los accesos y acciones definidos. No persiste contraseñas, hashes, tokens, la cabecera `Authorization`, el correo introducido en un `LOGIN_FALLIDO` ni payload clínico; sí conserva los identificadores técnicos y la `ip_origen` que prevé el modelo de auditoría.

**Ya implementado (SCRUM-97):**

- **Aprovisionamiento controlado de cuentas** PACIENTE y MEDICO para perfiles clínicos existentes, solo por ADMIN, sin registro público ni autoservicio.
- **Desactivación y reactivación** de la misma cuenta, sin borrar perfiles ni historia clínica.
- **Correo de acceso canónico** (`strip().lower()`) y **coherencia rol ↔ vínculo clínico** garantizadas también por PostgreSQL. Ver [Cuentas: aprovisionamiento y ciclo de vida](#cuentas-aprovisionamiento-y-ciclo-de-vida).

**Ya implementado (SCRUM-98):**

- **Aislamiento por fila (RLS) en PostgreSQL.** Seis tablas clínicas con `ENABLE` **y** `FORCE ROW LEVEL SECURITY` y sus políticas. El contexto del usuario viaja en la variable de transacción `fetalalert.id_usuario`, instalada con `set_config(..., true)` por la dependencia que resuelve la identidad, de modo que muere con la transacción y no puede viajar a otra petición por una conexión reutilizada. Sin contexto, o con uno ausente, vacío, no numérico o manipulado, las políticas no devuelven ninguna fila: el fallo es cerrado y silencioso.
- **Propiedad paciente→embarazo comprobada en la base, no solo en el código.** La política `pol_ingesta` sobre `sesion_monitoreo` y `lectura_biometrica` es un `WITH CHECK` que exige que el `id_embarazo` recibido pertenezca a la gestante autenticada. Una sesión dirigida al embarazo de otra persona no se inserta aunque el rol sea PACIENTE.
- **Alcance clínico por asignación, no por clínica.** Una gestante lee sus propios embarazos, finalizados incluidos. Un médico lee un embarazo solo a través de un `SeguimientoClinico` directo, activo y vigente hoy —`PRINCIPAL`, `APOYO` y `REEMPLAZO` conceden lo mismo—; pertenecer a la misma clínica no concede acceso clínico. ADMIN no tiene lectura clínica individual ni *bypass*.
- **Rutas mínimas de lectura clínica** para PACIENTE y MEDICO, con esquemas de respuesta minimizados. Un recurso ajeno y uno inexistente responden el mismo 404, con la misma frase.
- **Auditoría de accesos clínicos**, concedidos y denegados: `ACCESO_CLINICO_PERMITIDO` y `ACCESO_CLINICO_DENEGADO`, **una entrada por petición** y nunca una por fila devuelta.
- **Seudonimización de la capa publicada.** Ver [Protección analítica](docs/proteccion_analitica.md).

**Todavía no implementado:**

- **HTTPS/TLS.** Corresponde a un escenario de despliegue y no a una capacidad de este código. El MVP local habla HTTP contra `http://127.0.0.1:8000`; un token Bearer no debe circular fuera de ese entorno controlado sin HTTPS/TLS, y un JWT firmado **no** es un JWT cifrado.
- **Cifrado de datos en reposo.** La arquitectura de la tesis lo contempla, pero el código no lo demuestra. Ni Argon2id, ni RBAC, ni el JWT son evidencia de ello.
- **Configuración de Power BI Service.** El repositorio no contiene ningún artefacto Power BI versionable. La capa publicada y el contrato de *entitlements* existen en PostgreSQL; el origen de datos, el modelo, el rol de RLS del *dataset* y los permisos del workspace están descritos paso a paso y **no** están configurados.

> **Seudonimización, no anonimización.** La transformación de la capa publicada es **seudonimización**: el seudónimo es estable y existe un mapa privado que devuelve a la persona, así que los datos publicados siguen siendo datos personales. Lo que se protege es *quién puede recorrer el camino de vuelta*, no la imposibilidad de recorrerlo. Llamarlo anonimización afirmaría que el vínculo se destruyó, y no se destruye.

Controles alineados con los requisitos de la Ley 81 de 2019 de Panamá (Protección de Datos Personales). `auditoria_log` es *append-only por diseño de la aplicación* —el código solo inserta, la credencial de la API tiene `INSERT` y ninguna otra operación sobre esa tabla, y su clave foránea `ON DELETE RESTRICT` impide borrar una cuenta con historial— y aporta **trazabilidad y atribución técnica**. No constituye **inmutabilidad criptográfica ni no repudio fuerte**: quien tenga privilegios de administración sobre PostgreSQL puede alterar la tabla. Cualquier documento que describa esta garantía debe usar esos términos y no los más fuertes.

## Estructura del repositorio

Vista general; no lista todos los archivos.

```
tesis-bi-prenatal/
├── .github/workflows/   # CI: pruebas sin servidor, contrato de Compose y suites PostgreSQL
├── backend/
│   ├── app/
│   │   ├── api/         # rutas FastAPI y dependencias de autenticación y RBAC
│   │   ├── db/          # bases declarativas y sesión de SQLAlchemy
│   │   ├── edge/        # nodo edge simulado: SQLite, outbox, sincronización y traza
│   │   ├── etl/         # ETL del esquema operacional al analítico
│   │   ├── gestante/    # adaptador local de la interfaz de la gestante (SCRUM-72)
│   │   ├── loader/      # carga idempotente del dataset simulado
│   │   ├── models/      # modelos SQLAlchemy del esquema operacional
│   │   ├── schemas/     # contratos Pydantic
│   │   ├── services/    # ingesta, idempotencia, tokens, contraseñas y auditoría
│   │   ├── config.py
│   │   └── main.py      # aplicación FastAPI
│   ├── alembic/         # migraciones
│   ├── tests/           # pruebas pytest, sin servidor y contra PostgreSQL
│   ├── Dockerfile
│   └── requirements.txt
├── data/                # datasets y bases locales generados; no se versionan
├── docs/                # decision log, Definition of Done y especificaciones
├── frontend/gestante/   # interfaz que el navegador de la paciente descarga
├── scripts/             # comandos: generador, cargador, ETL, nodo edge y portal
├── docker-compose.yml   # PostgreSQL y API para el entorno local
├── .env.example
└── README.md
```

## Estado actual del proyecto

El repositorio se encuentra en una etapa temprana. Lo que ya existe y funciona es el esquema operacional en PostgreSQL con sus migraciones, el generador del dataset simulado, su carga idempotente, el endpoint que recibe una sesión de monitoreo con sus lecturas biométricas —con su contrato de idempotencia—, el nodo edge simulado, que captura paquetes sin conexión y los entrega después sin duplicarlos, con reintentos de espera incremental, agotamiento controlado y trazabilidad de extremo a extremo, y el esquema analítico (Star Schema) con su ETL reproducible, idempotente e incremental. A partir de SCRUM-70 existen además autenticación con JWT, autorización por rol y auditoría de accesos: el endpoint de ingesta ya no es público. SCRUM-97 añade el aprovisionamiento administrativo de cuentas para perfiles clínicos existentes y su desactivación y reactivación. SCRUM-98 añade el aislamiento por fila (RLS) sobre las tablas clínicas, las rutas mínimas de lectura para gestante y médico, la auditoría de los accesos clínicos concedidos y denegados, y la capa publicada y seudonimizada que Power BI consultará. **Aún no existen un servicio permanente o demonio que dispare la sincronización o el ETL por sí solo, la detección automática de conectividad, HTTPS/TLS, el cifrado en reposo, los dashboards ni la configuración del workspace de Power BI.** El desarrollo activo continúa en el Capítulo IV, centrado en seguridad, interfaces, aislamiento de datos y analítica del MVP, y todo el trabajo se desarrolla y prueba en un entorno controlado/local, no en comunidades rurales reales.

## Roadmap general

- Definición y consolidación de la arquitectura y modelos de datos (operacional y dimensional).
- Implementación de la generación de datos simulados y del nodo edge simulado (Python + SQLite).
- Desarrollo del backend REST (FastAPI) con autenticación JWT/RBAC.
- Implementación de la persistencia en PostgreSQL (esquema operacional y dimensional).
- Implementación de la sincronización asíncrona, reintentos e idempotencia.
- Definición e implementación del mecanismo de acceso limitado para la gestante.
- Desarrollo del proceso ETL y de los dashboards en Power BI.
- Incorporación de auditoría (`AuditoriaLog`), aislamiento por fila, seudonimización de la capa publicada y controles de cumplimiento con la Ley 81 de 2019.
- Pruebas automatizadas y documentación final de la tesis.

## Roles de base de datos y variables de conexión

Desde SCRUM-98 no hay una sola credencial. Cada pieza se conecta con la suya, y
el reparto es lo que hace que el aislamiento por fila signifique algo: si todo
corriera con el mismo superusuario, las políticas no filtrarían nada.

### Las tres URL, y por qué son tres

| Variable | Rol que la respalda | Quién la usa |
| --- | --- | --- |
| `DATABASE_URL` | `fetalalert_api` | El runtime de la API. Sujeto a RLS; solo ve las filas que el contexto de la petición autoriza. |
| `ALEMBIC_DATABASE_URL` | el migrador (`fetalalert_ci_migrador` en CI) | `alembic upgrade/downgrade`, `scripts/bootstrap_roles.py` y `scripts/load_mock_data.py`. |
| `ETL_DATABASE_URL` | `fetalalert_etl` | `scripts/etl_analitico.py`. Lee `operacional` sin filtro por su propia política y escribe `analitico` y el mapa de seudónimos. |

**Ninguna cae de vuelta en otra.** El cargador y el ETL se detienen si su
variable falta, en lugar de escribir con la credencial equivocada; `DATABASE_URL`
debe apuntar al rol de runtime restringido y nunca al migrador ni al
superusuario, y el arranque de la API lo comprueba.

### Roles con LOGIN y roles NOLOGIN

Dos familias, y la diferencia importa:

- **Con LOGIN — `fetalalert_api`, `fetalalert_etl`, `fetalalert_powerbi`.** Son
  credenciales: alguien se autentica con ellas. Las crea el despliegue (o el CI)
  a partir de secretos externos, **no** las migraciones: una contraseña no se
  versiona. La migración exige que existan y falla en el preflight si no están.
- **NOLOGIN — `fetalalert_rls_owner`, `fetalalert_provision_owner`,
  `fetalalert_mantenimiento`.** Nadie se conecta como ellos y no tienen
  contraseña. Son propietarios de funciones `SECURITY DEFINER` y de la capa
  publicada, o tienen escritura sin filtro sobre lo clínico. Los crea
  `scripts/bootstrap_roles.py`, que es idempotente, junto con las membresías que
  el migrador necesita.

Que los privilegiados sean NOLOGIN es deliberado: sus privilegios solo se
alcanzan a través de una función `SECURITY DEFINER` concreta, nunca abriendo una
sesión. Y `fetalalert_powerbi` **no es la identidad de ningún médico**: es una
cuenta técnica de solo lectura sobre `publicacion`; cada médico entra a Power BI
Service con la suya y el RLS del *dataset* filtra por ella.

### El orden local, de cero a publicación

```powershell
# 1. PostgreSQL
docker compose up -d db

# 2. Roles con LOGIN (una vez, desde tus secretos locales; ver .env.example)
# 3. Roles NOLOGIN y membresías del migrador
python scripts/bootstrap_roles.py      # usa ALEMBIC_DATABASE_URL

# 4. Esquema
cd backend; alembic upgrade head; cd ..

# 5. Dataset simulado
python scripts/generate_mock_data.py
python scripts/load_mock_data.py       # usa ALEMBIC_DATABASE_URL

# 6. API
docker compose up -d api               # usa DATABASE_URL

# 7. ETL hacia `analitico` y `publicacion`
python scripts/etl_analitico.py        # usa ETL_DATABASE_URL

# 8. Power BI se conecta como `fetalalert_powerbi` contra `publicacion`
```

Los pasos 2 y 3 se hacen una sola vez por base. El 4 debe preceder al 5, el 5 al
7, y el 7 al 8: la capa publicada son vistas sobre `analitico`, así que sin ETL
están vacías.

## Carga del dataset simulado en PostgreSQL

El dataset simulado se genera con un script y se carga en el esquema operacional
`operacional` con otro. Los dos trabajan **exclusivamente con datos ficticios**.

### 1. Levantar PostgreSQL

```powershell
docker compose up -d db
docker compose ps
```

El contenedor debe aparecer como `healthy` antes de continuar.

### 2. Desplegar el esquema

La carga **no crea el esquema**: exige que las migraciones ya estén aplicadas.

```powershell
cd backend
alembic upgrade head
```

### 3. Generar el dataset

Los dos scripts se ejecutan desde la raíz del repositorio, así que hay que
volver desde `backend/`:

```powershell
cd ..
$env:PYTHONIOENCODING = "utf-8"
python scripts/generate_mock_data.py
```

`PYTHONIOENCODING` es necesaria en consolas de Windows con una codificación
heredada: sin ella, el resumen final del generador no puede imprimir el símbolo
`SpO₂` y el comando termina con error **después** de haber escrito
correctamente el dataset.

Los archivos quedan en `data/generated/`, que **no se versiona**: son
reproducibles ejecutando de nuevo el generador con su semilla fija, **salvo
`password_hash`**, que cambia en cada ejecución (ver
[Regenerar el dataset no es repetir la carga](#regenerar-el-dataset-no-es-repetir-la-carga)).

### 4. Cargar el dataset

Seguimos en la raíz del repositorio:

```powershell
python scripts/load_mock_data.py
```

Por omisión procesa `data/generated/dataset_fetalalert.json`. Acepta una ruta
alternativa como único argumento; nunca recibe la URL de la base ni ninguna
credencial por línea de comandos.

La conexión sale de **`ALEMBIC_DATABASE_URL`**, no de `DATABASE_URL`: desde
SCRUM-98 el cargador escribe con la credencial del migrador, porque
`DATABASE_URL` apunta al rol de runtime `fetalalert_api`, que está sujeto a RLS y
no puede sembrar el dataset de otras personas. **No hay respaldo**: si la
variable falta, el cargador se detiene en lugar de escribir con la credencial
equivocada. El archivo que la aplicación lee es tu `.env` local, que no se
versiona y se crea copiando `.env.example`; **ambos traen `db:5432`**, un nombre
que solo existe dentro de la red de Docker. Si
ejecutas el comando directamente desde Windows, define `ALEMBIC_DATABASE_URL` apuntando
a `127.0.0.1:<POSTGRES_PORT>` en esa terminal —lo que tiene prioridad sobre el
`.env`— o ejecuta el comando dentro del contenedor. Conviene la dirección IPv4
literal y no `localhost`: Docker publica PostgreSQL solo en IPv4, mientras que
`localhost` en Windows se resuelve primero a `::1`, de modo que cada conexión
espera a que expire ese intento IPv6 antes de reintentar por IPv4. El cargador
además solo se ejecuta si `APP_ENV` es un ambiente seguro (`development`, `test`
o `ci`) y se detiene si detecta producción.

### Qué significa idempotencia aquí

Que la carga se pueda repetir sin duplicar nada y sin pisar nada:

- **Registro ausente** — se inserta.
- **Registro ya presente e idéntico** — se deja como está y se cuenta como
  existente sin cambios.
- **Registro ya presente con contenido distinto bajo la misma llave primaria** —
  se considera un conflicto: la carga se detiene, informa la tabla, la llave y
  los campos que difieren, y **revierte la transacción completa**. Nunca
  sobrescribe.

La carga entera ocurre en **una única transacción**: o se aplica completa, o no
queda ningún cambio. No se usa `TRUNCATE`, `DROP`, ni borrados de ningún tipo, y
la tabla `auditoria_log` no se toca.

### Qué pasa en la segunda ejecución

Nada cambia. La segunda corrida reporta **0 registros insertados** y 2.270
registros existentes sin cambios, los conteos siguen siendo 732 sesiones y 1.180
lecturas, y las secuencias no se mueven.

### Regenerar el dataset no es repetir la carga

La idempotencia se refiere al **mismo artefacto** de entrada:

- `generate → load → load` — idempotente: la segunda carga no inserta nada.
- `generate → load → generate → load` — **puede detenerse con un conflicto en
  `usuario.password_hash`**.

Desde SCRUM-70 cada cuenta simulada guarda un digest Argon2id con salt
aleatorio, así que cada ejecución del generador produce digests distintos para
la misma contraseña ficticia. El resto del dataset se reproduce idéntico, pero
un archivo regenerado ya es otro artefacto, y el cargador le aplica su regla de
siempre: una fila existente con contenido distinto es un conflicto, la carga se
revierte completa y nada se sobrescribe. `password_hash` no se ignora ni se
actualiza en silencio.

Una base local simulada cargada antes de SCRUM-70 —cuyo `password_hash` era un
marcador de posición— se **reconstruye una sola vez**: se parte de una base
vacía, se aplica `alembic upgrade head` y se carga el dataset actual. Si esa
base vive en el volumen de Docker del proyecto, `docker compose down -v` lo
elimina, **con todos los datos de esa base local**. Es aceptable porque todos
los datos son sintéticos y viven en un entorno controlado; no es un
procedimiento de migración y no existe ninguna migración para ello.

### Qué pasa ante un conflicto o un error

El comando escribe el motivo en la salida de error y termina con un código
distinto de cero. La base queda exactamente como estaba: una violación de
`UNIQUE` o de `CHECK` detectada por PostgreSQL revierte toda la carga, sin
dejar filas a medias. Los mensajes nunca incluyen la URL de conexión ni
contraseñas.

## Recepción de lecturas biométricas por la API

Primera entrada vertical de la aplicación: una solicitud HTTP llega, Pydantic la
valida, se comprueban las referencias, se persiste con SQLAlchemy y se responde
con un contrato tipado. Todos los datos son simulados y ficticios.

> **Este endpoint no es apto para producción.** Exige un token JWT válido y solo
> el rol PACIENTE puede ejecutar la ingesta: ADMIN y MEDICO reciben `403` (ver
> [Autenticación, RBAC y auditoría](#autenticación-rbac-y-auditoría)). Desde
> SCRUM-98 **sí** comprueba que la gestante autenticada sea la dueña del
> `id_embarazo` que envía, y la comprobación vive en la base: la política
> `pol_ingesta` es un `WITH CHECK` sobre `sesion_monitoreo` y
> `lectura_biometrica`, de modo que una sesión dirigida a un embarazo ajeno no
> se inserta aunque la petición traiga un token PACIENTE válido. Aun así se
> ejecuta únicamente en el entorno controlado de desarrollo y pruebas, nunca
> expuesto a una red pública: falta HTTPS/TLS.

### 1. Preparar la base y levantar la API

La API **no crea el esquema**. Hay que desplegar las migraciones primero:

```powershell
docker compose up -d db
cd backend
alembic upgrade head
```

Y después levantar el servidor, también desde `backend/`:

```powershell
uvicorn app.main:app --reload
```

La documentación interactiva queda en `http://localhost:8000/docs`, generada
automáticamente a partir de los schemas.

Alternativa: `docker compose up -d` levanta la base y la API juntas, y el
contenedor ya ejecuta ese mismo `uvicorn`. Esa API necesita `JWT_SECRET_KEY` en
el `.env` (ver [Configurar la firma](#configurar-la-firma)); sin un valor válido
el contenedor no arranca. `docker compose up -d db` no la necesita.

### 2. Ruta y método

```
POST /api/v1/sesiones-monitoreo
```

Recibe **una sesión de monitoreo junto con una o varias lecturas biométricas**.
La relación es 1:N: una sesión agrupa todas las lecturas del evento, y la lista
nunca puede venir vacía.

Los cuatro identificadores que trae el paquete —`id_embarazo`,
`id_dispositivo`, `id_tiempo_gest` e `id_semaforo`— son **referencias a filas
que ya deben existir**. El endpoint no crea catálogos ni entidades clínicas como
efecto secundario. En cambio `id_sesion` e `id_lectura` **los genera
PostgreSQL**: el cliente no los envía y los recibe en la respuesta.

### 3. Ejemplo de solicitud válida (datos simulados)

```json
{
  "id_embarazo": 100,
  "id_dispositivo": 100,
  "tipo_sesion": "SIGNOS_MATERNOS",
  "fecha_inicio": "2025-02-24T11:20:00+00:00",
  "fecha_fin": "2025-02-24T11:25:00+00:00",
  "estado_sesion": "COMPLETADA",
  "lecturas": [
    {
      "id_tiempo_gest": 107,
      "id_semaforo": 100,
      "fecha_hora_captura": "2025-02-24T11:21:00+00:00",
      "fecha_hora_sincronizacion": "2025-02-24T11:45:00+00:00",
      "hr_valor": 90,
      "spo2_valor": 97,
      "mov_valor": null
    }
  ]
}
```

Los cuatro identificadores salen del dataset simulado que produce
`scripts/generate_mock_data.py`, y las fechas están elegidas para que la
combinación se sostenga: el embarazo `100` empieza el `2025-01-06`, el
dispositivo `100` está asignado a él del `2025-01-06` al `2025-10-13`, y el
`2025-02-24` cae en la **semana gestacional 8**, que es justo la que declara
`id_tiempo_gest: 107`. Cambiar la fecha sin cambiar la semana —o al revés—
hace que el paquete deje de ser válido.

Reglas del cuerpo:

- **Las marcas de tiempo llevan offset obligatorio.** Las columnas son
  `TIMESTAMPTZ`; una fecha sin zona horaria se rechaza.
- **Una métrica que no aplica va en `null`**, nunca en cero ni en cadena vacía:
  la ETL tiene que poder distinguir «no se midió» de «se midió cero». Omitir el
  campo equivale a enviarlo en `null`.
- **Cada lectura tiene una de dos formas**: `hr_valor` + `spo2_valor` con
  `mov_valor` en `null`, o `mov_valor` con las otras dos en `null`. Una mezcla
  se rechaza.
- **La forma tiene que coincidir con `tipo_sesion`**: `SIGNOS_MATERNOS` solo
  admite lecturas de HR/SpO₂ y `MOVIMIENTOS_FETALES` solo lecturas de
  movimiento.
- **Los movimientos fetales se registran desde la semana gestacional 20**; antes
  de esa semana el paquete se rechaza.
- **Cada lectura tiene que haberse capturado durante su sesión**, entre
  `fecha_inicio` y `fecha_fin` (los extremos cuentan). La sincronización sí
  puede ser posterior al fin: en un sistema con conectividad intermitente, ése
  es el caso normal.
- **`estado_sesion` y `fecha_fin` tienen que coincidir.** `PENDIENTE` e
  `INTERRUMPIDA` no admiten `fecha_fin`; `COMPLETADA` y `PROCESADA` la exigen.
  Omitir el estado equivale a `PENDIENTE`, así que omitirlo y mandar `fecha_fin`
  también se rechaza.
- **No se aceptan campos desconocidos.** Un nombre mal escrito es un error, no
  un campo omitido.

Y dos reglas más que se comprueban contra la base, porque no se pueden decidir
leyendo solo el mensaje:

- **El dispositivo debe estar asignado a ese embarazo durante la sesión.** No
  basta con que el embarazo exista y el dispositivo exista: tiene que haber una
  `AsignacionDispositivo` que cubra las fechas de la sesión. La comprobación es
  temporal y no mira el campo `activo`, para que una sesión antigua siga siendo
  válida después de que el dispositivo se devolviera.
- **`id_tiempo_gest` debe ser la semana real del embarazo en la fecha de
  captura.** Se calcula desde `Embarazo.fecha_inicio` y se compara con la semana
  del catálogo. Esto es también lo que impide esquivar la regla de la semana 20:
  apuntar a otra semana ya no sirve, porque la semana sale del embarazo y no del
  paquete.

### 4. Ejemplo de respuesta exitosa

`201 Created`:

```json
{
  "id_sesion": 733,
  "lecturas_creadas": 1,
  "ids_lectura": [1181]
}
```

Solo identificadores y un conteo. La respuesta nunca devuelve hashes,
credenciales, configuración de conexión, SQL ni detalles internos del servidor.

**Los identificadores de éste y de los demás ejemplos de respuesta son
ilustrativos.** `id_sesion` e `ids_lectura` los asignan las secuencias de
PostgreSQL, así que dependen de cuántas filas haya en la base y cambian de una
ejecución a otra. Lo que sí es estable es su forma: un entero, un conteo, y
tantos identificadores como lecturas traía el paquete, en ese mismo orden.

### 5. Códigos de respuesta

| Código | Cuándo |
| --- | --- |
| `201` | Con un token válido de rol PACIENTE: la sesión y todas sus lecturas quedaron registradas, o ya lo estaban por una solicitud anterior con la misma clave. |
| `400` | Falta la cabecera `Idempotency-Key` o su formato no es válido. No se registró nada. |
| `401` | Falta la credencial o no es `Bearer`, o el token no verifica, expiró o pertenece a una cuenta inexistente o desactivada. Lleva `WWW-Authenticate`. No se registró nada. |
| `403` | La identidad es válida, pero su rol (ADMIN o MEDICO) no permite la operación. No se registró ninguna sesión ni lectura; el rechazo queda en `auditoria_log` como `ACCESO_DENEGADO_ROL`. |
| `404` | Alguna referencia del paquete no existe todavía. |
| `409` | Conflicto. O la clave ya identifica un paquete con contenido distinto, o una referencia dejó de existir mientras se procesaba el paquete. La solicitud en conflicto no agrega ni modifica datos, y la operación que ya estuviera almacenada bajo esa clave permanece intacta. |
| `422` | El cuerpo no cumple el contrato, o rompe una regla del dominio o una restricción de validez de la base. |
| `500` | Error interno. La transacción completa fue revertida. |

El detalle de `401` y `403` está en
[Semántica `401` y `403`](#semántica-401-y-403).

Ningún mensaje de error incluye la URL de conexión, contraseñas, SQL, nombres de
restricción ni trazas. El diagnóstico técnico queda en el log del servidor,
reducido a la clase de la excepción, el `SQLSTATE` y los nombres de restricción,
tabla y columna.

### 6. Atomicidad

**La sesión y todas sus lecturas se escriben en una única transacción**, con un
solo `commit`. Ante cualquier fallo —una referencia inexistente, una regla del
dominio, una restricción que PostgreSQL rechaza o un error imprevisto— se
revierte todo: no queda una sesión huérfana ni una carga parcial. Si falla la
tercera lectura de cinco, tampoco queda la sesión.

### 7. Reenvíos: la cabecera `Idempotency-Key`

En un sistema pensado para conectividad intermitente, un reenvío no es una
anomalía: es lo que hace un nodo edge cuando no llegó a saber si su paquete se
guardó. Lo que este endpoint garantiza es que, **para una misma clave y este
mismo recurso, no se crea dos veces el paquete confirmado y se reproduce su
resultado**, con los mismos identificadores que devolvió la primera vez.

**La cabecera es obligatoria.** La genera el cliente:

```
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000
```

- Entre **8 y 128 caracteres**, y solo los del alfabeto `[A-Za-z0-9_-]`. Un UUID
  sirve tal cual. El patrón exacto que aplica el servidor es
  `^[A-Za-z0-9_-]{8,128}$`, y es el mismo que publica `/docs`.
- **Una clave identifica un paquete y solo uno** para este recurso. No es un
  identificador de sesión ni de dispositivo: nombra *este envío concreto*.

#### Qué responde cada caso

| Situación | Código | `Idempotency-Replayed` |
| --- | --- | --- |
| Primera vez con esa clave | `201` | `false` |
| Reenvío con la misma clave y un contenido equivalente | `201`, mismo cuerpo y mismos identificadores | `true` |
| Misma clave con un contenido distinto | `409` | — |
| Cabecera ausente o mal formada | `400` | — |

«Contenido equivalente» significa lo que la base guardaría igual: el servidor
compara una huella SHA-256 del paquete ya validado, así que reordenar las
propiedades del JSON, escribir `97` donde antes iba `97.00`, o expresar la misma
marca de tiempo con otro offset **no** cuentan como un paquete distinto. Cambiar
un valor, añadir o quitar una lectura, o reordenarlas, sí.

#### Ejemplos

Con el mismo cuerpo del §3, la primera solicitud:

```
POST /api/v1/sesiones-monitoreo
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000

201 Created
Idempotency-Replayed: false
{"id_sesion": 733, "lecturas_creadas": 1, "ids_lectura": [1181]}
```

El mismo envío, repetido porque el primero no llegó a confirmarse del lado del
cliente. **La clave y el cuerpo son exactamente los mismos** que en la solicitud
anterior:

```
POST /api/v1/sesiones-monitoreo
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000

201 Created
Idempotency-Replayed: true
{"id_sesion": 733, "lecturas_creadas": 1, "ids_lectura": [1181]}
```

El cuerpo de la respuesta es idéntico al de la primera —mismo `id_sesion` y
mismos `ids_lectura`—; lo único que cambia es `Idempotency-Replayed`, que ahora
vale `true`.

Y una colisión: **la misma clave, ya usada arriba, con un cuerpo distinto**. Por
ejemplo, el paquete del §3 con `hr_valor` cambiado de `90` a `91`. Basta con
eso: no hace falta que el segundo paquete sea inválido, solo que no sea el
mismo.

```
POST /api/v1/sesiones-monitoreo
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000

409 Conflict
```

El `409` no revela nada del paquete anterior: ni sus identificadores, ni su
huella, ni qué campo difiere. Y la sesión creada por la primera solicitud sigue
ahí, sin cambios.

#### Recomendación para el cliente

- **Reintentar el mismo paquete: la misma clave.** Genera la clave junto con el
  paquete y guárdala con él en la cola de pendientes; un reintento reenvía el
  paquete tal como quedó guardado, ese timestamp de sincronización incluido.
- **Operación nueva: clave nueva.** Dos monitoreos legítimamente iguales —misma
  gestante, mismo dispositivo, mismos valores— son dos registros distintos, y la
  API no los deduplica por contenido. Lo que los distingue es la clave.

#### Atomicidad y claves no envenenadas

La clave se reclama **antes** de escribir ninguna fila de negocio, y la
reclamación, la sesión, las lecturas y el resultado se confirman en **una sola
transacción**. De ahí se siguen dos cosas:

- o queda todo —clave, sesión y lecturas—, o no queda nada;
- si algo falla después de reclamar —una referencia inexistente, una regla del
  dominio, una restricción de la base—, el rollback **también retira la
  reclamación**. La clave queda libre y el reintento corregido funciona con esa
  misma clave.

Quien decide la carrera cuando dos solicitudes llegan a la vez es PostgreSQL,
mediante una restricción `UNIQUE` sobre el par (recurso, clave): una crea y la
otra reproduce la respuesta de la primera, o recibe `409` si su contenido era
otro.

#### Antes de probarlo

La tabla que sostiene todo esto llega en una migración, así que la base tiene
que estar en el `head` de Alembic —el mismo `alembic upgrade head` del §1—. Y lo
de siempre: **solo datos simulados**, y solo en el entorno local o de pruebas.
El endpoint exige un token JWT válido y solo el rol PACIENTE puede registrar
sesiones; ADMIN y MEDICO reciben `403` (ver
[Autenticación, RBAC y auditoría](#autenticación-rbac-y-auditoría)). La propiedad
paciente→embarazo y el aislamiento por fila los aporta SCRUM-98 y los aplica
PostgreSQL. Aun así no es apto para exposición pública ni para producción: el MVP
local todavía no implementa HTTPS/TLS, el cifrado en reposo ni otros controles de
despliegue.

## Nodo edge simulado: captura sin conexión

En una zona rural la conexión puede desaparecer justo mientras se está midiendo.
El nodo edge simulado es la pieza que hace que eso no importe: **acepta una
sesión de monitoreo aunque la API esté apagada**, la conserva aunque el proceso
se cierre, y la entrega más tarde sin que se registre dos veces.

Es un componente de software, no un dispositivo: sustituye al wearable para
poder estudiar el comportamiento del sistema ante conectividad intermitente sin
depender de hardware. Todos los datos que maneja son simulados y ficticios.

### Cómo funciona

```
captura simulada
  → transacción SQLite: el paquete y su registro de envío, juntos o ninguno
  → cierre o reinicio del proceso, sin pérdida
  → recuperación de conectividad
  → envío a la API con la misma Idempotency-Key
  → confirmación remota
  → estado local ENVIADO
```

Tres piezas sostienen la garantía:

1. **SQLite** guarda el paquete validado y su clave antes de que exista ningún
   intento de red.
2. **Una outbox** lleva el estado de entrega, separado del paquete —que ya no
   vuelve a cambiar—.
3. **La idempotencia de la API** (`Idempotency-Key`, §7 anterior) hace que
   repetir un envío no cree una segunda sesión.

Lo que se puede afirmar con precisión no es «entrega exactamente una vez» —eso
no lo puede prometer ningún cliente sobre una red donde una respuesta se puede
perder—, sino: **el paquete sobrevive localmente hasta confirmarse, un reintento
repite exactamente la misma clave y el mismo cuerpo, y por eso el efecto en
PostgreSQL ocurre una sola vez**.

### Comandos

Desde la raíz del repositorio:

```powershell
python scripts/edge_node.py init                          # crea o actualiza el almacenamiento local
python scripts/edge_node.py capturar paquete.json         # guarda un paquete, sin usar la red
python scripts/edge_node.py estado                        # resumen de la outbox
python scripts/edge_node.py enviar                        # UNA ronda, sin esperas
python scripts/edge_node.py sincronizar                   # reintentos con espera incremental
python scripts/edge_node.py traza <Idempotency-Key>       # qué pasó con un evento
```

#### `enviar` y `sincronizar` no son lo mismo

| | `enviar` | `sincronizar` |
| --- | --- | --- |
| Rondas | una y termina | las que haga falta, hasta que no quede trabajo |
| Espera entre intentos | ninguna | espera incremental acotada |
| Fecha del próximo intento | **la ignora** | la respeta |
| Máximo de intentos | lo respeta | lo respeta |
| Repara un intento sin resultado | no | sí |
| Para qué sirve | forzar un envío ahora, en una demostración | la sincronización diferida del ticket |

`enviar` ignora la programación a propósito: es una acción manual y sería absurdo
hacer esperar a una persona que está pidiendo un envío inmediato. Lo que **no**
puede saltarse es el máximo de intentos —un límite que un comando puede rebasar
no es un límite— ni un intento que ya está en vuelo.

Ninguno de los dos es un demonio. `sincronizar` también termina.

El archivo del paquete tiene **exactamente** el mismo formato que el cuerpo de
la solicitud documentado en el §3: el nodo valida con el mismo contrato y no
añade ninguna regla propia. Todo lo que el endpoint acepta, la captura lo
acepta. Lo normal es que `fecha_hora_sincronizacion` vaya en `null` —una captura
sin conexión no se ha sincronizado todavía—, pero si el archivo ya trae un
instante válido, se guarda tal cual. La razón está más abajo.

`--base` apunta a otro archivo SQLite sin tocar la configuración, que es lo
recomendable para una demostración:

```powershell
python scripts/edge_node.py --base data/edge/demo.sqlite3 init
```

### Configuración

| Variable | Por omisión | Qué controla |
| --- | --- | --- |
| `EDGE_SQLITE_PATH` | `data/edge/nodo_edge.sqlite3` | archivo local del nodo |
| `EDGE_API_BASE_URL` | `http://127.0.0.1:8000` | API a la que se entrega |
| `EDGE_HTTP_TIMEOUT` | `10.0` | segundos de espera por respuesta |
| `EDGE_BUSY_TIMEOUT_MS` | `5000` | milisegundos de espera por bloqueo de SQLite |
| `EDGE_MAX_ATTEMPTS` | `5` | intentos por evento, **incluido el primero** |
| `EDGE_BASE_DELAY_SECONDS` | `1.0` | primera espera, en segundos |
| `EDGE_MAX_DELAY_SECONDS` | `60.0` | techo de la espera |
| `EDGE_BATCH_LIMIT` | `50` | eventos que toma **una ronda** |
| `EDGE_API_TOKEN` | *(sin valor)* | token de sesión de una cuenta PACIENTE; solo lo exigen `enviar` y `sincronizar` |

`EDGE_MAX_ATTEMPTS`, `EDGE_BASE_DELAY_SECONDS`, `EDGE_MAX_DELAY_SECONDS` y
`EDGE_BATCH_LIMIT` son solo valores por omisión. El límite que gobierna un evento
concreto es el que **adoptó** al reclamar su primer intento, guardado en
`max_intentos_aplicado`: cambiar el entorno alcanza a los eventos que todavía no
han empezado a sincronizarse, y no reescribe el contrato de los que ya están en
curso.

#### Qué duraciones se admiten

`EDGE_BASE_DELAY_SECONDS`, `EDGE_MAX_DELAY_SECONDS` y `EDGE_HTTP_TIMEOUT` no
aceptan cualquier número positivo: tienen que ser duraciones que este comando
pueda **programar de verdad**, porque las tres acaban dentro de un `timedelta`.

```
0.000001 s (1 µs)  ≤  duración  ≤  86400 s (24 h)
```

- **El mínimo es la resolución de `timedelta`.** No es un número elegido a ojo:
  `timedelta` redondea al microsegundo más cercano, así que por debajo del
  microsegundo la espera que se programa deja de ser la que se configuró, y por
  debajo de medio microsegundo se programa **cero** —es decir, ninguna espera—.
  `EDGE_BASE_DELAY_SECONDS=5e-324` pasaba el antiguo `> 0` y hacía desaparecer en
  silencio la espera incremental que el ticket promete.
- **El máximo es una decisión operacional.** No es el máximo de `float` ni
  `timedelta.max`, que serían cotas falsas: una espera de mil años es
  representable y no la programa nadie. `sincronizar` es un comando finito que
  una persona lanza y espera, y una sola espera de más de un día sobrevive a
  cualquier sesión manual. De paso deja fuera `1e308`, que hacía estallar
  `timedelta` con un `OverflowError` a mitad de una pasada.

El **lease** —`4 × EDGE_HTTP_TIMEOUT + 30 s`— se deriva de una de ellas, y tiene
que caber en el mismo intervalo. De ahí sale un límite implícito:

```
EDGE_HTTP_TIMEOUT ≤ 21592.5 s
```

Todo esto se comprueba **al construir la política**, antes de que exista una
petición HTTP, antes de incrementar `intentos`, antes de insertar una fila en
`intento_sincronizacion` y antes de mover el estado de ningún evento. Una
configuración fuera de rango termina en `Error: ...` por `stderr` y **código 1**,
con la base local intacta.

`data/edge/` está en `.gitignore`: la base del nodo es un artefacto local y
**nunca** se versiona.

`EDGE_API_TOKEN` es la única credencial del nodo, y cargar la configuración no la
exige: `init`, `capturar`, `estado` y `traza` funcionan sin ella. `enviar` y
`sincronizar` sí, porque el canal edge → API requiere una credencial PACIENTE
válida. El token viaja como cabecera `Authorization: Bearer` del cliente HTTP y
no se guarda en SQLite, ni en la outbox, ni en el paquete, ni en la huella de
idempotencia, ni en la traza. Antes de reclamar la outbox, los dos comandos hacen
un preflight contra `GET /api/v1/autenticacion/yo`: una credencial ausente o
inválida detiene la ejecución sin consumir intentos. Cómo cargar el token sin
dejarlo en el historial del shell: [El nodo edge](#el-nodo-edge).

### Estados de la outbox

| Estado | Significado |
| --- | --- |
| `PENDIENTE` | capturado localmente, nunca confirmado. Se intentará en la próxima pasada. |
| `ENVIADO` | la API confirmó el paquete, como creación o como reenvío equivalente. **Terminal.** |
| `FALLIDO` | el último intento no pudo confirmarse. Se conserva todo; `reintentable` dice si otra pasada puede volver a intentarlo o si hace falta revisarlo. |

Un `FALLIDO` **reintentable** (error de transporte, `5xx`) vuelve a la cola. Un
`FALLIDO` **en revisión** (`409`, `404`, `422`) no: repetir la misma operación
esperando otra respuesta no es una política, es una espera.

Un `FALLIDO` en revisión guarda además **por qué** dejó de reintentarse, en
`motivo_revision`:

| Motivo | Qué ocurrió |
| --- | --- |
| `RECHAZO_PERMANENTE` | la API lo rechazó: `409`, `4xx`, o un `201` que no cumple el contrato |
| `AGOTAMIENTO` | consumió todos sus intentos sin confirmarse |
| `AGOTAMIENTO_HEREDADO` | traía de SCRUM-64 tantos intentos como el límite que adoptó |

Se guarda en vez de deducirse porque deducirlo dependería del `EDGE_MAX_ATTEMPTS`
del momento en que se mira, y un evento no debería cambiar de diagnóstico porque
alguien editara un `.env`.

`ENVIADO` no se degrada nunca. Si dos envíos coincidieran, el que llegue después
con un fallo tardío no puede sobrescribir una entrega ya confirmada.

### La misma clave en cada intento

Cada paquete recibe **un UUID4, generado una sola vez**, guardado en la misma
transacción que el paquete. Esa cadena es la `Idempotency-Key` de todos sus
intentos. No cambia tras un timeout, ni tras un reinicio, ni tras un `5xx`, ni
cuando la API responde que es un reenvío.

Eso es justamente lo que convierte un segundo intento en un *replay* en vez de
una operación nueva: **una clave distinta con el mismo cuerpo crearía otra
sesión**, que es el duplicado que este diseño existe para evitar.

Por la misma razón el cuerpo tampoco se vuelve a construir: se guarda una vez y
se reenvía tal cual, byte por byte.

Eso es también lo que resuelve el caso de `fecha_hora_sincronizacion`. Ese campo
forma parte de la huella con la que el servidor reconoce un reenvío, así que lo
peligroso no es que traiga un valor sino que el valor **cambie entre intentos**:
un instante refrescado en el segundo envío convertiría un reenvío legítimo en un
`409`, justo después de una respuesta perdida, que es cuando más falta hace que
funcione. Guardar el paquete una sola vez lo congela, y con eso el peligro
desaparece sin necesidad de prohibir nada:

- **El nodo nunca lo estampa.** Un paquete capturado sin conexión no se ha
  sincronizado, así que el campo queda en `null`. El instante en que la entrega
  se confirmó se registra en `enviado_en` de la outbox, que es donde ese hecho
  realmente ocurre.
- **Si el archivo de entrada ya lo trae**, y el contrato lo acepta, el nodo lo
  acepta: lo guarda sin modificarlo y lo reenvía idéntico en cada intento.
  Rechazarlo habría significado que el nodo aplicara un contrato más estricto
  que el de la API a la que alimenta —dos juegos de reglas, libres de
  separarse—.

### Demostración de reinicio y recuperación

Con la API **apagada**:

```powershell
python scripts/edge_node.py --base data/edge/demo.sqlite3 init
python scripts/edge_node.py --base data/edge/demo.sqlite3 capturar paquete.json
python scripts/edge_node.py --base data/edge/demo.sqlite3 estado
```

La captura funciona y el resumen muestra un `PENDIENTE`. El comando termina: no
hay proceso residente. Volver a ejecutar `estado` —con el proceso anterior ya
cerrado— muestra el mismo evento, con la misma clave.

Con la API **encendida** (`uvicorn app.main:app` desde `backend/`, contra una
base en `head`):

```powershell
python scripts/edge_node.py --base data/edge/demo.sqlite3 sincronizar
python scripts/edge_node.py --base data/edge/demo.sqlite3 traza <clave>
```

El evento pasa a `ENVIADO`. Ejecutar `sincronizar` otra vez no selecciona nada:
lo confirmado no se reenvía. Y si la confirmación se hubiera perdido, el
siguiente intento sería un *replay* con los mismos identificadores —una sola
sesión en PostgreSQL—.

Para ver la recuperación completa, basta apagar la API, lanzar `sincronizar` con
un límite pequeño y volver a encenderla antes de que se agoten los intentos:

```powershell
python scripts/edge_node.py --base data/edge/demo.sqlite3 sincronizar --max-intentos 3 --espera-base 5
```

El primer intento falla, el comando anuncia la espera, y al recuperarse la API el
siguiente termina en `201`. Si la API no vuelve, los tres intentos se consumen y
el evento queda `FALLIDO` con motivo `AGOTAMIENTO` —**no hay un cuarto**—.

#### Códigos de salida de `sincronizar`

| Código | Significado |
| --- | --- |
| `0` | la outbox estaba vacía, o todo lo relevante quedó `ENVIADO` |
| `1` | error controlado de almacenamiento o de configuración |
| `2` | queda al menos un evento agotado, rechazado o bloqueado: hace falta una persona |
| `3` | condición interna inesperada: quedó trabajo elegible que no pudo intentarse |

`enviar` conserva sus códigos `0` y `1` de SCRUM-64.

### Reintentos, espera incremental y agotamiento

`sincronizar` aplica una política **finita, configurable y única**: no hay una
fórmula en el emisor y otra en el reintento.

```
delay(k) = min(EDGE_BASE_DELAY_SECONDS × 2^(k-1), EDGE_MAX_DELAY_SECONDS)
```

`k` es el **número ordinal del intento que acaba de fallar**. No es «los intentos
previos»: esa lectura produce un desfase de uno, y con intentos heredados de
SCRUM-64 se nota enseguida. Un evento que trae tres intentos de la versión
anterior hace el número 4; si falla, le toca `delay(4)`.

Cuántas duplicaciones caben hasta el techo **depende de la base**, así que no hay
ningún tope fijo del exponente: el punto de saturación se deriva de los dos
valores configurados. Con las omisiones la séptima espera ya está en el techo;
en el extremo del rango admisible —base 1 µs, techo 24 h— hacen falta 38. Un
ordinal enorme devuelve el techo, sin desbordarse.

> **`EDGE_MAX_ATTEMPTS` incluye el primer intento.** Con 3 hay un intento
> inmediato y dos reintentos, y **no existe un cuarto intento automático**.

Con los valores por omisión —5 intentos, base 1 s, techo 60 s— la secuencia es:

```
intento 1   inmediato
intento 2   tras 1 s
intento 3   tras 2 s
intento 4   tras 4 s
intento 5   tras 8 s
            agotado: FALLIDO / AGOTAMIENTO
```

Suma de esperas: **15 segundos**. A eso hay que añadir el tiempo de las cinco
peticiones HTTP. Es una estimación de las condiciones del prototipo, no un máximo
real: httpx no impone una fecha límite total de petición, y sus timeouts de
lectura y escritura acotan la inactividad entre fragmentos, no la duración
completa.

No hay *jitter*: un solo nodo no tiene manada que dispersar.

Un error **permanente** no espera nada. Gasta un intento y se cierra.

### Las cuatro fechas de un evento

| Fecha | Dónde vive | Qué significa exactamente |
| --- | --- | --- |
| **captura** | `captura_local.capturado_en` | instante leído del reloj **inmediatamente antes** de abrir la transacción de captura |
| **intento** | `intento_sincronizacion.iniciado_en` | inicio de cada intento. **Una fila por intento**, no un único «último» |
| **confirmación** | `finalizado_en` del intento que aplicó la transición | instante en que el edge **recibió y validó** una aceptación. Reloj local del nodo |
| **sincronización** | `outbox.enviado_en` | instante registrado para la transición local a `ENVIADO` |

Todas en UTC con zona explícita. La de confirmación se **deriva** del intento que
movió la fila, en lugar de copiarse a la outbox: dos copias del mismo instante en
dos tablas no se pueden mantener iguales con ninguna restricción de SQLite, y
acabarían discrepando.

PostgreSQL conserva además su propia evidencia temporal en
`operacional.idempotencia_solicitud.fecha_hora`. Son **relojes distintos** y la
documentación no finge lo contrario.

Un evento entregado antes de SCRUM-65 no tiene historial de intentos, así que su
confirmación aparece como «no medida». No se rellena con `enviado_en`, que es
otra medición de otro momento.

### Consultar la traza de un evento

```powershell
python scripts/edge_node.py traza 7c9e6679-7425-40de-944b-e07fc1f90ae7
```

El identificador de correlación **es la `Idempotency-Key`**. No se creó un
segundo identificador: la clave ya nace en el nodo, ya viaja en cada petición, ya
está guardada en `operacional.idempotencia_solicitud`, es opaca y no contiene
ningún dato clínico. Dos identificadores para la misma operación solo se pueden
desincronizar.

La salida distingue los desenlaces posibles —pendiente sin intentar, en espera de
un nuevo intento, confirmado en primera aceptación, confirmado por *replay*,
agotado, rechazado permanentemente— y muestra la secuencia de intentos con su
resultado, su código HTTP y la espera que se aplicó después de cada uno.

**Nunca imprime** el paquete, un valor clínico, una credencial, una URL, una
cabecera, SQL ni un *stack trace*, y no hay ninguna opción para pedirlos.

La correlación completa es:

```
SQLite: outbox.clave_idempotencia
  → HTTP: cabecera Idempotency-Key
  → PostgreSQL: operacional.idempotencia_solicitud (recurso, clave)
  → id_sesion e ids_lectura de esa misma fila
```

### Evolución del almacenamiento local

El esquema local está versionado con `PRAGMA user_version`. SCRUM-64 creó la
versión 1; esta versión es la 2, y `init` **actualiza una base v1 conservando
todos sus eventos**: no hay que borrar nada.

```powershell
python scripts/edge_node.py --base data/edge/demo.sqlite3 init
# Almacenamiento local actualizado a la version actual. Los eventos guardados se
# conservaron; los intentos anteriores se cuentan como heredados y no tienen
# historial detallado.
```

Los intentos que una base v1 ya había consumido se conservan en
`intentos_heredados` y **el contador no se reinicia**: cuentan para el límite. Lo
que no existe es su detalle, porque nunca se guardó, y la traza lo dice en vez de
inventarlo:

```
intentos            : 4 de 5  (3 heredados de SCRUM-64, sin historial detallado)
secuencia de intentos:
  1-3                 sin historial disponible: capturados antes de SCRUM-65
  4                   2026-03-01T12:00:01+00:00 | REINTENTABLE | http 503 | espera 8 s
```

Una base de una versión que esta instalación no conoce se **rechaza**; no se
migra ni se reescribe.

### Un intento que se quedó sin respuesta

Si el proceso muere entre el envío y el guardado del resultado, el intento queda
registrado **sin desenlace**. La siguiente ejecución lo repara: lo marca como
observado —sin inventarle un resultado— y, según queden intentos o no, programa
el siguiente o declara el agotamiento.

Que la ventana de recuperación venza **autoriza** a repararlo; no demuestra que
la petición original haya terminado. Una respuesta puede llegar más tarde, y el
diseño lo trata como caso normal: si es una entrega válida, prevalece; si es un
fallo, se guarda en el historial y no reabre un evento ya cerrado. Lo que impide
un duplicado no es esa ventana, sino la misma `Idempotency-Key` de siempre.

### Lo que este nodo todavía no hace

No hay servicio permanente, ni demonio, ni detección automática de conectividad,
ni orquestación de varios nodos, ni métricas operativas, ni purga de la outbox.
`sincronizar` es un comando que empieza, hace su trabajo y termina; quien decida
ejecutarlo periódicamente es trabajo posterior y no debe darse por implementado.

Desde SCRUM-70 el endpoint al que entrega **sí** exige autenticación, y el nodo
presenta una credencial de sesión: ver «Autenticación, RBAC y auditoría». La
renovación del token es manual en este MVP.

## Esquema analítico y ETL

El modelo dimensional aprobado —el Star Schema del Capítulo III, versión 6 del
diagrama— vive en su propio esquema de PostgreSQL, `analitico`, junto a
`operacional` y en la misma base. Lo alimenta un ETL *batch* reproducible: lee
`operacional`, clasifica cada lectura con la Tabla de Umbrales de Tamizaje y
carga dimensiones, bridge y hechos. Los tableros de Power BI, que se construirán
después, leerán únicamente `analitico`; el ETL no los crea.

### Qué contiene

| Estructura del modelo | Tabla física | Grano |
| --- | --- | --- |
| `Fact_LecturaBiometrica` | `analitico.fact_lectura_biometrica` | una fila por lectura biométrica operacional (`id_lectura`) |
| `Dim_Paciente` | `analitico.dim_paciente` | una fila por paciente |
| `Dim_Medico` | `analitico.dim_medico` | una fila por médico |
| `Dim_Clinica` | `analitico.dim_clinica` | una fila por clínica |
| `Dim_TiempoGestacional` | `analitico.dim_tiempo_gestacional` | una fila por semana del catálogo |
| `Dim_Embarazo` | `analitico.dim_embarazo` | una fila por embarazo |
| `Dim_Semaforo` | `analitico.dim_semaforo` | una fila por nivel (OK, WARNING, ERROR) |
| `Dim_FactorRiesgo` | `analitico.dim_factor_riesgo` | una fila por factor de riesgo |
| `Bridge_EmbarazoFactorRiesgo` | `analitico.bridge_embarazo_factor_riesgo` | una fila por par embarazo/factor |

- Las dimensiones usan como clave el identificador operacional, sin claves
  sustitutas ni historial: se sobrescriben en su lugar (tipo 1).
- `id_sesion` es una **adición técnica** al hecho respecto del diagrama: permite
  contar sesiones —732 frente a 1,180 lecturas— y rastrear cada lectura hasta su
  sesión, sin crear otra dimensión.
- Ninguna tabla de `analitico` tiene llave foránea hacia `operacional`. La
  trazabilidad se conserva con las claves operacionales copiadas y la comprueba
  la conciliación.
- El médico de cada lectura es el del seguimiento PRINCIPAL vigente ese día, y
  debe estar afiliado ese mismo día a la clínica del embarazo; si no, la
  ejecución se detiene.
- La clínica de `Dim_Medico` y `Dim_Paciente` es contexto y admite NULL cuando
  todavía no hay relación —una paciente sin embarazo registrado, un médico sin
  afiliación—: nunca una clínica inventada. La ruta para filtrar por clínica es
  `Fact_LecturaBiometrica.id_clinica`.
- `Dim_Embarazo.clasificacion_embarazo` queda en NULL: el modelo la prevé, pero
  las fuentes de negocio aún no definen una regla de clasificación. El ETL no
  inventa semántica clínica y cada ejecución informa cuántas quedan pendientes.

El mapeo campo por campo, las reglas y las verificaciones están en
[docs/modelo_analitico_etl.md](docs/modelo_analitico_etl.md).

### Ejecutar

Requisitos: la base en `alembic upgrade head` y el esquema operacional con
datos —por ejemplo, el dataset simulado cargado como se explica arriba. La
conexión sale de **`ETL_DATABASE_URL`**, la credencial del rol técnico
`fetalalert_etl`: desde SCRUM-98 el ETL no usa `DATABASE_URL` —que es la del
runtime de la API— ni `ALEMBIC_DATABASE_URL`. **No hay respaldo**; si la variable
falta, el ETL se detiene. Si el comando se ejecuta desde Windows fuera de Docker,
esa variable debe apuntar a `127.0.0.1` y al puerto publicado por el contenedor.

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
cd ..
.\backend\.venv\Scripts\python.exe scripts\etl_analitico.py ejecutar    # carga incremental y conciliación
.\backend\.venv\Scripts\python.exe scripts\etl_analitico.py conciliar   # solo conciliación, sin escribir
```

`ejecutar` es una única transacción: o se confirma completa —dimensiones, bridge
y hechos— o no queda nada. No hay scheduler ni demonio: una ejecución nocturna
futura invocaría este mismo comando.

Ejemplo de la primera ejecución sobre el dataset simulado (salida resumida; no
contiene ningún dato personal):

```text
ETL analítico FetalAlert
resultado=SUCCESS
revision_alembic=54053d46abd6
version_umbrales=SIM-1.0
dim_paciente: insertadas=30 actualizadas=0 sin_cambios=0
bridge_embarazo_factor_riesgo: insertadas=23 actualizadas=0 sin_cambios=0
hechos_nuevos=1180
hechos_existentes=0
conciliacion=OK verificaciones=27 fallidas=0
lecturas_origen=1180 hechos=1180
sesiones_con_lecturas_origen=732 sesiones_en_hecho=732 sesiones_monitoreo=732
forma_signos_maternos=560 forma_movimiento=620
semaforo: OK=826 WARNING=295 ERROR=59
estado_hr: OK=472 WARNING=76 ERROR=12
estado_spo2: OK=478 WARNING=64 ERROR=18
estado_mov: OK=436 WARNING=155 ERROR=29
clasificacion_embarazo_pendiente=30
```

Una segunda ejecución sobre el mismo origen informa `hechos_nuevos=0` y todas
las dimensiones `sin_cambios`.

| Código | Significado |
| ---: | --- |
| `0` | éxito (en `conciliar`: sin diferencias) |
| `1` | error de configuración, de precondición o de base de datos |
| `2` | el origen contiene algo que las reglas no permiten cargar sin inventar: un valor sin regla, una derivación ambigua o un semáforo distinto del registrado |
| `3` | la conciliación encontró diferencias |
| `4` | otra ejecución tiene el candado |

### Clasificación: estados por métrica y semáforo global

El ETL calcula `estado_hr`, `estado_spo2` y `estado_mov` con la versión
**SIM-1.0** de los umbrales, que traduce la Tabla de Umbrales de Tamizaje a
intervalos sobre valores continuos:

| Métrica | ERROR | WARNING | OK |
| --- | --- | --- | --- |
| Frecuencia cardíaca materna (lpm) | `< 55` o `> 110` | `55 ≤ HR < 60` o `100 ≤ HR ≤ 110` | `60 ≤ HR < 100` |
| SpO₂ (%) | `< 92` | `92 ≤ SpO₂ < 95` | `≥ 95` |
| Movimientos fetales (conteo) | `< 5` | `5 – 9` | `≥ 10` |

- El 100 figura en la tabla como normal y como precaución: gana la mayor
  severidad, así que es WARNING.
- Entre 55 y 60 lpm la clasificación es WARNING: una bradicardia que todavía no
  alcanza el umbral de alerta es precaución de **tamizaje**, no un diagnóstico
  individual. Los intervalos son exhaustivos, así que ningún valor de HR detiene
  la ejecución.
- El umbral de movimientos, 10, es la referencia de validación del dataset
  simulado, no una afirmación clínica universal. No hay movimientos válidos antes
  de la semana 20.
- Una métrica que no aplica queda en NULL, igual que su estado; `mov_valor = 0`
  es un conteo real y se clasifica.

El semáforo global es el estado más severo de las métricas que aplican. Antes de
confirmar, el ETL lo compara con el semáforo que la lectura ya tiene en
`operacional`: si alguno difiere, la ejecución completa se revierte y no se
corrige ni el origen ni el hecho.

### Idempotencia, incrementalidad y recuperación

- **Lecturas nuevas:** son las cuyo `id_lectura` no está en el hecho. Esa
  comparación de conjuntos encuentra una llegada tardía con fecha clínica
  antigua, un identificador menor que el mayor ya cargado o un hueco en la
  secuencia. No se usa `MAX(id)`, ni una marca de agua por fecha, ni
  `fecha_hora_sincronizacion`, ni la fecha de la tabla de idempotencia.
- **Dimensiones:** se actualizan solo si algún valor cambió de verdad.
- **Hechos:** son inmutables y se insertan con un `INSERT` normal. Si una
  lectura ya cargada cambia o desaparece en el origen, la conciliación lo
  detecta y la ejecución falla sin actualizar ni borrar el hecho; si una clave
  candidata ya existiera, la inserción falla en lugar de saltarse la fila.
- **Una sola transacción `REPEATABLE READ`,** cuya primera sentencia toma un
  candado de transacción de PostgreSQL (`pg_try_advisory_xact_lock`): una
  segunda ejecución simultánea se detiene en el acto, sin leer ni escribir, y
  termina con código 4. El candado lo libera PostgreSQL al confirmar o al
  revertir, así que no existe ninguna liberación manual.
- **Conciliación completa antes del commit**, con SQL independiente del código
  que transforma: conteos, conjuntos de claves, contenido, semáforo, formas de
  lectura, huérfanos, médico responsable, clínicas, dimensiones y bridge.
- **Sin tabla de control:** el progreso es el propio conjunto de claves del
  hecho, y solo avanza cuando la transacción se confirma.

### Seguridad

La salida del comando contiene solo identificadores técnicos, conteos y códigos.
Nunca imprime una cédula, un nombre, un teléfono, un correo, un valor biométrico,
`DATABASE_URL` ni una contraseña, y un error de base de datos se describe sin su
sentencia ni sus parámetros. Las dimensiones de paciente y médico contienen los
atributos personales del modelo aprobado, con datos exclusivamente simulados;
la seguridad por fila, la seudonimización y el acceso de solo lectura para Power
BI corresponden a tickets posteriores y no están implementados aquí.

### Desarrollo apilado

Durante su desarrollo inicial, SCRUM-69 se construyó temporalmente apilado sobre
la rama de la sincronización diferida del nodo edge (SCRUM-65), que entonces
seguía en revisión: la revisión de Alembic del esquema analítico
(`60facdbacf51`) se apoya en `87d8ed46686b`. La sincronización no cambió
PostgreSQL, así que el ETL no dependía de su código; solo compartía la misma línea
base. SCRUM-65 se integró después en `main` mediante el Pull Request #13, y
SCRUM-69 mediante el #14: los dos forman parte de `main`. La estrategia vigente
vuelve a ser crear cada ticket desde el `main` actualizado, salvo una dependencia
explícita y documentada (ver [Estrategia de ramas](#estrategia-de-ramas)).

## Autenticación, RBAC y auditoría

Desde SCRUM-70 la API tiene identidad. El endpoint de ingesta ya no es público:
exige una credencial de sesión válida y el rol PACIENTE.

> **RBAC limita operaciones por rol; el aislamiento por fila es otra capa, y ya
> existe.** SCRUM-70, que es lo que describe esta sección, comprueba que *el rol*
> PACIENTE puede registrar una sesión de monitoreo, y eso es todo lo que
> comprueba. La correlación `usuario_paciente → paciente → embarazo` la añade
> SCRUM-98 y no vive aquí: vive en las políticas de RLS de PostgreSQL, que se
> aplican incluso si una ruta futura olvidara comprobarla. Las dos capas son
> acumulativas —el rol decide *qué operación*, la política decide *sobre qué
> filas*— y ninguna sustituye a la otra.

### Configurar la firma

El material de firma viene de la variable `JWT_SECRET_KEY` y **no tiene valor por
omisión**: la API se niega a arrancar sin él. Debe aportar al menos 32 bytes de
material aleatorio y no puede repetir la contraseña de PostgreSQL.

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

El valor generado va en el `.env` local, que no se versiona. `.env.example`
documenta la variable **vacía** a propósito: un ejemplo que funcionara acabaría
reutilizado. Una variable vacía o con solo espacios cuenta como no definida.
`JWT_EXPIRATION_MINUTES` es opcional, acepta entre 1 y 1440 y vale 30 si no se
define.

Con Docker Compose el camino es explícito: el `.env` documenta y guarda el
material, y `docker-compose.yml` pasa `JWT_SECRET_KEY` y `JWT_EXPIRATION_MINUTES`
—30 si falta— al servicio `api`, y a ningún otro. Si la clave falta o no es
válida, el contenedor de la API se detiene al importar `app.main` con
`ConfiguracionJWTInvalida`, en vez de arrancar sin poder autenticar. Compose no
la declara obligatoria a propósito: `docker compose up -d db` debe seguir
funcionando sin ella. `EDGE_API_TOKEN` es del nodo edge y el contenedor de la API
no la recibe.

Alembic, el ETL y el cargador del dataset **no** necesitan esta variable: no
emiten ni verifican tokens, y hacerlos depender de una credencial que no usan
rompería las migraciones por un motivo ajeno.

### Obtener un token

```text
POST /api/v1/autenticacion/token
Content-Type: application/json

{"email": "paciente01@example.com", "password": "<credencial simulada>"}
```

Respuesta:

```json
{
  "access_token": "<redactado>",
  "token_type": "bearer",
  "expires_in": 1800
}
```

La respuesta no lleva nada más: ni identificador, ni rol, ni correo, ni hash, ni
*refresh token*. Las credenciales de las 37 cuentas simuladas las produce
`scripts/generate_mock_data.py`, que guarda un digest Argon2id auténtico con salt
aleatorio de la biblioteca; la contraseña de partida está declarada allí como
credencial ficticia del dataset académico y no protege nada.

Un token se presenta como `Authorization: Bearer <token>`. En Swagger
(`/docs`), el botón **Authorize** acepta pegarlo.

### Credenciales inválidas

Una cuenta inexistente, una contraseña incorrecta y una cuenta desactivada
producen **la misma respuesta**: mismo `401`, mismo cuerpo, misma cabecera. Las
tres pasan además por una verificación Argon2id —la del usuario inexistente
contra un digest ficticio construido una sola vez al arrancar— para que no haya
una diferencia trivial de tiempo que confirme qué correos existen.

### Rutas públicas y protegidas

| Método y ruta | Operación | Público/protegido | ADMIN | MEDICO | PACIENTE |
| --- | --- | --- | --- | --- | --- |
| `POST /api/v1/autenticacion/token` | Obtener un token | Público | n/a | n/a | n/a |
| `GET /api/v1/autenticacion/yo` | Identidad técnica | Protegido | ✔ | ✔ | ✔ |
| `POST /api/v1/sesiones-monitoreo` | Registrar sesión y lecturas | Protegido | **403** | **403** | **✔ 201** |
| `POST /api/v1/cuentas/pacientes/{id_paciente}` | Provisionar cuenta PACIENTE (SCRUM-97) | Protegido | **✔ 201** | **403** | **403** |
| `POST /api/v1/cuentas/medicos/{id_medico}` | Provisionar cuenta MEDICO (SCRUM-97) | Protegido | **✔ 201** | **403** | **403** |
| `PATCH /api/v1/cuentas/{id_usuario}/estado` | Desactivar o reactivar (SCRUM-97) | Protegido | **✔ 200** | **403** | **403** |
| `GET /health` | Sonda de salud | Público | ✔ | ✔ | ✔ |
| `GET /docs`, `/redoc`, `/openapi.json` | Documentación local | Público | ✔ | ✔ | ✔ |

El médico consulta información clínica y el administrador es responsable
técnico; por mínimo privilegio, ninguno de los dos crea sesiones clínicas, y una
credencial administrativa no sirve de atajo hacia datos clínicos.

`GET /api/v1/autenticacion/yo` devuelve exactamente `id_usuario` y `rol`. Sirve
para verificar identidad técnica —el nodo edge lo usa— y **no** es evidencia de
permisos de negocio diferenciados. Al cierre de SCRUM-70 la API tenía una sola
operación de negocio; SCRUM-97 añade las tres rutas administrativas de cuentas,
exclusivas de ADMIN.

### Semántica `401` y `403`

| Código | Significado | Cabecera |
| --- | --- | --- |
| `401` | Falta la credencial, o su esquema no es Bearer | `WWW-Authenticate: Bearer` |
| `401` | El token no verifica, expiró, o la cuenta ya no existe o está desactivada | `WWW-Authenticate: Bearer error="invalid_token"` |
| `403` | La identidad es válida y su rol no puede ejecutar la operación | *(sin desafío)* |

El rol **no viaja dentro del token**: se lee de PostgreSQL en cada petición, así
que desactivar una cuenta o cambiarle el rol surte efecto en la petición
siguiente y no al expirar el token. Un claim `rol` inyectado en un token no
cambia nada, porque nadie lo lee.

### Auditoría

Ocho acciones, y ninguna más: las cuatro de SCRUM-70 y las cuatro del ciclo de
cuentas de SCRUM-97.

| Acción | Actor | Entidad | Cuándo |
| --- | --- | --- | --- |
| `LOGIN_EXITOSO` | la cuenta | `usuario` | credenciales válidas |
| `LOGIN_FALLIDO` | `NULL` | — | cuenta inexistente, contraseña incorrecta o cuenta inactiva |
| `ACCESO_DENEGADO_ROL` | la cuenta | `sesion_monitoreo` o `usuario` | identidad válida, rol sin permiso |
| `SESION_MONITOREO_REGISTRADA` | la cuenta | `sesion_monitoreo` | paquete creado de verdad |
| `CUENTA_PACIENTE_PROVISIONADA` | el ADMIN | `usuario` + id de la cuenta creada | cuenta PACIENTE creada y vinculada |
| `CUENTA_MEDICO_PROVISIONADA` | el ADMIN | `usuario` + id de la cuenta creada | cuenta MEDICO creada y vinculada |
| `CUENTA_DESACTIVADA` | el ADMIN | `usuario` + id de la cuenta | transición activa → inactiva |
| `CUENTA_REACTIVADA` | el ADMIN | `usuario` + id de la cuenta | transición inactiva → activa |

Decisiones que conviene leer explícitas:

- **Un token rechazado no escribe ninguna fila.** No tiene actor que atribuir, y
  el endpoint es alcanzable sin autenticarse: persistirlo entregaría a cualquiera
  una escritura sin autenticar en la tabla de auditoría. El rechazo queda en el
  log de aplicación saneado.
- **Un *replay* idempotente tampoco.** No se creó ninguna fila de negocio, y ese
  camino revierte su transacción por contrato.
- **La auditoría de una creación viaja dentro de la transacción del paquete**,
  antes del único `commit`. Así la entrada y la sesión se confirman juntas o no
  se confirma ninguna, y un rollback de negocio no puede dejar atrás un éxito
  falso. No se añadió ningún `commit` intermedio.
- **El login y la denegación usan una transacción propia**, porque no hay
  transacción de negocio a la que unirse.
- **Fallo cerrado:** si la auditoría de un login exitoso no se puede escribir, no
  se emite token.
- `ip_origen` sale de `request.client.host`, o de un literal técnico fijo cuando
  el servidor no observa cliente o el valor no cabe en la columna. **No** se lee
  `X-Forwarded-For`: no hay proxy de confianza en este MVP. Una dirección IP
  puede considerarse dato personal bajo la Ley 81; la columna es una decisión
  heredada del modelo de SCRUM-51, no de este ticket.
- El correo introducido en un intento fallido **no se guarda** en ninguna parte.

### El nodo edge

La sincronización del nodo edge exige ahora una credencial. Conviene distinguir
dos canales que la arquitectura mantiene separados:

- **Consulta local de la gestante:** no necesita cuenta central, ni token, ni
  conectividad. Corresponde al prototipo original y este repositorio no la
  implementa.
- **Sincronización edge → API:** sí requiere identidad autenticada ante el
  backend.

La captura sin conexión sigue sin necesitar nada: `init`, `capturar`, `estado` y
`traza` funcionan sin `EDGE_API_TOKEN`. Solo los comandos que usan la red
—`enviar` y `sincronizar`— la exigen; una variable vacía o con solo espacios
cuenta como no definida, y el comando se detiene sin construir ninguna petición.

El token se carga en la variable de entorno **sin que quede en el historial del
shell**:

```powershell
$credencial = Read-Host "Pega el token del nodo edge" -AsSecureString
$puntero = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($credencial)
try {
    $env:EDGE_API_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto($puntero)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($puntero)
}
```

El valor es respuesta a un prompt, no parte del comando que PSReadLine almacena.
No hay ninguna opción de línea de comandos para pasarlo, a propósito.

El token se inyecta como cabecera del cliente HTTP, y ese punto es la razón de
que el diseño funcione: **no** entra en el paquete, ni en su forma canónica, ni
en la huella de idempotencia, ni en la `Idempotency-Key`, ni en SQLite, ni en la
outbox, ni en la traza, ni en los logs.

#### Preflight, y por qué existe

Antes de reclamar un solo evento, `enviar` y `sincronizar` preguntan por su
identidad en `GET /api/v1/autenticacion/yo`. El motivo es concreto: el
almacenamiento local incrementa el contador de intentos de un evento **antes**
de enviarlo, así que una ejecución con la credencial equivocada gastaría un
intento de cada paquete de la cola solo para descubrir un `401`, y repetirla
agotaría el presupuesto de paquetes que nunca estuvieron mal.

Solo un resultado autoriza empezar: `200`, cuerpo válido y rol PACIENTE.
Cualquier otro —token ausente, `401`, `403`, `5xx`, redirección, `2xx`
inesperado, cuerpo ilegible o fallo de transporte— detiene la ejecución **sin
abrir la outbox**, de modo que la cola conserva intacto su presupuesto.

Es una comprobación preventiva, no el control de autorización: el backend sigue
siendo la autoridad y `POST /api/v1/sesiones-monitoreo` exige PACIENTE por su
propia dependencia pase lo que pase en el preflight.

Si el token expira en la ventana entre el preflight y el envío, el evento queda
`FALLIDO` y **reintentable**, con su clave y su paquete intactos y sin
programación, y la corrida se detiene para no gastar el presupuesto del resto.
Corregida la credencial, una nueva invocación lo entrega con la misma clave, así
que no hay duplicados. Si ese envío excepcional coincidía con el último intento
del presupuesto, se aplica la regla de agotamiento de siempre: saltársela sería
subir el límite en silencio.

`sincronizar` devuelve el código de salida `4` cuando la credencial es el
problema.

### Ejecutar las pruebas de este bloque

Desde `backend/`, sin servidor PostgreSQL:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_passwords.py tests/test_tokens.py tests/test_autenticacion_api.py tests/test_rbac.py tests/test_auditoria.py tests/test_config_secretos.py tests/test_autenticacion_endurecimiento.py tests/test_edge_preflight.py tests/test_edge_cli_preflight.py
```

Con PostgreSQL 16:

```powershell
$env:SCRUM70_TEST_DATABASE_URL = "postgresql+psycopg://<usuario>:<clave>@127.0.0.1:<puerto>/<base>"
.\.venv\Scripts\python.exe -m pytest tests/test_autenticacion_postgresql.py -v
```

Esa suite crea y elimina sus propias bases temporales con prefijo
`scrum70_tmp_`, y solo elimina las que ella misma creó.

## Cuentas: aprovisionamiento y ciclo de vida

Desde SCRUM-97 un ADMIN puede crear la cuenta de acceso de un perfil clínico que
ya existe, y desactivarla o reactivarla. No hay registro público, autoservicio,
invitaciones, envío de credenciales, cambio o recuperación de contraseña, ni
creación de cuentas ADMIN: las dos cuentas ADMIN del dataset son cuentas semilla.
Todo es simulado.

### Perfil clínico, cuenta y embarazo no son lo mismo

| Entidad | Qué es | Ciclo de vida |
| --- | --- | --- |
| `Paciente` / `Medico` | Persona clínica simulada | Existe antes de la cuenta y la sobrevive |
| `Usuario` | Identidad digital que se autentica | Se activa y desactiva; nunca se recrea por embarazo |
| `UsuarioPaciente` / `UsuarioMedico` | El único vínculo entre esa cuenta y ese perfil | Uno por perfil y uno por cuenta |
| `Embarazo` | Un episodio gestacional | Varios por paciente |
| Asignación de dispositivo | Préstamo temporal del flujo edge | No define identidad |

Un segundo embarazo es un segundo `Embarazo` del mismo `id_paciente`: la
paciente conserva su perfil, su cuenta y su vínculo, y el historial anterior
queda intacto.

### Rutas

Las tres exigen un token de una cuenta **ADMIN activa**. La comprobación ocurre
antes de mirar si el perfil o la cuenta existen: un PACIENTE o un MEDICO reciben
el mismo `403` con un identificador existente que con uno inexistente, y queda
auditado como `ACCESO_DENEGADO_ROL`. No existe ningún `GET` de cuentas.

```text
POST  /api/v1/cuentas/pacientes/{id_paciente}
POST  /api/v1/cuentas/medicos/{id_medico}
PATCH /api/v1/cuentas/{id_usuario}/estado
```

Provisión (cuerpo idéntico para ambas rutas; `extra="forbid"`):

```json
{"email": "paciente31@example.com", "password": "<credencial simulada>"}
```

El **rol sale de la ruta**: `/pacientes/...` crea PACIENTE y `/medicos/...` crea
MEDICO. El cuerpo no admite `rol`, `activo`, `id_usuario`, `password_hash` ni
campos clínicos; cualquiera de ellos es `422`. La contraseña viaja como
`SecretStr`, se guarda solo como digest Argon2id con el mismo servicio de
SCRUM-70 y nunca se devuelve ni se registra; un `422` de estas rutas no incluye
el valor rechazado.

Respuestas `201`:

```json
{"id_usuario": 137, "id_paciente": 130, "rol": "PACIENTE", "activo": true}
{"id_usuario": 138, "id_medico": 105, "rol": "MEDICO", "activo": true}
```

Cambio de estado:

```json
{"activo": false}
```

Solo acepta booleanos JSON. Responde `{"id_usuario": 137, "rol": "PACIENTE", "activo": false}`.

| Situación | Código |
| --- | --- |
| Cuenta provisionada | `201` |
| Transición activa ↔ inactiva, o repetición del estado que ya tenía | `200` |
| Sin token, token inválido o ADMIN desactivado | `401` |
| Rol distinto de ADMIN | `403` |
| Perfil o cuenta inexistente | `404` |
| Correo ya en uso, perfil ya vinculado, cuenta ADMIN (incluida la propia), incoherencia rol/vínculo o carrera perdida | `409` |
| Identificador, cuerpo o campo extra inválido | `422` |
| Error interno (incluido un fallo de auditoría) | `500`, mensaje genérico |

Repetir una provisión ya hecha no es idempotente a propósito: es un `409`.

### Correo canónico

Hay **una sola regla**, aplicada en la provisión, en el login y en la búsqueda
interna de la cuenta: `email.strip().lower()`; vacío o con espacios internos se
rechaza, y el límite de 120 caracteres se mide después de quitar los espacios
exteriores. `Paciente31@Example.com` inicia sesión en la cuenta guardada como
`paciente31@example.com`, y no puede crearse una segunda cuenta con esa
variante. No se usa `EmailStr` ni ninguna dependencia nueva.

PostgreSQL lo garantiza por su cuenta: `ck_usuario_email_canonico` solo admite
valores canónicos y el `uq_usuario_email` existente, sobre valores canónicos, ya
es la unicidad sin distinción de mayúsculas. No se añadió `citext`, ninguna
extensión ni un índice funcional.

### Coherencia rol ↔ vínculo

| Rol | `usuario_paciente` | `usuario_medico` |
| --- | ---: | ---: |
| PACIENTE | exactamente 1 | 0 |
| MEDICO | 0 | exactamente 1 |
| ADMIN | 0 | 0 |

La regla abarca tres tablas, así que no es un CHECK: la función
`operacional.validar_rol_vinculo_usuario()` y tres *constraint triggers*
`DEFERRABLE INITIALLY DEFERRED` la evalúan **al confirmar** cada transacción.
Eso permite insertar la cuenta y después su vínculo dentro de la misma
transacción, y rechaza cualquier estado incoherente aunque se escriba
directamente en SQL. El rol se resuelve por `rol.nombre_rol`, nunca por un id
numérico.

### Atomicidad y auditoría

Una provisión es una sola transacción: bloqueo del perfil, comprobaciones, hash,
`Usuario`, vínculo, auditoría de éxito y un único `commit`. Si algo falla no
queda cuenta huérfana, ni vínculo parcial, ni auditoría de éxito falsa. El
servicio (`app/services/cuentas.py`) no confirma ni revierte: lo hace el router.

Las cuatro acciones del ciclo (`CUENTA_PACIENTE_PROVISIONADA`,
`CUENTA_MEDICO_PROVISIONADA`, `CUENTA_DESACTIVADA`, `CUENTA_REACTIVADA`) guardan
al ADMIN como actor y `usuario` + el id de la cuenta como objetivo. No guardan
correo, contraseña, hash, token, nombre, cédula ni teléfono. Repetir el estado que
la cuenta ya tenía no se audita como otra transición, y un rechazo de provisión
no escribe ninguna fila de éxito.

### Desactivar no borra, reactivar no recrea

Desactivar cambia `Usuario.activo`. La cuenta, su digest, su vínculo, el perfil,
los embarazos, las sesiones, las lecturas, los hechos analíticos y la auditoría
quedan exactamente como estaban. Reactivar vuelve a poner `activo = true` en la
**misma** fila: mismo `id_usuario`, mismo hash, mismo vínculo.

Como la API relee la cuenta en PostgreSQL en cada petición protegida, **un token
emitido antes de desactivar se rechaza con `401` en su siguiente uso**, sin lista
de revocación, `jti`, `token_version` ni *refresh token*.

> **Limitación aceptada.** Si la cuenta se reactiva antes de que ese token
> anterior expire (30 minutos por omisión), el token vuelve a servir, porque la
> autoridad es el estado actual de la cuenta y no hay revocación por token. Está
> documentada y fijada en una prueba; resolverla queda fuera del alcance.

### Probar con datos simulados

Las 30 pacientes y los 5 médicos del dataset ya tienen su cuenta, así que no hay
perfiles libres que provisionar. Las pruebas crean una paciente y un médico
ficticios adicionales **solo dentro de bases temporales** (`scrum97_tmp_*`) y
eliminan esas bases al terminar; el dataset canónico, su generador y sus conteos
no cambian.

Sin servidor PostgreSQL, desde `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cuentas.py
```

Con PostgreSQL 16:

```powershell
$env:SCRUM97_TEST_DATABASE_URL = "postgresql+psycopg://<usuario>:<clave>@127.0.0.1:<puerto>/<base>"
.\.venv\Scripts\python.exe -m pytest tests/test_cuentas_postgresql.py -v
```

La migración `54053d46abd6` valida antes de cambiar nada: si una cuenta existente
tuviera un correo vacío, con espacios internos o que colisione al normalizarse, o
un rol sin su vínculo exacto, se detiene con un conteo —sin mostrar correos— y no
corrige ni elimina nada.

### Lo que SCRUM-97 no hace

No decide **qué datos** puede ver cada cuenta: el aislamiento por paciente,
médico o clínica, RLS, la seudonimización y la protección analítica pertenecen a
SCRUM-98. SCRUM-97 deja listos los vínculos de identidad que ese trabajo
necesitará.

## Interfaz web de la gestante (SCRUM-72)

Un proceso aparte que corre **en el dispositivo de la paciente**: sirve la
interfaz, la autentica contra la API central y sigue en pie cuando no hay
conexión. No abre ninguna conexión a PostgreSQL y no escribe filas clínicas:
todo lo central lo pide por HTTP, y lo local lo delega en `app.edge`.

```powershell
python scripts/gestante_web.py --comprobar   # revisa configuración y sale
python scripts/gestante_web.py               # http://127.0.0.1:8100
```

Su configuración sale de variables `GESTANTE_*` (`GESTANTE_API_BASE_URL`,
`GESTANTE_PORT`, `GESTANTE_SQLITE_PATH`, `GESTANTE_MOVIMIENTOS_DIR`,
`GESTANTE_PROVISION_PATH`, `GESTANTE_VENTANA_SESION_HORAS`…), todas con valores
por omisión utilizables. **No existe ningún campo para una credencial**: la
paciente escribe la suya en la interfaz, viaja una vez hacia la API central y
no se guarda. El token que devuelve el servidor vive solo en memoria del
proceso; lo único que llega al navegador es un identificador opaco de sesión en
una cookie `HttpOnly`.

### Aprovisionar el dispositivo

Un paquete de monitoreo no lleva valores de negocio: lleva llaves subrogadas
—`id_dispositivo`, `id_tiempo_gest`, `id_semaforo`— que solo PostgreSQL conoce,
y que ninguna ruta le publica a una paciente. Como además la captura tiene que
funcionar **sin red**, esas referencias viajan con el dispositivo, escritas
cuando se le aprovisiona:

```powershell
python scripts/provisionar_demo.py provisionar   # prepara y escribe el archivo
python scripts/provisionar_demo.py verificar     # informa, sin escribir
```

Es el único componente de SCRUM-72 que habla con PostgreSQL, y usa
`ALEMBIC_DATABASE_URL` —la credencial de mantenimiento, como el cargador del
dataset—, nunca la de la API. Escribe `data/gestante/provision.json` con el
`id_usuario` al que sirve el dispositivo, su embarazo, su `id_dispositivo` y
los catálogos. **No guarda el correo ni ningún otro dato personal**: el correo
de la cuenta se imprime en pantalla para quien opera.

El comando existe por una razón concreta: los embarazos `ACTIVO` del dataset
canónico empezaron en 2025, así que hoy van por la semana 50 o más y el
contrato de ingesta solo admite 1–42. Ninguna sesión capturada hoy podría
ingresarse contra ellos. Así que agrega un episodio anclado al reloj real
—semana 28— para una cuenta PACIENTE sin embarazo en curso, con su dispositivo
y su seguimiento PRINCIPAL. Es idempotente y **no modifica ni borra una sola
fila del dataset canónico**; `tests/test_provisionar_demo_postgresql.py` lo
comprueba fila por fila.

### Registro simulado, operación sin conexión y sincronización

Desde «Inicio», el botón **Registrar sesión de movimientos** captura un
paquete en este dispositivo, siempre para el embarazo en curso. Funciona con la API central caída, que es el
requisito: la autorización de ese paso la da el aprovisionamiento —a qué cuenta
y a qué embarazo sirve—, que el navegador no puede alterar.

Los valores biométricos son constantes fijas del módulo
`app.gestante.simulacion`: no los escribe la paciente, no los genera el
navegador al azar y no son una medición. El semáforo **no se elige**: se deriva
con `app.etl.reglas.clasificar_lectura`, la misma autoridad SIM-1.0 con la que
el ETL vuelve a clasificar cada lectura —y que aborta la corrida completa ante
una discrepancia—. Si la semana queda fuera del catálogo, o se pide movimiento
fetal antes de la semana 20, el portal se niega con una frase precisa en vez de
guardar algo que el servidor rechazaría después.

Lo capturado queda `PENDIENTE` en un SQLite **propio de cada cuenta**
(`data/gestante/movimientos/cuenta-<id_usuario>.sqlite3`), sobrevive a un
reinicio del portal y no se mezcla con el de otra cuenta. La interfaz no
muestra contadores por estado: resume la cola de la cuenta en una frase
(«Registro guardado, pendiente de envío», «Registro enviado», «No se pudo
enviar; el registro continúa guardado…») derivada de los conteos reales. El
envío **no es automático**: el botón **Enviar ahora** —**Reintentar** si un
intento falló— ejecuta una sola ronda real contra
`POST /api/v1/sesiones-monitoreo` reutilizando `app.edge.ejecutar_pasada`, con
el token de la paciente que ya está en memoria —nunca `EDGE_API_TOKEN`, que es
del nodo edge y este proceso no lee—. El resultado se muestra tal cual: no se
finge un éxito. Un reenvío posterior no duplica nada: `ENVIADO` es terminal
para la elegibilidad del nodo, y la clave de idempotencia se conserva.

**El envío confirmado no es la actualización analítica.** Que el servidor
acepte el paquete lo deja en el esquema operacional; para verlo en el esquema
analítico y en `publicacion` hay que ejecutar después
`python scripts/etl_analitico.py ejecutar`.

### Inicio e Historial: dos contextos

- **Inicio** muestra solo el embarazo en curso (`actual` sin ambigüedad).
  - **Semana actual**: la de *hoy* en Panamá, calculada por el adaptador con
    `semana_gestacional` —la aritmética del servidor—, sin topes numéricos, y
    **solo mientras hoy no pase de la fecha probable de parto registrada en el
    episodio**. Pasada esa fecha el adaptador envía `semana_actual: null` y
    la fila pasa a «**Semana en el último registro** 39 (21 jun 2026)»: la
    semana de la lectura más reciente, con su fecha. No se muestra ningún
    aviso a la paciente ni se toca una fecha o un estado del episodio.
  - **Tus últimos registros**: una tarjeta por variable (FC, SpO₂,
    movimientos) con el **último valor no nulo de esa variable** en ese
    embarazo, por `(fecha_hora_captura, id_lectura)`. Cada tarjeta lleva su
    fecha y la semana de *esa* lectura. **Las tarjetas son neutrales**: la API
    solo clasifica cada lectura en su conjunto, así que ni las tarjetas ni
    `ultimos_registros`/`series` llevan semáforo. Pueden ser momentos
    distintos y la pantalla lo dice. El cero es un valor; una variable nunca
    registrada es «Sin registros»; una que no llegó es «No disponible».
    `id_lectura`/`id_sesion` viajan como atributos para trazabilidad y
    pruebas, no se muestran.
  - **Tus registros en el tiempo**: tres gráficas (FC materna y SpO₂ con
    puntos y línea; movimientos con una barra por registro) dibujadas con
    `frontend/gestante/graficas.js`, servido por el propio portal, sin
    dependencias ni CDN. Eje temporal proporcional, sin puntos inventados,
    sin interpolación ni agregación y sin bandas clínicas; período «Todo el
    embarazo» o «Últimos 30 días con registros» (contados desde el último
    registro, no desde hoy). Cada punto se consulta con ratón, toque o
    flechas del teclado, y hay una tabla alternativa por gráfica.
  - **Tu lectura más reciente**: la `ultima_lectura` de siempre —una sola
    lectura, con su fecha y lo que midió— y **su** semáforo, con el alcance
    escrito: «La clasificación corresponde a esta lectura completa, no a cada
    medición por separado». No hay semáforo conjunto de las tres tarjetas.
  - Con ambigüedad o sin embarazo en curso, Inicio no muestra lecturas y el
    registro queda deshabilitado.
- **Series**: `/adaptador/embarazos/{id}/monitoreo` entrega además `series`
  por variable (unidad y puntos en orden cronológico, solo lecturas
  existentes: sin interpolar, promediar ni agrupar por día). La tarjeta es por
  construcción el último punto de su serie (`app.gestante.clinico.serie` y
  `ultimo_registro` comparten la regla). La siguiente etapa las dibujará.
- **Mi historial** tiene su propia selección de embarazo y lista **todas** sus
  lecturas en una tabla (fecha, FC, SpO₂, movimientos, semana y semáforo),
  también cuando la última solo midió movimientos. Cambiar esa selección no
  repinta Inicio ni cambia el destino del registro, y una respuesta que llega
  tarde de una selección anterior se descarta.

Ninguna vista crea filas: abrir, navegar, refrescar o iniciar sesión solo
consulta. Las únicas filas nuevas son las que la paciente registra
explícitamente con el botón de movimientos. Las fechas se muestran en español
y en hora de Panamá, sea cual sea el navegador; las fechas de calendario
(`fecha_inicio`) no cambian de día por la zona horaria.

### Estado de conexión y sesión con el servidor

El indicador refleja **dos comprobaciones**, las de
`GET /adaptador/estado-conexion` (exige sesión local, no renueva nada y usa
`/yo`, que no escribe auditoría):

| Indicador | Qué se comprobó |
|---|---|
| Conectada al servidor | La API respondió y `/yo` aceptó el token. |
| Vuelve a iniciar sesión | La API respondió 401 (token vencido, inválido o cuenta desactivada, indistinguibles por diseño) o el portal se reinició y no tiene token. Se ofrece **Volver a iniciar sesión**. |
| Acceso no autorizado | 403: identidad válida sin permiso. No se ofrece reautenticar. |
| Sin conexión con el servidor | La API no respondió. |
| Error del servidor | Respuesta fuera de contrato (5xx…). |
| No se pudo comprobar la conexión | Ni el portal local contestó: típico al suspenderse el equipo. |

`POST /adaptador/reautenticar` vuelve a pedir la contraseña **de la misma
cuenta** sin cerrar la sesión local (otra cuenta: 403; credenciales que no
sirven: 400) y conserva los registros guardados. No hay refresh de token:
el contrato central no lo tiene, y no se guarda ninguna contraseña. El JWT
(30 min) y la ventana local (72 h) no cambian.

El refresco cada 20 s comprueba solo conexión y cola de envíos. Lo clínico
—que el servidor audita en cada lectura— se pide al entrar, al volver a
Inicio, al cambiar de embarazo, con «Actualizar información», al volver a la
pestaña con datos de más de un minuto (también `online`, `resume` y
`pageshow`), al recuperar la conexión o la autenticación y tras un envío
confirmado. Antes se recargaba cada 20 s: ~80 filas de auditoría por minuto
por pestaña abierta.

«Información consultada al servidor el …» (última actualización de los
datos) y «Último envío confirmado por el servidor: …» (`enviado_en` de la
outbox) son cosas distintas; un servidor disponible no mueve la segunda.

### Procedencia de los datos de la cuenta de demostración

La cuenta que aprovisiona `provisionar_demo.py` en el entorno local es la de
`id_usuario` 107 (paciente 100). Tiene dos episodios de origen distinto:

| Embarazo | Origen | Estado | Qué contiene |
|---|---|---|---|
| 130 | Agregado por `provisionar_demo.py` (inicio 2026-03-19) | `ACTIVO` | Solo las sesiones registradas desde el portal. Su 12 es la constante `MOV_SIMULADO` de `app.gestante.simulacion`: **no procede del dataset ni es una medición**. FC y SpO₂ son nulos porque una sesión de movimientos no los mide. |
| 100 | Dataset canónico (`data/generated`) | `FINALIZADO` | 41 lecturas en 25 sesiones. La última (id 679, 2025-10-01) solo midió movimientos; 20 lecturas de signos maternos tienen FC y SpO₂, por ejemplo la 110: 2025-09-08 17:13 UTC, FC 86, SpO₂ 97, semana 36, verde. |

Hay tres procedencias y no se mezclan: las **lecturas canónicas** del dataset
(embarazo 100 y el resto de `data/generated`), el **episodio de demostración**
que agrega el aprovisionamiento (embarazo 130, su dispositivo y su seguimiento)
y los **registros locales de movimiento** que la paciente crea desde el portal
(sesiones del embarazo 130, con la constante `MOV_SIMULADO`).

**Trazabilidad que ya existe.** Un registro local sincronizado se guarda con
`origen_dato = DISPOSITIVO`, igual que una sesión canónica, pero la base ya lo
distingue sin migraciones: tiene su fila en `operacional.idempotencia_solicitud`
(las sesiones del dataset, cargadas por el cargador, no tienen ninguna), una
entrada `SESION_MONITOREO_REGISTRADA` en `auditoria_log` con la cuenta que lo
envió, y el dispositivo que creó `provisionar_demo.py`, que no existe en el
dataset. `MOV_SIMULADO` se conserva a propósito como **nueva captura
simulada** de la demostración: sustituirla por una lectura histórica del
dataset haría pasar un dato antiguo por una medición nueva.

**Dataset canónico frente a base de demostración.** El dataset de la tesis es
`data/generated`: 30 embarazos, 732 sesiones y 1180 lecturas. El embarazo 130,
su dispositivo y las sesiones registradas desde el portal solo existen en las
bases de demostración (`scrum72_demo_gestante`, `scrum72_prueba_offline`) y no
deben usarse para estadísticas del dataset, ETL, Power BI ni resultados
cuantitativos. Esas cifras salen de una base cargada solo con
`data/generated`, sin ejecutar `provisionar_demo.py`. Detalle y verificación
en `docs/scrum72_etapa2_resumen.md`.

Los datos no se trasladan de un embarazo a otro. `tests/test_gestante_dataset.py`
regenera el dataset con su semilla y comprueba que la ruta de monitoreo
entrega esas lecturas con los valores de origen, y
`frontend/gestante/pruebas/app.comportamiento.test.js` comprueba cómo las
muestra la interfaz (`node --test`, sin dependencias; lo lanza también
`tests/test_gestante_frontend_comportamiento.py`).

### Revisar la interfaz

El entorno local de revisión usa el PostgreSQL de Docker Compose
(`tesis-bi-prenatal-db-1`, `127.0.0.1:5433`) y la base `scrum72_demo_gestante`.
La API y el portal se ejecutan con el entorno virtual del worktree; su `.env`
(no versionado) define `DATABASE_URL` con el rol restringido `fetalalert_api`,
`JWT_SECRET_KEY` y `GESTANTE_API_BASE_URL=http://127.0.0.1:8010`:

```powershell
cd backend; ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
.venv\Scripts\python.exe scripts\gestante_web.py   # desde la raíz; http://127.0.0.1:8100
```

En Windows, la URL de la base debe usar `127.0.0.1` y no `localhost`: Docker
publica el puerto solo en IPv4 y el intento previo por `::1` retrasa cada
conexión unos 8 segundos.

Dos cuentas (la contraseña de ambas es la `PASSWORD_SIMULADA` del generador):

- `paciente30@example.com` (embarazo canónico 129, `ACTIVO`): consulta y
  gráficas. Inicio muestra FC 83 y SpO₂ 96 del 29 may 2026 03:27 (lectura 549)
  y 7 movimientos del 21 jun 2026 09:56 (lectura 1259), en tarjetas neutrales;
  «Semana en el último registro 39 (21 jun 2026)»; gráficas con 30, 30 y 20
  registros; y el semáforo Ámbar de la
  lectura 1259 en su propio bloque.

- `paciente01@example.com`: historial longitudinal y registro de
  movimientos.

**Limitación del escenario de demostración.** El dataset simulado se generó
con fechas de 2025-2026 y sus episodios `ACTIVO` quedaron atrás en el
calendario real: el embarazo 129 tiene fecha probable de parto 1/7/2026 y sus
últimas lecturas son de junio. Es un desfase del escenario, no un hecho
clínico: por eso Inicio no presenta una «semana actual» para él ni lo
convierte en un aviso, y no se modifican fechas ni estados del dataset.

Con `paciente01` comprobar:

1. **Inicio** muestra el embarazo en curso (inicio 19 mar 2026, semana actual
   28). FC/SpO₂ dicen «Sin registros» —ese episodio nunca los midió— y
   movimientos muestra 12, que es `MOV_SIMULADO`, con su fecha.
2. **Ver embarazos anteriores en «Mi historial»** abre el embarazo 100: 41
   lecturas, con gráficas de 20, 20 y 21 registros por medición. En «Ver las
   41 lecturas en tabla», la fila del 8 sept 2025 a las 12:13 (hora de
   Panamá; 17:13 UTC) muestra 86 BPM, 97 %, semana 36 y semáforo verde.
3. Volver a **Inicio**: sigue mostrando el embarazo en curso.
4. **Registrar movimientos / Enviar ahora**: el resumen dice lo que la cola de
   la cuenta contiene; el envío es manual. Sin conexión, el registro se guarda
   en el dispositivo y el intento de envío lo conserva para «Reintentar».
5. **Cerrar sesión**, arriba a la derecha, pide confirmación; «Cancelar» (o
   Escape) no cierra nada y devuelve el foco al botón.

## Calidad del proyecto

- **Integración continua:** el workflow [`CI`](.github/workflows/ci.yml) se ejecuta en cada Pull Request hacia `main`, instala el backend con Python 3.12 y corre las pruebas automatizadas. Contra un servicio PostgreSQL 16 efímero se validan las migraciones, la carga idempotente del dataset, el endpoint de ingesta, la idempotencia de reenvíos —concurrencia real incluida—, el ciclo completo del nodo edge simulado hasta PostgreSQL, su sincronización resiliente con reintentos, reconciliación y trazabilidad, el esquema analítico con su ETL —carga inicial, idempotencia, incrementalidad, rollback, candado y zona horaria— y la autenticación JWT con hashes Argon2id, la matriz RBAC y la auditoría, y el aprovisionamiento y ciclo de vida de cuentas —migración, restricciones, trigger diferido y carreras reales incluidos—; las suites del ETL, de autenticación y de cuentas trabajan sobre bases temporales propias. También se validan el preflight y la autenticación del nodo edge, y un paso resuelve el modelo de Docker Compose para comprobar que la configuración JWT llega al servicio de la API. Un guardián final exige que las nueve suites de PostgreSQL se ejecuten: el job queda en rojo si alguna de sus pruebas se omite en lugar de ejecutarse. Las pruebas de tiempo no duermen: el reloj y la espera se inyectan.
- **Criterios de cierre de un ticket:** [Definition of Done](docs/definition_of_done.md).

## Estrategia de ramas

- `main` — rama de integración estable del proyecto.
- `feature/...` — una rama por ticket (por ejemplo,
  `feature/scrum-70-autenticacion-rbac-auditoria`), creada desde el `main`
  actualizado. Solo se apila sobre otra rama cuando existe una dependencia
  explícita y documentada.
- El trabajo vuelve a `main` mediante un Pull Request revisado por la otra autora
  y con el CI en verde.

## Autoras

- Tinuola Fagbemi
- Viviana Jaén
