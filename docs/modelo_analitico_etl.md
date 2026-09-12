# Modelo analítico y ETL de FetalAlert

Mapeo técnico del esquema `operacional` al esquema `analitico` (SCRUM-69):
qué contiene cada tabla, de dónde sale cada campo, qué regla lo transforma y
cómo se verifica. Todos los datos del proyecto son simulados y ficticios.

Fuentes de autoridad: el **modelo objetivo** es el Star Schema v6 del Capítulo
III con su Tabla de Umbrales de Tamizaje; los **datos disponibles** son los del
esquema operacional desplegado por Alembic; la **implementación** sigue las
convenciones del repositorio.

## Convenciones

- Esquema físico: `analitico`. Nombres físicos en snake_case; los nombres del
  diagrama (`Fact_LecturaBiometrica`, `Dim_Paciente`...) son los nombres
  lógicos.
- Revisión de Alembic: `60facdbacf51`, sobre `87d8ed46686b`.
- Clave de cada dimensión: el identificador operacional estable. Sin claves
  sustitutas, sin secuencias, sin SCD tipo 2. Dimensiones tipo 1.
- Hechos inmutables: se insertan una vez y nunca se actualizan.
- Ninguna llave foránea sale de `analitico` hacia `operacional`.
- Etiquetas de la columna «Tipo»: **D** diseño aprobado, **R** refinamiento
  aditivo, **C** corrección física, **E** derivación del ETL, **P** pendiente de
  regla.

## Fact_LecturaBiometrica — `analitico.fact_lectura_biometrica`

Grano: una fila por `operacional.lectura_biometrica.id_lectura`.

| Campo | Tipo físico | NULL | Origen | Transformación | Tipo |
| --- | --- | --- | --- | --- | --- |
| `id_lectura` (PK) | BIGINT | no | `lectura_biometrica.id_lectura` | copia | D |
| `id_sesion` | INTEGER, indexado, sin FK | no | `lectura_biometrica.id_sesion` | copia; dimensión degenerada | R |
| `id_paciente` (FK) | INTEGER | no | `embarazo.id_paciente` | lectura → sesión → embarazo | D |
| `id_medico` (FK) | INTEGER | no | `seguimiento_clinico.id_medico` y `medico_clinica` | seguimiento PRINCIPAL que cubre el día clínico (America/Panama), y ese médico debe tener exactamente una afiliación que cubra ese día y sea la clínica del embarazo. Cero o más de un seguimiento, cero o más de una afiliación, o una afiliación a otra clínica detienen la ejecución. `activo` no filtra: deciden las fechas | E |
| `id_clinica` (FK) | INTEGER | no | `embarazo.id_clinica` | clínica del embarazo de esa lectura | D |
| `id_tiempo_gestacional` (FK → `id_tiempo_gest`) | INTEGER | no | `lectura_biometrica.id_tiempo_gest` | copia de la semana ya validada por la ingesta; no se recalcula | D |
| `id_embarazo` (FK) | INTEGER | no | `sesion_monitoreo.id_embarazo` | lectura → sesión | D |
| `id_semaforo` (FK) | INTEGER | no | derivado | estado más severo de las métricas aplicables, resuelto contra `Dim_Semaforo` y comparado con `lectura_biometrica.id_semaforo` | E |
| `hr_valor` | NUMERIC(5,2) | sí | `lectura_biometrica.hr_valor` | copia exacta (el diagrama decía `INT`) | C |
| `spo2_valor` | NUMERIC(5,2) | sí | `lectura_biometrica.spo2_valor` | copia exacta (el diagrama decía `DECIMAL(4,1)`) | C |
| `mov_valor` | INTEGER | sí | `lectura_biometrica.mov_valor` | copia; 0 es un conteo real | D |
| `estado_hr` | VARCHAR(10) | sí | derivado | SIM-1.0 sobre `hr_valor` | E |
| `estado_spo2` | VARCHAR(10) | sí | derivado | SIM-1.0 sobre `spo2_valor` | E |
| `estado_mov` | VARCHAR(10) | sí | derivado | SIM-1.0 sobre `mov_valor` y el trimestre | E |
| `fecha_hora` | TIMESTAMPTZ | no | `lectura_biometrica.fecha_hora_captura` | copia del instante (el diagrama decía `TIMESTAMP`) | C |

