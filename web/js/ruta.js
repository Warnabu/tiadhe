// Guaguas FTV · cómo llegar: opciones de A a B, elegir camino (dibujo en el mapa) y plegar el panel.
// ── Cómo llegar (guaguas directas de A a B) ──
let rutaDesde = null;
function empezarRuta(p) {
  rutaDesde = p;
  const b = $('buscar'); b.value = ''; b.placeholder = '¿A dónde vas desde ' + p.nombre + '?'; b.focus();
  toast('Busca la parada o el pueblo de destino (o toca una parada en el mapa).', 5000);
}
function finRuta() { rutaDesde = null; $('buscar').placeholder = 'Buscar parada o lugar'; }
function quitarCamino() {
  if (!capaCamino) return;
  map.removeLayer(capaCamino); capaCamino = null;
  mostrarParadas();
  if (marcaYo) marcaYo.addTo(map);
  document.querySelectorAll('#lista .row').forEach(x => { x.style.display = ''; x.querySelector('.yo')?.remove(); });
  document.querySelectorAll('#lista h3').forEach(x => x.style.display = '');
  document.querySelectorAll('#lista .vermapa').forEach(b => b.remove());
  document.querySelectorAll('#lista .elegir').forEach(b => b.textContent = 'Elegir camino');
  plegar(false);
}
// Panel plegado: queda una barrita abajo (título y resumen) y el mapa se ve entero; tocándola se despliega
function plegar(si) {
  const pn = $('panel');
  if (si === pn.classList.contains('mini')) return;
  pn.classList.toggle('mini', si);
  if (si) { pn.scrollTop = 0; pn._ps = $('ps').textContent; if (capaCamino?._resumen) $('ps').textContent = capaCamino._resumen + ' · toca para ver los pasos'; }
  else if (pn._ps != null) { $('ps').textContent = pn._ps; pn._ps = null; }
  if (capaCamino) requestAnimationFrame(() => {
    if (si) map.fitBounds(capaCamino.getBounds(), { paddingTopLeft: [40, ancho() ? 40 : 110], paddingBottomRight: [40, ancho() ? 40 : pn.offsetHeight + 30], maxZoom: 16 });
    else capaCamino._encuadre?.(capaCamino.getBounds());
  });
}
$('panel').addEventListener('click', e => { if ($('panel').classList.contains('mini') && !e.target.closest('#cerrar')) plegar(false); });
document.querySelector('#panel .grab').addEventListener('click', e => { e.stopPropagation(); plegar(!$('panel').classList.contains('mini')); });
// deslizar el panel hacia abajo lo pliega; hacia arriba lo despliega
{
  let y0 = null;
  $('panel').addEventListener('touchstart', e => { y0 = $('panel').scrollTop <= 0 ? e.touches[0].clientY : null; }, { passive: true });
  $('panel').addEventListener('touchend', e => {
    if (y0 == null) return;
    const dy = e.changedTouches[0].clientY - y0; y0 = null;
    if (dy > 60) plegar(true); else if (dy < -40) plegar(false);
  }, { passive: true });
}
// Dónde está el usuario, solo si ya dio permiso (no se le pregunta aquí)
async function dondeEstoy() {
  if (marcaYo) return marcaYo.getLatLng();
  try {
    if (!navigator.permissions || (await navigator.permissions.query({ name: 'geolocation' })).state !== 'granted') return null;
    return await new Promise(ok => navigator.geolocation.getCurrentPosition(p => ok(L.latLng(p.coords.latitude, p.coords.longitude)), () => ok(null), { timeout: 6000, maximumAge: 120000 }));
  } catch (_) { return null; }
}
async function mostrarCamino(camino, fila, pasos, encuadre, o, d) {
  let j; try { j = await json('/api/camino?t=' + encodeURIComponent(JSON.stringify(camino))); } catch (_) { toast('No se pudo dibujar el camino.'); return false; }
  const yo = o.tipo === 'parada' ? await dondeEstoy() : null;   // desde un punto, el camino ya empieza andando
  quitarCamino();
  Object.values(rutas).forEach(cs => cs.forEach(c => c.setStyle({ opacity: 0 })));
  marcasParadas.forEach(m => map.removeLayer(m));
  if (marcaSel) map.removeLayer(marcaSel);
  const capa = L.featureGroup(), par = id => paradas.find(p => p.id === id);
  const pie = lls => L.polyline(lls, { color: '#5f6b7a', weight: 4, dashArray: '1 9', lineCap: 'round', interactive: false }).addTo(capa);
  const primera = par(j.tramos[0].paradas[0]);
  if (o.tipo !== 'parada') L.circleMarker([o.lat, o.lon], { radius: 8, color: '#fff', weight: 3, fillColor: '#1a73e8', fillOpacity: 1 })
    .bindTooltip(o.nombre, { permanent: true, direction: 'top', offset: [0, -8] }).addTo(capa);
  if (d.tipo !== 'parada') L.circleMarker([d.lat, d.lon], { radius: 10, color: '#ff3b30', weight: 3, fillColor: '#fff', fillOpacity: 1 })
    .bindTooltip(d.nombre, { permanent: true, direction: 'top', offset: [0, -8] }).addTo(capa);
  // de donde estás a la primera parada (si estás cerca: a más de 3 km no tiene sentido ir andando)
  if (yo && primera) {
    const m = yo.distanceTo([primera.lat, primera.lon]);
    if (m > 30 && m < 3000) {
      pie([yo, [primera.lat, primera.lon]]);
      L.circleMarker(yo, { radius: 8, color: '#fff', weight: 3, fillColor: '#1a73e8', fillOpacity: 1, interactive: false }).addTo(capa);
      const li = document.createElement('li'); li.className = 'yo';
      li.innerHTML = `Camina unos ${Math.max(1, Math.round(m * 1.3 / 70))} min hasta <b>${esc(primera.nombre.split(' · L')[0])}</b>`;
      (pasos || fila).prepend(li);
      if (!pasos) { const ul = document.createElement('ul'); ul.className = 'pasos yo'; ul.appendChild(li); fila.insertBefore(ul, fila.querySelector('.botones')); }
    }
  }
  const extremos = new Map(), andando = [];   // parada → color de la guagua que se coge o se deja ahí
  j.tramos.forEach(t => {
    if (t.tipo === 'pie') { pie(t.p[0]); andando.push(...t.paradas); return; }
    t.p.forEach(lls => {
      L.polyline(lls, { color: '#fff', weight: 9, opacity: .9, interactive: false }).addTo(capa);
      L.polyline(lls, { color: t.color, weight: 6, opacity: 1, interactive: false }).addTo(capa);
    });
    t.paradas.slice(1, -1).forEach(id => { const p = par(id); if (p) L.circleMarker([p.lat, p.lon], { radius: 4, color: t.color, weight: 2, fillColor: '#fff', fillOpacity: 1 }).bindTooltip(p.nombre).addTo(capa); });
    [t.paradas[0], t.paradas.at(-1)].forEach(id => extremos.has(id) || extremos.set(id, t.color));
  });
  andando.forEach(id => extremos.has(id) || extremos.set(id, '#5f6b7a'));   // paradas a las que solo se va andando
  const fin = d.tipo === 'parada' ? j.tramos.at(-1).paradas.at(-1) : null;
  extremos.forEach((c, id) => {
    const p = par(id); if (!p) return;
    L.circleMarker([p.lat, p.lon], { radius: id === fin ? 10 : 8, color: id === fin ? '#ff3b30' : '#fff', weight: 3, fillColor: c, fillOpacity: 1 })
      .bindTooltip(p.nombre, { permanent: id === fin || id === primera?.id, direction: 'top', offset: [0, -8] })
      .on('click', () => { foco(p); elegir({ tipo: 'parada', ...p }); }).addTo(capa);
  });
  capaCamino = capa.addTo(map);
  [...fila.parentElement.children].forEach(x => { if (x !== fila) x.style.display = 'none'; });   // solo la opción elegida
  encuadre(capa.getBounds());
  return true;
}
async function verRuta(o, d) {
  finRuta(); clearInterval(timer); enlace(null); quitarCamino();
  actual = { tipo: 'ruta' }; acciones(actual);
  const yo = actual;
  if (marcaSel) map.removeLayer(marcaSel);
  // encuadre en la parte del mapa que el panel no tapa (en el móvil el panel ocupa la parte de abajo)
  const encuadre = b => {
    const abajo = ancho() ? 40 : Math.min(innerHeight - 260, Math.max(innerHeight * .5, innerHeight - $('panel').getBoundingClientRect().top) + 24);
    map.fitBounds(b, { paddingTopLeft: [40, ancho() ? 40 : 110], paddingBottomRight: [40, Math.round(abajo)], maxZoom: 16 });
  };
  encuadre(L.latLngBounds([[o.lat, o.lon], [d.lat, d.lon]]));
  $('pn').textContent = o.nombre + ' → ' + d.nombre; $('ps').textContent = 'Cómo llegar'; $('seg').classList.add('oculto');
  $('lista').innerHTML = ''; $('nota').textContent = ''; $('panel').classList.remove('oculto', 'mini');
  const ref = x => x.tipo === 'parada' ? x.id : `@${x.lat.toFixed(5)},${x.lon.toFixed(5)}`;
  let j; try { j = await json(`/api/ruta?desde=${encodeURIComponent(ref(o))}&hasta=${encodeURIComponent(ref(d))}&dn=${encodeURIComponent(o.nombre)}&hn=${encodeURIComponent(d.nombre)}`); }
  catch (_) { $('lista').innerHTML = '<p class="vacio">No se pudo calcular. Comprueba tu conexión.</p>'; return; }
  if (actual !== yo) return;
  const todas = [...new Set(j.opciones.flatMap(t => t.pasos.filter(p => p.tipo === 'bus').map(p => p.linea)))];
  resaltar(todas);
  // «Elegir camino»: en el mapa queda solo ese camino (los trozos de línea que se usan, las paradas y lo que se camina)
  const boton = (r, camino, pasos) => {
    const b = document.createElement('button'); b.className = 'elegir'; b.textContent = 'Elegir camino';
    b.onclick = async () => {
      if (capaCamino) { quitarCamino(); resaltar(todas); encuadre(L.latLngBounds([[o.lat, o.lon], [d.lat, d.lon]])); return; }
      b.textContent = 'Cargando…';
      const ok = await mostrarCamino(camino, r, pasos, encuadre, o, d);
      if (actual !== yo) return;
      b.textContent = ok ? 'Ver todas las opciones' : 'Elegir camino';
      if (ok) {   // ver el mapa grande con el camino; tocando el panel plegado se vuelven a ver los pasos
        const v = document.createElement('button'); v.className = 'elegir vermapa'; v.textContent = 'Ver el mapa';
        v.onclick = e => { e.stopPropagation(); plegar(true); };
        bs.appendChild(v);
        const res = r.querySelector('.when b')?.textContent || '';
        capaCamino._resumen = [...r.querySelectorAll('.badge')].map(x => x.textContent).join(' › ') + ' · ' + res;
        capaCamino._encuadre = encuadre;
      }
    };
    const bs = document.createElement('div'); bs.className = 'botones';
    bs.appendChild(b); r.appendChild(bs);
  };
  const l = $('lista');
  if (!j.opciones.length) { l.innerHTML = '<p class="vacio">No hay forma de llegar en las próximas horas, ni directa ni con transbordos.</p>'; return; }
  if (o.tipo !== 'parada' || d.tipo !== 'parada') {
    const h = document.createElement('h3');
    h.textContent = 'Se miran todas las paradas a las que puedes ir andando';
    l.appendChild(h);
  }
  j.opciones.forEach(t => {
    const buses = t.pasos.filter(p => p.tipo === 'bus');
    const r = document.createElement('div'); r.className = 'row';
    const dur = t.duracion >= 60 ? `${Math.floor(t.duracion / 60)} h ${t.duracion % 60} min` : `${t.duracion} min`;
    const tipo = t.transbordos ? `${t.transbordos} transbordo${t.transbordos > 1 ? 's' : ''}` : 'directa';
    r.innerHTML = `<div class="trb">${buses.map(p => `<button class="badge" data-linea="${esc(p.linea)}" style="background:${esc(colores[p.linea] || '#555')}">${esc(p.linea)}</button>`).join('<span>›</span>')}</div>
      <div class="when"><b>Sale a las ${esc(t.sale)}${t.manana ? ' (mañana)' : ''}</b><small>Llega antes de las ${esc(t.llega)} · ${dur} · ${tipo}</small></div>`;
    const ol = document.createElement('ul'); ol.className = 'pasos';
    ol.innerHTML = t.pasos.map(p => p.tipo === 'pie'
      ? `<li>Camina ${p.minutos} min hasta <b>${esc(p.hasta)}</b>${p.salir ? ` (sal a las ${esc(p.salir)})` : ''}</li>`
      : `<li style="--c:${esc(colores[p.linea] || '#555')}">${p.espera != null ? `Cambia (espera ${p.espera} min): coge` : 'Coge'} la <b>${esc(p.linea)}</b> en <b>${esc(p.desde)}</b> a las ${esc(p.sale)} y bájate en <b>${esc(p.hasta)}</b> (antes de las ${esc(p.llega)})${p.nota && !p.espera ? ` · ${esc(p.nota)}` : ''}</li>`).join('');
    r.appendChild(ol); boton(r, t.camino, ol); l.appendChild(r);
  });
  $('nota').textContent = 'Horas de salida: lo más pronto que puede pasar. Horas de llegada: lo más tarde. En los transbordos se dejan al menos 5 min de margen. Los minutos andando son aproximados. ' + AVISO_RECORRIDOS;
}
// Origen o destino que no es una parada: tu ubicación (se pide al usuario) o un pueblo / lugar
function miUbicacion() {
  return new Promise((ok, mal) => {
    if (!navigator.geolocation) return mal(new Error('Tu navegador no permite la ubicación.'));
    navigator.geolocation.getCurrentPosition(pos => {
      const { latitude: lat, longitude: lon } = pos.coords;
      if (marcaYo) map.removeLayer(marcaYo);
      marcaYo = L.circleMarker([lat, lon], { radius: 8, color: '#fff', weight: 3, fillColor: '#1a73e8', fillOpacity: 1 }).addTo(map);
      ok({ tipo: 'yo', nombre: 'Tu ubicación', lat, lon });
    }, () => mal(new Error('No pude obtener tu ubicación. Actívala en los ajustes del navegador.')), { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
  });
}
async function irAqui(p) { try { verRuta(await miUbicacion(), p); } catch (e) { toast(e.message); } }
async function desdeAqui() { try { empezarRuta(await miUbicacion()); } catch (e) { toast(e.message); } }
