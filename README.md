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

El repositorio se encuentra en una etapa temprana. Lo que ya existe y funciona es el esquema operacional en PostgreSQL con sus migraciones, el generador del dataset simulado, su carga idempotente y la primera entrada de la API: el endpoint que recibe una sesión de monitoreo con sus lecturas biométricas. **Aún no existen la simulación del nodo edge, el ETL, el modelo dimensional, la autenticación y autorización, ni los dashboards**, y el endpoint disponible todavía no tiene control de acceso. El desarrollo activo se encuentra actualmente en el Sprint 4, y todo el trabajo se desarrolla y prueba en un entorno controlado/local, no en comunidades rurales reales.

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
  "fecha_inicio": "2026-03-01T09:00:00-05:00",
  "fecha_fin": "2026-03-01T09:30:00-05:00",
  "estado_sesion": "COMPLETADA",
  "lecturas": [
    {
      "id_tiempo_gest": 119,
      "id_semaforo": 100,
      "fecha_hora_captura": "2026-03-01T09:05:00-05:00",
      "fecha_hora_sincronizacion": "2026-03-01T09:40:00-05:00",
      "hr_valor": 88.5,
      "spo2_valor": 97,
      "mov_valor": null
    }
  ]
}
```

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
- **No se aceptan campos desconocidos.** Un nombre mal escrito es un error, no
  un campo omitido.

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

### 5. Códigos de respuesta

| Código | Cuándo |
| --- | --- |
| `201` | La sesión y todas sus lecturas quedaron registradas. |
| `404` | Alguna referencia del paquete no existe todavía. |
| `409` | Conflicto de integridad: la fila ya existe. **No se sobrescribe nada.** |
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

### 7. Reenvíos: pendiente de SCRUM-63

**La idempotencia de reenvíos no está implementada.** Este endpoint **no** trata
un paquete repetido como un éxito: si un reenvío provoca un conflicto de
integridad, responde `409` y no sobrescribe nada. El reconocimiento de reenvíos
—`Idempotency-Key`, identificador externo estable y reutilización de la
respuesta— corresponde a SCRUM-63.

## Calidad del proyecto

- **Integración continua:** el workflow [`CI`](.github/workflows/ci.yml) se ejecuta en cada Pull Request hacia `main`, instala el backend con Python 3.12 y corre las pruebas automatizadas. Contra un servicio PostgreSQL 16 efímero se validan las migraciones, la carga idempotente del dataset y el endpoint de ingesta; el job queda en rojo si alguna de esas pruebas se omite en lugar de ejecutarse.
- **Criterios de cierre de un ticket:** [Definition of Done](docs/definition_of_done.md).

## Estrategia de ramas

- `main` — versión estable del proyecto.
- `develop` — rama de integración de cambios.
- `feature/...` — una rama por módulo o sprint, creada desde `develop` (por ejemplo, `feature/sprint-4-project-foundation`).

## Autoras

- Tinuola Fagbemi
- Viviana Jaén
