#!/bin/bash
set -e

while getopts "u:p:m:t:r:" OPTION
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
        ?)
          exit
          ;;
    esac
done

# kubeadm init expects a token of <6chars>.<6chars>
pass1=`date +%s | sha256sum | base64 | head -c 6 ; echo`
pass2=`date +%s | sha256sum | base64 | head -c 6 ; echo`
pass="${pass1}.${pass2}"
TOKEN=${TOKEN-$pass}

AUTH_USERNAME=${AUTH_USERNAME:-admin}
AUTH_PASSWORD=${AUTH_PASSWORD:-admin}

# Find out which distro
VERSION=`lsb_release -ds 2>/dev/null || cat /etc/*release 2>/dev/null | head -n1 || uname -om`
DISTRO=`echo $VERSION | awk '{ print $1 }'`

install_ubuntu () {

# Install needed packages
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add -
cat <<EOF > /etc/apt/sources.list.d/kubernetes.list
deb http://apt.kubernetes.io/ kubernetes-xenial main
EOF
apt-get update
apt-get install -y docker.io kubelet kubeadm kubectl kubernetes-cni curl

}

install_centos () {

# Install needed packages
cat <<EOF > /etc/yum.repos.d/kubernetes.repo
[kubernetes]
name=Kubernetes
baseurl=http://yum.kubernetes.io/repos/kubernetes-el7-x86_64
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://packages.cloud.google.com/yum/doc/yum-key.gpg
       https://packages.cloud.google.com/yum/doc/rpm-package-key.gpg
EOF

setenforce 0
yum install -y docker kubelet kubeadm kubectl kubernetes-cni curl
systemctl enable docker && systemctl start docker
systemctl enable kubelet && systemctl start kubelet

}

install_master () {
mkdir -p /etc/kubernetes/auth
echo "$AUTH_PASSWORD,$AUTH_USERNAME,1" > /etc/kubernetes/auth/basicauth.csv

# Initialize kubeadm
kubeadm init --token "$TOKEN"

# Wait for kube-apiserver to be up and running
while true
do
    if [ -n "$(curl --silent "http://localhost:8080")" ]; then
        break
    fi
    sleep 2
done

# Initialize pod network (weave)
kubectl apply -f https://git.io/weave-kube

# Deploy kubernetes dashboard
kubectl create -f https://raw.githubusercontent.com/kubernetes/dashboard/v1.4.0/src/deploy/kubernetes-dashboard.yaml

# HACK:This is a hack to sed in place the kube-apiserver
awk '/tokens/{print "          \"--basic-auth-file=/etc/kubernetes/auth/basicauth.csv\","}1' /etc/kubernetes/manifests/kube-apiserver.json > tmp && \
cp tmp /etc/kubernetes/manifests/kube-apiserver.json
}

install_node () {
# Join cluster
kubeadm join --token $TOKEN $MASTER
}

if [ $DISTRO = "Ubuntu" ];then
    install_ubuntu
elif [ $DISTRO = "CentOS" ];then
    install_centos
fi

if [ $ROLE = "master" ]; then
    install_master
elif [ $ROLE = "node" ]; then
    install_node
fi
