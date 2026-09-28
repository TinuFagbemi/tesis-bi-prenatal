/* =========================================================================
   Pruebas de graficas.js (SCRUM-72, etapa 2): la parte pura.

   Lo que se comprueba es lo que haría engañosa una gráfica si fallara:
   - el eje temporal es proporcional (no un punto por posición);
   - no aparece ningún punto que no sea un registro;
   - un cero es un valor y un vacío o un null no se convierten en cero;
   - el período se cuenta desde el último registro, no desde hoy;
   - una serie vacía o de un solo registro tiene su propio caso;
   - en FC y SpO2 cada lectura es un punto: nada se promedia; las lecturas
     se agrupan por sesión --nunca por día-- y la gráfica se desplaza.

   Sin dependencias: node:test y node:assert. El dibujo se ejecuta sobre un
   DOM mínimo para contar lo que construye; su aspecto se revisa en el
   navegador real.
   ========================================================================= */

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

// Un ResizeObserver mínimo y controlable, instalado ANTES de cargar el módulo
// (que crea el suyo al cargarse): permite simular que una vista oculta pasa a
// tener tamaño, como hace el navegador.
const observadores = [];
globalThis.ResizeObserver = class {
  constructor(callback) { this.callback = callback; this.objetivos = []; observadores.push(this); }
  observe(objetivo) { this.objetivos.push(objetivo); }
  unobserve(objetivo) { this.objetivos = this.objetivos.filter((o) => o !== objetivo); }
  notificar(objetivo) { this.callback([{ target: objetivo }]); }
};

require(path.resolve(__dirname, '..', 'graficas.js'));
const G = globalThis.FetalAlertGraficas;

const DIA = 24 * 60 * 60 * 1000;
const t0 = Date.UTC(2026, 0, 1);

function punto(dias, valor) {
  return { instante: t0 + dias * DIA, valor };
}

test('El eje temporal es proporcional: la separación sigue a la distancia en días', () => {
  // Tres registros: día 0, día 1 y día 31.
  const m = G.modelo(G.normalizar([punto(0, 80), punto(1, 82), punto(31, 85)]), 'linea');
  const [a, b, c] = m.puntos.map((p) => p.x);
  const corto = b - a;
  const largo = c - b;
  assert.ok(Math.abs(largo / corto - 30) < 1e-9, `proporción ${largo / corto}`);
});

test('Cada marca es un registro: ni más ni menos, y en orden cronológico', () => {
  const entrada = [punto(10, 90), punto(0, 80), punto(5, 85)];
  const m = G.modelo(G.normalizar(entrada), 'linea');
  assert.equal(m.puntos.length, 3);
  assert.deepEqual(m.puntos.map((p) => p.dato.valor), [80, 85, 90]);
});

test('El cero es un valor; vacío, null y undefined no se convierten en cero', () => {
  const normalizados = G.normalizar([
    punto(0, 0), punto(1, '0'), punto(2, ''), punto(3, null), punto(4, undefined), punto(5, '12'), punto(6, '83.00')
  ]);
  assert.deepEqual(normalizados.map((p) => p.valor), [0, 0, 12, 83]);
  assert.ok(Number.isNaN(G.aNumero(null)));
  assert.ok(Number.isNaN(G.aNumero('')));
  assert.equal(G.aNumero('0'), 0);
});

test('«Últimos 30 días con registros» se cuenta desde el último registro, no desde hoy', () => {
  // Un embarazo histórico: todo es de hace años respecto de «hoy».
  const puntos = [punto(0, 1), punto(20, 2), punto(45, 3), punto(60, 4)];
  const ultimos = G.filtrarPorPeriodo(puntos, 'ultimos30');
  assert.deepEqual(ultimos.map((p) => p.valor), [3, 4]);
  assert.deepEqual(G.filtrarPorPeriodo(puntos, 'todo').map((p) => p.valor), [1, 2, 3, 4]);
});

test('Sin registros: el modelo lo dice y no hay marcas', () => {
  const m = G.modelo([], 'linea');
  assert.equal(m.vacio, true);
  assert.equal(m.puntos.length, 0);
});

test('Un solo registro: una marca centrada y una única fecha en el eje', () => {
  const m = G.modelo(G.normalizar([punto(3, 7)]), 'barras');
  assert.equal(m.unico, true);
  assert.equal(m.puntos.length, 1);
  assert.equal(m.marcasX.length, 1);
  assert.equal(m.puntos[0].x, (m.area.x0 + m.area.x1) / 2);
});

