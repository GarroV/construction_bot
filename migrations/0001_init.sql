CREATE TABLE chats (
    id                 BIGSERIAL PRIMARY KEY,
    country            TEXT,
    telegram_chat_id   BIGINT NOT NULL,
    message_thread_id  BIGINT,
    digest_language    TEXT NOT NULL,
    digest_time        TIME NOT NULL DEFAULT '09:00',
    timezone           TEXT NOT NULL DEFAULT 'UTC',
    last_digest_date   DATE,
    last_posted_at     TIMESTAMPTZ,
    last_ping_at       TIMESTAMPTZ,
    restricted         BOOLEAN NOT NULL DEFAULT FALSE,
    active             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (telegram_chat_id, message_thread_id)
);

CREATE TABLE cards (
    id              BIGSERIAL PRIMARY KEY,
    bitrix_task_id  BIGINT NOT NULL,
    chat_id         BIGINT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    alias           TEXT,
    added_by        BIGINT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bitrix_task_id, chat_id)
);

CREATE TABLE cursors (
    bitrix_task_id  BIGINT NOT NULL,
    chat_id         BIGINT NOT NULL,
    last_history_id BIGINT NOT NULL,
    last_message_id BIGINT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bitrix_task_id, chat_id),
    FOREIGN KEY (bitrix_task_id, chat_id)
        REFERENCES cards (bitrix_task_id, chat_id) ON DELETE CASCADE
);

CREATE TABLE chat_admins (
    chat_id          BIGINT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    telegram_user_id BIGINT NOT NULL,
    PRIMARY KEY (chat_id, telegram_user_id)
);
