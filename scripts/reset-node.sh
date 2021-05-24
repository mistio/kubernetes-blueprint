#!/usr/bin/env bash
set -e
rm -rf /etc/kubernetes/manifests/*.yaml
rm -rf /var/lib/etcd
rm -rf /etc/cni/net.d
kubeadm reset -f
iptables -F && iptables -t nat -F && iptables -t mangle -F && iptables -X
