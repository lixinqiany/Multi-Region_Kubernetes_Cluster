wget https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
# modify to --kubelet-preferred-address-types=InternalIP
# add --kubelet-insecure-tls

kubectl apply -f components.yaml