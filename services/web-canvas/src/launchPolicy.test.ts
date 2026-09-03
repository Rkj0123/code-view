import { describe, expect, it, vi } from "vitest";
import { approveThenStart } from "./launchPolicy";

describe("approveThenStart", () => {
  it("asks the host for approval before every start", async () => {
    const order: string[] = [];
    await approveThenStart(async () => { order.push("approve"); }, async () => { order.push("start"); return "running"; });
    expect(order).toEqual(["approve", "start"]);
  });

  it("never starts when server approval fails", async () => {
    const start = vi.fn();
    await expect(approveThenStart(async () => { throw new Error("denied"); }, start)).rejects.toThrow("denied");
    expect(start).not.toHaveBeenCalled();
  });
});
