import path from 'node:path'
import { defineConfig, type UserConfigExport } from '@tarojs/cli'
import tailwindcss from 'tailwindcss'
import { UnifiedViteWeappTailwindcssPlugin as uvtw } from 'weapp-tailwindcss/vite'
import type { Plugin } from 'vite'

import devConfig from './dev'
import prodConfig from './prod'

// https://taro.zone/config
export default defineConfig<'vite'>(async (merge) => {
  const baseConfig: UserConfigExport<'vite'> = {
    projectName: 'weapp',
    date: '2026-7-30',
    // 375 设计宽度：1px = 2rpx，与 web 端 px 数值一致，样式直接照搬
    designWidth: 375,
    deviceRatio: {
      640: 2.34 / 2,
      750: 1,
      375: 2,
      828: 1.81 / 2,
    },
    sourceRoot: 'src',
    outputRoot: 'dist',
    plugins: [],
    defineConstants: {},
    copy: {
      patterns: [],
      options: {},
    },
    framework: 'react',
    compiler: {
      type: 'vite',
      vitePlugins: [
        {
          // taro vite 不加载 postcss.config.js，内联注册 tailwindcss
          name: 'postcss-config-loader-plugin',
          config(config) {
            if (typeof config.css?.postcss === 'object') {
              config.css?.postcss.plugins?.unshift(tailwindcss())
            }
          },
        },
        {
          // @ → weapp/src；@core → 复用 web 端平台无关核心层（类型 / API / 存储抽象）
          name: 'alias-core-plugin',
          config(config) {
            config.resolve = config.resolve || {}
            config.resolve.alias = {
              ...(config.resolve.alias || {}),
              '@': path.resolve(__dirname, '..', 'src'),
              '@core': path.resolve(__dirname, '..', '..', 'src', 'core'),
            }
          },
        },
        uvtw({
          rem2rpx: true,
          disabled: process.env.TARO_ENV === 'h5' || process.env.TARO_ENV === 'rn',
          // taro vite 默认移除 css 变量，需重新注入（us-* 组件类依赖暖纸配色变量）
          injectAdditionalCssVarScope: true,
        }),
      ] as Plugin[],
    },
    mini: {
      postcss: {
        pxtransform: {
          enable: true,
          config: {},
        },
        cssModules: {
          enable: false,
        },
      },
    },
    h5: {},
  }

  if (process.env.NODE_ENV === 'development') {
    return merge({}, baseConfig, devConfig)
  }
  return merge({}, baseConfig, prodConfig)
})
