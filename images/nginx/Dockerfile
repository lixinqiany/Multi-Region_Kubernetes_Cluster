# 使用 Ubuntu 22.04 作为基础镜像
FROM ubuntu:22.04 AS builder

ARG TARGETARCH=amd64
ARG TARGETOS=linux
ENV CFLAGS="-march=x86-64-v3"

# 安装编译依赖
RUN apt-get update && \
    apt-get install -y \
    build-essential \
    libpcre3-dev \
    zlib1g-dev \
    libssl-dev \
    wget \
    git \
    curl \
    vim \
    libluajit-5.1-dev \
    && rm -rf /var/lib/apt/lists/*

# 下载 Nginx 源码和 VTS 模块
ARG NGINX_VERSION=1.25.3
RUN wget https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz && \
    tar -zxvf nginx-${NGINX_VERSION}.tar.gz && \
    git clone https://github.com/vozlt/nginx-module-vts.git

# 下载并安装Exporter
RUN wget https://github.com/hnlq715/nginx-vts-exporter/releases/download/v0.10.3/nginx-vts-exporter-0.10.3.linux-amd64.tar.gz \
    && tar -xzf nginx-vts-exporter-*.tar.gz \
    && mv nginx-vts-exporter-*/nginx-vts-exporter /usr/local/nginx-vts-exporter \
    && rm -rf nginx-vts-exporter-*

# 在下载模块部分添加
RUN git clone https://github.com/openresty/echo-nginx-module.git

WORKDIR /nginx-${NGINX_VERSION}
RUN ./configure \
    --prefix=/etc/nginx \
    --sbin-path=/usr/sbin/nginx \
    --modules-path=/usr/lib/nginx/modules \
    --conf-path=/etc/nginx/nginx.conf \
    --error-log-path=/var/log/nginx/error.log \
    --http-log-path=/var/log/nginx/access.log \
    --pid-path=/var/run/nginx.pid \
    --lock-path=/var/run/nginx.lock \
    --http-client-body-temp-path=/var/cache/nginx/client_temp \
    --http-proxy-temp-path=/var/cache/nginx/proxy_temp \
    --http-fastcgi-temp-path=/var/cache/nginx/fastcgi_temp \
    --http-uwsgi-temp-path=/var/cache/nginx/uwsgi_temp \
    --http-scgi-temp-path=/var/cache/nginx/scgi_temp \
    --with-http_ssl_module \
    --with-http_realip_module \
    --with-http_stub_status_module \
    --add-module=../echo-nginx-module \
    --add-module=../nginx-module-vts && \
    make && make install

RUN mkdir -p /var/log/nginx \
    && mkdir -p /var/cache/nginx \
    && mkdir -p /var/cache/nginx/client_temp \
    && mkdir -p /var/cache/nginx/proxy_temp \
    && mkdir -p /var/cache/nginx/fastcgi_temp \
    && mkdir -p /var/cache/nginx/uwsgi_temp \
    && mkdir -p /var/cache/nginx/scgi_temp \
    && chown -R www-data:www-data /var/cache/nginx \
    && chown -R www-data:www-data /var/log/nginx

COPY nginx.conf /etc/nginx/nginx.conf
COPY start.sh /start.sh
RUN chmod +x /start.sh
COPY nginx-vts-exporter.service  /usr/lib/systemd/system/

# 暴露端口
EXPOSE 80 8080 9913

# 指定运行用户和启动命令
CMD ["/start.sh"]