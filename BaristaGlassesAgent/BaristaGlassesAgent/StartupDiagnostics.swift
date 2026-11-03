import Foundation
import Darwin
import OSLog

// Startup phases only: never log callback URLs, tokens, or camera content.
enum StartupDiagnostics {
    nonisolated static func record(_ message: String) {
        Logger(subsystem: "com.meta.hackathon.BaristaGlassesAgent", category: "Startup")
            .notice("\(message, privacy: .public)")
        guard let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first else { return }
        let path = documents.appendingPathComponent("startup.log").path
        let descriptor = Darwin.open(path, O_WRONLY | O_CREAT | O_APPEND, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { return }
        defer { Darwin.close(descriptor) }
        let line = "\(Date().ISO8601Format()) \(message)\n"
        line.utf8CString.withUnsafeBytes { bytes in
            if let address = bytes.baseAddress {
                _ = Darwin.write(descriptor, address, bytes.count - 1)
            }
        }
    }
}
