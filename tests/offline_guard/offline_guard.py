"""Test-only fail-closed network and credential guard; loopback remains available. Copied from cgs-lead-workspace (MVP)."""
import ipaddress
import os
import socket


def install():
    if getattr(socket, '_cgs_offline_guard', False):
        return
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    for key in ('GEMINI_API_KEY', 'TAVILY_API_KEY', 'GEMINI_MODEL', 'OPENAI_API_KEY', 'OPENAI_MODEL', 'ANTHROPIC_API_KEY'):
        os.environ.pop(key, None)
    try:
        import dotenv
        import dotenv.main
    except ImportError:
        pass
    else:
        def disabled_dotenv(*args, **kwargs):
            return False
        dotenv.load_dotenv = disabled_dotenv
        dotenv.main.load_dotenv = disabled_dotenv
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_getaddrinfo = socket.getaddrinfo

    def local(host):
        if host in ('localhost', '::1'):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError):
            return False

    def check(address):
        if isinstance(address, tuple) and not local(address[0]):
            raise PermissionError('Offline test guard blocks non-loopback network access')

    def connect(sock, address):
        check(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)

    def sendto(sock, *args):
        check(args[-1])
        return original_sendto(sock, *args)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not local(host):
            raise PermissionError('Offline test guard blocks external DNS')
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo
    socket._cgs_offline_guard = True
