# Historial de mediciones de paciente01 para la demostración

`scripts/historial_demo.py` da al episodio de demostración de paciente01 un
historial de varios meses. Así la interfaz de la gestante y Power BI muestran
tendencias, con su embarazo anterior conservado. No cambia el diseño, las medidas
ni el modelo del BI.

Todos los datos son simulados y completamente ficticios.

## Quién es paciente01

`paciente01@example.com` es el **usuario 107**, vinculado a la **paciente 100**,
que en el informe aparece como «Ana Carrizo López — 4-900-0001». Tiene dos
embarazos:

| Embarazo | Origen | Estado | Fechas | Antes de este cambio |
|---|---|---|---|---|
| 100 | dataset canónico | FINALIZADO | 2025-01-06 → 2025-10-13 | 25 sesiones, 41 lecturas (se conservan) |
| 130 | `provisionar_demo.py` (`DEMO-SCRUM72-01`) | ACTIVO | inicio = día del aprovisionamiento − 27 semanas | sin mediciones |

`provisionar_demo.py` asigna el episodio 1 a la primera paciente sin embarazo
activo, en orden de identificador: la paciente 100. Este comando **reutiliza el
embarazo 130**. No crea embarazos y no toca las fechas.

## Qué carga

Solo semanas gestacionales **completas**, de la 1 a la anterior a la semana en
curso. La semana en curso queda libre para las capturas en vivo de los
escenarios A y C.

| Tipo | Semanas | Sesiones | Lecturas por sesión |
|---|---|---|---|
| Signos maternos | 1 a la anterior a la actual | 2 por semana (días 1 y 4, 08:30 y 19:00) | 5, cada una con FC y SpO₂, como el dataset |
| Movimientos fetales | 20 a la anterior a la actual | 2 por semana (días 2 y 5, 20:15) | 1, con el conteo consolidado de la sesión |

**Movimientos fetales.** El contrato guarda un conteo consolidado por sesión, en
una sola lectura: así lo hacen las 620 sesiones de movimiento del dataset y así
lo comprueba el servidor. Repartirlo en cinco lecturas inventaría mediciones que
no existen.

Por la misma razón no hay sesiones de movimiento antes de la semana 20
(`SEMANA_MINIMA_DE_MOVIMIENTO`, más restrictiva que el fin del primer trimestre),
y lo no medido no se representa con ceros.

**Reglas del sistema.**

- La semana de cada lectura se calcula con `semana_gestacional`, la misma
  función que usa el servidor.
- El semáforo se deriva con `clasificar_lectura` (SIM-1.0), la regla con la que
  el ETL vuelve a clasificar.
- Los valores son sintéticos y plausibles: la FC sube con el embarazo, la SpO₂
  se mantiene estable y hay alguna lectura en WARNING para ver el semáforo. Los
  umbrales y las reglas no cambian.

**Cómo entra.** El script inicia sesión como paciente01, con la contraseña
simulada del dataset, y envía cada sesión a `POST /api/v1/sesiones-monitoreo`
con el cliente del nodo edge. La API comprueba rol, propiedad del embarazo,
asignación del dispositivo, semana e idempotencia, y lo audita.

**Reproducible.** El plan depende de la fecha de inicio del embarazo, de la
semana en curso y de la semilla (`20261107` por omisión). Cada sesión lleva una
clave de idempotencia derivada de esos datos:

- repetir `cargar` no duplica nada: la API reconoce cada sesión ya confirmada;
- si en las semanas del plan hay sesiones que no son de este plan (otra semilla,
  otra herramienta), `cargar` se niega a mezclar historiales.

Dos equipos que aprovisionan en días distintos obtienen la **misma estructura
por semana gestacional y los mismos valores**, desplazados en el calendario
según su fecha de aprovisionamiento.

## Coherencia con las capturas en vivo

Los escenarios `A`, `B` y `C` (`scripts/escenarios_demo/`) declaran
`"lecturas": 5`. Así, una sesión de signos maternos capturada en vivo tiene la
misma forma que las del historial y del dataset: 5 lecturas de FC y SpO₂, una
por minuto. Las sesiones de movimiento siguen con un conteo consolidado.

