from types import SimpleNamespace
from src.telegram.capture import thread_id_of, chat_title_of


def _msg(thread_id, is_topic, title="Кыргызстан"):
    return SimpleNamespace(
        message_thread_id=thread_id,
        is_topic_message=is_topic,
        chat=SimpleNamespace(title=title),
    )


def test_topic_message_captures_thread_id():
    assert thread_id_of(_msg(77, True)) == 77


def test_general_topic_yields_none():
    assert thread_id_of(_msg(None, None)) is None


def test_reply_thread_id_is_not_captured():
    # reply в General: message_thread_id есть, но is_topic_message не True (§5)
    assert thread_id_of(_msg(4242, False)) is None


def test_chat_title_used_as_country():
    assert chat_title_of(_msg(None, None)) == "Кыргызстан"
