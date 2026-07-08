import { describe, expect, it } from "vitest";
import { buildPatch, inputStepForParameter } from "./Builder";

describe("buildPatch", () => {
  it("creates nested objects and arrays from editable parameter paths", () => {
    expect(
      buildPatch({
        "simulation.particles": 1200,
        "simulation.source.parameters.0": 1.5,
        "simulation.source.parameters.1": -2,
        "reactor.hot_leg_temp_c": 705
      })
    ).toEqual({
      simulation: {
        particles: 1200,
        source: {
          parameters: [1.5, -2]
        }
      },
      reactor: {
        hot_leg_temp_c: 705
      }
    });
  });

  it("honors the backend-provided step for editable fields", () => {
    expect(inputStepForParameter({ kind: "number", step: 0.01 })).toBe(0.01);
    expect(inputStepForParameter({ kind: "integer", step: 1000 })).toBe(1000);
  });

  it("falls back to a kind-appropriate step when none is provided", () => {
    expect(inputStepForParameter({ kind: "number", step: null })).toBe("any");
    expect(inputStepForParameter({ kind: "integer", step: null })).toBe(1);
    expect(inputStepForParameter({ kind: "number", step: 0 })).toBe("any");
  });
});
