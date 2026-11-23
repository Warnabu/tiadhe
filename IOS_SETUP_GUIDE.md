# iOS App Setup Guide - Guaguas FTV

## Requisitos Previos

### Hardware/Software
- **Mac** con macOS 13+ (Ventura o posterior)
- **Xcode 15+** (descarga desde App Store, ~12GB)
- **Cuenta Apple Developer** (gratuita para testing, $99/año para App Store)
- **iPhone físico** (simulador no recibe push notifications)
- **Cable USB** para conectar iPhone al Mac

### Cuentas necesarias
- [ ] Cuenta Apple Developer (developer.apple.com)
- [ ] Cuenta Firebase (ya tienes el proyecto `guaguas-ftv1`)
- [ ] Cuenta Google (para Google Sign-In)

---

## Paso 1: Descargar e Instalar Xcode

1. Abre **App Store** en el Mac
2. Busca **Xcode**
3. Click **Get** (descarga ~12GB, puede tardar 30-60 min)
4. Una vez instalado, abre Xcode → acepta licencia → instala componentes adicionales

---

## Paso 2: Obtener el Código

### Opción A: Clonar repo (si tienes git)
```bash
cd ~/Documents
git clone https://github.com/Warnabu/tiadhe.git
cd tiadhe/ios/GuaguasFTV
```

### Opción B: Copiar carpeta manualmente
1. Copia la carpeta `ios/GuaguasFTV/` desde tu Windows al Mac (USB, email, etc.)
2. Colócala en `~/Documents/GuaguasFTV/`

---

## Paso 3: Crear Proyecto Xcode

### 3.1 Crear nuevo proyecto
1. Abre **Xcode**
2. **File → New → Project**
3. Selecciona **iOS → App**
4. Configuración:
   - **Product Name**: `GuaguasFTV`
   - **Team**: selecciona tu Apple ID (o "Add Account" si no aparece)
   - **Organization Identifier**: `com.tuapellido` (ej: `com.warnabu`)
   - **Interface**: SwiftUI
   - **Language**: Swift
   - **Storage**: None
   - **Include Tests**: No
5. **Next** → elige carpeta `~/Documents/GuaguasFTV/` → **Create**

### 3.2 Reemplazar archivos del template
1. En el navegador de Xcode (panel izquierdo), **elimina** los archivos generados:
   - `ContentView.swift` (el que generó Xcode)
   - `GuaguasFTVApp.swift` (el que generó Xcode)
2. **Arrastra** los archivos de `ios/GuaguasFTV/` a la carpeta del proyecto en Xcode:
   - `GuaguasFTVApp.swift`
   - `AuthManager.swift`
   - `APIClient.swift`
   - `LocationManager.swift`
   - `ContentView.swift`
3. En el diálogo que aparece: **Copy items if needed** ✅ + **Create groups** ✅ → **Finish**

---

## Paso 4: Añadir Firebase (Swift Package Manager)

1. En Xcode: **File → Add Package Dependencies...**
2. En el campo de búsqueda: `https://github.com/firebase/firebase-ios-sdk`
3. Click **Add Package**
4. En la lista de productos, selecciona:
   - ✅ **FirebaseAuth**
   - ✅ **FirebaseMessaging**
   - ✅ **FirebaseCore**
5. Click **Add Package**
6. Espera a que descargue (puede tardar 1-2 min)

---

## Paso 5: Añadir Google Sign-In

1. **File → Add Package Dependencies...**
2. URL: `https://github.com/google/GoogleSignIn-iOS`
3. Click **Add Package**
4. Selecciona **GoogleSignIn** → **Add Package**

---

## Paso 6: Añadir GoogleService-Info.plist

1. Ve a **Firebase Console** → Project Settings → General → Your apps → iOS app
2. Click **Download GoogleService-Info.plist**
3. En Xcode: **arrastra** el archivo `GoogleService-Info.plist` a la carpeta del proyecto (junto a los otros archivos)
4. En el diálogo: **Copy items if needed** ✅ → **Finish**
5. **IMPORTANTE**: Verifica que el archivo aparece en el navegador de Xcode (panel izquierdo)

---

## Paso 7: Configurar URL Scheme (Google Sign-In)

