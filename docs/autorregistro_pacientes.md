# Autorregistro de pacientes (SCRUM-110)

Una paciente crea su propia cuenta desde «Crear cuenta», con un formulario vacío. Un médico puede ayudarla en persona a llenarlo, pero no inicia sesión, no aprueba el registro y no figura como autor. Al enviarlo se guardan en PostgreSQL el perfil, el celular, una cuenta PACIENTE **activa**, el vínculo entre ambos y las dos aceptaciones. La paciente puede iniciar sesión de inmediato con el acceso de siempre, y su portal muestra que todavía no tiene embarazos ni mediciones.

Todos los datos son ficticios y simulados.

Estado: **R1 (backend) y R2 (formulario en el portal) implementadas**, sin commit. Falta R3 (validación completa y tres ciclos de registro, inicio de sesión y restauración). Siguen pendientes las pruebas de integración que solo corren en CI (§6).

## 1. API

| Método y ruta | Sesión | Qué hace |
|---|---|---|
| `GET /api/v1/registro/documentos` | no | Devuelve la política de privacidad y los términos vigentes: documento, versión, título, texto y SHA-256. |
| `POST /api/v1/registro/pacientes` | no | Crea perfil, celular, cuenta PACIENTE activa, vínculo y aceptaciones. Responde 201 después del commit. |

### Cuerpo del registro

| Campo | Destino | Obligatorio | Validación |
|---|---|---|---|
| `primer_nombre` | `paciente.primer_nombre` | sí | Sin espacios exteriores; no vacío; ≤ 60 (columna). |
| `segundo_nombre` | `paciente.segundo_nombre` | no | Vacío o solo espacios → NULL; ≤ 60. |
| `apellido_paterno` | `paciente.apellido_paterno` | sí | Como `primer_nombre`. |
| `apellido_materno` | `paciente.apellido_materno` | no | Como `segundo_nombre`. |
| `cedula` | `paciente.cedula` | sí | Sin espacios exteriores; no vacía; ≤ 20; única. Sin formato impuesto. |
| `fecha_nac` | `paciente.fecha_nac` | sí | `AAAA-MM-DD`, una fecha que exista y no posterior a hoy en Panamá. Sin rango de edad. |
| `email` | `paciente.email_pac` **y** `usuario.email` | sí | El correo de acceso que asigna el personal de salud. El mismo tipo que el inicio de sesión (sin espacios, en minúsculas, ≤ 120) y, además, el formato `pacienteNN@example.com`: «paciente», al menos dos dígitos y el dominio exacto `example.com`. Único en las dos tablas. |
| `celular` | `telefono_paciente` (CELULAR, principal) | sí | Sin espacios exteriores; no vacío; ≤ 120. Sin formato impuesto. |
| `password` | `usuario.password_hash` (Argon2id) | sí | De 1 a 512 caracteres, como el inicio de sesión. **No se recorta ni se transforma.** |
| `confirmacion_password` | — (no se guarda) | sí | Idéntica a `password`, carácter por carácter. |
| `acepta_politica_privacidad` | `aceptacion_documento` | sí | Solo el booleano JSON `true`. |
| `version_politica_privacidad` | — | sí | Debe ser la versión vigente. |
| `acepta_terminos_condiciones` | `aceptacion_documento` | sí | Solo el booleano JSON `true`. |
| `version_terminos_condiciones` | — | sí | Debe ser la versión vigente. |

Cualquier otro campo (`rol`, `activo`, `id_paciente`, `id_usuario`, una clínica…) se responde con 422: el cliente no elige rol ni estado y no puede vincularse a un perfil existente.

### Respuestas

