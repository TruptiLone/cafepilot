import SwiftUI
import MWDATCore
import MWDATCamera
import AVKit

struct ContentView: View {
    @ObservedObject var cameraManager: CameraManager
    @AppStorage("autoStartRecording") private var autoStartRecording = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Label("Videos stay on this iPhone", systemImage: "iphone.and.arrow.forward")
                        .font(.headline).foregroundStyle(.green)
                    if cameraManager.isMockMode {
                        Text("MOCK GLASSES · Testing only, no physical glasses")
                            .font(.caption.bold()).foregroundStyle(.orange)
                    }
                    VStack(alignment: .leading, spacing: 16) {
                        Text("Glasses readiness").font(.title2.bold())
                        readiness("SDK configured", ready: cameraManager.isSDKReady,
                                  detail: cameraManager.isSDKReady ? "Initialized" : "Starting…")
                        readiness("App registered", ready: cameraManager.isRegistered,
                                  detail: cameraManager.registrationState.description)
                        readiness("Glasses connected", ready: cameraManager.hasConnectedDevice,
                                  detail: cameraManager.hasConnectedDevice ? "Bluetooth connected" : "Waiting for glasses")
                        readiness("Camera permission", ready: cameraManager.hasCameraPermission,
                                  detail: cameraManager.hasCameraPermission ? "Granted" : "Not granted or not checked")
                        readiness("Session ready", ready: cameraManager.isSessionReady,
                                  detail: cameraManager.sessionDescription)
                    readiness("Camera stream", ready: cameraManager.streamState == .streaming,
                                  detail: cameraManager.streamState.map(String.init(describing:)) ?? "Not started")
                    }
                    .padding().background(.background.secondary, in: RoundedRectangle(cornerRadius: 16))

