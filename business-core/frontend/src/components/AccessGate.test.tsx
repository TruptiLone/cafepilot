import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LOCKED_EVENT, watchForLockedApi } from "../api/demo";
import { assistant, setModelProvider } from "../utils/brand";
import AccessGate from "./AccessGate";
import StartOver from "./StartOver";

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setModelProvider("meta");
});

describe("AccessGate", () => {
  it("opens straight away when no code is needed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ required: false, granted: false, muse_calls_left: null }));
    render(<AccessGate><p>The café</p></AccessGate>);
    expect(await screen.findByText("The café")).toBeTruthy();
  });

  it("names the assistant for the models the API says run the café", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json({ required: false, granted: false, muse_calls_left: null, model_provider: "openai" }));
    const Name = () => <p>{assistant()} is ready</p>;
    render(<AccessGate><Name /></AccessGate>);
    expect(await screen.findByText("CaféPilot is ready")).toBeTruthy();
  });

  it("asks for the code, shows a wrong one, and opens on the right one", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      if (!init?.method) return json({ required: true, granted: false, muse_calls_left: 40 });
      const { code } = JSON.parse(String(init.body));
      return code === "latte"
        ? json({ required: true, granted: true, muse_calls_left: 40 })
        : json({ detail: "That code isn't right. Check it and try again." }, 401);
    });
    render(<AccessGate><p>The café</p></AccessGate>);

    const input = await screen.findByLabelText("Access code");
    expect(screen.queryByText("The café")).toBeNull();
    fireEvent.change(input, { target: { value: "mocha" } });
    fireEvent.click(screen.getByText("Open the demo"));
    expect(await screen.findByText(/isn't right/)).toBeTruthy();

    fireEvent.change(input, { target: { value: " latte " } });
    fireEvent.click(screen.getByText("Open the demo"));
    expect(await screen.findByText("The café")).toBeTruthy();
    expect(JSON.parse(String((fetchMock.mock.calls.at(-1)![1] as RequestInit).body))).toEqual({ code: "latte" });
  });

  it("comes back when an API call says the access has run out", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockImplementationOnce(() => json({ required: true, granted: true, muse_calls_left: null }))
      .mockImplementation(() => json({ required: true, granted: false, muse_calls_left: null }));
    render(<AccessGate><p>The café</p></AccessGate>);
    expect(await screen.findByText("The café")).toBeTruthy();
    act(() => {
      window.dispatchEvent(new Event(LOCKED_EVENT));
    });
    expect(await screen.findByLabelText("Access code")).toBeTruthy();
  });

  it("opens a fresh café without asking for the code again when the old one was closed", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ required: true, granted: true, muse_calls_left: null }));
    let mounts = 0;
    function Cafe() {
      mounts += 1;
      return <p>The café</p>;
    }
    render(<AccessGate><Cafe /></AccessGate>);
    expect(await screen.findByText("The café")).toBeTruthy();
    const before = mounts;
    act(() => {
      window.dispatchEvent(new Event(LOCKED_EVENT));
    });
    await vi.waitFor(() => expect(mounts).toBeGreaterThan(before)); // the pages load again from the new café
    expect(screen.queryByLabelText("Access code")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("watchForLockedApi", () => {
  it("announces a locked API answer and passes every answer through", async () => {
    const target = new EventTarget() as unknown as typeof globalThis;
    (target as { fetch: typeof fetch }).fetch = vi
      .fn()
      .mockImplementationOnce(() => json({ detail: "x" }, 401, { "X-CafePilot-Access": "required" }))
      .mockImplementationOnce(() => json({ detail: "x" }, 401));
    const heard = vi.fn();
    target.addEventListener(LOCKED_EVENT, heard);
    watchForLockedApi(target);
    expect((await target.fetch("/api/a")).status).toBe(401);
    await target.fetch("/api/b"); // a plain 401 (a wrong access code) is not a lock-out
    expect(heard).toHaveBeenCalledTimes(1);
  });
});

describe("StartOver", () => {
  it("asks first, then resets and reloads", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ reset: true, message: "ok" }));
    const reload = vi.fn();
    render(<StartOver reload={reload} />);

    fireEvent.click(screen.getByText("Start the demo over"));
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Start the demo over"));
    await vi.waitFor(() => expect(reload).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/v1/demo/reset");
  });
});
