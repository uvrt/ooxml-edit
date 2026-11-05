"""``render`` inside ``read_batch`` (round 35, R20): a reviewer's round -- pages of a document
and slides of a deck drawn, a check, a few reads -- in one call.  Each render runs as the
same call alone would (its locks let go while it draws), its images come in the reply after
the text, in op order, each captioned with its op, and count toward the reply's budget as
their tokens would; what does not fit is listed with the call that asks for it, its images
given back to the session's budget.  ``batch`` still refuses render, pointing at
``read_batch``.  Made-up documents and a toy renderer only."""

from __future__ import annotations

import base64
import threading
import time

import pytest

from ooxml_edit.tools import Limits, Result, Toolbox, adapters, shared
from ooxml_edit.tools import render as pipeline
from ooxml_edit.tools.dispatch import CHARS_PER_IMAGE_TOKEN, image_chars
from ooxml_edit.tools.results import Image, anthropic_image_tokens, openai_image_tokens

import synthetic
import tools_toys
from test_tools_shared import CLOCK, DECK, DECK_TOOLS, TEXT, TEXT_TOOLS, _ops

#: Seconds the stalling renderer takes per chunk (set by a test).
STALL = {"seconds": 0.0}


class StallingRenderer(tools_toys.ToyRenderer):
    """The toy renderer, slowed down while a test needs it to be."""

    name = "toy-stalling"

    def rasterise(self, views, raster):
        time.sleep(STALL["seconds"])
        return super().rasterise(views, raster)


RENDERER = StallingRenderer()


def _draw(call, numbers, width, label):
    drawn, _ = pipeline.draw(call, RENDERER, numbers, width, ids={n: n for n in numbers},
                             fonts=(), locale=None)
    for n in numbers:
        png = drawn[n][0]
        call.image(png, *pipeline.png_size(png), label=f"{label} {n}")
    return {"cached": [n for n in numbers if drawn[n][2]]}


@shared.handler("render", kind="pptx")
def render_deck(call, doc, slides=None, pages=None, width=None, sheet=None):
    numbers = [int(s) for s in slides or [1]]
    call.check_images(len(numbers))
    return Result(summary=f"Rendered slide(s) {numbers}",
                  data=_draw(call, numbers, width or 1280, "slide"))


@shared.handler("render", kind="docx")
def render_text(call, doc, slides=None, pages=None, width=None, sheet=None):
    numbers = list(pages or [1])
    call.check_images(len(numbers))
    return Result(summary=f"Rendered page(s) {numbers}",
                  data=_draw(call, numbers, width or 1000, "page"))


def _lay_out(call):
    """What a check that lays the document out does: the slow part with the locks let go."""
    with call.unlocked():
        time.sleep(STALL["seconds"])


@shared.handler("check", kind="docx")
def check_text(call, doc, **_):
    items = len(call.document.items())
    _lay_out(call)
    return Result(summary="Checked", data={"items": items})


@shared.handler("check", kind="pptx")
def check_deck(call, doc, **_):
    pages = len(call.document.pages)
    _lay_out(call)
    return Result(summary="Checked", data={"pages": pages})


TOOLS = [*TEXT_TOOLS, *DECK_TOOLS, render_deck, render_text, check_text, check_deck,
         tools_toys.toy_word_read, shared.READ_BATCH]


@pytest.fixture
def toolbox():
    STALL["seconds"] = 0.0
    with Toolbox(TOOLS, formats=[DECK, TEXT], groups=shared.GROUPS + tools_toys.GROUPS,
                 workers=0) as box:
        yield box


def _session(toolbox, **limits):
    session = toolbox.session(clock=CLOCK, limits=Limits(**limits) if limits else None)
    session.open(synthetic.outer_package(), "report.docx")    # d1
    session.open(synthetic.outer_package(), "slides.pptx")    # d2
    return session


#: A reviewer's round: a page and a slide drawn, both checked, the text read.
ROUND = _ops(("render", {"doc": "d1", "pages": [1, 2], "width": 400}),
             ("check", {"doc": "d1"}),
             ("render", {"doc": "d2", "slides": ["2"], "width": 640}),
             ("check", {"doc": "d2"}),
             ("toy_word_read", {"doc": "d1"}))


# -- one round, one call ----------------------------------------------------------------------


