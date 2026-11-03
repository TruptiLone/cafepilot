import SwiftUI

@main
struct BaristaGlassesAgentApp: App {
    @StateObject private var cameraManager = CameraManager()
    @Environment(\.scenePhase) private var scenePhase

    init() {
        StartupDiagnostics.record("App entry point reached; local-only build")
        do {
            let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            try FileManager.default.createDirectory(at: documents.appending(path: "Recordings"), withIntermediateDirectories: true)
        } catch { StartupDiagnostics.record("Could not create the Recordings folder: \(error.localizedDescription)") }
    }

    var body: some Scene {
        WindowGroup {
            ContentView(cameraManager: cameraManager)
                .task {
                    await cameraManager.initializeSDK()
                    #if DEBUG
                    if ProcessInfo.processInfo.arguments.contains("--stage2-self-test") {
                        await Stage2SmokeTest.run(cameraManager)
                    }
                    #endif
                }
                .onChange(of: scenePhase) { _, phase in
                    // Meta AI may temporarily foreground for registration/permission.
                    // No background recording or transfer service is started.
                    if phase != .inactive {
                        Task { await cameraManager.setForeground(phase == .active) }
                    }
                }
                .onOpenURL { url in
                    Task {
                        await cameraManager.initializeSDK()
                        await cameraManager.handleURL(url)
                    }
                }
        }
    }
}
