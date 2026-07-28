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

El repositorio se encuentra en una etapa temprana. Actualmente contiene principalmente la estructura inicial de un sprint previo (Sprint 0) y archivos de tipo placeholder (por ejemplo, un script de generación de datos sin lógica real). **Aún no existen implementaciones funcionales de backend, simulación de nodo edge, base de datos definitiva, ETL, seguridad ni dashboards.** El desarrollo activo se encuentra actualmente en el Sprint 4, y todo el trabajo se desarrolla y prueba en un entorno controlado/local, no en comunidades rurales reales.

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

## Estrategia de ramas

- `main` — versión estable del proyecto.
- `develop` — rama de integración de cambios.
- `feature/...` — una rama por módulo o sprint, creada desde `develop` (por ejemplo, `feature/sprint-4-project-foundation`).

## Autoras

- Tinuola Fagbemi
- Viviana Jaén
