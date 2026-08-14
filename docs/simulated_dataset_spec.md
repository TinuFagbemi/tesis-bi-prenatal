# Especificación del dataset simulado de FetalAlert

## Propósito

Definir el dataset simulado reproducible que se utilizará para la validación funcional de FetalAlert.

El dataset corresponde a una muestra técnica no probabilística. Su objetivo es validar la persistencia en la base de datos, el proceso ETL, la clasificación de alertas, la sincronización offline, los controles de seguridad y el análisis longitudinal en Power BI.

Los datos son completamente ficticios y no representan prevalencia clínica ni epidemiológica.

## Composición del dataset

- 30 gestantes simuladas
- 30 embarazos simulados
- 30 dispositivos FetalAlert simulados
- 5 médicos
- 3 clínicas
- 2 administradores
- 1,180 registros biométricos en total
- Máximo establecido para esta versión: 1,200 registros biométricos

### Frecuencia cardíaca materna y SpO₂

- 3 sesiones base por gestante, distribuidas longitudinalmente durante el embarazo
- 90 sesiones base de HR/SpO₂ en total
- 5 lecturas biométricas procesadas por sesión
- 450 registros base de HR/SpO₂
- 22 sesiones voluntarias adicionales
- 110 registros voluntarios adicionales de HR/SpO₂
- 560 registros de HR/SpO₂ en total
- Las sesiones base se distribuirán aproximadamente en las semanas gestacionales 8, 24 y 36, garantizando presencia de datos en los tres trimestres
- Las fechas y horas exactas variarán entre gestantes para evitar patrones artificialmente idénticos
- Las sesiones voluntarias adicionales se distribuirán de forma irregular entre las gestantes para representar un uso más frecuente del dispositivo
- Durante una sesión, el MAX30102 realiza múltiples muestras internas mientras la gestante mantiene el dedo sobre el sensor
- Las muestras internas de alta frecuencia no se almacenarán individualmente en el dataset
- Para esta muestra técnica, cada sesión almacenará 5 lecturas biométricas procesadas representativas después de la estabilización de la señal
- En los registros de HR/SpO₂, `mov_valor = NULL`

### Movimientos fetales

- Se generarán únicamente a partir de la semana gestacional 20
- Se modelará una sesión base semanal desde la semana 20 hasta la semana 39
- 20 sesiones base por embarazo
- 600 sesiones base en total
- 20 sesiones voluntarias adicionales distribuidas irregularmente entre las gestantes
- 620 sesiones y registros de movimientos fetales en total
- Cada sesión podrá durar aproximadamente entre 60 y 120 minutos
- La sesión de movimientos fetales se iniciará mediante la opción correspondiente en la interfaz web local de FetalAlert
- Durante una sesión, el sensor puede detectar múltiples eventos de movimiento
- Los eventos individuales detectados durante la sesión no se almacenarán como registros biométricos independientes
- El dataset almacenará un conteo consolidado de movimientos correspondiente a la sesión completa
- En los registros de movimientos fetales, `hr_valor = NULL` y `spo2_valor = NULL`
- No se establece una frecuencia de tres sesiones diarias ni un máximo obligatorio de uso

## Relación entre sesiones y lecturas biométricas

El dataset distinguirá entre una sesión de monitoreo y las lecturas biométricas obtenidas durante dicha sesión.

### Sesiones de HR/SpO₂

Una sesión de HR/SpO₂ representa el periodo durante el cual la gestante mantiene el dedo sobre el sensor MAX30102.

Durante este periodo, el dispositivo realiza múltiples muestras internas que son procesadas y estabilizadas localmente. Estas muestras internas no se almacenan individualmente en la base de datos.

Para la muestra técnica:

- 112 sesiones de HR/SpO₂ generarán 560 lecturas biométricas procesadas
- Cada sesión de HR/SpO₂ generará 5 lecturas procesadas representativas

### Sesiones de movimientos fetales

Una sesión de movimientos fetales representa una ventana prolongada de monitoreo activada desde la interfaz web local.

Durante esta ventana pueden detectarse múltiples eventos de movimiento. El resultado almacenado será un único conteo consolidado correspondiente a la sesión completa.

Para la muestra técnica:

- 620 sesiones de movimientos fetales generarán 620 registros consolidados

### Totales

- 112 sesiones de HR/SpO₂
- 620 sesiones de movimientos fetales
- 732 sesiones de monitoreo en total
- 560 registros de HR/SpO₂
- 620 registros de movimientos fetales
- 1,180 registros biométricos en total

## Modelo longitudinal

Las mismas 30 gestantes serán seguidas a lo largo de sus embarazos simulados.

Cada embarazo deberá contener registros distribuidos desde etapas tempranas de la gestación hasta el tercer trimestre.

Las fechas calendario de los embarazos variarán entre las pacientes, pero la semana gestacional permitirá comparar longitudinalmente su evolución.

El hecho de que las sesiones base de HR/SpO₂ se distribuyan en momentos determinados no implica que el dispositivo solo pueda utilizarse en esas fechas. Estas sesiones representan la cobertura mínima definida para la muestra técnica, mientras que las sesiones voluntarias adicionales permiten simular un uso más frecuente.

## Distribución por clínica y médico

