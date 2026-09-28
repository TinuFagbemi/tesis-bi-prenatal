/* =========================================================================
   FetalAlert — Gráficas de la interfaz de la gestante (SCRUM-72)

   SVG propio, sin bibliotecas ni recursos externos: lo sirve el portal
   local, así que se dibuja sin internet cuando los datos ya están en la
   página. No guarda nada ni pide nada a la red.

   Lo que estas gráficas NO hacen, a propósito:

   * No inventan puntos ni los resumen. Cada marca es UNA lectura del
     embarazo tal como la entregó el adaptador (`series` de
     /adaptador/embarazos/{id}/monitoreo). Una lectura sin la variable no
     aparece; un cero sí. No hay promedios, medianas ni valores
     representativos.
   * No suavizan ni interpolan, y en frecuencia cardíaca y saturación no hay
     ninguna línea: es una dispersión de puntos.
   * No clasifican: no hay bandas de referencia ni colores con significado
     clínico. La API solo clasifica lecturas completas, y esa clasificación
     vive en otro bloque de la pantalla.

   Dos formas, según el dato:

   * **Frecuencia cardíaca y saturación** (`tipo: 'dispersion'`): las
     lecturas se agrupan por sesión (`id_sesion`). Cada sesión ocupa su
     propia columna, en orden cronológico, rotulada con su fecha y hora
     reales; dentro de la columna, sus lecturas se separan un poco en
     horizontal solo para que no se tapen --ese desplazamiento no representa
     tiempo--, y la altura es siempre el valor exacto. La gráfica crece a lo
     ancho con el número de sesiones y se recorre con scroll horizontal y dos
     flechas.
   * **Movimientos** (`tipo: 'barras'`): una barra por registro sobre un eje
     temporal proporcional: dos registros separados por un mes quedan más
     lejos que dos separados por un día.

   Se divide en dos partes. `modelo`, `modeloDispersion`, `agruparPorSesion`,
   `filtrarPorPeriodo`, `marcasDeValor`, `estadoFlechas` y `pasoDeFlecha` son
   puras --números de entrada, números de salida-- y se prueban con Node sin
   DOM. `dibujar` construye el SVG con createElementNS, sin marcado
   interpolado.
   ========================================================================= */