Forma de una lectura, protegida por `ck_fact_lectura_biometrica_forma_valida`:

| Forma | `hr_valor` | `spo2_valor` | `mov_valor` | `estado_hr` | `estado_spo2` | `estado_mov` |
| --- | --- | --- | --- | --- | --- | --- |
| Signos maternos | valor | valor | NULL | código | código | NULL |
| Movimiento fetal | NULL | NULL | valor | NULL | NULL | código |

Un NULL nunca se convierte en 0, cadena vacía, falso ni «N/A». Los estados usan
exclusivamente `OK`, `WARNING` y `ERROR`.

No se usan como fecha clínica ni como marca de agua
`fecha_hora_sincronizacion` —el edge la envía NULL— ni
`idempotencia_solicitud.fecha_hora`. La traza del edge (outbox, intentos,
SQLite) no se copia al modelo estrella; la `Idempotency-Key` de una sesión se
obtiene, cuando existe, uniendo `id_sesion` con
`operacional.idempotencia_solicitud`.

## Dimensiones

### Dim_Clinica — `analitico.dim_clinica`

Una fila por clínica.

| Campo | Tipo físico | NULL | Origen | Tipo |
| --- | --- | --- | --- | --- |
| `id_clinica` (PK) | INTEGER | no | `clinica.id_clinica` | D |
| `nombre_clinica` | VARCHAR(150) | no | `clinica.nombre_clinica` | C (dibujo: 100) |
| `provincia` | VARCHAR(60) | no | `clinica.provincia` | C (dibujo: 50) |
| `distrito` | VARCHAR(60) | no | `clinica.distrito` | C (dibujo: 50) |

### Dim_Semaforo — `analitico.dim_semaforo`

Una fila por nivel. Copia del catálogo; el ETL exige exactamente OK, WARNING y
ERROR, prioridades 1, 2 y 3, y `version_referencia` igual a la versión de las
reglas.

| Campo | Tipo físico | NULL | Restricciones | Tipo |
| --- | --- | --- | --- | --- |
| `id_semaforo` (PK) | INTEGER | no | — | D |
| `codigo_nivel` | VARCHAR(7) | no | UNIQUE, CHECK de los tres códigos | D |
| `etiqueta_visual` | VARCHAR(60) | no | — | C (dibujo: 10) |
| `color_hex` | VARCHAR(7) | no | — | D |
| `prioridad` | INTEGER | no | UNIQUE, CHECK 1–3 | D |
| `mensaje_app` | VARCHAR(255) | no | — | C (dibujo: 120) |
| `version_referencia` | VARCHAR(30) | no | — | C (dibujo: 20) |

### Dim_TiempoGestacional — `analitico.dim_tiempo_gestacional`

Una fila por semana del catálogo operacional (40 en el dataset). Copia; no se
recalcula ninguna semana.

| Campo | Tipo físico | NULL | Restricciones | Tipo |
| --- | --- | --- | --- | --- |
| `id_tiempo_gest` (PK) | INTEGER | no | — | D |
| `semana_gestacion` | INTEGER | no | UNIQUE, CHECK 1–42 | D |
| `mes_gestacion` | INTEGER | no | CHECK 1–10 | D |
| `trimestre` | INTEGER | no | CHECK 1–3 | D |
| `descripcion` | TEXT | sí | — | C (dibujo: VARCHAR(100)) |

### Dim_Medico — `analitico.dim_medico`

Una fila por médico.

