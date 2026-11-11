import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Neutral, original palette for OpenQuant (not derived from any product).
        canvas: "#0b0d12",
        surface: "#141821",
        accent: "#4f8cff",
        positive: "#2ecc71",
        negative: "#e74c3c",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
