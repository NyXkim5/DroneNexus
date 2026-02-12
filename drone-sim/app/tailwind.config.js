/** @type {import('tailwindcss').Config} */
export default {
  content: ['./renderer/**/*.{html,tsx,ts}'],
  theme: {
    extend: {
      colors: {
        nexus: {
          bg: '#0c0c0c',
          panel: '#141414',
          surface: '#1a1a1a',
          border: '#2a2a2a',
          accent: '#4ade80',
          warn: '#f59e0b',
          danger: '#ef4444',
          info: '#60a5fa',
          text: '#d4d4d4',
          muted: '#737373',
          gold: '#d4a843',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Share Tech Mono', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
