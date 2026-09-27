# Protección analítica y publicación para Power BI (SCRUM-98 y SCRUM-99)

Este documento describe la capa que expone datos prenatales a Power BI: qué se
publica, con qué identidad se consulta, cómo se aplica el aislamiento por médico
y qué queda pendiente de configurar a mano.

Se ha escrito en dos tiempos, y conviene saber cuál es cuál:

- **SCRUM-98** construyó la superficie segura inicial —cuatro vistas
  seudonimizadas— y validó sobre ella el aislamiento por médico. Las secciones 1
  a 10 describen esa capa y **siguen siendo correctas**: ninguna de sus cuatro
  vistas cambia de contrato en SCRUM-99.
- **SCRUM-99** la *extiende* con una superficie clínica autorizada, porque el
  tablero médico de SCRUM-73 necesita algo que la primera no podía dar: saber a
  quién se está atendiendo. Las secciones 11 a 15 describen esa extensión.

SCRUM-99 no corrige a SCRUM-98. Aquella superficie era la adecuada para lo que
tenía que demostrar —que la seudonimización y el RLS funcionan— y sigue
publicada. Lo que cambió no es el diseño: es que un seudónimo no sirve para dar
seguimiento prenatal a una persona.

Todos los datos del proyecto son **simulados y completamente ficticios**. No hay
ni habrá datos de pacientes reales.

Los nombres, cédulas y teléfonos de la muestra son **sintéticos desde origen**:
se inventaron para la validación funcional del prototipo y no se derivan de
ningún expediente. Conviene decirlo así y no llamarlo «anonimizado», que
describiría un proceso distinto —partir de datos reales y quitarles la
identidad— y aquí no hay tal punto de partida.

Para que la muestra se lea coherente, los identificadores ficticios emplean un
prefijo consistente con la provincia asignada al episodio simulado. Es
coherencia interna de la simulación y nada más: **no** se está afirmando que la
clínica determine el documento de identidad de una persona. La dependencia va en
un solo sentido —el episodio decide la provincia, y la cédula la acompaña— y una
validación del generador comprueba que no se separen.

Los correos de las cuentas, en cambio, conservan identificadores deterministas
del tipo `pacienteNN@example.com` y `medicoNN@example.com`: son el contrato
reproducible de autenticación y autorización, entran en `usuario.email` y en
`USERPRINCIPALNAME()`, y cambiarlos reescribiría el ciclo de cuentas, los tokens
y el RLS. Que una gestante se llame «Sofía Mendoza Castillo» y entre con
`paciente05@example.com` no es una inconsistencia: es la separación entre
identidad de presentación e identidad de autenticación.

---

## 1. Tres actores que no son el mismo

La confusión más cara en un diseño como este es tratar como una sola cosa a tres
identidades distintas. Aquí no lo son:

| Actor | Qué es | Dónde vive |
|---|---|---|
| **Cuenta técnica de la fuente de datos** — `fetalalert_powerbi` | Un rol de PostgreSQL con `LOGIN`, solo lectura, sin `SUPERUSER` ni `BYPASSRLS`. Es la credencial con la que el *dataset* de Power BI se conecta a la base. | PostgreSQL |
| **Identidad individual del médico** | La cuenta con la que cada médico entra a **Power BI Service**, con rol `Viewer`. Es la que el RLS del dataset usa para filtrar. | Power BI Service |
| **Administrador técnico del workspace** | Quien publica el dataset, asigna el rol `Viewer` y configura la actualización. | Power BI Service |

Y ninguno de los tres es el **rol ADMIN de la aplicación**, que es una cuenta de
la API y no tiene acceso clínico individual en ninguna capa.

La cuenta técnica es una sola y eso es correcto: no se multiplica una credencial
de base de datos por médico. Lo que **no** se delega en ella es la autorización;
esa la resuelve el RLS del dataset a partir de la identidad individual.

---

## 2. Recorrido de autenticación y autorización

```
médico → Power BI Service (su identidad, rol Viewer)
       → dataset con RLS dinámico
       → USERPRINCIPALNAME() del visor
       → publicacion.v_entitlement_medico (upn_medico)
       → seudónimo del embarazo autorizado
       → publicacion.v_embarazo / publicacion.v_lectura
```

Y por debajo, una sola conexión:

```
dataset → fetalalert_powerbi → publicacion.* (SELECT, nada más)
```

La cadena completa, en la base:

```
identidad Power BI (UPN)
→ operacional.usuario.email
→ operacional.usuario_medico
→ operacional.medico
→ operacional.seguimiento_clinico vigente
→ operacional.embarazo
→ privado.seudonimo_embarazo
```

**La identidad no se toma de un filtro ni de un parámetro del reporte.**
`USERPRINCIPALNAME()` la resuelve el servicio a partir de la sesión autenticada;
un parámetro sería un campo que el visor puede escribir, y escribir el correo de
otro médico sería toda la escalada que hace falta.

---

## 3. El alcance del médico: por asignación, no por clínica

Un médico ve un embarazo si y solo si existe un `SeguimientoClinico` que cumpla
**las tres condiciones a la vez**:

```
activo = true
fecha_asignacion <= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date
fecha_fin IS NULL OR fecha_fin >= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date
```

