/* =========================================================================
   FetalAlert — Interfaz de la gestante

   Este archivo se encarga de la interaccion y de pedir datos al adaptador
   local. No decide nada clinico.

   Cuatro reglas gobiernan todo lo que hay aqui:

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

   4. Inicio e Historial son dos contextos distintos. Inicio muestra siempre
      el embarazo en curso; Historial tiene su propia seleccion. Cambiar el
      embarazo en Historial no repinta Inicio ni cambia el destino del
      registro de movimientos. Cada contexto lleva su propio turno: una
      respuesta que llega tarde, de una peticion ya superada, se descarta en
      lugar de pisar lo que se esta mirando.

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
    estadoConexion: '/adaptador/estado-conexion',
    reautenticar: '/adaptador/reautenticar',
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
  // fija: no hay un selector que la paciente pueda manipular para pedir otra
  // cosa.
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

  // Cada cuanto se comprueba el estado de la conexion y de los envios, en
  // milisegundos. **No recarga lo clinico**: cada lectura clinica queda
  // registrada en la auditoria del servidor (SCRUM-98), y repetirla cada
  // veinte segundos llenaba esa traza sin que la paciente hiciera nada. Lo
  // clinico se pide al entrar, al volver a Inicio, al cambiar de embarazo, al
  // volver a la pestaña con datos viejos, al recuperar la conexion o la
  // autenticacion, tras un envio confirmado y con «Actualizar».
  const INTERVALO_REFRESCO_MS = 20000;

  // Al volver a la pestaña, lo clinico se vuelve a pedir si lo mostrado tiene
  // mas de este tiempo.
  const ANTIGUEDAD_MAXIMA_DATOS_MS = 60000;

  // Toda fecha visible se presenta en español y en la hora de Panamá, sea
  // cual sea la configuración del navegador. Las marcas almacenadas no se
  // tocan: solo cambia cómo se leen.
  const IDIOMA = 'es';
  const ZONA_HORARIA = 'America/Panama';
  const FORMATO_FECHA_HORA = new Intl.DateTimeFormat(IDIOMA, {
    timeZone: ZONA_HORARIA,
    day: 'numeric',
    month: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  });
  const FORMATO_FECHA = new Intl.DateTimeFormat(IDIOMA, {
    timeZone: ZONA_HORARIA,
    day: 'numeric',
    month: 'numeric',
    year: 'numeric'
  });
  // Una fecha de calendario («2026-03-19») es un día, no un instante: se
  // construye como medianoche UTC y se formatea en UTC, así que ninguna zona
  // horaria puede moverla al día anterior.
  const FORMATO_DIA_CALENDARIO = new Intl.DateTimeFormat(IDIOMA, {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'numeric',
    year: 'numeric'
  });

  // Estado de la comunicación con el servidor, tal como lo comprueba
  // `/adaptador/estado-conexion`. Son dos preguntas distintas y se responden
  // por separado: si el servidor contesta, y si acepta la sesión de la
  // paciente.
  const API_CENTRAL = {
    DISPONIBLE: 'disponible',
    NO_DISPONIBLE: 'no_disponible',
    CON_ERRORES: 'con_errores'
  };
  const AUTENTICACION = {
    VIGENTE: 'vigente',
    REQUERIDA: 'reautenticacion_requerida',
    DENEGADA: 'acceso_denegado',
    NO_COMPROBADA: 'no_comprobada'
  };
  // Cuando ni siquiera la aplicación local contestó: no se sabe nada.
  const ESTADO_NO_COMPROBADO = {
    api_central: 'no_comprobada',
    autenticacion_central: AUTENTICACION.NO_COMPROBADA
  };

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
    'Tu sesión con el servidor terminó. Pulsa «Volver a iniciar sesión» para ' +
    'consultar tu información; tus registros guardados se conservan.';
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

  // Nombre legible del mismo codigo, para la tabla del historial.
  const ETIQUETAS_DE_SEMAFORO = {
    OK: 'Verde',
    WARNING: 'Ámbar',
    ERROR: 'Rojo'
  };

  // Nombre legible del estado de un episodio. Un estado desconocido se
  // muestra tal cual llega.
  const ESTADOS_DE_EMBARAZO = {
    ACTIVO: 'En curso',
    FINALIZADO: 'Finalizado',
    SUSPENDIDO: 'Suspendido'
  };

  const TEXTO_SIN_EMBARAZO_EN_CURSO =
    'Necesitas un embarazo en curso para registrar movimientos.';
  const TEXTO_LISTO_PARA_REGISTRAR =
    'Se guardará primero en este dispositivo.';
  const TEXTO_CARGANDO_LECTURAS = 'Cargando lecturas…';
  const TEXTO_SIN_CONEXION_CONTEXTO_CONSERVADO =
    'Sin conexión con el servidor. Se muestra la última información consultada; ' +
    'lo que registres se guarda en este dispositivo.';
  const TEXTO_REAUTENTICACION_CONTEXTO_CONSERVADO =
    'Se muestra la última información consultada. Para actualizarla, vuelve a ' +
    'iniciar sesión; lo que registres se guarda en este dispositivo.';

  // =======================================================================
  // Referencias al DOM
  // =======================================================================

  const ui = {
    menu: document.getElementById('main-menu'),
    areaCuenta: document.getElementById('area-cuenta'),
    conexion: document.getElementById('connection-status'),
    botonCerrarSesion: document.getElementById('btn-cerrar-sesion'),
    dialogoCierre: document.getElementById('dialogo-cerrar-sesion'),
    botonCancelarCierre: document.getElementById('btn-cancelar-cierre'),
    botonConfirmarCierre: document.getElementById('btn-confirmar-cierre'),

    formLogin: document.getElementById('form-login'),
    email: document.getElementById('login-email'),
    password: document.getElementById('login-password'),
    botonLogin: document.getElementById('btn-login'),
    mensajeLogin: document.getElementById('login-mensaje'),

    saludo: document.getElementById('saludo'),

    embarazoEstado: document.getElementById('embarazo-estado'),
    embarazoInicio: document.getElementById('embarazo-inicio'),
    embarazoSemana: document.getElementById('embarazo-semana'),
    embarazoAnteriores: document.getElementById('embarazo-anteriores'),
    notaEmbarazo: document.getElementById('nota-embarazo'),
    botonVerAnteriores: document.getElementById('btn-ver-anteriores'),

    // Una tarjeta por variable, cada una con su propio registro de origen.
    tarjetas: {
      frecuencia_cardiaca: tarjeta('hr'),
      saturacion_oxigeno: tarjeta('spo2'),
      movimientos_fetales: tarjeta('mov')
    },
    ultimaLectura: document.getElementById('last-update'),
    ultimaLecturaMide: document.getElementById('ultima-lectura-mide'),
    datosActualizados: document.getElementById('datos-actualizados'),
    botonActualizarDatos: document.getElementById('btn-actualizar-datos'),

    semaforo: document.getElementById('semaforo'),
    semaforoMensaje: document.getElementById('semaforo-mensaje'),

    avisoSesionCentral: document.getElementById('aviso-sesion-central'),
    avisoSesionCentralTexto: document.getElementById('aviso-sesion-central-texto'),
    botonMostrarReautenticar: document.getElementById('btn-mostrar-reautenticar'),
    formReautenticar: document.getElementById('form-reautenticar'),
    reautenticarEmail: document.getElementById('reauth-email'),
    reautenticarPassword: document.getElementById('reauth-password'),
    botonReautenticar: document.getElementById('btn-reautenticar'),
    botonCancelarReautenticar: document.getElementById('btn-cancelar-reautenticar'),
    mensajeReautenticar: document.getElementById('reauth-mensaje'),
    envioUltimo: document.getElementById('envio-ultimo'),

    botonRegistrarMovimiento: document.getElementById('btn-registrar-movimientos'),
    notaMovimientos: document.getElementById('nota-movimientos'),
    envioEstado: document.getElementById('envio-estado'),
    envioResultado: document.getElementById('envio-resultado'),
    botonSincronizarMovimientos: document.getElementById('btn-sincronizar-movimientos'),

    selectorEmbarazo: document.getElementById('selector-embarazo'),
    historialLista: document.getElementById('historial-lista')
  };

  /** Los elementos de la tarjeta de una variable, por el prefijo de sus `id`. */
  function tarjeta(prefijo) {
    return {
      contenedor: document.getElementById('tarjeta-' + prefijo),
      valor: document.getElementById(prefijo + '-value'),
      estado: document.getElementById(prefijo + '-status'),
      fecha: document.getElementById(prefijo + '-fecha'),
      semana: document.getElementById(prefijo + '-semana'),
      clasificacion: document.getElementById(prefijo + '-clasificacion')
    };
  }

  const VISTAS = ['login', 'inicio', 'historial', 'manual', 'acerca'];

  let temporizadorRefresco = null;

  // Último estado comprobado de la comunicación con el servidor, o null si
  // todavía no se sabe. Solo lo escribe `refrescarEstadoConexion`.
  let estadoConexion = null;

  // Una sola comprobación de estado en vuelo: el temporizador, la vuelta a la
  // pestaña y un aviso de reautenticación no abren tres peticiones a la vez.
  let comprobacionEnCurso = null;

  // Cuándo se recibió por última vez información clínica del servidor
  // (milisegundos), o null. Es «última actualización de los datos», que no
  // tiene nada que ver con el último envío confirmado.
  let datosConsultadosEn = null;

  // Si el portal privado está abierto, y un número que cambia en cada cierre
  // de sesión: una respuesta que vuelve con otra época no pinta nada.
  let portalAbierto = false;
  let epocaSesion = 0;

  // Episodios que el adaptador entrego en la ultima consulta. Todo
  // identificador que se usa despues sale de aqui: no se construye a partir
  // de nada escrito en la pagina.
  let episodios = null;

  // Contexto de Inicio: el embarazo en curso, o ninguno si no existe o es
  // ambiguo. Es tambien el destino del registro de movimientos.
  let idInicio = null;

  // Contexto de Historial: la seleccion propia de esa vista.
  let idHistorial = null;

  // Turnos. Cada peticion anota el turno vigente de su contexto y, al volver,
  // solo se pinta si sigue siendo el vigente. Cerrar sesion avanza todos.
  const turnos = { clinico: 0, inicio: 0, historial: 0, envios: 0, conexion: 0 };

  function avanzarTurno(contexto) {
    turnos[contexto] += 1;
    return turnos[contexto];
  }

  function esVigente(contexto, turno) {
    return turnos[contexto] === turno;
  }

  function invalidarTodosLosTurnos() {
    Object.keys(turnos).forEach(avanzarTurno);
  }

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

  function ausente(valor) {
    return valor === null || valor === undefined || valor === '';
  }

  /**
   * Un valor biométrico tal como llega, sin los ceros decimales de relleno.
   *
   * La API entrega NUMERIC(5,2) como texto: «86.00». Se recorta como texto
   * --«86.00» a «86», «97.50» se queda igual-- sin convertirlo en número, así
   * que una ausencia nunca puede acabar como cero.
   */
  function medida(valor) {
    return ausente(valor) ? SIN_DATO : String(valor).replace(/\.0+$/, '');
  }

  /** Valor con su unidad, o el marcador de ausencia sin unidad. */
  function conUnidad(valor, unidad) {
    return ausente(valor) ? SIN_DATO : medida(valor) + ' ' + unidad;
  }

  /** Fecha y hora legibles en hora de Panamá, o el marcador de ausencia. */
  function fechaLegible(valorIso) {
    if (ausente(valorIso)) {
      return SIN_DATO;
    }
    const momento = new Date(valorIso);
    if (Number.isNaN(momento.getTime())) {
      return SIN_DATO;
    }
    return FORMATO_FECHA_HORA.format(momento);
  }

  /**
   * Solo la fecha, para etiquetas donde la hora no aporta.
   *
   * Una fecha sin hora («2026-03-19») es un día de calendario, no un instante:
   * leída como medianoche UTC y mostrada en Panamá (UTC−5) sería el día
   * anterior. Por eso se formatea como día de calendario, sin conversión.
   */
  function fechaCortaLegible(valorIso) {
    if (ausente(valorIso)) {
      return SIN_DATO;
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(valorIso)) {
      // Medianoche UTC de ese día, formateada en UTC: el mismo día, siempre.
      return FORMATO_DIA_CALENDARIO.format(new Date(valorIso + 'T00:00:00Z'));
    }
    const momento = new Date(valorIso);
    if (Number.isNaN(momento.getTime())) {
      return SIN_DATO;
    }
    return FORMATO_FECHA.format(momento);
  }

  function estadoLegible(estado) {
    return ESTADOS_DE_EMBARAZO[estado] || texto(estado, NO_DISPONIBLE);
  }

  /** Un conteo del adaptador, o nada si no llego como numero. */
  function conteo(valor) {
    return typeof valor === 'number' ? valor : 0;
  }

  /** La frase en singular o en plural; `{n}` se sustituye por la cantidad. */
  function segun(cantidad, singular, plural) {
    return (cantidad === 1 ? singular : plural).replace('{n}', String(cantidad));
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

  /**
   * Texto y estilo del indicador para un estado **comprobado**.
   *
   * Habla del servidor central, nunca de un dispositivo: en este MVP no hay
   * hardware que conectar. `estado` es `null` mientras no se sabe, un booleano
   * en la pantalla de inicio de sesión (solo se puede preguntar si el servidor
   * contesta) o la respuesta de `/adaptador/estado-conexion` con sesión.
   *
   * Un servidor que contesta pero rechaza el token **no** es «sin conexión»:
   * la red funciona y lo que falta es volver a autenticarse.
   */
  function describirConectividad(estado) {
    if (estado === null || estado === undefined) {
      return { texto: 'Comprobando conexión…', clase: 'status-connecting' };
    }
    if (estado === true) {
      return { texto: 'Servidor disponible', clase: 'status-connected' };
    }
    if (estado === false) {
      return { texto: 'Sin conexión con el servidor', clase: 'status-disconnected' };
    }
    if (estado.api_central === API_CENTRAL.NO_DISPONIBLE) {
      return { texto: 'Sin conexión con el servidor', clase: 'status-disconnected' };
    }
    if (estado.api_central === API_CENTRAL.CON_ERRORES) {
      return { texto: 'El servidor no responde bien', clase: 'status-disconnected' };
    }
    if (estado.autenticacion_central === AUTENTICACION.VIGENTE) {
      return { texto: 'Conectada al servidor', clase: 'status-connected' };
    }
    if (estado.autenticacion_central === AUTENTICACION.REQUERIDA) {
      return { texto: 'Servidor disponible · inicia sesión de nuevo', clase: 'status-idle' };
    }
    if (estado.autenticacion_central === AUTENTICACION.DENEGADA) {
      return { texto: 'Acceso no autorizado', clase: 'status-disconnected' };
    }
    return { texto: 'No se pudo comprobar la conexión', clase: 'status-connecting' };
  }

  function pintarConectividad(estado) {
    const descripcion = describirConectividad(estado);
    ui.conexion.textContent = descripcion.texto;
    ui.conexion.className = 'connection-status ' + descripcion.clase;
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

  function mostrarNota(elemento, mensaje) {
    elemento.textContent = mensaje || '';
    elemento.hidden = !mensaje;
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
    portalAbierto = true;
    ui.menu.hidden = false;
    ui.areaCuenta.hidden = false;
    document.getElementById('vista-login').hidden = true;
    mostrarVista('inicio');
    iniciarRefrescoPeriodico();
  }

  /** La pantalla de inicio de sesion, con todo lo privado oculto. */
  function mostrarLogin() {
    portalAbierto = false;
    epocaSesion += 1;
    comprobacionEnCurso = null;
    detenerRefrescoPeriodico();
    invalidarTodosLosTurnos();
    cerrarDialogoDeCierre();
    ui.menu.hidden = true;
    ui.areaCuenta.hidden = true;
    VISTAS.forEach(function (vista) {
      const seccion = document.getElementById('vista-' + vista);
      if (seccion) {
        seccion.hidden = vista !== 'login';
      }
    });
    limpiarPanel();
    // Sin sesión solo se puede saber si el servidor contesta.
    pintarConectividad(null);
    refrescarConectividad();
  }

  /**
   * Devuelve el panel a su estado neutro.
   *
   * Se llama al cerrar sesion, para que ningun dato de una sesion quede
   * visible en la siguiente. Solo limpia lo que se ve: los registros
   * guardados en el dispositivo no se tocan.
   */
  function limpiarPanel() {
    ui.saludo.textContent = 'Bienvenida';

    ui.embarazoEstado.textContent = NO_DISPONIBLE;
    ui.embarazoInicio.textContent = NO_DISPONIBLE;
    ui.embarazoSemana.textContent = NO_DISPONIBLE;
    ui.embarazoAnteriores.textContent = NO_DISPONIBLE;
    mostrarNota(ui.notaEmbarazo, null);
    ui.botonVerAnteriores.hidden = true;

    limpiarMetricas();
    pintarSemaforo(null, null);

    mostrarNota(ui.envioEstado, null);
    mostrarNota(ui.envioResultado, null);
    mostrarNota(ui.envioUltimo, null);
    ui.botonSincronizarMovimientos.hidden = true;

    mostrarNota(ui.datosActualizados, null);
    ocultarAvisoSesionCentral();
    estadoConexion = null;
    datosConsultadosEn = null;

    // Nada de un episodio puede sobrevivir a un cierre de sesión.
    episodios = null;
    idInicio = null;
    idHistorial = null;
    actualizarBotonDeRegistro();

    ui.selectorEmbarazo.innerHTML = '';
    const vacio = document.createElement('option');
    vacio.value = '';
    vacio.textContent = NO_DISPONIBLE;
    ui.selectorEmbarazo.appendChild(vacio);
    ui.selectorEmbarazo.disabled = true;
    ui.historialLista.innerHTML = '';
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

  /** Solo para la pantalla de inicio de sesión: si el servidor contesta. */
  function refrescarConectividad() {
    return pedir(API.conectividad).then(function (resultado) {
      if (portalAbierto) {
        // Con sesión, el indicador lo decide `refrescarEstadoConexion`, que
        // sabe más. Esta respuesta llegó tarde y no lo pisa.
        return;
      }
      if (!resultado.ok || !resultado.cuerpo) {
        pintarConectividad(false);
        return;
      }
      pintarConectividad(resultado.cuerpo.api_central === API_CENTRAL.DISPONIBLE);
    });
  }

  /** Si con este estado se puede consultar y enviar al servidor. */
  function operativa(estado) {
    return Boolean(
      estado &&
        estado.api_central === API_CENTRAL.DISPONIBLE &&
        estado.autenticacion_central === AUTENTICACION.VIGENTE
    );
  }

  /**
   * Comprueba la comunicación con el servidor y la sesión central.
   *
   * Una sola comprobación en vuelo: si el temporizador, la vuelta a la pestaña
   * y un aviso coinciden, comparten la misma petición. Una respuesta que llega
   * después de cerrar sesión se descarta por turno.
   */
  function refrescarEstadoConexion() {
    if (comprobacionEnCurso !== null) {
      return comprobacionEnCurso;
    }
    const turno = avanzarTurno('conexion');
    const promesa = pedir(API.estadoConexion)
      .then(function (resultado) {
        if (!esVigente('conexion', turno)) {
          return;
        }
        if (resultado.estado === 401) {
          mostrarLogin();
          return;
        }
        if (!resultado.ok || !resultado.cuerpo) {
          // La petición a la aplicación local no llegó a contestarse. Es lo que
          // pasa cuando Windows suspende el equipo (por omisión, tras 15
          // minutos sin uso): el navegador corta las peticiones en curso. Eso
          // no prueba que el servidor esté caído, así que no se dice «sin
          // conexión»: se dice que no se pudo comprobar, y se vuelve a
          // comprobar al reanudar o en el siguiente intervalo.
          aplicarEstadoConexion(ESTADO_NO_COMPROBADO);
          return;
        }
        aplicarEstadoConexion(resultado.cuerpo);
      })
      .finally(function () {
        if (comprobacionEnCurso === promesa) {
          comprobacionEnCurso = null;
        }
      });
    comprobacionEnCurso = promesa;
    return promesa;
  }

  /**
   * Pinta un estado comprobado y reacciona a los cambios.
   *
   * Cuando la comunicación vuelve a ser operativa --el servidor regresó o la
   * paciente se volvió a autenticar--, lo clínico y los envíos se consultan de
   * nuevo una vez. No hay reintentos en bucle: la siguiente comprobación es la
   * del temporizador.
   */
  function aplicarEstadoConexion(nuevo) {
    const anterior = estadoConexion;
    estadoConexion = nuevo;
    pintarConectividad(nuevo);

    if (nuevo.autenticacion_central === AUTENTICACION.REQUERIDA) {
      mostrarAvisoSesionCentral(AVISOS[MOTIVO.REAUTENTICACION], true);
    } else if (nuevo.autenticacion_central === AUTENTICACION.DENEGADA) {
      // Reautenticarse no cambia un 403: no se ofrece.
      mostrarAvisoSesionCentral(AVISOS[CLASE.ACCESO_DENEGADO], false);
    } else if (nuevo.autenticacion_central === AUTENTICACION.VIGENTE) {
      ocultarAvisoSesionCentral();
    }

    if (operativa(nuevo) && anterior !== null && !operativa(anterior)) {
      cargarClinico();
      refrescarEnvios();
    }
  }

  function mostrarAvisoSesionCentral(mensaje, ofrecerAccion) {
    ui.avisoSesionCentralTexto.textContent = mensaje;
    ui.avisoSesionCentral.hidden = false;
    ui.botonMostrarReautenticar.hidden = !ofrecerAccion || !ui.formReautenticar.hidden;
    if (!ofrecerAccion) {
      ui.formReautenticar.hidden = true;
    }
  }

  function ocultarAvisoSesionCentral() {
    ui.avisoSesionCentral.hidden = true;
    ui.formReautenticar.hidden = true;
    ui.reautenticarPassword.value = '';
    mostrarNota(ui.mensajeReautenticar, null);
  }

  function abrirReautenticacion() {
    ui.formReautenticar.hidden = false;
    ui.botonMostrarReautenticar.hidden = true;
    mostrarNota(ui.mensajeReautenticar, null);
    ui.reautenticarEmail.focus();
  }

  function cancelarReautenticacion() {
    ui.formReautenticar.hidden = true;
    ui.reautenticarPassword.value = '';
    mostrarNota(ui.mensajeReautenticar, null);
    ui.botonMostrarReautenticar.hidden = false;
    ui.botonMostrarReautenticar.focus();
  }

  /**
   * Vuelve a autenticar **la misma cuenta** sin cerrar la sesión local.
   *
   * La contraseña viaja una vez y se borra del campo en cuanto sale, igual que
   * en el inicio de sesión. Los registros guardados no se tocan: viven en este
   * dispositivo y no dependen del token.
   */
  function manejarReautenticar(evento) {
    evento.preventDefault();
    const correo = ui.reautenticarEmail.value.trim();
    const contrasena = ui.reautenticarPassword.value;
    if (!correo || !contrasena) {
      mostrarNota(ui.mensajeReautenticar, 'Escribe tu correo y tu contraseña.');
      return;
    }

    ui.botonReautenticar.disabled = true;
    const epoca = epocaSesion;
    pedir(API.reautenticar, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ email: correo, password: contrasena })
    })
      .then(function (resultado) {
        ui.reautenticarPassword.value = '';
        if (epoca !== epocaSesion) {
          // Se cerró sesión mientras tanto: esta respuesta ya no es de nadie.
          return;
        }
        if (resultado.estado === 401) {
          mostrarLogin();
          return;
        }
        if (resultado.ok && resultado.cuerpo) {
          ui.reautenticarEmail.value = '';
          aplicarEstadoConexion(resultado.cuerpo);
          return;
        }
        mostrarNota(
          ui.mensajeReautenticar,
          resultado.cuerpo && typeof resultado.cuerpo.detail === 'string'
            ? resultado.cuerpo.detail
            : MENSAJE_SIN_CONEXION
        );
      })
      .finally(function () {
        ui.botonReautenticar.disabled = false;
      });
  }

  // =======================================================================
  // Envio de los registros de movimiento de esta cuenta
  // =======================================================================

  /**
   * Resume el estado real de la cola de **esta cuenta** en una frase.
   *
   * No hay contadores técnicos a la vista: cada frase sale de los conteos que
   * entrega el adaptador y dice solo lo que esos conteos confirman. El envío
   * es manual, así que ninguna frase promete que ocurra solo.
   *
   * Devuelve la frase y la acción que corresponde ofrecer, o `null` si no
   * hay nada que enviar.
   */
  function describirEnvios(estado) {
    if (!estado.inicializado) {
      return { mensaje: 'Todavía no has registrado sesiones de movimientos.', accion: null };
    }

    const pendientes = conteo(estado.pendientes);
    const reintentables = conteo(estado.fallidos_reintentables);
    const enRevision = conteo(estado.fallidos_en_revision);
    const enviados = conteo(estado.enviados);

    const frases = [];
    let accion = null;

    if (reintentables !== 0) {
      frases.push(segun(
        reintentables,
        'No se pudo enviar un registro; continúa guardado en este dispositivo.',
        'No se pudieron enviar {n} registros; continúan guardados en este dispositivo.'
      ));
      accion = 'Reintentar';
    }
    if (pendientes !== 0) {
      frases.push(segun(
        pendientes,
        'Registro guardado, pendiente de envío.',
        '{n} registros guardados, pendientes de envío.'
      ));
      accion = accion || 'Enviar ahora';
    }
    if (enRevision !== 0) {
      frases.push(segun(
        enRevision,
        'Un registro no se pudo enviar y requiere revisión del personal; ' +
          'sigue guardado en este dispositivo.',
        '{n} registros no se pudieron enviar y requieren revisión del personal; ' +
          'siguen guardados en este dispositivo.'
      ));
    }
    if (!frases.length) {
      frases.push(
        enviados !== 0
          ? 'Tus registros fueron enviados.'
          : 'Todavía no has registrado sesiones de movimientos.'
      );
    }

    return { mensaje: frases.join(' '), accion: accion };
  }

  function refrescarEnvios() {
    const turno = avanzarTurno('envios');
    return pedir(API.movimientosEstado).then(function (resultado) {
      if (!esVigente('envios', turno)) {
        return;
      }
      if (resultado.estado === 401) {
        mostrarLogin();
        return;
      }
      if (!resultado.ok || !resultado.cuerpo) {
        mostrarNota(ui.envioEstado, 'No se pudo consultar el estado de tus registros.');
        ui.botonSincronizarMovimientos.hidden = true;
        return;
      }

      const resumen = describirEnvios(resultado.cuerpo);
      mostrarNota(ui.envioEstado, resumen.mensaje);
      ui.botonSincronizarMovimientos.hidden = resumen.accion === null;
      if (resumen.accion !== null) {
        ui.botonSincronizarMovimientos.textContent = resumen.accion;
      }
      // Solo cambia cuando el servidor confirmó una entrega: una conexión
      // disponible o una consulta clínica no la mueven.
      const ultimoEnvio = resultado.cuerpo.ultimo_envio_confirmado;
      mostrarNota(
        ui.envioUltimo,
        ausente(ultimoEnvio) ? null : 'Último envío confirmado por el servidor: ' + fechaLegible(ultimoEnvio) + '.'
      );
    });
  }

  /**
   * Una frase fiel al resultado de una ronda de envío.
   *
   * Lo que se dice es exactamente lo que la API contestó en esa ronda: si no
   * se entregó nada, no se dice que se envió.
   */
  function describirRonda(r) {
    const entregados = conteo(r.entregados);
    const noEnviables = conteo(r.rechazados) + conteo(r.agotados);
    const reintentables = conteo(r.reintentables);
    const frases = [];

    if (conteo(r.seleccionados) === 0) {
      return 'No había registros pendientes de envío.';
    }
    if (entregados !== 0) {
      frases.push(segun(entregados, 'Registro enviado.', '{n} registros enviados.'));
    }
    if (reintentables !== 0 || r.detenida_por_transporte) {
      frases.push('No se pudo enviar; el registro continúa guardado en este dispositivo.');
    }
    if (noEnviables !== 0) {
      frases.push(segun(
        noEnviables,
        'Un registro requiere revisión del personal.',
        '{n} registros requieren revisión del personal.'
      ));
    }
    if (r.detenida_por_credencial) {
      frases.push('Para enviar hace falta volver a iniciar sesión; tus registros siguen guardados.');
    }
    return frases.join(' ') || 'No se envió ningún registro.';
  }

  // =======================================================================
  // Lectura clinica
  // =======================================================================

  /** Descripción legible de un episodio, para el título de su tarjeta. */
  function etiquetaDeEpisodio(episodio) {
    const desde = fechaCortaLegible(episodio.fecha_inicio);
    return 'Embarazo desde ' + desde + ' — ' + estadoLegible(episodio.estado_embarazo);
  }

  /**
   * Texto de la opción del selector. El campo ya se titula «Embarazo», y un
   * <select> nativo no parte líneas: con la etiqueta completa, a 360 px el
   * estado quedaba cortado («— Finalizad»).
   */
  function etiquetaDeOpcion(episodio) {
    const desde = fechaCortaLegible(episodio.fecha_inicio);
    return 'Desde ' + desde + ' — ' + estadoLegible(episodio.estado_embarazo);
  }

  function episodioPorId(id) {
    const todos = (episodios && episodios.todos) || [];
    for (let i = 0; i !== todos.length; i += 1) {
      if (todos[i].id_embarazo === id) {
        return todos[i];
      }
    }
    return null;
  }

  /**
   * Pinta la tarjeta del embarazo de Inicio y fija su contexto.
   *
   * Cuando el adaptador informa ambigüedad **no se llama «actual» a ninguno**
   * y Inicio no muestra lecturas: la paciente puede consultar cualquiera de
   * sus episodios en «Mi historial», pero la interfaz no afirma cuál está en
   * curso, porque el dato no permite decidirlo.
   */
  function pintarEpisodios(datos) {
    episodios = datos;

    // La semana de **hoy** del embarazo en curso, calculada por el adaptador
    // con la aritmética del servidor. Nunca la semana de una lectura, que
    // puede ser de hace meses. Sin topes: si el dato dice 53, se dice 53.
    ui.embarazoSemana.textContent =
      datos.actual && !datos.ambiguo ? texto(datos.semana_actual, NO_DISPONIBLE) : NO_DISPONIBLE;

    const todos = datos.todos || [];
    const anteriores = datos.anteriores || [];
    let nota = null;

    if (datos.ambiguo) {
      idInicio = null;
      ui.embarazoEstado.textContent = 'Sin determinar';
      ui.embarazoInicio.textContent = NO_DISPONIBLE;
      ui.embarazoAnteriores.textContent = NO_DISPONIBLE;
      nota =
        'No se pudo determinar automáticamente cuál es tu embarazo en curso. ' +
        'Puedes consultar cada uno en «Mi historial».';
    } else if (datos.actual) {
      idInicio = datos.actual.id_embarazo;
      ui.embarazoEstado.textContent = estadoLegible(datos.actual.estado_embarazo);
      ui.embarazoInicio.textContent = fechaCortaLegible(datos.actual.fecha_inicio);
      ui.embarazoAnteriores.textContent = String(anteriores.length);
      nota = notaDeFechaProbable(datos.actual, datos.hoy);
    } else if (todos.length) {
      idInicio = null;
      ui.embarazoEstado.textContent = 'Sin embarazo en curso';
      ui.embarazoInicio.textContent = NO_DISPONIBLE;
      ui.embarazoAnteriores.textContent = String(anteriores.length);
      nota = 'No tienes un embarazo en curso registrado.';
    } else {
      idInicio = null;
      ui.embarazoEstado.textContent = NO_DISPONIBLE;
      ui.embarazoInicio.textContent = NO_DISPONIBLE;
      ui.embarazoAnteriores.textContent = '0';
      nota = 'Todavía no hay episodios registrados.';
    }

    mostrarNota(ui.notaEmbarazo, nota);

    // El enlace lleva a los episodios que no son el de Inicio.
    const hayOtros = datos.ambiguo ? todos.length !== 0 : anteriores.length !== 0;
    ui.botonVerAnteriores.hidden = !hayOtros;
    ui.botonVerAnteriores.textContent = datos.ambiguo
      ? 'Ver mis embarazos en «Mi historial»'
      : 'Ver embarazos anteriores en «Mi historial»';

    llenarSelector(todos, datos.actual);
    actualizarBotonDeRegistro();
  }

  /**
   * Un hecho, sin interpretarlo: el episodio sigue registrado en curso y su
   * fecha probable de parto ya pasó. Las fechas son días de calendario en
   * formato ISO, así que se comparan como texto sin conversión horaria.
   */
  function notaDeFechaProbable(actual, hoy) {
    const fpp = actual.fecha_probable_parto;
    if (ausente(fpp) || ausente(hoy) || fpp >= hoy) {
      return null;
    }
    return (
      'La fecha probable de parto (' + fechaCortaLegible(fpp) + ') ya pasó y este ' +
      'embarazo sigue registrado como en curso.'
    );
  }

  /**
   * Prepara el selector de Historial sin tocar Inicio.
   *
   * Conserva la selección de la paciente mientras ese episodio siga en la
   * lista; si no, propone el actual o, sin actual, el más reciente.
   */
  function llenarSelector(todos, actual) {
    const selector = ui.selectorEmbarazo;
    selector.innerHTML = '';

    if (!todos.length) {
      const vacio = document.createElement('option');
      vacio.value = '';
      vacio.textContent = NO_DISPONIBLE;
      selector.appendChild(vacio);
      selector.disabled = true;
      idHistorial = null;
      return;
    }

    todos.forEach(function (episodio) {
      const opcion = document.createElement('option');
      opcion.value = String(episodio.id_embarazo);
      opcion.textContent = etiquetaDeOpcion(episodio);
      selector.appendChild(opcion);
    });
    selector.disabled = false;

    if (idHistorial === null || episodioPorId(idHistorial) === null) {
      idHistorial = actual ? actual.id_embarazo : todos[0].id_embarazo;
    }
    selector.value = String(idHistorial);
  }

  /**
   * El registro va siempre al embarazo de Inicio. Sin un embarazo en curso
   * que venga de `/adaptador/embarazos`, el botón no tiene contra qué
   * episodio registrar nada. La selección de Historial no cuenta.
   */
  function actualizarBotonDeRegistro() {
    ui.botonRegistrarMovimiento.disabled = idInicio === null;
    ui.notaMovimientos.textContent =
      idInicio === null ? TEXTO_SIN_EMBARAZO_EN_CURSO : TEXTO_LISTO_PARA_REGISTRAR;
  }

  /** Estado neutro de todo lo clínico, con el aviso que corresponda. */
  function pintarSinDatosClinicos(clasificacion) {
    const aviso = avisoDe(clasificacion);

    ui.embarazoEstado.textContent = NO_DISPONIBLE;
    ui.embarazoInicio.textContent = NO_DISPONIBLE;
    ui.embarazoSemana.textContent = NO_DISPONIBLE;
    ui.embarazoAnteriores.textContent = NO_DISPONIBLE;
    mostrarNota(ui.notaEmbarazo, aviso);
    ui.botonVerAnteriores.hidden = true;

    limpiarMetricas();
    pintarSemaforo(null, null);

    ui.selectorEmbarazo.disabled = true;
    // Sin lectura clínica confirmada no hay un embarazo autorizado contra el
    // cual registrar nada.
    idInicio = null;
    actualizarBotonDeRegistro();
    mostrarHistorialVacio(aviso);
  }

  /**
   * Pinta la tarjeta de una variable, o su ausencia.
   *
   * `registro` es `undefined` cuando no se sabe (el adaptador no lo envió o
   * no se pudo consultar) y `null` cuando el embarazo **nunca** registró esa
   * variable: son dos frases distintas, y ninguna es cero.
   *
   * La clasificación que acompaña al valor es la de **la lectura de origen**,
   * tal como la entrega el servidor. La API no clasifica cada métrica por
   * separado, y el texto lo dice para que nadie la lea como si lo hiciera.
   * `id_lectura` e `id_sesion` quedan como atributos para trazabilidad y
   * pruebas; no se muestran.
   */
  function pintarTarjeta(t, registro) {
    t.contenedor.removeAttribute('data-id-lectura');
    t.contenedor.removeAttribute('data-id-sesion');
    t.clasificacion.textContent = '';
    t.clasificacion.hidden = true;

    if (registro === undefined) {
      t.valor.textContent = SIN_DATO;
      t.estado.textContent = NO_DISPONIBLE;
      t.fecha.textContent = '';
      t.semana.textContent = '';
      return;
    }
    if (registro === null) {
      t.valor.textContent = SIN_DATO;
      t.estado.textContent = 'Sin registros';
      t.fecha.textContent = '';
      t.semana.textContent = '';
      return;
    }

    t.valor.textContent = medida(registro.valor);
    t.estado.textContent = 'Último registro';
    t.fecha.textContent = 'Registrado el ' + fechaLegible(registro.fecha_hora_captura);
    t.semana.textContent = ausente(registro.semana_gestacion_lectura)
      ? ''
      : 'Semana ' + registro.semana_gestacion_lectura + ' en esa lectura';

    t.clasificacion.appendChild(textoPlano('Clasificación de esa lectura: '));
    t.clasificacion.appendChild(marcaDeSemaforo(registro.codigo_semaforo_lectura));
    t.clasificacion.hidden = false;

    t.contenedor.setAttribute('data-id-lectura', String(registro.id_lectura));
    t.contenedor.setAttribute('data-id-sesion', String(registro.id_sesion));
  }

  function textoPlano(contenido) {
    const nodo = document.createElement('span');
    nodo.textContent = contenido;
    return nodo;
  }

  function limpiarMetricas() {
    Object.keys(ui.tarjetas).forEach(function (clave) {
      pintarTarjeta(ui.tarjetas[clave], undefined);
    });
    ui.ultimaLectura.textContent = SIN_DATO;
    ui.ultimaLecturaMide.textContent = '';
  }

  /**
   * «Tus últimos registros»: el último valor de **cada** variable, cada uno con
   * su fecha y su lectura de origen.
   *
   * Pueden ser momentos distintos y la pantalla no afirma lo contrario. Nada se
   * combina: no hay semáforo común de las tres tarjetas, ni una variable que
   * tome el valor de otra lectura para parecer completa.
   */
  function pintarUltimosRegistros(ultimos) {
    Object.keys(ui.tarjetas).forEach(function (clave) {
      const registro = ultimos && Object.prototype.hasOwnProperty.call(ultimos, clave)
        ? ultimos[clave]
        : undefined;
      pintarTarjeta(ui.tarjetas[clave], registro);
    });
  }

  /** Qué midió una lectura, en palabras. */
  function queMidio(lectura) {
    const partes = [];
    if (!ausente(lectura.hr_valor)) partes.push('frecuencia cardíaca');
    if (!ausente(lectura.spo2_valor)) partes.push('saturación de oxígeno');
    if (!ausente(lectura.mov_valor)) partes.push('movimientos fetales');
    return partes.join(' y ');
  }

  /**
   * La lectura más reciente del embarazo y **su** clasificación.
   *
   * Es una sola lectura, con su instante y su semáforo, tal como los decidió el
   * servidor. No resume las tarjetas: puede que solo haya medido una variable,
   * y así se dice.
   */
  function pintarUltimaLectura(lectura) {
    if (!lectura) {
      ui.ultimaLectura.textContent = SIN_DATO;
      ui.ultimaLecturaMide.textContent = '';
      pintarSemaforo(null, null);
      return;
    }

    ui.ultimaLectura.textContent = fechaLegible(lectura.fecha_hora_captura);
    const midio = queMidio(lectura);
    ui.ultimaLecturaMide.textContent = midio ? 'Midió ' + midio + '.' : '';

    // El nivel llega ya clasificado por la fuente autorizada. Aquí sólo se
    // traduce a una clase CSS.
    pintarSemaforo(lectura.codigo_semaforo, mensajeDeSemaforo(lectura.codigo_semaforo));
  }

  /** Inicio completo a partir del monitoreo del embarazo en curso. */
  function pintarInicio(datos) {
    pintarUltimosRegistros(datos.ultimos_registros);
    pintarUltimaLectura(datos.ultima_lectura);
  }

  /** Texto acompañante del nivel. No es una interpretación clínica. */
  function mensajeDeSemaforo(codigo) {
    if (codigo === 'OK') return 'Dentro de lo esperado';
    if (codigo === 'WARNING') return 'Conviene repetir la medición';
    if (codigo === 'ERROR') return 'Comunícate con tu personal de seguimiento';
    return null;
  }

  function mostrarHistorialVacio(mensaje) {
    ui.historialLista.innerHTML = '';
    const tarjeta = document.createElement('div');
    tarjeta.className = 'result-card estado-vacio';
    const parrafo = document.createElement('p');
    parrafo.className = 'texto-apoyo';
    parrafo.textContent = mensaje;
    tarjeta.appendChild(parrafo);
    ui.historialLista.appendChild(tarjeta);
  }

  function instante(valorIso) {
    const momento = new Date(valorIso).getTime();
    return Number.isNaN(momento) ? 0 : momento;
  }

  /**
   * Orden del historial: la más reciente primero, por (fecha_hora_captura,
   * id_lectura). Es la misma regla con la que el servidor elige la última
   * lectura, así que la primera fila coincide con la de Inicio.
   */
  function masRecientePrimero(a, b) {
    return (
      instante(b.fecha_hora_captura) - instante(a.fecha_hora_captura) ||
      b.id_lectura - a.id_lectura
    );
  }

  function celda(fila, etiqueta, contenido) {
    const td = document.createElement('td');
    td.setAttribute('data-etiqueta', etiqueta);
    if (typeof contenido === 'string') {
      td.textContent = contenido;
    } else {
      td.appendChild(contenido);
    }
    fila.appendChild(td);
  }

  function marcaDeSemaforo(codigo) {
    const marca = document.createElement('span');
    marca.className = 'semaforo-item ' + (CLASES_DE_SEMAFORO[codigo] || '');
    const luz = document.createElement('span');
    luz.className = 'alert-light';
    const nombre = document.createElement('span');
    nombre.textContent = ETIQUETAS_DE_SEMAFORO[codigo] || SIN_DATO;
    marca.appendChild(luz);
    marca.appendChild(nombre);
    return marca;
  }

  /**
   * Pinta todas las lecturas de **un** episodio en una tabla sencilla.
   *
   * Se vacía la lista antes de construirla, de modo que cambiar de episodio no
   * pueda dejar visible ni una fila del anterior. Se listan todas las
   * lecturas, también cuando la última del episodio solo midió movimientos.
   */
  function pintarHistorial(datos) {
    ui.historialLista.innerHTML = '';

    const sesiones = datos.sesiones || [];
    const lecturas = [];
    sesiones.forEach(function (sesion) {
      (sesion.lecturas || []).forEach(function (lectura) {
        lecturas.push(lectura);
      });
    });

    if (!lecturas.length) {
      mostrarHistorialVacio('Este embarazo todavía no tiene lecturas registradas.');
      return;
    }
    lecturas.sort(masRecientePrimero);

    const tarjeta = document.createElement('div');
    tarjeta.className = 'result-card historial-tarjeta';

    const episodio = episodioPorId(datos.id_embarazo);
    const titulo = document.createElement('h3');
    titulo.className = 'subtitulo';
    titulo.textContent = episodio ? etiquetaDeEpisodio(episodio) : 'Embarazo';
    tarjeta.appendChild(titulo);

    const resumen = document.createElement('p');
    resumen.className = 'texto-apoyo';
    resumen.textContent =
      (lecturas.length === 1 ? '1 lectura' : lecturas.length + ' lecturas') +
      ', de la más reciente a la más antigua. «—» indica un valor no registrado.';
    tarjeta.appendChild(resumen);

    const tabla = document.createElement('table');
    tabla.className = 'tabla-lecturas';
    const cabecera = document.createElement('thead');
    const filaCabecera = document.createElement('tr');
    ['Fecha', 'Frecuencia cardíaca', 'Saturación', 'Movimientos', 'Semana', 'Semáforo']
      .forEach(function (nombre) {
        const th = document.createElement('th');
        th.setAttribute('scope', 'col');
        th.textContent = nombre;
        filaCabecera.appendChild(th);
      });
    cabecera.appendChild(filaCabecera);
    tabla.appendChild(cabecera);

    const cuerpo = document.createElement('tbody');
    lecturas.forEach(function (lectura) {
      const fila = document.createElement('tr');
      celda(fila, 'Fecha', fechaLegible(lectura.fecha_hora_captura));
      celda(fila, 'Frecuencia cardíaca', conUnidad(lectura.hr_valor, 'BPM'));
      celda(fila, 'Saturación', conUnidad(lectura.spo2_valor, '%'));
      celda(fila, 'Movimientos', texto(lectura.mov_valor));
      celda(fila, 'Semana', texto(lectura.semana_gestacion));
      celda(fila, 'Semáforo', marcaDeSemaforo(lectura.codigo_semaforo));
      cuerpo.appendChild(fila);
    });
    tabla.appendChild(cuerpo);

    const contenedor = document.createElement('div');
    contenedor.className = 'tabla-contenedor';
    contenedor.appendChild(tabla);
    tarjeta.appendChild(contenedor);

    ui.historialLista.appendChild(tarjeta);
  }

  /**
   * Pide el monitoreo de un episodio. Dentro de una misma ronda de refresco,
   * Inicio e Historial comparten la petición si miran el mismo episodio.
   */
  function pedirMonitoreo(idEmbarazo, compartidas) {
    if (compartidas && compartidas[idEmbarazo]) {
      return compartidas[idEmbarazo];
    }
    const promesa = pedir(API.monitoreo(idEmbarazo)).then(clasificar);
    if (compartidas) {
      compartidas[idEmbarazo] = promesa;
    }
    return promesa;
  }

  /** Contexto de Inicio: la última lectura del embarazo en curso. */
  function cargarInicio(compartidas) {
    const turno = avanzarTurno('inicio');
    const destino = idInicio;

    if (destino === null) {
      limpiarMetricas();
      pintarUltimaLectura(null);
      return Promise.resolve();
    }

    return pedirMonitoreo(destino, compartidas).then(function (clasificacion) {
      if (!esVigente('inicio', turno)) {
        return;
      }
      if (clasificacion.clase === CLASE.SESION_LOCAL_INVALIDA) {
        mostrarLogin();
        return;
      }
      if (clasificacion.clase === CLASE.NO_DISPONIBLE) {
        // Sin conexión o sin token: lo ya mostrado se conserva --cada tarjeta
        // lleva su fecha, así que no se presenta como nuevo-- y se explica.
        avisarContextoConservado(clasificacion);
        return;
      }
      if (clasificacion.clase !== CLASE.DATOS) {
        // Un 403, un 404 o un fallo del servidor dejan el portal abierto: sólo
        // se vacía lo clínico y se explica por qué.
        limpiarMetricas();
        pintarSemaforo(null, null);
        mostrarNota(ui.notaEmbarazo, avisoDe(clasificacion));
        return;
      }
      pintarInicio(clasificacion.datos);
    });
  }

  function avisarContextoConservado(clasificacion) {
    if (clasificacion.motivo === MOTIVO.REAUTENTICACION) {
      // Que el indicador y el aviso con la acción lo reflejen ya, sin esperar
      // al temporizador.
      refrescarEstadoConexion();
    }
    mostrarNota(
      ui.notaEmbarazo,
      clasificacion.motivo === MOTIVO.SIN_CONEXION
        ? TEXTO_SIN_CONEXION_CONTEXTO_CONSERVADO
        : TEXTO_REAUTENTICACION_CONTEXTO_CONSERVADO
    );
  }

  /** Contexto de Historial: todas las lecturas del episodio elegido allí. */
  function cargarHistorial(compartidas) {
    const turno = avanzarTurno('historial');
    const destino = idHistorial;

    if (destino === null) {
      mostrarHistorialVacio('Todavía no hay episodios registrados.');
      return Promise.resolve();
    }

    return pedirMonitoreo(destino, compartidas).then(function (clasificacion) {
      // Una respuesta de una selección ya superada no pisa la vigente.
      if (!esVigente('historial', turno) || destino !== idHistorial) {
        return;
      }
      if (clasificacion.clase === CLASE.SESION_LOCAL_INVALIDA) {
        mostrarLogin();
        return;
      }
      if (clasificacion.clase !== CLASE.DATOS) {
        mostrarHistorialVacio(avisoDe(clasificacion));
        return;
      }
      pintarHistorial(clasificacion.datos);
    });
  }

  /** Pide los episodios y, con ellos, cada contexto por separado. */
  function cargarClinico() {
    const turno = avanzarTurno('clinico');
    return pedir(API.embarazos).then(function (resultado) {
      if (!esVigente('clinico', turno)) {
        return;
      }
      const clasificacion = clasificar(resultado);

      if (clasificacion.clase === CLASE.SESION_LOCAL_INVALIDA) {
        mostrarLogin();
        return;
      }

      if (clasificacion.clase === CLASE.NO_DISPONIBLE && episodios !== null) {
        // Sin conexión (o con reautenticación pendiente) no cambia nada de lo
        // que ya se sabía en esta sesión: el embarazo en curso sigue siendo el
        // mismo, y cada registro mostrado lleva su propia fecha. Se avisa
        // y se conserva el contexto, para que el registro local --que el
        // adaptador admite sin conexión-- siga disponible.
        avisarContextoConservado(clasificacion);
        return;
      }

      if (clasificacion.clase !== CLASE.DATOS) {
        if (clasificacion.clase === CLASE.NO_DISPONIBLE &&
            clasificacion.motivo === MOTIVO.REAUTENTICACION) {
          refrescarEstadoConexion();
        }
        pintarSinDatosClinicos(clasificacion);
        return;
      }

      pintarEpisodios(clasificacion.datos);
      datosConsultadosEn = Date.now();
      mostrarNota(
        ui.datosActualizados,
        'Información consultada al servidor el ' + fechaLegible(new Date(datosConsultadosEn).toISOString()) + '.'
      );

      const compartidas = {};
      return Promise.all([cargarInicio(compartidas), cargarHistorial(compartidas)]);
    });
  }

  /** Todo: estado, envíos y lo clínico. Para entrar, volver a Inicio o «Actualizar». */
  function refrescar() {
    return Promise.all([
      refrescarEstadoConexion(),
      refrescarEnvios(),
      cargarClinico()
    ]);
  }

  /**
   * Lo que se repite cada `INTERVALO_REFRESCO_MS`: el estado de la conexión y
   * de la cola de envíos. Ni una lectura clínica (ver la constante).
   */
  function refrescarPeriodico() {
    return Promise.all([refrescarEstadoConexion(), refrescarEnvios()]);
  }

  /**
   * Al volver a la pestaña. El navegador frena o congela los temporizadores
   * de una pestaña oculta, así que lo mostrado puede ser viejo: se comprueba
   * el estado enseguida y, si es operativo y los datos tienen más de un
   * minuto, se vuelven a pedir.
   */
  function alVolverALaPestana() {
    if (!portalAbierto || document.visibilityState !== 'visible') {
      return Promise.resolve();
    }
    return Promise.all([refrescarEstadoConexion(), refrescarEnvios()]).then(function () {
      const viejos =
        datosConsultadosEn === null || Date.now() - datosConsultadosEn > ANTIGUEDAD_MAXIMA_DATOS_MS;
      if (portalAbierto && operativa(estadoConexion) && viejos) {
        return cargarClinico();
      }
    });
  }

  function iniciarRefrescoPeriodico() {
    detenerRefrescoPeriodico();
    temporizadorRefresco = window.setInterval(refrescarPeriodico, INTERVALO_REFRESCO_MS);
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
   * Registra una sesión de movimiento simulada para el embarazo de Inicio.
   *
   * `idInicio` sólo puede ser el embarazo en curso que vino de
   * `/adaptador/embarazos`, así que esta función nunca fabrica ni adivina un
   * identificador, y la selección de «Mi historial» no la desvía. El
   * adaptador vuelve a comprobar del lado del servidor que ese embarazo es
   * de esta cuenta antes de guardar nada.
   */
  function manejarRegistrarMovimiento() {
    if (idInicio === null) {
      return;
    }
    const destino = idInicio;

    ui.botonRegistrarMovimiento.disabled = true;
    ui.notaMovimientos.textContent = 'Guardando…';

    pedir(API.sesionesSimuladas(destino), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ tipo_sesion: TIPO_SESION_SIMULADA })
    })
      .then(function (resultado) {
        if (resultado.estado === 401) {
          mostrarLogin();
          return;
        }

        actualizarBotonDeRegistro();

        if (resultado.estado === 200 && resultado.cuerpo && resultado.cuerpo.disponible === false) {
          ui.notaMovimientos.textContent = avisoDe({
            clase: CLASE.NO_DISPONIBLE,
            motivo: resultado.cuerpo.motivo
          });
          return;
        }

        if (resultado.ok && resultado.cuerpo && resultado.cuerpo.registrado) {
          ui.notaMovimientos.textContent =
            'Registro guardado en este dispositivo, pendiente de envío.';
          mostrarNota(ui.envioResultado, null);
          refrescarEnvios();
          return;
        }

        // El adaptador escribe un `detail` seguro y explicativo para cada
        // negativa: el dispositivo sin aprovisionar, una semana gestacional
        // fuera del catálogo, un episodio que no es suyo. Mostrarlo es más
        // útil que un texto genérico, y no expone nada: esos mensajes los
        // redacta este proyecto, no el servidor central.
        ui.notaMovimientos.textContent =
          resultado.cuerpo && typeof resultado.cuerpo.detail === 'string'
            ? resultado.cuerpo.detail
            : 'No se pudo guardar el registro. Inténtalo de nuevo más tarde.';
      });
  }

  /**
   * Envía ahora la cola de **esta cuenta**. Una sola ronda real contra el
   * servidor central: lo que se muestra es exactamente lo que la API
   * contestó, nunca un resultado inventado por esta pantalla.
   */
  function manejarSincronizarMovimientos() {
    const etiqueta = ui.botonSincronizarMovimientos.textContent;
    ui.botonSincronizarMovimientos.disabled = true;
    ui.botonSincronizarMovimientos.textContent = 'Enviando…';
    mostrarNota(ui.envioResultado, null);

    pedir(API.movimientosSincronizar, { method: 'POST' })
      .then(function (resultado) {
        if (resultado.estado === 401) {
          mostrarLogin();
          return;
        }

        if (resultado.estado === 200 && resultado.cuerpo && resultado.cuerpo.disponible === false) {
          mostrarNota(
            ui.envioResultado,
            'No se pudo enviar; tus registros continúan guardados en este dispositivo. ' +
              avisoDe({ clase: CLASE.NO_DISPONIBLE, motivo: resultado.cuerpo.motivo })
          );
          return Promise.all([refrescarEnvios(), refrescarEstadoConexion()]);
        }

        if (!resultado.ok || !resultado.cuerpo) {
          mostrarNota(
            ui.envioResultado,
            'No se pudo enviar; tus registros continúan guardados en este dispositivo.'
          );
          return refrescarEnvios();
        }

        mostrarNota(ui.envioResultado, describirRonda(resultado.cuerpo));
        const recargas = [refrescarEnvios()];
        if (resultado.cuerpo.detenida_por_credencial || resultado.cuerpo.detenida_por_transporte) {
          // El envío descubrió un cambio de estado: que el indicador lo diga.
          recargas.push(refrescarEstadoConexion());
        }
        if (conteo(resultado.cuerpo.entregados) !== 0) {
          // Lo entregado ya es parte del embarazo en el servidor central.
          recargas.push(cargarClinico());
        }
        return Promise.all(recargas);
      })
      .finally(function () {
        ui.botonSincronizarMovimientos.disabled = false;
        if (ui.botonSincronizarMovimientos.textContent === 'Enviando…') {
          ui.botonSincronizarMovimientos.textContent = etiqueta;
        }
      });
  }

  // -- Confirmación de cierre ----------------------------------------------

  function abrirDialogoDeCierre(evento) {
    if (evento) {
      evento.preventDefault();
    }
    ui.botonConfirmarCierre.disabled = false;
    if (typeof ui.dialogoCierre.showModal === 'function') {
      ui.dialogoCierre.showModal();
    } else {
      ui.dialogoCierre.setAttribute('open', '');
    }
    ui.botonCancelarCierre.focus();
  }

  function cerrarDialogoDeCierre() {
    if (!ui.dialogoCierre.hasAttribute('open')) {
      return;
    }
    if (typeof ui.dialogoCierre.close === 'function') {
      ui.dialogoCierre.close();
    } else {
      ui.dialogoCierre.removeAttribute('open');
    }
  }

  /** Cancelar: la sesión sigue exactamente como estaba. */
  function cancelarCierre() {
    cerrarDialogoDeCierre();
    ui.botonCerrarSesion.focus();
  }

  /**
   * Confirmar: se detiene todo lo que pudiera repintar datos, se invalida la
   * sesión en el adaptador y se vuelve al login con el panel limpio. Los
   * registros guardados en el dispositivo no se tocan.
   */
  function manejarLogout() {
    ui.botonConfirmarCierre.disabled = true;
    detenerRefrescoPeriodico();
    invalidarTodosLosTurnos();

    pedir(API.cerrarSesion, { method: 'POST' }).then(function () {
      // Se vuelve al inicio de sesion pase lo que pase: si el adaptador no
      // respondio, mantener el portal abierto seria peor.
      mostrarLogin();
    });
  }

  /** Ir a «Mi historial» desde Inicio, mostrando un embarazo que no es el actual. */
  function verEmbarazosAnteriores() {
    const anteriores = (episodios && episodios.anteriores) || [];
    if (anteriores.length && !(episodios && episodios.ambiguo)) {
      idHistorial = anteriores[0].id_embarazo;
      ui.selectorEmbarazo.value = String(idHistorial);
      ui.historialLista.innerHTML = '';
      mostrarHistorialVacio(TEXTO_CARGANDO_LECTURAS);
      cargarHistorial();
    }
    mostrarVista('historial');
  }

  // =======================================================================
  // Arranque
  // =======================================================================

  function conectarEventos() {
    ui.formLogin.addEventListener('submit', manejarLogin);
    ui.botonCerrarSesion.addEventListener('click', abrirDialogoDeCierre);
    ui.botonCancelarCierre.addEventListener('click', cancelarCierre);
    ui.botonConfirmarCierre.addEventListener('click', manejarLogout);
    ui.botonRegistrarMovimiento.addEventListener('click', manejarRegistrarMovimiento);
    ui.botonSincronizarMovimientos.addEventListener('click', manejarSincronizarMovimientos);
    ui.botonVerAnteriores.addEventListener('click', verEmbarazosAnteriores);
    ui.botonActualizarDatos.addEventListener('click', refrescar);
    ui.botonMostrarReautenticar.addEventListener('click', abrirReautenticacion);
    ui.botonCancelarReautenticar.addEventListener('click', cancelarReautenticacion);
    ui.formReautenticar.addEventListener('submit', manejarReautenticar);
    // Volver a la pestaña, reanudar una página congelada o recuperar la red:
    // en los tres casos lo mostrado puede ser viejo y se comprueba enseguida.
    document.addEventListener('visibilitychange', alVolverALaPestana);
    document.addEventListener('resume', alVolverALaPestana);
    if (typeof window.addEventListener === 'function') {
      window.addEventListener('online', alVolverALaPestana);
      window.addEventListener('pageshow', alVolverALaPestana);
    }

    const enlaces = ui.menu.querySelectorAll('a[data-vista]');
    Array.prototype.forEach.call(enlaces, function (enlace) {
      enlace.addEventListener('click', function (evento) {
        evento.preventDefault();
        mostrarVista(enlace.dataset.vista);
      });
    });

    // Cambiar el embarazo aquí sólo afecta a «Mi historial».
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

      idHistorial = elegido;
      // Se vacía antes de pedir, para que no quede a la vista ni una fila del
      // episodio anterior mientras llega la respuesta. Inicio no se toca.
      ui.historialLista.innerHTML = '';
      mostrarHistorialVacio(TEXTO_CARGANDO_LECTURAS);
      cargarHistorial();
    });
  }

  function arrancar() {
    conectarEventos();
    limpiarPanel();
    pintarConectividad(null);

    consultarSesion().then(function (sesion) {
      // Con sesión, el indicador lo pinta `refrescarEstadoConexion`; sin ella,
      // `mostrarLogin` comprueba si el servidor contesta.
      if (sesion) {
        mostrarPortal();
      } else {
        mostrarLogin();
      }
    });
  }

  document.addEventListener('DOMContentLoaded', arrancar);
})();
