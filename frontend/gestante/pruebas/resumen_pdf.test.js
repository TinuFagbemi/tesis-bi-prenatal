/* =========================================================================
   Pruebas de resumen_pdf.js: la definición de pdfmake del resumen.

   Es una función pura (modelo → definición), así que se prueba en Node sin
   DOM ni pdfmake. Lo que se comprueba es lo que no puede romperse: los
   textos llegan tal cual, el semáforo nunca va sin su nombre, la tabla
   repite la cabecera y no parte filas, la banda de marca es solo de la
   primera página y la numeración es de todas.

   Todos los datos son ficticios y simulados.
   ========================================================================= */

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { definicion } = require(path.resolve(__dirname, '..', 'resumen_pdf.js'));

function modelo(cambios) {
  return Object.assign({
    alcance: 'resumen',
    marca: 'FetalAlert',
    lema: 'Tecnología al cuidado de tu embarazo',
    titulo: 'Resumen de mis registros',
    embarazo: 'Embarazo desde 6 ene 2025 — Finalizado',
    fechas: [
      { etiqueta: 'Datos consultados el', valor: '25 sept 2026, 10:00' },
      { etiqueta: 'Resumen generado el', valor: '25 sept 2026, 10:05' }
    ],
    aviso: null,
    datosEmbarazo: [
      { etiqueta: 'Estado', valor: 'Finalizado' },
      { etiqueta: 'Inicio del embarazo', valor: '6 ene 2025' },
      { etiqueta: 'Fecha probable de parto', valor: '13 oct 2025' },
      { etiqueta: 'Fecha de cierre', valor: '13 oct 2025' }
    ],
    notaValores: 'El último valor de cada medición en todo este embarazo.',
    valores: [
      { clave: 'frecuencia_cardiaca', titulo: 'Frecuencia cardíaca materna', cifra: '95 BPM', detalles: ['Registrado el 8 sept 2025, 12:14'] },
      { clave: 'saturacion_oxigeno', titulo: 'Saturación de oxígeno (SpO₂)', cifra: '—', detalles: ['Sin registros'] },
      { clave: 'movimientos_fetales', titulo: 'Movimientos fetales', cifra: '0 movimientos', detalles: ['Registrado el 1 oct 2025, 11:04'] }
    ],
    clasificacion: {
      filas: [{ etiqueta: 'Lectura del', valor: '1 oct 2025, 11:04' }, { etiqueta: 'Midió', valor: 'movimientos fetales' }],
      codigo: 'WARNING', nombre: 'Amarillo', mensaje: 'Conviene repetir la medición',
      nota: 'La clasificación la asignó el sistema a esta lectura completa.'
    },
    lecturas: {
      titulo: 'Lecturas recientes',
      filas: [{ etiqueta: 'Período', valor: 'Todo el embarazo' }, { etiqueta: 'Lecturas incluidas', valor: '2' }],
      nota: 'Se incluyen las 2 lecturas del período.',
      columnas: ['Fecha y hora', 'Frecuencia cardíaca', 'SpO₂', 'Movimientos fetales', 'Semana', 'Clasificación'],
      filasDeTabla: [
        { celdas: ['1 oct 2025, 11:04', '—', '—', '0', '39'], codigo: 'WARNING', semaforo: 'Amarillo' },
        { celdas: ['8 sept 2025, 12:14', '95 BPM', '99 %', '—', '36'], codigo: null, semaforo: '—' }
      ]
    },
    pie: ['Horas de Panamá.', 'Información simulada con fines académicos.'],
    nombreArchivo: 'FetalAlert-resumen-2026-09-25.pdf'
  }, cambios || {});
}

/** Todos los textos del contenido, en orden (los iconos no llevan datos). */
function textos(nodo, salida) {
  const acumulado = salida || [];
  if (typeof nodo === 'string') { acumulado.push(nodo); return acumulado; }
  if (Array.isArray(nodo)) { nodo.forEach((n) => textos(n, acumulado)); return acumulado; }
  if (!nodo || typeof nodo !== 'object' || nodo.svg) return acumulado;
  ['text', 'stack', 'columns'].forEach((k) => { if (k in nodo) textos(nodo[k], acumulado); });
  if (nodo.table) textos(nodo.table.body, acumulado);
  return acumulado;
}

function tabla(def) {
  return def.content.find((n) => n.table && n.table.headerRows === 1);
}

test('Página Carta, metadatos sin datos personales y fuente incluida', () => {
  const def = definicion(modelo());
  assert.equal(def.pageSize, 'LETTER');
  assert.equal(def.defaultStyle.font, 'Roboto');
  assert.deepEqual(def.info, {
    title: 'FetalAlert — Resumen de mis registros', author: 'FetalAlert',
    subject: 'Datos simulados con fines académicos', creator: 'FetalAlert'
  });
});

