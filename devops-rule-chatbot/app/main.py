import os
import time
import secrets
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from kubernetes.client.exceptions import ApiException
from .nlu import parse, EXAMPLES
from .planner import plan
from .kube import DemoReader, KubeReader

app = FastAPI(title='Asistente Conversacional DevOps basado en reglas', version='0.5.0')
DEMO = os.getenv('DEMO_MODE','true').lower() == 'true'
reader = DemoReader() if DEMO else None
sessions = {}
SESSION_TTL = 3600

class Message(BaseModel):
    message: str = Field(min_length=1,max_length=500)
    session_id: str | None = None

@app.get('/')
def home():
    return FileResponse(Path(__file__).resolve().parent.parent / 'static' / 'index.html')

@app.get('/healthz')
def health():
    return {'ok':True,'demo':DEMO}

@app.post('/api/chat')
def chat(request: Message):
    now = time.time()
    for key, value in list(sessions.items()):
        if now - value['last_seen'] > SESSION_TTL:
            del sessions[key]
    sid = request.session_id if request.session_id in sessions else secrets.token_urlsafe(18)
    state = sessions.setdefault(sid,{'namespace':None,'entity':None,'last_items':[],'last_intent':None,'last_seen':now})
    state['last_seen'] = now
    result = parse(request.message, state)
    execution_plan = plan(request.message, state)
    if execution_plan['status'] == 'blocked':
        return {'session_id':sid,'status':'blocked','reply':'Solicitud bloqueada: contiene una operación de escritura. No se ejecutó ninguna acción.','nlu':result,'plan':execution_plan,'demo':DEMO}
    if execution_plan['status'] == 'ready' and len(execution_plan['steps']) > 1:
        output=[]
        try:
            backend = reader if DEMO else KubeReader()
            for step in execution_plan['steps']:
                rows = backend.query(step['intent'], step['namespace'])
                output.append({'intent':step['intent'],'namespace':step['namespace'],'items':rows,'count':len(rows)})
        except ApiException as exc:
            raise HTTPException(status_code=403 if exc.status in (401,403) else 502, detail='No se pudieron completar todas las consultas')
        except Exception:
            raise HTTPException(status_code=503,detail='Conexión con Kubernetes no disponible')
        state['last_items'] = output[-1]['items']
        state['last_intent'] = output[-1]['intent']
        state['namespace'] = output[-1]['namespace']
        return {'session_id':sid,'status':'compound','reply':'Consultas completadas: '+', '.join(f"{x['intent']}: {x['count']}" for x in output),'results':output,'plan':execution_plan,'nlu':result,'demo':DEMO}
    if result['status'] == 'unknown':
        return {'session_id':sid,'status':'unknown','reply':'No entendí la solicitud. Escribe "ayuda" para ver las consultas disponibles.','nlu':result,'plan':execution_plan,'demo':DEMO}
    if result['status'] == 'unsupported':
        return {'session_id':sid,'status':'unsupported','reply':'Reconozco la solicitud (' + str(result['intent']) + '), pero esta operación todavía no está implementada. No se ejecutó ninguna acción.','nlu':result,'plan':execution_plan,'demo':DEMO}
    if result['status'] == 'ambiguous':
        return {'session_id':sid,'status':'ambiguous','reply':'No estoy seguro. ¿Quieres '+ ' o '.join(result['candidates']) + '?','nlu':result,'plan':execution_plan,'demo':DEMO}
    intent = result['intent']
    if intent == 'help':
        return {'session_id':sid,'status':'single','reply':'Puedo consultar: pods, pods con errores, deployments, namespaces y eventos. Ejemplo: "muestra los pods en ns demo".','nlu':result,'plan':execution_plan,'demo':DEMO}
    if result['namespace']:
        state['namespace'] = result['namespace']
    if result.get('entity'):
        state['entity'] = result['entity']
    namespace = state['namespace']
    try:
        backend = reader if DEMO else KubeReader()
        rows = backend.query(intent, namespace)
    except ApiException as exc:
        if exc.status in (401,403):
            raise HTTPException(status_code=403,detail='Acceso a Kubernetes denegado por RBAC')
        raise HTTPException(status_code=502,detail='Error consultando Kubernetes')
    except Exception:
        raise HTTPException(status_code=503,detail='Conexión con Kubernetes no disponible')
    state['last_items'] = rows
    state['last_intent'] = intent
    scope = f' en namespace {namespace}' if namespace else ' en todos los namespaces autorizados'
    return {'session_id':sid,'status':'single','reply':f'Encontré {len(rows)} resultados{scope}.','items':rows,'plan':execution_plan,'nlu':result,'demo':DEMO}
