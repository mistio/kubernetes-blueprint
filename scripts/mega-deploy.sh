#!/bin/bash

set -e
while getopts "u:p:m:t:r:h:f:" OPTION
do
    case $OPTION in
        u)
          AUTH_USERNAME=$OPTARG
          ;;
        p)
          AUTH_PASSWORD=$OPTARG
          ;;
        m)
          MASTER=$OPTARG
          ;;
        t)
          TOKEN=$OPTARG
          ;;
        r)
          ROLE=$OPTARG
          ;;
        h)
          PUBLIC_IP=$OPTARG
          ;;
        f)
          FQDN=$OPTARG
          ;;
        ?)
          exit
          ;;
    esac
done

if [ -e /etc/mist.env ]; then
    source /etc/mist.env
fi

coreos_main() {
################################################################################
#
#           COREOS
#
################################################################################
if [ $ROLE = "master" ]; then
    install_master_coreos
elif [ $ROLE = "node" ]; then
    install_node_coreos
fi
}

function init_config_coreos_master {
    local REQUIRED=('ADVERTISE_IP' 'POD_NETWORK' 'ETCD_ENDPOINTS' 'SERVICE_IP_RANGE' 'K8S_SERVICE_IP' 'DNS_SERVICE_IP' 'K8S_VER' 'HYPERKUBE_IMAGE_REPO' 'USE_CALICO')

    if [ -f $ENV_FILE ]; then
        export $(cat $ENV_FILE | xargs)
    fi

    if [ -z $ADVERTISE_IP ]; then
        export ADVERTISE_IP=$(awk -F= '/COREOS_PRIVATE_IPV4/ {print $2}' /etc/environment)
    fi

    for REQ in "${REQUIRED[@]}"; do
        if [ -z "$(eval echo \$$REQ)" ]; then
            echo "Missing required config value: ${REQ}"
            exit 1
        fi
    done

    if [ $USE_CALICO = "true" ]; then
        export K8S_NETWORK_PLUGIN="cni"
    else
        export K8S_NETWORK_PLUGIN=""
    fi
}

function init_templates_coreos_master {
    local TEMPLATE=/etc/systemd/system/kubelet.service
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
[Service]
Environment=KUBELET_VERSION=${K8S_VER}
Environment=KUBELET_ACI=${HYPERKUBE_IMAGE_REPO}
ExecStartPre=/usr/bin/mkdir -p /etc/kubernetes/manifests
ExecStart=/usr/lib/coreos/kubelet-wrapper \
  --api-servers=http://127.0.0.1:8080 \
  --register-schedulable=false \
  --network-plugin-dir=/etc/kubernetes/cni/net.d \
  --network-plugin=${K8S_NETWORK_PLUGIN} \
  --allow-privileged=true \
  --config=/etc/kubernetes/manifests \
  --hostname-override=${ADVERTISE_IP} \
  --cluster_dns=${DNS_SERVICE_IP} \
  --cluster_domain=cluster.local
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    }


    local TEMPLATE=/etc/systemd/system/calico-node.service
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
[Unit]
Description=Calico per-host agent
Requires=network-online.target
After=network-online.target

[Service]
Slice=machine.slice
Environment=CALICO_DISABLE_FILE_LOGGING=true
Environment=HOSTNAME=${ADVERTISE_IP}
Environment=IP=${ADVERTISE_IP}
Environment=FELIX_FELIXHOSTNAME=${ADVERTISE_IP}
Environment=CALICO_NETWORKING=false
Environment=NO_DEFAULT_POOLS=true
Environment=ETCD_ENDPOINTS=${ETCD_ENDPOINTS}
ExecStart=/usr/bin/rkt run --inherit-env --stage1-from-dir=stage1-fly.aci \
--volume=modules,kind=host,source=/lib/modules,readOnly=false \
--mount=volume=modules,target=/lib/modules \
--trust-keys-from-https quay.io/calico/node:v0.19.0
KillMode=mixed
Restart=always
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
    }


    local TEMPLATE=/etc/kubernetes/manifests/kube-proxy.yaml
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
apiVersion: v1
kind: Pod
metadata:
  name: kube-proxy
  namespace: kube-system
spec:
  hostNetwork: true
  containers:
  - name: kube-proxy
    image: ${HYPERKUBE_IMAGE_REPO}:$K8S_VER
    command:
    - /hyperkube
    - proxy
    - --master=http://127.0.0.1:8080
    - --proxy-mode=iptables
    securityContext:
      privileged: true
EOF
    }

    local TEMPLATE=/etc/kubernetes/manifests/kube-apiserver.yaml
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
apiVersion: v1
kind: Pod
metadata:
  name: kube-apiserver
  namespace: kube-system
