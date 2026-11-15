/** Who the screens say did the work. On Meta's models it is Muse; on OpenAI's it is CaféPilot, so the
 * screens never credit Muse for work it didn't do. The provider comes from /api/v1/demo/access (AccessGate)
 * before any page renders. */
export type ModelProvider = "meta" | "openai";

let provider: ModelProvider = "meta";

export function setModelProvider(value: unknown): void {
  provider = value === "openai" ? "openai" : "meta";
}

export const modelProvider = (): ModelProvider => provider;

/** "Muse found a new event" / "CaféPilot found a new event". */
export const assistant = (): string => (provider === "openai" ? "CaféPilot" : "Muse");

/** "Powered by …" */
export const poweredBy = (): string => (provider === "openai" ? "OpenAI models" : "Meta Muse models");

/** The model that reads slips and photos, for "Read by …". */
export const visionModel = (): string => (provider === "openai" ? "OpenAI" : "Muse Spark");

/** The model that paints post backgrounds, mid-sentence. */
export const imageModel = (): string => (provider === "openai" ? "the image model" : "Muse Image");
