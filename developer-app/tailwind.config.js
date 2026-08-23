/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        background: {
          DEFAULT: '#080c14',
          surface: '#0d131f',
          elevated: '#141d2e',
          card: '#101827',
        },
        terminal: {
          border: '#1f2d44',
          borderLight: '#2e4263',
        },
        dev: {
          accent: '#10b981',    // Emerald operator accent
          cyan: '#06b6d4',      // Diagnostic telemetry cyan
          amber: '#f59e0b',     // Warning / degraded amber
          purple: '#a855f7',    // AI / sandbox purple
          blue: '#3b82f6',      // System info blue
        },
        bullish: '#10b981',
        bearish: '#f43f5e',
        warning: '#f59e0b',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
