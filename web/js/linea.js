// Guaguas FTV · ver una línea: recorrido resaltado, próximas salidas y sus paradas.
// ── Ver una línea: recorrido resaltado, próximas salidas y sus paradas en orden ──
$('lista').addEventListener('click', e => { const b = e.target.closest('button.badge[data-linea]'); if (b) abrirLinea(b.dataset.linea); });
async function abrirLinea(num) {
  let d; try { d = await json('/api/linea/' + encodeURIComponent(num)); } catch (_) { return toast('No se pudo cargar la línea.'); }
  finRuta(); clearInterval(timer); enlace(null); quitarCamino();
  actual = { tipo: 'linea', num }; acciones(actual);
  if (marcaSel) map.removeLayer(marcaSel);
  resaltar([num]);
  const pts = (rutas[num] || []).flatMap(c => c._base);
  if (pts.length) map.fitBounds(L.latLngBounds(pts), { paddingTopLeft: [20, 20], paddingBottomRight: [20, ancho() ? 20 : Math.round(innerHeight * .45)] });
  $('pn').textContent = 'Línea ' + d.num; $('ps').textContent = d.nombre;
  $('seg').classList.add('oculto'); $('nota').textContent = 'Toca una parada para ver sus horarios. ' + AVISO_RECORRIDOS;
  const l = $('lista'); l.innerHTML = '';
  const h = t => { const x = document.createElement('h3'); x.textContent = t; l.appendChild(x); };
  h('Próximas salidas');
  [['ida', d.origen], ['vuelta', d.destino]].forEach(([s, desde]) => {
    if (!d.salidas[s] || !d.salidas[s].length) return;
    const x = document.createElement('div'); x.className = 'horas';
    x.innerHTML = `<b>Desde ${esc(desde || s)}:</b> ${d.salidas[s].map(esc).join(' · ')}`; l.appendChild(x);
  });
  h(`Paradas (${d.paradas.length})`);
  d.paradas.forEach(pa => {
    const b = document.createElement('button'); b.className = 'pl';
    b.innerHTML = `<i style="--c:${esc(d.color)};background:${esc(d.color)}"></i>${esc(pa.nombre)}`;
    b.onclick = () => { const p = paradas.find(x => x.id === pa.id); if (p) { foco(p); elegir({ tipo: 'parada', ...p }); } };
    l.appendChild(b);
  });
  $('panel').classList.remove('oculto', 'mini'); $('panel').scrollTop = 0;
}
