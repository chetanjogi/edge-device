from enum import Enum


class RunState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# The whole lifecycle, declared in one place.
ALLOWED = {
    RunState.IDLE:      {RunState.RUNNING},
    RunState.RUNNING:   {RunState.COMPLETED, RunState.FAILED},
    RunState.COMPLETED: {RunState.IDLE},
    RunState.FAILED:    {RunState.IDLE},
}


class IllegalTransition(Exception):
    """Attempted a transition the lifecycle does not permit."""


class RunStateMachine:
    def __init__(self):
        self._state = RunState.IDLE
        self.history = [RunState.IDLE]

    @property
    def state(self) -> RunState:
        return self._state

    def can(self, new: RunState) -> bool:
        return new in ALLOWED[self._state]

    def to(self, new: RunState) -> RunState:
        if not self.can(new):
            raise IllegalTransition(
                f"illegal transition {self._state.value} -> {new.value}")
        self._state = new
        self.history.append(new)
        return new