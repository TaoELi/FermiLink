from __future__ import annotations

from pathlib import Path

import aiofiles
from chainlit.data.storage_clients.base import BaseStorageClient


def _normalize_subdir(value: str) -> str:
    """Normalize a relative storage subdirectory string."""

    cleaned = (value or "").replace("\\", "/")
    parts = [part for part in cleaned.split("/") if part and part != "."]
    return "/".join(parts)


def _join_url(root_path: str, path: str) -> str:
    """Join a root URL path prefix with a child path."""

    root = (root_path or "").rstrip("/")
    tail = "/" + path.lstrip("/")
    return f"{root}{tail}" if root else tail


class LocalPublicStorageClient(BaseStorageClient):
    """Chainlit storage client that persists artifacts under `/public`."""

    def __init__(self, public_root: Path, subdir: str, root_path: str):
        self.public_root = public_root
        self.subdir = _normalize_subdir(subdir) or ".chainlit/artifacts"
        self.base_dir = (self.public_root / self.subdir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.public_url_prefix = _join_url(root_path, "/public")

    def _resolve_path(self, object_key: str) -> Path:
        key = str(object_key or "").lstrip("/").replace("\\", "/")
        path = (self.base_dir / key).resolve()
        try:
            path.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError("Invalid object key") from exc
        return path

    def _url_for_key(self, object_key: str) -> str:
        key = str(object_key or "").lstrip("/").replace("\\", "/")
        rel = f"{self.subdir}/{key}" if self.subdir else key
        return f"{self.public_url_prefix}/{rel}"

    async def upload_file(
        self,
        object_key: str,
        data: bytes | str,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> dict:
        _ = mime
        _ = content_disposition
        path = self._resolve_path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite and path.exists():
            return {"object_key": object_key, "url": self._url_for_key(object_key)}
        if isinstance(data, str):
            data = data.encode("utf-8")
        async with aiofiles.open(path, "wb") as handle:
            await handle.write(data)
        return {"object_key": object_key, "url": self._url_for_key(object_key)}

    async def delete_file(self, object_key: str) -> bool:
        path = self._resolve_path(object_key)
        try:
            path.unlink()
        except FileNotFoundError:
            return True
        return True

    async def get_read_url(self, object_key: str) -> str:
        return self._url_for_key(object_key)

    async def close(self) -> None:
        return None


def _resolve_public_root(
    *,
    configured_public_dir: str,
    app_root: Path,
    package_public_root: Path,
    is_router_only_import: bool,
) -> Path:
    """Resolve effective Chainlit public root and seed packaged assets if needed."""

    configured = Path(configured_public_dir).expanduser()
    if not configured.is_absolute():
        configured = (app_root / configured).resolve()

    if is_router_only_import:
        if package_public_root.is_dir():
            return package_public_root
        return configured

    try:
        configured.mkdir(parents=True, exist_ok=True)
    except OSError:
        if package_public_root.is_dir():
            return package_public_root
        return configured

    if package_public_root.is_dir():
        for asset in package_public_root.iterdir():
            target = configured / asset.name
            if target.exists():
                continue
            if asset.is_file():
                try:
                    target.write_bytes(asset.read_bytes())
                except OSError:
                    continue
    return configured


def _build_storage_provider(*, subdir: str, public_root: Path, root_path: str) -> BaseStorageClient:
    """Instantiate the local public storage provider for Chainlit."""

    return LocalPublicStorageClient(
        public_root=public_root,
        subdir=subdir,
        root_path=root_path,
    )
