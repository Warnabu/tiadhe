// Guaguas FTV · cuenta de Google, modo (horarios / tiempo real), bienvenida y ajustes.
// ── Cuenta, modos y ajustes ──
let authListo = false;
const usuario = () => (window.firebaseCurrentUser && window.firebaseCurrentUser()) || null;
const esMovil = () => /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) || matchMedia('(display-mode: standalone)').matches;
function mensajeError(e) {
  const c = (e && e.code) || '';
  if (c === 'auth/network-request-failed') return 'No se pudo conectar con Google. Sigues con los horarios oficiales.';
  if (c === 'auth/unauthorized-domain') return 'Dominio no autorizado en Firebase (Authentication › Settings › Authorized domains).';
  if (c === 'auth/popup-closed-by-user' || c === 'auth/cancelled-popup-request') return null;
  return 'No se pudo iniciar sesión' + (c ? ` (${c})` : '') + '.';
}
function ui() {
  const u = usuario(), d = Guaguas.getModoDinamico();
  document.body.classList.add('con-sesion');   // el botón de ajustes está siempre (también sin cuenta)
  $('gear').classList.remove('oculto');
  const c = $('chip'); c.classList.toggle('live', d);
  c.innerHTML = d ? '<i class="pto"></i>Tiempo real'
    : Guaguas.tiempoRealDisponible ? '<i class="pto"></i>Horarios oficiales · <em>Activar tiempo real</em>'
    : '<i class="pto"></i>Tiempo real · <em>próximamente</em>';
  if (!$('sheet').classList.contains('oculto')) ajustes();
}
async function loginGoogle() {
  if (esMovil()) { await window.firebaseSignInRedirect(); return 'redirect'; }
  try { await window.firebaseSignIn(); return 'popup'; }
  catch (e) { if (e.code === 'auth/popup-blocked') { await window.firebaseSignInRedirect(); return 'redirect'; } throw e; }
}
// El tiempo real y los avisos no necesitan cuenta: cada dispositivo firma sus avisos (cliente.js).
// La cuenta de Google es opcional (paradas favoritas guardadas).
async function iniciarSesion() {
  if (!authListo) { toast('El inicio de sesión no está disponible ahora mismo.'); return false; }
  if (usuario()) return true;
  try {
    sessionStorage.setItem('pendingDinamico', '1');
    if ((await loginGoogle()) === 'redirect') return 'redirect';
    sessionStorage.removeItem('pendingDinamico'); return true;
  } catch (e) { sessionStorage.removeItem('pendingDinamico'); const m = mensajeError(e); if (m) toast(m); return false; }
}
const mostrarIntro = () => $('intro').classList.remove('oculto');
const ocultarIntro = () => { $('intro').classList.add('oculto'); setTimeout(() => map.invalidateSize(), 50); };
$('i-free').onclick = () => { Guaguas.setModoDinamico(true); ocultarIntro(); ui(); };
$('i-google').onclick = async () => { Guaguas.setModoDinamico(true); const r = await iniciarSesion(); if (r === true) { ocultarIntro(); ui(); } };
$('chip').onclick = () => {
  if (!Guaguas.tiempoRealDisponible) return toast('Muy pronto podrás ver en tiempo real dónde está tu guagua. Mientras tanto, la app muestra los horarios oficiales.');
  if (Guaguas.getModoDinamico()) ajustes(true); else { Guaguas.setModoDinamico(true); ui(); }
};
$('gear').onclick = () => ajustes(true);
$('sok').onclick = () => $('sheet').classList.add('oculto');
$('sheet').onclick = e => { if (e.target === $('sheet')) $('sheet').classList.add('oculto'); };

