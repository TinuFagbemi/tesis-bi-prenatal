/* =========================================================================
   Pruebas de comportamiento de registro.js — «Crear cuenta» (SCRUM-110, R2)

   Ejecutan el registro.js real sobre un DOM mínimo construido con los `id` de
   index.html y un `fetch` falso. **Todas las respuestas del adaptador son
   simuladas**: estas pruebas no levantan el adaptador ni la API central. La
   prueba contra el backend real es otra (ver docs/autorregistro_pacientes.md).

   Sin dependencias: `node:test`, `node:vm` y `node:assert`.

   Todos los datos son ficticios y simulados.
   ========================================================================= */

'use strict';

process.env.TZ = 'America/Panama';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const DIRECTORIO = path.resolve(__dirname, '..');
const HTML = fs.readFileSync(path.join(DIRECTORIO, 'index.html'), 'utf8');
const REGISTRO_JS = fs.readFileSync(path.join(DIRECTORIO, 'registro.js'), 'utf8');
const APP_JS = fs.readFileSync(path.join(DIRECTORIO, 'app.js'), 'utf8');

const RUTA_DOCUMENTOS = 'GET /adaptador/registro/documentos';
const RUTA_REGISTRO = 'POST /adaptador/registro';

// ---------------------------------------------------------------------------
// DOM mínimo
// ---------------------------------------------------------------------------

class Elemento {
  constructor(etiqueta, atributos) {
    this.tagName = etiqueta.toUpperCase();
    this.atributos = Object.assign({}, atributos || {});
    this.hijos = [];
    this.texto = '';
    this.oyentes = {};
    this.dataset = {};
    this.clases = new Set((this.atributos.class || '').split(/\s+/).filter(Boolean));
    this.value = '';
    this.checked = false;
    this.hidden = 'hidden' in this.atributos;
    this.disabled = 'disabled' in this.atributos;
    this.enfocado = false;
    Object.keys(this.atributos).forEach((nombre) => {
      if (nombre.startsWith('data-')) this.dataset[nombre.slice(5)] = this.atributos[nombre];
    });
  }
  get className() { return Array.from(this.clases).join(' '); }
  set className(v) { this.clases = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const propio = this;
    return {
      toggle(n, f) { const p = f === undefined ? !propio.clases.has(n) : f; if (p) propio.clases.add(n); else propio.clases.delete(n); },
      contains(n) { return propio.clases.has(n); }
    };
  }
  get textContent() { return this.texto + this.hijos.map((h) => h.textContent).join(''); }
  set textContent(v) { this.hijos = []; this.texto = String(v); }
  set innerHTML(v) {
    assert.equal(v, '', 'solo se vacía con innerHTML, nunca se inyecta marcado');
    this.hijos = []; this.texto = '';
  }
  appendChild(h) { this.hijos.push(h); return h; }
  setAttribute(n, v) { this.atributos[n] = String(v); }
  getAttribute(n) { return n in this.atributos ? this.atributos[n] : null; }
  hasAttribute(n) { return n in this.atributos; }
  removeAttribute(n) { delete this.atributos[n]; }
  focus() { documentoActual.activo = this; this.enfocado = true; }
  addEventListener(t, o) { (this.oyentes[t] = this.oyentes[t] || []).push(o); }
  disparar(tipo) {
    const evento = { type: tipo, preventDefault() {} };
    (this.oyentes[tipo] || []).forEach((o) => o(evento));
  }
  click() { if (!this.disabled) this.disparar('click'); }
  querySelectorAll(selector) {
    assert.equal(selector, 'a[data-vista]');
    return this.hijos.filter((h) => h.tagName === 'A' && 'vista' in h.dataset);
  }
  /** Las etiquetas de los nodos construidos, en orden: para ver la estructura. */
  estructura() {
    return this.hijos.map((h) => h.tagName.toLowerCase());
  }
}

class Dialogo extends Elemento {
  showModal() { this.atributos.open = ''; }
  close() { delete this.atributos.open; this.disparar('close'); }
}

