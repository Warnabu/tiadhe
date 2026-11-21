import Foundation
import Combine

struct Linea: Codable, Identifiable {
    let num: String
    let nombre: String
    let color: String
    let calibrada: Bool
    let real: Bool
    let trazado: [[[Double]]]?
    var id: String { num }
}

struct Parada: Codable, Identifiable {
    let id: String
    let nombre: String
    let lat: Double
    let lon: Double
    let lineas: [String]
}

struct TableroPrediccion: Codable {
    let linea: String
    let sentido: String
    let variante: String
    let llegada: String
    let off: Int
    let prec: String
    let n: Int?
}

struct TableroResponse: Codable {
    let predicciones: [TableroPrediccion]
    let parada: Parada
}

struct SubidaRequest: Codable {
    let viaje: String
    let parada: String
    let lat: Double
    let lon: Double
    let acc: Double
    let nonce: String
    let sig: String
    let pub: String
    let ts: Int
}

struct SubidaResponse: Codable {
    let ok: Bool
    let estado: String
    let mensaje: String
    let perfil: PerfilUsuario?
}

struct PerfilUsuario: Codable {
    let confirmadas: Int
    let discrepantes: Int
    let nivel: String
    let peso: Double
    let puntos: Int
    let subidas: Int
}

struct ETARequest: Codable {
    let linea: String
    let sentido: String
    let parada_origen: String
    let parada_destino: String
}

struct ETAResponse: Codable {
    let eta_min: Int
    let confianza: Double
    let origen: String
    let desglose: [String: Double]?
}

struct FirebaseTokenResponse: Codable {
    let ok: Bool
    let token: String
    let uid: String
    let email: String?
    let name: String?
}

struct FCMTokenRequest: Codable {
    let fcm_token: String
}

class APIClient: ObservableObject {
    static let shared = APIClient()

    let baseURL = "https://tiadhe-backend.onrender.com"
    private let session = URLSession.shared
    private var authToken: String?

    func setAuthToken(_ token: String) {
        authToken = token
    }

    private func request<T: Codable>(_ endpoint: String, method: String = "GET", body: Data? = nil) async throws -> T {
        guard let url = URL(string: baseURL + endpoint) else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        if let token = authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        request.httpBody = body

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }

        if httpResponse.statusCode == 401 {
            throw APIError.unauthorized
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            throw APIError.serverError(httpResponse.statusCode)
        }

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(T.self, from: data)
    }

    func getLineas() async throws -> [Linea] {
        return try await request("/api/lineas")
    }

    func getParadas() async throws -> [Parada] {
        return try await request("/api/paradas")
    }

    func getTablero(paradaId: String) async throws -> TableroResponse {
        return try await request("/api/parada/\(paradaId)")
    }

    func getCerca(lat: Double, lon: Double, nombre: String = "Parada") async throws -> TableroResponse {
        return try await request("/api/cerca?lat=\(lat)&lon=\(lon)&nombre=\(nombre.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")")
    }

    func enviarSubida(_ request: SubidaRequest) async throws -> SubidaResponse {
        let data = try JSONEncoder().encode(request)
        return try await request("/api/subida", method: "POST", body: data)
    }

    func getETA(linea: String, sentido: String, paradaOrigen: String, paradaDestino: String) async throws -> ETAResponse {
        return try await request("/api/eta?linea=\(linea)&sentido=\(sentido)&parada_origen=\(paradaOrigen)&parada_destino=\(paradaDestino)")
    }

    func exchangeFirebaseToken(idToken: String) async throws -> String {
        let request = ["id_token": idToken]
        let data = try JSONEncoder().encode(request)
        let response: FirebaseTokenResponse = try await request("/api/firebase-token", method: "POST", body: data)
        return response.token
    }

    func registerFCMToken(_ fcmToken: String) async throws {
        let request = FCMTokenRequest(fcm_token: fcmToken)
        let data = try JSONEncoder().encode(request)
        let _: [String: Bool] = try await request("/api/register-fcm-token", method: "POST", body: data)
    }
}

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case unauthorized
    case serverError(Int)
    case decodingError(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "URL inválida"
        case .invalidResponse: return "Respuesta inválida del servidor"
        case .unauthorized: return "No autorizado"
        case .serverError(let code): return "Error del servidor: \(code)"
        case .decodingError(let error): return "Error al decodificar: \(error.localizedDescription)"
        }
    }
}