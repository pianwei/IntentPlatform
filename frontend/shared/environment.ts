// Only an explicitly selected development build exposes local identity fixtures.
// The production API also rejects development routes independently of this UI.
export const DEVELOPMENT_UI = import.meta.env.MODE === "development";
