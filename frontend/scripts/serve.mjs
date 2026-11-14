import http from 'node:http';
import compression from 'compression';

process.env.ASTRO_NODE_AUTOSTART = 'disabled';
const {handler} = await import('../dist/server/entry.mjs');
const compress = compression();
const host = process.env.HOST || '127.0.0.1';
const port = Number(process.env.PORT || 7330);

const server = http.createServer((request, response) => {
  compress(request, response, () => handler(request, response));
});
server.listen(port, host, () => console.log(`AnyList frontend listening on ${host}:${port}`));