- 10 gestantes por clínica
- 6 embarazos por médico
- Un dispositivo FetalAlert asignado a cada embarazo simulado durante el periodo de seguimiento

## Factores de riesgo

- 14 embarazos sin factores de riesgo registrados
- 9 embarazos con un factor de riesgo
- 7 embarazos con dos factores de riesgo

Catálogo mínimo para las pruebas:

- Hipertensión
- Diabetes gestacional
- Obesidad

Los factores de riesgo se utilizarán para validar relaciones, filtros y dashboards.

No deberán determinar directamente las alertas biométricas ni interpretarse como causa de los valores simulados.

## Registros maestros y relacionales adicionales

Estos registros son maestros/relacionales y no forman parte del conteo de 1,180 registros biométricos ni de los 732 registros de sesiones de monitoreo.

### Usuarios y acceso

- 37 usuarios simulados en total
- 30 usuarios con rol PACIENTE
- 5 usuarios con rol MEDICO
- 2 usuarios con rol ADMIN
- 30 relaciones UsuarioPaciente (1:1 con cada gestante)
- 5 relaciones UsuarioMedico (1:1 con cada médico)

Los correos y los hashes de contraseña utilizados son completamente sintéticos y no representan credenciales válidas de producción.

### Información de contacto

- 40 contactos de pacientes:
  - 30 celulares principales
  - 10 correos alternos secundarios
- 10 contactos de médicos:
  - 5 celulares principales
  - 5 teléfonos fijos secundarios

`tipo_contacto` identifica la modalidad del contacto. `valor_contacto` contiene el número o correo correspondiente. `principal` identifica cuál es el medio principal de contacto de la persona; cada paciente y cada médico tiene exactamente un contacto marcado como principal.

### Clínicas

Las tres clínicas simuladas se ubican en zonas rurales de Panamá, coherentes con el alcance de FetalAlert:

- CLI-001: Chiriquí → Renacimiento → Plaza Caisán
- CLI-002: Veraguas → Santa Fe → Calovébora
- CLI-003: Darién → Chepigana → Camogantí

Los nombres de clínica y las direcciones específicas son completamente sintéticos y se utilizan únicamente para validación técnica.

### AuditoriaLog

No se generan filas ficticias en esta muestra. Los registros de auditoría se producirán posteriormente mediante acciones reales durante las pruebas funcionales del sistema.

### Limitación pendiente

Los valores permitidos de `tipo_contacto` (`CELULAR`, `FIJO`, `CORREO_ALTERNO`) y los nombres físicos de estas entidades son provisionales: se definieron a partir del diseño conceptual de la tesis porque, a la fecha de esta revisión (SCRUM-54), aún no existen modelos SQLAlchemy ni migraciones de Alembic implementados en el repositorio. Deberán revalidarse contra el modelo relacional físico en cuanto esté disponible.

## Distribución de alertas

- 70 % OK = 826 registros
- 25 % WARNING = 295 registros
- 5 % ERROR = 59 registros
- Total = 1,180 registros

Esta distribución se utiliza únicamente para fines de validación técnica y no representa prevalencia clínica o epidemiológica.

## Reglas de integridad de los datos

- Cada registro deberá tener un identificador único
- No podrá existir ningún registro de movimientos fetales antes de la semana gestacional 20
- `NULL` significa que la variable no fue medida o no aplica al evento
- El valor cero no deberá utilizarse como sustituto de `NULL`
- Las fechas del embarazo deberán ser cronológicamente coherentes
- Las marcas de tiempo de captura deberán encontrarse dentro del periodo correspondiente al embarazo
- Las sesiones de movimientos fetales deberán tener una duración máxima aproximada de 120 minutos
- Las lecturas de HR/SpO₂ pertenecientes a una misma sesión deberán conservar una secuencia temporal coherente
- Algunos registros deberán simular sincronización diferida para validar el funcionamiento offline-first
- La fecha y hora de sincronización no podrá ser anterior a la fecha y hora de captura
- El historial clínico deberá ordenarse según la fecha y hora de captura
- Los códigos de los dispositivos deberán ser únicos
- Cada embarazo deberá mantener el dispositivo asignado durante la simulación
- El generador deberá utilizar una semilla aleatoria fija
- La ejecución del generador utilizando la misma semilla deberá producir el mismo dataset

## Restricción actual de implementación

El dataset generado se exportará inicialmente en formato JSON y/o CSV.

La carga en PostgreSQL permanecerá pendiente hasta que el modelo relacional y las migraciones de Alembic estén disponibles.

Una vez desplegado el modelo relacional, el dataset deberá validarse contra los nombres de columnas, tipos de datos, claves primarias, claves foráneas, restricciones `NULL`, restricciones de unicidad y demás reglas implementadas físicamente en PostgreSQL.

**Estado a la fecha de SCRUM-54:** `backend/app/models/` no contiene todavía ninguna clase SQLAlchemy (solo existe una `Base` declarativa vacía) y `backend/alembic/versions/` está vacío en todas las ramas del repositorio. Por lo tanto, la revisión técnica de SCRUM-54 comparó el generador únicamente contra el diseño relacional conceptual aprobado de la tesis, no contra una implementación física. Esta validación cruzada deberá repetirse cuando el equipo de fundamentos de API implemente los modelos y las migraciones.