let documentoActual = null;

function atributosDe(fuente) {
  const atributos = {};
  const patron = /([a-zA-Z-]+)(?:="([^"]*)")?/g;
  let m;
  while ((m = patron.exec(fuente))) atributos[m[1]] = m[2] === undefined ? '' : m[2];
  return atributos;
}

function construirDocumento() {
  const porId = {};
  const patron = /<([a-z0-9]+)\s([^>]*\bid="([^"]+)"[^>]*)>/g;
  let m;
  while ((m = patron.exec(HTML))) {
    const Clase = m[1] === 'dialog' ? Dialogo : Elemento;
    porId[m[3]] = new Clase(m[1], atributosDe(m[2]));
  }
  const nav = HTML.match(/<nav[^>]*id="main-menu"[^>]*>([\s\S]*?)<\/nav>/)[1];
  const enlaces = /<a\s([^>]*)>([^<]*)<\/a>/g;
  while ((m = enlaces.exec(nav))) {
    const enlace = new Elemento('a', atributosDe(m[1]));
    enlace.texto = m[2].trim();
    porId['main-menu'].appendChild(enlace);
  }
  const oyentes = {};
  const doc = {
    porId,
    activo: null,
    visibilityState: 'visible',
    getElementById(id) { return porId[id] || null; },
    createElement(etiqueta) { return new Elemento(etiqueta); },
    addEventListener(t, o) { (oyentes[t] = oyentes[t] || []).push(o); },
    disparar(t) { (oyentes[t] || []).forEach((o) => o()); }
  };
  documentoActual = doc;
  return doc;
}

// ---------------------------------------------------------------------------
// Adaptador falso
// ---------------------------------------------------------------------------

const DOCUMENTOS = {
  documentos: [
    {
      documento: 'POLITICA_PRIVACIDAD',
      version: '2026-11-v1',
      titulo: 'Política de privacidad',
      texto:
        '# Política de privacidad de FetalAlert\n\n## 1. Qué es\n\nUn prototipo con **datos ficticios**.\n\n' +
        '- Nombres y cédula.\n- Un celular.\n\n<script>alert(1)</script>'
    },
    {
      documento: 'TERMINOS_CONDICIONES',
      version: '2026-11-v1',
      titulo: 'Términos y condiciones',
      texto: '# Términos\n\n## 1. Uso\n\nSolo demostración.'
    }
  ]
};

function respuesta(estado, cuerpo) {
  return { ok: estado >= 200 && estado <= 299, status: estado, json: () => Promise.resolve(cuerpo) };
}

function crearAdaptador(rutas) {
  const llamadas = [];
  const diferidas = [];
  let diferir = null;
  function fetch(url, opciones) {
    const metodo = (opciones && opciones.method) || 'GET';
    llamadas.push({ metodo, url, cuerpo: opciones && opciones.body ? JSON.parse(opciones.body) : null });
    const clave = metodo + ' ' + url;
    const manejador = rutas[clave];
    if (manejador === undefined) return Promise.reject(new Error('ruta no prevista: ' + clave));
    const producir = () => {
      const valor = typeof manejador === 'function' ? manejador() : manejador;
      if (valor === 'red-caida') return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve(respuesta(valor[0], valor[1]));
    };
    if (diferir && diferir(clave)) {
      return new Promise((resolver, rechazar) =>
        diferidas.push({ clave, soltar: () => producir().then(resolver, rechazar) }));
    }
    return producir();
  }
  return {
    fetch,
    llamadas,
    rutas,
    diferidas,
    diferirSi(filtro) { diferir = filtro; },
    contar(clave) { return llamadas.filter((l) => l.metodo + ' ' + l.url === clave).length; },
    ultimoCuerpo(clave) {
      const propias = llamadas.filter((l) => l.metodo + ' ' + l.url === clave);
      return propias.length ? propias[propias.length - 1].cuerpo : null;
    }
  };
}

async function asentar() {
  for (let i = 0; i !== 12; i += 1) await new Promise((r) => setImmediate(r));
}

