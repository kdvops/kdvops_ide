"""Rule-based DevOps intent parser. No ML or LLM involved."""
import json
import re
import unicodedata
from pathlib import Path
from rapidfuzz import fuzz

CATALOG = json.loads((Path(__file__).resolve().parent.parent / 'catalog' / 'vocabulary.json').read_text(encoding='utf-8'))
RULES = json.loads((Path(__file__).resolve().parent.parent / 'catalog' / 'intent_rules.json').read_text(encoding='utf-8'))
EXAMPLES = CATALOG['intents']
ALIASES = CATALOG['aliases']
VOCABULARY = {w for words in CATALOG['domains'].values() for w in words}
STOP = {'el','la','los','las','de','del','en','un','una','por','para','que','me','mi','mis','quiero','puedes','podrias','favor','estan','esta','hay','al','y','a','con','como','cual','cuales','dime','donde','se','lo'}
WRITES = set(CATALOG['write_intents'])


def normalize(text):
    text = ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if unicodedata.category(c) != 'Mn')
    text = re.sub(r'[^\w\s./:-]', ' ', text)
    words = [ALIASES.get(w, w) for w in text.split()]
    return ' '.join(words)


def _contains(text, phrase):
    return re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text) is not None


def _token_matches(tokens, choices):
    """Strict typo tolerance only for domain terms, not destructive verbs."""
    for term in choices:
        for token in tokens:
            if token == term or (len(term) >= 5 and len(token) >= 5 and fuzz.ratio(token, term) >= 82):
                return True
    return False


def parse(text, context=None):
    context = context or {}
    msg = normalize(text)
    tokens = msg.split()
    meaningful = [t for t in tokens if t not in STOP]
    namespace = None
    m = re.search(r'\b(?:namespace|ns)\s+([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\b', msg)
    if m:
        namespace = m.group(1)
    elif re.search(r'\b(?:ahi|alli|ese namespace|mismo namespace|en ese entorno)\b', msg):
        namespace = context.get('namespace')
    entity = None
    em = re.search(r'\b(?:deployment|deployments|deploy|pod|pods|servicio|servicios|service|aplicacion|app)\s+(?:llamado |llamada |de |del )?([a-z0-9][a-z0-9.-]*)', msg)
    if em and em.group(1) not in {'en','que','con','fallando','fallan','errores','error','no','estan','esta','del','los','las'}:
        entity = em.group(1)
    if not entity and re.search(r'\b(?:primero|primera|segundo|segunda|anterior)\b', msg):
        items = context.get('last_items', [])
        idx = 1 if re.search(r'\bsegund[oa]\b', msg) else 0
        if len(items) > idx:
            entity = items[idx].get('name')
            namespace = namespace or items[idx].get('namespace')
    if not entity and re.search(r'\b(?:ese|el mismo|la misma)\b', msg):
        entity = context.get('entity')

    # Safety guard: commands to modify infrastructure never fall through to a read intent.
    write_verbs = {'reinicia','reiniciar','reinicien','reiniciame','restart','reboot','borra','borrar','elimina','eliminar','delete','remove','escala','escalar','scale','despliega','desplegar','deploy','aplica','aplicar','rollback','revierte','revertir','deten','detener','stop','mata','matar','actualiza','actualizar'}
    if any(t in write_verbs for t in tokens):
        intent = ('restart_deployment' if any(t.startswith('reinici') or t == 'restart' for t in tokens) else
                  'scale_deployment' if any(t in {'escala','escalar','scale'} for t in tokens) else
                  'delete_resource' if any(t in {'borra','borrar','elimina','eliminar','delete','remove','mata','matar'} for t in tokens) else
                  'write_operation')
        return {'status':'unsupported','intent':intent,'score':100,'namespace':namespace,'entity':entity,'candidates':[], 'matched_terms':[],'domain_matches':[], 'reason':'write_operation_disabled'}

    scores = []
    for intent, rule in RULES.items():
        subjects = rule.get('subjects', [])
        triggers = rule.get('triggers', [])
        negatives = rule.get('negative', [])
        has_subject = _token_matches(meaningful, [normalize(x) for x in subjects]) if subjects else True
        if intent == 'list_namespaces' and re.search(r'\b(?:pod|pods|deployment|deployments|servicio|servicios|events|eventos)\b', msg):
            has_subject = False
        has_trigger = any(_contains(msg, normalize(p)) for p in triggers)
        has_negative = any(_contains(msg, normalize(p)) for p in negatives)
        # Only use fuzzy phrase similarity when a domain subject is present (except help).
        example_score = max((fuzz.token_set_ratio(msg, normalize(p)) for p in EXAMPLES.get(intent, [])), default=0)
        score = 0
        if has_subject and not has_negative:
            score = (55 if subjects else 55) + (38 if has_trigger else 0)
            if not has_trigger and example_score >= 85:
                score = 78 + min(12, (example_score - 85) / 2)
        if score:
            scores.append((intent, round(score, 1)))
    scores.sort(key=lambda x: x[1], reverse=True)
    matched_terms = sorted(set(meaningful) & VOCABULARY)
    domains = sorted(k for k,words in CATALOG['domains'].items() if set(meaningful) & set(words))
    base = {'namespace':namespace,'entity':entity,'matched_terms':matched_terms,'domain_matches':domains}
    if not scores or scores[0][1] < 75:
        return dict(base, status='unknown',intent=None,score=scores[0][1] if scores else 0,candidates=[])
    first = scores[0]
    # Tied independent intents must be clarified, never guessed.
    if len(scores) > 1 and first[1] - scores[1][1] < 7:
        return dict(base,status='ambiguous',intent=None,score=first[1],candidates=[x[0] for x in scores if first[1]-x[1]<7][:3])
    status = 'matched' if first[0] in CATALOG['supported_intents'] else 'unsupported'
    return dict(base,status=status,intent=first[0],score=first[1],candidates=[])
