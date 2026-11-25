// Guaguas FTV · el panel de una parada: elegir, cargar y pintar sus próximas guaguas; acciones (favorita, compartir).
function elegir(p) {
  quitarCamino();
  if (rutaDesde && (p.tipo === 'parada' || p.tipo === 'lugar') && !(p.id && p.id === rutaDesde.id)) return verRuta(rutaDesde, p);
  actual = p; filtro = null; marcadas = new Set();
  acciones(p);
  if (p.tipo === 'parada') {
    recordar(p);
    avisos = new Set();
    Guaguas.recordatorios().then(l => {
      avisos = new Set(l.filter(x => x.parada === p.id).map(x => x.viaje));
      if (p === actual && ultimo && avisos.size) pintar(ultimo, p);
    }).catch(() => {});
  }
  enlace(p);
  resaltar(p.tipo === 'parada' ? p.lineas : null);
  if (marcaSel) map.removeLayer(marcaSel);
  marcaSel = L.circleMarker([p.lat, p.lon], { radius: 13, color: '#ff3b30', weight: 3, fill: false }).addTo(map);
  $('pn').textContent = p.nombre; $('ps').textContent = ''; $('lista').innerHTML = '';
  $('panel').classList.remove('oculto', 'mini'); $('sug').classList.add('oculto');
  cargar(); clearInterval(timer); timer = setInterval(cargar, 30000);
}
async function cargar() {
  const p = actual; if (!p || (p.tipo !== 'parada' && p.tipo !== 'osm')) return;
  const modo = Guaguas.getModoDinamico() ? 'dinamico' : 'estatico';
  const url = (p.tipo === 'osm' ? `/api/cerca?lat=${p.lat}&lon=${p.lon}&nombre=${encodeURIComponent(p.nombre)}&` : `/api/parada/${p.id}?`) + `modo=${modo}`;
  try { pintar(await json(url), p); }
  catch (e) { if (p === actual) $('lista').innerHTML = '<p class="vacio">No se pudo cargar. Comprueba tu conexión.</p>'; }
}
// Lo que la comunidad ha hecho hoy: el motivo para avisar
let _act = null;
async function actividad() {
  try {
    if (!_act || Date.now() - _act.t > 60000) _act = { t: Date.now(), d: await json('/api/estadisticas') };
    const d = _act.d;
    if (!d.avisos) return 'Nadie ha avisado aún hoy: cuando te subas, pulsa «Me he subido» y la hora se corregirá para los demás.';
    return `Hoy en la isla: ${d.avisos} aviso${d.avisos > 1 ? 's' : ''} de ${d.personas} persona${d.personas > 1 ? 's' : ''}` +
      (d.confirmadas ? ` · ${d.confirmadas} guagua${d.confirmadas > 1 ? 's' : ''} confirmada${d.confirmadas > 1 ? 's' : ''}.` : '.');
  } catch (_) { return ''; }
}
let avisos = new Set(), ultimo = null;   // viajes con "avísame" pendiente en la parada abierta; último tablero
function pintar(d, p) {
  if (p !== actual) return;
  ultimo = d;
  $('ps').textContent = `${d.dia} · ${d.ahora}` + (d.parada.aprox ? ' · cálculo aproximado' : '');
  const ds = d.grupos.map(g => g.destino), sg = $('seg');
  sg.classList.toggle('oculto', ds.length < 2); sg.innerHTML = '';
  if (ds.length > 1) [['Todos', null], ...ds.map(x => ['→ ' + x, x])].forEach(([t, v]) => {
    const b = document.createElement('button'); b.textContent = t; b.className = filtro === v ? 'on' : '';
    b.onclick = () => { filtro = v; pintar(d, p); }; sg.appendChild(b);
  });
  const l = $('lista'); l.innerHTML = '';
  const gs = d.grupos.filter(g => !filtro || g.destino === filtro);
  if (!gs.length) l.innerHTML = '<p class="vacio">No hay más guaguas registradas para esta parada.</p>';
  gs.forEach(g => {
    const h = document.createElement('h3'); h.textContent = 'Hacia ' + g.destino; l.appendChild(h);
    g.salidas.forEach(s => {
      const vivo = s.prec === 'confirmada' || s.prec === 'vivo';
      const tag = s.prec === 'confirmada' ? `Confirmado · ${s.n}` : s.prec === 'vivo' ? `En directo · ${s.n}` : s.prec === 'oficial' ? 'Hora oficial' : s.cal ? 'Programada' : 'Estimada';
      const r = document.createElement('div'); r.className = 'row';
      r.innerHTML = `<button class="badge" data-linea="${esc(s.linea)}" aria-label="Ver la línea ${esc(s.linea)}" style="background:${esc(colores[s.linea] || '#555')}">${esc(s.linea)}</button>
        <div class="when"><b>${esc(s.texto)}</b>${s.nota ? `<small>${esc(s.nota)}</small>` : ''}</div><span class="tag${vivo ? ' live' : ''}">${tag}</span>`;
      if (p.tipo === 'parada' && s.subir && Guaguas.getModoDinamico()) {
        const w = document.createElement('div'); w.className = 'sub';
        if (marcadas.has(s.viaje)) w.innerHTML = '<span>Avisaste de esta guagua</span>';
        else {
          const b = document.createElement('button'); b.textContent = 'Me he subido a esta guagua';
          b.onclick = async () => {
            b.disabled = true; b.textContent = 'Enviando…';
            try {
              const x = await Guaguas.marcarSubida(p.id, s.viaje); marcadas.add(s.viaje);
              w.innerHTML = '<span>' + (x.estado === 'confirmada' ? 'Confirmado por varias personas' : 'Avisado, esperando a más gente') + '</span>';
              if (x.mensaje) toast(x.mensaje, 9000);
              if (x.estado === 'confirmada') cargar();
            } catch (e) { b.disabled = false; b.textContent = 'Me he subido a esta guagua'; toast(e.message); }
          };
          w.appendChild(b);
        }
        r.appendChild(w);
      } else if (p.tipo === 'parada' && s.minutos > 7 && s.minutos < 18 * 60) {
        // "Avísame 5 min antes": notificación push aunque la web esté cerrada
        const w = document.createElement('div'); w.className = 'sub';
        const b = document.createElement('button');
        const rotulo = () => { b.textContent = avisos.has(s.viaje) ? '🔔 Te avisaremos · cancelar' : '🔔 Avísame 5 min antes'; };
        rotulo();
        b.onclick = async () => {
          const cancelar = avisos.has(s.viaje); b.disabled = true; b.textContent = 'Un momento…';
          try {
            const x = await Guaguas.recordatorio(p.id, s.viaje, cancelar);
            if (cancelar) avisos.delete(s.viaje); else avisos.add(s.viaje);
            toast(x.mensaje || (cancelar ? 'Aviso cancelado.' : 'Te avisaremos.'));
          } catch (e) { toast(e.message, 9000); }
          b.disabled = false; rotulo();
        };
        w.appendChild(b); r.appendChild(w);
      }
      l.appendChild(r);
    });
  });
  $('nota').textContent = ds.length > 1 ? 'Hay guaguas en los dos sentidos: elige el de tu destino.' : '';
  if (p.tipo === 'parada' && Guaguas.getModoDinamico()) actividad().then(t => { if (p === actual && t) $('nota').textContent += ($('nota').textContent ? ' ' : '') + t; });
}
$('cerrar').onclick = () => { quitarCamino(); $('panel').classList.add('oculto'); clearInterval(timer); actual = null; if (marcaSel) map.removeLayer(marcaSel); resaltar(null); enlace(null); finRuta(); };

