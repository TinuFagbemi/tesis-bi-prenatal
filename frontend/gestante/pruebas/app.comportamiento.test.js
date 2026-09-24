/* =========================================================================
   Pruebas de comportamiento de app.js (SCRUM-72).

   Ejecutan el app.js real sobre un DOM mínimo construido a partir de los
   `id` de index.html, con un `fetch` falso y controlable. Sin dependencias:
   solo `node:test`, `node:vm` y `node:assert`.

   Cubren lo que una prueba de texto no puede ver:

   - Inicio e Historial son contextos independientes.
   - Una respuesta tardía de una selección ya superada no pisa la vigente.
   - Una última lectura con solo movimientos deja FC y SpO2 como «—».
   - El historial muestra las lecturas del dataset que sí tienen FC y SpO2.
   - Los mensajes de envío dicen lo que el adaptador confirmó, y nada más.
   - Cerrar sesión pide confirmación; Cancelar no cierra nada.

   Los valores de los episodios 100 y 130 y de las lecturas 110, 111 y 679 se
   copian literalmente de data/generated (cuenta 107, paciente 100) y de lo que
   `provisionar_demo.py` agrega (embarazo 130). Todos los datos son ficticios
   y simulados.
   ========================================================================= */

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const DIRECTORIO = path.resolve(__dirname, '..');
const HTML = fs.readFileSync(path.join(DIRECTORIO, 'index.html'), 'utf8');
const JS = fs.readFileSync(path.join(DIRECTORIO, 'app.js'), 'utf8');

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
    this.clases = new Set();
    this.value = '';
    this.hidden = 'hidden' in this.atributos;
    this.disabled = 'disabled' in this.atributos;
    this.enfocado = false;
    if (this.atributos.class) {
      this.atributos.class.split(/\s+/).forEach((c) => c && this.clases.add(c));
    }
    Object.keys(this.atributos).forEach((nombre) => {
      if (nombre.startsWith('data-')) {
        this.dataset[nombre.slice(5)] = this.atributos[nombre];
      }
    });
  }

  get className() {
    return Array.from(this.clases).join(' ');
  }
  set className(valor) {
    this.clases = new Set(String(valor).split(/\s+/).filter(Boolean));
  }
  get classList() {
    const propio = this;
    return {
      toggle(nombre, forzar) {
        const poner = forzar === undefined ? !propio.clases.has(nombre) : forzar;
        if (poner) propio.clases.add(nombre);
        else propio.clases.delete(nombre);
      },
      contains(nombre) {
        return propio.clases.has(nombre);
      }
    };
  }

  get textContent() {
    return this.texto + this.hijos.map((h) => h.textContent).join('');
  }
  set textContent(valor) {
    this.hijos = [];
    this.texto = String(valor);
  }
  set innerHTML(valor) {
    assert.equal(valor, '', 'app.js solo vacía con innerHTML, nunca inyecta marcado');
    this.hijos = [];
    this.texto = '';
  }

  appendChild(hijo) {
    this.hijos.push(hijo);
    return hijo;
  }
  setAttribute(nombre, valor) {
    this.atributos[nombre] = String(valor);
  }
  getAttribute(nombre) {
    return nombre in this.atributos ? this.atributos[nombre] : null;
  }
  hasAttribute(nombre) {
    return nombre in this.atributos;
  }
  removeAttribute(nombre) {
    delete this.atributos[nombre];
  }
  focus() {
    this.enfocado = true;
  }
  addEventListener(tipo, oyente) {
    (this.oyentes[tipo] = this.oyentes[tipo] || []).push(oyente);
  }
  disparar(tipo) {
    const evento = { type: tipo, preventDefault() {} };
    (this.oyentes[tipo] || []).forEach((oyente) => oyente(evento));
  }
  click() {
    if (!this.disabled) this.disparar('click');
  }
  querySelectorAll(selector) {
    assert.equal(selector, 'a[data-vista]');
    return this.hijos.filter((h) => h.tagName === 'A' && 'vista' in h.dataset);
  }
  // Todas las filas de datos de una tabla construida por app.js.
  filas() {
    const encontradas = [];
    const recorrer = (nodo) => {
      if (nodo.tagName === 'TR' && nodo.hijos.every((h) => h.tagName === 'TD')) {
        encontradas.push(nodo.hijos.map((td) => td.textContent));
      }
      nodo.hijos.forEach(recorrer);
    };
    recorrer(this);
    return encontradas;
  }
}