«Hoy» es **el día en Panamá**, no el del reloj de la sesión que ejecuta la
consulta. `CURRENT_DATE` habría servido solo mientras el servidor y todas las
sesiones estuvieran en la zona correcta: una sesión con `SET TIME ZONE 'UTC'`
—el valor por omisión de muchos clientes y del contenedor— adelanta el cambio de
día cinco horas, de modo que entre las 19:00 y la medianoche de Panamá la última
jornada de una asignación ya habría caducado para la base pero no para la
paciente. Fijar la zona hace que la decisión de acceso no dependa de cómo se
conectó quien pregunta.

Consecuencias, todas ellas cubiertas por pruebas:

- `PRINCIPAL`, `APOYO` y `REEMPLAZO` conceden **lo mismo**. El tipo no se filtra:
  hacerlo dejaría sin datos a quien cubre una baja, que es cuando el acceso más
  falta hace.
- Mientras la asignación esté vigente se ve **todo el historial** del embarazo,
  no solo el tramo que solapa con la asignación. Medio historial clínico no es
  un historial clínico.
- **No** se ven otros embarazos de la misma paciente. El alcance es por episodio.
- Al terminar o desactivarse la asignación, deja de conceder.
- Una asignación futura todavía no concede.

**`MedicoClinica` no concede acceso clínico.** Sirve para contexto
organizacional, filtros y agregados. Compartir clínica con un embarazo no es
seguir ese embarazo, y la vista de entitlements no menciona esa tabla.

> ⚠️ Esto **revoca** cualquier descripción anterior de un «RLS por clínica». No
> existe y no debe existir: derivaría acceso de una relación administrativa.

---

## 4. Superficies publicadas

Cuatro objetos en el esquema `publicacion`, y son lo único que la cuenta técnica
puede nombrar. Son **vistas**, no tablas: una vista se ejecuta con los
privilegios de su propietario, así que publicar una columna es un acto explícito.
Conceder `SELECT` sobre una tabla de `analitico` habría publicado también la
cédula, el nombre y el teléfono que esa tabla lleva.

| Vista | Para qué | Granularidad |
|---|---|---|
| `v_embarazo` | Superficie longitudinal por episodio | Un episodio seudonimizado |
| `v_lectura` | Serie de lecturas para alertas, tendencias y adherencia | Una lectura |
| `v_entitlement_medico` | Relación médico–embarazo para el RLS dinámico | Un par (UPN, seudónimo) |
| `v_resumen_administrativo` | Indicadores operativos | Agregado, mínimo 5 embarazos por celda |

### Lo que no sale en ninguna

Cédulas, nombres y apellidos, correos de pacientes, teléfonos, direcciones
exactas, texto libre, identificadores operacionales (`id_paciente`,
`id_embarazo`, `id_lectura`, `id_sesion`, `id_medico`, `id_clinica`) y claves
internas del *star schema* que no hagan falta.

### Generalización

- **Fechas**: no todas se generalizan igual, y la diferencia es deliberada.

  | Tipo de fecha | Columnas | Granularidad publicada |
  |---|---|---|
  | Fechas contextuales del episodio | `v_embarazo.mes_inicio`, `v_embarazo.mes_probable_parto` | **mes** |
  | Fecha clínica de captura | `v_lectura.fecha_captura` | **día**, sin hora |
  | Periodo del agregado administrativo | `v_resumen_administrativo.mes` | **mes** |

  **Fechas contextuales del episodio → mes.** El inicio y la fecha probable de
  parto describen el embarazo, no una medición. Ningún análisis de esta capa
  necesita el día exacto en que empezó un episodio, y ese día, combinado con la
  provincia y el tramo de edad, es un cuasi-identificador: reduce mucho el
  conjunto de personas compatibles. Truncarlo al mes elimina precisión que no
  se usa y le quita valor como dato de enlace.

  **Fecha clínica de captura → día, sin hora.** `fecha_captura` se publica como
  `(f.fecha_hora AT TIME ZONE 'America/Panama')::date`: conserva el **día
  calendario panameño** y elimina hora, minuto y segundo. **No está generalizada
  al mes**, y no debe describirse así. El día se conserva porque es
  funcionalmente necesario para:

  - el seguimiento longitudinal del episodio;
  - el análisis de tendencias entre lecturas;
  - la medición de adherencia al monitoreo;
  - la secuencia temporal de los monitoreos.

  Truncarla al mes haría indistinguibles todas las lecturas de un mismo mes y
  vaciaría esos cuatro análisis. Lo que sí se retira es la hora: ningún indicador
  de esta capa la necesita, y es la parte que más enlaza una lectura con un
  momento concreto de la vida de alguien.

  **Las tres conversiones nombran la zona.** `fecha_captura`,
  `v_resumen_administrativo.mes` y los dos meses de `v_embarazo` se calculan con
  `AT TIME ZONE 'America/Panama'`, nunca con `::date` a secas ni con
  `CURRENT_DATE`. La columna de origen es `timestamptz`, y un `::date` desnudo la
  convierte usando el `TimeZone` de la sesión: el mismo instante caería en un día
  —y en un mes— distinto según quién ejecutara el ETL o abriera Power BI. Una
  lectura tomada a las 22:30 del 28 de febrero en Panamá aparecería como 1 de
  marzo para una sesión en UTC, y eso no solo desplaza un punto en una serie:
  mueve una fila entre dos cubos del agregado administrativo y puede cruzar el
  mínimo de celda. La zona está fijada en la migración, se comprueba contra
  `app.etl.reglas.ZONA_HORARIA_CLINICA`, y hay pruebas que ejecutan las vistas
  bajo cuatro `TimeZone` de sesión distintos —incluido `Pacific/Kiritimati`,
  a +19 h de Panamá— y exigen el mismo resultado.

  `dim_embarazo.fecha_inicio` y `fecha_probable_parto` no se convierten: son
  columnas `DATE`, no instantes, y no tienen zona que interpretar.

  La semana gestacional se conserva en las dos vistas porque es la variable
  clínica central.
