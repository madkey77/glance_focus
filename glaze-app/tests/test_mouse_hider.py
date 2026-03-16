import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch, call
import pytest


def _make_fast_timer(delay, fn):
    """threading.Timer replacement that fires in 0.05s regardless of delay."""
    return threading.Timer(0.05, fn)


def _make_hider(**kwargs):
    """Create MouseHider with a no-op listener factory and fast timer factory."""
    from focus_controller import MouseHider
    fake_listener = MagicMock()
    fake_listener_instance = MagicMock()
    fake_listener.return_value = fake_listener_instance
    return MouseHider(
        _listener_factory=fake_listener,
        _timer_factory=_make_fast_timer,
        **kwargs
    ), fake_listener


def _mock_windll():
    """Return a context manager that mocks ctypes.windll (create=True for Linux)."""
    return patch('ctypes.windll', create=True)


def test_hide_called_after_delay():
    """Timer fires → ShowCursor(False) called once, _hidden becomes True."""
    hider, _ = _make_hider()
    fired = threading.Event()
    original_hide = hider._hide

    def patched_hide():
        original_hide()
        fired.set()

    hider._hide = patched_hide

    with _mock_windll() as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider.set_enabled(True)
        hider._reset_timer()  # manually trigger timer with near-zero delay
        # wait for timer
        assert fired.wait(timeout=3.0), "Timer never fired"
        mock_user32.ShowCursor.assert_called_once_with(False)
        hider.stop()


def test_no_double_hide():
    """Calling _hide() twice only calls ShowCursor(False) once."""
    hider, _ = _make_hider()
    with _mock_windll() as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()
        hider._hide()
        mock_user32.ShowCursor.assert_called_once_with(False)
        hider.stop()


def test_no_double_show():
    """Calling _show() twice only calls ShowCursor(True) once."""
    hider, _ = _make_hider()
    with _mock_windll() as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()   # first hide so _hidden=True
        hider._show()
        hider._show()   # second show — should be no-op
        # ShowCursor(True) called once
        true_calls = [c for c in mock_user32.ShowCursor.call_args_list if c == call(True)]
        assert len(true_calls) == 1
    hider.stop()


def test_show_on_move_teleports_cursor():
    """Movement: SetCursorPos called BEFORE ShowCursor(True)."""
    hider, _ = _make_hider()
    hider.set_gaze_pos(800, 400)
    call_order = []

    with _mock_windll() as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        mock_user32.SetCursorPos.side_effect = lambda x, y: call_order.append('set')
        mock_user32.ShowCursor.side_effect = lambda v: call_order.append(f'show_{v}')

        hider._hide()          # hide first so _hidden=True
        hider._on_move(0, 0)   # simulate mouse movement

        assert call_order == ['show_False', 'set', 'show_True'], \
            f"Expected [show_False, set, show_True], got {call_order}"
        mock_user32.SetCursorPos.assert_called_with(800, 400)
    hider.stop()


def test_gaze_pos_used_on_wake():
    """Cursor teleports to last set_gaze_pos coordinates on wake."""
    hider, _ = _make_hider()
    hider.set_gaze_pos(1200, 600)

    with _mock_windll() as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()
        hider._on_move(0, 0)
        mock_user32.SetCursorPos.assert_called_with(1200, 600)
    hider.stop()


def test_no_teleport_when_no_gaze():
    """If set_gaze_pos was never called, _on_move skips SetCursorPos."""
    hider, _ = _make_hider()
    # _gaze_pos is None — never set

    with _mock_windll() as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()
        hider._on_move(0, 0)
        mock_user32.SetCursorPos.assert_not_called()
        mock_user32.ShowCursor.assert_called_with(True)
    hider.stop()


def test_disabled_prevents_hide():
    """set_enabled(False) cancels timer and restores cursor."""
    hider, _ = _make_hider()

    with _mock_windll() as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()           # simulate hidden state
        hider.set_enabled(False)
        # ShowCursor(True) must be called to restore cursor
        mock_user32.ShowCursor.assert_called_with(True)
    hider.stop()


def test_stop_restores_cursor():
    """stop() calls ShowCursor(True) if cursor is currently hidden."""
    hider, _ = _make_hider()

    with _mock_windll() as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()
        hider.stop()
        true_calls = [c for c in mock_user32.ShowCursor.call_args_list if c == call(True)]
        assert len(true_calls) >= 1
