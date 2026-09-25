/* =========================================================================
   FetalAlert — Gráficas de la interfaz de la gestante (SCRUM-72)

   SVG propio, sin bibliotecas ni recursos externos: lo sirve el portal
   local, así que se dibuja sin internet cuando los datos ya están en la
   página. No guarda nada ni pide nada a la red.

   Lo que estas gráficas NO hacen, a propósito:

   * No inventan puntos. Cada marca es una lectura del embarazo tal como la
     entregó el adaptador (`series` de /adaptador/embarazos/{id}/monitoreo).
     Una lectura sin la variable no aparece; un cero sí.
   * No suavizan ni interpolan: la línea une observaciones consecutivas con
     segmentos rectos y no representa una medición continua.
   * No agregan: los movimientos se dibujan como una barra por registro,
     sin sumar lecturas ni sesiones por día.
   * No clasifican: no hay bandas de referencia ni colores con significado
     clínico. La API solo clasifica lecturas completas, y esa clasificación
     vive en otro bloque de la pantalla.
   * El eje temporal es proporcional: dos registros separados por un mes
     quedan más lejos que dos separados por un día.

   Se divide en dos partes. `modelo`, `filtrarPorPeriodo` y `marcasDeValor`
   son puras --números de entrada, números de salida-- y se prueban con Node
   sin DOM. `dibujar` construye el SVG con createElementNS, sin marcado
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
  const ALTO = 210;
  const MARGEN = { arriba: 12, derecha: 10, abajo: 28, izquierda: 34 };

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
   * variación pequeña no quede aplastada; los números del eje lo dicen.
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
    const inicio = desdeCero ? 0 : Math.floor(minimo / paso) * paso;
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
   */
  function modelo(puntos, tipo) {
    const area = {
      x0: MARGEN.izquierda,
      x1: ANCHO - MARGEN.derecha,
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
    // Las barras necesitan medio ancho de margen a cada lado.
    const holguraX = barras ? 10 : 6;
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
      const cuantas = 3;
      for (let i = 0; i <= cuantas; i += 1) {
        const t = t0 + ((t1 - t0) * i) / cuantas;
        marcasX.push({ instante: t, x: escalaX(t) });
      }
    }

    // Ancho de barra: el hueco mínimo entre registros, acotado, para que dos
    // registros cercanos no se tapen.
    let anchoBarra = 14;
    if (barras && puntos.length > 1) {
      let hueco = Infinity;
      for (let i = 1; i < puntos.length; i += 1) {
        hueco = Math.min(hueco, escalaX(puntos[i].instante) - escalaX(puntos[i - 1].instante));
      }
      anchoBarra = Math.max(3, Math.min(14, hueco * 0.8));
    }

    return {
      vacio: false,
      unico: puntos.length === 1,
      area: area,
      anchoBarra: anchoBarra,
      marcasY: marcasY.map(function (v) { return { valor: v, y: escalaY(v) }; }),
      marcasX: marcasX,
      puntos: puntos.map(function (p) {
        return { x: escalaX(p.instante), y: escalaY(p.valor), base: escalaY(yMin), dato: p };
      })
    };
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

  // -----------------------------------------------------------------------
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

  function vaciar(nodo) {
    while (nodo.firstChild) {
      nodo.removeChild(nodo.firstChild);
    }
  }

  /**
   * Dibuja una serie en `contenedor`.
   *
   * config = {
   *   titulo, unidad, tipo: 'linea' | 'barras', clase (color de marca),
   *   puntos: [{ instante, valor, textoValor, textoFecha, textoFechaCorta, semana }],
   *   formatoEje(ms) -> texto, periodo: 'todo' | 'ultimos30'
   * }
   *
   * Los textos llegan ya formateados por app.js (español, America/Panama):
   * este módulo no decide cómo se escribe una fecha ni un valor.
   */
  function dibujar(contenedor, config) {
    vaciar(contenedor);
    const periodo = PERIODOS[config.periodo] ? config.periodo : 'todo';
    const puntos = filtrarPorPeriodo(normalizar(config.puntos), periodo);
    const m = modelo(puntos, config.tipo);

    const resumen = html('p', 'grafica-periodo');
    if (m.vacio) {
      resumen.textContent = 'Sin registros de esta medición en este embarazo.';
      contenedor.appendChild(resumen);
      return;
    }
    const primero = puntos[0];
    const ultimo = puntos[puntos.length - 1];
    resumen.textContent =
      PERIODOS[periodo] + ': ' +
      (m.unico ? primero.textoFechaCorta : primero.textoFechaCorta + ' – ' + ultimo.textoFechaCorta) +
      ' · ' + (puntos.length === 1 ? '1 registro' : puntos.length + ' registros') +
      ' · ' + config.unidad;
    contenedor.appendChild(resumen);

    const lienzo = html('div', 'grafica-lienzo');
    contenedor.appendChild(lienzo);

    const svg = el('svg', {
      viewBox: '0 0 ' + ANCHO + ' ' + ALTO,
      class: 'grafica-svg ' + (config.clase || ''),
      role: 'group',
      'aria-label': config.titulo + ', ' + resumen.textContent +
        '. Usa las flechas para recorrer los registros.'
    }, lienzo);

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

    // Línea: segmentos rectos entre observaciones consecutivas.
    if (config.tipo !== 'barras' && m.puntos.length > 1) {
      el('polyline', {
        class: 'grafica-linea',
        points: m.puntos.map(function (p) { return p.x.toFixed(1) + ',' + p.y.toFixed(1); }).join(' ')
      }, svg);
    }

    const aviso = html('div', 'grafica-aviso');
    aviso.setAttribute('role', 'status');
    aviso.setAttribute('aria-live', 'polite');
    aviso.hidden = true;
    lienzo.appendChild(aviso);

    const marcas = m.puntos.map(function (p, i) {
      const grupo = el('g', {
        class: 'grafica-punto',
        tabindex: i === m.puntos.length - 1 ? 0 : -1,
        role: 'img',
        'aria-label': p.dato.textoFecha + ': ' + p.dato.textoValor +
          (p.dato.semana === null || p.dato.semana === undefined ? '' : ', semana ' + p.dato.semana)
      }, svg);
      if (config.tipo === 'barras') {
        el('rect', {
          class: 'grafica-barra',
          x: p.x - m.anchoBarra / 2,
          y: Math.min(p.y, p.base - 1.5),
          width: m.anchoBarra,
          height: Math.max(1.5, p.base - p.y),
          rx: Math.min(4, m.anchoBarra / 3)
        }, grupo);
      } else {
        el('circle', { class: 'grafica-marca', cx: p.x, cy: p.y, r: 3.5 }, grupo);
      }
      // Zona táctil amplia e invisible.
      el('circle', { class: 'grafica-zona', cx: p.x, cy: config.tipo === 'barras' ? (p.y + p.base) / 2 : p.y, r: 11 }, grupo);
      return grupo;
    });

    function mostrar(i) {
      const p = m.puntos[i];
      marcas.forEach(function (g, j) {
        g.setAttribute('tabindex', j === i ? '0' : '-1');
        g.classList.toggle('activa', j === i);
      });
      aviso.textContent = p.dato.textoFecha + ' · ' + p.dato.textoValor +
        (p.dato.semana === null || p.dato.semana === undefined ? '' : ' · semana ' + p.dato.semana);
      aviso.hidden = false;
      // Posición relativa al lienzo, en porcentaje del viewBox.
      aviso.style.left = Math.min(78, Math.max(2, (p.x / ANCHO) * 100 - 10)) + '%';
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

    // Varias lecturas de una misma sesión, separadas por minutos, quedan casi
    // en la misma posición del eje: se dice, para que un tramo casi vertical
    // no se lea como un cambio brusco entre días.
    const cercanas = puntos.some(function (p, i) {
      return i !== 0 && p.instante - puntos[i - 1].instante < 60 * 60 * 1000;
    });
    if (cercanas && config.tipo !== 'barras') {
      contenedor.appendChild(html('p', 'grafica-nota',
        'Las lecturas tomadas con minutos de diferencia aparecen casi en la misma fecha.'));
    }

    if (m.unico) {
      contenedor.appendChild(html('p', 'grafica-nota',
        'Solo hay un registro en este período: se muestra como ' +
        (config.tipo === 'barras' ? 'una barra' : 'un punto') + ', sin línea.'));
    }

    // Alternativa textual: los mismos registros, en tabla.
    const detalle = html('details', 'grafica-tabla');
    detalle.appendChild(html('summary', null, 'Ver los datos en tabla'));
    const tabla = html('table', 'tabla-lecturas tabla-compacta');
    const cabecera = html('tr');
    ['Fecha', config.titulo + ' (' + config.unidad + ')', 'Semana'].forEach(function (t) {
      const th = html('th', null, t);
      th.setAttribute('scope', 'col');
      cabecera.appendChild(th);
    });
    const thead = html('thead');
    thead.appendChild(cabecera);
    tabla.appendChild(thead);
    const cuerpo = html('tbody');
    puntos.slice().reverse().forEach(function (p) {
      const fila = html('tr');
      fila.appendChild(html('td', null, p.textoFecha));
      fila.appendChild(html('td', null, p.textoValor));
      fila.appendChild(html('td', null, p.semana === null || p.semana === undefined ? '—' : String(p.semana)));
      cuerpo.appendChild(fila);
    });
    tabla.appendChild(cuerpo);
    detalle.appendChild(tabla);
    contenedor.appendChild(detalle);
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
    marcasDeValor: marcasDeValor,
    modelo: modelo,
    dibujar: dibujar
  };
})(typeof window !== 'undefined' ? window : globalThis);
