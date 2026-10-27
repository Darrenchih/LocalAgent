"""Pure builtin tool schemas and canonical argument validation."""
import json
import re
import math
import struct
from .management import ProtocolError

HEX = re.compile(r'[0-9a-f]{64}\Z')

def path_parts(path, root_allowed=False):
    if root_allowed and path == '.': return []
    if type(path) is not str or not path or len(path)>200: raise ProtocolError('PATH_DENIED')
    values=path.replace('\\','/').split('/')
    devices={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(10)),*(f'LPT{i}' for i in range(10))}
    for p in values:
        if (not re.fullmatch(r'[A-Za-z0-9_. -]+',p) or p in ('.','..') or p.endswith(('.', ' ')) or p.split('.')[0].rstrip(' .').upper() in devices):
            raise ProtocolError('PATH_DENIED')
    return values

def validate_arguments(name,args):
    keys={'read_file':('path',),'write_file':('path','content','expected_sha256'),'list_directory':('path',),'create_directory':('path',),'search_text':('path','query'),'powershell':('template','path','expected')}
    if type(name) is not str or name not in keys: raise ProtocolError('TOOL_DENIED')
    if type(args) is not dict or set(args)!=set(keys[name]): raise ProtocolError('INVALID_ARGUMENT')
    result=dict(args)
    path_parts(args['path'],name in ('list_directory','search_text'))
    result['path']=args['path'].replace('\\','/')
    if name=='write_file':
        if type(args['content']) is not str or len(args['content'].encode('utf-8'))>32768: raise ProtocolError('RESOURCE_LIMIT')
        expected=args['expected_sha256']
        if expected is None: expected='absent'
        if type(expected) is not str or (expected!='absent' and not HEX.fullmatch(expected)): raise ProtocolError('INVALID_ARGUMENT')
        result['expected_sha256']=expected
    if name=='search_text':
        if type(args['query']) is not str or not args['query'] or len(args['query'])>200 or '\n' in args['query'] or '\r' in args['query']: raise ProtocolError('INVALID_ARGUMENT')
    if name=='powershell':
        if type(args['template']) is not str or args['template']!='test_json': raise ProtocolError('TEMPLATE_DENIED')
        if type(args['expected']) is not dict: raise ProtocolError('INVALID_ARGUMENT')
        try:
            if len(json.dumps(args['expected'],allow_nan=False).encode())>32768: raise ProtocolError('RESOURCE_LIMIT')
        except (ValueError,TypeError): raise ProtocolError('INVALID_ARGUMENT')
    return result

def schema(name,description,properties):
    return {'type':'function','function':{'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}}}
P={'type':'string','description':'Exact case relative path inside selected workspace; use / separators. No absolute paths, aliases, or .. .'}
D=dict(P,description=P['description']+' Use . for selected root.')
TOOLS=[
 schema('read_file','Read one UTF-8 file, at most 32 KiB.',{'path':P}),
 schema('write_file','Create or replace one UTF-8 file, at most 32 KiB. Existing files require their exact read SHA-256.',{'path':P,'content':{'type':'string'},'expected_sha256':{'type':['string','null'],'description':'For a new file use null or the literal string absent: file MUST NOT exist. Otherwise use exact lowercase 64-character read SHA-256.'}}),
 schema('list_directory','List at most 128 immediate entries. Unsafe entries are blocked.',{'path':D}),
 schema('create_directory','Create one directory whose parent already exists.',{'path':P}),
 schema('search_text','Literal case-sensitive bounded recursive UTF-8 text search; at most 100 matches.',{'path':D,'query':{'type':'string','description':'Nonempty literal single-line text, at most 200 characters.'}}),
 schema('powershell','Run only the enabled fixed test_json validation template. No scripts or commands.',{'template':{'type':'string','enum':['test_json']},'path':P,'expected':{'type':'object','description':'Expected JSON object fields and values.'}})
]

# Descriptive target roles for the existing tools, not execution authority.
# Broker's handle/path checks remain the final filesystem-kind decision.
TOOL_PATH_KINDS = {
    'read_file': 'file', 'write_file': 'file', 'powershell': 'file',
    'list_directory': 'directory', 'create_directory': 'directory', 'search_text': 'directory',
}


def grant_canonical(value):
    """Injective typed digest encoding shared with the native approval owner.

    This is deliberately separate from the JSON wire encoding. Binary64 values
    retain their exact bits, including negative zero, across JSON implementations.
    """
    if value is None: return b'n'
    if type(value) is bool: return b't' if value else b'f'
    if type(value) is str:
        raw=value.encode('utf-8')
        return b's'+str(len(raw)).encode('ascii')+b':'+raw
    if type(value) is int: return b'i'+str(value).encode('ascii')+b';'
    if type(value) is float:
        if not math.isfinite(value): raise ProtocolError('INVALID_ARGUMENT')
        return b'd'+struct.pack('>d',value).hex().encode('ascii')+b';'
    if type(value) is list:
        return b'a'+str(len(value)).encode('ascii')+b'['+b''.join(grant_canonical(item) for item in value)+b']'
    if type(value) is dict:
        if any(type(key) is not str for key in value): raise ProtocolError('INVALID_ARGUMENT')
        keys=sorted(value,key=lambda key:key.encode('utf-8'))
        return b'o'+str(len(keys)).encode('ascii')+b'{'+b''.join(grant_canonical(key)+grant_canonical(value[key]) for key in keys)+b'}'
    raise ProtocolError('INVALID_ARGUMENT')
