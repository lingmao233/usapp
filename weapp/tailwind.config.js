/** @type {import('tailwindcss').Config}
 *
 * 小程序端 tailwind 配置：类名体系与 web 端（根目录 tailwind.config.js 的默认色板）一致。
 * 关闭 preflight（WXSS 不支持通配选择器，基础样式由 app.scss 提供）。
 */
module.exports = {
  content: ['./src/**/*.{js,ts,jsx,tsx}'],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {},
  },
  plugins: [],
}
