import { describe, expect, it } from "vitest";
import { normalizeIndexStatus, normalizeLaunchConfiguration } from "./api";

const fullHostResponse = {
  generation: 7,
  display: "python -m tictactoe",
  argv: ["python", "-m", "tictactoe"],
  cwd: ".",
  mode: "manual",
  allowLaunch: true,
  running: true,
  started: true,
  pid: 1234,
};

describe("launch configuration response", () => {
  it("retains the full host state after start and ignores route metadata", () => {
    expect(normalizeLaunchConfiguration(fullHostResponse)).toMatchObject({
      display: "python -m tictactoe",
      mode: "manual",
      allowLaunch: true,
      running: true,
    });
  });

  it("rejects the old partial response that hid the running process", () => {
    expect(() => normalizeLaunchConfiguration({ started: true, pid: 1234 })).toThrow("Invalid launch configuration response");
  });

  it("requires a safe generation for displayed-command approval", () => {
    for (const generation of [undefined, -1, 1.5, "7", Number.MAX_SAFE_INTEGER + 1]) {
      expect(() => normalizeLaunchConfiguration({ ...fullHostResponse, generation })).toThrow("Invalid launch configuration response");
    }
  });

  it("preserves same-generation failures and in-progress state", () => {
    const failed = { generation: 7, state: "ready", lastError: "Invalid config" };
    expect(normalizeIndexStatus(failed)).toEqual(failed);
    expect(normalizeIndexStatus({ ...failed, state: "indexing", lastError: null })).toEqual({ generation: 7, state: "indexing", lastError: null });
    for (const body of [null, { generation: 7 }, { ...failed, generation: "7" }, { ...failed, lastError: false }, { ...failed, state: "done" }]) {
      expect(() => normalizeIndexStatus(body)).toThrow("Invalid index status response");
    }
  });
});
