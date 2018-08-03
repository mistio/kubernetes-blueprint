#!/bin/sh

set -ex

# Drain the node.

kubectl \
    --server="https://{{server_ip}}" \
    --username="{{auth_user}}" \
    --password="{{auth_pass}}" \
    --insecure-skip-tls-verify \
    drain {{hostname}} --delete-local-data --force --ignore-daemonsets

# Remove it from the cluster.

kubectl \
    --server="https://{{server_ip}}" \
    --username="{{auth_user}}" \
    --password="{{auth_pass}}" \
    --insecure-skip-tls-verify \
    delete nodes {{hostname}}
