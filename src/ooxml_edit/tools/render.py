"""One render pipeline for every format: a :class:`Renderer` adapter, and the rest shared.

A format's ``render`` tool draws views of a document -- its pages, or whatever its views
are -- as PNGs, in worker processes, and keeps each image by its content
(:mod:`.rendercache`), so a view an edit left as it was is found again and not drawn.  What
differs between formats is only the renderer: how a document's bytes become SVGs, what the
pixels depend on besides the SVG, and how an SVG becomes a PNG.  A format library says
that in a :class:`Renderer` -- a picklable object at module level, handed to the worker
tasks with their arguments -- and this module does everything else, the same way for each:

* :func:`draw`, on the caller's side: the images this version already has (the entry's
  ``render_keys``, the toolbox's store), the bytes' snapshot, the work over the workers
  with the locks let go, and what came back kept -- by its content key in the store, and
  by version, view and width on the entry;
* the worker tasks, each marked a :func:`~.worker.worker_task`, so a subprocess worker runs
  them: :func:`lay_out_views` (the document laid out once, each view's SVG and key),
  :func:`rasterise_views` (a chunk of those SVGs to PNGs), :func:`render_views` (both in
  one worker, for a chunk of views), :func:`rasterise_tiles` (a contact image's SVG,
  :mod:`.tiles`);
* the keys: :func:`fingerprint` (what the pixels depend on besides the SVG, with the code
  that draws them, :func:`~.rendercache.code_stamp`) and :func:`raster_key` (one image's);
* :func:`png_size` and :func:`checked_png`: every PNG a renderer returns is checked to be
  one before it is kept or sent.

Two shapes of work, by :attr:`Renderer.whole_document`.  A format whose views stand alone
(each laid out from the document by itself) splits a call's views into chunks, one per
worker, and each worker lays out and rasterises its own (:func:`render_views`).  A format
that lays out the whole document to find any one view (text flows from one view into the
next) lays it out once, in one worker (:func:`lay_out_views`), and rasterises the views
whose image is not held over all the workers at once (:func:`rasterise_views`).

What a format keeps between calls -- decoded fonts, a layout -- travels as ``state`` (to
the workers, as given) and comes back as each task's ``facts`` (:meth:`Renderer.facts`),
which :func:`draw` hands to the caller's ``on_facts`` and returns.

Nothing here imports a renderer: the format library passes its adapter.
"""

from __future__ import annotations

import struct
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from .rendercache import code_stamp, content_key
from .worker import worker_task

#: The eight bytes every PNG starts with.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(png: bytes | bytearray) -> tuple[int, int]:
    """A PNG's width and height, read from its header; ``ValueError`` when ``png`` is not
    one (no PNG signature, or no header chunk where the format puts it)."""
    if len(png) < 24 or png[:8] != PNG_SIGNATURE or png[12:16] != b"IHDR":
        raise ValueError(f"not a PNG ({len(png)} bytes starting {bytes(png[:8])!r}); "
                         "a render returns PNG bytes")
    return struct.unpack(">II", png[16:24])


def checked_png(png: Any, renderer: Any = None) -> bytes:
    """``png``, once it is known to be PNG bytes; else a ``RuntimeError`` naming the
    renderer that returned it -- a renderer's fault, not the call's."""
    if not isinstance(png, (bytes, bytearray)):
        raise RuntimeError(f"{_name(renderer)} returned {type(png).__name__}, not PNG bytes")
    try:
        png_size(png)
    except ValueError as exc:
        raise RuntimeError(f"{_name(renderer)} returned an image that is {exc}") from None
    return bytes(png)


def _name(renderer: Any) -> str:
    name = getattr(renderer, "name", None)
    return f"the {name} renderer" if name else "the renderer"


# -- the adapter ---------------------------------------------------------------------------------


@dataclass
class View:
    """One view a :meth:`Renderer.lay_out` made: ``item`` as the caller named it, its
    ``svg``, and ``info`` -- whatever :meth:`Renderer.rasterise` needs to know about it
    besides the SVG (picklable; ``None`` for nothing)."""

    item: Any
    svg: str
    info: Any = None


@dataclass
class Layout:
    """What :meth:`Renderer.lay_out` returns: the ``views`` the document has among the
    items asked for, in their order (an item the document does not have is left out);
    the raster ``fingerprint`` (:func:`fingerprint`) every view's key is made with;
    ``raster``, what :meth:`Renderer.rasterise` is handed with the views -- picklable for a
    :attr:`~Renderer.whole_document` renderer, whose views are rasterised in other workers;
    and ``facts``, the format's own, for :meth:`Renderer.facts`."""

    views: list[View]
    fingerprint: str
    raster: Any = None
    facts: Any = None