test('Las barras parten de cero y un cero no tiene altura inventada', () => {
  const m = G.modelo(G.normalizar([punto(0, 0), punto(1, 7)]), 'barras');
  assert.equal(m.marcasY[0].valor, 0);
  const cero = m.puntos[0];
  assert.equal(cero.y, cero.base, 'el cero queda sobre la base');
});

test('El eje de valores cubre todos los registros', () => {
  const valores = [83, 96, 71];
  const marcas = G.marcasDeValor(valores, false);
  assert.ok(marcas[0] <= 71 && marcas[marcas.length - 1] >= 96, JSON.stringify(marcas));
});

test('Las líneas dejan al menos un paso libre bajo el punto más bajo; las barras siguen en cero', () => {
  for (const valores of [[80.4, 91.2, 93.4], [94.6, 98.4], [85], [0, 3]]) {
    const marcas = G.marcasDeValor(valores, false);
    const paso = marcas[1] - marcas[0];
    assert.ok(Math.min(...valores) - marcas[0] >= paso - 1e-9, JSON.stringify([valores, marcas]));
  }
  assert.equal(G.marcasDeValor([4, 9], true)[0], 0);
  // Y el primer punto no queda pegado al eje de valores.
  const m = G.modelo(G.normalizar([punto(0, 80), punto(9, 90)]), 'linea');
  assert.ok(m.puntos[0].x - m.area.x0 >= 20, String(m.puntos[0].x - m.area.x0));
});

test('El módulo no clasifica ni pide nada a la red', () => {
  // El espacio de nombres de SVG es un identificador, no una dirección a la
  // que se llame: es el único literal «http://» admitido, y una sola vez.
  const bruta = require('node:fs').readFileSync(path.resolve(__dirname, '..', 'graficas.js'), 'utf8');
  const espacio = "const SVG = 'http://www.w3.org/2000/svg';";
  assert.equal(bruta.split(espacio).length - 1, 1);
  const fuente = bruta.replace(espacio, '');
  for (const prohibido of ['codigo_semaforo', 'fetch(', 'XMLHttpRequest', 'localStorage', 'http://', 'https://']) {
    assert.ok(!fuente.includes(prohibido), prohibido);
  }
});

test('Los valores se escriben sobre las marcas solo si caben TODOS', () => {
  // Separados: caben todos.
  assert.equal(G.etiquetasCaben([{ x: 40, valor: 82 }, { x: 90, valor: 85 }, { x: 140, valor: 88 }]), true);
  // Dos lecturas a minutos de distancia quedan casi en la misma x: ninguna
  // lleva su valor, en lugar de escribir unas sí y otras no.
  assert.equal(G.etiquetasCaben([{ x: 40, valor: 82 }, { x: 41, valor: 85 }, { x: 140, valor: 88 }]), false);
  // Uno solo, o ninguno, siempre cabe.
  assert.equal(G.etiquetasCaben([{ x: 40, valor: 12 }]), true);
  assert.equal(G.etiquetasCaben([]), true);
});

// ---------------------------------------------------------------------------
// Dispersión de FC y SpO2: un punto por lectura, agrupado por sesión
// ---------------------------------------------------------------------------

const MINUTO = 60 * 1000;
const HORA = 60 * MINUTO;

function lectura(idSesion, minutos, valor, extra) {
  return Object.assign({
    idSesion, instante: t0 + minutos * MINUTO, valor,
    textoValor: valor + ' BPM', textoFecha: 'f' + minutos, textoFechaCorta: 'c' + minutos, semana: 36
  }, extra || {});
}

/** Lo que dibuja la dispersión: normalizar, período y modelo, en ese orden. */
function dispersion(lecturas, periodo) {
  return G.modeloDispersion(G.filtrarPorPeriodo(G.normalizar(lecturas), periodo || 'todo'));
}

// Sesión 134 del dataset (lecturas_biometricas.csv): cinco FC en cuatro minutos.
const SESION_134 = [125, 73, 123, 52, 85].map((v, i) => lectura(134, i, String(v) + '.00'));