| Campo | Tipo físico | NULL | Origen y regla | Tipo |
| --- | --- | --- | --- | --- |
| `id_medico` (PK) | INTEGER | no | `medico.id_medico` | D |
| `id_clinica` (FK) | INTEGER | sí | clínica única de sus filas de `medico_clinica`; sin afiliación registrada queda NULL (ausencia de relación, no una clínica inventada) y dos clínicas distintas detienen la ejecución | E |
| `nombre_completo` | VARCHAR(243) | no | partes no NULL ni vacías del nombre, separadas por un espacio | C + E |
| `especialidad` | VARCHAR(100) | no | `especialidad.nombre_especialidad` | C (dibujo: 504) |
| `email_med` | VARCHAR(120) | no | `medico.email_med` | C (dibujo: 100) |
| `telefono_med` | VARCHAR(120) | sí | contacto `CELULAR` principal: uno se usa, ninguno es NULL, dos detienen la ejecución | C + E |

### Dim_Paciente — `analitico.dim_paciente`

Una fila por paciente.

| Campo | Tipo físico | NULL | Origen y regla | Tipo |
| --- | --- | --- | --- | --- |
| `id_paciente` (PK) | INTEGER | no | `paciente.id_paciente` | D |
| `id_clinica` (FK) | INTEGER | sí | clínica única de sus embarazos; sin embarazo registrado queda NULL (ausencia de relación, no una clínica inventada) y dos clínicas distintas detienen la ejecución | E |
| `cedula` | VARCHAR(20) | no | `paciente.cedula` | D |
| `nombre_completo` | VARCHAR(243) | no | igual que en Dim_Medico | C + E |
| `telefono_pac` | VARCHAR(120) | sí | igual que en Dim_Medico | C + E |
| `fecha_nac` | DATE | no | `paciente.fecha_nac` | D |

### Dim_Embarazo — `analitico.dim_embarazo`

Una fila por embarazo. Sin `id_clinica`: la clínica analítica está en el hecho.

| Campo | Tipo físico | NULL | Origen y regla | Tipo |
| --- | --- | --- | --- | --- |
| `id_embarazo` (PK) | INTEGER | no | `embarazo.id_embarazo` | D |
| `id_paciente` (FK) | INTEGER | no | `embarazo.id_paciente` | D |
| `numero_gestas`, `numero_partos` | INTEGER | no | copia | D |
| `estado_embarazo` | VARCHAR(20) | no | copia; CHECK de ACTIVO, FINALIZADO, SUSPENDIDO | D |
| `fecha_inicio`, `fecha_probable_parto` | DATE | no | copia | D |
| `fecha_cierre` | DATE | sí | copia | D |
| `duracion_est_semanas` | INTEGER | no | `(fecha_probable_parto − fecha_inicio) / 7`, exacto; un residuo detiene la ejecución | E |
| `clasificacion_embarazo` | VARCHAR(20) | sí | **sin regla aprobada**: queda en NULL y cada ejecución informa cuántas están pendientes | P |

### Dim_FactorRiesgo — `analitico.dim_factor_riesgo`

Una fila por factor. Copia.

| Campo | Tipo físico | NULL | Tipo |
| --- | --- | --- | --- |
| `id_factor_riesgo` (PK) | INTEGER | no | D |
| `clave_factor` | VARCHAR(50), UNIQUE | no | C (dibujo: 30) |
| `nombre_factor` | VARCHAR(150) | no | C (dibujo: 80) |
| `descripcion` | TEXT | sí | D |
| `activo` | BOOLEAN | no | D |

### Bridge_EmbarazoFactorRiesgo — `analitico.bridge_embarazo_factor_riesgo`

Una fila por par embarazo/factor de `embarazo_factor_riesgo`. Clave primaria
compuesta `(id_embarazo, id_factor_riesgo)`. Atributos: `fecha_diagnostico`,
`activo`, `observaciones`, exactamente los del diagrama; `fecha_fin` no se copia.
Un par que desaparece del origen es una inconsistencia: la conciliación falla y
no se borra nada.

## Reglas de clasificación (SIM-1.0)

Viven únicamente en `backend/app/etl/reglas.py`.

