"""Carry verified M2 citation metadata across work-snapshot branches."""

from collections import defaultdict

from .db import DomainError
from .outline_workspace import validate_frozen_reference_cards
from .reference_portability import ReferenceCardHead, ReferenceHistory
from .reference_sync import (
    _native_catalog,
    _ordered,
    _stored_catalog,
    _version_id,
    import_reference_archive,
    validate_reference_version,
    validate_version_parent,
)


def fork_reference_cards(tx, origin, target, cards):
    pinned = {card["version_id"] for card in cards if card.get("version_id")}
    if not pinned:
        return {}
    native, _, _ = _native_catalog(tx, origin)
    stored = _stored_catalog(tx, origin)
    sources = {**native, **{key: version for key, (version, _) in stored.items()}}
    validate_frozen_reference_cards(cards, sources.get)

    lineage = {}
    for identifier in sorted(pinned):
        while identifier and identifier not in lineage:
            source = sources.get(identifier)
            if not source:
                raise DomainError("snapshot_reference", f"缺少手法卡来源的历史修订 {identifier}。", 409)
            lineage[identifier] = source
            identifier = source.parent_id
    if len(lineage) > 5000:
        raise DomainError("snapshot_reference", "本次恢复引用的手法卡及其历史超过 5000 项。", 409)

    mapping, devices, versions = {}, {}, []
    selected = defaultdict(list)
    for source in _ordered(lineage):
        validate_reference_version(origin, source)
        validate_version_parent(source, lineage.get)
        rebound = source.model_copy(update={"parent_id": mapping.get(source.parent_id)})
        rebound = rebound.model_copy(update={"id": _version_id(target, rebound)})
        mapping[source.id] = rebound.id
        versions.append(rebound)
        if source.id in stored and stored[source.id][1]:
            devices[rebound.id] = stored[source.id][1]
        if source.id in pinned:
            selected[source.session_id, source.card_id].append(rebound.id)

    # When several revisions are pinned, keep their exact history without choosing one as the new head.
    heads = [
        ReferenceCardHead(session_id=session, card_id=card, revision_id=identifiers[0])
        for (session, card), identifiers in sorted(selected.items())
        if len(identifiers) == 1
    ]
    import_reference_archive(
        tx, target, ReferenceHistory(adopted_heads=heads, versions=versions, source_devices=devices)
    )
    return mapping