test('Cada lectura es exactamente un punto, con su valor real: no hay promedio', () => {
  const m = dispersion(SESION_134);
  assert.equal(m.puntos.length, 5);
  assert.deepEqual(m.puntos.map((p) => p.dato.valor), [125, 73, 123, 52, 85]);
  // La altura sale del valor y de nada más: mismo valor, misma y.
  const escala = (v) => m.puntos.find((p) => p.dato.valor === v).y;
  assert.ok(escala(125) < escala(123) && escala(123) < escala(85) && escala(85) < escala(73));
  assert.equal(G.promediarPorSesion, undefined, 'la función de promedio ya no existe');
  assert.ok(m.puntos.every((p) => Number.isInteger(p.dato.valor)), 'ningún valor derivado');
});

test('Cinco lecturas de una sesión son cinco puntos en un solo grupo, repartidos sin taparse', () => {
  const m = dispersion(SESION_134);
  assert.equal(m.grupos.length, 1);
  const g = m.grupos[0];
  assert.equal(g.lecturas.length, 5);
  assert.deepEqual(g.lecturas.map((p) => `${p.indice} de ${p.total}`), ['1 de 5', '2 de 5', '3 de 5', '4 de 5', '5 de 5']);
  const xs = g.lecturas.map((p) => p.x);
  for (let i = 1; i < xs.length; i += 1) assert.ok(xs[i] > xs[i - 1], 'orden dentro de la sesión');
  // El reparto es solo maquetación: queda dentro de la columna de ESA sesión.
  assert.ok(xs[0] > g.x0 && xs[4] < g.x0 + G.DISPERSION.anchoSesion);
  assert.ok(Math.abs((xs[0] + xs[4]) / 2 - g.centro) < 1e-9, 'centrado en su columna');
});

test('Dos sesiones del mismo día son dos grupos distintos, en orden cronológico', () => {
  const manana = [80, 84].map((v, i) => lectura(1, i, v));
  const tarde = [90, 96, 91].map((v, i) => lectura(2, 6 * 60 + i, v));
  const otroDia = [88].map((v) => lectura(3, 3 * 24 * 60, v));
  // Desordenadas a propósito.
  const m = dispersion([otroDia[0], tarde[1], manana[0], tarde[0], manana[1], tarde[2]]);
  assert.equal(m.grupos.length, 3);
  assert.deepEqual(m.grupos.map((g) => g.lecturas.length), [2, 3, 1]);
  assert.deepEqual(m.grupos.map((g) => g.instante), [t0, t0 + 6 * HORA, t0 + 3 * DIA]);
  for (let i = 1; i < m.grupos.length; i += 1) {
    assert.ok(m.grupos[i].x0 >= m.grupos[i - 1].x0 + G.DISPERSION.anchoSesion, 'cada sesión, su columna');
  }
  assert.equal(m.puntos.length, 6, 'ni un punto de más ni de menos');
});

test('El cero es un valor; null y vacío no se convierten en cero ni en un punto', () => {
  const m = dispersion([lectura(5, 0, 0), lectura(5, 1, null), lectura(5, 2, ''), lectura(5, 3, '0'), lectura(5, 4, '97.00')]);
  assert.deepEqual(m.puntos.map((p) => p.dato.valor), [0, 0, 97]);
  assert.deepEqual(m.grupos[0].lecturas.map((p) => p.total), [3, 3, 3]);
  assert.ok(m.marcasY[0].valor < 0 || m.marcasY[0].valor === 0);
});

test('Una lectura sin sesión conocida forma su propio grupo: no se junta con nadie', () => {
  const m = dispersion([lectura(null, 0, 80), lectura(undefined, 1, 90), lectura(9, 2, 83)]);
  assert.equal(m.grupos.length, 3);
  assert.ok(m.puntos.every((p) => p.total === 1));
});

test('«Últimos 30 días» filtra las lecturas primero y después organiza las sesiones visibles', () => {
  const vieja = [70, 72].map((v, i) => lectura(1, i, v));
  const reciente = [80, 82].map((v, i) => lectura(2, 40 * 24 * 60 + i, v));
  const ultima = [85].map((v) => lectura(3, 60 * 24 * 60, v));
  const todo = dispersion(vieja.concat(reciente, ultima), 'todo');
  const ultimos = dispersion(vieja.concat(reciente, ultima), 'ultimos30');
  assert.equal(todo.grupos.length, 3);
  assert.equal(ultimos.grupos.length, 2);
  assert.deepEqual(ultimos.puntos.map((p) => p.dato.valor), [80, 82, 85]);
});

