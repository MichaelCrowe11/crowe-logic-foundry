"""
Crowe-Synapse Orchestrator — the central coordinator.

Routes tasks, manages sessions, dispatches pipelines and agents,
injects context from memory into the model's system prompt.
"""

from crowe_synapse_engine.memory import MemoryStore
from crowe_synapse_engine.pipeline import PipelineEngine
from crowe_synapse_engine.agent_registry import AgentRegistry
from crowe_synapse_engine.quantum_bridge import QuantumBridge


class Orchestrator:
    def __init__(self, db_path: str = "~/.crowe-logic/memory.db",
                 agents_dir: str = "", templates_dir: str = ""):
        self.memory = MemoryStore(db_path=db_path)
        self.pipeline_engine = PipelineEngine(templates_dir=templates_dir)
        self.agent_registry = AgentRegistry(agents_dir=agents_dir)
        self.quantum = QuantumBridge()
        self._current_session_id: str | None = None

    # -- Session Lifecycle --

    def start_session(self, thread_id: str) -> str:
        context = self._build_project_context()
        self._current_session_id = self.memory.start_session(
            thread_id=thread_id, project_context=context
        )
        return self._current_session_id

    def end_session(self, summary: str = ""):
        if self._current_session_id:
            self.memory.end_session(self._current_session_id, summary=summary)
            self._current_session_id = None

    def get_history(self, limit: int = 10) -> list[dict]:
        return self.memory.get_recent_sessions(limit=limit)

    # -- Pre-Message Preparation --

    def prepare(self, user_input: str, thread_id: str) -> dict:
        """Analyze user input and prepare execution context."""
        # Check for pipeline template match
        template = self.pipeline_engine.match_template(user_input)
        if template:
            return {
                "mode": "pipeline",
                "pipeline_name": template.name,
                "template": template,
                "injection": self._build_context_injection(),
            }

        # Check for agent delegation (future: quantum-enhanced routing)
        agent = self._route_to_agent(user_input)
        if agent:
            return {
                "mode": "delegated",
                "agent_name": agent.name,
                "agent": agent,
                "pipeline_name": None,
                "injection": self._build_context_injection(),
            }

        # Default: direct execution
        return {
            "mode": "direct",
            "pipeline_name": None,
            "injection": self._build_context_injection(),
        }

    # -- Post-Execution Recording --

    def record_execution(self, tool_name: str, arguments: str = "",
                         output: str = "", duration_ms: int = 0,
                         status: str = "success"):
        self.memory.record_tool_execution(
            session_id=self._current_session_id,
            tool_name=tool_name,
            arguments=arguments,
            output=output,
            duration_ms=duration_ms,
            status=status,
        )

    # -- Listing --

    def list_agents(self):
        return self.agent_registry.list_agents()

    def list_pipelines(self):
        return self.pipeline_engine.list_templates()

    # -- Internal --

    def _route_to_agent(self, user_input: str):
        """Simple keyword-based agent routing. Returns AgentConfig or None."""
        text = user_input.lower()
        agents = self.agent_registry.list_agents()
        if not agents:
            return None

        keyword_map = {
            "music": ["compose", "music", "melody", "chord", "drum", "talon", "midi", "song", "track"],
            "quantum": ["quantum", "qubit", "circuit", "synapse", "superposition", "entangle"],
            "cultivation": ["mushroom", "substrate", "mycelium", "fruiting", "spawn", "cultivation", "growing"],
            "research": ["research", "look up", "find out", "search for", "investigate"],
            "code": ["refactor", "debug", "function", "class", "import", "compile", "test"],
        }

        best_agent = None
        best_score = 0
        for agent_name, keywords in keyword_map.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                best_agent = agent_name

        if best_score == 0:
            return None

        return self.agent_registry.get_agent(best_agent)

    def _build_context_injection(self) -> str:
        """Build context string to inject into system prompt from memory."""
        parts = []

        # Recent session summaries
        sessions = self.memory.get_recent_sessions(limit=3)
        for s in sessions:
            if s.get("summary") and s["id"] != self._current_session_id:
                parts.append(f"Previous session: {s['summary']}")

        # Project knowledge
        knowledge = self.memory.get_all_knowledge()
        for k in knowledge[:10]:
            parts.append(f"{k['key']}: {k['value']}")

        return "\n".join(parts) if parts else ""

    def _build_project_context(self) -> str:
        """Build initial project context for session start."""
        knowledge = self.memory.get_all_knowledge()
        if knowledge:
            return "; ".join(f"{k['key']}={k['value']}" for k in knowledge[:5])
        return ""
