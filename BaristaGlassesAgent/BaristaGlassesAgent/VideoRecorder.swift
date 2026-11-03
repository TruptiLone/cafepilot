import AVFoundation
import Foundation

// CMSampleBuffer is retained while the encoder queue uses it. The SDK delivers
// immutable frames; none of their storage is mutated here.
nonisolated struct VideoSample: @unchecked Sendable {
    let buffer: CMSampleBuffer
}

nonisolated struct RecordedVideo: Sendable {
    let url: URL
    let duration: Double
    let frameCount: Int
}

nonisolated enum RecordingError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let message): return message }
    }
}

/// All encoder state lives on one queue. At most three frames can be waiting,
/// so a slow encoder drops frames instead of growing memory without a bound.
nonisolated final class VideoRecorder: @unchecked Sendable {
    private final class Recording: @unchecked Sendable {
        let url: URL
        var writer: AVAssetWriter?
        var input: AVAssetWriterInput?
        var firstTime: CMTime?
        var lastTime: CMTime?
        var frameCount = 0
        var error: Error?
        init(url: URL) { self.url = url }
    }

    private let queue = DispatchQueue(label: "com.cafepilot.video-encoder", qos: .userInitiated)
    private let slots = DispatchSemaphore(value: 3)
    private var recording: Recording?

    func start(at url: URL) throws {
        try queue.sync {
            guard recording == nil else { throw RecordingError.message("A recording is already active.") }
            guard !FileManager.default.fileExists(atPath: url.path) else {
                throw RecordingError.message("The recording file already exists.")
            }
            recording = Recording(url: url)
        }
    }

    func append(_ sample: VideoSample) {
        guard slots.wait(timeout: .now()) == .success else { return }
        queue.async { [self] in
            defer { slots.signal() }
            guard let recording, recording.error == nil else { return }
            do { try append(sample.buffer, to: recording) }
            catch { recording.error = error }
        }
    }

    private func append(_ buffer: CMSampleBuffer, to recording: Recording) throws {
        guard let image = CMSampleBufferGetImageBuffer(buffer) else {
            throw RecordingError.message("The glasses did not provide a raw video frame.")
        }
        let time = CMSampleBufferGetPresentationTimeStamp(buffer)
        guard time.isNumeric else { throw RecordingError.message("A video frame has no valid timestamp.") }
        if let last = recording.lastTime, time <= last { return }

        if recording.writer == nil {
            let writer = try AVAssetWriter(outputURL: recording.url, fileType: .mp4)
            let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
                AVVideoCodecKey: AVVideoCodecType.h264,
                AVVideoWidthKey: CVPixelBufferGetWidth(image),
                AVVideoHeightKey: CVPixelBufferGetHeight(image),
                AVVideoCompressionPropertiesKey: [
                    AVVideoAverageBitRateKey: 4_000_000,
                    AVVideoExpectedSourceFrameRateKey: 7,
                    AVVideoMaxKeyFrameIntervalKey: 14,
                ],
            ])
            input.expectsMediaDataInRealTime = true
            guard writer.canAdd(input) else { throw RecordingError.message("Video encoding is unavailable.") }
            writer.add(input)
            guard writer.startWriting() else {
                throw writer.error ?? RecordingError.message("Could not start the video encoder.")
            }
            writer.startSession(atSourceTime: time)
            recording.writer = writer
            recording.input = input
            recording.firstTime = time
        }
        guard let writer = recording.writer, let input = recording.input else { return }
        guard writer.status == .writing else {
            throw writer.error ?? RecordingError.message("The video encoder stopped unexpectedly.")
        }
        guard input.isReadyForMoreMediaData else { return }
        guard input.append(buffer) else {
            throw writer.error ?? RecordingError.message("Could not encode a glasses video frame.")
        }
        recording.lastTime = time
        recording.frameCount += 1
    }

    func finish() async throws -> RecordedVideo {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                guard let current = recording else {
                    continuation.resume(throwing: RecordingError.message("There is no active recording."))
                    return
                }
                recording = nil
                if let error = current.error {
                    current.writer?.cancelWriting()
                    try? FileManager.default.removeItem(at: current.url)
                    continuation.resume(throwing: error)
                    return
                }
                guard let writer = current.writer, let input = current.input,
                      let first = current.firstTime, let last = current.lastTime, current.frameCount > 0 else {
                    try? FileManager.default.removeItem(at: current.url)
                    continuation.resume(throwing: RecordingError.message("No video frames were received. Start the camera stream and try again."))
                    return
                }
                let end = last + CMTime(value: 1, timescale: 24)
                writer.endSession(atSourceTime: end)
                input.markAsFinished()
                writer.finishWriting { [self] in
                    queue.async {
                        if writer.status == .completed {
                            continuation.resume(returning: RecordedVideo(
                                url: current.url, duration: (end - first).seconds, frameCount: current.frameCount
                            ))
                        } else {
                            try? FileManager.default.removeItem(at: current.url)
                            continuation.resume(throwing: writer.error ?? RecordingError.message("Could not finalize the video file."))
                        }
                    }
                }
            }
        }
    }
}
