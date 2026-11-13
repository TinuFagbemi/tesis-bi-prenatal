# Validación de R3 — SCRUM-110 (registro y restauración del entorno de demostración)

Fecha: 2026-11-07 (hora de Panamá, UTC−5). Rama `viviana/scrum-110-autorregistro`
en `eaeeed2`, integrada sobre `main` (`cd06b45`, con «Guardar mi resumen» y el
entorno restaurable). R3 no cambió código: se probó exactamente ese árbol.

**Alcance.** Esta validación se hizo en mi computadora, con una base local exclusiva
y el mecanismo de restauración de `scripts/entorno_demo.py` sin modificaciones. **No
cubre el equipo de presentación de Tinuola**: el ensayo en ese equipo queda
pendiente (ver «Pendiente»).

Todos los datos son simulados y completamente ficticios. Esta carpeta no contiene
credenciales: las claves viajaron solo por variables de entorno y toda la salida se
enmascaró antes de escribirse.

## Evidencia incluida y evidencia solo local

Se versiona **solo este informe**. Quedan en el equipo de la validación:

- los registros de cada paso, las respuestas del portal en JSON y el manifiesto del
  código probado;
- las herramientas (el lanzador, el ciclo del portal y las consultas);
- el respaldo de validación, que contiene un volcado de la base.

## 1. Baseline

| Paso | Resultado |
|---|---|
| Dataset | Generador oficial (`scripts/generate_mock_data.py`). Huella sin `password_hash`: `1976be7b…`, la misma que produce `main`. |
| Base | `scrum110_r3_20261107`, exclusiva de esta validación, en el clúster local. |
| Migraciones | `alembic upgrade head` → `c5d2a8f1e073` (head), incluida la migración del registro. |
| Carga | `load_mock_data.py`: 1180 lecturas verificadas. |
| Episodios de demostración | `provisionar_demo.py provisionar --gestantes 4`: embarazos 130–133 con sus dispositivos, sin mediciones. |
| Estado | 30 pacientes, 37 usuarios, 40 teléfonos, 34 embarazos, 1180 lecturas, 0 aceptaciones, 0 eventos de auditoría. |
| Respaldo inicial | `entorno_demo.py respaldar`, con la API y el portal detenidos: 36 tablas, 20 secuencias y 5 archivos locales (`provision.json` y 4 dispositivos). `verificar`: IDÉNTICO. |

El ETL no se ejecutó: requiere la credencial de `fetalalert_etl`, que este equipo
no tiene, y no interviene en el registro. El baseline no incluye, por tanto, la
publicación analítica de los episodios de demostración.

El portal usó su configuración por omisión (`data/gestante`, puerto 8100), la misma
que lee `entorno_demo.py` para negarse a restaurar con el portal en marcha.

## 2. Los tres ciclos

Cada ciclo usó **la misma cédula (`SIM-R3-0001`) y el mismo correo
(`paciente31@example.com`)**. Por eso, cada registro posterior a una restauración
demuestra que la anterior eliminó la cuenta: con la cuenta presente, el registro
habría respondido 409.

Pasos de cada ciclo:

1. Arrancar la API real (como `fetalalert_api`) y el portal.
2. A través del portal: documentos `2026-11-v3`, registro, inicio de sesión,
   portal sin embarazos, cierre de sesión y comprobación de que la sesión ya no
   sirve.
3. Verificar en PostgreSQL lo guardado.
4. Detener el portal y la API.
5. `entorno_demo.py restaurar --carpeta <respaldo>`.
6. Comprobar el estado restaurado.

