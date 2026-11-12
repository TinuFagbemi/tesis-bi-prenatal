# Informe Power BI de validación

`powerbi/FetalAlert_validacion_final.pbix` es el informe de Power BI Desktop con el
que se validan las vistas de `publicacion`, el rol `Medico` y el resumen
administrativo sobre el entorno de demostración, incluido el historial de
paciente01 (`docs/historial_demo_paciente01.md`).

Todos los datos son simulados y completamente ficticios.

## El archivo

| | |
|---|---|
| Ruta | `powerbi/FetalAlert_validacion_final.pbix` |
| SHA-256 | `d80f1ff6f271a02226d9f3aafd334a9f8b26ef13f96bb0d17793e5162a0264b0` |
| Power BI Desktop | Microsoft Store, versión 2.159.1231.0. No se probó con otras versiones. |
| Origen de datos guardado | PostgreSQL `127.0.0.1:5433`, base `historial_p01_20261107` |
| Datos importados | 2026-11-08 21:11 (hora de Panamá) |

Para comprobar que la copia es la validada:

```powershell
Get-FileHash powerbi\FetalAlert_validacion_final.pbix -Algorithm SHA256
```

El servidor y la base son los del equipo donde se validó. Se buscaron en el
archivo, comprimido y descomprimido, la contraseña de `fetalalert_powerbi` y las
del `.env` del equipo de validación, y no aparecieron. Power BI Desktop guarda
las credenciales de los orígenes de datos en el perfil de cada usuario.

## Qué contiene

**Consultas y tablas.** Diez consultas de Power Query, una por tabla importada,
todas como `fetalalert_powerbi` sobre el esquema `publicacion`:

| Tabla del modelo | Vista de origen |
|---|---|
| `publicacion v_embarazo` | `publicacion.v_embarazo_medico` (la consulta conserva su nombre para no romper visuales ni medidas) |
| `publicacion v_embarazo_factor_riesgo` | `publicacion.v_embarazo_factor_riesgo` |
| `publicacion v_entitlement_medico` | `publicacion.v_entitlement_medico` (oculta) |
| `publicacion v_entitlement_paciente_medico` | `publicacion.v_entitlement_paciente_medico` |
| `publicacion v_factor_riesgo` | `publicacion.v_factor_riesgo` |
| `publicacion v_lectura` | `publicacion.v_lectura` |
| `publicacion v_paciente_medico` | `publicacion.v_paciente_medico` |
| `publicacion v_resumen_administrativo` | `publicacion.v_resumen_administrativo` |
| `publicacion v_semaforo` | `publicacion.v_semaforo` |
| `publicacion v_tiempo_gestacional` | `publicacion.v_tiempo_gestacional` |

`publicacion` tiene once vistas. La que no se importa es `v_embarazo`, porque su
lugar lo ocupa `v_embarazo_medico`. Power BI añade además sus propias tablas
automáticas de fecha (una plantilla y nueve locales), que no son consultas.

**Relaciones.** Ocho entre vistas:

| Desde | Hacia | Filtro cruzado | Filtro de seguridad |
|---|---|---|---|
| `v_entitlement_medico[seudonimo_embarazo]` | `v_embarazo[seudonimo_embarazo]` | ambas direcciones | ambas direcciones |
| `v_entitlement_paciente_medico[seudonimo_paciente]` | `v_paciente_medico[seudonimo_paciente]` | ambas direcciones | ambas direcciones |
| `v_embarazo_factor_riesgo[seudonimo_embarazo]` | `v_embarazo[seudonimo_embarazo]` | ambas direcciones | simple |
| `v_lectura[seudonimo_embarazo]` | `v_embarazo[seudonimo_embarazo]` | simple | simple |
| `v_embarazo[seudonimo_paciente]` | `v_paciente_medico[seudonimo_paciente]` | simple | simple |
| `v_embarazo_factor_riesgo[clave_factor]` | `v_factor_riesgo[clave_factor]` | simple | simple |
| `v_lectura[codigo_semaforo]` | `v_semaforo[codigo_nivel]` | simple | simple |
| `v_lectura[semana_gestacion]` | `v_tiempo_gestacional[semana_gestacion]` | simple | simple |

`v_resumen_administrativo` no tiene relaciones con las demás vistas. Las nueve
relaciones restantes enlazan las columnas de fecha con las tablas automáticas.

