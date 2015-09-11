try:
    from urllib import request as urllib_request
    from urllib import error as urllib_error
except ImportError:
    # python2
    import urllib2 as urllib_request
    import urllib2 as urllib_error

import os
import socket


def geturl_len(url):
    if url.startswith("file:///"):
        path = url[len("file://"):]
        return os.stat(path).st_size
    if os.path.exists(url):
        return os.stat(url).st_size

    request = urllib_request.Request(url)
    request.get_method = lambda: 'HEAD'
    response = urllib_request.urlopen(request)
    return int(response.headers.get('content-length', 0))


def geturl_text(url, headers=None, data=None):
    return geturl(url, headers, data).decode()


def geturl(url, headers=None, data=None):
    def_headers = {}

    if headers is not None:
        def_headers.update(headers)

    headers = def_headers

    try:
        req = urllib_request.Request(url=url, data=data, headers=headers)
        r = urllib_request.urlopen(req).read()
        # python2, we want to return bytes, which is what python3 does
        if isinstance(r, str):
            return bytes(r.decode())
        return r
    except urllib_error.HTTPError as exc:
        myexc = UrlError(exc, code=exc.code, headers=exc.headers, url=url,
                         reason=exc.reason)
    except Exception as exc:
        myexc = UrlError(exc, code=None, headers=None, url=url,
                         reason="unknown")
    raise myexc


class UrlError(IOError):
    def __init__(self, cause, code=None, headers=None, url=None, reason=None):
        IOError.__init__(self, str(cause))
        self.cause = cause
        self.code = code
        self.headers = headers
        if self.headers is None:
            self.headers = {}
        self.url = url
        self.reason = reason

    def __str__(self):
        if isinstance(self.cause, urllib_error.HTTPError):
            msg = "http error: %s" % self.cause.code
        elif isinstance(self.cause, urllib_error.URLError):
            msg = "url error: %s" % self.cause.reason
        elif isinstance(self.cause, socket.timeout):
            msg = "socket timeout: %s" % self.cause
        else:
            msg = "Unknown Exception: %s" % self.cause
        return "[%s] " % self.url + msg
