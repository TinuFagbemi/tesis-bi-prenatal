/* =========================================================================
   Pruebas de graficas.js (SCRUM-72, etapa 2): la parte pura.

   Lo que se comprueba es lo que haría engañosa una gráfica si fallara:
   - el eje temporal es proporcional (no un punto por posición);
   - no aparece ningún punto que no sea un registro;
   - un cero es un valor y un vacío o un null no se convierten en cero;
   - el período se cuenta desde el último registro, no desde hoy;
   - una serie vacía o de un solo registro tiene su propio caso.

   Sin dependencias: node:test y node:assert. El dibujo (SVG) se revisa en
   el navegador real.
   ========================================================================= */

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

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
