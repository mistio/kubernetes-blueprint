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
pip install --upgrade pip
pip install pyyaml

# Install docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add -
add-apt-repository \
   "deb [arch=amd64] https://download.docker.com/linux/$(. /etc/os-release; echo "$ID") \
   $(lsb_release -cs) \
   stable"
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io
systemctl enable docker && systemctl start docker

# Install kubeadm
curl -s https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add -
add-apt-repository \
   "deb http://apt.kubernetes.io/ kubernetes-xenial main"
cat <<EOF >/etc/apt/sources.list.d/kubernetes.list
deb http://apt.kubernetes.io/ kubernetes-xenial main
EOF
apt-get update
apt-get install -y kubelet=$(apt-cache madison kubelet | grep 1.17 | head -1| awk '{print $3}') \
                   kubeadm=$(apt-cache madison kubeadm | grep 1.17 | head -1| awk '{print $3}') \
                   kubectl=$(apt-cache madison kubectl | grep 1.17 | head -1| awk '{print $3}')
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

yum update -y
yum install -y docker-ce docker-ce-cli containerd.io \
                      kubelet-$(yum list available kubelet --showduplicates | grep 1.17 | head -1 | awk '{print $2}') \
                      kubeadm-$(yum list available kubeadm --showduplicates | grep 1.17 | head -1 | awk '{print $2}') \
                      kubectl-$(yum list available kubectl --showduplicates | grep 1.17 | head -1 | awk '{print $2}')

# Install pip via curl.
curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py && python get-pip.py

# To be used later on yaml parsing
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

# Work-around for https://github.com/kubernetes/kubernetes/issues/57709. Force
# ETCD to use the correct IP address.
cat <<EOF > /etc/kubernetes/admin.yaml
apiVersion: kubeadm.k8s.io/v1alpha1
kind: MasterConfiguration

api:
  bindPort: 443

etcd:
  extraArgs:
    'listen-peer-urls': 'http://127.0.0.1:2380'

token: $TOKEN
tokenTTL: 0s
EOF

# Initialize kubeadm
kubeadm init --config /etc/kubernetes/admin.yaml
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
  labels:
    k8s-app: kubernetes-dashboard
  name: kubernetes-dashboard
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: ServiceAccount
  name: kubernetes-dashboard
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
fi

}

main
