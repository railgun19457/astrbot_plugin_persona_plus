from __future__ import annotations

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent


def iter_all_components(event: AstrMessageEvent):
    for component in event.get_messages():
        yield component
        if isinstance(component, Comp.Reply) and component.chain:
            yield from component.chain


def has_component_of_types(event: AstrMessageEvent, types: tuple[type, ...]) -> bool:
    for component in iter_all_components(event):
        if isinstance(component, types):
            return True
    return False


def first_component_of_types(
    event: AstrMessageEvent, types: tuple[type, ...]
) -> Comp.BaseMessageComponent | None:
    for component in iter_all_components(event):
        if isinstance(component, types):
            return component
    return None
