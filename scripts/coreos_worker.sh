#!/bin/bash
set -e

while getopts "m:" OPTION
do
    case $OPTION in
        m)
          MASTER=$OPTARG
          ;;
        ?)
          exit
          ;;
    esac
done

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

# -------------

function init_config {
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

function init_templates {
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
init_config
init_templates

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
