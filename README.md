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
5. **ETL y analítica** — procesos ETL en Python/pandas que alimentan dashboards en **Microsoft Power BI**, actualizados de forma periódica/asíncrona tras cada sincronización — no en tiempo real. Power BI está destinado exclusivamente a personal médico o autorizado.

## Seguimiento dual: gestante y personal médico

El objetivo general contempla un seguimiento **dual** entre la gestante y el personal médico, con niveles de acceso distintos:

- **Personal médico/autorizado:** accede a la analítica y los dashboards en Power BI, alimentados por el ETL tras cada sincronización.
- **Gestante:** el sistema contempla algún mecanismo de acceso limitado a su propia información, separado de Power BI. **La forma concreta de implementación de este acceso (aplicación, portal, u otro canal) todavía no está definida** y se documentará en esta sección una vez confirmada. No debe asumirse que ya existe una interfaz para la gestante.

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
- **Protección de datos:** anonimización (sobre datos ficticios), auditoría (`AuditoriaLog`) y controles alineados con la Ley 81 de 2019 de Panamá
- **ETL:** Python, pandas, SQLAlchemy
- **Analítica y dashboards:** Microsoft Power BI Desktop
- **Pruebas automatizadas:** pytest
- **Documentación/pruebas manuales de API:** Swagger/OpenAPI (integrado en FastAPI)
- **Entorno reproducible:** Docker, Docker Compose
- **Control de versiones:** Git y GitHub

## Modelos de datos

- **Modelo operacional:** modelo relacional normalizado en PostgreSQL para el funcionamiento del sistema (pacientes, embarazos, sesiones de monitoreo, lecturas, usuarios, roles, auditoría, etc.), con datos exclusivamente ficticios y sintéticos.
- **Modelo dimensional (Star Schema vigente):**
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

*(Todo lo siguiente está previsto para el diseño; nada de esto está implementado todavía.)*

- Autenticación mediante JWT y autorización basada en roles (RBAC).
- **Hash de contraseñas** con Argon2id (distinto del cifrado de datos: el hash protege credenciales de forma irreversible; no se usa para proteger datos en tránsito o en reposo).
- **HTTPS/TLS** para proteger los datos en tránsito entre los componentes del sistema.
- `AuditoriaLog` como mecanismo previsto para registrar acciones relevantes del sistema (auditoría).
- Anonimización aplicada sobre la información ficticia de pacientes utilizada en pruebas, para validar que el mecanismo funciona correctamente.
- Controles alineados con los requisitos de la Ley 81 de 2019 de Panamá (Protección de Datos Personales).

## Estructura prevista del repositorio

```
tesis-bi-prenatal/
├── data/        # Datos simulados y fixtures sintéticos de prueba
├── docs/        # Documentación de decisiones, arquitectura y modelos de datos
├── scripts/     # Scripts de generación de datos simulados y utilidades
├── README.md
└── .gitignore
```

*(Esta estructura se ampliará conforme avancen los sprints: backend, simulación del nodo edge, ETL y pruebas tendrán sus propios directorios.)*

## Estado actual del proyecto

El repositorio se encuentra en una etapa temprana. Lo que ya existe y funciona es el esquema operacional en PostgreSQL con sus migraciones, el generador del dataset simulado, su carga idempotente, el endpoint que recibe una sesión de monitoreo con sus lecturas biométricas —con su contrato de idempotencia— y el nodo edge simulado, que captura paquetes sin conexión y los entrega después sin duplicarlos, con reintentos de espera incremental, agotamiento controlado y trazabilidad de extremo a extremo. **Aún no existen un servicio permanente o demonio que dispare esa sincronización por sí solo, la detección automática de conectividad, el ETL, el modelo dimensional, la autenticación y autorización, ni los dashboards**, y el endpoint disponible todavía no tiene control de acceso. El desarrollo activo se encuentra actualmente en el Sprint 4, y todo el trabajo se desarrolla y prueba en un entorno controlado/local, no en comunidades rurales reales.

## Roadmap general

