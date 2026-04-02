"""
Crowe-Synapse — Orchestration Framework for Crowe Logic

Pipeline engine, multi-agent coordination, persistent memory,
and pluggable quantum decision-making.
"""

__version__ = "0.1.0"

from crowe_synapse_engine.orchestrator import Orchestrator
from crowe_synapse_engine.pipeline import PipelineEngine, PipelineStep, PipelineRun, PipelineTemplate
from crowe_synapse_engine.agent_registry import AgentRegistry, AgentConfig
from crowe_synapse_engine.memory import MemoryStore
from crowe_synapse_engine.quantum_bridge import QuantumBridge, DecisionPoint
