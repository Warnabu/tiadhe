import SwiftUI
import MapKit
import CoreLocation

struct ContentView: View {
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var locationManager: LocationManager
    @EnvironmentObject var apiClient: APIClient

    @State private var region = MKCoordinateRegion(
        center: CLLocationCoordinate2D(latitude: 28.3587, longitude: -14.0549),
        span: MKCoordinateSpan(latitudeDelta: 0.05, longitudeDelta: 0.05)
    )
    @State private var paradas: [Parada] = []
    @State private var lineas: [Linea] = []
    @State private var selectedParada: Parada?
    @State private var showingParadaDetail = false
    @State private var showingAuth = false
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var userLocation: CLLocationCoordinate2D?
    @State private var showingSubidaConfirmation = false
    @State private var pendingSubida: (Parada, Linea, String)?
    @State private var isSubidaButtonEnabled = false
    @State private var subidaButtonCountdown = 0
    @State private var countdownTimer: Timer?

    var body: some View {
        ZStack {
            Map(coordinateRegion: $region,
                showsUserLocation: true,
                userTrackingMode: .constant(.follow),
                annotationItems: paradas) { parada in
                MapAnnotation(coordinate: parada.coordinate) {
                    ParadaAnnotationView(parada: parada, isSelected: selectedParada?.id == parada.id) {
                        selectedParada = parada
                        showingParadaDetail = true
                    }
                }
            }
            .ignoresSafeArea()

            VStack {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Guaguas FTV")
                            .font(.title2).bold()
                            .foregroundColor(.white)
                        if authManager.isAuthenticated {
                            Text("Conectado como \(authManager.user?.email ?? "usuario")")
                                .font(.caption)
                                .foregroundColor(.white.opacity(0.8))
                        } else {
                            Text("Inicia sesión para usar todas las funciones")
                                .font(.caption)
                                .foregroundColor(.white.opacity(0.8))
                        }
                    }
                    Spacer()
                    if authManager.isAuthenticated {
                        Button(action: { authManager.signOut() }) {
                            Image(systemName: "person.crop.circle.badge.xmark")
                                .font(.title2)
                                .foregroundColor(.white)
                        }
                    } else {
                        Button(action: { showingAuth = true }) {
                            Image(systemName: "person.crop.circle.badge.plus")
                                .font(.title2)
                                .foregroundColor(.white)
                        }
                    }
                }
                .padding()
                .background(Color.black.opacity(0.6))
                .cornerRadius(12)
                .padding()

                Spacer()

                if let parada = selectedParada {
                    ParadaBottomSheet(parada: parada,
                                      lineas: lineas.filter { parada.lineas.contains($0.num) },
                                      onSubida: { linea, viaje in
                                          pendingSubida = (parada, linea, viaje)
                                          showingSubidaConfirmation = true
                                      },
                                      onDismiss: { selectedParada = nil }
                    )
                    .transition(.move(edge: .bottom))
                }

                if isSubidaButtonEnabled && !authManager.isAuthenticated {
                    SubidaButtonPrompt(onLogin: { showingAuth = true })
                        .transition(.move(edge: .bottom))
                }
            }
        }
        .alert("Error", isPresented: .constant(errorMessage != nil), actions: {
            Button("OK") { errorMessage = nil }
        }, message: { Text(errorMessage ?? "") })
        .sheet(isPresented: $showingAuth) {
            AuthView()
                .environmentObject(authManager)
        }
        .alert("¿Confirmar subida?", isPresented: $showingSubidaConfirmation, actions: {
            Button("Cancelar", role: .cancel) { pendingSubida = nil }
            Button("Confirmar", role: .destructive) {
                if let (parada, linea, viaje) = pendingSubida {
                    confirmarSubida(parada: parada, linea: linea, viaje: viaje)
                }
                pendingSubida = nil
            }
        }, message: {
            if let (parada, linea, _) = pendingSubida {
                Text("¿Confirmas que te subes a la línea \(linea.num) (\(linea.nombre)) en \(parada.nombre)?")
            }
        })
        .onAppear {
            locationManager.requestPermission()
            locationManager.startUpdating()
            Task { await cargarDatosIniciales() }
            if let loc = locationManager.location {
                userLocation = loc.coordinate
                region.center = loc.coordinate
            }
        }
        .onReceive(locationManager.$location.compactMap { $0 }) { loc in
            userLocation = loc.coordinate
            checkSubidaButtonEligibility()
        }
        .onReceive(NotificationCenter.default.publisher(for: .fcmTokenReceived)) { notification in
            if let token = notification.object as? String {
                Task {
                    try? await apiClient.registerFCMToken(token)
                }
            }
        }
    }

    private func cargarDatosIniciales() async {
        isLoading = true
        do {
            async let lineasTask = apiClient.getLineas()
            async let paradasTask = apiClient.getParadas()
            let (loadedLineas, loadedParadas) = try await (lineasTask, paradasTask)
            DispatchQueue.main.async {
                self.lineas = loadedLineas
                self.paradas = loadedParadas
                self.isLoading = false
            }
        } catch {
            DispatchQueue.main.async {
                self.errorMessage = error.localizedDescription
                self.isLoading = false
            }
        }
    }

    private func checkSubidaButtonEligibility() {
        guard let userLoc = userLocation,
              let parada = selectedParada else {
            updateSubidaButtonState(enabled: false)
            return
        }

        let paradaLoc = CLLocation(latitude: parada.lat, longitude: parada.lon)
        let userLocCL = CLLocation(latitude: userLoc.latitude, longitude: userLoc.longitude)
        let distance = userLocCL.distance(from: paradaLoc)

        let enabled = distance <= 200 && authManager.isAuthenticated
        updateSubidaButtonState(enabled: enabled)
    }

    private func updateSubidaButtonState(enabled: Bool) {
        if enabled && !isSubidaButtonEnabled {
            isSubidaButtonEnabled = true
            subidaButtonCountdown = 120
            countdownTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
                if subidaButtonCountdown > 0 {
                    subidaButtonCountdown -= 1
                } else {
                    isSubidaButtonEnabled = false
                    countdownTimer?.invalidate()
                    countdownTimer = nil
                }
            }
        } else if !enabled && isSubidaButtonEnabled {
            isSubidaButtonEnabled = false
            countdownTimer?.invalidate()
            countdownTimer = nil
        }
    }

    private func confirmarSubida(parada: Parada, linea: Linea, viaje: String) {
        guard let userLoc = userLocation,
              let authToken = authManager.internalToken else { return }

        apiClient.setAuthToken(authToken)

        let nonce = UUID().uuidString
        let ts = Int(Date().timeIntervalSince1970)
        let cuerpo = "\(nonce)\(ts)\(viaje)\(parada.id)"
        let pubKey = "dummy_pubkey"

        let request = SubidaRequest(
            viaje: viaje,
            parada: parada.id,
            lat: userLoc.latitude,
            lon: userLoc.longitude,
            acc: locationManager.locationAccuracy > 0 ? locationManager.locationAccuracy : 50,
            nonce: nonce,
            sig: "dummy_signature",
            pub: pubKey,
            ts: ts
        )

        Task {
            do {
                let response = try await apiClient.enviarSubida(request)
                DispatchQueue.main.async {
                    if response.ok {
                        self.errorMessage = response.mensaje
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    self.errorMessage = error.localizedDescription
                }
            }
        }
    }
}