**Rol `Medico`.** Filtra las dos tablas puente con el mismo DAX:

```dax
[upn_medico] = LOWER(USERPRINCIPALNAME())
```

sobre `v_entitlement_medico` (embarazos y datos clínicos) y
`v_entitlement_paciente_medico` (pacientes e información identificable).

**Páginas.** `VALIDACION_RLS`, `RESUMEN_ADMINISTRATIVO`, `RESUMEN_MEDICO` y
`EVOLUCION_EMBARAZO`. Desde el resumen médico se navega al detalle de un embarazo
(`EVOLUCION_EMBARAZO`, con obtención de detalles por `seudonimo_embarazo`). La
segmentación de paciente del resumen médico se guarda sin selección.

**Medidas.** 26. `Última fecha con datos` es
`MAX('publicacion v_lectura'[fecha_captura])`, con formato `dd/mm/yyyy`.

## Estado de los datos incluidos

El PBIX guarda los datos importados de `historial_p01_20261107` **después** del
historial de paciente01 y de la prueba del escenario A, sin restaurar la base:

| | En el PBIX |
|---|---|
| Lecturas publicadas en `v_lectura` | 1471 |
| Ana Carrizo López, embarazo 130 (paciente01) | 71 sesiones, 291 lecturas. La última, del 2026-11-08, en la semana 28 |
| `medico01@example.com` | 6 embarazos, entre ellos el de Ana |
| `medico02@example.com` | 3 embarazos (Camila, Gabriela y Natalia), 115 lecturas |
| `v_resumen_administrativo` | 884 lecturas publicadas; Chiriquí 286, Darién 316, Veraguas 282; ninguna celda con menos de 5 embarazos |

Una base restaurada desde un respaldo anterior a la prueba del escenario A
(70 sesiones y 286 lecturas en el embarazo 130) no coincide con estos datos hasta
que se vuelva a ejecutar ese escenario y el ETL. Después de una actualización,
el informe muestra lo que tenga la base conectada.

## Abrirlo en otro equipo

Cada equipo usa su propia base, su propio rol `fetalalert_powerbi` y su propia
contraseña (ver `docs/entorno_demo_restaurable.md`, «Equipo de respaldo»). Las
credenciales no se copian entre equipos ni se guardan en el repositorio.

1. Preparar la base: dataset, aprovisionamiento, historial de paciente01 y ETL
   (`docs/entorno_demo_restaurable.md`).
2. Abrir `powerbi/FetalAlert_validacion_final.pbix` con Power BI Desktop.
3. **Transformar datos → Configuración de origen de datos.** Seleccionar el
   origen `127.0.0.1:5433;historial_p01_20261107` y:
   - **Cambiar origen**: servidor `127.0.0.1:<POSTGRES_PORT>` y la base del
     `.env` local. El cambio se aplica a las diez consultas.
   - **Editar permisos → Editar credenciales**: tipo *Base de datos*, usuario
     `fetalalert_powerbi` y su contraseña local. Sin cifrado: el servidor tiene
     `ssl=off`.
4. **Inicio → Actualizar.**
5. Comprobar con **Modelado → Ver como → Medico**, con dos identidades
   distintas, que cada una ve un conjunto distinto de embarazos
   (`docs/proteccion_analitica.md` §8.4).

Si se guarda el PBIX después de cambiar el origen, ese cambio no se incorpora al
repositorio: la copia versionada conserva el origen con el que se validó.

## Validación

### Pruebas manuales en Power BI Desktop

Realizadas por Viviana Jaén sobre `historial_p01_20261107`:

- Ana, embarazo 130: 71 sesiones y 291 lecturas.
- La sesión nueva del 2026-11-08 aparece en la semana 28.
- La navegación del resumen médico al detalle y los filtros funcionan.
- `medico01@example.com` tiene acceso al embarazo de Ana.
- Con el rol `Medico`, `medico02@example.com` ve 3 embarazos y 115 lecturas. El
  selector muestra solo a Camila, Gabriela y Natalia, sin Ana. La comprobación
  se hizo sin una paciente seleccionada.
- El resumen administrativo muestra 884 lecturas publicadas, 286 de ellas en
  Chiriquí. Los totales coinciden con SQL.
- La supresión administrativa oculta cada grupo de provincia, mes y nivel de
  semáforo con menos de cinco embarazos distintos.