| Código | Cuándo | Cuerpo |
|---|---|---|
| 201 | Todo guardado y confirmado. | `id_usuario`, `id_paciente`, `rol` (`PACIENTE`), `activo` (`true`). |
| 409 | La cédula o el correo ya están registrados, o una operación concurrente ganó la carrera. | Mensaje genérico: no dice cuál de los dos valores ya existía. |
| 422 | El cuerpo no cumple el contrato. | El diagnóstico sin los valores recibidos: la ruta está entre las que llevan credenciales. |
| 429 | Límite de solicitudes alcanzado. | Mensaje y cabecera `Retry-After`. |
| 500 | Cualquier otro fallo. La transacción se revirtió. | Mensaje genérico. |

Ninguna respuesta lleva el correo, el nombre, la cédula, el celular, la contraseña ni su resumen. Los logs registran solo la clase y el SQLSTATE de un error.

## 2. Cómo se protege una escritura sin sesión

- **El proceso web no recibe privilegios sobre las tablas.** `fetalalert_api` solo puede ejecutar `seguridad.autorregistrar_paciente`. Sigue sin poder insertar, leer ni modificar `paciente`, `telefono_paciente` o `aceptacion_documento`, ni vincular cuentas por su cuenta.
- **La función fija la forma.** Es SECURITY DEFINER, pertenece al rol NOLOGIN `fetalalert_provision_owner` y tiene `search_path` fijo. El rol PACIENTE y el estado activo están escritos en ella. Vuelve a comprobar los datos obligatorios, la fecha y que la contraseña llegue como resumen Argon2id.
- **Una transacción.** La función y la auditoría se confirman juntas o no se confirma nada. El trigger diferido de SCRUM-97 sigue validando la coherencia entre rol y vínculo en el commit.
- **Duplicados y carreras.** Los deciden las restricciones UNIQUE de PostgreSQL. No se consulta antes «¿existe esta cédula?», porque sería un oráculo abierto a cualquiera.
- **Límite de solicitudes.** Ventana deslizante en la memoria del proceso: 10 por origen y 30 en total cada 15 minutos, por omisión. Se configura con `REGISTRO_MAXIMO_POR_ORIGEN`, `REGISTRO_MAXIMO_GLOBAL` y `REGISTRO_VENTANA_SEGUNDOS`, aunque `docker-compose.yml` no las pasa al contenedor de la API: ahí rigen los valores por omisión. Cuenta antes de validar el cuerpo y de calcular el hash. Detrás del adaptador local del portal todas las solicitudes llegan desde la misma dirección, así que para el portal el límite es en la práctica global. Un reinicio de la API lo vacía.
- **Auditoría.** `CUENTA_PACIENTE_AUTORREGISTRADA`, con la cuenta recién creada como actor. No se atribuye a ningún médico ni administrador.

ADMIN conserva `/api/v1/cuentas` tal cual. MEDICO no gana ningún permiso.

### El límite en los ensayos

El adaptador del portal reenvía cada registro a la API desde su propia dirección, así que **todas las personas que usen el portal comparten un mismo cupo**: 10 solicitudes cada 15 minutos (el límite por origen se agota antes que el global de 30). Cuentan todas las solicitudes que llegan a la API, también las rechazadas con 409 o 422. Las que el formulario de R2 detenga en el navegador antes de enviarse no cuentan.

Qué supone para la demostración:

- Un ciclo normal (un registro correcto, quizá un intento con un dato repetido para mostrar el 409) consume entre 1 y 3. Tres ciclos seguidos caben con holgura.
- Varias personas practicando a la vez, o muchos intentos con errores, pueden agotar el cupo. La API responde 429 con `Retry-After` y la interfaz debe mostrar que hay que esperar. No se crea nada y no se pierde nada.
- El cupo vive en la memoria del proceso de la API. La restauración del PR #24 detiene y vuelve a arrancar la API, así que **cada ciclo de restauración empieza con el cupo vacío**.
- Si en un ensayo se agota y no se quiere esperar, se puede reiniciar solo la API (`docker compose restart api`, o las opciones 3 y 4 de Demo Control). Eso vacía el cupo de esa ejecución; el límite sigue activo.

