// Guaguas FTV · el mapa: tema claro/oscuro, carga de líneas y paradas, ver una sola línea, bandas paralelas, resaltar.
// ── Mapa y tema ──
const map = L.map('map', { preferCanvas: true, zoomControl: false }).setView([28.35, -14.0], 9);
map.attributionControl.setPrefix(false);
if (!matchMedia('(pointer:coarse)').matches) L.control.zoom({ position: 'bottomright' }).addTo(map);
let tiles = null;
const mq = matchMedia('(prefers-color-scheme: dark)');
function tema() {
  const p = localStorage.getItem('tema') || 'auto', d = p === 'dark' || (p === 'auto' && mq.matches);
  document.documentElement.dataset.t = d ? 'dark' : 'light';
  document.querySelector('meta[name=theme-color]').content = d ? '#161b21' : '#ffffff';
  if (tiles) map.removeLayer(tiles);
  tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(map);
}
mq.addEventListener('change', tema); tema();
addEventListener('resize', () => map.invalidateSize());

// ── Datos ──
let marcasParadas = [], capaCamino = null, nombres = {}, lineaSola = null;
let paradas = [], lugares = [], colores = {}, rutas = {}, actual = null, filtro = null, timer = null, marcaSel = null, marcaYo = null, marcadas = new Set();
async function datos() {
  const [lineas, ps, osm, lug] = await Promise.all([json('/api/lineas?bandas=1'), json('/api/paradas'), json('/api/osm'), json('/api/lugares')]);
  lugares = lug; paradas = ps;
  lineas.forEach(l => {
    colores[l.num] = l.color; nombres[l.num] = l.nombre;
    rutas[l.num] = unir(l.tramos).map(t => { const c = L.polyline(t.p, { color: l.color, weight: 3.5, opacity: .75, interactive: false, lineJoin: 'round' }); c._base = t.p; c._os = t.os; return c.addTo(map); });
  });
  recolocar();
  osm.forEach(p => { const mk = L.circleMarker([p.lat, p.lon], { radius: 4, color: '#fff', weight: 1, fillColor: '#8b95a1', fillOpacity: .9 }).addTo(map)
    .on('click', () => elegir({ tipo: 'osm', nombre: p.n, lat: p.lat, lon: p.lon })); mk._osm = true; marcasParadas.push(mk); });
  ps.forEach(p => { const mk = L.circleMarker([p.lat, p.lon], { radius: 7, color: '#fff', weight: 2.5, fillColor: '#0b6e99', fillOpacity: 1 }).addTo(map)
    .on('click', () => elegir({ tipo: 'parada', ...p })); mk._lineas = p.lineas; marcasParadas.push(mk); });
  try { const g = localStorage.getItem('lineaSola'); if (g && rutas[g]) soloLinea(g, false); } catch (_) {}
}
// ── Ver una sola línea: en el mapa queda solo esa línea (centrada en su calle) y sus paradas ──
function mostrarParadas() {
  marcasParadas.forEach(mk => {
    const ver = !lineaSola || (!mk._osm && mk._lineas && mk._lineas.includes(lineaSola));
    if (ver) mk.addTo(map); else map.removeLayer(mk);
  });
}
function soloLinea(num, encuadrar = true) {
  lineaSola = num && rutas[num] ? num : null;
  try { if (lineaSola) localStorage.setItem('lineaSola', lineaSola); else localStorage.removeItem('lineaSola'); } catch (_) {}
  const f = $('filtro');
  f.classList.toggle('on', !!lineaSola);
  f.style.setProperty('--c', lineaSola ? colores[lineaSola] : '');
  f.innerHTML = lineaSola ? `<i class="pto"></i>Línea ${esc(lineaSola)} <span aria-hidden="true">✕</span>` : '<i class="pto"></i>Líneas';
  f.setAttribute('aria-label', lineaSola ? 'Ver todas las líneas' : 'Ver una sola línea');
  if (!capaCamino) { mostrarParadas(); resaltar(lineaSola ? [lineaSola] : (actual && actual.tipo === 'parada' ? actual.lineas : null)); }
  recolocar();
  if (lineaSola && encuadrar) {
    const pts = rutas[lineaSola].flatMap(c => c._base);
    map.fitBounds(L.latLngBounds(pts), { paddingTopLeft: [30, ancho() ? 30 : 100], paddingBottomRight: [30, ancho() ? 30 : Math.round(innerHeight * .45)] });
  }
}
function elegirLinea() {
  $('shead').querySelector('b').textContent = 'Ver una sola línea';
  $('sbody').innerHTML = `<div class="grp lineas">${Object.keys(rutas).sort((a, b) => a - b).map(n => `<button data-filtro="${esc(n)}"><span class="badge" style="background:${esc(colores[n])}">${esc(n)}</span><span>${esc(nombres[n] || '')}</span></button>`).join('')}</div>`;
  $('sheet').classList.remove('oculto');
}
$('filtro').onclick = () => { if (lineaSola) soloLinea(null); else elegirLinea(); };
function foco(p, z = 15) {
  map.setView([p.lat, p.lon], Math.max(map.getZoom(), z), { animate: false });
  if (!ancho()) map.panBy([0, Math.round(innerHeight * .2)], { animate: false });
}
// Donde varias líneas comparten calle se dibujan en paralelo (como un plano de metro): cada tramo trae su
// desplazamiento lateral "o" en anchos de línea, que aquí se pasa a píxeles para el zoom actual.
// Cada línea es UNA polilínea continua: los tramos seguidos se unen y cada segmento guarda su desplazamiento.
function unir(tramos) {
  const partes = [];
  let cur = null;
  const junto = (a, b) => Math.abs(a[0] - b[0]) < 2e-5 && Math.abs(a[1] - b[1]) < 2e-5;
  tramos.forEach(t => {
    if (t.p.length < 2) return;
    if (cur && junto(cur.p[cur.p.length - 1], t.p[0])) {
      for (let i = 1; i < t.p.length; i++) { cur.p.push(t.p[i]); cur.os.push(t.o); }
    } else {
      cur = { p: t.p.slice(), os: t.p.slice(1).map(() => t.o) };
      partes.push(cur);
    }
  });
  return partes;
}
// Desplazamiento lateral en píxeles (os[k] = el del segmento k, en anchos de línea). Donde cambia, la línea se va
// apartando poco a poco a lo largo de TRANSICION px (curva suave), en vez de saltar de golpe.
const TRANSICION = 36;
function desplazar(lls, os, paso) {
  if (lls.length < 2 || os.every(v => !v)) return lls;
  const P = lls.map(x => map.latLngToLayerPoint(x)), n = P.length, d = [0];
  for (let i = 1; i < n; i++) d.push(d[i - 1] + P[i].distanceTo(P[i - 1]));
  const bordes = [];
  for (let k = 1; k < n - 1; k++) if (os[k] !== os[k - 1]) bordes.push({ s: d[k], a: os[k - 1] * paso, b: os[k] * paso });
  const off = (s, k) => {
    let v = os[k] * paso;
    for (const B of bordes) {
      if (Math.abs(s - B.s) < TRANSICION / 2) { const u = (s - B.s) / TRANSICION + .5; v = B.a + (B.b - B.a) * u * u * (3 - 2 * u); }
    }
    return v;
  };
  const nor = (a, b) => { const dx = b.x - a.x, dy = b.y - a.y, l = Math.hypot(dx, dy); return l < 1e-6 ? null : { x: -dy / l, y: dx / l }; };
  const extra = bordes.flatMap(B => [-.5, -.3, -.1, .1, .3, .5].map(f => B.s + f * TRANSICION)).sort((a, b) => a - b);
  const out = [];
  let e = 0;
  for (let k = 0; k < n; k++) {
    if (k > 0) {
      const n1 = nor(P[k - 1], P[k]);
      for (; e < extra.length && extra[e] < d[k]; e++) {
        const s = extra[e];
        if (s <= d[k - 1] || !n1) continue;
        const u = (s - d[k - 1]) / (d[k] - d[k - 1]), v = off(s, k - 1);
        out.push(map.layerPointToLatLng(L.point(P[k - 1].x + (P[k].x - P[k - 1].x) * u + n1.x * v, P[k - 1].y + (P[k].y - P[k - 1].y) * u + n1.y * v)));
      }
    }
    const n1 = k > 0 ? nor(P[k - 1], P[k]) : null, n2 = k < n - 1 ? nor(P[k], P[k + 1]) : null;
    const a = n1 || n2, b = n2 || n1, v = off(d[k], Math.min(k, n - 2));
    if (!a || !v) { out.push(lls[k]); continue; }
    let nx = a.x + b.x, ny = a.y + b.y; const l = Math.hypot(nx, ny);
    if (l < 1e-6) { nx = a.x; ny = a.y; } else { nx /= l; ny /= l; }
    const mm = v / Math.max(.5, nx * a.x + ny * a.y);   // inglete limitado en las curvas cerradas
    out.push(map.layerPointToLatLng(L.point(P[k].x + nx * mm, P[k].y + ny * mm)));
  }
  return out;
}
function recolocar() {
  const paso = map.getZoom() >= 13 ? 3.5 : map.getZoom() >= 11 ? 2.5 : 1.5;
  Object.entries(rutas).forEach(([n, cs]) => cs.forEach(c => c.setLatLngs(lineaSola === n ? c._base : desplazar(c._base, c._os, paso))));
}
map.on('zoomend', recolocar);
// Al elegir una parada se resaltan los recorridos de sus líneas y se atenúan los demás.
function resaltar(nums) {
  if (lineaSola) nums = [lineaSola];   // viendo una sola línea: solo esa
  const set = nums && nums.length ? new Set(nums) : null;
  Object.entries(rutas).forEach(([n, cs]) => {
    const on = !set || set.has(n);
    cs.forEach(c => c.setStyle({ weight: set && on ? 5 : 3.5, opacity: on ? (set ? .95 : .75) : (lineaSola ? 0 : .12) }));   // sin bringToFront: taparía las paradas
  });
}
// Paradas recientes (en este navegador) y enlace compartible ?parada=<id>
const recientes = () => { try { return JSON.parse(localStorage.getItem('recientes') || '[]'); } catch (_) { return []; } };
function recordar(p) {
  try { localStorage.setItem('recientes', JSON.stringify([p.id, ...recientes().filter(x => x !== p.id)].slice(0, 5))); } catch (_) {}
}
function enlace(p) {
  const u = new URL(location.href);
  if (p && p.tipo === 'parada') u.searchParams.set('parada', p.id); else u.searchParams.delete('parada');
  history.replaceState(null, '', u.pathname + u.search + u.hash);
}