// ── Barra de acciones de una parada: favorita (con sesión), cómo llegar, compartir ──
let favs = new Set();
function acciones(p) {
  const a = $('acc'); a.innerHTML = '';
  a.classList.toggle('oculto', p.tipo !== 'parada');
  if (p.tipo !== 'parada') return;
  const boton = (t, f) => { const b = document.createElement('button'); b.textContent = t; b.onclick = f; a.appendChild(b); return b; };
  if (Guaguas.conSesion()) boton(favs.has(p.id) ? '★ Favorita' : '☆ Favorita', async () => {
    try { favs = new Set(await Guaguas.favoritas(p.id, !favs.has(p.id))); acciones(p); toast(favs.has(p.id) ? 'Guardada en favoritas.' : 'Quitada de favoritas.'); }
    catch (e) { toast(e.message); }
  });
  boton('Ir aquí', () => irAqui(p));
  boton('Salir desde aquí', () => empezarRuta(p));
  boton('Compartir', () => compartir(p));
}
async function compartir(p) {
  const url = location.origin + '/?parada=' + encodeURIComponent(p.id);
  try {
    if (navigator.share) await navigator.share({ title: 'Guaguas en ' + p.nombre, text: 'Próximas guaguas en ' + p.nombre, url });
    else { await navigator.clipboard.writeText(url); toast('Enlace copiado: pégalo donde quieras.'); }
  } catch (_) {}
}
async function cargarFavoritas() {
  try { favs = new Set((await Guaguas.favoritas()) || []); } catch (_) { favs = new Set(); }
  if (actual && actual.tipo === 'parada') acciones(actual);
}
