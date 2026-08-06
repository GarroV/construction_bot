-- Почасовой режим (§7): расписание становится интервальным вместо «раз в день в digest_time».
-- last_run_at — timestamp последнего прогона тика по чату (не отправки). NULL = прогонов ещё
-- не было, первый тик срабатывает сразу. is_digest_due сравнивает now - last_run_at с интервалом.
ALTER TABLE chats ADD COLUMN last_run_at TIMESTAMPTZ;