/**
 * Arranca registro.js (y, si se pide, app.js) con reloj, temporizadores y
 * MutationObserver controlados.
 */
async function arrancar(rutas, opciones) {
  const conApp = Boolean(opciones && opciones.conApp);
  const documento = construirDocumento();
  const adaptador = crearAdaptador(Object.assign({ [RUTA_DOCUMENTOS]: [200, DOCUMENTOS] }, rutas));
  const reloj = { ms: Date.UTC(2026, 10, 5, 15, 0, 0) };
  const temporizadores = new Map();
  const observadores = [];
  let siguiente = 0;
  class MutationObserverFalso {
    constructor(fn) { this.fn = fn; observadores.push(this); }
    observe(objetivo) { this.objetivo = objetivo; }
  }
  const contexto = vm.createContext({
    document: documento,
    window: {
      setTimeout: (fn, ms) => { siguiente += 1; temporizadores.set(siguiente, { fn, ms }); return siguiente; },
      clearTimeout: (id) => { temporizadores.delete(id); },
      setInterval: () => 0,
      clearInterval: () => {}
    },
    MutationObserver: MutationObserverFalso,
    fetch: adaptador.fetch,
    console,
    Intl,
    __reloj: () => reloj.ms
  });
  vm.runInContext('Date.now = function () { return __reloj(); };', contexto);
  if (conApp) vm.runInContext(APP_JS, contexto, { filename: 'app.js' });
  vm.runInContext(REGISTRO_JS, contexto, { filename: 'registro.js' });
  documento.disparar('DOMContentLoaded');
  await asentar();
  const $ = (id) => documento.porId[id];
  return {
    $, adaptador, documento, reloj, temporizadores,
    /** Ejecuta los temporizadores vencidos, como si hubiera pasado el tiempo. */
    async avanzar(ms) {
      reloj.ms += ms;
      Array.from(temporizadores.entries()).forEach(([id, t]) => {
        if (t.ms <= ms) { temporizadores.delete(id); t.fn(); }
      });
      await asentar();
    },
    /** Lo que haría el navegador cuando app.js cambia `hidden` de la vista. */
    async notificarMutacion() {
      observadores.forEach((o) => o.fn([]));
      await asentar();
    }
  };
}

const DATOS_VALIDOS = {
  primer_nombre: '  Ana ',
  segundo_nombre: '   ',
  apellido_paterno: 'Pérez',
  apellido_materno: '',
  cedula: ' SIM-8-123-456 ',
  fecha_nac: '1995-04-12',
  email: ' Paciente31@Example.com ',
  celular: '6000-0000',
  password: ' clave con espacios ',
  confirmacion_password: ' clave con espacios '
};

function llenar($, datos) {
  Object.keys(datos).forEach((nombre) => { $('reg-' + nombre).value = datos[nombre]; });
}

async function abrirYLlenar(entorno, datos) {
  const { $ } = entorno;
  $('btn-crear-cuenta').click();
  await asentar();
  llenar($, Object.assign({}, DATOS_VALIDOS, datos || {}));
  $('reg-acepta-politica').checked = true;
  $('reg-acepta-terminos').checked = true;
}

function enviar($) {
  $('form-registro').disparar('submit');
}

const CAMPOS_DE_TEXTO = Object.keys(DATOS_VALIDOS);

function textoVisible($) {
  return ['registro-resumen', 'registro-resultado-titulo', 'registro-resultado-texto']
    .concat(CAMPOS_DE_TEXTO.map((c) => 'err-' + c))
    .map((id) => $(id).textContent).join(' ');
}

// ---------------------------------------------------------------------------
// Estado inicial y apertura
// ---------------------------------------------------------------------------

test('Al cargar: se ve el inicio de sesión, el formulario está oculto y vacío', async () => {
  const { $, adaptador } = await arrancar({});

  assert.equal($('panel-inicio-sesion').hidden, false);
  assert.equal($('panel-registro').hidden, true);
  assert.equal($('panel-registro-resultado').hidden, true);
  CAMPOS_DE_TEXTO.forEach((c) => assert.equal($('reg-' + c).value, '', c));
  assert.equal($('reg-acepta-politica').checked, false);
  assert.equal($('reg-acepta-terminos').checked, false);
  // Hasta abrir el formulario no se pide nada.
  assert.equal(adaptador.llamadas.length, 0);
});