- **Edad**: en tramos de cinco años, calculada al inicio del embarazo.
- **Ubicación**: provincia. El distrito se queda fuera.

### Lo que sí se conserva

Semana gestacional, trimestre, código de semáforo y prioridad, `hr_valor`,
`spo2_valor`, `mov_valor`, los tres estados derivados, fecha de captura —al
día, sin hora— y `secuencia_sesion`.

**`secuencia_sesion` es cronológico, no el `id_sesion` disfrazado.** Se calcula
con `row_number()` sobre las sesiones del episodio, ordenadas por el **instante
de su primera lectura** y desempatadas por `id_sesion`:

```sql
row_number() OVER (PARTITION BY id_embarazo
                   ORDER BY inicio_sesion, id_sesion)
```

La distinción no es cosmética. `id_sesion` es una clave *surrogate*: la asigna el
servidor central cuando **recibe** la sesión, y este sistema es *offline-first* —
el nodo edge captura sin conexión y entrega cuando puede. Ese identificador es
por tanto orden de **sincronización**, no de **ocurrencia**: una sesión tomada el
10 de junio en una comunidad sin cobertura y subida el 25 recibe un id mayor que
otra tomada el 20 y subida el mismo día. Publicar el surrogate como «número de
sesión» habría invertido la serie temporal justo en el escenario que esta tesis
dice modelar, y la medición de adherencia —el motivo por el que la columna
existe— habría medido la conectividad de la comunidad en lugar del seguimiento
de la paciente.

El desempate por `id_sesion` está para que la consulta sea reproducible: sin un
segundo criterio, dos sesiones que empiezan en el mismo instante podrían
intercambiar su número entre ejecuciones y la serie dejaría de ser comparable
consigo misma.

La numeración es densa y empieza en 1 para cada episodio, así que sigue sin
publicar ninguna clave de la base: dice «la tercera sesión de este embarazo», no
«la sesión 8412 del sistema».

---

## 5. Seudonimización, que no es anonimización

La transformación es **seudonimización**. El término importa porque describe lo
que la protección puede y no puede prometer:

- el seudónimo es **estable**: el mismo embarazo tiene el mismo UUID en cada
  ejecución del ETL, y eso es lo que permite seguirlo a lo largo del tiempo;
- **enlazable**: existe un mapa que devuelve la persona;
- por lo tanto los datos publicados **siguen siendo datos personales**, y lo que
  se protege es *quién puede recorrer el camino de vuelta*, no la imposibilidad
  de recorrerlo.

Llamarlo «anonimización» afirmaría que el vínculo se destruyó. No se destruye: se
guarda en `privado`.

### El mapa privado

Dos tablas en el esquema `privado`:

- `privado.seudonimo_paciente (id_paciente → seudonimo uuid)`
- `privado.seudonimo_embarazo (id_embarazo → seudonimo uuid)`

**Los UUID son aleatorios, no derivados.** Los produce `gen_random_uuid()`. Un
hash de `id_paciente` se invertiría probando los enteros —son pocos y
consecutivos— y habría hecho decorativa la tabla entera. El mapa existe
precisamente porque la correspondencia tiene que *guardarse* y no calcularse.

Un seudónimo por **episodio** y otro por **paciente**, en dos espacios distintos:
así la publicación conserva la separación entre embarazos, y encadenar dos
episodios de la misma persona requiere el seudónimo de paciente, que se publica
aparte y a propósito.

### Quién lo alcanza

| Rol | Privilegio sobre `privado` |
|---|---|
| `fetalalert_etl` | `USAGE`, `SELECT`, `INSERT` |
| `fetalalert_mantenimiento` | `USAGE`, `SELECT`, `INSERT`, `UPDATE`, `DELETE` |
| `fetalalert_rls_owner` | `USAGE`, `SELECT` **solo** sobre `seudonimo_embarazo` |
| `fetalalert_powerbi` | **ninguno**, ni siquiera `USAGE` sobre el esquema |
| `fetalalert_api` | ninguno |
| `PUBLIC` | ninguno |

El ETL no recibe `UPDATE` ni `DELETE`: cambiar un seudónimo ya emitido partiría
en dos la serie de ese embarazo, y ninguna operación del ETL debería poder
hacerlo. Mantenimiento sí los tiene, porque restaurar un backup es exactamente
eso.

### Backup y recuperación

**El mapa debe incluirse en el backup.** Un restore que no devuelva `privado`
deja toda la superficie publicada sin longitudinalidad: la siguiente ejecución
del ETL emitiría seudónimos nuevos y cada embarazo aparecería como si fuera otro.

Procedimiento de recuperación:

1. restaurar `operacional`, `analitico` y **`privado`** de la misma copia;
2. ejecutar el ETL, que reutilizará los seudónimos existentes —el `ON CONFLICT
   DO NOTHING` es lo que lo garantiza— y emitirá solo los de filas nuevas;
3. comprobar que `seudonimos_seudonimo_paciente=0` y
   `seudonimos_seudonimo_embarazo=0` en el resumen, que es la señal observable
   de que no se regeneró nada;
4. actualizar el dataset de Power BI.

