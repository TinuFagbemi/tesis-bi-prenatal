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
    estadoLocal: '/adaptador/estado-local'
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

    ultimaLectura: document.getElementById('last-update'),
    ultimaSincronizacion: document.getElementById('last-sync')
  };

  const VISTAS = ['login', 'inicio', 'historial', 'manual', 'acerca'];

  let temporizadorRefresco = null;

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

    ui.ultimaLectura.textContent = SIN_DATO;
    ui.ultimaSincronizacion.textContent = SIN_DATO;
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

  function refrescar() {
    return Promise.all([refrescarConectividad(), refrescarEstadoLocal()]);
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

    const enlaces = ui.menu ? ui.menu.querySelectorAll('a[data-vista]') : [];
    Array.prototype.forEach.call(enlaces, function (enlace) {
      enlace.addEventListener('click', function (evento) {
        evento.preventDefault();
        mostrarVista(enlace.dataset.vista);
      });
    });
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
