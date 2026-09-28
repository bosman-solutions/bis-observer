# gpu-exporter — host-side Prometheus exporter for the passthrough RTX 3080.
#
# Reads GPU telemetry from INSIDE the VM that holds the card, via the host's
# qemu-guest-agent over a bind-mounted libvirt socket (same access pattern as
# vmctl_mcp). It therefore needs the virsh client; it carries no secrets, no state.
#
# gpu_probe.py and vmctl.py are vendored from ~/claude/scc by ./sync_modules.sh
# before build (run it, or `make build`). The scc repo stays the source of truth.
FROM python:3.12-slim

# libvirt-clients provides virsh so vmctl/gpu_probe can drive qemu:///system
# and issue guest-agent commands over the mounted socket.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libvirt-clients \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vendored SCC modules (placed by sync_modules.sh) + the exporter itself.
COPY gpu_probe.py vmctl.py app.py ./

EXPOSE 9339

CMD ["python3", "app.py"]