test('«Crear cuenta» abre el formulario y carga los documentos una vez; las casillas siguen desmarcadas', async () => {
  const { $, adaptador } = await arrancar({});

  $('btn-crear-cuenta').click();
  await asentar();

  assert.equal($('panel-inicio-sesion').hidden, true);
  assert.equal($('panel-registro').hidden, false);
  assert.equal($('registro-titulo').enfocado, true);
  assert.equal(adaptador.contar(RUTA_DOCUMENTOS), 1);
  for (const id of ['reg-acepta-politica', 'reg-acepta-terminos']) {
    assert.equal($(id).disabled, false, id);
    assert.equal($(id).checked, false, id);
  }
  assert.equal($('btn-leer-politica').disabled, false);

  // Volver y abrir otra vez no vuelve a pedirlos.
  $('btn-registro-volver').click();
  $('btn-crear-cuenta').click();
  await asentar();
  assert.equal(adaptador.contar(RUTA_DOCUMENTOS), 1);
});

test('Sin documentos no se puede aceptar ni enviar; «Volver a cargar» los vuelve a pedir', async () => {
  let fallar = true;
  const { $, adaptador } = await arrancar({
    [RUTA_DOCUMENTOS]: () => (fallar ? [502, { detail: 'detalle interno' }] : [200, DOCUMENTOS])
  });

  $('btn-crear-cuenta').click();
  await asentar();

  assert.equal($('reg-acepta-politica').disabled, true);
  assert.equal($('btn-leer-politica').disabled, true);
  assert.match($('registro-documentos-estado').textContent, /No se pudieron cargar/);
  assert.doesNotMatch($('registro-documentos-estado').textContent, /detalle interno/);
  assert.equal($('btn-reintentar-documentos').hidden, false);

  llenar($, DATOS_VALIDOS);
  enviar($);
  await asentar();
  assert.equal(adaptador.contar(RUTA_REGISTRO), 0);

  fallar = false;
  $('btn-reintentar-documentos').click();
  await asentar();
  assert.equal(adaptador.contar(RUTA_DOCUMENTOS), 2);
  assert.equal($('reg-acepta-politica').disabled, false);
  assert.equal($('btn-reintentar-documentos').hidden, true);
});

// ---------------------------------------------------------------------------
// Diálogo de los documentos
// ---------------------------------------------------------------------------

test('El diálogo muestra el texto del servidor como nodos, sin interpretar marcado, y devuelve el foco', async () => {
  const { $ } = await arrancar({});
  $('btn-crear-cuenta').click();
  await asentar();

  $('btn-leer-politica').click();

  const dialogo = $('dialogo-documento');
  assert.equal(dialogo.hasAttribute('open'), true);
  assert.equal($('documento-titulo').textContent, 'Política de privacidad');
  assert.equal($('documento-version').textContent, 'Versión 2026-11-v1');
  assert.equal($('documento-texto').enfocado, true);
  // «## » es un apartado, «- » una lista; el título principal no se repite.
  assert.deepEqual($('documento-texto').estructura(), ['h3', 'p', 'ul', 'p']);
  // El «<script>» del texto llega como texto, no como un elemento.
  assert.match($('documento-texto').textContent, /<script>alert\(1\)<\/script>/);
  // Abrir el documento no marca la casilla.
  assert.equal($('reg-acepta-politica').checked, false);

  $('btn-cerrar-documento').click();
  assert.equal(dialogo.hasAttribute('open'), false);
  assert.equal(documentoActual.activo, $('btn-leer-politica'));
});

// ---------------------------------------------------------------------------
// Validación de ayuda: nada sale si el formulario no está completo
// ---------------------------------------------------------------------------