- Definición y consolidación de la arquitectura y modelos de datos (operacional y dimensional).
- Implementación de la generación de datos simulados y del nodo edge simulado (Python + SQLite).
- Desarrollo del backend REST (FastAPI) con autenticación JWT/RBAC.
- Implementación de la persistencia en PostgreSQL (esquema operacional y dimensional).
- Implementación de la sincronización asíncrona, reintentos e idempotencia.
- Definición e implementación del mecanismo de acceso limitado para la gestante.
- Desarrollo del proceso ETL y de los dashboards en Power BI.
- Incorporación de auditoría (`AuditoriaLog`), anonimización y controles de cumplimiento con la Ley 81 de 2019.
- Pruebas automatizadas y documentación final de la tesis.

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
reproducibles ejecutando de nuevo el generador con su semilla fija.

### 4. Cargar el dataset

Seguimos en la raíz del repositorio:

```powershell
python scripts/load_mock_data.py
```

Por omisión procesa `data/generated/dataset_fetalalert.json`. Acepta una ruta
alternativa como único argumento; nunca recibe la URL de la base ni ninguna
credencial por línea de comandos.

La conexión sale de `DATABASE_URL`. El archivo que la aplicación lee es tu
`.env` local, que no se versiona y se crea copiando `.env.example`; **ambos
traen `db:5432`**, un nombre que solo existe dentro de la red de Docker. Si
ejecutas el comando directamente desde Windows, define `DATABASE_URL` apuntando
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

> **Este endpoint no es apto para producción.** No tiene autenticación ni
> control de acceso: JWT y RBAC corresponden a un ticket posterior. Se ejecuta
> únicamente en el entorno controlado de desarrollo y pruebas, nunca expuesto a
> una red pública.

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
contenedor ya ejecuta ese mismo `uvicorn`.

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
| `201` | La sesión y todas sus lecturas quedaron registradas, o ya lo estaban por una solicitud anterior con la misma clave. |
| `400` | Falta la cabecera `Idempotency-Key` o su formato no es válido. No se registró nada. |
| `404` | Alguna referencia del paquete no existe todavía. |
| `409` | Conflicto. O la clave ya identifica un paquete con contenido distinto, o una referencia dejó de existir mientras se procesaba el paquete. La solicitud en conflicto no agrega ni modifica datos, y la operación que ya estuviera almacenada bajo esa clave permanece intacta. |
| `422` | El cuerpo no cumple el contrato, o rompe una regla del dominio o una restricción de validez de la base. |
| `500` | Error interno. La transacción completa fue revertida. |

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
Este endpoint no tiene autenticación y no es apto para producción.

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

Las cuatro últimas son solo valores por omisión. El límite que gobierna un evento
concreto es el que **adoptó** al reclamar su primer intento, guardado en
`max_intentos_aplicado`: cambiar el entorno alcanza a los eventos que todavía no
han empezado a sincronizarse, y no reescribe el contrato de los que ya están en
curso.

`data/edge/` está en `.gitignore`: la base del nodo es un artefacto local y
**nunca** se versiona. No hay ninguna variable para credenciales, porque el nodo
no las necesita: escribe en un archivo local y habla HTTP con un endpoint que
todavía no tiene autenticación.

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
Tampoco hay autenticación: el endpoint al que entrega todavía no la tiene.

## Calidad del proyecto

- **Integración continua:** el workflow [`CI`](.github/workflows/ci.yml) se ejecuta en cada Pull Request hacia `main`, instala el backend con Python 3.12 y corre las pruebas automatizadas. Contra un servicio PostgreSQL 16 efímero se validan las migraciones, la carga idempotente del dataset, el endpoint de ingesta, la idempotencia de reenvíos —concurrencia real incluida—, el ciclo completo del nodo edge simulado hasta PostgreSQL y su sincronización resiliente con reintentos, reconciliación y trazabilidad; el job queda en rojo si alguna de esas pruebas se omite en lugar de ejecutarse. Las pruebas de tiempo no duermen: el reloj y la espera se inyectan.
- **Criterios de cierre de un ticket:** [Definition of Done](docs/definition_of_done.md).

## Estrategia de ramas

- `main` — versión estable del proyecto.
- `develop` — rama de integración de cambios.
- `feature/...` — una rama por módulo o sprint, creada desde `develop` (por ejemplo, `feature/sprint-4-project-foundation`).

## Autoras

- Tinuola Fagbemi
- Viviana Jaén