class Dialogo extends Elemento {
  showModal() {
    this.atributos.open = '';
  }
  close() {
    delete this.atributos.open;
  }
}

function atributosDe(fuente) {
  const atributos = {};
  const patron = /([a-zA-Z-]+)(?:="([^"]*)")?/g;
  let m;
  while ((m = patron.exec(fuente))) {
    atributos[m[1]] = m[2] === undefined ? '' : m[2];
  }
  return atributos;
}

/** Un documento con un elemento por cada `id` de index.html. */
function construirDocumento() {
  const porId = {};
  const patron = /<([a-z0-9]+)\s([^>]*\bid="([^"]+)"[^>]*)>/g;
  let m;
  while ((m = patron.exec(HTML))) {
    const Clase = m[1] === 'dialog' ? Dialogo : Elemento;
    porId[m[3]] = new Clase(m[1], atributosDe(m[2]));
  }

  // Los enlaces del menú, en orden, como hijos del <nav>.
  const nav = HTML.match(/<nav[^>]*id="main-menu"[^>]*>([\s\S]*?)<\/nav>/)[1];
  const enlaces = /<a\s([^>]*)>([^<]*)<\/a>/g;
  while ((m = enlaces.exec(nav))) {
    const enlace = new Elemento('a', atributosDe(m[1]));
    enlace.texto = m[2].trim();
    porId['main-menu'].appendChild(enlace);
  }

  const oyentes = {};
  return {
    porId,
    getElementById(id) {
      return porId[id] || null;
    },
    createElement(etiqueta) {
      return new Elemento(etiqueta);
    },
    addEventListener(tipo, oyente) {
      (oyentes[tipo] = oyentes[tipo] || []).push(oyente);
    },
    disparar(tipo) {
      (oyentes[tipo] || []).forEach((o) => o());
    }
  };
}

// ---------------------------------------------------------------------------
// Adaptador falso
// ---------------------------------------------------------------------------

function respuesta(estado, cuerpo) {
  return {
    ok: estado >= 200 && estado <= 299,
    status: estado,
    json: () => Promise.resolve(cuerpo)
  };
}

/** Un `fetch` falso: rutas fijas, o diferidas para controlar el orden. */
function crearAdaptador(rutas) {
  const llamadas = [];
  const diferidas = [];
  let diferir = null;

  function fetch(url, opciones) {
    const metodo = (opciones && opciones.method) || 'GET';
    llamadas.push({ metodo, url });
    const clave = metodo + ' ' + url;
    const manejador = rutas[clave];
    if (manejador === undefined) {
      return Promise.reject(new Error('ruta no prevista: ' + clave));
    }
    const producir = () => {
      const [estado, cuerpo] = typeof manejador === 'function' ? manejador() : manejador;
      return respuesta(estado, cuerpo);
    };
    if (diferir && diferir(clave)) {
      return new Promise((resolver) => diferidas.push({ clave, soltar: () => resolver(producir()) }));
    }
    return Promise.resolve(producir());
  }

  return {
    fetch,
    llamadas,
    rutas,
    diferidas,
    diferirSi(filtro) {
      diferir = filtro;
    },
    contar(metodo, url) {
      return llamadas.filter((l) => l.metodo === metodo && l.url === url).length;
    }
  };
}

async function asentar() {
  for (let i = 0; i !== 12; i += 1) {
    await new Promise((resolver) => setImmediate(resolver));
  }
}

async function arrancar(rutas) {
  const documento = construirDocumento();
  const adaptador = crearAdaptador(rutas);
  const contexto = vm.createContext({
    document: documento,
    window: { setInterval: () => 1, clearInterval: () => {} },
    fetch: adaptador.fetch,
    console
  });
  vm.runInContext(JS, contexto, { filename: 'app.js' });
  documento.disparar('DOMContentLoaded');
  await asentar();
  const $ = (id) => documento.porId[id];
  return { $, adaptador, documento };
}

