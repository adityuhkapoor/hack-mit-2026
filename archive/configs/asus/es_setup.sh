#!/bin/bash
# Elasticsearch without root or docker: the tarball, single node, security off, on the venue address only.
set -e
cd ~/nimbus
if [ ! -d elasticsearch ]; then
  curl -sSL -o es.tgz https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.15.3-linux-aarch64.tar.gz
  tar xzf es.tgz && mv elasticsearch-8.15.3 elasticsearch && rm es.tgz
  cat >> elasticsearch/config/elasticsearch.yml <<Y
discovery.type: single-node
xpack.security.enabled: false
network.host: 0.0.0.0
http.port: 9200
Y
  echo "-Xms1g" > elasticsearch/config/jvm.options.d/nimbus.options
  echo "-Xmx1g" >> elasticsearch/config/jvm.options.d/nimbus.options
fi
if ! ss -ltn | grep -q ":9200 "; then
  setsid nohup ./elasticsearch/bin/elasticsearch > ~/nimbus/es.log 2>&1 < /dev/null &
fi
for i in $(seq 1 90); do curl -s -m 2 127.0.0.1:9200 >/dev/null && break; sleep 2; done
curl -s -m 5 127.0.0.1:9200 | grep -E "number|cluster_name" | head -2
