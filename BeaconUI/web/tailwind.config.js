export default {
  content: ['./index.html', './src/**/*.{html,svelte,js,ts}'],
  theme: {
    extend: {
      keyframes: {
        'fade-in':     { from: { opacity: 0, transform: 'translateY(6px)' }, to: { opacity: 1, transform: 'translateY(0)' } },
        'slide-in-left': { from: { opacity: 0, transform: 'translateX(-8px)' }, to: { opacity: 1, transform: 'translateX(0)' } },
        'slide-down':  { from: { opacity: 0, transform: 'translateY(-4px)' }, to: { opacity: 1, transform: 'translateY(0)' } },
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition:  '200% 0' },
        },
        blink: { '0%,100%': { opacity: 1 }, '50%': { opacity: 0 } },
        'scan': {
          '0%':   { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100vh)' },
        },
      },
      animation: {
        'fade-in':      'fade-in 0.2s ease-out',
        'slide-in-left': 'slide-in-left 0.18s ease-out',
        'slide-down':   'slide-down 0.15s ease-out',
        blink:          'blink 1s step-end infinite',
        shimmer:        'shimmer 2s linear infinite',
      },
    },
  },
  plugins: [require('daisyui')],
  daisyui: {
    themes: [
      {
        c2dark: {
          'color-scheme':  'dark',
          'primary':       '#00e5b0',
          'primary-focus': '#00c99c',
          'secondary':     '#6366f1',
          'accent':        '#f97316',
          'neutral':       '#1c2333',
          'base-100':      '#0d1117',
          'base-200':      '#161b22',
          'base-300':      '#21262d',
          'base-content':  '#c9d1d9',
          'info':          '#58a6ff',
          'success':       '#3fb950',
          'warning':       '#d29922',
          'error':         '#f85149',
        },
      },
    ],
    darkTheme:    'c2dark',
    defaultTheme: 'c2dark',
  },
}