struct ParadaAnnotationView: View {
    let parada: Parada
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(isSelected ? Color.blue : Color.red)
                    .frame(width: isSelected ? 36 : 28, height: isSelected ? 36 : 28)
                    .shadow(radius: 3)

                Image(systemName: "bus.fill")
                    .font(.system(size: isSelected ? 18 : 14, weight: .bold))
                    .foregroundColor(.white)
            }
        }
        .buttonStyle(PlainButtonStyle())
    }
}

struct ParadaBottomSheet: View {
    let parada: Parada
    let lineas: [Linea]
    let onSubida: (Linea, String) -> Void
    let onDismiss: () -> Void

    @State private var predicciones: [TableroPrediccion] = []
    @State private var isLoading = true

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(parada.nombre)
                        .font(.headline)
                    Text("Líneas: \(lineas.map { $0.num }.joined(separator: ", "))")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Button(action: onDismiss) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title2)
                        .foregroundColor(.secondary)
                }
            }
            .padding()

            if isLoading {
                ProgressView("Cargando horarios...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if predicciones.isEmpty {
                Text("No hay guaguas programadas próximamente")
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .padding()
            } else {
                List(predicciones) { pred in
                    HStack {
                        Circle()
                            .fill(Color(hex: lineas.first { $0.num == pred.linea }?.color ?? "007AFF"))
                            .frame(width: 12, height: 12)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("L\(pred.linea) \(pred.sentido.capitalized) · \(pred.variante)")
                                .font(.subheadline)
                            Text(pred.llegada)
                                .font(.title2).bold()
                        }
                        Spacer()
                        Text(pred.prec.capitalized)
                            .font(.caption)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(Color.blue.opacity(0.2))
                            .cornerRadius(8)
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        let viaje = "\(pred.linea)|\(pred.sentido)|\(pred.variante)|\(pred.llegada)"
                        onSubida(lineas.first { $0.num == pred.linea }!, viaje)
                    }
                }
                .listStyle(.plain)
                .frame(maxHeight: 300)
            }
        }
        .background(Color(.systemBackground))
        .cornerRadius(16, corners: [.topLeft, .topRight])
        .shadow(radius: 10)
        .task {
            await cargarPredicciones()
        }
    }

    private func cargarPredicciones() async {
        do {
            let response = try await APIClient.shared.getTablero(paradaId: parada.id)
            predicciones = response.predicciones
            isLoading = false
        } catch {
            isLoading = false
        }
    }
}

