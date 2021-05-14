#!/usr/bin/env bash
set -ex
# Get worker node ('node/<name>' format)
node=$(kubectl get node --selector='!node-role.kubernetes.io/master' -o name)
# Drain the node
kubectl drain $node --delete-emptydir-data --force --ignore-daemonsets
# Remove the node from the cluster
kubectl delete $node