- `Última fecha con datos` conserva `MAX('publicacion v_lectura'[fecha_captura])`
  y muestra día/mes/año en el resumen médico y en la evolución.
- La vista previa de `publicacion v_tiempo_gestacional` en Power Query se
  actualizó sin errores.

### Comprobaciones técnicas sobre el archivo

Hechas sobre una copia, sin abrirlo en Power BI, leyendo los metadatos del
modelo y del informe:

- Diez consultas y diez tablas importadas, con los nombres de la tabla anterior.
  Las relaciones, las 26 medidas y las reglas del rol `Medico` coinciden con las
  de la versión anterior del informe (origen `127.0.0.1:55432`). Las diferencias
  se deben al origen, a los datos actualizados, al formato de
  `Última fecha con datos`, al orden de una tabla y a la segmentación guardada sin
  selección.
- Los 46 campos que usan los visuales existen en el modelo.
- Las diez consultas apuntan a `127.0.0.1:5433` / `historial_p01_20261107`.
- Las cifras de «Estado de los datos incluidos» se recalcularon a partir de los
  datos guardados en el archivo.
- Como `fetalalert_powerbi`, por `127.0.0.1:5433` y en solo lectura,
  `publicacion.v_tiempo_gestacional` devuelve 40 filas (semanas 1 a 40), las
  mismas que tiene el PBIX.
- **Observación de metadatos.** En los metadatos de la vista previa de Power
  Query, la consulta `publicacion v_tiempo_gestacional` conserva la marca
  `PBI_ResultType = Exception`. Las otras nueve consultas tienen `Table`. La
  causa no está confirmada. Lo que sí se verificó:
  - la vista previa se actualizó a mano sin errores;
  - el PBIX tiene las 40 filas de la tabla, las mismas que devuelve la vista;
  - la relación con `v_lectura` está presente.

  Los datos del modelo son los importados el 2026-11-08 21:11; no se ejecutó una
  actualización completa posterior.

## Evidencias

En `docs/evidencias/powerbi-validacion/`:

| Archivo | Qué muestra | Fecha |
|---|---|---|
| `01-resumen-medico-ana.png` | Resumen médico de Ana: 71 sesiones, 291 lecturas, `Última fecha con datos` 08/11/2026 | 2026-11-09 |
| `02-evolucion-ana-semana-28.png` | Detalle de Ana al que se llega desde el resumen médico: semana 28 y fecha con día/mes/año | 2026-11-09 |
| `03-rol-medico-medico01.png` | «Ver como Medico» con `medico01@example.com`: acceso al embarazo de Ana | 2026-11-08 |
| `04-rol-medico-medico02.png` | «Ver como Medico» con `medico02@example.com`: 3 embarazos, 115 lecturas, sin Ana | 2026-11-09 |
| `05-resumen-administrativo.png` | Resumen administrativo: 884 lecturas publicadas en 3 provincias | 2026-11-08 |
| `06-archivo-final-guardado.png` | `FetalAlert_validacion_final` guardado (22:01) | 2026-11-09 |

Las capturas 03 y 05 se tomaron con la versión anterior del archivo sobre la misma
base y los mismos datos (1471 lecturas). No hay capturas de la comparación con
SQL, de la supresión administrativa ni de la actualización de la vista previa de
`v_tiempo_gestacional`.

## Límites

- **El PBIX reúne las perspectivas médica y administrativa para validarlas.** No
  existe un modelo exclusivamente administrativo: queda pendiente para el
  despliegue. Que estén en páginas distintas no separa el acceso. Con el rol
  `Medico`, `v_resumen_administrativo` no se filtra, porque el rol solo actúa
  sobre las tablas puente.
- **No existe un rol de administración.** El único rol es `Medico`.
- **El archivo incluye los datos importados.** Quien abre el PBIX en Power BI
  Desktop ve todos los datos del modelo, no los filtrados por rol: el RLS se
  prueba con «Ver como». Los datos son ficticios.
- **Power BI Service no está configurado.** Faltan el workspace, la asignación del
  rol `Medico` a identidades reales, el gateway y la actualización programada
  (`docs/proteccion_analitica.md` §8.4).
- El origen guardado es el del equipo donde se validó. En cualquier otro equipo
  hay que cambiarlo y poner credenciales propias.
