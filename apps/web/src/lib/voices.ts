export const SUPPORTED_VOICES = [
  "alloy","echo","fable","onyx","nova","shimmer",
  "coral","verse","ballad","ash","sage","marin","cedar",
] as const;
export type Voice = typeof SUPPORTED_VOICES[number];

const KEY = "coach.voice";

export function getSavedVoice(): Voice | null {
  const v = (typeof window !== "undefined" && window.localStorage.getItem(KEY)) || "";
  return (SUPPORTED_VOICES as readonly string[]).includes(v) ? (v as Voice) : null;
}

export function saveVoice(v: Voice) {
  if (typeof window !== "undefined") localStorage.setItem(KEY, v);
}

export function defaultVoiceFor(lang: string): Voice {
  const x = (lang || "").toLowerCase();
  if (x.startsWith("en")) return "verse";
  if (x.startsWith("es")) return "sage";
  return "alloy";
}

export function resolveVoice(lang: string): Voice {
  return getSavedVoice() || defaultVoiceFor(lang);
}
