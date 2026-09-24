/* =========================================================================
   FetalAlert — Interfaz de la gestante

   Este archivo se encarga de la interaccion y de pedir datos al adaptador
   local. No decide nada clinico.

   Tres reglas gobiernan todo lo que hay aqui:

   1. NO se clasifica. El semaforo se pinta con el nivel que entrega la
      fuente autorizada. No hay umbrales de frecuencia cardiaca, de SpO2 ni
      de movimientos en este archivo, y no debe haberlos: duplicarlos aqui
      crearia una segunda version de las reglas clinicas, libre de
      separarse de la primera.

   2. NO se guardan credenciales. La contrasena viaja una sola vez, en el
      envio del formulario, y el campo se vacia despues.

      El token del servidor central nunca llega a este archivo y nunca sale
      del adaptador: vive solo en su memoria. Lo unico que recibe el
      navegador es un identificador opaco de la sesion local, en una cookie
      HttpOnly que JavaScript no puede leer y que no contiene ni el token ni
      dato alguno de la cuenta. Son dos cosas distintas y no deben
      confundirse.

      Tampoco se usa ningun almacenamiento del navegador, ni por sesion ni
      persistente.

   3. La ausencia de un dato NO es cero. Un valor nulo o ausente se muestra
      como "—" o "No disponible". La interfaz anterior convertia un conteo
      de movimientos ausente en 0, y ese es precisamente el error que
      `texto()` existe para no repetir.

   Todo lo que se muestra procede de un sistema academico con datos
   simulados.
   ========================================================================= */