Lo que no se hace: desactivarlo, ni subirlo para todos los entornos. Si hiciera falta otro valor para un ensayo concreto, habría que pasar `REGISTRO_MAXIMO_POR_ORIGEN` al contenedor de la API, lo que requiere cambiar `docker-compose.yml`. Queda como decisión aparte, no incluida en esta fase.

Una mejora posible para R2: que el adaptador transmita la dirección real del navegador. No se hace porque la API no confía en `X-Forwarded-For` (véase la auditoría): hacerlo exigiría definir un proxy de confianza.

## 3. Textos y aceptaciones

Los textos están en `backend/app/legal/` (`politica_privacidad.txt` y `terminos_condiciones.txt`, versión `2026-11-v3`). Son `.txt` porque `backend/.dockerignore` excluye `*.md` de la imagen de la API. La interfaz los mostrará tal como los devuelve `GET /api/v1/registro/documentos`.

Cada aceptación guarda la cuenta, el documento, la versión, el SHA-256 del texto que sirve el servidor y la fecha y hora de PostgreSQL. La versión y la huella salen del servidor, nunca de la solicitud. La huella se calcula con finales de línea `\n`, para que no dependa de cómo Git descargó el archivo.

**Cambiar un texto obliga a cambiar su versión.** `test_registro.py` fija la huella de cada versión.

**Tono y alcance.** Los textos están escritos como los de un servicio en operación, para la demostración: la clínica asigna el correo de acceso, el personal de salud acompaña el registro, y se citan la Ley 81 de 2019, el consentimiento para datos de salud y los derechos ARCO. Solo describen lo que el sistema hace: no mencionan cifrado y dicen que la cuenta no se elimina desde el portal. Cada texto termina con «Alcance de esta versión», que aclara que FetalAlert es un prototipo académico con datos simulados y no un servicio clínico en operación. `v1` y `v2` no se publicaron.

**Formato del correo.** Solo el registro exige `pacienteNN@example.com` (`PATRON_EMAIL_REGISTRO` en `app/schemas/registro.py`, y la misma regla en `registro.js`). Se comprueba sobre la forma canónica, así que `Paciente31@Example.com` se acepta y se guarda en minúsculas. El inicio de sesión y las cuentas existentes, incluidas las de médicos y administradores, no cambian.

## 4. Interfaz: «Crear cuenta» en el portal (R2)

### Recorrido

1. En el inicio de sesión, «¿Todavía no tienes cuenta? **Crear cuenta**».
2. El formulario empieza vacío y se llena con el acompañamiento del personal de salud. Tiene cuatro grupos: tus datos, contacto (correo de acceso asignado y celular, los dos obligatorios), contraseña y confirmación, y dos casillas **desmarcadas**: una para la política de privacidad y otra para los términos.
3. Cada documento se abre en un diálogo con el texto y la versión que sirve el servidor. Leerlo no marca la casilla: aceptar es un acto aparte.
4. «Crear cuenta» envía el formulario una vez. La confirmación («Tu cuenta está lista») solo aparece cuando el servidor respondió 201.
5. «Ir a iniciar sesión» lleva al inicio de sesión de siempre, con el correo ya escrito. La paciente escribe su contraseña y entra a un portal sin embarazos ni mediciones.

### Piezas

| Pieza | Qué hace |
|---|---|
| `frontend/gestante/registro.js` | Formulario, diálogo, envío y mensajes. Archivo propio, cargado con `defer` en `<head>`. `app.js` **no cambia**. |
| `frontend/gestante/index.html` | El formulario y el desenlace son paneles dentro de `vista-login`: `app.js` los muestra y los oculta con esa vista. Un `<dialog>` para los documentos. |
| `frontend/gestante/styles.css` | Un bloque propio con las mismas piezas del portal: dos columnas desde 641 px y una en el teléfono. |
| `GET /adaptador/registro/documentos` | Pide a la API los documentos vigentes, los valida con el contrato de R1 y entrega documento, versión, título y texto. |
| `POST /adaptador/registro` | Valida con `AutorregistroEntrada` y reenvía **una vez** a `/api/v1/registro/pacientes`. No abre sesión local ni guarda nada. |
| `GET /registro.js` | Sirve el archivo, como los otros de la interfaz. |

