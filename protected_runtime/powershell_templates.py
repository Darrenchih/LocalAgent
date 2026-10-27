"""Fixed trusted PowerShell templates. Metadata never confers execution authority."""
from .management import ProtocolError
TEMPLATES={
 'test_json':{'enabled':True,'description':'Validate a workspace JSON object against expected fields.','timeout_seconds':5},
 'project_tests':{'enabled':False,'description':'Reserved; no project test execution is enabled.','timeout_seconds':None},
}
_COMMANDS={'test_json':"$ErrorActionPreference='Stop'; $v=[Console]::In.ReadToEnd()|ConvertFrom-Json; if($null -eq $v){exit 2}; [Console]::Out.Write('json-valid')"}
def catalog(): return [{'name':name,**metadata} for name,metadata in TEMPLATES.items()]
def command(name):
    if name not in _COMMANDS or not TEMPLATES.get(name,{}).get('enabled'): raise ProtocolError('TEMPLATE_DENIED')
    return _COMMANDS[name]
