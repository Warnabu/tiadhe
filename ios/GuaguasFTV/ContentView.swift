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
    @State private var pendingSubida: (Parada, Salida)?
    @State private var infoMessage: String?
    @State private var isSubidaButtonEnabled = false
    @State private var subidaButtonCountdown = 0
    @State private var countdownTimer: Timer?

    var body: some View {
        ZStack {
            Map(coordinateRegion: $region,
                showsUserLocation: true,
                userTrackingMode: .constant(.none),   // .follow bloqueaba el mapa en la posición del usuario
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
                                      onSubida: { salida in
                                          guard authManager.isAuthenticated else { showingAuth = true; return }
                                          pendingSubida = (parada, salida)
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
        .alert("Error", isPresented: Binding(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } }), actions: {
            Button("OK") { errorMessage = nil }
        }, message: { Text(errorMessage ?? "") })
        .alert("Guaguas FTV", isPresented: Binding(get: { infoMessage != nil }, set: { if !$0 { infoMessage = nil } }), actions: {
            Button("OK") { infoMessage = nil }
        }, message: { Text(infoMessage ?? "") })
        .sheet(isPresented: $showingAuth) {
            AuthView()
                .environmentObject(authManager)
        }
        .alert("¿Confirmar subida?", isPresented: $showingSubidaConfirmation, actions: {
            Button("Cancelar", role: .cancel) { pendingSubida = nil }
            Button("Me he subido") {
                if let pendiente = pendingSubida {
                    confirmarSubida(parada: pendiente.0, salida: pendiente.1)
                }
                pendingSubida = nil
            }
        }, message: {
            if let pendiente = pendingSubida {
                Text("¿Te has subido a la línea \(pendiente.1.linea) de las \(pendiente.1.hora) en \(pendiente.0.nombre)? Cuando otras personas lo confirmen, la hora se actualizará para todos.")
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

        let enabled = distance <= 200   // sin sesión se muestra la invitación a iniciarla
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

    private func confirmarSubida(parada: Parada, salida: Salida) {
        Task {
            do {
                let r = try await apiClient.enviarSubida(paradaId: parada.id, viaje: salida.viaje,
                                                         ubicacion: locationManager.location)
                await MainActor.run { self.infoMessage = r.mensaje }
            } catch {
                await MainActor.run { self.errorMessage = error.localizedDescription }
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
    let onSubida: (Salida) -> Void
    let onDismiss: () -> Void

    @State private var tablero: TableroResponse?
    @State private var isLoading = true
    @State private var error: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(parada.nombre)
                        .font(.headline)
                    Text("Líneas: \(parada.lineas.joined(separator: ", "))")
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

            if isLoading && tablero == nil {
                ProgressView("Cargando horarios...")
                    .frame(maxWidth: .infinity, minHeight: 120)
            } else if let error {
                Text(error)
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120)
                    .padding()
            } else if let t = tablero, !t.grupos.isEmpty {
                List {
                    ForEach(t.grupos) { grupo in
                        Section(header: Text("Hacia \(grupo.destino)")) {
                            ForEach(grupo.salidas) { s in
                                fila(s)
                            }
                        }
                    }
                }
                .listStyle(.plain)
                .frame(maxHeight: 340)
            } else {
                Text("No hay guaguas programadas próximamente")
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120)
                    .padding()
            }
        }
        .background(Color(.systemBackground))
        .cornerRadius(16, corners: [.topLeft, .topRight])
        .shadow(radius: 10)
        .task(id: parada.id) {
            // se refresca cada 30 s mientras la ficha está abierta (como la web)
            while !Task.isCancelled {
                await cargar()
                try? await Task.sleep(nanoseconds: 30_000_000_000)
            }
        }
    }

    @ViewBuilder
    private func fila(_ s: Salida) -> some View {
        HStack(spacing: 10) {
            Text(s.linea)
                .font(.caption).bold()
                .foregroundColor(.white)
                .padding(.horizontal, 6).padding(.vertical, 3)
                .background(Color(hex: lineas.first { $0.num == s.linea }?.color ?? "0b6e99"))
                .cornerRadius(6)
            VStack(alignment: .leading, spacing: 2) {
                Text(s.texto).font(.body).bold()
                if let nota = s.nota, !nota.isEmpty {
                    Text(nota).font(.caption2).foregroundColor(.secondary)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(s.hora).font(.subheadline).monospacedDigit()
                Text(etiqueta(s)).font(.caption2).foregroundColor(s.prec == "confirmada" || s.prec == "vivo" ? .green : .secondary)
            }
            if s.subir {
                Button("Me subí") { onSubida(s) }
                    .font(.caption).buttonStyle(.borderedProminent)
            }
        }
    }

    private func etiqueta(_ s: Salida) -> String {
        switch s.prec {
        case "confirmada", "vivo": return (s.n ?? 0) > 0 ? "confirmada por \(s.n!)" : "en vivo"
        case "oficial": return "horario oficial"
        case "aprox": return "aproximada"
        default: return "estimada"
        }
    }

    private func cargar() async {
        do {
            let t = try await APIClient.shared.getTablero(paradaId: parada.id)
            await MainActor.run { tablero = t; error = nil; isLoading = false }
        } catch {
            await MainActor.run {
                if tablero == nil { self.error = "No se pudo cargar. Comprueba tu conexión." }
                isLoading = false
            }
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
        authManager.iniciarAppleSignIn()
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