def test_renders_of_a_document_and_a_deck_come_with_checks_and_reads_in_one_reply(toolbox):
    session = _session(toolbox)
    reviewer = session.view(roles=["review"])
    result = toolbox.dispatch(reviewer, "read_batch", {"ops": ROUND})
    assert result.ok, result.to_json()
    answers = result.data["ops"]
    assert [answer["op"] for answer in answers] == [0, 1, 2, 3, 4]
    assert all("error" not in answer for answer in answers)
    assert [image["label"] for image in answers[0]["images"]] == ["page 1", "page 2"]
    assert [image["label"] for image in answers[2]["images"]] == ["slide 2"]
    assert answers[1]["data"] == {"items": 0} and answers[3]["data"] == {"pages": 2}
    # The images, in op order, each captioned with its op and document.
    assert [image.caption for image in result.images] == [
        "ops[0] d1 page 1", "ops[0] d1 page 2", "ops[2] d2 slide 2"]
    assert [(image.width, image.height) for image in result.images] == [
        (400, 225), (400, 225), (640, 360)]
    assert "3 image(s) follow, in op order" in result.summary
    assert session.images_used == 3          # each counted once, as alone
    # As the same calls alone: the same images.
    alone = toolbox.dispatch(session, "render", {"doc": "d2", "slides": ["2"], "width": 640})
    assert alone.images[0].data == result.images[2].data and alone.data["cached"] == [2]
    assert alone.images[0].caption == ""                  # a lone call's: no caption


def test_batch_still_refuses_render_and_points_at_read_batch(toolbox):
    session = _session(toolbox)
    result = toolbox.dispatch(session, "batch", {"ops": _ops(
        ("t_add", {"doc": "d1", "items": [{"text": "x"}]}),
        ("render", {"doc": "d1", "pages": [1]}))})
    assert result.ok and result.data["failed"] == [1]
    error = result.data["ops"][1]["error"]
    assert "cannot run inside a batch; call it in read_batch" in error["message"]
    assert error["valid_options"] == ["read_batch", "render"]
    assert session.images_used == 0


# -- the providers -----------------------------------------------------------------------------


def _decoded(url):
    return base64.b64decode(url.split(",", 1)[1])


def test_every_provider_carries_the_images_each_after_its_caption(toolbox):
    session = _session(toolbox)
    result = toolbox.dispatch(session, "read_batch", {"ops": ROUND})
    captions = [image.caption for image in result.images]
    pngs = [image.data for image in result.images]

    block = toolbox.render_result("anthropic", result, "toolu_1")
    content = block["content"]
    assert content[0]["type"] == "text" and "is_error" not in block
    rest = content[1:]
    assert [part["text"] for part in rest[0::2]] == captions
    assert [base64.b64decode(part["source"]["data"]) for part in rest[1::2]] == pngs

    output = toolbox.render_result("openai-responses", result, "call_1")["output"]
    assert [part["text"] for part in output[1::2]] == captions
    assert [_decoded(part["image_url"]) for part in output[2::2]] == pngs
    assert adapters.openai_input_problems([{"type": "function_call_output",
                                            "call_id": "call_1", "output": output}]) == []

    items = toolbox.render_results("openai-responses", [("call_1", result)], images="message")
    parts = items[-1]["content"][1:]
    assert [part["text"] for part in parts[0::2]] == captions
    assert adapters.openai_input_problems(items) == []

    messages = toolbox.render_results("openai-chat", [("call_1", result)])
    assert messages[0]["role"] == "tool" and "3 image(s)" in messages[0]["content"]
    parts = messages[1]["content"][1:]
    assert [part["text"] for part in parts[0::2]] == captions
    assert [_decoded(part["image_url"]["url"]) for part in parts[1::2]] == pngs


# -- the budget --------------------------------------------------------------------------------


def test_an_image_counts_as_its_tokens_at_four_characters_each():
    slide = Image(b"", 1280, 720)
    tokens = max(anthropic_image_tokens(1280, 720), openai_image_tokens(1280, 720))
    assert image_chars([slide]) == CHARS_PER_IMAGE_TOKEN * tokens == 4_784
    assert image_chars([slide, Image(b"", 1000, 1414)]) == 4_784 + 4 * 1_836


def test_a_render_that_does_not_fit_is_listed_with_its_call_and_its_images_given_back(
        toolbox):
    session = _session(toolbox, max_result_chars=3_000, max_read_batch_chars=9_000)
    ops = _ops(("render", {"doc": "d2", "slides": ["1"], "width": 1280}),
               ("render", {"doc": "d1", "pages": [1], "width": 1280}),
               ("check", {"doc": "d1"}))
    result = toolbox.dispatch(session, "read_batch", {"ops": ops})
    assert [answer["op"] for answer in result.data["ops"]] == [0, 2]
    left = result.data["not_returned"]
    assert [item["op"] for item in left] == [1]
    assert "its images count 4,784" in left[0]["why"]
    assert result.data["resend"] == {"tool": "read_batch", "arguments": {"ops": [ops[1]]}}
    assert [image.caption for image in result.images] == ["ops[0] d2 slide 1"]
    assert session.images_used == 1                      # the one sent, not the one left
    sent = len(result.to_text()) + image_chars(result.images)
    assert sent <= 9_000
    again = toolbox.dispatch(session, "read_batch", result.data["resend"]["arguments"])
    assert [image.caption for image in again.images] == ["ops[0] d1 page 1"]
    assert again.data["ops"][0]["data"]["cached"] == [1]  # drawn once, kept
    assert session.images_used == 2


