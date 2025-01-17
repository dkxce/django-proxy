import re
import requests
import socket
from django.http import HttpResponse
from django.http import QueryDict
from django.views.decorators.csrf import csrf_exempt
try:
    from urlparse import urlparse
except:
    from urllib.parse import urlparse
    
version = '1.2.2-dkxce'
module = 'django-proxy'
allow_redirect = True
allow_request_content_headers  = True
allow_response_content_headers = True

def get_client_ip(request):
    try: # pip install django-ipware # https://github.com/un33k/django-ipware #
        from ipware import get_client_ip as gip
        ip, is_routable = gip(request)
        if ip and is_routable: return ip
    except: pass
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for: return x_forwarded_for.split(',')[0]
    else: return request.META.get('REMOTE_ADDR')        

def proxy_view(request, url, requests_args=None, *args, **kwargs):
    """
    Forward as close to an exact copy of the request as possible along to the
    given url.  Respond with as close to an exact copy of the resulting
    response as possible.

    If there are any additional arguments you wish to send to requests, put
    them in the requests_args dictionary.

    kwargs: HOST=, IP=, NOREDIRECT=True, allow_request_content_headers=True|False, allow_response_content_headers=True|False
    """
    requests_args = (requests_args or {}).copy()
    headers = get_headers(request.META)
    params = request.GET.copy()
    remote_ip = get_client_ip(request)
    follow_redirect = True

    # Remote IP Headers
    headers["X-Real-IP"] = remote_ip
    headers["X-Forwarded-For"] = remote_ip

    # Custom Headers
    #headers["Host"] = "..."
    for key, value in kwargs.items():
        if key.upper() == "HOST":
            headers["Host"] = value
            headers["X-Forwarded-Host"] = value
        if key.upper() == "IP":
            headers["X-Real-IP"] = value
            headers["X-Forwarded-For"] = value
        if key.upper() == "NOREDIRECT":
            follow_redirect = not value

    if 'headers' not in requests_args:
        requests_args['headers'] = {}
    if 'data' not in requests_args:
        requests_args['data'] = request.body
    if 'params' not in requests_args:
        requests_args['params'] = QueryDict('', mutable=True)

    # Overwrite any headers and params from the incoming request with explicitly
    # specified values for the requests library.
    headers.update(requests_args['headers'])
    params.update(requests_args['params'])

    # If there's a content-length header from Django, it's probably in all-caps
    # and requests might not notice it, so just remove it.
    for key in list(headers.keys()):
        if key.lower() == 'content-length':
            del headers[key]
            
    curr_allow_request_content_headers = allow_request_content_headers
    if (allow := kwargs.get('allow_request_content_headers')) != None: curr_allow_request_content_headers = allow
    curr_allow_response_content_headers = allow_response_content_headers
    if (allow := kwargs.get('allow_response_content_headers')) != None: curr_allow_response_content_headers = allow

    if not curr_allow_request_content_headers:
        if headers.get('ACCEPT-ENCODING'): del headers['ACCEPT-ENCODING']
        if headers.get('Accept-Encoding'): del headers['Accept-Encoding']
        if headers.get('accept-encoding'): del headers['accept-encoding']

    requests_args['headers'] = headers
    requests_args['params'] = params

    response = requests.request(request.method, url, **requests_args)

    if allow_redirect and follow_redirect and response.status_code in [301,302,303,304] and url != response.url:
        response = requests.request(request.method, response.url, **requests_args)

    proxy_response = HttpResponse(
        response.content,
        status=response.status_code)

    excluded_headers = set([
        # Hop-by-hop headers
        # ------------------
        # Certain response headers should NOT be just tunneled through.  These
        # are they.  For more info, see:
        # http://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html#sec13.5.1
        'connection', 'keep-alive', 'proxy-authenticate',
        'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
        'upgrade',

        # Although content-encoding is not listed among the hop-by-hop headers,
        # it can cause trouble as well.  Just let the server set the value as
        # it should be.
        'zzzzzzzzz' if curr_allow_response_content_headers else 'content-encoding',

        # Since the remote server may or may not have sent the content in the
        # same encoding as Django will, let Django worry about what the length
        # should be.
        'zzzzzzzzz' if curr_allow_response_content_headers else  'content-length',
    ])
    for key, value in response.headers.items():
        if key.lower() in excluded_headers:
            continue
        elif key.lower() == 'location':
            # If the location is relative at all, we want it to be absolute to
            # the upstream server.
            proxy_response[key] = make_absolute_location(response.url, value)
        else:
            proxy_response[key] = value
            
    uri = urlparse(url)
    try: HOSTNAME = socket.gethostname()
    except: HOSTNAME = 'localhost'    
    proxy_response["Via"] = f"{version} {module} {HOSTNAME}"
    proxy_response["Forwarded"] = f"by={version},{module};for={remote_ip};host={HOSTNAME};proto={uri.scheme}"

    return proxy_response


def make_absolute_location(base_url, location):
    """
    Convert a location header into an absolute URL.
    """
    absolute_pattern = re.compile(r'^[a-zA-Z]+://.*$')
    if absolute_pattern.match(location):
        return location

    parsed_url = urlparse(base_url)

    if location.startswith('//'):
        # scheme relative
        return parsed_url.scheme + ':' + location

    elif location.startswith('/'):
        # host relative
        return parsed_url.scheme + '://' + parsed_url.netloc + location

    else:
        # path relative
        return parsed_url.scheme + '://' + parsed_url.netloc + parsed_url.path.rsplit('/', 1)[0] + '/' + location

    return location


def get_headers(environ):
    """
    Retrieve the HTTP headers from a WSGI environment dictionary.  See
    https://docs.djangoproject.com/en/dev/ref/request-response/#django.http.HttpRequest.META
    """
    headers = {}
    for key, value in environ.items():
        # Sometimes, things don't like when you send the requesting host through.
        if key.startswith('HTTP_') and key != 'HTTP_HOST':
            headers[key[5:].replace('_', '-')] = value
        elif key in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            headers[key.replace('_', '-')] = value

    return headers

@csrf_exempt
def proxy_nopath(request, *args, **kwargs):
    """
    No Pass Path
    Usage: path("", proxy_nopath),        
    Usage: re_path('(^(?!(admin|accounts|api)).*$)', proxy_nopath),
    """
    return proxy_view(request, 'http://localhost:8080/', None)

@csrf_exempt
def proxy_default(request, path, *args, **kwargs):
    """    
    Pass Path
    Usage: re_path('^(?P<path>.*)$', default_proximizer),
    Usage: re_path('^proxy(?P<path>/.*)$', proxy_default),
    """
    return proxy_view(request, f'http://localhost:8080{path}', None)