test('Un formulario vacío no se envía y marca cada obligatorio y las dos casillas', async () => {
  const { $, adaptador } = await arrancar({});
  $('btn-crear-cuenta').click();
  await asentar();

  enviar($);
  await asentar();

  assert.equal(adaptador.contar(RUTA_REGISTRO), 0);
  for (const c of ['primer_nombre', 'apellido_paterno', 'cedula', 'fecha_nac', 'email', 'celular', 'password', 'confirmacion_password']) {
    assert.equal($('reg-' + c).getAttribute('aria-invalid'), 'true', c);
    assert.equal($('err-' + c).hidden, false, c);
  }
  for (const c of ['segundo_nombre', 'apellido_materno']) {
    assert.equal($('reg-' + c).getAttribute('aria-invalid'), null, c);
  }
  assert.equal($('reg-acepta-politica').getAttribute('aria-invalid'), 'true');
  assert.equal($('reg-acepta-terminos').getAttribute('aria-invalid'), 'true');
  assert.equal($('registro-resumen').hidden, false);
  assert.equal($('reg-primer_nombre').enfocado, true);
});

test('Fecha futura, inexistente o mal escrita, correo fuera del formato asignado y contraseñas distintas se señalan sin enviar', async () => {
  const casos = [
    [{ fecha_nac: '2026-11-06' }, 'fecha_nac', /posterior a hoy/],
    [{ fecha_nac: '2023-02-29' }, 'fecha_nac', /no existe/],
    [{ fecha_nac: '12/04/1995' }, 'fecha_nac', /AAAA-MM-DD/],
    [{ email: 'ana.example.com' }, 'email', /pacienteNN@example\.com\. Por ejemplo: paciente31@example\.com/],
    [{ email: 'ana perez@example.com' }, 'email', /pacienteNN@example\.com/],
    [{ email: 'ana.perez@example.com' }, 'email', /pacienteNN@example\.com/],
    [{ email: 'paciente1@example.com' }, 'email', /pacienteNN@example\.com/],
    [{ email: 'paciente31@example.co' }, 'email', /pacienteNN@example\.com/],
    [{ email: 'paciente31@correo.example.com' }, 'email', /pacienteNN@example\.com/],
    [{ confirmacion_password: ' clave con espacios' }, 'confirmacion_password', /no coinciden/],
    [{ primer_nombre: '    ' }, 'primer_nombre', /obligatorio/],
    [{ cedula: 'X'.repeat(21) }, 'cedula', /demasiado largo/]
  ];
  for (const [cambio, campo, mensaje] of casos) {
    const entorno = await arrancar({ [RUTA_REGISTRO]: [201, { creada: true }] });
    await abrirYLlenar(entorno, cambio);
    enviar(entorno.$);
    await asentar();
    assert.equal(entorno.adaptador.contar(RUTA_REGISTRO), 0, JSON.stringify(cambio));
    assert.match(entorno.$('err-' + campo).textContent, mensaje, JSON.stringify(cambio));
  }
});

test('Hoy en Panamá sí es una fecha de nacimiento válida, aunque en UTC ya sea mañana', async () => {
  const entorno = await arrancar({ [RUTA_REGISTRO]: [201, { creada: true }] });
  // 2026-11-06 03:00 UTC = 2026-11-05 22:00 en Panamá.
  entorno.reloj.ms = Date.UTC(2026, 10, 6, 3, 0, 0);
  await abrirYLlenar(entorno, { fecha_nac: '2026-11-05' });
  enviar(entorno.$);
  await asentar();
  assert.equal(entorno.adaptador.contar(RUTA_REGISTRO), 1);
});

// ---------------------------------------------------------------------------
// Envío correcto
// ---------------------------------------------------------------------------

