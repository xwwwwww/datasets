import importlib
import inspect
from functools import wraps
from typing import TYPE_CHECKING, Optional, Union

from .download.streaming_download_manager import (
    xbasename,
    xdirname,
    xet_parse,
    xgetsize,
    xglob,
    xisdir,
    xisfile,
    xjoin,
    xlistdir,
    xopen,
    xpandas_read_csv,
    xpandas_read_excel,
    xPath,
    xrelpath,
    xsio_loadmat,
    xsplit,
    xsplitext,
    xwalk,
    xxml_dom_minidom_parse,
)
from .utils.logging import get_logger
from .utils.patching import patch_submodule
from .utils.py_utils import get_imports


logger = get_logger(__name__)


if TYPE_CHECKING:
    from .builder import DatasetBuilder


def extend_module_for_streaming(module_path, use_auth_token: Optional[Union[str, bool]] = None):
    """Extend the module to support streaming.

    We patch some functions in the module to use `fsspec` to support data streaming:
    - We use `fsspec.open` to open and read remote files. We patch the module function:
      - `open`
    - We use the "::" hop separator to join paths and navigate remote compressed/archive files. We patch the module
      functions:
      - `os.path.join`
      - `pathlib.Path.joinpath` and `pathlib.Path.__truediv__` (called when using the "/" operator)

    The patched functions are replaced with custom functions defined to work with the
    :class:`~download.streaming_download_manager.StreamingDownloadManager`.

    Args:
        module_path: Path to the module to be extended.
        use_auth_token (``str`` or :obj:`bool`, optional): Optional string or boolean to use as Bearer token for remote files on the Datasets Hub.
            If True, will get token from `"~/.huggingface"`.
    """

    module = importlib.import_module(module_path)

    # TODO(QL): always update the module to add subsequent new authentication
    if hasattr(module, "_patched_for_streaming") and module._patched_for_streaming:
        return

    def wrap_auth(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            return function(*args, use_auth_token=use_auth_token, **kwargs)

        wrapper._decorator_name_ = "wrap_auth"
        return wrapper

    # open files in a streaming fashion
    patch_submodule(module, "open", wrap_auth(xopen)).start()
    patch_submodule(module, "os.listdir", wrap_auth(xlistdir)).start()
    patch_submodule(module, "os.walk", wrap_auth(xwalk)).start()
    patch_submodule(module, "glob.glob", wrap_auth(xglob)).start()
    # allow to navigate in remote zip files
    patch_submodule(module, "os.path.join", xjoin).start()
    patch_submodule(module, "os.path.dirname", xdirname).start()
    patch_submodule(module, "os.path.basename", xbasename).start()
    patch_submodule(module, "os.path.relpath", xrelpath).start()
    patch_submodule(module, "os.path.split", xsplit).start()
    patch_submodule(module, "os.path.splitext", xsplitext).start()
    # allow checks on paths
    patch_submodule(module, "os.path.isdir", wrap_auth(xisdir)).start()
    patch_submodule(module, "os.path.isfile", wrap_auth(xisfile)).start()
    patch_submodule(module, "os.path.getsize", wrap_auth(xgetsize)).start()
    patch_submodule(module, "pathlib.Path", xPath).start()
    # file readers
    patch_submodule(module, "pandas.read_csv", wrap_auth(xpandas_read_csv), attrs=["__version__"]).start()
    patch_submodule(module, "pandas.read_excel", xpandas_read_excel, attrs=["__version__"]).start()
    patch_submodule(module, "scipy.io.loadmat", wrap_auth(xsio_loadmat), attrs=["__version__"]).start()
    patch_submodule(module, "xml.etree.ElementTree.parse", wrap_auth(xet_parse)).start()
    patch_submodule(module, "xml.dom.minidom.parse", wrap_auth(xxml_dom_minidom_parse)).start()
    module._patched_for_streaming = True


def extend_dataset_builder_for_streaming(builder: "DatasetBuilder"):
    """Extend the dataset builder module and the modules imported by it to support streaming.

    Args:
        builder (:class:`DatasetBuilder`): Dataset builder instance.
    """
    # this extends the open and os.path.join functions for data streaming
    extend_module_for_streaming(builder.__module__, use_auth_token=builder.use_auth_token)
    # if needed, we also have to extend additional internal imports (like wmt14 -> wmt_utils)
    if not builder.__module__.startswith("datasets."):  # check that it's not a packaged builder like csv
        for imports in get_imports(inspect.getfile(builder.__class__)):
            if imports[0] == "internal":
                internal_import_name = imports[1]
                internal_module_name = ".".join(builder.__module__.split(".")[:-1] + [internal_import_name])
                extend_module_for_streaming(internal_module_name, use_auth_token=builder.use_auth_token)

    # builders can inherit from other builders that might use streaming functionality
    # (for example, ImageFolder and AudioFolder inherit from FolderBuilder which implements examples generation)
    # but these parents builders are not patched automatically as they are not instantiated, so we patch them here
    from .builder import DatasetBuilder

    parent_builder_modules = [
        cls.__module__
        for cls in type(builder).__mro__[1:]  # make sure it's not the same module we've already patched
        if issubclass(cls, DatasetBuilder) and cls.__module__ != DatasetBuilder.__module__
    ]  # check it's not a standard builder from datasets.builder
    for module in parent_builder_modules:
        extend_module_for_streaming(module, use_auth_token=builder.use_auth_token)
