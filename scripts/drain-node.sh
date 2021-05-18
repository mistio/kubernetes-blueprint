#!/usr/bin/env bash
set -ex
kubectl drain {{hostname}} --delete-emptydir-data --force --ignore-daemonsets
kubectl delete node {{hostname}}
