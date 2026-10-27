"""Management-only frozen entry; execution remains disabled."""
import sys
from protected_runtime.management import ManagementHost, serve_stream
from protected_runtime.release import release_binding


def main():
    if sys.argv[1:] != ["--management-stdio"]:
        return 2
    host = ManagementHost(release_binding())
    return serve_stream(sys.stdin.buffer, sys.stdout.buffer, host)


if __name__ == "__main__":
    raise SystemExit(main())