spec:
  hostNetwork: true
  volumes:
  - hostPath:
      path: /etc/kubernetes/auth
    name: kubernetes-auth
  - hostPath:
      path: /etc/kubernetes/ssl
    name: ssl-certs-kubernetes
  containers:
  - name: kube-apiserver
    image: ${HYPERKUBE_IMAGE_REPO}:$K8S_VER
    command:
    - /hyperkube
    - apiserver
    - --bind-address=0.0.0.0
    - --etcd-servers=${ETCD_ENDPOINTS}
    - --allow-privileged=true
    - --insecure-bind-address=0.0.0.0
    - --insecure-port=8080
    - --service-cluster-ip-range=${SERVICE_IP_RANGE}
    - --secure-port=443
    - --advertise-address=${ADVERTISE_IP}
    - --service-account-lookup=false
    - --basic-auth-file=/etc/kubernetes/auth/basicauth.csv
    - --tls-cert-file=/etc/kubernetes/ssl/apiserver.pem
    - --tls-private-key-file=/etc/kubernetes/ssl/apiserver-key.pem
    # - --admission-control=NamespaceLifecycle,LimitRanger,ServiceAccount,ResourceQuota
    # - --tls-cert-file=/etc/kubernetes/ssl/apiserver.pem
    # - --tls-private-key-file=/etc/kubernetes/ssl/apiserver-key.pem
    # - --client-ca-file=/etc/kubernetes/ssl/ca.pem
    # - --service-account-key-file=/etc/kubernetes/ssl/apiserver-key.pem
    # - --runtime-config=extensions/v1beta1/deployments=true,extensions/v1beta1/daemonsets=true,extensions/v1beta1=true,extensions/v1beta1/thirdpartyresources=true
    ports:
    - containerPort: 443
      hostPort: 443
      name: https
    - containerPort: 8080
      hostPort: 8080
      name: local
    volumeMounts:
    - mountPath: /etc/kubernetes/auth
      name: kubernetes-auth
    - mountPath: /etc/kubernetes/ssl
      name: ssl-certs-kubernetes
EOF
    }

    local TEMPLATE=/etc/kubernetes/manifests/kube-controller-manager.yaml
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
apiVersion: v1
kind: Pod
metadata:
  name: kube-controller-manager
  namespace: kube-system
spec:
  containers:
  - name: kube-controller-manager
    image: ${HYPERKUBE_IMAGE_REPO}:$K8S_VER
    command:
    - /hyperkube
    - controller-manager
    - --master=http://127.0.0.1:8080
    - --leader-elect=true
    livenessProbe:
      httpGet:
        host: 127.0.0.1
        path: /healthz
        port: 10252
      initialDelaySeconds: 15
      timeoutSeconds: 1
  hostNetwork: true
EOF
    }

    local TEMPLATE=/etc/kubernetes/manifests/kube-scheduler.yaml
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
apiVersion: v1
kind: Pod
metadata:
  name: kube-scheduler
  namespace: kube-system
spec:
  hostNetwork: true
  containers:
  - name: kube-scheduler
    image: ${HYPERKUBE_IMAGE_REPO}:$K8S_VER
    command:
    - /hyperkube
    - scheduler
    - --master=http://127.0.0.1:8080
    - --leader-elect=true
    livenessProbe:
      httpGet:
        host: 127.0.0.1
        path: /healthz
        port: 10251
      initialDelaySeconds: 15
      timeoutSeconds: 1
EOF
    }

    local TEMPLATE=/srv/kubernetes/manifests/calico-policy-agent.yaml
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
apiVersion: v1
kind: Pod
metadata:
  name: calico-policy-agent
  namespace: calico-system
spec:
  hostNetwork: true
  containers:
    # The Calico policy agent.
    - name: k8s-policy-agent
      image: calico/k8s-policy-agent:v0.1.4
      env:
        - name: ETCD_ENDPOINTS
          value: "${ETCD_ENDPOINTS}"
        - name: K8S_API
          value: "http://127.0.0.1:8080"
        - name: LEADER_ELECTION
          value: "true"
    # Leader election container used by the policy agent.
    - name: leader-elector
      image: quay.io/calico/leader-elector:v0.1.0
      imagePullPolicy: IfNotPresent
      args:
        - "--election=calico-policy-election"
        - "--election-namespace=calico-system"
        - "--http=127.0.0.1:4040"

