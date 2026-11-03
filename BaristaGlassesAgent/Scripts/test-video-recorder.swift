// Compile with VideoRecorder.swift on macOS; uses synthetic pixels, not a camera.
import AVFoundation
import Foundation

@main
struct RecorderSmokeTest {
    static func frame(at index: Int) throws -> VideoSample {
        var pixels: CVPixelBuffer?
        guard CVPixelBufferCreate(kCFAllocatorDefault, 320, 240, kCVPixelFormatType_32BGRA,
                                 [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &pixels) == kCVReturnSuccess,
              let pixels else { throw RecordingError.message("Could not create test pixels.") }
        CVPixelBufferLockBaseAddress(pixels, [])
        memset(CVPixelBufferGetBaseAddress(pixels), Int32(index * 8), CVPixelBufferGetDataSize(pixels))
        CVPixelBufferUnlockBaseAddress(pixels, [])
        var format: CMVideoFormatDescription?
        guard CMVideoFormatDescriptionCreateForImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: pixels,
                                                           formatDescriptionOut: &format) == noErr,
              let format else { throw RecordingError.message("Could not describe test pixels.") }
        var timing = CMSampleTimingInfo(duration: CMTime(value: 1, timescale: 24),
            presentationTimeStamp: CMTime(value: Int64(240 + index), timescale: 24), decodeTimeStamp: .invalid)
        var sample: CMSampleBuffer?
        guard CMSampleBufferCreateReadyWithImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: pixels,
            formatDescription: format, sampleTiming: &timing, sampleBufferOut: &sample) == noErr,
              let sample else { throw RecordingError.message("Could not create a test frame.") }
        return VideoSample(buffer: sample)
    }

    static func main() async throws {
        let path = CommandLine.arguments.dropFirst().first ?? "/tmp/cafepilot-recorder-test.mp4"
        let output = URL(fileURLWithPath: path)
        try? FileManager.default.removeItem(at: output)
        let recorder = VideoRecorder()
        try recorder.start(at: output)
        do {
            try recorder.start(at: output)
            fatalError("A second recording should be rejected")
        } catch RecordingError.message { }
        for index in 0..<24 {
            recorder.append(try frame(at: index))
            try await Task.sleep(for: .milliseconds(10))
        }
        let result = try await recorder.finish()
        precondition(result.frameCount > 1 && result.frameCount <= 24)
        precondition(result.duration > 0 && result.duration <= 1.01)
        let asset = AVURLAsset(url: output)
        let tracks = try await asset.loadTracks(withMediaType: .video)
        let audio = try await asset.loadTracks(withMediaType: .audio)
        let duration = try await asset.load(.duration)
        precondition(tracks.count == 1 && audio.isEmpty)
        precondition(duration.seconds > 0 && duration.seconds <= 1.01)
        // Decode an actual image from the MP4 rather than checking only its header.
        let generator = AVAssetImageGenerator(asset: asset)
        let image = try await generator.image(at: .zero).image
        precondition(image.width == 320 && image.height == 240)

        let empty = output.deletingLastPathComponent().appending(path: "empty-\(UUID().uuidString).mp4")
        try recorder.start(at: empty)
        do {
            _ = try await recorder.finish()
            fatalError("An empty recording should not produce a video")
        } catch RecordingError.message { }
        precondition(!FileManager.default.fileExists(atPath: empty.path))
        print("PASS: MP4 contains decodable 320×240 video, no audio, valid duration; duplicate starts and empty recordings are rejected.")
        print("Test video: \(output.path)")
    }
}
