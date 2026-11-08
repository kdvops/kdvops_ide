import os
import re
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
from .generators import analyze_yaml, generate_azure_pipeline, generate_from_message, generate_kubernetes_manifest
from .security import scan_content
from .devsecops import evaluate_devsecops

app = FastAPI(title='Asistente Conversacional DevOps basado en reglas', version='0.6.0')
DEMO = os.getenv('DEMO_MODE','true').lower() == 'true'
reader = DemoReader() if DEMO else None
sessions = {}
SESSION_TTL = 3600
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

class Message(BaseModel):
    message: str = Field(min_length=1,max_length=500)
    session_id: str | None = None


class GenerateRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    kind: str | None = None


class AnalyzeRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    kind: str | None = None


class ScanRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    filename: str = Field(default='input.yaml', min_length=1, max_length=200)


class DevSecOpsRequest(BaseModel):
    content: str | None = Field(default=None, max_length=200_000)
    filename: str = Field(default='pipeline.yml', min_length=1, max_length=200)
    files: list[dict[str, str]] = Field(default_factory=list, max_length=300)

@app.get('/')
def home():
    return FileResponse(Path(__file__).resolve().parent.parent / 'static' / 'index.html')

@app.get('/healthz')
def health():
    return {'ok':True,'demo':DEMO}


def _artifact_payload(artifact):
    return {
        'kind': artifact.kind,
        'filename': artifact.filename,
        'content': artifact.content,
        'warnings': artifact.warnings,
    }


@app.post('/api/generate')
def generate(request: GenerateRequest):
    try:
        request_scan = scan_content(request.message, 'request.txt')
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f'Secret scan unavailable: {exc}') from exc
    if not request_scan['clean']:
        return {'status':'blocked', 'reply':'Solicitud bloqueada porque contiene posibles secretos.', 'scan':request_scan}
    kind = (request.kind or '').strip().lower()
    if kind == 'azure_pipeline':
        artifact = generate_azure_pipeline(request.message)
    elif kind in {'kubernetes_manifest', 'k8s_manifest'}:
        artifact = generate_kubernetes_manifest(request.message)
    else:
        artifact = generate_from_message(request.message)
    if artifact is None:
        raise HTTPException(status_code=422, detail='No se pudo determinar el tipo de artefacto; usa azure_pipeline o kubernetes_manifest')
    try:
        scan = scan_content(artifact.content, artifact.filename)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f'Secret scan unavailable: {exc}') from exc
    if not scan['clean']:
        return {'status':'blocked', 'reply':'El artefacto fue bloqueado porque contiene posibles secretos.', 'scan':scan}
    return {'status':'generated', 'artifact':_artifact_payload(artifact), 'scan':scan}


@app.post('/api/analyze')
def analyze(request: AnalyzeRequest):
    try:
        request_scan = scan_content(request.content, 'analysis.yaml')
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f'Secret scan unavailable: {exc}') from exc
    if not request_scan['clean']:
        return {'status':'blocked', 'reply':'Análisis bloqueado porque contiene posibles secretos.', 'scan':request_scan}
    return {'status':'analyzed', 'analysis':analyze_yaml(request.content, request.kind)}


@app.post('/api/security/scan')
def security_scan(request: ScanRequest):
    try:
        result = scan_content(request.content, request.filename)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {'status':'clean' if result['clean'] else 'blocked', 'scan':result}


@app.post('/api/devsecops/evaluate')
def evaluate_devsecops_flow(request: DevSecOpsRequest):
    inputs = list(request.files)
    if request.content:
        inputs.append({'filename': request.filename, 'content': request.content})
    if not inputs:
        raise HTTPException(status_code=422, detail='Envía content o files con al menos un archivo')
    total_size = sum(len(item.get('content', '').encode('utf-8')) for item in inputs)
    if total_size > 500_000 or any(not item.get('filename') or not item.get('content') for item in inputs):
        raise HTTPException(status_code=422, detail='Los archivos deben tener nombre y contenido; límite total 500 KB')
    clean_files = []
    for item in inputs:
        try:
            result = scan_content(item['content'], item['filename'])
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=f'Secret scan unavailable: {exc}') from exc
        if not result['clean']:
            return {'status':'blocked', 'reply':'Evaluación bloqueada porque contiene posibles secretos.', 'file':item['filename'], 'scan':result}
        clean_files.append(item)
    combined = '\n\n'.join(f"# FILE: {item['filename']}\n{item['content']}" for item in clean_files)
    evaluation = evaluate_devsecops(combined, ', '.join(item['filename'] for item in clean_files))
    evaluation['files'] = [{'filename': item['filename'], 'bytes': len(item['content'].encode('utf-8'))} for item in clean_files]
    evaluation['file_count'] = len(clean_files)
    evaluation['summary'] += f" Se evaluaron {len(clean_files)} archivos como un mismo flujo."
    return {'status':'evaluated', 'evaluation':evaluation, 'scan':{'clean':True, 'files':len(clean_files), 'scanner':'gitleaks', 'version':'8.30.1'}}
    # Kept unreachable intentionally: all inputs are normalized above.

@app.post('/api/chat')
def chat(request: Message):
    try:
        request_scan = scan_content(request.message, 'request.txt')
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f'Secret scan unavailable: {exc}') from exc
    if not request_scan['clean']:
        return {
            'status': 'blocked',
            'reply': 'Solicitud bloqueada porque contiene posibles secretos. Retira el secreto y vuelve a intentarlo.',
            'scan': request_scan,
            'demo': DEMO,
        }
    now = time.time()
    for key, value in list(sessions.items()):
        if now - value['last_seen'] > SESSION_TTL:
            del sessions[key]
    requested_sid = request.session_id if request.session_id and SESSION_ID_PATTERN.fullmatch(request.session_id) else None
    sid = requested_sid or secrets.token_urlsafe(18)
    state = sessions.setdefault(sid,{'namespace':None,'entity':None,'last_items':[],'last_intent':None,'last_seen':now})
    state['last_seen'] = now
    result = parse(request.message, state)
    artifact = generate_from_message(request.message)
    if artifact is not None:
        try:
            scan = scan_content(artifact.content, artifact.filename)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=f'Secret scan unavailable: {exc}') from exc
        if not scan['clean']:
            return {'session_id':sid, 'status':'blocked', 'reply':'El artefacto fue bloqueado porque contiene posibles secretos.', 'scan':scan, 'demo':DEMO}
        return {
            'session_id': sid,
            'status': 'generated',
            'reply': f"Generé {artifact.filename}. Revisa las advertencias antes de usarlo.",
            'artifact': _artifact_payload(artifact),
            'scan': scan,
            'nlu': {'status':'matched', 'intent':artifact.kind, 'namespace':None, 'entity':None},
            'demo': DEMO,
        }
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
    count = len(rows)
    noun = 'resultado' if count == 1 else 'resultados'
    return {'session_id':sid,'status':'single','reply':f'Encontré {count} {noun}{scope}.','items':rows,'plan':execution_plan,'nlu':result,'demo':DEMO}
