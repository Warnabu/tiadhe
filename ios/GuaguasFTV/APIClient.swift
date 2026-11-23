import Foundation
import Combine
import CryptoKit
import CoreLocation
import Security

// MARK: - Modelos (idénticos a lo que devuelve servidor.py)

struct Linea: Decodable, Identifiable {
    let num: String
    let nombre: String
    let color: String
    let calibrada: Bool
    let real: Bool
    /// Siempre como lista de tramos. El servidor manda [[lat, lon], …] (un tramo) o [[[lat, lon], …], …] (varios).
    let tramos: [[CLLocationCoordinate2D]]
    var id: String { num }

    enum CodingKeys: String, CodingKey { case num, nombre, color, calibrada, real, trazado }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        num = try c.decode(String.self, forKey: .num)
        nombre = try c.decode(String.self, forKey: .nombre)
        color = try c.decode(String.self, forKey: .color)
        calibrada = (try? c.decode(Bool.self, forKey: .calibrada)) ?? false
        real = (try? c.decode(Bool.self, forKey: .real)) ?? false
        let aCoord: ([Double]) -> CLLocationCoordinate2D? = { $0.count >= 2 ? CLLocationCoordinate2D(latitude: $0[0], longitude: $0[1]) : nil }
        if let uno = try? c.decode([[Double]].self, forKey: .trazado) {
            tramos = [uno.compactMap(aCoord)]
        } else if let varios = try? c.decode([[[Double]]].self, forKey: .trazado) {
            tramos = varios.map { $0.compactMap(aCoord) }
        } else {
            tramos = []
        }
    }
}

struct Parada: Codable, Identifiable, Equatable {
    let id: String
    let nombre: String
    let lat: Double
    let lon: Double
    let lineas: [String]
    var coordinate: CLLocationCoordinate2D { CLLocationCoordinate2D(latitude: lat, longitude: lon) }
}

/// Una guagua que pasa por la parada (GET /api/parada/<id> → grupos[].salidas[]).
struct Salida: Decodable, Identifiable {
    let linea: String
    let texto: String        // "en 5 min", "a las 10:15", "pasó hace 2 min"…
    let hora: String         // "10:15"
    let minutos: Int
    let prec: String         // oficial | estimado | aprox | vivo | confirmada
    let cal: Bool?
    let nota: String?
    let viaje: String        // identificador del viaje: se envía tal cual al avisar "me he subido"
    let n: Int?              // personas que la han confirmado
    let subir: Bool          // ¿se puede avisar ahora?
    var id: String { viaje }
}

struct GrupoDestino: Decodable, Identifiable {
    let destino: String
    let salidas: [Salida]
    var id: String { destino }
}

struct ParadaTablero: Decodable {
    let id: String
    let nombre: String
    let lineas: [String]
}

struct TableroResponse: Decodable {
    let ahora: String
    let dia: String
    let grupos: [GrupoDestino]
    let parada: ParadaTablero
}

struct PerfilUsuario: Decodable {
    let confirmadas: Int
    let discrepantes: Int
    let nivel: String
    let peso: Double
    let puntos: Int
    let subidas: Int
}

struct SubidaResponse: Decodable {
    let ok: Bool
    let estado: String
    let mensaje: String
    let perfil: PerfilUsuario?
}

struct RegistroResponse: Decodable {
    let ok: Bool
    let dispositivo: String
    let perfil: PerfilUsuario?
}

struct FirebaseTokenResponse: Decodable {
    let ok: Bool
    let token: String
    let uid: String
    let email: String?
    let name: String?
}

struct OkResponse: Decodable { let ok: Bool }

private struct ErrorResponse: Decodable { let error: String? }

// MARK: - Identidad del dispositivo
// Igual que la web (cliente.js): clave ECDSA P-256 generada en el dispositivo, guardada en el Llavero y que
// nunca sale de él. El servidor solo conoce la clave pública. id = sha256(clave pública sin comprimir)[:32] en hex.

final class IdentidadDispositivo {
    private static let cuenta = "guaguas-ftv-identidad"
    let clave: P256.Signing.PrivateKey