- Los valores en vivo siguen siendo los fijos de `app.gestante.simulacion`:
  FC 80 y SpO₂ 97 con una variación fija (FC 79–83, SpO₂ 96–98) y
  `MOV_SIMULADO = 12`. Están en el rango de las últimas semanas del historial
  (FC media 78–84, SpO₂ ≈ 97,5, conteos 11–12 en las semanas 23 a 27).
- `lecturas` es opcional y vale 1 por omisión. `dispositivo_gestante.py` acepta
  `--lecturas N`. Sin ese campo, la captura es idéntica a la anterior.
- En un escenario, cada sesión ocupa tantos minutos como lecturas, así que las
  sesiones de una misma gestante no se solapan.
- La captura en vivo cae en la semana en curso, que el historial deja libre.

### Cambio en el código compartido de los escenarios

Este cambio toca código compartido de los escenarios de demostración (SCRUM-106).
Incorpora, sin modificarlo, el diseño de «lecturas por sesión» del commit
`10106c3` de la rama de SCRUM-109, limitado a su parte compartida. No necesita
nada más de esa rama: ni el escenario `S109.json` ni sus scripts.

| Archivo | Cambio |
|---|---|
| `backend/app/gestante/simulacion.py` | `construir_paquete_simulado(..., lecturas=1)`: de 1 a 10 lecturas conjuntas en signos maternos, una por minuto, terminando en `ahora`, con desvíos fijos (`DESVIOS_HR_SIMULADOS`, `DESVIOS_SPO2_SIMULADOS`). Cada lectura con su semana y su semáforo (`clasificar_lectura`). Movimientos con `lecturas != 1` se rechaza. |
| `backend/app/gestante/movimientos.py` | `registrar_sesion_simulada(..., lecturas=1)` lo pasa a la función anterior. |
| `scripts/dispositivo_gestante.py` | Opción `--lecturas N` (por omisión 1). |
| `scripts/escenario_demo.py` | Campo opcional `"lecturas"` por gestante (validado 1–10), espaciado de sesiones según las lecturas y `verificar` con las lecturas esperadas por sesión (los registros antiguos, sin ese dato, valen 1). |
| `scripts/escenarios_demo/A.json` | `"lecturas": 5` (paciente01, `DEMO-SCRUM72-01`), y dos sesiones: una de signos maternos y una de movimientos fetales (5 + 1 lecturas). |
| `scripts/escenarios_demo/B.json` | `"lecturas": 5` (`DEMO-GESTANTE-02`), por coherencia con el dataset aunque esta gestante no tiene historial nuevo. |
| `scripts/escenarios_demo/C.json` | `"lecturas": 5` en sus tres gestantes, por la misma razón. |
| `docs/entorno_demo_restaurable.md` | La frase «una lectura por sesión» y el ejemplo de escenario ahora describen `"lecturas"`; la tabla de escenarios indica cuántas lecturas añade cada uno (A 5 + 1, B 12, C 10 + 7 + 25). |
| `README.md` | Uso de `--lecturas 5` en el dispositivo, y fila del embarazo 130 con el historial y las capturas simuladas (antes decía «solo las sesiones registradas desde el portal»). |

Pruebas añadidas en `test_gestante_movimientos.py`, `test_gestante_dispositivo.py`
y `test_escenario_demo.py`. Esta última comprueba que A, B y C piden 5 lecturas.

## Cómo prepararlo

En la base de la demostración, después de cargar el dataset y aprovisionar, con
la API en marcha y `ALEMBIC_DATABASE_URL` y `GESTANTE_API_BASE_URL` definidas:

```powershell
python scripts/provisionar_demo.py provisionar --gestantes 4
python scripts/historial_demo.py previsualizar   # paciente, embarazo, fechas, sesiones por mes, totales
python scripts/historial_demo.py cargar          # envía las sesiones y verifica
python scripts/historial_demo.py verificar       # opcional: compara la base con el plan
python scripts/etl_analitico.py ejecutar
```

