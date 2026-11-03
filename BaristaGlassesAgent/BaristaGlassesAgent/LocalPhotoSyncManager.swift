import Foundation
import Combine
import Network

struct LocalPhotoJob: Codable, Identifiable, Equatable {
    let id: UUID
    let filename: String
    let capturedAt: Date
    let deliveryID: UUID?
    let imageRole: String?
}

@MainActor
final class LocalPhotoSyncManager: ObservableObject {
    @Published private(set) var states: [String: String] = [:]
    @Published private(set) var receiverStatus = "Searching for the Mac receiver on Wi-Fi…"

    private let browser = NWBrowser(for: .bonjour(type: "_baristaphoto._tcp", domain: "local."), using: .tcp)
    private let queueURL: URL
    private var jobs: [LocalPhotoJob] = []
    private var endpoint: NWEndpoint?
    private var connection: NWConnection?
    private var isSending = false
    private let token = Bundle.main.object(forInfoDictionaryKey: "LocalPhotoSyncToken") as? String ?? ""

    init() {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        queueURL = documents.appending(path: "Photos/upload-queue.json")
        loadQueue()
        guard !token.isEmpty else {
            receiverStatus = "Photo sync needs PHOTO_SYNC_TOKEN in the local Xcode configuration. Photos remain on this iPhone."
            return
        }
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            Task { @MainActor in
                guard let self else { return }
                self.endpoint = results.first?.endpoint
                self.receiverStatus = self.endpoint == nil ? "Mac receiver not found. Photos will retry when it appears." : "Mac receiver found on Wi-Fi."
                self.sendNextIfPossible()
            }
        }
        browser.stateUpdateHandler = { [weak self] state in
            if case .failed(let error) = state {
                Task { @MainActor in self?.receiverStatus = "Local network discovery failed: \(error.localizedDescription)" }
            }
        }
        browser.start(queue: .main)
    }

    func enqueue(_ url: URL, capturedAt: Date = Date(), deliveryID: UUID? = nil, imageRole: String? = nil) {
        let job = LocalPhotoJob(id: UUID(), filename: url.lastPathComponent, capturedAt: capturedAt,
                                deliveryID: deliveryID, imageRole: imageRole)
        jobs.append(job)
        states[job.filename] = "Waiting for Mac"
        persistQueue()
        sendNextIfPossible()
    }

    func state(for filename: String) -> String {
        states[filename] ?? "On this iPhone"
    }

    private func loadQueue() {
        guard let data = try? Data(contentsOf: queueURL),
              let saved = try? JSONDecoder().decode([LocalPhotoJob].self, from: data) else { return }
        jobs = saved
        for job in jobs { states[job.filename] = "Waiting for Mac" }
    }

    private func persistQueue() {
        do {
            try FileManager.default.createDirectory(at: queueURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(jobs).write(to: queueURL, options: .atomic)
        } catch { receiverStatus = "Could not save photo retry queue: \(error.localizedDescription)" }
    }

    private func sendNextIfPossible() {
        guard !isSending, let endpoint, let job = jobs.first else { return }
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let fileURL = documents.appending(path: "Photos").appending(path: job.filename)
        guard let body = try? Data(contentsOf: fileURL) else {
            jobs.removeFirst(); persistQueue(); sendNextIfPossible(); return
        }
        isSending = true
        states[job.filename] = "Sending to Mac…"
        let connection = NWConnection(to: endpoint, using: .tcp)
        self.connection = connection
        connection.stateUpdateHandler = { [weak self] state in
            guard case .ready = state else {
                if case .failed(let error) = state { Task { @MainActor in self?.finish(job, success: false, message: error.localizedDescription) } }
                return
            }
            guard let self else { return }
            let stamp = ISO8601DateFormatter().string(from: job.capturedAt)
            let pairHeaders = job.deliveryID.map { "X-Delivery-ID: \($0.uuidString)\r\nX-Image-Role: \(job.imageRole ?? "")\r\n" } ?? ""
            let headers = "POST /api/upload HTTP/1.1\r\nHost: barista.local\r\nAuthorization: Bearer \(self.token)\r\nContent-Type: image/jpeg\r\nContent-Length: \(body.count)\r\nConnection: close\r\nX-Capture-ID: \(job.id.uuidString)\r\nX-Captured-At: \(stamp)\r\nX-Capture-Source: meta-glasses\r\n\(pairHeaders)\r\n"
            var request = Data(headers.utf8)
            request.append(body)
            connection.send(content: request, completion: .contentProcessed { error in
                if let error { Task { @MainActor in self.finish(job, success: false, message: error.localizedDescription) }; return }
                self.receiveResponse(connection, job: job, accumulated: Data())
            })
        }
        connection.start(queue: .main)
    }

    private func receiveResponse(_ connection: NWConnection, job: LocalPhotoJob, accumulated: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16_384) { [weak self] data, _, complete, error in
            var received = accumulated
            if let data { received.append(data) }
            if let error {
                Task { @MainActor in self?.finish(job, success: false, message: error.localizedDescription) }
            } else if complete {
                let response = String(decoding: received.prefix(128), as: UTF8.self)
                let status = response.split(separator: " ").dropFirst().first.flatMap { Int($0) } ?? 0
                Task { @MainActor in self?.finish(job, success: (200..<300).contains(status), message: "HTTP \(status)") }
            } else {
                self?.receiveResponse(connection, job: job, accumulated: received)
            }
        }
    }

    private func finish(_ job: LocalPhotoJob, success: Bool, message: String) {
        guard isSending, jobs.first == job else { return }
        connection?.cancel(); connection = nil; isSending = false
        if success {
            jobs.removeFirst()
            states[job.filename] = "Saved on Mac"
            receiverStatus = "Photo copied to ~/Pictures/BaristaShared."
            persistQueue()
            sendNextIfPossible()
        } else {
            states[job.filename] = "Waiting to retry"
            receiverStatus = "Mac transfer failed (\(message)); keeping the iPhone copy and retrying."
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(5))
                self?.sendNextIfPossible()
            }
        }
    }
}
