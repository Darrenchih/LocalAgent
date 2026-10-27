"""Desktop-only discovery stdio service. EOF closes every owned MCP job."""
import argparse,json,sys
from pathlib import Path
from .service import DiscoveryService,DiscoveryError,strict_json

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stdio',action='store_true',required=True);parser.add_argument('--db',required=True)
    args=parser.parse_args();service=DiscoveryService(Path(args.db).parent)
    try:
        while True:
            line=sys.stdin.readline(65537)
            if not line:break
            ident='unknown'
            try:
                if len(line)>65536:break
                req=strict_json(line)
                if not isinstance(req,dict) or set(req)!={'version','id','action','params'} or req['version']!='1.0' or not isinstance(req['id'],str) or not 1<=len(req['id'])<=128:raise DiscoveryError('request_invalid')
                ident=req['id'];result=service.dispatch(req['action'],req['params']);response={'version':'1.0','id':ident,'ok':True,'result':result}
            except Exception as error:
                response={'version':'1.0','id':ident,'ok':False,'error':{'code':'discovery_error','message':str(error) if isinstance(error,DiscoveryError) else 'discovery_failed'}}
            print(json.dumps(response,ensure_ascii=True),flush=True)
    finally:service.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