@runtime_checkable
class Renderer(Protocol):
    """A format's renderer, as the pipeline calls it.  An instance is handed to the worker
    tasks as an argument, so it must pickle (a module-level class with nothing but plain
    attributes); its methods run in the worker.  :class:`RendererBase` has the defaults.

    ``name`` -- the keys' namespace (``"<library>"``): no two formats' images share a key.
    ``whole_document`` -- whether a view can only be laid out with the whole document
    (the module's docstring: the two shapes of work)."""

    name: str
    whole_document: bool

    def code_stamp(self) -> list:
        """The code that draws the pixels (:func:`~.rendercache.code_stamp` of the
        renderer's modules): part of every key, so a new install is a new key."""
        ...

    def lay_out(self, data: bytes, items: list, width: int, font_dirs: "list[str]",
                locale: Any, state: Any) -> Layout:
        """The document ``data`` laid out, and each of ``items`` it has as a :class:`View`,
        ``width`` px wide.  ``font_dirs`` is the application's font folders and ``locale``
        the session's (``None``: en-US), resolved by the caller; ``state`` is what the
        caller kept from an earlier call's facts (``None`` for nothing)."""
        ...

    def key_text(self, svg: str) -> str:
        """The part of a view's SVG its pixels depend on (the SVG itself, or less: ids the
        rasteriser never reads), which the key is made from."""
        ...

    def rasterise(self, views: "list[View]", raster: Any) -> "list[tuple[bytes, Any]]":
        """Each view's ``(png, meta)``, in order: ``meta`` is kept with the image and
        returned with it (what the image leaves out, say), ``None`` for nothing."""
        ...

    def facts(self, layout: Layout) -> Any:
        """What a task returns to the caller beside the views, once they are drawn."""
        ...

    def rasterise_tiles(self, svg: str, font_dirs: "list[str] | None") -> bytes:
        """The PNG of a contact image's SVG (:func:`.tiles.tiled_svg`), at its own size."""
        ...

    def warm_up(self) -> None:
        """What the first render in a new worker would pay for, paid before it: the
        renderer's imports, the rasteriser's load of the system's fonts."""
        ...


class RendererBase:
    """The defaults of a :class:`Renderer`: ``modules`` stamped as its code, the whole SVG
    keyed, the layout's ``facts`` returned, views that stand alone, nothing to warm."""

    name: str = ""
    whole_document: bool = False
    #: The importable modules whose code draws the pixels (:meth:`code_stamp`).
    modules: "tuple[str, ...]" = ()

    def code_stamp(self) -> list:
        return code_stamp(*self.modules)

    def key_text(self, svg: str) -> str:
        return svg

    def facts(self, layout: Layout) -> Any:
        return layout.facts

    def warm_up(self) -> None:
        return None

    def __eq__(self, other: object) -> bool:      # one per class: the instance has no state
        return type(self) is type(other)

    def __hash__(self) -> int:
        return hash(type(self))

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"


# -- the keys ------------------------------------------------------------------------------------


def fingerprint(renderer: Renderer, *parts: Any) -> str:
    """What a view's pixels depend on besides its SVG, as one key: ``parts`` -- the
    renderer's to choose: the font files the rasteriser may load, with their times and
    sizes (:func:`system_fonts`), embedded faces by digest, the raster options -- and the
    code that draws them (:meth:`Renderer.code_stamp`).  All the fonts there are, not only
    those one SVG names: a font changed anywhere is a new key, never a stale image."""
    return content_key(f"{renderer.name} raster", renderer.code_stamp(), *parts)


def raster_key(renderer: Renderer, svg: str, fingerprint: str) -> str:
    """One image's content key: its SVG (:meth:`Renderer.key_text`) and the
    :func:`fingerprint` of the render it is part of."""
    return content_key(f"{renderer.name} png", renderer.key_text(svg), fingerprint)


def system_fonts() -> Any:
    """The system's font files as the rasteriser finds them, each with its time and size
    (``ooxml_common.fonts.rasterdb``), or ``"fontconfig"`` where it leaves them to
    fontconfig: a fingerprint part for a render that lets the system's fonts take part."""
    from ooxml_common.fonts.rasterdb import file_stamps, resvg_system_dirs

    found = resvg_system_dirs()
    return file_stamps((), found) if found is not None else "fontconfig"


# -- the worker tasks ----------------------------------------------------------------------------


def _keyed(renderer: Renderer, layout: Layout, known: Sequence[str]) -> list:
    held = set(known)
    out = []
    for view in layout.views:
        key = raster_key(renderer, view.svg, layout.fingerprint)
        out.append((view, key, key in held))
    return out


def _rasterised(renderer: Renderer, views: "list[View]", raster: Any) -> list:
    drawn = list(renderer.rasterise(views, raster))
    if len(drawn) != len(views):
        raise RuntimeError(f"{_name(renderer)} returned {len(drawn)} image(s) for "
                           f"{len(views)} view(s)")
    return [(checked_png(png, renderer), meta) for png, meta in drawn]