El adaptador valida antes de reenviar para no gastar en vano el cupo compartido del portal (§2). El servidor vuelve a validarlo todo y es el que decide.

### Desenlaces

| Adaptador | Cuándo | Qué ve la paciente |
|---|---|---|
| 201 | La API confirmó (201). | «Tu cuenta está lista» e «Ir a iniciar sesión». El formulario se vacía. |
| 409 | Cédula o correo ya registrados. | «Ya existe una cuenta con esa cédula o ese correo…», sin decir cuál. |
| 422 | Un dato no pasó la validación. | Los campos marcados con «Revisa este dato.» Si fue la versión de un documento, las casillas se desmarcan y los textos se recargan. |
| 429 | Límite de solicitudes. | Cuánto esperar (de `Retry-After`). El botón queda bloqueado ese tiempo. No se reintenta solo. |
| 503 | **La petición no salió**: fallo de conexión, conexión agotada o pool sin conexión libre. | «Tu registro no se envió». |
| 504, o se cae la red entre navegador y adaptador | **La petición pudo llegar** y se perdió la respuesta. | «No sabemos si tu cuenta se creó». Primero se propone iniciar sesión y, si no se puede entrar, volver al formulario. No se reenvía nada. |
| 502 | Cualquier otra respuesta. | Mensaje genérico. |

Ningún texto viene del servidor. Del 422 solo pasan los nombres de los campos que el formulario conoce.

### Lo que no se guarda

- Ni `localStorage`, ni `sessionStorage`, ni IndexedDB, ni cookies escritas por JavaScript.
- Las contraseñas se vacían en cuanto se envía el formulario, salga como salga.
- El formulario entero se vacía al volver al inicio de sesión, tras un 201 y cuando se abre el portal.
- Los campos llevan `autocomplete="off"`. En un equipo compartido, el navegador puede ofrecer igualmente guardar la contraseña; eso depende de cada navegador y no se puede impedir desde la página.

### Accesibilidad

- Etiquetas `<label for>`, grupos con `<fieldset>`/`<legend>` y errores enlazados con `aria-describedby` y `aria-invalid`.
- El resumen de errores es `role="alert"` y el foco va al primer campo con error.
- El diálogo es modal (`showModal`). El foco va al texto, que es desplazable con el teclado; Escape lo cierra y el foco vuelve al botón que lo abrió.
- Mientras se envía, el botón cambia de texto y el formulario lleva `aria-busy`.

### Fecha de nacimiento

`<input type="date">` con `max` igual al día de hoy en Panamá. El aspecto del selector depende del idioma del navegador: con el navegador en inglés se ve `mm/dd/aaaa`. El valor enviado es siempre `AAAA-MM-DD`.

### Convivencia con el PR #22

El PR #22 («Guardar mi resumen») modifica `app.js`, `index.html`, `styles.css`, `rutas.py` y `test_gestante_rutas.py`. R2 no toca `app.js` ni `test_gestante_rutas.py`, y en los otros tres archivos escribe en zonas distintas: `<head>`, la vista de inicio de sesión y «Acerca de» en el HTML; tras el diálogo de cierre en el CSS; tras la ruta de cierre de sesión en el adaptador. La comprobación de fusión está en `docs/validacion_scrum110_r2/INFORME.md`.

## 5. Despliegue en el entorno de la demostración

Hasta que se apruebe, no se ha aplicado en ninguna base.

1. Detener la API.
2. Con la credencial de mantenimiento: `cd backend; alembic upgrade head`. El rol `fetalalert_provision_owner` ya existe desde SCRUM-98; el bootstrap de roles no cambia.
3. Reconstruir la imagen y arrancar la API: `docker compose up -d --build api`.

