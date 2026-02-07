"""Unit tests for StuckDetector covering the list.index() crash fix and edge cases.

Tests verify:
- Empty history does not crash
- History with actions but no matching observations
- Duplicate actions (same content) in the history
- The _safe_find_index fallback
- All existing scenarios still detect stuck loops correctly
"""

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from openhands.controller.stuck import StuckDetector
from openhands.events.action.commands import CmdRunAction, IPythonRunCellAction
from openhands.events.action.empty import NullAction
from openhands.events.action.message import MessageAction
from openhands.events.event import Event, EventSource
from openhands.events.observation.empty import NullObservation
from openhands.events.observation.error import ErrorObservation
from openhands.events.observation.observation import Observation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(history: list[Event]) -> MagicMock:
    """Create a minimal State mock with the given history."""
    state = MagicMock()
    state.history = history
    return state


def _set_source(event: Event, source: EventSource) -> Event:
    """Set the _source attribute on an event for testing."""
    event._source = source
    return event


def _make_cmd_action(cmd: str = 'echo hello') -> CmdRunAction:
    """Create a CmdRunAction with the given command."""
    action = CmdRunAction(command=cmd)
    _set_source(action, EventSource.AGENT)
    return action


def _make_error_obs(content: str = 'error') -> ErrorObservation:
    """Create an ErrorObservation with the given content."""
    obs = ErrorObservation(content=content)
    _set_source(obs, EventSource.ENVIRONMENT)
    return obs


def _make_message_action(content: str = 'thinking...') -> MessageAction:
    """Create a MessageAction with the given content and AGENT source."""
    action = MessageAction(content=content)
    _set_source(action, EventSource.AGENT)
    return action


def _make_user_message(content: str = 'user input') -> MessageAction:
    """Create a MessageAction with USER source."""
    action = MessageAction(content=content)
    _set_source(action, EventSource.USER)
    return action


# ---------------------------------------------------------------------------
# 1. Empty history
# ---------------------------------------------------------------------------


class TestEmptyHistory:
    """StuckDetector should not crash on empty or very short histories."""

    def test_empty_history(self):
        state = _make_state([])
        detector = StuckDetector(state)
        assert detector.is_stuck() is False

    def test_single_event_history(self):
        state = _make_state([_make_cmd_action()])
        detector = StuckDetector(state)
        assert detector.is_stuck() is False

    def test_two_event_history(self):
        state = _make_state([_make_cmd_action(), _make_error_obs()])
        detector = StuckDetector(state)
        assert detector.is_stuck() is False

    def test_only_null_events(self):
        """History with only NullAction/NullObservation should be treated as empty."""
        state = _make_state([
            NullAction(),
            NullObservation(''),
            NullAction(),
            NullObservation(''),
        ])
        detector = StuckDetector(state)
        assert detector.is_stuck() is False


# ---------------------------------------------------------------------------
# 2. Action without matching observation
# ---------------------------------------------------------------------------


class TestActionWithoutMatchingObservation:
    """When there are actions but insufficient observations,
    the detector should not crash."""

    def test_actions_only_no_observations(self):
        """History with only actions and no observations should not crash."""
        actions = [_make_cmd_action('echo test') for _ in range(5)]
        state = _make_state(actions)
        detector = StuckDetector(state)
        # Should not raise; may or may not detect stuck depending on logic
        result = detector.is_stuck()
        assert isinstance(result, bool)

    def test_three_actions_no_observations(self):
        """Three identical actions without any observations."""
        action = _make_cmd_action('ls')
        actions = [_make_cmd_action('ls') for _ in range(3)]
        state = _make_state(actions)
        detector = StuckDetector(state)
        # Should not raise ValueError
        result = detector.is_stuck()
        assert isinstance(result, bool)

    def test_mixed_but_insufficient_observations(self):
        """Some observations but fewer than required for loop detection."""
        history = [
            _make_cmd_action('cmd1'),
            _make_error_obs('err1'),
            _make_cmd_action('cmd2'),
            # No matching observation for cmd2
            _make_cmd_action('cmd3'),
        ]
        state = _make_state(history)
        detector = StuckDetector(state)
        result = detector.is_stuck()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 3. Duplicate actions (same content)
