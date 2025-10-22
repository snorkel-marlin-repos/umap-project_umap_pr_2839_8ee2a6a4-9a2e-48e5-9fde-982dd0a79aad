import gzip
import json
import os
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.serializers.json import DjangoJSONEncoder
from django.urls import URLPattern, URLResolver, get_resolver


def _get_url_names(module):
    def _get_names(resolver):
        names = []
        for pattern in resolver.url_patterns:
            if getattr(pattern, "url_patterns", None):
                # Do not add "admin" and other third party apps urls.
                if not pattern.namespace:
                    names.extend(_get_names(pattern))
            elif getattr(pattern, "name", None):
                names.append(pattern.name)
        return names

    return _get_names(get_resolver(module))


def _urls_for_js():
    """
    Return templated URLs prepared for javascript.
    """
    urls = {}
    modules = ["umap.urls"]
    if settings.REALTIME_ENABLED:
        modules.append("umap.sync.app")
    for module in modules:
        names = _get_url_names(module)
        urls.update(
            dict(zip(names, [get_uri_template(url, module=module) for url in names]))
        )
    urls.update(getattr(settings, "UMAP_EXTRA_URLS", {}))
    return urls


def get_uri_template(urlname, args=None, prefix="", module=None):
    """
    Utility function to return an URI Template from a named URL in django
    Copied from django-digitalpaper.

    Restrictions:
    - Only supports named urls! i.e. url(... name="toto")
    - Only support one namespace level
    - Only returns the first URL possibility.
    - Supports multiple pattern possibilities (i.e., patterns with
      non-capturing parenthesis in them) by trying to find a pattern
      whose optional parameters match those you specified (a parameter
      is considered optional if it doesn't appear in every pattern possibility)
    """

    def _convert(template, args=None):
        """URI template converter"""
        if not args:
            args = []
        paths = template % dict([p, "{%s}" % p] for p in args)
        return "%s/%s" % (prefix, paths)

    resolver = get_resolver(module)
    parts = urlname.split(":")
    if len(parts) > 1 and parts[0] in resolver.namespace_dict:
        namespace = parts[0]
        urlname = parts[1]
        nprefix, resolver = resolver.namespace_dict[namespace]
        prefix = prefix + "/" + nprefix.rstrip("/")
    possibilities = resolver.reverse_dict.getlist(urlname)
    for tmp in possibilities:
        possibility, pattern = tmp[:2]
        if not args:
            # If not args are specified, we only consider the first pattern
            # django gives us
            result, params = possibility[0]
            return _convert(result, params)
        else:
            # If there are optionnal arguments passed, use them to try to find
            # the correct pattern.
            # First, we need to build a list with all the arguments
            seen_params = []
            for result, params in possibility:
                seen_params.append(params)
            # Then build a set to find the common ones, and use it to build the
            # list of all the expected params
            common_params = reduce(lambda x, y: set(x) & set(y), seen_params)
            expected_params = sorted(common_params.union(args))
            # Then loop again over the pattern possibilities and return
            # the first one that strictly match expected params
            for result, params in possibility:
                if sorted(params) == expected_params:
                    return _convert(result, params)
    return None


class DecoratedURLPattern(URLPattern):
    def resolve(self, *args, **kwargs):
        result = URLPattern.resolve(self, *args, **kwargs)
        if result:
            for func in self._decorate_with:
                result.func = func(result.func)
        return result


def decorated_patterns(func, *urls):
    """
    Utility function to decorate a group of url in urls.py

    Taken from http://djangosnippets.org/snippets/532/ + comments
    See also http://friendpaste.com/6afByRiBB9CMwPft3a6lym

    Example:
    urlpatterns = [
        url(r'^language/(?P<lang_code>[a-z]+)$', views.MyView, name='name'),
    ] + decorated_patterns(login_required, url(r'^', include('cms.urls')),
    """

    def decorate(urls, func):
        for url in urls:
            if isinstance(url, URLPattern):
                url.__class__ = DecoratedURLPattern
                if not hasattr(url, "_decorate_with"):
                    setattr(url, "_decorate_with", [])
                url._decorate_with.append(func)
            elif isinstance(url, URLResolver):
                for pp in url.url_patterns:
                    if isinstance(pp, URLPattern):
                        pp.__class__ = DecoratedURLPattern
                        if not hasattr(pp, "_decorate_with"):
                            setattr(pp, "_decorate_with", [])
                        pp._decorate_with.append(func)

    if func:
        if not isinstance(func, (list, tuple)):
            func = [func]
        for f in func:
            decorate(urls, f)

    return urls


def gzip_file(from_path, to_path):
    stat = os.stat(from_path)
    with open(from_path, "rb") as f_in:
        with gzip.open(to_path, "wb") as f_out:
            f_out.writelines(f_in)
    os.utime(to_path, ns=(stat.st_mtime_ns, stat.st_mtime_ns))


def is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


class ConflictError(ValueError):
    pass


def merge_features(reference: list, latest: list, incoming: list):
    """Finds the changes between reference and incoming, and reapplies them on top of latest."""
    if latest == incoming:
        return latest

    removed = [item for item in reference if item not in incoming]
    added = [item for item in incoming if item not in reference]

    # Ensure that items changed in the reference weren't also changed in the latest.
    for item in removed:
        if item not in latest:
            raise ConflictError()

    merged = latest[:]

    # Reapply the changes on top of the latest.
    for item in removed:
        merged.remove(item)

    for item in added:
        merged.append(item)

    return merged


def json_dumps(obj, **kwargs):
    """Utility using the Django JSON Encoder when dumping objects"""
    return json.dumps(obj, cls=DjangoJSONEncoder, **kwargs)


def collect_pictograms():
    pictograms = {}

    for name, definition in settings.UMAP_PICTOGRAMS_COLLECTIONS.items():
        root = Path(definition["path"])
        subfolder = "pictograms"
        if not root.is_absolute():
            found_path = finders.find(root)
            if not found_path:
                print(f"Cannot find {root} in STATIFILES_DIRS")
                continue
            root = Path(found_path.removesuffix(definition["path"]))
            subfolder = definition["path"]
        categories = {}
        for path in (root / subfolder).iterdir():
            if path.is_dir():
                categories[path.name] = []
                for subpath in path.iterdir():
                    if subpath.is_dir() or subpath.name.startswith("."):
                        continue
                    src = subpath.relative_to(root)
                    categories[path.name].append(
                        {
                            "name": subpath.stem,
                            "src": f"{settings.STATIC_URL}{src}",
                        }
                    )
        pictograms[name] = {
            "attribution": definition.get("attribution"),
            "categories": categories,
        }

    return pictograms