EOF
    }

    local TEMPLATE=/srv/kubernetes/manifests/kube-system.json
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
{
  "apiVersion": "v1",
  "kind": "Namespace",
  "metadata": {
    "name": "kube-system"
  }
}
EOF
    }

    local TEMPLATE=/srv/kubernetes/manifests/calico-system.json
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
{
  "apiVersion": "v1",
  "kind": "Namespace",
  "metadata": {
    "name": "calico-system"
  }
}
EOF
    }

    local TEMPLATE=/srv/kubernetes/manifests/network-policy.json
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
{
  "kind": "ThirdPartyResource",
  "apiVersion": "extensions/v1beta1",
  "metadata": {
    "name": "network-policy.net.alpha.kubernetes.io"
  },
  "description": "Specification for a network isolation policy",
  "versions": [
    {
      "name": "v1alpha1"
    }
  ]
}
EOF
    }

    local TEMPLATE=/srv/kubernetes/manifests/kube-dns-rc.json
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
{
  "apiVersion": "v1",
  "kind": "ReplicationController",
  "metadata": {
    "labels": {
      "k8s-app": "kube-dns",
      "kubernetes.io/cluster-service": "true",
      "version": "v11"
    },
    "name": "kube-dns-v11",
    "namespace": "kube-system"
  },
  "spec": {
    "replicas": 1,
    "selector": {
      "k8s-app": "kube-dns",
      "version": "v11"
    },
    "template": {
      "metadata": {
        "labels": {
          "k8s-app": "kube-dns",
          "kubernetes.io/cluster-service": "true",
          "version": "v11"
        }
      },
      "spec": {
        "containers": [
          {
            "command": [
              "/usr/local/bin/etcd",
              "-data-dir",
              "/var/etcd/data",
              "-listen-client-urls",
              "http://127.0.0.1:2379,http://127.0.0.1:4001",
              "-advertise-client-urls",
              "http://127.0.0.1:2379,http://127.0.0.1:4001",
              "-initial-cluster-token",
              "skydns-etcd"
            ],
            "image": "gcr.io/google_containers/etcd-amd64:2.2.1",
            "name": "etcd",
            "resources": {
              "limits": {
                "cpu": "100m",
                "memory": "500Mi"
              },
              "requests": {
                "cpu": "100m",
                "memory": "50Mi"
              }
            },
            "volumeMounts": [
              {
                "mountPath": "/var/etcd/data",
                "name": "etcd-storage"
              }
            ]
          },
          {
            "args": [
              "--kube_master_url=http://${ADVERTISE_IP}:8080",
              "--domain=cluster.local"
            ],
            "image": "gcr.io/google_containers/kube2sky:1.14",
            "livenessProbe": {
              "failureThreshold": 5,
              "httpGet": {
                "path": "/healthz",
                "port": 8080,
                "scheme": "HTTP"
              },
              "initialDelaySeconds": 60,
              "successThreshold": 1,
              "timeoutSeconds": 5
            },
            "name": "kube2sky",
            "readinessProbe": {
              "httpGet": {
                "path": "/readiness",
                "port": 8081,
                "scheme": "HTTP"
              },
              "initialDelaySeconds": 30,
              "timeoutSeconds": 5
            },
            "resources": {
              "limits": {
                "cpu": "100m",
                "memory": "200Mi"
              },
              "requests": {
                "cpu": "100m",
                "memory": "50Mi"
              }
            }
          },
          {
            "args": [
              "-machines=http://127.0.0.1:4001",
              "-addr=0.0.0.0:53",
              "-ns-rotate=false",
              "-domain=cluster.local."
            ],
            "image": "gcr.io/google_containers/skydns:2015-10-13-8c72f8c",
            "name": "skydns",
            "ports": [
              {
                "containerPort": 53,
                "name": "dns",
                "protocol": "UDP"
              },
              {
                "containerPort": 53,
                "name": "dns-tcp",
                "protocol": "TCP"
              }
            ],
            "resources": {
              "limits": {
                "cpu": "100m",
                "memory": "200Mi"
              },
              "requests": {
                "cpu": "100m",
                "memory": "50Mi"
              }
            }
          },
          {
            "args": [
              "-cmd=nslookup kubernetes.default.svc.cluster.local 127.0.0.1 >/dev/null",
              "-port=8080"
            ],
            "image": "gcr.io/google_containers/exechealthz:1.0",
            "name": "healthz",
            "ports": [
              {
                "containerPort": 8080,
                "protocol": "TCP"
              }
            ],
            "resources": {
              "limits": {
                "cpu": "10m",
                "memory": "20Mi"
              },
              "requests": {
                "cpu": "10m",
                "memory": "20Mi"
              }
            }
          }
        ],
        "dnsPolicy": "Default",
        "volumes": [
          {
            "emptyDir": {},
            "name": "etcd-storage"
          }
        ]
      }
    }
  }
}
EOF
    }

    local TEMPLATE=/srv/kubernetes/manifests/kube-dns-svc.json
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
{
  "apiVersion": "v1",
  "kind": "Service",
  "metadata": {
    "name": "kube-dns",
    "namespace": "kube-system",
    "labels": {
      "k8s-app": "kube-dns",
      "kubernetes.io/name": "KubeDNS",
      "kubernetes.io/cluster-service": "true"
    }
  },
  "spec": {
    "clusterIP": "$DNS_SERVICE_IP",
    "ports": [
      {
        "protocol": "UDP",
        "name": "dns",
        "port": 53
      },
      {
        "protocol": "TCP",
        "name": "dns-tcp",
        "port": 53
      }
    ],
    "selector": {
      "k8s-app": "kube-dns"
    }
  }
}
EOF
    }

    local TEMPLATE=/etc/flannel/options.env
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
FLANNELD_IFACE=$ADVERTISE_IP
FLANNELD_ETCD_ENDPOINTS=$ETCD_ENDPOINTS
EOF
    }

    local TEMPLATE=/etc/systemd/system/flanneld.service.d/40-ExecStartPre-symlink.conf.conf
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
[Service]
ExecStartPre=/usr/bin/ln -sf /etc/flannel/options.env /run/flannel/options.env
EOF
    }

    local TEMPLATE=/etc/systemd/system/docker.service.d/40-flannel.conf
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
[Unit]
Requires=flanneld.service
After=flanneld.service
EOF
    }

    local TEMPLATE=/etc/kubernetes/cni/net.d/10-calico.conf
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
{
    "name": "calico",
    "type": "flannel",
    "delegate": {
        "type": "calico",
        "etcd_endpoints": "$ETCD_ENDPOINTS",
        "log_level": "none",
        "log_level_stderr": "info",
        "hostname": "${ADVERTISE_IP}",
        "policy": {
            "type": "k8s",
            "k8s_api_root": "http://127.0.0.1:8080/api/v1/"
        }
    }
}
EOF
    }

}

