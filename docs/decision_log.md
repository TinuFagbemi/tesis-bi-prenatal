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
- Se utilizan 5 médicos, con 6 embarazos asignados por médico.
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
- La carga definitiva en PostgreSQL queda pendiente hasta que estén disponibles las migraciones del modelo relacional.

### Revisión técnica final (cierre de SCRUM-54)

- Se confirmó que `backend/app/models/` no contiene modelos SQLAlchemy implementados (solo una `Base` declarativa vacía) y que `backend/alembic/versions/` está vacío en todas las ramas del repositorio. La comparación técnica del generador se realizó contra el diseño relacional conceptual aprobado de la tesis, no contra una implementación física existente.
- Se completaron en el generador los registros maestros/relacionales que ya estaban previstos en el diseño pero aún no se generaban: `TelefonoPaciente` (40 registros: 30 celulares principales, 10 correos alternos secundarios), `TelefonoMedico` (10 registros: 5 celulares principales, 5 teléfonos fijos secundarios), `Usuario` (37 cuentas: 30 PACIENTE, 5 MEDICO, 2 ADMIN), `UsuarioPaciente` (30 relaciones) y `UsuarioMedico` (5 relaciones).
- Las tres clínicas simuladas se reubicaron en zonas coherentes con el alcance rural de FetalAlert: CLI-001 (Chiriquí, Renacimiento, Plaza Caisán), CLI-002 (Veraguas, Santa Fe, Calovébora) y CLI-003 (Darién, Chepigana, Camogantí). Nombres y direcciones son completamente sintéticos.
- La información geográfica pertenece exclusivamente a `Clinica`. No se modela residencia de la paciente (sin provincia, distrito, corregimiento ni dirección residencial en `Paciente`).
- Los valores del enum `tipo_contacto` (`CELULAR`, `FIJO`, `CORREO_ALTERNO`) son provisionales, definidos a partir del prompt funcional, pendientes de validación contra el modelo físico cuando exista.
- No se generaron filas de `AuditoriaLog`; se producirán mediante acciones reales durante pruebas funcionales.
- Las cantidades biométricas aprobadas (732 sesiones, 1,180 lecturas, 560 HR/SpO₂, 620 movimientos, 826/295/59 semáforo) no se modificaron.
- Los valores físicos de enums, tipos de datos, longitudes, claves primarias/foráneas, restricciones `UNIQUE` y `nullable` deberán volver a validarse cuando estén disponibles los modelos SQLAlchemy y las migraciones Alembic.

### Alineación técnica con SQLAlchemy real (SCRUM-51 / rama feature/sprint-4-sqlalchemy-models)

Con los 22 modelos SQLAlchemy reales ya disponibles (aunque todavía no fusionados a esta rama), se corrigieron los hallazgos categoría A independientes de la cardinalidad Sesión↔Lectura:

- Las PK/FK del dataset pasaron de códigos de texto (`PAC-001`, `CLI-001`, ...) a enteros determinísticos que comienzan en 100 dentro de cada entidad, alineados con las PK `Integer` reales. Los códigos legibles se conservan solo donde el modelo real tiene una columna de negocio propia (`cedula`, `ruc`, `codigo_dispositivo`).
- `Clinica` genera `direccion_fisica` (antes `calle`); las tres ubicaciones rurales aprobadas no cambiaron.
- `TelefonoMedico` usa `TELEFONO_DOMICILIO` en vez de `FIJO` (valor eliminado del enum real `TipoContacto`).
- `Embarazo.estado_embarazo` usa exclusivamente `ACTIVO` / `FINALIZADO` / `SUSPENDIDO`, distribuidos determinísticamente 20/8/2 sobre los 30 embarazos, con `fecha_cierre` coherente (NULL solo en ACTIVO; en los demás, posterior o igual a la última captura biométrica real del embarazo, sin sesiones ni lecturas después del cierre).
- `Dispositivo.estado` refleja el estado del embarazo asociado: 20 `ASIGNADO` (embarazos ACTIVO) y 10 `DISPONIBLE` (embarazos FINALIZADO/SUSPENDIDO, asignación histórica cerrada con `fecha_fin = fecha_cierre`).
- `SesionMonitoreo.origen_dato` usa `DISPOSITIVO` (antes `API`, valor inexistente en el enum real).
- `SesionMonitoreo.tipo_sesion` (campo obligatorio real, antes no generado) se persiste en todas las sesiones: 112 `SIGNOS_MATERNOS`, 620 `MOVIMIENTOS_FETALES`.
- `SesionMonitoreo.fecha_inicio/fecha_fin`, `LecturaBiometrica.fecha_hora_captura/fecha_hora_sincronizacion` y `Dispositivo.fecha_registro` ahora son datetimes UTC offset-aware, alineados con `DateTime(timezone=True)`. Las fechas propias de `Embarazo` (Date en el modelo real) se mantienen sin componente de hora.
- La entidad `paciente_factor_riesgo` se renombró a `embarazo_factor_riesgo` (nombre físico real de la tabla); los campos y la distribución 14/9/7 no cambiaron.
- Ninguna de las cantidades biométricas aprobadas cambió: 732 sesiones, 1,180 lecturas, 560 HR/SpO₂, 620 movimientos, 826/295/59 semáforo, 37 usuarios, 40 TelefonoPaciente, 10 TelefonoMedico, 30 UsuarioPaciente, 5 UsuarioMedico.

**Pendiente (categoría B, no resuelta en esta fase):** el modelo SQLAlchemy real impone una relación 1:1 entre `SesionMonitoreo` y `LecturaBiometrica` (`UNIQUE(id_sesion)`), incompatible con las 5 lecturas por sesión HR/SpO₂ aprobadas. El dataset conserva la regla funcional aprobada; la corrección del lado del modelo la gestiona Tinuola por separado. No se modificó SQLAlchemy ni Alembic como parte de esta fase.
