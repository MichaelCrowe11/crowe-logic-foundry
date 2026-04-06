// deploy/ide/session-router/proxy.js
const httpProxy = require('http-proxy');

function createProxyServer() {
  const proxy = httpProxy.createProxyServer({
    ws: true,
    changeOrigin: true,
    xfwd: true,
  });

  proxy.on('error', (err, _req, resOrSocket) => {
    console.error(`[proxy] Error: ${err.message}`);
    if (resOrSocket && typeof resOrSocket.writeHead === 'function') {
      // HTTP response path
      try {
        resOrSocket.writeHead(502, { 'Content-Type': 'text/plain' });
        resOrSocket.end('IDE container is starting up. Refresh in a few seconds.');
      } catch (_) { /* already sent */ }
    } else if (resOrSocket && typeof resOrSocket.destroy === 'function') {
      // WebSocket upgrade error path — destroy the socket to prevent FD leak
      try { resOrSocket.destroy(); } catch (_) { /* already destroyed */ }
    }
  });

  function proxyRequest(req, res, port) {
    proxy.web(req, res, { target: `http://127.0.0.1:${port}` });
  }

  function proxyWebSocket(req, socket, head, port) {
    proxy.ws(req, socket, head, { target: `http://127.0.0.1:${port}` });
  }

  return { proxyRequest, proxyWebSocket };
}

module.exports = { createProxyServer };
