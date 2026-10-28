import { describe, expect, it } from "vitest";
import { resolveCafeTimestamp, toCafeTimestamp } from "./cafeTime";

describe("toCafeTimestamp", () => {
  it("converts a Los Angeles wall time to an explicit UTC timestamp", () => {
    expect(toCafeTimestamp("2025-01-08T09:00")).toBe("2025-01-08T17:00:00.000Z");
  });

  it("rejects a nonexistent daylight-saving wall time", () => {
    expect(toCafeTimestamp("2025-03-09T02:30")).toBeNull();
    expect(resolveCafeTimestamp("2025-03-09T02:30")).toEqual({
      status: "nonexistent",
    });
  });

  it("surfaces both offsets for an ambiguous daylight-saving wall time", () => {
    const resolution = resolveCafeTimestamp("2025-11-02T01:30");
    expect(resolution.status).toBe("ambiguous");
    if (resolution.status === "ambiguous") {
      expect(resolution.options.map((option) => option.timestamp)).toEqual([
        "2025-11-02T08:30:00.000Z",
        "2025-11-02T09:30:00.000Z",
      ]);
      expect(resolution.options[0].label).toContain("UTC-07:00");
      expect(resolution.options[1].label).toContain("UTC-08:00");
    }
    expect(toCafeTimestamp("2025-11-02T01:30")).toBeNull();
  });

  it("rejects malformed date-time values", () => {
    expect(toCafeTimestamp("2025-01-08")).toBeNull();
    expect(resolveCafeTimestamp("2025-02-30T09:00")).toEqual({ status: "invalid" });
  });
});
