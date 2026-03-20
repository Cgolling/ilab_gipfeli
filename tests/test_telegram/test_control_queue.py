"""Tests for the in-memory control queue."""

from src.telegram.control_queue import ControlQueue


def test_first_joiner_gets_control():
    queue = ControlQueue()

    position, added = queue.join(user_id=1, chat_id=10, display_name="User One")

    assert position == 1
    assert added is True
    assert queue.status_message_for(1) == "You are in control."


def test_second_joiner_gets_queue_position():
    queue = ControlQueue()
    queue.join(user_id=1, chat_id=10, display_name="User One")

    position, added = queue.join(user_id=2, chat_id=20, display_name="User Two")

    assert position == 2
    assert added is True
    assert queue.status_message_for(2) == "You are 2. in the queue."


def test_leaving_controller_promotes_next_user():
    queue = ControlQueue()
    queue.join(user_id=1, chat_id=10, display_name="User One")
    queue.join(user_id=2, chat_id=20, display_name="User Two")

    _, had_control = queue.leave(1)

    assert had_control is True
    assert queue.status_message_for(2) == "You are in control."


def test_joining_twice_does_not_duplicate_user():
    queue = ControlQueue()
    queue.join(user_id=1, chat_id=10, display_name="User One")

    position, added = queue.join(user_id=1, chat_id=10, display_name="User One")

    assert position == 1
    assert added is False
    assert len(queue.entries) == 1
