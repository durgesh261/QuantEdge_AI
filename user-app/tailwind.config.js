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
          DEFAULT: '#080B11',
          surface: '#0E131F',
          elevated: '#151D2F',
          modal: '#1A243B',
        },
        terminal: {
          border: '#1F293D',
          'border-focus': '#06B6D4',
        },
        bullish: {
          DEFAULT: '#10B981',
          subtle: '#064E3B',
          glow: 'rgba(16, 185, 129, 0.15)',
        },
        bearish: {
          DEFAULT: '#F43F5E',
          subtle: '#881337',
          glow: 'rgba(244, 63, 94, 0.15)',
        },
        brand: {
          cyan: '#06B6D4',
          blue: '#3B82F6',
          purple: '#8B5CF6',
        },
        warning: {
          DEFAULT: '#F59E0B',
          subtle: '#78350F',
        }
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['JetBrains Mono', 'Roboto Mono', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}