test('Todos los textos del modelo llegan tal cual, también el cero y la ausencia', () => {
  const t = textos(definicion(modelo()).content);
  for (const esperado of ['Fetal', 'Alert', 'Resumen de mis registros', 'Embarazo desde 6 ene 2025 — Finalizado',
    'Datos consultados el', '25 sept 2026, 10:00', 'Estado', 'Finalizado', 'Fecha de cierre',
    '95 BPM', '—', 'Sin registros', '0 movimientos', 'Saturación de oxígeno (SpO₂)',
    'Amarillo', ' — Conviene repetir la medición', 'Lectura del 1 oct 2025, 11:04   ·   Midió movimientos fetales',
    'Se incluyen las 2 lecturas del período.', '0', '39', 'Horas de Panamá.']) {
    assert.ok(t.includes(esperado), 'falta: ' + esperado);
  }
});

test('La tabla repite la cabecera, no parte filas y tiene una columna por dato', () => {
  const t = tabla(definicion(modelo())).table;
  assert.equal(t.headerRows, 1);
  assert.equal(t.dontBreakRows, true);
  assert.equal(t.keepWithHeaderRows, 1);
  assert.equal(t.widths.length, 6);
  assert.deepEqual(t.body[0].map((c) => c.text), modelo().lecturas.columnas);
  assert.equal(t.body.length, 3);
  t.body.slice(1).forEach((fila) => assert.equal(fila.length, 6));
});

test('El semáforo nunca va sin su nombre; un código desconocido no lleva color', () => {
  const t = tabla(definicion(modelo())).table;
  const conColor = t.body[1][5];
  assert.equal(conColor.columns[0].canvas[0].color, '#ffca28');
  assert.equal(conColor.columns[1].text, 'Amarillo');
  const sinColor = t.body[2][5];
  assert.deepEqual(sinColor, { text: '—' });

  const desconocido = definicion(modelo({ clasificacion: Object.assign({}, modelo().clasificacion,
    { codigo: null, nombre: null, mensaje: 'Sin clasificación disponible' }) }));
  const json = JSON.stringify(desconocido.content);
  assert.match(json, /Sin clasificación disponible/);
  assert.doesNotMatch(json.split('Sin clasificación disponible')[0].slice(-200), /#ffca28|#4caf50|#f44336/);
});

test('El aviso de conexión solo aparece si el modelo lo trae', () => {
  const sin = textos(definicion(modelo()).content);
  assert.ok(!sin.some((x) => /no se pudo confirmar la conexión/.test(x)));
  const aviso = 'Al generar este documento no se pudo confirmar la conexión con el servidor.';
  assert.ok(textos(definicion(modelo({ aviso })).content).includes(aviso));
});

test('Banda de marca solo en la primera página; cabecera discreta y numeración en las demás', () => {
  const def = definicion(modelo());
  assert.ok(def.background(1).canvas.length >= 1);
  assert.equal(def.background(2), null);
  assert.equal(def.header(1), null, 'sin descarga, nada en la esquina');
  assert.match(JSON.stringify(def.header(2)), /Resumen de mis registros/);
  assert.match(JSON.stringify(def.footer(2, 3)), /Página 2 de 3/);
  assert.match(JSON.stringify(def.footer(1, 1)), /Información simulada con fines académicos/);
});

test('El título de la tabla no se queda solo al pie de una página', () => {
  const def = definicion(modelo());
  const titulo = def.content.find((n) => n.headlineLevel === 1);
  assert.ok(titulo, 'el bloque previo a la tabla está marcado');
  assert.equal(def.pageBreakBefore({ headlineLevel: 1, startPosition: { top: 700 } }), true);
  assert.equal(def.pageBreakBefore({ headlineLevel: 1, startPosition: { top: 300 } }), false);
  assert.equal(def.pageBreakBefore({ startPosition: { top: 700 } }), false);
});

test('Ningún dato entra en un SVG: los iconos son fijos', () => {
  const malicioso = '<img src=x onerror=alert(1)>';
  const def = definicion(modelo({ embarazo: malicioso }));
  assert.ok(textos(def.content).includes(malicioso));
  const svgs = JSON.stringify(def.content).match(/"svg":"(?:[^"\\]|\\.)*"/g);
  assert.ok(svgs.length >= 7);
  svgs.forEach((s) => assert.doesNotMatch(s, /img|onerror|Embarazo/));
});

test('La marca de descarga va arriba a la derecha en todas las páginas', () => {
  const marca = 'Descargado el 10 oct 2026, 18:05:32 (hora de Panamá)';
  const def = definicion(modelo(), { descargadoEn: marca });
  const primera = def.header(1);
  assert.equal(primera.text, marca);
  assert.equal(primera.alignment, 'right');
  assert.ok(primera.fontSize <= 8, 'pequeña');
  assert.equal(primera.color, '#e2e8f0', 'clara sobre la banda oscura');
  for (const pagina of [2, 3]) {
    const columnas = def.header(pagina).stack[0].columns;
    const derecha = columnas[columnas.length - 1];
    assert.equal(derecha.text, marca);
    assert.equal(derecha.alignment, 'right');
  }
});
