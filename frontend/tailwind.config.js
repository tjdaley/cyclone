/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        navy: {
          DEFAULT: '#003057',
          light:   '#004a8f',
        },
        gold: {
          DEFAULT: '#C9A84C',
          light:   '#e8c97a',
        },
        surface: {
          DEFAULT: '#FFFFFF',
          raised:  '#F0EEE9',
        },
        'off-white': '#F5F5F0',
        border: '#D4D4CF',
        'text-primary':   '#1A1A1A',
        'text-secondary': '#5A5A5A',
        success: '#2D7A4F',
        warning: '#B87E00',
        danger:  '#C0392B',
      },
      fontFamily: {
        display: ['"Playfair Display"', 'Georgia', 'serif'],
        sans:    ['Inter', 'system-ui', 'sans-serif'],
        mono:    ['"JetBrains Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
}