**Consecuencia para el baseline común (PR #24).** Un respaldo tomado antes de la migración no tiene la tabla ni la función. Restaurarlo con el código nuevo dejaría la API sin poder registrar. El baseline común se vuelve a tomar después de aplicar la migración, y Demo Control seguirá informando FAIL en «Migraciones» hasta que la base esté en el head.

## 6. Pruebas

| Archivo | Servidor | Qué demuestra |
|---|---|---|
| `frontend/gestante/pruebas/registro.comportamiento.test.js` | no; **respuestas simuladas** del adaptador | Ejecuta `registro.js` sobre un DOM mínimo, solo o con `app.js`. Cubre: estado inicial vacío, carga de documentos, diálogo (texto como nodos, foco), validación sin envío, cuerpo exacto, doble envío, 201, 409, 422, 429, 503, respuesta perdida sin reenvío, vaciado del formulario y que el login no se ve afectado. |
| `backend/tests/test_gestante_registro.py` | no; `httpx.MockTransport` y un doble del servidor central | El cliente distingue «no salió» de «no se sabe» por la excepción de httpx y nunca reintenta. Además: los 422 solo dejan pasar nombres de campo, el `Retry-After` se acota, los documentos se validan, las rutas del adaptador traducen cada desenlace sin datos de la paciente, el formulario inválido no gasta el cupo y el 201 no abre sesión. |
| `backend/tests/test_gestante_frontend.py` (ampliado) | no | `registro.js` queda dentro de las salvaguardas de todo el frontend. Los `maxlength` coinciden con las columnas, las casillas empiezan desmarcadas y no hay campos de rol ni de estado. |
| `backend/tests/test_registro.py` | no | Campos y validaciones, normalización, textos y huellas, servicio (hash exacto, versión y huella del servidor), clasificación de rechazos, router (commit único, rollback, auditoría, 409/422/429/500 sin datos sensibles), límite de solicitudes (ventana, origen, global, hilos) y lo que la migración concede y a quién. |
| `backend/tests/test_registro_postgresql.py` | sí, como `fetalalert_api` | Registro real con todas sus filas, inicio de sesión inmediato y lista de embarazos vacía, duplicados, una carrera de cuatro solicitudes, rollback a mitad, ausencia de privilegios directos de la API, dueño y permisos de la función, y rechazos propios de la función. |
| `backend/tests/test_migration_postgresql.py` | sí | El escalón baja hasta SCRUM-98 y vuelve: tabla, función, política e INSERT del dueño desaparecen y vuelven. |

La suite de PostgreSQL se omite salvo que `SCRUM110_TEST_DATABASE_URL` apunte, como `fetalalert_api`, a una base **desechable** (la efímera de CI o un nombre que empiece por `scrum110_`), migrada y con el dataset cargado. Rechaza cualquier otro nombre, incluidas las bases de la demostración. CI la ejecuta en su propio paso (`pytest-scrum110.xml`, vigilado por el guardián de omisiones).

La limpieza por identificadores de esa suite es una herramienta de prueba: el entorno de la demostración no se limpia así, sino que se restaura con el mecanismo del PR #24.

**Recorrido contra el backend real (R2).** Chrome headless recorrió el navegador, el adaptador real, un proxy de prueba, la API real y PostgreSQL en una base desechable. Hizo dos pasadas, una de ellas sobre el código final. Las capturas de escritorio y móvil, los registros y las comprobaciones en la base están en `docs/validacion_scrum110_r2/`.

**Pendiente para CI, sin cambios desde R1:** `test_migration_postgresql.py` (exige `scrum52_validacion_tmp`), `test_bootstrap_postgresql.py` (crea y borra roles del servidor) y las demás suites de PostgreSQL con guardián propio. No se han ejecutado en local y no se declaran aprobadas.