En el procedimiento de `docs/entorno_demo_restaurable.md`, este paso va entre
el aprovisionamiento y el ETL, **antes de tomar el respaldo**, para que el
baseline restaurable ya incluya el historial.

## Validación local (base exclusiva, 2026-11-07)

| | Antes | Después |
|---|---|---|
| Sesiones / lecturas (total) | 732 / 1180 | 802 / 1466 |
| Embarazo 130 | 0 / 0 | 70 sesiones (54 de signos maternos, 16 de movimiento) / 286 lecturas |
| Embarazo 100 (anterior) | 25 / 41 | 25 / 41 |
| Hechos analíticos | 1180 | 1466 |
| Registros de idempotencia | 0 | 70 |

- Una segunda ejecución de `cargar` dio 0 creadas y 70 ya existentes, con los
  conteos sin cambios.
- Las 1180 lecturas canónicas y las sesiones de otros embarazos quedaron
  idénticas (huellas antes y después).
- Una carga con otra semilla se rechazó antes de enviar nada.
- El ETL incremental añadió 286 hechos, con conciliación OK. Repetido, 0.
- Acceso por la API: paciente01 ve los embarazos 100 y 130; paciente02 y
  medico02 no ven el 130; medico01, con seguimiento vigente, sí.
- En la capa `publicacion`, como `fetalalert_powerbi`, el embarazo 130 aparece
  con 286 lecturas y solo autorizado para medico01. `v_resumen_administrativo`
  no cambia: suprime grupos de menos de 5 embarazos.

## Prueba integrada con el escenario A (2026-11-08)

En la misma base `historial_p01_20261107`, con el historial ya cargado.

1. Huella previa de solo lectura: md5 de todas las lecturas, sesiones y hechos
   existentes (cortes: lectura 1565, sesión 901).
2. Respaldo con `entorno_demo.py respaldar`, en una carpeta fuera del
   repositorio; `verificar`: IDÉNTICO.
3. Escenario A: `capturar` sin red, una sesión PENDIENTE con 5 lecturas;
   `sincronizar`: llegada única; ETL: 5 hechos nuevos, conciliación OK 27/27.

| | Antes | Después |
|---|---|---|
| Sesiones / lecturas / hechos | 802 / 1466 / 1466 | 803 / 1471 / 1471 |
| Embarazo 130 | 70 sesiones / 286 lecturas | 71 / 291 |
| Embarazo 100 (anterior) | 41 lecturas | 41 |
| Registros de idempotencia | 70 | 71 |

- Sesión nueva 902 (embarazo 130, dispositivo 130, semana 28): lecturas
  1566–1570, de 09:40:07 a 09:44:07 (Panamá), una por minuto, FC 80/82/79/81/83,
  SpO₂ 97/96/97/98/97, semáforo OK. Cada una tiene su hecho con el mismo semáforo.
  La última sesión del historial es del 2026-11-05 20:15: no hay solapamiento.
- Las huellas de las lecturas, sesiones y hechos anteriores son idénticas antes y
  después.
- Reintentos sin duplicados: `sincronizar` de nuevo no reenvía (la cola registra
  un solo intento, HTTP 201); el ETL repetido da 0 hechos nuevos; `historial_demo
  cargar` da 0 creadas y 70 ya existentes, y `verificar` coincide con el plan.

## Revisión en el portal y en Power BI

El portal de la gestante muestra las 291 lecturas del embarazo 130, con las 5 de
la sesión del 2026-11-08. La revisión en Power BI y el estado de los datos
incluidos en el PBIX están en `docs/powerbi_validacion.md`.

## Estado de la base de validación

La base conserva el estado posterior a la prueba del escenario A: 71 sesiones y
291 lecturas en el embarazo 130. El respaldo tomado antes del escenario
corresponde a 70 sesiones y 286 lecturas. Si se restaura, el portal y Power BI
mostrarán esas cifras hasta que se vuelva a ejecutar el escenario y el ETL.