                    Text(cameraManager.connectionStatus).font(.body)
                        .accessibilityIdentifier("connection-status")
                    Text(cameraManager.deviceStatus).font(.caption).foregroundStyle(.secondary)
                    if let error = cameraManager.lastError {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(.footnote).foregroundStyle(.red)
                            .accessibilityIdentifier("last-error")
                    }
                    if cameraManager.glassesAppUpdateRequired {
                        Button("Open Meta AI Glasses Update") { Task { await cameraManager.openGlassesAppUpdate() } }
                            .buttonStyle(.borderedProminent)
                    }
                    VStack(spacing: 12) {
                        Button(cameraManager.isRegistered ? "Registered" : "Register with Meta AI") {
                            Task { await cameraManager.registerApp() }
                        }
                        .disabled(!cameraManager.isSDKReady || cameraManager.isRegistered || cameraManager.isBusy)
                        Button(cameraManager.hasCameraPermission ? "Camera Access Granted" : "Allow Glasses Camera") {
                            Task { await cameraManager.requestCameraPermission() }
                        }
                        .disabled(!cameraManager.isRegistered || cameraManager.hasCameraPermission || cameraManager.isBusy)
                        Button("Start Glasses Session") { Task { await cameraManager.startSession() } }
                            .disabled(!cameraManager.canStartSession)
                        if let reason = cameraManager.sessionStartBlockReason {
                            Text(reason).font(.footnote).foregroundStyle(.orange)
                                .accessibilityIdentifier("session-start-block-reason")
                        }
                        if cameraManager.sessionState != nil && cameraManager.sessionState != .stopped {
                            Button("End Session", role: .destructive) { cameraManager.stopSession() }
                                .disabled(cameraManager.isStopping)
                        }
                        Button(cameraManager.cameraPreviewActive ? "Stop Preview" : "Start Camera Preview") {
                            cameraManager.cameraPreviewActive ? cameraManager.stopPreview() : cameraManager.startPreview()
                        }
                        .disabled(!cameraManager.isSessionReady || cameraManager.isStartingStream)
                    }
                    .buttonStyle(.borderedProminent)
                    .frame(maxWidth: .infinity)
                    Button("Refresh Status") { Task { await cameraManager.refreshStatus() } }
                        .disabled(!cameraManager.isSDKReady || cameraManager.isBusy)
                    if let image = cameraManager.previewImage {
                        Image(uiImage: image).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 14))
                            .accessibilityIdentifier("glasses-preview")
                    }
                    Text("Frame: \(cameraManager.frameDimensions) · \(cameraManager.measuredFPS, specifier: "%.1f") fps")
                        .font(.footnote.monospacedDigit())
                    if let pairID = cameraManager.activeDeliveryID, let role = cameraManager.deliveryImageRole {
                        Text("Delivery check · \(pairID.uuidString.prefix(8)) · next: \(role.capitalized) photo")
                            .font(.footnote.bold())
                        Button("Cancel Delivery Check") { cameraManager.cancelDeliveryPair() }
                    } else {
                        Button("Start Delivery Check (Receipt + Delivery)") { cameraManager.startDeliveryPair() }
                    }
                    Button(cameraManager.isCapturingPhoto ? "Capturing Photo…" : cameraManager.deliveryImageRole.map { "Capture \($0.capitalized) Photo" } ?? "Take Photo") {
                        cameraManager.captureStreamPhoto()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(cameraManager.streamState != .streaming || cameraManager.isCapturingPhoto)
                    if !cameraManager.photoStatus.isEmpty { Text(cameraManager.photoStatus).font(.footnote) }
                    Text(cameraManager.photoSync.receiverStatus).font(.footnote).foregroundStyle(.secondary)
                    if !cameraManager.savedPhotos.isEmpty {
                        Text("Saved Photos").font(.headline)
                        ScrollView(.horizontal) {
                            HStack {
                                ForEach(cameraManager.savedPhotos, id: \.self) { url in
                                    if let image = UIImage(contentsOfFile: url.path) {
                                        Button { cameraManager.showPhoto(url) } label: {
                                            Image(uiImage: image).resizable().scaledToFill().frame(width: 90, height: 90).clipped().cornerRadius(8)
                                        }.accessibilityLabel(url.lastPathComponent)
                                        Text(cameraManager.photoSync.state(for: url.lastPathComponent)).font(.caption2)
                                    }
                                }
                            }
                        }
                    }
                    Text("Preview checkpoint: recording comes in the next stage. No backend URL or upload token is needed.")
                        .font(.footnote).foregroundStyle(.secondary)
                    if cameraManager.recordingCountdown != nil || cameraManager.isRecording {
                        Text(cameraManager.recordingCountdown.map { "Recording starts in \($0)…" } ?? "Recording · \(cameraManager.recordingSecondsRemaining ?? 0) seconds remaining")
                            .font(.headline).foregroundStyle(.red)
                        Button("Stop Early & Save Clip", role: .destructive) { cameraManager.stopRecording() }
                            .disabled(!cameraManager.isRecording)
                    } else {
                        Toggle("Auto-start when glasses are ready", isOn: $autoStartRecording)
                        Button("Record 15 Seconds") { cameraManager.startRecording() }
                            .buttonStyle(.borderedProminent)
                            .disabled(!cameraManager.isSessionReady || !cameraManager.cameraPreviewActive)
                    }
                    if !cameraManager.recordingStatus.isEmpty {
                        Text(cameraManager.recordingStatus).font(.footnote)
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Saved on this iPhone").font(.headline)
                        ForEach(cameraManager.savedRecordings, id: \.self) { url in
                            Button(url.lastPathComponent) { cameraManager.play(url) }
                        }
                    }
                    if Bundle.main.object(forInfoDictionaryKey: "WiFiStreamingConfigured") as? String == "NO" {
                        Text("Bluetooth-only setup build. Wi-Fi streaming requires Hotspot Configuration and a supported Apple Developer team.")
                            .font(.footnote).foregroundStyle(.orange)
                    }

                    #if DEBUG
                    if cameraManager.isMockMode {
                        DisclosureGroup("Mock test controls") {
                            VStack(alignment: .leading, spacing: 10) {
                                Button("Deny mock camera access") { Task { await cameraManager.setMockPermission(false) } }
                                Button("Grant mock camera access") { Task { await cameraManager.setMockPermission(true) } }
                                Button("Power off mock glasses") { cameraManager.mockPower(false) }
                                Button("Power on mock glasses") { cameraManager.mockPower(true) }
                                Button("Simulate glasses tap") { cameraManager.mockTap() }
                            }.padding(.top, 10)
                        }
                        if let test = cameraManager.mockTestStatus {
                            Text(test).font(.footnote).accessibilityIdentifier("mock-test-status")
                        }
                    }
                    #endif
                }
                .padding()
            }
            .navigationTitle("Barista")
            .navigationBarTitleDisplayMode(.inline)
            .task { cameraManager.loadRecordings() }
            .task { cameraManager.loadPhotos() }
            .onChange(of: cameraManager.isSessionReady) { _, ready in
                if ready && autoStartRecording { cameraManager.startRecording() }
            }
            .sheet(item: Binding(get: { cameraManager.playbackURL.map(PlaybackItem.init) }, set: { if $0 == nil { cameraManager.closePlayback() } })) { item in
                VideoPlayer(player: AVPlayer(url: item.url)).ignoresSafeArea()
            }
            .sheet(item: Binding(get: { cameraManager.photoPreviewURL.map(PlaybackItem.init) }, set: { if $0 == nil { cameraManager.closePhotoPreview() } })) { item in
                if let image = UIImage(contentsOfFile: item.url.path) { Image(uiImage: image).resizable().scaledToFit() }
            }
        }
    }

    private func readiness(_ title: String, ready: Bool, detail: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: ready ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(ready ? .green : .secondary)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.subheadline.bold())
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityValue(ready ? "Ready" : "Not ready")
    }
}

private struct PlaybackItem: Identifiable {
    let url: URL
    var id: URL { url }
    init(_ url: URL) { self.url = url }
}
