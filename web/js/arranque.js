// Guaguas FTV · arranque: se carga el último y pone todo en marcha.
// ── Arranque ──
try {   // antes el tiempo real exigía Google; quien eligió "sin cuenta" pasa a tiempo real (una sola vez)
  if (!localStorage.getItem('modoV2')) { if (localStorage.getItem('modoDinamico') === 'false') Guaguas.setModoDinamico(true); localStorage.setItem('modoV2', '1'); }
} catch (_) {}
if (localStorage.getItem('modoDinamico') === null && !sessionStorage.getItem('pendingDinamico')) mostrarIntro();
ui();
Guaguas.onModoChange(() => { ui(); if (actual) cargar(); });
Guaguas.onPerfil(() => { if (!$('sheet').classList.contains('oculto')) ajustes(); });
Guaguas.iniciar().catch(e => console.warn('identidad de dispositivo no disponible:', e.message));
datos().then(() => {
  const id = new URLSearchParams(location.search).get('parada');
  const p = id && paradas.find(x => x.id === id);
  if (p) { foco(p, 16); elegir({ tipo: 'parada', ...p }); }
}).catch(e => { console.warn('datos:', e.message); toast('No se pudieron cargar las paradas. Comprueba tu conexión y recarga la página.', 12000); });
(async () => {
  authListo = await window.firebaseListo;
  if (!authListo) return console.warn('Firebase no disponible: modo estático.');
  await handleRedirectResult();
  if (localStorage.getItem('modoDinamico') === null) mostrarIntro();
  window.firebaseOnAuthChanged(async u => {
    if (u) { try { if (await exchangeFirebaseToken(await u.getIdToken())) cargarFavoritas(); } catch (e) { console.warn(e.message); } }
    else { Guaguas.setInternalToken(null); favs = new Set(); }
    ui();
  });
})();
if ('serviceWorker' in navigator) {
  window.__swTeniaControl = !!navigator.serviceWorker.controller;
  window.swListo = navigator.serviceWorker.register('/sw.js').then(r => { r.update().catch(() => {}); return r; }).catch(e => { console.warn('SW:', e.message); return null; });
  navigator.serviceWorker.addEventListener('controllerchange', () => { if (!window.__swRecargado && window.__swTeniaControl) { window.__swRecargado = true; location.reload(); } });
}