function ajustes(abrir) {
  if (abrir) $('sheet').classList.remove('oculto');
  $('shead').querySelector('b').textContent = 'Ajustes';
  const u = usuario(), d = Guaguas.getModoDinamico(), p = Guaguas.perfil(), t = localStorage.getItem('tema') || 'auto';
  const nombre = u ? (u.displayName || u.email || 'Mi cuenta') : 'Mi cuenta';
  const desde = u && u.metadata && u.metadata.creationTime ? new Date(u.metadata.creationTime).toLocaleDateString('es', { month: 'long', year: 'numeric' }) : '';
  const seg = (id, ops, val) => `<div class="seg" data-id="${id}">${ops.map(([v, l]) => `<button data-v="${v}" class="${v === val ? 'on' : ''}">${l}</button>`).join('')}</div>`;
  const notif = !window.Notification ? 'No disponible' : Notification.permission === 'granted' ? 'Activadas' : Notification.permission === 'denied' ? 'Bloqueadas' : 'Activar';
  $('sbody').innerHTML = `
    ${u ? `<div class="acct"><div class="av">${esc(nombre[0].toUpperCase())}</div><div><b>${esc(nombre)}</b>${u.email ? `<small>${esc(u.email)}</small>` : ''}${desde ? `<small>Miembro desde ${esc(desde)}</small>` : ''}</div></div>`
         : `<div class="grp"><button class="li" id="s-login"><span>Iniciar sesión con Google</span><em>Paradas favoritas</em></button></div>`}
    ${d && p ? `<h4>Fiabilidad</h4><div class="grp"><div class="rel"><div class="relh"><b>${esc(p.nivel)}</b><span>${p.puntos} puntos</span></div><div class="bar"><i style="width:${Math.max(3, Math.min(100, p.puntos))}%"></i></div><small>${p.confirmadas} de ${p.subidas} avisos confirmados por otras personas</small></div></div>` : ''}
    <h4>Preferencias</h4><div class="grp">
      ${Guaguas.tiempoRealDisponible ? `<div class="li"><span>Datos</span>${seg('modo', [['estatico', 'Horarios'], ['dinamico', 'Tiempo real']], d ? 'dinamico' : 'estatico')}</div>`
        : '<div class="li"><span>Datos</span><em>Horarios oficiales</em></div><div class="li"><span>Tiempo real</span><em>Próximamente</em></div>'}
      <div class="li"><span>Apariencia</span>${seg('tema', [['auto', 'Auto'], ['light', 'Claro'], ['dark', 'Oscuro']], t)}</div>
      <button class="li" id="s-notif"><span>Notificaciones</span><em>${notif}</em></button></div>
    ${u ? `<h4></h4><div class="grp"><button class="li red" id="s-out">Cerrar sesión</button></div>` : ''}`;
}
$('sbody').onclick = async e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.filtro) { $('sheet').classList.add('oculto'); soloLinea(b.dataset.filtro); abrirLinea(b.dataset.filtro); return; }
  const id = b.parentElement.dataset && b.parentElement.dataset.id;
  if (id === 'tema') { localStorage.setItem('tema', b.dataset.v); tema(); ajustes(); }
  else if (id === 'modo') Guaguas.setModoDinamico(b.dataset.v === 'dinamico');
  else if (b.id === 's-login') { if ((await iniciarSesion()) === true) ajustes(); }
  else if (b.id === 's-notif') { const r = await Guaguas.activarNotificaciones(); toast(r === 'ok' ? 'Notificaciones activadas.' : r === 'denegado' ? 'Las notificaciones están bloqueadas en el navegador.' : 'Este navegador no admite notificaciones.'); ajustes(); }
  else if (b.id === 's-out') {
    try { await window.firebaseSignOut(); } catch (_) {}
    Guaguas.setInternalToken(null); $('sheet').classList.add('oculto'); ui();
  }
};

async function exchangeFirebaseToken(idToken) {
  try {
    const r = await fetch('/api/firebase-token', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id_token: idToken }) });
    const j = await r.json().catch(() => ({}));
    if (j.ok && j.token) { Guaguas.setInternalToken(j.token); return true; }
  } catch (e) { console.warn('firebase-token:', e.message); }
  return false;
}
async function handleRedirectResult() {
  const pendiente = !!sessionStorage.getItem('pendingDinamico');
  let user = null, err = null;
  try { const r = await window.firebaseGetRedirectResult(); user = r && r.user; } catch (e) { err = e; console.warn('redirect:', e.code, e.message); }
  try { await window.firebaseAuthReady(); } catch (_) {}
  user = user || usuario(); sessionStorage.removeItem('pendingDinamico');
  if (user && (pendiente || localStorage.getItem('modoDinamico') === null)) { Guaguas.setModoDinamico(true); ocultarIntro(); ui(); Guaguas.activarNotificaciones(false); return; }
  if (err) { const m = mensajeError(err); if (m) toast(m + ' [' + (err.code || err.message) + ']'); }
  else if (pendiente) toast('Google te devolvió a la app pero la sesión no se guardó. Prueba con ?authdomain=propio al final de la dirección.', 12000);
}
