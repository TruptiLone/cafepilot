import Foundation
import Combine
import MWDATCore
import MWDATCamera
import CoreVideo
import CoreMedia
import UIKit
import AVKit
#if DEBUG
import MWDATMockDevice
#endif

/// Stage 3 adds a live camera preview after the device session is ready.
@MainActor
final class CameraManager: ObservableObject {
    @Published private(set) var isSDKReady = false
    @Published private(set) var registrationState: RegistrationState = .unavailable
    @Published private(set) var cameraPermission: PermissionStatus?
    @Published private(set) var hasConnectedDevice = false
    @Published private(set) var hasEligibleDevice = false
    @Published private(set) var sessionState: DeviceSessionState?
    @Published private(set) var isConnecting = false
    @Published private(set) var isStopping = false
    @Published private(set) var isRequestingPermission = false
    @Published private(set) var connectionStatus = "Initializing glasses SDK…"
    @Published private(set) var deviceStatus = "No devices discovered."
    @Published private(set) var lastError: String?
    @Published private(set) var sessionStartCount = 0
    @Published private(set) var previewImage: UIImage?
    @Published private(set) var streamState: StreamState?
    @Published private(set) var frameDimensions = "No frames yet"
    @Published private(set) var measuredFPS = 0.0
    @Published private(set) var isStartingStream = false
    @Published private(set) var glassesAppUpdateRequired = false
    @Published private(set) var recordingCountdown: Int?
    @Published private(set) var recordingSecondsRemaining: Int?
    @Published private(set) var isRecording = false
    @Published private(set) var savedRecordings: [URL] = []
    @Published private(set) var playbackURL: URL?
    @Published private(set) var savedPhotos: [URL] = []
    @Published private(set) var photoPreviewURL: URL?
    @Published private(set) var isCapturingPhoto = false
    @Published private(set) var photoStatus = ""
    @Published private(set) var activeDeliveryID: UUID?
    @Published private(set) var deliveryImageRole: String?
    @Published private(set) var recordingStatus = ""
    private let recorder = VideoRecorder()
    let photoSync = LocalPhotoSyncManager()
    private var recordingTask: Task<Void, Never>?
    private let recordSeconds = 15
    @Published var mockTestStatus: String?
    let isMockMode: Bool

    var isRegistered: Bool { registrationState == .registered }
    var hasCameraPermission: Bool { cameraPermission == .granted }
    var cameraPreviewActive: Bool { camera != nil }
    var isSessionReady: Bool {
        isSDKReady && isRegistered && hasCameraPermission && hasConnectedDevice && sessionState == .started && !isStopping
    }
    var isBusy: Bool { registrationState == .registering || isRequestingPermission || isConnecting || isStopping }
    var canStartSession: Bool {
        isSDKReady && isRegistered && hasCameraPermission && hasEligibleDevice && !isBusy
            && (sessionState == nil || sessionState == .stopped)
    }
    var sessionStartBlockReason: String? {
        if !isSDKReady { return "Waiting for the glasses SDK." }
        if !isRegistered { return "Register Barista with Meta AI." }
        if !hasCameraPermission { return "Grant glasses camera permission." }
        if !hasEligibleDevice { return "No eligible glasses device. Refresh the connection in Meta AI." }
        if isBusy { return "Session is still starting or stopping. Wait, then refresh." }
        if let state = sessionState, state != .stopped { return "Current session is \(state.description). End it and wait for it to stop." }
        return nil
    }
    var sessionDescription: String { sessionState?.description ?? "Not started" }

    private var wearables: (any WearablesInterface)?
    private var deviceSelector: AutoDeviceSelector?
    private var deviceTokens: [any AnyListenerToken] = []
    private var deviceSession: DeviceSession?
    private var observers: [Task<Void, Never>] = []
    private let sessionTokens = ListenerTokenBag()
    private let streamTokens = ListenerTokenBag()
    private var camera: Camera?
    private var frameTimes: [TimeInterval] = []
    private var connectionTimeout: Task<Void, Never>?
    private var initializationTask: Task<Void, Never>?
    private var isForeground = true
    private var captureDeliveryID: UUID?
    private var captureImageRole: String?
    #if DEBUG
    private var mockGlasses: (any MockGlasses)?
    #endif

