// deploy/ide/session-router/proxy.js
const httpProxy = require('http-proxy');

function createProxyServer() {
  // changeOrigin is INTENTIONALLY false. With changeOrigin:true, http-proxy
  // rewrites the outgoing Host header to the target (e.g. 127.0.0.1:10001).
  // code-server's WebSocket upgrade handler enforces Origin == Host as a CSRF
  // defense, so when the browser sends Origin: https://ide.southwestmushrooms.com
  // and the proxy sends Host: 127.0.0.1:10001, the upgrade is rejected with
  // HTTP 403 and the workbench fails with "WebSocket close code 1006".
  // Preserving the original Host header keeps Origin == Host and lets the
  // upgrade through. xfwd:true still sets X-Forwarded-* headers correctly.
  const proxy = httpProxy.createProxyServer({
    ws: true,
    changeOrigin: false,
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
