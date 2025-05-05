# 使用 Python 3.9 作为基础镜像，这里也可根据实际需求切换 Python 版本
FROM python:3.9-slim

# 为了减少交互与冗余输出
ENV DEBIAN_FRONTEND=noninteractive

# 安装可能需要的依赖（gcc、libffi-dev 等），根据需求可增减
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libpng-dev \
    libfreetype6-dev && \
    rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 如果你有 requirements.txt，就先复制并安装依赖
RUN pip install --no-cache-dir matplotlib
RUN pip install --no-cache-dir requests
RUN pip install --no-cache-dir aiohttp

# 复制你上面提供的脚本到容器内
COPY run.py .

# 这里我们直接设置为启动脚本，方便后期在 K8s 里通过 args 来传不同参数
ENTRYPOINT ["python", "/app/run.py"]