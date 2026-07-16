import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "device_app"))

from run_state import RunStateMachine, RunState, IllegalTransition


def test_starts_idle():
    assert RunStateMachine().state == RunState.IDLE


def test_happy_path():
    fsm = RunStateMachine()
    fsm.to(RunState.RUNNING)
    fsm.to(RunState.COMPLETED)
    fsm.to(RunState.IDLE)
    assert fsm.state == RunState.IDLE


def test_abort_path():
    fsm = RunStateMachine()
    fsm.to(RunState.RUNNING)
    fsm.to(RunState.FAILED)
    assert fsm.state == RunState.FAILED


def test_cannot_complete_from_idle():
    fsm = RunStateMachine()
    with pytest.raises(IllegalTransition, match="idle -> completed"):
        fsm.to(RunState.COMPLETED)


def test_cannot_start_twice():
    fsm = RunStateMachine()
    fsm.to(RunState.RUNNING)
    with pytest.raises(IllegalTransition):
        fsm.to(RunState.RUNNING)


def test_cannot_skip_reset():
    fsm = RunStateMachine()
    fsm.to(RunState.RUNNING)
    fsm.to(RunState.COMPLETED)
    with pytest.raises(IllegalTransition, match="completed -> running"):
        fsm.to(RunState.RUNNING)      # must reset to idle first


def test_failed_state_survives_illegal_attempt():
    fsm = RunStateMachine()
    fsm.to(RunState.RUNNING)
    fsm.to(RunState.FAILED)
    try:
        fsm.to(RunState.COMPLETED)
    except IllegalTransition:
        pass
    assert fsm.state == RunState.FAILED    # state unchanged after rejection


def test_history_is_recorded():
    fsm = RunStateMachine()
    fsm.to(RunState.RUNNING)
    fsm.to(RunState.COMPLETED)
    assert fsm.history == [RunState.IDLE, RunState.RUNNING, RunState.COMPLETED]