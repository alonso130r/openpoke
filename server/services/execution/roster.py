"""Persist execution agent names and their optional routing summaries."""

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator, TypedDict

from ...logging_config import logger


class AgentRecord(TypedDict):
    name: str
    summary: str | None


class AgentRoster:
    """Store agent records while supporting existing name-based callers."""

    def __init__(self, roster_path: Path):
        self._roster_path = roster_path
        self._lock_path = roster_path.with_suffix(roster_path.suffix + ".lock")
        self._agents: dict[str, str | None] = {}
        self.load()

    def _read(self) -> dict[str, str | None]:
        try:
            with self._roster_path.open(encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            return {}

        if not isinstance(data, list):
            raise ValueError("Agent roster must be a list")

        agents: dict[str, str | None] = {}
        for entry in data:
            if isinstance(entry, str):
                agents[entry] = None
            elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
                summary = entry.get("summary")
                agents[entry["name"]] = summary if isinstance(summary, str) else None
            else:
                raise ValueError("Invalid agent roster entry")
        return agents

    def load(self) -> None:
        """Refresh the snapshot from disk."""
        try:
            self._agents = self._read()
        except (OSError, ValueError) as exc:
            logger.warning("Failed to load agent roster: %s", exc)
            self._agents = {}

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._roster_path.parent.mkdir(parents=True, exist_ok=True)
        # Lock a separate file so the lock survives replacement of the roster.
        with self._lock_path.open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def _write(self, agents: dict[str, str | None]) -> None:
        records = [{"name": name, "summary": summary} for name, summary in agents.items()]
        file = NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self._roster_path.parent, delete=False
        )
        temp_path = Path(file.name)
        try:
            with file:
                json.dump(records, file, indent=2)
            temp_path.replace(self._roster_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def add_agent(self, agent_name: str) -> bool:
        """Claim a new name and return whether it was inserted."""
        with self._locked():
            agents = self._read()
            if agent_name in agents:
                self._agents = agents
                return False
            agents[agent_name] = None
            self._write(agents)
            self._agents = agents
            return True

    def update_summary(self, agent_name: str, summary: str) -> bool:
        """Update a registered agent and return whether it exists."""
        with self._locked():
            agents = self._read()
            if agent_name not in agents:
                self._agents = agents
                return False
            agents[agent_name] = summary
            self._write(agents)
            self._agents = agents
            return True

    def get_agents(self) -> list[str]:
        """Return the names in the current snapshot."""
        return list(self._agents)

    def get_records(self) -> list[AgentRecord]:
        """Return a copy of the current roster records."""
        return [{"name": name, "summary": summary} for name, summary in self._agents.items()]

    def clear(self) -> None:
        """Remove all registered agents and their summaries."""
        with self._locked():
            self._roster_path.unlink(missing_ok=True)
            self._agents = {}


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_ROSTER_PATH = _DATA_DIR / "execution_agents" / "roster.json"

_agent_roster = AgentRoster(_ROSTER_PATH)


def get_agent_roster() -> AgentRoster:
    """Get the singleton roster instance."""
    return _agent_roster
