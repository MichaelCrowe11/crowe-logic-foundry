// deploy/ide/session-router/proxy.js
const httpProxy = require('http-proxy');

function createProxyServer() {
  const proxy = httpProxy.createProxyServer({
    ws: true,
    changeOrigin: true,
    xfwd: true,
  });

  proxy.on('error', (err, req, res) => {
    console.error(`[proxy] Error: ${err.message}`);
    if (res && res.writeHead) {
      res.writeHead(502, { 'Content-Type': 'text/plain' });
      res.end('IDE container is starting up. Refresh in a few seconds.');
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