test('Con muchas sesiones el lienzo crece a lo ancho; con pocas conserva un mínimo', () => {
  const muchas = Array.from({ length: 40 }, (_, s) =>
    [80, 81, 82, 83, 84].map((v, i) => lectura(s, s * 24 * 60 + i, v))).flat();
  const m = dispersion(muchas);
  assert.equal(m.grupos.length, 40);
  assert.equal(m.puntos.length, 200);
  assert.equal(m.ancho, 40 * G.DISPERSION.anchoSesion);
  assert.ok(m.ancho > 1280, 'más ancho que cualquier tarjeta: se desplaza');
  const pocas = dispersion(SESION_134);
  assert.equal(pocas.ancho, G.DISPERSION.anchoMinimo);
  assert.equal(pocas.alto, m.alto, 'la altura no depende del número de sesiones');
});

test('Flechas: se deshabilitan al principio y al final, y avanzan sesiones enteras', () => {
  assert.deepEqual(G.estadoFlechas(0, 400, 2000), { anterior: false, siguiente: true });
  assert.deepEqual(G.estadoFlechas(800, 400, 2000), { anterior: true, siguiente: true });
  assert.deepEqual(G.estadoFlechas(1600, 400, 2000), { anterior: true, siguiente: false });
  assert.deepEqual(G.estadoFlechas(0, 400, 300), { anterior: false, siguiente: false }, 'sin desborde');
  const sesion = G.DISPERSION.anchoSesion;
  assert.equal(G.pasoDeFlecha(1000), 3 * sesion, 'como máximo tres sesiones');
  assert.equal(G.pasoDeFlecha(3 * sesion), 2 * sesion, 'nunca todo lo visible: no se salta ninguna');
  assert.equal(G.pasoDeFlecha(100), sesion, 'al menos una sesión');
});

// data/generated no está versionado y el CI ejecuta estas pruebas antes de
// generarlo: sin el archivo, esta comprobación se omite (las anteriores, con
// la sesión 134 copiada literalmente, sí corren siempre).
const CSV_LECTURAS = path.resolve(__dirname, '..', '..', '..', 'data', 'generated', 'lecturas_biometricas.csv');
const HAY_DATASET = require('node:fs').existsSync(CSV_LECTURAS);

test('Dataset: las 560 lecturas de signos maternos son 560 puntos en 112 sesiones, con su valor exacto',
  { skip: HAY_DATASET ? false : 'data/generated no está generado' }, () => {
  const csv = require('node:fs').readFileSync(CSV_LECTURAS, 'utf8')
    .replace(/^﻿/, '').trim().split(/\r?\n/);
  const cabecera = csv[0].split(',');
  const col = (nombre) => cabecera.indexOf(nombre);
  const conSignos = csv.slice(1).map((l) => l.split(',')).filter((f) => f[col('hr_valor')] !== '');

  for (const campo of ['hr_valor', 'spo2_valor']) {
    const m = G.modeloDispersion(G.normalizar(conSignos.map((f) => ({
      idSesion: f[col('id_sesion')],
      instante: Date.parse(f[col('fecha_hora_captura')]),
      valor: f[col(campo)],
      idLectura: f[col('id_lectura')]
    }))));
    assert.equal(m.grupos.length, 112, campo);
    assert.equal(m.puntos.length, conSignos.length, campo + ': una marca por lectura');
    const porId = new Map(conSignos.map((f) => [f[col('id_lectura')], Number(f[col(campo)])]));
    m.puntos.forEach((p) => assert.equal(p.dato.valor, porId.get(p.dato.idLectura)));
    m.grupos.forEach((g) => assert.ok(new Set(g.lecturas.map((p) => p.dato.idSesion)).size === 1));
  }
});

// ---------------------------------------------------------------------------
// El dibujo, sobre un DOM mínimo
// ---------------------------------------------------------------------------