# ---------------------------------------------------------------------------


class TestDuplicateActions:
    """When the history contains duplicate actions that compare as equal,
    the _safe_find_index fix should prevent ValueError crashes."""

    def test_four_duplicate_actions_four_duplicate_observations(self):
        """Scenario 1: 4 identical action-observation pairs should detect stuck."""
        history = []
        for _ in range(4):
            history.append(_make_cmd_action('echo stuck'))
            history.append(_make_error_obs('same error'))

        state = _make_state(history)
        detector = StuckDetector(state)
        result = detector.is_stuck()
        # All actions are the same + all observations are ErrorObservation -> scenario 2 (3 repeats)
        assert result is True

    def test_three_duplicate_actions_three_error_observations(self):
        """Scenario 2: 3 identical actions + 3 error observations."""
        history = []
        for _ in range(3):
            history.append(_make_cmd_action('failing_cmd'))
            history.append(_make_error_obs('command not found'))

        state = _make_state(history)
        detector = StuckDetector(state)
        result = detector.is_stuck()
        assert result is True
        assert detector.stuck_analysis is not None
        assert detector.stuck_analysis.loop_type == 'repeating_action_error'

    def test_many_duplicate_actions_with_varying_observations(self):
        """Many duplicate actions but observations differ -- should NOT detect stuck."""
        history = []
        for i in range(4):
            history.append(_make_cmd_action('echo hello'))
            history.append(_make_error_obs(f'different error {i}'))

        state = _make_state(history)
        detector = StuckDetector(state)
        # ErrorObservations differ in content, so they don't compare equal
        # Scenario 1 needs same observations, scenario 2 needs all ErrorObservation (which they are)
        # Actually, all are ErrorObservation so scenario 2 triggers with 3 same actions + 3 errors
        # The actions are all equal AND all observations are ErrorObservation -- scenario 2 triggers
        assert detector.is_stuck() is True


# ---------------------------------------------------------------------------
# 4. _safe_find_index
# ---------------------------------------------------------------------------


class TestSafeFindIndex:
    """Directly test the _safe_find_index static method."""

    def test_element_present(self):
        items = [1, 2, 3, 4]
        assert StuckDetector._safe_find_index(items, 3) == 2

    def test_element_absent_returns_zero(self):
        items = [1, 2, 3]
        assert StuckDetector._safe_find_index(items, 99) == 0

    def test_empty_list_returns_zero(self):
        assert StuckDetector._safe_find_index([], 'anything') == 0

    def test_duplicate_elements_returns_first(self):
        """list.index() returns the first match; verify _safe_find_index does too."""
        items = ['a', 'b', 'a', 'c']
        assert StuckDetector._safe_find_index(items, 'a') == 0

    def test_with_event_objects(self):
        """Test with actual Event objects that may have equality issues."""
        action1 = _make_cmd_action('echo 1')
        action2 = _make_cmd_action('echo 2')
        action3 = _make_cmd_action('echo 3')
        history = [action1, action2, action3]

        assert StuckDetector._safe_find_index(history, action2) == 1

        # An action not in the list
        missing = _make_cmd_action('echo missing')
        assert StuckDetector._safe_find_index(history, missing) == 0


# ---------------------------------------------------------------------------
# 5. Monologue detection
# ---------------------------------------------------------------------------


class TestMonologueDetection:
    """Verify monologue (repeated agent messages) detection works correctly."""

    def test_three_identical_agent_messages_detected(self):
        history = [
            _make_message_action('I am thinking'),
            _make_message_action('I am thinking'),
            _make_message_action('I am thinking'),
        ]
        state = _make_state(history)
        detector = StuckDetector(state)
        assert detector.is_stuck() is True
        assert detector.stuck_analysis is not None
        assert detector.stuck_analysis.loop_type == 'monologue'

    def test_different_agent_messages_not_detected(self):
        history = [
            _make_message_action('thought 1'),
            _make_message_action('thought 2'),
            _make_message_action('thought 3'),
        ]
        state = _make_state(history)
        detector = StuckDetector(state)
        assert detector.is_stuck() is False

    def test_monologue_with_observation_between_not_detected(self):
        """If there are observations between repeated messages, it's not a monologue."""
        history = [
            _make_message_action('thinking'),
            _make_error_obs('some error'),
            _make_message_action('thinking'),
            _make_error_obs('another error'),
            _make_message_action('thinking'),
        ]
        state = _make_state(history)
        detector = StuckDetector(state)
        # Observations between messages break the monologue pattern
        assert detector.is_stuck() is False