    init() {
        if let datos = Self.leer(), let k = try? P256.Signing.PrivateKey(rawRepresentation: datos) {
            clave = k
        } else {
            clave = P256.Signing.PrivateKey()
            Self.guardar(clave.rawRepresentation)
        }
    }

    /// Clave pública sin comprimir (65 bytes, 0x04‖X‖Y), como exportKey("raw") de WebCrypto.
    var publica: Data { clave.publicKey.x963Representation }
    var id: String { String(SHA256.hash(data: publica).map { String(format: "%02x", $0) }.joined().prefix(32)) }

    /// Firma r‖s (64 bytes) en base64, el formato que espera comunidad_pg.verificar.
    func firmar(_ mensaje: Data) throws -> String {
        try clave.signature(for: mensaje).rawRepresentation.base64EncodedString()
    }

    private static func consulta() -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: cuenta]
    }
    private static func leer() -> Data? {
        var q = consulta()
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var res: AnyObject?
        return SecItemCopyMatching(q as CFDictionary, &res) == errSecSuccess ? res as? Data : nil
    }
    private static func guardar(_ datos: Data) {
        SecItemDelete(consulta() as CFDictionary)
        var q = consulta()
        q[kSecValueData as String] = datos
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(q as CFDictionary, nil)
    }
}

// MARK: - Cliente

final class APIClient: ObservableObject {
    static let shared = APIClient()

    /// Se puede cambiar en Xcode: Scheme → Run → Environment Variables → BASE_URL (p. ej. http://192.168.1.78:5000).
    let baseURL = ProcessInfo.processInfo.environment["BASE_URL"] ?? "https://tiadhe-backend.onrender.com"
    private let session = URLSession.shared
    private let identidad = IdentidadDispositivo()
    private var registrado = false
    @Published private(set) var perfil: PerfilUsuario?
    private var authToken: String?

    func setAuthToken(_ token: String?) { authToken = token }

    // Cuerpo JSON escrito a mano en la forma exacta que el servidor reconstruye para comprobar la firma
    // (json.dumps(..., separators=(',', ':'), ensure_ascii=False)): compacto, claves ordenadas, números con la
    // representación más corta (Double.description = repr de Python). JSONSerialization no sirve: puede escribir
    // 28.123456 como 28.123456000000001 y la firma dejaría de cuadrar.
    private func json(_ cuerpo: [String: Any]?) throws -> String {
        guard let cuerpo else { return "" }
        func cadena(_ s: String) -> String {
            var r = "\""
            for u in s.unicodeScalars {
                switch u {
                case "\"": r += "\\\""
                case "\\": r += "\\\\"
                case "\n": r += "\\n"
                case "\r": r += "\\r"
                case "\t": r += "\\t"
                case "\u{08}": r += "\\b"
                case "\u{0C}": r += "\\f"
                default:
                    if u.value < 0x20 { r += String(format: "\\u%04x", u.value) } else { r.unicodeScalars.append(u) }
                }
            }
            return r + "\""
        }
        func valor(_ v: Any) throws -> String {
            switch v {
            case let s as String: return cadena(s)
            case let b as Bool: return b ? "true" : "false"
            case let i as Int: return String(i)
            case let d as Double:
                guard d.isFinite else { throw APIError.invalidResponse }
                return d.description
            default: throw APIError.invalidResponse
            }
        }
        var partes: [String] = []
        for k in cuerpo.keys.sorted() {
            partes.append(cadena(k) + ":" + (try valor(cuerpo[k]!)))
        }
        return "{" + partes.joined(separator: ",") + "}"
    }