Si el mapa se perdiera, la longitudinalidad histórica no se puede reconstruir:
los seudónimos antiguos no existen en ningún otro sitio. Es una pérdida
irreversible y por eso el mapa se trata como parte del backup, no como caché.

---

## 6. Separación de credenciales

`fetalalert_powerbi`:

- solo lectura;
- no es propietario de ningún objeto;
- sin `SUPERUSER`, `BYPASSRLS`, `CREATEROLE`, `CREATEDB` ni `REPLICATION`;
- no hereda ni puede asumir al migrador, a los *owners*, a mantenimiento ni al
  ETL —comprobado por `MEMBER`, `USAGE` y `SET`, que no son intercambiables;
- **sin `USAGE`** sobre `operacional`, `analitico`, `privado` ni `seguridad`. Sin
  `USAGE` sobre un esquema no puede ni nombrar sus objetos, de modo que una
  tabla nueva queda fuera de su alcance sin que nadie se acuerde de revocarla;
- `SELECT` sobre las cuatro vistas de `publicacion`, y nada más.

Los roles y logins son **globales del clúster** y se aprovisionan fuera de
Alembic. El bootstrap versionado declara los `NOLOGIN`; despliegue y CI aportan
los logins y sus contraseñas desde secretos externos. Alembic no crea ni elimina
roles: comprueba que existan y que sean lo que dicen ser, y aborta si no.

---

## 7. ADMIN de la aplicación

- **No** accede a registros clínicos individuales.
- **No** recibe *bypass* de ninguna clase.
- **No** puede enumerar pacientes, embarazos, sesiones ni lecturas —ni por la
  API, ni por SQL, ni por los puentes de identidad.
- Puede consumir `v_resumen_administrativo`: indicadores agregados con un mínimo
  de cinco embarazos distintos por celda y sin ningún seudónimo, de modo que no
  permite reconstruir una trayectoria individual.

---

## 8. Configuración manual en Power BI Service

El repositorio **no contiene** un artefacto Power BI versionable —ni `.pbix`, ni
`.pbip`, ni modelo semántico—, así que lo que sigue queda pendiente de hacerse a
mano. **Nada de esto está configurado todavía.**

### 8.1 Origen de datos

Conectar a PostgreSQL con `fetalalert_powerbi` e importar exactamente:

- `publicacion.v_embarazo`
- `publicacion.v_lectura`
- `publicacion.v_entitlement_medico`
- `publicacion.v_resumen_administrativo`

### 8.2 Relaciones del modelo

| Desde | Hacia | Cardinalidad | Filtro cruzado | Filtro de seguridad |
|---|---|---|---|---|
| `v_entitlement_medico[seudonimo_embarazo]` | `v_embarazo[seudonimo_embarazo]` | muchos a uno | **ambas direcciones** | **ambas direcciones** |
| `v_embarazo[seudonimo_embarazo]` | `v_lectura[seudonimo_embarazo]` | uno a muchos | simple | — |

> ⚠️ **La primera relación no puede quedar en dirección simple.** Es el punto en
> el que un modelo que parece correcto no aísla nada, así que conviene decir por
> qué con precisión.
>
> `v_entitlement_medico` es una **tabla puente**: un médico tiene varios
> embarazos y un embarazo puede tener varios médicos —`PRINCIPAL`, `APOYO` y
> `REEMPLAZO` a la vez—, de modo que está en el lado *muchos* de su relación con
> `v_embarazo`. Y el rol de RLS filtra **esa** tabla.
>
> En Power BI un filtro cruzado simple propaga del lado *uno* al lado *muchos*,
> nunca al revés. Con dirección simple, filtrar el puente no filtra
> `v_embarazo`: cada visor seguiría viendo **todos** los episodios, y el único
> síntoma sería que las medidas contadas sobre el puente salieran bien. Un
> aislamiento que solo funciona en la tabla oculta no es un aislamiento.
>
> Hacen falta **las dos cosas**, y son dos ajustes distintos en la misma
> relación:
>
> 1. **Filtro cruzado en ambas direcciones** (`Cross filter direction: Both`).
> 2. **«Aplicar filtro de seguridad en ambas direcciones»** — la casilla
>    `Apply security filter in both directions`, que en el modelo tabular es
>    `securityFilteringBehavior: bothDirections`. El punto 1 por sí solo hace
>    viajar los filtros de los visuales; es este el que hace viajar también el
>    del rol de RLS. Marcar solo el primero deja el modelo exactamente igual de
>    abierto y es el error más fácil de cometer aquí.
>
> Es el patrón documentado de Microsoft para RLS dinámico a través de un puente,
> y la alternativa —poner `v_embarazo` en el lado *muchos* de una dimensión de
> médico— no existe aquí: la relación médico↔embarazo es de muchos a muchos, así
> que el puente es inevitable.

La segunda relación sí se queda en dirección simple: `v_embarazo` es el lado
*uno* y propaga hacia `v_lectura` sin ayuda. Ponerla bidireccional añadiría
caminos de filtrado que nadie necesita.

`v_resumen_administrativo` queda **sin relación** con las demás: es una tabla
aislada a propósito, para que ningún filtro cruzado la ligue a un episodio. Esa
es también la razón de que la bidireccionalidad de arriba no la alcance.

### 8.3 Rol de RLS

Crear un rol llamado `Medico` con este filtro DAX sobre
`v_entitlement_medico`:

```dax
[upn_medico] = LOWER( USERPRINCIPALNAME() )
```

