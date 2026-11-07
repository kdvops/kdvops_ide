import os
from kubernetes import client, config

class KubeReader:
    def __init__(self):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(config_file=os.getenv('KUBECONFIG') or None)
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()

    def query(self, intent, namespace):
        if intent == 'list_namespaces':
            return [{'name':x.metadata.name} for x in self.core.list_namespace(limit=100).items]
        if intent in ('list_pods', 'failing_pods'):
            result = self.core.list_namespaced_pod(namespace, limit=100) if namespace else self.core.list_pod_for_all_namespaces(limit=100)
            rows = []
            for pod in result.items:
                problems = []
                for cs in pod.status.container_statuses or []:
                    if cs.state and cs.state.waiting:
                        problems.append(cs.state.waiting.reason)
                    if cs.state and cs.state.terminated and cs.state.terminated.exit_code != 0:
                        problems.append(cs.state.terminated.reason or 'Terminated')
                if pod.status.phase not in ('Running','Succeeded'):
                    problems.append(pod.status.phase)
                if any(not cs.ready for cs in pod.status.container_statuses or []):
                    problems.append('NotReady')
                row = {'name':pod.metadata.name, 'namespace':pod.metadata.namespace, 'phase':pod.status.phase, 'problems':list(dict.fromkeys(problems))}
                if intent == 'list_pods' or problems:
                    rows.append(row)
            return rows
        if intent == 'list_deployments':
            result = self.apps.list_namespaced_deployment(namespace, limit=100) if namespace else self.apps.list_deployment_for_all_namespaces(limit=100)
            return [{'name':x.metadata.name,'namespace':x.metadata.namespace,'desired':x.spec.replicas or 0,'ready':x.status.ready_replicas or 0} for x in result.items]
        if intent == 'list_events':
            result = self.core.list_namespaced_event(namespace, limit=100) if namespace else self.core.list_event_for_all_namespaces(limit=100)
            return [{'namespace':x.metadata.namespace,'reason':x.reason,'message':x.message} for x in result.items]
        return []

class DemoReader:
    def query(self, intent, namespace):
        data = {
            'list_namespaces':[{'name':'default'},{'name':'demo'}],
            'list_pods':[{'name':'api-7db','namespace':'demo','phase':'Running','problems':[]},{'name':'pagos-8ac','namespace':'demo','phase':'Running','problems':['CrashLoopBackOff','NotReady']}],
            'failing_pods':[{'name':'pagos-8ac','namespace':'demo','phase':'Running','problems':['CrashLoopBackOff','NotReady']}],
            'list_deployments':[{'name':'pagos','namespace':'demo','desired':2,'ready':1}],
            'list_events':[{'namespace':'demo','reason':'BackOff','message':'Back-off restarting failed container'}],
        }
        return [r for r in data.get(intent,[]) if not namespace or r.get('namespace',r.get('name')) == namespace]
