// Guaguas FTV · utilidades comunes (el resto de archivos las usan). Se carga el primero.
// Promesa que resuelve (true/false) cuando el módulo de Firebase (js/firebase.js) termina de iniciar.
window.firebaseListo = new Promise(r => { window.__firebaseResolve = r; });

const $ = id => document.getElementById(id);
const AVISO_RECORRIDOS = 'Los recorridos dibujados en el mapa son orientativos: muestran por dónde circula cada línea, pero en algún tramo pueden no coincidir exactamente con las calles por las que pasa la guagua. Las paradas y los horarios son los oficiales.';
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const norm = s => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const json = async u => { const r = await fetch(u); if (!r.ok) throw new Error(u + ' ' + r.status); return r.json(); };
const ancho = () => matchMedia('(min-width:820px)').matches;
function toast(m, ms = 6500) { const t = $('toast'); t.textContent = m; t.classList.add('on'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('on'), ms); }
