# The core

The modules of `ooxml_edit` and how a format layer uses them.

## What is in it

| Module | What it is |
| --- | --- |
| `ooxml_edit.opc` | `OpcPackage`: parts as original bytes plus lazily parsed trees; content types, and `content_types_with` for a copy written with a part retyped while the open package stays as it is; relationships, internal and external; adding, replacing, removing and copying parts; `release` and `reap`, which remove a part only once it is proved unreferenced from anywhere in the package, and leave no empty relationships part and no unused `Default` behind, as Word writes none; packages inside the package (`open_embedded`, `replace_embedded`); snapshots for undo; saving with entry order, timestamps and compression kept, optionally with derived parts written in place (`replacements`); `changed_parts` and `opened` |
| `ooxml_edit.xml` | The namespace registry and `qn`; attribute helpers; `register_child_order` and `insert_in_order`, which put a new child where its parent's schema sequence requires, with rank groups for repeating choices, and append a detached child where no sequence is known; `replace_choice`; `remove`, which keeps the whitespace around what it removes |
| `ooxml_edit.history` | `History`: undo, redo and nested batches over anything with `snapshot` and `restore`; a failed batch rolls back; `version` numbers every state, never reusing a number, and undo and redo restore it, so a cache keyed by version is never stale |
| `ooxml_edit.stamp` | `ExtensionStamp`: an id frozen into an `extLst`/`ext` extension, which Office keeps when it does not know the URI |

The losslessness rule, which everything else rests on:

> A part that was never parsed is written back as the exact bytes that were read.

Only a part whose tree was changed, and marked so with `mark_dirty`, is serialized again.
Undo snapshots hold only those parts, and parts written as raw bytes, so undoing anything
gives back the original bytes, and redoing it gives back the edited ones.

## Using it

A format layer registers its namespaces and the child sequences of the elements it inserts
into, and usually subclasses `OpcPackage` with its own entry points:

```python
from ooxml_edit.history import History
from ooxml_edit.opc import OpcPackage
from ooxml_edit.xml import make, append_in_order, register_child_order, register_namespaces

register_namespaces({"my": "urn:example:my-format"})
register_child_order({"my:props": ("my:name", ("my:item", "my:note"), "my:extLst")})

package = OpcPackage.open("in.zip")
history = History(package)

part = package.main_document_part()
history.checkpoint()                      # before every mutation
append_in_order(package.tree(part), make("my:item", val="1"))
package.mark_dirty(part)

history.undo()                            # the original bytes again
package.save("out.zip")
```