El filtro llega a `v_embarazo` **solo si la primera relación de §8.2 tiene el
filtro de seguridad en ambas direcciones**; desde `v_embarazo` sigue hasta
`v_lectura` por la relación simple. Si la configuración de §8.2 no está hecha,
este DAX se aplica y no aísla nada.

Marcar `v_entitlement_medico` como **oculta** en el modelo: es una superficie de
seguridad, no una tabla analítica. `upn_medico` es un identificador directo y no
debe aparecer en ningún visual.

### 8.4 Workspace y permisos

1. Publicar el dataset en el workspace.
2. Asignar a cada médico el rol `Viewer` del workspace **y** pertenencia al rol
   RLS `Medico` del dataset.
3. Verificar con «Probar como rol» usando **dos identidades médicas distintas**,
   y confirmar que cada una ve un conjunto de embarazos diferente y que ninguna
   ve los de la otra.

   La comprobación tiene que hacerse sobre un visual de **`v_embarazo` o de
   `v_lectura`**, no sobre uno del puente. Un visual construido sobre
   `v_entitlement_medico` sale filtrado aunque la propagación esté mal
   configurada —el rol filtra esa tabla directamente—, así que es precisamente
   el que no distingue un modelo aislado de uno abierto. Contar episodios en
   `v_embarazo` sí lo distingue: con la configuración incompleta, las dos
   identidades verían el mismo total.
4. Configurar la actualización programada del dataset.

### 8.5 Fuera de alcance de este ticket

SSO institucional, seguridad por clínica y Power BI Report Server. El prototipo
usa Power BI Service.

---

## 9. Límites y riesgo residual

### 9.1 Latencia de revocación

El dataset está diseñado para **importación**. Por lo tanto:

- una asignación nueva, una revocación o un cambio de asignación se reflejan en
  Power BI **después de actualizar el modelo**, no antes;
- entre el cambio y la actualización, el dataset importado sigue mostrando el
  alcance anterior;
- **la revocación operacional sí es inmediata**: en PostgreSQL y en la API, un
  médico deja de leer en la misma petición siguiente.

No debe afirmarse que la revocación analítica es instantánea. No lo es mientras
el modelo importe.

### 9.2 Reidentificación

El riesgo no es cero y conviene decirlo:

- los seudónimos son estables, así que un observador con acceso al dataset puede
  seguir a un individuo a lo largo del tiempo aunque no sepa quién es;
- la combinación de provincia, tramo de edad, mes de inicio y número de gestas
  puede ser rara en una población pequeña;
- el mínimo de celda de la superficie administrativa (5 embarazos) reduce, pero
  no elimina, la inferencia por diferencia entre agregados.

Mitigaciones aplicadas: generalización al mes de las fechas contextuales del
episodio, retirada de la hora en la fecha de captura (que se conserva al día
porque el análisis longitudinal la necesita), tramos de edad y ubicación a nivel
de provincia; eliminación
de identificadores directos; mínimo de celda; y el hecho de que el mapa vive en
un esquema que la credencial de Power BI no puede ni nombrar.

### 9.3 Qué protege la cuenta técnica y qué no

`fetalalert_powerbi` garantiza que **nada fuera de lo publicado** sale de la
base. No garantiza el aislamiento entre médicos: eso lo hace el RLS del dataset,
que vive en Power BI. Si el dataset se publicara sin el rol `Medico` configurado,
cualquier visor vería la superficie completa. Por eso el paso 8.4 no es opcional.

---

## 10. Correcciones pendientes en el documento de la tesis

Estas secciones del documento Word tendrán que revisarse. **No se editan desde
este repositorio.**

1. **RLS «por clínica»** — cualquier pasaje que describa el aislamiento del
   médico derivado de `MedicoClinica` o de la clínica a la que pertenece. El
   aislamiento deriva de `SeguimientoClinico` vigente.
2. **Cuenta técnica vs. usuario Viewer** — cualquier pasaje que trate
   `fetalalert_powerbi` como «el usuario médico» o que sugiera una credencial de
   base de datos por médico. Son dos identidades y actúan en capas distintas.
3. **«Anonimización»** — cada uso del término aplicado a esta transformación.
   Es seudonimización: estable, enlazable y reversible con el mapa.
4. **Power BI como implementado** — cualquier afirmación de que los dashboards,
   el RLS del dataset o el workspace están configurados. Lo implementado es la
   capa de publicación y el contrato de entitlements en PostgreSQL; la
   configuración de Power BI Service está descrita en la sección 8 y pendiente.
5. **Revocación instantánea en analítica** — si el texto afirma que un cambio de
   asignación se refleja de inmediato en los dashboards, debe matizarse con la
   latencia de actualización del modelo importado.
6. **ADMIN** — cualquier pasaje que le atribuya acceso clínico individual o
   capacidad de enumerar pacientes.

---

# SCRUM-99 — Publicación clínica autorizada

## 11. Por qué el médico sí recibe datos identificables

La superficie de SCRUM-98 publica seudónimos. Para medir adherencia, distribuir
alertas o comparar provincias eso es exactamente lo que hace falta, y es lo que
aquella entrega tenía que demostrar. Para dar seguimiento prenatal no sirve:

> El nombre no es un identificador único. Dos gestantes pueden llamarse igual, y
> un médico que abre su tablero necesita saber **a cuál de las dos** está viendo
> antes de interpretar una alerta. Necesita además poder llamarla.

