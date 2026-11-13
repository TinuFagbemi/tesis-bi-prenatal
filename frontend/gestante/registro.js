/* =========================================================================
   FetalAlert — «Crear cuenta» (SCRUM-110, R2)

   La paciente crea su propia cuenta desde la pantalla de inicio de sesión. Un
   médico puede ayudarla en persona a llenar el formulario, pero no inicia
   sesión ni aprueba nada: este archivo no pide ninguna sesión previa.

   Vive aparte de app.js a propósito. El formulario es un panel dentro de
   `vista-login`, así que app.js lo muestra y lo oculta junto con esa vista sin
   saber que existe, y ninguno de los dos archivos tiene que cambiar para que
   el otro funcione.

   Reglas que gobiernan este archivo:

   1. Solo se habla con el adaptador local (`/adaptador/registro...`). El
      navegador no llama a la API central.

   2. Nada se guarda en el navegador. Los datos del formulario viven solo en
      sus campos, mientras el formulario está abierto; al salir de él o al
      terminar con éxito se vacían. Las contraseñas se vacían en cuanto el
      formulario se envía, salga como salga.

   3. Un envío a la vez y **nunca un reenvío automático**. Si se pierde la
      respuesta, no se afirma que la cuenta no se creó: se dice que no se sabe
      y se propone comprobarlo iniciando sesión.

   4. Los mensajes son de este archivo. No se muestra nada de lo que responda
      el servidor más allá de los nombres de campo que el adaptador ya filtró.

   5. Las comprobaciones de aquí solo ayudan a corregir antes de enviar. Quien
      decide es el servidor, que vuelve a validarlo todo.

   Todos los datos de esta versión son ficticios y simulados.
   ========================================================================= */