/** Lo justo de DOM para ejecutar `dibujar` y mirar lo que construye. */
class Nodo {
  constructor(etiqueta) {
    this.etiqueta = etiqueta;
    this.hijos = [];
    this.atributos = {};
    this.oyentes = {};
    this.style = {};
    this.clases = new Set();
    this.texto = '';
    this.hidden = false;
    this.disabled = false;
    this.scrollLeft = 0;
    this.clientWidth = 0;
    this.scrollWidth = 0;
    this.isConnected = true;
  }
  get firstChild() { return this.hijos[0] || null; }
  appendChild(h) { this.hijos.push(h); h.padre = this; return h; }
  removeChild(h) { this.hijos = this.hijos.filter((x) => x !== h); return h; }
  setAttribute(n, v) { this.atributos[n] = String(v); if (n === 'class') this.className = v; }
  getAttribute(n) { return n in this.atributos ? this.atributos[n] : null; }
  set className(v) { this.clases = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return Array.from(this.clases).join(' '); }
  get classList() {
    const c = this.clases;
    return {
      add: (n) => c.add(n), remove: (n) => c.delete(n), contains: (n) => c.has(n),
      toggle: (n, f) => ((f === undefined ? !c.has(n) : f) ? c.add(n) : c.delete(n))
    };
  }
  set textContent(v) { this.hijos = []; this.texto = String(v); }
  get textContent() { return this.texto + this.hijos.map((h) => h.textContent).join(''); }
  addEventListener(t, f) { (this.oyentes[t] = this.oyentes[t] || []).push(f); }
  removeEventListener(t, f) { this.oyentes[t] = (this.oyentes[t] || []).filter((x) => x !== f); }
  disparar(t, e) { (this.oyentes[t] || []).forEach((f) => f(Object.assign({ preventDefault() {} }, e))); }
  contains() { return false; }
  focus() { this.enfocado = true; this.disparar('focus'); }
  scrollBy(o) { this.scrollLeft = Math.max(0, Math.min(this.scrollWidth - this.clientWidth, this.scrollLeft + o.left)); this.disparar('scroll'); }
  buscar(pred, acc = []) { if (pred(this)) acc.push(this); this.hijos.forEach((h) => h.buscar(pred, acc)); return acc; }
  porClase(c) { return this.buscar((n) => n.clases.has(c)); }
}

function conDocumento(fn) {
  const anterior = globalThis.document;
  globalThis.document = {
    activeElement: null,
    createElementNS: (_, e) => new Nodo(e),
    createElement: (e) => new Nodo(e),
    createTextNode: (t) => { const n = new Nodo('#text'); n.texto = t; return n; }
  };
  try { return fn(); } finally { globalThis.document = anterior; }
}

function configDispersion(lecturas, extra) {
  return Object.assign({
    titulo: 'Frecuencia cardíaca materna', unidad: 'BPM', tipo: 'dispersion', clase: 'grafica-rosa',
    periodo: 'todo', puntos: lecturas,
    formatoEje: (ms) => 'eje ' + ms,
    formatoSesion: (ms) => ({ fecha: 'día ' + ms, hora: 'hora ' + ms })
  }, extra || {});
}

test('Dibujo de FC: un círculo por lectura, sin línea ni relleno, con franjas y rótulos por sesión', () => {
  conDocumento(() => {
    const raiz = new Nodo('div');
    const dos = SESION_134.concat([90, 91].map((v, i) => lectura(135, 6 * 60 + i, v)));
    G.dibujar(raiz, configDispersion(dos));
    assert.equal(raiz.porClase('grafica-marca').length, 7);
    assert.equal(raiz.buscar((n) => n.etiqueta === 'polyline').length, 0, 'sin línea');
    assert.equal(raiz.porClase('grafica-area').length, 0, 'sin relleno bajo una línea');
    assert.equal(raiz.porClase('grafica-franja').length, 2, 'una franja por sesión');
    const rotulos = raiz.porClase('grafica-eje').map((n) => n.textContent);
    assert.ok(rotulos.includes('día ' + t0) && rotulos.includes('hora ' + (t0 + 6 * HORA)));
    assert.ok(!rotulos.some((t) => /134|135/.test(t)), 'ningún id_sesion a la vista');
    assert.match(raiz.textContent, /2 sesiones \(7 lecturas\)/);
  });
});

test('Cada punto dice su valor, su fecha y que es la lectura i de n de esa sesión', () => {
  conDocumento(() => {
    const raiz = new Nodo('div');
    G.dibujar(raiz, configDispersion(SESION_134));
    const puntos = raiz.porClase('grafica-punto');
    assert.equal(puntos[0].getAttribute('aria-label'),
      'Frecuencia cardíaca materna: 125.00 BPM, f0, Lectura 1 de 5 de esta sesión, semana 36');
    puntos[3].disparar('focus');
    const aviso = raiz.porClase('grafica-aviso')[0];
    assert.equal(aviso.hidden, false);
    assert.equal(aviso.textContent, '52.00 BPM · f3 · Lectura 4 de 5 de esta sesión · semana 36');
    assert.ok(raiz.porClase('grafica-franja')[0].clases.has('activa'), 'se destaca su sesión');
    // Teclado: la flecha lleva al punto siguiente, que queda como el tabulable.
    puntos[3].disparar('keydown', { key: 'ArrowRight' });
    assert.equal(puntos[4].enfocado, true);
    assert.equal(puntos[4].getAttribute('tabindex'), '0');
  });
});

