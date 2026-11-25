// Guaguas FTV · buscador (paradas, líneas, lugares) y «mi ubicación».
// ── Búsqueda y ubicación ──
const cercana = (la, lo) => paradas.slice().sort((a, b) => Math.hypot(a.lat - la, (a.lon - lo) * .88) - Math.hypot(b.lat - la, (b.lon - lo) * .88))[0];
function sugerencias(r) {
  const s = $('sug');
  s.innerHTML = ''; s.classList.toggle('oculto', !r.length);
  r.forEach(x => { const b = document.createElement('button'); b.innerHTML = `${esc(x.t)}<small>${esc(x.sub)}</small>`; b.onclick = () => { $('buscar').value = ''; $('buscar').blur(); x.f(); }; s.appendChild(b); });
}
function mostrarRecientes() {
  if ($('buscar').value.trim()) return;
  const ir = p => () => { foco(p); elegir({ tipo: 'parada', ...p }); };
  const f = [...favs].map(id => paradas.find(p => p.id === id)).filter(Boolean);
  const r = recientes().filter(id => !favs.has(id)).map(id => paradas.find(p => p.id === id)).filter(Boolean);
  sugerencias([...(rutaDesde ? [] : [{ t: '📍 Cómo llegar desde donde estoy', sub: 'Elige a dónde vas y te digo qué guaguas coger', f: desdeAqui }]),
               ...f.map(p => ({ t: '★ ' + p.nombre, sub: 'Favorita · Líneas ' + p.lineas.join(', '), f: ir(p) })),
               ...r.map(p => ({ t: p.nombre, sub: 'Reciente · Líneas ' + p.lineas.join(', '), f: ir(p) }))]);
}
$('buscar').onfocus = mostrarRecientes;
$('buscar').onblur = () => setTimeout(() => $('sug').classList.add('oculto'), 200);   // tras el clic en una sugerencia
$('buscar').oninput = e => {
  const q = norm(e.target.value.trim()), s = $('sug');
  if (!q) { mostrarRecientes(); return; }
  const r = paradas.filter(p => norm(p.nombre.split(' → ')[0]).includes(q)).slice(0, 6).map(p => ({ t: p.nombre, sub: 'Líneas ' + p.lineas.join(', '), f: () => { foco(p); elegir({ tipo: 'parada', ...p }); } }));
  const mn = q.match(/^(?:l(?:inea)?\.?\s*)?0*(\d{1,3})$/);
  if (mn) Object.keys(colores).filter(n => String(Number(n)) === mn[1]).forEach(n => r.unshift({ t: 'Línea ' + n, sub: 'Ver recorrido y paradas', f: () => abrirLinea(n) }));
  lugares.filter(l => norm(l.n).includes(q)).slice(0, 3).forEach(l => r.push(rutaDesde
    ? { t: l.n, sub: 'Lugar · todas sus paradas', f: () => verRuta(rutaDesde, { tipo: 'lugar', nombre: l.n, lat: l.lat, lon: l.lon }) }
    : { t: l.n, sub: 'Lugar · parada más cercana', f: () => { const c = cercana(l.lat, l.lon); if (c) { foco(c); elegir({ tipo: 'parada', ...c }); } } }));
  sugerencias(r);
};
$('gps').onclick = () => {
  if (!navigator.geolocation) return toast('Tu navegador no permite la ubicación.');
  navigator.geolocation.getCurrentPosition(pos => {
    const { latitude: la, longitude: lo } = pos.coords;
    if (marcaYo) map.removeLayer(marcaYo);
    marcaYo = L.circleMarker([la, lo], { radius: 8, color: '#fff', weight: 3, fillColor: '#1a73e8', fillOpacity: 1 }).addTo(map);
    const c = cercana(la, lo); if (!c) return;
    const km = Math.hypot((c.lat - la) * 111, (c.lon - lo) * 98);
    if (km > 2) toast(`La parada más cercana está a ${km.toFixed(1)} km: ${c.nombre}.`);
    foco(c, 15); elegir({ tipo: 'parada', ...c });
  }, () => toast('No pude obtener tu ubicación. Actívala en los ajustes del navegador.'), { enableHighAccuracy: true, timeout: 10000 });
};
