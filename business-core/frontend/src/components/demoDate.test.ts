import { describe, expect, it } from "vitest";
import { demoDateLabel } from "./demoDate";

describe("demo date", () => {
  it("reads the pinned demo clock in café time", () => {
    expect(demoDateLabel("2026-11-06T02:00:00Z")).toBe("Thu Nov 5, 6 PM");
    expect(demoDateLabel("2026-11-05T18:30:00-08:00")).toBe("Thu Nov 5, 6:30 PM");
  });
});