(function (global) {
  'use strict';

  const SVG = 'http://www.w3.org/2000/svg';
  const DIA_MS = 24 * 60 * 60 * 1000;

  // Geometría del lienzo, en unidades del viewBox. El SVG escala al ancho
  // de su contenedor manteniendo la proporción.
  // Cerca del ancho real de una tarjeta, para que el texto del eje no se
  // encoja al escalar.
  const ANCHO = 360;
  // La gráfica que ocupa toda la fila: el doble de ancho y la misma altura,
  // para que al escalar no crezca en alto ni encoja el texto.
  const ANCHO_AMPLIO = 720;
  const ALTO = 220;
  // Arriba, sitio para el valor escrito sobre cada marca; a la izquierda,
  // para los números del eje y la unidad en vertical.
  const MARGEN = { arriba: 24, derecha: 12, abajo: 28, izquierda: 48 };

  // Tamaño de las marcas: puntos visibles con borde blanco y barras de un
  // mismo grosor en toda la gráfica, ni muy gruesas ni muy finas.
  const RADIO_PUNTO = 4.5;
  const ANCHO_BARRA_MAX = 16;
  const ANCHO_BARRA_MIN = 6;
  // Separación mínima entre dos barras vecinas.
  const HUECO_BARRAS = 2;

  // Geometría de la dispersión, en píxeles: esta gráfica no se escala, se
  // desplaza. Cada sesión ocupa una columna de `anchoSesion`; el eje de
  // valores va aparte, fijo, para que no se pierda al desplazarse.
  //
  // 96 px por sesión y no 64: la zona visible de una tarjeta en escritorio
  // mide unos 476 px y ningún embarazo del dataset pasa de 6 sesiones de
  // signos maternos, así que con 64 (6 × 64 = 384) nunca había nada que
  // desplazar y las flechas quedaban siempre deshabilitadas.
  const DISPERSION = {
    anchoSesion: 96,
    anchoMinimo: 300,
    alto: 240,
    arriba: 20,
    abajo: 44,
    ejeValores: 48,
    // Separación máxima entre dos lecturas vecinas de una misma sesión, y
    // fracción de la columna que pueden ocupar entre todas.
    separacion: 12,
    ocupacion: 0.7
  };

  // Sesiones que avanza cada flecha, como máximo.
  const SESIONES_POR_FLECHA = 3;

  const PERIODOS = {
    todo: 'Todo el embarazo',
    ultimos30: 'Últimos 30 días con registros'
  };

  // -----------------------------------------------------------------------
  // Parte pura
  // -----------------------------------------------------------------------

  /**
   * Los puntos que caen en el período elegido.
   *
   * «Últimos 30 días con registros» se cuenta hacia atrás desde el último
   * registro de la serie, no desde hoy: un embarazo histórico sigue
   * mostrando sus datos, y no se sugiere que haya mediciones recientes.
   */
  function filtrarPorPeriodo(puntos, periodo) {
    if (periodo !== 'ultimos30' || puntos.length === 0) {
      return puntos.slice();
    }
    const ultimo = puntos[puntos.length - 1].instante;
    const desde = ultimo - 30 * DIA_MS;
    return puntos.filter(function (p) {
      return p.instante >= desde;
    });
  }

  /** Un paso «redondo» (1, 2, 2,5 o 5 por una potencia de diez). */
  function pasoRedondo(bruto) {
    if (!(bruto > 0)) {
      return 1;
    }
    const potencia = Math.pow(10, Math.floor(Math.log10(bruto)));
    const fraccion = bruto / potencia;
    const base = fraccion <= 1 ? 1 : fraccion <= 2 ? 2 : fraccion <= 2.5 ? 2.5 : fraccion <= 5 ? 5 : 10;
    return base * potencia;
  }

  /**
   * Marcas del eje de valores.
   *
   * Las barras empiezan siempre en cero, porque su altura es el valor. Las
   * líneas se ajustan al rango observado con un margen, para que una
   * variación pequeña no quede aplastada; los números del eje lo dicen. Por
   * debajo del valor más bajo queda siempre al menos un paso entero de la
   * rejilla, para que ningún punto quede pegado al eje de las fechas.
   */
  function marcasDeValor(valores, desdeCero) {
    let minimo = Math.min.apply(null, valores);
    let maximo = Math.max.apply(null, valores);
    if (desdeCero) {
      minimo = 0;
    }
    if (maximo === minimo) {
      const holgura = maximo === 0 ? 1 : Math.abs(maximo) * 0.05;
      maximo += holgura;
      if (!desdeCero) {
        minimo -= holgura;
      }
    }
    const paso = pasoRedondo((maximo - minimo) / 4);
    const inicio = desdeCero ? 0 : Math.floor(minimo / paso) * paso - paso;
    const fin = Math.ceil(maximo / paso) * paso;
    const marcas = [];
    for (let v = inicio; v <= fin + paso / 1000; v += paso) {
      marcas.push(Number(v.toFixed(6)));
    }
    return marcas;
  }

  /**
   * Posiciones de cada punto y de las marcas de los ejes, en el viewBox.
   *
   * `puntos` es [{instante (ms), valor (número)}] en orden cronológico.
   * Con un solo punto no hay escala temporal que proporcionar: se centra.
   * `ancho` es el del lienzo (ANCHO si no se indica).
   */
  function modelo(puntos, tipo, ancho) {
    const lienzo = ancho || ANCHO;
    const area = {
      x0: MARGEN.izquierda,
      x1: lienzo - MARGEN.derecha,
      y0: MARGEN.arriba,
      y1: ALTO - MARGEN.abajo
    };
    if (puntos.length === 0) {
      return { vacio: true, area: area, puntos: [], marcasY: [], marcasX: [] };
    }

    const barras = tipo === 'barras';
    const valores = puntos.map(function (p) { return p.valor; });
    const marcasY = marcasDeValor(valores, barras);
    const yMin = marcasY[0];
    const yMax = marcasY[marcasY.length - 1];
    const escalaY = function (v) {
      return area.y1 - ((v - yMin) / (yMax - yMin)) * (area.y1 - area.y0);
    };

    const t0 = puntos[0].instante;
    const t1 = puntos[puntos.length - 1].instante;
    // Las barras necesitan medio ancho de margen a cada lado; los puntos,
    // aire suficiente para no quedar pegados al eje de valores.
    const holguraX = barras ? ANCHO_BARRA_MAX / 2 + 4 : 24;
    const escalaX = function (t) {
      if (t1 === t0) {
        return (area.x0 + area.x1) / 2;
      }
      return area.x0 + holguraX + ((t - t0) / (t1 - t0)) * (area.x1 - area.x0 - 2 * holguraX);
    };

    const marcasX = [];
    if (t1 === t0) {
      marcasX.push({ instante: t0, x: escalaX(t0) });
    } else {
      const cuantas = lienzo === ANCHO ? 3 : 5;
      for (let i = 0; i <= cuantas; i += 1) {
        const t = t0 + ((t1 - t0) * i) / cuantas;
        marcasX.push({ instante: t, x: escalaX(t) });
      }
    }

    // Ancho de barra: uno solo para toda la gráfica, según cuántos registros
    // hay. Si dos registros están tan cerca que sus barras se tocarían, la
    // que va detrás se desplaza lo justo para quedar al lado (unos pocos
    // puntos del lienzo); la fecha exacta se consulta tocándola o en la tabla.
    const xs = puntos.map(function (p) { return escalaX(p.instante); });
    const anchoBarra = Math.max(ANCHO_BARRA_MIN,
      Math.min(ANCHO_BARRA_MAX, (0.6 * (area.x1 - area.x0 - 2 * holguraX)) / puntos.length));
    if (barras) {
      separarBarras(xs, anchoBarra, area.x0 + anchoBarra / 2 + 1, area.x1 - anchoBarra / 2 - 1);
    }

    return {
      vacio: false,
      unico: puntos.length === 1,
      area: area,
      anchoBarra: anchoBarra,
      marcasY: marcasY.map(function (v) { return { valor: v, y: escalaY(v) }; }),
      marcasX: marcasX,
      puntos: puntos.map(function (p, i) {
        return { x: xs[i], y: escalaY(p.valor), base: escalaY(yMin), ancho: barras ? anchoBarra : 0, dato: p };
      })
    };
  }

  /**
   * Corre lo mínimo los centros `xs` (en orden) para que barras de `ancho`
   * no se toquen, sin salir de [minimo, maximo]. Primero hacia la derecha
   * y, si la última se sale, de vuelta hacia la izquierda.
   */
  function separarBarras(xs, ancho, minimo, maximo) {
    const paso = ancho + HUECO_BARRAS;
    for (let i = 0; i !== xs.length; i += 1) {
      xs[i] = Math.max(xs[i], i === 0 ? minimo : xs[i - 1] + paso);
    }
    for (let i = xs.length - 1; i !== -1; i -= 1) {
      xs[i] = Math.min(xs[i], i === xs.length - 1 ? maximo : xs[i + 1] - paso);
    }
  }

  /** El valor tal como se escribe sobre su marca: «83», «97,5». */
  function textoCorto(valor) {
    return String(valor).replace('.', ',');
  }

  /**
   * ¿Caben los valores escritos sobre TODAS las marcas sin encimarse?
   *
   * Todo o nada: escribir el valor solo en algunas marcas haría pensar que
   * esas son distintas. Si no caben todos, el valor se consulta tocando la
   * marca o en la tabla. `marcas` es [{x, valor}] en orden, en unidades del
   * viewBox; el ancho de cada texto se estima con el cuerpo de la etiqueta.
   */
  function etiquetasCaben(marcas) {
    const ancho = function (v) { return textoCorto(v).length * 6.6 + 4; };
    for (let i = 1; i < marcas.length; i += 1) {
      const separacion = marcas[i].x - marcas[i - 1].x;
      if (separacion < (ancho(marcas[i].valor) + ancho(marcas[i - 1].valor)) / 2 + 2) {
        return false;
      }
    }
    return true;
  }

  /**
   * El valor numérico de un registro, o NaN si no lo hay.
   *
   * Los NUMERIC llegan como texto («83.00») y los conteos como enteros. Una
   * cadena vacía, null o undefined son ausencia y dan NaN --nunca cero--, y
   * `normalizar` los deja fuera. Un 0 real sigue siendo 0.
   */
  function aNumero(valor) {
    if (typeof valor === 'number') {
      return valor;
    }
    if (typeof valor === 'string' && valor.trim() !== '') {
      return Number(valor);
    }
    return NaN;
  }

  /** Solo los registros con instante y valor válidos, en orden cronológico. */
  function normalizar(puntos) {
    return (puntos || [])
      .map(function (p) {
        return Object.assign({}, p, { valor: aNumero(p.valor) });
      })
      .filter(function (p) {
        return Number.isFinite(p.valor) && Number.isFinite(p.instante);
      })
      .sort(function (a, b) {
        return a.instante - b.instante;
      });
  }

  /**
   * Las lecturas agrupadas por sesión, en orden cronológico de sesión.
   *
   * `puntos` llega ya normalizado (orden cronológico, sin ausencias). Cada
   * grupo conserva TODAS sus lecturas, en su orden, sin tocar sus valores. El
   * orden de las sesiones es el de su primera lectura. Una lectura sin sesión
   * conocida forma su propio grupo: no se junta con nadie.
   */
  function agruparPorSesion(puntos) {
    const grupos = [];
    const porSesion = new Map();
    puntos.forEach(function (p) {
      const conSesion = p.idSesion !== null && p.idSesion !== undefined;
      let grupo = conSesion ? porSesion.get(p.idSesion) : undefined;
      if (!grupo) {
        grupo = { idSesion: conSesion ? p.idSesion : null, instante: p.instante, lecturas: [] };
        grupos.push(grupo);
        if (conSesion) {
          porSesion.set(p.idSesion, grupo);
        }
      }
      grupo.lecturas.push(p);
    });
    return grupos;
  }

  /**
   * Posiciones de la dispersión: una columna por sesión y un punto por lectura.
   *
   * El ancho crece con el número de sesiones (nunca por debajo de
   * `anchoMinimo`); con pocas sesiones, las columnas se centran. Dentro de una
   * columna, las lecturas se reparten simétricamente alrededor del centro:
   * es solo maquetación. La `y` sale del valor real y de nada más.
   */
  function modeloDispersion(puntos) {
    const d = DISPERSION;
    const area = { y0: d.arriba, y1: d.alto - d.abajo };
    const grupos = agruparPorSesion(puntos);
    const ancho = Math.max(d.anchoMinimo, grupos.length * d.anchoSesion);
    if (grupos.length === 0) {
      return { vacio: true, ancho: ancho, alto: d.alto, area: area, grupos: [], puntos: [], marcasY: [] };
    }

    const marcasY = marcasDeValor(puntos.map(function (p) { return p.valor; }), false);
    const yMin = marcasY[0];
    const yMax = marcasY[marcasY.length - 1];
    const escalaY = function (v) {
      return area.y1 - ((v - yMin) / (yMax - yMin)) * (area.y1 - area.y0);
    };
    const inicio = (ancho - grupos.length * d.anchoSesion) / 2;
    const todos = [];

    const columnas = grupos.map(function (g, i) {
      const x0 = inicio + i * d.anchoSesion;
      const centro = x0 + d.anchoSesion / 2;
      const k = g.lecturas.length;
      const paso = k > 1 ? Math.min(d.separacion, (d.anchoSesion * d.ocupacion) / (k - 1)) : 0;
      const lecturas = g.lecturas.map(function (p, j) {
        const punto = {
          x: centro + (j - (k - 1) / 2) * paso,
          y: escalaY(p.valor),
          indice: j + 1,
          total: k,
          grupo: i,
          dato: p
        };
        todos.push(punto);
        return punto;
      });
      return { x0: x0, centro: centro, instante: g.instante, lecturas: lecturas };
    });

    return {
      vacio: false,
      ancho: ancho,
      alto: d.alto,
      area: area,
      grupos: columnas,
      puntos: todos,
      marcasY: marcasY.map(function (v) { return { valor: v, y: escalaY(v) }; })
    };
  }

  /** Qué flechas tienen adónde ir, con un píxel de tolerancia. */
  function estadoFlechas(desplazado, visible, total) {
    return {
      anterior: desplazado > 1,
      siguiente: desplazado + visible < total - 1
    };
  }

  /**
   * Cuánto avanza una flecha: sesiones enteras, hasta `SESIONES_POR_FLECHA` y
   * nunca más de lo que cabe a la vista menos una, para no saltarse ninguna.
   */
  function pasoDeFlecha(visible) {
    const caben = Math.floor(visible / DISPERSION.anchoSesion);
    return Math.max(1, Math.min(SESIONES_POR_FLECHA, caben - 1)) * DISPERSION.anchoSesion;
  }

  // -----------------------------------------------------------------------
  // Dibujo
  // -----------------------------------------------------------------------  // -----------------------------------------------------------------------
  // Dibujo
  // -----------------------------------------------------------------------

  function el(etiqueta, atributos, padre) {
    const nodo = document.createElementNS(SVG, etiqueta);
    Object.keys(atributos || {}).forEach(function (a) {
      nodo.setAttribute(a, String(atributos[a]));
    });
    if (padre) {
      padre.appendChild(nodo);
    }
    return nodo;
  }

  function html(etiqueta, clase, texto) {
    const nodo = document.createElement(etiqueta);
    if (clase) {
      nodo.className = clase;
    }
    if (texto !== undefined) {
      nodo.textContent = texto;
    }
    return nodo;
  }

  // Identificadores únicos de los degradados: puede haber varias gráficas
  // en la misma página.
  let secuencia = 0;

  /** Una barra con las esquinas superiores redondeadas. */
  function trazoBarra(x, y, ancho, alto) {
    const r = Math.min(6, ancho / 2, alto);
    return 'M' + x + ',' + (y + alto) +
      ' L' + x + ',' + (y + r) +
      ' Q' + x + ',' + y + ' ' + (x + r) + ',' + y +
      ' L' + (x + ancho - r) + ',' + y +
      ' Q' + (x + ancho) + ',' + y + ' ' + (x + ancho) + ',' + (y + r) +
      ' L' + (x + ancho) + ',' + (y + alto) + ' Z';
  }

  /** Un degradado vertical del color de la serie, de `arriba` a `abajo` de opacidad. */
  function degradado(defs, id, arriba, abajo) {
    const g = el('linearGradient', { id: id, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
    el('stop', { offset: '0%', 'stop-opacity': arriba, class: 'grafica-tono' }, g);
    el('stop', { offset: '100%', 'stop-opacity': abajo, class: 'grafica-tono' }, g);
  }

  function vaciar(nodo) {
    while (nodo.firstChild) {
      nodo.removeChild(nodo.firstChild);
    }
  }

  /**
   * Dibuja una serie en `contenedor`.
   *
   * config = {
   *   titulo, unidad, tipo: 'dispersion' | 'barras', clase (color de marca),
   *   puntos: [{ instante, valor, idSesion, textoValor, textoFecha, textoFechaCorta, semana }],
   *   formatoEje(ms) -> texto, formatoSesion(ms) -> { fecha, hora },
   *   periodo: 'todo' | 'ultimos30',
   *   ancha: true si la gráfica de barras ocupa toda la fila
   * }
   *
   * Los textos llegan ya formateados por app.js (español, America/Panama):
   * este módulo no decide cómo se escribe una fecha ni un valor.
   */
  function dibujar(contenedor, config) {
    if (config.tipo === 'dispersion') {
      dibujarDispersion(contenedor, config);
      return;
    }
    vaciar(contenedor);
    const periodo = PERIODOS[config.periodo] ? config.periodo : 'todo';
    const puntos = filtrarPorPeriodo(normalizar(config.puntos), periodo);
    const ancho = config.ancha ? ANCHO_AMPLIO : ANCHO;
    const m = modelo(puntos, config.tipo, ancho);

    if (m.vacio) {
      contenedor.appendChild(html('p', 'grafica-periodo', 'Sin registros de esta medición en este embarazo.'));
      return;
    }
    const primero = puntos[0];
    const ultimo = puntos[puntos.length - 1];
    const rango = m.unico ? primero.textoFechaCorta : primero.textoFechaCorta + ' – ' + ultimo.textoFechaCorta;
    const cuantos = puntos.length === 1 ? '1 registro' : puntos.length + ' registros';

    // Tres líneas cortas en lugar de una frase larga: el período en negrita,
    // las fechas que abarca y cuántos registros hay.
    contenedor.appendChild(resumenDelPeriodo(periodo, rango, puntos.length,
      puntos.length === 1 ? ' registro' : ' registros'));
    const descripcion = PERIODOS[periodo] + ': ' + rango + ' · ' + cuantos + ' · ' + config.unidad;

    const lienzo = html('div', 'grafica-lienzo');
    contenedor.appendChild(lienzo);

    const svg = el('svg', {
      viewBox: '0 0 ' + ancho + ' ' + ALTO,
      class: 'grafica-svg ' + (config.clase || ''),
      role: 'group',
      'aria-label': config.titulo + ', ' + descripcion +
        '. Usa las flechas para recorrer los registros.'
    }, lienzo);
    secuencia += 1;
    const idTono = 'grafica-tono-' + secuencia;
    const defs = el('defs', {}, svg);
    degradado(defs, idTono, 1, 0.6);

    // La unidad, en vertical junto al eje de valores.
    const unidad = el('text', {
      class: 'grafica-unidad', 'text-anchor': 'middle',
      transform: 'translate(11 ' + ((m.area.y0 + m.area.y1) / 2) + ') rotate(-90)'
    }, svg);
    unidad.textContent = config.unidad;

    // Rejilla y eje de valores.
    m.marcasY.forEach(function (marca) {
      el('line', { x1: m.area.x0, x2: m.area.x1, y1: marca.y, y2: marca.y, class: 'grafica-rejilla' }, svg);
      const texto = el('text', { x: m.area.x0 - 6, y: marca.y + 4, class: 'grafica-eje', 'text-anchor': 'end' }, svg);
      texto.textContent = String(marca.valor).replace('.', ',');
    });
    // Eje temporal.
    el('line', { x1: m.area.x0, x2: m.area.x1, y1: m.area.y1, y2: m.area.y1, class: 'grafica-base' }, svg);
    m.marcasX.forEach(function (marca, i) {
      const ancla = m.marcasX.length === 1 ? 'middle' : i === 0 ? 'start' : i === m.marcasX.length - 1 ? 'end' : 'middle';
      const texto = el('text', { x: marca.x, y: ALTO - 9, class: 'grafica-eje', 'text-anchor': ancla }, svg);
      texto.textContent = config.formatoEje(marca.instante);
    });

    const aviso = html('div', 'grafica-aviso');
    aviso.setAttribute('role', 'status');
    aviso.setAttribute('aria-live', 'polite');
    aviso.hidden = true;
    lienzo.appendChild(aviso);

    // Lo que se dice de una marca: fecha, valor y semana.
    function partes(dato) {
      return [dato.textoFecha, dato.textoValor,
        dato.semana === null || dato.semana === undefined ? '' : 'semana ' + dato.semana]
        .filter(Boolean);
    }

    const conValores = etiquetasCaben(m.puntos.map(function (p) { return { x: p.x, valor: p.dato.valor }; }));
    const marcas = m.puntos.map(function (p, i) {
      const grupo = el('g', {
        class: 'grafica-punto',
        tabindex: i === m.puntos.length - 1 ? 0 : -1,
        role: 'img',
        'aria-label': partes(p.dato).join(', ')
      }, svg);
      const alto = Math.max(1.5, p.base - p.y);
      el('path', {
        class: 'grafica-barra',
        fill: 'url(#' + idTono + ')',
        d: trazoBarra(p.x - p.ancho / 2, p.base - alto, p.ancho, alto)
      }, grupo);
      if (conValores) {
        const valor = el('text', {
          class: 'grafica-valor', 'text-anchor': 'middle', x: p.x,
          y: Math.min(p.y, p.base - 1.5) - 8,
          'aria-hidden': 'true'
        }, grupo);
        valor.textContent = textoCorto(p.dato.valor);
      }
      // Zona táctil amplia e invisible.
      el('circle', { class: 'grafica-zona', cx: p.x, cy: (p.y + p.base) / 2, r: 11 }, grupo);
      return grupo;
    });

    function mostrar(i) {
      const p = m.puntos[i];
      marcas.forEach(function (g, j) {
        g.setAttribute('tabindex', j === i ? '0' : '-1');
        g.classList.toggle('activa', j === i);
      });
      aviso.textContent = partes(p.dato).join(' · ');
      aviso.hidden = false;
      // Posición relativa al lienzo, en porcentaje del viewBox.
      aviso.style.left = Math.min(78, Math.max(2, (p.x / ancho) * 100 - 10)) + '%';
      aviso.style.top = Math.max(0, (p.y / ALTO) * 100 - 26) + '%';
    }

    marcas.forEach(function (g, i) {
      g.addEventListener('focus', function () { mostrar(i); });
      g.addEventListener('mouseenter', function () { mostrar(i); });
      g.addEventListener('pointerdown', function () { g.focus(); mostrar(i); });
      g.addEventListener('blur', function () { g.classList.remove('activa'); aviso.hidden = true; });
      g.addEventListener('keydown', function (evento) {
        let destino = null;
        if (evento.key === 'ArrowRight' || evento.key === 'ArrowUp') destino = Math.min(marcas.length - 1, i + 1);
        if (evento.key === 'ArrowLeft' || evento.key === 'ArrowDown') destino = Math.max(0, i - 1);
        if (evento.key === 'Home') destino = 0;
        if (evento.key === 'End') destino = marcas.length - 1;
        if (destino !== null) {
          evento.preventDefault();
          marcas[destino].focus();
        }
      });
    });
    svg.addEventListener('mouseleave', function () {
      if (!svg.contains(document.activeElement)) {
        aviso.hidden = true;
      }
    });

    if (m.unico) {
      contenedor.appendChild(html('p', 'grafica-nota',
        'Solo hay un registro en este período: se muestra como una barra, sin línea.'));
    }

    // Alternativa textual: los mismos registros, en tabla.
    contenedor.appendChild(tablaAlternativa(
      ['Fecha', config.titulo + ' (' + config.unidad + ')', 'Semana'],
      puntos.slice().reverse().map(function (p) {
        return [p.textoFecha, p.textoValor, textoSemana(p.semana)];
      })
    ));
  }

  function textoSemana(semana) {
    return semana === null || semana === undefined ? '—' : String(semana);
  }

  /** El resumen de tres líneas sobre cada gráfica: período, fechas y conteo. */
  function resumenDelPeriodo(periodo, rango, cantidad, sustantivo, extra) {
    const resumen = html('div', 'grafica-periodo');
    resumen.appendChild(html('p', 'grafica-periodo-nombre', PERIODOS[periodo]));
    resumen.appendChild(html('p', 'grafica-rango', rango));
    const conteo = html('p', 'grafica-conteo');
    conteo.appendChild(html('strong', null, String(cantidad)));
    conteo.appendChild(document.createTextNode(sustantivo + (extra || '')));
    resumen.appendChild(conteo);
    return resumen;
  }

  /** Tabla desplegable con los mismos datos de la gráfica. */
  function tablaAlternativa(columnas, filas) {
    const detalle = html('details', 'grafica-tabla');
    detalle.appendChild(html('summary', null, 'Ver los datos en tabla'));
    const tabla = html('table', 'tabla-lecturas tabla-compacta');
    const cabecera = html('tr');
    columnas.forEach(function (t) {
      const th = html('th', null, t);
      th.setAttribute('scope', 'col');
      cabecera.appendChild(th);
    });
    const thead = html('thead');
    thead.appendChild(cabecera);
    tabla.appendChild(thead);
    const cuerpo = html('tbody');
    filas.forEach(function (celdas) {
      const fila = html('tr');
      celdas.forEach(function (c) { fila.appendChild(html('td', null, c)); });
      cuerpo.appendChild(fila);
    });
    tabla.appendChild(cuerpo);
    detalle.appendChild(tabla);
    return detalle;
  }

  // Ajustes pendientes de cada zona desplazable: recalcular las flechas y, la
  // primera vez que tiene ancho, ir a las sesiones más recientes. Un solo
  // observador para todas; una zona que sale del documento deja de
  // observarse.
  const ajustes = new WeakMap();
  const observador = typeof ResizeObserver === 'function'
    ? new ResizeObserver(function (entradas) {
      entradas.forEach(function (entrada) {
        if (!entrada.target.isConnected) {
          observador.unobserve(entrada.target);
          return;
        }
        const ajustar = ajustes.get(entrada.target);
        if (ajustar) {
          ajustar();
        }
      });
    })
    : null;

  function movimientoReducido() {
    return typeof global.matchMedia === 'function' &&
      global.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  /**
   * Frecuencia cardíaca o saturación: una dispersión de lecturas agrupadas
   * por sesión, desplazable en horizontal. Ver la cabecera del módulo.
   */
  function dibujarDispersion(contenedor, config) {
    vaciar(contenedor);
    const periodo = PERIODOS[config.periodo] ? config.periodo : 'todo';
    // Primero el período sobre las lecturas reales; después, las sesiones.
    const puntos = filtrarPorPeriodo(normalizar(config.puntos), periodo);
    const m = modeloDispersion(puntos);

    if (m.vacio) {
      contenedor.appendChild(html('p', 'grafica-periodo', 'Sin registros de esta medición en este embarazo.'));
      return;
    }
    const sesiones = m.grupos.length;
    const primero = puntos[0];
    const ultimo = puntos[puntos.length - 1];
    const rango = primero.textoFechaCorta === ultimo.textoFechaCorta
      ? primero.textoFechaCorta
      : primero.textoFechaCorta + ' – ' + ultimo.textoFechaCorta;
    const deLecturas = ' (' + puntos.length + (puntos.length === 1 ? ' lectura)' : ' lecturas)');
    contenedor.appendChild(resumenDelPeriodo(periodo, rango, sesiones,
      sesiones === 1 ? ' sesión' : ' sesiones', deLecturas));

    // Flechas: complemento del scroll, no su sustituto. La nota dice por qué
    // están o no disponibles: con pocas sesiones todo cabe.
    const navegacion = html('div', 'grafica-navegacion');
    const nota = html('span', 'grafica-navegacion-nota');
    navegacion.appendChild(nota);
    const anterior = html('button', 'grafica-flecha', '‹');
    anterior.type = 'button';
    anterior.setAttribute('aria-label', 'Ver sesiones anteriores de ' + config.titulo.toLowerCase());
    const siguiente = html('button', 'grafica-flecha', '›');
    siguiente.type = 'button';
    siguiente.setAttribute('aria-label', 'Ver sesiones siguientes de ' + config.titulo.toLowerCase());
    navegacion.appendChild(anterior);
    navegacion.appendChild(siguiente);
    contenedor.appendChild(navegacion);

    const lienzo = html('div', 'grafica-lienzo grafica-lienzo-dispersion');
    contenedor.appendChild(lienzo);

    // Eje de valores, fijo a la izquierda: no se desplaza con las sesiones.
    const eje = el('svg', {
      class: 'grafica-eje-fijo',
      width: DISPERSION.ejeValores,
      height: m.alto,
      viewBox: '0 0 ' + DISPERSION.ejeValores + ' ' + m.alto,
      'aria-hidden': 'true'
    }, lienzo);
    const unidad = el('text', {
      class: 'grafica-unidad', 'text-anchor': 'middle',
      transform: 'translate(11 ' + ((m.area.y0 + m.area.y1) / 2) + ') rotate(-90)'
    }, eje);
    unidad.textContent = config.unidad;
    m.marcasY.forEach(function (marca) {
      const texto = el('text', {
        x: DISPERSION.ejeValores - 6, y: marca.y + 4, class: 'grafica-eje', 'text-anchor': 'end'
      }, eje);
      texto.textContent = String(marca.valor).replace('.', ',');
    });

    const desplazable = html('div', 'grafica-desplazable');
    desplazable.setAttribute('tabindex', '0');
    desplazable.setAttribute('role', 'region');
    desplazable.setAttribute('aria-label', config.titulo +
      ': una columna por sesión. Desplázate en horizontal para ver todas.');
    lienzo.appendChild(desplazable);
    const contenido = html('div', 'grafica-contenido');
    contenido.style.width = m.ancho + 'px';
    desplazable.appendChild(contenido);

    const svg = el('svg', {
      class: 'grafica-svg-dispersion ' + (config.clase || ''),
      width: m.ancho,
      height: m.alto,
      viewBox: '0 0 ' + m.ancho + ' ' + m.alto,
      role: 'group',
      'aria-label': config.titulo + ', ' + PERIODOS[periodo] + ': ' + rango + ' · ' +
        sesiones + (sesiones === 1 ? ' sesión' : ' sesiones') + deLecturas + ' · ' +
        config.unidad + '. Usa las flechas del teclado para recorrer las lecturas.'
    }, contenido);

    // Una franja por sesión, alternando el tono: es lo que agrupa a la vista.
    const franjas = m.grupos.map(function (g, i) {
      return el('rect', {
        class: 'grafica-franja' + (i % 2 ? ' impar' : ''),
        x: g.x0 + 2, y: m.area.y0 - 8, width: DISPERSION.anchoSesion - 4,
        height: m.area.y1 - m.area.y0 + 16, rx: 8
      }, svg);
    });
    m.marcasY.forEach(function (marca) {
      el('line', { x1: 0, x2: m.ancho, y1: marca.y, y2: marca.y, class: 'grafica-rejilla' }, svg);
    });
    el('line', { x1: 0, x2: m.ancho, y1: m.area.y1, y2: m.area.y1, class: 'grafica-base' }, svg);

    // Rótulo real de cada sesión: su fecha y, debajo, su hora.
    m.grupos.forEach(function (g) {
      const rotulo = config.formatoSesion(g.instante);
      const fecha = el('text', {
        x: g.centro, y: m.alto - 24, class: 'grafica-eje', 'text-anchor': 'middle'
      }, svg);
      fecha.textContent = rotulo.fecha;
      const hora = el('text', {
        x: g.centro, y: m.alto - 10, class: 'grafica-eje grafica-hora', 'text-anchor': 'middle'
      }, svg);
      hora.textContent = rotulo.hora;
    });

    const aviso = html('div', 'grafica-aviso');
    aviso.setAttribute('role', 'status');
    aviso.setAttribute('aria-live', 'polite');
    aviso.hidden = true;
    contenido.appendChild(aviso);

    function partes(p) {
      return [
        p.dato.textoValor,
        p.dato.textoFecha,
        p.total === 1 ? 'Única lectura de esta sesión' : 'Lectura ' + p.indice + ' de ' + p.total + ' de esta sesión',
        p.dato.semana === null || p.dato.semana === undefined ? '' : 'semana ' + p.dato.semana
      ].filter(Boolean);
    }

    const marcas = m.puntos.map(function (p, i) {
      const grupo = el('g', {
        class: 'grafica-punto',
        tabindex: i === m.puntos.length - 1 ? 0 : -1,
        role: 'img',
        'aria-label': config.titulo + ': ' + partes(p).join(', ')
      }, svg);
      el('circle', { class: 'grafica-marca', cx: p.x, cy: p.y, r: RADIO_PUNTO }, grupo);
      el('circle', { class: 'grafica-zona', cx: p.x, cy: p.y, r: 9 }, grupo);
      return grupo;
    });

    function mostrar(i) {
      const p = m.puntos[i];
      marcas.forEach(function (g, j) {
        g.setAttribute('tabindex', j === i ? '0' : '-1');
        g.classList.toggle('activa', j === i);
      });
      franjas.forEach(function (f, j) {
        f.classList.toggle('activa', j === p.grupo);
      });
      aviso.textContent = partes(p).join(' · ');
      aviso.hidden = false;
      // En píxeles del contenido, que es lo que se desplaza con el aviso.
      aviso.style.left = Math.max(0, Math.min(m.ancho - 180, p.x - 90)) + 'px';
      aviso.style.top = (p.y < 80 ? p.y + 12 : p.y - 64) + 'px';
    }

    function ocultar() {
      aviso.hidden = true;
      franjas.forEach(function (f) { f.classList.remove('activa'); });
    }

    marcas.forEach(function (g, i) {
      g.addEventListener('focus', function () { mostrar(i); });
      g.addEventListener('mouseenter', function () { mostrar(i); });
      // Sin mover el scroll: un toque que empieza sobre un punto puede ser
      // el inicio de un deslizamiento.
      g.addEventListener('pointerdown', function () { g.focus({ preventScroll: true }); mostrar(i); });
      g.addEventListener('blur', function () { g.classList.remove('activa'); ocultar(); });
      g.addEventListener('keydown', function (evento) {
        let destino = null;
        if (evento.key === 'ArrowRight' || evento.key === 'ArrowUp') destino = Math.min(marcas.length - 1, i + 1);
        if (evento.key === 'ArrowLeft' || evento.key === 'ArrowDown') destino = Math.max(0, i - 1);
        if (evento.key === 'Home') destino = 0;
        if (evento.key === 'End') destino = marcas.length - 1;
        if (destino !== null) {
          evento.preventDefault();
          // El foco lleva la zona desplazable hasta el punto.
          marcas[destino].focus();
        }
      });
    });
    svg.addEventListener('mouseleave', function () {
      if (!svg.contains(document.activeElement)) {
        ocultar();
      }
    });

    // Barra propia, solo donde el navegador no dibuja la suya: las barras
    // superpuestas de los móviles no ocupan espacio y no se ven. Refleja
    // `scrollLeft` y se puede arrastrar o tocar; la zona desplazable y las
    // flechas siguen siendo los controles accesibles, así que es aria-hidden.
    const riel = html('div', 'grafica-riel');
    riel.setAttribute('aria-hidden', 'true');
    riel.hidden = true;
    const guia = html('div', 'grafica-riel-guia');
    riel.appendChild(guia);
    contenedor.appendChild(riel);
    riel.addEventListener('pointerdown', function (evento) {
      const caja = riel.getBoundingClientRect();
      const escala = desplazable.scrollWidth / caja.width;
      if (evento.target !== guia) {
        // Un toque en la pista centra la vista en ese punto.
        desplazable.scrollLeft = (evento.clientX - caja.left) * escala - desplazable.clientWidth / 2;
      }
      const desde = evento.clientX;
      const base = desplazable.scrollLeft;
      function arrastrar(movimiento) {
        desplazable.scrollLeft = base + (movimiento.clientX - desde) * escala;
      }
      function soltar() {
        riel.removeEventListener('pointermove', arrastrar);
        riel.removeEventListener('pointerup', soltar);
        riel.removeEventListener('pointercancel', soltar);
      }
      if (typeof riel.setPointerCapture === 'function') {
        riel.setPointerCapture(evento.pointerId);
      }
      riel.addEventListener('pointermove', arrastrar);
      riel.addEventListener('pointerup', soltar);
      riel.addEventListener('pointercancel', soltar);
      evento.preventDefault();
    });

    function actualizarFlechas() {
      const visible = desplazable.clientWidth;
      const total = desplazable.scrollWidth;
      const estado = estadoFlechas(desplazable.scrollLeft, visible, total);
      anterior.disabled = !estado.anterior;
      siguiente.disabled = !estado.siguiente;
      const desborda = estado.anterior || estado.siguiente;
      nota.textContent = desborda
        ? 'Desliza o usa las flechas para ver más sesiones.'
        : 'Todas las sesiones están a la vista.';
      const barraNativa = desplazable.offsetHeight - desplazable.clientHeight;
      riel.hidden = !desborda || barraNativa > 0;
      if (!riel.hidden) {
        guia.style.width = (visible / total) * 100 + '%';
        guia.style.left = (desplazable.scrollLeft / total) * 100 + '%';
      }
    }
    function mover(sentido) {
      desplazable.scrollBy({
        left: sentido * pasoDeFlecha(desplazable.clientWidth),
        behavior: movimientoReducido() ? 'auto' : 'smooth'
      });
    }
    anterior.addEventListener('click', function () { mover(-1); });
    siguiente.addEventListener('click', function () { mover(1); });
    desplazable.addEventListener('scroll', actualizarFlechas);

    // Se empieza por las sesiones más recientes. Si la gráfica aún no tiene
    // ancho (vista oculta), se hace en cuanto lo tenga.
    let alFinal = false;
    function ajustar() {
      if (!alFinal && desplazable.clientWidth > 0) {
        desplazable.scrollLeft = desplazable.scrollWidth;
        alFinal = true;
      }
      actualizarFlechas();
    }
    ajustes.set(desplazable, ajustar);
    if (observador) {
      observador.observe(desplazable);
    }
    ajustar();

    contenedor.appendChild(html('p', 'grafica-nota',
      'Cada punto es una lectura. Las lecturas de una misma sesión comparten ' +
      'columna; su separación dentro de la columna no indica tiempo.'));

    // Alternativa textual: todas las lecturas, de la más reciente a la más
    // antigua, con su lugar dentro de la sesión.
    contenedor.appendChild(tablaAlternativa(
      ['Fecha y hora', config.titulo + ' (' + config.unidad + ')', 'Lectura de la sesión', 'Semana'],
      m.puntos.slice().reverse().map(function (p) {
        return [p.dato.textoFecha, p.dato.textoValor, p.indice + ' de ' + p.total, textoSemana(p.dato.semana)];
      })
    ));
  }

  /** Un icono del juego de la página (`<symbol id>` del HTML), para `app.js`. */
  function icono(nombre) {
    const svg = el('svg', { class: 'icono', 'aria-hidden': 'true', focusable: 'false' });
    el('use', { href: '#' + nombre }, svg);
    return svg;
  }

  global.FetalAlertGraficas = {
    icono: icono,
    PERIODOS: PERIODOS,
    aNumero: aNumero,
    normalizar: normalizar,
    filtrarPorPeriodo: filtrarPorPeriodo,
    agruparPorSesion: agruparPorSesion,
    modeloDispersion: modeloDispersion,
    estadoFlechas: estadoFlechas,
    pasoDeFlecha: pasoDeFlecha,
    DISPERSION: DISPERSION,
    marcasDeValor: marcasDeValor,
    etiquetasCaben: etiquetasCaben,
    modelo: modelo,
    dibujar: dibujar
  };
})(typeof window !== 'undefined' ? window : globalThis);
