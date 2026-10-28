import { describe, expect, it } from "vitest";
import { toCafeTimestamp } from "./cafeTime";

describe("toCafeTimestamp", () => {
  it("converts a Los Angeles wall time to an explicit UTC timestamp", () => {
    expect(toCafeTimestamp("2025-01-08T09:00")).toBe("2025-01-08T17:00:00.000Z");
  });

  it("rejects a nonexistent daylight-saving wall time", () => {
    expect(toCafeTimestamp("2025-03-09T02:30")).toBeNull();
  });

  it("rejects malformed date-time values", () => {
    expect(toCafeTimestamp("2025-01-08")).toBeNull();
  });
});
