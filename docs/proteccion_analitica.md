# Protección analítica y publicación para Power BI (SCRUM-98)

Este documento describe la capa que expone datos prenatales a Power BI: qué se
publica, con qué identidad se consulta, cómo se aplica el aislamiento por médico
y qué queda pendiente de configurar a mano.

Todos los datos del proyecto son **simulados y completamente ficticios**. No hay
ni habrá datos de pacientes reales.

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
fecha_asignacion <= CURRENT_DATE
fecha_fin IS NULL OR fecha_fin >= CURRENT_DATE
```

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
  `f.fecha_hora::date`: conserva el **día calendario** y elimina hora, minuto y
  segundo. **No está generalizada al mes**, y no debe describirse así. El día se
  conserva porque es funcionalmente necesario para:

  - el seguimiento longitudinal del episodio;
  - el análisis de tendencias entre lecturas;
  - la medición de adherencia al monitoreo;
  - la secuencia temporal de los monitoreos.

  Truncarla al mes haría indistinguibles todas las lecturas de un mismo mes y
  vaciaría esos cuatro análisis. Lo que sí se retira es la hora: ningún indicador
  de esta capa la necesita, y es la parte que más enlaza una lectura con un
  momento concreto de la vida de alguien.

  La semana gestacional se conserva en las dos vistas porque es la variable
  clínica central.
- **Edad**: en tramos de cinco años, calculada al inicio del embarazo.
- **Ubicación**: provincia. El distrito se queda fuera.

### Lo que sí se conserva

Semana gestacional, trimestre, código de semáforo y prioridad, `hr_valor`,
`spo2_valor`, `mov_valor`, los tres estados derivados, fecha de captura —al
día, sin hora— y
`secuencia_sesion` — el número de la sesión dentro del episodio, que permite
medir adherencia sin publicar una clave de la base.

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