De ahí que SCRUM-99 publique, en **una sola vista**, nombre completo, cédula,
teléfono y correo de contacto.

### Por qué esto sigue siendo mínimo privilegio

Mínimo privilegio no quiere decir «los menos datos posibles»: quiere decir los
datos necesarios para un propósito, y solo para quien tiene ese propósito. Los
cuatro límites que lo sostienen aquí:

1. **Una sola vista lleva PII.** `publicacion.v_paciente_medico`, y ninguna otra
   de las once. Una prueba de lista blanca fija sus siete columnas exactas y
   otra comprueba que las diez restantes no tienen ninguna.
2. **Solo pacientes en seguimiento.** La vista solo tiene fila para una paciente
   con al menos un seguimiento clínico vigente. En el conjunto canónico eso son
   20 de 30: las otras diez no están publicadas en absoluto.
3. **Y dentro de eso, solo las suyas.** El rol de RLS del modelo reduce esas 20 a
   las pacientes de quien consulta, a través de
   `v_entitlement_paciente_medico`.
4. **El ADMIN no la ve.** Su modelo es otro y contiene una sola tabla agregada.

### Qué no se publica, y por qué

| Excluido | Motivo |
|---|---|
| `observaciones` del puente de factores | Texto libre por embarazo. Hoy el dato simulado es inocuo; el contrato tiene que proteger también los datasets que vengan después |
| `descripcion` del catálogo gestacional | Vale «Semana gestacional N»: redundante con `semana_gestacion` |
| `descripcion` del catálogo de factores | Glosa sin valor clínico para el tablero |
| `grupo_clinico` | Derivable de `trimestre`; no se añadió al modelo estrella |
| Dimensión de médico | SCRUM-73 no la necesita, y una lista de profesionales es PII de terceros sin propósito |
| RUC, dirección física, corregimiento de la clínica | Nunca llegan a `analitico`: el ETL ya los descarta |
| `id_paciente`, `id_embarazo`, `id_clinica`, `id_usuario`… | Las relaciones usan seudónimos y claves de negocio |

`clasificacion_embarazo` **sí** se publica, aunque el ETL la deja siempre NULL
porque ninguna regla de negocio la define todavía. Está solo por compatibilidad
con el informe existente (§15); no debe construirse ningún visual sobre ella.

---

## 12. La superficie completa: once vistas

Las cuatro de SCRUM-98 **sin un solo cambio**, y siete nuevas.

| Vista | Grano | Clave | PII | Filtro en SQL | Consumidor |
|---|---|---|---|---|---|
| `v_entitlement_medico` | médico × embarazo | `(upn_medico, seudonimo_embarazo)` | UPN, solo seguridad | seguimiento vigente | RLS del dataset |
| `v_entitlement_paciente_medico` | médico × paciente | `(upn_medico, seudonimo_paciente)` | UPN, solo seguridad | derivado del anterior | RLS del dataset |
| `v_paciente_medico` | **una paciente** | `seudonimo_paciente` | **sí, aprobada** | con seguimiento vigente | Power BI MEDICO |
| `v_embarazo_medico` | **un episodio** | `seudonimo_embarazo` | no | con seguimiento vigente | Power BI MEDICO |
| `v_lectura` | **una lectura** | — (1180 filas) | no | ninguno | Power BI MEDICO |
| `v_embarazo_factor_riesgo` | embarazo × factor | `(seudonimo_embarazo, clave_factor)` | no | con seguimiento vigente | Power BI MEDICO |
| `v_tiempo_gestacional` | una semana | `semana_gestacion` | no | catálogo | Power BI MEDICO |
| `v_semaforo` | un nivel | `codigo_nivel` | no | catálogo | Power BI MEDICO |
| `v_factor_riesgo` | un factor | `clave_factor` | no | catálogo | Power BI MEDICO |
| `v_resumen_administrativo` | provincia × mes × semáforo | esa tripleta | no | `HAVING >= 5` | Power BI ADMIN |
| `v_embarazo` | un episodio | `seudonimo_embarazo` | no | ninguno | — *(ver abajo)* |

**`v_embarazo` se conserva sin cambios y queda sin consumidor activo.** Es la
superficie seudonimizada heredada de SCRUM-98; se mantiene por compatibilidad y
**no** forma parte de los modelos MEDICO ni ADMIN definidos por SCRUM-99. Su
existencia técnica no autoriza a nadie a navegar trayectorias individuales desde
el informe administrativo.

### Dos entitlements, y por qué no uno

La forma obvia de filtrar la PII sería meter `upn_medico` dentro de
`v_paciente_medico`. No funciona: una paciente seguida por dos médicos daría dos
filas, `seudonimo_paciente` dejaría de ser única y Power BI no admite valores
repetidos en el lado *uno* de una relación. La dimensión acabaría en una relación
muchos a muchos con el embarazo, que es justo la ambigüedad que hay que evitar.

Separando las dos funciones —un puente que lleva el UPN y una dimensión que lleva
la PII— la dimensión conserva una fila por paciente y el filtro de RLS sigue
actuando **directamente sobre la tabla identificable**, a un solo salto. Es el
mismo patrón que SCRUM-98 ya usa para el embarazo, aplicado dos veces.

### Propiedad de las vistas

Las siete nuevas pertenecen al **migrador**, igual que tres de las cuatro
anteriores. Ninguna necesita `fetalalert_rls_owner` porque **ninguna lee
`operacional`**: leen `analitico`, `privado` y `publicacion.v_entitlement_medico`,
que es la única que cruza al operacional y ya resolvía ese problema en SCRUM-98.

