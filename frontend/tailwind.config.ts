import type { Config } from 'tailwindcss'
import plugin from 'tailwindcss/plugin'

// Токены стиля «Трасса» (Figma rzq7Hlpmq8hTkGJQsJ7AoF, страница «v2 · Трасса»).
// Фирменный фиолетовый — единственный яркий цвет; красный только для сроков и «против».
const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: '#402FFF', hover: '#3526D9', press: '#2B1FB8' },
        ink: { DEFAULT: '#1B1C21', 2: '#4A4B52', 3: '#686970' },
        hairline: '#E4E4E7',
        panel: '#F6F6F8',
        tint: '#F3F1FF',
        steel: '#C8C9CA',
        // красный: плашки/точки сроков и «против» — #EC1D35, текст — #C8102E (контраст на белом ≥ 5:1)
        danger: { DEFAULT: '#EC1D35', text: '#C8102E' },
        // шкала теплокарты (Карта талантов)
        heat: { 1: '#F1EFFF', 2: '#DEDAFF', 3: '#B9B1FF', 4: '#6A5CFF', 5: '#402FFF' },
        identicon: '#E6E2FF',
        bar: '#DCDCE2',
        code: { bg: '#1B1C21', dim: '#9A9BA3' },
      },
      fontFamily: {
        sans: ['Onest', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: {
        pill: '999px',
        panel: '12px',
        'panel-lg': '16px',
        'panel-xl': '20px',
        hero: '24px',
        cell: '10px',
      },
      borderWidth: { 1.5: '1.5px' },
      maxWidth: { page: '1440px' },
      boxShadow: {
        // единственная тень — у плавающих элементов (тосты, поповеры)
        float: '0 8px 28px rgba(27, 28, 33, 0.14), 0 1px 3px rgba(27, 28, 33, 0.08)',
      },
      keyframes: {
        'toast-in': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: { from: { transform: 'translateX(-100%)' }, to: { transform: 'translateX(300%)' } },
        'trace-draw': { from: { transform: 'scaleX(0)' }, to: { transform: 'scaleX(1)' } },
      },
      animation: {
        'toast-in': 'toast-in 180ms ease-out',
        shimmer: 'shimmer 1.4s infinite',
        'trace-draw': 'trace-draw 420ms ease-out both',
      },
    },
  },
  plugins: [
    // Типографическая шкала Figma (v2/*): JetBrains Mono — заголовки и числа, Onest — весь остальной текст.
    plugin(({ addComponents }) => {
      addComponents({
        '.t-display': {
          fontFamily: 'var(--font-mono)',
          fontWeight: '500',
          fontSize: '32px',
          lineHeight: '38px',
          letterSpacing: '-0.8px',
          '@media (min-width: 640px)': { fontSize: '40px', lineHeight: '48px', letterSpacing: '-1px' },
        },
        '.t-title': {
          fontFamily: 'var(--font-mono)',
          fontWeight: '500',
          fontSize: '24px',
          lineHeight: '30px',
          letterSpacing: '-0.4px',
          '@media (min-width: 640px)': { fontSize: '28px', lineHeight: '34px', letterSpacing: '-0.5px' },
        },
        '.t-num-l': {
          fontFamily: 'var(--font-mono)',
          fontWeight: '500',
          fontSize: '32px',
          lineHeight: '36px',
          letterSpacing: '-1px',
          fontVariantNumeric: 'tabular-nums',
        },
        '.t-num-m': {
          fontFamily: 'var(--font-mono)',
          fontWeight: '500',
          fontSize: '20px',
          lineHeight: '24px',
          letterSpacing: '-0.3px',
          fontVariantNumeric: 'tabular-nums',
        },
        '.t-lead': { fontSize: '17px', lineHeight: '26px', fontWeight: '400' },
        '.t-name': { fontSize: '17px', lineHeight: '24px', fontWeight: '600' },
        '.t-body': { fontSize: '15px', lineHeight: '22px', fontWeight: '400' },
        '.t-body-strong': { fontSize: '15px', lineHeight: '22px', fontWeight: '600' },
        '.t-label': { fontSize: '14px', lineHeight: '20px', fontWeight: '500' },
        '.t-small': { fontSize: '13px', lineHeight: '18px', fontWeight: '400' },
        '.t-code': {
          fontFamily: 'var(--font-mono)',
          fontSize: '14px',
          lineHeight: '22px',
          fontWeight: '400',
        },
      })
    }),
  ],
}

export default config
