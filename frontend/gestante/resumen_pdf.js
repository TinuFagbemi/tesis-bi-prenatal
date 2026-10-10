/* =========================================================================
   FetalAlert — El PDF de «Guardar mi resumen»

   Convierte el modelo que arma app.js (`modeloDelResumen`: solo textos ya
   formateados) en la definición de documento de pdfmake, con la estética de
   la interfaz: la banda oscura del encabezado con su degradado y el
   logotipo, los iconos de la página en círculos de color de marca, las
   tarjetas con la franja superior morada y rosa, el semáforo con sus colores
   y SIEMPRE con su nombre, y la tabla con la cabecera lila.

   Este archivo no decide nada: no formatea fechas, no elige qué lecturas
   entran ni clasifica. Recibe textos y los coloca. El único dato que no es
   texto es el código del semáforo (OK, WARNING, ERROR o null), y solo sirve
   para elegir el color de una marca que ya lleva su nombre al lado.

   Es una función pura, sin DOM: las pruebas la llaman en Node. pdfmake se
   carga aparte (app.js, `cargarMotorPdf`) y recibe el texto como texto: no
   interpreta marcado.

   Todo lo que se muestra procede de un sistema académico con datos
   simulados.
   ========================================================================= */

(function (global) {
  'use strict';

  // Carta: el tamaño de papel de uso común en Panamá. Márgenes en puntos.
  const PAGINA = { ancho: 612, alto: 792 };
  const MARGEN = { izquierda: 40, arriba: 52, derecha: 40, abajo: 48 };
  const ANCHO_UTIL = PAGINA.ancho - MARGEN.izquierda - MARGEN.derecha;

  // Alto de la banda de marca de la primera página.
  const BANDA = 104;

  // La paleta de styles.css. Los colores de marca no tienen significado
  // clínico; los del semáforo solo acompañan a su nombre.
  const COLOR = {
    pizarra: ['#0f172a', '#1e293b', '#334155', '#4c4a8f'],
    morado: '#8b5cf6',
    moradoMedio: '#a855f7',
    lila: '#c084fc',
    rosa: '#db7093',
    texto: '#1e293b',
    titulo: '#0f172a',
    secundario: '#5b6b80',
    tenue: '#64748b',
    borde: '#e4dcfb',
    bordeFuerte: '#d8ccfa',
    rosaSuave: '#fce7f3',
    moradoSuave: '#ede9fe',
    lilaSuave: '#f3e8ff',
    cabeceraTabla: '#f5f3ff',
    filaPar: '#faf8ff'
  };

  const SEMAFORO = {
    OK: { punto: '#4caf50', fondo: '#e6ffed', texto: '#14532d' },
    WARNING: { punto: '#ffca28', fondo: '#fff9db', texto: '#713f12' },
    ERROR: { punto: '#f44336', fondo: '#fee2e2', texto: '#7f1d1d' },
    NINGUNO: { punto: null, fondo: '#e2e8f0', texto: '#334155' }
  };

  // Los mismos trazos que el sprite de index.html.
  const ICONOS = {
    gestante: '<circle cx="11" cy="4.5" r="2.2"/><path d="M9.5 21v-4.5M9.5 16.5c-1.5-.3-2.3-1.5-2-3.2l.9-4.3c.3-1.3 1.4-2 2.6-1.8 1.1.2 1.8 1 1.8 2.2v1.2c2.6.3 4.2 2 4.2 4.1 0 2-1.6 3.3-3.7 3.3H12M12 21v-4.5"/>',
    reloj: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    lectura: '<circle cx="12" cy="12" r="8.5"/><path d="M8 12.5l2.7 2.7L16 9.5"/>',
    calendario: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 9.5h17M8 3v4m8-4v4"/>',
    corazon: '<path d="M12 20.5s-7.5-4.6-9.2-9.6C1.7 7.5 3.9 4.5 7.1 4.5c2 0 3.6 1.1 4.9 2.9 1.3-1.8 2.9-2.9 4.9-2.9 3.2 0 5.4 3 4.3 6.4-1.7 5-9.2 9.6-9.2 9.6z"/>',
    pulmones: '<path d="M12 4v7m0 0c-1.2 1.4-2.4 2-3.6 2M12 11c1.2 1.4 2.4 2 3.6 2M8.4 7.2C6 8.5 3.5 12 3.5 16.5c0 2 1 3 2.5 3 2.4 0 3.6-2 3.6-5V9.6c0-1.5-.5-2.4-1.2-2.4zm7.2 0c2.4 1.3 4.9 4.8 4.9 9.3 0 2-1 3-2.5 3-2.4 0-3.6-2-3.6-5V9.6c0-1.5.5-2.4 1.2-2.4z"/>',
    huellas: '<path d="M7.5 13.5c-1.8 0-3-1.7-3-4.3S5.8 4.5 7.6 4.5s2.9 2 2.9 4.6-1.2 4.4-3 4.4zM6 16.2l3-.4.6 2.3c.3 1.3-.4 2.4-1.6 2.4-1.1 0-1.9-.9-2-2.1zM16.5 11c-1.8 0-3-1.7-3-4.3S14.8 2 16.6 2s2.9 2 2.9 4.6-1.2 4.4-3 4.4zM15 13.7l3-.4.6 2.3c.3 1.3-.4 2.4-1.6 2.4-1.1 0-1.9-.9-2-2.1z"/>'
  };

  // Los círculos de color de la interfaz: fondo suave y trazo de marca.
  const CIRCULOS = {
    rosa: { fondo: COLOR.rosaSuave, trazo: COLOR.rosa },
    morado: { fondo: COLOR.moradoSuave, trazo: COLOR.morado },
    lila: { fondo: COLOR.lilaSuave, trazo: COLOR.moradoMedio }
  };

  const ICONO_DE_MEDICION = {
    frecuencia_cardiaca: ['corazon', 'rosa'],
    saturacion_oxigeno: ['pulmones', 'morado'],
    movimientos_fetales: ['huellas', 'lila']
  };

  /** Un icono del sprite dentro de su círculo, como SVG de un solo nodo. */
  function icono(nombre, circulo, tamano) {
    const c = CIRCULOS[circulo];
    const t = tamano || 24;
    return {
      svg: '<svg xmlns="http://www.w3.org/2000/svg" width="' + t + '" height="' + t +
        '" viewBox="0 0 24 24"><circle cx="12" cy="12" r="12" fill="' + c.fondo + '"/>' +
        '<g transform="translate(4.8 4.8) scale(0.6)" fill="none" stroke="' + c.trazo +
        '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        ICONOS[nombre] + '</g></svg>',
      width: t,
      height: t
    };
  }

  /** La franja superior de las tarjetas: morado a rosa, como en la interfaz. */
  function franja(ancho, alto) {
    return {
      canvas: [{ type: 'rect', x: 0, y: 0, w: ancho, h: alto || 3.5,
        linearGradient: [COLOR.morado, COLOR.moradoMedio, COLOR.rosa] }]
    };
  }

  /** El punto de color del semáforo. Nunca va sin su nombre al lado. */
  function punto(codigo, radio) {
    const r = radio || 4;
    return {
      canvas: [{ type: 'ellipse', x: r, y: r + 2, r1: r, r2: r, color: SEMAFORO[codigo].punto }],
      width: r * 2 + 4
    };
  }

  function encabezadoDeSeccion(nombreIcono, circulo, titulo) {
    return {
      columns: [
        Object.assign(icono(nombreIcono, circulo, 22), { width: 22 }),
        { text: titulo, style: 'tituloSeccion', margin: [8, 3, 0, 0] }
      ],
      margin: [0, 16, 0, 7]
    };
  }

  /** Pares «etiqueta / valor» en filas de tres, con la línea lila a la izquierda. */
  function rejillaDeDatos(filas) {
    const cuerpo = [];
    for (let i = 0; i < filas.length; i += 3) {
      const fila = filas.slice(i, i + 3).map(function (f) {
        return {
          stack: [
            { text: f.etiqueta, style: 'etiqueta' },
            { text: f.valor, style: 'valor' }
          ],
          border: [true, false, false, false]
        };
      });
      while (fila.length < 3) {
        fila.push({ text: '', border: [false, false, false, false] });
      }
      cuerpo.push(fila);
    }
    return {
      table: { widths: ['*', '*', '*'], body: cuerpo },
      layout: {
        hLineWidth: function () { return 0; },
        vLineWidth: function () { return 2; },
        // El color de la línea va en el layout: en una celda, el borde
        // izquierdo de una es el derecho de la anterior y salía negro.
        vLineColor: function () { return COLOR.bordeFuerte; },
        paddingLeft: function () { return 8; },
        paddingRight: function () { return 6; },
        paddingTop: function () { return 3; },
        paddingBottom: function () { return 6; }
      }
    };
  }

  /** Una tarjeta de «Últimos valores registrados». */
  function tarjetaDeValor(valor, ancho) {
    const [nombreIcono, circulo] = ICONO_DE_MEDICION[valor.clave] || ['lectura', 'morado'];
    return {
      width: ancho,
      table: {
        widths: ['*'],
        body: [[{
          stack: [
            franja(ancho),
            {
              columns: [
                Object.assign(icono(nombreIcono, circulo, 26), { width: 26 }),
                {
                  stack: [
                    { text: valor.titulo, style: 'etiqueta' },
                    { text: valor.cifra, style: 'cifra' }
                  ].concat(valor.detalles.map(function (d) {
                    return { text: d, style: 'detalle' };
                  })),
                  margin: [8, 0, 0, 0]
                }
              ],
              margin: [10, 9, 10, 0]
            }
          ]
        }]]
      },
      layout: {
        hLineWidth: function () { return 0.8; },
        vLineWidth: function () { return 0.8; },
        hLineColor: function () { return COLOR.borde; },
        vLineColor: function () { return COLOR.borde; },
        paddingLeft: function () { return 0; },
        paddingRight: function () { return 0; },
        paddingTop: function () { return 0; },
        paddingBottom: function () { return 10; }
      }
    };
  }

  /** El bloque del semáforo, con los colores del aviso de la interfaz. */
  function bloqueDeClasificacion(c) {
    const tono = SEMAFORO[c.codigo] || SEMAFORO.NINGUNO;
    const titular = c.codigo
      ? { columns: [punto(c.codigo, 5.5), {
          text: [{ text: c.nombre, bold: true }, { text: ' — ' + c.mensaje }],
          fontSize: 12, color: tono.texto, margin: [4, 0, 0, 0] }] }
      : { text: c.mensaje, fontSize: 12, bold: true, color: tono.texto };
    return {
      table: {
        widths: ['*'],
        body: [[{
          stack: [
            titular,
            {
              text: c.filas.map(function (f) { return f.etiqueta + ' ' + f.valor; }).join('   ·   '),
              fontSize: 9, color: tono.texto, margin: [0, 5, 0, 0]
            }
          ],
          fillColor: tono.fondo
        }]]
      },
      layout: {
        hLineWidth: function () { return 0; },
        vLineWidth: function () { return 0; },
        paddingLeft: function () { return 12; },
        paddingRight: function () { return 12; },
        paddingTop: function () { return 10; },
        paddingBottom: function () { return 10; }
      }
    };
  }

  /** La tabla de lecturas: cabecera lila que se repite y filas que no se parten. */
  function tablaDeLecturas(l) {
    const cabecera = l.columnas.map(function (nombre) {
      return { text: nombre, style: 'cabeceraTabla' };
    });
    const filas = l.filasDeTabla.map(function (f) {
      const semaforo = f.codigo
        ? { columns: [punto(f.codigo, 3.5), { text: f.semaforo, margin: [2, 0, 0, 0] }] }
        : { text: f.semaforo };
      return f.celdas.map(function (c) { return { text: c }; }).concat([semaforo]);
    });
    return {
      table: {
        headerRows: 1,
        // La cabecera nunca queda sola al pie de una página.
        keepWithHeaderRows: 1,
        dontBreakRows: true,
        widths: ['*', 74, 48, 66, 40, 72],
        body: [cabecera].concat(filas)
      },
      fontSize: 9,
      layout: {
        hLineWidth: function (i) { return i === 0 ? 0 : 0.6; },
        vLineWidth: function () { return 0; },
        hLineColor: function () { return COLOR.moradoSuave; },
        fillColor: function (fila) {
          if (fila === 0) return COLOR.cabeceraTabla;
          return fila % 2 === 0 ? COLOR.filaPar : null;
        },
        paddingLeft: function () { return 6; },
        paddingRight: function () { return 4; },
        paddingTop: function () { return 5; },
        paddingBottom: function () { return 5; }
      }
    };
  }

  /** La definición de documento de pdfmake para un modelo de resumen. */
  function definicion(modelo) {
    const anchoTarjeta = (ANCHO_UTIL - 2 * 10) / 3;

    const contenido = [
      // El logotipo, sobre la banda de la primera página.
      {
        absolutePosition: { x: MARGEN.izquierda, y: 26 },
        stack: [
          { text: [{ text: 'Fetal', color: '#e9d5ff' }, { text: 'Alert', color: '#f0abfc' }],
            fontSize: 30, bold: true, characterSpacing: -0.5 },
          { text: modelo.lema, italics: true, fontSize: 12, color: '#e2e8f0', margin: [0, 1, 0, 0] }
        ]
      },

      // Título, embarazo y fechas.
      { text: modelo.titulo, style: 'tituloDocumento', margin: [0, BANDA + 18 - MARGEN.arriba, 0, 0] },
      { text: modelo.embarazo, fontSize: 11.5, color: '#334155', bold: true, margin: [0, 2, 0, 10] },
      {
        columns: modelo.fechas.map(function (f) {
          return {
            width: 'auto',
            table: { body: [[{ stack: [
              { text: f.etiqueta, style: 'etiqueta' },
              { text: f.valor, style: 'valor' }
            ], fillColor: COLOR.lilaSuave }]] },
            layout: {
              hLineWidth: function () { return 0; },
              vLineWidth: function () { return 0; },
              paddingLeft: function () { return 9; },
              paddingRight: function () { return 9; },
              paddingTop: function () { return 5; },
              paddingBottom: function () { return 5; }
            }
          };
        }),
        columnGap: 10
      }
    ];

    if (modelo.aviso) {
      contenido.push({
        table: { widths: ['*'], body: [[{
          text: modelo.aviso, fontSize: 9.5, color: COLOR.texto, fillColor: COLOR.rosaSuave,
          border: [true, false, false, false], borderColor: [COLOR.rosa, null, null, null]
        }]] },
        layout: {
          vLineWidth: function () { return 3; }, hLineWidth: function () { return 0; },
          paddingLeft: function () { return 9; }, paddingTop: function () { return 6; },
          paddingBottom: function () { return 6; }
        },
        margin: [0, 10, 0, 0]
      });
    }

    contenido.push({
      stack: [encabezadoDeSeccion('gestante', 'rosa', 'Embarazo'), rejillaDeDatos(modelo.datosEmbarazo)],
      unbreakable: true
    });

    contenido.push({
      stack: [
        encabezadoDeSeccion('reloj', 'morado', 'Últimos valores registrados'),
        { text: modelo.notaValores, style: 'nota', margin: [0, 0, 0, 7] },
        { columns: modelo.valores.map(function (v) { return tarjetaDeValor(v, anchoTarjeta); }), columnGap: 10 }
      ],
      unbreakable: true
    });

    contenido.push({
      stack: [
        encabezadoDeSeccion('lectura', 'lila', 'Clasificación de la lectura más reciente'),
        bloqueDeClasificacion(modelo.clasificacion),
        { text: modelo.clasificacion.nota, style: 'nota', margin: [0, 5, 0, 0] }
      ],
      unbreakable: true
    });

    const l = modelo.lecturas;
    contenido.push({
      stack: [
        encabezadoDeSeccion('calendario', 'morado', l.titulo),
        rejillaDeDatos(l.filas),
        { text: l.nota, style: 'nota', margin: [0, 2, 0, 7] }
      ],
      unbreakable: true,
      // Si el título de la tabla cae al pie de una página, pasa a la siguiente.
      headlineLevel: 1
    });
    contenido.push(tablaDeLecturas(l));

    contenido.push({
      stack: [
        { canvas: [{ type: 'line', x1: 0, y1: 0, x2: ANCHO_UTIL, y2: 0, lineWidth: 0.8, lineColor: COLOR.moradoSuave }],
          margin: [0, 14, 0, 6] }
      ].concat(modelo.pie.map(function (p) { return { text: p, style: 'nota' }; })),
      unbreakable: true
    });

    return {
      pageSize: 'LETTER',
      pageMargins: [MARGEN.izquierda, MARGEN.arriba, MARGEN.derecha, MARGEN.abajo],
      info: {
        title: 'FetalAlert — ' + modelo.titulo,
        author: 'FetalAlert',
        subject: 'Datos simulados con fines académicos',
        creator: 'FetalAlert'
      },
      defaultStyle: { font: 'Roboto', fontSize: 10, color: COLOR.texto, lineHeight: 1.15 },
      styles: {
        tituloDocumento: { fontSize: 20, bold: true, color: COLOR.titulo },
        tituloSeccion: { fontSize: 12.5, bold: true, color: COLOR.titulo },
        etiqueta: { fontSize: 8, bold: true, color: COLOR.secundario },
        valor: { fontSize: 10.5, bold: true, color: COLOR.titulo, margin: [0, 1, 0, 0] },
        cifra: { fontSize: 15, bold: true, color: COLOR.titulo, margin: [0, 1, 0, 2] },
        detalle: { fontSize: 8, color: COLOR.secundario },
        nota: { fontSize: 8.5, color: COLOR.secundario },
        cabeceraTabla: { fontSize: 8, bold: true, color: '#475569' }
      },
      // La banda de marca, solo en la primera página.
      background: function (pagina) {
        if (pagina !== 1) {
          return null;
        }
        return {
          canvas: [
            { type: 'rect', x: 0, y: 0, w: PAGINA.ancho, h: BANDA, linearGradient: COLOR.pizarra },
            { type: 'rect', x: 0, y: BANDA, w: PAGINA.ancho, h: 3,
              linearGradient: [COLOR.morado, COLOR.moradoMedio, COLOR.rosa] }
          ]
        };
      },
      // En las páginas siguientes, una cabecera discreta con la marca.
      header: function (pagina) {
        if (pagina === 1) {
          return null;
        }
        return {
          margin: [MARGEN.izquierda, 22, MARGEN.derecha, 0],
          stack: [
            {
              columns: [
                { text: [{ text: 'Fetal', color: COLOR.morado }, { text: 'Alert', color: COLOR.rosa }],
                  bold: true, fontSize: 11, width: 'auto' },
                { text: modelo.titulo, alignment: 'right', fontSize: 8.5, color: COLOR.secundario, margin: [0, 2, 0, 0] }
              ]
            },
            { canvas: [{ type: 'line', x1: 0, y1: 4, x2: ANCHO_UTIL, y2: 4, lineWidth: 0.8, lineColor: COLOR.moradoSuave }] }
          ]
        };
      },
      footer: function (pagina, paginas) {
        return {
          margin: [MARGEN.izquierda, 16, MARGEN.derecha, 0],
          columns: [
            { text: 'FetalAlert · Información simulada con fines académicos · Horas de Panamá',
              fontSize: 7.5, color: COLOR.tenue },
            { text: 'Página ' + pagina + ' de ' + paginas, alignment: 'right',
              fontSize: 7.5, color: COLOR.tenue, width: 80 }
          ]
        };
      },
      pageBreakBefore: function (nodo) {
        return nodo.headlineLevel === 1 &&
          nodo.startPosition.top > PAGINA.alto - MARGEN.abajo - 150;
      },
      content: contenido
    };
  }

  const publico = { definicion: definicion, PAGINA: PAGINA };
  global.FetalAlertResumenPdf = publico;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = publico;
  }
})(typeof window !== 'undefined' ? window : globalThis);
