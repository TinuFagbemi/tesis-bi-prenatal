/* Validación en Chrome real (PR #22): descargas del PDF y su invalidación.

   - Chrome headless con un perfil PROPIO (no el de la usuaria), conducido por
     CDP con el WebSocket de Node. Clics con Input.dispatchMouseEvent.
   - La descarga se observa en el navegador (Browser.downloadWillBegin /
     downloadProgress) y el archivo se lee después con pypdf.
   - pdfmake es el real del portal. Lo instrumentado se dice en cada caso:
       [red]  el servidor de prueba retiene una respuesta hasta soltarla;
       [gen]  en la página se retiene el evento 'end' del stream de pdfmake
              (el PDF ya está generado en memoria) hasta soltarlo.
   - Esperas por condición (sondeo cada 50 ms con tope), no pausas fijas.

   Uso: node conductor.js <url base> <dir salida> [solo-normal]
   (variable CHROME con la ruta de chrome.exe si no está en la habitual)

   El perfil de Chrome y las descargas quedan en <dir salida>: elíjase uno
   FUERA del repositorio.
*/
'use strict';
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const BASE = process.argv[2];
const SALIDA = path.resolve(process.argv[3]);
const SOLO_NORMAL = process.argv[4] === 'solo-normal';
const CHROME = process.env.CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PUERTO_CDP = SOLO_NORMAL ? 9334 : 9333;
fs.mkdirSync(SALIDA, { recursive: true });

const pausa = (ms) => new Promise((r) => setTimeout(r, ms));

async function hasta(condicion, descripcion, tope = 20000) {
  const inicio = Date.now();
  for (;;) {
    const valor = await condicion();
    if (valor) return valor;
    if (Date.now() - inicio > tope) throw new Error('Tiempo agotado esperando: ' + descripcion);
    await pausa(50);
  }
}

class Cdp {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pendientes = new Map(); this.oyentes = [];
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id) {
        const p = this.pendientes.get(m.id); this.pendientes.delete(m.id);
        if (m.error) p.rechazar(new Error(JSON.stringify(m.error))); else p.resolver(m.result);
      } else this.oyentes.forEach((f) => f(m));
    };
  }
  enviar(metodo, params = {}, sessionId) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method: metodo, params, sessionId }));
    return new Promise((resolver, rechazar) => this.pendientes.set(id, { resolver, rechazar }));
  }
}

