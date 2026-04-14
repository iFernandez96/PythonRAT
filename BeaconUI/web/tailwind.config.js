/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{html,svelte,js,ts}'],
  theme: { extend: {} },
  plugins: [require('daisyui')],
  daisyui: {
    themes: [
      {
        c2dark: {
          'color-scheme':  'dark',
          'primary':       '#00e5b0',   /* neon teal */
          'secondary':     '#6366f1',   /* indigo    */
          'accent':        '#f97316',   /* orange    */
          'neutral':       '#1c2333',
          'base-100':      '#0d1117',   /* near-black (GitHub-dark style) */
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