function init_ssl_certs_coreos_master {
    mkdir -p /etc/kubernetes/ssl
    openssl genrsa -out /etc/kubernetes/ssl/ca-key.pem 2048
    openssl req -x509 -new -nodes -key /etc/kubernetes/ssl/ca-key.pem -days 10000 -out /etc/kubernetes/ssl/ca.pem -subj "/CN=kube-ca"

    local TEMPLATE=/etc/kubernetes/ssl/openssl.cnf
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
[req]
req_extensions = v3_req
distinguished_name = req_distinguished_name
[req_distinguished_name]
[ v3_req ]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment
subjectAltName = @alt_names
[alt_names]
DNS.1 = kubernetes
DNS.2 = kubernetes.default
DNS.3 = kubernetes.default.svc
DNS.4 = kubernetes.default.svc.cluster.local
DNS.5 = ${FQDN}
IP.1 = ${K8S_SERVICE_IP}
IP.2 = ${ADVERTISE_IP}
IP.3 = ${PUBLIC_IP}
EOF
    }

    openssl genrsa -out /etc/kubernetes/ssl/apiserver-key.pem 2048
    openssl req -new -key /etc/kubernetes/ssl/apiserver-key.pem -out /etc/kubernetes/ssl/apiserver.csr -subj "/CN=kube-apiserver" -config /etc/kubernetes/ssl/openssl.cnf
    openssl x509 -req -in /etc/kubernetes/ssl/apiserver.csr -CA /etc/kubernetes/ssl/ca.pem -CAkey /etc/kubernetes/ssl/ca-key.pem -CAcreateserial -out /etc/kubernetes/ssl/apiserver.pem -days 365 -extensions v3_req -extfile /etc/kubernetes/ssl/openssl.cnf
    chmod 600 /etc/kubernetes/ssl/*-key.pem
    chown root:root /etc/kubernetes/ssl/*-key.pem
}


function init_flannel_coreos_master {
    echo "Waiting for etcd..."
    while true
    do
        IFS=',' read -ra ES <<< "$ETCD_ENDPOINTS"
        for ETCD in "${ES[@]}"; do
            echo "Trying: $ETCD"
            if [ -n "$(curl --silent "$ETCD/v2/machines")" ]; then
                local ACTIVE_ETCD=$ETCD
                break
            fi
            sleep 1
        done
        if [ -n "$ACTIVE_ETCD" ]; then
            break
        fi
    done
    RES=$(curl --silent -X PUT -d "value={\"Network\":\"$POD_NETWORK\",\"Backend\":{\"Type\":\"vxlan\"}}" "$ACTIVE_ETCD/v2/keys/coreos.com/network/config?prevExist=false")
    if [ -z "$(echo $RES | grep '"action":"create"')" ] && [ -z "$(echo $RES | grep 'Key already exists')" ]; then
        echo "Unexpected error configuring flannel pod network: $RES"
    fi
}

function start_addons_coreos_master {
    echo "Waiting for Kubernetes API..."
    until curl --silent "http://127.0.0.1:8080/version"
    do
        sleep 5
    done
    echo
    echo "K8S: kube-system namespace"
    curl --silent -H "Content-Type: application/json" -XPOST -d"$(cat /srv/kubernetes/manifests/kube-system.json)" "http://127.0.0.1:8080/api/v1/namespaces" > /dev/null
    echo "K8S: DNS addon"
    curl --silent -H "Content-Type: application/json" -XPOST -d"$(cat /srv/kubernetes/manifests/kube-dns-rc.json)" "http://127.0.0.1:8080/api/v1/namespaces/kube-system/replicationcontrollers" > /dev/null
    curl --silent -H "Content-Type: application/json" -XPOST -d"$(cat /srv/kubernetes/manifests/kube-dns-svc.json)" "http://127.0.0.1:8080/api/v1/namespaces/kube-system/services" > /dev/null
}

install_master_coreos() {
PUBLIC_IP=${PUBLIC_IP:-127.0.0.1}
FQDN=${FQDN:-kubernetes.local}
mkdir -p /etc/kubernetes/auth
echo "$AUTH_PASSWORD,$AUTH_USERNAME,1" > /etc/kubernetes/auth/basicauth.csv

# List of etcd servers (http://ip:port), comma separated
export ETCD_ENDPOINTS=http://127.0.0.1:2379

# Specify the version (vX.Y.Z) of Kubernetes assets to deploy
export K8S_VER=v1.3.2_coreos.0

# Hyperkube image repository to use.
export HYPERKUBE_IMAGE_REPO=quay.io/coreos/hyperkube

# The CIDR network to use for pod IPs.
# Each pod launched in the cluster will be assigned an IP out of this range.
# Each node will be configured such that these IPs will be routable using the flannel overlay network.
export POD_NETWORK=10.2.0.0/16

# The CIDR network to use for service cluster IPs.
# Each service will be assigned a cluster IP out of this range.
# This must not overlap with any IP ranges assigned to the POD_NETWORK, or other existing network infrastructure.
# Routing to these IPs is handled by a proxy service local to each node, and are not required to be routable between nodes.
export SERVICE_IP_RANGE=10.3.0.0/24

# The IP address of the Kubernetes API Service
# If the SERVICE_IP_RANGE is changed above, this must be set to the first IP in that range.
export K8S_SERVICE_IP=10.3.0.1

# The IP address of the cluster DNS service.
# This IP must be in the range of the SERVICE_IP_RANGE and cannot be the first IP in the range.
# This same IP must be configured on all worker nodes to enable DNS service discovery.
export DNS_SERVICE_IP=10.3.0.10

# Whether to use Calico for Kubernetes network policy. When using Calico,
# K8S_VER (above) must be changed to an image tagged with CNI (e.g. v1.2.4_coreos.cni.1).
export USE_CALICO=false

# The above settings can optionally be overridden using an environment file:
ENV_FILE=/run/coreos-kubernetes/options.env

init_config_coreos_master
init_templates_coreos_master

echo "Configuring SSL Certificates"
init_ssl_certs_coreos_master

mkdir -p /etc/systemd/system/etcd2.service.d
cat << EOF > /etc/systemd/system/etcd2.service.d/40-listen-address.conf
[Service]
Environment=ETCD_LISTEN_CLIENT_URLS=http://0.0.0.0:2379
Environment=ETCD_ADVERTISE_CLIENT_URLS=http://${ADVERTISE_IP}:2379
EOF

systemctl start etcd2
systemctl enable etcd2

init_flannel_coreos_master

systemctl stop update-engine; systemctl mask update-engine

systemctl daemon-reload
systemctl enable flanneld; systemctl start flanneld
systemctl enable kubelet; systemctl start kubelet
if [ $USE_CALICO = "true" ]; then
        systemctl enable calico-node; systemctl start calico-node
        enable_calico_policy
fi

echo "Waiting for docker to come up..."
until systemctl status docker | grep running
do
    sleep 5
done

sleep 10
echo "Pre-pulling hyperkube image"
docker pull quay.io/coreos/hyperkube:$K8S_VER

start_addons_coreos_master
echo "DONE"
}

function init_config_coreos_node {
    local REQUIRED=( 'ADVERTISE_IP' 'ETCD_ENDPOINTS' 'CONTROLLER_ENDPOINT' 'DNS_SERVICE_IP' 'K8S_VER' 'HYPERKUBE_IMAGE_REPO' 'USE_CALICO' )

    if [ -f $ENV_FILE ]; then
        export $(cat $ENV_FILE | xargs)
    fi

    if [ -z $ADVERTISE_IP ]; then
        export ADVERTISE_IP=$(awk -F= '/COREOS_PRIVATE_IPV4/ {print $2}' /etc/environment)
    fi

    for REQ in "${REQUIRED[@]}"; do
        if [ -z "$(eval echo \$$REQ)" ]; then
            echo "Missing required config value: ${REQ}"
            exit 1
        fi
    done

    if [ $USE_CALICO = "true" ]; then
        export K8S_NETWORK_PLUGIN="cni"
    else
        export K8S_NETWORK_PLUGIN=""
    fi
}

function init_templates_coreos_node {
    local TEMPLATE=/etc/systemd/system/kubelet.service
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
[Service]
Environment=KUBELET_VERSION=${K8S_VER}
Environment=KUBELET_ACI=${HYPERKUBE_IMAGE_REPO}
ExecStartPre=/usr/bin/mkdir -p /etc/kubernetes/manifests
ExecStart=/usr/lib/coreos/kubelet-wrapper \
  --api-servers=${CONTROLLER_ENDPOINT} \
  --network-plugin-dir=/etc/kubernetes/cni/net.d \
  --network-plugin=${K8S_NETWORK_PLUGIN} \
  --register-node=true \
  --allow-privileged=true \
  --config=/etc/kubernetes/manifests \
  --hostname-override=${ADVERTISE_IP} \
  --cluster_dns=${DNS_SERVICE_IP} \
  --cluster_domain=cluster.local
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    }

    local TEMPLATE=/etc/systemd/system/calico-node.service
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
[Unit]
Description=Calico per-host agent
Requires=network-online.target
After=network-online.target

[Service]
Slice=machine.slice
Environment=CALICO_DISABLE_FILE_LOGGING=true
Environment=HOSTNAME=${ADVERTISE_IP}
Environment=IP=${ADVERTISE_IP}
Environment=FELIX_FELIXHOSTNAME=${ADVERTISE_IP}
Environment=CALICO_NETWORKING=false
Environment=NO_DEFAULT_POOLS=true
Environment=ETCD_ENDPOINTS=${ETCD_ENDPOINTS}
ExecStart=/usr/bin/rkt run --inherit-env --stage1-from-dir=stage1-fly.aci \
--volume=modules,kind=host,source=/lib/modules,readOnly=false \
--mount=volume=modules,target=/lib/modules \
--trust-keys-from-https quay.io/calico/node:v0.19.0
KillMode=mixed
Restart=always
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
    }

    local TEMPLATE=/etc/kubernetes/manifests/kube-proxy.yaml
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
apiVersion: v1
kind: Pod
metadata:
  name: kube-proxy
  namespace: kube-system
spec:
  hostNetwork: true
  containers:
  - name: kube-proxy
    image: ${HYPERKUBE_IMAGE_REPO}:$K8S_VER
    command:
    - /hyperkube
    - proxy
    - --master=${CONTROLLER_ENDPOINT}
    - --proxy-mode=iptables
    securityContext:
      privileged: true
EOF
    }

    local TEMPLATE=/etc/flannel/options.env
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
FLANNELD_IFACE=$ADVERTISE_IP
FLANNELD_ETCD_ENDPOINTS=$ETCD_ENDPOINTS
EOF
    }

    local TEMPLATE=/etc/systemd/system/flanneld.service.d/40-ExecStartPre-symlink.conf.conf
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
[Service]
ExecStartPre=/usr/bin/ln -sf /etc/flannel/options.env /run/flannel/options.env
EOF
    }

    local TEMPLATE=/etc/systemd/system/docker.service.d/40-flannel.conf
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
[Unit]
Requires=flanneld.service
After=flanneld.service
EOF
    }

     local TEMPLATE=/etc/kubernetes/cni/net.d/10-calico.conf
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
{
    "name": "calico",
    "type": "flannel",
    "delegate": {
        "type": "calico",
        "etcd_endpoints": "$ETCD_ENDPOINTS",
        "log_level": "none",
        "log_level_stderr": "info",
        "hostname": "${ADVERTISE_IP}",
        "policy": {
            "type": "k8s",
            "k8s_api_root": "${CONTROLLER_ENDPOINT}:443/api/v1/",
            "k8s_client_key": "/etc/kubernetes/ssl/worker-key.pem",
            "k8s_client_certificate": "/etc/kubernetes/ssl/worker.pem"
        }
    }
}
EOF
    }

}


function init_dashboard {
    mkdir -p /srv/kubernetes/manifests
    local TEMPLATE=/srv/kubernetes/manifests/kubernetes-dashboard.json
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
        {
        	"apiVersion": "v1",
        	"kind": "ReplicationController",
        	"metadata": {
        		"name": "kubernetes-dashboard-v1.1.0",
        		"namespace": "kube-system",
        		"labels": {
        			"app": "kubernetes-dashboard",
        			"version": "v1.1.0",
        			"kubernetes.io/cluster-service": "true"
        		}
        	},
        	"spec": {
        		"replicas": 1,
        		"selector": {
        			"app": "kubernetes-dashboard"
        		},
        		"template": {
        			"metadata": {
        				"labels": {
        					"app": "kubernetes-dashboard",
        					"version": "v1.1.0",
        					"kubernetes.io/cluster-service": "true"
        				}
        			},
        			"spec": {
        				"containers": [
        					{
        						"name": "kubernetes-dashboard",
        						"image": "gcr.io/google_containers/kubernetes-dashboard-amd64:v1.1.0",
        						"args": [
        							"--apiserver-host=http://${MASTER}:8080"
        						],
        						"ports": [
        							{
        								"containerPort": 9090
        							}
        						]
        					}
        				]
        			}
        		}
        	}
        }
EOF
    }

    local TEMPLATE=/srv/kubernetes/manifests/kubernetes-dashboard-svc.json
    [ -f $TEMPLATE ] || {
        echo "TEMPLATE: $TEMPLATE"
        mkdir -p $(dirname $TEMPLATE)
        cat << EOF > $TEMPLATE
        {
        	"kind": "Service",
        	"apiVersion": "v1",
        	"metadata": {
        		"labels": {
        			"app": "kubernetes-dashboard",
        			"kubernetes.io/cluster-service": "true"
        		},
        		"name": "kubernetes-dashboard",
        		"namespace": "kube-system"
        	},
        	"spec": {
        		"ports": [
        			{
        				"port": 80,
        				"targetPort": 9090
        			}
        		],
        		"selector": {
        			"app": "kubernetes-dashboard"
        		}
        	}
        }
EOF
    }

    echo "K8S: Dashboard addon"
    curl --silent -H "Content-Type: application/json" -XPOST -d"$(cat /srv/kubernetes/manifests/kubernetes-dashboard.json)" "http://$MASTER:8080/api/v1/namespaces/kube-system/replicationcontrollers" > /dev/null | echo "Done"
    curl --silent -H "Content-Type: application/json" -XPOST -d"$(cat /srv/kubernetes/manifests/kubernetes-dashboard-svc.json)" "http://$MASTER:8080/api/v1/namespaces/kube-system/services" > /dev/null | echo "Done"

}


install_node_coreos() {
# List of etcd servers (http://ip:port), comma separated
ETCD_ENDPOINTS=http://${MASTER}:2379

# The endpoint the worker node should use to contact controller nodes (https://ip:port)
# In HA configurations this should be an external DNS record or loadbalancer in front of the control nodes.
# However, it is also possible to point directly to a single control node.
export CONTROLLER_ENDPOINT=http://${MASTER}:8080

# Specify the version (vX.Y.Z) of Kubernetes assets to deploy
export K8S_VER=v1.3.2_coreos.0

# Hyperkube image repository to use.
export HYPERKUBE_IMAGE_REPO=quay.io/coreos/hyperkube

# The IP address of the cluster DNS service.
# This must be the same DNS_SERVICE_IP used when configuring the controller nodes.
export DNS_SERVICE_IP=10.3.0.10

# Whether to use Calico for Kubernetes network policy. When using Calico,
# K8S_VER (above) must be changed to an image tagged with CNI (e.g. v1.2.4_coreos.cni.1).
export USE_CALICO=false

# The above settings can optionally be overridden using an environment file:
ENV_FILE=/run/coreos-kubernetes/options.env

init_config_coreos_node
init_templates_coreos_node

systemctl stop update-engine; systemctl mask update-engine

systemctl daemon-reload
systemctl enable flanneld; systemctl start flanneld
systemctl enable kubelet; systemctl start kubelet
if [ $USE_CALICO = "true" ]; then
        systemctl enable calico-node; systemctl start calico-node
fi

echo "Waiting for docker to come up..."
until systemctl status docker | grep running
do
    sleep 5
done

sleep 10
echo "Pre-pulling hyperkube image"
docker pull quay.io/coreos/hyperkube:$K8S_VER

init_dashboard

}


ubuntu_main() {
################################################################################
#
#           UBUNTU
#
################################################################################

# Install needed packages
apt-get install -y apt-transport-https | echo "Error while installing apt-transport-https. Moving forward"
apt-get update
apt-get install -y curl apt-transport-https software-properties-common ca-certificates python-pip

# To be used later on yaml parsing
pip2 install --upgrade pip==9.0.3
pip install pyyaml

# Install docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add -
add-apt-repository \
   "deb [arch=amd64] https://download.docker.com/linux/$(. /etc/os-release; echo "$ID") \
   $(lsb_release -cs) \
   stable"
apt-get update && apt-get install -y docker-ce=$(apt-cache madison docker-ce | grep 17.09 | head -1 | awk '{print $3}')
systemctl enable docker && systemctl start docker

# Install kubeadm
curl -s https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add -
cat <<EOF >/etc/apt/sources.list.d/kubernetes.list
deb http://apt.kubernetes.io/ kubernetes-xenial main
EOF
apt-get update
apt-get install -y kubelet=$(apt-cache madison kubelet | grep 1.9.0 | head -1| awk '{print $3}') \
                   kubeadm=$(apt-cache madison kubeadm | grep 1.9.0 | head -1| awk '{print $3}') \
                   kubectl=$(apt-cache madison kubectl | grep 1.9.0 | head -1| awk '{print $3}')
systemctl enable kubelet

if [ $ROLE = "master" ]; then
    install_master_ubuntu_centos
elif [ $ROLE = "node" ]; then
    install_node_ubuntu_centos
fi

}

centos_main() {
################################################################################
#
#           CENTOS
#
################################################################################

# Install needed packages
cat <<EOF > /etc/yum.repos.d/kubernetes.repo
[kubernetes]
name=Kubernetes
baseurl=https://packages.cloud.google.com/yum/repos/kubernetes-el7-x86_64
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://packages.cloud.google.com/yum/doc/yum-key.gpg https://packages.cloud.google.com/yum/doc/rpm-package-key.gpg
EOF

setenforce 0
sed -i --follow-symlinks 's/^SELINUX=.*/SELINUX=disabled/g' /etc/sysconfig/selinux

