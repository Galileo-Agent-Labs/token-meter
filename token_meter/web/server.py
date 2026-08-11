"""Local-only HTTP server lifecycle."""

import threading


def serve_local(*, handler_class, server_class, port, background=(), output=print):
    for target in background:
        threading.Thread(target=target, daemon=True).start()
    server = server_class(("127.0.0.1", int(port)), handler_class)
    output("Token Meter live -> http://localhost:{}".format(port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        output("\nstopped.")
