import { useEffect, useState } from "react";
import { isTextSize, type TextSize } from "../../shared/displayPreferences";

// Only this display preference is stored. Runtime credentials keep their existing lifecycle.
const memory = new Map<string, TextSize>();
export function readChatTextSize(publicId: string): TextSize {
  if (memory.has(publicId)) return memory.get(publicId)!;
  try {
    const value = localStorage.getItem(`intent.chat.text-size.${publicId}`);
    return isTextSize(value) ? value : "standard";
  } catch { return "standard"; }
}
export function saveChatTextSize(publicId: string, value: TextSize) {
  memory.set(publicId, value);
  try { localStorage.setItem(`intent.chat.text-size.${publicId}`, value); } catch { /* Current browser context retains the preference in memory. */ }
}
export function useChatTextSize(publicId: string): [TextSize, (value: TextSize) => void] {
  const [preference, setPreference] = useState(() => ({ publicId, value: readChatTextSize(publicId) }));
  useEffect(() => { setPreference({ publicId, value: readChatTextSize(publicId) }); }, [publicId]);
  return [preference.publicId === publicId ? preference.value : readChatTextSize(publicId), value => {
    saveChatTextSize(publicId, value); setPreference({ publicId, value });
  }];
}
