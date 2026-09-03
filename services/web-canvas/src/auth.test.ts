import { describe, expect, it, vi } from "vitest";
import { bootstrapCapabilityToken } from "./auth";

describe("bootstrapCapabilityToken", () => {
  it("returns the live token in memory and removes it from the visible URL", () => {
    const replace = vi.fn();
    expect(bootstrapCapabilityToken(true, "http://127.0.0.1:4173/repository?token=secret&base=HEAD#graph", replace)).toBe("secret");
    expect(replace).toHaveBeenCalledWith("/repository?base=HEAD#graph");
  });

  it("strips an accidental demo token without retaining it", () => {
    const replace = vi.fn();
    expect(bootstrapCapabilityToken(false, "http://127.0.0.1:4173/?token=secret", replace)).toBeNull();
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("retains a session capability after the bootstrap URL is stripped", () => {
    expect(bootstrapCapabilityToken(true, "http://127.0.0.1:4173/", () => undefined, "retained")).toBe("retained");
  });
});