def test_the_session_s_image_budget_holds_inside_a_read_batch(toolbox):
    session = _session(toolbox, image_budget=2)
    result = toolbox.dispatch(session, "read_batch", {"ops": ROUND})
    answers = {answer["op"]: answer for answer in result.data["ops"]}
    assert "error" not in answers[0] and answers[2]["error"]["code"] == "limit"
    assert "error" not in answers[3] and "error" not in answers[4]   # the rest still run
    assert len(result.images) == 2 and session.images_used == 2


# -- locks -------------------------------------------------------------------------------------


def test_a_render_in_a_read_batch_lets_its_document_go_while_it_draws(toolbox):
    session = _session(toolbox)
    toolbox.dispatch(session, "render", {"doc": "d2", "slides": ["1"], "width": 320})
    STALL["seconds"] = 2.0
    done: dict = {}

    def review():
        done["result"] = toolbox.dispatch(session, "read_batch", {"ops": _ops(
            ("render", {"doc": "d2", "slides": ["2"], "width": 330}), ("check", {"doc": "d2"}),
            ("toy_word_read", {"doc": "d1"}))})
        done["at"] = time.perf_counter()

    thread = threading.Thread(target=review, daemon=True)
    started = time.perf_counter()
    thread.start()
    time.sleep(0.4)                           # the render has its snapshot and draws
    edit = toolbox.dispatch(session, "toy_ppt_set_title",
                            {"doc": "d2", "page": 1, "text": "Edited meanwhile"})
    edited = time.perf_counter()
    thread.join(timeout=30)
    assert not thread.is_alive(), "the read_batch hung"
    assert edit.ok and edited - started < 1.5, "the edit waited for the render"
    assert done["at"] > edited
    result = done["result"]
    assert [answer["op"] for answer in result.data["ops"]] == [0, 1, 2]
    rendered = result.data["ops"][0]
    # It drew the version it took; the document moved on meanwhile, which it says.
    assert rendered["version"] == edit.version - 1
    assert any("d2 changed while this call read it" in w for w in rendered["warnings"])


def test_read_batches_rendering_both_documents_and_edits_of_each_all_end(toolbox):
    """Two reviewers' rounds over both documents at once, each render letting its own
    document go while it holds the other's, and an edit of each document meanwhile: no
    wait closes a cycle, so every call ends."""
    session = _session(toolbox)
    STALL["seconds"] = 0.3
    results: dict = {}

    def run(name, tool, arguments):
        results[name] = toolbox.dispatch(session, tool, arguments)

    calls = [("a", "read_batch", {"ops": ROUND}),
             ("b", "read_batch", {"ops": list(reversed(ROUND))}),
             ("d1", "t_add", {"doc": "d1", "items": [{"text": "added"}]}),
             ("d2", "toy_ppt_set_title", {"doc": "d2", "page": 2, "text": "Retitled"})]
    threads = [threading.Thread(target=run, args=call, daemon=True) for call in calls]
    for thread in threads:
        thread.start()
        time.sleep(0.05)
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a call hung"
    assert all(results[name].ok for name in ("a", "b", "d1", "d2"))
    for name in ("a", "b"):
        assert len(results[name].data["ops"]) == 5
        assert len(results[name].images) == 3


def test_read_batches_checking_two_documents_in_either_order_both_end(toolbox):
    """Before round 35 an op let its own document's lock go and took it back while the
    batch still held the other's: two such batches, in opposite orders, each waited for the
    lock the other held (a check that lays out, as docx-agent's, was enough)."""
    session = _session(toolbox)
    STALL["seconds"] = 0.3
    ops = _ops(("check", {"doc": "d1"}), ("check", {"doc": "d2"}))
    results: list = []
    threads = [threading.Thread(target=lambda o=o: results.append(
        toolbox.dispatch(session, "read_batch", {"ops": o})), daemon=True)
        for o in (ops, ops[::-1])]
    for thread in threads:
        thread.start()
        time.sleep(0.05)
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads), "the read_batches hung"
    assert len(results) == 2 and all(len(r.data["ops"]) == 2 for r in results)


def test_a_batch_s_checks_after_its_change_on_two_documents_and_a_read_batch_both_end(
        toolbox):
    """The same for a batch's reads after its last change (round 33), which run so too."""
    session = _session(toolbox)
    STALL["seconds"] = 0.3
    results: list = []
    batch = _ops(("t_add", {"doc": "d1", "items": [{"text": "x"}]}),
                 ("check", {"doc": "d1"}), ("check", {"doc": "d2"}))
    reads = _ops(("check", {"doc": "d2"}), ("check", {"doc": "d1"}))
    threads = [threading.Thread(target=lambda: results.append(
                   toolbox.dispatch(session, "batch", {"ops": batch})), daemon=True),
               threading.Thread(target=lambda: results.append(
                   toolbox.dispatch(session, "read_batch", {"ops": reads})), daemon=True)]
    for thread in threads:
        thread.start()
        time.sleep(0.05)
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads), "a call hung"
    assert len(results) == 2 and all(r.ok for r in results)