1. En Xcode → selecciona el **proyecto** (icono azul arriba) → **Target GuaguasFTV** → **Info** tab
2. Busca **URL Types** → click **+**
3. En **URL Schemes**: pega el valor de `REVERSED_CLIENT_ID` de tu `GoogleService-Info.plist`
   - Ejemplo: `com.googleusercontent.apps.492276628357-mt7tmbthuveuau2t6gk24qqi9hfb168d`
4. Deja **Role** como `Editor`

---

## Paso 8: Activar Push Notifications

1. En Xcode → **Target GuaguasFTV** → **Signing & Capabilities** tab
2. Click **+ Capability**
3. Busca **Push Notifications** → **Add**
4. Click **+ Capability** de nuevo
5. Busca **Background Modes** → **Add**
6. En Background Modes, marca:
   - ✅ **Remote notifications**

---

## Paso 9: Configurar Signing

1. En **Signing & Capabilities** tab:
   - **Team**: selecciona tu Apple ID
   - **Bundle Identifier**: `com.tuapellido.guaguasftv` (debe coincidir con Firebase)
2. Si dice "Failed to register bundle identifier":
   - Cambia el bundle ID a algo único: `com.tuapellido.guaguasftv2`
   - Actualiza Firebase Console → Project Settings → iOS app → Bundle ID
   - Descarga nuevo `GoogleService-Info.plist`

---

## Paso 10: Conectar iPhone y Ejecutar

1. Conecta iPhone al Mac con cable USB
2. En iPhone: **Confía en este ordenador** → **Trust**
3. En Xcode → barra superior → selecciona tu iPhone (no simulador)
4. Click **Play** (▶) o `Cmd+R`
5. Primera vez: iPhone → **Ajustes → General → VPN y gestión de dispositivos** → **Confía en tu Apple ID**
6. La app se instala y ejecuta

---

## Paso 11: Probar Funcionalidad

### Checklist de pruebas:
- [ ] App arranca sin crash
- [ ] Mapa muestra paradas (puntos rojos)
- [ ] Login Google funciona
- [ ] Al estar <200m de parada aparece banner "Estás cerca"
- [ ] Botón "Me voy a subir" aparece con countdown 2 min
- [ ] Confirmar subida → backend registra
- [ ] Push notification de prueba llega (desde `/api/test-push`)

---

## Troubleshooting Común

### "No such module 'FirebaseCore'"
→ File → Add Package Dependencies → firebase-ios-sdk → Add

### "GoogleService-Info.plist not found"
→ Arrastra el archivo a la raíz del proyecto en Xcode, asegúrate de "Copy items if needed"

### "Failed to register bundle identifier"
→ Cambia Bundle ID a algo único + actualiza Firebase Console

### "Push notifications not working"
→ Solo funciona en dispositivo físico, no simulador
→ Verifica que Push Notifications capability está añadida
→ Verifica que el iPhone tiene permisos de notificación

### "Google Sign-In fails"
→ Verifica URL Scheme = REVERSED_CLIENT_ID de GoogleService-Info.plist
→ Verifica que Google Auth está enabled en Firebase Console

---

## Estructura de Archivos en Xcode

```
GuaguasFTV/
├── GuaguasFTVApp.swift       # Entry point
├── AuthManager.swift          # Firebase Auth + Google/Apple
├── APIClient.swift            # REST client
├── LocationManager.swift      # GPS
├── ContentView.swift          # UI principal
├── GoogleService-Info.plist   # Firebase config (añadir manualmente)
└── (Packages)
    ├── FirebaseAuth
    ├── FirebaseMessaging
    ├── FirebaseCore
    └── GoogleSignIn
```

---

## Variables de Entorno (Xcode)

Para testing local, puedes añadir en **Scheme → Edit Scheme → Run → Arguments → Environment Variables**:
- `BASE_URL` = `http://192.168.1.78:5000` (tu IP local del servidor)

`APIClient.swift` ya lee `BASE_URL`; si no está definida usa `https://tiadhe-backend.onrender.com`.

---

## Contacto

Si tienes problemas, revisa:
1. Logs de Xcode (panel inferior)
2. Logs de Render (pestaña Logs) — servicio `tiadhe-backend`
3. Firebase Console → Authentication → Users (verifica que el usuario se creó)
4. Firebase Console → Cloud Messaging → Send test message