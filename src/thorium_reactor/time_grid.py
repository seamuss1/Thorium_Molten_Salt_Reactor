"""Shared transient boundaries; events are right-continuous controls."""

import math
from bisect import bisect_left
from collections.abc import Iterator, Mapping, Sequence


def transient_intervals(
    duration: float, step: float, events: Sequence[Mapping]
) -> Iterator[tuple[float, float, float]]:
    """Yield (start, end, dt), including an initial zero-length observation."""
    if not math.isfinite(duration) or duration < 0 or not math.isfinite(step) or step <= 0:
        raise ValueError("Transient duration must be finite/non-negative and step finite/positive.")
    boundaries = {0.0, duration}
    for event in events:
        time = float(event["time_s"])
        if not math.isfinite(time):
            raise ValueError("Event times must be finite.")
        if 0 <= time <= duration:
            boundaries.add(time)
    # Declared event/end times take priority over roundoff in i * step. Keep
    # distinct declared events intact, even when deliberately very close.
    anchors = sorted(boundaries)
    for i in range(1, math.ceil(duration / step)):
        time = i * step
        if time >= duration:
            continue
        insertion = bisect_left(anchors, time)
        neighbors = anchors[max(insertion - 1, 0) : insertion + 1]
        if any(abs(time - anchor) <= 4 * max(math.ulp(time), math.ulp(anchor)) for anchor in neighbors):
            continue
        boundaries.add(time)
    previous = 0.0
    yield 0.0, 0.0, 0.0
    for end in sorted(boundaries - {0.0}):
        yield previous, end, end - previous
        previous = end


def apply_events(controls: dict, events: Sequence[Mapping], time: float) -> None:
    aliases = {"reactivity_step_pcm": "reactivity_pcm", "secondary_sink_temp_offset_c": "sink_temp_offset_c"}
    for event in events:
        if float(event["time_s"]) <= time:
            for key, value in event.items():
                target = aliases.get(key, key)
                if target in controls:
                    controls[target] = float(value)
