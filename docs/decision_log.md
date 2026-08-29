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
