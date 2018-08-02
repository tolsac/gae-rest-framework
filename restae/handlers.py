"""
Handlers modules
"""
import json
import re
import traceback

import webapp2
import logging

from webob import Response

from exceptions import NotFound, NotAuthorized, Forbidden, MissingParameter, BadRequest, MissingBody
from response import CorsResponse, JsonResponse
from helpers import get_object_from_urlsafe, get_middlewares, cached_property, get_key_from_urlsafe, \
    get_model_class_from_query, load_class
from restae.conf import DEFAULT_PAGINATION_CLASS
from restae.exceptions import DispatchError


class BaseHandler(webapp2.RequestHandler):
    """
    BaseHandler with some intelligence & automation
    """
    model = None
    lookup_field = None
    query_param = None

    def __init__(self, *args, **kwargs):
        super(BaseHandler, self).__init__(*args, **kwargs)
        self.route_args = {}
        self.user = None

    @cached_property
    def middlewares(self):
        """
        Cached property that load all middlewares

        :return: :py:class:`list` of :py:class:`Middleware` instances
        """
        return get_middlewares()

    def dispatch(self):
        """
        Function called to make the dispatch job.
        Calls internally :py:meth:`restae.handlers.BaseHandler.apply_dispatch`

        :return: :py:class:`webob.Response`
        """
        return self.apply_dispatch()

    def apply_dispatch(self):
        """
        Does the hole mechanism of dispatch.

        Loops over all middlewares before and after the dispatch method call


        .. code-block:: python

            for middleware in self.middlewares:
                middleware.process_request(self.request)

            self.route_args = self.get_route_args()
            response = self.do_dispatch()

            for middleware in self.middlewares:
                middleware.process_response(self.request, response)

        :return: :py:class:`webob.Response`
        """
        try:
            for middleware in self.middlewares:
                middleware.process_request(self.request)

            self.route_args = self.get_route_args()
            response = self.do_dispatch()

            for middleware in self.middlewares:
                middleware.process_response(self.request, response)

            return response
        except NotFound as nf:
            return JsonResponse(status=404, data=str(nf) or 'Not found')
        except NotAuthorized as na:
            return JsonResponse(status=403, data=str(na) or 'Not authorized')
        except Forbidden as fo:
            return JsonResponse(status=401, data=str(fo) or 'Forbidden')
        except MissingParameter as mp:
            return JsonResponse(status=400, data=str(mp) or 'Missing parameter')
        except BadRequest as br:
            return JsonResponse(status=400, data=str(br) or 'Bad request')
        except MissingBody as mb:
            return JsonResponse(status=400, data=str(mb) or 'Request is missing a body')
        except ValueError as ve:
            return JsonResponse(status=400, data=str(ve) or 'Value error')
        except DispatchError:
            return Response(status=404)
        except Exception as err:
            logging.error('%s: %s',
                          err.__class__.__name__,
                          str(err))
            logging.error(traceback.format_exc())
            return self.handle_exception(err, self.app.debug)

    def do_dispatch(self):
        """
        Function that does the dispatch job

        :return: :py:class:`webob.Response`
        """
        return super(BaseHandler, self).dispatch()

    def get_body(self):
        """
        Tries to parse the request body as JSON

        :return: JSON serialized data as :py:class:`dict` instance.
        """
        if self.request.body:
            try:
                json_body = json.loads(self.request.body)
                return json_body
            except Exception as err:
                logging.warning('Request is missing a body: %s -> %s',
                                err.__class__.__name__, str(err))
        raise MissingBody('Request is missing a body')

    def get_route_args(self):
        """
        Parses the route arguments based on the router generated url

        :return: route arguments in a :py:class:`dict` instance.

        For instance if the view url is ``r'/resource/(?P<slug>[^/]+)/'`` and the calling url is ``GET /resource/my-slug/``

        The returned dict will be formatted like this

        .. code-block:: python

           {'slug': 'my-slug'}

        .. note::
            Those route arguments are then available in each of the Handler methods in the self.route_args attribute

        """
        return self.request.route.regex.search(self.request.upath_info).groupdict()

    @classmethod
    def get_extra_actions(cls):
        actions = []

        for _method in cls.__dict__.values():
            if getattr(_method, 'is_dynamic_action', False):
                actions.append(_method)
        return actions


