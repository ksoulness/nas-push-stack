# imessage-gw：SMS → iMessage 网关（自包含，不依赖自定义基础镜像）
# 构建上下文 = /srv/push/imessage/
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# imsg 客户端（与 imessaged 同源同版本），只用来和共享 session bus 说话
COPY --chown=root:root bin/imsg /usr/local/bin/imsg
COPY --chown=root:root gw/gateway.py /app/gateway.py
RUN chmod 0755 /usr/local/bin/imsg

RUN useradd --uid 10001 --create-home --shell /bin/sh gw
USER 10001
ENV STATE_DIR=/data

ENTRYPOINT ["python3", "-u", "/app/gateway.py"]