test('El cuerpo lleva exactamente los campos del contrato, recortados salvo la contraseña', async () => {
  const entorno = await arrancar({ [RUTA_REGISTRO]: [201, { creada: true }] });
  await abrirYLlenar(entorno);
  enviar(entorno.$);
  await asentar();

  assert.deepEqual(entorno.adaptador.ultimoCuerpo(RUTA_REGISTRO), {
    primer_nombre: 'Ana',
    segundo_nombre: null,
    apellido_paterno: 'Pérez',
    apellido_materno: null,
    cedula: 'SIM-8-123-456',
    fecha_nac: '1995-04-12',
    email: 'Paciente31@Example.com',
    celular: '6000-0000',
    password: ' clave con espacios ',
    confirmacion_password: ' clave con espacios ',
    acepta_politica_privacidad: true,
    version_politica_privacidad: '2026-11-v1',
    acepta_terminos_condiciones: true,
    version_terminos_condiciones: '2026-11-v1'
  });
});

test('Mientras se envía no hay segundo envío: botón desactivado y el submit repetido se ignora', async () => {
  const entorno = await arrancar({ [RUTA_REGISTRO]: [201, { creada: true }] });
  const { $, adaptador } = entorno;
  adaptador.diferirSi((clave) => clave === RUTA_REGISTRO);
  await abrirYLlenar(entorno);

  enviar($);
  await asentar();
  assert.equal($('btn-registro-enviar').disabled, true);
  assert.equal($('btn-registro-enviar').textContent, 'Creando la cuenta…');
  assert.equal($('btn-registro-volver').disabled, true);
  assert.equal($('form-registro').getAttribute('aria-busy'), 'true');

  enviar($);
  enviar($);
  $('btn-registro-volver').click();
  await asentar();
  assert.equal(adaptador.contar(RUTA_REGISTRO), 1);
  assert.equal($('panel-registro').hidden, false);

  adaptador.diferidas.forEach((d) => d.soltar());
  await asentar();
  assert.equal($('panel-registro-resultado').hidden, false);
});

test('Solo tras el 201 se confirma; el formulario se vacía y se ofrece el inicio de sesión de siempre', async () => {
  const entorno = await arrancar({ [RUTA_REGISTRO]: [201, { creada: true }] });
  const { $ } = entorno;
  await abrirYLlenar(entorno);
  enviar($);
  await asentar();

  assert.equal($('panel-registro').hidden, true);
  assert.equal($('panel-registro-resultado').hidden, false);
  assert.equal($('registro-resultado-titulo').textContent, 'Tu cuenta está lista');
  assert.equal($('registro-resultado-titulo').enfocado, true);
  assert.equal($('btn-resultado-volver-formulario').hidden, true);
  CAMPOS_DE_TEXTO.forEach((c) => assert.equal($('reg-' + c).value, '', c));
  assert.equal($('reg-acepta-politica').checked, false);

  $('btn-resultado-iniciar-sesion').click();
  assert.equal($('panel-inicio-sesion').hidden, false);
  assert.equal($('panel-registro-resultado').hidden, true);
  // El correo, recortado, para no volver a escribirlo; la contraseña no.
  assert.equal($('login-email').value, 'Paciente31@Example.com');
  assert.equal($('login-password').value, '');
  assert.equal($('login-password').enfocado, true);
});

// ---------------------------------------------------------------------------
// Rechazos y fallos: nada de detalles internos
// ---------------------------------------------------------------------------

test('409: mensaje propio, sin decir qué dato existía; los datos se conservan y las contraseñas no', async () => {
  const entorno = await arrancar({
    [RUTA_REGISTRO]: [409, { detail: 'duplicate key value violates unique constraint uq_paciente_cedula' }]
  });
  const { $ } = entorno;
  await abrirYLlenar(entorno);
  enviar($);
  await asentar();

  assert.equal($('panel-registro').hidden, false);
  assert.match($('registro-resumen').textContent, /Ya existe una cuenta con esa cédula o ese correo/);
  assert.doesNotMatch(textoVisible($), /unique|constraint|uq_/);
  assert.equal($('reg-cedula').value, ' SIM-8-123-456 ');
  assert.equal($('reg-password').value, '');
  assert.equal($('reg-confirmacion_password').value, '');
  assert.equal($('btn-registro-enviar').disabled, false);
});

