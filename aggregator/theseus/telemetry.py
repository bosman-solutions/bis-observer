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
import time
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

    async def _prom_range(
        self,
        client: httpx.AsyncClient,
        promql: str,
        minutes: int = 15,
        step: str = "30s",
    ) -> list[list]:
        """Fire a PromQL range query, return [[unix_ts, value], ...] for the
        first series. Used for sparklines (e.g. bis-starmap ECG)."""
        end = time.time()
        start = end - minutes * 60
        try:
            resp = await client.get(
                f"{self.prom_url}/api/v1/query_range",
                params={"query": promql, "start": start, "end": end, "step": step},
                timeout=10.0,
            )
            resp.raise_for_status()
            results = resp.json().get("data", {}).get("result", [])
            if results:
                return [
                    [float(t), float(v)]
                    for t, v in results[0].get("values", [])
                ]
            return []
        except Exception as e:
            logger.warning(f"prom range query failed ({promql!r}): {e}")
            return []

    async def _loki_tail(
        self, client: httpx.AsyncClient, logql: str, limit: int = 100
    ) -> list[dict]:
        """Fire a LogQL query, return [{ts, level, line}, ...] newest first.
        ts is unix seconds (float); level comes from the stream label if set."""
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
                level = stream.get("stream", {}).get("level")
                for ts, line in stream.get("values", []):
                    lines.append({
                        "ts": int(ts) / 1e9,  # Loki timestamps are ns
                        "level": level,
                        "line": line,
                    })
            lines.sort(key=lambda x: x["ts"], reverse=True)
            return lines[:limit]
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

    NETDEV_FILTER = 'device!~"lo|veth.*|br-.*|docker.*|virbr.*"'
    FS_FILTER     = 'fstype!~"tmpfs|squashfs|overlay|devtmpfs|ramfs|efivarfs|fuse.lxcfs"'

    async def net_io_bps(self, client: httpx.AsyncClient) -> dict:
        """Aggregate physical-interface rx/tx bytes per second."""
        rx, tx = await asyncio.gather(
            self._prom_scalar(client, f'sum(rate(node_network_receive_bytes_total{{{self._i},{self.NETDEV_FILTER}}}[5m]))'),
            self._prom_scalar(client, f'sum(rate(node_network_transmit_bytes_total{{{self._i},{self.NETDEV_FILTER}}}[5m]))'),
        )
        return {"rx_bps": rx, "tx_bps": tx}

    async def disk_io_bps(self, client: httpx.AsyncClient) -> dict:
        """Aggregate disk read/write bytes per second."""
        rd, wr = await asyncio.gather(
            self._prom_scalar(client, f'sum(rate(node_disk_read_bytes_total{{{self._i}}}[5m]))'),
            self._prom_scalar(client, f'sum(rate(node_disk_written_bytes_total{{{self._i}}}[5m]))'),
        )
        return {"read_bps": rd, "write_bps": wr}

    async def mounts(self, client: httpx.AsyncClient) -> list[dict]:
        """Per-mountpoint usage: mountpoint, size, available, used%."""
        sizes, avails = await asyncio.gather(
            self._prom_query(client, f'node_filesystem_size_bytes{{{self._i},{self.FS_FILTER}}}'),
            self._prom_query(client, f'node_filesystem_avail_bytes{{{self._i},{self.FS_FILTER}}}'),
        )
        avail_by_mp = {}
        for r in avails:
            mp = r.get("metric", {}).get("mountpoint")
            try:
                avail_by_mp[mp] = float(r["value"][1])
            except (KeyError, IndexError, ValueError):
                continue
        out = []
        for r in sizes:
            mp = r.get("metric", {}).get("mountpoint")
            try:
                size = float(r["value"][1])
            except (KeyError, IndexError, ValueError):
                continue
            if not mp or not size:
                continue
            avail = avail_by_mp.get(mp)
            used_pct = round(100 - (avail / size * 100), 1) if avail is not None else None
            out.append({
                "mountpoint": mp,
                "size_bytes": size,
                "avail_bytes": avail,
                "used_pct": used_pct,
            })
        out.sort(key=lambda m: m["mountpoint"])
        return out

    async def cpu_range(self, client: httpx.AsyncClient, minutes: int = 15) -> list[list]:
        """CPU usage % time series for sparklines."""
        return await self._prom_range(
            client,
            f'100 - (avg by(instance)(rate(node_cpu_seconds_total{{{self._i},mode="idle"}}[2m])) * 100)',
            minutes=minutes,
        )

    async def log_tail(
        self,
        client: httpx.AsyncClient,
        limit: int = 100,
        level: Optional[str] = None,
        containers: Optional[str] = None,
    ) -> list[dict]:
        """All container logs on this host via Loki. `containers` optionally
        narrows to a regex alternation of container names (stack scope)."""
        sel = f'node="{self.instance}",container_name=~"{containers or ".+"}"'
        if level:
            sel += f',level=~"{level}"'
        return await self._loki_tail(client, "{" + sel + "}", limit=limit)

    async def summary(self, client: httpx.AsyncClient) -> dict:
        """All host metrics in one dict. Used by aggroboard and API."""
        (cpu, mem_pct, disk_pct, swap, load, uptime, mem_bytes, mem_total,
         net_io, disk_io, mounts) = (
            await asyncio.gather(
                self.cpu_usage_pct(client),
                self.memory_used_pct(client),
                self.disk_used_pct(client),
                self.swap_used_pct(client),
                self.load1(client),
                self.uptime_seconds(client),
                self.memory_used_bytes(client),
                self.memory_total_bytes(client),
                self.net_io_bps(client),
                self.disk_io_bps(client),
                self.mounts(client),
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
            "net_io": net_io,
            "disk_io": disk_io,
            "mounts": mounts,
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

    async def net_io_bps(self, client: httpx.AsyncClient) -> dict:
        """Container network rx/tx bytes per second (all interfaces)."""
        rx, tx = await asyncio.gather(
            self._prom_scalar(client, f'sum(rate(container_network_receive_bytes_total{{{self._c},instance=~"{self._host}.*"}}[5m]))'),
            self._prom_scalar(client, f'sum(rate(container_network_transmit_bytes_total{{{self._c},instance=~"{self._host}.*"}}[5m]))'),
        )
        return {"rx_bps": rx, "tx_bps": tx}

    async def disk_io_bps(self, client: httpx.AsyncClient) -> dict:
        """Container filesystem read/write bytes per second (cAdvisor)."""
        rd, wr = await asyncio.gather(
            self._prom_scalar(client, f'sum(rate(container_fs_reads_bytes_total{{{self._c},instance=~"{self._host}.*"}}[5m]))'),
            self._prom_scalar(client, f'sum(rate(container_fs_writes_bytes_total{{{self._c},instance=~"{self._host}.*"}}[5m]))'),
        )
        return {"read_bps": rd, "write_bps": wr}

    async def cpu_range(self, client: httpx.AsyncClient, minutes: int = 15) -> list[list]:
        """CPU usage % time series for sparklines."""
        return await self._prom_range(
            client,
            f'rate(container_cpu_usage_seconds_total{{{self._c},instance=~"{self._host}.*"}}[2m]) * 100',
            minutes=minutes,
        )

    async def log_tail(
        self,
        client: httpx.AsyncClient,
        limit: int = 100,
        level: Optional[str] = None,
    ) -> list[dict]:
        """Recent log lines via Loki. level accepts a single value or
        regex alternation, e.g. "error" or "warn|error"."""
        sel = f'container_name="{self.container_name}",node="{self._host}"'
        if level:
            sel += f',level=~"{level}"'
        return await self._loki_tail(client, "{" + sel + "}", limit=limit)

    async def summary(self, client: httpx.AsyncClient) -> dict:
        cpu, mem, net_io, disk_io = await asyncio.gather(
            self.cpu_usage_pct(client),
            self.memory_used_bytes(client),
            self.net_io_bps(client),
            self.disk_io_bps(client),
        )
        return {
            "instance": self.instance,
            "container": self.container_name,
            "cpu_pct": cpu,
            "mem_used_bytes": mem,
            "net_io": net_io,
            "disk_io": disk_io,
        }


import asyncio  # noqa: E402 — imported here to avoid circular at module level