// ---------------------------------------------------------------------------
// Datos del dataset (cuenta 107)
// ---------------------------------------------------------------------------

const EMBARAZO_ACTUAL = {
  // Agregado por scripts/provisionar_demo.py, anclado al reloj real.
  id_embarazo: 130,
  fecha_inicio: '2026-03-19',
  fecha_probable_parto: null,
  estado_embarazo: 'ACTIVO',
  fecha_cierre: null
};
const EMBARAZO_ANTERIOR = {
  // Canónico: data/generated/embarazos.csv, fila 100.
  id_embarazo: 100,
  fecha_inicio: '2025-01-06',
  fecha_probable_parto: '2025-10-13',
  estado_embarazo: 'FINALIZADO',
  fecha_cierre: '2025-10-13'
};

function embarazos(extra) {
  return [200, {
    disponible: true,
    datos: Object.assign({
      actual: EMBARAZO_ACTUAL,
      anteriores: [EMBARAZO_ANTERIOR],
      todos: [EMBARAZO_ACTUAL, EMBARAZO_ANTERIOR],
      ambiguo: false
    }, extra || {})
  }];
}

// Sesión de movimiento registrada con el flujo simulado: MOV_SIMULADO = 12.
const LECTURA_SIMULADA = {
  id_lectura: 5001,
  fecha_hora_captura: '2026-09-24T06:40:00+00:00',
  codigo_semaforo: 'OK',
  semana_gestacion: 27,
  hr_valor: null,
  spo2_valor: null,
  mov_valor: 12
};

// Lecturas canónicas del embarazo 100 (lecturas_biometricas.csv).
const LECTURA_110 = {
  id_lectura: 110,
  fecha_hora_captura: '2025-09-08T17:13:00+00:00',
  codigo_semaforo: 'OK',
  semana_gestacion: 36,
  hr_valor: '86.00',
  spo2_valor: '97.00',
  mov_valor: null
};
const LECTURA_111 = {
  id_lectura: 111,
  fecha_hora_captura: '2025-09-08T17:14:00+00:00',
  codigo_semaforo: 'OK',
  semana_gestacion: 36,
  hr_valor: '95.00',
  spo2_valor: '99.00',
  mov_valor: null
};
const LECTURA_679 = {
  id_lectura: 679,
  fecha_hora_captura: '2025-10-01T16:04:00+00:00',
  codigo_semaforo: 'WARNING',
  semana_gestacion: 39,
  hr_valor: null,
  spo2_valor: null,
  mov_valor: 7
};

function monitoreo(idEmbarazo, sesiones, ultima) {
  return [200, {
    disponible: true,
    datos: { id_embarazo: idEmbarazo, sesiones, ultima_lectura: ultima, id_sesion_de_la_ultima: null }
  }];
}

const MONITOREO_130 = monitoreo(130, [
  { id_sesion: 9001, tipo_sesion: 'MOVIMIENTOS_FETALES', estado_sesion: 'COMPLETADA',
    fecha_inicio: LECTURA_SIMULADA.fecha_hora_captura, fecha_fin: null, lecturas: [LECTURA_SIMULADA] }
], LECTURA_SIMULADA);

const MONITOREO_100 = monitoreo(100, [
  { id_sesion: 102, tipo_sesion: 'SIGNOS_MATERNOS', estado_sesion: 'COMPLETADA',
    fecha_inicio: '2025-09-08T17:13:00+00:00', fecha_fin: '2025-09-08T17:18:00+00:00',
    lecturas: [LECTURA_110, LECTURA_111] },
  { id_sesion: 231, tipo_sesion: 'MOVIMIENTOS_FETALES', estado_sesion: 'COMPLETADA',
    fecha_inicio: '2025-10-01T14:52:00+00:00', fecha_fin: '2025-10-01T16:04:00+00:00',
    lecturas: [LECTURA_679] }
], LECTURA_679);

function estadoEnvios(cambios) {
  return [200, Object.assign({
    inicializado: true, pendientes: 0, enviados: 0, fallidos_reintentables: 0,
    fallidos_en_revision: 0, total: 0, detalle: null
  }, cambios || {})];
}