| Métrica | ERROR | WARNING | OK | Sin regla |
| --- | --- | --- | --- | --- |
| HR (lpm) | `< 55` o `> 110` | `55 ≤ HR < 60` o `100 ≤ HR ≤ 110` | `60 ≤ HR < 100` | — (intervalos exhaustivos) |
| SpO₂ (%) | `< 92` | `92 ≤ SpO₂ < 95` | `≥ 95` | — |
| Movimiento (conteo) | `mov < 5` | `5 ≤ mov < 10` | `mov ≥ 10` | antes de la semana 20: lectura inválida |

- El 100 lpm es WARNING: la Tabla 4 lo pone en dos rangos y gana la mayor
  severidad.
- `55 ≤ HR < 60` es WARNING: bradicardia que no alcanza el umbral de alerta. Es
  una regla de **tamizaje** poblacional, no un diagnóstico individual, y ningún
  valor de HR queda sin clasificar.
- 94.5 % es WARNING: la frontera es 95 y el valor es continuo.
- El umbral 10 de movimientos es el del dataset simulado (semanas 20 a 42,
  trimestres 2 y 3), no una norma clínica universal.
- Semáforo global: la mayor severidad entre los estados aplicables
  (OK < WARNING < ERROR).

## Orden de carga

Una ejecución es una transacción `REPEATABLE READ` cuya **primera sentencia**
toma el candado de transacción (`pg_try_advisory_xact_lock`). Si está ocupado,
la ejecución termina de inmediato con código 4 sin leer ni escribir. Lo libera
PostgreSQL al confirmar o al revertir; no hay `pg_advisory_unlock`. Después:

1. precondición: PostgreSQL en el head de Alembic;
2. `dim_clinica`, `dim_semaforo`, `dim_tiempo_gestacional`, `dim_factor_riesgo`;
3. `dim_medico`, `dim_paciente` (dependen de la clínica);
4. `dim_embarazo` (depende de la paciente);
5. `bridge_embarazo_factor_riesgo`;
6. lecturas nuevas por anti-join, clasificación, comparación del semáforo e
   inserción en `fact_lectura_biometrica`;
7. conciliación completa;
8. commit solo si todo es correcto; rollback ante cualquier diferencia.

## Conciliación

`backend/app/etl/conciliacion.py`, en SQL e independiente de la transformación.
Bloqueantes:

| Verificación | Qué compara |
| --- | --- |
| `lecturas_sin_hecho` / `hechos_sin_lectura_origen` | conjuntos de `id_lectura` en ambos sentidos |
| `sesiones_distintas` | `id_sesion` distintos de las lecturas de origen frente a los del hecho |
| `contenido_divergente` | sesión, semana, valores, instante, embarazo, paciente y clínica de cada hecho |
| `semaforo_distinto_del_origen` | semáforo analítico frente al operacional |
| `semaforo_distinto_de_la_mayor_severidad` | semáforo global frente a los estados por métrica |
| `forma_invalida` | combinaciones de valores y estados |
| `movimiento_antes_de_semana_20` | movimientos en semanas anteriores a la 20 |
| `fecha_fuera_del_embarazo` | día clínico antes del inicio o después del cierre |
| `medico_no_resoluble` / `medico_distinto_del_responsable` | seguimiento PRINCIPAL que cubre el día clínico |
| `afiliacion_no_aplicable` | que el médico del hecho tenga exactamente una afiliación que cubra ese día y sea la clínica del embarazo |
| `claves_duplicadas` | claves repetidas en el hecho y el bridge |
| `huerfanos_del_hecho` / `huerfanos_de_dimensiones` | llaves que no encuentran su dimensión |
| `dim_*_distinta_del_origen` | cada dimensión frente a lo que el origen determina, con las derivaciones repetidas en SQL |
| `catalogo_semaforo_incoherente` | prioridades del catálogo frente al orden de severidad |
| `bridge_distinto_del_origen` | relaciones embarazo/factor en ambos sentidos |

Informativas: `clasificacion_embarazo_pendiente`, `semana_recalculada_distinta`
(la API validó la semana con el offset recibido, que `TIMESTAMPTZ` no conserva) y
`sesiones_sin_lecturas` (no generan hechos, porque el grano es la lectura).

