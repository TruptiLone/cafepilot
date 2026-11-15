import { afterEach, describe, expect, it } from "vitest";
import { modelName } from "../components/muse/story";
import { assistant, imageModel, poweredBy, setModelProvider, visionModel } from "./brand";

describe("brand", () => {
  afterEach(() => setModelProvider("meta"));

  it("says Muse on Meta's models (the default, also for an older API that doesn't say)", () => {
    setModelProvider(undefined);
    expect([assistant(), poweredBy(), visionModel(), imageModel()]).toEqual(["Muse", "Meta Muse models", "Muse Spark", "Muse Image"]);
  });

  it("never credits Muse when OpenAI's models run the café", () => {
    setModelProvider("openai");
    expect([assistant(), poweredBy(), visionModel(), imageModel()]).toEqual(["CaféPilot", "OpenAI models", "OpenAI", "the image model"]);
  });

  it("makes model ids readable for both providers", () => {
    expect(modelName("muse-wonder-1.0")).toBe("Muse Wonder 1.0");
    expect(modelName("gpt-6-sol")).toBe("GPT-6 Sol");
    expect(modelName("gpt-image-2.6-flare")).toBe("GPT-Image 2.6 Flare");
  });
});
