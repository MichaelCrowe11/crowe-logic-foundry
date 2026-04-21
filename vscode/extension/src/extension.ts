import * as vscode from 'vscode';

const THEME_ID = 'Crowe Logic Dark';
const ICON_THEME_ID = 'crowe-logic-icons';
const FIRST_RUN_KEY = 'croweLogic.firstRunComplete';

const SYSTEM_PROMPT = [
  'You are Crowe Logic, the universal Crowe Logic agent powered by the CroweLM model stack.',
  'You speak with calm, precise, professional tone. Prefer concise answers and concrete code.',
  'Never refer to yourself as Copilot, GitHub Copilot, or an OpenAI/Anthropic model — you are Crowe Logic.',
  'Surface tool calls, model fallbacks, and reasoning steps in the Crowe Logic style (gold-on-graphite).',
].join(' ');

export async function activate(ctx: vscode.ExtensionContext): Promise<void> {
  ctx.subscriptions.push(
    vscode.commands.registerCommand('crowe-logic.applyTheme', applyTheme),
    vscode.commands.registerCommand('crowe-logic.applyProductIcons', applyProductIcons),
    vscode.commands.registerCommand('crowe-logic.applyAll', () => applyAll(ctx)),
    vscode.commands.registerCommand('crowe-logic.showWelcome', () =>
      vscode.commands.executeCommand(
        'workbench.action.openWalkthrough',
        'crowe-logic.crowe-logic-vscode#crowe-logic.getting-started',
        false,
      ),
    ),
  );

  applyTitleBar();
  vscode.workspace.onDidChangeConfiguration((e) => {
    if (e.affectsConfiguration('croweLogic.titleBarText')) applyTitleBar();
  });

  registerChatParticipant(ctx);

  const cfg = vscode.workspace.getConfiguration('croweLogic');
  const firstRun = !ctx.globalState.get<boolean>(FIRST_RUN_KEY, false);
  if (firstRun && cfg.get<boolean>('autoApplyOnFirstRun', true)) {
    await applyAll(ctx);
    await ctx.globalState.update(FIRST_RUN_KEY, true);
    vscode.commands.executeCommand('crowe-logic.showWelcome');
  }
}

export function deactivate(): void {}

async function applyTheme(): Promise<void> {
  await vscode.workspace.getConfiguration().update('workbench.colorTheme', THEME_ID, vscode.ConfigurationTarget.Global);
  vscode.window.showInformationMessage(`Crowe Logic theme applied (${THEME_ID}).`);
}

async function applyProductIcons(): Promise<void> {
  await vscode.workspace
    .getConfiguration()
    .update('workbench.productIconTheme', ICON_THEME_ID, vscode.ConfigurationTarget.Global);
  vscode.window.showInformationMessage('Crowe Logic product icons applied.');
}

async function applyAll(_ctx: vscode.ExtensionContext): Promise<void> {
  await applyTheme();
  await applyProductIcons();
  applyTitleBar();
}

function applyTitleBar(): void {
  const cfg = vscode.workspace.getConfiguration();
  const desired = vscode.workspace
    .getConfiguration('croweLogic')
    .get<string>('titleBarText', 'Crowe Logic — ${rootName}${separator}${activeEditorShort}');
  cfg.update('window.title', desired, vscode.ConfigurationTarget.Global);
}

function registerChatParticipant(ctx: vscode.ExtensionContext): void {
  const chatApi = (vscode as any).chat;
  if (!chatApi || typeof chatApi.createChatParticipant !== 'function') {
    return;
  }
  const participant = chatApi.createChatParticipant(
    'crowe-logic.agent',
    async (
      request: any,
      _ctxReq: any,
      stream: any,
      token: vscode.CancellationToken,
    ): Promise<any> => {
      const models = await (vscode as any).lm?.selectChatModels?.({ vendor: 'copilot' });
      if (!models || models.length === 0) {
        stream.markdown(
          '**Crowe Logic** is online but no language model is currently available in this VS Code instance.',
        );
        return {};
      }
      const model = models[0];
      const messages = [
        (vscode as any).LanguageModelChatMessage.User(SYSTEM_PROMPT),
        (vscode as any).LanguageModelChatMessage.User(request.prompt),
      ];
      const response = await model.sendRequest(messages, {}, token);
      for await (const fragment of response.text) {
        stream.markdown(fragment);
      }
      return {};
    },
  );
  participant.iconPath = vscode.Uri.joinPath(ctx.extensionUri, 'media', 'crowe-logic-mark.png');
  ctx.subscriptions.push(participant);
}
