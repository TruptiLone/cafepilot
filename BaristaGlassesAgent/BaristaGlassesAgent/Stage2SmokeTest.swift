#if DEBUG
import Foundation
import MWDATCore

/// Runs inside the simulator app against the real SDK's MockDeviceKit. This
/// never uses physical glasses, starts a camera, records, or uploads a file.
@MainActor
enum Stage2SmokeTest {
    private static func wait(_ message: String, until condition: () -> Bool) async throws {
        for _ in 0..<120 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(100))
        }
        throw RecordingError.message("Timed out: \(message)")
    }

    static func run(_ manager: CameraManager) async {
        var checks: [String] = []
        var failure: String?
        do {
            guard manager.isMockMode else { throw RecordingError.message("Mock mode is required for this test.") }
            try await wait("SDK initialized") { manager.isSDKReady }
            guard !manager.isRegistered && !manager.hasCameraPermission else {
                throw RecordingError.message("The test must begin unregistered with camera permission denied.")
            }
            checks.append("SDK configured; registration and permission start false")
            await manager.startSession()
            guard manager.sessionStartCount == 0 else { throw RecordingError.message("Started before registration.") }
            checks.append("Session cannot start before registration")

            await manager.registerApp()
            try await wait("mock registration") { manager.isRegistered }
            try await wait("mock device discovery") { manager.hasEligibleDevice && manager.hasConnectedDevice }
            checks.append("Registration via startRegistration and registrationStateStream")
            await manager.setMockPermission(false)
            await manager.startSession()
            guard manager.sessionStartCount == 0 else { throw RecordingError.message("Started without camera permission.") }
            checks.append("Denied camera permission blocks session startup")

            await manager.requestCameraPermission()
            try await wait("camera permission") { manager.hasCameraPermission }
            checks.append("Camera permission is checked and requested through the SDK")
            await manager.startSession()
            try await wait("session started") { manager.isSessionReady }
            let count = manager.sessionStartCount
            await manager.startSession()
            guard manager.sessionStartCount == count else { throw RecordingError.message("Duplicate session created.") }
            checks.append("Started session and duplicate-start protection")

            manager.mockTap()
            try await wait("session paused by device") { manager.sessionState == .paused }
            await manager.startSession()
            guard manager.sessionState == .paused && manager.sessionStartCount == count else {
                throw RecordingError.message("A paused session was restarted.")
            }
            checks.append("Paused session is retained and never restarted")
            manager.mockTap()
            try await wait("device resumes session") { manager.isSessionReady }
            checks.append("Device-controlled resume returns to readiness")

            manager.mockPower(false)
            try await wait("device disconnect") { !manager.hasConnectedDevice && !manager.isSessionReady }
            manager.stopSession()
            try await wait("terminal stopped state") { manager.sessionState == .stopped }
            manager.mockPower(true)
            try await wait("device rediscovery") { manager.hasEligibleDevice }
            await manager.startSession()
            try await wait("fresh session after reconnection") { manager.isSessionReady }
            checks.append("Disconnect clears readiness; rediscovery permits a fresh session")

            await manager.setForeground(false)
            try await wait("foreground-only cleanup") { manager.sessionState == .stopped }
            await manager.setForeground(true)
            await manager.startSession()
            try await wait("session after foreground return") { manager.isSessionReady }
            checks.append("Background stops session; explicit restart works after foreground return")
        } catch { failure = error.localizedDescription }
        let report: [String: Any] = ["stage": 2, "mock_only": true, "passed": failure == nil,
                                    "checks": checks, "error": failure as Any? ?? NSNull(),
                                    "session_starts": manager.sessionStartCount,
                                    "run_id": runID,
                                    "at": Date().ISO8601Format()]
        do {
            let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            let directory = documents.appending(path: "Diagnostics")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
                .write(to: directory.appending(path: "stage2-smoke-test.json"), options: .atomic)
        } catch { failure = failure ?? error.localizedDescription }
        manager.mockTestStatus = failure.map { "Mock test failed: \($0)" } ?? "Mock checks passed: \(checks.count)"
        StartupDiagnostics.record(manager.mockTestStatus ?? "Mock tests finished")
    }

    private static var runID: String {
        let arguments = ProcessInfo.processInfo.arguments
        if let index = arguments.firstIndex(of: "--test-run-id"), index + 1 < arguments.count {
            return arguments[index + 1]
        }
        return "manual"
    }
}
#endif