test('La tabla alternativa conserva cada lectura con su valor real', () => {
  conDocumento(() => {
    const raiz = new Nodo('div');
    G.dibujar(raiz, configDispersion(SESION_134));
    const filas = raiz.buscar((n) => n.etiqueta === 'tr' && n.hijos.every((h) => h.etiqueta === 'td'))
      .map((tr) => tr.hijos.map((td) => td.textContent));
    assert.equal(filas.length, 5);
    assert.deepEqual(filas.map((f) => f[1]), ['85.00 BPM', '52.00 BPM', '123.00 BPM', '73.00 BPM', '125.00 BPM']);
    assert.deepEqual(filas.map((f) => f[2]), ['5 de 5', '4 de 5', '3 de 5', '2 de 5', '1 de 5']);
  });
});

test('Zona desplazable: botones reales con aria-label que se deshabilitan en los extremos', () => {
  conDocumento(() => {
    const raiz = new Nodo('div');
    const muchas = Array.from({ length: 20 }, (_, s) => [80, 81].map((v, i) => lectura(s, s * 24 * 60 + i, v))).flat();
    G.dibujar(raiz, configDispersion(muchas));
    const zona = raiz.porClase('grafica-desplazable')[0];
    assert.equal(zona.getAttribute('tabindex'), '0', 'navegable con teclado');
    assert.equal(raiz.porClase('grafica-contenido')[0].style.width, 20 * G.DISPERSION.anchoSesion + 'px');
    const [anterior, siguiente] = raiz.porClase('grafica-flecha');
    assert.equal(anterior.etiqueta, 'button');
    assert.match(anterior.getAttribute('aria-label'), /anteriores/);
    assert.match(siguiente.getAttribute('aria-label'), /siguientes/);

    // Simula el navegador: 400 px visibles, al principio.
    zona.clientWidth = 400;
    zona.scrollWidth = 20 * G.DISPERSION.anchoSesion;
    zona.scrollLeft = 0;
    zona.disparar('scroll');
    assert.equal(anterior.disabled, true);
    assert.equal(siguiente.disabled, false);
    siguiente.disparar('click');
    assert.equal(zona.scrollLeft, G.pasoDeFlecha(400));
    assert.equal(anterior.disabled, false);
    zona.scrollLeft = zona.scrollWidth - zona.clientWidth;
    zona.disparar('scroll');
    assert.equal(siguiente.disabled, true);
    // Y la flecha izquierda vuelve hacia la izquierda.
    const alFinal = zona.scrollLeft;
    anterior.disparar('click');
    assert.equal(zona.scrollLeft, alFinal - G.pasoDeFlecha(400));
    assert.equal(siguiente.disabled, false);
  });
});

/** Dibuja FC con `n` sesiones y devuelve la zona, las flechas, la nota y la barra propia. */
function dispersionDibujada(raiz, n, extra) {
  const lecturas = Array.from({ length: n }, (_, s) =>
    [80, 81, 82].map((v, i) => lectura(s, s * 24 * 60 + i, v))).flat();
  G.dibujar(raiz, configDispersion(lecturas, extra));
  const [anterior, siguiente] = raiz.porClase('grafica-flecha');
  return {
    zona: raiz.porClase('grafica-desplazable')[0],
    anterior, siguiente,
    nota: raiz.porClase('grafica-navegacion-nota')[0],
    riel: raiz.porClase('grafica-riel')[0],
    guia: raiz.porClase('grafica-riel-guia')[0]
  };
}