@worker_task
def lay_out_views(renderer: Renderer, data: bytes, items: list, width: int,
                  font_dirs: "list[str]", locale: Any = None, known: Sequence[str] = (),
                  state: Any = None) -> dict:
    """In a worker: the document laid out once (:meth:`Renderer.lay_out`), and each of
    ``items`` it has as ``(item, key, svg, info)`` -- ``key`` its image's
    (:func:`raster_key`), ``svg`` ``None`` where the key is in ``known`` (the caller holds
    that image).  Also ``raster`` (for :func:`rasterise_views`) and ``facts``."""
    layout = renderer.lay_out(data, list(items), int(width), list(font_dirs), locale, state)
    views = [(view.item, key, None if held else view.svg, view.info)
             for view, key, held in _keyed(renderer, layout, known)]
    return {"views": views, "raster": layout.raster, "facts": renderer.facts(layout)}


@worker_task
def rasterise_views(renderer: Renderer, views: "list[tuple[str, Any]]", raster: Any
                    ) -> "list[tuple[bytes, Any]]":
    """In a worker: ``(png, meta)`` of each ``(svg, info)`` of :func:`lay_out_views`, with
    its ``raster``."""
    return _rasterised(renderer, [View(None, svg, info) for svg, info in views], raster)


@worker_task
def render_views(renderer: Renderer, data: bytes, items: list, width: int,
                 font_dirs: "list[str]", locale: Any = None, known: Sequence[str] = (),
                 state: Any = None) -> dict:
    """In a worker: :func:`lay_out_views` and :func:`rasterise_views` in one, for views
    that stand alone.  ``{"views": [(item, key, png, meta, drawn), ...], "facts": ...}``:
    ``png`` and ``meta`` are ``None`` and ``drawn`` false where the key is in ``known``."""
    layout = renderer.lay_out(data, list(items), int(width), list(font_dirs), locale, state)
    keyed = _keyed(renderer, layout, known)
    todo = [view for view, _, held in keyed if not held]
    drawn = iter(_rasterised(renderer, todo, layout.raster) if todo else ())
    views = []
    for view, key, held in keyed:
        if held:
            views.append((view.item, key, None, None, False))
        else:
            png, meta = next(drawn)
            views.append((view.item, key, png, meta, True))
    return {"views": views, "facts": renderer.facts(layout)}


