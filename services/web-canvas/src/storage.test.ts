import { describe, expect, it } from "vitest";
import { resolveTheme } from "./storage";

describe("resolveTheme", () => {
  it("honors a saved choice before the system preference", () => {
    expect(resolveTheme("dark", true)).toBe("dark");
    expect(resolveTheme("light", false)).toBe("light");
  });

  it("uses the system preference when no valid choice is saved", () => {
    expect(resolveTheme(null, true)).toBe("light");
    expect(resolveTheme("unexpected", false)).toBe("dark");
  });
});