function rutasBase(cambios) {
  return Object.assign({
    'GET /adaptador/sesion': [200, { autenticada: true, rol: 'PACIENTE' }],
    'GET /adaptador/conectividad': [200, { api_central: 'disponible' }],
    'GET /adaptador/embarazos': embarazos(),
    'GET /adaptador/embarazos/130/monitoreo': MONITOREO_130,
    'GET /adaptador/embarazos/100/monitoreo': MONITOREO_100,
    'GET /adaptador/movimientos/estado': estadoEnvios({ inicializado: false }),
    'POST /adaptador/cerrar-sesion': [200, { autenticada: false }]
  }, cambios || {});
}

function elegirEnHistorial($, id) {
  $('selector-embarazo').value = String(id);
  $('selector-embarazo').disparar('change');
}

// ---------------------------------------------------------------------------
// Inicio: una sola lectura, con sus nulos
// ---------------------------------------------------------------------------

test('Inicio muestra la última lectura del embarazo en curso, con los nulos como «—»', async () => {
  const { $ } = await arrancar(rutasBase());

  assert.equal($('embarazo-estado').textContent, 'En curso');
  assert.equal($('embarazo-anteriores').textContent, '1');
  assert.equal($('movs-value').textContent, '12');
  assert.equal($('hr-value').textContent, '—');
  assert.equal($('spo2-value').textContent, '—');
  assert.equal($('hr-status').textContent, 'No medido en esta lectura');
  assert.equal($('spo2-status').textContent, 'No medido en esta lectura');
  assert.equal($('mov-status').textContent, 'Medido en esta lectura');
  assert.notEqual($('last-update').textContent, '—', 'la fecha de la lectura se muestra');
  assert.equal($('embarazo-semana').textContent, '27');
  assert.equal($('btn-ver-anteriores').hidden, false);
});

test('Una última lectura de signos maternos llena FC y SpO2 con el valor tal cual llega', async () => {
  const signos = monitoreo(130, [
    { id_sesion: 9002, tipo_sesion: 'SIGNOS_MATERNOS', estado_sesion: 'COMPLETADA',
      fecha_inicio: LECTURA_111.fecha_hora_captura, fecha_fin: null, lecturas: [LECTURA_111] }
  ], LECTURA_111);
  const { $ } = await arrancar(rutasBase({ 'GET /adaptador/embarazos/130/monitoreo': signos }));

  // «95.00» del NUMERIC(5,2) se presenta como «95»; nunca se convierte en número.
  assert.equal($('hr-value').textContent, '95');
  assert.equal($('spo2-value').textContent, '99');
  assert.equal($('movs-value').textContent, '—');
  assert.equal($('mov-status').textContent, 'No medido en esta lectura');
});

// ---------------------------------------------------------------------------
// Historial: lecturas del dataset con FC y SpO2
// ---------------------------------------------------------------------------

test('Historial lista las lecturas canónicas con FC y SpO2 aunque la última solo tenga movimientos', async () => {
  const { $ } = await arrancar(rutasBase());

  elegirEnHistorial($, 100);
  await asentar();

  const filas = $('historial-lista').filas();
  assert.equal(filas.length, 3);
  // La más reciente primero: la 679, que solo midió movimientos.
  assert.deepEqual(filas[0].slice(1, 5), ['—', '—', '7', '39']);
  assert.match(filas[0][5], /Ámbar/);
  // Y debajo, las de signos maternos con sus unidades.
  assert.deepEqual(filas[1].slice(1, 5), ['95 BPM', '99 %', '—', '36']);
  assert.deepEqual(filas[2].slice(1, 5), ['86 BPM', '97 %', '—', '36']);
  assert.match(filas[2][5], /Verde/);
});

// ---------------------------------------------------------------------------
// Independencia entre Inicio e Historial
// ---------------------------------------------------------------------------