    private func pedir<T: Decodable>(_ ruta: String, metodo: String = "GET", cuerpo: [String: Any]? = nil,
                                     firmar: Bool = false, conDispositivo: Bool = true) async throws -> T {
        guard let url = URL(string: baseURL + ruta) else { throw APIError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = metodo
        req.timeoutInterval = 20
        let texto = try json(cuerpo)
        if !texto.isEmpty {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = Data(texto.utf8)
        }
        if firmar {
            let ts = String(Int(Date().timeIntervalSince1970))
            let nonce = (0..<12).map { _ in String(format: "%02x", UInt8.random(in: 0...255)) }.joined()
            let rutaSinQuery = ruta.split(separator: "?", maxSplits: 1).first.map(String.init) ?? ruta
            let msg = [metodo, rutaSinQuery, ts, nonce, texto].joined(separator: "\n")
            if conDispositivo { req.setValue(identidad.id, forHTTPHeaderField: "X-Dispositivo") }
            req.setValue(ts, forHTTPHeaderField: "X-Ts")
            req.setValue(nonce, forHTTPHeaderField: "X-Nonce")
            req.setValue(try identidad.firmar(Data(msg.utf8)), forHTTPHeaderField: "X-Firma")
        }
        if let authToken { req.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization") }

        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200...299).contains(http.statusCode) else {
            let msg = (try? JSONDecoder().decode(ErrorResponse.self, from: data))?.error
            if http.statusCode == 401 { throw APIError.unauthorized(msg) }
            throw APIError.serverError(http.statusCode, msg)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    // MARK: Datos públicos

    func getLineas() async throws -> [Linea] { try await pedir("/api/lineas") }
    func getParadas() async throws -> [Parada] { try await pedir("/api/paradas") }

    func getTablero(paradaId: String, dinamico: Bool = true) async throws -> TableroResponse {
        try await pedir("/api/parada/\(paradaId)?modo=\(dinamico ? "dinamico" : "estatico")")
    }

    func getCerca(lat: Double, lon: Double, nombre: String = "Parada", dinamico: Bool = true) async throws -> TableroResponse {
        let n = nombre.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "Parada"
        return try await pedir("/api/cerca?lat=\(lat)&lon=\(lon)&nombre=\(n)&modo=\(dinamico ? "dinamico" : "estatico")")
    }

    // MARK: Comunidad (peticiones firmadas)

    /// Alta idempotente del dispositivo. Se llama sola antes de la primera petición firmada.
    func registrarDispositivo() async throws {
        if registrado { return }
        let r: RegistroResponse = try await pedir("/api/registro", metodo: "POST",
                                                  cuerpo: ["pub": identidad.publica.base64EncodedString()],
                                                  firmar: true, conDispositivo: false)
        registrado = true
        await MainActor.run { self.perfil = r.perfil }
    }

    /// "Me he subido a esta guagua en esta parada". La ubicación es opcional pero hace que el aviso pese más.
    func enviarSubida(paradaId: String, viaje: String, ubicacion: CLLocation?) async throws -> SubidaResponse {
        try await registrarDispositivo()
        var cuerpo: [String: Any] = ["parada": paradaId, "viaje": viaje]
        if let u = ubicacion, u.horizontalAccuracy >= 0 {
            cuerpo["lat"] = u.coordinate.latitude
            cuerpo["lon"] = u.coordinate.longitude
            cuerpo["acc"] = u.horizontalAccuracy
        }
        let r: SubidaResponse = try await pedir("/api/subida", metodo: "POST", cuerpo: cuerpo, firmar: true)
        if let p = r.perfil { await MainActor.run { self.perfil = p } }
        return r
    }

    func exchangeFirebaseToken(idToken: String) async throws -> String {
        let r: FirebaseTokenResponse = try await pedir("/api/firebase-token", metodo: "POST", cuerpo: ["id_token": idToken])
        return r.token
    }

    func registerFCMToken(_ fcmToken: String) async throws {
        try await registrarDispositivo()
        let _: OkResponse = try await pedir("/api/register-fcm-token", metodo: "POST", cuerpo: ["fcm_token": fcmToken], firmar: true)
    }
}

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case unauthorized(String?)
    case serverError(Int, String?)
    case decodingError(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "URL inválida"
        case .invalidResponse: return "Respuesta inválida del servidor"
        case .unauthorized(let m): return m ?? "No autorizado"
        case .serverError(let code, let m): return m ?? "Error del servidor: \(code)"
        case .decodingError(let error): return "Error al leer la respuesta: \(error.localizedDescription)"
        }
    }
}