Línea base del dataset canónico: 1,180 hechos; 732 sesiones distintas en las
lecturas y en el hecho; 560 lecturas de signos maternos y 620 de movimiento;
semáforo 826 OK, 295 WARNING, 59 ERROR; `estado_hr` 472/76/12,
`estado_spo2` 478/64/18, `estado_mov` 436/155/29; 30 clasificaciones
pendientes.

## Relaciones

| Relación | Llave foránea en PostgreSQL | Relación del Star Schema | Recomendación para Power BI |
| --- | --- | --- | --- |
| Hecho → las seis dimensiones | sí | sí | activas, 1:N, filtro en un solo sentido |
| **Hecho → Dim_Clinica** | sí | sí | **ruta principal, y la única, para filtrar por clínica** |
| Bridge → Dim_Embarazo y Dim_FactorRiesgo | sí | sí | activas; ver la nota sobre factores de riesgo |
| Dim_Embarazo → Dim_Paciente | sí | sí | inactiva: con Hecho → Dim_Paciente formaría un ciclo |
| Dim_Paciente → Dim_Clinica, Dim_Medico → Dim_Clinica | sí | atributos de contexto | **no activar**: crearía una segunda ruta hacia la clínica |

**La clínica se filtra por el hecho.** `Fact_LecturaBiometrica.id_clinica` sale
del embarazo de cada lectura y es la ruta prevista por el Capítulo III para que
cada profesional vea su clínica. Las columnas `id_clinica` de `Dim_Paciente` y
`Dim_Medico` son contexto descriptivo, admiten NULL cuando todavía no hay
relación y **no deben activarse como rutas alternativas**: dos caminos hacia la
misma dimensión producen resultados que dependen de cuál tomó el motor.

**Factores de riesgo.** El puente permite responder «qué factores tiene este
embarazo». Filtrar *hechos* a partir de un factor necesita una configuración
controlada en Power BI —propagación decidida caso por caso sobre la relación
entre el puente y `Dim_Embarazo`— o una medida posterior. SCRUM-69 no
implementa DAX, medidas ni tableros: solo deja el modelo y esta advertencia.

El modelo semántico lo configura el trabajo de Power BI; este documento solo
describe el esperado. `clasificacion_embarazo` no debe usarse en filtros,
perfiles ni seguridad mientras no tenga regla.

## Cambios documentales pendientes

Para reflejar en el diagrama v6 y en el Capítulo III (no en este repositorio):

- `id_sesion` en el hecho;
- tipos y longitudes corregidos (NUMERIC(5,2), TIMESTAMPTZ, VARCHAR(100) para
  `especialidad`, longitudes operacionales) y la clave compuesta del bridge;
- las fronteras SIM-1.0: 100 lpm como WARNING, `55 ≤ HR < 60` como WARNING,
  SpO₂ entre 92 y 95, y el umbral de movimientos del dataset simulado;
- el día clínico en America/Panama;
- el comando `scripts/etl_analitico.py` como mecanismo del proceso batch;
- la redacción de la fase de limpieza: el documento habla de eliminar lecturas
  duplicadas o fuera de rango, mientras la implementación aprobada **valida y
  falla** —con rollback de toda la ejecución— para no perder ninguna lectura en
  silencio;
- la carga en la Figura 5: la figura presenta genéricamente el hecho, las
  dimensiones y el puente como «upsert», mientras la implementación aprobada
  usa *upsert* Tipo 1 en las dimensiones, *upsert* por su clave estable en el
  puente e `INSERT` inmutable en los hechos después del anti-join. Una colisión
  de la clave primaria del hecho es una anomalía que provoca rollback; no se
  oculta con `ON CONFLICT DO NOTHING`;
- `clasificacion_embarazo`: definir su regla de negocio;
- el diccionario de datos describe `hr_valor` como frecuencia cardíaca fetal;
  el modelo la define materna.