test('Cambiar el embarazo en Historial no repinta Inicio ni desvía el registro', async () => {
  const { $, adaptador } = await arrancar(rutasBase({
    'POST /adaptador/embarazos/130/sesiones-simuladas': [201, { registrado: true, estado: 'local' }]
  }));

  elegirEnHistorial($, 100);
  await asentar();

  // Inicio sigue con la lectura del embarazo 130.
  assert.equal($('movs-value').textContent, '12');
  assert.equal($('hr-value').textContent, '—');
  assert.equal($('embarazo-estado').textContent, 'En curso');

  // El registro va al embarazo de Inicio, no al elegido en Historial.
  $('btn-registrar-movimientos').click();
  await asentar();
  assert.equal(adaptador.contar('POST', '/adaptador/embarazos/130/sesiones-simuladas'), 1);
  assert.equal(adaptador.contar('POST', '/adaptador/embarazos/100/sesiones-simuladas'), 0);
  assert.equal($('nota-movimientos').textContent,
    'Registro guardado en este dispositivo, pendiente de envío.');
});

test('Un refresco conserva la selección de Historial', async () => {
  const { $ } = await arrancar(rutasBase());

  elegirEnHistorial($, 100);
  await asentar();
  // Volver a Inicio dispara un refresco completo.
  $('main-menu').querySelectorAll('a[data-vista]')[0].click();
  await asentar();

  assert.equal($('selector-embarazo').value, '100');
  assert.equal($('historial-lista').filas().length, 3);
  assert.equal($('movs-value').textContent, '12');
});

test('El enlace de Inicio abre Historial en el embarazo anterior', async () => {
  const { $ } = await arrancar(rutasBase());

  $('btn-ver-anteriores').click();
  await asentar();

  assert.equal($('vista-historial').hidden, false);
  assert.equal($('selector-embarazo').value, '100');
  assert.equal($('historial-lista').filas().length, 3);
  assert.equal($('movs-value').textContent, '12');
});

test('Con ambigüedad, Inicio no llama «actual» a ninguno ni habilita el registro', async () => {
  const { $, adaptador } = await arrancar(rutasBase({
    'GET /adaptador/embarazos': embarazos({ actual: null, anteriores: [], ambiguo: true })
  }));

  assert.equal($('embarazo-estado').textContent, 'Sin determinar');
  assert.equal($('hr-value').textContent, '—');
  assert.equal($('movs-value').textContent, '—');
  assert.equal($('btn-registrar-movimientos').disabled, true);
  // Solo Historial pidió monitoreo, y de un único episodio.
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos/130/monitoreo'), 1);
});

// ---------------------------------------------------------------------------
// Respuestas tardías
// ---------------------------------------------------------------------------

test('Una respuesta tardía de una selección anterior no pisa la vigente', async () => {
  const { $, adaptador } = await arrancar(rutasBase());

  adaptador.diferirSi((clave) => clave.endsWith('/monitoreo'));
  elegirEnHistorial($, 100);   // petición A, lenta
  elegirEnHistorial($, 130);   // petición B, la vigente
  const [a, b] = adaptador.diferidas;
  assert.match(a.clave, /100/);
  assert.match(b.clave, /130/);

  b.soltar();
  await asentar();
  a.soltar();                  // llega tarde
  await asentar();

  const filas = $('historial-lista').filas();
  assert.equal(filas.length, 1, 'solo la lectura del embarazo 130');
  assert.equal(filas[0][3], '12');
  assert.equal($('movs-value').textContent, '12');
});

test('Una respuesta que llega después de cerrar sesión no repinta nada', async () => {
  const { $, adaptador } = await arrancar(rutasBase());

  adaptador.diferirSi((clave) => clave.endsWith('/monitoreo'));
  elegirEnHistorial($, 100);
  $('btn-cerrar-sesion').click();
  $('btn-confirmar-cierre').click();
  await asentar();

  adaptador.diferidas.forEach((d) => d.soltar());
  await asentar();

  assert.equal($('vista-login').hidden, false);
  assert.equal($('historial-lista').filas().length, 0);
});

// ---------------------------------------------------------------------------
// Mensajes de envío
// ---------------------------------------------------------------------------

async function estadoMostrado(cambios) {
  const { $ } = await arrancar(rutasBase({
    'GET /adaptador/movimientos/estado': estadoEnvios(cambios)
  }));
  return {
    texto: $('envio-estado').textContent,
    accion: $('btn-sincronizar-movimientos').hidden ? null : $('btn-sincronizar-movimientos').textContent
  };
}

