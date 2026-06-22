/**
 * BrowserViewProvider — Wave-style in-panel browser for the Crowe Logic
 * IDE extension. Provides:
 *   - URL bar (input + Go button)
 *   - Clickable port chips populated by PortScanner
 *   - <iframe> preview that uses vscode.env.asExternalUri so localhost
 *     resolves through the pod's /proxy/<port>/ path (same-origin, no
 *     raw localhost iframe hitting the user's machine)
 *   - "open externally ↗" affordance
 *
 * Theme: gold var(--clm-gold,#bfa669) on #0a0a0c, monospace, pill chips.
 */

import * as vscode from 'vscode';

export class BrowserViewProvider implements vscode.WebviewViewProvider {
    public static readonly VIEW_ID = 'crowe-logic.browser';

    private _view: vscode.WebviewView | undefined;

    constructor(private readonly extensionUri: vscode.Uri) {}

    resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken,
    ): void {
        this._view = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this.extensionUri],
        };

        webviewView.webview.html = this._buildHtml();

        webviewView.webview.onDidReceiveMessage(async (msg: { type: string; url?: string; port?: number }) => {
            switch (msg.type) {
                case 'navigate': {
                    if (msg.url) {
                        webviewView.webview.postMessage({ type: 'setUrl', url: msg.url });
                    }
                    break;
                }
                case 'openPort': {
                    if (typeof msg.port === 'number') {
                        await this.previewPort(msg.port);
                    }
                    break;
                }
                case 'openExternal': {
                    if (msg.url) {
                        await vscode.env.openExternal(vscode.Uri.parse(msg.url));
                    }
                    break;
                }
            }
        });
    }

    setPorts(ports: number[]): void {
        this._view?.webview.postMessage({ type: 'setPorts', ports });
    }

    async previewPort(port: number): Promise<void> {
        const raw = vscode.Uri.parse(`http://localhost:${port}`);
        const resolved = await vscode.env.asExternalUri(raw);
        const url = resolved.toString(true);
        this._view?.webview.postMessage({ type: 'setUrl', url });
        this._view?.show?.(true);
    }

    focus(): void {
        this._view?.show?.(true);
    }

    refresh(): void {
        this._view?.webview.postMessage({ type: 'reload' });
    }

    private _buildHtml(): string {
        const nonce = Math.random().toString(36).slice(2);
        return /* html */`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; frame-src *; img-src * data:; connect-src *;">
<style>
  :root {
    --clm-gold: #bfa669;
    --clm-gold-deep: #9c8451;
    --clm-bg: #0a0a0c;
    --clm-panel: #111113;
    --clm-line: #222224;
    --clm-muted: #706860;
    --clm-text: #e4dfc8;
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; background: var(--clm-bg); color: var(--clm-text); overflow: hidden; }
  #root { display: flex; flex-direction: column; height: 100%; }

  /* URL bar */
  #urlbar {
    display: flex; align-items: center; gap: 6px;
    padding: 6px 8px;
    background: var(--clm-panel);
    border-bottom: 1px solid var(--clm-line);
    flex-shrink: 0;
  }
  #urlbar input {
    flex: 1; background: var(--clm-bg); color: var(--clm-text);
    border: 1px solid var(--clm-line); border-radius: 5px;
    padding: 4px 8px; font: inherit; font-size: 11.5px; outline: none;
  }
  #urlbar input:focus { border-color: var(--clm-gold); }
  #urlbar button {
    background: transparent; border: 1px solid var(--clm-line);
    color: var(--clm-muted); border-radius: 5px;
    padding: 4px 9px; cursor: pointer; font: inherit; font-size: 11px;
    transition: color 120ms, border-color 120ms;
  }
  #urlbar button:hover { color: var(--clm-gold); border-color: var(--clm-gold-deep); }

  /* Port chip row */
  #portbar {
    display: flex; flex-wrap: wrap; gap: 5px; align-items: center;
    padding: 5px 8px;
    background: var(--clm-panel);
    border-bottom: 1px solid var(--clm-line);
    min-height: 32px;
    flex-shrink: 0;
  }
  #portbar .label {
    font-size: 9.5px; color: var(--clm-muted); text-transform: uppercase;
    letter-spacing: 0.10em; font-weight: 600; margin-right: 2px;
  }
  #portbar .chip {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 2px 10px; border-radius: 999px;
    background: rgba(191,166,105,0.07);
    border: 1px solid rgba(191,166,105,0.22);
    color: var(--clm-gold); cursor: pointer; font-size: 11px;
    transition: background 120ms, border-color 120ms;
  }
  #portbar .chip:hover { background: rgba(191,166,105,0.18); border-color: var(--clm-gold-deep); }
  #portbar .chip .dot {
    width: 5px; height: 5px; border-radius: 50%;
    background: var(--clm-gold);
  }
  #portbar .empty-hint {
    font-size: 10.5px; color: var(--clm-muted); font-style: italic;
  }

  /* iframe area */
  #frame-wrap {
    flex: 1; position: relative; overflow: hidden;
    background: #fff;
  }
  iframe {
    width: 100%; height: 100%; border: none; display: block;
  }
  #frame-placeholder {
    position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 10px;
    background: var(--clm-bg); color: var(--clm-muted);
    font-size: 12px;
  }
  #frame-placeholder .mark {
    font-size: 26px; color: var(--clm-gold); opacity: 0.55;
  }
  #frame-placeholder.hidden { display: none; }
</style>
</head>
<body>
<div id="root">
  <div id="urlbar">
    <input id="url-input" type="text" placeholder="http://localhost:3000" spellcheck="false" />
    <button id="go-btn">Go</button>
    <button id="ext-btn" title="Open externally ↗">↗</button>
  </div>
  <div id="portbar">
    <span class="label">Ports</span>
    <span class="empty-hint" id="empty-hint">none detected</span>
  </div>
  <div id="frame-wrap">
    <div id="frame-placeholder">
      <div class="mark">◈</div>
      <div>Enter a URL above or click a port chip to preview</div>
    </div>
    <iframe id="preview" src="about:blank" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>
  </div>
</div>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const urlInput = document.getElementById('url-input');
  const goBtn = document.getElementById('go-btn');
  const extBtn = document.getElementById('ext-btn');
  const portbar = document.getElementById('portbar');
  const emptyHint = document.getElementById('empty-hint');
  const iframe = document.getElementById('preview');
  const placeholder = document.getElementById('frame-placeholder');
  const label = portbar.querySelector('.label');

  let currentUrl = '';

  function navigate(url) {
    if (!url) return;
    currentUrl = url;
    urlInput.value = url;
    iframe.src = url;
    placeholder.classList.add('hidden');
  }

  goBtn.addEventListener('click', () => {
    let url = urlInput.value.trim();
    if (!url) return;
    if (!/^https?:\/\//i.test(url)) url = 'http://' + url;
    navigate(url);
  });

  urlInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') goBtn.click();
  });

  extBtn.addEventListener('click', () => {
    const url = urlInput.value.trim() || currentUrl;
    if (url) vscode.postMessage({ type: 'openExternal', url });
  });

  function renderChips(ports) {
    // Remove existing chips
    portbar.querySelectorAll('.chip').forEach(c => c.remove());
    if (ports.length === 0) {
      emptyHint.style.display = '';
    } else {
      emptyHint.style.display = 'none';
      for (const port of ports) {
        const chip = document.createElement('button');
        chip.className = 'chip';
        chip.innerHTML = '<span class="dot"></span>' + port;
        chip.title = 'Preview port ' + port;
        chip.addEventListener('click', () => {
          vscode.postMessage({ type: 'openPort', port });
        });
        portbar.appendChild(chip);
      }
    }
  }

  window.addEventListener('message', (event) => {
    const msg = event.data;
    switch (msg.type) {
      case 'setPorts':
        renderChips(msg.ports || []);
        break;
      case 'setUrl':
        navigate(msg.url);
        break;
      case 'reload':
        if (currentUrl) iframe.src = currentUrl;
        break;
    }
  });
</script>
</body>
</html>`;
    }
}