Eso tiene una consecuencia que era el objetivo del diseño:
`fetalalert_rls_owner` **sigue sin acceso a `privado.seudonimo_paciente`**, el
mapa que ata un seudónimo a una persona. Construir el entitlement de paciente
desde el operacional habría obligado a concedérselo; derivarlo de la vista
existente no.

Para que el migrador pueda leer `v_entitlement_medico` —cuya propiedad SCRUM-98
transfirió— la migración pide prestado el permiso: concede `USAGE` sobre
`publicacion` a `fetalalert_rls_owner`, asume ese rol con `SET LOCAL ROLE`,
concede `SELECT` a `SESSION_USER` y retira el `USAGE` en la misma transacción. Es
el mismo patrón que SCRUM-98 emplea con `CREATE` para transferir esa vista.

### Privilegios de la cuenta técnica

`fetalalert_powerbi` sigue sin `SUPERUSER`, sin `BYPASSRLS`, sin `CREATEROLE`,
sin `CREATEDB` y **sin `USAGE`** sobre `operacional`, `analitico`, `privado` ni
`seguridad`. Lo único que gana en SCRUM-99 es `SELECT` sobre las siete vistas
nuevas. No se concedió nada sobre ninguna tabla del modelo estrella.

---

## 13. Frontera de seguridad y riesgo residual

Esta sección existe para que no haya que deducirla. La limitación es real y es
defendible; esconderla no lo sería.

### 13.1 Qué protege cada capa

1. **PostgreSQL** protege la *frontera de publicación*: qué objetos puede nombrar
   la cuenta técnica. Sin `USAGE` sobre los esquemas internos, ninguna tabla del
   modelo estrella, del operacional o del mapa privado es alcanzable —ni hoy ni
   cuando alguien añada una tabla nueva y se olvide de revocarla.
2. **El rol técnico** `fetalalert_powerbi` no posee ningún objeto, no alcanza
   ningún rol privilegiado y solo tiene `SELECT` sobre las once vistas
   aprobadas.
3. **El RLS del dataset de Power BI** es lo que separa a un médico de otro,
   filtrando los dos puentes de entitlement por `USERPRINCIPALNAME()`.

### 13.2 Lo que la cuenta técnica puede ver

Todo lo publicado, incluida la PII de las 20 pacientes con seguimiento vigente.
Dicho con precisión, y sin matizarlo:

> En modo Import, PostgreSQL no aplica RLS individual por médico durante cada
> visualización. PostgreSQL protege la frontera de publicación y Power BI aplica
> el aislamiento entre médicos dentro del modelo semántico, mediante UPN.

Por eso `fetalalert_powerbi` es una **credencial de servicio**: no se entrega a
ningún médico, vive en la configuración del origen de datos del *workspace* y su
contraseña es un secreto de despliegue. Quien la tenga puede leer la superficie
publicada entera.

Dos consecuencias prácticas que se siguen de ahí:

- **El rol `Medico` no es opcional.** Un dataset publicado sin él enseña la
  superficie completa a cualquier visor. Los dos filtros DAX de §14.3 son parte
  de un único contrato de seguridad: aplicar solo uno **no** es una
  configuración válida.
- **ADMIN necesita un modelo aparte.** No por política, sino porque un modelo
  importado contiene físicamente sus datos: si la superficie médica estuviera en
  el PBIX administrativo, la PII estaría en ese archivo aunque ningún visual la
  mostrara.

### 13.3 Qué mejoraría DirectQuery con SSO

Con DirectQuery e inicio de sesión único, cada consulta del informe llegaría a
PostgreSQL con la identidad del médico, y el filtrado podría hacerse con
políticas RLS en la base en lugar de en el modelo. La PII dejaría de residir en
el archivo del informe y el aislamiento no dependería de una configuración de
Power BI.

**Queda expresamente fuera del alcance de SCRUM-99** y se documenta como
evolución para una implantación productiva: exige Power BI Service con
identidades institucionales, una pasarela de datos y un modelo de conexión
distinto del validado en SCRUM-98, y cambiaría el contrato de la cuenta técnica.
Para un prototipo de tesis en entorno controlado y con datos ficticios, la
combinación Import + RLS del modelo es adecuada y su límite queda declarado.

---

## 14. Cómo debe modelarse en Power BI

### 14.1 Dos modelos, no uno

```
FetalAlert_Medico   ->  las diez vistas clínicas
FetalAlert_Admin    ->  v_resumen_administrativo, y nada más
```

### 14.2 Relaciones del modelo médico

| # | Desde | Hacia | Columnas | Cardinalidad | Filtro cruzado | Filtro de seguridad |
|---|---|---|---|---|---|---|
| R1 | `v_entitlement_medico` | `v_embarazo_medico` | `seudonimo_embarazo` | muchos a uno | **Ambas** | **Ambas** |
| R2 | `v_entitlement_paciente_medico` | `v_paciente_medico` | `seudonimo_paciente` | muchos a uno | **Ambas** | **Ambas** |
| R3 | `v_paciente_medico` | `v_embarazo_medico` | `seudonimo_paciente` | uno a muchos | Simple | **Simple** |
| R4 | `v_embarazo_medico` | `v_lectura` | `seudonimo_embarazo` | uno a muchos | Simple | — |
| R5 | `v_tiempo_gestacional` | `v_lectura` | `semana_gestacion` | uno a muchos | Simple | — |
| R6 | `v_semaforo` | `v_lectura` | `codigo_nivel` -> `codigo_semaforo` | uno a muchos | Simple | — |
| R7 | `v_embarazo_medico` | `v_embarazo_factor_riesgo` | `seudonimo_embarazo` | uno a muchos | **Ambas** | Simple |
| R8 | `v_factor_riesgo` | `v_embarazo_factor_riesgo` | `clave_factor` | uno a muchos | Simple | — |

