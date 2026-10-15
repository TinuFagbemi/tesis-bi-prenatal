# Entorno de demostración restaurable

Permite ejecutar la demostración tantas veces como haga falta y volver siempre al
mismo punto de partida: el dataset canónico (1180 lecturas, 1180 hechos) más N
episodios de demostración, sin lecturas, auditoría ni idempotencia residuales.
Todos los datos son simulados y completamente ficticios.

## Piezas

| Script | Qué hace |
|---|---|
| `scripts/provisionar_demo.py provisionar --gestantes N` | N episodios de demostración (1–8), cada uno con su cuenta, embarazo y dispositivo. El primero es el de siempre (`DEMO-SCRUM72-01`, también en `provision.json`); los demás (`DEMO-GESTANTE-02`…) se crean sobre pacientes cuyos embarazos están todos cerrados. Es idempotente. |
| `scripts/dispositivo_gestante.py capturar --dispositivo CODIGO` | Captura sin red con el aprovisionamiento de ese dispositivo (`data/gestante/dispositivos/CODIGO.json`). |
| `scripts/escenario_demo.py ejecutar ESCENARIO.json` | Captura, inicia sesión con la cuenta de cada gestante, sincroniza y verifica la llegada única. `verificar REGISTRO.json`, después del ETL, comprueba también los hechos. |
| `scripts/entorno_demo.py respaldar \| verificar \| restaurar --carpeta X` | Respaldo de la base y de `data/gestante`, comparación con el respaldo y restauración. |

Un escenario es un JSON con las gestantes por su dispositivo y cuántas sesiones
captura cada una (una lectura por sesión):

```json
{"nombre": "C-varias-gestantes", "concurrente": true,
 "gestantes": [
   {"dispositivo": "DEMO-SCRUM72-01", "sesiones": 2, "tipo": "SIGNOS_MATERNOS"},
   {"dispositivo": "DEMO-GESTANTE-03", "sesiones": 3, "tipo": ["MOVIMIENTOS_FETALES", "SIGNOS_MATERNOS"]}]}
```

## Qué garantiza la restauración

- **Comprueba antes de cambiar.** El dump se restaura en `<base>__restaurando` y
  se compara con el manifiesto. Solo si coincide se intercambia con la activa.
- **No borra.** La base anterior queda como `<base>__previa_<marca>`, sin conexiones
  permitidas. Los archivos locales anteriores quedan en `data/gestante__previa_<marca>`.
- **Comprueba después.** Tablas fila por fila, secuencias, políticas y estado de
  RLS, vistas, funciones, permisos, roles y membresías, más las SQLite por
  contenido. Cualquier diferencia se informa con código de salida 3.
- **Se niega** si hay sesiones abiertas en la base (API, Adminer, Power BI), si el
  portal está en marcha, si el respaldo está alterado (`SHA256SUMS`) o si el
  respaldo es de otra base y no se declaró con `--respaldo-de`.
- No guarda contraseñas: los roles se respaldan con `--no-role-passwords`.

La huella se calcula con `psql` dentro del contenedor, como superusuario y en solo
lectura. El superusuario no está sujeto a RLS, así que ve todas las filas de las
tablas con FORCE ROW LEVEL SECURITY. Las filas se ordenan con `COLLATE "C"`.

## Secuencia del día D

1. Demo Control, opción 3 (detener la API). Cerrar Adminer, Power BI Desktop y el portal.
2. Poner en uso la base limpia:

   ```powershell
   python scripts/entorno_demo.py restaurar --carpeta C:\FetalAlert_respaldos\<baseline> `
       --respaldo-de fetalalert_demo_baseline
   ```

3. Demo Control, opción 4 (iniciar la API) y opción 1 (verificar). Arrancar el portal.
4. Ejecutar la demostración (escenarios A, B o C, o el portal), y después el ETL (opciones 5 y 6).
5. Para repetirla, volver al paso 1.

El nombre de la base activa no cambia, así que la API, Demo Control y Power BI
no necesitan otra configuración. Con la opción `--respaldo-de` se aplica a la
base activa el respaldo de la base limpia construida aparte.

## Validación

- `backend/tests/test_entorno_demo.py`, `test_escenario_demo.py` y la sección H de
  `test_gestante_dispositivo.py`, sin servidor.
- `backend/tests/test_entorno_demo_postgresql.py`, contra PostgreSQL real y su
  contenedor (`ENTORNO_DEMO_TEST_DATABASE_URL`, `ENTORNO_DEMO_TEST_CONTENEDOR`,
  `ENTORNO_DEMO_TEST_ADMIN`). Tiene su propio step en CI: varias gestantes, cero
  filas canónicas cambiadas, ciclo respaldar → alterar → restaurar sin residuos
  y RLS vigente tras restaurar.
