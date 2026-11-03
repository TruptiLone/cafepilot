import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import VideosPage from "./VideosPage";

const clip = {
  id: "clip-1", filename: "20261031T070000Z_clip-1.mp4", url: "/api/videos/clip-1/content",
  captured_at: "2026-10-31T07:00:00Z", received_at: "2026-10-31T07:00:01Z", size_bytes: 100000,
  source: "test", processing: { status: "complete", model: "muse-spark-1.3", duration_seconds: 15,
    width: 360, height: 640, codec: "h264", total_frames: 15, completed_frames: 15, error: null,
    frames: [{ number: 0, offset_seconds: 0, latency_ms: 100, error: null,
      result: { items: [{ product: "Whole milk", visible_units: 2, possibly_hidden_units: true, confidence: "high" }],
        visible_damage: null, notes: "Two cartons are visible." } }], updated_at: "2026-10-31T07:00:05Z" },
};
const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("URL", Object.assign(URL, {
    createObjectURL: vi.fn(() => "blob:test-video"), revokeObjectURL: vi.fn(),
  }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); sessionStorage.clear(); });

describe("VideosPage", () => {
  it("authenticates with a header and shows model results and playable video", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(input =>
      String(input).includes("/content") ? Promise.resolve(new Response(new Blob(["video"], { type: "video/mp4" }))) : json([clip]));
    render(<VideosPage />);
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Upload token"), { target: { value: "secret-for-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Test clip · not from glasses")).toBeTruthy();
    expect(screen.getByText("Model: muse-spark-1.3")).toBeTruthy();
    expect(screen.getByText(/15.0 seconds · 360 × 640/)).toBeTruthy();
    expect(await screen.findByLabelText(/Clip recorded/)).toBeTruthy();
    expect((screen.getByLabelText(/Clip recorded/) as HTMLVideoElement).controls).toBe(true);
    fireEvent.click(screen.getByText("Frame-by-frame results"));
    expect(screen.getByText("Two cartons are visible.")).toBeTruthy();
    expect(screen.getByText(/Repeated views are not added together/)).toBeTruthy();
    for (const [url, options] of fetchMock.mock.calls) {
      expect(String(url)).not.toContain("secret-for-test");
      expect((options?.headers as Record<string, string>).Authorization).toBe("Bearer secret-for-test");
    }
  });

  it("polls new clips into the page without a reload", async () => {
    vi.useFakeTimers();
    sessionStorage.setItem("cafepilot-video-token", "token");
    let reads = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(input => String(input).includes("/content")
      ? Promise.resolve(new Response(new Blob(["video"]))) : json(reads++ === 0 ? [] : [clip]));
    await act(async () => { render(<VideosPage />); });
    expect(screen.getByText("Ready for your first clip")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText("Test clip · not from glasses")).toBeTruthy();
  });

  it("keeps loaded clips visible during a polling outage", async () => {
    vi.useFakeTimers();
    sessionStorage.setItem("cafepilot-video-token", "token");
    let reads = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(input => {
      if (String(input).includes("/content")) return Promise.resolve(new Response(new Blob(["video"])));
      return reads++ === 0 ? json([clip]) : Promise.reject(new Error("Network unavailable"));
    });
    await act(async () => { render(<VideosPage />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByRole("alert").textContent).toContain("Network unavailable");
    expect(screen.getByText("Model: muse-spark-1.3")).toBeTruthy();
    expect(screen.getByText("Reconnecting")).toBeTruthy();
  });

  it("clears a rejected token and returns to the connection form", async () => {
    sessionStorage.setItem("cafepilot-video-token", "wrong-token");
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ detail: "Invalid media service token." }, 401));
    render(<VideosPage />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(await screen.findByLabelText("Upload token")).toBeTruthy();
    expect(sessionStorage.getItem("cafepilot-video-token")).toBeNull();
  });
});
