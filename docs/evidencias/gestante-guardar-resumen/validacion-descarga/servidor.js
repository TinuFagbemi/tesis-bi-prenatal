/* Portal de la gestante para la validación en Chrome (PR #22).

   Sirve los mismos archivos que backend/app/gestante/rutas.py, desde el
   frontend que se le indica, y simula /adaptador/* con datos FICTICIOS
   (los del arnés de pruebas). Escucha solo en 127.0.0.1. No usa la base, el
   .env ni los servicios de la demo.

   Control de tiempos (solo para la prueba):
     POST /__control/retener?clave=GET%20/vendor/pdfmake.min.js
     POST /__control/soltar?clave=...      responde lo retenido
     POST /__control/fallar?clave=...      corta la conexión de lo retenido
     POST /__control/reiniciar             sesión iniciada, nada retenido
     GET  /__control/estado

   Uso: node servidor.js <dir frontend/gestante> <puerto>
*/
'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const FRONT = path.resolve(process.argv[2]);
const PUERTO = Number(process.argv[3] || 8765);

const ARCHIVOS = {
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/styles.css': ['styles.css', 'text/css; charset=utf-8'],
  '/app.js': ['app.js', 'text/javascript; charset=utf-8'],
  '/graficas.js': ['graficas.js', 'text/javascript; charset=utf-8'],
  '/resumen_pdf.js': ['resumen_pdf.js', 'text/javascript; charset=utf-8'],
  '/vendor/pdfmake.min.js': ['vendor/pdfmake.min.js', 'text/javascript; charset=utf-8'],
  '/vendor/vfs_fonts.js': ['vendor/vfs_fonts.js', 'text/javascript; charset=utf-8']
};

// --- Datos ficticios (copiados del arnés app.comportamiento.test.js) -------
const EMB_130 = { id_embarazo: 130, fecha_inicio: '2026-03-19', fecha_probable_parto: null,
  estado_embarazo: 'ACTIVO', fecha_cierre: null };
const EMB_100 = { id_embarazo: 100, fecha_inicio: '2025-01-06', fecha_probable_parto: '2025-10-13',
  estado_embarazo: 'FINALIZADO', fecha_cierre: '2025-10-13' };

function registro(l, idSesion, campo, unidad) {
  return { valor: l[campo], unidad, fecha_hora_captura: l.fecha_hora_captura,
    semana_gestacion_lectura: l.semana_gestacion, id_lectura: l.id_lectura, id_sesion: idSesion };
}

// Embarazo 130: 15 lecturas de movimientos, para que «Resumen» (10) e
// «Historial completo» (15) sean distintos.
const LECTURAS_130 = [];
for (let i = 0; i !== 15; i += 1) {
  LECTURAS_130.push({ id_lectura: 5000 + i,
    fecha_hora_captura: new Date(Date.UTC(2026, 8, 24, 11, 40) - i * 2 * 86400000).toISOString().replace('.000Z', '+00:00'),
    codigo_semaforo: 'OK', semana_gestacion: 27 - Math.floor(i * 2 / 7),
    hr_valor: null, spo2_valor: null, mov_valor: 12 + i });
}
const MONITOREO_130 = { disponible: true, datos: { id_embarazo: 130,
  sesiones: LECTURAS_130.map((l) => ({ id_sesion: l.id_lectura, tipo_sesion: 'MOVIMIENTOS_FETALES',
    estado_sesion: 'COMPLETADA', fecha_inicio: l.fecha_hora_captura, fecha_fin: null, lecturas: [l] })),
  ultima_lectura: LECTURAS_130[0], id_sesion_de_la_ultima: null,
  ultimos_registros: { frecuencia_cardiaca: null, saturacion_oxigeno: null,
    movimientos_fetales: registro(LECTURAS_130[0], 5000, 'mov_valor', 'movimientos') } } };

const L110 = { id_lectura: 110, fecha_hora_captura: '2025-09-08T17:13:00+00:00', codigo_semaforo: 'OK',
  semana_gestacion: 36, hr_valor: '86.00', spo2_valor: '97.00', mov_valor: null };
const L111 = { id_lectura: 111, fecha_hora_captura: '2025-09-08T17:14:00+00:00', codigo_semaforo: 'OK',
  semana_gestacion: 36, hr_valor: '95.00', spo2_valor: '99.00', mov_valor: null };
const L679 = { id_lectura: 679, fecha_hora_captura: '2025-10-01T16:04:00+00:00', codigo_semaforo: 'WARNING',
  semana_gestacion: 39, hr_valor: null, spo2_valor: null, mov_valor: 7 };
