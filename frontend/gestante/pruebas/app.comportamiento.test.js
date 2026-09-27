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
   - Inicio solo consulta: no ofrece registrar ni enviar, y no hay un
     indicador de conexión permanente; solo se avisa cuando algo falta.
   - Cerrar sesión pide confirmación; Cancelar no cierra nada.

   Los valores de los episodios 100 y 130 y de las lecturas 110, 111 y 679 se
   copian literalmente de data/generated (cuenta 107, paciente 100) y de lo que
   `provisionar_demo.py` agrega (embarazo 130). Todos los datos son ficticios
   y simulados.
   ========================================================================= */

'use strict';

// La zona de la demostración (UTC−5). Fijarla hace deterministas las fechas y
// es la que destapó el desfase de un día en las fechas sin hora.
process.env.TZ = 'America/Panama';

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
    visibilityState: 'visible',
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

/**
 * Arranca app.js con un reloj y unos temporizadores controlados.
 *
 * `reloj.ms` es lo que devuelve `Date.now()` dentro de app.js; `tic()` ejecuta
 * una vez cada intervalo activo, como si hubieran pasado sus milisegundos. Así
 * se reproducen la inactividad y el vencimiento sin esperar de verdad.
 */
async function arrancar(rutas) {
  const documento = construirDocumento();
  const adaptador = crearAdaptador(rutas);
  const reloj = { ms: Date.UTC(2026, 8, 25, 15, 0, 0) };
  const intervalos = new Map();
  let siguiente = 0;
  const contexto = vm.createContext({
    document: documento,
    window: {
      setInterval: (fn) => { siguiente += 1; intervalos.set(siguiente, fn); return siguiente; },
      clearInterval: (id) => { intervalos.delete(id); }
    },
    fetch: adaptador.fetch,
    console,
    __reloj: () => reloj.ms
  });
  vm.runInContext('Date.now = function () { return __reloj(); };', contexto);
  vm.runInContext(JS, contexto, { filename: 'app.js' });
  documento.disparar('DOMContentLoaded');
  await asentar();
  const $ = (id) => documento.porId[id];
  const tic = async () => {
    Array.from(intervalos.values()).forEach((fn) => fn());
    await asentar();
  };
  return { $, adaptador, documento, reloj, intervalos, tic };
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
      hoy: '2026-09-25',
      // Semana de HOY del embarazo 130 (inicio 2026-03-19), no de una lectura.
      semana_actual: 28,
      actual: EMBARAZO_ACTUAL,
      anteriores: [EMBARAZO_ANTERIOR],
      todos: [EMBARAZO_ACTUAL, EMBARAZO_ANTERIOR],
      ambiguo: false,
      // El dispositivo de la demo está aprovisionado para el 130 de esta cuenta.
      registro_en_este_dispositivo: true
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

/**
 * Un registro de «Tus últimos registros» con la forma que entrega el
 * adaptador. Solo copia campos de la lectura indicada: **no elige** cuál es
 * la última; eso lo decide el adaptador y se prueba en Python.
 */
function registro(lectura, idSesion, campo, unidad) {
  return {
    valor: lectura[campo],
    unidad,
    fecha_hora_captura: lectura.fecha_hora_captura,
    semana_gestacion_lectura: lectura.semana_gestacion,
    id_lectura: lectura.id_lectura,
    id_sesion: idSesion
  };
}

function ultimos(hr, spo2, mov) {
  return { frecuencia_cardiaca: hr, saturacion_oxigeno: spo2, movimientos_fetales: mov };
}

function monitoreo(idEmbarazo, sesiones, ultima, ultimosRegistros) {
  return [200, {
    disponible: true,
    datos: {
      id_embarazo: idEmbarazo, sesiones, ultima_lectura: ultima, id_sesion_de_la_ultima: null,
      ultimos_registros: ultimosRegistros
    }
  }];
}

const MONITOREO_130 = monitoreo(130, [
  { id_sesion: 9001, tipo_sesion: 'MOVIMIENTOS_FETALES', estado_sesion: 'COMPLETADA',
    fecha_inicio: LECTURA_SIMULADA.fecha_hora_captura, fecha_fin: null, lecturas: [LECTURA_SIMULADA] }
], LECTURA_SIMULADA, ultimos(null, null, registro(LECTURA_SIMULADA, 9001, 'mov_valor', 'movimientos')));

const MONITOREO_100 = monitoreo(100, [
  { id_sesion: 102, tipo_sesion: 'SIGNOS_MATERNOS', estado_sesion: 'COMPLETADA',
    fecha_inicio: '2025-09-08T17:13:00+00:00', fecha_fin: '2025-09-08T17:18:00+00:00',
    lecturas: [LECTURA_110, LECTURA_111] },
  { id_sesion: 231, tipo_sesion: 'MOVIMIENTOS_FETALES', estado_sesion: 'COMPLETADA',
    fecha_inicio: '2025-10-01T14:52:00+00:00', fecha_fin: '2025-10-01T16:04:00+00:00',
    lecturas: [LECTURA_679] }
], LECTURA_679, ultimos(
  registro(LECTURA_111, 102, 'hr_valor', 'BPM'),
  registro(LECTURA_111, 102, 'spo2_valor', '%'),
  registro(LECTURA_679, 231, 'mov_valor', 'movimientos')
));

function estadoConexion(api, autenticacion) {
  return [200, {
    api_central: api, autenticacion_central: autenticacion,
    sesion_local_expira_en: '2026-09-28T15:00:00+00:00'
  }];
}
const CONECTADA = estadoConexion('disponible', 'vigente');

function rutasBase(cambios) {
  return Object.assign({
    'GET /adaptador/sesion': [200, { autenticada: true, rol: 'PACIENTE' }],
    'GET /adaptador/estado-conexion': CONECTADA,
    'GET /adaptador/embarazos': embarazos(),
    'GET /adaptador/embarazos/130/monitoreo': MONITOREO_130,
    'GET /adaptador/embarazos/100/monitoreo': MONITOREO_100,
    'POST /adaptador/cerrar-sesion': [200, { autenticada: false }]
  }, cambios || {});
}

function elegirEnHistorial($, id) {
  $('selector-embarazo').value = String(id);
  $('selector-embarazo').disparar('change');
}

// ---------------------------------------------------------------------------
// Inicio: «Tus últimos registros», una tarjeta por variable
// ---------------------------------------------------------------------------

test('Inicio: cada tarjeta es su propio último registro; lo nunca registrado dice «Sin registros»', async () => {
  const { $ } = await arrancar(rutasBase());

  assert.equal($('embarazo-estado').textContent, 'En curso');
  assert.equal($('embarazo-anteriores').textContent, '1');
  assert.equal($('mov-value').textContent, '12');
  assert.equal($('mov-status').textContent, 'Último registro');
  assert.equal($('mov-fecha').textContent, 'Registrado el 24 sept 2026, 01:40');
  assert.equal($('mov-semana').textContent, 'Semana 27 en esa lectura');
  assert.equal($('tarjeta-mov').getAttribute('data-id-lectura'), '5001');
  // El 130 nunca registró FC ni SpO2: no se rellena con otra lectura ni embarazo.
  assert.equal($('hr-value').textContent, '—');
  assert.equal($('hr-status').textContent, 'Sin registros');
  assert.equal($('hr-fecha').textContent, '');
  assert.equal($('spo2-status').textContent, 'Sin registros');
  assert.equal($('tarjeta-hr').getAttribute('data-id-lectura'), null);
  assert.equal($('btn-ver-anteriores').hidden, false);
});

test('Semana actual y semana de la lectura son cosas distintas', async () => {
  const { $ } = await arrancar(rutasBase());

  // semana_actual del adaptador (hoy), no la 27 de la lectura del 24/9.
  assert.equal($('embarazo-semana-etiqueta').textContent, 'Semana actual');
  assert.equal($('embarazo-semana').textContent, '28');
  assert.equal($('mov-semana').textContent, 'Semana 27 en esa lectura');
  // Sin fecha probable de parto en el dato: no se inventa ni se marca histórico.
  assert.equal($('embarazo-fpp').textContent, 'No disponible');
  assert.equal($('embarazo-historico'), undefined);
});

// Cuenta paciente30@example.com, embarazo 129 (data/generated): su última FC
// y SpO2 es la lectura 549 y su último movimiento la 1259, semanas después.
const EMBARAZO_129 = {
  id_embarazo: 129, fecha_inicio: '2025-09-24', fecha_probable_parto: '2026-07-01',
  estado_embarazo: 'ACTIVO', fecha_cierre: null
};
const LECTURA_549 = {
  id_lectura: 549, fecha_hora_captura: '2026-05-29T08:27:00+00:00', codigo_semaforo: 'OK',
  semana_gestacion: 36, hr_valor: '83.00', spo2_valor: '96.00', mov_valor: null
};
const LECTURA_1259 = {
  id_lectura: 1259, fecha_hora_captura: '2026-06-21T14:56:00+00:00', codigo_semaforo: 'WARNING',
  semana_gestacion: 39, hr_valor: null, spo2_valor: null, mov_valor: 7
};

function rutasPaciente30(cambios) {
  return rutasBase(Object.assign({
    'GET /adaptador/embarazos': [200, { disponible: true, datos: {
      // Su fecha probable de parto (1/7/2026) ya pasó: el adaptador no
      // publica una semana actual.
      hoy: '2026-09-25', semana_actual: null, actual: EMBARAZO_129,
      anteriores: [], todos: [EMBARAZO_129], ambiguo: false,
      // Este dispositivo no está aprovisionado para el 129.
      registro_en_este_dispositivo: false
    } }],
    'GET /adaptador/embarazos/129/monitoreo': monitoreo(129, [
      { id_sesion: 1, tipo_sesion: 'SIGNOS_MATERNOS', estado_sesion: 'COMPLETADA',
        fecha_inicio: LECTURA_549.fecha_hora_captura, fecha_fin: null, lecturas: [LECTURA_549] },
      { id_sesion: 811, tipo_sesion: 'MOVIMIENTOS_FETALES', estado_sesion: 'COMPLETADA',
        fecha_inicio: LECTURA_1259.fecha_hora_captura, fecha_fin: null, lecturas: [LECTURA_1259] }
    ], LECTURA_1259, ultimos(
      registro(LECTURA_549, 1, 'hr_valor', 'BPM'),
      registro(LECTURA_549, 1, 'spo2_valor', '%'),
      registro(LECTURA_1259, 811, 'mov_valor', 'movimientos')
    ))
  }, cambios || {}));
}

test('paciente30: FC/SpO2 y movimientos de lecturas distintas, cada uno con su fecha, en tarjetas neutrales', async () => {
  const { $ } = await arrancar(rutasPaciente30());

  assert.equal($('hr-value').textContent, '83');
  assert.equal($('spo2-value').textContent, '96');
  assert.equal($('mov-value').textContent, '7');
  // Fechas en hora de Panamá (UTC−5): 08:27Z → 03:27; 14:56Z → 09:56.
  assert.equal($('hr-fecha').textContent, 'Registrado el 29 may 2026, 03:27');
  assert.equal($('spo2-fecha').textContent, 'Registrado el 29 may 2026, 03:27');
  assert.equal($('mov-fecha').textContent, 'Registrado el 21 jun 2026, 09:56');
  assert.equal($('tarjeta-hr').getAttribute('data-id-lectura'), '549');
  assert.equal($('tarjeta-mov').getAttribute('data-id-lectura'), '1259');
  // Tarjetas neutrales: la API solo clasifica lecturas completas, así que
  // ninguna tarjeta lleva el semáforo de su lectura de origen.
  ['hr', 'spo2', 'mov'].forEach((p) => {
    assert.equal($(p + '-clasificacion'), undefined, 'sin elemento de clasificación');
    ['-value', '-status', '-fecha', '-semana'].forEach((s) =>
      assert.doesNotMatch($(p + s).textContent, /Verde|Amarillo|Ámbar|Rojo|Clasificación/));
  });
  // El semáforo grande es el de UNA lectura, la más reciente, y dice qué midió.
  assert.ok($('semaforo').classList.contains('warning'));
  assert.equal($('last-update').textContent, '21 jun 2026, 09:56');
  assert.equal($('ultima-lectura-mide').textContent, 'Midió movimientos fetales.');
  // Sin semana actual vigente: la del último registro, rotulada y con fecha.
  assert.equal($('embarazo-semana-etiqueta').textContent, 'Semana en el último registro');
  assert.equal($('embarazo-semana').textContent, '39 (21 jun 2026)');
  assert.equal($('hr-semana').textContent, 'Semana 36 en esa lectura');
  // El desfase del escenario de demostración no se convierte en un aviso.
  assert.equal($('nota-embarazo').hidden, true);
  // La fecha probable de parto registrada (1/7/2026) ya pasó: se muestra
  // sin aviso de registro histórico y sin cambiar su estado.
  assert.equal($('embarazo-fpp').textContent, '1 jul 2026');
  assert.equal($('embarazo-historico'), undefined);
  assert.doesNotMatch(HTML + JS, /Registro histórico: la fecha probable de parto ya pasó\./);
  assert.equal(EMBARAZO_129.estado_embarazo, 'ACTIVO');
  assert.equal(EMBARAZO_129.fecha_inicio, '2025-09-24');
  assert.equal(EMBARAZO_129.fecha_probable_parto, '2026-07-01');
  assert.equal($('embarazo-inicio').textContent, '24 sept 2025');
  assert.equal($('embarazo-estado').textContent, 'En curso');
});

test('El alcance del semáforo está escrito: es de la lectura completa', () => {
  assert.match(HTML, /La clasificación corresponde a esta lectura completa, no a cada\s+medición por separado\./);
  assert.doesNotMatch(HTML, /-clasificacion"/);
});

test('El cero es un valor registrado, no una ausencia', async () => {
  const cero = Object.assign({}, LECTURA_1259, { id_lectura: 1300, mov_valor: 0 });
  const { $ } = await arrancar(rutasPaciente30({
    'GET /adaptador/embarazos/129/monitoreo': monitoreo(129, [], cero,
      ultimos(null, null, registro(cero, 811, 'mov_valor', 'movimientos')))
  }));

  assert.equal($('mov-value').textContent, '0');
  assert.equal($('mov-status').textContent, 'Último registro');
  assert.equal($('hr-status').textContent, 'Sin registros');
});

test('Sin ninguna lectura: las tres tarjetas dicen «Sin registros» y no hay semáforo', async () => {
  const { $ } = await arrancar(rutasPaciente30({
    'GET /adaptador/embarazos/129/monitoreo': monitoreo(129, [], null, ultimos(null, null, null))
  }));

  ['hr', 'spo2', 'mov'].forEach((p) => {
    assert.equal($(p + '-value').textContent, '—');
    assert.equal($(p + '-status').textContent, 'Sin registros');
  });
  assert.ok($('semaforo').classList.contains('waiting'), 'la ausencia de datos no es verde');
  assert.equal($('last-update').textContent, '—');
});

test('Respuesta parcial: una variable que no llegó es «No disponible», no «Sin registros»', async () => {
  const parcial = { frecuencia_cardiaca: registro(LECTURA_549, 1, 'hr_valor', 'BPM') };
  const { $ } = await arrancar(rutasPaciente30({
    'GET /adaptador/embarazos/129/monitoreo': monitoreo(129, [], LECTURA_549, parcial)
  }));

  assert.equal($('hr-value').textContent, '83');
  assert.equal($('spo2-status').textContent, 'No disponible');
  assert.equal($('mov-status').textContent, 'No disponible');
});

test('Una fecha sin hora es un día de calendario: no se corre al día anterior en UTC−5', async () => {
  const { $ } = await arrancar(rutasBase());

  // fecha_inicio '2026-03-19' leída como medianoche UTC mostraría el 18 en Panamá.
  assert.equal($('embarazo-inicio').textContent, '19 mar 2026');
  const opcion = $('selector-embarazo').hijos.find((o) => o.value === '130');
  assert.equal(opcion.textContent, 'Desde 19 mar 2026 — En curso');
});

test('Las opciones del selector de Historial no repiten «Embarazo»: a 360 px se cortaban', async () => {
  const { $ } = await arrancar(rutasBase());

  const opcion = $('selector-embarazo').hijos.find((o) => o.value === '100');
  assert.equal(opcion.textContent, 'Desde 6 ene 2025 — Finalizado');
});

test('FC y SpO2 se muestran tal cual llegan, sin convertirlos en número', async () => {
  const signos = monitoreo(130, [
    { id_sesion: 9002, tipo_sesion: 'SIGNOS_MATERNOS', estado_sesion: 'COMPLETADA',
      fecha_inicio: LECTURA_111.fecha_hora_captura, fecha_fin: null, lecturas: [LECTURA_111] }
  ], LECTURA_111, ultimos(
    registro(LECTURA_111, 9002, 'hr_valor', 'BPM'),
    registro(LECTURA_111, 9002, 'spo2_valor', '%'),
    null
  ));
  const { $ } = await arrancar(rutasBase({ 'GET /adaptador/embarazos/130/monitoreo': signos }));

  // «95.00» del NUMERIC(5,2) se presenta como «95»; nunca se convierte en número.
  assert.equal($('hr-value').textContent, '95');
  assert.equal($('spo2-value').textContent, '99');
  assert.equal($('mov-value').textContent, '—');
  assert.equal($('mov-status').textContent, 'Sin registros');
  assert.equal($('ultima-lectura-mide').textContent,
    'Midió frecuencia cardíaca y saturación de oxígeno.');
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
  assert.match(filas[0][5], /Amarillo/);
  // Y debajo, las de signos maternos con sus unidades.
  assert.deepEqual(filas[1].slice(1, 5), ['95 BPM', '99 %', '—', '36']);
  assert.deepEqual(filas[2].slice(1, 5), ['86 BPM', '97 %', '—', '36']);
  assert.match(filas[2][5], /Verde/);
});

// ---------------------------------------------------------------------------
// Independencia entre Inicio e Historial
// ---------------------------------------------------------------------------

test('Cambiar el embarazo en Historial no repinta Inicio', async () => {
  const { $ } = await arrancar(rutasBase());

  elegirEnHistorial($, 100);
  await asentar();

  // Inicio sigue con la lectura del embarazo 130.
  assert.equal($('mov-value').textContent, '12');
  assert.equal($('hr-value').textContent, '—');
  assert.equal($('embarazo-estado').textContent, 'En curso');
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
  assert.equal($('mov-value').textContent, '12');
});

test('El enlace de Inicio abre Historial en el embarazo anterior', async () => {
  const { $ } = await arrancar(rutasBase());

  $('btn-ver-anteriores').click();
  await asentar();

  assert.equal($('vista-historial').hidden, false);
  assert.equal($('selector-embarazo').value, '100');
  assert.equal($('historial-lista').filas().length, 3);
  assert.equal($('mov-value').textContent, '12');
});

test('Con ambigüedad, Inicio no llama «actual» a ninguno ni muestra lecturas', async () => {
  const { $, adaptador } = await arrancar(rutasBase({
    'GET /adaptador/embarazos': embarazos({ actual: null, anteriores: [], ambiguo: true })
  }));

  assert.equal($('embarazo-estado').textContent, 'Sin determinar');
  assert.equal($('hr-value').textContent, '—');
  assert.equal($('mov-value').textContent, '—');
  // Solo Historial pidió monitoreo, y de un único episodio.
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos/130/monitoreo'), 1);
});

// ---------------------------------------------------------------------------
// Sin conexión
// ---------------------------------------------------------------------------

test('Sin conexión se conserva el contexto conocido y se dice', async () => {
  const { $, adaptador } = await arrancar(rutasBase());

  // La API central cae: el adaptador responde 200 con disponible: false.
  adaptador.rutas['GET /adaptador/embarazos'] = [200, { disponible: false, motivo: 'sin_conexion' }];
  $('main-menu').querySelectorAll('a[data-vista]')[0].click();
  await asentar();

  assert.equal($('nota-embarazo').textContent,
    'Sin conexión con el servidor. Se muestra la última información consultada.');
  assert.equal($('embarazo-estado').textContent, 'En curso');
  assert.equal($('mov-value').textContent, '12', 'la última lectura, con su fecha, sigue a la vista');
});

test('Sin conexión y sin contexto previo no se inventa un embarazo en curso', async () => {
  const { $ } = await arrancar(rutasBase({
    'GET /adaptador/embarazos': [200, { disponible: false, motivo: 'sin_conexion' }]
  }));

  assert.equal($('embarazo-estado').textContent, 'No disponible');
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
  assert.equal($('mov-value').textContent, '12');
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
// Inicio solo consulta
// ---------------------------------------------------------------------------

test('Inicio no ofrece registrar ni enviar movimientos, ni muestra un indicador de conexión', async () => {
  const { $, adaptador, tic } = await arrancar(rutasBase());
  await tic();

  for (const id of ['btn-registrar-movimientos', 'nota-movimientos', 'btn-sincronizar-movimientos',
    'envio-estado', 'envio-resultado', 'envio-ultimo', 'connection-status']) {
    assert.equal($(id), undefined, id);
  }
  assert.doesNotMatch(HTML, /Sesión de movimientos|Enviar ahora|Registrar sesión/);
  // Ni una petición que escriba, ni las consultas de la cola o de la conectividad.
  assert.ok(adaptador.llamadas.every((l) => l.metodo === 'GET'),
    JSON.stringify(adaptador.llamadas.filter((l) => l.metodo !== 'GET')));
  assert.equal(adaptador.contar('GET', '/adaptador/movimientos/estado'), 0);
  assert.equal(adaptador.contar('GET', '/adaptador/conectividad'), 0);
});

test('El pie ofrece el contacto técnico y los derechos reservados; ya no firma la tesis', () => {
  const pie = HTML.split('<footer')[1];
  assert.match(pie, /Comunícate con el administrador del sistema/);
  assert.match(pie, /href="tel:\+50769210003">\+507 6921-0003</);
  assert.match(pie, /Todos los derechos reservados\./);
  assert.doesNotMatch(HTML, /Trabajo de tesis de/);
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
  assert.equal($('mov-value').textContent, '12');

  $('btn-cerrar-sesion').click();
  $('btn-confirmar-cierre').click();
  await asentar();
  assert.equal(adaptador.contar('POST', '/adaptador/cerrar-sesion'), 1);
  assert.equal($('vista-login').hidden, false);
  assert.equal($('main-menu').hidden, true);
  assert.equal($('area-cuenta').hidden, true);
  assert.equal($('mov-value').textContent, '—');
  assert.equal($('embarazo-estado').textContent, 'No disponible');
  // Ninguna petición que borre registros locales.
  assert.ok(adaptador.llamadas.every((l) => l.metodo !== 'DELETE'));
});

// ---------------------------------------------------------------------------
// Conexión y autenticación: estados comprobados, no supuestos
// ---------------------------------------------------------------------------

test('Con el token vencido el servidor sigue disponible: no se pinta «Sin conexión» y se ofrece reautenticar', async () => {
  const { $ } = await arrancar(rutasBase({
    'GET /adaptador/estado-conexion': estadoConexion('disponible', 'reautenticacion_requerida')
  }));

  assert.doesNotMatch($('nota-embarazo').textContent, /Sin conexión/);
  assert.equal($('aviso-sesion-central').hidden, false);
  assert.equal($('btn-mostrar-reautenticar').hidden, false);
  assert.equal($('vista-inicio').hidden, false, 'la sesión local sigue abierta');
});

test('Si la API cae con la página abierta, se avisa y se conserva lo mostrado; no pide credenciales', async () => {
  const { $, adaptador, tic } = await arrancar(rutasBase());
  assert.equal($('nota-embarazo').hidden, true, 'con conexión no hay aviso');

  adaptador.rutas['GET /adaptador/estado-conexion'] = estadoConexion('no_disponible', 'no_comprobada');
  await tic();

  assert.equal($('nota-embarazo').hidden, false);
  assert.equal($('nota-embarazo').textContent,
    'Sin conexión con el servidor. Se muestra la última información consultada.');
  assert.equal($('mov-value').textContent, '12');
  assert.equal($('aviso-sesion-central').hidden, true);
});

test('Un 403 es «acceso no autorizado»: no se confunde con un token vencido ni se ofrece reautenticar', async () => {
  const { $ } = await arrancar(rutasBase({
    'GET /adaptador/estado-conexion': estadoConexion('disponible', 'acceso_denegado')
  }));

  assert.equal($('aviso-sesion-central').hidden, false);
  assert.equal($('btn-mostrar-reautenticar').hidden, true);
});

test('Reautenticar recupera la sesión central y recarga lo clínico', async () => {
  let vigente = false;
  const { $, adaptador } = await arrancar(rutasBase({
    'GET /adaptador/estado-conexion': () =>
      estadoConexion('disponible', vigente ? 'vigente' : 'reautenticacion_requerida'),
    'POST /adaptador/reautenticar': () => { vigente = true; return CONECTADA; }
  }));
  const clinicasAntes = adaptador.contar('GET', '/adaptador/embarazos');

  $('btn-mostrar-reautenticar').click();
  assert.equal($('form-reautenticar').hidden, false);
  $('reauth-email').value = 'paciente01@example.com';
  $('reauth-password').value = 'clave-de-prueba';
  $('form-reautenticar').disparar('submit');
  await asentar();

  assert.equal(adaptador.contar('POST', '/adaptador/reautenticar'), 1);
  assert.equal($('reauth-password').value, '', 'la contraseña no se conserva');
  assert.equal($('aviso-sesion-central').hidden, true);
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos'), clinicasAntes + 1, 'se recarga una vez');
  assert.equal(adaptador.contar('POST', '/adaptador/cerrar-sesion'), 0);
  assert.ok(adaptador.llamadas.every((l) => l.metodo !== 'DELETE'));
});

test('Reautenticar con credenciales que no sirven avisa y no cierra la sesión local', async () => {
  const { $, adaptador } = await arrancar(rutasBase({
    'GET /adaptador/estado-conexion': estadoConexion('disponible', 'reautenticacion_requerida'),
    'POST /adaptador/reautenticar': [400, { detail: 'Correo o contraseña incorrectos.' }]
  }));

  $('btn-mostrar-reautenticar').click();
  $('reauth-email').value = 'paciente01@example.com';
  $('reauth-password').value = 'otra';
  $('form-reautenticar').disparar('submit');
  await asentar();

  assert.equal($('reauth-mensaje').textContent, 'Correo o contraseña incorrectos.');
  assert.equal($('vista-inicio').hidden, false);
  assert.equal($('aviso-sesion-central').hidden, false);
  assert.equal(adaptador.contar('POST', '/adaptador/cerrar-sesion'), 0);
});

test('El refresco periódico comprueba la conexión, pero no repite lecturas clínicas', async () => {
  const { adaptador, tic } = await arrancar(rutasBase());
  const clinicas = adaptador.contar('GET', '/adaptador/embarazos');
  const monitoreos = adaptador.contar('GET', '/adaptador/embarazos/130/monitoreo');
  const estados = adaptador.contar('GET', '/adaptador/estado-conexion');

  await tic();
  await tic();
  await tic();

  assert.equal(adaptador.contar('GET', '/adaptador/estado-conexion'), estados + 3);
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos'), clinicas);
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos/130/monitoreo'), monitoreos);
});

test('Cuando la API vuelve, lo clínico se recarga una sola vez', async () => {
  let api = 'no_disponible';
  const { $, adaptador, tic } = await arrancar(rutasBase({
    'GET /adaptador/estado-conexion': () =>
      estadoConexion(api, api === 'disponible' ? 'vigente' : 'no_comprobada')
  }));
  const clinicas = adaptador.contar('GET', '/adaptador/embarazos');

  await tic();
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos'), clinicas, 'sigue caída: nada');

  assert.match($('nota-embarazo').textContent, /^Sin conexión con el servidor/);

  api = 'disponible';
  await tic();
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos'), clinicas + 1);
  assert.equal($('nota-embarazo').hidden, true, 'la recarga reemplaza el aviso');

  await tic();
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos'), clinicas + 1, 'sin bucle de recargas');
});

test('Comprobaciones simultáneas comparten una sola petición', async () => {
  const { $, adaptador, documento, tic } = await arrancar(rutasBase());
  const antes = adaptador.contar('GET', '/adaptador/estado-conexion');

  adaptador.diferirSi((clave) => clave === 'GET /adaptador/estado-conexion');
  tic();
  documento.disparar('visibilitychange');
  tic();
  await asentar();
  assert.equal(adaptador.contar('GET', '/adaptador/estado-conexion'), antes + 1);
  adaptador.diferidas.forEach((d) => d.soltar());
  await asentar();
  assert.equal($('aviso-sesion-central').hidden, true);
});

test('Volver a la pestaña con datos de más de un minuto los vuelve a pedir; con datos recientes, no', async () => {
  const { adaptador, documento, reloj } = await arrancar(rutasBase());
  const clinicas = adaptador.contar('GET', '/adaptador/embarazos');

  reloj.ms += 30 * 1000;
  documento.disparar('visibilitychange');
  await asentar();
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos'), clinicas);

  reloj.ms += 15 * 60 * 1000;   // la pestaña estuvo oculta un buen rato
  documento.disparar('visibilitychange');
  await asentar();
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos'), clinicas + 1);
});

test('Tras cerrar sesión no queda ningún temporizador ni se repinta con una comprobación tardía', async () => {
  const { $, adaptador, intervalos } = await arrancar(rutasBase());
  assert.equal(intervalos.size, 1);

  adaptador.diferirSi((clave) => clave === 'GET /adaptador/estado-conexion');
  $('main-menu').querySelectorAll('a[data-vista]')[0].click();
  $('btn-cerrar-sesion').click();
  $('btn-confirmar-cierre').click();
  await asentar();
  assert.equal(intervalos.size, 0);

  adaptador.rutas['GET /adaptador/estado-conexion'] =
    estadoConexion('disponible', 'reautenticacion_requerida');
  adaptador.diferidas.forEach((d) => d.soltar());
  await asentar();
  assert.equal($('aviso-sesion-central').hidden, true);
});

test('Iniciar sesión otra vez no duplica el temporizador', async () => {
  const { $, intervalos } = await arrancar(rutasBase({
    'POST /adaptador/iniciar-sesion': [200, { autenticada: true }]
  }));
  $('btn-cerrar-sesion').click();
  $('btn-confirmar-cierre').click();
  await asentar();

  $('login-email').value = 'paciente01@example.com';
  $('login-password').value = 'clave-de-prueba';
  $('form-login').disparar('submit');
  await asentar();
  assert.equal($('vista-inicio').hidden, false);
  assert.equal(intervalos.size, 1);
});

test('Inicio ya no muestra la hora de consulta ni el botón «Actualizar información»', async () => {
  const { $ } = await arrancar(rutasBase());

  assert.equal($('datos-actualizados'), undefined);
  assert.equal($('btn-actualizar-datos'), undefined);
  assert.doesNotMatch(HTML + JS, /Información consultada al servidor|Actualizar información/);
});

test('Si la petición local se corta (equipo suspendido), no se afirma «Sin conexión»; al reanudar se comprueba y recarga una vez', async () => {
  const { $, adaptador, documento } = await arrancar(rutasBase());
  const clinicas = adaptador.contar('GET', '/adaptador/embarazos');
  const vigente = adaptador.rutas['GET /adaptador/estado-conexion'];

  // Sin ruta, el fetch falso rechaza: es lo que ve la página cuando el
  // navegador corta la petición (ERR_NETWORK_IO_SUSPENDED).
  delete adaptador.rutas['GET /adaptador/estado-conexion'];
  documento.disparar('visibilitychange');
  await asentar();
  assert.doesNotMatch($('nota-embarazo').textContent, /Sin conexión/);
  assert.equal($('vista-inicio').hidden, false);

  adaptador.rutas['GET /adaptador/estado-conexion'] = vigente;
  documento.disparar('resume');
  await asentar();
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos'), clinicas + 1);
});

test('Volver a Inicio no borra la semana del último registro mientras llega el monitoreo', async () => {
  const { $, adaptador } = await arrancar(rutasPaciente30());
  assert.equal($('embarazo-semana').textContent, '39 (21 jun 2026)');

  adaptador.diferirSi((clave) => clave.endsWith('/monitoreo'));
  $('main-menu').querySelectorAll('a[data-vista]')[0].click();
  await asentar();
  assert.equal($('embarazo-semana').textContent, '39 (21 jun 2026)', 'sin «No disponible» intermedio');

  adaptador.diferidas.forEach((d) => d.soltar());
  await asentar();
  assert.equal($('embarazo-semana').textContent, '39 (21 jun 2026)');
});

// ---------------------------------------------------------------------------
// Entrega confirmada por el envío automático: una sola recarga clínica
// ---------------------------------------------------------------------------
//
// `ultimo_envio_confirmado` llega con cada comprobación de estado (lectura
// local del portal). La primera es la línea base; solo un cambio posterior
// recarga lo clínico, y una sola vez. Se cuenta con las peticiones a
// /adaptador/embarazos, que es lo que hace cargarClinico().

function estadoConEnvio(valor) {
  return [200, Object.assign({}, CONECTADA[1], { ultimo_envio_confirmado: valor })];
}

const RUTA_ESTADO = 'GET /adaptador/estado-conexion';
const clinicas = (adaptador) => adaptador.contar('GET', '/adaptador/embarazos');

test('Envío confirmado: el valor inicial es la línea base y no recarga nada', async () => {
  const { adaptador, tic } = await arrancar(rutasBase({ [RUTA_ESTADO]: estadoConEnvio('2026-09-25T08:00:00+00:00') }));
  assert.equal(clinicas(adaptador), 1, 'solo la carga de la entrada');

  await tic();
  await tic();
  assert.equal(clinicas(adaptador), 1, 'mismo valor: nada');
});

test('Envío confirmado: un valor nuevo recarga lo clínico exactamente una vez', async () => {
  let valor = '2026-09-25T08:00:00+00:00';
  const { $, adaptador, tic } = await arrancar(rutasBase({ [RUTA_ESTADO]: () => estadoConEnvio(valor) }));
  await tic();
  assert.equal(clinicas(adaptador), 1);

  valor = '2026-09-25T08:15:00+00:00';
  await tic();
  assert.equal(clinicas(adaptador), 2, 'una recarga por la entrega nueva');
  assert.equal($('mov-value').textContent, '12');

  await tic();
  await tic();
  assert.equal(clinicas(adaptador), 2, 'el mismo valor nuevo no vuelve a recargar');
});

test('Envío confirmado: de ninguno a uno también es una entrega nueva', async () => {
  let valor = null;
  const { adaptador, tic } = await arrancar(rutasBase({ [RUTA_ESTADO]: () => estadoConEnvio(valor) }));
  await tic();
  valor = '2026-09-25T08:15:00+00:00';
  await tic();
  assert.equal(clinicas(adaptador), 2);
});

test('Envío confirmado: al cerrar sesión se olvida la línea base; otra cuenta empieza la suya', async () => {
  const { $, adaptador, tic } = await arrancar(rutasBase({
    [RUTA_ESTADO]: estadoConEnvio('2026-09-25T08:15:00+00:00'),
    'POST /adaptador/iniciar-sesion': [200, { autenticada: true }]
  }));
  $('btn-cerrar-sesion').click();
  $('btn-confirmar-cierre').click();
  await asentar();

  // Entra otra paciente, con su propio último envío.
  Object.assign(adaptador.rutas, rutasPaciente30({
    [RUTA_ESTADO]: estadoConEnvio('2026-09-24T10:00:00+00:00'),
    'POST /adaptador/iniciar-sesion': [200, { autenticada: true }]
  }));
  const antes = clinicas(adaptador);
  $('login-email').value = 'paciente30@example.com';
  $('login-password').value = 'clave-de-prueba';
  $('form-login').disparar('submit');
  await asentar();
  assert.equal(clinicas(adaptador), antes + 1, 'solo la carga de la entrada de B');

  // Si hubiera heredado la línea base de A, este valor distinto recargaría.
  await tic();
  await tic();
  assert.equal(clinicas(adaptador), antes + 1);
  assert.equal($('mov-value').textContent, '7');
});

test('Envío confirmado: una respuesta tardía de la cuenta A no toca a B', async () => {
  const { $, adaptador, tic } = await arrancar(rutasBase({
    [RUTA_ESTADO]: estadoConEnvio('2026-09-25T08:00:00+00:00'),
    'POST /adaptador/iniciar-sesion': [200, { autenticada: true }]
  }));

  // A: una entrega nueva cuya respuesta queda retenida.
  adaptador.rutas[RUTA_ESTADO] = estadoConEnvio('2026-09-25T09:30:00+00:00');
  adaptador.diferirSi((clave) => clave === RUTA_ESTADO);
  await tic();
  assert.equal(adaptador.diferidas.length, 1);
  adaptador.diferirSi(() => false);

  // A cierra sesión y entra B.
  $('btn-cerrar-sesion').click();
  $('btn-confirmar-cierre').click();
  await asentar();
  Object.assign(adaptador.rutas, rutasPaciente30({
    [RUTA_ESTADO]: estadoConEnvio('2026-09-24T10:00:00+00:00'),
    'POST /adaptador/iniciar-sesion': [200, { autenticada: true }]
  }));
  $('login-email').value = 'paciente30@example.com';
  $('login-password').value = 'clave-de-prueba';
  $('form-login').disparar('submit');
  await asentar();
  const deB = clinicas(adaptador);
  const monitoreos130 = adaptador.contar('GET', '/adaptador/embarazos/130/monitoreo');

  // Llega tarde la respuesta de A.
  adaptador.diferidas[0].soltar();
  await asentar();
  assert.equal(clinicas(adaptador), deB, 'ninguna recarga por un evento de A');
  assert.equal(adaptador.contar('GET', '/adaptador/embarazos/130/monitoreo'), monitoreos130);
  assert.equal($('mov-value').textContent, '7', 'B sigue a la vista');

  // Y la línea base de B no cambió: su propio valor no recarga.
  await tic();
  assert.equal(clinicas(adaptador), deB);
});

test('Envío confirmado: si la comprobación falla no se borra nada ni se inventa una entrega', async () => {
  let valor = '2026-09-25T08:00:00+00:00';
  const { $, adaptador, tic } = await arrancar(rutasBase({ [RUTA_ESTADO]: () => estadoConEnvio(valor) }));
  const vigente = adaptador.rutas[RUTA_ESTADO];

  // La aplicación local no contesta.
  delete adaptador.rutas[RUTA_ESTADO];
  await tic();
  await tic();
  assert.equal(clinicas(adaptador), 1, 'sin recarga falsa');
  assert.equal($('mov-value').textContent, '12', 'lo mostrado se conserva');
  assert.equal($('embarazo-estado').textContent, 'En curso');

  // Vuelve con el mismo valor. Hay UNA recarga, pero es la de siempre al
  // recuperar la comunicación (ver la prueba del equipo suspendido), no una
  // entrega: el valor es el mismo y la línea base se conservó.
  adaptador.rutas[RUTA_ESTADO] = vigente;
  await tic();
  assert.equal(clinicas(adaptador), 2, 'solo la recarga por la recuperación');
  await tic();
  assert.equal(clinicas(adaptador), 2, 'mismo valor tras la recuperación: nada');
  // Y una entrega nueva se sigue detectando contra esa línea base.
  valor = '2026-09-25T08:15:00+00:00';
  await tic();
  assert.equal(clinicas(adaptador), 3);
});