struct SubidaButtonPrompt: View {
    let onLogin: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Image(systemName: "hand.raised.fill")
                    .font(.title2)
                    .foregroundColor(.orange)
                Text("Estás cerca de una parada")
                    .font(.headline)
                Spacer()
            }
            Text("Inicia sesión para usar el botón \"Me voy a subir\" y mejorar los horarios para todos")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Button(action: onLogin) {
                Text("Iniciar sesión")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(16)
        .shadow(radius: 10)
        .padding()
    }
}

struct AuthView: View {
    @EnvironmentObject var authManager: AuthManager
    @Environment(\.dismiss) var dismiss

    var body: some View {
        NavigationView {
            VStack(spacing: 24) {
                Image(systemName: "bus.fill")
                    .font(.system(size: 64))
                    .foregroundColor(.blue)

                Text("Guaguas FTV")
                    .font(.largeTitle).bold()

                Text("Inicia sesión para acceder a todas las funciones")
                    .multilineTextAlignment(.center)
                    .foregroundColor(.secondary)

                VStack(spacing: 16) {
                    Button(action: signInWithGoogle) {
                        HStack {
                            Image("google_logo")
                                .renderingMode(.template)
                                .frame(width: 24, height: 24)
                            Text("Continuar con Google")
                                .fontWeight(.medium)
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)

                    Button(action: signInWithApple) {
                        HStack {
                            Image(systemName: "applelogo")
                                .frame(width: 24, height: 24)
                            Text("Continuar con Apple")
                                .fontWeight(.medium)
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
                .padding(.horizontal, 32)

                if let error = authManager.errorMessage {
                    Text(error)
                        .foregroundColor(.red)
                        .font(.caption)
                }

                Spacer()
            }
            .padding()
            .navigationTitle("Iniciar sesión")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Cancelar") { dismiss() }
                }
            }
        }
    }

    private func signInWithGoogle() {
        guard let windowScene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
              let rootVC = windowScene.windows.first?.rootViewController else { return }
        authManager.signInWithGoogle(presentingViewController: rootVC)
    }

    private func signInWithApple() {
        let request = ASAuthorizationAppleIDProvider().createRequest()
        request.requestedScopes = [.fullName, .email]
        let controller = ASAuthorizationController(authorizationRequests: [request])
        controller.delegate = AppleSignInDelegate(authManager: authManager)
        controller.presentationContextProvider = AppleSignInDelegate(authManager: authManager)
        controller.performRequests()
    }
}

class AppleSignInDelegate: NSObject, ASAuthorizationControllerDelegate, ASAuthorizationControllerPresentationContextProviding {
    let authManager: AuthManager

    init(authManager: AuthManager) {
        self.authManager = authManager
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithAuthorization authorization: ASAuthorization) {
        if let appleCredential = authorization.credential as? ASAuthorizationAppleIDCredential {
            authManager.signInWithApple(credential: appleCredential)
        }
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        authManager.errorMessage = error.localizedDescription
    }

    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        UIApplication.shared.windows.first { $0.isKeyWindow } ?? UIWindow()
    }
}

extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let a, r, g, b: UInt64
        switch hex.count {
        case 3: (a, r, g, b) = (255, (int >> 8) * 17, (int >> 4 & 0xF) * 17, (int & 0xF) * 17)
        case 6: (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
        case 8: (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
        default: (a, r, g, b) = (1, 1, 1, 0)
        }
        self.init(.sRGB, red: Double(r) / 255, green: Double(g) / 255, blue: Double(b) / 255, opacity: Double(a) / 255)
    }
}

extension View {
    func cornerRadius(_ radius: CGFloat, corners: UIRectCorner) -> some View {
        clipShape(RoundedCorner(radius: radius, corners: corners))
    }
}

struct RoundedCorner: Shape {
    var radius: CGFloat = .infinity
    var corners: UIRectCorner = .allCorners

    func path(in rect: CGRect) -> Path {
        let path = UIBezierPath(roundedRect: rect, byRoundingCorners: corners, cornerRadii: CGSize(width: radius, height: radius))
        return Path(path.cgPath)
    }
}