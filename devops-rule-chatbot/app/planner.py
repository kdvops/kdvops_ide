"""Deterministic multi-step request planner; no generated shell commands."""
import re
from .nlu import parse, normalize

READ_INTENTS = {'list_pods','failing_pods','list_deployments','list_namespaces','list_events'}
FOLLOWUP = re.compile(r"\b(?:y|tambien|ademas)\s+(?:los|las|el|la)?\s*(?:que\s+)?(?:fallan|fallando|tienen errores|estan mal)\b")


def plan(message, context=None):
    context = context or {}
    # Never execute any part of a request containing a potentially mutating verb.
    full = parse(message, context)
    if full.get('reason') == 'write_operation_disabled':
        return {'status':'blocked','steps':[], 'nlu':full, 'reason':'write_operation_disabled'}
    # Split explicit independent requests; avoid splitting ordinary noun phrases.
    parts = [p.strip(' ,.;') for p in re.split(r'\s*(?:;|\by\s+(?=(?:muestra|listar|lista|dame|consulta|consultar|revisa|ver|ensename|enseña|mostrar)\b)|\bluego\s+(?=(?:muestra|listar|lista|dame|consulta|consultar|revisa|ver|ensename|enseña|mostrar)\b))', message, flags=re.I) if p.strip(' ,.;')]
    if len(parts)>5:
        return {'status':'unsupported','steps':[], 'reason':'too_many_steps', 'kind':'compound'}
    steps=[]
    propagated_namespace = context.get('namespace')
    for part in parts:
        result=parse(part,context)
        if result['status'] == 'unknown' and FOLLOWUP.search(normalize(part)) and context.get('last_intent') in ('list_pods','failing_pods'):
            result=parse('pods que fallan',context)
        if result['status'] != 'matched' or result['intent'] not in READ_INTENTS:
            return {'status':'unsupported' if result['status']=='unsupported' else result['status'], 'steps':[], 'nlu':result,'reason':'step_not_executable', 'kind':'compound' if len(parts) > 1 else 'single'}
        explicit_namespace = result.get('namespace')
        namespace = explicit_namespace or propagated_namespace
        if explicit_namespace:
            source = 'explicit'
            propagated_namespace = explicit_namespace
        elif propagated_namespace:
            source = 'propagated'
        else:
            source = 'default'
        steps.append({'step_id':str(len(steps) + 1),'intent':result['intent'],'namespace':namespace,'parameters':{'namespace':namespace},'entity':result.get('entity'),'source':source,'status':'ready'})
    return {'status':'ready','kind':'compound' if len(steps) > 1 else 'single','steps':steps,'nlu':full}