test('Seis sesiones ya no caben en una tarjeta de escritorio (476 px): hay algo que desplazar', () => {
  // Con 64 px por sesión cabían (384 px) y las flechas nunca se activaban.
  assert.ok(6 * G.DISPERSION.anchoSesion > 476, String(6 * G.DISPERSION.anchoSesion));
  conDocumento(() => {
    const raiz = new Nodo('div');
    const d = dispersionDibujada(raiz, 6);
    d.zona.clientWidth = 476;
    d.zona.scrollWidth = 6 * G.DISPERSION.anchoSesion;
    observadores[0].notificar(d.zona);
    assert.equal(d.zona.scrollLeft, d.zona.scrollWidth, 'empieza en las más recientes');
    d.zona.scrollLeft = d.zona.scrollWidth - d.zona.clientWidth;
    d.zona.disparar('scroll');
    assert.equal(d.anterior.disabled, false);
    assert.equal(d.siguiente.disabled, true);
    assert.equal(d.nota.textContent, 'Desliza o usa las flechas para ver más sesiones.');
  });
});

test('Una vista oculta al dibujar recalcula flechas y posición cuando pasa a tener tamaño', () => {
  conDocumento(() => {
    const raiz = new Nodo('div');
    const d = dispersionDibujada(raiz, 20);
    // Oculta: sin tamaño. Nada que desplazar todavía, y no se ha movido.
    assert.equal(d.anterior.disabled, true);
    assert.equal(d.siguiente.disabled, true);
    assert.equal(d.zona.scrollLeft, 0);
    assert.ok(observadores[0].objetivos.includes(d.zona), 'la zona queda observada');

    // Se muestra: el navegador le da tamaño y avisa.
    d.zona.clientWidth = 300;
    d.zona.scrollWidth = 20 * G.DISPERSION.anchoSesion;
    observadores[0].notificar(d.zona);
    assert.equal(d.zona.scrollLeft, d.zona.scrollWidth, 'va a las sesiones más recientes');
    d.zona.scrollLeft = d.zona.scrollWidth - d.zona.clientWidth;
    d.zona.disparar('scroll');
    assert.equal(d.anterior.disabled, false);
    assert.equal(d.siguiente.disabled, true);

    // Un segundo aviso (otro cambio de tamaño) no la devuelve al final si la
    // paciente ya se movió.
    d.zona.scrollLeft = 0;
    observadores[0].notificar(d.zona);
    assert.equal(d.zona.scrollLeft, 0);
    assert.equal(d.anterior.disabled, true);
    assert.equal(d.siguiente.disabled, false);
  });
});

test('Cambiar de período redibuja con otro ancho y recalcula los controles', () => {
  conDocumento(() => {
    const raiz = new Nodo('div');
    const lecturas = [0, 1, 2, 3, 50, 60].map((dia, s) => lectura(s, dia * 24 * 60, 80 + s));
    G.dibujar(raiz, configDispersion(lecturas));
    const todo = raiz.porClase('grafica-contenido')[0].style.width;
    G.dibujar(raiz, configDispersion(lecturas, { periodo: 'ultimos30' }));
    const ultimos = raiz.porClase('grafica-contenido')[0].style.width;
    assert.equal(todo, 6 * G.DISPERSION.anchoSesion + 'px');
    assert.equal(ultimos, G.DISPERSION.anchoMinimo + 'px', 'dos sesiones: ancho mínimo');
    assert.equal(raiz.porClase('grafica-marca').length, 2, 'un punto por lectura del período');
    const [anterior, siguiente] = raiz.porClase('grafica-flecha');
    const zona = raiz.porClase('grafica-desplazable')[0];
    zona.clientWidth = 476;
    zona.scrollWidth = 476;
    zona.disparar('scroll');
    assert.equal(anterior.disabled, true);
    assert.equal(siguiente.disabled, true);
    assert.equal(raiz.porClase('grafica-navegacion-nota')[0].textContent, 'Todas las sesiones están a la vista.');
  });
});

