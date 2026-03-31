import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#0a0a0a",
        "surface-elevated": "#141414",
        accent: "#8b5cf6",
        "accent-dim": "#6d28d9",
      },
    },
  },
  plugins: [],
};

export default config;
