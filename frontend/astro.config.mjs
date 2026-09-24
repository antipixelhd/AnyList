// @ts-check
import { defineConfig } from 'astro/config';

import tailwindcss from '@tailwindcss/vite';

import node from '@astrojs/node';
import react from '@astrojs/react';

// https://astro.build/config
export default defineConfig({
  integrations: [react()],
  output: 'server',
  devToolbar: {enabled: false},

  security: {
    checkOrigin: false,
  },

  server: {
    port: 7330,
  },

  vite: {
    plugins: [tailwindcss()],
    // Astro routes and islands are loaded on demand. Bundle their client
    // dependencies at startup so visiting a new page does not invalidate
    // optimized URLs already referenced by an open page.
    optimizeDeps: {
      include: ['@mdxeditor/editor', '@mdxeditor/gurx', 'qrcode', 'chart.js/auto', 'hls.js'],
    },
    server: {
      allowedHosts: ['abstract-dev.bellamylab.com', 'scrob-dev.bellamylab.com'],
    }
  },

  adapter: node({
    mode: 'standalone'
  })
});