test('422: se marcan solo los campos que devolvió el adaptador, con un texto propio', async () => {
  const entorno = await arrancar({
    [RUTA_REGISTRO]: [422, { detail: 'String should have at most 20 characters', campos: ['cedula', 'celular'] }]
  });
  const { $ } = entorno;
  await abrirYLlenar(entorno);
  enviar($);
  await asentar();

  assert.equal($('reg-cedula').getAttribute('aria-invalid'), 'true');
  assert.equal($('reg-celular').getAttribute('aria-invalid'), 'true');
  assert.equal($('reg-email').getAttribute('aria-invalid'), null);
  assert.equal($('err-cedula').textContent, 'Revisa este dato.');
  assert.doesNotMatch(textoVisible($), /should have/);
  assert.equal($('reg-cedula').enfocado, true);
});

test('422 del correo: se repite la regla del correo asignado, con su ejemplo', async () => {
  const entorno = await arrancar({
    [RUTA_REGISTRO]: [422, { detail: 'Value error', campos: ['email'] }]
  });
  const { $ } = entorno;
  await abrirYLlenar(entorno);
  enviar($);
  await asentar();

  assert.equal($('reg-email').getAttribute('aria-invalid'), 'true');
  assert.match($('err-email').textContent, /pacienteNN@example\.com\. Por ejemplo: paciente31@example\.com/);
  assert.doesNotMatch(textoVisible($), /Value error/);
});

test('422 por versión: se desmarcan las casillas y se recargan los documentos', async () => {
  const entorno = await arrancar({
    [RUTA_REGISTRO]: [422, { campos: ['version_politica_privacidad'] }]
  });
  const { $, adaptador } = entorno;
  await abrirYLlenar(entorno);
  enviar($);
  await asentar();

  assert.equal($('reg-acepta-politica').checked, false);
  assert.equal($('reg-acepta-terminos').checked, false);
  assert.equal(adaptador.contar(RUTA_DOCUMENTOS), 2);
  assert.match($('registro-resumen').textContent, /cambiaron/);
});

test('429: se dice cuánto esperar, el botón queda bloqueado ese tiempo y no se reintenta solo', async () => {
  const entorno = await arrancar({
    [RUTA_REGISTRO]: [429, { detail: 'x', reintentar_en_segundos: 125 }]
  });
  const { $, adaptador } = entorno;
  await abrirYLlenar(entorno);
  enviar($);
  await asentar();

  assert.match($('registro-resumen').textContent, /Espera 3 minutos/);
  assert.equal($('btn-registro-enviar').disabled, true);
  llenar($, { password: DATOS_VALIDOS.password, confirmacion_password: DATOS_VALIDOS.password });
  enviar($);
  await asentar();
  assert.equal(adaptador.contar(RUTA_REGISTRO), 1);

  await entorno.avanzar(125000);
  assert.equal($('btn-registro-enviar').disabled, false);
  // Pasado el tiempo tampoco se envía nada por su cuenta.
  assert.equal(adaptador.contar(RUTA_REGISTRO), 1);
});

test('503: el adaptador sabe que no salió, y solo entonces se dice que no se envió', async () => {
  const entorno = await arrancar({ [RUTA_REGISTRO]: [503, { enviado: false }] });
  const { $ } = entorno;
  await abrirYLlenar(entorno);
  enviar($);
  await asentar();

  assert.match($('registro-resumen').textContent, /Tu registro no se envió/);
  assert.equal($('panel-registro').hidden, false);
});

