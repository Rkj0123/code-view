import cytoscape from "cytoscape";
import { describe, expect, it } from "vitest";
import { replaceCanvasElements } from "./GraphCanvas";

describe("canvas graph replacement", () => {
  it("retains descendants and edges when an existing group gains a parent", () => {
    const cy = cytoscape({ headless: true });
    const child = { data: { id: "child", parent: "class" }, position: { x: 120, y: 100 } };
    const edge = { data: { id: "call", source: "caller", target: "child" } };
    replaceCanvasElements(cy, [{ data: { id: "class" } }, child, { data: { id: "caller" } }, edge]);
    cy.zoom(1.5);
    cy.pan({ x: 20, y: 30 });
    replaceCanvasElements(cy, [child, { data: { id: "caller" } }, { data: { id: "file" } }, { data: { id: "class", parent: "file" } }, edge]);
    expect(cy.nodes()).toHaveLength(4);
    expect(cy.getElementById("child").parent().first().id()).toBe("class");
    expect(cy.getElementById("class").parent().first().id()).toBe("file");
    expect(cy.getElementById("call").target().id()).toBe("child");
    expect(cy.getElementById("child").position()).toEqual({ x: 120, y: 100 });
    expect(cy.zoom()).toBe(1.5);
    expect(cy.pan()).toEqual({ x: 20, y: 30 });
    cy.destroy();
  });
});
