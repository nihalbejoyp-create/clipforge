import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#07090d',
          900: '#0a0d13',
          850: '#0e1219',
          800: '#121722',
          750: '#161c29',
          700: '#1c2331',
          600: '#26303f',
        },
        mist: {
          100: '#eef2f9',
          200: '#dfe6f2',
          300: '#c3cddd',
          400: '#9aa7bd',
          500: '#78849a',
        },
        accent: {
          DEFAULT: '#6d8cff',
          soft: '#8ea5ff',
          purple: '#a06bff',
          green: '#3ddc97',
          amber: '#ffb84d',
          red: '#ff6b6b',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Inter', 'Segoe UI', 'sans-serif'],
        mono: ['SF Mono', 'ui-monospace', 'Menlo', 'monospace'],
      },
      boxShadow: {
        panel: '0 1px 0 rgba(255,255,255,0.03) inset, 0 24px 60px -30px rgba(0,0,0,0.9)',
        glow: '0 0 0 1px rgba(109,140,255,0.35), 0 12px 40px -12px rgba(109,140,255,0.45)',
      },
      keyframes: {
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pulseSoft: {
          '0%,100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-400px 0' },
          '100%': { backgroundPosition: '400px 0' },
        },
      },
      animation: {
        fadeUp: 'fadeUp 0.35s ease-out both',
        pulseSoft: 'pulseSoft 1.6s ease-in-out infinite',
        shimmer: 'shimmer 1.6s linear infinite',
      },
    },
  },
  plugins: [],
};

export default config;
