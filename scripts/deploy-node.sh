#!/usr/bin/env bash

set -e
while getopts "m:t:r:n:" OPTION
do
    case $OPTION in
        m)
          MASTER=$OPTARG
          ;;
        t)
          TOKEN=$OPTARG
          ;;
        r)
          ROLE=$OPTARG
          ;;
        n)
          NODE_NAME=$OPTARG
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
# Disable swap
swapoff -a
# Load br_netfilter
modprobe br_netfilter
# Set iptables to correctly see bridged traffic
cat <<EOF | tee /etc/modules-load.d/k8s.conf
br_netfilter
EOF
cat <<EOF | tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-ip6tables = 1
net.bridge.bridge-nf-call-iptables = 1
EOF
sysctl --system
# Install kubeadm, kubelet and kubectl
# Update the apt package index and install packages needed to use the Kubernetes apt repository:
apt-get update
apt-get install -y apt-transport-https ca-certificates curl
# Download the Google Cloud public signing key:
curl -fsSLo /usr/share/keyrings/kubernetes-archive-keyring.gpg https://packages.cloud.google.com/apt/doc/apt-key.gpg
# Add the Kubernetes apt repository
echo "deb [signed-by=/usr/share/keyrings/kubernetes-archive-keyring.gpg] https://apt.kubernetes.io/ kubernetes-xenial main" | tee /etc/apt/sources.list.d/kubernetes.list
# Update apt package index, install kubelet, kubeadm and kubectl, and pin their version
apt-get update
apt-get install -y --allow-change-held-packages kubelet kubeadm kubectl
apt-mark hold kubelet kubeadm kubectl
systemctl enable kubelet
# Configuring a cgroup driver
# Use containerd as CRI runtime
# Install and configure prerequisites
cat <<EOF | tee /etc/modules-load.d/containerd.conf
overlay
br_netfilter
EOF
modprobe overlay
modprobe br_netfilter
# Setup required sysctl params, these persist across reboots.
cat <<EOF | tee /etc/sysctl.d/99-kubernetes-cri.conf
net.bridge.bridge-nf-call-iptables  = 1
net.ipv4.ip_forward                 = 1
net.bridge.bridge-nf-call-ip6tables = 1
EOF
# Apply sysctl params without reboot
sysctl --system
# Install containerd
# Install Docker Engine (Ubuntu)
# Uninstall old versions
apt-get remove -y docker docker-engine docker.io containerd runc
apt-get update
apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release
# Add Docker's official GPG key
rm -f /usr/share/keyrings/docker-archive-keyring.gpg
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
# Set up the stable repository (x86_64/amd64)
echo \
  "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
# Install the latest version of Docker Engine and containerd
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io
# Verify that Docker Enginer is installed
docker run hello-world
# Configure containerd
mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml
sed -i -e 's/SystemdCgroup = false/SystemdCgroup = true/g' /etc/containerd/config.toml
cat /etc/containerd/config.toml
# Restart containerd
systemctl restart containerd
# Configure docker systemd cgroup driver
mkdir -p /etc/docker
mkdir -p /etc/systemd/system/docker.service.d
cat <<EOF | sudo tee /etc/docker/daemon.json
{
  "exec-opts": ["native.cgroupdriver=systemd"],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m"
  },
  "storage-driver": "overlay2"
}
EOF
systemctl enable docker
systemctl daemon-reload
systemctl restart docker
# Reset kubeadm in case it was already ran
set -e
rm -rf /etc/kubernetes/manifests/*.yaml
rm -rf /var/lib/etcd
rm -rf /etc/cni/net.d
kubeadm reset -f
iptables -F && iptables -t nat -F && iptables -t mangle -F && iptables -X

if [ $ROLE = "master" ]; then
    install_master_ubuntu
elif [ $ROLE = "node" ]; then
    install_node_ubuntu
fi

}

install_master_ubuntu() {
cat <<EOF > /etc/kubernetes/kubeadm-config.yaml
apiVersion: kubeadm.k8s.io/v1beta2
kind: InitConfiguration
nodeRegistration:
  name: "$NODE_NAME"
localAPIEndpoint:
  bindPort: 443
bootstrapTokens:
- token: "$TOKEN"
---
apiVersion: kubeadm.k8s.io/v1beta2
kind: ClusterConfiguration
etcd:
  local:
    extraArgs:
      'listen-peer-urls': 'http://127.0.0.1:2380'
---
kind: KubeletConfiguration
apiVersion: kubelet.config.k8s.io/v1beta1
cgroupDriver: systemd
EOF
# Verify connectivity to the gcr.io container image registry
kubeadm config images pull
# Initialize kubeadm
kubeadm init --config /etc/kubernetes/kubeadm-config.yaml
mkdir -p $HOME/.kube
sudo cp /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config
# Wait for kube-apiserver to be up and running
until $(curl --output /dev/null --silent --head --insecure https://localhost:443); do
    printf '.'
    sleep 5
done
# Initialize pod network (weave)
kubever=$(kubectl --kubeconfig /etc/kubernetes/admin.conf version | base64 | tr -d '\n')
kubectl apply -f "https://cloud.weave.works/k8s/net?k8s-version=$kubever"
}

install_node_ubuntu() {
# Join cluster
kubeadm join $MASTER:443 \
  --discovery-token-unsafe-skip-ca-verification \
  --token $TOKEN \
  --node-name $NODE_NAME
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

# Role must be provided
if [ -z "$ROLE" ]
then
    echo "Role is not set. You must specify role [-r <master><node>]"
    exit 1
fi

find_distro

if [ $DISTRO = "Ubuntu" ] || [ $DISTRO = "Debian" ];then
    ubuntu_main
fi

}

main
