def thread_id_of(message) -> int | None:
    """§5: thread_id берём ТОЛЬКО у топик-сообщений; id reply-треда не сохраняем."""
    if getattr(message, "is_topic_message", False):
        return message.message_thread_id
    return None


def chat_title_of(message) -> str | None:
    chat = getattr(message, "chat", None)
    return getattr(chat, "title", None)
