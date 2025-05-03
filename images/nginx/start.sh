#!/bin/bash

# 确保日志和缓存目录权限正确
chown -R www-data:www-data /var/log/nginx
chown -R www-data:www-data /var/cache/nginx

/usr/local/nginx-vts-exporter \
  -nginx.scrape_uri=http://localhost/status/format/json &

# 启动 Nginx（前台运行）
exec nginx -g "daemon off;"