async function principal() {
  const perfil = path.join(SALIDA, 'perfil-chrome');
  const chrome = spawn(CHROME, ['--headless=new', '--user-data-dir=' + perfil,
    '--remote-debugging-port=' + PUERTO_CDP, '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--disable-sync', '--window-size=1280,1800', 'about:blank'], { stdio: 'ignore' });
  const informe = { chrome: null, base: BASE, escenarios: [] };
  try {
    const version = await hasta(async () => {
      try { return await (await fetch('http://127.0.0.1:' + PUERTO_CDP + '/json/version')).json(); } catch { return null; }
    }, 'CDP');
    informe.chrome = version.Browser;
    const ws = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((r, x) => { ws.onopen = r; ws.onerror = x; });
    const cdp = new Cdp(ws);
    const { targetId } = await cdp.enviar('Target.createTarget', { url: 'about:blank' });
    const { sessionId: sid } = await cdp.enviar('Target.attachToTarget', { targetId, flatten: true });
    await cdp.enviar('Page.enable', {}, sid);
    await cdp.enviar('Runtime.enable', {}, sid);
    const errores = [];
    cdp.oyentes.push((m) => {
      if (m.method === 'Runtime.exceptionThrown') errores.push(m.params.exceptionDetails.text + ' ' +
        ((m.params.exceptionDetails.exception || {}).description || ''));
    });

    // --- utilidades -------------------------------------------------------
    const evaluar = async (expresion) => {
      const r = await cdp.enviar('Runtime.evaluate', { expression: expresion, awaitPromise: true, returnByValue: true }, sid);
      if (r.exceptionDetails) throw new Error('evaluar: ' + JSON.stringify(r.exceptionDetails).slice(0, 300));
      return r.result.value;
    };
    const control = async (accion, clave) => (await fetch(BASE + '/__control/' + accion +
      (clave ? '?clave=' + encodeURIComponent(clave) : ''), { method: 'POST' })).json();
    const pulsar = async (selector) => {
      const r = await evaluar(`(() => { const e = document.querySelector(${JSON.stringify(selector)});
        if (!e) return null; e.scrollIntoView({ block: 'center' }); const b = e.getBoundingClientRect();
        return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; })()`);
      if (!r) throw new Error('no existe ' + selector);
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
        await cdp.enviar('Input.dispatchMouseEvent', { type, x: r.x, y: r.y, button: 'left', clickCount: 1 }, sid);
      }
    };
    const ui = () => evaluar(`(() => { const est = document.getElementById('resumen-descarga-estado');
      return { boton_deshabilitado: document.getElementById('btn-descargar-pdf').disabled,
        estado: est.hidden ? null : est.textContent,
        vista_resumen: !document.getElementById('vista-resumen').hidden,
        vista_login: !document.getElementById('vista-login').hidden,
        documento: (document.getElementById('resumen-documento').textContent.match(/Embarazo desde [^—]+— (En curso|Finalizado)/) || [null])[0],
        titulo: (document.getElementById('resumen-documento').textContent.match(/(Resumen|Historial) de mis registros/) || [null])[0] }; })()`);

    let descargas = [];
    cdp.oyentes.push((m) => {
      if (m.method === 'Browser.downloadWillBegin') descargas.push({ guid: m.params.guid,
        nombre: m.params.suggestedFilename, url_esquema: m.params.url.split(':')[0], t: Date.now(), estado: 'iniciada' });
      if (m.method === 'Browser.downloadProgress' && m.params.state !== 'inProgress') {
        const d = descargas.find((x) => x.guid === m.params.guid); if (d) d.estado = m.params.state;
      }
    });

    async function escenario(nombre, instrumentacion, cuerpo) {
      const dir = path.join(SALIDA, 'descargas', nombre);
      fs.mkdirSync(dir, { recursive: true });
      await cdp.enviar('Browser.setDownloadBehavior', { behavior: 'allowAndName', downloadPath: dir, eventsEnabled: true });
      await control('reiniciar');
      descargas = [];
      errores.length = 0;
      const pasos = [];
      const anotar = async (tras, extra) => pasos.push(Object.assign({ tras, ui: await ui() }, extra || {}));
      // Página nueva en cada escenario: el motor empieza sin cargar.
      await cdp.enviar('Page.navigate', { url: BASE + '/' }, sid);
      await hasta(() => evaluar(`document.readyState === 'complete' && !document.getElementById('vista-inicio').hidden`), 'portal abierto');
      const ctx = { anotar, dir };
      let resultado = 'ok';
      try { await cuerpo(ctx); } catch (e) { resultado = 'ERROR: ' + e.message; }
      await hasta(() => descargas.every((d) => d.estado !== 'iniciada'), 'descargas terminadas', 20000).catch(() => {});
      // Archivo en disco con el nombre que el navegador propuso.
      const archivos = descargas.map((d, i) => {
        const destino = path.join(dir, (i + 1) + '-' + d.nombre);
        if (fs.existsSync(path.join(dir, d.guid))) fs.renameSync(path.join(dir, d.guid), destino);
        return Object.assign({}, d, { archivo: fs.existsSync(destino) ? destino : null,
          bytes: fs.existsSync(destino) ? fs.statSync(destino).size : 0 });
      });
      informe.escenarios.push({ nombre, instrumentacion, resultado, pasos, descargas: archivos,
        errores_pagina: errores.slice() });
      console.log((resultado === 'ok' ? 'ok   ' : 'FALLO') + ' ' + nombre + ' — descargas: ' + archivos.length);
    }

    const irAHistorial = async () => {
      await pulsar('a[data-vista="historial"]');
      await hasta(() => evaluar(`!document.getElementById('btn-guardar-resumen').disabled`), 'resumen habilitado');
    };
    const elegir = async (id) => {
      await evaluar(`(() => { const s = document.getElementById('selector-embarazo'); s.value = '${id}';
        s.dispatchEvent(new Event('change')); })()`);
      await hasta(() => evaluar(`!document.getElementById('btn-guardar-resumen').disabled`), 'monitoreo ' + id);
    };
    const abrirResumen = async () => {
      await pulsar('#btn-guardar-resumen');
      await hasta(() => evaluar(`!document.getElementById('vista-resumen').hidden`), 'vista previa');
    };
    const descargar = async () => {
      const t = Date.now();
      await pulsar('#btn-descargar-pdf');
      return t;
    };
    const esperarDescargas = (n) => hasta(() => descargas.length >= n && descargas.slice(0, n).every((d) => d.estado === 'completed'), n + ' descarga(s)');
    const motorCargado = () => hasta(() => evaluar(`Boolean(window.pdfMake && typeof window.vfs === 'object')`), 'motor cargado');
    const esperarAsentado = () => hasta(() => evaluar(`!document.getElementById('btn-descargar-pdf').disabled`), 'botón libre');
    const entrar = async () => {
      await evaluar(`(() => { document.getElementById('login-email').value = 'paciente@example.com';
        document.getElementById('login-password').value = 'clave-ficticia';
        document.getElementById('form-login').requestSubmit(); })()`);
      await hasta(() => evaluar(`!document.getElementById('vista-inicio').hidden`), 'portal tras login');
    };
    // [gen] Retiene el final de cada generación de pdfmake (el PDF ya está
    // hecho en memoria) hasta __soltarFines().
    const retenerFinDeGeneracion = () => evaluar(`(() => {
      const original = window.pdfMake.createPdf; window.__fines = [];
      window.pdfMake.createPdf = function () {
        const doc = original.apply(this, arguments); const getStream = doc.getStream;
        doc.getStream = function () { const s = getStream.apply(this, arguments); const on = s.on;
          s.on = function (evento, fn) { return evento === 'end'
            ? on.call(this, evento, function () { window.__fines.push(fn); }) : on.apply(this, arguments); };
          return s; };
        return doc; };
      window.__soltarFines = () => { const f = window.__fines.splice(0); f.forEach((x) => x()); return f.length; };
      return true; })()`);
    const MOTOR = 'GET /vendor/pdfmake.min.js';
    const LOGOUT = 'POST /adaptador/cerrar-sesion';

    // --- escenarios ---------------------------------------------------------
    await escenario('N1-normal-frio-y-caliente', 'ninguna', async ({ anotar }) => {
      await irAHistorial(); await abrirResumen();
      const t1 = await descargar(); await esperarDescargas(1);
      await anotar('descarga con motor frío', { ms_hasta_inicio: descargas[0].t - t1 });
      await esperarAsentado();
      const t2 = await descargar(); await esperarDescargas(2);
      await anotar('descarga con motor ya cargado', { ms_hasta_inicio: descargas[1].t - t2 });
      const captura = await cdp.enviar('Page.captureScreenshot', { format: 'png' }, sid);
      fs.writeFileSync(path.join(SALIDA, 'vista-previa-resumen.png'), Buffer.from(captura.data, 'base64'));
      await pulsar('#resumen-alcance-completo');
      await esperarAsentado();
      await descargar(); await esperarDescargas(3);
      await anotar('descarga de Historial completo');
      if (!SOLO_NORMAL) {
        await pulsar('#btn-volver-historial'); await elegir(100); await abrirResumen();
        await descargar(); await esperarDescargas(4);
        await anotar('descarga del embarazo 100');
      }
    });
    if (SOLO_NORMAL) return informe;

    await escenario('A-cambio-a-historial-completo', '[red] motor retenido', async ({ anotar }) => {
      await control('retener', MOTOR);
      await irAHistorial(); await abrirResumen();
      await descargar();
      await hasta(() => evaluar(`document.getElementById('resumen-descarga-estado').textContent === 'Preparando el PDF…'`), 'preparando');
      await anotar('Descargar (Resumen) con el motor retenido');
      await pulsar('#resumen-alcance-completo');
      await anotar('cambio a Historial completo');
      await control('soltar', MOTOR); await motorCargado();
      await anotar('motor cargado', { descargas_hasta_aqui: descargas.length });
      await descargar(); await esperarDescargas(1);
      await anotar('nueva descarga (control)');
    });

    await escenario('B-otro-embarazo', '[red] motor retenido', async ({ anotar }) => {
      await control('retener', MOTOR);
      await irAHistorial(); await abrirResumen();
      await descargar();
      await anotar('Descargar del embarazo 130');
      await pulsar('#btn-volver-historial'); await elegir(100); await abrirResumen();
      await anotar('resumen del embarazo 100 abierto');
      await control('soltar', MOTOR); await motorCargado();
      await anotar('motor cargado', { descargas_hasta_aqui: descargas.length });
      await descargar(); await esperarDescargas(1);
      await anotar('nueva descarga (control)');
    });

    await escenario('B2-mismo-embarazo-reabierto', '[red] motor retenido', async ({ anotar }) => {
      await control('retener', MOTOR);
      await irAHistorial(); await abrirResumen();
      await descargar();
      await pulsar('#btn-volver-historial'); await abrirResumen();
      await anotar('salir y reabrir');
      await control('soltar', MOTOR); await motorCargado();
      await anotar('motor cargado', { descargas_hasta_aqui: descargas.length });
      await descargar(); await esperarDescargas(1);
      await anotar('nueva descarga (control)');
    });

    await escenario('C-logout-pendiente', '[red] motor y POST cerrar-sesion retenidos', async ({ anotar }) => {
      await control('retener', MOTOR); await control('retener', LOGOUT);
      await irAHistorial(); await abrirResumen();
      await descargar();
      await pulsar('#btn-cerrar-sesion'); await pulsar('#btn-confirmar-cierre');
      await hasta(async () => ((await control('estado')).retenidas[LOGOUT] || 0) === 1, 'POST retenido');
      await anotar('logout confirmado, POST pendiente');
      await control('soltar', MOTOR); await motorCargado();
      await anotar('motor cargado, POST aún pendiente', { descargas_hasta_aqui: descargas.length });
      await control('soltar', LOGOUT);
      await hasta(() => evaluar(`!document.getElementById('vista-login').hidden`), 'login');
      await anotar('POST respondido');
      await entrar(); await irAHistorial(); await abrirResumen();
      await descargar(); await esperarDescargas(1);
      await anotar('nueva sesión, nueva descarga (control)');
    });

    await escenario('C2-logout-falla-por-red', '[red] motor retenido; POST cerrar-sesion cortado', async ({ anotar }) => {
      await control('retener', MOTOR); await control('retener', LOGOUT);
      await irAHistorial(); await abrirResumen();
      await descargar();
      await pulsar('#btn-cerrar-sesion'); await pulsar('#btn-confirmar-cierre');
      await hasta(async () => ((await control('estado')).retenidas[LOGOUT] || 0) === 1, 'POST retenido');
      await control('soltar', MOTOR); await motorCargado();
      await anotar('motor cargado, POST pendiente', { descargas_hasta_aqui: descargas.length });
      await control('fallar', LOGOUT);
      await hasta(() => evaluar(`!document.getElementById('vista-login').hidden`), 'login');
      await anotar('POST cortado: vuelve al login', { descargas_hasta_aqui: descargas.length });
      await entrar(); await irAHistorial(); await abrirResumen();
      await descargar(); await esperarDescargas(1);
      await anotar('nueva descarga (control)');
    });

    await escenario('K2-cancelar-logout', '[red] motor retenido', async ({ anotar }) => {
      await control('retener', MOTOR);
      await irAHistorial(); await abrirResumen();
      await descargar();
      await pulsar('#btn-cerrar-sesion'); await pulsar('#btn-cancelar-cierre');
      await anotar('diálogo cancelado');
      await control('soltar', MOTOR); await esperarDescargas(1);
      await anotar('motor cargado');
    });

    await escenario('G1-motor-cargado-cambio-durante-generacion', '[gen] fin de generación retenido', async ({ anotar }) => {
      await irAHistorial(); await abrirResumen();
      await descargar(); await esperarDescargas(1); await esperarAsentado();
      await retenerFinDeGeneracion();
      await descargar();
      await hasta(() => evaluar('window.__fines.length === 1'), 'PDF generado en memoria');
      await anotar('PDF generado y retenido antes de entregarse');
      await pulsar('#resumen-alcance-completo');
      const soltados = await evaluar('window.__soltarFines()');
      await anotar('cambio a Historial completo y fin soltado', { soltados, descargas_hasta_aqui: descargas.length });
      await descargar();
      await hasta(() => evaluar('window.__fines.length === 1'), 'PDF del control');
      await evaluar('window.__soltarFines()'); await esperarDescargas(2);
      await anotar('nueva descarga (control)');
    });

    await escenario('G2-motor-cargado-salir-durante-generacion', '[gen] fin de generación retenido', async ({ anotar }) => {
      await irAHistorial(); await abrirResumen();
      await descargar(); await esperarDescargas(1); await esperarAsentado();
      await retenerFinDeGeneracion();
      await descargar();
      await hasta(() => evaluar('window.__fines.length === 1'), 'PDF generado en memoria');
      await pulsar('#btn-volver-historial'); await elegir(100); await abrirResumen();
      const soltados = await evaluar('window.__soltarFines()');
      await anotar('otro embarazo abierto y fin soltado', { soltados, descargas_hasta_aqui: descargas.length });
      await descargar();
      await hasta(() => evaluar('window.__fines.length === 1'), 'PDF del control');
      await evaluar('window.__soltarFines()'); await esperarDescargas(2);
      await anotar('nueva descarga (control)');
    });

    await escenario('G3-motor-cargado-logout-durante-generacion', '[gen] fin retenido; [red] POST cerrar-sesion retenido', async ({ anotar }) => {
      await irAHistorial(); await abrirResumen();
      await descargar(); await esperarDescargas(1); await esperarAsentado();
      await retenerFinDeGeneracion();
      await control('retener', LOGOUT);
      await descargar();
      await hasta(() => evaluar('window.__fines.length === 1'), 'PDF generado en memoria');
      await pulsar('#btn-cerrar-sesion'); await pulsar('#btn-confirmar-cierre');
      await hasta(async () => ((await control('estado')).retenidas[LOGOUT] || 0) === 1, 'POST retenido');
      const soltados = await evaluar('window.__soltarFines()');
      await anotar('logout confirmado y fin soltado, POST pendiente', { soltados, descargas_hasta_aqui: descargas.length });
      await control('soltar', LOGOUT);
      await hasta(() => evaluar(`!document.getElementById('vista-login').hidden`), 'login');
      await anotar('POST respondido', { descargas_hasta_aqui: descargas.length });
    });

    await escenario('F1-fallo-del-motor', '[red] carga de pdfmake cortada', async ({ anotar }) => {
      await control('retener', MOTOR);
      await irAHistorial(); await abrirResumen();
      await descargar();
      await control('fallar', MOTOR);
      await esperarAsentado();
      await anotar('motor cortado', { descargas_hasta_aqui: descargas.length });
      await descargar(); await esperarDescargas(1);
      await anotar('reintento');
    });
    return informe;
  } finally {
    fs.writeFileSync(path.join(SALIDA, 'informe.json'), JSON.stringify(informe, null, 2) + '\n');
    chrome.kill();
  }
}

principal().then(() => process.exit(0), (e) => { console.error(e); process.exit(1); });