> ⚠️ **R3 no puede ser bidireccional, y es la salvaguarda del multiembarazo.**
> Si la seguridad viajara de la paciente hacia sus embarazos, ver a una paciente
> arrastraría **todos** sus episodios —incluido uno que el médico no sigue—. El
> diseño depende de que los dos entitlements se apliquen por separado y su
> intersección sea el universo visible: la paciente es identificable porque hay
> un embarazo autorizado suyo, y el episodio no autorizado sigue sin aparecer.

> **R1 y R2 sí necesitan «aplicar filtro de seguridad en ambas direcciones»**
> (`securityFilteringBehavior: bothDirections`), no solo el filtro cruzado. Es el
> punto en el que un modelo que parece correcto no aísla nada; la explicación
> larga está en §8.2 y vale igual para R2.

> **R7 es bidireccional solo en el filtro cruzado**, para que seleccionar un
> factor de riesgo filtre embarazos. La seguridad se queda en simple: el puente
> ya queda filtrado por propagación desde el embarazo.

**Relación que no debe crearse:** `v_lectura` publica también
`seudonimo_paciente`. Relacionarla con `v_paciente_medico` crearía un segundo
camino paciente->lectura además de paciente->embarazo->lectura, y Power BI lo
rechazaría por ambiguo o lo desactivaría en silencio. Esa columna se deja **sin
relacionar y oculta**.

**Columnas a ocultar en `v_lectura`** por redundar con las dimensiones:
`trimestre`, `prioridad_semaforo`, `provincia` y `seudonimo_paciente`.

### 14.3 El rol de RLS: dos filtros, un solo contrato

```dax
v_entitlement_medico:           [upn_medico] = LOWER( USERPRINCIPALNAME() )
v_entitlement_paciente_medico:  [upn_medico] = LOWER( USERPRINCIPALNAME() )
```

Los dos, en el mismo rol `Medico`. Aplicar solo el primero dejaría la dimensión
de paciente —la que lleva la PII— sin filtrar.

### 14.4 Identificar a la paciente en el informe

El segmentador debe usar **`paciente_display`**, que PostgreSQL compone como
`nombre_completo — cedula`. Se construye en SQL y no en Power Query ni en DAX
porque así se audita leyendo la vista y no depende de configuración del informe.

`cedula` se publica además como columna propia, porque buscar por cédula es un
requisito. **El nombre no es clave**: las relaciones usan `seudonimo_paciente`.

La **edad** es la excepción: se calcula en DAX desde `fecha_nac`, porque una edad
almacenada envejece mal entre refrescos.

---

## 15. Compatibilidad con el informe existente de SCRUM-73

El tablero médico ya está parcialmente construido sobre la superficie de
SCRUM-98. SCRUM-99 se diseñó para **extender ese modelo, no para obligar a
rehacerlo**.

### 15.1 Qué no cambia

`v_entitlement_medico`, `v_lectura` y `v_resumen_administrativo` no cambian:
ni una columna, ni un tipo, ni un nombre. Las tablas del modelo que las usan se
refrescan y siguen funcionando.

### 15.2 `v_embarazo_medico` es un superset de `v_embarazo`

Publica **las doce columnas de `v_embarazo` con el mismo nombre y el mismo
tipo**, y añade las nuevas al lado. Por eso conviven dos representaciones de la
misma fecha: `mes_inicio` es la que el informe ya usa y `fecha_inicio` la exacta
que el seguimiento necesita. En esta vista el mes no protege nada —lleva el
episodio completo y se une a una vista con cédula—, así que se conserva por
compatibilidad, no por privacidad.

Dos pruebas lo fijan como contrato: una comprueba que no falta ninguna columna
ni cambia ningún tipo, y otra que las heredadas **valen lo mismo** en las dos
vistas, porque se recalculan en la nueva en vez de leerse de la vieja.

### 15.3 Migración esperada del informe

Sobre una **copia** del PBIX:

1. Cambiar el origen de la tabla `v_embarazo` a `publicacion.v_embarazo_medico`,
   **conservando el nombre de la tabla en el modelo**. Visuales, medidas,
   formato, páginas, marcadores, segmentadores y relaciones existentes se
   mantienen.
2. Refrescar `v_entitlement_medico`, `v_lectura` y `v_resumen_administrativo`.
3. Importar las seis vistas nuevas.
4. Crear las relaciones R2 a R8 de §14.2.
5. Añadir el segundo filtro DAX al rol `Medico`.
6. Añadir el segmentador de paciente sobre `paciente_display`.
7. Validar con dos identidades de médico distintas.

El superset hace que el paso 1 no requiera remapear ningún campo. La única
salvedad conocida es de alcance, no de esquema: `v_embarazo` publica el universo
seudonimizado completo y `v_embarazo_medico` solo los episodios con seguimiento
vigente, de modo que **los totales de un visual sin filtrar bajarán**. Eso no es
una incompatibilidad: es el aislamiento haciendo su trabajo, y hasta ahora lo
aplicaba únicamente el RLS del modelo.
