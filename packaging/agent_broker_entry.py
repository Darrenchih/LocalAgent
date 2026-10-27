import sys
from protected_runtime.agent_broker import serve
if __name__ == '__main__':
    raise SystemExit(serve(sys.stdin.buffer,sys.stdout.buffer) if sys.argv[1:] == ['--agent-stdio'] else 2)