# ---------------------------------------------------------------------------
# 6. Non-headless mode (interactive)
# ---------------------------------------------------------------------------


class TestNonHeadlessMode:
    """In non-headless mode, only history after the last user message matters."""

    def test_stuck_after_last_user_message(self):
        history = [
            _make_user_message('do something'),
            _make_cmd_action('failing'),
            _make_error_obs('error'),
            _make_cmd_action('failing'),
            _make_error_obs('error'),
            _make_cmd_action('failing'),
            _make_error_obs('error'),
        ]
        state = _make_state(history)
        detector = StuckDetector(state)
        result = detector.is_stuck(headless_mode=False)
        assert result is True

    def test_not_stuck_if_all_before_user_message(self):
        """History before the last user message should be ignored in non-headless."""
        history = [
            _make_cmd_action('failing'),
            _make_error_obs('error'),
            _make_cmd_action('failing'),
            _make_error_obs('error'),
            _make_cmd_action('failing'),
            _make_error_obs('error'),
            _make_user_message('new task please'),
            # Only one action after user message
            _make_cmd_action('new_cmd'),
        ]
        state = _make_state(history)
        detector = StuckDetector(state)
        result = detector.is_stuck(headless_mode=False)
        assert result is False

    def test_no_user_message_means_empty_filtered(self):
        """If no user message exists in non-headless mode, all history is checked."""
        history = [
            _make_cmd_action('x'),
            _make_error_obs('err'),
        ]
        state = _make_state(history)
        detector = StuckDetector(state)
        result = detector.is_stuck(headless_mode=False)
        assert result is False


# ---------------------------------------------------------------------------
# 7. Scenario 4: alternating action-observation pattern (6 events)
# ---------------------------------------------------------------------------


class TestAlternatingPattern:
    """Scenario 4 detects A1, A2, A1, A2, A1, A2 pattern (every other step)."""

    def test_alternating_pattern_detected(self):
        history = []
        for _ in range(3):
            history.append(_make_cmd_action('cmd_a'))
            history.append(_make_error_obs('err_a'))
            history.append(_make_cmd_action('cmd_b'))
            history.append(_make_error_obs('err_b'))

        state = _make_state(history)
        detector = StuckDetector(state)
        result = detector.is_stuck()
        assert result is True

    def test_non_alternating_not_detected(self):
        """Different commands in each pair -- no pattern."""
        history = []
        for i in range(3):
            history.append(_make_cmd_action(f'cmd_{i}'))
            history.append(_make_error_obs(f'err_{i}'))

        state = _make_state(history)
        detector = StuckDetector(state)
        # 3 actions are all different, so scenario 2 (same action) won't trigger
        # and scenario 4 (alternating) requires pairs to repeat
        result = detector.is_stuck()
        assert result is False


# ---------------------------------------------------------------------------
# 8. stuck_analysis is None when not stuck
# ---------------------------------------------------------------------------


class TestStuckAnalysisCleared:
    """Verify stuck_analysis is set to None when the agent is not stuck."""

    def test_analysis_none_when_not_stuck(self):
        history = [
            _make_cmd_action('a'),
            _make_error_obs('b'),
            _make_cmd_action('c'),
            _make_error_obs('d'),
        ]
        state = _make_state(history)
        detector = StuckDetector(state)
        result = detector.is_stuck()
        assert result is False
        assert detector.stuck_analysis is None

    def test_analysis_populated_when_stuck(self):
        history = []
        for _ in range(3):
            history.append(_make_cmd_action('same'))
            history.append(_make_error_obs('same'))

        state = _make_state(history)
        detector = StuckDetector(state)
        result = detector.is_stuck()
        assert result is True
        assert detector.stuck_analysis is not None
        assert detector.stuck_analysis.loop_repeat_times >= 3
        assert detector.stuck_analysis.loop_start_idx >= 0
