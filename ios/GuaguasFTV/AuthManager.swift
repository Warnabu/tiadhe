import Foundation
import FirebaseAuth
import FirebaseCore
import GoogleSignIn
import AuthenticationServices
import CryptoKit
import UIKit

class AuthManager: ObservableObject {
    @Published var user: User?
    @Published var isAuthenticated = false
    @Published var internalToken: String?
    @Published var errorMessage: String?

    private var authStateHandle: AuthStateDidChangeListenerHandle?

    func configureFirebaseAuth() {
        authStateHandle = Auth.auth().addStateDidChangeListener { [weak self] _, user in
            DispatchQueue.main.async {
                self?.user = user
                self?.isAuthenticated = user != nil
                if let user = user {
                    self?.fetchInternalToken(for: user)
                } else {
                    self?.internalToken = nil
                }
            }
        }
    }

    func signInWithGoogle(presentingViewController: UIViewController) {
        guard let clientID = FirebaseApp.app()?.options.clientID else {
            errorMessage = "Firebase client ID not configured"
            return
        }

        let config = GIDConfiguration(clientID: clientID)
        GIDSignIn.sharedInstance.configuration = config

        GIDSignIn.sharedInstance.signIn(withPresenting: presentingViewController) { [weak self] result, error in
            if let error = error {
                DispatchQueue.main.async {
                    self?.errorMessage = error.localizedDescription
                }
                return
            }

            guard let user = result?.user,
                  let idToken = user.idToken?.tokenString else {
                DispatchQueue.main.async {
                    self?.errorMessage = "Failed to get ID token"
                }
                return
            }

            let credential = GoogleAuthProvider.credential(withIDToken: idToken, accessToken: user.accessToken.tokenString)

            Auth.auth().signIn(with: credential) { authResult, error in
                if let error = error {
                    DispatchQueue.main.async {
                        self?.errorMessage = error.localizedDescription
                    }
                }
            }
        }
    }

    // Sign in with Apple. Firebase exige un nonce: se envía su SHA-256 a Apple y el valor original a Firebase.
    // El delegado se guarda aquí porque ASAuthorizationController solo lo referencia de forma débil.
    private var appleNonce: String?
    private var appleDelegate: AppleSignInDelegate?

    func iniciarAppleSignIn() {
        let nonce = (0..<32).map { _ in String(format: "%02x", UInt8.random(in: 0...255)) }.joined()
        appleNonce = nonce
        let request = ASAuthorizationAppleIDProvider().createRequest()
        request.requestedScopes = [.fullName, .email]
        request.nonce = SHA256.hash(data: Data(nonce.utf8)).map { String(format: "%02x", $0) }.joined()
        let controller = ASAuthorizationController(authorizationRequests: [request])
        let delegado = AppleSignInDelegate(authManager: self)
        appleDelegate = delegado
        controller.delegate = delegado
        controller.presentationContextProvider = delegado
        controller.performRequests()
    }

    func signInWithApple(credential: ASAuthorizationAppleIDCredential) {
        guard let identityToken = credential.identityToken,
              let idTokenString = String(data: identityToken, encoding: .utf8),
              let nonce = appleNonce else {
            errorMessage = "No se pudo obtener el token de Apple"
            return
        }

        let firebaseCredential = OAuthProvider.credential(withProviderID: "apple.com",
                                                           idToken: idTokenString,
                                                           rawNonce: nonce)

        Auth.auth().signIn(with: firebaseCredential) { [weak self] authResult, error in
            if let error = error {
                DispatchQueue.main.async {
                    self?.errorMessage = error.localizedDescription
                }
            }
        }
    }

    private func fetchInternalToken(for firebaseUser: User) {
        firebaseUser.getIDTokenForcingRefresh(true) { [weak self] idToken, error in
            guard let idToken = idToken, error == nil else {
                DispatchQueue.main.async {
                    self?.errorMessage = error?.localizedDescription ?? "Failed to get ID token"
                }
                return
            }

            Task {
                do {
                    let token = try await APIClient.shared.exchangeFirebaseToken(idToken: idToken)
                    APIClient.shared.setAuthToken(token)
                    DispatchQueue.main.async {
                        self?.internalToken = token
                    }
                } catch {
                    DispatchQueue.main.async {
                        self?.errorMessage = error.localizedDescription
                    }
                }
            }
        }
    }

    func signOut() {
        do {
            try Auth.auth().signOut()
            GIDSignIn.sharedInstance.signOut()
            internalToken = nil
            APIClient.shared.setAuthToken(nil)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    deinit {
        if let handle = authStateHandle {
            Auth.auth().removeStateDidChangeListener(handle)
        }
    }
}

final class AppleSignInDelegate: NSObject, ASAuthorizationControllerDelegate, ASAuthorizationControllerPresentationContextProviding {
    private weak var authManager: AuthManager?

    init(authManager: AuthManager) {
        self.authManager = authManager
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithAuthorization authorization: ASAuthorization) {
        if let appleCredential = authorization.credential as? ASAuthorizationAppleIDCredential {
            authManager?.signInWithApple(credential: appleCredential)
        }
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        DispatchQueue.main.async { self.authManager?.errorMessage = error.localizedDescription }
    }

    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }
            .first { $0.isKeyWindow } ?? ASPresentationAnchor()
    }
}
