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
      embarazo en Historial no repinta Inicio. Cada contexto lleva su propio
      turno: una respuesta que llega tarde, de una peticion ya superada, se
      descarta en lugar de pisar lo que se esta mirando.

   Esta pantalla solo consulta: no registra sesiones ni envia nada.

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
    estadoConexion: '/adaptador/estado-conexion',
    reautenticar: '/adaptador/reautenticar',
    embarazos: '/adaptador/embarazos',
    monitoreo: function (idEmbarazo) {
      // El identificador procede siempre de la lista de episodios que devolvio
      // el adaptador. Esta funcion no lo fabrica ni lo adivina.
      return '/adaptador/embarazos/' + encodeURIComponent(idEmbarazo) + '/monitoreo';
    }
  };

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

  // Cada cuanto se comprueba el estado de la conexion, en milisegundos. **No
  // recarga lo clinico**: cada lectura clinica queda registrada en la
  // auditoria del servidor (SCRUM-98), y repetirla cada veinte segundos
  // llenaba esa traza sin que la paciente hiciera nada. Lo clinico se pide al
  // entrar, al volver a Inicio, al cambiar de embarazo, al volver a la
  // pestaña con datos viejos y al recuperar la conexion o la autenticacion.
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
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  });
  const FORMATO_FECHA = new Intl.DateTimeFormat(IDIOMA, {
    timeZone: ZONA_HORARIA,
    day: 'numeric',
    month: 'short',
    year: 'numeric'
  });
  // Una fecha de calendario («2026-03-19») es un día, no un instante: se
  // construye como medianoche UTC y se formatea en UTC, así que ninguna zona
  // horaria puede moverla al día anterior.
  const FORMATO_DIA_CALENDARIO = new Intl.DateTimeFormat(IDIOMA, {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    year: 'numeric'
  });
  // Marcas del eje temporal de las gráficas: día, mes y año corto.
  const FORMATO_EJE = new Intl.DateTimeFormat(IDIOMA, {
    timeZone: ZONA_HORARIA,
    day: 'numeric',
    month: 'short',
    year: '2-digit'
  });
  // Hora de una sesión, bajo su fecha: distingue dos sesiones del mismo día.
  const FORMATO_HORA = new Intl.DateTimeFormat(IDIOMA, {
    timeZone: ZONA_HORARIA,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  });

  // Las tres gráficas: mismas variables que las tarjetas. El color es de
  // marca, no clínico; la forma sigue el tipo de dato. Frecuencia cardíaca y
  // saturación se toman en sesiones de varias lecturas seguidas: una
  // dispersión con un punto por lectura, agrupada por sesión. Los
  // movimientos, una barra por registro, ocupan toda la fila.
  const GRAFICAS = [
    { clave: 'frecuencia_cardiaca', titulo: 'Frecuencia cardíaca materna', unidad: 'BPM',
      tipo: 'dispersion', clase: 'grafica-rosa', icono: 'i-corazon', circulo: 'rosa' },
    { clave: 'saturacion_oxigeno', titulo: 'Saturación de oxígeno', unidad: '%',
      tipo: 'dispersion', clase: 'grafica-morada', icono: 'i-pulmones', circulo: 'morado' },
    { clave: 'movimientos_fetales', titulo: 'Movimientos fetales', unidad: 'movimientos',
      tipo: 'barras', clase: 'grafica-lila', icono: 'i-huellas', circulo: 'lila', ancha: true }
  ];

  // Con la gráfica de movimientos a todo el ancho de la fila (mismo corte que
  // styles.css), su lienzo es más ancho; en una sola columna, el normal.
  const MEDIA_GRAFICA_ANCHA = '(min-width: 769px)';

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
    'consultar tu información.';
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
    WARNING: 'Amarillo',
    ERROR: 'Rojo'
  };

  // Nombre legible del estado de un episodio. Un estado desconocido se
  // muestra tal cual llega.
  const ESTADOS_DE_EMBARAZO = {
    ACTIVO: 'En curso',
    FINALIZADO: 'Finalizado',
    SUSPENDIDO: 'Suspendido'
  };

  const TEXTO_CARGANDO_LECTURAS = 'Cargando lecturas…';
  const TEXTO_SIN_CONEXION_CONTEXTO_CONSERVADO =
    'Sin conexión con el servidor. Se muestra la última información consultada.';
  const TEXTO_REAUTENTICACION_CONTEXTO_CONSERVADO =
    'Se muestra la última información consultada. Para actualizarla, vuelve a ' +
    'iniciar sesión.';

  // =======================================================================
  // Referencias al DOM
  // =======================================================================

  const ui = {
    menu: document.getElementById('main-menu'),
    areaCuenta: document.getElementById('area-cuenta'),
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
    embarazoFpp: document.getElementById('embarazo-fpp'),
    embarazoSemana: document.getElementById('embarazo-semana'),
    embarazoSemanaEtiqueta: document.getElementById('embarazo-semana-etiqueta'),
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

    selectorEmbarazo: document.getElementById('selector-embarazo'),
    historialLista: document.getElementById('historial-lista'),
    graficasInicio: document.getElementById('graficas-inicio'),
    periodoInicio: document.getElementById('periodo-inicio'),
    periodoHistorial: document.getElementById('periodo-historial'),

    botonGuardarResumen: document.getElementById('btn-guardar-resumen'),
    mensajeResumen: document.getElementById('resumen-mensaje'),
    botonDescargarPdf: document.getElementById('btn-descargar-pdf'),
    estadoDescarga: document.getElementById('resumen-descarga-estado'),
    alcanceResumen: document.getElementById('resumen-alcance-resumen'),
    alcanceCompleto: document.getElementById('resumen-alcance-completo'),
    botonVolverHistorial: document.getElementById('btn-volver-historial'),
    resumenDocumento: document.getElementById('resumen-documento')
  };

  /** Los elementos de la tarjeta de una variable, por el prefijo de sus `id`. */
  function tarjeta(prefijo) {
    return {
      contenedor: document.getElementById('tarjeta-' + prefijo),
      valor: document.getElementById(prefijo + '-value'),
      estado: document.getElementById(prefijo + '-status'),
      fecha: document.getElementById(prefijo + '-fecha'),
      semana: document.getElementById(prefijo + '-semana')
    };
  }

  const VISTAS = ['login', 'inicio', 'historial', 'manual', 'acerca', 'resumen'];

  let temporizadorRefresco = null;

  // Último estado comprobado de la comunicación con el servidor, o null si
  // todavía no se sabe. Solo lo escribe `refrescarEstadoConexion`.
  let estadoConexion = null;

  // Una sola comprobación de estado en vuelo: el temporizador, la vuelta a la
  // pestaña y un aviso de reautenticación no abren tres peticiones a la vez.
  let comprobacionEnCurso = null;

  // `ultimo_envio_confirmado` de esta sesión tal como se vio la última vez, o
  // `undefined` mientras no hay línea base. Llega con cada comprobación de
  // estado (lectura local, sin red) y solo sirve para una cosa: si cambia, el
  // envío automático acaba de entregar una captura nueva y lo clínico se
  // recarga una vez. Se vacía al cerrar sesión: nunca se compara el valor de
  // una cuenta con el de otra.
  let envioConfirmadoVisto = undefined;

  // Cuándo se recibió por última vez información clínica del servidor
  // (milisegundos), o null. Es «última actualización de los datos».
  let datosConsultadosEn = null;

  // Series ya recibidas, para volver a dibujar al cambiar el período sin
  // pedir nada a la red. Se vacían al cerrar sesión.
  let seriesInicio = null;
  let seriesHistorial = null;

  // Si el portal privado está abierto, y un número que cambia en cada cierre
  // de sesión: una respuesta que vuelve con otra época no pinta nada.
  let portalAbierto = false;
  let epocaSesion = 0;

  // Episodios que el adaptador entrego en la ultima consulta. Todo
  // identificador que se usa despues sale de aqui: no se construye a partir
  // de nada escrito en la pagina.
  let episodios = null;

  // Contexto de Inicio: el embarazo en curso, o ninguno si no existe o es
  // ambiguo.
  let idInicio = null;

  // Contexto de Historial: la seleccion propia de esa vista.
  let idHistorial = null;

  // Lo que «Guardar mi resumen» puede usar: el monitoreo del embarazo elegido
  // en Historial tal como llegó, con el episodio y el momento en que se
  // recibió. Es null mientras se carga, cambia o falla la selección: el
  // resumen nunca mezcla un embarazo con otro ni se arma a medio cargar. Solo
  // vive en memoria y se vacía al cerrar sesión.
  let instantaneaHistorial = null;

  // Turnos. Cada peticion anota el turno vigente de su contexto y, al volver,
  // solo se pinta si sigue siendo el vigente. Cerrar sesion avanza todos.
  // `resumen` avanza cada vez que el resumen abierto cambia, se cierra o se
  // sustituye: una descarga del PDF pertenece al turno en que se pidió.
  const turnos = { clinico: 0, inicio: 0, historial: 0, conexion: 0, resumen: 0 };

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

    // El resumen es parte de «Mi historial»: su enlace sigue marcado.
    const seccionDelMenu = destino === 'resumen' ? 'historial' : destino;
    const enlaces = ui.menu ? ui.menu.querySelectorAll('a[data-vista]') : [];
    Array.prototype.forEach.call(enlaces, function (enlace) {
      enlace.classList.toggle('activo', enlace.dataset.vista === seccionDelMenu);
    });

    // El resumen es una instantánea: al salir de su vista se descarta, y la
    // siguiente vez se arma de nuevo con lo que esté cargado entonces.
    if (destino !== 'resumen') {
      limpiarResumen();
    }
    marcarVistaDeResumen(destino === 'resumen');

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
  }

  /**
   * Devuelve el panel a su estado neutro.
   *
   * Se llama al cerrar sesion, para que ningun dato de una sesion quede
   * visible en la siguiente.
   */
  function limpiarPanel() {
    ui.saludo.textContent = '¡Hola!';
    seriesHistorial = null;

    ui.embarazoEstado.textContent = NO_DISPONIBLE;
    ui.embarazoInicio.textContent = NO_DISPONIBLE;
    ui.embarazoFpp.textContent = NO_DISPONIBLE;
    ui.embarazoSemanaEtiqueta.textContent = 'Semana actual';
    ui.embarazoSemana.textContent = NO_DISPONIBLE;
    ui.embarazoAnteriores.textContent = NO_DISPONIBLE;
    mostrarNota(ui.notaEmbarazo, null);
    ui.botonVerAnteriores.hidden = true;

    limpiarMetricas();
    pintarSemaforo(null, null);

    ocultarAvisoSesionCentral();
    estadoConexion = null;
    envioConfirmadoVisto = undefined;
    datosConsultadosEn = null;

    // Nada de un episodio puede sobrevivir a un cierre de sesión.
    episodios = null;
    idInicio = null;
    idHistorial = null;

    ui.selectorEmbarazo.innerHTML = '';
    const vacio = document.createElement('option');
    vacio.value = '';
    vacio.textContent = NO_DISPONIBLE;
    ui.selectorEmbarazo.appendChild(vacio);
    ui.selectorEmbarazo.disabled = true;
    ui.historialLista.innerHTML = '';
    fijarInstantaneaHistorial(null);
    limpiarResumen();
    marcarVistaDeResumen(false);
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

  /** Si con este estado se puede consultar al servidor. */
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
   * Reacciona a un estado comprobado.
   *
   * No hay indicador permanente: solo se avisa cuando algo falta. Si el
   * servidor no responde, lo mostrado se conserva y se dice. Un servidor que
   * contesta pero rechaza el token **no** es «sin conexión»: lo que falta es
   * volver a autenticarse, y eso tiene su propio aviso. Si ni la aplicación
   * local contestó (equipo suspendido), no se afirma nada.
   *
   * Lo clínico se consulta de nuevo **una vez** en dos casos: cuando la
   * comunicación vuelve a ser operativa --el servidor regresó o la paciente se
   * volvió a autenticar--, lo que además reemplaza el aviso; y cuando
   * `ultimo_envio_confirmado` cambió, porque el envío automático acaba de
   * entregar una captura nueva. Nunca por el mero paso del tiempo: no hay
   * sondeo clínico. La siguiente comprobación es la del temporizador.
   */
  function aplicarEstadoConexion(nuevo) {
    const anterior = estadoConexion;
    estadoConexion = nuevo;

    if (nuevo.api_central === API_CENTRAL.NO_DISPONIBLE ||
        nuevo.api_central === API_CENTRAL.CON_ERRORES) {
      mostrarNota(ui.notaEmbarazo, TEXTO_SIN_CONEXION_CONTEXTO_CONSERVADO);
    }

    if (nuevo.autenticacion_central === AUTENTICACION.REQUERIDA) {
      mostrarAvisoSesionCentral(AVISOS[MOTIVO.REAUTENTICACION], true);
    } else if (nuevo.autenticacion_central === AUTENTICACION.DENEGADA) {
      // Reautenticarse no cambia un 403: no se ofrece.
      mostrarAvisoSesionCentral(AVISOS[CLASE.ACCESO_DENEGADO], false);
    } else if (nuevo.autenticacion_central === AUTENTICACION.VIGENTE) {
      ocultarAvisoSesionCentral();
    }

    const volvio = operativa(nuevo) && anterior !== null && !operativa(anterior);
    const entregado = hayEnvioConfirmadoNuevo(nuevo);
    if (volvio || entregado) {
      cargarClinico();
    }
  }

  /**
   * ¿Confirmó el servidor una entrega nueva desde la última comprobación?
   *
   * Solo cuenta un estado que trae el campo: si la comprobación falló (sin
   * respuesta de la aplicación local) o viene de otra ruta, la línea base se
   * conserva y no se supone nada. La primera observación de la sesión es la
   * línea base, no una entrega nueva. Un mismo valor repetido no vuelve a
   * contar.
   */
  function hayEnvioConfirmadoNuevo(estado) {
    if (!Object.prototype.hasOwnProperty.call(estado, 'ultimo_envio_confirmado')) {
      return false;
    }
    const valor = estado.ultimo_envio_confirmado;
    const visto = envioConfirmadoVisto;
    envioConfirmadoVisto = valor;
    return visto !== undefined && valor !== visto && !ausente(valor);
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
   * en el inicio de sesión.
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
    const inicioAnterior = idInicio;
    episodios = datos;

    // La semana de **hoy** del embarazo en curso, calculada por el adaptador
    // con la aritmética del servidor. Nunca la semana de una lectura, que
    // puede ser de hace meses. Si el adaptador no la publica (la fecha
    // probable de parto registrada ya pasó), la fila pasa a mostrar la semana
    // del último registro cuando llegue el monitoreo: ver
    // `pintarSemanaDelUltimoRegistro`.
    const conSemanaActual = datos.actual && !datos.ambiguo && !ausente(datos.semana_actual);
    ui.embarazoSemanaEtiqueta.textContent = conSemanaActual
      ? 'Semana actual'
      : 'Semana en el último registro';
    const mismoEpisodio = Boolean(datos.actual) && inicioAnterior === datos.actual.id_embarazo;
    if (conSemanaActual) {
      ui.embarazoSemana.textContent = String(datos.semana_actual);
    } else if (!mismoEpisodio) {
      // Si es el mismo embarazo, se conserva la semana del último registro
      // ya mostrada hasta que llegue el monitoreo: borrarla dejaba la fila en
      // «No disponible» junto a unas tarjetas que sí tienen datos.
      ui.embarazoSemana.textContent = NO_DISPONIBLE;
    }

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

    // Fecha probable de parto registrada, sin cambiar el estado del episodio.
    const actual = !datos.ambiguo && datos.actual ? datos.actual : null;
    ui.embarazoFpp.textContent = actual && !ausente(actual.fecha_probable_parto)
      ? fechaCortaLegible(actual.fecha_probable_parto)
      : NO_DISPONIBLE;

    // El enlace lleva a los episodios que no son el de Inicio.
    const hayOtros = datos.ambiguo ? todos.length !== 0 : anteriores.length !== 0;
    ui.botonVerAnteriores.hidden = !hayOtros;
    ui.botonVerAnteriores.textContent = datos.ambiguo
      ? 'Ver mis embarazos en «Mi historial»'
      : 'Ver embarazos anteriores en «Mi historial»';

    llenarSelector(todos, datos.actual);
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

  /** Estado neutro de todo lo clínico, con el aviso que corresponda. */
  function pintarSinDatosClinicos(clasificacion) {
    const aviso = avisoDe(clasificacion);

    ui.embarazoEstado.textContent = NO_DISPONIBLE;
    ui.embarazoInicio.textContent = NO_DISPONIBLE;
    ui.embarazoFpp.textContent = NO_DISPONIBLE;
    ui.embarazoSemana.textContent = NO_DISPONIBLE;
    ui.embarazoAnteriores.textContent = NO_DISPONIBLE;
    mostrarNota(ui.notaEmbarazo, aviso);
    ui.botonVerAnteriores.hidden = true;

    limpiarMetricas();
    pintarSemaforo(null, null);

    ui.selectorEmbarazo.disabled = true;
    // Sin lectura clínica confirmada no hay un embarazo en curso que mostrar.
    idInicio = null;
    mostrarHistorialVacio(aviso);
  }

  /**
   * Pinta la tarjeta de una variable, o su ausencia.
   *
   * `registro` es `undefined` cuando no se sabe (el adaptador no lo envió o
   * no se pudo consultar) y `null` cuando el embarazo **nunca** registró esa
   * variable: son dos frases distintas, y ninguna es cero.
   *
   * **Tarjeta neutral.** La API solo clasifica cada lectura en su conjunto, no
   * cada métrica: poner aquí el semáforo de la lectura de origen lo haría pasar
   * por una clasificación de esta variable. El semáforo vive en el bloque de
   * la lectura más reciente, con su fecha y su alcance. `id_lectura` e
   * `id_sesion` quedan como atributos para trazabilidad y pruebas; no se
   * muestran.
   */
  function pintarTarjeta(t, registro) {
    t.contenedor.removeAttribute('data-id-lectura');
    t.contenedor.removeAttribute('data-id-sesion');

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

    t.contenedor.setAttribute('data-id-lectura', String(registro.id_lectura));
    t.contenedor.setAttribute('data-id-sesion', String(registro.id_sesion));
  }

  function limpiarMetricas() {
    Object.keys(ui.tarjetas).forEach(function (clave) {
      pintarTarjeta(ui.tarjetas[clave], undefined);
    });
    // Las gráficas de Inicio son las mismas lecturas: se van con ellas.
    seriesInicio = null;
    dibujarGraficas(ui.graficasInicio, null);
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
    pintarSemanaDelUltimoRegistro(datos.ultima_lectura);
    seriesInicio = datos.series || null;
    dibujarGraficas(ui.graficasInicio, seriesInicio, periodoElegido(ui.periodoInicio));
  }

  function periodoElegido(selector) {
    return selector && selector.value === 'ultimos30' ? 'ultimos30' : 'todo';
  }

  /** Un icono del juego de la página, dentro de un círculo de color de marca. */
  function iconoEnCirculo(nombre, circulo) {
    const envoltura = document.createElement('span');
    envoltura.className = 'icono-circulo ' + circulo;
    const motor = typeof window !== 'undefined' ? window.FetalAlertGraficas : undefined;
    if (motor) {
      envoltura.appendChild(motor.icono(nombre));
    }
    return envoltura;
  }

  /** ¿La gráfica ancha tiene hoy la fila entera? Sin `matchMedia`, no. */
  function pantallaAncha() {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function' &&
      window.matchMedia(MEDIA_GRAFICA_ANCHA).matches;
  }

  /**
   * Las tres gráficas de un embarazo, a partir de las series del adaptador.
   *
   * Solo se traducen textos (fecha y valor, en español y hora de Panamá) y se
   * pasa la sesión de cada lectura; lo que se dibuja --y cómo se agrupan por
   * sesión las lecturas de FC y SpO2-- lo decide graficas.js con esos mismos
   * registros, sin resumir ninguno.
   * Sin series (no llegaron, o no se pudieron consultar) no se dibuja nada.
   */
  function dibujarGraficas(contenedor, series, periodo) {
    if (!contenedor) {
      return;
    }
    contenedor.innerHTML = '';
    if (!series) {
      return;
    }
    const motor = typeof window !== 'undefined' ? window.FetalAlertGraficas : undefined;
    const ancha = pantallaAncha();
    GRAFICAS.forEach(function (g) {
      const tarjeta = document.createElement('article');
      tarjeta.className = g.ancha ? 'tarjeta grafica grafica-ancha' : 'tarjeta grafica';
      const cabecera = document.createElement('div');
      cabecera.className = 'grafica-cabecera';
      cabecera.appendChild(iconoEnCirculo(g.icono, g.circulo));
      const titulo = document.createElement('h4');
      titulo.textContent = g.titulo;
      cabecera.appendChild(titulo);
      tarjeta.appendChild(cabecera);
      const cuerpo = document.createElement('div');
      tarjeta.appendChild(cuerpo);
      contenedor.appendChild(tarjeta);

      const serie = series[g.clave];
      if (!serie || !motor) {
        const nota = document.createElement('p');
        nota.className = 'grafica-periodo';
        nota.textContent = serie ? 'No se pudo preparar la gráfica.' : NO_DISPONIBLE;
        cuerpo.appendChild(nota);
        return;
      }
      motor.dibujar(cuerpo, {
        titulo: g.titulo,
        unidad: g.unidad,
        tipo: g.tipo,
        clase: g.clase,
        periodo: periodo,
        ancha: Boolean(g.ancha) && ancha,
        formatoEje: function (ms) {
          return FORMATO_EJE.format(new Date(ms));
        },
        formatoSesion: function (ms) {
          const momento = new Date(ms);
          return { fecha: FORMATO_EJE.format(momento), hora: FORMATO_HORA.format(momento) };
        },
        puntos: (serie.puntos || []).map(function (p) {
          return {
            instante: new Date(p.fecha_hora_captura).getTime(),
            valor: p.valor,
            idSesion: p.id_sesion,
            textoValor: conUnidad(p.valor, g.unidad),
            textoFecha: fechaLegible(p.fecha_hora_captura),
            textoFechaCorta: fechaCortaLegible(p.fecha_hora_captura),
            semana: p.semana_gestacion_lectura
          };
        })
      });
    });
  }

  /**
   * Cuando el adaptador no publica una semana actual (la fecha probable de
   * parto registrada ya pasó), la fila de semana muestra la del **último
   * registro**, rotulada así y con su fecha. No es una alerta: es lo único
   * que el dato permite afirmar sin presentar un calendario vencido como
   * vigente.
   */
  function pintarSemanaDelUltimoRegistro(lectura) {
    if (!episodios || !episodios.actual || episodios.ambiguo || !ausente(episodios.semana_actual)) {
      return;
    }
    ui.embarazoSemanaEtiqueta.textContent = 'Semana en el último registro';
    ui.embarazoSemana.textContent = lectura && !ausente(lectura.semana_gestacion)
      ? lectura.semana_gestacion + ' (' + fechaCortaLegible(lectura.fecha_hora_captura) + ')'
      : 'Sin registros';
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
    seriesHistorial = null;
    // Cargando, sin episodio o con error: no hay nada coherente que resumir.
    fijarInstantaneaHistorial(null);
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
      ' en este embarazo. Cada gráfica muestra solo los registros de su medición.';
    tarjeta.appendChild(resumen);

    const graficas = document.createElement('div');
    graficas.className = 'graficas';
    tarjeta.appendChild(graficas);
    seriesHistorial = datos.series || null;
    dibujarGraficas(graficas, seriesHistorial, periodoElegido(ui.periodoHistorial));

    // La tabla completa, a un toque: todas las lecturas con su semáforo.
    const detalle = document.createElement('details');
    detalle.className = 'historial-detalle';
    const titular = document.createElement('summary');
    titular.textContent = 'Ver ' + (lecturas.length === 1 ? 'la lectura' : 'las ' + lecturas.length + ' lecturas') +
      ' en tabla (de la más reciente a la más antigua)';
    detalle.appendChild(titular);
    const leyenda = document.createElement('p');
    leyenda.className = 'texto-apoyo';
    leyenda.textContent = '«—» indica un valor no registrado.';
    detalle.appendChild(leyenda);

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
    detalle.appendChild(contenedor);
    tarjeta.appendChild(detalle);

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
    // Mientras llega la respuesta no se puede generar el resumen: lo que hay
    // en pantalla está a punto de reemplazarse.
    fijarInstantaneaHistorial(null);

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
      // Después de pintar: `pintarHistorial` vacía la lista (y con ella la
      // instantánea) antes de construirla.
      const episodio = episodioPorId(destino);
      if (episodio && clasificacion.datos && clasificacion.datos.id_embarazo === destino) {
        fijarInstantaneaHistorial({
          idEmbarazo: destino,
          datos: clasificacion.datos,
          episodio: episodio,
          // La semana de hoy solo existe para el embarazo en curso, y solo si
          // el adaptador la publicó. Nunca se calcula aquí.
          semanaActual: esEmbarazoEnCurso(destino) && !ausente(episodios.semana_actual)
            ? episodios.semana_actual
            : null,
          hoy: episodios && !ausente(episodios.hoy) ? episodios.hoy : null,
          consultadoEn: Date.now()
        });
      }
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
        // y se conserva lo mostrado.
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

      const compartidas = {};
      return Promise.all([cargarInicio(compartidas), cargarHistorial(compartidas)]);
    });
  }

  /** Estado y lo clínico. Para entrar o volver a Inicio. */
  function refrescar() {
    return Promise.all([
      refrescarEstadoConexion(),
      cargarClinico()
    ]);
  }

  /**
   * Lo que se repite cada `INTERVALO_REFRESCO_MS`: el estado de la conexión.
   * Ni una lectura clínica (ver la constante).
   */
  function refrescarPeriodico() {
    return refrescarEstadoConexion();
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
    return refrescarEstadoConexion().then(function () {
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
  // «Guardar mi resumen»
  //
  // Un documento de UN embarazo de UNA cuenta, armado con la instantánea de
  // «Mi historial». Primero se construye un **modelo** --solo textos ya
  // formateados, con las mismas reglas que el resto de la pantalla-- y de él
  // salen las dos presentaciones: la vista previa en la página y el PDF que
  // se descarga (resumen_pdf.js + pdfmake, servidos por el propio portal).
  // Así, lo que se ve es exactamente lo que se descarga.
  //
  // No se envía nada a ningún servicio y no se guarda nada en el navegador.
  // La vista previa se construye con `textContent`, así que ningún dato puede
  // convertirse en marcado; el PDF recibe los mismos textos como texto.
  //
  // Las reglas: el semáforo es el de la fuente autorizada y de UNA lectura;
  // cada variable lleva su propia fecha; el cero es un valor y la ausencia no
  // lo es.
  // =======================================================================

  // Lecturas de la tabla en el modo «Resumen», como máximo: el documento debe
  // caber en una o dos páginas. No limita nada de la aplicación.
  const LECTURAS_EN_RESUMEN = 10;

  // Un día en milisegundos, escrito entero: test_gestante_frontend.py vigila
  // que en este archivo no aparezca ninguna cifra de umbral clínico.
  const DIA_MS = 86400000;

  // Los mismos nombres que el selector «Período de las gráficas».
  const PERIODOS = {
    todo: 'Todo el embarazo',
    ultimos30: 'Últimos 30 días con registros'
  };

  // Qué lleva el documento: el resumen breve o todas las lecturas del período.
  const ALCANCE = { RESUMEN: 'resumen', COMPLETO: 'completo' };

  const TITULOS_DEL_DOCUMENTO = {
    resumen: 'Resumen de mis registros',
    completo: 'Historial de mis registros'
  };

  const TITULO_RESUMEN = 'FetalAlert — Mi resumen';
  const TITULO_PAGINA = typeof document.title === 'string' ? document.title : null;

  const MENSAJES_RESUMEN = {
    cargando: 'Espera a que terminen de cargarse las lecturas de este embarazo.',
    sinLecturas:
      'Este embarazo todavía no tiene lecturas registradas, así que no hay un ' +
      'resumen que guardar.',
    preparando: 'Preparando el PDF…',
    fallo: 'No se pudo preparar el PDF. Inténtalo de nuevo.'
  };

  // El motor de PDF pesa unos 2 MB: se pide al propio portal la primera vez
  // que alguien descarga, no en cada visita. Nunca a una CDN: tiene que
  // funcionar sin internet.
  const MOTOR_PDF = ['/vendor/pdfmake.min.js', '/vendor/vfs_fonts.js'];

  const MEDICIONES_RESUMEN = [
    { clave: 'frecuencia_cardiaca', titulo: 'Frecuencia cardíaca materna', unidad: 'BPM' },
    { clave: 'saturacion_oxigeno', titulo: 'Saturación de oxígeno (SpO₂)', unidad: '%' },
    { clave: 'movimientos_fetales', titulo: 'Movimientos fetales', unidad: 'movimientos' }
  ];

  const COLUMNAS_DE_LECTURAS = [
    'Fecha y hora', 'Frecuencia cardíaca', 'SpO₂', 'Movimientos fetales', 'Semana', 'Clasificación'
  ];

  // El instante exacto de una descarga, con segundos, en hora de Panamá.
  const FORMATO_DESCARGA = new Intl.DateTimeFormat(IDIOMA, {
    timeZone: ZONA_HORARIA, day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
  });

  // Fecha del día en Panamá como AAAA-MM-DD, para el nombre del archivo.
  const FORMATO_FECHA_ARCHIVO = new Intl.DateTimeFormat('en-CA', {
    timeZone: ZONA_HORARIA, year: 'numeric', month: '2-digit', day: '2-digit'
  });

  // El resumen abierto en pantalla: su instantánea, su período, su alcance y
  // el modelo que se ve y se descarga. null fuera de la vista del resumen.
  let resumenAbierto = null;

  // La carga del motor de PDF, compartida entre clics. Se reinicia si falla.
  // pdfmake registra sus fuentes por dentro (`addVirtualFileSystem`), así que
  // «listo» es haber cargado los dos archivos, en orden, sin error.
  let motorPdf = null;

  /** ¿Es este el embarazo que Inicio llama «en curso»? Con ambigüedad, ninguno. */
  function esEmbarazoEnCurso(id) {
    return Boolean(episodios && !episodios.ambiguo && episodios.actual &&
      episodios.actual.id_embarazo === id);
  }

  /**
   * Fija lo que el resumen puede usar y, con ello, si el botón se habilita.
   * Cualquier cambio de contexto borra también el último mensaje.
   */
  function fijarInstantaneaHistorial(valor) {
    instantaneaHistorial = valor;
    if (ui.botonGuardarResumen) {
      ui.botonGuardarResumen.disabled = !instantaneaUsable();
    }
    mostrarMensajeResumen(null);
  }

  function instantaneaUsable() {
    return Boolean(portalAbierto && instantaneaHistorial &&
      instantaneaHistorial.idEmbarazo === idHistorial);
  }

  function mostrarMensajeResumen(mensaje) {
    if (ui.mensajeResumen) {
      mostrarNota(ui.mensajeResumen, mensaje);
    }
  }

  function mostrarEstadoDescarga(mensaje) {
    if (ui.estadoDescarga) {
      mostrarNota(ui.estadoDescarga, mensaje);
    }
  }

  /**
   * Da por terminada cualquier descarga pedida sobre el resumen anterior: no
   * se entregará, y sus errores o su final no tocan lo que venga después. El
   * botón queda listo para una descarga nueva.
   */
  function invalidarDescarga() {
    avanzarTurno('resumen');
    if (ui.botonDescargarPdf) {
      ui.botonDescargarPdf.disabled = false;
    }
    mostrarEstadoDescarga(null);
  }

  /** Vacía el documento del resumen. Nada de él sobrevive a una salida. */
  function limpiarResumen() {
    resumenAbierto = null;
    if (ui.resumenDocumento) {
      ui.resumenDocumento.textContent = '';
    }
    invalidarDescarga();
  }

  /**
   * Con la vista del resumen abierta, una impresión (Ctrl+P) muestra solo el
   * documento (styles.css, `body.con-resumen`). Fuera de ella, todo queda como
   * estaba.
   */
  function marcarVistaDeResumen(activa) {
    if (document.body && document.body.classList) {
      document.body.classList.toggle('con-resumen', activa);
    }
    if (TITULO_PAGINA !== null) {
      document.title = activa ? TITULO_RESUMEN : TITULO_PAGINA;
    }
  }

  /** Fecha y hora legibles de un instante en milisegundos. */
  function momentoLegible(ms) {
    return FORMATO_FECHA_HORA.format(new Date(ms));
  }

  /** Todas las lecturas del monitoreo, de la más reciente a la más antigua. */
  function lecturasDe(datos) {
    const lecturas = [];
    (datos.sesiones || []).forEach(function (sesion) {
      (sesion.lecturas || []).forEach(function (lectura) {
        lecturas.push(lectura);
      });
    });
    return lecturas.sort(masRecientePrimero);
  }

  /**
   * Las lecturas del período elegido. «Últimos 30 días con registros» se
   * cuenta hacia atrás desde la lectura más reciente del embarazo, no desde
   * hoy: la misma idea que las gráficas, sin sugerir mediciones recientes en
   * un embarazo antiguo. `lecturas` llega ya ordenada.
   */
  function lecturasDelPeriodo(lecturas, periodo) {
    if (periodo !== 'ultimos30' || !lecturas.length) {
      return lecturas.slice();
    }
    const desde = instante(lecturas[0].fecha_hora_captura) - 30 * DIA_MS;
    return lecturas.filter(function (lectura) {
      return instante(lectura.fecha_hora_captura) >= desde;
    });
  }

  function cantidadDeLecturas(n) {
    return n === 1 ? '1 lectura' : n + ' lecturas';
  }

  /** Qué cubre la tabla, dicho sin exagerar. */
  function textoCobertura(incluidas, delPeriodo, total, periodo) {
    const partes = [];
    if (delPeriodo === incluidas) {
      partes.push(
        incluidas === 1
          ? 'Se incluye la única lectura del período.'
          : 'Se incluyen las ' + incluidas + ' lecturas del período.'
      );
    } else {
      partes.push(
        'Se incluyen las ' + incluidas + ' lecturas más recientes de las ' +
        delPeriodo + ' del período.'
      );
    }
    if (periodo === 'ultimos30') {
      partes.push('El embarazo tiene ' + cantidadDeLecturas(total) + ' en total.');
    }
    return partes.join(' ');
  }

  function textoDePeriodo(periodo, lecturas) {
    const nombre = PERIODOS[periodo] || PERIODOS.todo;
    if (!lecturas.length) {
      return nombre;
    }
    const masAntigua = lecturas[lecturas.length - 1].fecha_hora_captura;
    const masReciente = lecturas[0].fecha_hora_captura;
    return nombre + ' (del ' + fechaCortaLegible(masAntigua) + ' al ' +
      fechaCortaLegible(masReciente) + ')';
  }

  /** Código de semáforo que se sabe pintar, o null: uno desconocido no se adivina. */
  function codigoConocido(codigo) {
    return CLASES_DE_SEMAFORO[codigo] ? codigo : null;
  }

  // -- El modelo -----------------------------------------------------------

  /** Embarazo: lo que se sabe del episodio, sin inventar semanas. */
  function datosDelEmbarazo(inst) {
    const filas = [];
    const episodio = inst.episodio;
    filas.push({ etiqueta: 'Estado', valor: estadoLegible(episodio.estado_embarazo) });
    filas.push({ etiqueta: 'Inicio del embarazo', valor: fechaCortaLegible(episodio.fecha_inicio) });
    if (!ausente(episodio.fecha_probable_parto)) {
      filas.push({ etiqueta: 'Fecha probable de parto', valor: fechaCortaLegible(episodio.fecha_probable_parto) });
    }
    if (!ausente(episodio.fecha_cierre)) {
      filas.push({ etiqueta: 'Fecha de cierre', valor: fechaCortaLegible(episodio.fecha_cierre) });
    }
    // «Semana actual» solo para el embarazo en curso con semana publicada por
    // el adaptador. En cualquier otro caso, la del último registro, rotulada
    // así y con su fecha: nunca una semana «actual» de un embarazo cerrado.
    const ultima = inst.datos.ultima_lectura;
    if (!ausente(inst.semanaActual)) {
      filas.push({
        etiqueta: 'Semana actual',
        valor: String(inst.semanaActual) + (inst.hoy ? ' (al ' + fechaCortaLegible(inst.hoy) + ')' : '')
      });
    } else if (ultima && !ausente(ultima.semana_gestacion)) {
      filas.push({
        etiqueta: 'Semana en el último registro',
        valor: ultima.semana_gestacion + ' (' + fechaCortaLegible(ultima.fecha_hora_captura) + ')'
      });
    }
    return filas;
  }

  /**
   * Últimos valores: uno por variable, de todo el embarazo, cada uno con su
   * fecha. `undefined` (no llegó) y `null` (nunca se registró) se dicen con
   * frases distintas, como en Inicio.
   */
  function ultimosValores(datos) {
    const ultimosRegistros = datos.ultimos_registros;
    return MEDICIONES_RESUMEN.map(function (m) {
      const registro = ultimosRegistros &&
        Object.prototype.hasOwnProperty.call(ultimosRegistros, m.clave)
        ? ultimosRegistros[m.clave]
        : undefined;
      if (registro === undefined || registro === null) {
        return { clave: m.clave, titulo: m.titulo, cifra: SIN_DATO,
          detalles: [registro === null ? 'Sin registros' : NO_DISPONIBLE] };
      }
      const detalles = ['Registrado el ' + fechaLegible(registro.fecha_hora_captura)];
      if (!ausente(registro.semana_gestacion_lectura)) {
        detalles.push('Semana ' + registro.semana_gestacion_lectura + ' en esa lectura');
      }
      return { clave: m.clave, titulo: m.titulo, cifra: conUnidad(registro.valor, m.unidad), detalles: detalles };
    });
  }

  /** La clasificación de la lectura más reciente, con su alcance escrito. */
  function clasificacionDe(lectura) {
    const filas = [{ etiqueta: 'Lectura del', valor: fechaLegible(lectura.fecha_hora_captura) }];
    const midio = queMidio(lectura);
    if (midio) {
      filas.push({ etiqueta: 'Midió', valor: midio });
    }
    const codigo = codigoConocido(lectura.codigo_semaforo);
    return {
      filas: filas,
      codigo: codigo,
      nombre: codigo ? ETIQUETAS_DE_SEMAFORO[codigo] : null,
      mensaje: codigo ? mensajeDeSemaforo(codigo) : SIN_CLASIFICACION,
      nota: 'La clasificación la asignó el sistema a esta lectura completa, no a ' +
        'cada medición por separado ni a los últimos valores de arriba.'
    };
  }

  /** Las lecturas de la tabla: hasta diez, o todas las del período. */
  function lecturasDelDocumento(lecturas, periodo, alcance) {
    const delPeriodo = lecturasDelPeriodo(lecturas, periodo);
    const incluidas = alcance === ALCANCE.COMPLETO
      ? delPeriodo
      : delPeriodo.slice(0, LECTURAS_EN_RESUMEN);
    return {
      titulo: alcance === ALCANCE.COMPLETO ? 'Todas las lecturas del período' : 'Lecturas recientes',
      filas: [
        { etiqueta: 'Período', valor: textoDePeriodo(periodo, delPeriodo) },
        { etiqueta: 'Lecturas incluidas', valor: String(incluidas.length) }
      ],
      nota: textoCobertura(incluidas.length, delPeriodo.length, lecturas.length, periodo) +
        ' Cada fila es una lectura con su propia clasificación.',
      columnas: COLUMNAS_DE_LECTURAS.slice(),
      filasDeTabla: incluidas.map(function (lectura) {
        const codigo = codigoConocido(lectura.codigo_semaforo);
        return {
          celdas: [
            fechaLegible(lectura.fecha_hora_captura),
            conUnidad(lectura.hr_valor, 'BPM'),
            conUnidad(lectura.spo2_valor, '%'),
            texto(lectura.mov_valor),
            texto(lectura.semana_gestacion)
          ],
          codigo: codigo,
          semaforo: codigo ? ETIQUETAS_DE_SEMAFORO[codigo] : SIN_DATO
        };
      })
    };
  }

  /**
   * El documento entero, en textos. null si la instantánea no permite un
   * resumen útil (sin lecturas): no se produce un documento engañoso.
   */
  function modeloDelResumen(inst, periodo, alcance, generadoEn) {
    const lecturas = lecturasDe(inst.datos);
    if (!lecturas.length || !inst.datos.ultima_lectura) {
      return null;
    }
    const completo = alcance === ALCANCE.COMPLETO;
    return {
      alcance: completo ? ALCANCE.COMPLETO : ALCANCE.RESUMEN,
      marca: 'FetalAlert',
      lema: 'Tecnología al cuidado de tu embarazo',
      titulo: TITULOS_DEL_DOCUMENTO[completo ? 'completo' : 'resumen'],
      embarazo: etiquetaDeEpisodio(inst.episodio),
      fechas: [
        { etiqueta: 'Datos consultados el', valor: momentoLegible(inst.consultadoEn) },
        { etiqueta: (completo ? 'Historial' : 'Resumen') + ' generado el', valor: momentoLegible(generadoEn) }
      ],
      // La fecha de generación no es la de los datos: si no se pudo confirmar
      // la conexión, se dice que son los de la última consulta.
      aviso: operativa(estadoConexion)
        ? null
        : 'Al generar este documento no se pudo confirmar la conexión con el ' +
          'servidor. Los datos son los de la última consulta y podrían no incluir ' +
          'registros posteriores.',
      datosEmbarazo: datosDelEmbarazo(inst),
      notaValores: 'El último valor de cada medición en todo este embarazo. Pueden ' +
        'ser de lecturas y fechas distintas.',
      valores: ultimosValores(inst.datos),
      clasificacion: clasificacionDe(inst.datos.ultima_lectura),
      lecturas: lecturasDelDocumento(lecturas, periodo, alcance),
      pie: [
        'Horas de Panamá. «—» indica un valor no registrado; el cero sí es un valor.',
        'Información simulada con fines académicos: no corresponde a pacientes ' +
          'reales. Este documento no sustituye la valoración de tu personal de salud.'
      ],
      // Sin nombres ni identificadores: solo el tipo y el día.
      nombreArchivo: 'FetalAlert-' + (completo ? 'historial' : 'resumen') + '-' +
        FORMATO_FECHA_ARCHIVO.format(new Date(generadoEn)) + '.pdf'
    };
  }

  // -- Vista previa en la página -------------------------------------------

  function nodo(etiqueta, clase, contenido) {
    const elemento = document.createElement(etiqueta);
    if (clase) {
      elemento.className = clase;
    }
    if (contenido !== undefined && contenido !== null) {
      elemento.textContent = contenido;
    }
    return elemento;
  }

  /** Una lista de definiciones con pares «etiqueta: valor». */
  function listaDeDatos(clase, filas) {
    const lista = nodo('dl', clase);
    filas.forEach(function (f) {
      const fila = nodo('div', 'resumen-fila');
      fila.appendChild(nodo('dt', null, f.etiqueta));
      fila.appendChild(nodo('dd', null, f.valor));
      lista.appendChild(fila);
    });
    return lista;
  }

  function seccionDelResumen(titulo) {
    const seccion = nodo('section', 'resumen-seccion');
    seccion.appendChild(nodo('h3', null, titulo));
    return seccion;
  }

  /** Pinta el modelo y devuelve el título, para llevar el foco allí. */
  function pintarResumen(modelo) {
    const doc = ui.resumenDocumento;
    doc.textContent = '';

    const encabezado = nodo('header', 'resumen-encabezado');
    const marca = nodo('div', 'resumen-marca');
    marca.appendChild(nodo('p', 'resumen-marca-nombre', modelo.marca));
    marca.appendChild(nodo('p', 'resumen-marca-lema', modelo.lema));
    encabezado.appendChild(marca);
    const titulo = nodo('h2', 'resumen-titulo', modelo.titulo);
    titulo.setAttribute('id', 'resumen-titulo');
    titulo.setAttribute('tabindex', '-1');
    encabezado.appendChild(titulo);
    encabezado.appendChild(nodo('p', 'resumen-subtitulo', modelo.embarazo));
    encabezado.appendChild(listaDeDatos('resumen-datos resumen-fechas', modelo.fechas));
    if (modelo.aviso) {
      encabezado.appendChild(nodo('p', 'resumen-aviso', modelo.aviso));
    }
    doc.appendChild(encabezado);

    const embarazo = seccionDelResumen('Embarazo');
    embarazo.appendChild(listaDeDatos('resumen-datos', modelo.datosEmbarazo));
    doc.appendChild(embarazo);

    const valores = seccionDelResumen('Últimos valores registrados');
    valores.appendChild(nodo('p', 'resumen-nota', modelo.notaValores));
    const lista = nodo('ul', 'resumen-valores');
    modelo.valores.forEach(function (v) {
      const item = nodo('li', 'resumen-valor');
      item.appendChild(nodo('span', 'resumen-valor-nombre', v.titulo));
      item.appendChild(nodo('span', 'resumen-valor-cifra', v.cifra));
      v.detalles.forEach(function (d) {
        item.appendChild(nodo('span', 'resumen-valor-detalle', d));
      });
      lista.appendChild(item);
    });
    valores.appendChild(lista);
    doc.appendChild(valores);

    const c = modelo.clasificacion;
    const clasificacion = seccionDelResumen('Clasificación de la lectura más reciente');
    const datosClasificacion = listaDeDatos('resumen-datos', c.filas);
    const fila = nodo('div', 'resumen-fila');
    fila.appendChild(nodo('dt', null, 'Clasificación'));
    const valor = nodo('dd');
    // El color nunca va solo: el nombre y su significado lo acompañan.
    if (c.codigo) {
      valor.appendChild(marcaDeSemaforo(c.codigo));
      valor.appendChild(nodo('span', 'resumen-semaforo-texto', ' — ' + c.mensaje));
    } else {
      valor.textContent = c.mensaje;
    }
    fila.appendChild(valor);
    datosClasificacion.appendChild(fila);
    clasificacion.appendChild(datosClasificacion);
    clasificacion.appendChild(nodo('p', 'resumen-nota', c.nota));
    doc.appendChild(clasificacion);

    const l = modelo.lecturas;
    const seccionLecturas = seccionDelResumen(l.titulo);
    // Esta sección sí puede partirse entre páginas; sus filas, no.
    seccionLecturas.className = 'resumen-seccion resumen-seccion-tabla';
    seccionLecturas.appendChild(listaDeDatos('resumen-datos', l.filas));
    seccionLecturas.appendChild(nodo('p', 'resumen-nota', l.nota));
    const tabla = nodo('table', 'resumen-tabla');
    const cabecera = nodo('thead');
    const filaCabecera = nodo('tr');
    l.columnas.forEach(function (nombre) {
      const th = nodo('th', null, nombre);
      th.setAttribute('scope', 'col');
      filaCabecera.appendChild(th);
    });
    cabecera.appendChild(filaCabecera);
    tabla.appendChild(cabecera);
    const cuerpo = nodo('tbody');
    l.filasDeTabla.forEach(function (f) {
      const tr = nodo('tr');
      f.celdas.forEach(function (contenido, i) {
        celda(tr, l.columnas[i], contenido);
      });
      celda(tr, l.columnas[5], marcaDeSemaforo(f.codigo));
      cuerpo.appendChild(tr);
    });
    tabla.appendChild(cuerpo);
    seccionLecturas.appendChild(tabla);
    doc.appendChild(seccionLecturas);

    const pie = nodo('footer', 'resumen-pie');
    modelo.pie.forEach(function (p) {
      pie.appendChild(nodo('p', null, p));
    });
    doc.appendChild(pie);
    return titulo;
  }

  function alcanceElegido() {
    return ui.alcanceCompleto && ui.alcanceCompleto.checked === true
      ? ALCANCE.COMPLETO
      : ALCANCE.RESUMEN;
  }

  /**
   * Arma el modelo con la instantánea y lo pinta. Devuelve el título, o null
   * si no hay un resumen útil.
   */
  function abrirResumen(inst, periodo, alcance) {
    const modelo = modeloDelResumen(inst, periodo, alcance, Date.now());
    if (!modelo) {
      return null;
    }
    // Un objeto nuevo en cada apertura y en cada cambio de contenido; nada lo
    // modifica después, así que una descarga puede quedarse con él tal cual.
    resumenAbierto = { inst: inst, periodo: periodo, modelo: modelo };
    invalidarDescarga();
    return pintarResumen(modelo);
  }

  /** El botón «Guardar mi resumen». */
  function generarResumen() {
    const inst = instantaneaHistorial;
    if (!instantaneaUsable()) {
      mostrarMensajeResumen(MENSAJES_RESUMEN.cargando);
      return;
    }
    // Cada apertura empieza en «Resumen».
    if (ui.alcanceResumen) {
      ui.alcanceResumen.checked = true;
    }
    if (ui.alcanceCompleto) {
      ui.alcanceCompleto.checked = false;
    }
    const titulo = abrirResumen(inst, periodoElegido(ui.periodoHistorial), ALCANCE.RESUMEN);
    if (!titulo) {
      mostrarMensajeResumen(MENSAJES_RESUMEN.sinLecturas);
      return;
    }
    mostrarMensajeResumen(null);
    mostrarVista('resumen');
    // El foco va al título del documento: un lector de pantalla empieza por
    // ahí, y el teclado no se queda en un botón que ya no está visible.
    titulo.focus();
  }

  /** Cambiar entre «Resumen» e «Historial completo»: la misma instantánea. */
  function cambiarAlcance() {
    if (!resumenAbierto) {
      return;
    }
    abrirResumen(resumenAbierto.inst, resumenAbierto.periodo, alcanceElegido());
  }

  // -- Descarga del PDF ----------------------------------------------------

  function cargarScript(ruta) {
    return new Promise(function (resolver, rechazar) {
      const script = document.createElement('script');
      script.src = ruta;
      script.onload = resolver;
      script.onerror = rechazar;
      (document.head || document.body).appendChild(script);
    });
  }

  /** pdfmake y sus fuentes, una sola vez por página. */
  function cargarMotorPdf() {
    if (motorPdf === null) {
      motorPdf = MOTOR_PDF.reduce(function (anterior, ruta) {
        return anterior.then(function () { return cargarScript(ruta); });
      }, Promise.resolve())
        .then(function () {
          if (!window.pdfMake || typeof window.pdfMake.createPdf !== 'function') {
            throw new Error('motor de PDF incompleto');
          }
          return window.pdfMake;
        })
        .catch(function (error) {
          motorPdf = null;
          throw error;
        });
    }
    return motorPdf;
  }

  /**
   * El PDF completo en memoria, como Blob. `getStream()` sin callback arma el
   * documento en el acto y lanza aquí mismo si algo falla; `getBlob()` y
   * `download()` lo hacen dentro de una promesa propia de pdfmake y un error
   * se pierde sin avisar. Los trozos llegan después, de forma asíncrona.
   */
  function generarPdf(pdfMake, definicion) {
    return new Promise(function (resolver, rechazar) {
      const documento = pdfMake.createPdf(definicion).getStream();
      const trozos = [];
      documento.on('data', function (trozo) { trozos.push(trozo); });
      documento.on('end', function () {
        resolver(new Blob(trozos, { type: 'application/pdf' }));
      });
      documento.on('error', rechazar);
      documento.end();
    });
  }

  // Lo que tarda en liberarse la dirección temporal del PDF: lo mismo que
  // espera FileSaver, que es lo que usa `download()` de pdfmake.
  const LIBERAR_PDF_MS = 40000;

  /** Entrega el PDF ya generado al navegador, como haría `download()`. */
  function entregarPdf(blob, nombre) {
    const direccion = URL.createObjectURL(blob);
    const enlace = document.createElement('a');
    enlace.href = direccion;
    enlace.download = nombre;
    enlace.rel = 'noopener';
    enlace.click();
    // Solo vive en memoria de esta página. No se libera enseguida: el
    // navegador tiene que alcanzar a leerla.
    window.setTimeout(function () { URL.revokeObjectURL(direccion); }, LIBERAR_PDF_MS);
  }

  /**
   * «Descargar PDF»: el resumen que estaba en pantalla al pulsar, en un
   * archivo. Cambiar el contenido, salir del resumen, abrir otro o confirmar
   * el cierre de sesión mientras se prepara deja esta descarga sin efecto:
   * el PDF puede terminar de generarse, pero no se entrega, y nada de esta
   * descarga cambia la pantalla. Un archivo ya entregado no se puede retirar.
   */
  function descargarPdf() {
    const abierto = resumenAbierto;
    if (!abierto) {
      return Promise.resolve();
    }
    const turno = turnos.resumen;
    const epoca = epocaSesion;
    function vigente() {
      return esVigente('resumen', turno) && epoca === epocaSesion;
    }
    // El momento en que la paciente pulsa «Descargar PDF»: cada descarga
    // queda marcada con el suyo en todas las páginas.
    const descargadoEn = 'Descargado el ' + FORMATO_DESCARGA.format(new Date(Date.now())) +
      ' (hora de Panamá)';
    const boton = ui.botonDescargarPdf;
    boton.disabled = true;
    mostrarEstadoDescarga(MENSAJES_RESUMEN.preparando);
    return cargarMotorPdf()
      .then(function (pdfMake) {
        if (!vigente()) {
          return null;
        }
        const definicion = window.FetalAlertResumenPdf.definicion(abierto.modelo,
          { descargadoEn: descargadoEn });
        return generarPdf(pdfMake, definicion);
      })
      .then(function (blob) {
        // La última comprobación, justo antes de entregarlo y sin nada
        // asíncrono entre ambas cosas.
        if (!blob || !vigente()) {
          return;
        }
        entregarPdf(blob, abierto.modelo.nombreArchivo);
        // El navegador decide dónde lo guarda; aquí solo se sabe que empezó.
        mostrarEstadoDescarga('Descarga iniciada: «' + abierto.modelo.nombreArchivo +
          '». Búscalo en la carpeta de descargas.');
      })
      .catch(function () {
        if (vigente()) {
          mostrarEstadoDescarga(MENSAJES_RESUMEN.fallo);
        }
      })
      .then(function () {
        // Una descarga que ya no es la vigente no toca el botón: lo dejó
        // listo quien la invalidó, y puede haber otra en curso.
        if (vigente()) {
          boton.disabled = false;
        }
      });
  }

  function volverAlHistorial() {
    mostrarVista('historial');
    ui.botonGuardarResumen.focus();
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

  function redibujarInicio() {
    if (seriesInicio) {
      dibujarGraficas(ui.graficasInicio, seriesInicio, periodoElegido(ui.periodoInicio));
    }
  }

  function redibujarHistorial() {
    const graficas = ui.historialLista.querySelector('.graficas');
    if (graficas && seriesHistorial) {
      dibujarGraficas(graficas, seriesHistorial, periodoElegido(ui.periodoHistorial));
    }
  }

  function conectarEventos() {
    ui.formLogin.addEventListener('submit', manejarLogin);
    ui.botonCerrarSesion.addEventListener('click', abrirDialogoDeCierre);
    ui.botonCancelarCierre.addEventListener('click', cancelarCierre);
    ui.botonConfirmarCierre.addEventListener('click', manejarLogout);
    ui.botonVerAnteriores.addEventListener('click', verEmbarazosAnteriores);
    ui.botonGuardarResumen.addEventListener('click', generarResumen);
    ui.botonDescargarPdf.addEventListener('click', descargarPdf);
    ui.alcanceResumen.addEventListener('change', cambiarAlcance);
    ui.alcanceCompleto.addEventListener('change', cambiarAlcance);
    ui.botonVolverHistorial.addEventListener('click', volverAlHistorial);

    // El período solo cambia el dibujo de lo ya recibido: no pide nada.
    ui.periodoInicio.addEventListener('change', redibujarInicio);
    ui.periodoHistorial.addEventListener('change', redibujarHistorial);
    // Al cruzar el corte de una a dos columnas cambia el lienzo de la gráfica
    // ancha: se vuelve a dibujar lo ya recibido, sin pedir nada.
    if (typeof window.matchMedia === 'function') {
      const consulta = window.matchMedia(MEDIA_GRAFICA_ANCHA);
      if (typeof consulta.addEventListener === 'function') {
        consulta.addEventListener('change', function () {
          redibujarInicio();
          redibujarHistorial();
        });
      }
    }
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

    consultarSesion().then(function (sesion) {
      if (sesion) {
        mostrarPortal();
      } else {
        mostrarLogin();
      }
    });
  }

  document.addEventListener('DOMContentLoaded', arrancar);
})();
