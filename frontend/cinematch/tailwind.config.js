/** @type {import('tailwindcss').Config} */
// Cores vêm de variáveis CSS (canais RGB) definidas em index.css para os temas escuro e claro.
const token = name => `rgb(var(--c-${name}) / <alpha-value>)`;
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        background: token('bg'),
        surface: token('surface'),
        raised: token('raised'),
        ink: token('ink'),
        muted: token('muted'),
        amber: token('amber'),
        focus: token('focus'),
        // contraste neutro para bordas e véus: branco no escuro, quase preto no claro
        fg: token('fg'),
        footer: token('footer'),
      },
      fontFamily: { sans: ['Inter', 'system-ui'], display: ['Sora', 'system-ui'] },
      boxShadow: { cinema: '0 22px 55px rgb(0 0 0 / var(--shadow-alpha))' },
    },
  },
  plugins: [],
};