class APIModelBaseHandler(BaseHandler):
    """

    """
    lookup_field = 'urlsafe'
    queryset = None
    serializer_class = None
    pagination_class = load_class(DEFAULT_PAGINATION_CLASS)

    def get_object(self, urlsafe=None, query_param=None, lookup_field=None, raise_exception=True):
        _object = None
        _lookup_field = lookup_field or self.lookup_field
        _query_param = query_param or self.query_param

        if _lookup_field is None and urlsafe is None and _query_param is None:
            if raise_exception is True:
                raise BadRequest
            else:
                return None

        try:
            if urlsafe is None:
                if _lookup_field is not None and _lookup_field in self.route_args:
                    urlsafe_from_request = self.route_args[_lookup_field]
                elif _query_param is not None:
                    urlsafe_from_request = self.request.GET.get(_query_param, None)
                else:
                    urlsafe_from_request = None

                if urlsafe_from_request is None and raise_exception is False:
                    return None

                if urlsafe_from_request is None:
                    raise MissingParameter('Parameter {} is missing from request'.format(
                        _lookup_field or _query_param
                    ))
                else:
                    urlsafe = urlsafe_from_request

            _object = get_object_from_urlsafe(urlsafe)
            # if not _object.check_object_permissions(self.user):
            #     raise NotAuthorized
        except Exception as err:
            logging.error('Error in get_object: %s -> %s\n%s',
                          err.__class__.__name__,
                          str(err),
                          traceback.format_exc())
            if raise_exception:
                raise NotFound
        finally:
            return _object

    @property
    def paginator(self):
        """
        The paginator instance associated with the view, or `None`.
        """
        if not hasattr(self, '_paginator'):
            if self.pagination_class is None:
                self._paginator = None
            else:
                self._paginator = self.pagination_class()
        return self._paginator

    def paginate_queryset(self, queryset):
        """
        Return a single page of results, or `None` if pagination is disabled.
        """
        if self.paginator is None:
            return None
        return self.paginator.paginate_queryset(queryset, self.request, view=self)

    def get_paginated_response(self, data):
        """
        Return a paginated style `Response` object for the given output data.
        """
        assert self.paginator is not None
        return self.paginator.get_paginated_response(data)


class APIHandler(BaseHandler):
    def options(self, *args, **kwargs):
        return CorsResponse()

    def get(self, *args, **kwargs):
        raise NotImplemented

    def post(self, *args, **kwargs):
        raise NotImplemented

    def patch(self, *args, **kwargs):
        raise NotImplemented

    def delete(self, *args, **kwargs):
        raise NotImplemented

    def head(self, *args, **kwargs):
        raise NotImplemented

    def trace(self, *args, **kwargs):
        raise NotImplemented


class APIModelListHandler(APIModelBaseHandler):
    """
    ModelHandler that perform a generic list operation
    """
    def list(self, request, **kwargs):
        """
        List operation

        :return: :py:class:`restae.response.JsonResponse`
        """
        page = self.paginate_queryset(self.queryset)

        if page is not None:
            serializer = self.serializer_class(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.serializer_class(self.queryset, many=True)
        return Response(serializer.data)


class APIModelCreateHandler(APIModelBaseHandler):
    """
    ModelHandler that perform a generic create operation
    """
    def create(self, request, **kwargs):
        """

        :return: :py:class:`restae.response.JsonResponse`
        """
        _model = get_model_class_from_query(self.queryset)
        obj = _model(**self.serializer_class(data=self.get_body()).data)
        obj.put()
        return JsonResponse(data=self.serializer_class(obj).data)


class APIModelRetrieveHandler(APIModelBaseHandler):
    """
    ModelHandler that perform a generic get operation
    """
    def retrieve(self, request, key=None):
        """

        :return: :py:class:`restae.response.JsonResponse`
        """
        try:
            obj = key.get()
            if obj is None:
                raise NotFound
        except Exception:
            raise NotFound

        return JsonResponse(data=self.serializer_class(obj).data)


class APIModelUpdateHandler(APIModelBaseHandler):
    """
    ModelHandler that perform a generic update operation
    """
    def update(self, request, key=None):
        raise NotImplementedError


class APIModelPatchHandler(APIModelBaseHandler):
    """
    ModelHandler that perform a generic patch operation
    """
    def partial_update(self, request, key=None):
        raise NotImplementedError


class APIModelDestroyHandler(APIModelBaseHandler):
    """
    ModelHandler that perform a generic delete operation
    """
    def destroy(self, request, key=None):
        """

        :return: :py:class:`restae.response.JsonResponse`
        """
        key.delete()
        return JsonResponse()


class APIModelMixinHandler(APIModelListHandler,
                           APIModelCreateHandler,
                           APIModelDestroyHandler,
                           APIModelRetrieveHandler,
                           APIModelUpdateHandler,
                           APIModelPatchHandler):
    pass


class APIModelHandler(APIModelMixinHandler):
    def do_dispatch(self):
        """
        Dispatches the request.

        This will first check if there's a handler_method defined in the
        matched route, and if not it'll use the method correspondent to the
        request method (``get()``, ``post()`` etc).
        """
        route_args = self.get_route_args()
        route_kwargs = {}
        matched_url = None

        for url in getattr(self, 'urls', []):
            if re.match(url.regex, self.request.path_info):
                matched_url = url
                break

        if matched_url is None:
            raise NotFound('URL not found on this server')

        if matched_url.route.detail is True:
            try:
                route_kwargs['key'] = get_key_from_urlsafe(route_args['urlsafe'])
            except Exception:
                return JsonResponse(status=400, data='Given urlsafe is invalid')

        if self.request.method.lower() not in matched_url.route.mapping.keys():
            raise DispatchError('Invalid method {}'.format(self.request.method))

        method = getattr(self, matched_url.route.mapping[self.request.method.lower()], None)
        # The handler only receives *args if no named variables are set.
        args, kwargs = route_args, route_kwargs
        if kwargs:
            args = ()

        return method(self, *args, **kwargs)
