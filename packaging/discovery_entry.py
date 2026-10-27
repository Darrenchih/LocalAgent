"""Frozen discovery-only entry. Not a Host, Gate, Broker or tool executor."""
import sys
from agent_discovery.server import main
for stream in (sys.stdin,sys.stdout,sys.stderr):
    if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='strict')
if __name__=='__main__':raise SystemExit(main())
