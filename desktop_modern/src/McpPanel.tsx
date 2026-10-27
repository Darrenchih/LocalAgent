import { useCallback, useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { open } from '@tauri-apps/plugin-dialog';
import { useTranslation } from 'react-i18next';

type Server={id:string;name:string;executable:string;args:string[];state:'connected'|'disconnected'|'error';error:string|null;tools:{name:string;description:string;inputSchema:Record<string,unknown>}[]};
const request=<T,>(action:string,params:Record<string,unknown>={})=>invoke<T>('discovery_request',{action,params});
function errorKey(code:string){
  if(['server_timeout','server_write_timeout'].includes(code))return 'timeoutError';
  if(code==='server_disconnected')return 'disconnectedError';
  if(code==='executable_changed')return 'changedError';
  if(['protocol_invalid','protocol_version_invalid','response_id_invalid','tools_capability_missing','tools_schema_invalid','pagination_invalid','pagination_limit_exceeded','message_limit_exceeded','response_too_large'].includes(code))return 'protocolError';
  return 'connectionError';
}
export default function McpPanel(){
  const {t}=useTranslation();
  const [servers,setServers]=useState<Server[]>([]);const [version,setVersion]=useState('');const [busy,setBusy]=useState(false);const [failed,setFailed]=useState(false);
  const [pending,setPending]=useState<Server|null>(null);
  const [id,setId]=useState('');const [name,setName]=useState('');const [executable,setExecutable]=useState('');const [args,setArgs]=useState('[]');
  const reload=useCallback(async()=>{const [health,items]=await Promise.all([request<{version:string}>('health'),request<Server[]>('list_servers')]);setVersion(health.version);setServers(items);},[]);
  useEffect(()=>{let active=true;let pending=false;const refresh=async()=>{if(pending||busy)return;pending=true;try{if(active)await reload();}catch{if(active)setFailed(true);}finally{pending=false;}};void refresh();const timer=setInterval(()=>void refresh(),2000);return()=>{active=false;clearInterval(timer);};},[reload,busy]);
  const run=async(action:()=>Promise<unknown>)=>{setBusy(true);setFailed(false);try{await action();await reload();}catch{setFailed(true);}finally{setBusy(false);}};
  const save=()=>run(async()=>{const parsed:unknown=JSON.parse(args);if(!Array.isArray(parsed)||parsed.some(v=>typeof v!=='string'))throw new Error('invalid');await request('save_server',{value:{id,name,executable,args:parsed}});});
  const connect=(server:Server)=>setPending(server);
  return <div className="mcp-panel"><h3>{t('mcp.title')}</h3><p className="notice">{t('mcp.safety')}</p><p>{t('mcp.runtime')}: {version||'—'}</p><p className="notice">{t('mcp.incomplete')}</p>{failed&&<p className="error-banner" role="alert">{t('mcp.error')}</p>}
    <div className="card mcp-form"><label className="field"><span>{t('mcp.id')}</span><input value={id} onChange={e=>setId(e.target.value)} maxLength={48}/></label><label className="field"><span>{t('mcp.name')}</span><input value={name} onChange={e=>setName(e.target.value)} maxLength={100}/></label><label className="field"><span>{t('mcp.executable')}</span><input value={executable} onChange={e=>setExecutable(e.target.value)}/></label><button disabled={busy} onClick={()=>void open({multiple:false,filters:[{name:'Executable',extensions:['exe']}]}).then(p=>{if(typeof p==='string')setExecutable(p);}).catch(()=>setFailed(true))}>{t('mcp.choose')}</button><label className="field"><span>{t('mcp.args')}</span><textarea rows={3} value={args} onChange={e=>setArgs(e.target.value)}/></label><button disabled={busy||!id||!name||!executable} onClick={()=>void save()}>{t('common.save')}</button></div>
    {servers.map(server=><div className="card" key={server.id}><div className="card-line"><strong>{server.name}</strong><span className="pill">{t(server.state==='error'?'mcp.errorState':`mcp.${server.state}`)}</span></div><p><code>{server.executable}</code></p><pre>{JSON.stringify(server.args)}</pre>{server.error&&<p>{t(`mcp.${errorKey(server.error)}`)}</p>}<div className="row-actions"><button disabled={busy} onClick={()=>{setId(server.id);setName(server.name);setExecutable(server.executable);setArgs(JSON.stringify(server.args));}}>{t('common.edit')}</button>{server.state==='connected'?<button disabled={busy} onClick={()=>void run(()=>request('disconnect',{id:server.id}))}>{t('mcp.disconnect')}</button>:<button disabled={busy} onClick={()=>connect(server)}>{t('mcp.connect')}</button>}</div>{server.tools.map((tool,index)=><details key={`${tool.name}-${index}`}><summary>{tool.name}</summary><p>{tool.description}</p><pre>{JSON.stringify(tool.inputSchema,null,2)}</pre></details>)}</div>)}
    {!servers.length&&<p>{t('mcp.empty')}</p>}
    {pending&&<div className="modal-backdrop"><section role="dialog" aria-modal="true" aria-label={t('mcp.confirmTitle')} className="settings-modal mcp-confirm"><header><h3>{t('mcp.confirmTitle')}</h3></header><div className="mcp-confirm-body"><p>{t('mcp.confirm')}</p><pre>{pending.executable}</pre><pre>{JSON.stringify(pending.args)}</pre><div className="row-actions"><button onClick={()=>setPending(null)}>{t('common.cancel')}</button><button onClick={()=>{const chosen=pending;setPending(null);void run(()=>request('connect',{id:chosen.id}));}}>{t('mcp.confirmConnect')}</button></div></div></section></div>}
  </div>;
}
