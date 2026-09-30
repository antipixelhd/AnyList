// @ts-check
import { defineConfig } from 'astro/config';

import tailwindcss from '@tailwindcss/vite';

import node from '@astrojs/node';

const previewHostname = process.env.PREVIEW_HOSTNAME;
if (previewHostname && !/^[a-z0-9][a-z0-9.-]*$/i.test(previewHostname)) {
  throw new Error('PREVIEW_HOSTNAME must be a hostname without scheme or port');
}
const allowedHosts = ['abstract-dev.bellamylab.com', 'scrob-dev.bellamylab.com',
  ...(previewHostname ? [previewHostname] : [])];
const previewUrl = previewHostname ? new URL(process.env.SERVER_URL || '') : undefined;

// https://astro.build/config
export default defineConfig({
  output: 'server',
  compressHTML: true,
  devToolbar: {enabled: false},

  security: {
    // middleware.ts checks browser writes while preserving external API clients.
    checkOrigin: false,
  },

  server: {
    port: 7330,
    allowedHosts,
  },

  vite: {
    plugins: [tailwindcss()],
    // Astro routes and islands are loaded on demand. Bundle their client
    // dependencies at startup so visiting a new page does not invalidate
    // optimized URLs already referenced by an open page.
    optimizeDeps: {
      include: ['qrcode', 'chart.js/auto'],
    },
    server: {
      allowedHosts,
      // Serve binds the same port on the Tailscale interface. Vite otherwise
      // probes the wildcard address and silently moves to another port.
      ...(previewHostname ? { host: '127.0.0.1', strictPort: true } : {}),
      ...(previewHostname ? { hmr: { protocol: 'wss', host: previewHostname,
        clientPort: Number(previewUrl?.port || 443) } } : {}),
    }
  },

  adapter: node({
    mode: 'standalone'
  })
});
