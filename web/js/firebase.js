// Firebase: inicio de sesión con Google y notificaciones push (módulo ES). Deja sus funciones en window.
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, signInWithPopup, signInWithRedirect, getRedirectResult, GoogleAuthProvider, onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import { getMessaging, getToken, onMessage, isSupported } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging.js";

// authDomain propio (proxy /__/auth/* de Flask): evita el bloqueo de almacenamiento de terceros en móvil.
// Actívalo cuando hayas añadido https://tiadhe-backend.onrender.com/__/auth/handler en el cliente OAuth de Google Cloud.
// Para probarlo antes de activarlo del todo: abre la app con ?authdomain=propio
const AUTH_DOMAIN_PROPIO = false;
const usarPropio = AUTH_DOMAIN_PROPIO || new URLSearchParams(location.search).get("authdomain") === "propio";

const firebaseConfig = {
  apiKey: "AIzaSyBlcA2zXuqVpQRnwso0aZ83HjRxj1KWo_k",   // clave web pública: se protege con restricciones en Google Cloud, no ocultándola
  authDomain: usarPropio ? location.host : "guaguas-ftv1.firebaseapp.com",
  projectId: "guaguas-ftv1",
  storageBucket: "guaguas-ftv1.firebasestorage.app",
  messagingSenderId: "492276628357",
  appId: "1:492276628357:web:1c35bd297d7c68f40c62ec",
  measurementId: "G-29X3LWCFY5"
};
window.VAPID_KEY = "BNApzvqctw8OIzaUdQbvfUerCqgZRJxPJ3jgkrmOd_txqwb8jWbn4zLDrBLLr-emzqHVkIGoV2Uh9coLAxsCffo";

let ok = false;
try {
  const app = initializeApp(firebaseConfig);
  const auth = getAuth(app);
  const provider = new GoogleAuthProvider();
  window.firebaseAuth = auth;
  window.firebaseSignIn = () => signInWithPopup(auth, provider);
  window.firebaseSignInRedirect = () => signInWithRedirect(auth, provider);
  window.firebaseGetRedirectResult = () => getRedirectResult(auth);
  window.firebaseSignOut = () => signOut(auth);
  window.firebaseCurrentUser = () => auth.currentUser;
  window.firebaseAuthReady = () => auth.authStateReady();
  window.firebaseOnAuthChanged = cb => onAuthStateChanged(auth, cb);
  ok = true;

  // Messaging es opcional: en navegadores sin soporte (p. ej. Safari iOS fuera de la PWA instalada) getMessaging lanza
  // y antes tumbaba TODO el módulo, dejando "Firebase Auth no disponible".
  try {
    if (await isSupported()) {
      const messaging = getMessaging(app);
      window.firebaseMessaging = messaging;
      window.firebaseGetToken = reg => getToken(messaging, { vapidKey: window.VAPID_KEY, serviceWorkerRegistration: reg });
      window.firebaseOnMessage = cb => onMessage(messaging, cb);
    }
  } catch (e) { console.warn("Firebase Messaging no disponible:", e.message); }
} catch (e) {
  console.error("Firebase no pudo iniciarse:", e.code || "", e.message);
}
window.__firebaseResolve(ok);