cat <<EOF >  /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-ip6tables = 1
net.bridge.bridge-nf-call-iptables = 1
EOF
sysctl --system

yum install -y docker kubelet-$(yum list available kubelet --showduplicates | grep 1.9.0 | head -1 | awk '{print $2}') \
                      kubeadm-$(yum list available kubeadm --showduplicates | grep 1.9.0 | head -1 | awk '{print $2}') \
                      kubectl-$(yum list available kubectl --showduplicates | grep 1.9.0 | head -1 | awk '{print $2}') \
                      python-pip

# To be used later on yaml parsing
pip install --upgrade pip
pip install pyyaml

systemctl enable docker && systemctl start docker
systemctl enable kubelet && systemctl start kubelet


if [ $ROLE = "master" ]; then
    install_master_ubuntu_centos
elif [ $ROLE = "node" ]; then
    install_node_ubuntu_centos
fi

}

install_master_ubuntu_centos() {
mkdir -p /etc/kubernetes/auth
echo "$AUTH_PASSWORD,$AUTH_USERNAME,1" > /etc/kubernetes/auth/basicauth.csv

# Initialize kubeadm
kubeadm init --token "$TOKEN" --apiserver-bind-port 443
sysctl net.bridge.bridge-nf-call-iptables=1

# Wait for kube-apiserver to be up and running
until $(curl --output /dev/null --silent --head --insecure https://localhost:443); do
    printf '.'
    sleep 5
done


# Initialize pod network (weave)
kubever=$(kubectl --kubeconfig /etc/kubernetes/admin.conf version | base64 | tr -d '\n')
kubectl --kubeconfig /etc/kubernetes/admin.conf apply -f "https://cloud.weave.works/k8s/net?k8s-version=$kubever"


mkdir -p /var/lib/mist
# Hack to enable basicauth
cat <<EOF > /var/lib/mist/parser.py
#!/usr/bin/env python
import sys
import yaml
file = sys.argv[-1]
with open(file, 'r') as f:
    manifest = yaml.load(open(file, 'r'))
manifest['spec']['containers'][0]['command'].append('--basic-auth-file=/etc/kubernetes/auth/basicauth.csv')
auth_volume = {'hostPath': {'path': '/etc/kubernetes/auth', 'type': 'DirectoryOrCreate'},
                'name': 'auth'}
manifest['spec']['volumes'].append(auth_volume)
auth_volume_mount = {'mountPath': '/etc/kubernetes/auth', 'name': 'auth', 'readOnly': True}
manifest['spec']['containers'][0]['volumeMounts'].append(auth_volume_mount)
with open(file, 'w') as outfile:
    yaml.dump(manifest, outfile, default_flow_style=False)
EOF

python /var/lib/mist/parser.py /etc/kubernetes/manifests/kube-apiserver.yaml

systemctl restart kubelet

# Wait for kube-apiserver to be up and running
until $(curl --output /dev/null --silent --head --insecure https://localhost:443); do
    printf '.'
    sleep 5
done



# Deploy kubernetes dashboard
cat <<EOF > /etc/kubernetes/kubernetes-dashboard.yaml
# ------------------- Dashboard Secret ------------------- #

apiVersion: v1
kind: Secret
metadata:
  labels:
    k8s-app: kubernetes-dashboard
  name: kubernetes-dashboard-certs
  namespace: kube-system
type: Opaque

---
# ------------------- Dashboard Service Account ------------------- #

apiVersion: v1
kind: ServiceAccount
metadata:
  labels:
    k8s-app: kubernetes-dashboard
  name: kubernetes-dashboard
  namespace: kube-system

---
# ------------------- Dashboard Role & Role Binding ------------------- #

kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: kubernetes-dashboard-minimal
  namespace: kube-system
rules:
  # Allow Dashboard to create 'kubernetes-dashboard-key-holder' secret.
- apiGroups: [""]
  resources: ["secrets"]
  verbs: ["create"]
  # Allow Dashboard to get, update and delete Dashboard exclusive secrets.
- apiGroups: [""]
  resources: ["secrets"]
  resourceNames: ["kubernetes-dashboard-key-holder", "kubernetes-dashboard-certs"]
  verbs: ["get", "update", "delete"]
  # Allow Dashboard to get and update 'kubernetes-dashboard-settings' config map.
- apiGroups: [""]
  resources: ["configmaps"]
  resourceNames: ["kubernetes-dashboard-settings"]
  verbs: ["get", "update"]
  # Allow Dashboard to get metrics from heapster.
- apiGroups: [""]
  resources: ["services"]
  resourceNames: ["heapster"]
  verbs: ["proxy"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: kubernetes-dashboard-minimal
  namespace: kube-system
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: kubernetes-dashboard-minimal
subjects:
- kind: ServiceAccount
  name: kubernetes-dashboard
  namespace: kube-system

---
# ------------------- Dashboard Deployment ------------------- #

kind: Deployment
apiVersion: apps/v1beta2
metadata:
  labels:
    k8s-app: kubernetes-dashboard
  name: kubernetes-dashboard
  namespace: kube-system
spec:
  replicas: 1
  revisionHistoryLimit: 10
  selector:
    matchLabels:
      k8s-app: kubernetes-dashboard
  template:
    metadata:
      labels:
        k8s-app: kubernetes-dashboard
    spec:
      containers:
      - name: kubernetes-dashboard
        image: gcr.io/google_containers/kubernetes-dashboard-amd64:v1.8.1
        ports:
        - containerPort: 8443
          protocol: TCP
        args:
          - --auto-generate-certificates
          - --authentication-mode=basic
          # Uncomment the following line to manually specify Kubernetes API server Host
          # If not specified, Dashboard will attempt to auto discover the API server and connect
          # to it. Uncomment only if the default does not work.
          # - --apiserver-host=http://my-address:port
        volumeMounts:
        - name: kubernetes-dashboard-certs
          mountPath: /certs
          # Create on-disk volume to store exec logs
        - mountPath: /tmp
          name: tmp-volume
        livenessProbe:
          httpGet:
            scheme: HTTPS
            path: /
            port: 8443
          initialDelaySeconds: 30
          timeoutSeconds: 30
      volumes:
      - name: kubernetes-dashboard-certs
        secret:
          secretName: kubernetes-dashboard-certs
      - name: tmp-volume
        emptyDir: {}
      serviceAccountName: kubernetes-dashboard
      # Comment the following tolerations if Dashboard must not be deployed on master
      tolerations:
      - key: node-role.kubernetes.io/master
        effect: NoSchedule

---
# ------------------- Dashboard Service ------------------- #

kind: Service
apiVersion: v1
metadata:
  labels:
    k8s-app: kubernetes-dashboard
  name: kubernetes-dashboard
  namespace: kube-system
spec:
  ports:
    - port: 443
      targetPort: 8443
  selector:
    k8s-app: kubernetes-dashboard
EOF
kubectl --kubeconfig /etc/kubernetes/admin.conf apply -f /etc/kubernetes/kubernetes-dashboard.yaml

cat <<EOF > /etc/kubernetes/dashboard-rbac.yaml
kind: ClusterRoleBinding
apiVersion: rbac.authorization.k8s.io/v1beta1
metadata:
  name: dashboard-admin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: ServiceAccount
  name: default
  namespace: kube-system
EOF
kubectl --kubeconfig /etc/kubernetes/admin.conf apply -f /etc/kubernetes/dashboard-rbac.yaml


cat <<EOF > /etc/kubernetes/admin-rbac.yaml
kind: ClusterRoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: admin-user-global
subjects:
- kind: User
  name: $AUTH_USERNAME
  apiGroup: rbac.authorization.k8s.io
roleRef:
  kind: ClusterRole
  name: cluster-admin
  apiGroup: rbac.authorization.k8s.io
EOF
kubectl --kubeconfig /etc/kubernetes/admin.conf apply -f /etc/kubernetes/admin-rbac.yaml

}

install_node_ubuntu_centos() {
# Join cluster
kubeadm join --discovery-token-unsafe-skip-ca-verification --token $TOKEN $MASTER:443
}



find_distro () {
################################################################################
#
#           FIND WHICH OS/DISTRO WE HAVE
#
################################################################################


VERSION=`lsb_release -ds 2>/dev/null || cat /etc/*release 2>/dev/null | head -n1 || uname -om`

if [[ $VERSION =~ .*Ubuntu* ]]
then
   echo "Found Ubuntu distro"
   DISTRO="Ubuntu"
elif [[ $VERSION =~ .*Debian* ]]
then
   echo "Found Debian distro"
   DISTRO="Debian"
elif [[ $VERSION =~ .*CentOS* ]]
then
   echo "Found CentOS distro"
   DISTRO="CentOS"
elif [[ $VERSION =~ .*CoreOS* ]]
then
   echo "Found CoreOS distro"
   DISTRO="CoreOS"
else
    echo "Distro not supported"
    exit 1
fi

}

main () {
################################################################################
#
#           MAIN FUNCTION
#
################################################################################


# kubeadm init expects a token of <6chars>.<16chars>
pass1=`date +%s | sha256sum | head -c 6 ; echo`
pass2=`date +%s | sha256sum | head -c 16 ; echo`
pass="${pass1}.${pass2}"
TOKEN=${TOKEN-$pass}

# If username and password not given then they become admin
AUTH_USERNAME=${AUTH_USERNAME:-admin}
AUTH_PASSWORD=${AUTH_PASSWORD:-admin}

# Role must be provided
if [ -z "$ROLE" ]
then
    echo "Role is not set. You must specify role [-r <master><node>]"
    exit 1
fi

find_distro

if [ $DISTRO = "Ubuntu" ] || [ $DISTRO = "Debian" ];then
    ubuntu_main
elif [ $DISTRO = "CentOS" ];then
    centos_main
elif [ $DISTRO = "CoreOS" ];then
    coreos_main
fi

}

main
