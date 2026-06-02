"""
telemetry.py — query factory for bis-theseus.

Three classes covering the three topology tiers:
  HostQuery      — node-level metrics (cpu, mem, disk, swap, io, info)
  ServiceQuery   — stack-level aggregate (container count, cpu, mem, disk)
  ContainerQuery — container-level metrics (cpu, mem, log tail)

Each class takes identifying labels and fires PromQL/LogQL against
Prometheus and Loki at query time. Nothing is cached — callers always
get present state.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class TelemetryBase:
    def __init__(self, prom_url: str, loki_url: str):
        self.prom_url = prom_url
        self.loki_url = loki_url

    async def _prom_query(self, client: httpx.AsyncClient, promql: str) -> list[dict]:
        """Fire a PromQL instant query, return result list."""
        try:
            resp = await client.get(
                f"{self.prom_url}/api/v1/query",
                params={"query": promql},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("result", [])
        except Exception as e:
            logger.warning(f"prom query failed ({promql!r}): {e}")
            return []

    async def _prom_scalar(
        self, client: httpx.AsyncClient, promql: str
    ) -> Optional[float]:
        """Fire a PromQL query, return a single float value or None."""
        results = await self._prom_query(client, promql)
        if results:
            try:
                return float(results[0]["value"][1])
            except (KeyError, IndexError, ValueError):
                pass
        return None

    async def _loki_tail(
        self, client: httpx.AsyncClient, logql: str, limit: int = 100
    ) -> list[str]:
        """Fire a LogQL query, return list of log line strings."""
        try:
            resp = await client.get(
                f"{self.loki_url}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "limit": limit,
                    "direction": "backward",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            streams = resp.json().get("data", {}).get("result", [])
            lines = []
            for stream in streams:
                for ts, line in stream.get("values", []):
                    lines.append(line)
            return lines
        except Exception as e:
            logger.warning(f"loki query failed ({logql!r}): {e}")
            return []


class HostQuery(TelemetryBase):
    """
    Node-level metrics for a single host running Node Exporter.

    instance: the Prometheus instance label, e.g. "rex:9100"
    """

    def __init__(self, instance: str, prom_url: str, loki_url: str):
        super().__init__(prom_url, loki_url)
        self.instance = instance
        self._i = f'instance="{instance}"'

    async def cpu_usage_pct(self, client: httpx.AsyncClient) -> Optional[float]:
        """Average CPU usage across all cores, percent."""
        v = await self._prom_scalar(
            client,
            f'100 - (avg by(instance)(rate(node_cpu_seconds_total{{{self._i},mode="idle"}}[5m])) * 100)',
        )
        return round(v, 2) if v is not None else None

    async def memory_used_pct(self, client: httpx.AsyncClient) -> Optional[float]:
        """Used memory as percent of total."""
        v = await self._prom_scalar(
            client,
            f'100 - ((node_memory_MemAvailable_bytes{{{self._i}}} / node_memory_MemTotal_bytes{{{self._i}}}) * 100)',
        )
        return round(v, 2) if v is not None else None

    async def memory_used_bytes(self, client: httpx.AsyncClient) -> Optional[float]:
        return await self._prom_scalar(
            client,
            f'node_memory_MemTotal_bytes{{{self._i}}} - node_memory_MemAvailable_bytes{{{self._i}}}',
        )

    async def memory_total_bytes(self, client: httpx.AsyncClient) -> Optional[float]:
        return await self._prom_scalar(
            client, f'node_memory_MemTotal_bytes{{{self._i}}}'
        )

    async def disk_used_pct(self, client: httpx.AsyncClient) -> Optional[float]:
        """Root filesystem used percent."""
        v = await self._prom_scalar(
            client,
            f'100 - ((node_filesystem_avail_bytes{{{self._i},mountpoint="/",fstype!="tmpfs"}} / node_filesystem_size_bytes{{{self._i},mountpoint="/",fstype!="tmpfs"}}) * 100)',
        )
        return round(v, 2) if v is not None else None

    async def swap_used_pct(self, client: httpx.AsyncClient) -> Optional[float]:
        """Swap used percent. Returns None if no swap."""
        total = await self._prom_scalar(
            client, f'node_memory_SwapTotal_bytes{{{self._i}}}'
        )
        if not total:
            return None
        v = await self._prom_scalar(
            client,
            f'100 - ((node_memory_SwapFree_bytes{{{self._i}}} / node_memory_SwapTotal_bytes{{{self._i}}}) * 100)',
        )
        return round(v, 2) if v is not None else None

    async def load1(self, client: httpx.AsyncClient) -> Optional[float]:
        return await self._prom_scalar(
            client, f'node_load1{{{self._i}}}'
        )

    async def uptime_seconds(self, client: httpx.AsyncClient) -> Optional[float]:
        return await self._prom_scalar(
            client, f'time() - node_boot_time_seconds{{{self._i}}}'
        )

    async def summary(self, client: httpx.AsyncClient) -> dict:
        """All host metrics in one dict. Used by aggroboard and API."""
        cpu, mem_pct, disk_pct, swap, load, uptime, mem_bytes, mem_total = (
            await asyncio.gather(
                self.cpu_usage_pct(client),
                self.memory_used_pct(client),
                self.disk_used_pct(client),
                self.swap_used_pct(client),
                self.load1(client),
                self.uptime_seconds(client),
                self.memory_used_bytes(client),
                self.memory_total_bytes(client),
            )
        )
        return {
            "instance": self.instance,
            "cpu_pct": cpu,
            "mem_pct": mem_pct,
            "mem_used_bytes": mem_bytes,
            "mem_total_bytes": mem_total,
            "disk_pct": disk_pct,
            "swap_pct": swap,
            "load1": load,
            "uptime_seconds": uptime,
        }


class ServiceQuery(TelemetryBase):
    """
    Stack-level aggregate metrics for a Docker Compose project.

    instance: host instance label, e.g. "balthazar:9100"
    project:  compose_project label, e.g. "obs-aggregator"
    """

    def __init__(self, instance: str, project: str, prom_url: str, loki_url: str):
        super().__init__(prom_url, loki_url)
        self.instance = instance
        self.project = project
        self._base = f'compose_project="{project}",node="{instance.split(":")[0]}"'

    async def container_count(self, client: httpx.AsyncClient) -> Optional[int]:
        v = await self._prom_scalar(
            client, f'count(docker_container_info{{{self._base}}})'
        )
        return int(v) if v is not None else None

    async def running_count(self, client: httpx.AsyncClient) -> Optional[int]:
        v = await self._prom_scalar(
            client,
            f'count(docker_container_info{{{self._base},state="running"}})',
        )
        return int(v) if v is not None else None

    async def summary(self, client: httpx.AsyncClient) -> dict:
        total, running = await asyncio.gather(
            self.container_count(client),
            self.running_count(client),
        )
        return {
            "instance": self.instance,
            "project": self.project,
            "container_count": total,
            "running_count": running,
        }


class ContainerQuery(TelemetryBase):
    """
    Container-level metrics from cAdvisor + Loki log tail.

    instance:       host instance label
    container_name: Docker container name
    """

    def __init__(
        self, instance: str, container_name: str, prom_url: str, loki_url: str
    ):
        super().__init__(prom_url, loki_url)
        self.instance = instance
        self.container_name = container_name
        self._c = f'name="{container_name}"'
        self._host = instance.split(":")[0]

    async def cpu_usage_pct(self, client: httpx.AsyncClient) -> Optional[float]:
        v = await self._prom_scalar(
            client,
            f'rate(container_cpu_usage_seconds_total{{{self._c},instance=~"{self._host}.*"}}[5m]) * 100',
        )
        return round(v, 2) if v is not None else None

    async def memory_used_bytes(self, client: httpx.AsyncClient) -> Optional[float]:
        return await self._prom_scalar(
            client,
            f'container_memory_usage_bytes{{{self._c},instance=~"{self._host}.*"}}',
        )

    async def log_tail(
        self, client: httpx.AsyncClient, limit: int = 100
    ) -> list[str]:
        return await self._loki_tail(
            client,
            f'{{container_name="{self.container_name}",host="{self._host}"}}',
            limit=limit,
        )

    async def summary(self, client: httpx.AsyncClient) -> dict:
        cpu, mem = await asyncio.gather(
            self.cpu_usage_pct(client),
            self.memory_used_bytes(client),
        )
        return {
            "instance": self.instance,
            "container": self.container_name,
            "cpu_pct": cpu,
            "mem_used_bytes": mem,
        }


import asyncio  # noqa: E402 — imported here to avoid circular at module level