for (const [nombre, manejador] of [
  ['504 del adaptador', [504, { resultado: 'desconocido' }]],
  ['la red entre navegador y adaptador', 'red-caida']
]) {
  test('Respuesta perdida (' + nombre + '): no se afirma que no se guardó y no se reenvía', async () => {
    const entorno = await arrancar({ [RUTA_REGISTRO]: manejador });
    const { $, adaptador } = entorno;
    await abrirYLlenar(entorno);
    enviar($);
    await asentar();

    assert.equal($('panel-registro-resultado').hidden, false);
    assert.equal($('registro-resultado-titulo').textContent, 'No sabemos si tu cuenta se creó');
    assert.match($('registro-resultado-texto').textContent, /intenta iniciar sesión/);
    assert.doesNotMatch(textoVisible($), /no se (envió|guardó|creó)/i);
    assert.equal($('btn-resultado-volver-formulario').hidden, false);

    await entorno.avanzar(600000);
    assert.equal(adaptador.contar(RUTA_REGISTRO), 1);

    // De vuelta al formulario: los datos siguen, salvo las contraseñas, y
    // reenviar exige escribirlas otra vez.
    $('btn-resultado-volver-formulario').click();
    assert.equal($('panel-registro').hidden, false);
    assert.equal($('reg-cedula').value, ' SIM-8-123-456 ');
    assert.equal($('reg-password').value, '');
    enviar($);
    await asentar();
    assert.equal(adaptador.contar(RUTA_REGISTRO), 1);
  });
}

test('Cualquier otro fallo del servidor: mensaje genérico, sin el detalle', async () => {
  const entorno = await arrancar({
    [RUTA_REGISTRO]: [502, { detail: 'Traceback (most recent call last): psycopg.errors' }]
  });
  const { $ } = entorno;
  await abrirYLlenar(entorno);
  enviar($);
  await asentar();
  assert.match($('registro-resumen').textContent, /no pudo completar el registro/);
  assert.doesNotMatch(textoVisible($), /Traceback|psycopg/);
});

// ---------------------------------------------------------------------------
// Nada queda escrito
// ---------------------------------------------------------------------------

test('Volver a iniciar sesión vacía el formulario entero', async () => {
  const entorno = await arrancar({});
  const { $ } = entorno;
  await abrirYLlenar(entorno);
  $('btn-registro-volver').click();

  assert.equal($('panel-inicio-sesion').hidden, false);
  CAMPOS_DE_TEXTO.forEach((c) => assert.equal($('reg-' + c).value, '', c));
  assert.equal($('reg-acepta-politica').checked, false);
  assert.equal($('login-email').value, '');
});

test('Si app.js oculta la vista (se abrió el portal), el formulario se vacía y vuelve el panel de login', async () => {
  const entorno = await arrancar({});
  const { $ } = entorno;
  await abrirYLlenar(entorno);
  $('btn-leer-terminos').click();

  $('vista-login').hidden = true;
  await entorno.notificarMutacion();

  CAMPOS_DE_TEXTO.forEach((c) => assert.equal($('reg-' + c).value, '', c));
  assert.equal($('dialogo-documento').hasAttribute('open'), false);
  assert.equal($('panel-registro').hidden, true);
  assert.equal($('panel-inicio-sesion').hidden, false);
});

// ---------------------------------------------------------------------------
// Convivencia con app.js
// ---------------------------------------------------------------------------

test('Con app.js cargado, el inicio de sesión sigue igual y «Crear cuenta» no lo toca', async () => {
  const entorno = await arrancar(
    {
      'GET /adaptador/sesion': [200, { autenticada: false }],
      'POST /adaptador/iniciar-sesion': [401, { detail: 'Correo o contraseña incorrectos.' }]
    },
    { conApp: true }
  );
  const { $, adaptador } = entorno;

  // app.js arrancó y mostró el login; el formulario sigue oculto.
  assert.equal($('vista-login').hidden, false);
  assert.equal($('panel-registro').hidden, true);

  $('btn-crear-cuenta').click();
  await asentar();
  assert.equal(adaptador.contar('POST /adaptador/iniciar-sesion'), 0);
  $('btn-registro-volver').click();

  $('login-email').value = 'paciente01@example.com';
  $('login-password').value = 'clave';
  $('form-login').disparar('submit');
  await asentar();
  assert.equal(adaptador.contar('POST /adaptador/iniciar-sesion'), 1);
  assert.equal($('login-password').value, '');
  assert.equal(adaptador.contar(RUTA_REGISTRO), 0);
});