test('Barra propia: solo donde la nativa no ocupa espacio, sincronizada y arrastrable', () => {
  conDocumento(() => {
    const raiz = new Nodo('div');
    const d = dispersionDibujada(raiz, 10);
    d.zona.clientWidth = 240;
    d.zona.scrollWidth = 960;
    d.zona.scrollLeft = 0;

    // Escritorio: la barra nativa ocupa 10 px; no se duplica.
    d.zona.offsetHeight = 254;
    d.zona.clientHeight = 244;
    d.zona.disparar('scroll');
    assert.equal(d.riel.hidden, true);

    // Móvil: barra superpuesta, 0 px. Aparece la propia, sincronizada.
    d.zona.offsetHeight = 244;
    d.zona.disparar('scroll');
    assert.equal(d.riel.hidden, false);
    assert.equal(d.guia.style.width, '25%');
    assert.equal(d.guia.style.left, '0%');
    d.zona.scrollLeft = 480;
    d.zona.disparar('scroll');
    assert.equal(d.guia.style.left, '50%');

    // Arrastrar la guía 30 px en un riel de 240 px mueve 120 px el contenido.
    d.riel.getBoundingClientRect = () => ({ left: 0, width: 240 });
    d.riel.disparar('pointerdown', { target: d.guia, clientX: 130, pointerId: 1 });
    d.riel.disparar('pointermove', { clientX: 160 });
    assert.equal(d.zona.scrollLeft, 600);
    d.riel.disparar('pointerup', {});
    d.riel.disparar('pointermove', { clientX: 230 });
    assert.equal(d.zona.scrollLeft, 600, 'soltada, ya no arrastra');

    // Sin nada que desplazar, no se muestra.
    d.zona.scrollWidth = 240;
    d.zona.scrollLeft = 0;
    d.zona.disparar('scroll');
    assert.equal(d.riel.hidden, true);
  });
});

test('Movimientos no cambian: sigue siendo una barra por registro', () => {
  conDocumento(() => {
    const raiz = new Nodo('div');
    G.dibujar(raiz, {
      titulo: 'Movimientos fetales', unidad: 'movimientos', tipo: 'barras', clase: 'grafica-lila',
      periodo: 'todo', formatoEje: () => 'e',
      puntos: [punto(0, 6), punto(1, 0), punto(9, 12)].map((p) => Object.assign(p, { textoFecha: 'f', textoFechaCorta: 'c', textoValor: String(p.valor) }))
    });
    assert.equal(raiz.porClase('grafica-barra').length, 3);
    assert.equal(raiz.porClase('grafica-desplazable').length, 0, 'sin scroll propio');
    assert.match(raiz.textContent, /3 registros/);
  });
});

test('El lienzo ancho mantiene la altura y reparte más marcas de fecha', () => {
  const puntos = G.normalizar([punto(0, 8), punto(10, 12), punto(20, 15)]);
  const normal = G.modelo(puntos, 'barras');
  const ancho = G.modelo(puntos, 'barras', 720);
  assert.equal(normal.area.x1, 360 - 12);
  assert.equal(ancho.area.x1, 720 - 12);
  assert.equal(ancho.area.y1, normal.area.y1, 'misma altura');
  assert.equal(normal.marcasX.length, 4);
  assert.equal(ancho.marcasX.length, 6);
});

test('Todas las barras tienen el mismo grosor, acotado, y nunca se tapan', () => {
  const pocas = G.modelo(G.normalizar([punto(0, 8), punto(10, 12), punto(20, 15)]), 'barras');
  assert.ok(pocas.puntos.every((p) => p.ancho === 16), 'con pocos registros, el máximo: ni muy gruesas');

  // Veinte registros, algunos a un día o a horas: el mismo ancho para todos.
  for (const lienzo of [360, 720]) {
    const dias = [0, 0.1, 1, ...Array.from({ length: 17 }, (_, i) => 8 + i * 7)];
    const m = G.modelo(G.normalizar(dias.map((d) => punto(d, 10))), 'barras', lienzo);
    assert.equal(m.puntos.length, 20, 'una barra por registro, sin sumar');
    assert.ok(m.puntos.every((p) => p.ancho === m.anchoBarra), 'un solo grosor');
    assert.ok(m.anchoBarra >= 6 && m.anchoBarra <= 16, String(m.anchoBarra));
    for (let i = 1; i < m.puntos.length; i += 1) {
      const a = m.puntos[i - 1];
      const b = m.puntos[i];
      assert.ok(a.x + a.ancho / 2 < b.x - b.ancho / 2, `${lienzo}: las barras ${i - 1} y ${i} se tapan`);
    }
    assert.ok(m.puntos[0].x - m.puntos[0].ancho / 2 >= m.area.x0, 'no se sale por la izquierda');
    assert.ok(m.puntos[19].x + m.puntos[19].ancho / 2 <= m.area.x1, 'ni por la derecha');
    // La pareja que chocaba queda justo al lado, no lejos de su fecha.
    assert.ok(m.puntos[1].x - m.puntos[0].x < 2 * m.anchoBarra, 'desplazamiento mínimo');
  }
});