    init(useMock: Bool? = nil) {
        #if DEBUG
        #if targetEnvironment(simulator)
        let defaultMock = !ProcessInfo.processInfo.arguments.contains("--use-real-glasses")
        #else
        let defaultMock = false
        #endif
        isMockMode = useMock ?? (defaultMock || ProcessInfo.processInfo.arguments.contains("--use-mock-glasses")
            || ProcessInfo.processInfo.arguments.contains("--stage2-self-test"))
        #else
        isMockMode = false
        #endif
        StartupDiagnostics.record("Readiness manager created; mock: \(isMockMode)")
    }

    func initializeSDK() async {
        if let initializationTask { await initializationTask.value; return }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.configureSDK()
        }
        initializationTask = task
        await task.value
    }

    private func configureSDK() async {
        let slowStartup = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(15)) } catch { return }
            guard let self, !self.isSDKReady else { return }
            self.connectionStatus = "Glasses SDK is still initializing. Keep the app open."
        }
        defer { slowStartup.cancel() }
        do {
            try await Task.detached(priority: .userInitiated) { try Wearables.configure() }.value
            #if DEBUG
            if isMockMode {
                let kit = MockDeviceKit.shared
                kit.enable(config: MockDeviceKitConfig(initiallyRegistered: false, initialPermissionsGranted: false))
                let glasses = try kit.pairGlasses(model: .rayBanMeta)
                mockGlasses = glasses
                glasses.powerOn()
                glasses.unfold()
                glasses.don()
            }
            #endif
            let wearables = Wearables.shared
            self.wearables = wearables
            let selector = AutoDeviceSelector(wearables: wearables)
            deviceSelector = selector
            isSDKReady = true
            registrationChanged(wearables.registrationState)
            refreshDeviceObservers()
            observers = [
                Task { [weak self] in
                    for await state in wearables.registrationStateStream() {
                        guard !Task.isCancelled else { break }
                        self?.registrationChanged(state)
                    }
                },
                Task { [weak self] in
                    for await _ in wearables.devicesStream() {
                        guard !Task.isCancelled, let self else { break }
                        self.refreshDeviceObservers()
                    }
                },
                Task { [weak self] in
                    for await _ in selector.activeDeviceStream() {
                        guard !Task.isCancelled else { break }
                        self?.updateDeviceDetails()
                    }
                },
            ]
            StartupDiagnostics.record("SDK initialized")
        } catch { report("SDK initialization failed", error) }
    }

    deinit {
        observers.forEach { $0.cancel() }
        sessionTokens.clear()
        connectionTimeout?.cancel()
        deviceSession?.stop()
        let tokens = deviceTokens
        Task { for token in tokens { await token.cancel() } }
    }

    func handleURL(_ url: URL) async {
        guard let wearables, url.scheme == "baristaglassesagent",
              URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?
                .contains(where: { $0.name == "metaWearablesAction" }) == true else { return }
        do {
            _ = try await wearables.handleUrl(url)
            await refreshStatus()
        } catch { report("Meta AI callback failed", error) }
    }

    func registerApp() async {
        guard let wearables, isSDKReady, !isRegistered, registrationState != .registering else { return }
        lastError = nil
        connectionStatus = isMockMode ? "Registering mock glasses…" : "Complete app registration in Meta AI."
        do {
            try await wearables.startRegistration()
            registrationChanged(wearables.registrationState)
        } catch { report("Registration failed", error) }
    }

    func checkCameraPermission(reportErrors: Bool = false) async {
        guard let wearables, isRegistered else { cameraPermission = nil; return }
        do {
            let permission = try await wearables.checkPermissionStatus(.camera)
            guard isRegistered else { return }
            cameraPermission = permission
        } catch {
            cameraPermission = nil
            if reportErrors { report("Camera permission check failed", error) }
        }
    }

    func requestCameraPermission() async {
        guard let wearables, isRegistered, !isRequestingPermission else { return }
        isRequestingPermission = true
        lastError = nil
        defer { isRequestingPermission = false }
        do {
            let existing = try await wearables.checkPermissionStatus(.camera)
            cameraPermission = existing
            if existing != .granted {
                connectionStatus = isMockMode ? "Requesting mock camera access…" : "Allow glasses camera access in Meta AI, then return to Barista."
                cameraPermission = try await wearables.requestPermission(.camera)
            }
            if cameraPermission == .granted { connectionStatus = "Camera permission granted. Ready to start a session." }
            else { lastError = "Camera access was denied. Allow glasses camera access in Meta AI." }
        } catch { report("Camera permission request failed", error) }
    }

    func startSession() async {
        guard let wearables, isSDKReady, isForeground else { return }
        if let current = deviceSession {
            // Never replace a paused/starting/stopping session with another one.
            sessionChanged(current.state, session: current)
            return
        }
        guard !isConnecting, !isStopping else { return }
        lastError = nil
        guard isRegistered else { lastError = "Register this app with Meta AI first."; return }
        isConnecting = true
        defer { if deviceSession == nil { isConnecting = false } }
        await checkCameraPermission(reportErrors: true)
        guard hasCameraPermission else { lastError = lastError ?? "Allow glasses camera access first."; return }
        guard isForeground, let selector = deviceSelector, selector.activeDevice != nil else {
            updateDeviceDetails()
            lastError = "No eligible glasses are ready. Keep them worn and unfolded, and check their connection in Meta AI."
            return
        }
        connectionStatus = "Starting glasses session…"
        do {
            let session = try wearables.createSession(deviceSelector: selector)
            deviceSession = session
            sessionState = .idle
            // Subscribe synchronously before start(), as in Meta's CameraAccess
            // sample. Creating an AsyncStream inside a later Task can miss the
            // short initial transitions of a mock or fast device.
            session.statePublisher.listen { [weak self] state in
                Task { @MainActor [weak self] in
                    guard let self, self.deviceSession === session else { return }
                    self.sessionChanged(state, session: session)
                }
            }.store(in: sessionTokens)
            session.errorPublisher.listen { [weak self] error in
                Task { @MainActor [weak self] in
                    guard let self, self.deviceSession === session else { return }
                    if error == .datAppOnTheGlassesUpdateRequired {
                        self.glassesAppUpdateRequired = true
                        self.lastError = "The Meta glasses system app needs an update. Open Meta AI → Devices → Glasses → Connections and install the update, then reconnect."
                    } else {
                        self.report("Glasses session failed", error)
                    }
                    self.stopSession()
                }
            }.store(in: sessionTokens)
            try session.start()
            sessionStartCount += 1
            sessionChanged(session.state, session: session)
            if isConnecting {
                connectionTimeout = Task { [weak self] in
                    do { try await Task.sleep(for: .seconds(20)) } catch { return }
                    guard let self, self.deviceSession === session, self.isConnecting else { return }
                    self.lastError = "Session startup timed out. Check Meta AI and the glasses connection."
                    self.stopSession()
                }
            }
        } catch {
            isConnecting = false
            report("Glasses connection failed", error)
            stopSession()
        }
    }

    func stopSession() {
        guard let session = deviceSession else { return }
        if session.state == .stopped { sessionChanged(.stopped, session: session); return }
        guard !isStopping else { return }
        isStopping = true
        stopPreview()
        connectionTimeout?.cancel()
        session.stop()
        // Retain the session and observers until the terminal stopped state.
        sessionChanged(session.state, session: session)
    }

    func startPreview() {
        guard isSessionReady, let session = deviceSession, camera == nil, !isStartingStream else { return }
        isStartingStream = true
        lastError = nil
        do {
            let config = StreamConfiguration(videoCodec: .raw, resolution: .medium, frameRate: 7)
            guard let camera = try session.addCamera(config: config) else {
                isStartingStream = false
                lastError = "Camera could not attach. Confirm the session is started and glasses camera permission is granted."
                return
            }
            self.camera = camera
            let stream = camera.stream
            stream.statePublisher.listen { [weak self] state in
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    self.streamState = state
                    if state == .streaming { self.isStartingStream = false }
                    if state == .stopped { self.clearPreview() }
                    if state == .paused { self.isStartingStream = false }
                }
            }.store(in: streamTokens)
            stream.videoFramePublisher.listen { [weak self] frame in
                guard let image = frame.makeUIImage() else { return }
                let buffer = CMSampleBufferGetImageBuffer(frame.sampleBuffer)
                let size = buffer.map { "\(CVPixelBufferGetWidth($0)) × \(CVPixelBufferGetHeight($0))" } ?? "Unknown size"
                let pts = CMSampleBufferGetPresentationTimeStamp(frame.sampleBuffer)
                let timestamp = pts.isNumeric ? pts.seconds : ProcessInfo.processInfo.systemUptime
                self?.recorder.append(VideoSample(buffer: frame.sampleBuffer))
                Task { @MainActor [weak self] in self?.receiveFrame(image, dimensions: size, at: timestamp) }
            }.store(in: streamTokens)
            stream.errorPublisher.listen { [weak self] error in
                Task { @MainActor [weak self] in
                    self?.lastError = "Camera stream failed: \(error.localizedDescription)"
                    if error == .photoCaptureFailed {
                        self?.isCapturingPhoto = false
                        self?.photoStatus = "Photo capture failed. Try again."
                    }
                }
            }.store(in: streamTokens)
            stream.photoDataPublisher.listen { [weak self] photo in
                Task { @MainActor [weak self] in self?.saveStreamPhoto(photo) }
            }.store(in: streamTokens)
            stream.start()
        } catch {
            isStartingStream = false
            lastError = "Camera stream failed: \(error.localizedDescription)"
        }
    }

    func loadRecordings() {
        let folder = recordingsFolder
        savedRecordings = ((try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.creationDateKey], options: [.skipsHiddenFiles])) ?? [])
            .filter { $0.pathExtension.lowercased() == "mp4" }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
    }

    func startRecording() {
        guard isSessionReady, cameraPreviewActive, !isRecording, recordingTask == nil else { return }
        recordingTask = Task { [weak self] in
            guard let self else { return }
            for count in stride(from: 3, through: 1, by: -1) {
                guard self.isSessionReady, self.cameraPreviewActive, !Task.isCancelled else { self.recordingTask = nil; return }
                self.recordingCountdown = count
                try? await Task.sleep(for: .seconds(1))
            }
            self.recordingCountdown = nil
            guard self.isSessionReady, self.cameraPreviewActive, !Task.isCancelled else { self.recordingTask = nil; return }
            do {
                try FileManager.default.createDirectory(at: self.recordingsFolder, withIntermediateDirectories: true)
                let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
                try self.recorder.start(at: self.recordingsFolder.appending(path: "\(stamp).mp4"))
                self.isRecording = true
                for remaining in stride(from: self.recordSeconds, through: 1, by: -1) {
                    guard self.isSessionReady, self.cameraPreviewActive, !Task.isCancelled else { break }
                    self.recordingSecondsRemaining = remaining
                    try? await Task.sleep(for: .seconds(1))
                }
                await self.finishRecording(message: "Recording saved on this iPhone.")
            } catch { self.recordingStatus = "Recording failed: \(error.localizedDescription)"; self.isRecording = false }
            self.recordingTask = nil
        }
    }

    func stopRecording() {
        guard isRecording else { recordingTask?.cancel(); recordingTask = nil; recordingCountdown = nil; return }
        recordingTask?.cancel()
        Task { await finishRecording(message: "Partial recording saved on this iPhone.") }
    }

    private var recordingsFolder: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appending(path: "Recordings")
    }

    private func finishRecording(message: String) async {
        guard isRecording else { return }
        isRecording = false
        recordingSecondsRemaining = nil
        do {
            let result = try await recorder.finish()
            recordingStatus = "\(message) \(String(format: "%.1f", result.duration)) s · \(result.frameCount) frames"
            loadRecordings()
        } catch { recordingStatus = "Could not save recording: \(error.localizedDescription)" }
    }

    func play(_ url: URL) { playbackURL = url }
    func closePlayback() { playbackURL = nil }

    func captureStreamPhoto() {
        guard let camera, streamState == .streaming, !isCapturingPhoto else { return }
        isCapturingPhoto = true
        captureDeliveryID = activeDeliveryID
        captureImageRole = deliveryImageRole
        photoStatus = "Capturing photo…"
        if !camera.stream.capturePhoto(format: .jpeg) {
            isCapturingPhoto = false
            captureDeliveryID = nil
            captureImageRole = nil
            photoStatus = "Photo capture did not start. Wait for the stream and try again."
        }
    }

    private func saveStreamPhoto(_ photo: PhotoData) {
        do {
            let folder = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appending(path: "Photos")
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
            let url = folder.appending(path: "\(stamp).jpg")
            try photo.data.write(to: url, options: .atomic)
            isCapturingPhoto = false
            photoStatus = "Photo saved on this iPhone."
            photoPreviewURL = url
            loadPhotos()
            photoSync.enqueue(url, deliveryID: captureDeliveryID, imageRole: captureImageRole)
            if captureDeliveryID == activeDeliveryID, captureImageRole == deliveryImageRole {
                if deliveryImageRole == "receipt" { deliveryImageRole = "delivery" }
                else if deliveryImageRole == "delivery" { activeDeliveryID = nil; deliveryImageRole = nil }
            }
            captureDeliveryID = nil
            captureImageRole = nil
        } catch {
            isCapturingPhoto = false
            captureDeliveryID = nil
            captureImageRole = nil
            photoStatus = "Could not save photo: \(error.localizedDescription)"
        }
    }

    func startDeliveryPair() {
        activeDeliveryID = UUID()
        deliveryImageRole = "receipt"
        photoStatus = "Delivery check started. Capture the receipt first, then the delivery."
    }

    func cancelDeliveryPair() {
        activeDeliveryID = nil
        deliveryImageRole = nil
        photoStatus = "Delivery photo pairing cancelled. Photos already captured remain saved."
    }

    func loadPhotos() {
        let folder = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appending(path: "Photos")
        savedPhotos = ((try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension.lowercased() == "jpg" }.sorted { $0.lastPathComponent > $1.lastPathComponent }
    }

    func showPhoto(_ url: URL) { photoPreviewURL = url }
    func closePhotoPreview() { photoPreviewURL = nil }

    func stopPreview() {
        if isRecording { stopRecording() }
        guard let camera else { return }
        camera.stop()
        self.camera = nil
        streamTokens.clear()
        clearPreview()
    }

    func openGlassesAppUpdate() async {
        do { try await Wearables.shared.openDATGlassesAppUpdate() }
        catch { report("Could not open the glasses update page", error) }
    }

    private func receiveFrame(_ image: UIImage, dimensions: String, at time: TimeInterval) {
        previewImage = image
        frameDimensions = dimensions
        frameTimes.append(time)
        frameTimes.removeAll { time - $0 > 2 }
        if frameTimes.count > 1, let first = frameTimes.first {
            measuredFPS = Double(frameTimes.count - 1) / max(time - first, 0.001)
        }
    }

    private func clearPreview() {
        previewImage = nil
        frameTimes.removeAll()
        measuredFPS = 0
        frameDimensions = "No frames yet"
        streamState = .stopped
        isStartingStream = false
        camera = nil
        streamTokens.clear()
    }

    func setForeground(_ foreground: Bool) async {
        isForeground = foreground
        if foreground { await refreshStatus() }
        else { stopSession() }
    }

    func refreshStatus() async {
        guard let wearables else { return }
        registrationChanged(wearables.registrationState)
        updateDeviceDetails()
        await checkCameraPermission()
        if let session = deviceSession { sessionChanged(session.state, session: session) }
    }

    private func registrationChanged(_ state: RegistrationState) {
        registrationState = state
        if state != .registered { cameraPermission = nil; stopSession() }
        else { Task { [weak self] in await self?.checkCameraPermission() } }
        if deviceSession == nil {
            switch state {
            case .registered: connectionStatus = "Registered. Check camera access, then start a session."
            case .registering: connectionStatus = "Complete registration, then return to Barista."
            case .available: connectionStatus = "Register Barista with Meta AI."
            case .unavailable: connectionStatus = isMockMode ? "Mock glasses ready for registration." : "Meta AI is unavailable. Check that it is installed and the glasses are paired."
            }
        }
    }

    private func refreshDeviceObservers() {
        let previous = deviceTokens
        deviceTokens.removeAll()
        Task { for token in previous { await token.cancel() } }
        guard let wearables else { return }
        for id in wearables.devices {
            guard let device = wearables.deviceForIdentifier(id) else { continue }
            deviceTokens.append(device.addDeviceStateListener { [weak self] _ in
                Task { @MainActor [weak self] in self?.updateDeviceDetails() }
            })
            deviceTokens.append(device.addCompatibilityListener { [weak self] _ in
                Task { @MainActor [weak self] in self?.updateDeviceDetails() }
            })
        }
        updateDeviceDetails()
    }

    private func updateDeviceDetails() {
        guard let wearables else { return }
        let devices = wearables.devices.compactMap { wearables.deviceForIdentifier($0) }
        hasConnectedDevice = devices.contains { $0.linkState == .connected }
        hasEligibleDevice = deviceSelector?.activeDevice != nil
        let details = devices.enumerated().map { index, device in
            let link: String
            switch device.linkState {
            case .connected: link = "Bluetooth connected"
            case .connecting: link = "Bluetooth connecting"
            case .disconnected: link = "Bluetooth disconnected"
            }
            let compatibility: String
            switch device.compatibility() {
            case .compatible: compatibility = "Compatible"
            case .undefined: compatibility = "Compatibility not reported yet"
            case .deviceUpdateRequired: compatibility = "Glasses firmware update required"
            case .sdkUpdateRequired: compatibility = "App SDK update required"
            @unknown default: compatibility = "Unknown compatibility"
            }
            return "Glasses \(index + 1): \(link) · \(compatibility)"
        }
        deviceStatus = (details.isEmpty ? "No devices discovered." : details.joined(separator: "\n"))
            + "\nEligible for session: \(hasEligibleDevice ? "Yes" : "No")"
    }

    private func sessionChanged(_ state: DeviceSessionState, session: DeviceSession) {
        guard deviceSession === session else { return }
        sessionState = state
        switch state {
        case .started:
            connectionTimeout?.cancel()
            isConnecting = false
            connectionStatus = "Session ready. Keep Barista in the foreground."
        case .starting, .idle:
            connectionStatus = "Starting glasses session…"
        case .paused:
            if isRecording { stopRecording() }
            streamState = .paused
            connectionTimeout?.cancel()
            isConnecting = false
            connectionStatus = "Session paused by the glasses. Waiting for them to resume; no restart is attempted."
        case .stopping:
            isConnecting = false
            isStopping = true
            connectionStatus = "Stopping session…"
        case .stopped:
            if isRecording { stopRecording() }
            stopPreview()
            connectionTimeout?.cancel()
            isConnecting = false
            isStopping = false
            deviceSession = nil
            sessionTokens.clear()
            connectionStatus = "Session stopped. Start a new session when the glasses are ready."
        }
        StartupDiagnostics.record("Session: \(state.description)")
    }

    private func report(_ context: String, _ error: Error) {
        lastError = "\(context): \(error.localizedDescription)"
        StartupDiagnostics.record(lastError ?? context)
    }

    #if DEBUG
    func setMockPermission(_ granted: Bool) async {
        guard isMockMode else { return }
        MockDeviceKit.shared.permissions.set(.camera, granted ? .granted : .denied)
        MockDeviceKit.shared.permissions.setRequestResult(.camera, result: .granted)
        await checkCameraPermission()
    }

    func mockPower(_ on: Bool) {
        guard let mockGlasses else { return }
        if on { mockGlasses.powerOn(); mockGlasses.unfold(); mockGlasses.don() }
        else { mockGlasses.powerOff() }
    }

    func mockTap() { mockGlasses?.services.captouch.tap() }
    #endif
}
