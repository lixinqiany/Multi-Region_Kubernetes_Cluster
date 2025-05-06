FROM ubuntu:22.04

# 禁用交互式提示（apt-get安装过程中请求用户确认）
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update
RUN apt-get install -y wget php-cli php-xml expect vim unzip php-zip apt-utils
RUN wget https://github.com/phoronix-test-suite/phoronix-test-suite/releases/download/v10.8.4/phoronix-test-suite-10.8.4.tar.gz
RUN tar -xzvf phoronix-test-suite-10.8.4.tar.gz
RUN rm phoronix-test-suite-10.8.4.tar.gz
RUN cd phoronix-test-suite && ./install-sh

RUN phoronix-test-suite install mbw

# 减小镜像体积，删除apt-get update的元数据
RUN rm -rf /var/lib/apt/lists/*

RUN mkdir scripts
COPY response.sh /scripts
COPY executor.sh /scripts

WORKDIR /scripts
RUN chmod +x executor.sh
CMD [ "./executor.sh" ]