| | Ciclo 1 | Ciclo 2 | Ciclo 3 |
|---|---|---|---|
| Registro | 201 | 201 | 201 |
| Inicio de sesión | 200 | 200 | 200 |
| Portal | 0 embarazos | 0 embarazos | 0 embarazos |
| Cierre de sesión → consulta posterior | 200 → 401 | 200 → 401 | 200 → 401 |
| En PostgreSQL | perfil 130 y cuenta PACIENTE activa 137 (Argon2id), celular principal, vínculo, aceptaciones `2026-11-v3` (`ef37ea4e`, `8362b180`), 0 embarazos, auditoría | igual | igual |
| Restauración | IDÉNTICO, código 0 | IDÉNTICO, código 0 | IDÉNTICO, código 0 |
| Cuenta del ensayo tras restaurar | ausente (0 perfiles, 0 cuentas) | ausente | ausente |
| Totales tras restaurar | iguales al baseline | iguales | iguales |
| Archivos del portal | iguales al respaldo; `sesion_local.sqlite3` del ensayo, apartado | iguales | iguales |
| `seguridad` y permisos de la API | iguales al baseline (14 elementos) | iguales | iguales |

Tras el ciclo 3 se registró una vez más con la misma cédula y el mismo correo:
201, inicio de sesión, portal vacío y lo guardado completo. `verificar` contra el
respaldo detecta exactamente esas filas (1 perfil, 1 cuenta, 1 teléfono, 1
vínculo, 2 aceptaciones y 3 eventos de auditoría) y nada más.

Las restauraciones se ejecutaron a mano, una por ciclo, con la orden de
`entorno_demo.py`. El portal no restaura nada por sí mismo.

## 3. Comprobación del esquema `seguridad`

La huella de `entorno_demo.py` compara los esquemas `operacional`, `analitico`,
`privado`, `publicacion` y `public`. **No incluye `seguridad`**, que es donde están
`seguridad.autorregistrar_paciente` y las funciones de RLS. La restauración sí
recupera ese esquema, porque `pg_dump` vuelca la base completa, pero la
comparación con el manifiesto no detectaría una diferencia en él.

Por eso, antes y después de cada restauración se comprobó aparte:

- cada función de `seguridad`: definición (md5), `SECURITY DEFINER`, propietario,
  permisos y si `fetalalert_api` puede ejecutarla;
- el propietario y los permisos del esquema;
- que `fetalalert_api` sigue sin INSERT, UPDATE ni DELETE sobre
  `operacional.paciente`, y conserva el INSERT de `main` sobre
  `operacional.usuario_paciente`;
- la versión de Alembic.

Los 14 elementos coincidieron con el baseline en los tres ciclos.

**Mejora propuesta, no aplicada:** añadir `seguridad` a `ESQUEMAS` en
`scripts/entorno_demo.py`, para que el propio mecanismo compare sus funciones y
permisos. No es un fallo de la restauración y no se modificó el mecanismo; queda
para decidirlo con quien lo mantiene.

## 4. Lo que no cambió

- Las seis bases que existían antes conservan su nombre y su OID.
- Roles y membresías del clúster, idénticos a la instantánea inicial. La
  restauración solo los compara; no los modifica.
- No se modificaron credenciales.
- Docker Desktop estaba apagado y se volvió a apagar. Los puertos 5433, 8100 y
  8110 quedaron libres.

## 5. Recursos que quedan en el equipo de la validación

| Recurso | Estado |
|---|---|
| `scrum110_r3_20261107` | Base activa de R3, con la cuenta del último registro. Clúster local, contenedor detenido. |
| `scrum110_r3_20261107__previa_20261107t221505z`, `…t221616z`, `…t221712z` | Estado anterior a cada restauración, sin conexiones permitidas. Se conservan. |
| `data/gestante` | Archivos del portal del último registro. |
| `data/gestante__previa_20261107t221505z`, `…t221616z`, `…t221712z` | Archivos del portal anteriores a cada restauración (en `.gitignore`). Se conservan. |
| Respaldo de validación | Respaldo inicial de R3 (volcado, roles sin claves, archivos locales y manifiesto). Se conserva y no se versiona. |

## 6. Pendiente

- **Ensayo en el equipo de presentación.** No se ha probado. Requiere que ese
  equipo construya su baseline con la migración `c5d2a8f1e073` y repita los ciclos.
- **La mejora de la huella** descrita en §3.
- **La rotación de la credencial de desarrollo `fetalalert_dev`**, no autorizada y
  fuera del alcance de este PR.
