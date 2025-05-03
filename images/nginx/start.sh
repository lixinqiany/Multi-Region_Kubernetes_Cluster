#!/bin/bash

chown -R www-data:www-data /var/log/nginx
chown -R www-data:www-data /var/cache/nginx

/usr/local/nginx-vts-exporter \
  -nginx.scrape_uri=http://localhost/status/format/json &

exec nginx -g "daemon off;"