test('Envío: pendiente, fallido reintentable, en revisión, enviado y sin registros', async () => {
  let r = await estadoMostrado({ pendientes: 1 });
  assert.equal(r.texto, 'Registro guardado, pendiente de envío.');
  assert.equal(r.accion, 'Enviar ahora');

  r = await estadoMostrado({ fallidos_reintentables: 1 });
  assert.equal(r.texto, 'No se pudo enviar un registro; continúa guardado en este dispositivo.');
  assert.equal(r.accion, 'Reintentar');

  r = await estadoMostrado({ fallidos_en_revision: 2 });
  assert.match(r.texto, /^2 registros no se pudieron enviar/);
  assert.equal(r.accion, null, 'lo que está en revisión no se reintenta desde aquí');

  r = await estadoMostrado({ enviados: 3 });
  assert.equal(r.texto, 'Tus registros fueron enviados.');
  assert.equal(r.accion, null);

  r = await estadoMostrado({ inicializado: false });
  assert.equal(r.texto, 'Todavía no has registrado sesiones de movimientos.');
  assert.equal(r.accion, null);
});

test('Envío: el resultado de una ronda dice exactamente lo que confirmó la API', async () => {
  const ronda = { seleccionados: 1, entregados: 0, reintentables: 1, rechazados: 0,
    agotados: 0, ya_entregados: 0, detenida_por_transporte: true, detenida_por_credencial: false };
  const { $ } = await arrancar(rutasBase({
    'GET /adaptador/movimientos/estado': estadoEnvios({ pendientes: 1 }),
    'POST /adaptador/movimientos/sincronizar': () => [200, ronda]
  }));

  $('btn-sincronizar-movimientos').click();
  await asentar();
  assert.equal($('envio-resultado').textContent,
    'No se pudo enviar; el registro continúa guardado en este dispositivo.');
  assert.doesNotMatch($('envio-resultado').textContent, /enviado\./);

  Object.assign(ronda, { entregados: 1, reintentables: 0, detenida_por_transporte: false });
  $('btn-sincronizar-movimientos').click();
  await asentar();
  assert.equal($('envio-resultado').textContent, 'Registro enviado.');
});

// ---------------------------------------------------------------------------
// Cierre de sesión
// ---------------------------------------------------------------------------

test('Login: los controles privados están ocultos', async () => {
  const { $ } = await arrancar(rutasBase({
    'GET /adaptador/sesion': [200, { autenticada: false }]
  }));

  assert.equal($('vista-login').hidden, false);
  assert.equal($('main-menu').hidden, true);
  assert.equal($('area-cuenta').hidden, true);
  assert.equal($('vista-inicio').hidden, true);
});

test('Cerrar sesión: Cancelar conserva la sesión; confirmar la invalida y limpia', async () => {
  const { $, adaptador } = await arrancar(rutasBase());

  // El botón está en el área de cuenta, no en el menú de secciones.
  const enMenu = $('main-menu').hijos.map((a) => a.textContent);
  assert.ok(!enMenu.includes('Cerrar sesión'));

  $('btn-cerrar-sesion').click();
  assert.ok($('dialogo-cerrar-sesion').hasAttribute('open'));
  assert.equal(adaptador.contar('POST', '/adaptador/cerrar-sesion'), 0);

  $('btn-cancelar-cierre').click();
  await asentar();
  assert.ok(!$('dialogo-cerrar-sesion').hasAttribute('open'));
  assert.equal(adaptador.contar('POST', '/adaptador/cerrar-sesion'), 0);
  assert.equal($('vista-inicio').hidden, false);
  assert.equal($('movs-value').textContent, '12');

  $('btn-cerrar-sesion').click();
  $('btn-confirmar-cierre').click();
  await asentar();
  assert.equal(adaptador.contar('POST', '/adaptador/cerrar-sesion'), 1);
  assert.equal($('vista-login').hidden, false);
  assert.equal($('main-menu').hidden, true);
  assert.equal($('area-cuenta').hidden, true);
  assert.equal($('movs-value').textContent, '—');
  assert.equal($('embarazo-estado').textContent, 'No disponible');
  // Ninguna petición que borre registros locales.
  assert.ok(adaptador.llamadas.every((l) => l.metodo !== 'DELETE'));
});
