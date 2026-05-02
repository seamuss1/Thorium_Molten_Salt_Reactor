import { describe, expect, it } from "vitest";
import { buildPatch } from "./Builder";

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
});
