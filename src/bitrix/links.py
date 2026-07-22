import logging
from dataclasses import dataclass

from .client import BitrixClient, BitrixError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileLink:
    name: str
    url: str | None  # DETAIL_URL; None = ссылку получить не удалось


def task_url(webhook_url: str, webhook_user_id: int, task_id: int) -> str:
    host = webhook_url.split("/rest/")[0]
    return f"{host}/company/personal/user/{webhook_user_id}/tasks/task/view/{task_id}/"


async def resolve_files(bx: BitrixClient, file_ids: list[int]) -> list[FileLink]:
    out: list[FileLink] = []
    for fid in file_ids:
        try:
            info = await bx.call("disk.file.get", {"id": fid})
            out.append(FileLink(name=str(info.get("NAME") or f"файл {fid}"),
                                url=info.get("DETAIL_URL") or None))
        except BitrixError as e:
            log.warning("disk.file.get(%s) не удался: %s", fid, e)
            out.append(FileLink(name=f"файл {fid}", url=None))
    return out