const MONITOREO_100 = { disponible: true, datos: { id_embarazo: 100,
  sesiones: [
    { id_sesion: 102, tipo_sesion: 'SIGNOS_MATERNOS', estado_sesion: 'COMPLETADA',
      fecha_inicio: '2025-09-08T17:13:00+00:00', fecha_fin: '2025-09-08T17:18:00+00:00', lecturas: [L110, L111] },
    { id_sesion: 231, tipo_sesion: 'MOVIMIENTOS_FETALES', estado_sesion: 'COMPLETADA',
      fecha_inicio: '2025-10-01T14:52:00+00:00', fecha_fin: '2025-10-01T16:04:00+00:00', lecturas: [L679] }],
  ultima_lectura: L679, id_sesion_de_la_ultima: null,
  ultimos_registros: { frecuencia_cardiaca: registro(L111, 102, 'hr_valor', 'BPM'),
    saturacion_oxigeno: registro(L111, 102, 'spo2_valor', '%'),
    movimientos_fetales: registro(L679, 231, 'mov_valor', 'movimientos') } } };

const EMBARAZOS = { disponible: true, datos: { hoy: '2026-09-25', semana_actual: 28,
  actual: EMB_130, anteriores: [EMB_100], todos: [EMB_130, EMB_100], ambiguo: false,
  registro_en_este_dispositivo: true } };

// --- Estado y control -------------------------------------------------------
let autenticada = true;
const retener = new Set();
const retenidas = new Map();     // clave → [{ responder, cortar }]
const registroPeticiones = [];

function json(res, estado, cuerpo) {
  res.writeHead(estado, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(cuerpo));
}

function adaptador(clave, res) {
  if (clave === 'GET /adaptador/sesion') return json(res, 200, autenticada ? { autenticada: true, rol: 'PACIENTE' } : { autenticada: false });
  if (clave === 'POST /adaptador/iniciar-sesion') { autenticada = true; return json(res, 200, { autenticada: true }); }
  if (clave === 'POST /adaptador/cerrar-sesion') { autenticada = false; return json(res, 200, { autenticada: false }); }
  if (!autenticada) return json(res, 401, { detail: 'Sesión no válida.' });
  if (clave === 'GET /adaptador/estado-conexion') return json(res, 200, { api_central: 'disponible',
    autenticacion_central: 'vigente', sesion_local_expira_en: '2026-12-31T15:00:00+00:00' });
  if (clave === 'GET /adaptador/embarazos') return json(res, 200, EMBARAZOS);
  if (clave === 'GET /adaptador/embarazos/130/monitoreo') return json(res, 200, MONITOREO_130);
  if (clave === 'GET /adaptador/embarazos/100/monitoreo') return json(res, 200, MONITOREO_100);
  return json(res, 404, { detail: 'No disponible.' });
}

function servir(clave, ruta, res) {
  if (ARCHIVOS[ruta] && clave.startsWith('GET ')) {
    const [nombre, tipo] = ARCHIVOS[ruta];
    res.writeHead(200, { 'Content-Type': tipo, 'Cache-Control': 'no-cache' });
    return fs.createReadStream(path.join(FRONT, nombre)).pipe(res);
  }
  if (ruta.startsWith('/adaptador/')) return adaptador(clave, res);
  res.writeHead(404); res.end();
}

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const clave = req.method + ' ' + url.pathname;
  if (url.pathname.startsWith('/__control/')) {
    const c = url.searchParams.get('clave');
    const accion = url.pathname.slice('/__control/'.length);
    if (accion === 'retener') retener.add(c);
    if (accion === 'soltar' || accion === 'fallar') {
      retener.delete(c);
      (retenidas.get(c) || []).forEach((p) => (accion === 'soltar' ? p.responder() : p.cortar()));
      retenidas.delete(c);
    }
    if (accion === 'reiniciar') { autenticada = true; retener.clear(); retenidas.clear(); registroPeticiones.length = 0; }
    return json(res, 200, { autenticada, retener: [...retener],
      retenidas: Object.fromEntries([...retenidas].map(([k, v]) => [k, v.length])),
      peticiones: registroPeticiones });
  }
  registroPeticiones.push(clave);
  req.resume();
  req.on('end', () => {
    if (retener.has(clave)) {
      const lista = retenidas.get(clave) || [];
      lista.push({ responder: () => servir(clave, url.pathname, res),
        // Un <script> cortado lo reintenta Chrome por su cuenta: para que falle
        // de verdad, se responde 503. Una petición del adaptador se corta.
        cortar: () => (clave.startsWith('GET /vendor/') ? (res.writeHead(503), res.end()) : res.destroy()) });
      retenidas.set(clave, lista);
      return;
    }
    servir(clave, url.pathname, res);
  });
}).listen(PUERTO, '127.0.0.1', () => console.log('portal simulado en http://127.0.0.1:' + PUERTO + ' sirviendo ' + FRONT));
