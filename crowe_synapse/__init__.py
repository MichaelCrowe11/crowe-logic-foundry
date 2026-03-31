"""
Crowe-Synapse — Orchestration Framework for Crowe Logic

Pipeline engine, multi-agent coordination, persistent memory,
and pluggable quantum decision-making.
"""

__version__ = "0.1.0"

from crowe_synapse.orchestrator import Orchestrator
from crowe_synapse.pipeline import PipelineEngine, PipelineStep, PipelineRun, PipelineTemplate
from crowe_synapse.agent_registry import AgentRegistry, AgentConfig
from crowe_synapse.memory import MemoryStore
from crowe_synapse.quantum_bridge import QuantumBridge, DecisionPoint