(function () {
  'use strict';

  // =======================================================================
  // Constantes
  // =======================================================================

  // Rutas del adaptador local. Son las unicas direcciones que este archivo
  // conoce: no habla con el servidor central ni con ninguna base de datos.
  const API = {
    sesion: '/adaptador/sesion',
    iniciarSesion: '/adaptador/iniciar-sesion',
    cerrarSesion: '/adaptador/cerrar-sesion',
    conectividad: '/adaptador/conectividad',
    estadoLocal: '/adaptador/estado-local',
    embarazos: '/adaptador/embarazos',
    monitoreo: function (idEmbarazo) {
      // El identificador procede siempre de la lista de episodios que devolvio
      // el adaptador. Esta funcion no lo fabrica ni lo adivina.
      return '/adaptador/embarazos/' + encodeURIComponent(idEmbarazo) + '/monitoreo';
    },
    sesionesSimuladas: function (idEmbarazo) {
      return '/adaptador/embarazos/' + encodeURIComponent(idEmbarazo) + '/sesiones-simuladas';
    },
    movimientosEstado: '/adaptador/movimientos/estado',
    movimientosSincronizar: '/adaptador/movimientos/sincronizar'
  };

  // El unico tipo de sesion simulada que esta pantalla ofrece. La eleccion es
  // fija porque el control vive bajo la tarjeta de «Actividad Fetal»: no hay
  // un selector que la paciente pueda manipular para pedir otra cosa.
  const TIPO_SESION_SIMULADA = 'MOVIMIENTOS_FETALES';

  // Como se interpreta una respuesta del adaptador. La clasificacion vive en un
  // solo sitio --`clasificar()`-- y las vistas leen el resultado; repetir
  // comprobaciones de codigos por cada pantalla es como se acaba teniendo cinco
  // criterios distintos para el mismo 404.
  const CLASE = {
    DATOS: 'datos',                    // 200 con disponible: true
    NO_DISPONIBLE: 'no_disponible',    // 200 con disponible: false + motivo
    SESION_LOCAL_INVALIDA: 'sesion_local_invalida', // 401 del adaptador
    ACCESO_DENEGADO: 'acceso_denegado',            // 403
    RECURSO_NO_DISPONIBLE: 'recurso_no_disponible',// 404
    ERROR_UPSTREAM: 'error_upstream',              // 502 y demas
    ADAPTADOR_CAIDO: 'adaptador_caido'             // ni siquiera respondio
  };

  // Los dos motivos que el adaptador envia con 200 y disponible: false.
  const MOTIVO = {
    REAUTENTICACION: 'reautenticacion_requerida',
    SIN_CONEXION: 'sin_conexion'
  };

  // Cada cuanto se refrescan conectividad y estado local, en milisegundos.
  // No es un sondeo de sensores: son dos lecturas baratas del adaptador.
  const INTERVALO_REFRESCO_MS = 20000;

  const SIN_DATO = '—';
  const NO_DISPONIBLE = 'No disponible';
  const SIN_CLASIFICACION = 'Sin clasificación disponible';

  const MENSAJE_ERROR_GENERICO =
    'No se pudo iniciar sesión. Revisa el correo y la contraseña.';
  const MENSAJE_SIN_CONEXION =
    'No hay conexión con el servidor. Inténtalo de nuevo cuando vuelva.';

  // Textos de los estados en que la sesion local sigue viva y no hay datos
  // centrales. Ninguno provoca cierre de sesion.
  const AVISOS = {};
  AVISOS[MOTIVO.REAUTENTICACION] =
    'Tu sesión en este dispositivo sigue activa, pero para ver tu información ' +
    'clínica hace falta iniciar sesión de nuevo cuando haya conexión.';
  AVISOS[MOTIVO.SIN_CONEXION] =
    'Sin conexión con el servidor. Tu información clínica se mostrará cuando ' +
    'vuelva la conexión.';
  AVISOS[CLASE.ACCESO_DENEGADO] =
    'Tu cuenta no puede consultar esta información.';
  AVISOS[CLASE.RECURSO_NO_DISPONIBLE] =
    'Ese episodio no está disponible.';
  AVISOS[CLASE.ERROR_UPSTREAM] =
    'El servidor no pudo responder. Inténtalo de nuevo más tarde.';
  AVISOS[CLASE.ADAPTADOR_CAIDO] =
    'No se pudo contactar con la aplicación de este dispositivo.';

  // Niveles que el semaforo sabe pintar. La clave es el codigo que entrega
  // la fuente autorizada; el valor, la clase CSS. Un nivel desconocido no se
  // adivina: cae en el estado neutro.
  const CLASES_DE_SEMAFORO = {
    OK: 'ok',
    WARNING: 'warning',
    ERROR: 'error'
  };

  // =======================================================================
  // Referencias al DOM
  // =======================================================================

  const ui = {
    menu: document.getElementById('main-menu'),
    conexion: document.getElementById('connection-status'),
    botonCerrarSesion: document.getElementById('btn-cerrar-sesion'),

    formLogin: document.getElementById('form-login'),
    email: document.getElementById('login-email'),
    password: document.getElementById('login-password'),
    botonLogin: document.getElementById('btn-login'),
    mensajeLogin: document.getElementById('login-mensaje'),

    saludo: document.getElementById('saludo'),

    embarazoEstado: document.getElementById('embarazo-estado'),
    embarazoSemana: document.getElementById('embarazo-semana'),
    embarazoAnteriores: document.getElementById('embarazo-anteriores'),

    hrValor: document.getElementById('hr-value'),
    hrEstado: document.getElementById('hr-status'),
    spo2Valor: document.getElementById('spo2-value'),
    spo2Estado: document.getElementById('spo2-status'),
    movValor: document.getElementById('movs-value'),
    movEstado: document.getElementById('mov-status'),

    semaforo: document.getElementById('semaforo'),
    semaforoMensaje: document.getElementById('semaforo-mensaje'),

    outboxPendientes: document.getElementById('outbox-pendientes'),
    outboxEnviados: document.getElementById('outbox-enviados'),
    outboxReintentables: document.getElementById('outbox-reintentables'),
    outboxRevision: document.getElementById('outbox-revision'),
    notaEstadoLocal: document.getElementById('nota-estado-local'),

    botonRegistrarMovimiento: document.getElementById('btn-registrar-movimientos'),
    notaMovimientos: document.getElementById('nota-movimientos'),
    movPendientes: document.getElementById('movimientos-pendientes'),
    movEnviados: document.getElementById('movimientos-enviados'),
    movReintentables: document.getElementById('movimientos-reintentables'),
    movRevision: document.getElementById('movimientos-revision'),
    notaMovimientosEstado: document.getElementById('nota-movimientos-estado'),
    botonSincronizarMovimientos: document.getElementById('btn-sincronizar-movimientos'),

    ultimaLectura: document.getElementById('last-update'),
    ultimaSincronizacion: document.getElementById('last-sync'),

    notaEmbarazo: document.getElementById('nota-embarazo'),
    selectorEmbarazo: document.getElementById('selector-embarazo'),
    historialLista: document.getElementById('historial-lista'),
    historialVacio: document.getElementById('historial-vacio')
  };

  const VISTAS = ['login', 'inicio', 'historial', 'manual', 'acerca'];

  let temporizadorRefresco = null;

  // Episodios que el adaptador entrego en la ultima consulta, y cual se esta
  // mirando. `seleccionado` siempre es un id que vino de `/adaptador/embarazos`:
  // no se construye a partir de nada escrito en la pagina.
  let episodios = null;
  let seleccionado = null;

  // =======================================================================
  // Utilidades de presentacion
  // =======================================================================

  /**
   * Texto que se muestra para un valor que puede no existir.
   *
   * El cero es un valor legitimo y se muestra como cero. Lo que se sustituye
   * es la ausencia: null, undefined y la cadena vacia. Esa distincion es el
   * motivo de que esta funcion exista.
   */
  function texto(valor, alternativa) {
    const reemplazo = alternativa === undefined ? SIN_DATO : alternativa;
    if (valor === null || valor === undefined || valor === '') {
      return reemplazo;
    }
    return String(valor);
  }

  /** Fecha y hora legibles, o el marcador de ausencia. */
  function fechaLegible(valorIso) {
    if (valorIso === null || valorIso === undefined || valorIso === '') {
      return SIN_DATO;
    }
    const momento = new Date(valorIso);
    if (Number.isNaN(momento.getTime())) {
      return SIN_DATO;
    }
    return momento.toLocaleString();
  }

  /** Solo la fecha, para etiquetas donde la hora no aporta. */
  function fechaCortaLegible(valorIso) {
    if (valorIso === null || valorIso === undefined || valorIso === '') {
      return SIN_DATO;
    }
    const momento = new Date(valorIso);
    if (Number.isNaN(momento.getTime())) {
      return SIN_DATO;
    }
    return momento.toLocaleDateString();
  }

  /**
   * Pinta el semaforo con el nivel recibido. No lo calcula.
   *
   * `nivel` es el codigo que entrega la fuente autorizada. Cualquier valor
   * que no sea uno de los tres conocidos deja el semaforo en su estado
   * neutro, que es lo honesto: no se sabe.
   */
  function pintarSemaforo(nivel, mensaje) {
    const clase = CLASES_DE_SEMAFORO[nivel];
    ui.semaforo.className = clase ? 'alert ' + clase : 'alert waiting';
    ui.semaforoMensaje.textContent = texto(mensaje, SIN_CLASIFICACION);
  }

  /** Estado visual de la conexion con el servidor central. */
  function pintarConectividad(disponible) {
    if (disponible === true) {
      ui.conexion.textContent = 'Conectado';
      ui.conexion.className = 'connection-status status-connected';
    } else if (disponible === false) {
      ui.conexion.textContent = 'Sin conexión';
      ui.conexion.className = 'connection-status status-disconnected';
    } else {
      ui.conexion.textContent = 'Comprobando conexión…';
      ui.conexion.className = 'connection-status status-connecting';
    }
  }

  function mostrarMensajeLogin(mensaje) {
    if (!mensaje) {
      ui.mensajeLogin.hidden = true;
      ui.mensajeLogin.textContent = '';
      return;
    }
    ui.mensajeLogin.textContent = mensaje;
    ui.mensajeLogin.hidden = false;
  }

  // =======================================================================
  // Navegacion entre vistas
  // =======================================================================

  function mostrarVista(nombre) {
    const destino = VISTAS.indexOf(nombre) === -1 ? 'inicio' : nombre;

    VISTAS.forEach(function (vista) {
      const seccion = document.getElementById('vista-' + vista);
      if (seccion) {
        seccion.hidden = vista !== destino;
      }
    });

    const enlaces = ui.menu ? ui.menu.querySelectorAll('a[data-vista]') : [];
    Array.prototype.forEach.call(enlaces, function (enlace) {
      enlace.classList.toggle('activo', enlace.dataset.vista === destino);
    });

    if (destino === 'inicio') {
      refrescar();
    }
  }

  /** La aplicacion con sesion iniciada. */
  function mostrarPortal() {
    ui.menu.hidden = false;
    document.getElementById('vista-login').hidden = true;
    mostrarVista('inicio');
    iniciarRefrescoPeriodico();
  }

  /** La pantalla de inicio de sesion, con todo lo demas oculto. */
  function mostrarLogin() {
    detenerRefrescoPeriodico();
    ui.menu.hidden = true;
    VISTAS.forEach(function (vista) {
      const seccion = document.getElementById('vista-' + vista);
      if (seccion) {
        seccion.hidden = vista !== 'login';
      }
    });
    limpiarPanel();
  }

  /**
   * Devuelve el panel a su estado neutro.
   *
   * Se llama al cerrar sesion, para que ningun dato de una sesion quede
   * visible en la siguiente.
   */
  function limpiarPanel() {
    ui.saludo.textContent = 'Bienvenida';

    ui.embarazoEstado.textContent = NO_DISPONIBLE;
    ui.embarazoSemana.textContent = NO_DISPONIBLE;
    ui.embarazoAnteriores.textContent = NO_DISPONIBLE;

    ui.hrValor.textContent = SIN_DATO;
    ui.spo2Valor.textContent = SIN_DATO;
    ui.movValor.textContent = SIN_DATO;
    ui.hrEstado.textContent = 'Sin lectura';
    ui.spo2Estado.textContent = 'Sin lectura';
    ui.movEstado.textContent = 'Sin lectura';

    pintarSemaforo(null, null);

    ui.outboxPendientes.textContent = SIN_DATO;
    ui.outboxEnviados.textContent = SIN_DATO;
    ui.outboxReintentables.textContent = SIN_DATO;
    ui.outboxRevision.textContent = SIN_DATO;
    ui.notaEstadoLocal.hidden = true;

    ui.movPendientes.textContent = SIN_DATO;
    ui.movEnviados.textContent = SIN_DATO;
    ui.movReintentables.textContent = SIN_DATO;
    ui.movRevision.textContent = SIN_DATO;
    ui.notaMovimientosEstado.hidden = true;
    ui.botonRegistrarMovimiento.disabled = true;
    ui.notaMovimientos.textContent =
      'Selecciona un embarazo en «Mi historial» para registrar una sesión simulada.';

    ui.ultimaLectura.textContent = SIN_DATO;
    ui.ultimaSincronizacion.textContent = SIN_DATO;

    // Nada de un episodio puede sobrevivir a un cierre de sesión.
    episodios = null;
    seleccionado = null;
    if (ui.selectorEmbarazo) {
      ui.selectorEmbarazo.innerHTML = '';
      const vacio = document.createElement('option');
      vacio.value = '';
      vacio.textContent = NO_DISPONIBLE;
      ui.selectorEmbarazo.appendChild(vacio);
      ui.selectorEmbarazo.disabled = true;
    }
    if (ui.historialLista) {
      ui.historialLista.innerHTML = '';
    }
    if (ui.notaEmbarazo) {
      ui.notaEmbarazo.hidden = true;
    }
  }

  // =======================================================================
  // Peticiones al adaptador local
  // =======================================================================

  /**
   * Una peticion JSON al adaptador.
   *
   * `credentials: 'same-origin'` envia la cookie de sesion, que es HttpOnly
   * y por tanto invisible para este archivo. Un fallo de red no se propaga
   * como excepcion al llamador: se devuelve null y cada pantalla decide como
   * representarlo.
   */
  function pedir(ruta, opciones) {
    const configuracion = Object.assign(
      { credentials: 'same-origin', headers: { Accept: 'application/json' } },
      opciones || {}
    );

    return fetch(ruta, configuracion)
      .then(function (respuesta) {
        return respuesta
          .json()
          .catch(function () {
            return null;
          })
          .then(function (cuerpo) {
            return { ok: respuesta.ok, estado: respuesta.status, cuerpo: cuerpo };
          });
      })
      .catch(function () {
        return { ok: false, estado: 0, cuerpo: null };
      });
  }

  /**
   * Traduce una respuesta del adaptador a una de las clases conocidas.
   *
   * Es el unico sitio donde se miran codigos HTTP, y eso es deliberado: sin un
   * punto central, cada vista acabaria decidiendo por su cuenta que hacer ante
   * un 404 y tarde o temprano alguna cerraria la sesion por un error que no
   * tiene nada que ver con la sesion.
   *
   * **Solo `SESION_LOCAL_INVALIDA` devuelve al login.** El adaptador emite 401
   * unicamente cuando la sesion local no vale --inexistente, cerrada o
   * expirada--. Un 403, un 404 o un 502 hablan del servidor central o del
   * recurso pedido, y ninguno de ellos es motivo para expulsar a una paciente
   * cuya ventana local sigue vigente.
   */
  function clasificar(resultado) {
    if (resultado.estado === 0) {
      return { clase: CLASE.ADAPTADOR_CAIDO };
    }
    if (resultado.estado === 401) {
      return { clase: CLASE.SESION_LOCAL_INVALIDA };
    }
    if (resultado.estado === 403) {
      return { clase: CLASE.ACCESO_DENEGADO };
    }
    if (resultado.estado === 404) {
      return { clase: CLASE.RECURSO_NO_DISPONIBLE };
    }
    if (resultado.estado >= 500) {
      return { clase: CLASE.ERROR_UPSTREAM };
    }
    if (resultado.ok && resultado.cuerpo) {
      if (resultado.cuerpo.disponible === true) {
        return { clase: CLASE.DATOS, datos: resultado.cuerpo.datos };
      }
      if (resultado.cuerpo.disponible === false) {
        return { clase: CLASE.NO_DISPONIBLE, motivo: resultado.cuerpo.motivo };
      }
    }
    // Un 2xx que no cumple el contrato del adaptador. No se adivina.
    return { clase: CLASE.ERROR_UPSTREAM };
  }

  /** El texto neutro que corresponde a una clasificación sin datos. */
  function avisoDe(clasificacion) {
    if (clasificacion.clase === CLASE.NO_DISPONIBLE) {
      return AVISOS[clasificacion.motivo] || AVISOS[CLASE.ERROR_UPSTREAM];
    }
    return AVISOS[clasificacion.clase] || AVISOS[CLASE.ERROR_UPSTREAM];
  }

  function consultarSesion() {
    return pedir(API.sesion).then(function (resultado) {
      if (resultado.ok && resultado.cuerpo && resultado.cuerpo.autenticada) {
        return resultado.cuerpo;
      }
      return null;
    });
  }

  function refrescarConectividad() {
    return pedir(API.conectividad).then(function (resultado) {
      if (!resultado.ok || !resultado.cuerpo) {
        pintarConectividad(false);
        return;
      }
      pintarConectividad(resultado.cuerpo.api_central === 'disponible');
    });
  }

  function refrescarEstadoLocal() {
    return pedir(API.estadoLocal).then(function (resultado) {
      if (!resultado.ok || !resultado.cuerpo) {
        ui.notaEstadoLocal.textContent =
          'No se pudo leer el estado local de este dispositivo.';
        ui.notaEstadoLocal.hidden = false;
        return;
      }

      const estado = resultado.cuerpo;

      if (!estado.inicializado) {
        ui.outboxPendientes.textContent = SIN_DATO;
        ui.outboxEnviados.textContent = SIN_DATO;
        ui.outboxReintentables.textContent = SIN_DATO;
        ui.outboxRevision.textContent = SIN_DATO;
        ui.notaEstadoLocal.textContent = texto(
          estado.detalle,
          'Estado local no inicializado en este dispositivo.'
        );
        ui.notaEstadoLocal.hidden = false;
        return;
      }

      // Los conteos son cero legitimos cuando valen cero: `texto()` solo
      // sustituye la ausencia, nunca el numero.
      ui.outboxPendientes.textContent = texto(estado.pendientes);
      ui.outboxEnviados.textContent = texto(estado.enviados);
      ui.outboxReintentables.textContent = texto(estado.fallidos_reintentables);
      ui.outboxRevision.textContent = texto(estado.fallidos_en_revision);
      ui.ultimaSincronizacion.textContent = fechaLegible(estado.ultima_sincronizacion);
      ui.notaEstadoLocal.hidden = true;
    });
  }

  /**
   * Conteos de la cola de **esta cuenta**, nunca los del nodo compartido.
   *
   * Mismo contrato que `refrescarEstadoLocal`, sobre una ruta distinta: cada
   * cuenta tiene su propio archivo del lado del adaptador, así que estos
   * números nunca incluyen lo que otra cuenta haya registrado.
   */
  function refrescarMovimientosEstado() {
    return pedir(API.movimientosEstado).then(function (resultado) {
      if (resultado.estado === 401) {
        mostrarLogin();
        return;
      }
      if (!resultado.ok || !resultado.cuerpo) {
        ui.notaMovimientosEstado.textContent =
          'No se pudo leer el estado de tus sesiones simuladas.';
        ui.notaMovimientosEstado.hidden = false;
        return;
      }

      const estado = resultado.cuerpo;

      if (!estado.inicializado) {
        ui.movPendientes.textContent = SIN_DATO;
        ui.movEnviados.textContent = SIN_DATO;
        ui.movReintentables.textContent = SIN_DATO;
        ui.movRevision.textContent = SIN_DATO;
        ui.notaMovimientosEstado.textContent = texto(
          estado.detalle,
          'Todavía no has registrado ninguna sesión simulada.'
        );
        ui.notaMovimientosEstado.hidden = false;
        return;
      }

      ui.movPendientes.textContent = texto(estado.pendientes);
      ui.movEnviados.textContent = texto(estado.enviados);
      ui.movReintentables.textContent = texto(estado.fallidos_reintentables);
      ui.movRevision.textContent = texto(estado.fallidos_en_revision);
      ui.notaMovimientosEstado.hidden = true;
    });
  }

  // =======================================================================
  // Lectura clinica
  // =======================================================================

  /** Descripción legible de un episodio para el selector. */
  function etiquetaDeEpisodio(episodio) {
    const desde = fechaCortaLegible(episodio.fecha_inicio);
    const estado = texto(episodio.estado_embarazo, NO_DISPONIBLE);
    return 'Embarazo desde ' + desde + ' — ' + estado;
  }

  /**
   * Pinta la tarjeta del embarazo y prepara el selector.
   *
   * Cuando el adaptador informa ambigüedad **no se llama «actual» a ninguno**.
   * La paciente puede consultar cualquiera de sus episodios, pero la interfaz
   * no afirma cuál está en curso, porque el dato no permite decidirlo.
   */
  function pintarEpisodios(datos) {
    episodios = datos;

    const todos = datos.todos || [];
    const anteriores = datos.anteriores || [];

    if (datos.ambiguo) {
      ui.embarazoEstado.textContent = 'Sin determinar';
      ui.embarazoAnteriores.textContent = String(todos.length);
      ui.notaEmbarazo.textContent =
        'No se pudo determinar automáticamente cuál es tu embarazo en curso. ' +
        'Selecciona un episodio en «Mi historial» para consultar su información.';
      ui.notaEmbarazo.hidden = false;
    } else if (datos.actual) {
      ui.embarazoEstado.textContent = texto(datos.actual.estado_embarazo, NO_DISPONIBLE);
      ui.embarazoAnteriores.textContent = String(anteriores.length);
      ui.notaEmbarazo.hidden = true;
    } else if (todos.length) {
      ui.embarazoEstado.textContent = 'Sin embarazo en curso';
      ui.embarazoAnteriores.textContent = String(anteriores.length);
      ui.notaEmbarazo.textContent =
        'No tienes un embarazo en curso registrado. Puedes consultar tus ' +
        'episodios anteriores en «Mi historial».';
      ui.notaEmbarazo.hidden = false;
    } else {
      ui.embarazoEstado.textContent = NO_DISPONIBLE;
      ui.embarazoAnteriores.textContent = '0';
      ui.notaEmbarazo.textContent = 'Todavía no hay episodios registrados.';
      ui.notaEmbarazo.hidden = false;
    }

    llenarSelector(todos, datos.actual);
  }

  function llenarSelector(todos, actual) {
    const selector = ui.selectorEmbarazo;
    selector.innerHTML = '';

    if (!todos.length) {
      const vacio = document.createElement('option');
      vacio.value = '';
      vacio.textContent = NO_DISPONIBLE;
      selector.appendChild(vacio);
      selector.disabled = true;
      seleccionado = null;
      actualizarBotonDeRegistro();
      return;
    }

    todos.forEach(function (episodio) {
      const opcion = document.createElement('option');
      opcion.value = String(episodio.id_embarazo);
      opcion.textContent = etiquetaDeEpisodio(episodio);
      selector.appendChild(opcion);
    });
    selector.disabled = false;

    // Se preselecciona el actual sólo cuando existe uno sin ambigüedad.
    const inicial = actual ? actual.id_embarazo : todos[0].id_embarazo;
    seleccionado = inicial;
    selector.value = String(inicial);
    actualizarBotonDeRegistro();
  }

  /**
   * Habilita el registro de una sesión simulada sólo cuando hay un embarazo
   * elegido. Sin un `seleccionado` que venga de `/adaptador/embarazos`, el
   * botón no tiene contra qué episodio registrar nada.
   */
  function actualizarBotonDeRegistro() {
    ui.botonRegistrarMovimiento.disabled = seleccionado === null;
    ui.notaMovimientos.textContent =
      seleccionado === null
        ? 'Selecciona un embarazo en «Mi historial» para registrar una sesión simulada.'
        : 'Se registrará como una sesión de movimiento simulada, no como un dato real.';
  }

  /** Estado neutro de todo lo clínico, con el aviso que corresponda. */
  function pintarSinDatosClinicos(clasificacion) {
    const aviso = avisoDe(clasificacion);

    ui.embarazoEstado.textContent = NO_DISPONIBLE;
    ui.embarazoSemana.textContent = NO_DISPONIBLE;
    ui.embarazoAnteriores.textContent = NO_DISPONIBLE;
    ui.notaEmbarazo.textContent = aviso;
    ui.notaEmbarazo.hidden = false;

    limpiarMetricas();
    pintarSemaforo(null, null);

    ui.selectorEmbarazo.disabled = true;
    // Sin lectura clínica confirmada no hay un embarazo autorizado contra el
    // cual registrar nada.
    seleccionado = null;
    actualizarBotonDeRegistro();
    mostrarHistorialVacio(aviso);
  }

  function limpiarMetricas() {
    ui.hrValor.textContent = SIN_DATO;
    ui.spo2Valor.textContent = SIN_DATO;
    ui.movValor.textContent = SIN_DATO;
    ui.hrEstado.textContent = 'Sin lectura';
    ui.spo2Estado.textContent = 'Sin lectura';
    ui.movEstado.textContent = 'Sin lectura';
    ui.ultimaLectura.textContent = SIN_DATO;
  }

  /**
   * Pinta **una** lectura, entera y coherente.
   *
   * Las tres métricas, el instante y el semáforo salen de la misma captura. No
   * se compone un panel con la última frecuencia cardíaca de una lectura y el
   * último movimiento de otra: serían instantes distintos bajo un único
   * «última lectura», y eso sería engañoso.
   *
   * Una métrica que esa lectura no midió se muestra como «—». Nunca como 0.
   */
  function pintarUltimaLectura(lectura) {
    if (!lectura) {
      limpiarMetricas();
      pintarSemaforo(null, null);
      ui.embarazoSemana.textContent = NO_DISPONIBLE;
      return;
    }

    ui.hrValor.textContent = texto(lectura.hr_valor);
    ui.spo2Valor.textContent = texto(lectura.spo2_valor);
    ui.movValor.textContent = texto(lectura.mov_valor);

    ui.hrEstado.textContent =
      lectura.hr_valor === null || lectura.hr_valor === undefined
        ? 'No medido en esta lectura'
        : 'Última lectura registrada';
    ui.spo2Estado.textContent =
      lectura.spo2_valor === null || lectura.spo2_valor === undefined
        ? 'No medido en esta lectura'
        : 'Última lectura registrada';
    ui.movEstado.textContent =
      lectura.mov_valor === null || lectura.mov_valor === undefined
        ? 'No medido en esta lectura'
        : 'Última lectura registrada';

    ui.embarazoSemana.textContent = texto(lectura.semana_gestacion, NO_DISPONIBLE);
    ui.ultimaLectura.textContent = fechaLegible(lectura.fecha_hora_captura);

    // El nivel llega ya clasificado por la fuente autorizada. Aquí sólo se
    // traduce a una clase CSS.
    pintarSemaforo(lectura.codigo_semaforo, mensajeDeSemaforo(lectura.codigo_semaforo));
  }

  /** Texto acompañante del nivel. No es una interpretación clínica. */
  function mensajeDeSemaforo(codigo) {
    if (codigo === 'OK') return 'Dentro de lo esperado para el seguimiento simulado';
    if (codigo === 'WARNING') return 'Conviene repetir la medición';
    if (codigo === 'ERROR') return 'Comunícate con tu personal de seguimiento';
    return null;
  }

  function mostrarHistorialVacio(mensaje) {
    ui.historialLista.innerHTML = '';
    const tarjeta = document.createElement('div');
    tarjeta.className = 'result-card estado-vacio';
    const titulo = document.createElement('h3');
    titulo.className = 'subtitulo';
    titulo.textContent = 'Sin información que mostrar';
    const parrafo = document.createElement('p');
    parrafo.className = 'texto-apoyo';
    parrafo.textContent = mensaje;
    tarjeta.appendChild(titulo);
    tarjeta.appendChild(parrafo);
    ui.historialLista.appendChild(tarjeta);
  }

  /** Construye una fila etiqueta/valor reutilizando el estilo existente. */
  function filaDeDatos(etiqueta, valor) {
    const fila = document.createElement('div');
    fila.className = 'data-row';
    const izquierda = document.createElement('span');
    izquierda.className = 'label';
    izquierda.textContent = etiqueta;
    const derecha = document.createElement('span');
    derecha.className = 'value';
    derecha.textContent = valor;
    fila.appendChild(izquierda);
    fila.appendChild(derecha);
    return fila;
  }

  /**
   * Pinta las sesiones de **un** episodio.
   *
   * Se vacía la lista antes de construirla, de modo que cambiar de episodio no
   * pueda dejar visible ni una fila del anterior.
   */
  function pintarHistorial(datos) {
    ui.historialLista.innerHTML = '';

    const sesiones = datos.sesiones || [];
    if (!sesiones.length) {
      mostrarHistorialVacio(
        'Este episodio todavía no tiene sesiones de monitoreo registradas.'
      );
      return;
    }

    sesiones.forEach(function (sesion) {
      const tarjeta = document.createElement('div');
      tarjeta.className = 'result-card';

      const titulo = document.createElement('h3');
      titulo.className = 'subtitulo';
      titulo.textContent = texto(sesion.tipo_sesion, NO_DISPONIBLE);
      tarjeta.appendChild(titulo);

      tarjeta.appendChild(filaDeDatos('Estado:', texto(sesion.estado_sesion, NO_DISPONIBLE)));
      tarjeta.appendChild(filaDeDatos('Inicio:', fechaLegible(sesion.fecha_inicio)));
      tarjeta.appendChild(filaDeDatos('Fin:', fechaLegible(sesion.fecha_fin)));

      const lecturas = sesion.lecturas || [];
      if (!lecturas.length) {
        tarjeta.appendChild(filaDeDatos('Lecturas:', 'Sin lecturas registradas'));
      } else {
        lecturas.forEach(function (lectura) {
          const bloque = document.createElement('div');
          bloque.className = 'semaforo-item ' +
            (CLASES_DE_SEMAFORO[lectura.codigo_semaforo] || '');
          const luz = document.createElement('span');
          luz.className = 'alert-light';
          const detalle = document.createElement('span');
          detalle.textContent =
            fechaLegible(lectura.fecha_hora_captura) +
            ' · semana ' + texto(lectura.semana_gestacion) +
            ' · FC ' + texto(lectura.hr_valor) +
            ' · SpO₂ ' + texto(lectura.spo2_valor) +
            ' · mov. ' + texto(lectura.mov_valor);
          bloque.appendChild(luz);
          bloque.appendChild(detalle);
          tarjeta.appendChild(bloque);
        });
      }

      ui.historialLista.appendChild(tarjeta);
    });
  }

  /** Pide los episodios y, si hay uno seleccionable, su monitoreo. */
  function cargarClinico() {
    return pedir(API.embarazos).then(function (resultado) {
      const clasificacion = clasificar(resultado);

      if (clasificacion.clase === CLASE.SESION_LOCAL_INVALIDA) {
        mostrarLogin();
        return;
      }

      if (clasificacion.clase !== CLASE.DATOS) {
        pintarSinDatosClinicos(clasificacion);
        return;
      }

      pintarEpisodios(clasificacion.datos);

      if (seleccionado === null) {
        pintarUltimaLectura(null);
        mostrarHistorialVacio('Todavía no hay episodios registrados.');
        return;
      }
      return cargarMonitoreo(seleccionado);
    });
  }

  /** Pide sesiones y lecturas de un episodio concreto. */
  function cargarMonitoreo(idEmbarazo) {
    return pedir(API.monitoreo(idEmbarazo)).then(function (resultado) {
      const clasificacion = clasificar(resultado);

      if (clasificacion.clase === CLASE.SESION_LOCAL_INVALIDA) {
        mostrarLogin();
        return;
      }

      if (clasificacion.clase !== CLASE.DATOS) {
        // Un 403, un 404 o un fallo del servidor dejan el portal abierto: sólo
        // se vacía lo clínico y se explica por qué.
        limpiarMetricas();
        pintarSemaforo(null, null);
        mostrarHistorialVacio(avisoDe(clasificacion));
        return;
      }

      pintarUltimaLectura(clasificacion.datos.ultima_lectura);
      pintarHistorial(clasificacion.datos);
    });
  }

  function refrescar() {
    return Promise.all([
      refrescarConectividad(),
      refrescarEstadoLocal(),
      refrescarMovimientosEstado(),
      cargarClinico()
    ]);
  }

  function iniciarRefrescoPeriodico() {
    detenerRefrescoPeriodico();
    refrescar();
    temporizadorRefresco = window.setInterval(refrescar, INTERVALO_REFRESCO_MS);
  }

  function detenerRefrescoPeriodico() {
    if (temporizadorRefresco !== null) {
      window.clearInterval(temporizadorRefresco);
      temporizadorRefresco = null;
    }
  }

  // =======================================================================
  // Inicio y cierre de sesion
  // =======================================================================

  function manejarLogin(evento) {
    evento.preventDefault();
    mostrarMensajeLogin(null);

    const correo = ui.email.value.trim();
    const contrasena = ui.password.value;

    if (!correo || !contrasena) {
      mostrarMensajeLogin('Escribe tu correo y tu contraseña.');
      return;
    }

    ui.botonLogin.disabled = true;
    ui.botonLogin.textContent = 'Comprobando…';

    pedir(API.iniciarSesion, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ email: correo, password: contrasena })
    })
      .then(function (resultado) {
        // La contrasena se borra en cuanto sale de aqui, salga bien o mal.
        // No se conserva en ninguna variable, ni en el campo del formulario.
        ui.password.value = '';

        if (resultado.ok) {
          mostrarMensajeLogin(null);
          ui.email.value = '';
          mostrarPortal();
          return;
        }

        if (resultado.estado === 0) {
          mostrarMensajeLogin(MENSAJE_SIN_CONEXION);
          return;
        }

        // El detalle procede del adaptador, que ya lo generaliza. Si no
        // llega ninguno, se usa el texto propio: nunca se construye un
        // mensaje a partir de la respuesta cruda.
        const detalle =
          resultado.cuerpo && typeof resultado.cuerpo.detail === 'string'
            ? resultado.cuerpo.detail
            : MENSAJE_ERROR_GENERICO;
        mostrarMensajeLogin(detalle);
      })
      .finally(function () {
        ui.botonLogin.disabled = false;
        ui.botonLogin.textContent = 'Iniciar sesión';
      });
  }

  /**
   * Registra una sesión de movimiento simulada para el embarazo elegido.
   *
   * `seleccionado` sólo puede ser un id que ya vino de `/adaptador/embarazos`
   * -- ver `llenarSelector` --, así que esta función nunca fabrica ni adivina
   * un identificador. El adaptador vuelve a comprobar del lado del servidor
   * que ese embarazo es de esta cuenta antes de guardar nada: esta función no
   * sustituye esa comprobación, sólo evita una petición que ya se sabe sin
   * sentido cuando no hay ningún embarazo elegido.
   */
  function manejarRegistrarMovimiento() {
    if (seleccionado === null) {
      return;
    }

    ui.botonRegistrarMovimiento.disabled = true;
    ui.notaMovimientos.textContent = 'Registrando sesión simulada…';

    pedir(API.sesionesSimuladas(seleccionado), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ tipo_sesion: TIPO_SESION_SIMULADA })
    })
      .then(function (resultado) {
        if (resultado.estado === 401) {
          mostrarLogin();
          return;
        }

        if (resultado.estado === 200 && resultado.cuerpo && resultado.cuerpo.disponible === false) {
          ui.notaMovimientos.textContent = avisoDe({
            clase: CLASE.NO_DISPONIBLE,
            motivo: resultado.cuerpo.motivo
          });
          return;
        }

        if (resultado.ok && resultado.cuerpo && resultado.cuerpo.registrado) {
          ui.notaMovimientos.textContent =
            'Sesión simulada registrada en este dispositivo. Queda pendiente ' +
            'hasta que la sincronices.';
          refrescarMovimientosEstado();
          return;
        }

        if (resultado.estado === 404) {
          ui.notaMovimientos.textContent = 'Ese embarazo no está disponible para registrar sesiones.';
          return;
        }

        ui.notaMovimientos.textContent =
          'No se pudo registrar la sesión simulada. Inténtalo de nuevo más tarde.';
      })
      .finally(function () {
        actualizarBotonDeRegistro();
      });
  }

  /**
   * Sincroniza ahora la cola de **esta cuenta**. Una sola ronda real contra
   * el servidor central: lo que responde este botón es exactamente lo que la
   * API contestó, nunca un resultado inventado por esta pantalla.
   */
  function manejarSincronizarMovimientos() {
    ui.botonSincronizarMovimientos.disabled = true;
    ui.notaMovimientosEstado.textContent = 'Sincronizando…';
    ui.notaMovimientosEstado.hidden = false;

    pedir(API.movimientosSincronizar, { method: 'POST' })
      .then(function (resultado) {
        if (resultado.estado === 401) {
          mostrarLogin();
          return;
        }

        if (resultado.estado === 200 && resultado.cuerpo && resultado.cuerpo.disponible === false) {
          ui.notaMovimientosEstado.textContent = avisoDe({
            clase: CLASE.NO_DISPONIBLE,
            motivo: resultado.cuerpo.motivo
          });
          ui.notaMovimientosEstado.hidden = false;
          return refrescarMovimientosEstado();
        }

        if (!resultado.ok || !resultado.cuerpo) {
          ui.notaMovimientosEstado.textContent =
            'No se pudo sincronizar. Inténtalo de nuevo más tarde.';
          ui.notaMovimientosEstado.hidden = false;
          return refrescarMovimientosEstado();
        }

        const r = resultado.cuerpo;
        ui.notaMovimientosEstado.textContent =
          'Sincronización: ' + texto(r.entregados, '0') + ' entregada(s), ' +
          texto(r.rechazados, '0') + ' rechazada(s), ' +
          texto(r.reintentables, '0') + ' pendiente(s) de reintento.';
        ui.notaMovimientosEstado.hidden = false;
        return refrescarMovimientosEstado();
      })
      .finally(function () {
        ui.botonSincronizarMovimientos.disabled = false;
      });
  }

  function manejarLogout(evento) {
    if (evento) {
      evento.preventDefault();
    }
    pedir(API.cerrarSesion, { method: 'POST' }).then(function () {
      // Se vuelve al inicio de sesion pase lo que pase: si el adaptador no
      // respondio, mantener el portal abierto seria peor.
      mostrarLogin();
    });
  }

  // =======================================================================
  // Arranque
  // =======================================================================

  function conectarEventos() {
    if (ui.formLogin) {
      ui.formLogin.addEventListener('submit', manejarLogin);
    }

    if (ui.botonCerrarSesion) {
      ui.botonCerrarSesion.addEventListener('click', manejarLogout);
    }

    if (ui.botonRegistrarMovimiento) {
      ui.botonRegistrarMovimiento.addEventListener('click', manejarRegistrarMovimiento);
    }

    if (ui.botonSincronizarMovimientos) {
      ui.botonSincronizarMovimientos.addEventListener('click', manejarSincronizarMovimientos);
    }

    const enlaces = ui.menu ? ui.menu.querySelectorAll('a[data-vista]') : [];
    Array.prototype.forEach.call(enlaces, function (enlace) {
      enlace.addEventListener('click', function (evento) {
        evento.preventDefault();
        mostrarVista(enlace.dataset.vista);
      });
    });

    if (ui.selectorEmbarazo) {
      ui.selectorEmbarazo.addEventListener('change', function () {
        const elegido = Number(ui.selectorEmbarazo.value);

        // Sólo se acepta un identificador que vino de `/adaptador/embarazos`.
        // Un valor manipulado en la página no llega a convertirse en petición.
        const conocido =
          episodios &&
          (episodios.todos || []).some(function (e) {
            return e.id_embarazo === elegido;
          });
        if (!conocido) {
          return;
        }

        seleccionado = elegido;
        // Se vacía antes de pedir, para que no quede a la vista ni una fila del
        // episodio anterior mientras llega la respuesta.
        ui.historialLista.innerHTML = '';
        limpiarMetricas();
        pintarSemaforo(null, null);
        cargarMonitoreo(elegido);
      });
    }
  }

  function arrancar() {
    conectarEventos();
    limpiarPanel();
    pintarConectividad(null);

    consultarSesion().then(function (sesion) {
      if (sesion) {
        mostrarPortal();
      } else {
        mostrarLogin();
      }
      // La conectividad se comprueba tambien sin sesion: la pantalla de
      // inicio de sesion tiene que poder decir que el servidor no responde.
      refrescarConectividad();
    });
  }

  document.addEventListener('DOMContentLoaded', arrancar);
})();