@worker_task
def rasterise_tiles(renderer: Renderer, svg: str, font_dirs: "list[str] | None" = None) -> bytes:
    """In a worker: the PNG of a contact image's SVG (:meth:`Renderer.rasterise_tiles`),
    its warnings left out -- the labels are no document's text, and the worker's stderr is
    nobody's to read."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return checked_png(renderer.rasterise_tiles(svg, font_dirs), renderer)


def warm_up(renderer: Renderer) -> None:
    """A new worker's first task, before any call's: the stamp of the renderer's code and
    :meth:`Renderer.warm_up`, its warnings left out.  A format marks a task of no
    arguments (``worker_task``) that calls this with its renderer (the pool's ``warm_up`` takes none)."""
    renderer.code_stamp()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        renderer.warm_up()


# -- the caller's side ---------------------------------------------------------------------------


def draw(call: Any, renderer: Renderer, items: Sequence[Any], width: int, *,
         ids: Mapping[Any, Any], fonts: Sequence[str], locale: Any,
         wire: "Mapping[Any, Any] | None" = None, state: Any = None, first_alone: bool = False,
         on_facts: "Callable[[Any, bool], Any] | None" = None) -> "tuple[dict, list]":
    """``({item: (png, meta, cached)}, facts)`` for the views ``items`` drawn ``width`` px
    wide, by the call's workers; ``cached`` says an image was not drawn by this call.  An
    item the document does not have is left out of the dict.

    An image is kept by its content (:func:`raster_key`) in the toolbox's store
    (``call.images``), and this version's key of each view on the entry, by ``ids[item]``
    (what stays the same for a view across edits), ``width``, ``fonts`` and ``locale``:
    a repeat is answered here.  The rest go to the workers, told which images the store
    holds -- a view an edit left as it was, or the same view in another document, is keyed,
    found and not drawn again.  The bytes are the version's snapshot, taken under the
    lock; the workers work with the lock let go, and what they drew is kept under that
    version.  What one chunk drew is kept when another fails; the failure is raised then.

    ``wire`` is how an item is named to the worker where that differs (``{item: name}``);
    ``fonts`` and ``locale`` are handed to it as resolved here (it sees neither the
    session nor the environment).  ``state`` goes to every task -- given as a function, it
    is called with the snapshot's bytes once there is work, and what it returns goes (what
    a format keeps by the bytes' digest, made only when needed); ``on_facts(facts,
    alone)`` gets each task's facts as it comes back.  ``first_alone`` (views that stand
    alone, more than one chunk): one chunk is drawn first, by itself, and what
    ``on_facts(facts, True)`` returns -- unless ``None`` -- is the state the other chunks
    are handed (what the first worker decoded, which the others then do not decode again).
    ``facts`` is every task's that came back, in order."""
    entry, version, limits, store = call.entry, call.entry.version, call.limits, call.images
    drawn: dict = {}
    todo = []
    for item in dict.fromkeys(items):
        key = entry.render_keys.get((version, ids[item], width, fonts, locale))
        found = store.get(key) if key is not None else None
        if found is not None:
            drawn[item] = (found[0], found[1], True)
        else:
            todo.append(item)
    if not todo:
        return drawn, []
    data = entry.snapshot()
    held = store.entries()               # what the workers are told is drawn already
    if callable(state):
        state = state(data)
    wire = wire or {}
    back = {wire.get(item, item): item for item in todo}
    sent = list(back)
    note = on_facts or (lambda facts, alone: None)
    facts: list = []
    views: dict = {}                     # sent item -> (key, png, meta, new)
    failed: "BaseException | None" = None
    if renderer.whole_document:
        with call.unlocked():
            laid = call.run(lay_out_views, renderer, data, sent, width, list(fonts), locale,
                            list(held), state, timeout=limits.render_timeout_for(len(todo)))
            fresh = [view for view in laid["views"] if view[2] is not None]
            parallel = min(call.parallelism, len(fresh)) or 1
            chunks = [chunk for chunk in (fresh[start::parallel] for start in range(parallel))
                      if chunk]
            results = call.run_all(rasterise_views,
                                   [(renderer, [(svg, info) for _, _, svg, info in chunk],
                                     laid["raster"]) for chunk in chunks],
                                   timeouts=[limits.render_timeout_for(len(chunk))
                                             for chunk in chunks])
        facts.append(laid["facts"])
        note(laid["facts"], False)
        for item, key, svg, _ in laid["views"]:
            if svg is None:
                views[item] = (key, None, None, False)
        for chunk, result in zip(chunks, results):
            if isinstance(result, BaseException):
                failed = failed or result
                continue
            for (item, key, _, _), (png, meta) in zip(chunk, result):
                views[item] = (key, png, meta, True)
    else:
        parallel = call.parallelism
        chunks = [chunk for chunk in (sent[start::parallel] for start in range(parallel))
                  if chunk]
        tasks = [(renderer, data, chunk, width, list(fonts), locale, list(held), state)
                 for chunk in chunks]
        timeouts = [limits.render_timeout_for(len(chunk)) for chunk in chunks]
        first: list = []
        if first_alone and len(tasks) > 1:
            with call.unlocked():
                first = call.run_all(render_views, tasks[:1], timeouts=timeouts[:1])
            if not isinstance(first[0], BaseException):
                handed = note(first[0]["facts"], True)
                if handed is not None:
                    tasks = [(*task[:-1], handed) for task in tasks]
        with call.unlocked():
            results = first + call.run_all(render_views, tasks[len(first):],
                                           timeouts=timeouts[len(first):])
        for result in results:
            if isinstance(result, BaseException):
                failed = failed or result
                continue
            facts.append(result["facts"])
            note(result["facts"], False)
            for item, key, png, meta, new in result["views"]:
                views[item] = (key, png, meta, new)
    for name, (key, png, meta, new) in views.items():
        if png is None:
            png, meta = held[key]
        else:
            store.put(key, png, meta)
        item = back[name]
        entry.render_keys.put((version, ids[item], width, fonts, locale), key)
        drawn[item] = (png, meta, not new)
    if failed is not None:
        raise failed                     # what the other chunks drew is kept
    return drawn, facts


def draw_tiles(call: Any, renderer: Renderer, images: "Sequence[tuple[bytes, str]]",
               tiling: Any, *, name: str, what: str, fonts: Sequence[str]) -> "tuple[bytes, int, int]":
    """A contact image of ``images`` (``(png, label)`` each, in order) laid out by
    ``tiling`` (:mod:`.tiles`), as ``(png, width, height)``: refused when too tall for the
    image's edge (:func:`.tiles.check_size`, with ``name`` and ``what`` for its message),
    rasterised in a worker (:func:`rasterise_tiles`) with the call's locks let go.  The
    tiles are a snapshot already: the caller took them under the lock."""
    from . import tiles

    with call.unlocked():
        views = [(png, *png_size(png), label) for png, label in images]
        tiles.check_size(tiling, [h for _, _, h, _ in views], name=name, what=what,
                         max_edge=call.limits.max_image_edge)
        svg, w, h = tiles.tiled_svg(views, tiling)
        png = call.run(rasterise_tiles, renderer, svg, list(fonts))
    return png, w, h
