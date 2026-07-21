from pathlib import Path

import asyncpg


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5)


async def apply_migrations(pool: asyncpg.Pool, migrations_dir: str = "migrations") -> list[str]:
    """Применяет по порядку *.sql, которых нет в schema_migrations. Возвращает применённые."""
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        applied: list[str] = []
        for path in sorted(Path(migrations_dir).glob("*.sql")):
            if path.name in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (name) VALUES ($1)", path.name
                )
            applied.append(path.name)
        return applied
