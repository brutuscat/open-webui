"""Configuration for the isolated GPT-OSS Harmony Python endpoint."""

import asyncio
import os

from jupyter_server.services.kernels.kernelmanager import AsyncMappingKernelManager
from tornado.web import HTTPError


class SingleKernelManager(AsyncMappingKernelManager):
    """Reject a second kernel while the stateless Harmony call is active."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._harmony_start_lock = asyncio.Lock()

    async def start_kernel(self, *, kernel_id=None, path=None, **kwargs):
        """Use the overridable async entrypoint instead of the inherited alias."""

        return await self._async_start_kernel(kernel_id=kernel_id, path=path, **kwargs)

    async def _async_start_kernel(self, *, kernel_id=None, path=None, **kwargs):
        async with self._harmony_start_lock:
            if (kernel_id is None or kernel_id not in self) and len(self._kernels) >= 1:
                raise HTTPError(429, "only one isolated Harmony Python kernel may run at a time")
            return await super()._async_start_kernel(kernel_id=kernel_id, path=path, **kwargs)


c.ServerApp.root_dir = "/home/user"
c.ServerApp.open_browser = False
c.ServerApp.terminals_enabled = False
c.ServerApp.token = os.environ["JUPYTER_TOKEN"]
c.ServerApp.kernel_manager_class = SingleKernelManager
