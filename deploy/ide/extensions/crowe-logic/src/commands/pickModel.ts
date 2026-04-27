/**
 * `Crowe Logic: Pick Model` — opens a QuickPick of curated CroweLM
 * tiers grouped by purpose (Auto, Reasoning, Code, Fast). Sets
 * `croweLogic.model` globally on selection.
 *
 * The list is intentionally curated rather than dumping all 52 chain
 * entries. Power users can still type any model id by hand in
 * settings; this picker is for the 80% case.
 */

import * as vscode from 'vscode';

interface ModelChoice {
    label: string;
    description: string;
    detail: string;
    value: string;
    kind: 'auto' | 'reasoning' | 'code' | 'fast' | 'frontier';
}

// Curated list of CroweLM tiers that are SUPPORTED in headless mode.
// Watsonx-backed labels (Prime, Sovereign, Apex, Reason, etc.) are
// intentionally excluded — they trip "Headless mode does not support
// provider kind 'watsonx'" because no watsonx provider is wired up.
const CHOICES: ModelChoice[] = [
    {
        label: '$(sparkle) Auto',
        description: 'Recommended',
        detail: 'Routes the right tier per task. Crowe Logic picks automatically.',
        value: 'auto',
        kind: 'auto',
    },
    // Frontier
    {
        label: 'CroweLM Supreme',
        description: 'Anthropic · Claude Opus 4.7',
        detail: '1M context. Frontier reasoning, deep analysis, complex tool chains.',
        value: 'CroweLM Supreme',
        kind: 'frontier',
    },
    {
        label: 'CroweLM Sovereign Premium',
        description: 'Anthropic · Claude Opus 4.6.2',
        detail: 'Premium long-form writing, creative + analytical synthesis.',
        value: 'CroweLM Sovereign Premium',
        kind: 'frontier',
    },
    {
        label: 'CroweLM Prime Premium',
        description: 'Anthropic · Claude Opus 4.6',
        detail: 'Premium domain Q&A. Tuned for scientific reasoning.',
        value: 'CroweLM Prime Premium',
        kind: 'frontier',
    },
    // Reasoning
    {
        label: 'CroweLM Frontier',
        description: 'NVIDIA NIM · Mistral Large 3',
        detail: 'Mistral Large 3 675B. Open-weight frontier reasoning.',
        value: 'CroweLM Frontier',
        kind: 'reasoning',
    },
    {
        label: 'CroweLM Titan',
        description: 'NVIDIA NIM · GLM-5.1',
        detail: 'Z.AI GLM-5.1 flagship. General-purpose reasoning.',
        value: 'CroweLM Titan',
        kind: 'reasoning',
    },
    {
        label: 'CroweLM Titan Premium',
        description: 'Azure OpenAI · GPT-5.4',
        detail: 'Azure-managed GPT-5.4. Deep reasoning + tool use.',
        value: 'CroweLM Titan Premium',
        kind: 'reasoning',
    },
    {
        label: 'CroweLM Apex Premium',
        description: 'Azure OpenAI · GPT-5.4 Pro',
        detail: 'Azure-managed GPT-5.4 Pro. Premium reasoning tier.',
        value: 'CroweLM Apex Premium',
        kind: 'reasoning',
    },
    {
        label: 'CroweLM Ultra',
        description: 'NVIDIA NIM · Nemotron Ultra 253B',
        detail: 'Llama 3.1 Nemotron Ultra 253B. Long-form research synthesis.',
        value: 'CroweLM Ultra',
        kind: 'reasoning',
    },
    {
        label: 'CroweLM Depth',
        description: 'NVIDIA NIM · DeepSeek V3',
        detail: 'DeepSeek V3.2. Strong technical reasoning.',
        value: 'CroweLM Depth',
        kind: 'reasoning',
    },
    // Code
    {
        label: 'CroweLM Coder',
        description: 'NVIDIA NIM · Qwen 3 Coder 480B',
        detail: 'Code generation + refactoring specialist.',
        value: 'CroweLM Coder',
        kind: 'code',
    },
    {
        label: 'CroweLM Maverick',
        description: 'NVIDIA NIM · Llama 4 Maverick',
        detail: 'Tool-discipline-tuned. Best for agentic flows.',
        value: 'CroweLM Maverick',
        kind: 'code',
    },
    {
        label: 'CroweLM Forge',
        description: 'NVIDIA NIM · Llama 3.3 70B',
        detail: 'Balanced code + chat tier.',
        value: 'CroweLM Forge',
        kind: 'code',
    },
    // Fast
    {
        label: 'CroweLM Talon',
        description: 'OpenRouter · curated',
        detail: 'Default fast tier — first chain entry, lowest latency.',
        value: 'CroweLM Talon',
        kind: 'fast',
    },
    {
        label: 'CroweLM Nexus',
        description: 'OpenRouter · Kimi K2.5',
        detail: 'Low latency, low cost. Quick turns.',
        value: 'CroweLM Nexus',
        kind: 'fast',
    },
    {
        label: 'CroweLM Pulse',
        description: 'NVIDIA NIM · Kimi Thinking',
        detail: 'Mid-latency reasoning at chat-tier cost.',
        value: 'CroweLM Pulse',
        kind: 'fast',
    },
];

export async function pickModel(): Promise<void> {
    const cfg = vscode.workspace.getConfiguration('croweLogic');
    const current = cfg.get<string>('model') || 'auto';

    const items: vscode.QuickPickItem[] = [];
    let lastKind: string | null = null;
    for (const c of CHOICES) {
        if (c.kind !== lastKind) {
            const labels: Record<typeof c.kind, string> = {
                auto: 'Auto-routing',
                frontier: 'Frontier',
                reasoning: 'Reasoning',
                code: 'Code',
                fast: 'Fast & low-cost',
            };
            items.push({
                label: labels[c.kind] ?? c.kind,
                kind: vscode.QuickPickItemKind.Separator,
            });
            lastKind = c.kind;
        }
        items.push({
            label: c.label + (c.value === current ? '  $(check)' : ''),
            description: c.description,
            detail: c.detail,
        });
    }

    const picked = await vscode.window.showQuickPick(items, {
        placeHolder: `Current model: ${current}. Pick a CroweLM tier.`,
        matchOnDescription: true,
        matchOnDetail: true,
    });
    if (!picked) return;

    // Find which choice matches the picked label (strip $(check) suffix)
    const cleanLabel = picked.label.replace(/\s*\$\(check\)\s*$/, '');
    const match = CHOICES.find((c) => c.label === cleanLabel);
    if (!match) return;

    await cfg.update('model', match.value, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage(
        `Crowe Logic: now using ${match.value}.`,
    );
}
