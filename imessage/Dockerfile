# imessaged：Linux 原生 iMessage daemon（rustpush + 真 Mac anisette）
# 构建上下文 = /srv/push/imessage/
# 基础镜像与编译机一致（Ubuntu 24.04 / glibc 2.39）
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        dbus ca-certificates zlib1g libgcc-s1 openssh-client curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 10001 --create-home --shell /bin/sh imsg

COPY --chown=root:root bin/imessaged /usr/local/bin/imessaged
COPY --chown=root:root bin/imsg /usr/local/bin/imsg
COPY --chown=root:root entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh /usr/local/bin/imessaged /usr/local/bin/imsg

USER 10001
ENV IMESSAGED_STATE_DIR=/state \
    IMESSAGED_BUS_DIR=/bus \
    XDG_RUNTIME_DIR=/run/xdg \
    XDG_DATA_HOME=/apple-data

ENTRYPOINT ["/entrypoint.sh"]