(function () {
  'use strict';

  const API = {
    documentos: '/adaptador/registro/documentos',
    registro: '/adaptador/registro'
  };

  const ZONA_HORARIA = 'America/Panama';

  // Los dos documentos que se aceptan por separado, con su casilla y su botón.
  const DOCUMENTOS = {
    POLITICA_PRIVACIDAD: {
      casilla: 'reg-acepta-politica',
      boton: 'btn-leer-politica',
      campoAcepta: 'acepta_politica_privacidad',
      campoVersion: 'version_politica_privacidad'
    },
    TERMINOS_CONDICIONES: {
      casilla: 'reg-acepta-terminos',
      boton: 'btn-leer-terminos',
      campoAcepta: 'acepta_terminos_condiciones',
      campoVersion: 'version_terminos_condiciones'
    }
  };

  // Los campos de texto, con el nombre que espera el contrato de R1. Las
  // longitudes son las de las columnas (y las del atributo `maxlength`).
  const CAMPOS = [
    { nombre: 'primer_nombre', obligatorio: true, maximo: 60 },
    { nombre: 'segundo_nombre', obligatorio: false, maximo: 60 },
    { nombre: 'apellido_paterno', obligatorio: true, maximo: 60 },
    { nombre: 'apellido_materno', obligatorio: false, maximo: 60 },
    { nombre: 'cedula', obligatorio: true, maximo: 20 },
    { nombre: 'fecha_nac', obligatorio: true, tipo: 'fecha' },
    { nombre: 'email', obligatorio: true, maximo: 120, tipo: 'correo' },
    { nombre: 'celular', obligatorio: true, maximo: 120 },
    // Las contraseñas no se recortan: se envían tal cual se escriben.
    { nombre: 'password', obligatorio: true, maximo: 512, tipo: 'clave' },
    { nombre: 'confirmacion_password', obligatorio: true, maximo: 512, tipo: 'clave' }
  ];

  const TEXTO = {
    obligatorio: 'Este dato es obligatorio.',
    largo: 'Es demasiado largo.',
    fechaFormato: 'Escribe la fecha como AAAA-MM-DD, o elígela en el calendario.',
    fechaInexistente: 'Esa fecha no existe.',
    fechaFutura: 'La fecha de nacimiento no puede ser posterior a hoy.',
    correo:
      'Escribe el correo de acceso que te asignaron, con el formato ' +
      'pacienteNN@example.com. Por ejemplo: paciente31@example.com.',
    distintas: 'Las contraseñas no coinciden.',
    aceptar: 'Debes aceptar este documento para crear la cuenta.',
    revisarDato: 'Revisa este dato.',

    resumenErrores: 'Revisa los datos marcados antes de continuar.',
    duplicado:
      'Ya existe una cuenta con esa cédula o ese correo. Si ya tienes cuenta, ' +
      'inicia sesión. Si no, revisa la cédula y el correo.',
    sinConexion:
      'No hay conexión con el servidor. Tu registro no se envió; inténtalo de ' +
      'nuevo cuando vuelva la conexión.',
    errorServidor:
      'El servidor no pudo completar el registro. Inténtalo de nuevo más tarde.',
    versionCambio:
      'La política de privacidad o los términos cambiaron. Vuelve a leerlos y ' +
      'acéptalos de nuevo.',

    cargandoDocumentos: 'Cargando la política de privacidad y los términos…',
    documentosNoDisponibles:
      'No se pudieron cargar la política de privacidad y los términos. Sin ' +
      'ellos no se puede crear la cuenta.',

    enviando: 'Creando la cuenta…',
    enviar: 'Crear cuenta',

    exitoTitulo: 'Tu cuenta está lista',
    exitoTexto:
      'Ya puedes iniciar sesión con tu correo de acceso y la contraseña que elegiste. ' +
      'Todavía no tienes embarazos ni mediciones registrados.',
    desconocidoTitulo: 'No sabemos si tu cuenta se creó',
    desconocidoTexto:
      'Se perdió la respuesta del servidor. Antes de enviar el formulario otra ' +
      'vez, intenta iniciar sesión con el correo y la contraseña que elegiste. ' +
      'Si no puedes entrar, vuelve al formulario y envíalo de nuevo.'
  };

  // =======================================================================
  // Referencias al DOM
  // =======================================================================

  const $ = function (id) {
    return document.getElementById(id);
  };

  const ui = {
    vistaLogin: $('vista-login'),
    panelLogin: $('panel-inicio-sesion'),
    panelRegistro: $('panel-registro'),
    panelResultado: $('panel-registro-resultado'),
    botonCrearCuenta: $('btn-crear-cuenta'),
    loginEmail: $('login-email'),
    loginPassword: $('login-password'),
    loginMensaje: $('login-mensaje'),

    titulo: $('registro-titulo'),
    form: $('form-registro'),
    resumen: $('registro-resumen'),
    botonEnviar: $('btn-registro-enviar'),
    botonVolver: $('btn-registro-volver'),
    estadoDocumentos: $('registro-documentos-estado'),
    botonReintentarDocumentos: $('btn-reintentar-documentos'),

    resultadoTitulo: $('registro-resultado-titulo'),
    resultadoTexto: $('registro-resultado-texto'),
    botonResultadoLogin: $('btn-resultado-iniciar-sesion'),
    botonResultadoFormulario: $('btn-resultado-volver-formulario'),

    dialogo: $('dialogo-documento'),
    dialogoTitulo: $('documento-titulo'),
    dialogoVersion: $('documento-version'),
    dialogoTexto: $('documento-texto'),
    botonCerrarDocumento: $('btn-cerrar-documento')
  };

  function campo(nombre) {
    return $('reg-' + nombre);
  }

  function errorDe(nombre) {
    return $('err-' + nombre);
  }

  // =======================================================================
  // Estado (solo en memoria)
  // =======================================================================

  // Los documentos vigentes, por código: { version, titulo, texto }.
  let documentos = null;
  let documentosEnCurso = null;

  // Un envío a la vez. Mientras dura, ningún botón vuelve a enviar.
  let enviando = false;

  // Fin del bloqueo por límite de solicitudes, en milisegundos, o 0.
  let bloqueoHasta = 0;
  let temporizadorBloqueo = null;

  // El correo con que se envió el último formulario, solo para ofrecerlo en
  // el inicio de sesión. Se olvida al usarlo o al salir.
  let correoParaIniciarSesion = '';

  // Quién abrió el diálogo, para devolverle el foco al cerrarlo.
  let abridorDelDialogo = null;

  // =======================================================================
  // Utilidades
  // =======================================================================

  /** Hoy en Panamá, como AAAA-MM-DD: el mismo día clínico que usa el servidor. */
  function hoyEnPanama() {
    const partes = new Intl.DateTimeFormat('en-CA', {
      timeZone: ZONA_HORARIA,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).formatToParts(new Date(Date.now()));
    const valor = {};
    partes.forEach(function (p) {
      valor[p.type] = p.value;
    });
    return valor.year + '-' + valor.month + '-' + valor.day;
  }

  /** Si «AAAA-MM-DD» es un día que existe en el calendario. */
  function fechaExiste(texto) {
    const anio = Number(texto.slice(0, 4));
    const mes = Number(texto.slice(5, 7));
    const dia = Number(texto.slice(8, 10));
    const fecha = new Date(Date.UTC(anio, mes - 1, dia));
    return (
      fecha.getUTCFullYear() === anio &&
      fecha.getUTCMonth() === mes - 1 &&
      fecha.getUTCDate() === dia
    );
  }

  function mostrar(elemento, visible) {
    if (elemento) {
      elemento.hidden = !visible;
    }
  }

  function enfocar(elemento) {
    if (elemento && typeof elemento.focus === 'function') {
      elemento.focus();
    }
  }

  // =======================================================================
  // Paneles
  // =======================================================================

  function mostrarPanel(nombre) {
    mostrar(ui.panelLogin, nombre === 'login');
    mostrar(ui.panelRegistro, nombre === 'registro');
    mostrar(ui.panelResultado, nombre === 'resultado');
  }

  function abrirRegistro() {
    // Lo que hubiera en el inicio de sesión no pasa al registro.
    ui.loginPassword.value = '';
    ui.loginMensaje.hidden = true;
    ui.loginMensaje.textContent = '';
    mostrarPanel('registro');
    enfocar(ui.titulo);
    cargarDocumentos();
  }

  /** Volver al inicio de sesión: el formulario se vacía entero. */
  function volverAlLogin() {
    if (enviando) {
      return;
    }
    vaciarFormulario();
    mostrarPanel('login');
    if (correoParaIniciarSesion) {
      ui.loginEmail.value = correoParaIniciarSesion;
      correoParaIniciarSesion = '';
      enfocar(ui.loginPassword);
    } else {
      enfocar(ui.botonCrearCuenta);
    }
  }

  function volverAlFormulario() {
    correoParaIniciarSesion = '';
    mostrarPanel('registro');
    enfocar(campo('password'));
  }

  function mostrarResultado(titulo, textoResultado, ofrecerFormulario) {
    ui.resultadoTitulo.textContent = titulo;
    ui.resultadoTexto.textContent = textoResultado;
    mostrar(ui.botonResultadoFormulario, ofrecerFormulario);
    mostrarPanel('resultado');
    enfocar(ui.resultadoTitulo);
  }

  /**
   * Todo a su estado inicial: campos vacíos, casillas desmarcadas, sin
   * errores. Se usa al salir del formulario y cuando app.js oculta la vista.
   */
  function vaciarFormulario() {
    CAMPOS.forEach(function (c) {
      campo(c.nombre).value = '';
    });
    Object.keys(DOCUMENTOS).forEach(function (codigo) {
      $(DOCUMENTOS[codigo].casilla).checked = false;
    });
    limpiarErrores();
  }

  // =======================================================================
  // Errores
  // =======================================================================

  function marcarError(idCampo, idError, mensaje) {
    const control = $(idCampo);
    const aviso = $(idError);
    control.setAttribute('aria-invalid', 'true');
    aviso.textContent = mensaje;
    aviso.hidden = false;
  }

  function limpiarErrores() {
    CAMPOS.forEach(function (c) {
      campo(c.nombre).removeAttribute('aria-invalid');
      errorDe(c.nombre).textContent = '';
      errorDe(c.nombre).hidden = true;
    });
    Object.keys(DOCUMENTOS).forEach(function (codigo) {
      const d = DOCUMENTOS[codigo];
      $(d.casilla).removeAttribute('aria-invalid');
      errorDe(d.campoAcepta).textContent = '';
      errorDe(d.campoAcepta).hidden = true;
    });
    mostrarResumen(null);
  }

  function mostrarResumen(mensaje) {
    ui.resumen.textContent = mensaje || '';
    ui.resumen.hidden = !mensaje;
  }

  /** El error de un campo del contrato, sea de texto o una aceptación. */
  function marcarCampo(nombre, mensaje) {
    const documento = Object.keys(DOCUMENTOS).filter(function (codigo) {
      const d = DOCUMENTOS[codigo];
      return d.campoAcepta === nombre || d.campoVersion === nombre;
    })[0];
    if (documento) {
      const d = DOCUMENTOS[documento];
      marcarError(d.casilla, 'err-' + d.campoAcepta, mensaje);
      return d.casilla;
    }
    if (campo(nombre)) {
      marcarError('reg-' + nombre, 'err-' + nombre, mensaje);
      return 'reg-' + nombre;
    }
    return null;
  }

  // =======================================================================
  // Validación de ayuda (el servidor decide)
  // =======================================================================

  /** Los errores del formulario, como [nombre, mensaje], en orden de pantalla. */
  function errores() {
    const hallados = [];
    const hoy = hoyEnPanama();

    CAMPOS.forEach(function (c) {
      const crudo = campo(c.nombre).value;
      const valor = c.tipo === 'clave' ? crudo : crudo.trim();

      if (!valor) {
        if (c.obligatorio) {
          hallados.push([c.nombre, TEXTO.obligatorio]);
        }
        return;
      }
      if (c.maximo && valor.length > c.maximo) {
        hallados.push([c.nombre, TEXTO.largo]);
        return;
      }
      if (c.tipo === 'fecha') {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(valor)) {
          hallados.push([c.nombre, TEXTO.fechaFormato]);
        } else if (!fechaExiste(valor)) {
          hallados.push([c.nombre, TEXTO.fechaInexistente]);
        } else if (valor > hoy) {
          hallados.push([c.nombre, TEXTO.fechaFutura]);
        }
      }
      // La misma regla que el servidor (PATRON_EMAIL_REGISTRO): «paciente», al
      // menos dos dígitos y exactamente example.com, sin distinguir mayúsculas
      // porque el servidor guarda el correo en minúsculas.
      if (c.tipo === 'correo' && !/^paciente[0-9]{2,}@example\.com$/.test(valor.toLowerCase())) {
        hallados.push([c.nombre, TEXTO.correo]);
      }
    });

    const clave = campo('password').value;
    const confirmacion = campo('confirmacion_password').value;
    if (clave && confirmacion && clave !== confirmacion) {
      hallados.push(['confirmacion_password', TEXTO.distintas]);
    }

    Object.keys(DOCUMENTOS).forEach(function (codigo) {
      const d = DOCUMENTOS[codigo];
      if (!$(d.casilla).checked) {
        hallados.push([d.campoAcepta, TEXTO.aceptar]);
      }
    });
    return hallados;
  }

  function mostrarErrores(lista, resumen) {
    let primero = null;
    lista.forEach(function (par) {
      const marcado = marcarCampo(par[0], par[1]);
      primero = primero || marcado;
    });
    mostrarResumen(resumen);
    enfocar(primero ? $(primero) : ui.resumen);
  }

  // =======================================================================
  // Peticiones al adaptador
  // =======================================================================

  /** Igual que en app.js: un fallo de red es estado 0, no una excepción. */
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
            return { estado: respuesta.status, cuerpo: cuerpo };
          });
      })
      .catch(function () {
        return { estado: 0, cuerpo: null };
      });
  }

  // -- Documentos -----------------------------------------------------------

  function documentosValidos(cuerpo) {
    const lista = cuerpo && Array.isArray(cuerpo.documentos) ? cuerpo.documentos : null;
    if (!lista) {
      return null;
    }
    const porCodigo = {};
    lista.forEach(function (d) {
      if (
        d && DOCUMENTOS[d.documento] &&
        typeof d.version === 'string' && d.version &&
        typeof d.titulo === 'string' && typeof d.texto === 'string' && d.texto
      ) {
        porCodigo[d.documento] = { version: d.version, titulo: d.titulo, texto: d.texto };
      }
    });
    const completos = Object.keys(DOCUMENTOS).every(function (codigo) {
      return porCodigo[codigo];
    });
    return completos ? porCodigo : null;
  }

  function habilitarAceptaciones(habilitar) {
    Object.keys(DOCUMENTOS).forEach(function (codigo) {
      const d = DOCUMENTOS[codigo];
      $(d.casilla).disabled = !habilitar;
      $(d.boton).disabled = !habilitar;
    });
  }

  function cargarDocumentos(forzar) {
    if ((documentos && !forzar) || documentosEnCurso) {
      return documentosEnCurso || Promise.resolve();
    }
    habilitarAceptaciones(false);
    ui.estadoDocumentos.textContent = TEXTO.cargandoDocumentos;
    ui.estadoDocumentos.hidden = false;
    mostrar(ui.botonReintentarDocumentos, false);

    documentosEnCurso = pedir(API.documentos).then(function (resultado) {
      documentosEnCurso = null;
      const validos = resultado.estado === 200 ? documentosValidos(resultado.cuerpo) : null;
      if (!validos) {
        documentos = null;
        ui.estadoDocumentos.textContent = TEXTO.documentosNoDisponibles;
        mostrar(ui.botonReintentarDocumentos, true);
        return;
      }
      documentos = validos;
      ui.estadoDocumentos.textContent = '';
      ui.estadoDocumentos.hidden = true;
      habilitarAceptaciones(true);
    });
    return documentosEnCurso;
  }

  // -- Diálogo de un documento ---------------------------------------------

  /**
   * El texto del documento, como párrafos, títulos y listas.
   *
   * Los textos de R1 usan una forma mínima: «## » para un apartado, «- » para
   * un elemento de lista y «**» para resaltar. Se construyen nodos con
   * `textContent`, nunca con marcado: el texto no puede inyectar nada.
   */
  function pintarTexto(contenedor, texto) {
    contenedor.textContent = '';
    let lista = null;

    function enLinea(destino, linea) {
      linea.split('**').forEach(function (trozo, i) {
        if (!trozo) {
          return;
        }
        const nodo = document.createElement(i % 2 === 1 ? 'strong' : 'span');
        nodo.textContent = trozo;
        destino.appendChild(nodo);
      });
    }

    texto.split('\n').forEach(function (linea) {
      const limpia = linea.trim();
      if (!limpia || limpia.indexOf('# ') === 0) {
        // Línea vacía o el título principal, que ya está en el encabezado.
        lista = null;
        return;
      }
      if (limpia.indexOf('## ') === 0) {
        lista = null;
        const titulo = document.createElement('h3');
        titulo.textContent = limpia.slice(3);
        contenedor.appendChild(titulo);
        return;
      }
      if (limpia.indexOf('- ') === 0) {
        if (!lista) {
          lista = document.createElement('ul');
          contenedor.appendChild(lista);
        }
        const elemento = document.createElement('li');
        enLinea(elemento, limpia.slice(2));
        lista.appendChild(elemento);
        return;
      }
      lista = null;
      const parrafo = document.createElement('p');
      enLinea(parrafo, limpia);
      contenedor.appendChild(parrafo);
    });
  }

  function abrirDocumento(codigo, boton) {
    const documento = documentos && documentos[codigo];
    if (!documento) {
      return;
    }
    abridorDelDialogo = boton;
    ui.dialogoTitulo.textContent = documento.titulo;
    ui.dialogoVersion.textContent = 'Versión ' + documento.version;
    pintarTexto(ui.dialogoTexto, documento.texto);
    if (typeof ui.dialogo.showModal === 'function') {
      ui.dialogo.showModal();
    } else {
      ui.dialogo.setAttribute('open', '');
    }
    // El foco va al texto, que se puede recorrer con el teclado.
    enfocar(ui.dialogoTexto);
  }

  function cerrarDocumento() {
    if (ui.dialogo.hasAttribute('open')) {
      if (typeof ui.dialogo.close === 'function') {
        ui.dialogo.close();
      } else {
        ui.dialogo.removeAttribute('open');
      }
    }
  }

  /** Al cerrar --con el botón o con Escape--, el foco vuelve a quien lo abrió. */
  function alCerrarDocumento() {
    const abridor = abridorDelDialogo;
    abridorDelDialogo = null;
    enfocar(abridor);
  }

  // -- Envío ------------------------------------------------------------------

  function cuerpoDelFormulario() {
    const cuerpo = {};
    CAMPOS.forEach(function (c) {
      const crudo = campo(c.nombre).value;
      const valor = c.tipo === 'clave' ? crudo : crudo.trim();
      // Un opcional vacío viaja como null: así lo guarda el servidor.
      cuerpo[c.nombre] = valor === '' && !c.obligatorio ? null : valor;
    });
    Object.keys(DOCUMENTOS).forEach(function (codigo) {
      const d = DOCUMENTOS[codigo];
      cuerpo[d.campoAcepta] = $(d.casilla).checked === true;
      cuerpo[d.campoVersion] = documentos[codigo].version;
    });
    return cuerpo;
  }

  function vaciarContrasenas() {
    campo('password').value = '';
    campo('confirmacion_password').value = '';
  }

  function ponerEnviando(activo) {
    enviando = activo;
    ui.botonEnviar.disabled = activo || Date.now() < bloqueoHasta;
    ui.botonVolver.disabled = activo;
    ui.botonEnviar.textContent = activo ? TEXTO.enviando : TEXTO.enviar;
    if (activo) {
      ui.form.setAttribute('aria-busy', 'true');
    } else {
      ui.form.removeAttribute('aria-busy');
    }
  }

  /** 429: se bloquea el botón el tiempo indicado, sin reintentar nada. */
  function bloquearPorLimite(segundos) {
    const espera = typeof segundos === 'number' && segundos > 0 ? segundos : null;
    const minutos = espera ? Math.ceil(espera / 60) : null;
    mostrarResumen(
      'El servidor recibió demasiadas solicitudes de registro en poco tiempo. ' +
        (minutos
          ? 'Espera ' + (minutos === 1 ? 'un minuto' : minutos + ' minutos') + ' e inténtalo de nuevo.'
          : 'Espera unos minutos e inténtalo de nuevo.') +
        ' Lo que escribiste sigue aquí, salvo la contraseña.'
    );
    enfocar(ui.resumen);
    if (!espera) {
      return;
    }
    bloqueoHasta = Date.now() + espera * 1000;
    ui.botonEnviar.disabled = true;
    if (temporizadorBloqueo !== null) {
      window.clearTimeout(temporizadorBloqueo);
    }
    temporizadorBloqueo = window.setTimeout(function () {
      temporizadorBloqueo = null;
      bloqueoHasta = 0;
      ui.botonEnviar.disabled = enviando;
    }, espera * 1000);
  }

  function manejarResultado(resultado, correo) {
    const estado = resultado.estado;
    const cuerpo = resultado.cuerpo || {};

    if (estado === 201) {
      vaciarFormulario();
      correoParaIniciarSesion = correo;
      mostrarResultado(TEXTO.exitoTitulo, TEXTO.exitoTexto, false);
      return;
    }
    if (estado === 0 || estado === 504) {
      // No se sabe si se creó. No se reintenta y no se dice que no se guardó.
      correoParaIniciarSesion = correo;
      mostrarResultado(TEXTO.desconocidoTitulo, TEXTO.desconocidoTexto, true);
      return;
    }
    if (estado === 409) {
      mostrarResumen(TEXTO.duplicado);
      enfocar(ui.resumen);
      return;
    }
    if (estado === 422) {
      const campos = Array.isArray(cuerpo.campos) ? cuerpo.campos : [];
      const cambioDeVersion = campos.some(function (nombre) {
        return /^version_/.test(nombre);
      });
      if (cambioDeVersion) {
        // Otra versión vigente: hay que leerla y aceptarla de nuevo.
        Object.keys(DOCUMENTOS).forEach(function (codigo) {
          $(DOCUMENTOS[codigo].casilla).checked = false;
        });
        cargarDocumentos(true);
        mostrarErrores([], TEXTO.versionCambio);
        return;
      }
      mostrarErrores(
        campos.map(function (nombre) {
          // El correo tiene un formato fijo: se repite la regla con su ejemplo.
          return [nombre, nombre === 'email' ? TEXTO.correo : TEXTO.revisarDato];
        }),
        TEXTO.resumenErrores
      );
      return;
    }
    if (estado === 429) {
      bloquearPorLimite(cuerpo.reintentar_en_segundos);
      return;
    }
    if (estado === 503) {
      mostrarResumen(TEXTO.sinConexion);
      enfocar(ui.resumen);
      return;
    }
    mostrarResumen(TEXTO.errorServidor);
    enfocar(ui.resumen);
  }

  function manejarEnvio(evento) {
    evento.preventDefault();
    if (enviando || Date.now() < bloqueoHasta) {
      return;
    }
    limpiarErrores();

    if (!documentos) {
      mostrarResumen(TEXTO.documentosNoDisponibles);
      enfocar(ui.resumen);
      return;
    }
    const hallados = errores();
    if (hallados.length) {
      mostrarErrores(hallados, TEXTO.resumenErrores);
      return;
    }

    const cuerpo = cuerpoDelFormulario();
    const correo = cuerpo.email;
    ponerEnviando(true);

    pedir(API.registro, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(cuerpo)
    })
      .then(function (resultado) {
        // La contraseña viajó una vez. Se vacía pase lo que pase.
        vaciarContrasenas();
        ponerEnviando(false);
        manejarResultado(resultado, correo);
      });
  }

  // =======================================================================
  // Arranque
  // =======================================================================

  function conectar() {
    if (!ui.form || !ui.panelRegistro) {
      return;
    }
    ui.botonCrearCuenta.addEventListener('click', abrirRegistro);
    ui.botonVolver.addEventListener('click', volverAlLogin);
    ui.form.addEventListener('submit', manejarEnvio);
    ui.botonReintentarDocumentos.addEventListener('click', function () {
      cargarDocumentos(true);
    });
    ui.botonResultadoLogin.addEventListener('click', volverAlLogin);
    ui.botonResultadoFormulario.addEventListener('click', volverAlFormulario);

    Object.keys(DOCUMENTOS).forEach(function (codigo) {
      const boton = $(DOCUMENTOS[codigo].boton);
      boton.addEventListener('click', function () {
        abrirDocumento(codigo, boton);
      });
    });
    ui.botonCerrarDocumento.addEventListener('click', cerrarDocumento);
    ui.dialogo.addEventListener('close', alCerrarDocumento);

    campo('fecha_nac').setAttribute('max', hoyEnPanama());
    habilitarAceptaciones(false);
    vaciarFormulario();
    mostrarPanel('login');

    // Cuando app.js oculta la vista de inicio de sesión --se abrió el portal--,
    // no puede quedar nada escrito en el formulario para la próxima vez.
    if (typeof MutationObserver === 'function') {
      new MutationObserver(function () {
        if (ui.vistaLogin.hidden && !enviando) {
          cerrarDocumento();
          vaciarFormulario();
          correoParaIniciarSesion = '';
          mostrarPanel('login');
        }
      }).observe(ui.vistaLogin, { attributes: true, attributeFilter: ['hidden'] });
    }
  }

  document.addEventListener('DOMContentLoaded', conectar);
})();
