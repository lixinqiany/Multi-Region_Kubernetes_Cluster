wget https://github.com/prometheus-operator/kube-prometheus/archive/refs/tags/v0.14.0.tar.gz

tar -xf v0.14.0.tar.gz

cd kube-prometheus-0.14.0/manifests/

# vim prometheus-service.yaml
# vim grafana-service.yaml
# vim alertmanager-service.yaml
# use nodeport
cd ..
kubectl apply --server-side -f manifests/setup
kubectl wait \
	--for condition=Established \
	--all CustomResourceDefinition \
	--namespace=monitoring
kubectl apply -f manifests/

kubectl delete -f manifests/prometheus-networkPolicy.yaml
kubectl delete -f manifests/grafana-networkPolicy.yaml
kubectl delete -f manifests/alertmanager-networkPolicy.yaml